"""Apply OCR to a student-owned upload row (used by Celery and synchronous analyze)."""
from __future__ import annotations

import logging
import os

from sqlalchemy.orm import Session

from app.models.phase3_models import StudentOwnedUpload
from app.services.phase3.document_ocr_service import extract_text_from_file

logger = logging.getLogger(__name__)


def run_ocr_for_upload_id(db: Session, upload_id: int) -> None:
    row = db.query(StudentOwnedUpload).filter(StudentOwnedUpload.id == int(upload_id)).first()
    if not row:
        logger.warning("run_ocr_for_upload_id: upload %s not found", upload_id)
        return
    path = row.storage_path
    if not path or not os.path.isfile(path):
        row.ocr_status = "failed"
        row.ai_notes = "file_not_found"
        db.commit()
        return

    text, err = extract_text_from_file(path, mime_type=row.mime_type)
    if text:
        row.ocr_extracted_text = text[:500_000]
        row.ocr_status = "completed"
        row.ai_notes = None
    else:
        row.ocr_extracted_text = None
        row.ocr_status = "failed"
        row.ai_notes = (err or "ocr_failed")[:8000]
    db.commit()
    db.refresh(row)
