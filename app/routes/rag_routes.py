from flask import Blueprint, request, jsonify, session, render_template, Response, stream_with_context, current_app, send_file, redirect
from app.utils.auth import login_required
from app.utils.routes import get_default_route_by_role
from app.utils.rag_service import (
    ingest_pdf,
    chatbot,
    thread_has_document,
    thread_document_metadata,
    get_finalized_lesson,
    save_finalized_lesson,
    update_lesson_finalized_status,
    delete_thread,
    warmup_rag_embeddings,
    MARKDOWN_EXPORTS_DIR,
)
from app.utils.db import get_db
from app.models.database_models import RAGThread, RAGPrompt, UserDocument, RAGChunk
from app.services.chat_service import ChatService
from app.tasks.ingest_tasks import ingest_pdf_task
from langchain_core.messages import HumanMessage
import logging
import threading
import uuid
from datetime import datetime
import os
import json
import time
import base64
logger = logging.getLogger(__name__)
bp = Blueprint('rag', __name__)

# Per-user lock so only one RAG chat request is processed at a time per user (prevents duplicate/concatenated responses)
_user_chat_locks = {}
_locks_lock = threading.Lock()

def _get_user_chat_lock(user_id):
    with _locks_lock:
        if user_id not in _user_chat_locks:
            _user_chat_locks[user_id] = threading.Lock()
        return _user_chat_locks[user_id]


def _strip_lesson_finalization_from_response(text):
    """
    Remove internal lesson-finalization lines from AI response so they are never
    shown on the frontend or stored in chat history.
    """
    if not text or not isinstance(text, str):
        return text
    import re
    # Remove lines like "Lesson Finalized", "lesson_finalized = true", "lesson_title = \"...\""
    cleaned = re.sub(r'^\s*Lesson\s+Finalized\s*$', '', text, flags=re.IGNORECASE | re.MULTILINE)
    cleaned = re.sub(r'^\s*lesson_finalized\s*=\s*(?:true|false)\s*$', '', cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r'^\s*lesson_title\s*=\s*["\'][^"\']*["\']\s*$', '', cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r'^\s*last_lesson_text\s*=\s*.*$', '', cleaned, flags=re.MULTILINE)
    # Collapse multiple blank lines and trim
    cleaned = re.sub(r'\n\s*\n\s*\n+', '\n\n', cleaned).strip()
    return cleaned


def _strip_tool_names_from_response(text):
    """Remove tool names from AI response so they are never shown in chat."""
    if not text or not isinstance(text, str):
        return text
    import re
    # Tool names used by RAG (do not expose to user)
    tool_names = [
        r"rag_tool", r"get_page_tool", r"list_topics_whole_doc_tool",
        r"count_pdf_words_tool", r"count_words_in_text_tool", r"calculator",
        r"duckduckgo_search", r"stock_price",
    ]
    cleaned = text
    for name in tool_names:
        # Remove phrases like "I'll use rag_tool", "Calling get_page_tool", "using rag_tool"
        cleaned = re.sub(rf"\b(?:using|use|call(?:ing)?|invok(?:e|ing)|via)\s+{name}\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(rf"\b{name}\s*(?:to|for)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\n\s*\n\s*\n+", "\n\n", cleaned)
    return cleaned.strip()


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
    Always creates a new unique thread ID for each upload,
    while still encoding the conversation_id when available.
    This allows multiple PDFs per conversation.
    """
    # Generate unique suffix with timestamp and UUID
    unique_id = str(uuid.uuid4())[:8]
    timestamp = int(datetime.utcnow().timestamp())
    if conversation_id:
        # Keep conversation_id in the pattern so existing regex-based
        # logic (e.g. extracting conv_id from thread_id) continues to work,
        # but allow multiple threads per conversation by adding a unique suffix.
        return f"user_{user_id}_conv_{conversation_id}_{timestamp}_{unique_id}"
    return f"user_{user_id}_thread_{timestamp}_{unique_id}"


@bp.route('/chatbot', methods=['GET'])
@login_required
def chatbot_page():
    """
    Render the PDF chat interface.
    """
    try:
        # Legacy behavior previously rendered `chat.html`. Keep backwards
        # compatibility but ensure legacy UI is only reachable explicitly.
        return redirect('/legacy/chat')
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


def _get_thread_id_for_conversation(user_id: int, conversation_id: int):
    """
    Resolve the RAG thread_id for a given conversation (one thread per conversation).
    Returns the most recent thread whose thread_id matches user_X_conv_Y_*.
    """
    db = get_db()
    prefix = f"user_{user_id}_conv_{conversation_id}_"
    row = (
        db.query(RAGThread.thread_id)
        .filter(
            RAGThread.user_id == user_id,
            RAGThread.thread_id.like(prefix + "%"),
            RAGThread.has_document == True,
        )
        .order_by(RAGThread.created_at.desc())
        .first()
    )
    return row.thread_id if row else None


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
        # IMPORTANT: Each new PDF upload must get its own conversation and thread.
        # When create_new_thread is True, we never reuse the current conversation so we
        # never override the existing thread the user is viewing.
        provided_thread_id = request.form.get('thread_id')
        create_new_thread = request.form.get('create_new_thread', 'true').lower() == 'true'
        # For a new PDF we ignore conversation_id from the client so we always create a fresh thread
        conversation_id = None if create_new_thread else request.form.get('conversation_id', type=int)
        
        # If no conversation_id (new upload or not provided), create a new conversation
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
        
        # Verify conversation ownership (but allow multiple PDFs per conversation)
        if conversation_id:
            try:
                from app.models.models import ConversationModel
                conversation_model = ConversationModel(user_id)
                conv = conversation_model.get_conversation_by_id(conversation_id)
                if not conv:
                    # Conversation doesn't exist or doesn't belong to user - create a new one
                    logger.warning(
                        f"Conversation {conversation_id} not found or doesn't belong to user {user_id}, creating new conversation"
                    )
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
            # Always create/use thread for THIS conversation only (never reuse another conversation's thread).
            # Same document uploaded in a new chat gets a new thread_id (user_X_conv_{conversation_id}).
            # Filtration in RAG uses thread_id, so each chat only sees its own document chunks.
            thread_id = _get_thread_id(user_id, conversation_id)
            logger.info(f"Using thread {thread_id} for PDF upload (filename: {file.filename}, conversation_id: {conversation_id})")
        
        filename = file.filename

        # Read file bytes
        file_bytes = file.read()
        if not file_bytes:
            return jsonify({'error': 'File is empty'}), 400

        # If streaming is requested, use SSE for backend processing progress (legacy support)
        if stream_progress:
            return _ingest_with_progress(file_bytes, thread_id, filename, user_id)

        # When Celery is disabled (e.g. local dev), run ingestion synchronously in-process
        use_celery = current_app.config.get('USE_CELERY_FOR_INGESTION', False)
        if not use_celery:
            try:
                # Warm up embedding model first so it's cached before ingestion and first query is fast
                warmup_rag_embeddings()
                result = ingest_pdf(
                    file_bytes=file_bytes,
                    thread_id=thread_id,
                    filename=filename,
                    progress_callback=None,
                    user_id=user_id,
                )
                _save_thread_to_db(user_id, thread_id, filename, ingest_result=result)
                logger.info(f"PDF ingested synchronously: {filename} (thread_id: {thread_id})")
                return jsonify({
                    'success': True,
                    'status': 'success',
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
                })
            except ValueError as e:
                logger.error(f"Value error in sync ingest: {str(e)}")
                return jsonify({'error': str(e)}), 400
            except Exception as e:
                logger.error(f"Error in sync PDF ingestion: {str(e)}", exc_info=True)
                return jsonify({'error': f'Failed to ingest PDF: {str(e)}'}), 500

        # Use Celery for background processing (production)
        file_bytes_b64 = base64.b64encode(file_bytes).decode('utf-8')
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


@bp.route('/download-markdown/<thread_id>', methods=['GET'])
@login_required
def download_markdown(thread_id):
    """Download the PDF-extracted text as a markdown file for the given thread (user must own the thread)."""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    user_id = session['user_id']
    db = get_db()
    thread = db.query(RAGThread).filter_by(thread_id=thread_id, user_id=user_id).first()
    if not thread:
        return jsonify({'error': 'Thread not found or access denied'}), 404
    matches = list(MARKDOWN_EXPORTS_DIR.glob(f"{thread_id}_*.md"))
    if not matches:
        return jsonify({'error': 'Markdown export not found for this document'}), 404
    md_path = matches[0]
    download_name = (thread.filename or "document").rstrip(".pdf").rstrip(".PDF") or "document"
    if not download_name.endswith(".md"):
        download_name += ".md"
    return send_file(
        str(md_path),
        as_attachment=True,
        download_name=download_name,
        mimetype="text/markdown",
    )


def _save_thread_to_db(user_id: int, thread_id: str, filename: str, ingest_result: dict = None):
    """Save or update RAG thread in database. On success, set has_document, doc_count, num_pages."""
    db = get_db()
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
            logger.info("Updated thread %s with has_document=true, doc_count=%s", thread_id, existing_thread.doc_count)
        else:
            if not existing_thread.has_document:
                existing_thread.filename = filename
                existing_thread.updated_at = now
                db.commit()
    except Exception as e:
        logger.error("Error saving thread to database: %s", e)
        db.rollback()


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
                        progress_callback=progress_callback,
                        user_id=user_id,
                    )
                    # Ensure thread_id is in the result
                    if 'thread_id' not in result:
                        result['thread_id'] = thread_id
                    result_container['result'] = result
                    _save_thread_to_db(user_id, thread_id, filename, ingest_result=result)
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
                res = dict(result_container['result'])
                res['markdown_download_url'] = f'/api/rag/download-markdown/{res.get("thread_id", "")}'
                yield f"data: {json.dumps({'step': 'complete', 'progress': 100, 'message': 'PDF ingestion complete!', 'result': res})}\n\n"
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
    import json as _json
    # Debug log path under project root (works on any machine); skip log if path missing/unwritable
    _project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    _log_dir = os.path.join(_project_root, 'logs')
    _log_path = os.path.join(_log_dir, 'rag_debug.log')
    try:
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401

        user_id = session['user_id']

        # If audio is sent (voice input), return error - RAG uses Groq/vLLM only, no OpenAI Whisper
        audio_file = request.files.get('audio')
        if audio_file and audio_file.filename:
            return jsonify({
                'error': 'Voice input is not supported for RAG chat. Please use text input.',
                'code': 'VOICE_NOT_SUPPORTED'
            }), 400

        # If audio is sent (voice input), transcribe using local Whisper base model
        audio_text = None
        tmp_path = None
        try:
            audio_file = request.files.get('audio')
            if audio_file and audio_file.filename:
                from app.utils.whisper_stt import transcribe_audio
                with NamedTemporaryFile(delete=False, suffix=".webm") as tmp:
                    audio_file.save(tmp.name)
                    tmp_path = tmp.name
                audio_text = transcribe_audio(tmp_path)
        except Exception as stt_error:
            logger.error(f"Error transcribing audio for RAG chat: {str(stt_error)}", exc_info=True)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        
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
        # FormData with audio file: form may have thread_id/conversation_id but no message; use transcription
        if data and not (data.get('message') or '').strip() and audio_text:
            data['message'] = audio_text
            if data.get('thread_id') is None and request.form:
                data['thread_id'] = request.form.get('thread_id') or None
            if data.get('conversation_id') is None and request.form:
                try:
                    data['conversation_id'] = request.form.get('conversation_id', type=int) or None
                except (TypeError, ValueError):
                    data['conversation_id'] = None

        if not data:
            logger.warning("RAG chat 400: No data provided (no JSON/form/audio)")
            return jsonify({'error': 'No data provided. Please send JSON, form-data with "message" field, or an audio file.', 'code': 'NO_DATA'}), 400

        message = (data.get('message') or '').strip()
        if not message:
            logger.warning("RAG chat 400: Empty message")
            return jsonify({'error': 'Message is required', 'code': 'MESSAGE_REQUIRED'}), 400

        # thread_id is REQUIRED for RAG chat (no auto-create, no auto-pick)
        provided_thread_id = data.get('thread_id')
        if isinstance(provided_thread_id, str):
            provided_thread_id = provided_thread_id.strip() or None
        raw_conversation_id = data.get('conversation_id')
        if raw_conversation_id == '' or (isinstance(raw_conversation_id, str) and not raw_conversation_id.strip()):
            raw_conversation_id = None
        conversation_id = None
        if raw_conversation_id is not None:
            try:
                conversation_id = int(raw_conversation_id) if not isinstance(raw_conversation_id, int) else raw_conversation_id
            except (TypeError, ValueError):
                conversation_id = None

        if provided_thread_id:
            thread_id = str(provided_thread_id).strip()
        elif conversation_id is not None:
            # Resolve thread from conversation so each chat uses its own document (no cross-talk)
            thread_id = _get_thread_id_for_conversation(user_id, conversation_id)
            if not thread_id:
                logger.warning("RAG chat 400: No thread with document for conversation_id=%s user_id=%s", conversation_id, user_id)
                return jsonify({
                    "error": "No PDF has been uploaded for this conversation yet. Please upload a PDF document first to chat with your document.",
                    "code": "NO_DOCUMENT_UPLOADED",
                }), 400
        else:
            logger.warning("RAG chat 400: Missing thread_id and conversation_id (user_id=%s)", user_id)
            return jsonify({
                'error': 'thread_id or conversation_id is required for RAG chat. Please select a conversation with an uploaded PDF or upload a PDF first.',
                'code': 'MISSING_THREAD_ID'
            }), 400

        # Validate thread exists and belongs to user
        db = get_db()
        thread_row = db.query(RAGThread).filter_by(thread_id=thread_id, user_id=user_id).first()
        if not thread_row:
            logger.warning("RAG chat 400: No thread found for thread_id=%s user_id=%s", thread_id, user_id)
            return jsonify({
                'error': 'No PDF has been uploaded for this conversation yet. Please upload a PDF document first to chat with your document.',
                'code': 'NO_DOCUMENT_UPLOADED',
                'thread_id': thread_id
            }), 400

        if not getattr(thread_row, 'has_document', False):
            # Auto-repair: if chunks exist in DB, ingest completed but has_document was never set
            chunk_count = db.query(RAGChunk).filter_by(thread_id=thread_id, user_id=user_id).count()
            if chunk_count > 0:
                try:
                    thread_row.has_document = True
                    if getattr(thread_row, 'doc_count', None) is None or thread_row.doc_count == 0:
                        thread_row.doc_count = chunk_count
                    db.commit()
                    logger.info("RAG chat: repaired has_document=True for thread_id=%s (chunk_count=%s)", thread_id, chunk_count)
                except Exception as e:
                    logger.warning("RAG chat: failed to repair has_document for thread_id=%s: %s", thread_id, e)
                    db.rollback()
                    return jsonify({
                        'error': 'Your PDF may still be processing. Please wait a moment and try again, or upload the PDF again.',
                        'code': 'NO_DOCUMENT',
                        'thread_id': thread_id
                    }), 400
            else:
                logger.warning("RAG chat 400: Thread exists but has_document=False and no chunks thread_id=%s", thread_id)
                return jsonify({
                    'error': 'Your PDF may still be processing. Please wait a moment and try again, or upload the PDF again.',
                    'code': 'NO_DOCUMENT',
                    'thread_id': thread_id
                }), 400

        # One message at a time per user: queue by rejecting concurrent requests
        user_lock = _get_user_chat_lock(user_id)
        if not user_lock.acquire(blocking=False):
            return jsonify({
                'error': 'Please wait for your current message to be answered before sending another.',
                'code': 'CONCURRENT_REQUEST'
            }), 429

        try:
            # Prepare config for LangGraph
            config = {
                "configurable": {
                    "thread_id": thread_id
                }
            }
            # #region agent log
            try:
                os.makedirs(_log_dir, exist_ok=True)
                with open(_log_path, 'a', encoding='utf-8') as _f:
                    _f.write(_json.dumps({"location": "rag_routes.py:chat:thread_resolved", "message": "thread_id resolved", "data": {"thread_id": thread_id}, "timestamp": __import__('time').time() * 1000, "sessionId": "debug-session", "hypothesisId": "H2,H4"}) + "\n")
            except Exception:
                pass
            # #endregion
            # Create HumanMessage
            human_message = HumanMessage(content=message)

            # Invoke the chatbot - LangGraph returns the final state
            # #region agent log
            try:
                with open(_log_path, 'a', encoding='utf-8') as _f:
                    _f.write(_json.dumps({"location": "rag_routes.py:chat:invoke_start", "message": "chatbot.invoke start", "data": {}, "timestamp": __import__('time').time() * 1000, "sessionId": "debug-session", "hypothesisId": "H4"}) + "\n")
            except Exception:
                pass
            # #endregion
            state = chatbot.invoke(
                {"messages": [human_message]},
                config=config
            )
            # #region agent log
            _msgs = state.get("messages", [])
            try:
                with open(_log_path, 'a', encoding='utf-8') as _f:
                    _f.write(_json.dumps({"location": "rag_routes.py:chat:invoke_done", "message": "chatbot.invoke done", "data": {"messages_len": len(_msgs)}, "timestamp": __import__('time').time() * 1000, "sessionId": "debug-session", "hypothesisId": "H4"}) + "\n")
            except Exception:
                pass
            # #endregion

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

            # Remove internal lesson-finalization fields and tool names so they never appear on frontend
            response_content = _strip_lesson_finalization_from_response(response_content)
            response_content = _strip_tool_names_from_response(response_content)

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
                    # Extract conversation_id from thread_id (format: user_{user_id}_conv_{conversation_id})
                    import re
                    thread_conv_match = re.search(r'user_\d+_conv_(\d+)', thread_id)
                    if thread_conv_match:
                        db_conversation_id = int(thread_conv_match.group(1))
                        conv = conversation_model.get_conversation_by_id(db_conversation_id)
                        if not conv:
                            db_conversation_id = conversation_model.create_conversation(
                                title=message[:50] if len(message) > 50 else message
                            )
                        logger.info("Extracted conversation_id %s from thread_id %s", db_conversation_id, thread_id)
                    else:
                        db_conversation_id = conversation_model.create_conversation(
                            title=message[:50] if len(message) > 50 else message
                        )
                        logger.info("Created new conversation %s for thread %s", db_conversation_id, thread_id)
                
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

            # #region agent log
            try:
                with open(_log_path, 'a', encoding='utf-8') as _f:
                    _f.write(_json.dumps({"location": "rag_routes.py:chat:return_success", "message": "returning success", "data": {"response_len": len(response_content)}, "timestamp": __import__('time').time() * 1000, "sessionId": "debug-session", "hypothesisId": "H4,H5"}) + "\n")
            except Exception:
                pass
            # #endregion
            return jsonify({
                'success': True,
                'message': response_content,
                'thread_id': thread_id,
                'conversation_id': db_conversation_id if db_conversation_id else conversation_id,
                'has_document': thread_has_document(thread_id)
            })
        finally:
            user_lock.release()

    except Exception as e:
        # #region agent log
        try:
            os.makedirs(_log_dir, exist_ok=True)
            with open(_log_path, 'a', encoding='utf-8') as _f:
                _f.write(_json.dumps({"location": "rag_routes.py:chat:exception", "message": "chat exception", "data": {"error": str(e)}, "timestamp": __import__('time').time() * 1000, "sessionId": "debug-session", "hypothesisId": "H4"}) + "\n")
        except Exception:
            pass
        # #endregion
        logger.error(f"Error in RAG chat: {str(e)}", exc_info=True)
        return jsonify({'error': f'Failed to process chat: {str(e)}'}), 500


@bp.route('/ingest/cancel/<task_id>', methods=['POST'])
@login_required
def cancel_ingest(task_id):
    """
    Gracefully cancel a PDF ingestion task (Celery revoke).
    When USE_CELERY_FOR_INGESTION is False, returns 400.
    """
    try:
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401
        if not current_app.config.get('USE_CELERY_FOR_INGESTION', False):
            return jsonify({
                'error': 'Cancel is not available. PDF ingestion is running in-process (Celery is disabled).'
            }), 400
        task = ingest_pdf_task.AsyncResult(task_id)
        if task.state not in ('PENDING', 'PROCESSING', 'RETRY'):
            return jsonify({'message': 'Task already finished or not found', 'task_id': task_id}), 200
        task.revoke(terminate=True)
        logger.info(f"User {session['user_id']} cancelled ingest task {task_id}")
        return jsonify({'message': 'Upload cancelled', 'task_id': task_id, 'status': 'revoked'}), 200
    except Exception as e:
        logger.error(f"Error cancelling ingest task: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.route('/ingest/status/<task_id>', methods=['GET'])
@login_required
def get_ingest_status(task_id):
    """
    Get the status of a PDF ingestion task.
    Returns task status and progress information.
    Validates that the task belongs to the requesting user.
    When USE_CELERY_FOR_INGESTION is False, ingestion is synchronous and this endpoint is unused.
    """
    try:
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401

        if not current_app.config.get('USE_CELERY_FOR_INGESTION', False):
            return jsonify({
                'error': 'Task status is not available. PDF ingestion is running in-process (Celery is disabled).'
            }), 400

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
            # Task completed successfully - warm up embedding model in this process so first query is fast
            warmup_rag_embeddings()
            result = task.result
            thread_id = result.get('thread_id')
            response = {
                'task_id': task_id,
                'status': 'success',
                'state': task.state,
                'message': result.get('message', 'PDF ingested successfully'),
                'thread_id': thread_id,
                'conversation_id': result.get('conversation_id'),
                'filename': result.get('filename'),
                'documents': result.get('documents', result.get('num_pages', 0)),
                'num_pages': result.get('num_pages', result.get('documents', 0)),
                'pages': result.get('pages', result.get('num_pages', result.get('documents', 0))),
                'chunks': result.get('chunks', 0),
                'markdown_download_url': result.get('markdown_download_url') or (f'/api/rag/download-markdown/{thread_id}' if thread_id else None),
                'processing_time_seconds': result.get('processing_time_seconds'),
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
        # Do not expose lesson-finalization fields to frontend
        if isinstance(metadata, dict):
            metadata = {k: v for k, v in metadata.items() if k not in ('lesson_finalized', 'lesson_title', 'last_lesson_text')}
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
                    'has_document': getattr(thread, 'has_document', False)
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


@bp.route('/thread/<thread_id>/finalized-lesson', methods=['GET'])
@login_required
def get_finalized_lesson_route(thread_id):
    """
    Get the last finalized lesson for a thread (last_lesson_text, lesson_title).
    Used by the frontend so the download/save button uses the finalized lecture, not the last message.
    """
    try:
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401

        user_id = session['user_id']

        if not _validate_thread_id(thread_id, user_id):
            return jsonify({'error': 'Access denied. You can only access your own threads.'}), 403

        lesson = get_finalized_lesson(thread_id)
        if lesson is None:
            return jsonify({'error': 'Thread not found'}), 404

        return jsonify({
            'success': True,
            'last_lesson_text': lesson.get('last_lesson_text', ''),
            'lesson_title': lesson.get('lesson_title', ''),
            'lesson_finalized': lesson.get('lesson_finalized', False),
        })
    except Exception as e:
        logger.error(f"Error getting finalized lesson: {str(e)}")
        return jsonify({'error': f'Failed to get finalized lesson: {str(e)}'}), 500


@bp.route('/thread/<thread_id>/finalized-lesson', methods=['PUT'])
@login_required
def put_finalized_lesson_route(thread_id):
    """
    Save the finalized lesson (last_lesson_text, lesson_title) for a thread.
    Used when the UI shows "Lesson Finalized: true" but the backend did not persist it (e.g. Groq).
    """
    try:
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401

        user_id = session['user_id']

        if not _validate_thread_id(thread_id, user_id):
            return jsonify({'error': 'Access denied. You can only access your own threads.'}), 403

        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        last_lesson_text = data.get('last_lesson_text', '')
        lesson_title = data.get('lesson_title', '')
        if not (last_lesson_text or '').strip():
            return jsonify({'error': 'last_lesson_text is required'}), 400

        success = save_finalized_lesson(thread_id, last_lesson_text, lesson_title)
        if not success:
            return jsonify({'error': 'Thread not found'}), 404

        return jsonify({
            'success': True,
            'message': 'Finalized lesson saved',
            'thread_id': thread_id,
        })
    except Exception as e:
        logger.error(f"Error saving finalized lesson: {str(e)}")
        return jsonify({'error': f'Failed to save finalized lesson: {str(e)}'}), 500


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
        
        # Method 1: Check DB for thread with conversation pattern
        expected_thread_id = f"user_{user_id}_conv_{conversation_id}"
        db = get_db()
        thread_row = db.query(RAGThread).filter_by(thread_id=expected_thread_id, user_id=user_id).first()
        if thread_row and thread_row.has_document:
            return jsonify({
                'success': True,
                'is_rag_conversation': True,
                'thread_id': expected_thread_id,
                'has_document': True
            })
        
        # Method 2: Check RAGThread table (DB-based)
        db = get_db()
        try:
            threads = db.query(RAGThread).filter_by(user_id=user_id).all()
            for thread in threads:
                import re
                thread_conv_match = re.search(r'user_\d+_conv_(\d+)', thread.thread_id)
                if thread_conv_match:
                    thread_conv_id = int(thread_conv_match.group(1))
                    if thread_conv_id == conversation_id and thread.has_document:
                        return jsonify({
                            'success': True,
                            'is_rag_conversation': True,
                            'thread_id': thread.thread_id,
                            'has_document': True,
                            'filename': thread.filename
                        })
        except Exception as e:
            logger.warning("Error checking RAGThread for conversation %s: %s", conversation_id, e)
        
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
        
        # Method 1: Check DB for thread with conversation pattern
        expected_thread_id = f"user_{user_id}_conv_{conversation_id}"
        db = get_db()
        thread_row = db.query(RAGThread).filter_by(thread_id=expected_thread_id, user_id=user_id).first()
        if thread_row and thread_row.has_document:
            # Warm up embedding model in background so first query in this chat is fast
            t = threading.Thread(target=warmup_rag_embeddings, daemon=True)
            t.start()
            return jsonify({
                'success': True,
                'has_pdf': True,
                'filename': thread_row.filename or 'Unknown PDF',
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
                    if thread_conv_id == conversation_id and thread.has_document:
                        t = threading.Thread(target=warmup_rag_embeddings, daemon=True)
                        t.start()
                        return jsonify({
                            'success': True,
                            'has_pdf': True,
                            'filename': thread.filename or 'Unknown PDF',
                            'thread_id': thread.thread_id
                        })
        except Exception as e:
            logger.warning("Error checking RAGThread for conversation %s: %s", conversation_id, e)
        
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

