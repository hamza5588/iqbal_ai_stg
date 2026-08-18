"""Optional token sink for RAG chat streaming.

When admin streaming is off, nothing in this module is used and chat stays on
the existing blocking JSON path.

The sink is stored on a threading.local (and a ContextVar backup). LangChain
copies contextvars when entering a runnable, which would hide a ContextVar-only
sink and force a blocking invoke() — tokens would then appear only after the
full answer was generated.
"""
from __future__ import annotations

import threading
import time
from contextvars import ContextVar, Token
from typing import Any, Optional, Protocol


class RagTokenSink(Protocol):
    def on_token(self, text: str) -> None:
        """A content delta that is safe to show (no tool-call payload)."""

    def on_retract(self) -> None:
        """Discard speculative tokens; this LLM call was a tool round."""

    def on_replace(self, text: str) -> None:
        """Replace the visible draft (quality-gate rewrite or sanitizer)."""


class _ThreadSink(threading.local):
    sink: Optional[RagTokenSink] = None


_thread_sink = _ThreadSink()
_current_sink: ContextVar[Optional[RagTokenSink]] = ContextVar("rag_token_sink", default=None)


def get_rag_token_sink() -> Optional[RagTokenSink]:
    local = getattr(_thread_sink, "sink", None)
    if local is not None:
        return local
    return _current_sink.get()


def set_rag_token_sink(sink: Optional[RagTokenSink]) -> Token:
    _thread_sink.sink = sink
    return _current_sink.set(sink)


def reset_rag_token_sink(token: Token) -> None:
    _thread_sink.sink = None
    _current_sink.reset(token)


class QueueRagTokenSink:
    """Puts SSE-ready dicts onto a queue for the request thread to yield."""

    def __init__(self, event_queue: Any) -> None:
        self._queue = event_queue

    def on_token(self, text: str) -> None:
        if text:
            self._queue.put({"type": "token", "text": text})
            # Give the WSGI thread a chance to flush this event immediately.
            time.sleep(0)

    def on_retract(self) -> None:
        self._queue.put({"type": "retract"})
        time.sleep(0)

    def on_replace(self, text: str) -> None:
        self._queue.put({"type": "replace", "text": text or ""})
        time.sleep(0)
