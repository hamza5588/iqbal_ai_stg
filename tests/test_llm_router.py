"""
Tests for the Phase 1 LLM-driven turn-intent router (app/utils/rag_service.py).

Covers:
1. RouterOutput schema validity.
2. _router_fallback_from_regex parametrized over the exact production bug phrasings.
3. _classify_turn_intent with a fake LLM (rate-limiter wiring, exception fallback, kill-switch).
4. Static wiring checks (inspect.getsource) proving the router output actually reaches the
   places that must consume it.
5. _find_last_n_real_user_questions / _find_last_real_user_question.
6. _build_meta_conversation_prefetch_blob.
7. _chat_build_system_message branch selection with a directly-constructed router_output.
8. Consumption tests for the two production bugs the router fixes.
"""
import inspect
import types

import pytest
from pydantic import ValidationError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.utils import rag_service
from app.utils.rag_service import (
    RouterOutput,
    _build_meta_conversation_prefetch_blob,
    _chat_build_system_message,
    _chat_invoke_llm_with_retry,
    _ChatTurnSystemPrep,
    _classify_turn_intent,
    _find_last_human_message_index_and_text,
    _find_last_n_real_user_questions,
    _find_last_real_user_question,
    _looks_like_meta_conversation_text,
    _router_fallback_from_regex,
)


# ---------------------------------------------------------------------------
# 1. RouterOutput schema
# ---------------------------------------------------------------------------

class TestRouterOutputSchema:
    def test_minimal_valid_construction(self):
        out = RouterOutput(intent="document_qa")
        assert out.intent == "document_qa"
        assert out.requested_brevity is False
        assert out.meta_conversation_scope is None
        assert out.meta_conversation_n is None
        assert out.reasoning == ""

    @pytest.mark.parametrize(
        "intent",
        [
            "document_qa", "lesson_generation", "own_answer_followup",
            "meta_conversation", "greeting_casual", "clarification",
            "general_knowledge_qa", "lesson_modification", "lesson_qa", "lesson_save",
        ],
    )
    def test_all_ten_intents_are_valid(self, intent):
        out = RouterOutput(intent=intent)
        assert out.intent == intent

    def test_invalid_intent_rejected(self):
        with pytest.raises(ValidationError):
            RouterOutput(intent="not_a_real_intent")

    def test_missing_intent_rejected(self):
        with pytest.raises(ValidationError):
            RouterOutput()

    def test_full_meta_conversation_construction(self):
        out = RouterOutput(
            intent="meta_conversation",
            meta_conversation_scope="last_n_questions",
            meta_conversation_n=3,
            reasoning="user asked for last 3 questions",
        )
        assert out.meta_conversation_scope == "last_n_questions"
        assert out.meta_conversation_n == 3

    def test_invalid_meta_scope_rejected(self):
        with pytest.raises(ValidationError):
            RouterOutput(intent="meta_conversation", meta_conversation_scope="not_a_real_scope")

    def test_requested_brevity_flag(self):
        out = RouterOutput(intent="document_qa", requested_brevity=True)
        assert out.requested_brevity is True

    def test_no_extra_fields_deliberately_deferred(self):
        """Phase 1 deliberately omits confidence/requires_permission/operations - later phases."""
        fields = set(RouterOutput.model_fields.keys())
        assert fields == {
            "intent", "requested_brevity", "meta_conversation_scope",
            "meta_conversation_n", "reasoning",
        }


# ---------------------------------------------------------------------------
# 2. _router_fallback_from_regex
# ---------------------------------------------------------------------------

class TestRouterFallbackFromRegex:
    @pytest.mark.parametrize(
        "text,expected_intent",
        [
            ("create a lesson on photosynthesis", "lesson_generation"),
            ("please generate a lecture about gravity", "lesson_generation"),
            ("explain why you used 2x there", "own_answer_followup"),
            ("how did you get that number", "own_answer_followup"),
            ("explain", "clarification"),
            ("what", "clarification"),
            ("hi", "clarification"),
            ("what is the discriminant of a quadratic equation", "document_qa"),
        ],
    )
    def test_regex_fallback_priority_matches_pre_phase1_behavior(self, text, expected_intent):
        out = _router_fallback_from_regex(text)
        assert isinstance(out, RouterOutput)
        assert out.intent == expected_intent

    def test_regex_fallback_document_qa_with_brevity_phrasing_stays_document_qa(self):
        """Not a meta-conversation message - "just answer main one line" asks about document
        content with a brevity request, so it must stay document_qa even through the
        meta-conversation check added below (regex fallback has no brevity detection at all;
        that's the router LLM's job - the fallback only needs to get the base intent right)."""
        out = _router_fallback_from_regex("what is zero discriminat just answer main one line")
        assert out.intent == "document_qa"

    @pytest.mark.parametrize(
        "text",
        [
            "what i ask last question?",
            "what i ask?",
            "paste exactly to me what ia sk",
        ],
    )
    def test_regex_fallback_now_detects_common_meta_conversation_phrasings(self, text):
        """
        QA-sweep bug: a router LLM failure/timeout on a meta-conversation message (e.g. "what
        did I ask you first in this conversation?") fell through to document_qa and ran a
        pointless document search - confirmed live. _router_fallback_from_regex now reuses
        _looks_like_meta_conversation_text (the same loose pattern list already used to skip
        past prior meta-turns elsewhere) to catch the common phrasings, closing most - not
        all - of the gap. See test_regex_fallback_residual_meta_conversation_gap below for
        what's still NOT caught.
        """
        out = _router_fallback_from_regex(text)
        assert out.intent == "meta_conversation"
        assert out.meta_conversation_scope == "last_question"

    def test_regex_fallback_residual_meta_conversation_gap(self):
        """
        Documented residual gap: the loose pattern list is not a full re-implementation of the
        router's own classification, so unusual phrasings it doesn't recognize still fall
        through to document_qa. This is acceptable because the fallback only fires when the
        LLM router itself is unavailable, and document_qa is the same conservative default the
        pre-Phase-1 code used for anything it didn't recognize.
        """
        out = _router_fallback_from_regex("could you remind me what I asked you a few messages back")
        assert out.intent == "document_qa"

    def test_empty_text_falls_back_to_clarification(self):
        """Empty text is treated as underspecified (same as pre-Phase-1 _is_underspecified_rag_query)."""
        assert _router_fallback_from_regex("").intent == "clarification"


# ---------------------------------------------------------------------------
# 3. _classify_turn_intent with a fake LLM
# ---------------------------------------------------------------------------

class _FakeRouterLlm:
    def __init__(self, verdict=None, raise_exc=None):
        self.verdict = verdict
        self.raise_exc = raise_exc
        self.invoke_calls = 0

    def with_structured_output(self, cls):
        return self

    def invoke(self, prompt, config=None):
        self.invoke_calls += 1
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.verdict


class _FakeGroqRateLimiter:
    def __init__(self):
        self.wait_calls = 0
        self.success_calls = 0

    def wait_if_needed(self):
        self.wait_calls += 1

    def record_success(self):
        self.success_calls += 1


class TestClassifyTurnIntent:
    def test_happy_path_returns_llm_verdict(self, monkeypatch):
        expected = RouterOutput(intent="meta_conversation", meta_conversation_scope="last_question")
        fake_llm = _FakeRouterLlm(verdict=expected)
        monkeypatch.setattr(rag_service, "_get_router_llm", lambda user_id, provider: fake_llm)
        monkeypatch.setenv("RAG_LLM_ROUTER_ENABLED", "true")

        result = _classify_turn_intent(
            last_user_msg_text="what i ask last question?",
            raw_messages=[HumanMessage(content="what i ask last question?")],
            user_id=1,
            provider="openai",
            has_document=True,
        )
        assert result is expected
        assert fake_llm.invoke_calls == 1

    def test_groq_provider_uses_rate_limiter(self, monkeypatch):
        fake_llm = _FakeRouterLlm(verdict=RouterOutput(intent="document_qa"))
        fake_limiter = _FakeGroqRateLimiter()
        monkeypatch.setattr(rag_service, "_get_router_llm", lambda user_id, provider: fake_llm)
        monkeypatch.setattr(rag_service, "groq_rate_limiter", fake_limiter)
        monkeypatch.setenv("RAG_LLM_ROUTER_ENABLED", "true")

        _classify_turn_intent(
            last_user_msg_text="what is the capital of France",
            raw_messages=[HumanMessage(content="what is the capital of France")],
            user_id=1,
            provider="groq",
            has_document=False,
        )
        assert fake_limiter.wait_calls == 1
        assert fake_limiter.success_calls == 1

    def test_non_groq_provider_never_touches_rate_limiter(self, monkeypatch):
        fake_llm = _FakeRouterLlm(verdict=RouterOutput(intent="document_qa"))
        fake_limiter = _FakeGroqRateLimiter()
        monkeypatch.setattr(rag_service, "_get_router_llm", lambda user_id, provider: fake_llm)
        monkeypatch.setattr(rag_service, "groq_rate_limiter", fake_limiter)
        monkeypatch.setenv("RAG_LLM_ROUTER_ENABLED", "true")

        _classify_turn_intent(
            last_user_msg_text="hello",
            raw_messages=[HumanMessage(content="hello")],
            user_id=1,
            provider="openai",
            has_document=False,
        )
        assert fake_limiter.wait_calls == 0
        assert fake_limiter.success_calls == 0

    def test_llm_exception_falls_back_safely_without_raising(self, monkeypatch):
        fake_llm = _FakeRouterLlm(raise_exc=RuntimeError("boom"))
        monkeypatch.setattr(rag_service, "_get_router_llm", lambda user_id, provider: fake_llm)
        monkeypatch.setenv("RAG_LLM_ROUTER_ENABLED", "true")

        result = _classify_turn_intent(
            last_user_msg_text="create a lesson on gravity",
            raw_messages=[HumanMessage(content="create a lesson on gravity")],
            user_id=1,
            provider="openai",
            has_document=True,
        )
        assert isinstance(result, RouterOutput)
        assert result.intent == "lesson_generation"  # regex fallback recognizes this phrasing
        assert fake_llm.invoke_calls == 1

    def test_kill_switch_never_invokes_llm(self, monkeypatch):
        fake_llm = _FakeRouterLlm(verdict=RouterOutput(intent="meta_conversation"))
        monkeypatch.setattr(rag_service, "_get_router_llm", lambda user_id, provider: fake_llm)
        monkeypatch.setenv("RAG_LLM_ROUTER_ENABLED", "false")

        result = _classify_turn_intent(
            last_user_msg_text="what i ask last question?",
            raw_messages=[HumanMessage(content="what i ask last question?")],
            user_id=1,
            provider="openai",
            has_document=True,
        )
        assert fake_llm.invoke_calls == 0
        # Kill-switch routes straight to the regex fallback, which now recognizes this common
        # meta-conversation phrasing on its own (see TestRouterFallbackFromRegex above).
        assert result.intent == "meta_conversation"

    def test_non_router_output_return_value_falls_back_safely(self, monkeypatch):
        """Defensive: if the structured-output call somehow returns something unexpected."""
        fake_llm = _FakeRouterLlm(verdict={"intent": "document_qa"})  # not a RouterOutput instance
        monkeypatch.setattr(rag_service, "_get_router_llm", lambda user_id, provider: fake_llm)
        monkeypatch.setenv("RAG_LLM_ROUTER_ENABLED", "true")

        result = _classify_turn_intent(
            last_user_msg_text="hi",
            raw_messages=[HumanMessage(content="hi")],
            user_id=1,
            provider="openai",
            has_document=False,
        )
        assert isinstance(result, RouterOutput)


# ---------------------------------------------------------------------------
# 4. Static wiring checks
# ---------------------------------------------------------------------------

class TestStaticWiring:
    def test_chat_build_system_message_uses_router_output_intent(self):
        src = inspect.getsource(rag_service._chat_build_system_message)
        assert "router_output.intent" in src
        assert "meta_conversation" in src

    def test_chat_turn_system_prep_has_meta_conversation_active_field(self):
        src = inspect.getsource(_ChatTurnSystemPrep)
        assert "meta_conversation_active: bool = False" in src

    def test_chat_invoke_llm_with_retry_unpacks_and_uses_meta_conversation_active(self):
        src = inspect.getsource(_chat_invoke_llm_with_retry)
        assert "meta_conversation_active = prep.meta_conversation_active" in src
        assert "or meta_conversation_active:" in src

    def test_tool_router_uses_router_intent_with_defensive_default(self):
        src = inspect.getsource(rag_service._tool_router)
        assert 'state.get("router_intent", "document_qa")' in src
        assert "lesson_generation" in src

    def test_chat_node_caches_router_verdict_on_state(self):
        src = inspect.getsource(rag_service.chat_node)
        assert "router_intent_turn_key" in src
        assert "_classify_turn_intent" in src


# ---------------------------------------------------------------------------
# 5. _find_last_n_real_user_questions / _find_last_real_user_question
# ---------------------------------------------------------------------------

class TestFindLastRealUserQuestions:
    def test_skips_a_chain_of_two_meta_questions_to_find_the_real_one_beneath(self):
        messages = [
            HumanMessage(content="what is the discriminant of a quadratic equation"),
            AIMessage(content="It's b^2 - 4ac."),
            HumanMessage(content="what i ask last question?"),
            AIMessage(content="You asked about the discriminant."),
            HumanMessage(content="what did i ask?"),
        ]
        result = _find_last_real_user_question(messages)
        assert result == "what is the discriminant of a quadratic equation"

    def test_skips_internal_recovery_text(self):
        messages = [
            HumanMessage(content="explain photosynthesis"),
            HumanMessage(content="Your previous response was empty, please re-run the needed tools."),
        ]
        result = _find_last_real_user_question(messages)
        assert result == "explain photosynthesis"

    def test_correct_count_for_n_greater_than_one(self):
        messages = [
            HumanMessage(content="question one"),
            AIMessage(content="answer one"),
            HumanMessage(content="question two"),
            AIMessage(content="answer two"),
            HumanMessage(content="question three"),
        ]
        result = _find_last_n_real_user_questions(messages, n=2)
        # most-recent-first
        assert result == ["question three", "question two"]

    def test_empty_list_when_no_real_question_exists(self):
        messages = [
            HumanMessage(content="what did i ask?"),
            HumanMessage(content="what i ask last question?"),
        ]
        assert _find_last_n_real_user_questions(messages, n=1) == []
        assert _find_last_real_user_question(messages) == ""

    def test_empty_conversation(self):
        assert _find_last_n_real_user_questions([], n=1) == []
        assert _find_last_real_user_question([]) == ""

    def test_looks_like_meta_conversation_text_typo_variant(self):
        assert _looks_like_meta_conversation_text("paste exactly to me what ia sk") is True
        assert _looks_like_meta_conversation_text("what is the capital of France") is False


# ---------------------------------------------------------------------------
# 6. _build_meta_conversation_prefetch_blob
# ---------------------------------------------------------------------------

class TestBuildMetaConversationPrefetchBlob:
    def test_verbatim_text_present_not_paraphrased(self):
        router_output = RouterOutput(intent="meta_conversation", meta_conversation_scope="last_question")
        search_range = [HumanMessage(content="what is zero discriminat just answer main one line")]
        blob = _build_meta_conversation_prefetch_blob(router_output, search_range)
        assert "what is zero discriminat just answer main one line" in blob
        assert "not a document search" in blob.lower() or "not a document question" in blob.lower()

    def test_no_earlier_question_fallback_text(self):
        router_output = RouterOutput(intent="meta_conversation", meta_conversation_scope="last_question")
        blob = _build_meta_conversation_prefetch_blob(router_output, [])
        assert "no earlier" in blob.lower()

    def test_numbered_multi_question_formatting_for_n_greater_than_one(self):
        router_output = RouterOutput(
            intent="meta_conversation",
            meta_conversation_scope="last_n_questions",
            meta_conversation_n=2,
        )
        search_range = [
            HumanMessage(content="first real question"),
            AIMessage(content="answer"),
            HumanMessage(content="second real question"),
        ]
        blob = _build_meta_conversation_prefetch_blob(router_output, search_range)
        assert "1. \"first real question\"" in blob
        assert "2. \"second real question\"" in blob

    def test_single_question_uses_plain_quoted_form_not_numbered(self):
        router_output = RouterOutput(intent="meta_conversation", meta_conversation_scope="last_question")
        search_range = [HumanMessage(content="only question")]
        blob = _build_meta_conversation_prefetch_blob(router_output, search_range)
        assert '"only question"' in blob
        assert "1. " not in blob


# ---------------------------------------------------------------------------
# 7 & 8. _chat_build_system_message branch selection + bug consumption tests
# ---------------------------------------------------------------------------

def _make_state(messages):
    return {
        "messages": messages,
        "lesson_in_progress": False,
        "lesson_finalized": False,
        "last_lesson_text": "",
    }


class TestChatBuildSystemMessageBranchSelection:
    def test_meta_conversation_never_calls_rag_tool_and_injects_exact_question(self, monkeypatch):
        def _explode(*args, **kwargs):
            raise AssertionError("rag_tool must never be invoked for meta_conversation")

        # rag_tool is a pydantic-based StructuredTool that rejects setting attributes not in
        # its schema, so swap the whole module-level reference for a fake with an .invoke().
        monkeypatch.setattr(rag_service, "rag_tool", types.SimpleNamespace(invoke=_explode))

        messages = [
            HumanMessage(content="what is zero discriminat just answer main one line"),
            AIMessage(content="If you have any further questions, feel free to ask!"),
            HumanMessage(content="what i ask last question?"),
        ]
        router_output = RouterOutput(intent="meta_conversation", meta_conversation_scope="last_question")
        prep = _chat_build_system_message(
            _make_state(messages),
            has_document=True,
            thread_id_str="test_thread_meta",
            custom_prompt=None,
            token_pressure_active=False,
            short_mode_active=False,
            router_output=router_output,
        )
        assert prep.meta_conversation_active is True
        assert "what is zero discriminat just answer main one line" in prep.system_message.content
        assert "not a document search" in prep.system_message.content.lower()

    def test_greeting_casual_produces_empty_prefetch_no_tool_call(self, monkeypatch):
        def _explode(*args, **kwargs):
            raise AssertionError("rag_tool must never be invoked for greeting_casual")

        monkeypatch.setattr(rag_service, "rag_tool", types.SimpleNamespace(invoke=_explode))

        messages = [HumanMessage(content="hi there, how are you")]
        router_output = RouterOutput(intent="greeting_casual")
        prep = _chat_build_system_message(
            _make_state(messages),
            has_document=True,
            thread_id_str="test_thread_greet",
            custom_prompt=None,
            token_pressure_active=False,
            short_mode_active=False,
            router_output=router_output,
        )
        assert prep.prefetch_evidence_for_eval == ""
        assert prep.meta_conversation_active is False

    def test_clarification_produces_empty_prefetch(self, monkeypatch):
        def _explode(*args, **kwargs):
            raise AssertionError("rag_tool must never be invoked for clarification")

        monkeypatch.setattr(rag_service, "rag_tool", types.SimpleNamespace(invoke=_explode))

        messages = [HumanMessage(content="explain")]
        router_output = RouterOutput(intent="clarification")
        prep = _chat_build_system_message(
            _make_state(messages),
            has_document=True,
            thread_id_str="test_thread_clar",
            custom_prompt=None,
            token_pressure_active=False,
            short_mode_active=False,
            router_output=router_output,
        )
        assert prep.prefetch_evidence_for_eval == ""

    def test_lesson_generation_calls_lecture_prefetch_same_as_before(self, monkeypatch):
        calls = []

        def _fake_prefetch(thread_id, query):
            calls.append((thread_id, query))
            return "## Prefetched lecture evidence (use this; you may still call tools if needed)\n\nSTUB"

        monkeypatch.setattr(rag_service, "_prefetch_lecture_evidence_for_chat", _fake_prefetch)

        messages = [HumanMessage(content="create a lesson on photosynthesis")]
        router_output = RouterOutput(intent="lesson_generation")
        prep = _chat_build_system_message(
            _make_state(messages),
            has_document=True,
            thread_id_str="test_thread_lesson",
            custom_prompt=None,
            token_pressure_active=False,
            short_mode_active=False,
            router_output=router_output,
        )
        assert prep.is_lesson_creation_turn is True
        assert calls == [("test_thread_lesson", "create a lesson on photosynthesis")]
        assert "Prefetched lecture evidence" in prep.system_message.content

    def test_own_answer_followup_injects_own_prior_answer_verbatim(self):
        long_answer = "The derivative of x^2 is 2x. " * 15  # comfortably over the length floor
        messages = [
            HumanMessage(content="explain the derivative rule"),
            AIMessage(content=long_answer),
            HumanMessage(content="explain why 2x"),
        ]
        router_output = RouterOutput(intent="own_answer_followup")
        prep = _chat_build_system_message(
            _make_state(messages),
            has_document=True,
            thread_id_str="test_thread_ownanswer",
            custom_prompt=None,
            token_pressure_active=False,
            short_mode_active=False,
            router_output=router_output,
        )
        assert prep.own_answer_followup_active is True
        assert "Your own previous answer" in prep.system_message.content
        assert "The derivative of x^2 is 2x." in prep.system_message.content

    def test_document_qa_falls_through_to_generic_rag_tool_prefetch(self, monkeypatch):
        def _fake_invoke(payload):
            return "Some retrieved excerpt text."

        monkeypatch.setattr(rag_service, "rag_tool", types.SimpleNamespace(invoke=_fake_invoke))

        messages = [HumanMessage(content="what is the boiling point of water on this planet")]
        router_output = RouterOutput(intent="document_qa")
        prep = _chat_build_system_message(
            _make_state(messages),
            has_document=True,
            thread_id_str="test_thread_docqa",
            custom_prompt=None,
            token_pressure_active=False,
            short_mode_active=False,
            router_output=router_output,
        )
        assert "Retrieved document excerpts" in prep.system_message.content
        assert "Some retrieved excerpt text." in prep.system_message.content


class TestBugConsumption:
    def test_bug_a_requested_brevity_adds_override_that_wins_over_formatting(self):
        """Bug A: 'just answer main one line' must not be silently overridden by admin's
        LOCKED header/section formatting rules."""
        messages = [HumanMessage(content="what is zero discriminat just answer main one line")]
        router_output = RouterOutput(intent="document_qa", requested_brevity=True)
        prep = _chat_build_system_message(
            _make_state(messages),
            has_document=False,
            thread_id_str=None,
            custom_prompt="LOCKED: at least one bold section header in every message.",
            token_pressure_active=False,
            short_mode_active=False,
            router_output=router_output,
        )
        assert prep.requested_brevity is True
        content = prep.system_message.content
        assert "USER BREVITY OVERRIDE" in content
        assert "do not deflect with a generic closing remark" in content
        # Appended after the admin's custom instructions so it wins via recency.
        assert content.index("USER BREVITY OVERRIDE") > content.index("LOCKED: at least one bold section header")

    def test_bug_a_no_brevity_request_does_not_add_override(self):
        messages = [HumanMessage(content="what is the discriminant")]
        router_output = RouterOutput(intent="document_qa", requested_brevity=False)
        prep = _chat_build_system_message(
            _make_state(messages),
            has_document=False,
            thread_id_str=None,
            custom_prompt=None,
            token_pressure_active=False,
            short_mode_active=False,
            router_output=router_output,
        )
        assert prep.requested_brevity is False
        assert "USER BREVITY OVERRIDE" not in prep.system_message.content

    def test_bug_b_meta_conversation_wiring_present_end_to_end(self, monkeypatch):
        """Bug B: meta-questions must get exact-retrieval evidence AND tool-suppression wiring,
        never a PDF search against the meta-question text itself."""

        def _explode(*args, **kwargs):
            raise AssertionError("rag_tool must never be invoked for meta_conversation")

        monkeypatch.setattr(rag_service, "rag_tool", types.SimpleNamespace(invoke=_explode))

        messages = [
            HumanMessage(content="what is the discriminant formula"),
            AIMessage(content="It's b^2 - 4ac, used to determine the nature of roots."),
            HumanMessage(content="what i ask?"),
        ]
        router_output = RouterOutput(intent="meta_conversation", meta_conversation_scope="last_question")
        prep = _chat_build_system_message(
            _make_state(messages),
            has_document=True,
            thread_id_str="test_thread_bugb",
            custom_prompt=None,
            token_pressure_active=False,
            short_mode_active=False,
            router_output=router_output,
        )
        assert prep.meta_conversation_active is True
        assert "what is the discriminant formula" in prep.system_message.content

        # Suppression wiring: _chat_invoke_llm_with_retry must hard-suppress tools when
        # meta_conversation_active is True (same mechanism as own_answer_followup_active).
        src = inspect.getsource(_chat_invoke_llm_with_retry)
        assert "meta_conversation_active = prep.meta_conversation_active" in src
        assert "or meta_conversation_active:" in src


# ---------------------------------------------------------------------------
# Shared last-human-message helper
# ---------------------------------------------------------------------------

class TestFindLastHumanMessageIndexAndText:
    def test_finds_last_human_message(self):
        messages = [
            HumanMessage(content="first"),
            AIMessage(content="reply"),
            HumanMessage(content="second"),
        ]
        idx, text = _find_last_human_message_index_and_text(messages)
        assert idx == 2
        assert text == "second"

    def test_no_human_message_returns_negative_one_and_empty(self):
        messages = [AIMessage(content="only ai")]
        idx, text = _find_last_human_message_index_and_text(messages)
        assert idx == -1
        assert text == ""

    def test_empty_messages(self):
        idx, text = _find_last_human_message_index_and_text([])
        assert idx == -1
        assert text == ""
