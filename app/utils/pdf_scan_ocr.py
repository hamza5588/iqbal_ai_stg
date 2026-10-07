"""Vision OCR for scanned / image-only PDFs (chat, tutor, lesson ingest).

Plain loaders and the text layer return nothing when every page is a photograph
of a worksheet. Quiz MCQ extraction already falls back to page images via
``hybrid_vision_pipeline``; this module does the same for RAG-style ingest:

1. Detect a scanned PDF (near-zero extractable text + page images present).
2. Render each page and transcribe it with the Groq vision model.
3. Return ``{0-based page index: text}`` so callers can fill Document.page_content.

Never raises for a single bad page; returns whatever pages succeeded.
Disable with ``RAG_SCAN_OCR=0``.
"""
from __future__ import annotations

import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[A-Za-z]{3,}")


@dataclass(frozen=True)
class ScanInfo:
    is_scanned: bool
    page_count: int
    text_chars: int
    has_images: bool
    reason: str


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def scan_ocr_enabled() -> bool:
    return _env_flag("RAG_SCAN_OCR", True)


def _pymupdf():
    try:
        import pymupdf

        return pymupdf
    except Exception:  # noqa: BLE001
        try:
            import fitz

            return fitz
        except Exception:  # noqa: BLE001
            return None


def detect_scanned_pdf(pdf_path: str, *, min_chars_per_page: float = 40.0) -> ScanInfo:
    """True when the PDF looks like a scan (little/no text layer, images present)."""
    pymupdf = _pymupdf()
    if pymupdf is None:
        return ScanInfo(False, 0, 0, False, "pymupdf_missing")

    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scan detect: cannot open %s: %s", pdf_path, exc)
        return ScanInfo(False, 0, 0, False, "open_failed")

    try:
        page_count = len(doc)
        text_chars = 0
        has_images = False
        for page in doc:
            text_chars += len((page.get_text("text") or "").strip())
            try:
                if page.get_images():
                    has_images = True
            except Exception:  # noqa: BLE001
                pass

        if page_count <= 0:
            return ScanInfo(False, 0, text_chars, has_images, "empty")

        avg = text_chars / float(page_count)
        # No text layer at all → treat as scan (even if embedded-image list is empty;
        # some scanners paste the page as a content stream without /XObject images).
        if text_chars == 0:
            return ScanInfo(True, page_count, text_chars, has_images, "no_text_layer")
        if avg < min_chars_per_page and has_images:
            return ScanInfo(True, page_count, text_chars, has_images, "sparse_text_layer")
        return ScanInfo(False, page_count, text_chars, has_images, "digital_text")
    finally:
        doc.close()


def _render_page_data_url(page, dpi: int, jpeg_quality: int) -> str:
    import base64

    pymupdf = _pymupdf()
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    return "data:image/jpeg;base64," + base64.b64encode(
        pix.tobytes("jpeg", jpg_quality=jpeg_quality)
    ).decode("ascii")


_SYSTEM = (
    "You are a careful OCR engine for educational worksheets and textbooks. "
    "Transcribe the page image exactly. Keep maths as LaTeX ($...$ or \\(...\\)). "
    "Preserve MCQ labels (A/B/C/D), numbering, and line breaks. "
    "Output ONLY the page text — no preamble, no markdown fences."
)


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


def _clean(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I).strip()
    fence = re.match(r"^```[a-zA-Z]*\s*\n(.*?)\n```\s*$", text, re.S)
    if fence:
        text = fence.group(1).strip()
    return text


def _ocr_one_page(llm, page_no: int, data_url: str) -> Optional[str]:
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.utils.groq_rate_limit import invoke_with_groq_rate_limit

    messages = [
        SystemMessage(content=_SYSTEM),
        HumanMessage(
            content=[
                {
                    "type": "text",
                    "text": (
                        f"OCR page {page_no}. Transcribe all readable text and maths "
                        "from this scanned page image."
                    ),
                },
                {"type": "image_url", "image_url": {"url": data_url}},
            ]
        ),
    ]
    resp = invoke_with_groq_rate_limit(
        lambda: llm.invoke(messages),
        description=f"scan OCR page {page_no}",
    )
    text = _clean(_message_text(resp))
    if len(text) < 8:
        return None
    return text


def _vision_llm():
    try:
        from app.services.quiz.hybrid_vision_pipeline import create_vision_llm

        return create_vision_llm()
    except Exception as exc:  # noqa: BLE001
        logger.info("Scan OCR unavailable (%s)", exc)
        return None


def ocr_scanned_pages(
    pdf_path: str,
    *,
    progress: Optional[Callable[[int, int], None]] = None,
    llm=None,
) -> Dict[int, str]:
    """OCR every page of a scanned PDF. Returns {0-based index: text}."""
    out: Dict[int, str] = {}
    if not scan_ocr_enabled():
        logger.info("Scan OCR disabled (RAG_SCAN_OCR=0)")
        return out

    info = detect_scanned_pdf(pdf_path)
    if not info.is_scanned:
        logger.info("Scan OCR skipped: %s (%s chars / %s pages)", info.reason, info.text_chars, info.page_count)
        return out

    pymupdf = _pymupdf()
    if pymupdf is None:
        return out

    llm = llm or _vision_llm()
    if llm is None:
        logger.warning("Scan OCR: vision model unavailable — set GROQ_API_KEY")
        return out

    max_pages = int(os.getenv("RAG_SCAN_OCR_MAX_PAGES", "40"))
    budget = float(os.getenv("RAG_SCAN_OCR_BUDGET_SECONDS", "240"))
    workers = max(1, int(os.getenv("RAG_SCAN_OCR_WORKERS", "2")))
    dpi = int(os.getenv("RAG_SCAN_OCR_DPI", os.getenv("PDF_RENDER_DPI", "180")))
    quality = int(os.getenv("PDF_RENDER_JPEG_QUALITY", "85"))

    started = time.time()
    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scan OCR: cannot open %s: %s", pdf_path, exc)
        return out

    jobs: List[Tuple[int, str]] = []
    try:
        limit = min(len(doc), max_pages)
        for idx in range(limit):
            try:
                jobs.append((idx, _render_page_data_url(doc[idx], dpi, quality)))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Scan OCR: render failed page %s: %s", idx + 1, exc)
    finally:
        doc.close()

    if not jobs:
        return out

    done = 0
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {
            pool.submit(_ocr_one_page, llm, idx + 1, url): idx for idx, url in jobs
        }
        try:
            for fut in as_completed(futures, timeout=max(1.0, budget)):
                idx = futures[fut]
                done += 1
                try:
                    text = fut.result()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Scan OCR failed page %s: %s", idx + 1, exc)
                    text = None
                if text:
                    out[idx] = text
                if progress:
                    try:
                        progress(done, len(jobs))
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            logger.warning(
                "Scan OCR: time budget (%ss) after %d/%d page(s)",
                budget,
                done,
                len(jobs),
            )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    logger.info(
        "Scan OCR: %s — %d/%d page(s) transcribed in %.1fs",
        info.reason,
        len(out),
        len(jobs),
        time.time() - started,
    )
    return out
