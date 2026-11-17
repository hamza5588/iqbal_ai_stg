"""Scenario-Based Assessment orchestration: authorization, CRUD, submissions, review.

Authorization model (no multi-tenant layer exists in IqbalAI; the boundary is the class):
* A teacher manages only assessments they created (admins manage all).
* An assessment is linked to one class the teacher owns.
* A student sees an assessment only when it is published AND they are actively enrolled
  in its class; they see only APPROVED questions and only their own submissions.
* Reference-answer documents, evaluation criteria, retrieved excerpts and review reasons
  are teacher-only and never appear in student payloads.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.exc import IntegrityError

from app.models.database_models import User
from app.models.lms_models import SchoolClass
from app.models.scenario_models import (
    ScenarioAssessment,
    ScenarioChunk,
    ScenarioDocument,
    ScenarioEvaluation,
    ScenarioQuestion,
    ScenarioSampleQuestion,
    ScenarioSubmission,
)
from app.services.lms import class_service
from app.services.lms.exceptions import LMSNotFoundError, LMSPermissionError, LMSValidationError
from app.services.scenario import documents
from app.services.scenario.common import DEFAULT_RUBRIC, clean_list, dumps, loads, text_key
from app.services.scenario.evaluation import normalize_rubric
from app.utils.db import get_db

MAX_TITLE = 255


# ── access ─────────────────────────────────────────────────────────────────────

def get_assessment(assessment_id: int) -> ScenarioAssessment:
    a = get_db().query(ScenarioAssessment).filter(ScenarioAssessment.id == assessment_id).first()
    if a is None:
        raise LMSNotFoundError("Assessment not found")
    return a


def require_manage(assessment_id: int, user_id: int, role: str) -> ScenarioAssessment:
    a = get_assessment(assessment_id)
    if role != "admin" and a.teacher_id != user_id:
        # Same 404 as a missing id: do not reveal other teachers' assessments.
        raise LMSNotFoundError("Assessment not found")
    return a


def require_student_access(assessment_id: int, student_id: int) -> ScenarioAssessment:
    a = get_assessment(assessment_id)
    if a.status != "published" or not a.class_id or not class_service.student_in_class(student_id, a.class_id):
        raise LMSNotFoundError("Assessment not found")
    return a


def _question_for_student(question_id: int, student_id: int) -> ScenarioQuestion:
    q = get_db().query(ScenarioQuestion).filter(ScenarioQuestion.id == question_id).first()
    if q is None or q.status != "approved":
        raise LMSNotFoundError("Question not found")
    require_student_access(q.assessment_id, student_id)
    return q


# ── stale background work ──────────────────────────────────────────────────────

STALE_MINUTES = 15


def recover_stale(assessment_id: int) -> None:
    """Background threads die with their worker (deploy / gunicorn restart). Anything
    left 'processing' / 'running' / 'evaluating' too long becomes retryable 'failed'."""
    from datetime import timedelta

    from sqlalchemy import update

    db = get_db()
    cutoff = datetime.utcnow() - timedelta(minutes=STALE_MINUTES)
    db.execute(
        update(ScenarioDocument)
        .where(ScenarioDocument.assessment_id == assessment_id, ScenarioDocument.status == "processing",
               ScenarioDocument.created_at < cutoff,
               (ScenarioDocument.processed_at.is_(None)) | (ScenarioDocument.processed_at < cutoff))
        .values(status="failed", error_message="Processing was interrupted. Click Retry.")
    )
    db.execute(
        update(ScenarioAssessment)
        .where(ScenarioAssessment.id == assessment_id, ScenarioAssessment.generation_status == "running",
               ScenarioAssessment.updated_at < cutoff)
        .values(generation_status="failed", generation_error="Question generation was interrupted. Generate again.")
    )
    db.execute(
        update(ScenarioSubmission)
        .where(ScenarioSubmission.assessment_id == assessment_id,
               ScenarioSubmission.status.in_(("submitted", "evaluating")), ScenarioSubmission.updated_at < cutoff)
        .values(status="failed", evaluation_error="Evaluation was interrupted; retry it.")
    )
    db.commit()


# ── assessments ────────────────────────────────────────────────────────────────

def _clean_objectives(value) -> List[str]:
    if isinstance(value, str):
        value = [v for v in value.splitlines()]
    return clean_list(value or [], limit=12, max_len=300)


def _apply_config(a: ScenarioAssessment, data: Dict, user_id: int, role: str) -> None:
    if "title" in data:
        title = (data.get("title") or "").strip()
        if not title:
            raise LMSValidationError("Title is required.")
        a.title = title[:MAX_TITLE]
    if "class_id" in data:
        class_id = data.get("class_id")
        if class_id in (None, ""):
            a.class_id = None
        else:
            class_id = int(class_id)
            if role != "admin" and not class_service.teacher_owns_class(user_id, class_id):
                raise LMSValidationError("Choose one of your own classes.")
            a.class_id = class_id
    if "topic" in data:
        a.topic = ((data.get("topic") or "").strip() or None) and data["topic"].strip()[:500]
    if "instructions" in data:
        a.instructions = (data.get("instructions") or "").strip()[:4000] or None
    if "learning_objectives" in data:
        a.learning_objectives_json = dumps(_clean_objectives(data.get("learning_objectives")))
    if "question_count" in data:
        n = int(data.get("question_count") or 5)
        if not 1 <= n <= 20:
            raise LMSValidationError("Question count must be between 1 and 20.")
        a.question_count = n
    if "difficulty" in data:
        if data["difficulty"] not in ("easy", "medium", "hard"):
            raise LMSValidationError("Difficulty must be easy, medium or hard.")
        a.difficulty = data["difficulty"]
    if "max_score_per_question" in data:
        m = int(data.get("max_score_per_question") or 10)
        if not 1 <= m <= 100:
            raise LMSValidationError("Marks per question must be between 1 and 100.")
        a.max_score_per_question = m
    if "release_mode" in data:
        if data["release_mode"] not in ("immediate", "manual"):
            raise LMSValidationError("Release mode must be immediate or manual.")
        a.release_mode = data["release_mode"]
    if "rubric" in data:
        rubric = normalize_rubric(data.get("rubric"))
        if loads(a.rubric_json) != rubric:
            a.rubric_json = dumps(rubric)
            a.rubric_version = int(a.rubric_version or 0) + 1


def create_assessment(user_id: int, role: str, data: Dict) -> ScenarioAssessment:
    a = ScenarioAssessment(
        teacher_id=user_id,
        title="Untitled",
        rubric_json=dumps(normalize_rubric(DEFAULT_RUBRIC)),
        rubric_version=1,
    )
    data = dict(data or {})
    data.setdefault("title", "")
    _apply_config(a, data, user_id, role)
    a.rubric_version = 1
    db = get_db()
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def update_assessment(assessment_id: int, user_id: int, role: str, data: Dict) -> ScenarioAssessment:
    a = require_manage(assessment_id, user_id, role)
    if a.status == "published" and "class_id" in data and data.get("class_id") != a.class_id:
        raise LMSValidationError("Unpublish the assessment before moving it to another class.")
    _apply_config(a, data or {}, a.teacher_id, role)
    get_db().commit()
    return a


def list_teacher_assessments(user_id: int, role: str) -> List[Dict]:
    q = get_db().query(ScenarioAssessment)
    if role != "admin":
        q = q.filter(ScenarioAssessment.teacher_id == user_id)
    out = []
    for a in q.order_by(ScenarioAssessment.created_at.desc()).all():
        out.append(_summary(a))
    return out


def _class_name(class_id: Optional[int]) -> Optional[str]:
    if not class_id:
        return None
    c = get_db().query(SchoolClass).filter(SchoolClass.id == class_id).first()
    return c.name if c else None


def _summary(a: ScenarioAssessment) -> Dict:
    db = get_db()
    qs = a.questions
    subs = db.query(ScenarioSubmission).filter(ScenarioSubmission.assessment_id == a.id)
    pending_review = (
        db.query(ScenarioEvaluation)
        .join(ScenarioSubmission, ScenarioSubmission.id == ScenarioEvaluation.submission_id)
        .filter(
            ScenarioSubmission.assessment_id == a.id,
            ScenarioEvaluation.is_current.is_(True),
            ScenarioEvaluation.review_required.is_(True),
            ScenarioEvaluation.reviewed_at.is_(None),
        )
        .count()
    )
    return {
        "id": a.id,
        "title": a.title,
        "topic": a.topic,
        "class_id": a.class_id,
        "class_name": _class_name(a.class_id),
        "status": a.status,
        "difficulty": a.difficulty,
        "question_count": a.question_count,
        "questions": {s: sum(1 for q in qs if q.status == s) for s in ("draft", "approved", "rejected")},
        "submissions": subs.filter(ScenarioSubmission.status != "draft").count(),
        "pending_review": pending_review,
        "generation_status": a.generation_status,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def teacher_detail(assessment_id: int, user_id: int, role: str) -> Dict:
    a = require_manage(assessment_id, user_id, role)
    recover_stale(a.id)
    db = get_db()
    docs = (
        db.query(ScenarioDocument)
        .filter(ScenarioDocument.assessment_id == a.id)
        .order_by(ScenarioDocument.doc_type, ScenarioDocument.created_at)
        .all()
    )
    samples = (
        db.query(ScenarioSampleQuestion)
        .filter(ScenarioSampleQuestion.assessment_id == a.id, ScenarioSampleQuestion.is_active.is_(True))
        .order_by(ScenarioSampleQuestion.id)
        .all()
    )
    return {
        **_summary(a),
        "instructions": a.instructions,
        "learning_objectives": loads(a.learning_objectives_json, []),
        "max_score_per_question": a.max_score_per_question,
        "rubric": loads(a.rubric_json, []),
        "rubric_version": a.rubric_version,
        "release_mode": a.release_mode,
        "results_released": bool(a.results_released),
        "generation_error": a.generation_error,
        "documents": [documents.serialize_document(d) for d in docs],
        "sample_questions": [
            {"id": s.id, "document_id": s.document_id, "text": s.text, "page": s.page,
             "question_type": s.question_type, "objective": s.objective, "difficulty": s.difficulty}
            for s in samples
        ],
        "items": [serialize_question_teacher(q) for q in a.questions],
    }


def serialize_question_teacher(q: ScenarioQuestion) -> Dict:
    return {
        "id": q.id,
        "sort_order": q.sort_order,
        "scenario": q.scenario,
        "question_text": q.question_text,
        "difficulty": q.difficulty,
        "learning_objectives": loads(q.learning_objectives_json, []),
        "concepts": loads(q.concepts_json, []),
        "source_refs": loads(q.source_refs_json, []),
        "evaluation_criteria": loads(q.evaluation_criteria_json, []),
        "generation_meta": loads(q.generation_meta_json, {}),
        "status": q.status,
        "edited_by_teacher": bool(q.edited_by_teacher),
        "has_submissions": get_db().query(ScenarioSubmission.id)
        .filter(ScenarioSubmission.question_id == q.id, ScenarioSubmission.status != "draft").first() is not None,
    }


def publish(assessment_id: int, user_id: int, role: str) -> ScenarioAssessment:
    a = require_manage(assessment_id, user_id, role)
    if not a.class_id:
        raise LMSValidationError("Choose the class this assessment is for before publishing.")
    if a.generation_status == "running":
        raise LMSValidationError("Wait for question generation to finish.")
    if not any(q.status == "approved" for q in a.questions):
        raise LMSValidationError("Approve at least one question before publishing.")
    a.status = "published"
    a.published_at = a.published_at or datetime.utcnow()
    get_db().commit()
    return a


def unpublish(assessment_id: int, user_id: int, role: str) -> ScenarioAssessment:
    a = require_manage(assessment_id, user_id, role)
    a.status = "draft"
    get_db().commit()
    return a


def release_results(assessment_id: int, user_id: int, role: str) -> ScenarioAssessment:
    a = require_manage(assessment_id, user_id, role)
    a.results_released = True
    get_db().commit()
    return a


# ── documents (teacher) ────────────────────────────────────────────────────────

def upload_document(assessment_id: int, user_id: int, role: str, doc_type: str, filename: str, data: bytes):
    a = require_manage(assessment_id, user_id, role)
    doc = documents.create_document(a.id, doc_type, filename, data, user_id)
    documents.process_in_background(doc.id)
    return doc


def _manage_doc(document_id: int, user_id: int, role: str) -> ScenarioDocument:
    doc = documents.get_document(document_id)
    require_manage(doc.assessment_id, user_id, role)
    return doc


def replace_document(document_id: int, user_id: int, role: str, filename: str, data: bytes):
    old = _manage_doc(document_id, user_id, role)
    if not old.is_active:
        raise LMSValidationError("Only the current version of a document can be replaced.")
    doc = documents.create_document(old.assessment_id, old.doc_type, filename, data, user_id, replaces=old)
    documents.process_in_background(doc.id)
    return doc


def delete_document(document_id: int, user_id: int, role: str) -> None:
    documents.deactivate_document(_manage_doc(document_id, user_id, role))


def reprocess_document(document_id: int, user_id: int, role: str):
    doc = _manage_doc(document_id, user_id, role)
    if not doc.is_active:
        raise LMSValidationError("Old document versions cannot be reprocessed.")
    if doc.status == "processing":
        raise LMSValidationError("This document is already being processed.")
    get_db().query(ScenarioDocument).filter(ScenarioDocument.id == doc.id).update({"status": "pending"})
    get_db().commit()
    documents.process_in_background(doc.id)
    return documents.get_document(doc.id)


def update_sample(sample_id: int, user_id: int, role: str, data: Dict) -> Dict:
    s = get_db().query(ScenarioSampleQuestion).filter(ScenarioSampleQuestion.id == sample_id).first()
    if s is None:
        raise LMSNotFoundError("Sample question not found")
    require_manage(s.assessment_id, user_id, role)
    if data.get("delete"):
        s.is_active = False
    else:
        text = (data.get("text") or "").strip()
        if len(text) < 10:
            raise LMSValidationError("Sample question text is too short.")
        s.text = text[:4000]
        s.dedupe_key = text_key(text)
        for k in ("question_type", "objective"):
            if k in data:
                setattr(s, k, (data.get(k) or "").strip()[:500] or None)
    get_db().commit()
    return {"id": s.id, "is_active": s.is_active}


# ── questions (teacher) ────────────────────────────────────────────────────────

def _manage_question(question_id: int, user_id: int, role: str) -> ScenarioQuestion:
    q = get_db().query(ScenarioQuestion).filter(ScenarioQuestion.id == question_id).first()
    if q is None:
        raise LMSNotFoundError("Question not found")
    require_manage(q.assessment_id, user_id, role)
    return q


def update_question(question_id: int, user_id: int, role: str, data: Dict) -> ScenarioQuestion:
    q = _manage_question(question_id, user_id, role)
    has_subs = serialize_question_teacher(q)["has_submissions"]
    text_change = any(k in data for k in ("scenario", "question_text", "evaluation_criteria"))
    if has_subs and text_change:
        raise LMSValidationError("Students have already answered this question; its wording can no longer change.")
    if "scenario" in data:
        v = (data.get("scenario") or "").strip()
        if len(v) < 20:
            raise LMSValidationError("Scenario is too short.")
        q.scenario = v[:6000]
    if "question_text" in data:
        v = (data.get("question_text") or "").strip()
        if len(v) < 10:
            raise LMSValidationError("Question text is too short.")
        q.question_text = v[:2000]
    if "evaluation_criteria" in data:
        q.evaluation_criteria_json = dumps(_clean_objectives(data.get("evaluation_criteria")))
    if "difficulty" in data and data["difficulty"] in ("easy", "medium", "hard"):
        q.difficulty = data["difficulty"]
    if text_change:
        q.edited_by_teacher = True
    if "status" in data:
        if data["status"] not in ("draft", "approved", "rejected"):
            raise LMSValidationError("Invalid question status.")
        if has_subs and data["status"] != "approved":
            raise LMSValidationError("Students have already answered this question; it must stay approved.")
        q.status = data["status"]
    get_db().commit()
    return q


def regenerate(question_id: int, user_id: int, role: str) -> ScenarioQuestion:
    from app.services.scenario.generation import regenerate_question

    q = _manage_question(question_id, user_id, role)
    if serialize_question_teacher(q)["has_submissions"]:
        raise LMSValidationError("Students have already answered this question; it cannot be regenerated.")
    return regenerate_question(q.id)


def start_generation(assessment_id: int, user_id: int, role: str, count: Optional[int] = None) -> None:
    from app.services.scenario.generation import generate_in_background

    a = require_manage(assessment_id, user_id, role)
    ready = (
        get_db()
        .query(ScenarioDocument)
        .filter(ScenarioDocument.assessment_id == a.id, ScenarioDocument.doc_type == "learning",
                ScenarioDocument.is_active.is_(True), ScenarioDocument.status == "ready")
        .count()
    )
    if not ready:
        raise LMSValidationError("Upload at least one learning-content PDF and wait until it is processed.")
    generate_in_background(a.id, count)


# ── student side ───────────────────────────────────────────────────────────────

def _visible_result(a: ScenarioAssessment, ev: Optional[ScenarioEvaluation]) -> Optional[Dict]:
    if ev is None:
        return None
    if a.release_mode == "manual" and not a.results_released:
        return {"state": "awaiting_release"}
    if ev.review_required and ev.reviewed_at is None:
        return {"state": "under_review"}
    result = loads(ev.result_json, {})
    score = ev.teacher_score if ev.teacher_score is not None else ev.total_score
    return {
        "state": "released",
        "score": round(score, 2),
        "max_score": ev.max_score,
        "teacher_adjusted": ev.teacher_score is not None,
        "teacher_feedback": ev.teacher_feedback,
        "criteria": [
            {k: c.get(k) for k in ("label", "weight", "level", "score", "max", "justification")}
            for c in loads(ev.criteria_json, [])
        ],
        "correct_concepts": result.get("correct_concepts", []),
        "missing_concepts": result.get("missing_concepts", []),
        "incorrect_claims": result.get("incorrect_claims", []),
        "reasoning_feedback": result.get("reasoning_feedback", ""),
        "suggested_improvements": result.get("suggested_improvements", []),
        # Students only see learning-content page references, never reference-answer material.
        "references": [
            {"page_start": r.get("page_start"), "page_end": r.get("page_end"), "heading": r.get("heading")}
            for r in result.get("supporting_references", [])
            if r.get("doc_type") == "learning"
        ],
    }


def _current_eval(submission_id: int) -> Optional[ScenarioEvaluation]:
    return (
        get_db()
        .query(ScenarioEvaluation)
        .filter(ScenarioEvaluation.submission_id == submission_id, ScenarioEvaluation.is_current.is_(True))
        .order_by(ScenarioEvaluation.id.desc())
        .first()
    )


def list_student_assessments(student_id: int) -> List[Dict]:
    db = get_db()
    class_ids = [c.id for c in class_service.list_student_classes(student_id)]
    if not class_ids:
        return []
    rows = (
        db.query(ScenarioAssessment)
        .filter(ScenarioAssessment.class_id.in_(class_ids), ScenarioAssessment.status == "published")
        .order_by(ScenarioAssessment.published_at.desc())
        .all()
    )
    out = []
    for a in rows:
        approved = [q for q in a.questions if q.status == "approved"]
        subs = (
            db.query(ScenarioSubmission)
            .filter(ScenarioSubmission.assessment_id == a.id, ScenarioSubmission.student_id == student_id)
            .all()
        )
        submitted = sum(1 for s in subs if s.status != "draft" and s.question_id in {q.id for q in approved})
        out.append({
            "id": a.id, "title": a.title, "topic": a.topic, "class_name": _class_name(a.class_id),
            "question_count": len(approved), "submitted_count": submitted,
            "published_at": a.published_at.isoformat() if a.published_at else None,
        })
    return out


def student_detail(assessment_id: int, student_id: int) -> Dict:
    a = require_student_access(assessment_id, student_id)
    recover_stale(a.id)
    db = get_db()
    items = []
    for q in a.questions:
        if q.status != "approved":
            continue
        sub = (
            db.query(ScenarioSubmission)
            .filter(ScenarioSubmission.question_id == q.id, ScenarioSubmission.student_id == student_id)
            .first()
        )
        items.append({
            "id": q.id,
            "scenario": q.scenario,
            "question_text": q.question_text,
            "difficulty": q.difficulty,
            "max_score": a.max_score_per_question,
            "submission": None if sub is None else {
                "id": sub.id,
                "status": sub.status,
                "answer_text": sub.answer_text,
                "submitted_at": sub.submitted_at.isoformat() if sub.submitted_at else None,
                "evaluation_failed": sub.status == "failed",
                "result": _visible_result(a, _current_eval(sub.id)) if sub.status == "evaluated" else None,
            },
        })
    return {
        "id": a.id, "title": a.title, "topic": a.topic, "instructions": a.instructions,
        "class_name": _class_name(a.class_id),
        "rubric": [{"label": c["label"], "weight": c["weight"]} for c in loads(a.rubric_json, [])],
        "items": items,
    }


def _get_or_create_submission(q: ScenarioQuestion, student_id: int) -> ScenarioSubmission:
    db = get_db()
    sub = (
        db.query(ScenarioSubmission)
        .filter(ScenarioSubmission.question_id == q.id, ScenarioSubmission.student_id == student_id)
        .first()
    )
    if sub is None:
        sub = ScenarioSubmission(assessment_id=q.assessment_id, question_id=q.id, student_id=student_id,
                                 answer_text="", status="draft")
        db.add(sub)
        try:
            db.commit()
        except IntegrityError:  # concurrent first save
            db.rollback()
            sub = (
                db.query(ScenarioSubmission)
                .filter(ScenarioSubmission.question_id == q.id, ScenarioSubmission.student_id == student_id)
                .first()
            )
    return sub


def save_draft(question_id: int, student_id: int, answer_text: str) -> ScenarioSubmission:
    from app.services.scenario.evaluation import MAX_ANSWER_CHARS

    q = _question_for_student(question_id, student_id)
    text = answer_text or ""
    if len(text) > MAX_ANSWER_CHARS:
        raise LMSValidationError(f"Answers are limited to {MAX_ANSWER_CHARS} characters.")
    sub = _get_or_create_submission(q, student_id)
    if sub.status != "draft":
        raise LMSValidationError("This answer has already been submitted.")
    sub.answer_text = text
    get_db().commit()
    return sub


def submit_answer(question_id: int, student_id: int, answer_text: Optional[str]) -> ScenarioSubmission:
    from sqlalchemy import update

    from app.services.scenario.evaluation import evaluate_in_background

    sub = save_draft(question_id, student_id, answer_text) if answer_text is not None else None
    if sub is None:
        q = _question_for_student(question_id, student_id)
        sub = _get_or_create_submission(q, student_id)
    db = get_db()
    # Atomic draft -> submitted: a double click / two tabs cannot submit twice.
    res = db.execute(
        update(ScenarioSubmission)
        .where(ScenarioSubmission.id == sub.id, ScenarioSubmission.status == "draft")
        .values(status="submitted", submitted_at=datetime.utcnow())
    )
    db.commit()
    if not res.rowcount:
        raise LMSValidationError("This answer has already been submitted.")
    evaluate_in_background(sub.id)
    db.expire_all()
    return db.query(ScenarioSubmission).get(sub.id)


# ── teacher review ─────────────────────────────────────────────────────────────

def _student_name(student_id: int) -> str:
    u = get_db().query(User).filter(User.id == student_id).first()
    return (u.full_name or u.username) if u else f"Student #{student_id}"


def list_submissions(assessment_id: int, user_id: int, role: str) -> List[Dict]:
    a = require_manage(assessment_id, user_id, role)
    recover_stale(a.id)
    db = get_db()
    subs = (
        db.query(ScenarioSubmission)
        .filter(ScenarioSubmission.assessment_id == a.id, ScenarioSubmission.status != "draft")
        .order_by(ScenarioSubmission.submitted_at.desc())
        .all()
    )
    order = {q.id: i + 1 for i, q in enumerate(q for q in a.questions if q.status == "approved")}
    out = []
    for s in subs:
        ev = _current_eval(s.id)
        out.append({
            "id": s.id,
            "student_id": s.student_id,
            "student_name": _student_name(s.student_id),
            "question_id": s.question_id,
            "question_no": order.get(s.question_id),
            "status": s.status,
            "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
            "ai_score": ev.total_score if ev else None,
            "teacher_score": ev.teacher_score if ev else None,
            "max_score": ev.max_score if ev else a.max_score_per_question,
            "review_required": bool(ev and ev.review_required and ev.reviewed_at is None),
            "reviewed": bool(ev and ev.reviewed_at),
            "error": s.evaluation_error,
        })
    return out


def submission_detail(submission_id: int, user_id: int, role: str) -> Dict:
    db = get_db()
    s = db.query(ScenarioSubmission).filter(ScenarioSubmission.id == submission_id).first()
    if s is None:
        raise LMSNotFoundError("Submission not found")
    a = require_manage(s.assessment_id, user_id, role)
    q = db.query(ScenarioQuestion).get(s.question_id)
    history = (
        db.query(ScenarioEvaluation)
        .filter(ScenarioEvaluation.submission_id == s.id)
        .order_by(ScenarioEvaluation.id.desc())
        .all()
    )
    chunk_ids = set()
    for ev in history[:1]:
        for r in loads(ev.result_json, {}).get("supporting_references", []):
            chunk_ids.add(r.get("chunk_id"))
    excerpts = {
        c.id: c.text[:1500]
        for c in db.query(ScenarioChunk).filter(ScenarioChunk.id.in_(chunk_ids or {-1})).all()
    }
    return {
        "id": s.id,
        "assessment_id": a.id,
        "student_name": _student_name(s.student_id),
        "status": s.status,
        "error": s.evaluation_error,
        "answer_text": s.answer_text,
        "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
        "question": serialize_question_teacher(q),
        "evaluations": [
            {
                "id": ev.id,
                "is_current": bool(ev.is_current),
                "total_score": ev.total_score,
                "max_score": ev.max_score,
                "criteria": loads(ev.criteria_json, []),
                "result": {
                    **loads(ev.result_json, {}),
                    "supporting_references": [
                        {**r, "excerpt": excerpts.get(r.get("chunk_id"))}
                        for r in loads(ev.result_json, {}).get("supporting_references", [])
                    ],
                },
                "review_required": bool(ev.review_required),
                "review_reasons": loads(ev.review_reasons_json, []),
                "rubric_snapshot": loads(ev.rubric_snapshot_json, {}),
                "doc_versions": loads(ev.doc_versions_json, []),
                "prompt_version": ev.prompt_version,
                "model_name": ev.model_name,
                "teacher_score": ev.teacher_score,
                "teacher_feedback": ev.teacher_feedback,
                "reviewed_at": ev.reviewed_at.isoformat() if ev.reviewed_at else None,
                "created_at": ev.created_at.isoformat() if ev.created_at else None,
            }
            for ev in history
        ],
    }


def review_submission(submission_id: int, user_id: int, role: str, data: Dict) -> Dict:
    db = get_db()
    s = db.query(ScenarioSubmission).filter(ScenarioSubmission.id == submission_id).first()
    if s is None:
        raise LMSNotFoundError("Submission not found")
    require_manage(s.assessment_id, user_id, role)
    ev = _current_eval(s.id)
    if ev is None:
        raise LMSValidationError("There is no AI evaluation to review yet.")
    score = data.get("teacher_score")
    if score in ("", None):
        ev.teacher_score = None
    else:
        try:
            score = float(score)
        except (TypeError, ValueError):
            raise LMSValidationError("Score must be a number.") from None
        if not 0 <= score <= ev.max_score:
            raise LMSValidationError(f"Score must be between 0 and {ev.max_score:g}.")
        ev.teacher_score = round(score, 2)
    ev.teacher_feedback = (data.get("teacher_feedback") or "").strip()[:4000] or None
    ev.reviewed_by = user_id
    ev.reviewed_at = datetime.utcnow()
    db.commit()
    return {"id": ev.id, "teacher_score": ev.teacher_score, "reviewed_at": ev.reviewed_at.isoformat()}


def retry_evaluation(submission_id: int, user_id: int, role: str) -> Dict:
    from app.services.scenario.evaluation import evaluate_in_background

    s = get_db().query(ScenarioSubmission).filter(ScenarioSubmission.id == submission_id).first()
    if s is None:
        raise LMSNotFoundError("Submission not found")
    require_manage(s.assessment_id, user_id, role)
    if s.status in ("draft", "evaluating"):
        raise LMSValidationError("This submission cannot be re-evaluated right now.")
    if not evaluate_in_background(s.id):
        raise LMSValidationError("Evaluation is already running.")
    return {"id": s.id, "status": "evaluating"}
