"""Rephrase a diagnostic MCQ for a student who is stuck on the wording -
without revealing, hinting at, or eliminating any option.

DIL feedback: "if a student doesn't understand a question, there should be
a rephrase/clarify option that does not reveal the answer."

The clarification is student-agnostic (it is just clearer wording), so it
is cached per question on the assessment's description meta JSON - the same
place weakness_analyzer caches - so repeated clicks and a whole class do
not each trigger a Groq call.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from app.models.lms_models import Assessment, Question
from app.services.lms.assessment_service import get_assessment
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError
from app.services.lms.mcq_utils import options_from_json, pick_display_fields
from app.utils.db import get_db

logger = logging.getLogger(__name__)

_MAX_CHARS = 600

_CLARIFY_PROMPT = """A student on a diagnostic test does not understand how this multiple-choice
question is worded. Rewrite the QUESTION so it is easier to understand.

STRICT RULES - breaking any of these makes the test invalid:
- Do NOT give the answer, and do NOT say or hint which option is right or wrong.
- Do NOT rank, compare, eliminate, or point to any option.
- Do NOT solve the problem or show any step of the working.
- Only: restate what is being asked in plain words, explain any hard term, and
  spell out any notation (e.g. what "P(B)" or "x^2" means).
- Keep the same difficulty. 2-3 short sentences. No option letters (A/B/C/D).

QUESTION:
{stem}

(The options are given only so you know the context - never refer to them.)
OPTIONS:
{options}

Rewrite of the question:"""

# Phrases that mean the model leaked guidance about the answer.
_LEAK_RE = re.compile(
    r"\b(the answer is|correct (option|answer|choice)|right (option|answer|choice)"
    r"|option [a-d]\b|choice [a-d]\b|is correct|is incorrect|eliminate|rule out"
    r"|the solution is|equals?\s|therefore|so the value)\b",
    re.I,
)


def _parse_meta(assessment: Assessment) -> dict:
    if not assessment or not assessment.description:
        return {}
    try:
        meta = json.loads(assessment.description)
    except (json.JSONDecodeError, TypeError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _save_meta(assessment: Assessment, meta: dict) -> None:
    assessment.description = json.dumps(meta, ensure_ascii=False)
    get_db().commit()


def _cached(assessment: Assessment, question_id: int) -> Optional[str]:
    return (_parse_meta(assessment).get("clarify_cache") or {}).get(str(question_id))


def _store(assessment: Assessment, question_id: int, text: str) -> None:
    meta = _parse_meta(assessment)
    meta.setdefault("clarify_cache", {})[str(question_id)] = text
    _save_meta(assessment, meta)


def _looks_leaky(text: str, options: list) -> bool:
    if _LEAK_RE.search(text or ""):
        return True
    lowered = (text or "").lower()
    for o in options:
        ot = (o.get("text") or "").strip().lower()
        # Only flag a near-complete echo of a substantial option - a short
        # shared term (e.g. "irrational number") is fine in a rephrase.
        if len(ot.split()) >= 5 and len(ot) >= 20 and ot in lowered:
            return True
    return False


def _fallback(stem: str) -> str:
    stem = re.sub(r"\s+", " ", (stem or "").strip())
    return (
        "Read the question one part at a time. It is asking: "
        + (stem[:280] + ("…" if len(stem) > 280 else ""))
        + " Work out your own answer first, then pick the option that matches it."
    )


def clarify_question(assessment_id: int, question_id: int, *, use_cache: bool = True) -> dict:
    db = get_db()
    assessment = get_assessment(assessment_id)
    if not any(aq.question_id == question_id for aq in assessment.questions):
        raise LMSNotFoundError("Question is not part of this assessment")

    if use_cache:
        hit = _cached(assessment, question_id)
        if hit:
            return {"clarification": hit, "cached": True}

    q = db.query(Question).filter(Question.id == question_id).first()
    if not q:
        raise LMSNotFoundError(f"Question {question_id} not found")

    stem, _ = pick_display_fields(q.question_text, q.question_latex)
    stem = stem or q.question_text or ""
    opts = options_from_json(q.options_json)
    opt_display = []
    for o in opts:
        t, _l = pick_display_fields(o.get("text"), o.get("latex"))
        opt_display.append({"text": t or o.get("text") or ""})

    clarification = None
    try:
        from app.utils.groq_rate_limit import invoke_with_groq_rate_limit
        from app.utils.llm_factory import get_chat_model

        llm = get_chat_model(temperature=0.2, max_tokens=220)
        prompt = _CLARIFY_PROMPT.format(
            stem=stem.strip()[:800],
            options="\n".join("- " + (o["text"] or "")[:120] for o in opt_display),
        )
        resp = invoke_with_groq_rate_limit(
            lambda: llm.invoke(prompt), description="diagnostic question clarify"
        )
        text = getattr(resp, "content", None) or str(resp)
        text = re.sub(r"\s+", " ", str(text).strip())[:_MAX_CHARS]
        if text and not _looks_leaky(text, opt_display):
            clarification = text
        elif text:
            logger.info("Clarify for q%s rejected as leaky, using fallback", question_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Clarify LLM call failed for q%s: %s", question_id, exc)

    if not clarification:
        clarification = _fallback(stem)

    try:
        _store(assessment, question_id, clarification)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not cache clarification for q%s: %s", question_id, exc)
        db.rollback()

    return {"clarification": clarification, "cached": False}
