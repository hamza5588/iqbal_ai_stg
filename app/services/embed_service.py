"""Business logic for the B2B embed chatbot service."""

from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import datetime
from typing import Optional

from werkzeug.security import check_password_hash, generate_password_hash

from app.models.database_models import (
    EmbedCallbackRequest,
    EmbedClient,
    EmbedConversation,
    EmbedMessage,
    RAGChunk,
    RAGThread,
)
from app.utils.db import get_db

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}")
ESCALATE_MARKER = "[ESCALATE]"

_EMBED_SYSTEM_PROMPT = (
    "You are a knowledgeable AI consultant for a business website. "
    "Answer questions clearly using the provided document context when available. "
    "If you are unsure, the question is outside the document, or the user seems confused, "
    "politely ask for their email address and/or phone number so a human can follow up. "
    "When the user provides contact information, acknowledge it and confirm someone will reach out. "
    "If a human must follow up, append the exact token [ESCALATE] at the very end of your response "
    "(this token is stripped before the user sees your message)."
)


def generate_client_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_client_secret(secret: str) -> str:
    return generate_password_hash(secret)


def verify_client_secret(secret: str, secret_hash: str) -> bool:
    return check_password_hash(secret_hash, secret)


def parse_allowed_origins(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(o).strip() for o in data if str(o).strip()]
    except json.JSONDecodeError:
        pass
    return [o.strip() for o in raw.split(",") if o.strip()]


def serialize_allowed_origins(origins: list[str]) -> str:
    return json.dumps(origins)


def find_client_by_secret(secret: str) -> Optional[EmbedClient]:
    if not secret:
        return None
    db = get_db()
    for client in db.query(EmbedClient).filter(EmbedClient.active.is_(True)).all():
        if verify_client_secret(secret, client.secret_key_hash):
            return client
    return None


def get_client_by_slug(slug: str) -> Optional[EmbedClient]:
    db = get_db()
    return db.query(EmbedClient).filter_by(client_slug=slug, active=True).first()


def get_client_by_id(client_id: int) -> Optional[EmbedClient]:
    db = get_db()
    return db.query(EmbedClient).filter_by(id=client_id).first()


def make_visitor_id(client_slug: str) -> str:
    return f"guest_{client_slug}_{secrets.token_hex(8)}"


def is_valid_visitor_id(visitor_id: str, client_slug: str) -> bool:
    if not visitor_id or not client_slug:
        return False
    pattern = f"^guest_{re.escape(client_slug)}_[a-zA-Z0-9_-]{{8,64}}$"
    return bool(re.match(pattern, visitor_id))


def get_or_create_conversation(
    client: EmbedClient,
    visitor_id: str,
    conversation_id: Optional[int] = None,
) -> EmbedConversation:
    db = get_db()
    if conversation_id:
        conv = (
            db.query(EmbedConversation)
            .filter_by(id=conversation_id, client_id=client.id, visitor_id=visitor_id)
            .first()
        )
        if conv:
            return conv

    conv = (
        db.query(EmbedConversation)
        .filter_by(client_id=client.id, visitor_id=visitor_id, status="active")
        .order_by(EmbedConversation.created_at.desc())
        .first()
    )
    if conv:
        return conv

    conv = EmbedConversation(client_id=client.id, visitor_id=visitor_id, status="active")
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def add_message(conversation_id: int, role: str, content: str, channel: str = "text") -> EmbedMessage:
    db = get_db()
    msg = EmbedMessage(conversation_id=conversation_id, role=role, content=content, channel=channel)
    db.add(msg)
    conv = db.query(EmbedConversation).filter_by(id=conversation_id).first()
    if conv:
        conv.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(msg)
    return msg


def get_messages(conversation_id: int) -> list[EmbedMessage]:
    db = get_db()
    return (
        db.query(EmbedMessage)
        .filter_by(conversation_id=conversation_id)
        .order_by(EmbedMessage.created_at.asc())
        .all()
    )


def get_message_history_for_llm(conversation_id: int) -> list[dict[str, str]]:
    return [
        {"role": m.role, "content": m.content}
        for m in get_messages(conversation_id)
        if m.role in ("user", "assistant")
    ]


def extract_contact_info(text: str) -> tuple[Optional[str], Optional[str]]:
    email_match = EMAIL_RE.search(text or "")
    phone_match = PHONE_RE.search(text or "")
    return (
        email_match.group(0) if email_match else None,
        phone_match.group(0) if phone_match else None,
    )


def extract_contact_from_messages(messages: list[EmbedMessage]) -> tuple[Optional[str], Optional[str]]:
    """Latest email/phone from visitor messages only (not AI replies)."""
    email = phone = None
    for m in messages:
        if m.role != "user":
            continue
        found_email, found_phone = extract_contact_info(m.content or "")
        if found_email:
            email = found_email
        if found_phone:
            phone = found_phone
    return email, phone


def strip_escalate_marker(text: str) -> tuple[str, bool]:
    if not text:
        return "", False
    escalated = ESCALATE_MARKER in text
    return text.replace(ESCALATE_MARKER, "").strip(), escalated


def build_embed_system_prompt(client: EmbedClient, doc_context: str = "", filename: str = "") -> str:
    base = client.system_prompt or _EMBED_SYSTEM_PROMPT
    if doc_context:
        name = filename or "uploaded document"
        return (
            f"{base}\n\n--- DOCUMENT CONTEXT ({name}) ---\n{doc_context}\n"
            "--- END DOCUMENT CONTEXT ---"
        )
    return base


def client_has_document(client: EmbedClient) -> bool:
    if not client.rag_thread_id or not client.service_user_id:
        return False
    db = get_db()
    row = (
        db.query(RAGThread)
        .filter_by(thread_id=client.rag_thread_id, user_id=client.service_user_id)
        .first()
    )
    return bool(row and row.has_document)


def get_client_document_filename(client: EmbedClient) -> Optional[str]:
    if not client.rag_thread_id or not client.service_user_id:
        return None
    db = get_db()
    row = (
        db.query(RAGThread)
        .filter_by(thread_id=client.rag_thread_id, user_id=client.service_user_id)
        .first()
    )
    return row.filename if row else None


def mark_escalation(
    conversation: EmbedConversation,
    email: Optional[str] = None,
    phone: Optional[str] = None,
) -> EmbedConversation:
    db = get_db()
    conversation.needs_attention = True
    conversation.escalated_at = datetime.utcnow()
    if email:
        conversation.visitor_email = email
    if phone:
        conversation.visitor_phone = phone
    db.commit()
    db.refresh(conversation)
    return conversation


def create_callback_request(
    conversation_id: int,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    notes: Optional[str] = None,
) -> EmbedCallbackRequest:
    db = get_db()
    row = EmbedCallbackRequest(
        conversation_id=conversation_id, email=email, phone=phone, notes=notes
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_conversations_for_client(
    client_id: int,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
) -> list[EmbedConversation]:
    db = get_db()
    q = db.query(EmbedConversation).filter_by(client_id=client_id)
    if from_date:
        q = q.filter(EmbedConversation.created_at >= from_date)
    if to_date:
        q = q.filter(EmbedConversation.created_at <= to_date)
    return q.order_by(EmbedConversation.created_at.asc()).all()


def create_embed_client(
    client_slug: str,
    owner_email: str,
    secret: Optional[str] = None,
    owner_name: Optional[str] = None,
    allowed_origins: Optional[list[str]] = None,
    rag_thread_id: Optional[str] = None,
    service_user_id: Optional[int] = None,
    system_prompt: Optional[str] = None,
) -> tuple[EmbedClient, str]:
    db = get_db()
    plain_secret = secret or generate_client_secret()
    client = EmbedClient(
        client_slug=client_slug,
        secret_key_hash=hash_client_secret(plain_secret),
        owner_email=owner_email,
        owner_name=owner_name,
        allowed_origins=serialize_allowed_origins(allowed_origins or []),
        rag_thread_id=rag_thread_id,
        service_user_id=service_user_id,
        system_prompt=system_prompt,
        active=True,
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    return client, plain_secret


def update_embed_client(client_id: int, **fields) -> Optional[EmbedClient]:
    db = get_db()
    client = db.query(EmbedClient).filter_by(id=client_id).first()
    if not client:
        return None
    if "allowed_origins" in fields and isinstance(fields["allowed_origins"], list):
        fields["allowed_origins"] = serialize_allowed_origins(fields["allowed_origins"])
    for key, val in fields.items():
        if hasattr(client, key) and val is not None:
            setattr(client, key, val)
    client.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(client)
    return client


def list_embed_clients() -> list[EmbedClient]:
    db = get_db()
    return db.query(EmbedClient).order_by(EmbedClient.created_at.desc()).all()


def get_client_rag_info(client: EmbedClient) -> dict:
    if not client.rag_thread_id or not client.service_user_id:
        return {
            "has_document": False,
            "filename": None,
            "num_pages": None,
            "last_ingested_at": None,
        }
    db = get_db()
    row = (
        db.query(RAGThread)
        .filter_by(thread_id=client.rag_thread_id, user_id=client.service_user_id)
        .first()
    )
    if not row:
        return {
            "has_document": False,
            "filename": None,
            "num_pages": None,
            "last_ingested_at": None,
        }
    return {
        "has_document": bool(row.has_document),
        "filename": row.filename,
        "num_pages": row.num_pages,
        "last_ingested_at": row.last_ingested_at.isoformat() if row.last_ingested_at else None,
    }


def serialize_embed_client_admin(client: EmbedClient) -> dict:
    return {
        "id": client.id,
        "client_slug": client.client_slug,
        "owner_email": client.owner_email,
        "owner_name": client.owner_name,
        "allowed_origins": parse_allowed_origins(client.allowed_origins),
        "active": client.active,
        "rag_thread_id": client.rag_thread_id,
        "service_user_id": client.service_user_id,
        "created_at": client.created_at.isoformat() if client.created_at else None,
        "updated_at": client.updated_at.isoformat() if client.updated_at else None,
        "document": get_client_rag_info(client),
    }


def delete_embed_client(client_id: int) -> bool:
    db = get_db()
    client = db.query(EmbedClient).filter_by(id=client_id).first()
    if not client:
        return False

    thread_id = client.rag_thread_id
    user_id = client.service_user_id
    if thread_id and user_id:
        from app.utils.rag_service import delete_thread

        try:
            delete_thread(thread_id)
        except Exception as exc:
            logger.warning("Vector cleanup failed for embed client %s: %s", client_id, exc)
        try:
            db.query(RAGChunk).filter_by(thread_id=thread_id).delete()
            row = db.query(RAGThread).filter_by(thread_id=thread_id, user_id=user_id).first()
            if row:
                db.delete(row)
        except Exception as exc:
            logger.warning("RAG DB cleanup failed for embed client %s: %s", client_id, exc)
            db.rollback()
            raise

    try:
        db.delete(client)
        db.commit()
        return True
    except Exception as exc:
        logger.error("Failed to delete embed client %s: %s", client_id, exc, exc_info=True)
        db.rollback()
        raise
