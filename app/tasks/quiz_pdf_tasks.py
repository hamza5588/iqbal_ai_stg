"""Celery tasks for PDF → MCQ quiz pipeline."""
from __future__ import annotations

import logging
import os
import tempfile
import uuid
from datetime import datetime
from typing import Optional

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


def _shared_upload_dir() -> str:
    """Directory visible to both web and Celery containers (docker volume ./tmp:/app/tmp)."""
    candidates = [
        os.environ.get("PDF_QUIZ_TMP_DIR"),
        "/app/tmp/pdf_quiz_uploads",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "tmp", "pdf_quiz_uploads")),
    ]
    for path in candidates:
        if not path:
            continue
        try:
            os.makedirs(path, exist_ok=True)
            return path
        except OSError:
            continue
    fallback = tempfile.gettempdir()
    os.makedirs(fallback, exist_ok=True)
    return fallback


def _write_shared_temp_file(file_bytes: bytes, filename: str) -> str:
    suffix = os.path.splitext(filename)[1] or ".pdf"
    fd, temp_path = tempfile.mkstemp(suffix=suffix, dir=_shared_upload_dir())
    os.close(fd)
    with open(temp_path, "wb") as f:
        f.write(file_bytes)
    return temp_path


def _try_llamaparse_text(
    file_bytes: bytes,
    filename: str,
    *,
    progress_callback=None,
) -> Optional[str]:
    """Extract PDF text with LlamaParse when enabled; None on skip/failure."""
    try:
        from app.services.quiz.llamaparse_extract import (
            extract_pdf_text_llamaparse,
            llamaparse_enabled,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("LlamaParse import failed: %s", exc)
        return None

    if not llamaparse_enabled():
        return None

    if progress_callback:
        try:
            progress_callback("extract", 12, "Extracting PDF with LlamaParse...")
        except Exception:
            pass

    try:
        text = extract_pdf_text_llamaparse(file_bytes, filename=filename)
        if text and text.strip():
            logger.info("LlamaParse OK for %s (%s chars)", filename, len(text))
            return text.strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "LlamaParse failed for %s; falling back to local PDF loaders: %s",
            filename,
            exc,
        )
    return None


@celery.task(bind=True, name="app.tasks.quiz_pdf_tasks.process_pdf_quiz_task", queue="default")
def process_pdf_quiz_task(
    self,
    assessment_id: int,
    file_path: str,
    filename: str,
    user_id: int,
    topic_id: int | None = None,
    thread_id: str | None = None,
    question_count: int | None = None,
):
    """Ingest PDF then run PDF→MCQ pipeline for an assessment."""
    self.update_state(state="PROCESSING", meta={"step": "ingest", "progress": 10, "message": "Ingesting PDF..."})

    thread_id = thread_id or _new_lms_thread_id(user_id)
    from app.services.lms import assessment_service
    from app.services.quiz.pipeline import _set_pdf_source_status

    source = assessment_service.link_pdf_source(
        assessment_id=assessment_id,
        rag_thread_id=thread_id,
        original_filename=filename,
    )
    _set_pdf_source_status(source.id, "processing")
    try:
        with open(file_path, "rb") as f:
            file_bytes = f.read()

        def progress_callback(step: str, progress: int, message: str):
            try:
                self.update_state(
                    state="PROCESSING",
                    meta={"step": step, "progress": progress, "message": message},
                )
            except Exception as exc:
                logger.warning("Failed to update quiz task progress: %s", exc)

        from app.utils.rag_service import ingest_pdf

        # Primary path: LlamaParse → text → ingest + LLM MCQ pipeline → FE.
        llamaparse_text = _try_llamaparse_text(
            file_bytes, filename, progress_callback=progress_callback
        )

        ingest_pdf(
            file_bytes=file_bytes,
            thread_id=thread_id,
            filename=filename,
            progress_callback=progress_callback,
            user_id=user_id,
            preextracted_text=llamaparse_text,
        )

        self.update_state(
            state="PROCESSING",
            meta={"step": "convert", "progress": 60, "message": "Converting to MCQs..."},
        )

        def pipeline_progress(step: str, progress: int, message: str):
            # Map pipeline 60–100 onto celery meta already past ingest.
            mapped = max(60, min(99, progress))
            try:
                self.update_state(
                    state="PROCESSING",
                    meta={"step": step, "progress": mapped, "message": message},
                )
            except Exception as exc:
                logger.warning("Failed to update quiz pipeline progress: %s", exc)

        # Always pass PDF bytes so hybrid vision (text + page images) can run.
        # LlamaParse text remains available as a secondary/legacy text source.
        result = run_pdf_quiz_pipeline(
            assessment_id=assessment_id,
            rag_thread_id=thread_id,
            user_id=user_id,
            topic_id=topic_id,
            question_count=question_count,
            progress_callback=pipeline_progress,
            pdf_text=llamaparse_text,
            pdf_bytes=file_bytes,
        )
        self.update_state(
            state="SUCCESS",
            meta={"step": "done", "progress": 100, "message": "Quiz ready"},
        )
        return {"success": True, **result}
    except Exception as exc:
        logger.exception("PDF quiz Celery task failed for assessment %s", assessment_id)
        from app.services.quiz.assessment_doc_validation import (
            AssessmentDocValidationError,
            public_error_message,
        )

        msg = (
            public_error_message(exc)
            if isinstance(exc, AssessmentDocValidationError)
            else str(exc)
        )
        _set_pdf_source_status(source.id, "failed", error_message=msg)
        raise
    finally:
        try:
            os.unlink(file_path)
        except OSError:
            pass


def enqueue_or_run_pdf_quiz(
    assessment_id: int,
    file_bytes: bytes,
    filename: str,
    user_id: int,
    topic_id: int | None = None,
    async_mode: bool = True,
    progress_job_id: str | None = None,
    question_count: int | None = None,
) -> dict:
    """
    Save file to temp path and enqueue Celery task, or run synchronously if async unavailable.
    Local dev (USE_CELERY_FOR_INGESTION=false) always runs in-process — no Redis required.
    """
    if async_mode and not _celery_async_enabled():
        async_mode = False

    thread_id = _new_lms_thread_id(user_id)
    temp_path = _write_shared_temp_file(file_bytes, filename)

    # Create pdf_source immediately so status polling shows pending/processing (not "none")
    from app.services.lms import assessment_service

    assessment_service.link_pdf_source(
        assessment_id=assessment_id,
        rag_thread_id=thread_id,
        original_filename=filename,
    )

    if async_mode:
        try:
            task = process_pdf_quiz_task.delay(
                assessment_id=assessment_id,
                file_path=temp_path,
                filename=filename,
                user_id=user_id,
                topic_id=topic_id,
                thread_id=thread_id,
                question_count=question_count,
            )
            return {"async": True, "task_id": task.id, "thread_id": thread_id, "assessment_id": assessment_id}
        except Exception as exc:
            logger.warning("Celery unavailable, running PDF quiz pipeline synchronously: %s", exc)

    from app.utils.rag_service import ingest_pdf
    from app.utils.diagnostic_upload_progress import set_progress as _set_upload_progress

    _set_upload_progress(progress_job_id, 58, "Ingesting diagnostic Q&A PDF...", stage="qa_ingest")

    def _qa_progress(step: str, progress: int, message: str) -> None:
        mapped = 58 + int(max(0, min(100, progress)) * 0.12)
        _set_upload_progress(
            progress_job_id,
            mapped,
            message or "Ingesting diagnostic Q&A PDF...",
            stage="qa_ingest",
        )

    def _lp_progress(step: str, progress: int, message: str) -> None:
        _set_upload_progress(
            progress_job_id,
            54,
            message or "Extracting PDF with LlamaParse...",
            stage="qa_ingest",
        )

    llamaparse_text = _try_llamaparse_text(
        file_bytes,
        filename,
        progress_callback=_lp_progress if progress_job_id else None,
    )

    ingest_pdf(
        file_bytes=file_bytes,
        thread_id=thread_id,
        filename=filename,
        user_id=user_id,
        progress_callback=_qa_progress if progress_job_id else None,
        preextracted_text=llamaparse_text,
    )
    _set_upload_progress(
        progress_job_id, 72, "Extracting questions and answers...", stage="qa_extract"
    )
    # Always pass PDF bytes so hybrid vision (text + page images) can run.
    result = run_pdf_quiz_pipeline(
        assessment_id=assessment_id,
        rag_thread_id=thread_id,
        user_id=user_id,
        topic_id=topic_id,
        question_count=question_count,
        pdf_text=llamaparse_text,
        pdf_bytes=file_bytes,
    )
    _set_upload_progress(progress_job_id, 88, "Saving generated questions...", stage="qa_save")
    try:
        os.unlink(temp_path)
    except OSError:
        pass
    return {"async": False, **result}
