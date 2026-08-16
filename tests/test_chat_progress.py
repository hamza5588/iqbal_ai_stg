"""
Tests for the live "what is the AI doing right now" progress indicator.

Backend: app/utils/chat_progress.py (Redis-backed, best-effort, never raises) and its wiring
into rag_service.py's chat_node/rag_tool/teach_topic_tool/list_topics_whole_doc_tool/
get_page_tool/finalize_lesson_tool. Frontend: templates/teacher_dashboard.html polls
GET /api/rag/chat-progress/<thread_id> every 800ms while a chat turn is in flight and updates
the "AI is thinking..." label instead of leaving it static.

Requires the project's real dependencies (pip install -r requirements.txt).
"""
import re
from pathlib import Path

import pytest

chat_progress = pytest.importorskip("app.utils.chat_progress")

ROOT = Path(__file__).resolve().parent.parent
RAG_SERVICE_SRC = (ROOT / "app" / "utils" / "rag_service.py").read_text(encoding="utf-8")
RAG_ROUTES_SRC = (ROOT / "app" / "routes" / "rag_routes.py").read_text(encoding="utf-8")
DASHBOARD_SRC = (ROOT / "templates" / "teacher_dashboard.html").read_text(encoding="utf-8")


class _FakeRedis:
    """In-process stand-in for redis.Redis so tests don't need a live Redis server."""

    def __init__(self):
        self.store = {}

    def ping(self):
        return True

    def set(self, key, value, ex=None):
        self.store[key] = value

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture(autouse=True)
def _reset_client(monkeypatch):
    monkeypatch.setattr(chat_progress, "_redis_client", None)
    yield
    monkeypatch.setattr(chat_progress, "_redis_client", None)


def test_set_and_get_progress_roundtrip(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(chat_progress, "_get_redis_client", lambda: fake)

    chat_progress.set_progress("thread_1", "🔍 Searching the document...")
    result = chat_progress.get_progress("thread_1")

    assert result == {"message": "🔍 Searching the document..."}


def test_get_progress_returns_empty_when_nothing_set(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(chat_progress, "_get_redis_client", lambda: fake)

    assert chat_progress.get_progress("thread_never_set") == {}


def test_get_progress_returns_empty_when_redis_unavailable(monkeypatch):
    monkeypatch.setattr(chat_progress, "_get_redis_client", lambda: None)
    assert chat_progress.get_progress("thread_1") == {}


def test_set_progress_never_raises_when_redis_unavailable(monkeypatch):
    monkeypatch.setattr(chat_progress, "_get_redis_client", lambda: None)
    # Must not raise - progress tracking is a UX nicety, never a dependency for the chat turn.
    chat_progress.set_progress("thread_1", "🔍 Searching the document...")


def test_set_progress_never_raises_on_broken_redis_client(monkeypatch):
    class _BrokenRedis:
        def set(self, *a, **k):
            raise ConnectionError("redis is down")

    monkeypatch.setattr(chat_progress, "_get_redis_client", lambda: _BrokenRedis())
    chat_progress.set_progress("thread_1", "🔍 Searching the document...")  # must not raise


def test_clear_progress_removes_the_key(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(chat_progress, "_get_redis_client", lambda: fake)

    chat_progress.set_progress("thread_1", "🔍 Searching the document...")
    chat_progress.clear_progress("thread_1")

    assert chat_progress.get_progress("thread_1") == {}


def test_empty_thread_id_or_message_are_no_ops(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(chat_progress, "_get_redis_client", lambda: fake)

    chat_progress.set_progress("", "message")
    chat_progress.set_progress("thread_1", "")
    assert fake.store == {}


# --- Wiring: rag_service.py tool/node hooks ---------------------------------------

def test_progress_hooks_wired_into_key_tools():
    for marker in (
        '_set_chat_progress(thread_id, "🔍 Searching the document...")',
        '_set_chat_progress(thread_id, f"📚 Gathering every section on',
        '_set_chat_progress(thread_id, "📋 Reviewing the document outline...")',
        '_set_chat_progress(thread_id, f"📄 Looking up page {page}...")',
        '_set_chat_progress(thread_id, "💾 Saving your lesson...")',
        '_set_chat_progress(thread_id_str, "🤔 Thinking about your question...")',
        '_set_chat_progress(thread_id_str, "✍️ Composing your answer...")',
    ):
        assert marker in RAG_SERVICE_SRC, f"missing progress hook: {marker}"


def test_chat_progress_import_present():
    assert "from app.utils.chat_progress import set_progress as _set_chat_progress" in RAG_SERVICE_SRC


# --- Wiring: rag_routes.py polling endpoint ---------------------------------------

def test_chat_progress_route_exists_and_scoped_to_owner():
    assert "@bp.route('/chat-progress/<thread_id>', methods=['GET'])" in RAG_ROUTES_SRC
    m = re.search(r"def chat_progress\(thread_id\):(.*?)\n@bp\.route", RAG_ROUTES_SRC, re.S)
    assert m, "chat_progress route body not found"
    body = m.group(1)
    # Must verify the requesting user actually owns this thread before returning anything.
    assert "RAGThread" in body and "user_id=user_id" in body


# --- Wiring: frontend polling ------------------------------------------------------

def test_frontend_polls_progress_endpoint_and_updates_indicator():
    assert "function startChatProgressPolling(threadId)" in DASHBOARD_SRC
    assert "function stopChatProgressPolling()" in DASHBOARD_SRC
    assert "/api/rag/chat-progress/" in DASHBOARD_SRC
    assert "function updateTypingIndicatorText(message)" in DASHBOARD_SRC
    assert 'id="typing-indicator-text"' in DASHBOARD_SRC


def test_frontend_starts_polling_on_send_and_stops_in_finally():
    start_idx = DASHBOARD_SRC.index("startChatProgressPolling(window.currentRAGThreadId);")
    show_indicator_idx = DASHBOARD_SRC.rindex("showTypingIndicator();", 0, start_idx)
    # Polling must start right after (not before) the indicator appears.
    assert show_indicator_idx < start_idx

    finally_block = re.search(
        r"\} finally \{(.*?)\n        \}\n      \}",
        DASHBOARD_SRC[start_idx:],
        re.S,
    )
    assert finally_block, "finally block after polling start not found"
    body = finally_block.group(1)
    assert "stopChatProgressPolling();" in body
    assert "removeTypingIndicator();" in body


if __name__ == "__main__":
    import sys
    exit_code = pytest.main([__file__, "-v"])
    sys.exit(exit_code)
