"""LlamaParse PDF text extraction for quiz / diagnostic uploads.

Flow: PDF bytes → LlamaParse (markdown) → caller feeds text to LLM MCQ path.
Requires ``LLAMA_CLOUD_API_KEY`` (already loaded via app config dotenv).
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)


def llamaparse_enabled() -> bool:
    raw = (os.environ.get("QUIZ_LLAMAPARSE") or "1").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool((os.environ.get("LLAMA_CLOUD_API_KEY") or "").strip())


def extract_pdf_text_llamaparse(
    pdf_bytes: bytes,
    *,
    filename: Optional[str] = None,
) -> str:
    """Extract markdown/text from a PDF via Llama Cloud LlamaParse.

    Raises ``ValueError`` / ``RuntimeError`` when the API key is missing, the
    package is not installed, or the parse returns empty text.
    """
    if not pdf_bytes:
        raise ValueError("No PDF bytes provided for LlamaParse.")

    api_key = (os.environ.get("LLAMA_CLOUD_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "LLAMA_CLOUD_API_KEY is not set. Add it to the environment to use LlamaParse."
        )

    try:
        from llama_parse import LlamaParse
    except ImportError as exc:
        raise RuntimeError(
            "llama-parse is not installed. Run: pip install llama-parse"
        ) from exc

    # Prefer markdown so equations / structure survive for the LLM MCQ step.
    result_type = (os.environ.get("QUIZ_LLAMAPARSE_RESULT") or "markdown").strip()
    parser = LlamaParse(
        api_key=api_key,
        result_type=result_type if result_type in {"markdown", "text"} else "markdown",
        verbose=False,
    )

    suffix = ".pdf"
    if filename and "." in filename:
        ext = os.path.splitext(filename)[1]
        if ext:
            suffix = ext

    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        with open(temp_path, "wb") as fh:
            fh.write(pdf_bytes)

        documents = parser.load_data(temp_path)
        parts: list[str] = []
        for doc in documents or []:
            chunk = (getattr(doc, "text", None) or str(doc) or "").strip()
            if chunk:
                parts.append(chunk)
        text = "\n\n".join(parts).strip()
        if not text:
            raise RuntimeError("LlamaParse returned empty text for this PDF.")
        logger.info(
            "LlamaParse extracted %s chars from %s (%s docs)",
            len(text),
            filename or "upload.pdf",
            len(parts),
        )
        return text
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
