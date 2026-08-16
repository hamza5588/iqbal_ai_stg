"""
Tests for deterministic handling of "explain X from your own previous answer" follow-ups.

Live bug: after generating and finalizing a lesson plan (which had already derived "2x" from
a pool word-problem), asking "explain why 2x, how did you get 2x and not x" got rag_tool
called with the full question as query, weak/irrelevant evidence came back, and the model
fell back to a generic "the lesson has been saved" remark - twice, even after two rounds of
system-prompt-only instructions telling it to answer from its own prior reasoning instead.
Confirmed live via server logs both times that finalize_lesson_tool was NOT re-called and
rag_tool genuinely was attempted, ruling out the earlier message-reordering bug.

Fix: _is_own_answer_followup_request() detects this pattern, and _chat_build_system_message
deterministically injects the model's own last substantive answer into the system prompt as
grounding context, instead of relying purely on a system-prompt instruction the model wasn't
reliably following.

Requires the project's real dependencies (pip install -r requirements.txt).
"""
import re

import pytest

rag_service = pytest.importorskip("app.utils.rag_service")


class TestIsOwnAnswerFollowupRequest:
    @pytest.mark.parametrize("text", [
        "explain why 2x, how did you get 2x and not x",
        "how did you get that number",
        "why did you use that formula",
        "why not x",
        "where did that come from",
        "What does that variable mean?",
    ])
    def test_detects_own_answer_followups(self, text):
        assert rag_service._is_own_answer_followup_request(text) is True

    @pytest.mark.parametrize("text", [
        "what is a quadratic equation?",
        "create a lecture on quadratic equations",
        "please save this as a lesson",
        "explain quadratic equations",  # asks about the topic, not "your own answer"
        "",
        None,
    ])
    def test_does_not_flag_normal_questions(self, text):
        assert rag_service._is_own_answer_followup_request(text) is False


def test_own_answer_prefetch_wired_into_system_message_builder():
    """Static wiring check: the router's own_answer_followup verdict must actually be consulted
    before falling through to the generic rag_tool prefetch path. (Phase 1 of the routing rework
    replaced the direct _is_own_answer_followup_request() regex call with a check against the
    LLM router's classification - _is_own_answer_followup_request itself lives on now only as
    the regex fallback used when the router is disabled/fails, see _router_fallback_from_regex.)"""
    import inspect
    src = inspect.getsource(rag_service._chat_build_system_message)
    assert 'router_output.intent == "own_answer_followup"' in src
    assert "Your own previous answer in this conversation" in src
    # Must search strictly before the current human message, not include it.
    assert "raw_messages[:last_human_idx_pf]" in src
    assert "_find_last_substantive_ai_answer(search_range)" in src
    assert "own_answer_followup_active = True" in src


def test_own_answer_followup_hard_suppresses_tool_binding():
    """Static wiring check: a prompt-only "don't call tools" instruction was tried first and
    the model still occasionally re-invoked finalize_lesson_tool anyway when the recent
    conversation history was dominated by save-confirmation messages. own_answer_followup_active
    must therefore also force the tools-unbound LLM (user_llm, not user_llm_with_tools) so the
    model physically cannot call any tool on this turn, the same hard-suppression mechanism
    already used for token_pressure_active."""
    import inspect
    assert "own_answer_followup_active: bool = False" in inspect.getsource(rag_service._ChatTurnSystemPrep)
    src = inspect.getsource(rag_service._chat_invoke_llm_with_retry)
    assert "own_answer_followup_active = prep.own_answer_followup_active" in src
    # The suppression condition now also includes meta_conversation_active (Phase 1 of the
    # routing rework) - assert own_answer_followup_active is still part of it, not that it's
    # still the last condition before the colon.
    assert "or own_answer_followup_active" in src
    assert re.search(r"own_answer_followup_active[^\n]*:\s*\n\s*response = user_llm\.invoke", src)


class TestFindLastSubstantiveAiAnswer:
    """Regression coverage for the bug where the search picked up Step 2's short
    'Lesson finalized and saved. You can download it now.' confirmation instead of Step 1's
    actual lesson content, because it was chronologically more recent and the search had no
    length filter."""

    def _ai(self, content, tool_calls=None):
        from langchain_core.messages import AIMessage
        return AIMessage(content=content, tool_calls=tool_calls or [])

    def _human(self, content):
        from langchain_core.messages import HumanMessage
        return HumanMessage(content=content)

    def test_skips_short_confirmation_and_finds_real_answer(self):
        real_answer = "A" * 250 + " this is the actual lesson content about quadratic equations."
        messages = [
            self._human("please make a lesson plan"),
            self._ai(real_answer),
            self._human("please save this as a lesson"),
            self._ai("Lesson finalized and saved. You can download it now."),
        ]
        assert rag_service._find_last_substantive_ai_answer(messages) == real_answer

    def test_returns_empty_string_when_no_substantive_answer_exists(self):
        messages = [
            self._human("hi"),
            self._ai("Lesson finalized and saved. You can download it now."),
            self._ai("Sure, one moment."),
        ]
        assert rag_service._find_last_substantive_ai_answer(messages) == ""

    def test_ignores_ai_messages_with_tool_calls(self):
        real_answer = "B" * 250 + " full explanation text."
        messages = [
            self._ai(real_answer),
            self._ai("", tool_calls=[{"name": "rag_tool", "args": {}, "id": "1"}]),
        ]
        assert rag_service._find_last_substantive_ai_answer(messages) == real_answer

    def test_empty_message_list_returns_empty_string(self):
        assert rag_service._find_last_substantive_ai_answer([]) == ""
