"""Celery tasks for PDF → MCQ quiz pipeline."""
from __future__ import annotations

import logging
import os
import tempfile
import uuid
from datetime import datetime

from app.celery_app import celery
from app.services.quiz.pipeline import run_pdf_quiz_pipeline

logger = logging.getLogger(__name__)


def _celery_async_enabled() -> bool:
    """True only when USE_CELERY_FOR_INGESTION is set (production/staging)."""
    try:
        from flask import has_app_context, current_app

        if has_app_context():
            return bool(current_app.config.get("USE_CELERY_FOR_INGESTION", False))
    except Exception:
        pass
    from app.config import Config

    return bool(Config.USE_CELERY_FOR_INGESTION)


def _new_lms_thread_id(user_id: int) -> str:
    return f"user_{user_id}_lms_{int(datetime.utcnow().timestamp())}_{uuid.uuid4().hex[:8]}"


@celery.task(bind=True, name="app.tasks.quiz_pdf_tasks.process_pdf_quiz_task", queue="default")
def process_pdf_quiz_task(
    self,
    assessment_id: int,
    file_path: str,
    filename: str,
    user_id: int,
    topic_id: int | None = None,
    thread_id: str | None = None,
):
    """Ingest PDF then run PDF→MCQ pipeline for an assessment."""
    self.update_state(state="PROCESSING", meta={"step": "ingest", "progress": 10, "message": "Ingesting PDF..."})

    thread_id = thread_id or _new_lms_thread_id(user_id)
    try:
        with open(file_path, "rb") as f:
            file_bytes = f.read()
    finally:
        try:
            os.unlink(file_path)
        except OSError:
            pass

    def progress_callback(step: str, progress: int, message: str):
        try:
            self.update_state(
                state="PROCESSING",
                meta={"step": step, "progress": progress, "message": message},
            )
        except Exception as exc:
            logger.warning("Failed to update quiz task progress: %s", exc)

    from app.utils.rag_service import ingest_pdf

    ingest_pdf(
        file_bytes=file_bytes,
        thread_id=thread_id,
        filename=filename,
        progress_callback=progress_callback,
        user_id=user_id,
    )

    self.update_state(
        state="PROCESSING",
        meta={"step": "convert", "progress": 60, "message": "Converting to MCQs..."},
    )

    result = run_pdf_quiz_pipeline(
        assessment_id=assessment_id,
        rag_thread_id=thread_id,
        user_id=user_id,
        topic_id=topic_id,
    )
    return {"success": True, **result}


def enqueue_or_run_pdf_quiz(
    assessment_id: int,
    file_bytes: bytes,
    filename: str,
    user_id: int,
    topic_id: int | None = None,
    async_mode: bool = True,
    progress_job_id: str | None = None,
) -> dict:
    """
    Save file to temp path and enqueue Celery task, or run synchronously if async unavailable.
    Local dev (USE_CELERY_FOR_INGESTION=false) always runs in-process — no Redis required.
    """
    if async_mode and not _celery_async_enabled():
        async_mode = False

    thread_id = _new_lms_thread_id(user_id)
    suffix = os.path.splitext(filename)[1] or ".pdf"
    fd, temp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    with open(temp_path, "wb") as f:
        f.write(file_bytes)

    if async_mode:
        try:
            task = process_pdf_quiz_task.delay(
                assessment_id=assessment_id,
                file_path=temp_path,
                filename=filename,
                user_id=user_id,
                topic_id=topic_id,
                thread_id=thread_id,
            )
            return {"async": True, "task_id": task.id, "thread_id": thread_id, "assessment_id": assessment_id}
        except Exception as exc:
            logger.warning("Celery unavailable, running PDF quiz pipeline synchronously: %s", exc)

    from app.utils.rag_service import ingest_pdf
    from app.utils.diagnostic_upload_progress import set_progress as _set_upload_progress

    _set_upload_progress(progress_job_id, 58, "Ingesting diagnostic Q&A PDF...", stage="qa_ingest")

    def _qa_progress(step: str, progress: int, message: str) -> None:
        mapped = 58 + int(max(0, min(100, progress)) * 0.12)
        _set_upload_progress(progress_job_id, mapped, message or "Ingesting diagnostic Q&A PDF...", stage="qa_ingest")

    ingest_pdf(
        file_bytes=file_bytes,
        thread_id=thread_id,
        filename=filename,
        user_id=user_id,
        progress_callback=_qa_progress if progress_job_id else None,
    )
    _set_upload_progress(progress_job_id, 72, "Extracting questions and answers...", stage="qa_extract")
    result = run_pdf_quiz_pipeline(
        assessment_id=assessment_id,
        rag_thread_id=thread_id,
        user_id=user_id,
        topic_id=topic_id,
    )
    _set_upload_progress(progress_job_id, 88, "Saving generated questions...", stage="qa_save")
    try:
        os.unlink(temp_path)
    except OSError:
        pass
    return {"async": False, **result}
