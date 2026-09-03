"""
Celery tasks for PDF ingestion.
"""
import base64
import logging
import os
from app.celery_app import celery
from app.utils.llm_gateway import (
    LlmTelemetryContext,
    reset_llm_telemetry_context,
    set_llm_telemetry_context,
)
from app.models.database_models import RAGThread, RAGChunk, RAGHeading
from datetime import datetime

logger = logging.getLogger(__name__)
_CANCELLED_UPLOAD_FILENAME = "__CANCELLED_UPLOAD__"

_LOAD_TEST_MODE = os.getenv("LOAD_TEST_MODE", "true").lower() in ("true", "1", "yes")
# Use a dedicated headings queue only when explicitly requested (or in load tests).
# This prevents "headings pending forever" when a deployment runs workers on ingest/default only.
RAG_HEADINGS_QUEUE = os.getenv(
    "RAG_HEADINGS_QUEUE",
    "headings" if _LOAD_TEST_MODE else "ingest",
).strip() or "ingest"
logger.info("Configured headings extraction queue: %s", RAG_HEADINGS_QUEUE)


def _run_ingest_in_context(self, file_path: str, thread_id: str, filename: str, user_id: int, conversation_id: int = None):
    """Run ingestion logic inside a Flask application context (for get_db(), current_app, etc.).

    The caller passes a temporary file path; this function is responsible for
    reading the bytes and unlinking the file immediately afterwards.
    """
    # Update task state to processing
    self.update_state(
        state='PROCESSING',
        meta={'step': 'init', 'progress': 5, 'message': 'Starting PDF ingestion...'}
    )

    # Read file bytes from the temporary path and unlink as soon as possible
    try:
        with open(file_path, 'rb') as f:
            file_bytes = f.read()
    finally:
        try:
            os.unlink(file_path)
        except OSError:
            pass

    def progress_callback(step: str, progress: int, message: str):
        try:
            self.update_state(state='PROCESSING', meta={'step': step, 'progress': progress, 'message': message})
        except Exception as e:
            logger.warning(f"Error updating task progress: {e}")

    from app.utils.rag_service import ingest_pdf

    result = ingest_pdf(
        file_bytes=file_bytes,
        thread_id=thread_id,
        filename=filename,
        progress_callback=progress_callback,
        user_id=user_id,
    )
    _save_thread_to_db(user_id, thread_id, filename, ingest_result=result)
    return {
        'success': True,
        'message': 'PDF ingested successfully',
        'thread_id': thread_id,
        'conversation_id': conversation_id,
        'filename': result.get('filename', filename),
        'documents': result.get('documents', result.get('num_pages', 0)),
        'num_pages': result.get('num_pages', result.get('documents', 0)),
        'pages': result.get('pages', result.get('num_pages', result.get('documents', 0))),
        'chunks': result.get('chunks', 0),
        'markdown_download_url': f'/api/rag/download-markdown/{thread_id}',
        'processing_time_seconds': result.get('processing_time_seconds'),
        'warning': result.get('warning'),
    }


@celery.task(bind=True, name='app.tasks.ingest_tasks.ingest_pdf_task', queue='ingest')
def ingest_pdf_task(self, file_path: str, thread_id: str, filename: str, user_id: int, conversation_id: int = None):
    """
    Celery task to ingest a PDF document in the background.
    Runs inside a Flask application context via Celery's ContextTask wrapper.
    """
    try:
        return _run_ingest_in_context(self, file_path, thread_id, filename, user_id, conversation_id)
    except ValueError as e:
        error_msg = str(e)
        logger.error(f"Value error in ingest_pdf_task: {error_msg}")
        self.update_state(
            state='FAILURE',
            meta={'error': error_msg, 'message': f'Validation error: {error_msg}', 'exc_type': 'ValueError', 'exc_message': error_msg}
        )
        _save_thread_to_db(user_id, thread_id, filename, ingest_error=error_msg)
        return {'success': False, 'error': error_msg, 'message': f'Validation error: {error_msg}'}
    except Exception as e:
        error_msg = f'Failed to ingest PDF: {str(e)}'
        exc_type = type(e).__name__
        logger.error(f"Error in ingest_pdf_task: {error_msg}", exc_info=True)
        self.update_state(
            state='FAILURE',
            meta={'error': error_msg, 'message': error_msg, 'exc_type': exc_type, 'exc_message': str(e)}
        )
        _save_thread_to_db(user_id, thread_id, filename, ingest_error=error_msg)
        return {'success': False, 'error': error_msg, 'message': error_msg, 'exc_type': exc_type}


def _save_thread_to_db(user_id: int, thread_id: str, filename: str, ingest_result: dict = None, ingest_error: str = None):
    """
    Helper function to save thread to database.
    Uses the shared SQLAlchemy session factory via get_db(), which is already
    configured per-process in Celery through init_celery's ContextTask.
    """
    from app.utils.db import get_db

    db = get_db()

    try:
        existing_thread = db.query(RAGThread).filter_by(thread_id=thread_id).first()
        now = datetime.utcnow()
        if existing_thread and (getattr(existing_thread, "filename", None) or "") == _CANCELLED_UPLOAD_FILENAME:
            logger.info(
                "Skipping persist for cancelled upload thread_id=%s user_id=%s",
                thread_id,
                user_id,
            )
            try:
                from app.utils.rag_vectorstore import delete_by_thread
                delete_by_thread(thread_id, user_id)
            except Exception:
                logger.warning("Failed to cleanup vectors for cancelled thread_id=%s", thread_id, exc_info=True)
            try:
                db.query(RAGChunk).filter_by(thread_id=thread_id, user_id=user_id).delete(synchronize_session=False)
                db.query(RAGHeading).filter_by(thread_id=thread_id, user_id=user_id).delete(synchronize_session=False)
                db.commit()
            except Exception:
                db.rollback()
                logger.warning("Failed to cleanup db artifacts for cancelled thread_id=%s", thread_id, exc_info=True)
            return

        if not existing_thread:
            thread_name = f"Thread {now.strftime('%Y-%m-%d %H:%M')}"
            rag_thread = RAGThread(
                user_id=user_id,
                thread_id=thread_id,
                name=thread_name,
                filename=filename,
                created_at=now,
                updated_at=now,
            )
            db.add(rag_thread)
            db.commit()
            db.refresh(rag_thread)
            existing_thread = rag_thread
            logger.info("Created new thread %s for user %s", thread_id, user_id)

        if ingest_error:
            existing_thread.ingest_status = 'failed'
            existing_thread.ingest_error = ingest_error[:2000]
            existing_thread.updated_at = now
            db.commit()
            logger.info("Marked thread %s as failed: %s", thread_id, ingest_error[:200])
        elif ingest_result:
            existing_thread.filename = filename
            existing_thread.has_document = True
            existing_thread.doc_count = (existing_thread.doc_count or 0) + 1
            existing_thread.num_pages = ingest_result.get("num_pages") or ingest_result.get("pages")
            existing_thread.last_ingested_at = now
            existing_thread.embedding_model = ingest_result.get("embedding_model")
            existing_thread.embedding_dim = ingest_result.get("embedding_dim")
            existing_thread.ingest_status = 'success'
            existing_thread.ingest_error = None
            # Persist the ingestion warning to the thread (not just the
            # one-time Celery task result) so it stays visible after the
            # upload UI's toast disappears - see #19. A successful
            # re-ingestion with no warning clears any stale one, including
            # one set retroactively by identify_truncated_threads.py.
            ingest_warning = ingest_result.get("warning")
            existing_thread.ingest_warning = ingest_warning
            existing_thread.ingest_warning_at = now if ingest_warning else None
            existing_thread.updated_at = now
            db.commit()
            logger.info("Updated thread %s with has_document=true", thread_id)
        else:
            if not getattr(existing_thread, "has_document", False):
                existing_thread.filename = filename
                existing_thread.updated_at = now
                db.commit()
    except Exception as e:
        logger.error(f"Error saving thread to database: {str(e)}")
        db.rollback()
        # Continue even if database save fails


@celery.task(bind=True, name='app.tasks.ingest_tasks.extract_headings_task', queue=RAG_HEADINGS_QUEUE)
def extract_headings_task(self, thread_id: str, user_id: int):
    """
    Celery task to extract headings/topics for a thread and store them in the database.
    Runs inside a Flask application context so get_db() and other app utilities work.
    """
    try:
        from app.utils.rag_service import extract_and_store_headings_for_thread

        self.update_state(
            state='PROCESSING',
            meta={
                'step': 'init',
                'progress': 0,
                'message': 'Starting heading extraction...'
            },
        )
        _ts = (
            "load_test"
            if os.getenv("LOAD_TEST_MODE", "false").lower() in ("true", "1", "yes")
            else "production"
        )
        _tok = set_llm_telemetry_context(
            LlmTelemetryContext(
                user_id=user_id,
                user_role=None,
                workflow="rag_heading_extraction",
                traffic_source=_ts,
                thread_id=thread_id,
                celery_task_name="extract_headings_task",
            )
        )
        try:
            result = extract_and_store_headings_for_thread(
                thread_id=thread_id,
                user_id=user_id,
            )
        finally:
            reset_llm_telemetry_context(_tok)
        headings_count = result.get('topics_count', 0)
        self.update_state(
            state='SUCCESS',
            meta={
                'step': 'complete',
                'progress': 100,
                'message': f'Extracted {headings_count} headings',
            },
        )
        return {
            'success': True,
            'thread_id': thread_id,
            'user_id': user_id,
            'headings_count': headings_count,
        }
    except Exception as e:
        error_msg = f'Failed to extract headings: {str(e)}'
        exc_type = type(e).__name__
        logger.error(error_msg, exc_info=True)
        self.update_state(
            state='FAILURE',
            meta={
                'error': error_msg,
                'message': error_msg,
                'exc_type': exc_type,
                'exc_message': str(e),
            },
        )
        return {
            'success': False,
            'error': error_msg,
            'thread_id': thread_id,
            'user_id': user_id,
            'exc_type': exc_type,
        }
