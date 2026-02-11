"""
Celery tasks for PDF ingestion.
"""
import base64
import logging
from app.celery_app import celery
from app.utils.rag_service import ingest_pdf
from app.models.database_models import RAGThread
from datetime import datetime
from sqlalchemy.pool import StaticPool

logger = logging.getLogger(__name__)


def _run_ingest_in_context(self, file_bytes_b64: str, thread_id: str, filename: str, user_id: int, conversation_id: int = None):
    """Run ingestion logic inside a Flask application context (for get_db(), current_app, etc.)."""
    # Update task state to processing
    self.update_state(
        state='PROCESSING',
        meta={'step': 'init', 'progress': 5, 'message': 'Starting PDF ingestion...'}
    )
    file_bytes = base64.b64decode(file_bytes_b64)

    def progress_callback(step: str, progress: int, message: str):
        try:
            self.update_state(state='PROCESSING', meta={'step': step, 'progress': progress, 'message': message})
        except Exception as e:
            logger.warning(f"Error updating task progress: {e}")

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
    }


@celery.task(bind=True, name='app.tasks.ingest_tasks.ingest_pdf_task')
def ingest_pdf_task(self, file_bytes_b64: str, thread_id: str, filename: str, user_id: int, conversation_id: int = None):
    """
    Celery task to ingest a PDF document in the background.
    Runs inside a Flask application context so get_db() and current_app work.
    """
    from app import create_app
    app = create_app()
    with app.app_context():
        try:
            return _run_ingest_in_context(self, file_bytes_b64, thread_id, filename, user_id, conversation_id)
        except ValueError as e:
            error_msg = str(e)
            logger.error(f"Value error in ingest_pdf_task: {error_msg}")
            self.update_state(
                state='FAILURE',
                meta={'error': error_msg, 'message': f'Validation error: {error_msg}', 'exc_type': 'ValueError', 'exc_message': error_msg}
            )
            return {'success': False, 'error': error_msg, 'message': f'Validation error: {error_msg}'}
        except Exception as e:
            error_msg = f'Failed to ingest PDF: {str(e)}'
            exc_type = type(e).__name__
            logger.error(f"Error in ingest_pdf_task: {error_msg}", exc_info=True)
            self.update_state(
                state='FAILURE',
                meta={'error': error_msg, 'message': error_msg, 'exc_type': exc_type, 'exc_message': str(e)}
            )
            return {'success': False, 'error': error_msg, 'message': error_msg, 'exc_type': exc_type}


def _save_thread_to_db(user_id: int, thread_id: str, filename: str, ingest_result: dict = None):
    """
    Helper function to save thread to database.
    Uses session factory directly since Flask's 'g' is not available in Celery tasks.
    Creates engine directly from Config to avoid needing current_app.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.config import Config
    
    # Create engine directly from Config (works in Celery context)
    db_url = Config.SQLALCHEMY_DATABASE_URI
    engine_options = Config.SQLALCHEMY_ENGINE_OPTIONS.copy()
    
    # SQLite-specific optimizations
    if db_url.startswith('sqlite'):
        engine_options['poolclass'] = StaticPool
        engine_options['connect_args'] = {
            'check_same_thread': False,
            'timeout': 20.0
        }
    else:
        from sqlalchemy.pool import QueuePool
        engine_options.setdefault('poolclass', QueuePool)
    
    # Create engine and session for this operation
    engine = create_engine(db_url, **engine_options)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = Session()
    
    try:
        existing_thread = db.query(RAGThread).filter_by(thread_id=thread_id).first()
        now = datetime.utcnow()
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

        if ingest_result:
            existing_thread.filename = filename
            existing_thread.has_document = True
            existing_thread.doc_count = (existing_thread.doc_count or 0) + 1
            existing_thread.num_pages = ingest_result.get("num_pages") or ingest_result.get("pages")
            existing_thread.last_ingested_at = now
            existing_thread.embedding_model = ingest_result.get("embedding_model")
            existing_thread.embedding_dim = ingest_result.get("embedding_dim")
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
    finally:
        db.close()  # Always close the session
        engine.dispose()  # Dispose of the engine to close all connections
