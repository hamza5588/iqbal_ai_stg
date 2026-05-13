"""Background processors for Phase 3 (OCR, optional Celery hooks)."""
from __future__ import annotations

import logging

from app.celery_app import celery

logger = logging.getLogger(__name__)


@celery.task(name="phase3.fanout_learning_event")
def fanout_learning_event_task(event_id: int) -> str:
    """Secondary hook after persistence (Redis publish happens inline in emit_learning_event)."""
    logger.debug("fanout_learning_event_task ack event_id=%s", event_id)
    return "ok"


@celery.task(name="phase3.ocr_student_upload")
def ocr_student_upload_task(upload_id: int) -> str:
    """Run Kreuzberg/Tesseract OCR for a student upload."""
    from app.utils.db import get_db

    db = get_db()
    try:
        from app.services.phase3.student_upload_ocr import run_ocr_for_upload_id

        run_ocr_for_upload_id(db, upload_id)
        return "ok"
    except Exception as exc:
        logger.exception("ocr_student_upload_task failed upload_id=%s: %s", upload_id, exc)
        try:
            from app.models.phase3_models import StudentOwnedUpload

            row = db.query(StudentOwnedUpload).filter(StudentOwnedUpload.id == int(upload_id)).first()
            if row:
                row.ocr_status = "failed"
                row.ai_notes = str(exc)[:8000]
                db.commit()
        except Exception:
            db.rollback()
        raise
