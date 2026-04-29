"""Conversation summary service with context-safe map/reduce generation."""
from __future__ import annotations

import logging
import os
import re
import threading
from datetime import datetime
from typing import Dict, List, Optional, Literal

from sqlalchemy.exc import IntegrityError

from app.models.database_models import ChatHistory, Conversation, ConversationSummary
from app.utils.db import get_db
from app.utils.llm_factory import get_chat_model
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = (
    "You are summarizing a teacher-student lesson conversation. "
    "Create a concise, structured summary of the conversation so far. Include:\n"
    "- Main topic\n"
    "- Key points discussed\n"
    "- Decisions or conclusions\n"
    "- Open questions\n"
    "- Next steps\n\n"
    "Do not invent information. Keep it clear and useful for a teacher reviewing the lesson."
)

SUMMARY_INTENT_PHRASES = {
    "summarize",
    "summary",
    "summarize this",
    "what did we discuss",
}
SUMMARY_INTENT_CLASSIFIER_PROMPT = (
    "Classify whether the user is explicitly asking for a conversation summary.\n"
    "Return a classification with only YES or NO.\n"
    "YES when the user is asking to summarize the discussion/chat/conversation.\n"
    "NO for all other requests."
)

_SUMMARY_LOCKS: Dict[int, threading.Lock] = {}
_SUMMARY_LOCKS_GUARD = threading.Lock()


def _get_summary_lock(conversation_id: int) -> threading.Lock:
    with _SUMMARY_LOCKS_GUARD:
        if conversation_id not in _SUMMARY_LOCKS:
            _SUMMARY_LOCKS[conversation_id] = threading.Lock()
        return _SUMMARY_LOCKS[conversation_id]


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def _safe_int_env(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


def _normalize_message_text(msg: ChatHistory) -> str:
    role = "User" if msg.role == "user" else "Assistant"
    return f"{role}: {msg.message or ''}".strip()


def _invoke_summary_llm(text: str, user_id: int, max_output_tokens: Optional[int] = None) -> str:
    output_tokens = (
        max_output_tokens
        if max_output_tokens is not None
        else _safe_int_env("CONV_SUMMARY_MAX_OUTPUT_TOKENS", 3000)
    )
    llm = get_chat_model(
        user_id=user_id,
        temperature=0.1,
        max_tokens=output_tokens,
    )
    response = llm.invoke(
        [
            SystemMessage(content=SUMMARY_PROMPT),
            HumanMessage(content=text),
        ]
    )
    content = getattr(response, "content", "")
    if isinstance(content, list):
        extracted_parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                extracted_parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text" and item.get("text"):
                    extracted_parts.append(str(item.get("text")))
                elif item.get("content"):
                    extracted_parts.append(str(item.get("content")))
            elif hasattr(item, "text") and getattr(item, "text"):
                extracted_parts.append(str(getattr(item, "text")))
            else:
                extracted_parts.append(str(item))
        content = " ".join(part for part in extracted_parts if str(part).strip())
    return str(content or "").strip()


class SummaryIntentClassification(BaseModel):
    """Structured classifier output for summary intent."""

    intent: Literal["YES", "NO"] = Field(
        ...,
        description="YES when user asks for summary, otherwise NO.",
    )


class ConversationSummaryService:
    """Generate, retrieve, and maintain conversation summaries."""

    @staticmethod
    def is_summary_intent(message: str, user_id: Optional[int] = None) -> bool:
        normalized = re.sub(r"[^a-z0-9\s]", " ", (message or "").strip().lower())
        normalized = " ".join(normalized.split())
        if normalized in SUMMARY_INTENT_PHRASES:
            return True
        return ConversationSummaryService._is_summary_intent_with_llm(
            message=(message or "").strip(),
            user_id=user_id,
        )

    @staticmethod
    def _is_summary_intent_with_llm(message: str, user_id: Optional[int] = None) -> bool:
        if not message:
            return False
        # Allow quick opt-out for environments that prefer strict static intent only.
        if os.getenv("CONV_SUMMARY_ENABLE_LLM_INTENT", "true").lower() not in ("1", "true", "yes"):
            return False
        try:
            llm = get_chat_model(
                user_id=user_id,
                temperature=0.0,
                max_tokens=_safe_int_env("CONV_SUMMARY_INTENT_MAX_OUTPUT_TOKENS", 8),
            )
            structured_llm = llm.with_structured_output(SummaryIntentClassification)
            response = structured_llm.invoke(
                [
                    SystemMessage(content=SUMMARY_INTENT_CLASSIFIER_PROMPT),
                    HumanMessage(content=message),
                ]
            )
            intent_value = str(getattr(response, "intent", "")).strip().upper()
            return intent_value == "YES"
        except Exception as exc:
            logger.warning("LLM summary-intent classifier failed, defaulting to NO: %s", exc)
            return False

    @staticmethod
    def _get_conversation(conversation_id: int, user_id: int) -> Optional[Conversation]:
        db = get_db()
        return db.query(Conversation).filter(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        ).first()

    @staticmethod
    def get_conversation_for_user(conversation_id: int, user_id: int) -> Optional[Conversation]:
        return ConversationSummaryService._get_conversation(conversation_id, user_id)

    @staticmethod
    def _get_ordered_messages(conversation_id: int) -> List[ChatHistory]:
        db = get_db()
        return (
            db.query(ChatHistory)
            .filter(ChatHistory.conversation_id == conversation_id)
            .order_by(ChatHistory.created_at.asc(), ChatHistory.id.asc())
            .all()
        )

    @staticmethod
    def _latest_message(conversation_id: int) -> Optional[ChatHistory]:
        db = get_db()
        return (
            db.query(ChatHistory)
            .filter(ChatHistory.conversation_id == conversation_id)
            .order_by(ChatHistory.created_at.desc(), ChatHistory.id.desc())
            .first()
        )

    @staticmethod
    def get_latest_summary(conversation_id: int, lesson_id: Optional[int] = None) -> Optional[ConversationSummary]:
        db = get_db()
        query = db.query(ConversationSummary).filter(
            ConversationSummary.conversation_id == conversation_id
        )
        if lesson_id is not None:
            query = query.filter(
                (ConversationSummary.lesson_id == lesson_id) | (ConversationSummary.lesson_id.is_(None))
            )
        return query.order_by(ConversationSummary.generated_at.desc(), ConversationSummary.id.desc()).first()

    @staticmethod
    def is_summary_outdated(conversation_id: int, latest_summary: Optional[ConversationSummary]) -> bool:
        latest_message = ConversationSummaryService._latest_message(conversation_id)
        if not latest_message:
            return False
        if latest_summary is None:
            return True
        if latest_summary.last_message_id is not None:
            return int(latest_message.id) > int(latest_summary.last_message_id)
        if latest_summary.last_message_timestamp is not None and latest_message.created_at is not None:
            return latest_message.created_at > latest_summary.last_message_timestamp
        return latest_summary.generated_at < latest_message.created_at

    @staticmethod
    def _build_chunk_texts(messages: List[ChatHistory]) -> List[str]:
        max_chunk_tokens = _safe_int_env("CONV_SUMMARY_CHUNK_INPUT_TOKENS", 1800)
        max_chunk_chars = max(800, max_chunk_tokens * 4)
        chunks: List[str] = []
        current_lines: List[str] = []
        current_chars = 0

        for msg in messages:
            line = _normalize_message_text(msg)
            line_size = len(line) + 1
            if current_lines and (current_chars + line_size) > max_chunk_chars:
                chunks.append("\n".join(current_lines))
                current_lines = []
                current_chars = 0
            current_lines.append(line)
            current_chars += line_size

        if current_lines:
            chunks.append("\n".join(current_lines))
        return chunks

    @staticmethod
    def _to_langchain_messages(messages: List[ChatHistory]) -> List[BaseMessage]:
        converted: List[BaseMessage] = []
        for msg in messages:
            text = (msg.message or "").strip()
            if not text:
                continue
            if msg.role == "user":
                converted.append(HumanMessage(content=text))
            elif msg.role == "bot":
                converted.append(AIMessage(content=text))
        return converted

    @staticmethod
    def _messages_to_text(messages: List[BaseMessage]) -> str:
        lines: List[str] = []
        for msg in messages:
            if isinstance(msg, HumanMessage):
                lines.append(f"User: {msg.content}")
            elif isinstance(msg, AIMessage):
                lines.append(f"Assistant: {msg.content}")
        return "\n".join(lines)

    @staticmethod
    def _summary_looks_truncated(summary_text: str, messages: List[ChatHistory]) -> bool:
        text = (summary_text or "").strip()
        if not text:
            return True
        # Common truncation tails from LLM cutoffs.
        if re.search(r"[:\-]\s*$", text):
            return True
        if re.search(r"\b\d+\.\s+[^\n:]{0,80}:\s*$", text):
            return True
        # If conversation has substance, ultra-short summaries are likely cut.
        non_empty_messages = [m for m in messages if (m.message or "").strip()]
        if len(non_empty_messages) >= 6 and len(text) < 160:
            return True
        return False

    @staticmethod
    def _fallback_summary_from_messages(messages: List[ChatHistory]) -> str:
        """Build a deterministic non-empty fallback summary when LLM output is blank."""
        user_messages = [m.message.strip() for m in messages if m.role == "user" and (m.message or "").strip()]
        bot_messages = [m.message.strip() for m in messages if m.role == "bot" and (m.message or "").strip()]
        main_topic = user_messages[0] if user_messages else "Conversation context available in chat history."
        key_points = user_messages[-3:] if user_messages else ["No explicit user points captured."]
        latest_response = bot_messages[-1] if bot_messages else "No assistant response captured."
        bullet_points = "\n".join(f"- {point}" for point in key_points)
        return (
            "Main topic:\n"
            f"- {main_topic}\n\n"
            "Key points discussed:\n"
            f"{bullet_points}\n\n"
            "Decisions or conclusions:\n"
            f"- {latest_response}\n\n"
            "Open questions:\n"
            "- None explicitly identified.\n\n"
            "Next steps:\n"
            "- Continue the lesson conversation and request an updated summary when needed."
        )

    @staticmethod
    def _generate_summary_text(messages: List[ChatHistory], user_id: int) -> str:
        if not messages:
            return "No summary available yet."

        lc_messages = ConversationSummaryService._to_langchain_messages(messages)
        full_text = ConversationSummaryService._messages_to_text(lc_messages)
        max_input_tokens = _safe_int_env("CONV_SUMMARY_MAX_INPUT_TOKENS", 3000)
        if _estimate_tokens(full_text) <= max_input_tokens:
            initial = _invoke_summary_llm(full_text, user_id=user_id)
            if not ConversationSummaryService._summary_looks_truncated(initial, messages):
                return initial
            retry_tokens = _safe_int_env("CONV_SUMMARY_RETRY_MAX_OUTPUT_TOKENS", 1100)
            retried = _invoke_summary_llm(
                full_text,
                user_id=user_id,
                max_output_tokens=retry_tokens,
            )
            return retried

        chunk_summaries: List[str] = []
        for idx, chunk_text in enumerate(ConversationSummaryService._build_chunk_texts(messages), start=1):
            partial = _invoke_summary_llm(chunk_text, user_id=user_id)
            if partial:
                chunk_summaries.append(f"Chunk {idx} summary:\n{partial}")

        if not chunk_summaries:
            return "No summary available yet."

        combined = "\n\n".join(chunk_summaries)
        return _invoke_summary_llm(combined, user_id=user_id)

    @staticmethod
    def generate_and_persist_summary(
        conversation_id: int,
        user_id: int,
        lesson_id: Optional[int] = None,
        force: bool = False,
    ) -> Dict:
        db = get_db()
        conversation = ConversationSummaryService._get_conversation(conversation_id, user_id)
        if not conversation:
            raise ValueError("Conversation not found or access denied")

        with _get_summary_lock(conversation_id):
            latest_summary = ConversationSummaryService.get_latest_summary(conversation_id, lesson_id)
            is_outdated = ConversationSummaryService.is_summary_outdated(conversation_id, latest_summary)

            if latest_summary and not force and not is_outdated:
                return {
                    "summary": latest_summary.summary_text,
                    "generated_at": latest_summary.generated_at,
                    "last_message_id": latest_summary.last_message_id,
                    "last_message_timestamp": latest_summary.last_message_timestamp,
                    "is_outdated": False,
                    "was_regenerated": False,
                }

            messages = ConversationSummaryService._get_ordered_messages(conversation_id)
            latest_message = messages[-1] if messages else None

            if not messages:
                return {
                    "summary": "No summary available yet.",
                    "generated_at": None,
                    "last_message_id": None,
                    "last_message_timestamp": None,
                    "is_outdated": False,
                    "was_regenerated": False,
                }

            summary_text = ConversationSummaryService._generate_summary_text(messages, user_id=user_id)
            if not summary_text:
                logger.warning(
                    "Summary generation returned empty content for conversation %s; using fallback summary.",
                    conversation_id,
                )
                summary_text = ConversationSummaryService._fallback_summary_from_messages(messages)

            generated_at = datetime.utcnow()
            summary_row = ConversationSummary(
                conversation_id=conversation_id,
                lesson_id=lesson_id,
                summary_text=summary_text,
                generated_at=generated_at,
                last_message_id=latest_message.id if latest_message else None,
                last_message_timestamp=latest_message.created_at if latest_message else None,
            )
            db.add(summary_row)
            try:
                db.commit()
                db.refresh(summary_row)
            except IntegrityError:
                db.rollback()
                summary_row = ConversationSummaryService.get_latest_summary(conversation_id, lesson_id)
                if not summary_row:
                    raise
            except Exception:
                db.rollback()
                raise

            return {
                "summary": summary_row.summary_text,
                "generated_at": summary_row.generated_at,
                "last_message_id": summary_row.last_message_id,
                "last_message_timestamp": summary_row.last_message_timestamp,
                "is_outdated": False,
                "was_regenerated": True,
            }
