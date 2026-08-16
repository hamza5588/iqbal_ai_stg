"""
Phase 4: general-knowledge consent state machine (pure logic, no DB / no LLM).

Tracks, per RAG thread, the single outstanding "would you like me to answer from general
knowledge?" offer, if any, so that offer can be honored or declined on the *next* turn based on
real persisted state instead of the model just re-reading the system prompt each turn and
deciding on its own whether the user "agreed" earlier in the conversation.

Deliberately per-question and single-use, not a standing thread-level grant: a "yes" to answer
question A from general knowledge should not silently authorize skipping retrieval for an
unrelated question C several turns later. See PHASE4_DESIGN.md section 2 for the full rationale
and the four-state transition table this module implements.

Everything in this module is pure / dependency-free on purpose, so it's unit-testable with plain
function calls - no fake DB session, no LLM mocking. The DB-touching glue (reading/writing
RAGThread.gk_consent_* columns) lives in app/utils/rag_service.py, which is the only caller that
needs an ORM session.
"""
from __future__ import annotations

import re
from typing import NamedTuple, Optional

# --- States ---
GK_CONSENT_NONE = "none"
GK_CONSENT_OFFERED = "offered"
GK_CONSENT_GRANTED = "granted"
GK_CONSENT_DENIED = "denied"

VALID_GK_CONSENT_STATES = frozenset(
    {GK_CONSENT_NONE, GK_CONSENT_OFFERED, GK_CONSENT_GRANTED, GK_CONSENT_DENIED}
)

# --- Events understood by resolve_gk_consent_transition ---
GK_EVENT_OFFER = "offer"                # a "would you like general knowledge?" offer was just made
GK_EVENT_AFFIRMATIVE = "affirmative"    # user said yes, only meaningful while offered
GK_EVENT_NEGATIVE = "negative"          # user said no, only meaningful while offered
GK_EVENT_UNRELATED = "unrelated"        # user's reply wasn't yes/no - offer lapses
GK_EVENT_CONSUME = "consume"            # granted/denied has been read/acted on - reset to none


class GkConsentState(NamedTuple):
    """Snapshot of the consent state machine's state (mirrors RAGThread's 2 mutable columns)."""

    state: str
    question: Optional[str]


def resolve_gk_consent_transition(
    current_state: Optional[str],
    current_question: Optional[str],
    event: str,
    event_text: Optional[str] = None,
) -> GkConsentState:
    """
    Pure state transition function - no I/O, no side effects.

    Transition table (see PHASE4_DESIGN.md section 2):
      * any state + "offer"                          -> offered, question=event_text
        (a new offer always overwrites whatever was previously outstanding - only one
        outstanding offer can exist per thread at a time)
      * offered + "affirmative"                       -> granted (question unchanged)
      * offered + "negative"                           -> denied (question unchanged)
      * offered + "unrelated" (topic changed)          -> none, question cleared (lapsed)
      * granted|denied + "consume"                     -> none, question cleared (single-use)
      * anything else (event not applicable to the current state) -> unchanged (no-op)
    """
    state = current_state if current_state in VALID_GK_CONSENT_STATES else GK_CONSENT_NONE

    if event == GK_EVENT_OFFER:
        return GkConsentState(GK_CONSENT_OFFERED, event_text)

    if state == GK_CONSENT_OFFERED:
        if event == GK_EVENT_AFFIRMATIVE:
            return GkConsentState(GK_CONSENT_GRANTED, current_question)
        if event == GK_EVENT_NEGATIVE:
            return GkConsentState(GK_CONSENT_DENIED, current_question)
        if event == GK_EVENT_UNRELATED:
            return GkConsentState(GK_CONSENT_NONE, None)
        return GkConsentState(state, current_question)

    if state in (GK_CONSENT_GRANTED, GK_CONSENT_DENIED) and event == GK_EVENT_CONSUME:
        return GkConsentState(GK_CONSENT_NONE, None)

    # Event not applicable to the current state (e.g. "affirmative" while state == "none"): no-op.
    return GkConsentState(state, current_question)


def consume_gk_consent(thread_like) -> bool:
    """
    Read the current consent state off `thread_like` (any object exposing
    `gk_consent_state` / `gk_consent_question` attributes - a RAGThread ORM row in production,
    or a plain stand-in object in tests), reset it back to 'none' (single-use consumption), and
    return whether it was 'granted' at the time of the call.

    No-op (returns False, leaves state untouched) if the current state is anything other than
    'granted' or 'denied' - there's nothing to consume from 'none' or 'offered'.
    """
    state = getattr(thread_like, "gk_consent_state", None) or GK_CONSENT_NONE
    if state not in (GK_CONSENT_GRANTED, GK_CONSENT_DENIED):
        return False
    was_granted = state == GK_CONSENT_GRANTED
    thread_like.gk_consent_state = GK_CONSENT_NONE
    thread_like.gk_consent_question = None
    return was_granted


# --- Offer detection (literal string match against the model's own reply) ---
#
# Mirrors the exact fallback-offer wording in DEFAULT_RAG_CHAT_SYSTEM_BODY_WITH_PDF /
# DEFAULT_RAG_CHAT_SYSTEM_BODY_WITH_PDF_LOAD_TEST in rag_service.py. Kept here (not duplicated
# ad hoc at each call site) so the two stay in sync deliberately. Per PHASE4_DESIGN.md open
# question 5 (resolved): router-driven offer-detection is deferred - this literal-match approach
# is the intended mechanism for now.
_GK_OFFER_MARKERS = (
    "would you like me to answer from my own knowledge base",
    "do you want me to answer from my own knowledge base",
)


def response_contains_gk_offer(text: Optional[str]) -> bool:
    """True if the assistant's reply text contains one of the known consent-offer phrasings."""
    t = (text or "").lower()
    return any(marker in t for marker in _GK_OFFER_MARKERS)


# --- Narrow yes/no classifier (deterministic; scoped to be invoked ONLY while state == offered) ---
#
# Matches at the START of the (stripped) message so a "yes"/"no" embedded mid-sentence in an
# unrelated question doesn't false-positive (e.g. "no, I mean on page 4" still starts with "no"
# and correctly counts; "the value is not zero" does NOT start with a negative keyword and
# correctly falls through to None/unrelated). Deliberately conservative: ambiguous or unrelated
# phrasing returns None (treated as "unrelated" -> the offer lapses) rather than guessing.
_AFFIRMATIVE_RE = re.compile(
    r"^\s*(yes|yeah|yep|yup|sure|ok(ay)?|please\s*do|go\s*ahead|do\s*it|of\s*course|"
    r"sounds?\s*good|please\s*answer|answer\s*it|please)\b",
    re.IGNORECASE,
)
_NEGATIVE_RE = re.compile(
    r"^\s*(no|nope|nah|n/?a|don'?t|do\s*not|never\s*mind|nevermind|skip\s*it|not\s*now|no\s*thanks)\b",
    re.IGNORECASE,
)


def classify_yes_no(text: Optional[str]) -> Optional[str]:
    """
    Deterministic yes/no classifier for consent replies. Returns "yes", "no", or None
    (ambiguous / not a yes-no reply at all - caller should treat this as GK_EVENT_UNRELATED).

    Intentionally keyword-only for this pass - an LLM fallback for genuinely ambiguous phrasing
    was scoped in PHASE4_DESIGN.md as a possible future addition but is not wired in here, to
    keep this module's core logic free of any live-call dependency (and therefore fully
    unit-testable with plain function calls).
    """
    t = (text or "").strip()
    if not t:
        return None
    if _NEGATIVE_RE.match(t):
        return "no"
    if _AFFIRMATIVE_RE.match(t):
        return "yes"
    return None
