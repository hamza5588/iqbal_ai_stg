"""Hybrid PDF page reading for RAG ingestion (chat / tutor / lesson PDFs).

Plain text extraction flattens mathematics: a superscript "10" after "−4" comes out as
"−410", a nested power or a stacked fraction is split across lines, and Word's math font
is emitted as Unicode "mathematical alphanumerics" the embedder barely matches. The chat
then answers from that broken text (e.g. "A. −410" instead of "A. −4^10").

Same idea as the quiz reader (app/services/quiz/hybrid_vision_pipeline.py), applied per page:

1. TEXT EVIDENCE  - the PDF text layer, rebuilt span by span so superscripts / subscripts
                    survive as ``^{..}`` / ``_{..}`` (free, deterministic, every page).
2. VISUAL EVIDENCE - pages that actually contain maths are rendered and transcribed by the
                    vision model with the text evidence alongside (fractions, roots, nested
                    powers), within a page cap and a time budget.

Pages without maths are left exactly as the normal loader produced them. Anything that
fails (no Groq key, rate limit, odd output) falls back to step 1 or to the original text -
ingestion never fails because of this module.
"""
from __future__ import annotations

import logging
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_MATH_FONT_RE = re.compile(r"math|cmmi|cmsy|cmex|cmr\d|symbol|mt ?extra|euclid|stix", re.I)
_MATH_CHAR_RE = re.compile(r"[∀-⋿√²³¹⁰-₟\U0001d400-\U0001d7ff]")
_WORD_RE = re.compile(r"[A-Za-z]{3,}")


def _pymupdf():
    """PyMuPDF under its current name, or the legacy ``fitz`` name on older installs."""
    try:
        import pymupdf

        return pymupdf
    except Exception:  # noqa: BLE001
        try:
            import fitz

            return fitz
        except Exception:  # noqa: BLE001
            return None


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def hybrid_text_enabled() -> bool:
    return _env_flag("RAG_HYBRID_PDF_TEXT", True)


def normalize_math_unicode(text: str) -> str:
    """Mathematical alphanumerics (bold / italic digits and letters from math fonts) -> plain."""
    if not text:
        return text
    out = []
    for ch in text:
        if "\U0001d400" <= ch <= "\U0001d7ff":
            out.append(unicodedata.normalize("NFKC", ch))
        else:
            out.append(ch)
    return "".join(out)


def _line_metrics(spans: List[dict]) -> Tuple[float, float]:
    """(main font size, baseline y) of a line = those of its largest spans."""
    sized = [s for s in spans if (s.get("text") or "").strip()]
    if not sized:
        return 0.0, 0.0
    main = max(float(s.get("size") or 0) for s in sized)
    base = [float(s["origin"][1]) for s in sized if float(s.get("size") or 0) >= main * 0.95]
    return main, (sum(base) / len(base) if base else float(sized[0]["origin"][1]))


def _script_kind(span: dict, main: float, base_y: float) -> str:
    """'sup' / 'sub' / '' for one span relative to its line."""
    text = (span.get("text") or "").strip()
    if not text or not main:
        return ""
    size = float(span.get("size") or 0)
    y = float(span["origin"][1])
    # A script is a short, visibly smaller fragment. (PyMuPDF also sets its superscript flag
    # on full-size text sitting beside a stacked fraction - that is not an exponent.)
    if size > main * 0.90 or len(text) > 12 or not re.search(r"\w", text):
        return ""
    if int(span.get("flags") or 0) & 1:  # PyMuPDF TEXT_FONT_SUPERSCRIPT
        return "sup"
    if size <= main * 0.86:
        if y <= base_y - main * 0.18:
            return "sup"
        if y >= base_y + main * 0.10:
            return "sub"
    return ""


def page_text_with_scripts(page) -> Tuple[str, bool]:
    """Rebuild a page's text from its spans, keeping super/subscripts.

    Returns (text, has_math). ``has_math`` is True when the page uses a maths font, maths
    symbols or raised/lowered text - i.e. the plain loader text is likely wrong for it.
    """
    try:
        data = page.get_text("dict")
    except Exception as exc:  # noqa: BLE001
        logger.debug("span read failed: %s", exc)
        return "", False

    has_math = False
    lines_out: List[dict] = []
    for block in data.get("blocks") or []:
        for line in block.get("lines") or []:
            spans = line.get("spans") or []
            if not any((s.get("text") or "").strip() for s in spans):
                continue
            main, base_y = _line_metrics(spans)
            parts: List[str] = []
            for s in spans:
                raw = s.get("text") or ""
                if not raw:
                    continue
                if _MATH_FONT_RE.search(str(s.get("font") or "")) and raw.strip():
                    has_math = True
                if _MATH_CHAR_RE.search(raw):
                    has_math = True
                txt = normalize_math_unicode(raw)
                kind = _script_kind(s, main, base_y)
                if kind:
                    has_math = True
                    lead = txt[: len(txt) - len(txt.lstrip())]
                    trail = txt[len(txt.rstrip()):]
                    txt = f"{lead}{'^' if kind == 'sup' else '_'}{{{txt.strip()}}}{trail}"
                parts.append(txt)
            text = "".join(parts)
            bbox = line.get("bbox") or (0, 0, 0, 0)
            lines_out.append({"text": text, "main": main, "base": base_y, "bbox": bbox})

    # A lone small fragment that PyMuPDF put on its own line right above/after a maths line
    # is the OUTER exponent of a nested power: "{(−4)^{2}}" + "5"  ->  "{(−4)^{2}}^{5}".
    merged: List[dict] = []
    for ln in lines_out:
        stripped = ln["text"].strip()
        prev = merged[-1] if merged else None
        if (
            prev
            and 0 < len(stripped) <= 4
            and re.fullmatch(r"[\w+\-−]+", stripped)
            and ln["main"] and prev["main"] and ln["main"] <= prev["main"] * 0.86
            and ln["base"] <= prev["base"] - prev["main"] * 0.18         # raised above prev's baseline
            and ln["bbox"][0] >= prev["bbox"][2] - prev["main"] * 1.5    # at / after prev's right end
            and re.search(r"[\w)\]}]\s*$", prev["text"])                  # follows a value, not "=" / "+"
        ):
            prev["text"] = prev["text"].rstrip() + "^{" + stripped + "}"
            has_math = True
            continue
        merged.append(dict(ln))

    text = "\n".join(ln["text"].rstrip() for ln in merged)
    text = re.sub(r"\^\{([^{}]*)\}\^\{([^{}]*)\}(?=\S)", r"^{\1}^{\2}", text)
    return text.strip(), has_math


# --------------------------------------------------------------------------------------
# Vision transcription
# --------------------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You transcribe ONE page of a PDF into clean Markdown so it can be searched and quoted "
    "later. You are a faithful copyist: never solve, answer, explain, summarise, reorder or "
    "add anything that is not printed on the page."
)

_USER_PROMPT = """Transcribe page {page_no} exactly as printed.

You get two views of the same page:
- TEXT EVIDENCE: the PDF's own text layer. Its words, numbers, labels and reading order are
  reliable, but mathematics in it is often flattened or scrambled (exponents, fractions,
  roots, subscripts).
- The page IMAGE: the truth for how every mathematical expression is laid out.

Rules:
- Keep ALL text, in reading order: headings, question numbers (Q1., Q2. ...), option labels
  (A. B. C. D.), metadata lines, tables, footers. Do not drop or merge questions or options.
- Write every mathematical expression in LaTeX between single dollar signs, reading its
  structure from the IMAGE: powers ($-4^{{10}}$, $[(-4)^2]^5$), fractions ($\\frac{{3}}{{2}}$),
  roots ($\\sqrt{{x}}$), subscripts ($\\log_a 8$). Never flatten an exponent into the digits
  before it.
- Copy ordinary words from the TEXT EVIDENCE spelling. Do not translate.
- Tables -> Markdown tables. A figure/diagram -> one line: [Figure: short neutral description].
- Output ONLY the transcription. No preamble, no code fences, no comments.

<TEXT_EVIDENCE>
{evidence}
</TEXT_EVIDENCE>"""


def _render_page_data_url(page, dpi: int, jpeg_quality: int) -> str:
    import base64

    pymupdf = _pymupdf()

    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    return "data:image/jpeg;base64," + base64.b64encode(
        pix.tobytes("jpeg", jpg_quality=jpeg_quality)
    ).decode("ascii")


def _message_text(resp) -> str:
    content = getattr(resp, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            (b.get("text") or "") if isinstance(b, dict) else str(getattr(b, "text", "") or "")
            for b in content
        )
    return ""


def _clean_transcription(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I).strip()
    fence = re.match(r"^```[a-zA-Z]*\s*\n(.*?)\n```\s*$", text, re.S)
    if fence:
        text = fence.group(1).strip()
    return text


def transcription_is_faithful(vision_text: str, evidence_text: str) -> bool:
    """Reject a transcription that lost or invented a large part of the page.

    The words of the page are reliable in the text layer, so a faithful transcription must
    contain nearly all of them and must not be wildly longer or shorter.

    When there is no text layer (scanned page), accept any non-trivial transcription.
    """
    if not vision_text or not vision_text.strip():
        return False
    evidence_text = evidence_text or ""
    if not evidence_text.strip():
        return len(vision_text.strip()) >= 8
    want = [w.lower() for w in _WORD_RE.findall(evidence_text)]
    if want:
        have = set(w.lower() for w in _WORD_RE.findall(vision_text))
        if sum(1 for w in want if w in have) / len(want) < 0.85:
            return False
    # LaTeX makes maths longer than the flattened text layer, so allow generous growth,
    # but not a page that shrank to a fraction or ran away.
    upper = max(400, 3 * len(evidence_text))
    return 0.5 * len(evidence_text) <= len(vision_text) <= upper


def _transcribe_page(llm, page_no: int, data_url: str, evidence: str) -> Optional[str]:
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.utils.groq_rate_limit import invoke_with_groq_rate_limit

    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(
            content=[
                {"type": "text", "text": _USER_PROMPT.format(page_no=page_no, evidence=evidence[:12000])},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]
        ),
    ]
    resp = invoke_with_groq_rate_limit(
        lambda: llm.invoke(messages), description=f"RAG hybrid page transcription (page {page_no})"
    )
    text = _clean_transcription(_message_text(resp))
    if not transcription_is_faithful(text, evidence):
        logger.info("Hybrid transcription for page %s rejected (not faithful to the text layer)", page_no)
        return None
    return text


def _vision_llm():
    """The quiz reader's Groq vision model, or None when it cannot be used here."""
    if not _env_flag("RAG_HYBRID_VISION", True):
        return None
    try:
        from app.services.quiz.hybrid_vision_pipeline import create_vision_llm

        return create_vision_llm()
    except Exception as exc:  # noqa: BLE001 - no key / package: text evidence only
        logger.info("Hybrid vision unavailable for RAG ingest (%s) - using text evidence only", exc)
        return None


def hybrid_page_texts(
    pdf_path: str,
    progress: Optional[Callable[[int, int], None]] = None,
    llm=None,
) -> Dict[int, str]:
    """Better text for the pages of ``pdf_path`` that contain mathematics.

    Returns {0-based page index: text}. Pages that are not in the dict should keep the text
    the normal loader produced. Never raises.
    """
    out: Dict[int, str] = {}
    if not hybrid_text_enabled():
        return out
    pymupdf = _pymupdf()
    if pymupdf is None:
        logger.warning("Hybrid PDF text skipped: PyMuPDF is not installed")
        return out

    max_pages = int(os.getenv("RAG_HYBRID_VISION_MAX_PAGES", "25"))
    budget = float(os.getenv("RAG_HYBRID_VISION_BUDGET_SECONDS", "150"))
    workers = max(1, int(os.getenv("RAG_HYBRID_VISION_WORKERS", "3")))
    dpi = int(os.getenv("RAG_HYBRID_VISION_DPI", os.getenv("PDF_RENDER_DPI", "150")))
    quality = int(os.getenv("PDF_RENDER_JPEG_QUALITY", "85"))

    started = time.time()
    doc = None
    jobs = []
    math_pages: List[int] = []
    try:
        doc = pymupdf.open(str(pdf_path))
        for idx, page in enumerate(doc):
            text, has_math = page_text_with_scripts(page)
            if has_math and text:
                out[idx] = text  # step 1: text evidence with exponents kept
                math_pages.append(idx)
        if not math_pages:
            pass  # closed in finally; scan OCR below
        else:
            vision_pages = math_pages[:max_pages]
            llm = llm or _vision_llm()
            if llm is None or not vision_pages:
                logger.info("Hybrid PDF text: %d maths page(s), text evidence only", len(math_pages))
                return out
            for idx in vision_pages:
                try:
                    jobs.append((idx, _render_page_data_url(doc[idx], dpi, quality), out[idx]))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Hybrid PDF text: render failed for page %s: %s", idx + 1, exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Hybrid PDF text: cannot open %s: %s", pdf_path, exc)
        return out
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:  # noqa: BLE001
                pass

    if not math_pages:
        try:
            from app.utils.pdf_scan_ocr import detect_scanned_pdf, ocr_scanned_pages

            if detect_scanned_pdf(pdf_path).is_scanned:
                return ocr_scanned_pages(pdf_path, progress=progress, llm=llm)
        except Exception as scan_exc:  # noqa: BLE001
            logger.info("Hybrid PDF text: scan OCR fallback skipped: %s", scan_exc)
        return out

    if not jobs:
        return out

    done = 0
    improved = 0
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {pool.submit(_transcribe_page, llm, idx + 1, url, ev): idx for idx, url, ev in jobs}
        try:
            for fut in as_completed(futures, timeout=max(1.0, budget)):
                idx = futures[fut]
                done += 1
                try:
                    text = fut.result()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Hybrid transcription failed for page %s: %s", idx + 1, exc)
                    text = None
                if text:
                    out[idx] = text
                    improved += 1
                if progress:
                    try:
                        progress(done, len(jobs))
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001 - time budget hit: keep what finished, rest stay text evidence
            logger.warning(
                "Hybrid PDF text: time budget (%ss) reached after %d/%d page(s)", budget, done, len(jobs)
            )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    logger.info(
        "Hybrid PDF text: %d maths page(s), %d transcribed with vision, %d on text evidence (%.1fs)",
        len(math_pages), improved, len(math_pages) - improved, time.time() - started,
    )
    return out
