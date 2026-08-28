"""Celery tasks for PDF → MCQ quiz pipeline."""
from __future__ import annotations

import logging
import os
import tempfile
import uuid
from datetime import datetime

from app.celery_app import celery
from app.services.quiz.pipeline import run_pdf_quiz_pipeline
from app.utils.rag_service import ingest_pdf

logger = logging.getLogger(__name__)


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
) -> dict:
    """
    Save file to temp path and enqueue Celery task, or run synchronously if async unavailable.
    """
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

    ingest_pdf(file_bytes=file_bytes, thread_id=thread_id, filename=filename, user_id=user_id)
    result = run_pdf_quiz_pipeline(
        assessment_id=assessment_id,
        rag_thread_id=thread_id,
        user_id=user_id,
        topic_id=topic_id,
    )
    try:
        os.unlink(temp_path)
    except OSError:
        pass
    return {"async": False, **result}
