"""
Tests for the cheap heuristic pre-filter that decides whether a chat turn's response is
even worth paying for the expensive answer-quality LLM eval.

Live bug (staging): user asked "what is zero discriminat just answer main one line" against
an uploaded PDF. Mandatory prefetch (rag_tool.invoke) returned strong evidence (score=1.0,
page=41). The model replied with pure filler: "If you have any further questions about
quadratic equations or any related topics, feel free to ask!" - completely ignoring the
evidence it had access to.

Design: PHASE2_DESIGN.md. The old lecture-only failsafe (_lecture_failsafe_eval_and_maybe_
regenerate) only ran for lesson-generation turns and was disabled by default, since running a
full structured-output LLM eval on every qualifying turn would double latency+cost. This
heuristic (_looks_like_filler_non_answer / _quality_gate_should_escalate) is a pure regex/
length check, no LLM call, that only escalates to the LLM eval when BOTH (a) real evidence
was available this turn and (b) the response looks like filler - which is what makes it safe
to default the generalized gate to enabled.

Requires the project's real dependencies (pip install -r requirements.txt).
"""
import pytest

rag_service = pytest.importorskip("app.utils.rag_service")


class TestLooksLikeFillerNonAnswer:
    def test_flags_the_live_zero_discriminant_filler_reply(self):
        text = (
            "If you have any further questions about quadratic equations or any related "
            "topics, feel free to ask!"
        )
        assert rag_service._looks_like_filler_non_answer(text) is True

    @pytest.mark.parametrize("text", [
        "Let me know if you need anything else!",
        "Don't hesitate to ask if you have more questions.",
        "Happy to help further - just ask!",
        "Anything else I can help with?",
        "I'm here to help if you need more.",
        "Hope this helps!",
        "Please reach out if you have other questions.",
    ])
    def test_flags_other_filler_phrasings(self, text):
        assert rag_service._looks_like_filler_non_answer(text) is True

    def test_flags_lesson_saved_confirmation_leaking_into_a_followup(self):
        """The real own_answer_followup staging bug (see test_own_answer_followup.py): the
        model answers a genuine "explain your own prior answer" follow-up with the lesson-
        save confirmation instead. Tools are hard-suppressed on these turns, so this text
        can never be a legitimate response to a follow-up question."""
        text = "Lesson finalized and saved. You can download it now."
        assert rag_service._looks_like_filler_non_answer(text) is True

    def test_does_not_flag_not_present_in_document_fallback(self):
        text = (
            "The answer is not present in the document. Would you like me to answer from "
            "my own knowledge base?"
        )
        assert rag_service._looks_like_filler_non_answer(text) is False

    def test_does_not_flag_irrelevant_question_fallback(self):
        text = "Irrelevant question. Do you want me to answer from my own knowledge base?"
        assert rag_service._looks_like_filler_non_answer(text) is False

    def test_does_not_flag_long_answer_with_citation_that_ends_politely(self):
        text = (
            "The discriminant of a quadratic equation ax^2 + bx + c = 0 is b^2 - 4ac. "
            "When the discriminant equals zero, the equation has exactly one repeated real "
            "root, since the two solutions given by the quadratic formula collapse to the "
            "same value. This is explained in detail with worked examples (Page 41). "
            "Let me know if you'd like more detail on any part of the derivation."
        )
        assert len(text) > 350
        assert rag_service._looks_like_filler_non_answer(text) is False

    def test_does_not_flag_substantive_short_answer_without_filler_phrase(self):
        text = "The discriminant is b^2 - 4ac; zero means one repeated real root (Page 41)."
        assert rag_service._looks_like_filler_non_answer(text) is False

    def test_empty_response_not_flagged(self):
        assert rag_service._looks_like_filler_non_answer("") is False
        assert rag_service._looks_like_filler_non_answer(None) is False

    def test_short_filler_with_source_marker_not_flagged(self):
        """A citation marker anywhere in the reply means it isn't pure filler, even if a
        filler phrase also appears and the whole thing is short."""
        text = "See (Source: Chapter 3) for details. Let me know if you have any other questions."
        assert rag_service._looks_like_filler_non_answer(text) is False


class TestQualityGateShouldEscalate:
    _FILLER = (
        "If you have any further questions about quadratic equations or any related "
        "topics, feel free to ask!"
    )
    _SUBSTANTIVE = "The discriminant is b^2 - 4ac; zero means one repeated real root (Page 41)."

    def test_escalates_on_filler_with_evidence_present(self):
        evidence = "## Prefetched document evidence\n[Evidence 1 | Page 41] score=1.0 ..."
        assert rag_service._quality_gate_should_escalate(self._FILLER, evidence) is True

    def test_does_not_escalate_when_no_evidence_even_if_filler(self):
        assert rag_service._quality_gate_should_escalate(self._FILLER, "") is False
        assert rag_service._quality_gate_should_escalate(self._FILLER, "   ") is False
        assert rag_service._quality_gate_should_escalate(self._FILLER, None) is False

    def test_does_not_escalate_on_substantive_response_even_with_evidence(self):
        evidence = "## Prefetched document evidence\n[Evidence 1 | Page 41] score=1.0 ..."
        assert rag_service._quality_gate_should_escalate(self._SUBSTANTIVE, evidence) is False

    def test_does_not_escalate_on_legitimate_fallback_even_with_evidence(self):
        """Evidence was returned (e.g. a weak/irrelevant match) but the model correctly
        said so - must not be treated as a failure to escalate on."""
        evidence = "## Retrieved document excerpts ...\nsome weakly related text"
        fallback = (
            "The answer is not present in the document. Would you like me to answer from "
            "my own knowledge base?"
        )
        assert rag_service._quality_gate_should_escalate(fallback, evidence) is False
