"""Conceptual answer evaluation for scenario-based questions.

Evaluation agent : LLM judges one criterion level (0-4) per rubric criterion using the
                   question, teacher criteria, scoped learning content and scoped general
                   reference answers (no exact answer match required).
Validation agent : deterministic checks on the LLM output - every rubric criterion
                   present exactly once, levels in range, score recomputed server-side
                   from levels x teacher weights (the LLM never states the score), cited
                   evidence restricted to excerpts actually retrieved, consistency and
                   injection checks that route doubtful results to teacher review.

The student answer is untrusted: it is delimited, tag look-alikes are stripped, and a
heuristic injection detector runs independently of the model.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy import update

from app.models.scenario_models import ScenarioDocument, ScenarioEvaluation, ScenarioSubmission
from app.services.lms.exceptions import LMSValidationError
from app.services.scenario import retrieval
from app.services.scenario.common import (
    PROMPT_VERSION,
    clean_list,
    dumps,
    loads,
    render_prompt,
    structured_call,
    wrap_untrusted,
)
from app.services.scenario.schemas import AnswerEvaluation
from app.utils.db import get_db

logger = logging.getLogger(__name__)

MAX_ANSWER_CHARS = int(os.getenv("SBA_MAX_ANSWER_CHARS", "8000"))
EVAL_SAMPLES = max(1, min(3, int(os.getenv("SBA_EVAL_SAMPLES", "1"))))
CONSISTENCY_TOLERANCE = float(os.getenv("SBA_CONSISTENCY_TOLERANCE", "0.15"))
# Notes recorded on the evaluation that do not by themselves need a teacher's review
# (the fabricated citation is already removed and never affects the score).
INFO_ONLY_REASONS = ("No reference answers", "AI cited evidence that was not provided")

_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+|the\s+|any\s+|your\s+|previous\s+|prior\s+|above\s+)*(instructions?|rules?|rubric|prompt|criteria)",
    r"disregard\s+(the\s+|all\s+|your\s+)*(instructions?|rules?|rubric|prompt)",
    r"(give|award|assign)\s+(me\s+|this\s+(answer\s+)?)?(full|maximum|max|perfect|100%?|10\s*/\s*10|all)\s*(marks?|points?|score|credit)?",
    r"\byou\s+are\s+now\b",
    r"\b(system|assistant)\s*(prompt|message)?\s*:",
    r"new\s+instructions?\s*:",
    r"\bset\s+(the\s+)?(score|level|grade)s?\s+to\b",
    r"</?\s*(student_answer|system|reference_answers|evaluation_criteria)\s*>",
    r"\"?(level|total_score|score)\"?\s*:\s*\d",
]


def detect_injection(text: str) -> List[str]:
    low = (text or "").lower()
    return [p for p in _INJECTION_PATTERNS if re.search(p, low)]


def normalize_rubric(rubric: List[Dict]) -> List[Dict]:
    out, seen, total = [], set(), 0
    for c in rubric or []:
        key = re.sub(r"[^a-z0-9_]", "", str(c.get("key", "")).strip().lower())
        if not key or key in seen:
            raise LMSValidationError("Each rubric criterion needs a unique key.")
        try:
            weight = float(c.get("weight"))
        except (TypeError, ValueError):
            raise LMSValidationError(f"Rubric weight for {key} must be a number.") from None
        if weight < 0:
            raise LMSValidationError("Rubric weights cannot be negative.")
        seen.add(key)
        total += weight
        out.append({"key": key, "label": str(c.get("label") or key.replace("_", " ").title())[:80], "weight": weight})
    if not out:
        raise LMSValidationError("The rubric needs at least one criterion.")
    if abs(total - 100) > 0.01:
        raise LMSValidationError(f"Rubric weights must add up to 100 (currently {total:g}).")
    return out


def compute_score(levels: Dict[str, int], rubric: List[Dict], max_score: float) -> Tuple[float, List[Dict]]:
    criteria, total = [], 0.0
    for c in rubric:
        level = int(levels.get(c["key"], 0))
        level = max(0, min(4, level))
        pts = max_score * (c["weight"] / 100.0) * (level / 4.0)
        total += pts
        criteria.append({
            "key": c["key"],
            "label": c["label"],
            "weight": c["weight"],
            "level": level,
            "score": round(pts, 2),
            "max": round(max_score * c["weight"] / 100.0, 2),
        })
    return round(total, 2), criteria


def _doc_versions(assessment_id: int) -> List[Dict]:
    docs = (
        get_db()
        .query(ScenarioDocument)
        .filter(ScenarioDocument.assessment_id == assessment_id, ScenarioDocument.is_active.is_(True),
                ScenarioDocument.status == "ready")
        .all()
    )
    return [{"document_id": d.id, "doc_type": d.doc_type, "version": d.version, "filename": d.filename} for d in docs]


def _one_pass(prompt: str, rubric_keys: List[str]) -> Tuple[AnswerEvaluation, str]:
    last_exc: Optional[Exception] = None
    for _ in range(2):  # one retry on malformed / incomplete output
        try:
            result, model = structured_call(AnswerEvaluation, prompt, description="sba answer evaluation", max_tokens=3000)
            keys = [c.key for c in result.criteria]
            if sorted(set(keys)) != sorted(rubric_keys) or len(keys) != len(rubric_keys):
                raise ValueError(f"criteria keys {keys} do not match rubric {rubric_keys}")
            return result, model
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning("SBA evaluation pass invalid: %s", exc)
    raise LMSValidationError(f"AI evaluation failed: {last_exc}")


def evaluate_submission(submission_id: int) -> ScenarioEvaluation:
    db = get_db()
    sub = db.query(ScenarioSubmission).get(submission_id)
    if sub is None:
        raise LMSValidationError("Submission not found")
    from app.models.scenario_models import ScenarioQuestion

    q = db.query(ScenarioQuestion).get(sub.question_id)
    assessment = q.assessment
    rubric = normalize_rubric(loads(assessment.rubric_json, []))
    max_score = float(assessment.max_score_per_question or 10)
    answer = (sub.answer_text or "")[:MAX_ANSWER_CHARS]
    reasons: List[str] = []
    refs_used: List[Dict] = []
    model = None

    if not answer.strip():
        total, criteria = compute_score({}, rubric, max_score)
        result = {
            "correct_concepts": [], "missing_concepts": clean_list(loads(q.concepts_json, [])),
            "incorrect_claims": [], "addresses_scenario": False, "injection_detected": False,
            "reasoning_feedback": "No answer was submitted, so no credit could be given.",
            "suggested_improvements": ["Write an answer that applies the course concepts to the scenario."],
            "confidence": "high", "justifications": {}, "evaluation_status": "empty_answer",
        }
    else:
        injection_hits = detect_injection(answer)
        concepts = " ".join(loads(q.concepts_json, []) or [])
        criteria_list = loads(q.evaluation_criteria_json, []) or []
        base_query = f"{q.scenario}\n{q.question_text}\n{concepts}"
        learning = retrieval.retrieve(assessment.id, "learning", f"{base_query}\n{answer[:600]}", k=5)
        references = retrieval.retrieve(assessment.id, "reference_answers", f"{base_query}\n{' '.join(criteria_list)}", k=5)
        allowed = {r["chunk_id"]: r for r in learning + references}
        if not references:
            reasons.append("No reference answers available - graded from learning content only.")
        prompt = render_prompt(
            "answer_evaluation",
            rubric_lines="\n".join(f"- {c['key']} ({c['label']}, weight {c['weight']:g}%)" for c in rubric),
            scenario=wrap_untrusted(q.scenario),
            question=wrap_untrusted(q.question_text),
            objectives="; ".join(loads(q.learning_objectives_json, []) or []) or "(not specified)",
            criteria="\n".join(f"- {wrap_untrusted(c)}" for c in criteria_list) or "(none provided)",
            content=retrieval.format_excerpts(learning, max_chars=6000),
            references=retrieval.format_excerpts(references, max_chars=5000),
            answer=wrap_untrusted(answer),
        )
        rubric_keys = [c["key"] for c in rubric]
        passes = []
        for _ in range(EVAL_SAMPLES):
            res, model = _one_pass(prompt, rubric_keys)
            passes.append(res)
        primary = passes[0]
        levels_by_pass = [{c.key: c.level for c in p.criteria} for p in passes]
        if len(passes) > 1:
            totals = [compute_score(lv, rubric, max_score)[0] for lv in levels_by_pass]
            if max(totals) - min(totals) > CONSISTENCY_TOLERANCE * max_score:
                reasons.append(f"Inconsistent repeated AI scores {totals}.")
            # Median level per criterion keeps a single noisy pass from deciding the grade.
            levels = {k: sorted(lv[k] for lv in levels_by_pass)[len(passes) // 2] for k in rubric_keys}
        else:
            levels = levels_by_pass[0]
        total, criteria = compute_score(levels, rubric, max_score)
        just = {c.key: c.justification[:800] for c in primary.criteria}
        for c in criteria:
            c["justification"] = just.get(c["key"], "")

        cited = [cid for cid in primary.supporting_chunk_ids if cid in allowed]
        if len(cited) < len(primary.supporting_chunk_ids):
            reasons.append("AI cited evidence that was not provided; it was removed.")
        refs_used = [
            {k: allowed[c][k] for k in ("chunk_id", "document_id", "doc_type", "doc_version", "page_start", "page_end", "heading")}
            for c in cited
        ]
        if injection_hits or primary.injection_detected:
            reasons.append("Possible prompt-injection text in the answer.")
        if primary.confidence == "low":
            reasons.append("AI reported low confidence.")
        pct = total / max_score if max_score else 0
        if not primary.addresses_scenario and pct > 0.5:
            reasons.append("Answer judged off-scenario but scored above 50%.")
        first_key = rubric[0]["key"]
        if levels.get(first_key, 0) == 0 and pct > 0.4:
            reasons.append("Score above 40% with zero conceptual accuracy.")
        if len(answer.split()) < 15 and pct >= 0.7:
            reasons.append("Very short answer received a high score.")
        result = {
            "correct_concepts": clean_list(primary.correct_concepts),
            "missing_concepts": clean_list(primary.missing_concepts),
            "incorrect_claims": clean_list(primary.incorrect_claims),
            "addresses_scenario": bool(primary.addresses_scenario),
            "injection_detected": bool(injection_hits or primary.injection_detected),
            "reasoning_feedback": (primary.reasoning_feedback or "").strip()[:3000],
            "suggested_improvements": clean_list(primary.suggested_improvements, limit=5),
            "confidence": primary.confidence,
            "evaluation_status": "evaluated",
            "passes": len(passes),
        }

    review_required = any(not r.startswith(INFO_ONLY_REASONS) for r in reasons)
    db.execute(
        update(ScenarioEvaluation).where(ScenarioEvaluation.submission_id == sub.id).values(is_current=False)
    )
    ev = ScenarioEvaluation(
        submission_id=sub.id,
        is_current=True,
        total_score=total,
        max_score=max_score,
        criteria_json=dumps(criteria),
        result_json=dumps({**result, "supporting_references": refs_used}),
        review_required=review_required,
        review_reasons_json=dumps(reasons),
        rubric_snapshot_json=dumps({"version": assessment.rubric_version, "criteria": rubric}),
        doc_versions_json=dumps(_doc_versions(assessment.id)),
        prompt_version=PROMPT_VERSION,
        model_name=model,
    )
    db.add(ev)
    sub.status = "evaluated"
    sub.evaluation_error = None
    db.commit()
    db.refresh(ev)
    return ev


def claim_for_evaluation(submission_id: int) -> bool:
    db = get_db()
    res = db.execute(
        update(ScenarioSubmission)
        .where(ScenarioSubmission.id == submission_id, ScenarioSubmission.status.in_(("submitted", "failed", "evaluated")))
        .values(status="evaluating", evaluation_error=None)
    )
    db.commit()
    return bool(res.rowcount)


def run_evaluation(submission_id: int) -> None:
    db = get_db()
    try:
        evaluate_submission(submission_id)
    except Exception as exc:  # noqa: BLE001 - recorded; safe to retry
        db.rollback()
        sub = db.query(ScenarioSubmission).get(submission_id)
        if sub is not None:
            sub.status = "failed"
            sub.evaluation_error = str(exc)[:2000]
            db.commit()
        logger.warning("SBA evaluation failed for submission %s: %s", submission_id, exc)


def evaluate_in_background(submission_id: int) -> bool:
    if not claim_for_evaluation(submission_id):
        return False
    if os.getenv("SBA_SYNC_PROCESSING", "").lower() in ("1", "true", "yes"):
        run_evaluation(submission_id)
        return True
    from flask import current_app

    app_obj = current_app._get_current_object()

    def run():
        with app_obj.app_context():
            run_evaluation(submission_id)

    threading.Thread(target=run, name=f"sba-eval-{submission_id}", daemon=True).start()
    return True


def recover_stuck(minutes: int = 15) -> int:
    """Evaluations left 'evaluating' by a restarted worker become retryable 'failed'."""
    from datetime import timedelta

    db = get_db()
    cutoff = datetime.utcnow() - timedelta(minutes=minutes)
    res = db.execute(
        update(ScenarioSubmission)
        .where(ScenarioSubmission.status == "evaluating", ScenarioSubmission.updated_at < cutoff)
        .values(status="failed", evaluation_error="Evaluation was interrupted; retry it.")
    )
    db.commit()
    return res.rowcount or 0
