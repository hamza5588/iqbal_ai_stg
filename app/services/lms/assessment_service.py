"""Assessment and quiz service."""
from __future__ import annotations

import logging
from typing import List, Optional

from app.models.lms_models import Assessment, AssessmentQuestion, DiagnosticTargetPdf, PdfQaExtraction, QuizPdfSource
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError
from app.utils.db import get_db

CONFIDENCE_PUBLISH_MIN = 0.60
logger = logging.getLogger(__name__)


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


def add_target_pdf(
    assessment_id: int,
    target_rag_thread_id: str,
    target_original_filename: Optional[str] = None,
    sort_order: Optional[int] = None,
) -> DiagnosticTargetPdf:
    """Add a target content PDF to a diagnostic (supports multiple files)."""
    db = get_db()
    get_assessment(assessment_id)
    if sort_order is None:
        existing = (
            db.query(DiagnosticTargetPdf)
            .filter(DiagnosticTargetPdf.assessment_id == assessment_id)
            .count()
        )
        sort_order = existing
    entry = DiagnosticTargetPdf(
        assessment_id=assessment_id,
        rag_thread_id=target_rag_thread_id,
        original_filename=target_original_filename,
        sort_order=sort_order,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    # Keep legacy single-column fields in sync with first target PDF only
    if sort_order == 0:
        link_target_pdf(assessment_id, target_rag_thread_id, target_original_filename)
    return entry


def list_target_pdfs(assessment_id: int) -> List[dict]:
    db = get_db()
    rows = (
        db.query(DiagnosticTargetPdf)
        .filter(DiagnosticTargetPdf.assessment_id == assessment_id)
        .order_by(DiagnosticTargetPdf.sort_order, DiagnosticTargetPdf.id)
        .all()
    )
    return [
        {
            "id": r.id,
            "rag_thread_id": r.rag_thread_id,
            "original_filename": r.original_filename,
            "sort_order": r.sort_order,
        }
        for r in rows
    ]


def remove_target_pdf(assessment_id: int, target_pdf_id: int) -> None:
    db = get_db()
    row = (
        db.query(DiagnosticTargetPdf)
        .filter(
            DiagnosticTargetPdf.id == target_pdf_id,
            DiagnosticTargetPdf.assessment_id == assessment_id,
        )
        .first()
    )
    if not row:
        raise LMSNotFoundError("Target PDF not found")
    db.delete(row)
    db.commit()
    # Refresh legacy column from remaining targets
    remaining = list_target_pdfs(assessment_id)
    src = db.query(QuizPdfSource).filter(QuizPdfSource.assessment_id == assessment_id).first()
    if src:
        if remaining:
            src.target_rag_thread_id = remaining[0]["rag_thread_id"]
            src.target_original_filename = remaining[0]["original_filename"]
        else:
            src.target_rag_thread_id = None
            src.target_original_filename = None
        db.commit()


def link_target_pdf(
    assessment_id: int,
    target_rag_thread_id: str,
    target_original_filename: Optional[str] = None,
) -> QuizPdfSource:
    db = get_db()
    get_assessment(assessment_id)
    source = (
        db.query(QuizPdfSource).filter(QuizPdfSource.assessment_id == assessment_id).first()
    )
    if not source:
        source = QuizPdfSource(assessment_id=assessment_id)
        db.add(source)
    source.target_rag_thread_id = target_rag_thread_id
    source.target_original_filename = target_original_filename
    db.commit()
    db.refresh(source)
    return source


def has_target_pdf(assessment_id: int) -> bool:
    db = get_db()
    count = (
        db.query(DiagnosticTargetPdf)
        .filter(DiagnosticTargetPdf.assessment_id == assessment_id)
        .count()
    )
    if count > 0:
        return True
    assessment = get_assessment(assessment_id)
    src = assessment.pdf_source
    return bool(src and src.target_rag_thread_id)


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
    if assessment.assessment_type == "diagnostic" and not has_target_pdf(assessment_id):
        return False, "Upload the target content PDF before publishing (used for Learning Chat)"
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

    if assessment.assessment_type == "diagnostic":
        from app.services.lms.diagnostic_timer_service import estimate_question_times

        try:
            estimate_question_times(assessment_id)
        except Exception as exc:
            logger.warning("Timer estimation on publish failed: %s", exc)
        # Archive other published diagnostics (only one active platform diagnostic)
        others = (
            db.query(Assessment)
            .filter(
                Assessment.assessment_type == "diagnostic",
                Assessment.status == "published",
                Assessment.id != assessment_id,
            )
            .all()
        )
        for other in others:
            other.status = "archived"

    assessment.status = "published"
    db.commit()
    db.refresh(assessment)
    return assessment


def archive_diagnostic(assessment_id: int) -> Assessment:
    """Archive a diagnostic so admin can upload a new one."""
    db = get_db()
    assessment = get_assessment(assessment_id)
    if assessment.assessment_type != "diagnostic":
        raise LMSValidationError("Not a diagnostic assessment")
    assessment.status = "archived"
    db.commit()
    db.refresh(assessment)
    return assessment


def get_active_platform_diagnostic() -> Optional[Assessment]:
    """Return the single published platform diagnostic."""
    db = get_db()
    return (
        db.query(Assessment)
        .filter(
            Assessment.assessment_type == "diagnostic",
            Assessment.status == "published",
        )
        .order_by(Assessment.updated_at.desc(), Assessment.id.desc())
        .first()
    )


def list_assessments_by_teacher(
    teacher_id: int,
    assessment_type: Optional[str] = None,
) -> List[Assessment]:
    db = get_db()
    q = db.query(Assessment).filter(Assessment.created_by == teacher_id)
    if assessment_type:
        q = q.filter(Assessment.assessment_type == assessment_type)
    return q.order_by(Assessment.updated_at.desc()).all()


def get_assessment_with_questions(assessment_id: int, include_answers: bool = False) -> dict:
    from app.services.lms import question_bank_service

    assessment = get_assessment(assessment_id)
    questions_out = []
    for aq in sorted(assessment.questions, key=lambda x: x.sort_order):
        item = {"sort_order": aq.sort_order, "question_id": aq.question_id}
        if include_answers:
            try:
                q = question_bank_service.get_question(aq.question_id)
                item["question"] = question_bank_service.question_to_dict(q)
            except LMSNotFoundError:
                pass
        questions_out.append(item)

    pdf_status = None
    if assessment.pdf_source:
        src = assessment.pdf_source
        pdf_status = {
            "source_id": src.id,
            "rag_thread_id": src.rag_thread_id,
            "original_filename": src.original_filename,
            "extraction_status": src.extraction_status,
            "overall_confidence": src.overall_confidence,
            "error_message": src.error_message,
        }

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
        "pdf_source": pdf_status,
        "questions": questions_out,
    }


def get_pdf_processing_status(assessment_id: int) -> dict:
    assessment = get_assessment(assessment_id)
    if not assessment.pdf_source:
        return {
            "assessment_id": assessment_id,
            "extraction_status": "pending" if assessment.creation_mode == "pdf_qa_auto" else "none",
        }
    src = assessment.pdf_source
    return {
        "assessment_id": assessment_id,
        "source_id": src.id,
        "extraction_status": src.extraction_status,
        "overall_confidence": src.overall_confidence,
        "error_message": src.error_message,
        "question_count": len(assessment.questions),
        "requires_review": assessment.requires_review,
    }


def finalize_pdf_quiz(source_id: int, teacher_id: int) -> Assessment:
    """Mark PDF-generated quiz ready for publish after pipeline completes."""
    db = get_db()
    source = db.query(QuizPdfSource).filter(QuizPdfSource.id == source_id).first()
    if not source:
        raise LMSNotFoundError(f"PDF source {source_id} not found")
    assessment = get_assessment(source.assessment_id)
    if assessment.created_by != teacher_id:
        raise LMSValidationError("Not authorized")
    if source.extraction_status != "completed":
        raise LMSValidationError("PDF extraction not completed")
    if not assessment.questions:
        raise LMSValidationError("No questions attached")
    assessment.creation_mode = "pdf_qa_auto"
    db.commit()
    db.refresh(assessment)
    return assessment
