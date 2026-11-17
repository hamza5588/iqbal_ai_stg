"""Scenario question generation + validation.

Generation agent  : grounded scenario questions from learning content, styled on samples.
Validation agent  : per-question LLM quality verdict (grounded / clear / no answer leak /
                    real scenario / grammar) plus deterministic checks (source ids exist,
                    length bounds, semantic duplicate & sample-copy detection).
Only questions that pass both are saved, always as teacher-review drafts.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from app.models.scenario_models import ScenarioAssessment, ScenarioQuestion, ScenarioSampleQuestion
from app.services.lms.exceptions import LMSValidationError
from app.services.scenario import retrieval
from app.services.scenario.common import (
    PROMPT_VERSION,
    clean_list,
    dumps,
    jaccard,
    loads,
    render_prompt,
    structured_call,
    wrap_untrusted,
)
from app.services.scenario.schemas import GeneratedQuestion, GeneratedQuestionBatch, QuestionVerdict
from app.utils.db import get_db

logger = logging.getLogger(__name__)

DUP_COSINE = float(os.getenv("SBA_DUP_COSINE", "0.90"))
DUP_JACCARD = float(os.getenv("SBA_DUP_JACCARD", "0.60"))
SAMPLE_COPY_COSINE = float(os.getenv("SBA_SAMPLE_COPY_COSINE", "0.92"))
SAMPLE_COPY_JACCARD = float(os.getenv("SBA_SAMPLE_COPY_JACCARD", "0.55"))
MAX_QUESTIONS = 20


def _q_text(scenario: str, question: str) -> str:
    return f"{scenario.strip()}\n{question.strip()}"


def _similarity(a_text: str, a_vec, b_text: str, b_vec) -> Tuple[float, float]:
    cos = retrieval._cosine(a_vec, b_vec) if a_vec and b_vec else 0.0
    return cos, jaccard(a_text, b_text)


def is_duplicate(text: str, vec, others: List[Tuple[str, Optional[list]]], cos_t: float, jac_t: float):
    best = (0.0, 0.0)
    for o_text, o_vec in others:
        cos, jac = _similarity(text, vec, o_text, o_vec)
        if cos >= cos_t or jac >= jac_t:
            return True, (round(cos, 3), round(jac, 3))
        best = max(best, (cos, jac))
    return False, (round(best[0], 3), round(best[1], 3))


def _content_for(assessment: ScenarioAssessment, focus: str = "") -> List[Dict]:
    objectives = " ".join(loads(assessment.learning_objectives_json, []) or [])
    query = " ".join(filter(None, [focus, assessment.topic, objectives, assessment.instructions, assessment.title]))
    refs = retrieval.retrieve(assessment.id, "learning", query, k=8)
    seen = {r["chunk_id"] for r in refs}
    for r in retrieval.spread_sample(assessment.id, "learning", k=8):
        if r["chunk_id"] not in seen:
            refs.append(r)
            seen.add(r["chunk_id"])
    return refs


def _samples_block(assessment_id: int, limit: int = 8) -> Tuple[str, List[Tuple[str, Optional[list]]]]:
    rows = (
        get_db()
        .query(ScenarioSampleQuestion)
        .filter(ScenarioSampleQuestion.assessment_id == assessment_id, ScenarioSampleQuestion.is_active.is_(True))
        .order_by(ScenarioSampleQuestion.id)
        .all()
    )
    pairs = [(r.text, retrieval.embed_query(r.text)) for r in rows]
    if not rows:
        return "(no sample questions uploaded - use a standard university case-study style)", pairs
    lines = []
    for r in rows[:limit]:
        meta = ", ".join(filter(None, [r.question_type, r.difficulty, r.objective]))
        lines.append(f"- {wrap_untrusted(r.text)[:1200]}" + (f"  ({meta})" if meta else ""))
    return "\n".join(lines), pairs


def _existing(assessment_id: int, exclude_id: Optional[int] = None) -> List[ScenarioQuestion]:
    q = get_db().query(ScenarioQuestion).filter(
        ScenarioQuestion.assessment_id == assessment_id, ScenarioQuestion.status != "rejected"
    )
    if exclude_id:
        q = q.filter(ScenarioQuestion.id != exclude_id)
    return q.order_by(ScenarioQuestion.sort_order).all()


def _validate_one(gq: GeneratedQuestion, content_refs: Dict[int, Dict]) -> Tuple[bool, Dict]:
    issues: List[str] = []
    scenario, question = (gq.scenario or "").strip(), (gq.question_text or "").strip()
    if len(scenario) < 120:
        issues.append("scenario too short")
    if len(question) < 20 or len(scenario) > 3000 or len(question) > 1200:
        issues.append("question length out of bounds")
    used = [cid for cid in gq.source_chunk_ids if cid in content_refs]
    if not used:
        issues.append("no valid learning-content source cited")
    verdict = None
    try:
        excerpts = [content_refs[c] for c in used] or list(content_refs.values())[:4]
        verdict, _ = structured_call(
            QuestionVerdict,
            render_prompt(
                "question_validation",
                scenario=wrap_untrusted(scenario),
                question=wrap_untrusted(question),
                content=retrieval.format_excerpts(excerpts, max_chars=6000),
            ),
            description="sba question validation",
            max_tokens=800,
        )
        if not verdict.grounded:
            issues.append("not grounded in learning content")
        if verdict.reveals_answer:
            issues.append("reveals the answer")
        if not verdict.is_scenario:
            issues.append("no real scenario")
        if not verdict.clear:
            issues.append("ambiguous")
    except Exception as exc:  # noqa: BLE001 - validation unavailable: keep, but tell the teacher
        logger.warning("SBA question validation unavailable: %s", exc)
        issues.append("automatic quality check unavailable - review carefully")
    hard = [i for i in issues if i not in ("ambiguous", "automatic quality check unavailable - review carefully")]
    return not hard, {
        "issues": issues,
        "verdict": verdict.model_dump() if verdict else None,
        "used_chunk_ids": used,
    }


def _build_question(gq: GeneratedQuestion, assessment: ScenarioAssessment, content_refs, check, model, sort_order):
    refs = [
        {k: content_refs[c][k] for k in ("chunk_id", "document_id", "doc_version", "page_start", "page_end", "heading")}
        for c in check["used_chunk_ids"]
    ]
    return ScenarioQuestion(
        assessment_id=assessment.id,
        sort_order=sort_order,
        scenario=gq.scenario.strip(),
        question_text=gq.question_text.strip(),
        difficulty=gq.difficulty if gq.difficulty in ("easy", "medium", "hard") else assessment.difficulty,
        learning_objectives_json=dumps(clean_list(gq.learning_objectives, limit=6)),
        concepts_json=dumps(clean_list(gq.concepts, limit=8)),
        source_refs_json=dumps(refs),
        evaluation_criteria_json=dumps(clean_list(gq.evaluation_criteria, limit=8)),
        generation_meta_json=dumps(
            {
                "model": model,
                "prompt_version": PROMPT_VERSION,
                "validation": check,
                "similarity": check.get("similarity"),
            }
        ),
        status="draft",
    )


def _call_generator(assessment, content_refs, count, avoid_texts, samples_text, focus=""):
    objectives = loads(assessment.learning_objectives_json, []) or []
    avoid_block = ""
    if avoid_texts:
        avoid_block = "# EXISTING QUESTIONS (write different situations and angles)\n" + "\n".join(
            f"- {t[:300]}" for t in avoid_texts[:20]
        )
    prompt = render_prompt(
        "question_generation",
        count=count,
        title=wrap_untrusted(assessment.title),
        difficulty=assessment.difficulty,
        topic=wrap_untrusted(focus or assessment.topic or "the whole learning content"),
        objectives="\n".join(f"- {o}" for o in objectives) or "(derive them from the learning content)",
        instructions=wrap_untrusted(assessment.instructions or "(none)"),
        samples=samples_text,
        content=retrieval.format_excerpts(list(content_refs.values()), max_chars=14000),
        avoid_block=avoid_block,
    )
    batch, model = structured_call(
        GeneratedQuestionBatch, prompt, description="sba question generation", max_tokens=6000
    )
    return batch.questions, model


def generate_questions(assessment_id: int, count: Optional[int] = None) -> Dict:
    db = get_db()
    assessment = db.query(ScenarioAssessment).get(assessment_id)
    if assessment is None:
        raise LMSValidationError("Assessment not found")
    content = _content_for(assessment)
    if not content:
        raise LMSValidationError("Upload and process at least one learning-content PDF first.")
    content_refs = {r["chunk_id"]: r for r in content}
    samples_text, sample_pairs = _samples_block(assessment.id)
    existing = _existing(assessment.id)
    existing_pairs = [(_q_text(q.scenario, q.question_text), None) for q in existing]
    existing_pairs = [(t, retrieval.embed_query(t)) for t, _ in existing_pairs]

    wanted = max(1, min(MAX_QUESTIONS, int(count or assessment.question_count or 5)))
    saved: List[ScenarioQuestion] = []
    rejected: List[Dict] = []
    model = None
    next_order = (max((q.sort_order for q in existing), default=-1) + 1)
    for _attempt in range(2):
        need = wanted - len(saved)
        if need <= 0:
            break
        candidates, model = _call_generator(
            assessment, content_refs, min(MAX_QUESTIONS, need + 2), [t for t, _ in existing_pairs], samples_text
        )
        for gq in candidates:
            if len(saved) >= wanted:
                break
            text = _q_text(gq.scenario or "", gq.question_text or "")
            vec = retrieval.embed_query(text)
            dup, sim = is_duplicate(text, vec, existing_pairs, DUP_COSINE, DUP_JACCARD)
            if dup:
                rejected.append({"question": gq.question_text[:200], "reason": f"duplicate of an existing question {sim}"})
                continue
            copy, ssim = is_duplicate(text, vec, sample_pairs, SAMPLE_COPY_COSINE, SAMPLE_COPY_JACCARD)
            if copy:
                rejected.append({"question": gq.question_text[:200], "reason": f"too close to a sample question {ssim}"})
                continue
            ok, check = _validate_one(gq, content_refs)
            check["similarity"] = {"max_existing": sim, "max_sample": ssim}
            if not ok:
                rejected.append({"question": gq.question_text[:200], "reason": "; ".join(check["issues"])})
                continue
            q = _build_question(gq, assessment, content_refs, check, model, next_order)
            next_order += 1
            db.add(q)
            db.flush()
            saved.append(q)
            existing_pairs.append((text, vec))
    db.commit()
    return {"created": [q.id for q in saved], "rejected": rejected, "requested": wanted, "model": model}


def regenerate_question(question_id: int) -> ScenarioQuestion:
    """Replace one draft/rejected question in place with a freshly generated one."""
    db = get_db()
    q = db.query(ScenarioQuestion).get(question_id)
    if q is None:
        raise LMSValidationError("Question not found")
    assessment = q.assessment
    concepts = " ".join(loads(q.concepts_json, []) or [])
    content = _content_for(assessment, focus=concepts)
    if not content:
        raise LMSValidationError("Upload and process at least one learning-content PDF first.")
    content_refs = {r["chunk_id"]: r for r in content}
    samples_text, sample_pairs = _samples_block(assessment.id)
    others = [(_q_text(o.scenario, o.question_text), None) for o in _existing(assessment.id, exclude_id=q.id)]
    others.append((_q_text(q.scenario, q.question_text), None))  # must differ from the one it replaces
    others = [(t, retrieval.embed_query(t)) for t, _ in others]
    last_reason = "no candidate passed validation"
    for _attempt in range(2):
        candidates, model = _call_generator(assessment, content_refs, 2, [t for t, _ in others], samples_text, focus=concepts)
        for gq in candidates:
            text = _q_text(gq.scenario or "", gq.question_text or "")
            vec = retrieval.embed_query(text)
            if is_duplicate(text, vec, others, DUP_COSINE, DUP_JACCARD)[0]:
                last_reason = "duplicate"
                continue
            if is_duplicate(text, vec, sample_pairs, SAMPLE_COPY_COSINE, SAMPLE_COPY_JACCARD)[0]:
                last_reason = "copied a sample question"
                continue
            ok, check = _validate_one(gq, content_refs)
            if not ok:
                last_reason = "; ".join(check["issues"])
                continue
            fresh = _build_question(gq, assessment, content_refs, check, model, q.sort_order)
            for col in ("scenario", "question_text", "difficulty", "learning_objectives_json", "concepts_json",
                        "source_refs_json", "evaluation_criteria_json", "generation_meta_json"):
                setattr(q, col, getattr(fresh, col))
            q.status = "draft"
            q.edited_by_teacher = False
            db.commit()
            return q
    raise LMSValidationError(f"Could not regenerate a valid question ({last_reason}). Try again.")


def generate_in_background(assessment_id: int, count: Optional[int] = None) -> None:
    from flask import current_app
    from sqlalchemy import update

    db = get_db()
    res = db.execute(
        update(ScenarioAssessment)
        .where(ScenarioAssessment.id == assessment_id, ScenarioAssessment.generation_status != "running")
        .values(generation_status="running", generation_error=None, updated_at=datetime.utcnow())
    )
    db.commit()
    if not res.rowcount:
        raise LMSValidationError("Question generation is already running for this assessment.")

    def work():
        try:
            result = generate_questions(assessment_id, count)
            a = get_db().query(ScenarioAssessment).get(assessment_id)
            a.generation_status = "idle"
            msg = None
            if len(result["created"]) < result["requested"]:
                msg = (
                    f"Generated {len(result['created'])} of {result['requested']} questions; "
                    f"{len(result['rejected'])} candidates failed quality checks."
                )
            a.generation_error = msg
            get_db().commit()
        except Exception as exc:  # noqa: BLE001
            get_db().rollback()
            a = get_db().query(ScenarioAssessment).get(assessment_id)
            a.generation_status = "failed"
            a.generation_error = str(exc)[:2000]
            get_db().commit()
            logger.warning("SBA generation failed for %s: %s", assessment_id, exc)

    if os.getenv("SBA_SYNC_PROCESSING", "").lower() in ("1", "true", "yes"):
        work()
        return
    app_obj = current_app._get_current_object()

    def run():
        with app_obj.app_context():
            work()

    threading.Thread(target=run, name=f"sba-gen-{assessment_id}", daemon=True).start()
