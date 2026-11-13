"""
Admin PDF Extraction Inspector — text extraction only.

Reuses the same loader cascade, hybrid math pass, invisible-text strip,
sanitize step, and pdfplumber table addendum that `ingest_pdf` uses for
Lesson/Quiz RAG. Does **not** write chunks, vectors, durable uploads, or
markdown exports — this tool is inspection-only.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# In-memory registry of temp PDFs for the left-panel viewer (token → meta).
_PDF_LOCK = threading.Lock()
_PDF_STORE: Dict[str, Dict[str, Any]] = {}
_PDF_TTL_SECONDS = 3600
_INSPECTOR_TMP_ROOT = Path(
    os.getenv("ADMIN_PDF_INSPECTOR_TMP_DIR")
    or (Path(tempfile.gettempdir()) / "iqbalai_pdf_inspector")
)


def _ensure_tmp_root() -> Path:
    _INSPECTOR_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    return _INSPECTOR_TMP_ROOT


def cleanup_expired_pdfs(now: Optional[float] = None) -> None:
    """Remove expired inspector PDF temp files from disk and memory."""
    now = now if now is not None else time.time()
    with _PDF_LOCK:
        expired = [
            token
            for token, meta in _PDF_STORE.items()
            if now - float(meta.get("created_at", 0)) > _PDF_TTL_SECONDS
        ]
        for token in expired:
            meta = _PDF_STORE.pop(token, None)
            if not meta:
                continue
            path = meta.get("path")
            if path and os.path.isfile(path):
                try:
                    os.unlink(path)
                except OSError as exc:
                    logger.debug("Could not delete expired inspector PDF %s: %s", path, exc)


def store_inspector_pdf(
    *,
    file_bytes: bytes,
    filename: str,
    user_id: int,
) -> str:
    """Persist PDF bytes for browser preview; return opaque token."""
    cleanup_expired_pdfs()
    token = uuid.uuid4().hex
    root = _ensure_tmp_root() / str(user_id)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{token}.pdf"
    path.write_bytes(file_bytes)
    with _PDF_LOCK:
        _PDF_STORE[token] = {
            "path": str(path),
            "user_id": int(user_id),
            "filename": filename,
            "created_at": time.time(),
        }
    return token


def resolve_inspector_pdf(token: str, user_id: int) -> Optional[Tuple[str, str]]:
    """Return (path, filename) if token is valid for this admin user."""
    cleanup_expired_pdfs()
    with _PDF_LOCK:
        meta = _PDF_STORE.get(token)
        if not meta:
            return None
        if int(meta.get("user_id", -1)) != int(user_id):
            return None
        path = meta.get("path")
        if not path or not os.path.isfile(path):
            _PDF_STORE.pop(token, None)
            return None
        return str(path), str(meta.get("filename") or "document.pdf")


def release_inspector_pdf(token: str, user_id: int) -> None:
    """Delete a stored preview PDF early (e.g. after a new upload)."""
    with _PDF_LOCK:
        meta = _PDF_STORE.get(token)
        if not meta or int(meta.get("user_id", -1)) != int(user_id):
            return
        _PDF_STORE.pop(token, None)
        path = meta.get("path")
    if path and os.path.isfile(path):
        try:
            os.unlink(path)
        except OSError:
            pass


def extract_pdf_text_like_ingest(
    file_bytes: bytes,
    *,
    filename: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run the same text-extraction stages as `ingest_pdf` (without indexing).

    Returns pages, full text, method name, timings, and warnings.
    """
    from langchain_community.document_loaders import PyPDFLoader

    try:
        from langchain_community.document_loaders import PyMuPDFLoader

        pymupdf_available = True
    except ImportError:
        PyMuPDFLoader = None  # type: ignore
        pymupdf_available = False

    try:
        from langchain_community.document_loaders import PDFPlumberLoader

        pdfplumber_available = True
    except ImportError:
        PDFPlumberLoader = None  # type: ignore
        pdfplumber_available = False

    from app.utils.rag_service import _sanitize_pdf_text

    if not file_bytes:
        raise ValueError("No PDF bytes received.")

    start = time.time()
    warnings: List[str] = []
    safe_filename = filename or "document.pdf"
    safe_filename = "".join(c for c in safe_filename if c.isalnum() or c in "._- ") or "document.pdf"

    temp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
            temp_file.write(file_bytes)
            temp_path = temp_file.name

        docs = None
        loader_used = None
        last_error = None

        # Try 1: PyPDFLoader (same order as ingest_pdf)
        try:
            loader = PyPDFLoader(temp_path)
            docs = loader.load()
            loader_used = "PyPDFLoader"
        except Exception as e1:
            last_error = str(e1)
            logger.warning("Inspector PyPDFLoader failed: %s", last_error)
            docs = None

        # Try 2: PyMuPDFLoader
        if not docs or (
            docs and len([d for d in docs if d.page_content and d.page_content.strip()]) == 0
        ):
            if pymupdf_available and PyMuPDFLoader is not None:
                try:
                    loader = PyMuPDFLoader(temp_path)
                    docs = loader.load()
                    loader_used = "PyMuPDFLoader"
                except Exception as e2:
                    last_error = str(e2)
                    logger.warning("Inspector PyMuPDFLoader failed: %s", last_error)
                    if not docs:
                        docs = None

        # Try 3: PDFPlumberLoader
        if not docs or (
            docs and len([d for d in docs if d.page_content and d.page_content.strip()]) == 0
        ):
            if pdfplumber_available and PDFPlumberLoader is not None:
                try:
                    loader = PDFPlumberLoader(temp_path)
                    docs = loader.load()
                    loader_used = "PDFPlumberLoader"
                except Exception as e3:
                    last_error = str(e3)
                    logger.warning("Inspector PDFPlumberLoader failed: %s", last_error)
                    if not docs:
                        docs = None

        if not docs:
            error_msg = (
                "Failed to load PDF with all available loaders (PyPDFLoader"
            )
            if pymupdf_available:
                error_msg += ", PyMuPDFLoader"
            if pdfplumber_available:
                error_msg += ", PDFPlumberLoader"
            error_msg += (
                "). The PDF might be corrupted, password-protected, or image-based (scanned). "
                "For scanned PDFs, OCR support is required."
            )
            if last_error:
                error_msg += f" Last error: {last_error}"
            raise ValueError(error_msg)

        num_pages = len(docs)
        if num_pages == 0:
            raise ValueError("PDF appears to be empty or could not be loaded. No pages found.")

        valid_pages = [d for d in docs if d.page_content and d.page_content.strip()]
        if len(valid_pages) < num_pages:
            missing_page_count = num_pages - len(valid_pages)
            if missing_page_count / num_pages >= 0.3:
                warnings.append(
                    f"Only {len(valid_pages)} of {num_pages} pages in this PDF had readable text — "
                    f"the other {missing_page_count} came back empty. This can happen with a damaged, "
                    "truncated, or partially corrupted file, so the document may be missing content."
                )

        if len(valid_pages) == 0:
            scanned_hint = ""
            try:
                import fitz

                pdf_doc = fitz.open(temp_path)
                has_images = any(len(page.get_images()) > 0 for page in pdf_doc)
                pdf_doc.close()
                if has_images:
                    scanned_hint = (
                        " This PDF appears to contain images and might be scanned. "
                        "OCR support is required for scanned documents."
                    )
            except Exception:
                pass
            raise ValueError(
                f"PDF loaded with {loader_used} but contains no extractable text content.{scanned_hint}"
            )

        # Hybrid math pages (same as ingest_pdf when no LlamaParse preextract)
        try:
            from app.utils.pdf_hybrid_text import hybrid_page_texts

            better_pages = hybrid_page_texts(temp_path)
            replaced = 0
            for i, doc in enumerate(docs):
                page_idx = doc.metadata.get("page", i) if isinstance(doc.metadata, dict) else i
                if not isinstance(page_idx, int):
                    page_idx = i
                better = better_pages.get(page_idx)
                if better and better.strip():
                    doc.page_content = better
                    replaced += 1
            if replaced:
                loader_used = f"{loader_used}+hybrid"
                logger.info(
                    "Inspector: hybrid text used for %d of %d page(s)",
                    replaced,
                    len(docs),
                )
        except Exception as hybrid_exc:  # noqa: BLE001
            logger.warning("Inspector hybrid PDF page reading skipped: %s", hybrid_exc)
            warnings.append(f"Hybrid math page reading skipped: {hybrid_exc}")

        # Invisible text strip + mixed content warning (same as ingest_pdf)
        try:
            import fitz

            pdf_doc = fitz.open(temp_path)
            try:
                has_images = any(len(page.get_images()) > 0 for page in list(pdf_doc)[:25])
                invisible_text_hits = 0
                for page_index, page in enumerate(pdf_doc):
                    if page_index >= len(docs):
                        break
                    try:
                        traces = page.get_texttrace()
                    except Exception:
                        continue
                    invisible_strings = []
                    for trace in traces:
                        if trace.get("type") in (3, 7):
                            chars = trace.get("chars") or []
                            hidden_text = "".join(chr(c[0]) for c in chars if c and c[0])
                            if hidden_text.strip():
                                invisible_strings.append(hidden_text)
                    if invisible_strings and docs[page_index].page_content:
                        content = docs[page_index].page_content
                        for hidden_text in invisible_strings:
                            if hidden_text in content:
                                content = content.replace(hidden_text, " ")
                                invisible_text_hits += 1
                        docs[page_index].page_content = content
                if has_images:
                    warnings.append(
                        "This PDF contains images as well as text. The text has been "
                        "extracted, but the images were not — compare text content only."
                    )
                if invisible_text_hits:
                    warnings.append(
                        f"Stripped invisible/hidden PDF text at {invisible_text_hits} location(s)."
                    )
            finally:
                pdf_doc.close()
        except Exception as e:
            logger.debug("Inspector mixed/invisible text check skipped: %s", e)

        for doc in docs:
            if doc.page_content:
                doc.page_content = _sanitize_pdf_text(doc.page_content)

        # pdfplumber table addendum (same as ingest_pdf)
        if pdfplumber_available:
            try:
                import pdfplumber

                with pdfplumber.open(temp_path) as plumber_pdf:
                    for page_index, plumber_page in enumerate(plumber_pdf.pages):
                        if page_index >= len(docs):
                            break
                        try:
                            tables = plumber_page.extract_tables()
                        except Exception:
                            continue
                        if not tables:
                            continue
                        table_blocks = []
                        for table in tables:
                            rows = [r for r in table if r and any((c or "").strip() for c in r)]
                            if len(rows) < 2:
                                continue
                            header, body_rows = rows[0], rows[1:]
                            header_cells = [(c or "").strip() for c in header]
                            lines = [
                                "| " + " | ".join(header_cells) + " |",
                                "| " + " | ".join(["---"] * len(header_cells)) + " |",
                            ]
                            for row in body_rows:
                                cells = [(c or "").strip() for c in row]
                                if len(cells) < len(header_cells):
                                    cells += [""] * (len(header_cells) - len(cells))
                                lines.append(
                                    "| " + " | ".join(cells[: len(header_cells)]) + " |"
                                )
                            table_blocks.append("\n".join(lines))
                        if table_blocks:
                            addendum = (
                                "\n\n[Table data, extracted with row/column structure]\n"
                                + "\n\n".join(table_blocks)
                            )
                            docs[page_index].page_content = (
                                docs[page_index].page_content or ""
                            ) + addendum
            except Exception as e:
                logger.debug("Inspector table-aware extraction skipped: %s", e)

        pages: List[Dict[str, Any]] = []
        full_parts: List[str] = []
        for i, doc in enumerate(docs):
            page_num = i + 1
            text = doc.page_content or ""
            pages.append(
                {
                    "page_number": page_num,
                    "text": text,
                    "char_count": len(text),
                }
            )
            full_parts.append(f"## Page {page_num}\n\n{text}")

        full_text = "\n\n".join(full_parts)
        elapsed = round(time.time() - start, 3)

        return {
            "filename": safe_filename,
            "num_pages": num_pages,
            "char_count": len(full_text),
            "processing_time_seconds": elapsed,
            "extraction_method": loader_used,
            "warnings": warnings,
            "full_text": full_text,
            "pages": pages,
        }
    finally:
        if temp_path and os.path.isfile(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def wipe_user_inspector_files(user_id: int) -> None:
    """Remove all inspector temp PDFs for a user (best-effort)."""
    with _PDF_LOCK:
        tokens = [
            t for t, m in _PDF_STORE.items() if int(m.get("user_id", -1)) == int(user_id)
        ]
        paths = []
        for token in tokens:
            meta = _PDF_STORE.pop(token, None)
            if meta and meta.get("path"):
                paths.append(meta["path"])
    for path in paths:
        try:
            if os.path.isfile(path):
                os.unlink(path)
        except OSError:
            pass
    user_dir = _INSPECTOR_TMP_ROOT / str(user_id)
    if user_dir.is_dir():
        try:
            shutil.rmtree(user_dir, ignore_errors=True)
        except Exception:
            pass
