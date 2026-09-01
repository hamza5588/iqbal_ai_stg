"""Short-term and long-term memory for the general AI Tutor modal."""
from __future__ import annotations

import logging
import os
from typing import List, Optional

from app.models.lms_models import TutorChatMessage, TutorChatSession
from app.utils.constants import MAX_MESSAGE_WINDOW, SUMMARY_THRESHOLD
from app.utils.db import get_db

logger = logging.getLogger(__name__)

UI_MESSAGE_LIMIT = 100


def get_or_create_session(user_id: int, mode: str = "student") -> TutorChatSession:
    db = get_db()
    mode = "teacher" if mode == "teacher" else "student"
    session = (
        db.query(TutorChatSession)
        .filter(TutorChatSession.user_id == user_id, TutorChatSession.mode == mode)
        .first()
    )
    if session:
        return session
    session = TutorChatSession(user_id=user_id, mode=mode)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_session_for_user(user_id: int, mode: str = "student") -> Optional[TutorChatSession]:
    db = get_db()
    mode = "teacher" if mode == "teacher" else "student"
    return (
        db.query(TutorChatSession)
        .filter(TutorChatSession.user_id == user_id, TutorChatSession.mode == mode)
        .first()
    )


def get_ui_messages(user_id: int, mode: str = "student", limit: int = UI_MESSAGE_LIMIT) -> List[dict]:
    session = get_session_for_user(user_id, mode)
    if not session:
        return []
    db = get_db()
    rows = (
        db.query(TutorChatMessage)
        .filter(TutorChatMessage.session_id == session.id)
        .order_by(TutorChatMessage.created_at.asc())
        .limit(limit)
        .all()
    )
    return [
        {
            "role": "user" if m.role == "user" else "bot",
            "text": m.content,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in rows
    ]


def get_llm_history(session_id: int, window: int = MAX_MESSAGE_WINDOW) -> List[dict]:
    """Recent messages for short-term LLM context (excludes current user turn)."""
    db = get_db()
    rows = (
        db.query(TutorChatMessage)
        .filter(TutorChatMessage.session_id == session_id)
        .order_by(TutorChatMessage.created_at.desc())
        .limit(window)
        .all()
    )
    rows.reverse()
    return [{"role": m.role, "content": m.content} for m in rows]


def append_message(session_id: int, role: str, content: str) -> TutorChatMessage:
    db = get_db()
    msg = TutorChatMessage(session_id=session_id, role=role, content=content)
    db.add(msg)
    session = db.query(TutorChatSession).filter(TutorChatSession.id == session_id).first()
    if session:
        session.updated_at = msg.created_at
    db.commit()
    db.refresh(msg)
    return msg


def get_message_count(session_id: int) -> int:
    db = get_db()
    return db.query(TutorChatMessage).filter(TutorChatMessage.session_id == session_id).count()


def clear_session(user_id: int, mode: str = "student") -> None:
    db = get_db()
    mode = "teacher" if mode == "teacher" else "student"
    session = (
        db.query(TutorChatSession)
        .filter(TutorChatSession.user_id == user_id, TutorChatSession.mode == mode)
        .first()
    )
    if not session:
        return
    db.query(TutorChatMessage).filter(TutorChatMessage.session_id == session.id).delete()
    session.summary_text = None
    db.commit()


def get_summary_context(session: TutorChatSession) -> Optional[str]:
    if session.summary_text and session.summary_text.strip():
        return (
            "Long-term memory — summary of earlier tutor conversations with this student:\n"
            + session.summary_text.strip()
        )
    return None


def maybe_refresh_summary(session: TutorChatSession, api_key: str = "") -> None:
    """Compress older messages into summary_text when thread grows (long-term memory)."""
    count = get_message_count(session.id)
    if count <= SUMMARY_THRESHOLD:
        return

    db = get_db()
    rows = (
        db.query(TutorChatMessage)
        .filter(TutorChatMessage.session_id == session.id)
        .order_by(TutorChatMessage.created_at.asc())
        .all()
    )
    keep_recent = rows[-MAX_MESSAGE_WINDOW:]
    to_summarize = rows[: -MAX_MESSAGE_WINDOW]
    if not to_summarize:
        return

    lines = []
    if session.summary_text:
        lines.append(f"Prior summary: {session.summary_text[:2000]}")
    for m in to_summarize:
        speaker = "Student" if m.role == "user" else "Tutor"
        lines.append(f"{speaker}: {m.content[:500]}")

    prompt = (
        "Summarize this AI tutor conversation in 150-250 words. "
        "Keep: topics discussed, student struggles, strategies suggested, and open questions. "
        "Do not invent facts.\n\n"
        + "\n".join(lines[-40:])
    )

    try:
        from app.utils.llm_factory import create_llm, get_chat_model

        if not api_key:
            api_key = os.getenv("GROQ_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
        if api_key:
            llm = create_llm(api_key=api_key)
        else:
            llm = get_chat_model(temperature=0.2, max_tokens=512)
        resp = llm.invoke([{"role": "user", "content": prompt}])
        summary = getattr(resp, "content", str(resp))
        if isinstance(summary, str) and summary.strip():
            session.summary_text = summary.strip()[:4000]
            for m in to_summarize:
                db.delete(m)
            db.commit()
            logger.info("Updated tutor long-term summary for session %s", session.id)
    except Exception as exc:
        logger.warning("Tutor summary refresh failed for session %s: %s", session.id, exc)
        db.rollback()


def chat_with_memory(
    user_id: int,
    message: str,
    mode: str,
    api_key: str,
    context: Optional[str],
    tutor_chat_fn,
) -> dict:
    """
    Persist turn, load short-term history + long-term summary, call tutor_chat_fn.
    tutor_chat_fn signature: (message, api_key, mode, context, history, assist_level) -> str
    """
    session = get_or_create_session(user_id, mode)
    append_message(session.id, "user", message)

    history = get_llm_history(session.id, window=MAX_MESSAGE_WINDOW)
    # Drop the message we just appended — tutor_chat adds it again
    if history and history[-1]["role"] == "user" and history[-1]["content"] == message:
        history = history[:-1]

    summary_ctx = get_summary_context(session)
    full_context = context or ""
    if summary_ctx:
        full_context = (full_context + "\n\n" + summary_ctx).strip() if full_context else summary_ctx

    reply = tutor_chat_fn(
        message,
        api_key,
        mode="teacher" if mode == "teacher" else "student",
        context=full_context or None,
        history=history,
    )
    append_message(session.id, "assistant", reply)
    maybe_refresh_summary(session, api_key=api_key)

    return {
        "reply": reply,
        "session_id": session.id,
        "message_count": get_message_count(session.id),
        "has_long_term_memory": bool(session.summary_text),
    }
