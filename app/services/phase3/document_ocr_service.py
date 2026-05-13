"""Extract text from student uploads using Kreuzberg (Tesseract OCR backend)."""
from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def _guess_mime(path: str, mime_type: Optional[str]) -> Optional[str]:
    if mime_type:
        return mime_type.split(";")[0].strip().lower() or None
    lower = path.lower()
    if lower.endswith(".pdf"):
        return "application/pdf"
    if lower.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp", ".gif")):
        return "image/jpeg" if lower.endswith((".jpg", ".jpeg")) else "image/png"
    return None


def _fallback_extract(path: str, mime: Optional[str]) -> Tuple[str, Optional[str]]:
    """Lightweight fallback when Kreuzberg is unavailable or fails."""
    text_parts: list[str] = []
    try:
        import fitz  # PyMuPDF

        if mime == "application/pdf" or path.lower().endswith(".pdf"):
            doc = fitz.open(path)
            try:
                for page in doc:
                    text_parts.append(page.get_text() or "")
            finally:
                doc.close()
            joined = "\n".join(text_parts).strip()
            if joined:
                return joined, None
    except Exception as exc:
        logger.debug("PyMuPDF fallback failed: %s", exc)

    try:
        from PIL import Image
        import pytesseract

        if mime and mime.startswith("image/") or path.lower().split(".")[-1] in (
            "png",
            "jpg",
            "jpeg",
            "tif",
            "tiff",
            "webp",
            "bmp",
        ):
            lang = os.getenv("PHASE3_OCR_LANG", "eng")
            img = Image.open(path)
            raw = pytesseract.image_to_string(img, lang=lang)
            if raw and raw.strip():
                return raw.strip(), None
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("pytesseract fallback failed: %s", exc)

    return "", "ocr_fallback_empty"


def extract_text_from_file(path: str, *, mime_type: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """
    Returns (extracted_text, error_message). error_message is None on success.
    Uses Kreuzberg with Tesseract when installed; otherwise PyMuPDF / pytesseract fallbacks.
    """
    if not path or not os.path.isfile(path):
        return "", "file_not_found"

    mime = _guess_mime(path, mime_type)

    try:
        from kreuzberg import ExtractionConfig, OcrConfig, extract_file_sync

        lang = os.getenv("PHASE3_OCR_LANG", "eng")
        config = ExtractionConfig(ocr=OcrConfig(backend="tesseract", language=lang))
        result = extract_file_sync(path, config=config)
        content = getattr(result, "content", None)
        if content is None:
            content = getattr(result, "text", None)
        if content is None:
            content = str(result)
        text = (content or "").strip()
        if text:
            return text[:800_000], None
        fb, err = _fallback_extract(path, mime)
        if fb:
            return fb[:800_000], err
        return "", "kreuzberg_returned_empty"
    except ImportError:
        logger.warning("kreuzberg not installed — using OCR fallbacks only (pip install kreuzberg)")
        text, err = _fallback_extract(path, mime)
        if text:
            return text[:800_000], err or None
        return "", "kreuzberg_missing_and_fallback_failed"
    except Exception as exc:
        logger.warning("Kreuzberg extraction failed (%s); trying fallback", exc)
        text, err = _fallback_extract(path, mime)
        if text:
            return text[:800_000], err or str(exc)[:500]
        return "", str(exc)[:2000]
