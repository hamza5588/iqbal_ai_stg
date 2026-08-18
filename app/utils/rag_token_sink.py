"""Optional token sink for RAG chat streaming.

When admin streaming is off, nothing in this module is used and chat stays on
the existing blocking JSON path.

The sink is stored in a ContextVar (not LangGraph config) so the checkpointer
never tries to serialize a live queue object.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any, Optional, Protocol


class RagTokenSink(Protocol):
    def on_token(self, text: str) -> None:
        """A content delta that is safe to show (no tool-call payload)."""

    def on_retract(self) -> None:
        """Discard speculative tokens; this LLM call was a tool round."""

    def on_replace(self, text: str) -> None:
        """Replace the visible draft (quality-gate rewrite or sanitizer)."""


_current_sink: ContextVar[Optional[RagTokenSink]] = ContextVar("rag_token_sink", default=None)


def get_rag_token_sink() -> Optional[RagTokenSink]:
    return _current_sink.get()


def set_rag_token_sink(sink: Optional[RagTokenSink]) -> Token:
    return _current_sink.set(sink)


def reset_rag_token_sink(token: Token) -> None:
    _current_sink.reset(token)


class QueueRagTokenSink:
    """Puts SSE-ready dicts onto a queue for the request thread to yield."""

    def __init__(self, event_queue: Any) -> None:
        self._queue = event_queue

    def on_token(self, text: str) -> None:
        if text:
            self._queue.put({"type": "token", "text": text})

    def on_retract(self) -> None:
        self._queue.put({"type": "retract"})

    def on_replace(self, text: str) -> None:
        self._queue.put({"type": "replace", "text": text or ""})
