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


@celery.task(bind=True, name='app.tasks.ingest_tasks.ingest_pdf_task')
def ingest_pdf_task(self, file_bytes_b64: str, thread_id: str, filename: str, user_id: int, conversation_id: int = None):
    """
    Celery task to ingest a PDF document in the background.
    
    Args:
        self: Celery task instance (for updating state)
        file_bytes_b64: Base64 encoded file bytes
        thread_id: Thread ID for the RAG service
        filename: Original filename
        user_id: User ID who uploaded the file
        conversation_id: Optional conversation ID
    
    Returns:
        dict: Result containing ingestion details
    """
    try:
        # Update task state to processing
        self.update_state(
            state='PROCESSING',
            meta={'step': 'init', 'progress': 5, 'message': 'Starting PDF ingestion...'}
        )
        
        # Decode file bytes
        file_bytes = base64.b64decode(file_bytes_b64)
        
        # Progress callback for Celery task
        def progress_callback(step: str, progress: int, message: str):
            """Update task state with progress information."""
            try:
                self.update_state(
                    state='PROCESSING',
                    meta={
                        'step': step,
                        'progress': progress,
                        'message': message
                    }
                )
            except Exception as e:
                logger.warning(f"Error updating task progress: {e}")
        
        # Ingest the PDF
        result = ingest_pdf(
            file_bytes=file_bytes,
            thread_id=thread_id,
            filename=filename,
            progress_callback=progress_callback
        )
        
        # Save thread to database
        # Flask app context should be available from ContextTask
        _save_thread_to_db(user_id, thread_id, filename)
        
        # Return success result
        return {
            'success': True,
            'message': 'PDF ingested successfully',
            'thread_id': thread_id,
            'conversation_id': conversation_id,
            'filename': result.get('filename', filename),
            'documents': result.get('documents', result.get('num_pages', 0)),
            'num_pages': result.get('num_pages', result.get('documents', 0)),
            'pages': result.get('pages', result.get('num_pages', result.get('documents', 0))),
            'chunks': result.get('chunks', 0)
        }
        
    except ValueError as e:
        error_msg = str(e)
        logger.error(f"Value error in ingest_pdf_task: {error_msg}")
        # Update state with error info
        self.update_state(
            state='FAILURE',
            meta={
                'error': error_msg,
                'message': f'Validation error: {error_msg}',
                'exc_type': 'ValueError',
                'exc_message': error_msg
            }
        )
        # Don't re-raise - let Celery handle it with the state we set
        return {
            'success': False,
            'error': error_msg,
            'message': f'Validation error: {error_msg}'
        }
    except Exception as e:
        error_msg = f'Failed to ingest PDF: {str(e)}'
        exc_type = type(e).__name__
        logger.error(f"Error in ingest_pdf_task: {error_msg}", exc_info=True)
        # Update state with error info including exception type
        self.update_state(
            state='FAILURE',
            meta={
                'error': error_msg,
                'message': error_msg,
                'exc_type': exc_type,
                'exc_message': str(e)
            }
        )
        # Don't re-raise - let Celery handle it with the state we set
        return {
            'success': False,
            'error': error_msg,
            'message': error_msg,
            'exc_type': exc_type
        }


def _save_thread_to_db(user_id: int, thread_id: str, filename: str):
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
        # Check if thread already exists
        existing_thread = db.query(RAGThread).filter_by(thread_id=thread_id).first()
        if not existing_thread:
            # Create new thread record
            thread_name = f"Thread {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}"
            now = datetime.utcnow()
            rag_thread = RAGThread(
                user_id=user_id,
                thread_id=thread_id,
                name=thread_name,
                filename=filename,
                created_at=now,
                updated_at=now
            )
            db.add(rag_thread)
            db.commit()
            db.refresh(rag_thread)
            logger.info(f"Created new thread {thread_id} for user {user_id} with filename {filename}")
        else:
            # Update existing thread (only if it doesn't already have a document)
            from app.utils.rag_service import thread_has_document
            if not thread_has_document(thread_id):
                existing_thread.filename = filename
                existing_thread.updated_at = datetime.utcnow()
                db.commit()
                logger.info(f"Updated existing thread {thread_id} with filename {filename}")
            else:
                logger.warning(f"Attempted to update thread {thread_id} that already has a document")
    except Exception as e:
        logger.error(f"Error saving thread to database: {str(e)}")
        db.rollback()
        # Continue even if database save fails
    finally:
        db.close()  # Always close the session
        engine.dispose()  # Dispose of the engine to close all connections
