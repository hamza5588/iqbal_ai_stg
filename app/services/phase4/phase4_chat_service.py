"""Floating product chatbot (separate from teacher RAG threads)."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.phase4_models import Phase4ChatConversation, Phase4ChatMessage


def get_or_create_conversation(
    db: Session, *, user_id: int, conversation_id: Optional[int] = None, subject_hint: Optional[str] = None
) -> Phase4ChatConversation:
    if conversation_id:
        row = (
            db.query(Phase4ChatConversation)
            .filter(Phase4ChatConversation.id == int(conversation_id), Phase4ChatConversation.user_id == user_id)
            .first()
        )
        if row:
            return row
    row = Phase4ChatConversation(user_id=int(user_id), subject_hint=(subject_hint or "")[:255] or None)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def append_message(
    db: Session, *, conversation_id: int, role: str, content: str, sources: Optional[Dict[str, Any]] = None
) -> Phase4ChatMessage:
    msg = Phase4ChatMessage(
        conversation_id=int(conversation_id),
        role=role,
        content=content,
        sources_json=json.dumps(sources or {}, default=str),
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def list_messages(db: Session, *, conversation_id: int, user_id: int, limit: int = 50) -> List[Phase4ChatMessage]:
    conv = (
        db.query(Phase4ChatConversation)
        .filter(Phase4ChatConversation.id == conversation_id, Phase4ChatConversation.user_id == user_id)
        .first()
    )
    if not conv:
        return []
    return (
        db.query(Phase4ChatMessage)
        .filter(Phase4ChatMessage.conversation_id == conversation_id)
        .order_by(Phase4ChatMessage.id.asc())
        .limit(limit)
        .all()
    )


def generate_assistant_reply(user_message: str, *, preferred_language: str = "en") -> tuple[str, Dict[str, Any]]:
    """Provider-agnostic stub; swap for LLM via env."""
    if os.getenv("PHASE4_CHAT_DISABLE_LLM", "").lower() in ("1", "true", "yes"):
        return (
            f"[{preferred_language}] I'm here to help with your subjects. "
            "Try asking a specific syllabus question — source links will appear when content is grounded.",
            {"badges": [{"label": "stub", "type": "system"}]},
        )
    try:
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import StrOutputParser

        from app.utils.llm_factory import get_chat_model

        llm = get_chat_model()
        prompt = ChatPromptTemplate.from_template(
            "You are IqbalAI study help. Reply briefly in {lang}. If unsure, say so.\n\nUser: {msg}"
        )
        chain = prompt | llm | StrOutputParser()
        text = chain.invoke({"msg": user_message[:4000], "lang": preferred_language})
        return text, {"badges": [{"label": "LLM", "type": "ai"}]}
    except Exception:
        return (
            "I could not reach the AI provider right now. Please try again shortly.",
            {"badges": [{"label": "fallback", "type": "system"}]},
        )
