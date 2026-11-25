"""OCR for scanned pages during RAG ingest (lesson / chat PDFs).

Plain loaders (PyPDF / PyMuPDF / PDFPlumber) only read the PDF text layer. A scanned
page is a picture with no text layer (or only a page number), so before this module
such PDFs were rejected at upload. Here every page whose text layer is (almost) empty
but that carries an image is rendered and read with Tesseract.

Why Tesseract, run as a CLI process per page:
    - It is the fastest of the common open-source engines on CPU (Tesseract, RapidOCR,
      EasyOCR, PaddleOCR) and needs no GPU or Python ML stack.
    - It ships Urdu (``urd``) as well as English language data.
    - One ``tesseract`` process per page with ``OMP_THREAD_LIMIT=1`` and several pages
      in flight is how OCRmyPDF scales it: it uses all cores without the OpenMP
      oversubscription that makes Tesseract slower when its own threads compete.

Pages are rendered in grayscale by PyMuPDF on the calling thread (PyMuPDF documents are
not thread-safe); only the OCR processes run in parallel, with a bounded number of
rendered pages waiting so memory stays flat on long books.

Nothing here fails the ingest: a missing binary, a timeout or bad output leaves the page
as it was.

Configuration (env, all optional):
    RAG_OCR_ENABLED          - "true" (default) / "false"
    TESSERACT_CMD            - path to the tesseract binary (default: found on PATH)
    RAG_OCR_LANG             - Tesseract languages, default "eng" (e.g. "eng+urd")
    RAG_OCR_DPI              - render resolution, default 300
    RAG_OCR_WORKERS          - parallel tesseract processes, default min(4, CPUs)
    RAG_OCR_MAX_PAGES        - most pages OCR'd per document, default 300
    RAG_OCR_PAGE_TIMEOUT     - seconds per page, default 60
    RAG_OCR_BUDGET_SECONDS   - total seconds per document, default 900
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from collections import OrderedDict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from functools import lru_cache
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Same threshold as the upload preflight: fewer visible characters than this means the
# page has no real text (scanned books often keep only a page number in the text layer).
MIN_PAGE_TEXT_CHARS = 40

_WINDOWS_DEFAULT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

ProgressFn = Callable[[str, int, str], None]


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def visible_char_count(text: Optional[str]) -> int:
    return len(re.sub(r"\s+", "", text or ""))


@lru_cache(maxsize=1)
def tesseract_cmd() -> Optional[str]:
    configured = (os.getenv("TESSERACT_CMD") or "").strip()
    if configured:
        return configured if os.path.exists(configured) or shutil.which(configured) else None
    found = shutil.which("tesseract")
    if found:
        return found
    if os.name == "nt" and os.path.exists(_WINDOWS_DEFAULT_CMD):
        return _WINDOWS_DEFAULT_CMD
    return None


@lru_cache(maxsize=1)
def installed_languages() -> Tuple[str, ...]:
    cmd = tesseract_cmd()
    if not cmd:
        return ()
    try:
        proc = subprocess.run(
            [cmd, "--list-langs"], capture_output=True, timeout=20, check=False
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not list Tesseract languages: %s", exc)
        return ()
    out = (proc.stdout or b"").decode("utf-8", "replace").splitlines()
    # First line is a header ("List of available languages ..."), the rest are codes.
    return tuple(line.strip() for line in out[1:] if line.strip())


def ocr_languages() -> Optional[str]:
    """Requested languages that are actually installed, joined for ``-l``."""
    installed = set(installed_languages())
    requested = [p.strip() for p in (os.getenv("RAG_OCR_LANG") or "eng").split("+") if p.strip()]
    usable = [lang for lang in requested if lang in installed]
    missing = [lang for lang in requested if lang not in installed]
    if missing:
        logger.warning("Tesseract language data not installed, skipped: %s", "+".join(missing))
    if not usable and "eng" in installed:
        usable = ["eng"]
    return "+".join(usable) or None


def ocr_available() -> bool:
    """True when scanned pages can be read on this host."""
    if not _env_flag("RAG_OCR_ENABLED", True):
        return False
    return bool(tesseract_cmd() and ocr_languages())


def _run_tesseract(cmd: str, png_bytes: bytes, args: List[str], timeout: int) -> str:
    env = dict(os.environ, OMP_THREAD_LIMIT="1")
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(
        [cmd, "stdin", "stdout", *args],
        input=png_bytes,
        capture_output=True,
        timeout=timeout,
        env=env,
        check=False,
        **kwargs,
    )
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(f"tesseract exit {proc.returncode}: {err[:300]}")
    return (proc.stdout or b"").decode("utf-8", "replace")


def _ocr_image(cmd: str, png_bytes: bytes, lang: str, timeout: int, psm: str = "3") -> str:
    return _run_tesseract(cmd, png_bytes, ["-l", lang, "--psm", str(psm)], timeout)


# Tesseract language codes by the script their text is written in. Running every
# configured language on every page ("eng+urd" on an English page) makes Tesseract
# both slower and less accurate, so each page gets only the languages of its script.
_SCRIPT_LANGS = {
    "Arabic": {"urd", "ara", "fas", "pus", "snd", "uig"},
    "Latin": {"eng", "fra", "deu", "spa", "ita", "por", "nld"},
}


def _detect_script(cmd: str, png_bytes: bytes, timeout: int) -> Optional[str]:
    """Page script from Tesseract OSD (``--psm 0``), or None when it cannot tell."""
    try:
        out = _run_tesseract(cmd, png_bytes, ["--psm", "0"], timeout)
    except Exception:  # noqa: BLE001 - e.g. "Too few characters" on sparse pages
        return None
    match = re.search(r"^Script:\s*(\S+)", out, re.MULTILINE)
    return match.group(1) if match else None


def _page_lang(cmd: str, png_bytes: bytes, langs: str, timeout: int) -> str:
    """Configured languages narrowed to the page's script (all of them if unknown)."""
    parts = langs.split("+")
    families = {name for name, codes in _SCRIPT_LANGS.items() if codes & set(parts)}
    if len(families) < 2 or "osd" not in installed_languages():
        return langs
    script = _detect_script(cmd, png_bytes, timeout)
    own = _SCRIPT_LANGS.get(script or "")
    if not own:
        return langs
    other_scripts = set().union(*(c for n, c in _SCRIPT_LANGS.items() if n != script))
    if script == "Latin":
        chosen = [p for p in parts if p not in other_scripts]
    else:
        # Keep Latin languages after the page's own: English words and numbers
        # inside an Urdu page still need to be read.
        chosen = [p for p in parts if p in own] + [p for p in parts if p not in own]
    return "+".join(chosen) or langs


def _ocr_job(cmd: str, png_bytes: bytes, langs: str, timeout: int, psm: str = "3", tsv: bool = False) -> str:
    lang = _page_lang(cmd, png_bytes, langs, timeout)
    args = ["-l", lang, "--psm", str(psm)]
    if tsv:
        args.append("tsv")
    return _run_tesseract(cmd, png_bytes, args, timeout)


def _clean_ocr_text(text: str) -> str:
    lines = [ln.rstrip() for ln in text.replace("\f", "\n").splitlines()]
    cleaned = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _scanned_page_indices(doc, page_texts: Dict[int, str]) -> List[int]:
    result: List[int] = []
    for idx, page in enumerate(doc):
        if visible_char_count(page_texts.get(idx, "")) >= MIN_PAGE_TEXT_CHARS:
            continue
        try:
            if page.get_images():
                result.append(idx)
        except Exception:  # noqa: BLE001
            continue
    return result


def pages_needing_ocr(pdf_path: str, page_texts: Dict[int, str]) -> List[int]:
    """0-based pages with (almost) no text that carry an image, i.e. scanned pages.

    ``page_texts`` is the text the loaders produced per page index, after any hidden
    text was stripped, so an "OCR'd" scan whose text layer is invisible counts as
    scanned here and gets read from the image instead.
    """
    import fitz  # PyMuPDF

    doc = fitz.open(pdf_path)
    try:
        return _scanned_page_indices(doc, page_texts)
    finally:
        doc.close()


def ocr_pdf_pages(
    pdf_path: str,
    page_indices: Sequence[int],
    progress: Optional[ProgressFn] = None,
) -> Dict[int, str]:
    """OCR the given 0-based pages. Returns {page_index: text} for pages that produced text."""
    import fitz  # PyMuPDF

    doc = fitz.open(pdf_path)
    try:
        return _ocr_doc_pages(doc, page_indices, progress)
    finally:
        doc.close()


def _ocr_doc_pages(
    doc,
    page_indices: Sequence[int],
    progress: Optional[ProgressFn] = None,
    tsv: bool = False,
) -> Dict[int, str]:
    """OCR pages of an open document in parallel: {page_index: text}.

    With ``tsv=True`` the values are Tesseract TSV (word boxes in pixels at
    ``RAG_OCR_DPI``) instead of plain text.
    """
    cmd = tesseract_cmd()
    lang = ocr_languages()
    if not cmd or not lang or not page_indices:
        return {}

    import fitz  # PyMuPDF

    dpi = _env_int("RAG_OCR_DPI", 300, minimum=72)
    workers = _env_int("RAG_OCR_WORKERS", min(4, os.cpu_count() or 1))
    max_pages = _env_int("RAG_OCR_MAX_PAGES", 300)
    page_timeout = _env_int("RAG_OCR_PAGE_TIMEOUT", 60)
    budget = _env_int("RAG_OCR_BUDGET_SECONDS", 900)

    todo = list(page_indices)[:max_pages]
    if len(page_indices) > max_pages:
        logger.warning(
            "OCR capped at %d of %d scanned pages (RAG_OCR_MAX_PAGES)", max_pages, len(page_indices)
        )

    started = time.time()
    results: Dict[int, str] = {}
    done = 0
    total = len(todo)

    def _report() -> None:
        if progress:
            try:
                progress("ocr", 29, f"Reading scanned pages with OCR ({done}/{total})...")
            except Exception:  # noqa: BLE001
                pass

    _report()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {}

        def _collect(finished: Iterable) -> None:
            nonlocal done
            for fut in finished:
                idx = pending.pop(fut)
                done += 1
                try:
                    text = fut.result() if tsv else _clean_ocr_text(fut.result())
                    if text:
                        results[idx] = text
                except Exception as exc:  # noqa: BLE001
                    logger.warning("OCR failed on page %d: %s", idx + 1, exc)
            _report()

        for idx in todo:
            if time.time() - started > budget:
                logger.warning(
                    "OCR time budget (%ss) reached; %d page(s) left unread", budget, total - done - len(pending)
                )
                break
            # Keep only a few rendered pages waiting so memory stays flat on long books.
            while len(pending) >= workers * 2:
                finished, _ = wait(list(pending), return_when=FIRST_COMPLETED)
                _collect(finished)
            try:
                pix = doc[idx].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
                png = pix.tobytes("png")
                del pix
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not render page %d for OCR: %s", idx + 1, exc)
                done += 1
                continue
            pending[pool.submit(_ocr_job, cmd, png, lang, page_timeout, "3", tsv)] = idx

        if pending:
            finished, _ = wait(list(pending))
            _collect(finished)

    logger.info(
        "OCR read %d of %d scanned page(s) in %.1fs (lang=%s, dpi=%d, workers=%d)",
        len(results), total, time.time() - started, lang, dpi, workers,
    )
    return results


def apply_ocr_to_docs(pdf_path: str, docs: list, progress: Optional[ProgressFn] = None) -> int:
    """Replace the text of scanned pages in loader ``docs`` with OCR text.

    ``docs`` are LangChain Documents, one per page, whose ``metadata['page']`` is the
    0-based page index (falls back to list position). Returns how many pages were filled.
    """
    index_of: Dict[int, int] = {}
    page_texts: Dict[int, str] = {}
    for pos, doc in enumerate(docs):
        meta = doc.metadata if isinstance(doc.metadata, dict) else {}
        page_idx = meta.get("page", pos)
        if not isinstance(page_idx, int):
            page_idx = pos
        index_of[page_idx] = pos
        page_texts[page_idx] = doc.page_content or ""

    scanned = [idx for idx in pages_needing_ocr(pdf_path, page_texts) if idx in index_of]
    if not scanned:
        return 0
    logger.info("Scanned pages detected: %d of %d; running OCR", len(scanned), len(docs))
    texts = ocr_pdf_pages(pdf_path, scanned, progress)
    filled = 0
    for page_idx, text in texts.items():
        if visible_char_count(text) == 0:
            continue
        doc = docs[index_of[page_idx]]
        doc.page_content = text
        if isinstance(doc.metadata, dict):
            doc.metadata["ocr"] = True
        filled += 1
    return filled


# ---------------------------------------------------------------------------
# Shared entry point for every PDF upload pipeline (quiz / MCQ, diagnostic,
# target bank, scenario assessment, lesson): text layer per page, with scanned
# pages filled in by OCR.
# ---------------------------------------------------------------------------

_PAGE_TEXT_CACHE: "OrderedDict[str, Tuple[str, ...]]" = OrderedDict()
_PAGE_TEXT_CACHE_LOCK = threading.Lock()
_PAGE_TEXT_CACHE_SIZE = 8


def _read_source(source) -> bytes:
    if isinstance(source, (bytes, bytearray, memoryview)):
        return bytes(source)
    with open(os.fspath(source), "rb") as fh:
        return fh.read()


def pdf_page_texts(source, *, ocr: bool = True, sort: bool = False) -> List[str]:
    """Text of every page of a PDF (bytes or path), one string per page.

    The text layer is used as-is; pages that are scanned images (fewer than
    ``MIN_PAGE_TEXT_CHARS`` visible characters plus an image) are read with OCR
    when it is available. Results are cached per file content, so pipelines that
    read the same upload more than once only OCR it once.
    """
    import fitz  # PyMuPDF

    data = _read_source(source)
    use_ocr = ocr and ocr_available()
    key = f"{hashlib.sha256(data).hexdigest()}:{int(use_ocr)}:{int(sort)}"
    with _PAGE_TEXT_CACHE_LOCK:
        cached = _PAGE_TEXT_CACHE.get(key)
        if cached is not None:
            _PAGE_TEXT_CACHE.move_to_end(key)
            return list(cached)

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        texts = [(page.get_text("text", sort=sort) or "") for page in doc]
        if use_ocr:
            scanned = _scanned_page_indices(doc, dict(enumerate(texts)))
            if scanned:
                logger.info("Scanned pages detected: %d of %d; running OCR", len(scanned), len(texts))
                for idx, text in _ocr_doc_pages(doc, scanned).items():
                    if visible_char_count(text):
                        texts[idx] = text
    finally:
        doc.close()

    with _PAGE_TEXT_CACHE_LOCK:
        _PAGE_TEXT_CACHE[key] = tuple(texts)
        _PAGE_TEXT_CACHE.move_to_end(key)
        while len(_PAGE_TEXT_CACHE) > _PAGE_TEXT_CACHE_SIZE:
            _PAGE_TEXT_CACHE.popitem(last=False)
    return texts


def pdf_plain_text(source, *, ocr: bool = True) -> str:
    """Whole-document text (pages joined by newlines), scanned pages OCR'd."""
    return "\n".join(pdf_page_texts(source, ocr=ocr))


def scanned_page_indices(doc, page_texts: Optional[Dict[int, str]] = None) -> List[int]:
    """0-based scanned pages of an open PyMuPDF document (text layer used if not given)."""
    if page_texts is None:
        page_texts = {i: (page.get_text("text") or "") for i, page in enumerate(doc)}
    return _scanned_page_indices(doc, page_texts)


def _tsv_to_text_dict(tsv: str, scale: float) -> dict:
    """Tesseract TSV -> the shape of PyMuPDF ``page.get_text("dict")`` (blocks/lines/spans).

    Word boxes are converted from image pixels to PDF points with ``scale``; each word
    is one span (a space is appended to all but the last word of a line).
    """
    rows: Dict[Tuple[int, int, int], list] = {}
    for raw in tsv.splitlines()[1:]:
        cols = raw.split("\t")
        if len(cols) < 12 or cols[0] != "5":  # level 5 = word
            continue
        text = cols[11]
        if not text.strip():
            continue
        try:
            left, top, width, height = (float(c) for c in cols[6:10])
        except ValueError:
            continue
        key = (int(cols[2]), int(cols[3]), int(cols[4]))  # block, paragraph, line
        bbox = (left * scale, top * scale, (left + width) * scale, (top + height) * scale)
        rows.setdefault(key, []).append({"text": text, "bbox": bbox, "size": height * scale})
    blocks: Dict[int, list] = {}
    for (block, _par, _line), words in sorted(rows.items()):
        words.sort(key=lambda w: w["bbox"][0])
        for w in words[:-1]:
            w["text"] += " "
        bbox = (
            min(w["bbox"][0] for w in words), min(w["bbox"][1] for w in words),
            max(w["bbox"][2] for w in words), max(w["bbox"][3] for w in words),
        )
        blocks.setdefault(block, []).append({"bbox": bbox, "spans": words})
    return {"blocks": [{"lines": lines} for _, lines in sorted(blocks.items())]}


def ocr_page_dicts(doc, page_indices: Sequence[int]) -> Dict[int, dict]:
    """OCR word boxes for parsers that need positions: {page_index: get_text("dict")-shaped}.

    Pages are read in parallel, each with the languages of its own script. Pages that
    fail are left out, so callers fall back to the page's text layer.
    """
    if not ocr_available() or not page_indices:
        return {}
    scale = 72.0 / _env_int("RAG_OCR_DPI", 300, minimum=72)
    return {
        idx: _tsv_to_text_dict(tsv, scale)
        for idx, tsv in _ocr_doc_pages(doc, page_indices, tsv=True).items()
    }


def ocr_page_text(page, psm: str = "3") -> str:
    """OCR one PyMuPDF page with a chosen Tesseract page-segmentation mode.

    psm "4" (one column of variable-size lines) reads ruled tables row by row,
    e.g. an answer-key table, where the default mode drops short cells.
    Returns "" when OCR is unavailable or fails.
    """
    import fitz  # PyMuPDF

    cmd, lang = tesseract_cmd(), ocr_languages()
    if not ocr_available() or not cmd or not lang:
        return ""
    try:
        png = page.get_pixmap(dpi=_env_int("RAG_OCR_DPI", 300, minimum=72), colorspace=fitz.csGRAY).tobytes("png")
        return _clean_ocr_text(_ocr_job(cmd, png, lang, _env_int("RAG_OCR_PAGE_TIMEOUT", 60), psm))
    except Exception as exc:  # noqa: BLE001
        logger.warning("OCR failed on page %d: %s", getattr(page, "number", -1) + 1, exc)
        return ""
