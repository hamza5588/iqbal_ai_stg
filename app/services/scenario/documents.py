"""Document upload, versioning and processing for Scenario-Based Assessments.

Three logically separate document types share one pipeline:
validate -> store original -> extract per-page text -> chunk with page references ->
embed (best effort) -> store chunks tagged with doc_type / version -> (sample questions:
extract individual questions for teacher review).

A replaced document is kept (is_active = False) so historical evaluations keep pointing at
the version they used; retrieval only ever reads chunks of active, ready documents.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
from datetime import datetime
from typing import List, Optional

from sqlalchemy import update

from app.models.scenario_models import (
    DOC_TYPES,
    ScenarioChunk,
    ScenarioDocument,
    ScenarioSampleQuestion,
)
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError
from app.services.scenario.common import dumps, loads, render_prompt, structured_call, text_key
from app.services.scenario.schemas import ExtractedSampleBatch
from app.utils.db import get_db

logger = logging.getLogger(__name__)

MAX_PDF_MB = int(os.getenv("SBA_MAX_PDF_MB", "50"))
MAX_PAGES = int(os.getenv("SBA_MAX_PAGES", "400"))
CHUNK_CHARS = int(os.getenv("SBA_CHUNK_CHARS", "1100"))
MIN_TEXT_CHARS_PER_PAGE = 25

DOC_TYPE_LABELS = {
    "learning": "Learning content",
    "sample_questions": "Sample questions",
    "reference_answers": "Reference answers",
}


def storage_dir() -> str:
    configured = os.environ.get("SBA_STORAGE_DIR")
    if configured:
        return configured
    try:
        from flask import current_app

        return os.path.join(current_app.instance_path, "scenario_docs")
    except RuntimeError:
        return os.path.join(os.getcwd(), "instance", "scenario_docs")


# ── validation & storage ───────────────────────────────────────────────────────

def validate_pdf(filename: str, data: bytes) -> None:
    if not data:
        raise LMSValidationError("The uploaded file is empty.")
    if not (filename or "").lower().endswith(".pdf"):
        raise LMSValidationError("Only PDF files can be uploaded.")
    if not data[:1024].lstrip().startswith(b"%PDF"):
        raise LMSValidationError("The file is not a valid PDF.")
    size_mb = len(data) / (1024 * 1024)
    if size_mb > MAX_PDF_MB:
        raise LMSValidationError(f"PDF is too large ({size_mb:.1f} MB). The limit is {MAX_PDF_MB} MB.")


def _store(data: bytes, sha: str) -> str:
    os.makedirs(storage_dir(), exist_ok=True)
    path = os.path.join(storage_dir(), f"{sha}.pdf")
    if not os.path.exists(path):
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    return path


def create_document(
    assessment_id: int,
    doc_type: str,
    filename: str,
    data: bytes,
    uploaded_by: int,
    *,
    replaces: Optional[ScenarioDocument] = None,
) -> ScenarioDocument:
    if doc_type not in DOC_TYPES:
        raise LMSValidationError("Unknown document type.")
    validate_pdf(filename, data)
    sha = hashlib.sha256(data).hexdigest()
    db = get_db()
    duplicate = (
        db.query(ScenarioDocument)
        .filter(
            ScenarioDocument.assessment_id == assessment_id,
            ScenarioDocument.doc_type == doc_type,
            ScenarioDocument.sha256 == sha,
            ScenarioDocument.is_active.is_(True),
        )
        .first()
    )
    if duplicate is not None:
        raise LMSValidationError(
            f"This PDF is already uploaded as {DOC_TYPE_LABELS[doc_type].lower()} ({duplicate.filename})."
        )
    path = _store(data, sha)
    version = 1
    if replaces is not None:
        version = int(replaces.version or 1) + 1
    doc = ScenarioDocument(
        assessment_id=assessment_id,
        doc_type=doc_type,
        filename=os.path.basename(filename)[:500],
        storage_path=path,
        sha256=sha,
        size_bytes=len(data),
        version=version,
        replaces_id=replaces.id if replaces is not None else None,
        status="pending",
        uploaded_by=uploaded_by,
    )
    db.add(doc)
    if replaces is not None:
        # The old version stays for history but leaves retrieval immediately.
        replaces.is_active = False
        db.query(ScenarioSampleQuestion).filter(ScenarioSampleQuestion.document_id == replaces.id).update(
            {"is_active": False}, synchronize_session=False
        )
    db.commit()
    db.refresh(doc)
    return doc


def get_document(document_id: int) -> ScenarioDocument:
    doc = get_db().query(ScenarioDocument).filter(ScenarioDocument.id == document_id).first()
    if doc is None:
        raise LMSNotFoundError("Document not found")
    return doc


def deactivate_document(doc: ScenarioDocument) -> None:
    """'Delete' keeps the row and file (historical evaluations reference them)."""
    db = get_db()
    doc.is_active = False
    db.query(ScenarioSampleQuestion).filter(ScenarioSampleQuestion.document_id == doc.id).update(
        {"is_active": False}, synchronize_session=False
    )
    db.commit()


def serialize_document(doc: ScenarioDocument) -> dict:
    return {
        "id": doc.id,
        "doc_type": doc.doc_type,
        "doc_type_label": DOC_TYPE_LABELS.get(doc.doc_type, doc.doc_type),
        "filename": doc.filename,
        "size_bytes": doc.size_bytes,
        "page_count": doc.page_count,
        "version": doc.version,
        "replaces_id": doc.replaces_id,
        "is_active": bool(doc.is_active),
        "status": doc.status,
        "error_message": doc.error_message,
        "warnings": loads(doc.warnings_json, []),
        "chunk_count": doc.chunk_count,
        "embedded": bool(doc.embedded),
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "processed_at": doc.processed_at.isoformat() if doc.processed_at else None,
    }


# ── extraction & chunking ──────────────────────────────────────────────────────

def extract_pages(data: bytes) -> List[str]:
    import fitz  # PyMuPDF

    try:
        pdf = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise LMSValidationError("The PDF could not be opened (it may be corrupted).") from exc
    try:
        if pdf.needs_pass:
            raise LMSValidationError("The PDF is password-protected. Upload an unlocked copy.")
        if pdf.page_count == 0:
            raise LMSValidationError("The PDF has no pages.")
        if pdf.page_count > MAX_PAGES:
            raise LMSValidationError(f"The PDF has {pdf.page_count} pages; the limit is {MAX_PAGES}.")
        return [page.get_text("text") or "" for page in pdf]
    finally:
        pdf.close()


_HEADING_RE = re.compile(r"^(?:\d+(?:\.\d+)*[.)]?\s+)?[A-Z][^.!?]{2,90}$")


def _is_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 90 or s.endswith((".", ",", ";")):
        return False
    words = s.split()
    if len(words) > 12:
        return False
    return bool(_HEADING_RE.match(s)) and (s.isupper() or sum(w[:1].isupper() for w in words) >= max(1, len(words) // 2))


def chunk_pages(pages: List[str], max_chars: int = CHUNK_CHARS) -> List[dict]:
    """Paragraph-aware chunks that keep page_start/page_end and the nearest heading."""
    chunks: List[dict] = []
    buf: List[str] = []
    buf_len = 0
    start_page = None
    end_page = None
    heading = None
    buf_heading = None

    def flush():
        nonlocal buf, buf_len, start_page, end_page, buf_heading
        text = "\n".join(buf).strip()
        if len(text) >= 40:
            chunks.append(
                {"text": text, "page_start": start_page, "page_end": end_page, "heading": buf_heading}
            )
        buf, buf_len, start_page, end_page, buf_heading = [], 0, None, None, None

    for page_no, raw in enumerate(pages, start=1):
        text = raw.replace("\r", "")
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        if len(paragraphs) <= 1:
            paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
            # Re-join layout-wrapped lines into paragraphs of a few hundred chars.
            merged, cur = [], ""
            for line in paragraphs:
                if _is_heading(line):
                    if cur:
                        merged.append(cur)
                    merged.append(line)
                    cur = ""
                    continue
                cur = f"{cur} {line}".strip() if cur else line
                if len(cur) > 350 and line.endswith((".", "?", "!", ":")):
                    merged.append(cur)
                    cur = ""
            if cur:
                merged.append(cur)
            paragraphs = merged
        for para in paragraphs:
            para = re.sub(r"[ \t]+", " ", para)
            first = para.split("\n", 1)[0]
            if _is_heading(first) and len(para) < 120:
                heading = first.strip()
                if buf_len > max_chars * 0.5:
                    flush()
            if buf_len + len(para) > max_chars and buf:
                flush()
            if start_page is None:
                start_page = page_no
                buf_heading = heading
            end_page = page_no
            buf.append(para)
            buf_len += len(para) + 1
    if buf:
        flush()
    for i, c in enumerate(chunks):
        c["chunk_index"] = i
    return chunks


def _embed(texts: List[str]) -> Optional[List[List[float]]]:
    if os.getenv("SBA_DISABLE_EMBEDDINGS", "").lower() in ("1", "true", "yes"):
        return None
    try:
        from app.utils.rag_service import get_rag_embeddings

        vectors = get_rag_embeddings().embed_documents(texts)
        return [[round(float(x), 5) for x in v] for v in vectors]
    except Exception as exc:  # noqa: BLE001 - lexical retrieval still works
        logger.warning("SBA embedding failed, falling back to lexical retrieval: %s", exc)
        return None


# ── sample-question extraction ─────────────────────────────────────────────────

_Q_START = re.compile(r"^\s*(?:Q(?:uestion)?\s*)?(\d{1,3})\s*[.):\-]\s+(.*)", re.IGNORECASE)


def _regex_samples(pages: List[str]) -> List[dict]:
    items: List[dict] = []
    cur = None
    for page_no, text in enumerate(pages, start=1):
        for line in text.splitlines():
            m = _Q_START.match(line)
            if m:
                if cur:
                    items.append(cur)
                cur = {"text": m.group(2).strip(), "page": page_no}
            elif cur and line.strip():
                cur["text"] += " " + line.strip()
    if cur:
        items.append(cur)
    return [i for i in items if len(i["text"]) >= 25]


def extract_samples(pages: List[str]) -> tuple[List[dict], List[str]]:
    warnings: List[str] = []
    marked = "\n".join(f"[Page {i}]\n{t.strip()}" for i, t in enumerate(pages, start=1) if t.strip())
    items: List[dict] = []
    if marked:
        try:
            batch, _ = structured_call(
                ExtractedSampleBatch,
                render_prompt("sample_extraction", text=marked[:24000]),
                description="sba sample question extraction",
            )
            items = [q.model_dump() for q in batch.questions if len((q.text or "").strip()) >= 15]
            if len(marked) > 24000:
                warnings.append("Only the first ~24,000 characters were scanned for sample questions.")
        except Exception as exc:  # noqa: BLE001
            logger.warning("SBA sample extraction via LLM failed: %s", exc)
            warnings.append("AI extraction was unavailable; questions were split by their numbering.")
    if not items:
        items = _regex_samples(pages)
    return items, warnings


# ── processing ─────────────────────────────────────────────────────────────────

def claim_for_processing(document_id: int) -> bool:
    """Atomically move pending/failed -> processing so two workers never process one doc."""
    db = get_db()
    res = db.execute(
        update(ScenarioDocument)
        .where(ScenarioDocument.id == document_id, ScenarioDocument.status.in_(("pending", "failed")))
        .values(status="processing", error_message=None, processed_at=datetime.utcnow())
    )
    db.commit()
    return bool(res.rowcount)


def process_document(document_id: int) -> ScenarioDocument:
    if not claim_for_processing(document_id):
        return get_document(document_id)
    db = get_db()
    doc = get_document(document_id)
    try:
        with open(doc.storage_path, "rb") as fh:
            data = fh.read()
        pages = extract_pages(data)
        doc.page_count = len(pages)
        warnings: List[str] = []
        text_pages = [p for p in pages if len(p.strip()) >= MIN_TEXT_CHARS_PER_PAGE]
        if not text_pages:
            raise LMSValidationError(
                "No readable text was found. This looks like a scanned or image-only PDF; "
                "OCR is not available for assessment documents, so upload a text-based PDF."
            )
        empty = len(pages) - len(text_pages)
        if empty:
            warnings.append(f"{empty} of {len(pages)} pages had no readable text (scanned images are skipped).")

        chunks = chunk_pages(pages)
        if not chunks:
            raise LMSValidationError("The PDF text was too short to use.")
        vectors = _embed([c["text"] for c in chunks])
        if vectors is None:
            warnings.append("Semantic embeddings unavailable; keyword retrieval is used for this document.")

        db.query(ScenarioChunk).filter(ScenarioChunk.document_id == doc.id).delete(synchronize_session=False)
        for i, c in enumerate(chunks):
            db.add(
                ScenarioChunk(
                    document_id=doc.id,
                    assessment_id=doc.assessment_id,
                    doc_type=doc.doc_type,
                    doc_version=doc.version,
                    page_start=c["page_start"],
                    page_end=c["page_end"],
                    chunk_index=c["chunk_index"],
                    heading=(c.get("heading") or None) and c["heading"][:500],
                    text=c["text"],
                    embedding_json=dumps(vectors[i]) if vectors else None,
                )
            )

        if doc.doc_type == "sample_questions":
            samples, sample_warnings = extract_samples(pages)
            warnings.extend(sample_warnings)
            db.query(ScenarioSampleQuestion).filter(ScenarioSampleQuestion.document_id == doc.id).delete(
                synchronize_session=False
            )
            seen = set()
            for s in samples:
                key = text_key(s["text"])
                if key in seen:
                    continue
                seen.add(key)
                db.add(
                    ScenarioSampleQuestion(
                        document_id=doc.id,
                        assessment_id=doc.assessment_id,
                        text=s["text"].strip()[:4000],
                        page=s.get("page"),
                        question_type=(s.get("question_type") or None),
                        objective=(s.get("objective") or None),
                        difficulty=s.get("difficulty") if s.get("difficulty") in ("easy", "medium", "hard") else None,
                        dedupe_key=key,
                    )
                )
            if not seen:
                warnings.append("No individual questions could be identified in this document.")

        doc.chunk_count = len(chunks)
        doc.embedded = vectors is not None
        doc.warnings_json = dumps(warnings)
        doc.status = "ready"
        doc.error_message = None
        doc.processed_at = datetime.utcnow()
        db.commit()
    except Exception as exc:  # noqa: BLE001 - every failure is recorded on the row
        db.rollback()
        doc = get_document(document_id)
        doc.status = "failed"
        doc.error_message = str(exc) if isinstance(exc, LMSValidationError) else f"Processing failed: {exc}"
        doc.processed_at = datetime.utcnow()
        db.commit()
        logger.warning("SBA document %s failed: %s", document_id, exc)
    return doc


def process_in_background(document_id: int) -> None:
    from flask import current_app

    if os.getenv("SBA_SYNC_PROCESSING", "").lower() in ("1", "true", "yes"):
        process_document(document_id)
        return
    app_obj = current_app._get_current_object()

    def run():
        with app_obj.app_context():
            try:
                process_document(document_id)
            except Exception:  # noqa: BLE001
                logger.exception("SBA background processing crashed for document %s", document_id)

    threading.Thread(target=run, name=f"sba-doc-{document_id}", daemon=True).start()
