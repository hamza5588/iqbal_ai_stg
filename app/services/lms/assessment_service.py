"""Assessment and quiz service."""
from __future__ import annotations

from typing import List, Optional

from app.models.lms_models import Assessment, AssessmentQuestion, PdfQaExtraction, QuizPdfSource
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError
from app.utils.db import get_db

CONFIDENCE_PUBLISH_MIN = 0.60


def create_assessment(
    created_by: int,
    title: str,
    assessment_type: str,
    description: Optional[str] = None,
    creation_mode: str = "manual",
    time_limit_minutes: Optional[int] = None,
) -> Assessment:
    if assessment_type not in ("diagnostic", "quiz"):
        raise LMSValidationError("assessment_type must be diagnostic or quiz")
    db = get_db()
    assessment = Assessment(
        title=title,
        description=description,
        assessment_type=assessment_type,
        creation_mode=creation_mode,
        created_by=created_by,
        status="draft",
        time_limit_minutes=time_limit_minutes,
    )
    db.add(assessment)
    db.commit()
    db.refresh(assessment)
    return assessment


def get_assessment(assessment_id: int) -> Assessment:
    db = get_db()
    a = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not a:
        raise LMSNotFoundError(f"Assessment {assessment_id} not found")
    return a


def add_questions(assessment_id: int, question_ids: List[int]) -> Assessment:
    db = get_db()
    assessment = get_assessment(assessment_id)
    existing = {aq.question_id for aq in assessment.questions}
    sort_order = len(assessment.questions)
    for qid in question_ids:
        if qid in existing:
            continue
        db.add(
            AssessmentQuestion(
                assessment_id=assessment_id,
                question_id=qid,
                sort_order=sort_order,
            )
        )
        sort_order += 1
    db.commit()
    db.refresh(assessment)
    return assessment


def reorder_questions(assessment_id: int, question_ids_in_order: List[int]) -> Assessment:
    db = get_db()
    assessment = get_assessment(assessment_id)
    by_qid = {aq.question_id: aq for aq in assessment.questions}
    for idx, qid in enumerate(question_ids_in_order):
        if qid in by_qid:
            by_qid[qid].sort_order = idx
    db.commit()
    db.refresh(assessment)
    return assessment


def remove_question(assessment_id: int, question_id: int) -> Assessment:
    db = get_db()
    assessment = get_assessment(assessment_id)
    for aq in list(assessment.questions):
        if aq.question_id == question_id:
            db.delete(aq)
    db.commit()
    db.refresh(assessment)
    return assessment


def link_pdf_source(
    assessment_id: int,
    rag_thread_id: Optional[str],
    original_filename: Optional[str] = None,
) -> QuizPdfSource:
    db = get_db()
    get_assessment(assessment_id)
    source = (
        db.query(QuizPdfSource).filter(QuizPdfSource.assessment_id == assessment_id).first()
    )
    if not source:
        source = QuizPdfSource(assessment_id=assessment_id)
        db.add(source)
    source.rag_thread_id = rag_thread_id
    source.original_filename = original_filename
    source.extraction_status = "pending"
    db.commit()
    db.refresh(source)
    return source


def save_pdf_extraction(
    quiz_pdf_source_id: int,
    raw_json: str,
    pair_count: int,
    warnings_json: Optional[str] = None,
    overall_confidence: Optional[float] = None,
) -> PdfQaExtraction:
    db = get_db()
    extraction = PdfQaExtraction(
        quiz_pdf_source_id=quiz_pdf_source_id,
        raw_extraction_json=raw_json,
        pair_count=pair_count,
        warnings_json=warnings_json,
    )
    db.add(extraction)
    source = db.query(QuizPdfSource).filter(QuizPdfSource.id == quiz_pdf_source_id).first()
    if source:
        source.extraction_status = "completed"
        if overall_confidence is not None:
            source.overall_confidence = overall_confidence
        assessment = get_assessment(source.assessment_id)
        assessment.overall_confidence = overall_confidence
        if overall_confidence is not None and overall_confidence < 0.85:
            assessment.requires_review = True
    db.commit()
    db.refresh(extraction)
    return extraction


def can_publish(assessment_id: int) -> tuple[bool, Optional[str]]:
    assessment = get_assessment(assessment_id)
    if not assessment.questions:
        return False, "Assessment has no questions"
    if assessment.creation_mode == "pdf_qa_auto" and assessment.overall_confidence is not None:
        if assessment.overall_confidence < CONFIDENCE_PUBLISH_MIN:
            return False, "Confidence too low — manual review required before publish"
    return True, None


def publish_assessment(assessment_id: int) -> Assessment:
    ok, reason = can_publish(assessment_id)
    if not ok:
        raise LMSValidationError(reason or "Cannot publish assessment")
    db = get_db()
    assessment = get_assessment(assessment_id)
    assessment.status = "published"
    db.commit()
    db.refresh(assessment)
    return assessment


def list_assessments_by_teacher(
    teacher_id: int,
    assessment_type: Optional[str] = None,
) -> List[Assessment]:
    db = get_db()
    q = db.query(Assessment).filter(Assessment.created_by == teacher_id)
    if assessment_type:
        q = q.filter(Assessment.assessment_type == assessment_type)
    return q.order_by(Assessment.updated_at.desc()).all()


def get_assessment_with_questions(assessment_id: int) -> dict:
    assessment = get_assessment(assessment_id)
    return {
        "id": assessment.id,
        "title": assessment.title,
        "description": assessment.description,
        "assessment_type": assessment.assessment_type,
        "creation_mode": assessment.creation_mode,
        "status": assessment.status,
        "time_limit_minutes": assessment.time_limit_minutes,
        "overall_confidence": assessment.overall_confidence,
        "requires_review": assessment.requires_review,
        "questions": [
            {
                "sort_order": aq.sort_order,
                "question_id": aq.question_id,
            }
            for aq in sorted(assessment.questions, key=lambda x: x.sort_order)
        ],
    }
