"""
Tests for Phase 4 routing-decision tracing: app/utils/router_telemetry.py's
persist_router_decision_event write path.

No live DB, no live LLM: uses a FakeDbSession double (following the same shape as
llm_gateway.persist_llm_usage_event's own local `from app.utils.db import get_db` import - the
function re-imports get_db from app.utils.db at call time, so monkeypatching the module
attribute app.utils.db.get_db is what actually takes effect, not patching a name inside
router_telemetry itself). Real LlmTelemetryContext plumbing (app.utils.llm_gateway) is used
as-is (it's pure ContextVar bookkeeping, no I/O) rather than mocked, since exercising the real
context-read path is exactly what should be under test here.
"""
import pytest

from app.models.database_models import RouterDecisionEvent
from app.utils.llm_gateway import (
    LlmTelemetryContext,
    reset_llm_telemetry_context,
    set_llm_telemetry_context,
)
from app.utils.router_telemetry import persist_router_decision_event


class FakeDbSession:
    def __init__(self, raise_on_commit=False):
        self.added = []
        self.committed = False
        self.rolled_back = False
        self.raise_on_commit = raise_on_commit

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        if self.raise_on_commit:
            raise RuntimeError("commit boom")
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class _FakeRouterOutput:
    """Plain stand-in - persist_router_decision_event reads router_output via getattr, not an
    isinstance check, so it should work with anything exposing the right attributes."""

    def __init__(self, intent="document_qa", requested_brevity=False, meta_conversation_scope=None,
                 meta_conversation_n=None, reasoning=""):
        self.intent = intent
        self.requested_brevity = requested_brevity
        self.meta_conversation_scope = meta_conversation_scope
        self.meta_conversation_n = meta_conversation_n
        self.reasoning = reasoning


@pytest.fixture
def telemetry_context():
    """Installs a known LlmTelemetryContext for the duration of the test, restoring afterward."""
    ctx = LlmTelemetryContext(
        user_id=42,
        user_role="student",
        workflow="rag_chat",
        traffic_source="production",
        conversation_id=7,
        thread_id="thread-abc",
    )
    token = set_llm_telemetry_context(ctx)
    yield ctx
    reset_llm_telemetry_context(token)


class TestPersistRouterDecisionEvent:
    def test_writes_expected_fields_and_commits(self, monkeypatch, telemetry_context):
        fake_db = FakeDbSession()
        monkeypatch.setattr("app.utils.db.get_db", lambda: fake_db)
        monkeypatch.setenv("ROUTER_DECISION_TRACING_ENABLED", "true")

        router_output = _FakeRouterOutput(
            intent="meta_conversation",
            requested_brevity=True,
            meta_conversation_scope="last_question",
            meta_conversation_n=None,
            reasoning="user asked what they asked before",
        )
        persist_router_decision_event(
            router_output=router_output,
            router_used_fallback=False,
            fallback_reason=None,
            prefetch_branch="meta_conversation",
            meta_conversation_active=True,
            own_answer_followup_active=False,
            tool_rounds_used=0,
            tool_round_limit_reached=False,
            outcome="success",
            duration_ms=1234,
        )

        assert len(fake_db.added) == 1
        ev = fake_db.added[0]
        assert isinstance(ev, RouterDecisionEvent)
        assert fake_db.committed is True

        # Actor/context fields sourced from LlmTelemetryContext.
        assert ev.user_id == 42
        assert ev.user_role == "student"
        assert ev.workflow == "rag_chat"
        assert ev.traffic_source == "production"
        assert ev.conversation_id == 7
        assert ev.thread_id == "thread-abc"

        # RouterOutput fields.
        assert ev.intent == "meta_conversation"
        assert ev.requested_brevity is True
        assert ev.meta_conversation_scope == "last_question"
        assert ev.meta_conversation_n is None
        assert ev.reasoning == "user asked what they asked before"

        # Fallback / branch / outcome fields.
        assert ev.router_used_fallback is False
        assert ev.fallback_reason is None
        assert ev.prefetch_branch == "meta_conversation"
        assert ev.meta_conversation_active is True
        assert ev.own_answer_followup_active is False
        assert ev.tool_rounds_used == 0
        assert ev.tool_round_limit_reached is False
        assert ev.outcome == "success"
        assert ev.duration_ms == 1234

    def test_records_fallback_metadata(self, monkeypatch, telemetry_context):
        fake_db = FakeDbSession()
        monkeypatch.setattr("app.utils.db.get_db", lambda: fake_db)
        monkeypatch.setenv("ROUTER_DECISION_TRACING_ENABLED", "true")

        persist_router_decision_event(
            router_output=_FakeRouterOutput(intent="document_qa"),
            router_used_fallback=True,
            fallback_reason="exception:TimeoutError",
            outcome="success",
        )

        ev = fake_db.added[0]
        assert ev.router_used_fallback is True
        assert ev.fallback_reason == "exception:TimeoutError"

    def test_negative_duration_is_clamped_to_zero(self, monkeypatch, telemetry_context):
        fake_db = FakeDbSession()
        monkeypatch.setattr("app.utils.db.get_db", lambda: fake_db)
        monkeypatch.setenv("ROUTER_DECISION_TRACING_ENABLED", "true")

        persist_router_decision_event(
            router_output=_FakeRouterOutput(),
            duration_ms=-50,
        )
        assert fake_db.added[0].duration_ms == 0

    def test_swallows_db_exception_and_rolls_back(self, monkeypatch, telemetry_context):
        def _raise():
            raise RuntimeError("db unavailable")

        monkeypatch.setattr("app.utils.db.get_db", _raise)
        monkeypatch.setenv("ROUTER_DECISION_TRACING_ENABLED", "true")

        # Must not raise - a tracing failure can never break a chat turn.
        persist_router_decision_event(router_output=_FakeRouterOutput())

    def test_swallows_commit_exception_and_rolls_back(self, monkeypatch, telemetry_context):
        fake_db = FakeDbSession(raise_on_commit=True)
        monkeypatch.setattr("app.utils.db.get_db", lambda: fake_db)
        monkeypatch.setenv("ROUTER_DECISION_TRACING_ENABLED", "true")

        persist_router_decision_event(router_output=_FakeRouterOutput())

        assert fake_db.rolled_back is True

    def test_disabled_flag_short_circuits_before_any_db_access(self, monkeypatch, telemetry_context):
        calls = {"n": 0}

        def _spy_get_db():
            calls["n"] += 1
            return FakeDbSession()

        monkeypatch.setattr("app.utils.db.get_db", _spy_get_db)
        monkeypatch.setenv("ROUTER_DECISION_TRACING_ENABLED", "false")

        persist_router_decision_event(router_output=_FakeRouterOutput())

        assert calls["n"] == 0

    def test_reasoning_and_error_message_are_truncated(self, monkeypatch, telemetry_context):
        fake_db = FakeDbSession()
        monkeypatch.setattr("app.utils.db.get_db", lambda: fake_db)
        monkeypatch.setenv("ROUTER_DECISION_TRACING_ENABLED", "true")

        long_reasoning = "x" * 3000
        long_error = "y" * 3000
        persist_router_decision_event(
            router_output=_FakeRouterOutput(reasoning=long_reasoning),
            outcome="error",
            error_class="RuntimeError",
            error_message=long_error,
        )

        ev = fake_db.added[0]
        assert len(ev.reasoning) <= 2001  # 2000 chars + the truncation ellipsis
        assert ev.reasoning.endswith("…")
        assert len(ev.error_message) <= 2001
        assert ev.error_message.endswith("…")
        assert ev.error_class == "RuntimeError"

    def test_missing_router_output_attrs_default_gracefully(self, monkeypatch, telemetry_context):
        """getattr-based field access must not blow up on a minimal/duck-typed router_output."""
        fake_db = FakeDbSession()
        monkeypatch.setattr("app.utils.db.get_db", lambda: fake_db)
        monkeypatch.setenv("ROUTER_DECISION_TRACING_ENABLED", "true")

        class _Bare:
            intent = "document_qa"

        persist_router_decision_event(router_output=_Bare())

        ev = fake_db.added[0]
        assert ev.intent == "document_qa"
        assert ev.requested_brevity is None
        assert ev.meta_conversation_scope is None
        assert ev.meta_conversation_n is None
        assert ev.reasoning is None

    def test_no_telemetry_context_defaults_safely(self, monkeypatch):
        """With no context installed at all, get_llm_telemetry_context() returns a default
        LlmTelemetryContext() - persisting should still succeed with those defaults."""
        fake_db = FakeDbSession()
        monkeypatch.setattr("app.utils.db.get_db", lambda: fake_db)
        monkeypatch.setenv("ROUTER_DECISION_TRACING_ENABLED", "true")

        persist_router_decision_event(router_output=_FakeRouterOutput())

        ev = fake_db.added[0]
        assert ev.user_id is None
        assert ev.workflow == "unknown"
        assert ev.traffic_source == "production"
