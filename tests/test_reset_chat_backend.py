"""
Regression tests for Group A (bugs #2 and #26): Reset Chat must clear the
LangGraph checkpointed conversation history for a thread without deleting
the underlying document (vectors/uploaded file).

Requires the project's real dependencies (pip install -r requirements.txt).
"""
import pytest

rag_service = pytest.importorskip("app.utils.rag_service")


def test_clear_thread_conversation_history_uses_checkpointer_delete_thread(monkeypatch):
    calls = []

    class FakeCheckpointer:
        def delete_thread(self, thread_id):
            calls.append(thread_id)

    monkeypatch.setattr(rag_service, "checkpointer", FakeCheckpointer())

    result = rag_service.clear_thread_conversation_history("user_1_abc123")

    assert result["success"] is True
    assert calls == ["user_1_abc123"]


def test_clear_thread_conversation_history_reports_failure_when_unsupported(monkeypatch):
    class FakeCheckpointerNoDeleteThread:
        pass

    monkeypatch.setattr(rag_service, "checkpointer", FakeCheckpointerNoDeleteThread())

    result = rag_service.clear_thread_conversation_history("user_1_abc123")

    assert result["success"] is False


def test_clear_thread_conversation_history_never_deletes_the_document(monkeypatch):
    """
    Reset Chat must only clear conversation history, never the document
    (Milvus vectors / uploaded file) - that's what the pre-existing, more
    destructive delete_thread() does and must stay untouched by a reset.
    """
    document_delete_calls = []
    monkeypatch.setattr(
        rag_service,
        "delete_thread",
        lambda thread_id: document_delete_calls.append(thread_id) or {"success": True},
    )

    class FakeCheckpointer:
        def delete_thread(self, thread_id):
            pass

    monkeypatch.setattr(rag_service, "checkpointer", FakeCheckpointer())

    rag_service.clear_thread_conversation_history("user_1_abc123")

    assert document_delete_calls == []


def test_clear_thread_conversation_history_handles_checkpointer_exception(monkeypatch):
    class FakeCheckpointerRaises:
        def delete_thread(self, thread_id):
            raise RuntimeError("db connection lost")

    monkeypatch.setattr(rag_service, "checkpointer", FakeCheckpointerRaises())

    result = rag_service.clear_thread_conversation_history("user_1_abc123")

    assert result["success"] is False
    assert "Failed to clear conversation history" in result["message"]
