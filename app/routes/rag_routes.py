from flask import Blueprint, request, jsonify, session, render_template, Response, stream_with_context, current_app, send_file, redirect, g
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
    clear_thread_conversation_history,
    warmup_rag_embeddings,
    MARKDOWN_EXPORTS_DIR,
    DEFAULT_RAG_CHAT_SYSTEM_BODY_WITH_PDF,
    RAG_SYSTEM_SETTING_KEY_WITH_PDF,
    _get_stored_rag_system_template,
    _substitute_rag_system_placeholders,
    _rag_chat_streaming_enabled,
)
from app.utils.rag_token_sink import (
    QueueRagTokenSink,
    get_rag_token_sink,
    reset_rag_token_sink,
    set_rag_token_sink,
)
from app.utils.groq_rate_limit import GroqRateLimitError, GroqBusyError
from app.utils.chat_lock import acquire_chat_lock, release_chat_lock
from app.utils.db import get_db
from app.models.database_models import RAGThread, RAGPrompt, UserDocument, RAGChunk, RAGHeading
from app.services.chat_service import ChatService
from app.services.conversation_summary_service import ConversationSummaryService
from app.tasks.ingest_tasks import ingest_pdf_task, extract_headings_task
from langchain_core.messages import HumanMessage
import logging
import queue
import threading
import uuid
from datetime import datetime, timedelta
import os
import re
import json
import time
import base64
import tempfile
import zlib
logger = logging.getLogger(__name__)
bp = Blueprint('rag', __name__)
_CANCELLED_UPLOAD_FILENAME = "__CANCELLED_UPLOAD__"

# Max word count for user-supplied RAG custom prompt (teacher / user UI)
RAG_USER_PROMPT_MAX_WORDS = int(os.getenv("RAG_USER_PROMPT_MAX_WORDS", "300"))


def _count_words(text: str) -> int:
    if not text or not str(text).strip():
        return 0
    return len(re.findall(r"\S+", str(text).strip()))

# Per-thread cross-process lock so only one RAG chat request is processed at a time per
# thread (prevents duplicate/concatenated/cross-wired responses) - see app/utils/chat_lock.py
# for why this must be Redis-backed rather than a plain in-process threading.Lock().
_user_chat_rate = {}
_rate_lock = threading.Lock()


def _check_and_record_user_chat_rate(user_id):
    """
    Per-user burst throttling. Returns (allowed: bool, retry_after_sec: int).
    Uses a sliding window with conservative defaults to protect backend stability.
    """
    max_requests = int(os.getenv("RAG_USER_RATE_LIMIT_COUNT", "6"))
    window_seconds = int(os.getenv("RAG_USER_RATE_LIMIT_WINDOW_SECONDS", "10"))
    now = time.time()
    with _rate_lock:
        history = _user_chat_rate.get(user_id, [])
        cutoff = now - window_seconds
        history = [ts for ts in history if ts >= cutoff]
        if len(history) >= max_requests:
            oldest = history[0]
            retry_after = max(1, int(window_seconds - (now - oldest)))
            _user_chat_rate[user_id] = history
            return False, retry_after
        history.append(now)
        _user_chat_rate[user_id] = history
    return True, 0


def _client_wants_rag_stream(data) -> bool:
    """True only when the client explicitly asked for SSE. Old clients stay on JSON."""
    if not isinstance(data, dict):
        return False
    value = data.get("stream")
    if value is True:
        return True
    if isinstance(value, (int, float)) and value == 1:
        return True
    if isinstance(value, str) and value.strip().lower() in ("true", "1", "yes"):
        return True
    return False


def _is_transient_db_connection_error(exc: Exception) -> bool:
    msg = str(exc or "").lower()
    markers = (
        "server closed the connection unexpectedly",
        "consuming input failed",
        "connection not open",
        "connection reset by peer",
        "ssl syserror: eof detected",
        "terminating connection due to administrator command",
    )
    return any(marker in msg for marker in markers)


def _get_ingest_tier(file_size_mb: float) -> dict:
    """
    Resolve ingest execution strategy by file size.
    Large docs get longer task limits and a dedicated queue.
    """
    threshold_mb = float(os.getenv("RAG_LARGE_DOC_THRESHOLD_MB", "40"))
    is_large = float(file_size_mb) > threshold_mb
    if is_large:
        return {
            "name": "large",
            "queue": os.getenv("RAG_INGEST_LARGE_QUEUE", "ingest_large"),
            "soft_time_limit": int(os.getenv("RAG_INGEST_LARGE_SOFT_TIME_LIMIT", "5400")),
            "time_limit": int(os.getenv("RAG_INGEST_LARGE_TIME_LIMIT", "6000")),
            "stream_join_timeout": int(os.getenv("RAG_INGEST_LARGE_STREAM_TIMEOUT", "1800")),
        }
    return {
        "name": "standard",
        "queue": os.getenv("RAG_INGEST_STANDARD_QUEUE", "ingest"),
        "soft_time_limit": int(os.getenv("RAG_INGEST_STANDARD_SOFT_TIME_LIMIT", "2400")),
        "time_limit": int(os.getenv("RAG_INGEST_STANDARD_TIME_LIMIT", "2700")),
        "stream_join_timeout": int(os.getenv("RAG_INGEST_STANDARD_STREAM_TIMEOUT", "600")),
    }


def _preflight_check_pdf(file_bytes: bytes, max_scan_pages: int = 25, timeout_seconds: int = 15) -> dict:
    """
    Runs `_preflight_check_pdf_inner` with a hard wall-clock timeout.

    PyMuPDF is a C extension: a malformed PDF can make it hang inside
    `fz_open_document` with no Python-level exception to catch, which would
    otherwise block the request thread forever. Running it in a daemon
    thread with a bounded `.join()` means a hang costs one leaked thread
    instead of an unresponsive request/worker.
    """
    import threading

    box = {}

    def _run():
        box['result'] = _preflight_check_pdf_inner(file_bytes, max_scan_pages)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout_seconds)

    if t.is_alive():
        return {
            'ok': False,
            'code': 'PDF_VALIDATION_TIMEOUT',
            'message': (
                'This PDF took too long to validate and may be malformed or '
                'unusually complex. Please try a different file.'
            ),
        }
    return box.get('result') or {
        'ok': False,
        'code': 'PDF_UNREADABLE',
        'message': 'This file could not be validated. Please check the file and upload it again.',
    }


_STREAM_START_RE = re.compile(rb'stream\r?\n')
_MAX_BOMB_SCAN_STREAMS = 200


def _check_decompression_bomb(file_bytes: bytes, max_output_mb: int = 60) -> str:
    """
    Cheap pre-scan for PDF decompression bombs: a FlateDecode stream that
    expands far beyond anything a legitimate single PDF stream (a page's
    content, one embedded font, one image) should ever need. Runs on the
    raw bytes before fitz/pypdf ever open the file, so a bomb is rejected
    before paying the cost of fully decompressing it.

    Scoped to FlateDecode, the overwhelmingly common PDF compression
    filter — other filters still fall back on the existing 100 MB total
    upload-size cap. Returns an error message string, or '' if clean.
    """
    max_output_bytes = max_output_mb * 1024 * 1024
    scanned = 0
    for match in _STREAM_START_RE.finditer(file_bytes):
        if scanned >= _MAX_BOMB_SCAN_STREAMS:
            break
        start = match.end()
        end = file_bytes.find(b'endstream', start)
        if end == -1:
            continue
        raw = file_bytes[start:end]
        scanned += 1
        if len(raw) < 256:
            continue  # too small to be a bomb
        try:
            decompressor = zlib.decompressobj()
            out_len = 0
            out = decompressor.decompress(raw, max_output_bytes + 1)
            out_len += len(out)
            while decompressor.unconsumed_tail and out_len <= max_output_bytes:
                out = decompressor.decompress(decompressor.unconsumed_tail, max_output_bytes + 1 - out_len)
                if not out:
                    break
                out_len += len(out)
            if out_len > max_output_bytes:
                return (
                    f"This PDF contains a compressed stream that expands to over {max_output_mb} MB "
                    "when decompressed, which is far larger than any normal document needs. It was "
                    "rejected to protect server memory — please upload a different file."
                )
        except zlib.error:
            continue  # not a zlib stream at this offset — not what we're checking for
    return ''


def _preflight_check_pdf_inner(file_bytes: bytes, max_scan_pages: int) -> dict:
    """
    Fast, synchronous check run at upload time, before the (potentially slow)
    background ingestion is ever queued. Catches PDFs that can never be
    ingested — corrupted files, password-protected files, and scanned/
    image-only files with no selectable text — so the user is told
    immediately, instead of the file being queued for background processing
    and only failing later (which previously surfaced as a misleading
    "PDF may still be processing" message when the user tried to chat).

    Only scans the first `max_scan_pages` pages so this stays fast even for
    large documents; the real ingestion step still processes every page.
    """
    import fitz  # PyMuPDF

    bomb_message = _check_decompression_bomb(file_bytes)
    if bomb_message:
        return {'ok': False, 'code': 'PDF_TOO_COMPLEX', 'message': bomb_message}

    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception:
        return {
            'ok': False,
            'code': 'PDF_UNREADABLE',
            'message': (
                'This file could not be opened as a PDF. It may be corrupted '
                'or not actually a PDF file. Please check the file and upload it again.'
            ),
        }

    try:
        if doc.is_encrypted and not doc.authenticate(""):
            return {
                'ok': False,
                'code': 'PDF_PASSWORD_PROTECTED',
                'message': (
                    'This PDF is password-protected. Please remove the password '
                    'and upload it again.'
                ),
            }

        if doc.page_count == 0:
            return {
                'ok': False,
                'code': 'PDF_EMPTY',
                'message': 'This PDF has no pages.',
            }

        has_text = False
        has_images = False
        for i, page in enumerate(doc):
            if i >= max_scan_pages:
                break
            if not has_text and (page.get_text("text") or "").strip():
                has_text = True
            if not has_images and page.get_images():
                has_images = True
            if has_text and has_images:
                break

        if not has_text:
            scanned_hint = (
                " It looks like a scanned document (it contains images but no selectable text)."
                if has_images else ""
            )
            return {
                'ok': False,
                'code': 'PDF_NO_TEXT',
                'message': (
                    f'This PDF has no readable text content.{scanned_hint} '
                    'OCR support is required for scanned documents — please upload '
                    'a PDF with real, selectable text instead.'
                ),
            }

        return {'ok': True, 'has_images': has_images}
    except Exception:
        return {
            'ok': False,
            'code': 'PDF_UNREADABLE',
            'message': (
                'This file could not be read as a PDF. It may be corrupted. '
                'Please check the file and upload it again.'
            ),
        }
    finally:
        try:
            doc.close()
        except Exception:
            pass


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


def _strip_internal_reasoning_from_response(text):
    """
    Remove leaked internal planning/rules blocks from model output.
    This is a defensive filter for prompt-leak style responses.
    """
    if not text or not isinstance(text, str):
        return text
    import re

    cleaned = text
    t,h,i,n,k = map(chr, (116,104,105,110,107))
    think = t+h+i+n+k
    think_o = chr(60) + think + chr(62)
    think_c = chr(60) + chr(47) + think + chr(62)
    red = "redacted_thinking"
    red_o = chr(60) + red + chr(62)
    red_c = chr(60) + chr(47) + red + chr(62)
    pattern = think_o + r"[\s\S]*?" + think_c + "|" + red_o + r"[\s\S]*?" + red_c
    cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()

    # Common leaked headings/sections that should never be shown to end users.
    leak_markers = [
        "Assistant's Rules Summary for This Conversation",
        "Document Access Tools",
        "Word Count Rules",
        "Page-specific queries:",
        "General queries:",
        "I need to figure out how to handle their queries",
        "Just the final answer based on the tool outputs provided.",
    ]

    # If a leak marker appears, drop everything from the marker onward.
    marker_positions = [cleaned.find(marker) for marker in leak_markers if marker in cleaned]
    if marker_positions:
        cut_at = min(marker_positions)
        cleaned = cleaned[:cut_at]

    # Remove obvious preamble labels often produced in leaked meta output.
    cleaned = re.sub(r"^\s*AI Assistant\s*\n", "", cleaned, flags=re.IGNORECASE)

    # Remove known chain-of-thought style opener when it appears as standalone prose.
    cleaned = re.sub(
        r"^\s*Okay,\s*let'?s\s+see\.[\s\S]{0,280}?(?=\n\n|\n[A-Z][^\n]*:|$)",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    # Remove common meta/reasoning paragraphs if they leak.
    meta_phrases = [
        "the user uploaded a pdf called",
        "i need to figure out how to handle",
        "if they ask",
        "for generating lessons",
        "when finalizing",
        "if the user's question isn't related to the pdf",
        "do not mention internal tools or processes",
        "based on the tool outputs",
    ]
    parts = [p.strip() for p in re.split(r"\n\s*\n", cleaned) if p.strip()]
    if parts:
        filtered_parts = [
            p for p in parts
            if not any(phrase in p.lower() for phrase in meta_phrases)
        ]
        if filtered_parts:
            cleaned = "\n\n".join(filtered_parts)

    cleaned = re.sub(r"\n\s*\n\s*\n+", "\n\n", cleaned).strip()

    # Ensure we do not return an empty message after sanitization.
    if not cleaned:
        return "I can help with your uploaded PDF. Please ask your question again and I will answer directly from the document."
    return cleaned


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
    NOTE: Do not require has_document=True here; under heavy async load,
    the worker may finish chunk writes before the thread flag update commits.
    Chat flow already validates/repairs has_document based on chunk presence.
    """
    db = get_db()
    prefix = f"user_{user_id}_conv_{conversation_id}_"
    row = (
        db.query(RAGThread.thread_id)
        .filter(
            RAGThread.user_id == user_id,
            RAGThread.thread_id.like(prefix + "%"),
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

        # Enforce a hard maximum upload size to avoid extremely large PDFs
        # causing ingest timeouts under load (e.g. 90MB+ scanned documents).
        # Default: 100 MB, configurable via MAX_RAG_PDF_MB.
        import os
        max_mb = int(os.getenv("MAX_RAG_PDF_MB", "100"))
        file.seek(0, os.SEEK_END)
        size_bytes = file.tell()
        file.seek(0)
        size_mb = size_bytes / (1024 * 1024)
        ingest_tier = _get_ingest_tier(size_mb)
        if size_mb > max_mb:
            return jsonify({
                'error': f'PDF is too large for ingestion ({size_mb:.1f} MB). '
                         f'Maximum allowed size is {max_mb} MB. '
                         'Please upload a smaller or split document.',
                'code': 'PDF_TOO_LARGE'
            }), 400

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

        # Read file bytes once so we can reuse them for sync/SSE paths
        file_bytes = file.read()
        if not file_bytes:
            return jsonify({'error': 'File is empty'}), 400

        # Reject PDFs that can never be ingested (corrupted, password-protected,
        # or scanned/image-only with no selectable text) right now, instead of
        # queuing them for background processing and only finding out later.
        preflight = _preflight_check_pdf(file_bytes)
        if not preflight['ok']:
            return jsonify({'error': preflight['message'], 'code': preflight['code']}), 400

        # If streaming is requested, use SSE for backend processing progress (legacy support)
        if stream_progress:
            return _ingest_with_progress(
                file_bytes,
                thread_id,
                filename,
                user_id,
                join_timeout_seconds=ingest_tier["stream_join_timeout"],
            )

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
                # Kick off background heading extraction in-process when Celery is disabled
                try:
                    app_obj = current_app._get_current_object()
                    load_test_mode = current_app.config.get("LOAD_TEST_MODE", False) or str(current_app.config.get("ENV", "")).lower() == "staging"
                    enable_headings = current_app.config.get("ENABLE_RAG_HEADINGS", True)
                    delay_headings_for_load_test = current_app.config.get("DELAY_RAG_HEADINGS_FOR_LOAD_TEST", False)
                    if enable_headings and not (load_test_mode and delay_headings_for_load_test):
                        _start_heading_extraction_background(thread_id, user_id, app_obj)
                    elif enable_headings and load_test_mode and delay_headings_for_load_test:
                        # Load-test mode: delay headings extraction so it doesn't contend with ingestion.
                        delay_seconds = current_app.config.get("RAG_HEADINGS_DELAY_SECONDS", 30)
                        t = threading.Timer(
                            delay_seconds,
                            _start_heading_extraction_background,
                            args=(thread_id, user_id, app_obj),
                        )
                        t.daemon = True
                        t.start()
                except Exception as bg_err:
                    logger.error(
                        "Failed to start background heading extraction for thread %s (sync mode): %s",
                        thread_id,
                        bg_err,
                        exc_info=True,
                    )
                logger.info(f"PDF ingested synchronously: {filename} (thread_id: {thread_id})")
                return jsonify({
                    'success': True,
                    'status': 'success',
                    'message': 'PDF ingested successfully',
                    'thread_id': thread_id,
                    'conversation_id': conversation_id,
                    'ingest_tier': ingest_tier["name"],
                    'filename': result.get('filename', filename),
                    'documents': result.get('documents', result.get('num_pages', 0)),
                    'num_pages': result.get('num_pages', result.get('documents', 0)),
                    'pages': result.get('pages', result.get('num_pages', result.get('documents', 0))),
                    'chunks': result.get('chunks', 0),
                    'markdown_download_url': f'/api/rag/download-markdown/{thread_id}',
                    'processing_time_seconds': result.get('processing_time_seconds'),
                    'warning': result.get('warning'),
                })
            except ValueError as e:
                logger.error(f"Value error in sync ingest: {str(e)}")
                return jsonify({'error': str(e)}), 400
            except Exception as e:
                logger.error(f"Error in sync PDF ingestion: {str(e)}", exc_info=True)
                return jsonify({'error': f'Failed to ingest PDF: {str(e)}'}), 500

        # Use Celery for background processing (production).
        # To keep Redis payloads small and avoid broker memory pressure under load,
        # we write the upload to a temporary file and pass only the file path.
        # Pre-create/update the thread row before queueing so conversation->thread
        # resolution is available immediately even if worker-side DB flag update
        # is delayed or transiently fails under load.
        try:
            _ingest_deadline = datetime.utcnow() + timedelta(seconds=ingest_tier["time_limit"] + 60)
            _save_thread_to_db(user_id, thread_id, filename, ingest_result=None, ingest_deadline_at=_ingest_deadline)
        except Exception:
            logger.warning("Failed to precreate thread row for thread_id=%s", thread_id, exc_info=True)

        configured_tmp_dir = current_app.config.get("UPLOAD_TEMP_DIR") or "/app/tmp"
        fallback_tmp_dir = os.path.join(tempfile.gettempdir(), "iqbalai_uploads")
        tmp_dir = configured_tmp_dir
        try:
            os.makedirs(tmp_dir, exist_ok=True)
            if not os.access(tmp_dir, os.W_OK):
                raise OSError(f"Upload temp dir is not writable: {tmp_dir}")
        except OSError:
            # In some staging/container setups `/app` can be read-only.
            # Fall back to system temp so ingestion still works.
            tmp_dir = fallback_tmp_dir
            os.makedirs(tmp_dir, exist_ok=True)
        tmp_kwargs = {"delete": False, "suffix": ".pdf", "dir": tmp_dir}
        with tempfile.NamedTemporaryFile(**tmp_kwargs) as tmp:
            tmp.write(file_bytes)
            tmp.flush()
            tmp_path = tmp.name

        task = ingest_pdf_task.apply_async(
            kwargs={
                "file_path": tmp_path,
                "thread_id": thread_id,
                "filename": filename,
                "user_id": user_id,
                "conversation_id": conversation_id,
            },
            queue=ingest_tier["queue"],
            soft_time_limit=ingest_tier["soft_time_limit"],
            time_limit=ingest_tier["time_limit"],
        )
        logger.info(
            "Started Celery task %s for PDF ingestion: %s (thread_id: %s, tier=%s, queue=%s, limits=%ss/%ss)",
            task.id,
            filename,
            thread_id,
            ingest_tier["name"],
            ingest_tier["queue"],
            ingest_tier["soft_time_limit"],
            ingest_tier["time_limit"],
        )
        # Start a separate Celery task to extract and store headings for this thread
        try:
            started = False
            load_test_mode = current_app.config.get("LOAD_TEST_MODE", False) or str(current_app.config.get("ENV", "")).lower() == "staging"
            enable_headings = current_app.config.get("ENABLE_RAG_HEADINGS", True)
            delay_headings_for_load_test = current_app.config.get("DELAY_RAG_HEADINGS_FOR_LOAD_TEST", False)
            if enable_headings and not (load_test_mode and delay_headings_for_load_test):
                extract_headings_task.delay(thread_id=thread_id, user_id=user_id)
                started = True
            elif enable_headings and load_test_mode and delay_headings_for_load_test:
                delay_seconds = current_app.config.get("RAG_HEADINGS_DELAY_SECONDS", 30)
                extract_headings_task.apply_async(
                    kwargs={"thread_id": thread_id, "user_id": user_id},
                    countdown=delay_seconds,
                )
                started = True
            if started:
                logger.info(
                    "Started Celery task for heading extraction (thread_id=%s, user_id=%s)",
                    thread_id,
                    user_id,
                )
            else:
                logger.info(
                    "Skipping heading extraction dispatch (enable_headings=%s, load_test_mode=%s, delay_headings_for_load_test=%s)",
                    enable_headings,
                    load_test_mode,
                    delay_headings_for_load_test,
                )
        except Exception as e:
            logger.error(
                "Failed to start heading extraction Celery task for thread %s: %s",
                thread_id,
                e,
                exc_info=True,
            )
        return jsonify({
            'success': True,
            'message': 'PDF ingestion started in background',
            'task_id': task.id,
            'status': 'processing',
            'thread_id': thread_id,
            'conversation_id': conversation_id,
            'filename': filename,
            'ingest_tier': ingest_tier["name"],
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
    # NOTE: rstrip() strips a *character set*, not a suffix — e.g.
    # "Chapter_1_pdf.pdf".rstrip(".pdf") wrongly yields "Chapter_1_".
    # Strip the literal ".pdf"/".PDF" suffix instead.
    download_name = thread.filename or "document"
    if download_name.lower().endswith(".pdf"):
        download_name = download_name[:-4]
    download_name = download_name or "document"
    if not download_name.endswith(".md"):
        download_name += ".md"
    return send_file(
        str(md_path),
        as_attachment=True,
        download_name=download_name,
        mimetype="text/markdown",
    )


def _save_thread_to_db(
    user_id: int,
    thread_id: str,
    filename: str,
    ingest_result: dict = None,
    ingest_deadline_at=None,
    ingest_error: str = None,
):
    """
    Save or update RAG thread in database. On success, set has_document, doc_count, num_pages.

    ingest_status transitions:
      - precreate (no ingest_result, no ingest_error): 'processing', deadline set from ingest_deadline_at
      - success (ingest_result provided): 'success'
      - failure (ingest_error provided): 'failed', error message stored for the chat endpoint to surface
    """
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
            existing_thread.updated_at = now
            db.commit()
            logger.info("Updated thread %s with has_document=true, doc_count=%s", thread_id, existing_thread.doc_count)
        else:
            if not existing_thread.has_document:
                existing_thread.filename = filename
                existing_thread.ingest_status = 'processing'
                if ingest_deadline_at:
                    existing_thread.ingest_deadline_at = ingest_deadline_at
                existing_thread.updated_at = now
                db.commit()
    except Exception as e:
        logger.error("Error saving thread to database: %s", e)
        db.rollback()


def _ingest_with_progress(
    file_bytes: bytes,
    thread_id: str,
    filename: str,
    user_id: int,
    join_timeout_seconds: int = 300,
):
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
            ingestion_thread.join(timeout=max(30, int(join_timeout_seconds)))
            
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


def _start_heading_extraction_background(thread_id: str, user_id: int, app=None):
    """
    Start background heading extraction when Celery is disabled.
    Extracts headings/topics for the given thread and stores them in the database.
    """
    from app.utils.rag_service import extract_and_store_headings_for_thread

    if app is None:
        try:
            app = current_app._get_current_object()
        except RuntimeError:
            logger.error(
                "Cannot start heading extraction without Flask app context "
                "(thread_id=%s, user_id=%s)",
                thread_id,
                user_id,
            )
            return

    def run():
        with app.app_context():
            try:
                from app.utils.llm_gateway import (
                    LlmTelemetryContext,
                    reset_llm_telemetry_context,
                    set_llm_telemetry_context,
                )

                _ts = (
                    "load_test"
                    if os.getenv("LOAD_TEST_MODE", "false").lower() in ("true", "1", "yes")
                    else "production"
                )
                _tok = set_llm_telemetry_context(
                    LlmTelemetryContext(
                        user_id=user_id,
                        workflow="rag_heading_extraction",
                        traffic_source=_ts,
                        thread_id=thread_id,
                        celery_task_name="heading_extraction_background",
                    )
                )
                try:
                    extract_and_store_headings_for_thread(thread_id=thread_id, user_id=user_id)
                finally:
                    reset_llm_telemetry_context(_tok)
            except Exception as e:
                logger.error(
                    "Error in background heading extraction for thread %s: %s",
                    thread_id,
                    e,
                    exc_info=True,
                )

    t = threading.Thread(target=run, daemon=True)
    t.start()


@bp.route('/chat-progress/<thread_id>', methods=['GET'])
@login_required
def chat_progress(thread_id):
    """
    Lightweight polling endpoint: what step is the AI currently on for this in-flight chat
    turn (e.g. "Searching the document...", "Composing your answer...")? Best-effort - returns
    {} if nothing has been recorded yet or Redis is unavailable, which the frontend treats as
    "keep showing the generic thinking indicator". Scoped to the requesting user's own thread.
    """
    if 'user_id' not in session:
        return jsonify({}), 200
    user_id = session['user_id']

    db = get_db()
    owns_thread = db.query(RAGThread).filter_by(thread_id=thread_id, user_id=user_id).first()
    if not owns_thread:
        return jsonify({}), 200

    from app.utils.chat_progress import get_progress
    return jsonify(get_progress(thread_id))


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
    enable_debug_file_logs = current_app.config.get("ENABLE_RAG_DEBUG_FILE_LOGS", False)
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

        # ── Summary intent: intercept BEFORE thread/document validation ──────────
        # Chat-history summarize still works with no PDF. When a PDF is present,
        # document phrases like "summarize the doc" / bare "summarize" go to RAG.
        _summary_thread = provided_thread_id
        if not _summary_thread and conversation_id is not None:
            _summary_thread = _get_thread_id_for_conversation(user_id, conversation_id)
        _has_document_for_summary = bool(
            _summary_thread and thread_has_document(str(_summary_thread).strip())
        )
        if ConversationSummaryService.is_summary_intent(
            message,
            user_id=user_id,
            has_document=_has_document_for_summary,
        ):
            from app.models.models import ConversationModel
            conversation_model = ConversationModel(user_id)
            db_conversation_id = None
            if conversation_id:
                conv = conversation_model.get_conversation_by_id(conversation_id)
                if conv:
                    db_conversation_id = conversation_id
            if not db_conversation_id and provided_thread_id:
                conv_match = re.search(r'user_\d+_conv_(\d+)', provided_thread_id)
                if conv_match:
                    candidate_id = int(conv_match.group(1))
                    if conversation_model.get_conversation_by_id(candidate_id):
                        db_conversation_id = candidate_id
            if not db_conversation_id:
                db_conversation_id = conversation_model.create_conversation(
                    title=message[:50] if len(message) > 50 else message
                )

            # Keep summarize command in chat history, then persist summary response.
            try:
                conversation_model.save_message(
                    conversation_id=db_conversation_id,
                    message=message,
                    role='user',
                )
            except Exception:
                logger.warning(
                    "Failed to save summarize intent message for conversation %s",
                    db_conversation_id,
                    exc_info=True,
                )

            try:
                summary_result = ConversationSummaryService.generate_and_persist_summary(
                    conversation_id=db_conversation_id,
                    user_id=user_id,
                    force=True,
                )
                summary_text = summary_result.get("summary") or "No summary available yet."
            except Exception as summary_err:
                logger.error(
                    "Summary generation failed for conversation %s: %s",
                    db_conversation_id,
                    summary_err,
                    exc_info=True,
                )
                return jsonify({
                    'error': 'Failed to generate summary.',
                    'code': 'SUMMARY_FAILED',
                }), 500

            try:
                conversation_model.save_message(
                    conversation_id=db_conversation_id,
                    message=summary_text,
                    role='bot',
                )
            except Exception:
                logger.warning(
                    "Failed to save summary response message for conversation %s",
                    db_conversation_id,
                    exc_info=True,
                )

            resp_thread_id = provided_thread_id or None
            return jsonify({
                'success': True,
                'message': summary_text,
                'thread_id': resp_thread_id,
                'conversation_id': db_conversation_id,
                'has_document': thread_has_document(resp_thread_id) if resp_thread_id else False,
                'summary_generated': True,
            })
        # ── End summary intent early-exit ─────────────────────────────────────────

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

        if (getattr(thread_row, 'filename', None) or "") == _CANCELLED_UPLOAD_FILENAME:
            logger.info("RAG chat blocked: thread_id=%s was marked as cancelled upload", thread_id)
            return jsonify({
                'error': 'This PDF upload was cancelled. Please upload the document again before asking questions.',
                'code': 'UPLOAD_CANCELLED',
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
                # Distinguish a genuinely-still-running ingest from one that
                # already failed (explicit failure) or was abandoned (worker
                # hard-killed on Celery's time_limit, so its except block
                # never ran) — instead of returning the same "still processing"
                # message for all three, which used to hide permanent failures
                # forever.
                ingest_status = getattr(thread_row, 'ingest_status', None)
                deadline = getattr(thread_row, 'ingest_deadline_at', None)
                if ingest_status == 'failed':
                    logger.warning("RAG chat 400: thread_id=%s ingestion failed: %s", thread_id, thread_row.ingest_error)
                    return jsonify({
                        'error': f"PDF processing failed: {thread_row.ingest_error or 'unknown error'}. Please upload the document again.",
                        'code': 'INGEST_FAILED',
                        'thread_id': thread_id
                    }), 400
                if deadline and datetime.utcnow() > deadline:
                    logger.warning("RAG chat 400: thread_id=%s ingestion abandoned past deadline=%s", thread_id, deadline)
                    return jsonify({
                        'error': 'PDF processing took too long and did not complete. Please upload the document again.',
                        'code': 'INGEST_TIMEOUT',
                        'thread_id': thread_id
                    }), 400
                logger.warning("RAG chat 400: Thread exists but has_document=False and no chunks thread_id=%s", thread_id)
                return jsonify({
                    'error': 'Your PDF may still be processing. Please wait a moment and try again, or upload the PDF again.',
                    'code': 'NO_DOCUMENT',
                    'thread_id': thread_id
                }), 400

        # Summary intent was already handled above (before thread validation).

        try:
            from app.utils.llm_gateway import update_llm_telemetry_context

            update_llm_telemetry_context(
                workflow="rag_chat",
                thread_id=thread_id,
                conversation_id=conversation_id,
                user_id=user_id,
            )
        except Exception:
            pass

        allowed, retry_after = _check_and_record_user_chat_rate(user_id)
        if not allowed:
            return jsonify({
                'error': f'Too many requests in a short time. Please wait {retry_after} seconds and try again.',
                'code': 'USER_RATE_LIMITED',
                'retry_after': retry_after,
            }), 429

        # One message at a time per thread: enforce a cross-process lock (Redis-backed, see
        # app/utils/chat_lock.py) with a SHORT bounded wait. The wait absorbs normal
        # back-to-back sends, but must stay well under the client timeout: a long wait here
        # means an abandoned turn silently blocks every following message until the browser
        # gives up, turning one slow turn into a cascade of errors.
        #
        # Keyed by thread_id, not user_id: the actual hazard is two requests racing against the
        # same LangGraph thread's checkpointed state. A plain in-process threading.Lock() here
        # only ever serialized requests landing on the same gunicorn worker process - two
        # requests for the same thread on two different workers ran concurrently and could
        # deliver one request's answer back to a different, unrelated request (confirmed live).
        lock_wait_seconds = max(1, int(os.getenv("RAG_USER_CHAT_LOCK_WAIT_SECONDS", "5")))
        chat_lock_handle = acquire_chat_lock(thread_id, lock_wait_seconds)
        if chat_lock_handle is None:
            logger.info(
                "RAG chat busy: previous turn still in flight for user_id=%s (waited %ss)",
                user_id,
                lock_wait_seconds,
            )
            return jsonify({
                'error': 'Your previous message is still being generated. Please wait for it to finish before sending another.',
                'code': 'CONCURRENT_REQUEST_TIMEOUT',
                'retry_after': lock_wait_seconds,
            }), 429

        sse_owns_lock = False
        try:
            # Prepare config for LangGraph
            max_tool_rounds = max(
                1,
                int(
                    os.getenv(
                        "RAG_LESSON_MAX_TOOL_ROUNDS_PER_TURN",
                        # Must match rag_service.py's own default for this same env var (15) —
                        # otherwise the graph's hard recursion_limit floor below is sized for far
                        # fewer rounds than the soft "stop calling tools" prompt instruction ever
                        # gets a chance to fire at, and the turn crashes with GraphRecursionError
                        # before the model can even see that instruction.
                        os.getenv("RAG_MAX_TOOL_ROUNDS_PER_TURN", "15"),
                    )
                ),
            )
            # chat -> tools -> chat consumes multiple graph steps per tool round.
            # Ensure recursion budget can accommodate configured tool rounds.
            runtime_recursion_limit = max(
                16,
                int(os.getenv("RAG_GRAPH_RECURSION_LIMIT", str((max_tool_rounds * 2) + 8))),
            )
            config = {
                "configurable": {
                    "thread_id": thread_id
                },
                # Defensive: keep recursion bounded under load while allowing deeper tool loops.
                "recursion_limit": runtime_recursion_limit
            }
            # #region agent log
            try:
                if enable_debug_file_logs:
                    os.makedirs(_log_dir, exist_ok=True)
                    with open(_log_path, 'a', encoding='utf-8') as _f:
                        _f.write(_json.dumps({"location": "rag_routes.py:chat:thread_resolved", "message": "thread_id resolved", "data": {"thread_id": thread_id}, "timestamp": __import__('time').time() * 1000, "sessionId": "debug-session", "hypothesisId": "H2,H4"}) + "\n")
            except Exception:
                pass
            # #endregion
            use_sse = _client_wants_rag_stream(data) and _rag_chat_streaming_enabled()
            sse_queue = queue.Queue() if use_sse else None
            # Create HumanMessage
            human_message = HumanMessage(content=message)

            # Release DB session before long-running invoke so other requests aren't blocked (avoids connection pool exhaustion and long lock waits)
            if 'db' in g:
                _db = g.pop('db')
                try:
                    _db.commit()
                except Exception:
                    _db.rollback()
                finally:
                    _db.close()

            def _execute_rag_chat_turn():
                # Invoke the chatbot - LangGraph returns the final state
                # #region agent log
                try:
                    if enable_debug_file_logs:
                        with open(_log_path, 'a', encoding='utf-8') as _f:
                            _f.write(_json.dumps({"location": "rag_routes.py:chat:invoke_start", "message": "chatbot.invoke start", "data": {}, "timestamp": __import__('time').time() * 1000, "sessionId": "debug-session", "hypothesisId": "H4"}) + "\n")
                except Exception:
                    pass
                # #endregion
                try:
                    state = chatbot.invoke(
                        {"messages": [human_message]},
                        config=config
                    )
                except Exception as invoke_err:
                    if not _is_transient_db_connection_error(invoke_err):
                        raise
                    logger.warning(
                        "RAG chat invoke hit transient DB connection error; resetting engine and retrying once. "
                        "thread_id=%s user_id=%s err=%s",
                        thread_id,
                        user_id,
                        invoke_err,
                    )
                    try:
                        from app.utils.db import reset_db_engine
                        reset_db_engine()
                    except Exception:
                        logger.warning("Failed to reset DB engine before retry", exc_info=True)
                    state = chatbot.invoke(
                        {"messages": [human_message]},
                        config=config
                    )
                # #region agent log
                _msgs = state.get("messages", [])
                try:
                    if enable_debug_file_logs:
                        with open(_log_path, 'a', encoding='utf-8') as _f:
                            _f.write(_json.dumps({"location": "rag_routes.py:chat:invoke_done", "message": "chatbot.invoke done", "data": {"messages_len": len(_msgs)}, "timestamp": __import__('time').time() * 1000, "sessionId": "debug-session", "hypothesisId": "H4"}) + "\n")
                except Exception:
                    pass
                # #endregion

                def _extract_and_sanitize_response(state_obj):
                    messages_local = state_obj.get("messages", []) if isinstance(state_obj, dict) else []
                    if not messages_local:
                        content_local = ""
                    else:
                        last_msg_local = messages_local[-1]
                        if hasattr(last_msg_local, 'content'):
                            content_local = last_msg_local.content
                        elif isinstance(last_msg_local, dict):
                            content_local = last_msg_local.get('content', str(last_msg_local))
                        else:
                            content_local = str(last_msg_local)

                    content_local = _strip_lesson_finalization_from_response(content_local)
                    content_local = _strip_tool_names_from_response(content_local)
                    content_local = _strip_internal_reasoning_from_response(content_local)
                    return content_local

                response_content = _extract_and_sanitize_response(state)

                # If model returned an empty/stripped response, do one recovery turn that
                # explicitly asks the agent to run needed tools and provide the final answer.
                if not isinstance(response_content, str) or not response_content.strip():
                    logger.warning(
                        "RAG chat produced empty response after first invoke. Running one recovery invoke. "
                        "thread_id=%s user_id=%s",
                        thread_id,
                        user_id,
                    )
                    recovery_prompt = (
                        "Your previous response was empty. Re-run the needed tools for the user's last question "
                        "and provide a final direct answer from the uploaded document. "
                        "If document evidence is not found, say that clearly."
                    )
                    try:
                        recovery_state = chatbot.invoke(
                            {"messages": [HumanMessage(content=recovery_prompt)]},
                            config=config
                        )
                    except Exception as recovery_err:
                        if not _is_transient_db_connection_error(recovery_err):
                            raise
                        logger.warning(
                            "RAG recovery invoke hit transient DB connection error; resetting engine and retrying once. "
                            "thread_id=%s user_id=%s err=%s",
                            thread_id,
                            user_id,
                            recovery_err,
                        )
                        try:
                            from app.utils.db import reset_db_engine
                            reset_db_engine()
                        except Exception:
                            logger.warning("Failed to reset DB engine before recovery retry", exc_info=True)
                        recovery_state = chatbot.invoke(
                            {"messages": [HumanMessage(content=recovery_prompt)]},
                            config=config
                        )
                    response_content = _extract_and_sanitize_response(recovery_state)

                # Last-resort fallback to avoid blank frontend messages.
                if not isinstance(response_content, str) or not response_content.strip():
                    response_content = (
                        "I could not generate a complete response this time. "
                        "Please ask again and I will answer directly from your document."
                    )

                _sink = get_rag_token_sink()
                if _sink is not None:
                    try:
                        _sink.on_replace(response_content)
                    except Exception:
                        logger.warning("token sink on_replace after sanitize failed", exc_info=True)

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
                    if enable_debug_file_logs:
                        with open(_log_path, 'a', encoding='utf-8') as _f:
                            _f.write(_json.dumps({"location": "rag_routes.py:chat:return_success", "message": "returning success", "data": {"response_len": len(response_content)}, "timestamp": __import__('time').time() * 1000, "sessionId": "debug-session", "hypothesisId": "H4,H5"}) + "\n")
                except Exception:
                    pass
                # #endregion
                return {
                    'success': True,
                    'message': response_content,
                    'thread_id': thread_id,
                    'conversation_id': db_conversation_id if db_conversation_id else conversation_id,
                    'has_document': thread_has_document(thread_id)
                }

            if sse_queue is not None:
                sse_owns_lock = True
                app_obj = current_app._get_current_object()
                sink = QueueRagTokenSink(sse_queue)

                def _sse_worker():
                    with app_obj.app_context():
                        sink_token = set_rag_token_sink(sink)
                        try:
                            payload = _execute_rag_chat_turn()
                            done_evt = {"type": "done"}
                            done_evt.update(payload)
                            sse_queue.put(done_evt)
                        except GroqRateLimitError as rl_exc:
                            sse_queue.put({
                                "type": "error",
                                "error": "The AI service is temporarily rate limited. Please wait and try again.",
                                "code": rl_exc.info.kind,
                                "retry_after": rl_exc.info.retry_after,
                            })
                        except GroqBusyError:
                            sse_queue.put({
                                "type": "error",
                                "error": "The AI service is temporarily at capacity. Please try again in a moment.",
                                "code": "SERVICE_AT_CAPACITY",
                                "retry_after": 10,
                            })
                        except Exception as exc:
                            logger.error("Error in RAG chat stream worker: %s", exc, exc_info=True)
                            sse_queue.put({
                                "type": "error",
                                "error": "Failed to process chat. Please try again.",
                                "code": "INTERNAL_ERROR",
                            })
                        finally:
                            reset_rag_token_sink(sink_token)
                            try:
                                from app.utils.db import close_db
                                close_db()
                            except Exception:
                                pass
                            sse_queue.put(None)

                worker = threading.Thread(target=_sse_worker, name="rag-chat-sse", daemon=True)
                worker.start()

                def generate():
                    try:
                        # Defeat proxy/nginx buffering so tokens flush immediately.
                        yield ":" + (" " * 4096) + "\n\n"
                        yield 'data: {"type":"start"}\n\n'
                        while True:
                            try:
                                ev = sse_queue.get(timeout=1)
                            except queue.Empty:
                                yield ": keepalive\n\n"
                                continue
                            if ev is None:
                                break
                            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    finally:
                        worker.join(timeout=620)
                        release_chat_lock(chat_lock_handle)

                response = Response(
                    stream_with_context(generate()),
                    mimetype="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache, no-store, no-transform",
                        "X-Accel-Buffering": "no",
                        "Connection": "keep-alive",
                        "Content-Type": "text/event-stream; charset=utf-8",
                    },
                )
                return response

            payload = _execute_rag_chat_turn()
            return jsonify(payload)
        finally:
            if not sse_owns_lock:
                release_chat_lock(chat_lock_handle)

    except GroqRateLimitError as rl_exc:
        logger.warning(
            "RAG chat: Groq rate limit for user %s — %s retry_after=%ds",
            session.get('user_id'), rl_exc.info.kind, rl_exc.info.retry_after,
        )
        return jsonify({
            'error': 'The AI service is temporarily rate limited. Please wait and try again.',
            'code': rl_exc.info.kind,
            'retry_after': rl_exc.info.retry_after,
        }), 429
    except GroqBusyError as busy_exc:
        logger.warning("RAG chat: Groq semaphore busy for user %s — %s", session.get('user_id'), busy_exc)
        return jsonify({
            'error': 'The AI service is temporarily at capacity. Please try again in a moment.',
            'code': 'SERVICE_AT_CAPACITY',
            'retry_after': 10,
        }), 503
    except Exception as e:
        # #region agent log
        try:
            if enable_debug_file_logs:
                os.makedirs(_log_dir, exist_ok=True)
                with open(_log_path, 'a', encoding='utf-8') as _f:
                    _f.write(_json.dumps({"location": "rag_routes.py:chat:exception", "message": "chat exception", "data": {"error": type(e).__name__}, "timestamp": __import__('time').time() * 1000, "sessionId": "debug-session", "hypothesisId": "H4"}) + "\n")
        except Exception:
            pass
        # #endregion
        logger.error("Error in RAG chat: %s", e, exc_info=True)
        # Do NOT leak raw provider error text to clients
        return jsonify({'error': 'Failed to process chat. Please try again.', 'code': 'INTERNAL_ERROR'}), 500


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
        payload = request.get_json(silent=True) or {}
        provided_thread_id = (
            payload.get('thread_id')
            or request.form.get('thread_id')
            or request.args.get('thread_id')
        )
        thread_id = str(provided_thread_id).strip() if provided_thread_id else None
        if thread_id and not _validate_thread_id(thread_id, session['user_id']):
            return jsonify({'error': 'Access denied. You can only cancel your own thread uploads.'}), 403

        task = ingest_pdf_task.AsyncResult(task_id)
        if task.state in ('PENDING', 'PROCESSING', 'RETRY'):
            task.revoke(terminate=True)

        cleanup_performed = False
        if thread_id:
            db = get_db()
            try:
                # Persist a cancellation marker so late worker completion cannot resurrect this thread.
                thread_row = db.query(RAGThread).filter_by(thread_id=thread_id, user_id=session['user_id']).first()
                now = datetime.utcnow()
                if thread_row:
                    thread_row.has_document = False
                    thread_row.doc_count = 0
                    thread_row.num_pages = None
                    thread_row.headings_ready = False
                    thread_row.headings_count = 0
                    thread_row.filename = _CANCELLED_UPLOAD_FILENAME
                    thread_row.updated_at = now
                else:
                    db.add(RAGThread(
                        user_id=session['user_id'],
                        thread_id=thread_id,
                        name=f"Cancelled Upload {now.strftime('%Y-%m-%d %H:%M')}",
                        filename=_CANCELLED_UPLOAD_FILENAME,
                        has_document=False,
                        doc_count=0,
                        created_at=now,
                        updated_at=now,
                    ))
                db.commit()
            except Exception:
                db.rollback()
                logger.warning("Failed to persist cancellation marker for thread_id=%s", thread_id, exc_info=True)

            # Best-effort cleanup of already-written data.
            try:
                delete_thread(thread_id)
            except Exception:
                logger.warning("Failed vector/file cleanup for cancelled thread_id=%s", thread_id, exc_info=True)
            try:
                db.query(RAGChunk).filter_by(thread_id=thread_id, user_id=session['user_id']).delete(synchronize_session=False)
                db.query(RAGHeading).filter_by(thread_id=thread_id, user_id=session['user_id']).delete(synchronize_session=False)
                db.commit()
                cleanup_performed = True
            except Exception:
                db.rollback()
                logger.warning("Failed DB chunk/heading cleanup for cancelled thread_id=%s", thread_id, exc_info=True)

        logger.info(
            "User %s cancelled ingest task %s (thread_id=%s, state=%s, cleanup_performed=%s)",
            session['user_id'],
            task_id,
            thread_id,
            task.state,
            cleanup_performed,
        )
        return jsonify({
            'message': 'Upload cancelled',
            'task_id': task_id,
            'status': 'revoked',
            'thread_id': thread_id,
            'cleanup_performed': cleanup_performed
        }), 200
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
            if thread_id:
                try:
                    db = get_db()
                    cancelled_row = db.query(RAGThread).filter_by(
                        thread_id=thread_id,
                        user_id=user_id,
                        filename=_CANCELLED_UPLOAD_FILENAME
                    ).first()
                    if cancelled_row:
                        return jsonify({
                            'task_id': task_id,
                            'status': 'revoked',
                            'state': 'REVOKED',
                            'message': 'Task was cancelled'
                        })
                except Exception:
                    logger.warning("Could not verify cancelled marker for task_id=%s thread_id=%s", task_id, thread_id, exc_info=True)
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
                'warning': result.get('warning'),
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


@bp.route('/prompt/preview', methods=['GET'])
@login_required
def get_rag_prompt_system_preview():
    """
    Read-only full system prompt for PDF chats: optional user custom text + separator + server RAG instructions.
    Uses sample values for {filename}, {page_info}, and {thread_id} so teachers can see the combined shape.
    """
    try:
        if 'user_id' not in session:
            return jsonify({'success': False, 'error': 'Not authenticated', 'code': 'NOT_AUTHENTICATED'}), 401

        user_id = session['user_id']
        db = get_db()
        prompt_obj = (
            db.query(RAGPrompt)
            .filter(RAGPrompt.user_id == user_id, RAGPrompt.thread_id.is_(None))
            .order_by(RAGPrompt.updated_at.desc())
            .first()
        )
        custom = (prompt_obj.prompt or "").strip() if prompt_obj else ""

        template_src = (
            _get_stored_rag_system_template(RAG_SYSTEM_SETTING_KEY_WITH_PDF)
            or DEFAULT_RAG_CHAT_SYSTEM_BODY_WITH_PDF
        )
        demo_filename = "your-document.pdf"
        demo_page_info = " The PDF has 12 pages."
        demo_thread_id = f"user_{user_id}_conv_<conversation_id>_…"
        rag_body = _substitute_rag_system_placeholders(
            template_src,
            filename=demo_filename,
            page_info=demo_page_info,
            thread_id=demo_thread_id,
        )
        if custom:
            full = f"{custom}\n\n---\n\n{rag_body}"
        else:
            full = rag_body

        return jsonify({
            'success': True,
            'custom_prompt': custom,
            'rag_system_body': rag_body,
            'full_combined_preview': full,
            'note': (
                'Sample values are used for filename, page count, and thread id. '
                'Real chats substitute the actual PDF name and thread id.'
            ),
        })
    except Exception as e:
        logger.error(f"Error building RAG prompt preview: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Could not build the prompt preview. Try again in a moment.',
            'code': 'PREVIEW_FAILED',
            'detail': str(e),
        }), 500


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
            return jsonify({
                'success': False,
                'error': 'Prompt cannot be empty. Enter your custom instructions (up to '
                f'{RAG_USER_PROMPT_MAX_WORDS} words), or use Delete to remove your custom prompt.',
                'code': 'PROMPT_REQUIRED',
            }), 400

        wc = _count_words(prompt)
        if wc > RAG_USER_PROMPT_MAX_WORDS:
            return jsonify({
                'success': False,
                'error': (
                    f'Your custom prompt is {wc} words. '
                    f'Shorten it to {RAG_USER_PROMPT_MAX_WORDS} words or fewer, then save again.'
                ),
                'code': 'PROMPT_TOO_LONG',
                'max_words': RAG_USER_PROMPT_MAX_WORDS,
                'word_count': wc,
                # backwards compatibility for older clients
                'max_length': RAG_USER_PROMPT_MAX_WORDS,
                'length': wc,
            }), 400
        
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


@bp.route('/thread/<thread_id>/reset', methods=['POST'])
@login_required
def reset_thread_conversation_route(thread_id):
    """
    Reset a thread's conversation: clears the LangGraph checkpointed message
    history so the next message starts fresh, but keeps the uploaded document
    (vectors / file) intact. Used by the "Reset Chat" button.
    """
    try:
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401

        user_id = session['user_id']

        if not _validate_thread_id(thread_id, user_id):
            return jsonify({'error': 'Access denied. You can only reset your own threads.'}), 403

        result = clear_thread_conversation_history(thread_id)
        if not result.get('success'):
            return jsonify({'error': result.get('message', 'Failed to reset conversation')}), 500

        return jsonify({
            'success': True,
            'message': result.get('message', 'Conversation reset successfully'),
            'thread_id': thread_id
        })

    except Exception as e:
        logger.error(f"Error resetting thread conversation: {str(e)}", exc_info=True)
        return jsonify({'error': f'Failed to reset conversation: {str(e)}'}), 500

