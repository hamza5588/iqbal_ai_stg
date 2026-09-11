"""Question set for a diagnostic retake.

DIL feedback: "on retake the questions should be different but equivalent."

Architecture: an AI-built pool of equivalent variant questions (same
concept, new numbers/wording) is kept per original question in the
assessment's ``description`` meta as ``variant_pool: {orig_qid: [var_qid,
...]}``. Each retake draws one entry per slot; the whole question order is
also shuffled. Building the pool is bounded (rate-limited, resumable) and
happens off the request path - a retake never waits on it, it just falls
back to the original question for any slot that has no ready variant yet.

This module (7a) provides the selection + ordering; variant generation and
validation live in ``_generate_variants`` (7b).
"""
from __future__ import annotations

import json
import logging
import random
from typing import Dict, List, Optional

from app.models.lms_models import Assessment, Question
from app.services.lms.assessment_service import get_assessment
from app.services.lms.mcq_utils import options_from_json, pick_display_fields
from app.utils.db import get_db

logger = logging.getLogger(__name__)

# questions.source_type has a CHECK constraint
# (manual, pdf_qa_converted, pdf_ai, mixed) - variants are AI-built from an
# existing item, so they store as "mixed". They are identified as variants
# only through the assessment's variant_pool meta, never by source_type.
VARIANT_SOURCE_TYPE = "mixed"
TARGET_VARIANTS_PER_QUESTION = 2


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


def get_variant_pool(assessment_id: int) -> Dict[str, List[int]]:
    assessment = get_assessment(assessment_id)
    pool = _parse_meta(assessment).get("variant_pool") or {}
    return pool if isinstance(pool, dict) else {}


def _original_question_ids(assessment: Assessment) -> List[int]:
    return [aq.question_id for aq in sorted(assessment.questions, key=lambda x: x.sort_order)]


def select_and_shuffle(
    originals: List[int],
    pool: Dict[str, List[int]],
    seed: str,
    attempt_number: int,
) -> List[int]:
    """Pure selection: for each original slot pick variant
    ``(N-2) % len(variants)`` if the slot has ready variants, else keep the
    original; then shuffle with a deterministic per-attempt seed."""
    if attempt_number <= 1:
        return list(originals)
    pick_idx = attempt_number - 2
    chosen: List[int] = []
    for oid in originals:
        variants = pool.get(str(oid)) or []
        chosen.append(int(variants[pick_idx % len(variants)]) if variants else oid)
    random.Random(seed).shuffle(chosen)
    return chosen


def question_set_for_attempt(assessment_id: int, attempt_number: int) -> List[int]:
    """Ordered question ids for attempt N (1 = original)."""
    assessment = get_assessment(assessment_id)
    originals = _original_question_ids(assessment)
    if attempt_number <= 1:
        return originals
    return select_and_shuffle(
        originals,
        get_variant_pool(assessment_id),
        f"{assessment_id}:{attempt_number}",
        attempt_number,
    )


# --- pool building (7b wires the LLM into _generate_variants) --------------

def enqueue_pool_prewarm(assessment_id: int) -> None:
    """Best-effort background build of the variant pool - a retake never
    waits on it (it just falls back to the original for unfilled slots)."""
    try:
        from app.tasks.lms_tasks import enqueue_variant_pool_prewarm

        enqueue_variant_pool_prewarm(assessment_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("variant pool prewarm unavailable: %s", exc)


# --- LLM generation + self-check -----------------------------------------

_GEN_PROMPT = """Rewrite this multiple-choice maths question as an EQUIVALENT new question.

Rules:
- Same concept, same method, same difficulty, same structure/format.
- Change the numbers, names, or context so it is clearly a different question.
- Exactly 4 options. Exactly one correct. Make the 3 wrong options plausible
  (common mistakes), not obviously silly.
- Keep option style consistent (all numbers, or all expressions, etc.).
- Return plain text (you may use ^ for powers and / for fractions).

ORIGINAL QUESTION:
{stem}

ORIGINAL OPTIONS:
{options}
(correct: {correct_letter})
"""

_SOLVE_PROMPT = """Solve this multiple-choice question. Think step by step, then give
the 0-based index of the correct option and your confidence 0-1.

{stem}

Options:
{options}
"""


def _generate_one_variant(orig: Question) -> Optional[dict]:
    """Return {question_text, options[4], correct_index} for a validated
    equivalent variant, or None if generation/validation failed."""
    try:
        from pydantic import BaseModel, Field, conlist

        from app.utils.groq_rate_limit import invoke_with_groq_rate_limit
        from app.utils.llm_factory import get_chat_model

        class VariantMCQ(BaseModel):
            question_text: str = Field(..., min_length=3)
            options: conlist(str, min_length=4, max_length=4)  # type: ignore
            correct_index: int = Field(..., ge=0, le=3)

        class SolvedMCQ(BaseModel):
            chosen_index: int = Field(..., ge=0, le=3)
            confidence: float = Field(..., ge=0.0, le=1.0)

        stem, _ = pick_display_fields(orig.question_text, orig.question_latex)
        opts = options_from_json(orig.options_json)
        opt_texts = []
        for o in opts:
            t, _l = pick_display_fields(o.get("text"), o.get("latex"))
            opt_texts.append(t or o.get("text") or "")
        correct_letter = "ABCD"[orig.correct_option_index] if orig.correct_option_index is not None else "?"

        gen_llm = get_chat_model(temperature=0.7, max_tokens=500).with_structured_output(VariantMCQ)
        variant: VariantMCQ = invoke_with_groq_rate_limit(
            lambda: gen_llm.invoke(
                _GEN_PROMPT.format(
                    stem=(stem or "").strip()[:800],
                    options="\n".join(f"{l}. {t[:120]}" for l, t in zip("ABCD", opt_texts)),
                    correct_letter=correct_letter,
                )
            ),
            description="diagnostic variant generation",
        )
        # The structured-output JSON round trip can mis-escape a literal
        # backslash before a LaTeX command as a control character (\frac ->
        # a real form-feed + "rac" - see math_text.py). Repair BEFORE the
        # .strip() calls below, which would otherwise treat that leading
        # control character as whitespace and destroy the only evidence of
        # where it belonged (found live via E2E testing: a stored variant
        # option read "rac{3}{8}" with the backslash-f gone for good).
        from app.services.quiz.math_text import recover_eaten_backslash_commands

        variant.question_text = recover_eaten_backslash_commands(variant.question_text)
        variant.options = [recover_eaten_backslash_commands(o) for o in variant.options]
        v_opts = [str(x).strip() for x in variant.options]
        if len(set(o.lower() for o in v_opts)) < 4 or any(not o for o in v_opts):
            return None

        solve_llm = get_chat_model(temperature=0.0, max_tokens=400).with_structured_output(SolvedMCQ)
        solved: SolvedMCQ = invoke_with_groq_rate_limit(
            lambda: solve_llm.invoke(
                _SOLVE_PROMPT.format(
                    stem=variant.question_text.strip()[:800],
                    options="\n".join(f"{i}. {o[:120]}" for i, o in enumerate(v_opts)),
                )
            ),
            description="diagnostic variant self-check",
        )
        if solved.chosen_index != variant.correct_index or solved.confidence < 0.6:
            logger.info(
                "Variant of q%s rejected (gen says %s, solver says %s @%.2f)",
                orig.id, variant.correct_index, solved.chosen_index, solved.confidence,
            )
            return None

        return {
            "question_text": variant.question_text.strip(),
            "options": v_opts,
            "correct_index": variant.correct_index,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Variant generation for q%s failed: %s", orig.id, exc)
        return None


def ensure_variant_pool(
    assessment_id: int,
    target_per_question: int = TARGET_VARIANTS_PER_QUESTION,
    max_generate: int = 6,
) -> dict:
    """Top the variant pool up towards ``target_per_question`` per original,
    generating at most ``max_generate`` this call (so a worker run is
    bounded and resumable). Slots with the fewest variants go first."""
    from app.services.lms import question_bank_service

    db = get_db()
    assessment = get_assessment(assessment_id)
    if assessment.assessment_type != "diagnostic":
        return {"assessment_id": assessment_id, "generated": 0, "reason": "not_diagnostic"}

    meta = _parse_meta(assessment)
    pool: Dict[str, List[int]] = meta.setdefault("variant_pool", {})
    originals = {aq.question_id: aq for aq in assessment.questions}
    orig_rows = {r.id: r for r in db.query(Question).filter(Question.id.in_(list(originals))).all()}

    need = sorted(
        (oid for oid in originals if len(pool.get(str(oid), [])) < target_per_question),
        key=lambda oid: len(pool.get(str(oid), [])),
    )
    generated = 0
    for oid in need:
        if generated >= max_generate:
            break
        orig = orig_rows.get(oid)
        if not orig:
            continue
        v = _generate_one_variant(orig)
        if not v:
            continue
        try:
            q = question_bank_service.create_question(
                created_by=assessment.created_by or (orig.created_by or 1),
                question_text=v["question_text"],
                options=v["options"],
                correct_option_index=v["correct_index"],
                topic_id=orig.topic_id,
                difficulty=orig.difficulty or "medium",
                source_type=VARIANT_SOURCE_TYPE,
            )
            if orig.time_limit_seconds:
                q.time_limit_seconds = orig.time_limit_seconds
            pool.setdefault(str(oid), []).append(q.id)
            generated += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Storing variant of q%s failed: %s", oid, exc)
            db.rollback()

    if generated:
        _save_meta(assessment, meta)
    total = sum(len(v) for v in pool.values())
    return {"assessment_id": assessment_id, "generated": generated, "pool_total": total}
