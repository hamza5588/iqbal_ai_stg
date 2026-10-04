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

# Bump when the prompt or the leak guard changes so old cached
# clarifications (e.g. ones that leaked the method) are regenerated.
# v3: drop cached dumps of raw AIMessage when Qwen reasoning emptied content.
# v4: drop cached fallbacks (the question repeated back) from when every call failed.
_CLARIFY_VERSION = 4

_CLARIFY_PROMPT = """A student on a diagnostic test cannot follow the WORDING of this
multiple-choice question. Say the SAME question again in the simplest
possible words. You are only re-wording it - not teaching it.

STRICT RULES - breaking any of these makes the test invalid:
- Do NOT answer it, and do NOT say or hint which option is right or wrong.
- Do NOT mention, compare, rank, or rule out any option.
- Do NOT say HOW to solve it. No method, no formula, no steps, no
  operation. Never say to multiply / divide / add / subtract anything.
  Never write things like "X is found by ...", "the area is length times
  width", "you calculate it by ...". Say only WHAT is being asked.
- You MAY swap hard words for easy ones and give a plain-language meaning
  of a term (e.g. "area" = the space inside a shape, "median" = the
  middle value) - but never turn that into a way to work out the answer.
- Very simple English, like for a younger student. 1 to 3 short sentences.
  No option letters (A/B/C/D).

QUESTION:
{stem}

(The options are shown for context only - never refer to them.)
OPTIONS:
{options}

The same question in very simple words:"""

# Phrases that mean the model leaked the answer or the solving method.
_LEAK_RE = re.compile(
    r"\b(the answer is|correct (option|answer|choice)|right (option|answer|choice)"
    r"|option [a-d]\b|choice [a-d]\b|is correct|is incorrect|eliminate|rule out"
    r"|the solution is|therefore|so the value is"
    # --- method / formula disclosure (avoid bare "equals"/"add" — too many false rejects) ---
    r"|multiply(?:ing|ied)? by|multiplication|divid(?:e|ing|ed by)|divided by"
    r"|subtract(?:ing)?|add(?:ing)? (?:up|together)|times the"
    r"|by the width|by the length|by the height|by the base"
    r"|is found by|is calculated|is computed|is obtained by|is worked out"
    r"|is given by|is the product of|is the sum of|is the difference of"
    r"|is the quotient of|formula"
    r"|to (find|get|calculate|work out|figure out) (the|its|your)"
    r"|you (can |should |just |then |need to |simply )?(multiply|divide|add|subtract|calculate|compute)"
    r")\b",
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
    entry = (_parse_meta(assessment).get("clarify_cache") or {}).get(str(question_id))
    if isinstance(entry, dict):
        if entry.get("v") == _CLARIFY_VERSION:
            return entry.get("text")
        return None
    # legacy plain-string entry from an older guard version - ignore it
    return None


def _store(assessment: Assessment, question_id: int, text: str) -> None:
    meta = _parse_meta(assessment)
    meta.setdefault("clarify_cache", {})[str(question_id)] = {
        "v": _CLARIFY_VERSION,
        "text": text,
    }
    _save_meta(assessment, meta)


# Plain operation words. Saying them is a leak for a word problem ("multiply the
# length by the width"), but not when the question itself is written with that
# arithmetic ("log 2 + log 5", "3x + 7 = 22") - there, reading it aloud needs them.
_ARITH_RE = re.compile(
    r"\b(multiply(?:ing|ied)? by|multiplication|divid(?:e|ing|ed by)|divided by"
    r"|subtract(?:ing)?|add(?:ing)? (?:up|together)|times the"
    r"|is the (?:product|sum|difference|quotient) of"
    r"|you (can |should |just |then |need to |simply )?(multiply|divide|add|subtract))\b",
    re.I,
)
_STEM_HAS_ARITH_RE = re.compile(
    r"[+×÷^]|\\(?:times|div|frac|cdot)|\b[\da-z]\s*[-−]\s*[\da-z]\b|\b\d+[a-z]\b"
    r"|\b(divided|multiplied|sum|product|remainder|quotient|difference|plus|minus|times)\b",
    re.I,
)


def _looks_leaky(text: str, options: list, stem: str = "") -> bool:
    checked = text or ""
    if stem and _STEM_HAS_ARITH_RE.search(stem):
        checked = _ARITH_RE.sub(" ", checked)
    if _LEAK_RE.search(checked):
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
        "Read it slowly, one part at a time. The question is asking: "
        + (stem[:280] + ("…" if len(stem) > 280 else ""))
        + " It does not want you to do anything except answer that. "
        "Work out your own answer first, then choose the option that matches it."
    )


def _message_text(resp) -> str:
    """Pull usable text from an LLM response — never stringify the whole message object."""
    content = getattr(resp, "content", None)
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str) and block.strip():
                parts.append(block.strip())
            elif isinstance(block, dict):
                piece = block.get("text") or block.get("content") or ""
                if isinstance(piece, str) and piece.strip():
                    parts.append(piece.strip())
            else:
                piece = getattr(block, "text", None) or getattr(block, "content", None)
                if isinstance(piece, str) and piece.strip():
                    parts.append(piece.strip())
        joined = "\n".join(parts).strip()
        if joined:
            return joined
    # Never fall back to str(resp) — that dumps AIMessage metadata into the UI.
    return ""


def clarify_question(assessment_id: int, question_id: int, *, use_cache: bool = True) -> dict:
    db = get_db()
    assessment = get_assessment(assessment_id)
    if not any(aq.question_id == question_id for aq in assessment.questions):
        raise LMSNotFoundError("Question is not part of this assessment")

    if use_cache:
        hit = _cached(assessment, question_id)
        if hit and "additional_kwargs=" not in hit and "response_metadata=" not in hit:
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

        # Reasoning can consume a small max_tokens budget entirely
        # (finish_reason=length, content=''). Leave headroom + prefer little reasoning.
        base_llm = get_chat_model(temperature=0.2, max_tokens=1024)
        prompt = _CLARIFY_PROMPT.format(
            stem=stem.strip()[:800],
            options="\n".join("- " + (o["text"] or "")[:120] for o in opt_display),
        )
        # Reasoning knobs differ per model: Qwen takes "none", GPT-OSS only
        # low/medium/high (a wrong value is a 400 and used to send EVERY click to
        # the fallback). Last try is the factory's own defaults, which always work.
        model_name = str(getattr(base_llm, "model_name", "") or getattr(base_llm, "model", "")).lower()
        first = (
            {"reasoning_effort": "none", "reasoning_format": "hidden"}
            if "qwen" in model_name
            else {"reasoning_effort": "low"}
        )
        for bind_kwargs in (first, {}):
            try:
                llm = base_llm.bind(**bind_kwargs) if bind_kwargs else base_llm
                resp = invoke_with_groq_rate_limit(
                    lambda: llm.invoke(prompt), description="diagnostic question clarify"
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Clarify LLM call failed for q%s (%s): %s", question_id, bind_kwargs or "defaults", exc
                )
                continue
            text = re.sub(r"\s+", " ", _message_text(resp))[:_MAX_CHARS]
            if text and not _looks_leaky(text, opt_display, stem):
                clarification = text
            elif text:
                logger.info("Clarify for q%s rejected as leaky, using fallback", question_id)
            else:
                meta = getattr(resp, "response_metadata", None) or {}
                logger.warning(
                    "Clarify for q%s returned empty content (finish_reason=%s)",
                    question_id,
                    meta.get("finish_reason"),
                )
                continue
            break
    except Exception as exc:  # noqa: BLE001
        logger.warning("Clarify LLM call failed for q%s: %s", question_id, exc)

    if not clarification:
        # Never cached: the fallback only repeats the question, so the next click
        # must get another chance at a real rephrase.
        return {"clarification": _fallback(stem), "cached": False, "fallback": True}

    try:
        _store(assessment, question_id, clarification)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not cache clarification for q%s: %s", question_id, exc)
        db.rollback()

    return {"clarification": clarification, "cached": False}
