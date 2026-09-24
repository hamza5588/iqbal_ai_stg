"""Question set for a diagnostic retake.

DIL feedback: on retake the questions should be different but equivalent —
same concept/topic, new numbers and options so the student cannot memorize
the previous paper.

Architecture: an AI-built pool of equivalent variant questions is kept per
original question in the assessment's ``description`` meta as
``variant_pool: {orig_qid: [var_qid, ...]}``. Each retake draws one unused
entry per slot (never a question the student already saw), then shuffles
order. If the pool is empty for a slot, a variant is generated on the
retake path so we do not fall back to the identical original paper.
"""
from __future__ import annotations

import json
import logging
import random
import re
from typing import Dict, List, Optional, Set

from app.models.lms_models import Assessment, AssessmentAttempt, Question
from app.services.lms.assessment_service import get_assessment
from app.services.lms.mcq_utils import options_from_json, pick_display_fields
from app.utils.db import get_db

logger = logging.getLogger(__name__)

# questions.source_type has a CHECK constraint
# (manual, pdf_qa_converted, pdf_ai, mixed) - variants are AI-built from an
# existing item, so they store as "mixed". They are identified as variants
# only through the assessment's variant_pool meta, never by source_type.
VARIANT_SOURCE_TYPE = "mixed"
TARGET_VARIANTS_PER_QUESTION = 3


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


def previously_seen_question_ids(student_id: int, assessment_id: int) -> Set[int]:
    """Every question id this student already faced on this diagnostic."""
    db = get_db()
    seen: Set[int] = set()
    rows = (
        db.query(AssessmentAttempt)
        .filter(
            AssessmentAttempt.student_id == student_id,
            AssessmentAttempt.assessment_id == assessment_id,
            AssessmentAttempt.status.in_(("submitted", "in_progress", "abandoned")),
        )
        .all()
    )
    assessment = get_assessment(assessment_id)
    default_ids = [aq.question_id for aq in sorted(assessment.questions, key=lambda x: x.sort_order)]
    for att in rows:
        raw = getattr(att, "question_ids_json", None)
        if raw:
            try:
                ids = json.loads(raw)
                if isinstance(ids, list) and ids:
                    seen.update(int(x) for x in ids)
                    continue
            except (ValueError, TypeError):
                pass
        seen.update(default_ids)
    return seen


def select_and_shuffle(
    originals: List[int],
    pool: Dict[str, List[int]],
    seed: str,
    attempt_number: int,
    exclude_ids: Optional[Set[int]] = None,
) -> List[int]:
    """Pick one unused variant per original slot (never a seen id), then shuffle.

    On retake (attempt_number > 1) we prefer a pool variant that is not in
    ``exclude_ids``. If none are available the original is returned as a
    placeholder — callers should fill those slots by generating first.
    """
    if attempt_number <= 1:
        return list(originals)
    exclude = set(exclude_ids or ())
    pick_idx = max(0, attempt_number - 2)
    chosen: List[int] = []
    for oid in originals:
        variants = [int(v) for v in (pool.get(str(oid)) or []) if int(v) not in exclude]
        if variants:
            chosen.append(int(variants[pick_idx % len(variants)]))
        else:
            # Placeholder — retake path must replace with a fresh variant.
            chosen.append(int(oid))
    random.Random(seed).shuffle(chosen)
    return chosen


def question_set_for_attempt(
    assessment_id: int,
    attempt_number: int,
    student_id: Optional[int] = None,
) -> List[int]:
    """Ordered question ids for attempt N (1 = original paper).

    Retakes (N > 1) never reuse a question this student already saw when a
    same-concept variant can be provided.
    """
    assessment = get_assessment(assessment_id)
    originals = _original_question_ids(assessment)
    if attempt_number <= 1:
        return originals

    # Never reuse the first-paper items or anything this student already saw.
    exclude: Set[int] = set(originals)
    if student_id:
        exclude |= previously_seen_question_ids(student_id, assessment_id)

    pool = get_variant_pool(assessment_id)
    missing = [
        oid
        for oid in originals
        if not any(int(v) not in exclude for v in (pool.get(str(oid)) or []))
    ]
    if missing:
        ensure_variant_pool(
            assessment_id,
            target_per_question=TARGET_VARIANTS_PER_QUESTION,
            max_generate=max(len(missing), 1),
            prefer_originals=missing,
            exclude_ids=exclude,
        )
        pool = get_variant_pool(assessment_id)

    # Still missing after batch fill — generate slot-by-slot.
    still_need = [
        oid
        for oid in originals
        if not any(int(v) not in exclude for v in (pool.get(str(oid)) or []))
    ]
    for oid in still_need:
        _generate_and_store_variant(assessment_id, oid)
        pool = get_variant_pool(assessment_id)

    chosen = select_and_shuffle(
        originals,
        pool,
        f"{assessment_id}:{attempt_number}:{student_id or 0}",
        attempt_number,
        exclude_ids=exclude,
    )

    reused = [qid for qid in chosen if qid in exclude]
    if reused:
        logger.warning(
            "Retake for assessment %s still reuses %s question(s) after variant generation",
            assessment_id,
            len(reused),
        )
    return chosen


def enqueue_pool_prewarm(assessment_id: int) -> None:
    """Best-effort background build of the variant pool."""
    try:
        from app.tasks.lms_tasks import enqueue_variant_pool_prewarm

        enqueue_variant_pool_prewarm(assessment_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("variant pool prewarm unavailable: %s", exc)


_GEN_PROMPT = """Rewrite this multiple-choice maths question as an EQUIVALENT new question for a retake exam.

Rules:
- SAME concept / topic / method / difficulty / structure (e.g. if it tested fractions, keep fractions).
- MUST change the numeric values (and names/context if present) so it is clearly a different question.
- MUST change the option values too — do not keep the same four numbers/expressions.
- Exactly 4 options. Exactly one correct. Wrong options should be plausible mistakes.
- Keep option style consistent (all percents, or all expressions, etc.).
- Return plain readable text (you may use ^ for powers and / for fractions). No \\frac, \\tfrac, \\,, \\quad.

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


def _extract_numbers(text: str) -> List[str]:
    return re.findall(r"\d+(?:\.\d+)?", text or "")


def _variant_too_similar(orig_stem: str, orig_opts: List[str], new_stem: str, new_opts: List[str]) -> bool:
    """Reject variants that keep the same numbers (student could memorize)."""
    o_nums = _extract_numbers(orig_stem) + [n for o in orig_opts for n in _extract_numbers(o)]
    n_nums = _extract_numbers(new_stem) + [n for o in new_opts for n in _extract_numbers(o)]
    if o_nums and o_nums == n_nums:
        return True
    o_norm = re.sub(r"\s+", "", (orig_stem or "").lower())
    n_norm = re.sub(r"\s+", "", (new_stem or "").lower())
    return bool(o_norm and o_norm == n_norm)


def _generate_one_variant(orig: Question) -> Optional[dict]:
    """Return {question_text, options[4], correct_index} or None."""
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

        gen_llm = get_chat_model(temperature=0.85, max_tokens=700).with_structured_output(VariantMCQ)
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
        from app.services.quiz.math_text import recover_eaten_backslash_commands

        variant.question_text = recover_eaten_backslash_commands(variant.question_text)
        variant.options = [recover_eaten_backslash_commands(o) for o in variant.options]
        v_opts = [str(x).strip() for x in variant.options]
        if len(set(o.lower() for o in v_opts)) < 4 or any(not o for o in v_opts):
            return None
        if _variant_too_similar(stem or "", opt_texts, variant.question_text, v_opts):
            logger.info("Variant of q%s rejected — numbers/stem too similar to original", orig.id)
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
        if solved.chosen_index != variant.correct_index or solved.confidence < 0.55:
            logger.info(
                "Variant of q%s rejected (gen says %s, solver says %s @%.2f)",
                orig.id, variant.correct_index, solved.chosen_index, solved.confidence,
            )
            return None

        try:
            from app.services.quiz.display_format_qa import finalize_display_text, finalize_option_text

            q_text, _ql = finalize_display_text(variant.question_text.strip(), None)
            clean_opts = []
            for o in v_opts:
                ot, _ol = finalize_option_text(o, None)
                clean_opts.append(ot)
            v_opts = clean_opts
            variant_text = q_text
        except Exception:  # noqa: BLE001
            variant_text = variant.question_text.strip()

        return {
            "question_text": variant_text,
            "options": v_opts,
            "correct_index": variant.correct_index,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Variant generation for q%s failed: %s", orig.id, exc)
        return None


def _generate_and_store_variant(assessment_id: int, orig_id: int) -> Optional[int]:
    """Generate one variant for ``orig_id``, store it, return new question id."""
    from app.services.lms import question_bank_service

    db = get_db()
    assessment = get_assessment(assessment_id)
    orig = db.query(Question).filter(Question.id == orig_id).first()
    if not orig:
        return None

    for _ in range(3):
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
            meta = _parse_meta(assessment)
            pool: Dict[str, List[int]] = meta.setdefault("variant_pool", {})
            pool.setdefault(str(orig_id), []).append(q.id)
            _save_meta(assessment, meta)
            return q.id
        except Exception as exc:  # noqa: BLE001
            logger.warning("Storing variant of q%s failed: %s", orig_id, exc)
            db.rollback()
    return None


def ensure_variant_pool(
    assessment_id: int,
    target_per_question: int = TARGET_VARIANTS_PER_QUESTION,
    max_generate: int = 6,
    prefer_originals: Optional[List[int]] = None,
    exclude_ids: Optional[Set[int]] = None,
) -> dict:
    """Top the variant pool up towards ``target_per_question`` per original."""
    assessment = get_assessment(assessment_id)
    if assessment.assessment_type != "diagnostic":
        return {"assessment_id": assessment_id, "generated": 0, "reason": "not_diagnostic"}

    meta = _parse_meta(assessment)
    pool: Dict[str, List[int]] = meta.setdefault("variant_pool", {})
    originals = {aq.question_id for aq in assessment.questions}
    exclude = set(exclude_ids or ())

    def unused_count(oid: int) -> int:
        return sum(1 for v in (pool.get(str(oid)) or []) if int(v) not in exclude)

    if prefer_originals:
        need = [oid for oid in prefer_originals if oid in originals and unused_count(oid) < 1]
        need += [
            oid
            for oid in originals
            if oid not in need and len(pool.get(str(oid), [])) < target_per_question
        ]
    else:
        need = sorted(
            (oid for oid in originals if len(pool.get(str(oid), [])) < target_per_question),
            key=lambda oid: len(pool.get(str(oid), [])),
        )

    generated = 0
    for oid in need:
        if generated >= max_generate:
            break
        new_id = _generate_and_store_variant(assessment_id, oid)
        if new_id:
            generated += 1
            pool = get_variant_pool(assessment_id)

    total = sum(len(v) for v in get_variant_pool(assessment_id).values())
    return {"assessment_id": assessment_id, "generated": generated, "pool_total": total}
