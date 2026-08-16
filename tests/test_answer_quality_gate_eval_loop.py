"""
Direct unit tests for _answer_quality_gate_eval_and_maybe_regenerate's internals: the
eval/regen loop, the router_intent-based gate, the heuristic pre-filter integration, and the
meta_conversation_active/greeting_casual short-circuits.

There was no pre-existing direct test coverage for this loop before Phase 2 (the old
_lecture_failsafe_eval_and_maybe_regenerate only had indirect coverage via static prompt-
content checks in test_lecture_generation_fixes_static.py) - this file is net-new coverage,
not a pre-existing regression baseline. The "lesson_generation still works exactly as
before" tests below are the regression proof that the generalized gate preserves the
original lecture-only eval/regen mechanics.

Requires the project's real dependencies (pip install -r requirements.txt).
"""
import pytest

rag_service = pytest.importorskip("app.utils.rag_service")

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage  # noqa: E402


_FILLER_RESPONSE = (
    "If you have any further questions about quadratic equations or any related topics, "
    "feel free to ask!"
)
_SUBSTANTIVE_RESPONSE = "The discriminant is b^2 - 4ac; zero means one repeated real root (Page 41)."
_EVIDENCE = "## Prefetched document evidence\n[Evidence 1 | Page 41] score=1.0 discriminant..."


class _FakeVerdict:
    def __init__(self, passed, is_underspecified_clarification=False, reasoning="", feedback_for_regeneration=""):
        self.passed = passed
        self.is_underspecified_clarification = is_underspecified_clarification
        self.reasoning = reasoning
        self.feedback_for_regeneration = feedback_for_regeneration


class _FakeEvalLlm:
    """Stub for user_llm.with_structured_output(AnswerQualityEvalResult)."""

    def __init__(self, verdicts):
        self._verdicts = list(verdicts)
        self.invoke_calls = []

    def invoke(self, prompt, config=None):
        self.invoke_calls.append(prompt)
        if not self._verdicts:
            raise AssertionError("eval_llm.invoke called more times than scripted verdicts")
        return self._verdicts.pop(0)


class _FakeUserLlm:
    """Stub for user_llm: with_structured_output(...) returns the eval stub; invoke(...) is
    the tool-free regeneration call."""

    def __init__(self, verdicts, regen_texts=None):
        self._eval_llm = _FakeEvalLlm(verdicts)
        self._regen_texts = list(regen_texts or [])
        self.regen_calls = []
        self.with_structured_output_calls = 0

    def with_structured_output(self, model):
        self.with_structured_output_calls += 1
        return self._eval_llm

    def invoke(self, messages, config=None):
        self.regen_calls.append(messages)
        if not self._regen_texts:
            raise AssertionError("user_llm.invoke (regen) called more times than scripted regen texts")
        text = self._regen_texts.pop(0)
        return AIMessage(content=text)


def _call_gate(
    user_llm,
    response_content,
    router_intent,
    *,
    is_lesson_creation_turn=False,
    meta_conversation_active=False,
    prefetch_evidence_for_eval=_EVIDENCE,
    has_document=True,
    short_mode_active=False,
    token_pressure_active=False,
    last_user_msg_text="what is zero discriminat just answer main one line",
):
    system_message = SystemMessage(content="You are a helpful assistant.")
    conversation_messages = [HumanMessage(content=last_user_msg_text)]
    response = AIMessage(content=response_content)
    steps = []
    return rag_service._answer_quality_gate_eval_and_maybe_regenerate(
        user_llm=user_llm,
        system_message=system_message,
        conversation_messages=conversation_messages,
        response=response,
        response_content=response_content,
        last_user_msg_text=last_user_msg_text,
        prefetch_evidence_for_eval=prefetch_evidence_for_eval,
        has_document=has_document,
        is_lesson_creation_turn=is_lesson_creation_turn,
        router_intent=router_intent,
        meta_conversation_active=meta_conversation_active,
        user_id=1,
        provider="openai",
        config={},
        max_input_tokens=4200,
        short_mode_active=short_mode_active,
        token_pressure_active=token_pressure_active,
        _mark_step=lambda *a, **k: steps.append(a),
    )


@pytest.fixture(autouse=True)
def _gate_enabled(monkeypatch):
    """The gate defaults to enabled, but pin it explicitly so these tests don't depend on
    ambient environment variables set elsewhere."""
    monkeypatch.delenv("RAG_LECTURE_FAILSAFE_ENABLED", raising=False)
    monkeypatch.setenv("RAG_ANSWER_QUALITY_GATE_ENABLED", "true")
    monkeypatch.delenv("LOAD_TEST_MODE", raising=False)
    monkeypatch.setattr(rag_service, "_LOAD_TEST_MODE", False)


# --- Regression: lesson_generation keeps the original unconditional-eval behavior -------

class TestLessonGenerationRegression:
    def test_passes_on_first_eval_router_intent_path(self):
        user_llm = _FakeUserLlm(verdicts=[_FakeVerdict(passed=True)])
        content, response = _call_gate(
            user_llm, _SUBSTANTIVE_RESPONSE, router_intent="lesson_generation",
        )
        assert user_llm._eval_llm.invoke_calls.__len__() == 1
        assert user_llm.regen_calls == []
        assert content == _SUBSTANTIVE_RESPONSE
        assert response.content == _SUBSTANTIVE_RESPONSE

    def test_passes_on_first_eval_legacy_flag_path(self):
        """is_lesson_creation_turn=True with router_intent=None must behave identically to
        the router_intent="lesson_generation" path - proves the legacy flag still works."""
        user_llm = _FakeUserLlm(verdicts=[_FakeVerdict(passed=True)])
        content, response = _call_gate(
            user_llm, _SUBSTANTIVE_RESPONSE, router_intent=None, is_lesson_creation_turn=True,
        )
        assert len(user_llm._eval_llm.invoke_calls) == 1
        assert user_llm.regen_calls == []
        assert content == _SUBSTANTIVE_RESPONSE

    def test_lesson_generation_ignores_the_heuristic_even_for_substantive_filler_looking_text(self):
        """Central regression: lesson mode must NOT apply the heuristic pre-filter. A
        response containing a filler phrase would normally be a candidate to skip the
        eval for non-lesson intents if it looked substantive, but lesson mode always
        calls the eval regardless."""
        user_llm = _FakeUserLlm(verdicts=[_FakeVerdict(passed=True)])
        content, response = _call_gate(
            user_llm, _SUBSTANTIVE_RESPONSE, router_intent="lesson_generation",
        )
        assert len(user_llm._eval_llm.invoke_calls) == 1

    def test_regenerates_until_pass(self):
        user_llm = _FakeUserLlm(
            verdicts=[
                _FakeVerdict(passed=False, feedback_for_regeneration="add citations"),
                _FakeVerdict(passed=True),
            ],
            regen_texts=["Revised lecture body with citations (Page 41)."],
        )
        content, response = _call_gate(
            user_llm, "Original lecture body with no citations.", router_intent="lesson_generation",
        )
        assert len(user_llm._eval_llm.invoke_calls) == 2
        assert len(user_llm.regen_calls) == 1
        # The regen prompt must carry the required-fixes feedback.
        regen_messages = user_llm.regen_calls[0]
        human_msgs = [m for m in regen_messages if isinstance(m, HumanMessage)]
        assert any("Required fixes:\nadd citations" in m.content for m in human_msgs)
        assert any("[Automated quality verification]" in m.content for m in human_msgs)
        assert content == "Revised lecture body with citations (Page 41)."
        assert response.content == "Revised lecture body with citations (Page 41)."

    def test_hits_max_rounds_keeps_last_draft_without_crashing(self):
        always_fail = [_FakeVerdict(passed=False, reasoning="still ungrounded") for _ in range(4)]
        user_llm = _FakeUserLlm(
            verdicts=always_fail,
            regen_texts=["draft v2", "draft v3", "draft v4"],
        )
        content, response = _call_gate(
            user_llm, "draft v1", router_intent="lesson_generation",
        )
        # 4 rounds default: 4 eval calls, 3 regen calls (no regen after the last failed eval).
        assert len(user_llm._eval_llm.invoke_calls) == 4
        assert len(user_llm.regen_calls) == 3
        assert content == "draft v4"

    def test_is_underspecified_clarification_treated_as_pass(self):
        user_llm = _FakeUserLlm(verdicts=[_FakeVerdict(passed=False, is_underspecified_clarification=True)])
        content, response = _call_gate(
            user_llm, "Could you clarify which chapter you mean?", router_intent="lesson_generation",
        )
        assert len(user_llm._eval_llm.invoke_calls) == 1
        assert user_llm.regen_calls == []
        assert content == "Could you clarify which chapter you mean?"


# --- document_qa / general non-lesson intents: heuristic pre-filter gates the eval ------

class TestHeuristicGatedIntents:
    def test_document_qa_skips_eval_when_heuristic_says_no(self):
        user_llm = _FakeUserLlm(verdicts=[])
        content, response = _call_gate(
            user_llm, _SUBSTANTIVE_RESPONSE, router_intent="document_qa",
        )
        assert user_llm.with_structured_output_calls == 0
        assert content == _SUBSTANTIVE_RESPONSE

    def test_document_qa_runs_eval_when_heuristic_escalates(self):
        user_llm = _FakeUserLlm(
            verdicts=[
                _FakeVerdict(passed=False, feedback_for_regeneration="cite the page"),
                _FakeVerdict(passed=True),
            ],
            regen_texts=["Grounded answer (Page 41)."],
        )
        content, response = _call_gate(
            user_llm, _FILLER_RESPONSE, router_intent="document_qa",
        )
        assert user_llm.with_structured_output_calls == 1
        assert len(user_llm._eval_llm.invoke_calls) == 2
        assert len(user_llm.regen_calls) == 1
        assert content == "Grounded answer (Page 41)."

    def test_general_knowledge_qa_with_no_evidence_never_escalates(self):
        """general_knowledge_qa turns don't run the document rag_tool prefetch, so
        prefetch_evidence_for_eval is empty by design - the heuristic must never escalate
        regardless of how filler-like the response looks."""
        user_llm = _FakeUserLlm(verdicts=[])
        content, response = _call_gate(
            user_llm, _FILLER_RESPONSE, router_intent="general_knowledge_qa",
            prefetch_evidence_for_eval="",
        )
        assert user_llm.with_structured_output_calls == 0
        assert content == _FILLER_RESPONSE

    def test_lesson_qa_heuristic_gated_same_as_document_qa(self):
        user_llm = _FakeUserLlm(verdicts=[])
        content, response = _call_gate(
            user_llm, _SUBSTANTIVE_RESPONSE, router_intent="lesson_qa",
        )
        assert user_llm.with_structured_output_calls == 0


# --- own_answer_followup: the intent added specifically for the "explain why 2x" bug ----

class TestOwnAnswerFollowup:
    def test_escalates_on_filler_over_injected_prior_answer(self):
        """Regression test for the bug that got own_answer_followup added to the default
        qualifying-intent set: the model falls back to lesson-saved-style filler instead of
        using its own prior answer (injected as prefetch_evidence_for_eval, mirroring the
        real prefetch_blob shape built in _chat_build_system_message)."""
        injected_prior_answer = (
            "## Your own previous answer in this conversation (the user is asking you to "
            "explain or justify something from it)\n\nWe used 2x because the pool's width "
            "shrinks by x on each side, so the total reduction is 2x..."
        )
        user_llm = _FakeUserLlm(
            verdicts=[
                _FakeVerdict(passed=False, feedback_for_regeneration="answer the follow-up directly"),
                _FakeVerdict(passed=True),
            ],
            regen_texts=["You used 2x because the width shrinks by x on each side (Page 41)."],
        )
        content, response = _call_gate(
            user_llm,
            "Lesson finalized and saved. You can download it now.",
            router_intent="own_answer_followup",
            prefetch_evidence_for_eval=injected_prior_answer,
            last_user_msg_text="explain why 2x, how did you get 2x and not x",
        )
        assert user_llm.with_structured_output_calls == 1
        assert len(user_llm._eval_llm.invoke_calls) == 2
        assert content == "You used 2x because the width shrinks by x on each side (Page 41)."

    def test_skips_eval_when_heuristic_says_no(self):
        injected_prior_answer = "## Your own previous answer...\n\nWe used 2x because..."
        user_llm = _FakeUserLlm(verdicts=[])
        content, response = _call_gate(
            user_llm,
            "You used 2x because the width shrinks by x on each side.",
            router_intent="own_answer_followup",
            prefetch_evidence_for_eval=injected_prior_answer,
        )
        assert user_llm.with_structured_output_calls == 0


# --- Full no-ops: intents/flags that must never touch user_llm at all -------------------

class TestFullNoops:
    def test_greeting_casual_intent_is_a_full_noop(self):
        user_llm = _FakeUserLlm(verdicts=[])
        content, response = _call_gate(
            user_llm, "Hi there! How can I help you today?", router_intent="greeting_casual",
        )
        assert content == "Hi there! How can I help you today?"
        assert user_llm.with_structured_output_calls == 0
        assert user_llm.regen_calls == []

    def test_clarification_intent_is_a_full_noop(self):
        user_llm = _FakeUserLlm(verdicts=[])
        content, response = _call_gate(
            user_llm, "Could you clarify what you mean?", router_intent="clarification",
        )
        assert user_llm.with_structured_output_calls == 0

    def test_lesson_save_intent_is_a_full_noop(self):
        """lesson_save replies are backend-forced deterministic strings; nothing to grade."""
        user_llm = _FakeUserLlm(verdicts=[])
        content, response = _call_gate(
            user_llm, "Lesson finalized and saved. You can download it now.", router_intent="lesson_save",
        )
        assert user_llm.with_structured_output_calls == 0

    def test_lesson_modification_intent_is_a_full_noop_by_default(self):
        """Excluded from the default qualifying set per PHASE2_DESIGN.md section 6a."""
        user_llm = _FakeUserLlm(verdicts=[])
        content, response = _call_gate(
            user_llm, _FILLER_RESPONSE, router_intent="lesson_modification",
        )
        assert user_llm.with_structured_output_calls == 0

    def test_meta_conversation_active_is_a_full_noop_even_with_qualifying_intent(self):
        """meta_conversation_active must short-circuit before the intent check even fires,
        for any intent value - it's a separate signal, not derived from router_intent."""
        user_llm = _FakeUserLlm(verdicts=[])
        content, response = _call_gate(
            user_llm, _FILLER_RESPONSE, router_intent="document_qa", meta_conversation_active=True,
        )
        assert user_llm.with_structured_output_calls == 0
        assert content == _FILLER_RESPONSE

    def test_short_mode_active_is_a_full_noop_even_for_lesson_generation(self):
        user_llm = _FakeUserLlm(verdicts=[])
        content, response = _call_gate(
            user_llm, _SUBSTANTIVE_RESPONSE, router_intent="lesson_generation", short_mode_active=True,
        )
        assert user_llm.with_structured_output_calls == 0

    def test_gate_disabled_env_var_is_a_full_noop(self, monkeypatch):
        monkeypatch.setenv("RAG_ANSWER_QUALITY_GATE_ENABLED", "false")
        user_llm = _FakeUserLlm(verdicts=[])
        content, response = _call_gate(
            user_llm, _SUBSTANTIVE_RESPONSE, router_intent="lesson_generation",
        )
        assert user_llm.with_structured_output_calls == 0

    def test_deprecated_old_env_var_still_honored_when_new_one_unset(self, monkeypatch):
        """An ops config that pins the OLD var to false must not be silently overridden by
        the new default-true behavior."""
        monkeypatch.delenv("RAG_ANSWER_QUALITY_GATE_ENABLED", raising=False)
        monkeypatch.setenv("RAG_LECTURE_FAILSAFE_ENABLED", "false")
        user_llm = _FakeUserLlm(verdicts=[])
        content, response = _call_gate(
            user_llm, _SUBSTANTIVE_RESPONSE, router_intent="lesson_generation",
        )
        assert user_llm.with_structured_output_calls == 0
