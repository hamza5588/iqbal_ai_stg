from flask import Blueprint, request, jsonify, session, render_template, Response, stream_with_context
from app.utils.auth import login_required
from app.utils.rag_service import (
    ingest_pdf,
    chatbot,
    thread_has_document,
    thread_document_metadata,
    update_lesson_finalized_status,
    delete_thread
)
from app.utils.db import get_db
from app.models.database_models import RAGThread, RAGPrompt
from app.services.chat_service import ChatService
from app.tasks.ingest_tasks import ingest_pdf_task
from langchain_core.messages import HumanMessage
import logging
import uuid
from datetime import datetime
import os
import json
import time
import base64
from tempfile import NamedTemporaryFile

from openai import OpenAI

logger = logging.getLogger(__name__)
bp = Blueprint('rag', __name__)


def _get_openai_client():
    """
    Lazily create an OpenAI client for Whisper STT.
    Expects OPENAI_API_KEY in environment.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is not set")
    return OpenAI(api_key=api_key)


def _get_thread_id(user_id: int, conversation_id: int = None) -> str:
    """
    Generate a unique thread_id for the RAG service.
    Creates a new unique thread ID for each upload.
    """
    if conversation_id:
        return f"user_{user_id}_conv_{conversation_id}"
    # Generate unique thread ID with timestamp and UUID
    unique_id = str(uuid.uuid4())[:8]
    timestamp = int(datetime.utcnow().timestamp())
    return f"user_{user_id}_thread_{timestamp}_{unique_id}"


@bp.route('/chatbot', methods=['GET'])
@login_required
def chatbot_page():
    """
    Render the PDF chat interface.
    """
    try:
        # Redirect to main chat interface instead of separate chatbot page
        return render_template('chat.html')
    except Exception as e:
        logger.error(f"Error rendering chatbot page: {str(e)}", exc_info=True)
        return jsonify({'error': f'Failed to render page: {str(e)}'}), 500


def _validate_thread_id(thread_id: str, user_id: int) -> bool:
    """
    Validate that a thread_id belongs to the current user.
    Prevents users from accessing other users' threads.
    """
    if not thread_id:
        return False
    
    # Thread ID must start with the user's ID
    expected_prefix = f"user_{user_id}_"
    return thread_id.startswith(expected_prefix)


@bp.route('/ingest', methods=['POST'])
@login_required
def ingest():
    """
    Upload and ingest a PDF document for RAG.
    Expects a file in the 'file' field of the request.
    Optionally accepts 'thread_id' or 'conversation_id' in form data.
    Supports progress streaming via Server-Sent Events if 'stream' parameter is 'true'.
    """
    try:
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401
        
        user_id = session['user_id']
        
        # Check if streaming is requested
        stream_progress = request.form.get('stream', 'false').lower() == 'true'
        
        # Check if file is present
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['file']
        if not file or file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        # Validate file type
        if not file.filename.lower().endswith('.pdf'):
            return jsonify({'error': 'Only PDF files are supported'}), 400

        # Get thread_id from request or create new thread
        # IMPORTANT: By default, each PDF upload creates a NEW thread.
        # This ensures that each uploaded document has its own separate thread/context.
        conversation_id = request.form.get('conversation_id', type=int)
        provided_thread_id = request.form.get('thread_id')
        create_new_thread = request.form.get('create_new_thread', 'true').lower() == 'true'
        
        # If no conversation_id is provided, automatically create a new conversation
        if not conversation_id:
            try:
                api_key = session.get('groq_api_key', '')
                chat_service = ChatService(user_id, api_key)
                # Create conversation with filename as title
                filename = file.filename
                conversation_title = f"Chat: {filename}" if filename else "New Chat"
                conversation_id = chat_service.create_conversation(conversation_title)
                logger.info(f"Auto-created conversation {conversation_id} for file upload: {filename}")
            except Exception as e:
                logger.error(f"Error creating conversation for file upload: {str(e)}")
                # Continue without conversation_id if creation fails
                conversation_id = None
        
        # ENFORCE: Check if conversation already has a PDF
        if conversation_id:
            # First verify conversation ownership
            try:
                from app.models.models import ConversationModel
                conversation_model = ConversationModel(user_id)
                conv = conversation_model.get_conversation_by_id(conversation_id)
                if not conv:
                    # Conversation doesn't exist or doesn't belong to user - create a new one
                    logger.warning(f"Conversation {conversation_id} not found or doesn't belong to user {user_id}, creating new conversation")
                    filename = file.filename
                    conversation_title = f"Chat: {filename}" if filename else "New Chat"
                    conversation_id = conversation_model.create_conversation(conversation_title)
                    logger.info(f"Created new conversation {conversation_id} for file upload: {filename}")
            except Exception as e:
                logger.error(f"Error verifying conversation ownership: {str(e)}")
                # Try to create a new conversation as fallback
                try:
                    from app.models.models import ConversationModel
                    conversation_model = ConversationModel(user_id)
                    filename = file.filename
                    conversation_title = f"Chat: {filename}" if filename else "New Chat"
                    conversation_id = conversation_model.create_conversation(conversation_title)
                    logger.info(f"Created new conversation {conversation_id} after error: {filename}")
                except Exception as create_error:
                    logger.error(f"Error creating fallback conversation: {str(create_error)}")
                    return jsonify({
                        'error': 'Error verifying conversation ownership. Please try again.'
                    }), 500
            
            # Check if this conversation already has a PDF uploaded
            expected_thread_id = f"user_{user_id}_conv_{conversation_id}"
            if thread_has_document(expected_thread_id):
                # Also check RAGThread table for any thread linked to this conversation
                db = get_db()
                existing_thread = db.query(RAGThread).filter_by(
                    user_id=user_id,
                    thread_id=expected_thread_id
                ).first()
                
                if existing_thread:
                    return jsonify({
                        'error': 'This conversation already has a PDF uploaded. Each conversation can only have one PDF. Please create a new conversation to upload another PDF.',
                        'existing_filename': existing_thread.filename
                    }), 400
            
            # Also check metadata for stored conversation_id
            try:
                from app.utils.rag_service import _THREAD_METADATA, _load_metadata
                _load_metadata()
                
                for thread_id, metadata in _THREAD_METADATA.items():
                    stored_conv_id = metadata.get('conversation_id')
                    if stored_conv_id == conversation_id:
                        if _validate_thread_id(thread_id, user_id) and thread_has_document(thread_id):
                            existing_filename = metadata.get('filename', 'Unknown PDF')
                            return jsonify({
                                'error': 'This conversation already has a PDF uploaded. Each conversation can only have one PDF. Please create a new conversation to upload another PDF.',
                                'existing_filename': existing_filename
                            }), 400
            except Exception as e:
                logger.warning(f"Error checking metadata for existing PDF: {str(e)}")
        
        # If create_new_thread is False AND a thread_id is provided, use existing thread
        # (This is rare - normally each upload creates a new thread)
        if provided_thread_id and not create_new_thread:
            logger.info(f"Using existing thread {provided_thread_id} for PDF upload (create_new_thread=False)")
            if not _validate_thread_id(provided_thread_id, user_id):
                return jsonify({'error': 'Invalid thread_id. You can only use your own threads.'}), 403
            
            # Check if thread already has a document - only one document per thread allowed
            if thread_has_document(provided_thread_id):
                return jsonify({
                    'error': 'This thread already has a document. Only one document per thread is allowed. Please create a new thread for a new document.'
                }), 400
            
            thread_id = provided_thread_id
        else:
            # Always create a new thread for new PDF uploads (default behavior)
            # This ensures each uploaded PDF gets its own thread
            thread_id = _get_thread_id(user_id, conversation_id)
            logger.info(f"Creating new thread {thread_id} for PDF upload (filename: {file.filename}, conversation_id: {conversation_id})")
        
        filename = file.filename

        # Read file bytes
        file_bytes = file.read()
        if not file_bytes:
            return jsonify({'error': 'File is empty'}), 400

        # If streaming is requested, use SSE for backend processing progress (legacy support)
        if stream_progress:
            return _ingest_with_progress(file_bytes, thread_id, filename, user_id)
        
        # Use Celery for background processing
        # Encode file bytes to base64 for Celery task
        file_bytes_b64 = base64.b64encode(file_bytes).decode('utf-8')
        
        # Start Celery task
        task = ingest_pdf_task.delay(
            file_bytes_b64=file_bytes_b64,
            thread_id=thread_id,
            filename=filename,
            user_id=user_id,
            conversation_id=conversation_id
        )
        
        logger.info(f"Started Celery task {task.id} for PDF ingestion: {filename} (thread_id: {thread_id})")
        
        return jsonify({
            'success': True,
            'message': 'PDF ingestion started in background',
            'task_id': task.id,
            'status': 'processing',
            'thread_id': thread_id,
            'conversation_id': conversation_id,
            'filename': filename
        })

    except ValueError as e:
        logger.error(f"Value error in ingest: {str(e)}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error ingesting PDF: {str(e)}", exc_info=True)
        return jsonify({'error': f'Failed to ingest PDF: {str(e)}'}), 500


def _save_thread_to_db(user_id: int, thread_id: str, filename: str):
    """Helper function to save thread to database"""
    db = get_db()
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
                updated_at=now  # Ensure updated_at is set on creation
            )
            db.add(rag_thread)
            db.commit()
            db.refresh(rag_thread)
            logger.info(f"Created new thread {thread_id} for user {user_id} with filename {filename}")
        else:
            # Update existing thread (only if it doesn't already have a document)
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


def _ingest_with_progress(file_bytes: bytes, thread_id: str, filename: str, user_id: int):
    """
    Ingest PDF with Server-Sent Events (SSE) for real-time progress updates.
    """
    from flask import current_app
    
    # Capture the Flask app instance from the current request context
    # This must be done before the generator function to capture the app
    app = current_app._get_current_object()
    
    def generate():
        """Generator function for SSE streaming"""
        import queue
        import threading
        
        progress_queue = queue.Queue()
        ingestion_complete = threading.Event()
        result_container = {'result': None, 'error': None}
        
        def progress_callback(step: str, progress: int, message: str):
            """Callback to capture progress updates"""
            try:
                progress_queue.put({
                    'step': step,
                    'progress': progress,
                    'message': message,
                    'timestamp': time.time()
                }, timeout=1)
            except:
                pass  # Ignore queue full errors
        
        def run_ingestion():
            """Run ingestion in background with application context"""
            # Push application context for database operations
            with app.app_context():
                try:
                    result = ingest_pdf(
                        file_bytes=file_bytes,
                        thread_id=thread_id,
                        filename=filename,
                        progress_callback=progress_callback
                    )
                    # Ensure thread_id is in the result
                    if 'thread_id' not in result:
                        result['thread_id'] = thread_id
                    result_container['result'] = result
                    _save_thread_to_db(user_id, thread_id, filename)
                except Exception as e:
                    result_container['error'] = str(e)
                    logger.error(f"Error during PDF ingestion: {str(e)}", exc_info=True)
                finally:
                    ingestion_complete.set()
        
        try:
            # Send initial progress
            yield f"data: {json.dumps({'step': 'start', 'progress': 0, 'message': 'Starting PDF ingestion...'})}\n\n"
            
            # Start ingestion in background thread
            ingestion_thread = threading.Thread(target=run_ingestion)
            ingestion_thread.daemon = True
            ingestion_thread.start()
            
            # Stream progress updates
            last_progress = 0
            while not ingestion_complete.is_set() or not progress_queue.empty():
                try:
                    # Get progress update (with timeout)
                    try:
                        update = progress_queue.get(timeout=0.5)
                        yield f"data: {json.dumps(update)}\n\n"
                        last_progress = update.get('progress', last_progress)
                    except queue.Empty:
                        # Send heartbeat to keep connection alive
                        yield f"data: {json.dumps({'step': 'processing', 'progress': last_progress, 'message': 'Processing...'})}\n\n"
                except Exception as e:
                    logger.warning(f"Error sending progress update: {e}")
                    break
            
            # Wait for ingestion to complete
            ingestion_thread.join(timeout=300)  # 5 minute timeout
            
            if result_container['error']:
                error_msg = f"Error: {result_container['error']}"
                yield f"data: {json.dumps({'step': 'error', 'progress': 0, 'message': error_msg, 'error': result_container['error']})}\n\n"
            elif result_container['result']:
                yield f"data: {json.dumps({'step': 'complete', 'progress': 100, 'message': 'PDF ingestion complete!', 'result': result_container['result']})}\n\n"
            else:
                yield f"data: {json.dumps({'step': 'error', 'progress': 0, 'message': 'Processing timeout or unknown error'})}\n\n"
                
        except Exception as e:
            logger.error(f"Error in progress streaming: {str(e)}", exc_info=True)
            yield f"data: {json.dumps({'step': 'error', 'progress': 0, 'message': f'Streaming error: {str(e)}', 'error': str(e)})}\n\n"
    
    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',  # Disable nginx buffering
            'Connection': 'keep-alive'
        }
    )


@bp.route('/chat', methods=['POST'])
@login_required
def chat():
    """
    Chat with the RAG-enabled chatbot.
    Accepts JSON or form-data with 'message' and optionally 'thread_id' or 'conversation_id'.
    """
    try:
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401

        user_id = session['user_id']

        # If audio is sent (voice input), transcribe it first using Whisper
        audio_text = None
        try:
            audio_file = request.files.get('audio')
            if audio_file and audio_file.filename:
                client = _get_openai_client()
                with NamedTemporaryFile(delete=False, suffix=".webm") as tmp:
                    audio_file.save(tmp.name)
                    tmp_path = tmp.name

                with open(tmp_path, "rb") as f:
                    transcription = client.audio.transcriptions.create(
                        model="whisper-1",
                        file=f,
                        response_format="json"
                    )

                audio_text = getattr(transcription, "text", None) or transcription.get("text")
        except Exception as stt_error:
            logger.error(f"Error transcribing audio for RAG chat: {str(stt_error)}", exc_info=True)
            # Continue without audio_text; will fall back to text message if provided
        
        # Try to get JSON data first
        data = request.get_json(force=True, silent=True)
        
        # If no JSON data, try form data
        if not data:
            if request.form:
                data = {
                    'message': request.form.get('message', '').strip(),
                    'thread_id': request.form.get('thread_id'),
                    'conversation_id': request.form.get('conversation_id', type=int)
                }
            elif request.args:  # Also support query parameters
                data = {
                    'message': request.args.get('message', '').strip(),
                    'thread_id': request.args.get('thread_id'),
                    'conversation_id': request.args.get('conversation_id', type=int)
                }
            elif audio_text:
                # Pure audio request – use transcribed text as the message
                data = {
                    'message': audio_text,
                    'thread_id': request.args.get('thread_id') if request.args else None,
                    'conversation_id': request.args.get('conversation_id', type=int) if request.args else None
                }
        
        if not data:
            return jsonify({'error': 'No data provided. Please send JSON, form-data with \"message\" field, or an audio file.'}), 400

        message = data.get('message', '').strip()
        if not message:
            return jsonify({'error': 'Message is required'}), 400
        
        # Get thread_id from request or auto-select thread with document
        provided_thread_id = data.get('thread_id')
        conversation_id = data.get('conversation_id')
        
        # If thread_id is provided, validate it belongs to this user
        if provided_thread_id:
            if not _validate_thread_id(provided_thread_id, user_id):
                return jsonify({'error': 'Invalid thread_id. You can only access your own threads.'}), 403
            thread_id = provided_thread_id
            logger.info(f"Using provided thread_id: {thread_id} for user {user_id}")
            
            # Verify thread has document
            if not thread_has_document(thread_id):
                logger.warning(f"Provided thread_id {thread_id} does not have a document, but continuing anyway")
        else:
            # Auto-select the most recent thread with a document for this user
            # IMPORTANT: Order by created_at to get the most recently uploaded PDF
            # created_at is more reliable than updated_at for determining upload order
            db = get_db()
            try:
                # Get all threads for this user, ordered by most recently created first
                # This ensures we get the thread for the most recently uploaded PDF
                threads = db.query(RAGThread).filter_by(user_id=user_id).order_by(
                    RAGThread.created_at.desc()
                ).all()
                
                # Find the most recent thread that has a document
                # Check threads in order (most recently created first)
                for thread in threads:
                    if thread_has_document(thread.thread_id):
                        thread_id = thread.thread_id
                        logger.info(f"Auto-selected thread {thread_id} with document for user {user_id} (most recently created)")
                        break
                else:
                    # No thread with document found, generate new thread_id
                    thread_id = _get_thread_id(user_id, conversation_id)
                    logger.info(f"No thread with document found, generated new thread_id: {thread_id}")
            except Exception as e:
                logger.warning(f"Error finding thread with document: {str(e)}, generating new thread_id")
                # Fallback: generate thread_id based on user_id
                thread_id = _get_thread_id(user_id, conversation_id)

        # Prepare config for LangGraph
        config = {
            "configurable": {
                "thread_id": thread_id
            }
        }

        # Create HumanMessage
        human_message = HumanMessage(content=message)

        # Invoke the chatbot - LangGraph returns the final state
        state = chatbot.invoke(
            {"messages": [human_message]},
            config=config
        )

        # Extract the last message from the state
        messages = state.get("messages", [])
        if not messages:
            response_content = "I'm sorry, I couldn't generate a response."
        else:
            # Get the last message (should be the AI response)
            last_msg = messages[-1]
            if hasattr(last_msg, 'content'):
                response_content = last_msg.content
            elif isinstance(last_msg, dict):
                response_content = last_msg.get('content', str(last_msg))
            else:
                response_content = str(last_msg)

        # Save messages to database for chat history
        db_conversation_id = None
        try:
            from app.models.models import ConversationModel
            conversation_model = ConversationModel(user_id)
            
            # Priority 1: Use conversation_id from request if provided (most reliable)
            if conversation_id:
                conv = conversation_model.get_conversation_by_id(conversation_id)
                if conv:
                    db_conversation_id = conversation_id
                    logger.info(f"Using provided conversation_id: {conversation_id} for thread {thread_id}")
                else:
                    # Conversation doesn't exist or doesn't belong to user, create new one
                    db_conversation_id = conversation_model.create_conversation(
                        title=message[:50] if len(message) > 50 else message
                    )
                    logger.info(f"Created new conversation {db_conversation_id} (provided conversation_id {conversation_id} was invalid)")
            else:
                # Priority 2: Extract conversation_id from thread_id if present (format: user_{user_id}_conv_{conversation_id})
                import re
                thread_conv_match = re.search(r'user_\d+_conv_(\d+)', thread_id)
                if thread_conv_match:
                    # Conversation ID is in thread_id
                    db_conversation_id = int(thread_conv_match.group(1))
                    # Verify conversation belongs to user
                    conv = conversation_model.get_conversation_by_id(db_conversation_id)
                    if not conv:
                        # Create new conversation if it doesn't exist or doesn't belong to user
                        db_conversation_id = conversation_model.create_conversation(
                            title=message[:50] if len(message) > 50 else message
                        )
                    logger.info(f"Extracted conversation_id {db_conversation_id} from thread_id {thread_id}")
                else:
                    # Priority 3: Check thread metadata for stored conversation_id
                    from app.utils.rag_service import _THREAD_METADATA, _save_metadata, _load_metadata
                    _load_metadata()  # Ensure we have latest metadata
                    
                    stored_conv_id = _THREAD_METADATA.get(thread_id, {}).get('conversation_id')
                    if stored_conv_id:
                        # Verify it still exists and belongs to user
                        conv = conversation_model.get_conversation_by_id(stored_conv_id)
                        if conv:
                            db_conversation_id = stored_conv_id
                            logger.info(f"Using stored conversation_id {db_conversation_id} from metadata for thread {thread_id}")
                        else:
                            # Create new conversation
                            db_conversation_id = conversation_model.create_conversation(
                                title=message[:50] if len(message) > 50 else message
                            )
                            # Update metadata
                            if thread_id not in _THREAD_METADATA:
                                _THREAD_METADATA[thread_id] = {}
                            _THREAD_METADATA[thread_id]['conversation_id'] = db_conversation_id
                            _save_metadata()
                            logger.info(f"Created new conversation {db_conversation_id} (stored conversation_id was invalid)")
                    else:
                        # Priority 4: Create new conversation
                        db_conversation_id = conversation_model.create_conversation(
                            title=message[:50] if len(message) > 50 else message
                        )
                        
                        # Store conversation_id in thread metadata for future reference
                        from app.utils.rag_service import _THREAD_METADATA, _save_metadata, _load_metadata
                        _load_metadata()
                        if thread_id not in _THREAD_METADATA:
                            _THREAD_METADATA[thread_id] = {}
                        _THREAD_METADATA[thread_id]['conversation_id'] = db_conversation_id
                        _save_metadata()
                        logger.info(f"Created new conversation {db_conversation_id} for thread {thread_id}")
            
            # Save user message
            conversation_model.save_message(
                conversation_id=db_conversation_id,
                message=message,
                role='user'
            )
            
            # Save AI response
            conversation_model.save_message(
                conversation_id=db_conversation_id,
                message=response_content,
                role='bot'  # Database constraint requires 'bot' not 'assistant'
            )
            
            logger.info(f"Saved RAG chat messages to conversation {db_conversation_id} for thread {thread_id}")
        except Exception as save_error:
            # Don't fail the request if saving to database fails
            logger.error(f"Failed to save RAG chat messages to database: {str(save_error)}", exc_info=True)

        return jsonify({
            'success': True,
            'message': response_content,
            'thread_id': thread_id,
            'conversation_id': db_conversation_id if db_conversation_id else conversation_id,
            'has_document': thread_has_document(thread_id)
        })

    except Exception as e:
        logger.error(f"Error in RAG chat: {str(e)}", exc_info=True)
        return jsonify({'error': f'Failed to process chat: {str(e)}'}), 500


@bp.route('/ingest/status/<task_id>', methods=['GET'])
@login_required
def get_ingest_status(task_id):
    """
    Get the status of a PDF ingestion task.
    Returns task status and progress information.
    Validates that the task belongs to the requesting user.
    """
    try:
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401
        
        user_id = session['user_id']
        
        # Get task result
        task = ingest_pdf_task.AsyncResult(task_id)
        
        # Security: Validate task belongs to this user
        # Check if task exists
        if task.state not in ('PENDING', 'PROCESSING', 'SUCCESS', 'FAILURE', 'REVOKED', 'RETRY'):
            # Task doesn't exist or is in unknown state
            return jsonify({'error': 'Task not found'}), 404
        
        # Validate task ownership by checking thread_id in result or metadata
        # For PENDING/PROCESSING: validate via thread_id if available in metadata
        # For SUCCESS/FAILURE: validate via thread_id in result
        task_thread_id = None
        
        if task.state in ('SUCCESS', 'FAILURE') and task.result:
            # Task completed - check result
            if isinstance(task.result, dict):
                task_thread_id = task.result.get('thread_id')
        elif task.state == 'PROCESSING' and task.info:
            # Task in progress - check metadata (thread_id might be in custom metadata)
            if isinstance(task.info, dict):
                # Thread ID might be stored in custom metadata, but we can't reliably get it here
                # We'll validate on completion instead
                pass
        
        # If we have a thread_id, validate it belongs to the user
        if task_thread_id:
            if not _validate_thread_id(task_thread_id, user_id):
                return jsonify({'error': 'Access denied. This task does not belong to you.'}), 403
        
        # For PENDING/PROCESSING tasks without thread_id in metadata, we can't fully validate
        # but we'll validate on completion. This is acceptable since:
        # 1. Task IDs are UUIDs (hard to guess)
        # 2. We validate on completion
        # 3. No sensitive data is exposed in status endpoint
        
        if task.state == 'PENDING':
            # Task is waiting to be processed
            response = {
                'task_id': task_id,
                'status': 'pending',
                'state': task.state,
                'message': 'Task is waiting to be processed'
            }
        elif task.state == 'PROCESSING':
            # Task is being processed
            meta = task.info or {}
            response = {
                'task_id': task_id,
                'status': 'processing',
                'state': task.state,
                'step': meta.get('step', 'processing'),
                'progress': meta.get('progress', 0),
                'message': meta.get('message', 'Processing PDF...')
            }
        elif task.state == 'SUCCESS':
            # Task completed successfully
            result = task.result
            response = {
                'task_id': task_id,
                'status': 'success',
                'state': task.state,
                'message': result.get('message', 'PDF ingested successfully'),
                'thread_id': result.get('thread_id'),
                'conversation_id': result.get('conversation_id'),
                'filename': result.get('filename'),
                'documents': result.get('documents', result.get('num_pages', 0)),
                'num_pages': result.get('num_pages', result.get('documents', 0)),
                'pages': result.get('pages', result.get('num_pages', result.get('documents', 0))),
                'chunks': result.get('chunks', 0)
            }
        elif task.state == 'FAILURE':
            # Task failed
            error_msg = 'Unknown error occurred'
            if task.info:
                if isinstance(task.info, dict):
                    error_msg = task.info.get('error', task.info.get('message', 'Unknown error occurred'))
                else:
                    error_msg = str(task.info)
            response = {
                'task_id': task_id,
                'status': 'failure',
                'state': task.state,
                'error': error_msg,
                'message': f'PDF ingestion failed: {error_msg}'
            }
        elif task.state == 'REVOKED':
            # Task was revoked/cancelled
            response = {
                'task_id': task_id,
                'status': 'revoked',
                'state': task.state,
                'message': 'Task was cancelled'
            }
        elif task.state == 'RETRY':
            # Task is being retried
            response = {
                'task_id': task_id,
                'status': 'retrying',
                'state': task.state,
                'message': 'Task is being retried'
            }
        else:
            # Unknown state
            response = {
                'task_id': task_id,
                'status': 'unknown',
                'state': task.state,
                'message': f'Task is in {task.state} state'
            }
        
        return jsonify(response)
        
    except Exception as e:
        logger.error(f"Error getting ingest task status: {str(e)}", exc_info=True)
        return jsonify({'error': f'Failed to get task status: {str(e)}'}), 500


@bp.route('/thread/status/<thread_id>', methods=['GET'])
@login_required
def get_thread_status(thread_id):
    """
    Get the status of a thread, including whether it has a document.
    """
    try:
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401

        user_id = session['user_id']
        
        # Validate thread_id belongs to this user
        if not _validate_thread_id(thread_id, user_id):
            return jsonify({'error': 'Access denied. You can only access your own threads.'}), 403

        has_doc = thread_has_document(thread_id)
        metadata = thread_document_metadata(thread_id) if has_doc else {}
        # Remove lesson-finalization metadata from API responses (deprecated behavior)
        if isinstance(metadata, dict):
            metadata.pop('lesson_finalized', None)
            metadata.pop('last_lesson_text', None)
            metadata.pop('lesson_title', None)

        return jsonify({
            'thread_id': thread_id,
            'has_document': has_doc,
            'metadata': metadata
        })

    except Exception as e:
        logger.error(f"Error getting thread status: {str(e)}")
        return jsonify({'error': f'Failed to get thread status: {str(e)}'}), 500


@bp.route('/thread/document/<thread_id>', methods=['GET'])
@login_required
def get_thread_document(thread_id):
    """
    Get document metadata for a thread.
    """
    try:
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401

        user_id = session['user_id']
        
        # Validate thread_id belongs to this user
        if not _validate_thread_id(thread_id, user_id):
            return jsonify({'error': 'Access denied. You can only access your own threads.'}), 403

        if not thread_has_document(thread_id):
            return jsonify({
                'error': 'No document found for this thread',
                'thread_id': thread_id
            }), 404

        metadata = thread_document_metadata(thread_id)
        return jsonify({
            'thread_id': thread_id,
            'metadata': metadata
        })

    except Exception as e:
        logger.error(f"Error getting thread document: {str(e)}")
        return jsonify({'error': f'Failed to get thread document: {str(e)}'}), 500


@bp.route('/threads', methods=['GET'])
@login_required
def get_threads():
    """
    Get all RAG threads for the current user, ordered by most recent first.
    """
    try:
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401

        user_id = session['user_id']
        
        db = get_db()
        try:
            # Get all threads for this user, ordered by most recent first
            threads = db.query(RAGThread).filter_by(user_id=user_id).order_by(RAGThread.created_at.desc()).all()
            
            threads_list = []
            for thread in threads:
                threads_list.append({
                    'thread_id': thread.thread_id,
                    'name': thread.name,
                    'filename': thread.filename,
                    'created_at': thread.created_at.isoformat() if thread.created_at else None,
                    'updated_at': thread.updated_at.isoformat() if thread.updated_at else None,
                    'has_document': thread_has_document(thread.thread_id)
                })
            
            return jsonify({
                'success': True,
                'threads': threads_list
            })
        except Exception as e:
            logger.error(f"Error retrieving threads from database: {str(e)}")
            return jsonify({'error': f'Failed to retrieve threads: {str(e)}'}), 500
    except Exception as e:
        logger.error(f"Error getting threads: {str(e)}")
        return jsonify({'error': f'Failed to get threads: {str(e)}'}), 500


@bp.route('/prompt', methods=['GET'])
@login_required
def get_rag_prompt():
    """
    Get the custom RAG prompt for the current user.
    Prompts are user-level and apply to all threads for that user.
    """
    try:
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401

        user_id = session['user_id']
        
        db = get_db()
        try:
            # Get user-specific prompt (applies to all threads)
            prompt_obj = db.query(RAGPrompt).filter(
                RAGPrompt.user_id == user_id,
                RAGPrompt.thread_id.is_(None)
            ).order_by(RAGPrompt.updated_at.desc()).first()
            
            if prompt_obj:
                return jsonify({
                    'success': True,
                    'prompt': prompt_obj.prompt,
                    'thread_id': None,
                    'updated_at': prompt_obj.updated_at.isoformat() if prompt_obj.updated_at else None
                })
            else:
                return jsonify({
                    'success': True,
                    'prompt': None,
                    'message': 'No custom prompt set'
                })
        except Exception as e:
            logger.error(f"Error retrieving RAG prompt: {str(e)}")
            return jsonify({'error': f'Failed to retrieve prompt: {str(e)}'}), 500
    except Exception as e:
        logger.error(f"Error getting RAG prompt: {str(e)}")
        return jsonify({'error': f'Failed to get prompt: {str(e)}'}), 500


@bp.route('/prompt', methods=['POST'])
@login_required
def set_rag_prompt():
    """
    Set or update the custom RAG prompt for the current user.
    Prompts are user-level and apply to all threads for that user.
    Thread_id parameter is ignored - prompts always apply to all threads.
    """
    try:
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401

        user_id = session['user_id']
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        prompt = data.get('prompt', '').strip()
        
        if not prompt:
            return jsonify({'error': 'Prompt is required'}), 400
        
        db = get_db()
        try:
            # Delete existing user-level prompt (thread_id is always None)
            db.query(RAGPrompt).filter(
                RAGPrompt.user_id == user_id,
                RAGPrompt.thread_id.is_(None)
            ).delete()
            
            # Create new user-level prompt (applies to all threads)
            rag_prompt = RAGPrompt(
                user_id=user_id,
                thread_id=None,  # Always None - prompts are user-level
                prompt=prompt
            )
            db.add(rag_prompt)
            db.commit()
            db.refresh(rag_prompt)
            
            return jsonify({
                'success': True,
                'message': 'Prompt saved successfully. It will apply to all your threads.',
                'prompt_id': rag_prompt.id,
                'thread_id': None
            })
        except Exception as e:
            logger.error(f"Error saving RAG prompt: {str(e)}")
            db.rollback()
            return jsonify({'error': f'Failed to save prompt: {str(e)}'}), 500
    except Exception as e:
        logger.error(f"Error setting RAG prompt: {str(e)}")
        return jsonify({'error': f'Failed to set prompt: {str(e)}'}), 500


@bp.route('/prompt', methods=['DELETE'])
@login_required
def delete_rag_prompt():
    """
    Delete the custom RAG prompt for the current user.
    Prompts are user-level and apply to all threads.
    Thread_id parameter is ignored - always deletes the user-level prompt.
    """
    try:
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401

        user_id = session['user_id']
        
        db = get_db()
        try:
            # Delete user-level prompt (thread_id is always None)
            deleted_count = db.query(RAGPrompt).filter(
                RAGPrompt.user_id == user_id,
                RAGPrompt.thread_id.is_(None)
            ).delete()
            db.commit()
            
            if deleted_count > 0:
                return jsonify({
                    'success': True,
                    'message': 'Prompt deleted successfully'
                })
            else:
                return jsonify({
                    'success': True,
                    'message': 'No prompt found to delete'
                })
        except Exception as e:
            logger.error(f"Error deleting RAG prompt: {str(e)}")
            db.rollback()
            return jsonify({'error': f'Failed to delete prompt: {str(e)}'}), 500
    except Exception as e:
        logger.error(f"Error deleting RAG prompt: {str(e)}")
        return jsonify({'error': f'Failed to delete prompt: {str(e)}'}), 500


@bp.route('/thread/<thread_id>/lesson-finalized', methods=['PUT'])
@login_required
def update_lesson_finalized(thread_id):
    """
    Update the lesson_finalized status for a thread.
    Expects JSON with 'finalized' boolean field.
    """
    try:
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401

        user_id = session['user_id']
        
        # Validate thread_id belongs to this user
        if not _validate_thread_id(thread_id, user_id):
            return jsonify({'error': 'Access denied. You can only access your own threads.'}), 403

        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        finalized = data.get('finalized')
        if not isinstance(finalized, bool):
            return jsonify({'error': 'finalized must be a boolean value'}), 400

        # Update the status
        success = update_lesson_finalized_status(thread_id, finalized)
        
        if success:
            return jsonify({
                'success': True,
                'message': f'Lesson finalized status updated to {finalized}',
                'thread_id': thread_id,
                'finalized': finalized
            })
        else:
            return jsonify({
                'error': 'Thread not found or no document associated with this thread'
            }), 404

    except Exception as e:
        logger.error(f"Error updating lesson finalized status: {str(e)}")
        return jsonify({'error': f'Failed to update lesson finalized status: {str(e)}'}), 500


@bp.route('/conversation/<int:conversation_id>/thread', methods=['GET'])
@login_required
def get_thread_for_conversation(conversation_id):
    """
    Get the RAG thread_id associated with a conversation.
    Returns thread_id if conversation is a RAG conversation, None otherwise.
    """
    try:
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401

        user_id = session['user_id']
        
        # Method 1: Check if there's a thread with the pattern user_{user_id}_conv_{conversation_id}
        expected_thread_id = f"user_{user_id}_conv_{conversation_id}"
        if thread_has_document(expected_thread_id):
            return jsonify({
                'success': True,
                'is_rag_conversation': True,
                'thread_id': expected_thread_id,
                'has_document': True
            })
        
        # Method 2: Check RAGThread table for threads with this conversation_id pattern
        db = get_db()
        try:
            threads = db.query(RAGThread).filter_by(user_id=user_id).all()
            for thread in threads:
                # Check if thread_id matches the conversation pattern
                import re
                thread_conv_match = re.search(r'user_\d+_conv_(\d+)', thread.thread_id)
                if thread_conv_match:
                    thread_conv_id = int(thread_conv_match.group(1))
                    if thread_conv_id == conversation_id and thread_has_document(thread.thread_id):
                        return jsonify({
                            'success': True,
                            'is_rag_conversation': True,
                            'thread_id': thread.thread_id,
                            'has_document': True,
                            'filename': thread.filename
                        })
        except Exception as e:
            logger.warning(f"Error checking RAGThread table for conversation {conversation_id}: {str(e)}")
        
        # Method 3: Check thread metadata for stored conversation_id
        try:
            from app.utils.rag_service import _THREAD_METADATA, _load_metadata
            _load_metadata()
            
            for thread_id, metadata in _THREAD_METADATA.items():
                stored_conv_id = metadata.get('conversation_id')
                if stored_conv_id == conversation_id:
                    # Validate thread belongs to user
                    if _validate_thread_id(thread_id, user_id) and thread_has_document(thread_id):
                        return jsonify({
                            'success': True,
                            'is_rag_conversation': True,
                            'thread_id': thread_id,
                            'has_document': True
                        })
        except Exception as e:
            logger.warning(f"Error checking thread metadata for conversation {conversation_id}: {str(e)}")
        
        # Not a RAG conversation
        return jsonify({
            'success': True,
            'is_rag_conversation': False,
            'thread_id': None,
            'has_document': False
        })

    except Exception as e:
        logger.error(f"Error getting thread for conversation: {str(e)}")
        return jsonify({'error': f'Failed to get thread: {str(e)}'}), 500


@bp.route('/conversation/<int:conversation_id>/pdf-info', methods=['GET'])
@login_required
def get_pdf_info_for_conversation(conversation_id):
    """
    Get PDF information for a conversation (for tooltip display).
    Returns filename if conversation has a PDF, None otherwise.
    """
    try:
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401

        user_id = session['user_id']
        
        # Method 1: Check if there's a thread with the pattern user_{user_id}_conv_{conversation_id}
        expected_thread_id = f"user_{user_id}_conv_{conversation_id}"
        if thread_has_document(expected_thread_id):
            metadata = thread_document_metadata(expected_thread_id)
            filename = metadata.get('filename', 'Unknown PDF')
            return jsonify({
                'success': True,
                'has_pdf': True,
                'filename': filename,
                'thread_id': expected_thread_id
            })
        
        # Method 2: Check RAGThread table
        db = get_db()
        try:
            threads = db.query(RAGThread).filter_by(user_id=user_id).all()
            for thread in threads:
                import re
                thread_conv_match = re.search(r'user_\d+_conv_(\d+)', thread.thread_id)
                if thread_conv_match:
                    thread_conv_id = int(thread_conv_match.group(1))
                    if thread_conv_id == conversation_id and thread_has_document(thread.thread_id):
                        return jsonify({
                            'success': True,
                            'has_pdf': True,
                            'filename': thread.filename or 'Unknown PDF',
                            'thread_id': thread.thread_id
                        })
        except Exception as e:
            logger.warning(f"Error checking RAGThread table for conversation {conversation_id}: {str(e)}")
        
        # Method 3: Check thread metadata
        try:
            from app.utils.rag_service import _THREAD_METADATA, _load_metadata
            _load_metadata()
            
            for thread_id, metadata in _THREAD_METADATA.items():
                stored_conv_id = metadata.get('conversation_id')
                if stored_conv_id == conversation_id:
                    if _validate_thread_id(thread_id, user_id) and thread_has_document(thread_id):
                        filename = metadata.get('filename', 'Unknown PDF')
                        return jsonify({
                            'success': True,
                            'has_pdf': True,
                            'filename': filename,
                            'thread_id': thread_id
                        })
        except Exception as e:
            logger.warning(f"Error checking thread metadata for conversation {conversation_id}: {str(e)}")
        
        # No PDF found for this conversation
        return jsonify({
            'success': True,
            'has_pdf': False,
            'filename': None,
            'thread_id': None
        })

    except Exception as e:
        logger.error(f"Error getting PDF info for conversation: {str(e)}")
        return jsonify({'error': f'Failed to get PDF info: {str(e)}'}), 500


@bp.route('/thread/<thread_id>', methods=['DELETE'])
@login_required
def delete_thread_route(thread_id):
    """
    Delete a thread and all associated data.
    """
    try:
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401

        user_id = session['user_id']
        
        # Validate thread_id belongs to this user
        if not _validate_thread_id(thread_id, user_id):
            return jsonify({'error': 'Access denied. You can only delete your own threads.'}), 403

        # Delete thread from RAG service (metadata, etc.)
        result = delete_thread(thread_id)
        
        if not result.get('success'):
            return jsonify({'error': result.get('message', 'Failed to delete thread')}), 500

        # Delete thread from database
        db = get_db()
        try:
            thread = db.query(RAGThread).filter_by(thread_id=thread_id).first()
            if thread:
                db.delete(thread)
                db.commit()
                logger.info(f"Deleted thread {thread_id} from database")
            else:
                logger.warning(f"Thread {thread_id} not found in database, but metadata was deleted")
        except Exception as e:
            logger.error(f"Error deleting thread from database: {str(e)}")
            db.rollback()
            # Continue even if database deletion fails - metadata is already deleted

        return jsonify({
            'success': True,
            'message': result.get('message', 'Thread deleted successfully'),
            'thread_id': thread_id
        })

    except Exception as e:
        logger.error(f"Error deleting thread: {str(e)}", exc_info=True)
        return jsonify({'error': f'Failed to delete thread: {str(e)}'}), 500

