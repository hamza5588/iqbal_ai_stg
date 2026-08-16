"""
Tests for Phase 4's general-knowledge consent state machine (app/utils/gk_consent.py).

Everything under test here is pure logic - no DB, no LLM, no mocking. Plain function calls and
a plain in-memory stand-in object for the "consume and clear" mutation test (mirroring how a
RAGThread ORM row would be mutated in production, without needing a real one).
"""
import pytest

from app.utils.gk_consent import (
    GK_CONSENT_DENIED,
    GK_CONSENT_GRANTED,
    GK_CONSENT_NONE,
    GK_CONSENT_OFFERED,
    GK_EVENT_AFFIRMATIVE,
    GK_EVENT_CONSUME,
    GK_EVENT_NEGATIVE,
    GK_EVENT_OFFER,
    GK_EVENT_UNRELATED,
    GkConsentState,
    classify_yes_no,
    consume_gk_consent,
    resolve_gk_consent_transition,
    response_contains_gk_offer,
)


# ---------------------------------------------------------------------------
# resolve_gk_consent_transition - transition table
# ---------------------------------------------------------------------------


class TestResolveGkConsentTransition:
    def test_none_plus_offer_becomes_offered_with_question(self):
        result = resolve_gk_consent_transition(GK_CONSENT_NONE, None, GK_EVENT_OFFER, event_text="what is X?")
        assert result == GkConsentState(GK_CONSENT_OFFERED, "what is X?")

    def test_offered_plus_affirmative_becomes_granted_question_unchanged(self):
        result = resolve_gk_consent_transition(GK_CONSENT_OFFERED, "what is X?", GK_EVENT_AFFIRMATIVE)
        assert result == GkConsentState(GK_CONSENT_GRANTED, "what is X?")

    def test_offered_plus_negative_becomes_denied_question_unchanged(self):
        result = resolve_gk_consent_transition(GK_CONSENT_OFFERED, "what is X?", GK_EVENT_NEGATIVE)
        assert result == GkConsentState(GK_CONSENT_DENIED, "what is X?")

    def test_offered_plus_unrelated_lapses_to_none(self):
        result = resolve_gk_consent_transition(GK_CONSENT_OFFERED, "what is X?", GK_EVENT_UNRELATED)
        assert result == GkConsentState(GK_CONSENT_NONE, None)

    def test_granted_plus_consume_resets_to_none(self):
        result = resolve_gk_consent_transition(GK_CONSENT_GRANTED, "what is X?", GK_EVENT_CONSUME)
        assert result == GkConsentState(GK_CONSENT_NONE, None)

    def test_denied_plus_consume_resets_to_none(self):
        result = resolve_gk_consent_transition(GK_CONSENT_DENIED, "what is X?", GK_EVENT_CONSUME)
        assert result == GkConsentState(GK_CONSENT_NONE, None)

    def test_a_new_offer_always_overwrites_regardless_of_current_state(self):
        for current_state in (GK_CONSENT_NONE, GK_CONSENT_OFFERED, GK_CONSENT_GRANTED, GK_CONSENT_DENIED):
            result = resolve_gk_consent_transition(
                current_state, "old question", GK_EVENT_OFFER, event_text="new question"
            )
            assert result == GkConsentState(GK_CONSENT_OFFERED, "new question"), current_state

    @pytest.mark.parametrize("event", [GK_EVENT_AFFIRMATIVE, GK_EVENT_NEGATIVE, GK_EVENT_UNRELATED])
    def test_affirmative_negative_unrelated_are_noops_outside_offered(self, event):
        # These events only make sense while state == offered; anywhere else, no-op.
        for state in (GK_CONSENT_NONE, GK_CONSENT_GRANTED, GK_CONSENT_DENIED):
            result = resolve_gk_consent_transition(state, "q", event)
            assert result == GkConsentState(state, "q")

    def test_consume_is_a_noop_outside_granted_denied(self):
        for state in (GK_CONSENT_NONE, GK_CONSENT_OFFERED):
            result = resolve_gk_consent_transition(state, "q", GK_EVENT_CONSUME)
            assert result == GkConsentState(state, "q")

    def test_invalid_current_state_defaults_to_none_before_processing(self):
        result = resolve_gk_consent_transition("garbage-state", "q", GK_EVENT_AFFIRMATIVE)
        # "garbage-state" isn't a valid state, so it's treated as GK_CONSENT_NONE, and
        # affirmative is a no-op outside "offered".
        assert result == GkConsentState(GK_CONSENT_NONE, "q")

    def test_none_current_state_input_defaults_to_none(self):
        result = resolve_gk_consent_transition(None, None, GK_EVENT_UNRELATED)
        assert result == GkConsentState(GK_CONSENT_NONE, None)


# ---------------------------------------------------------------------------
# consume_gk_consent - mutation on a duck-typed stand-in object
# ---------------------------------------------------------------------------


class _FakeThreadRow:
    """Stands in for a RAGThread ORM row - same two attributes, no DB involved."""

    def __init__(self, gk_consent_state, gk_consent_question=None):
        self.gk_consent_state = gk_consent_state
        self.gk_consent_question = gk_consent_question


class TestConsumeGkConsent:
    def test_granted_returns_true_and_resets_state(self):
        thread = _FakeThreadRow(GK_CONSENT_GRANTED, "what is X?")
        was_granted = consume_gk_consent(thread)
        assert was_granted is True
        assert thread.gk_consent_state == GK_CONSENT_NONE
        assert thread.gk_consent_question is None

    def test_denied_returns_false_and_resets_state(self):
        thread = _FakeThreadRow(GK_CONSENT_DENIED, "what is X?")
        was_granted = consume_gk_consent(thread)
        assert was_granted is False
        assert thread.gk_consent_state == GK_CONSENT_NONE
        assert thread.gk_consent_question is None

    def test_none_state_is_a_noop(self):
        thread = _FakeThreadRow(GK_CONSENT_NONE, None)
        was_granted = consume_gk_consent(thread)
        assert was_granted is False
        assert thread.gk_consent_state == GK_CONSENT_NONE

    def test_offered_state_is_a_noop_not_consumed_early(self):
        thread = _FakeThreadRow(GK_CONSENT_OFFERED, "what is X?")
        was_granted = consume_gk_consent(thread)
        assert was_granted is False
        # Must NOT clear an outstanding offer just because consume was called prematurely.
        assert thread.gk_consent_state == GK_CONSENT_OFFERED
        assert thread.gk_consent_question == "what is X?"

    def test_missing_state_attribute_defaults_to_none_behavior(self):
        class _Empty:
            pass

        was_granted = consume_gk_consent(_Empty())
        assert was_granted is False


# ---------------------------------------------------------------------------
# response_contains_gk_offer
# ---------------------------------------------------------------------------


class TestResponseContainsGkOffer:
    def test_matches_not_present_in_document_phrasing(self):
        text = "The answer is not present in the document. Would you like me to answer from my own knowledge base?"
        assert response_contains_gk_offer(text) is True

    def test_matches_irrelevant_question_phrasing(self):
        text = "Irrelevant question. Do you want me to answer from my own knowledge base?"
        assert response_contains_gk_offer(text) is True

    def test_case_insensitive(self):
        text = "IRRELEVANT QUESTION. DO YOU WANT ME TO ANSWER FROM MY OWN KNOWLEDGE BASE?"
        assert response_contains_gk_offer(text) is True

    def test_no_match_on_unrelated_reply(self):
        text = "The discriminant is b^2 - 4ac, and here it equals zero."
        assert response_contains_gk_offer(text) is False

    @pytest.mark.parametrize("text", [None, ""])
    def test_none_and_empty_are_falsy(self, text):
        assert response_contains_gk_offer(text) is False


# ---------------------------------------------------------------------------
# classify_yes_no
# ---------------------------------------------------------------------------


class TestClassifyYesNo:
    @pytest.mark.parametrize("text", [
        "yes", "Yes please", "yeah", "yep", "yup", "sure", "ok", "okay",
        "go ahead", "please do", "do it", "of course", "sounds good",
        "please answer", "answer it", "please",
    ])
    def test_affirmative_phrasings(self, text):
        assert classify_yes_no(text) == "yes"

    @pytest.mark.parametrize("text", [
        "no", "No thanks", "nope", "nah", "don't", "do not", "never mind",
        "nevermind", "skip it", "not now",
    ])
    def test_negative_phrasings(self, text):
        assert classify_yes_no(text) == "no"

    @pytest.mark.parametrize("text", [
        "what is the capital of France",
        "the value is not zero",  # contains "not" but doesn't start with a negative keyword
        "know the answer already",  # must not false-positive-match "no" inside "know"
        "",
        None,
        "   ",
    ])
    def test_ambiguous_or_unrelated_returns_none(self, text):
        assert classify_yes_no(text) is None

    def test_leading_whitespace_is_tolerated(self):
        assert classify_yes_no("   yes, go ahead") == "yes"
        assert classify_yes_no("   no, don't") == "no"
