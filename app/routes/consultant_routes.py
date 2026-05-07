"""
Consultant Chatbot Routes
=========================
Separate blueprint for the AI Consultant widget.  Reuses the existing RAG/LLM
infrastructure without touching any other routes.

URL prefix (registered in app/__init__.py):  /api/consultant

Endpoints
---------
POST /api/consultant/chat         – text chat (RAG if doc present, else LLM)
POST /api/consultant/ingest       – PDF upload & ingest
POST /api/consultant/voice/session – create OpenAI Realtime ephemeral token
GET  /api/consultant/thread/status/<thread_id>
"""

import logging
import os
import uuid
from datetime import datetime

import requests
from flask import Blueprint, jsonify, request, session

from app.models.database_models import RAGChunk, RAGThread
from app.utils.auth import login_required
from app.utils.db import get_db
from app.utils.rag_service import (
    MARKDOWN_EXPORTS_DIR,
    ingest_pdf,
    thread_has_document,
    warmup_rag_embeddings,
)

# Max tokens used exclusively by the consultant LLM — kept within model limits.
# The main RAG flow keeps its own (higher) RAG_RESPONSE_MAX_TOKENS value.
_CONSULTANT_MAX_TOKENS = 8192

logger = logging.getLogger(__name__)
bp = Blueprint("consultant", __name__)

# ---------------------------------------------------------------------------
# System prompt for the consultant voice/text sessions
# ---------------------------------------------------------------------------
_BASE_SYSTEM_PROMPT = (
    "You are a knowledgeable AI consultant. "
    "Provide helpful, accurate, and professional answers. "
    "When document context is provided, prioritize information from that document. "
    "Be clear, concise, and actionable."
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _validate_thread(thread_id: str, user_id: int) -> bool:
    """Return True only when the thread_id prefix matches the user."""
    return bool(thread_id) and thread_id.startswith(f"user_{user_id}_")


def _get_doc_chunks(thread_id: str, user_id: int, max_chars: int = 4000) -> str:
    """
    Fetch text chunks for *thread_id* from the database and return them
    concatenated (capped at *max_chars*).  Used to build voice-session system
    prompts that include document context.
    """
    try:
        db = get_db()
        chunks = (
            db.query(RAGChunk)
            .filter_by(thread_id=thread_id, user_id=user_id)
            .order_by(RAGChunk.chunk_index)
            .limit(25)
            .all()
        )
        parts: list[str] = []
        total = 0
        for c in chunks:
            if total + len(c.text) > max_chars:
                break
            parts.append(c.text)
            total += len(c.text)
        return "\n\n".join(parts)
    except Exception as exc:
        logger.warning("Could not fetch doc chunks for %s: %s", thread_id, exc)
        return ""


def _save_thread(user_id: int, thread_id: str, filename: str, result: dict | None = None) -> None:
    """Persist (or update) a RAGThread row for a consultant session."""
    db = get_db()
    try:
        row = db.query(RAGThread).filter_by(thread_id=thread_id).first()
        now = datetime.utcnow()
        if not row:
            row = RAGThread(
                user_id=user_id,
                thread_id=thread_id,
                name=f"Consultant {now.strftime('%Y-%m-%d %H:%M')}",
                filename=filename,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            db.commit()
            db.refresh(row)

        if result:
            row.filename = filename
            row.has_document = True
            row.num_pages = result.get("num_pages")
            row.last_ingested_at = now
            row.embedding_model = result.get("embedding_model")
            row.updated_at = now
            db.commit()
    except Exception as exc:
        logger.error("Error saving consultant thread %s: %s", thread_id, exc)
        db.rollback()


def _get_openai_key() -> str:
    """
    Return the OpenAI API key from (in priority order):
    1. SystemSettings table (encrypted)
    2. OPENAI_API_KEY environment variable
    """
    try:
        from app.models.database_models import SystemSettings
        from app.utils.encryption import decrypt_api_key

        db = get_db()
        setting = db.query(SystemSettings).filter_by(key="openai_api_key").first()
        if setting and setting.value:
            return decrypt_api_key(setting.value)
    except Exception:
        pass
    return os.getenv("OPENAI_API_KEY", "")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.route("/chat", methods=["POST"])
@login_required
def chat():
    """
    Text chat for the consultant.

    Request JSON:
        message       (str, required)
        thread_id     (str, optional)  – if set and has_document, uses RAG
        conversation_id (int, optional)

    Response JSON:
        success, message, thread_id, conversation_id, used_rag
    """
    try:
        user_id = session["user_id"]
        data = request.get_json(force=True, silent=True) or {}

        message = (data.get("message") or "").strip()
        if not message:
            return jsonify({"error": "message is required"}), 400

        thread_id = data.get("thread_id") or None
        conversation_id = data.get("conversation_id") or None

        # Security: validate thread ownership
        if thread_id and not _validate_thread(thread_id, user_id):
            return jsonify({"error": "Access denied: invalid thread_id"}), 403

        # Decide whether to use RAG
        use_rag = False
        if thread_id:
            db = get_db()
            trow = db.query(RAGThread).filter_by(thread_id=thread_id, user_id=user_id).first()
            use_rag = bool(trow and trow.has_document)

        if use_rag:
            # ---  Consultant RAG: direct retrieval + isolated LLM (8192 token cap) ---
            # We intentionally bypass the shared chatbot LangGraph graph so that
            # the consultant never inherits the main flow's high RAG_RESPONSE_MAX_TOKENS.
            try:
                from app.utils.llm_factory import get_chat_model
                from langchain_core.messages import HumanMessage as LCHuman, SystemMessage

                # Fetch document chunks stored for this thread
                doc_ctx = _get_doc_chunks(thread_id, user_id, max_chars=6000)

                system_content = _BASE_SYSTEM_PROMPT
                if doc_ctx:
                    system_content = (
                        f"{_BASE_SYSTEM_PROMPT}\n\n"
                        "DOCUMENT CONTEXT — use this to answer the user's question:\n\n"
                        f"{doc_ctx}"
                    )

                llm = get_chat_model(
                    user_id=user_id,
                    max_tokens=_CONSULTANT_MAX_TOKENS,
                    temperature=0.5,
                    timeout=60,
                )
                ai_msg = llm.invoke([
                    SystemMessage(content=system_content),
                    LCHuman(content=message),
                ])
                response_text = (
                    ai_msg.content
                    if hasattr(ai_msg, "content")
                    else str(ai_msg)
                )
            except Exception as rag_exc:
                logger.error("Consultant RAG LLM error: %s", rag_exc, exc_info=True)
                response_text = "I'm sorry, I couldn't generate a response. Please try again."
        else:
            # ---  Standard ChatService (Groq / admin-selected LLM)  ---
            from app.services.chat_service import ChatService

            api_key = session.get("groq_api_key", "")
            svc = ChatService(user_id, api_key)
            result = svc.process_message(message, conversation_id)
            response_text = result.get("response") or result.get("message") or ""
            conversation_id = result.get("conversation_id", conversation_id)

        return jsonify(
            {
                "success": True,
                "message": response_text,
                "thread_id": thread_id,
                "conversation_id": conversation_id,
                "used_rag": use_rag,
            }
        )

    except Exception as exc:
        logger.error("Consultant /chat error: %s", exc, exc_info=True)
        return jsonify({"error": f"Chat failed: {exc}"}), 500


@bp.route("/ingest", methods=["POST"])
@login_required
def ingest():
    """
    Upload a PDF for the consultant chatbot.

    Form-data fields:
        file        (required) – the PDF file
        session_id  (optional) – client-provided session identifier

    Response JSON:
        success, thread_id, filename, num_pages, chunks
    """
    try:
        user_id = session["user_id"]

        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400

        file = request.files["file"]
        if not file or not file.filename:
            return jsonify({"error": "No file selected"}), 400

        if not file.filename.lower().endswith(".pdf"):
            return jsonify({"error": "Only PDF files are supported"}), 400

        session_id = request.form.get("session_id") or str(uuid.uuid4())[:8]
        thread_id = f"user_{user_id}_consultant_{session_id}"
        filename = file.filename

        file_bytes = file.read()
        if not file_bytes:
            return jsonify({"error": "File is empty"}), 400

        # Check if this consultant thread already has a document
        db = get_db()
        existing = db.query(RAGThread).filter_by(thread_id=thread_id, user_id=user_id).first()
        if existing and existing.has_document:
            return jsonify(
                {
                    "error": (
                        "This session already has a document.  "
                        "Start a new session to upload a different document."
                    ),
                    "existing_filename": existing.filename,
                }
            ), 400

        # Warm up embedding model then ingest
        warmup_rag_embeddings()
        result = ingest_pdf(
            file_bytes=file_bytes,
            thread_id=thread_id,
            filename=filename,
            progress_callback=None,
            user_id=user_id,
        )

        _save_thread(user_id, thread_id, filename, result)

        return jsonify(
            {
                "success": True,
                "thread_id": thread_id,
                "filename": result.get("filename", filename),
                "num_pages": result.get("num_pages", 0),
                "chunks": result.get("chunks", 0),
            }
        )

    except ValueError as exc:
        logger.error("Consultant /ingest value error: %s", exc)
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.error("Consultant /ingest error: %s", exc, exc_info=True)
        return jsonify({"error": f"Upload failed: {exc}"}), 500


@bp.route("/voice/session", methods=["POST"])
@login_required
def voice_session():
    """
    Create an OpenAI Realtime session and return the ephemeral client_secret.

    Optionally injects document context from *thread_id* into the session's
    system instructions so the voice assistant can answer doc-grounded questions.

    Request JSON:
        thread_id  (str, optional)

    Response JSON:
        success, client_secret {value, expires_at}, session_id, model, voice,
        has_doc_context
    """
    try:
        user_id = session["user_id"]
        data = request.get_json(force=True, silent=True) or {}
        thread_id = data.get("thread_id") or None

        # Validate thread ownership
        if thread_id and not _validate_thread(thread_id, user_id):
            return jsonify({"error": "Access denied: invalid thread_id"}), 403

        # Build system instructions, optionally with document context
        instructions = _BASE_SYSTEM_PROMPT
        has_doc_context = False

        if thread_id:
            db = get_db()
            trow = db.query(RAGThread).filter_by(thread_id=thread_id, user_id=user_id).first()
            if trow and trow.has_document:
                doc_ctx = _get_doc_chunks(thread_id, user_id, max_chars=4000)
                if doc_ctx:
                    instructions = (
                        f"{_BASE_SYSTEM_PROMPT}\n\n"
                        f"DOCUMENT CONTEXT – the user uploaded '{trow.filename}'.\n"
                        f"Use this content to answer questions:\n\n{doc_ctx}"
                    )
                    has_doc_context = True

        # OpenAI API key is required for Realtime
        openai_key = _get_openai_key()
        if not openai_key:
            return jsonify(
                {
                    "error": (
                        "OpenAI API key is not configured.  "
                        "Voice requires an OpenAI API key set in Admin → Settings."
                    ),
                    "code": "OPENAI_KEY_MISSING",
                }
            ), 503

        # Create ephemeral session via OpenAI Realtime REST endpoint
        resp = requests.post(
            "https://api.openai.com/v1/realtime/sessions",
            headers={
                "Authorization": f"Bearer {openai_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini-realtime-preview-2024-12-17",
                "voice": "alloy",
                "instructions": instructions,
                "modalities": ["audio", "text"],
                "input_audio_transcription": {"model": "whisper-1"},
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 600,
                },
            },
            timeout=30,
        )

        if resp.status_code != 200:
            logger.error(
                "OpenAI Realtime session failed: %s %s",
                resp.status_code,
                resp.text[:300],
            )
            return jsonify(
                {
                    "error": "Failed to create voice session with OpenAI.",
                    "code": "OPENAI_SESSION_FAILED",
                    "detail": resp.text[:200],
                }
            ), 502

        sess_data = resp.json()
        return jsonify(
            {
                "success": True,
                "client_secret": sess_data.get("client_secret"),
                "session_id": sess_data.get("id"),
                "model": sess_data.get("model"),
                "voice": sess_data.get("voice"),
                "has_doc_context": has_doc_context,
            }
        )

    except requests.exceptions.Timeout:
        return jsonify({"error": "Timeout contacting OpenAI", "code": "TIMEOUT"}), 504
    except Exception as exc:
        logger.error("Consultant /voice/session error: %s", exc, exc_info=True)
        return jsonify({"error": f"Voice session failed: {exc}"}), 500


@bp.route("/thread/status/<path:thread_id>", methods=["GET"])
@login_required
def thread_status(thread_id: str):
    """
    Return document-presence status for a consultant thread.

    Response JSON:
        thread_id, has_document, filename, num_pages
    """
    try:
        user_id = session["user_id"]

        if not _validate_thread(thread_id, user_id):
            return jsonify({"error": "Access denied"}), 403

        db = get_db()
        row = db.query(RAGThread).filter_by(thread_id=thread_id, user_id=user_id).first()

        if not row:
            return jsonify({"thread_id": thread_id, "has_document": False})

        return jsonify(
            {
                "thread_id": thread_id,
                "has_document": bool(row.has_document),
                "filename": row.filename,
                "num_pages": row.num_pages,
            }
        )

    except Exception as exc:
        logger.error("Consultant /thread/status error: %s", exc)
        return jsonify({"error": str(exc)}), 500
