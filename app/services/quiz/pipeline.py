"""End-to-end PDF → MCQ quiz pipeline."""
from __future__ import annotations

import json
import logging
from typing import Optional

from app.models.lms_models import AssessmentQuestion, QuizPdfSource
from app.services.lms import assessment_service, question_bank_service
from app.services.quiz.mcq_converter import convert_pairs_batch, mcq_to_question_fields
from app.services.quiz.section_topics import (
    parse_section_topics,
    question_number_from_label,
    topic_for_question_number,
)
from app.services.quiz.models import PDFExtractionResult
from app.services.quiz.pdf_extractor import extract_qa_from_text, pair_questions_answers
from app.services.quiz.thread_text import get_thread_full_text
from app.utils.db import get_db

logger = logging.getLogger(__name__)


def _set_pdf_source_status(
    source_id: int,
    status: str,
    error_message: Optional[str] = None,
    overall_confidence: Optional[float] = None,
) -> None:
    db = get_db()
    source = db.query(QuizPdfSource).filter(QuizPdfSource.id == source_id).first()
    if not source:
        return
    source.extraction_status = status
    source.error_message = error_message
    if overall_confidence is not None:
        source.overall_confidence = overall_confidence
    db.commit()


def run_pdf_quiz_pipeline(
    assessment_id: int,
    rag_thread_id: str,
    user_id: int,
    topic_id: Optional[int] = None,
    pdf_text: Optional[str] = None,
) -> dict:
    """
    Extract Q&A from PDF text, convert to MCQs, save to question bank, link assessment.

    Returns summary dict with counts and confidence.
    """
    assessment = assessment_service.get_assessment(assessment_id)
    source = assessment_service.link_pdf_source(
        assessment_id,
        rag_thread_id=rag_thread_id,
        original_filename=getattr(assessment, "title", None),
    )
    _set_pdf_source_status(source.id, "processing")

    try:
        text = pdf_text or get_thread_full_text(rag_thread_id, user_id)
        if not text.strip():
            raise ValueError("No PDF text found for thread — ingest PDF first")

        extraction: PDFExtractionResult = extract_qa_from_text(text)
        pairs = pair_questions_answers(extraction)
        if not pairs:
            detail = (
                f"Extracted {len(extraction.questions)} questions and "
                f"{len(extraction.answers)} answers but could not match any pairs."
            )
            if extraction.warnings:
                detail += " Warnings: " + "; ".join(extraction.warnings[:5])
            raise ValueError(detail)

        batch = convert_pairs_batch(pairs, quiz_title=extraction.title or assessment.title)

        sections = parse_section_topics(text)
        question_pdf_topics: dict[str, str] = {}
        question_concepts: dict[str, str] = {}

        db = get_db()
        # Clear existing assessment questions on re-process
        db.query(AssessmentQuestion).filter(
            AssessmentQuestion.assessment_id == assessment_id
        ).delete()
        db.commit()

        created_ids = []
        confidences = []
        for idx, mcq in enumerate(batch.questions):
            fields = mcq_to_question_fields(mcq)
            q = question_bank_service.create_question(
                created_by=user_id,
                topic_id=topic_id,
                source_type="pdf_qa_converted",
                source_pdf_thread_id=rag_thread_id,
                source_question_number=idx + 1,
                correct_answer_raw=pairs[idx].answer_text if idx < len(pairs) else None,
                **fields,
            )
            created_ids.append(q.id)
            confidences.append(fields.get("extraction_confidence") or 0.85)
            qnum = question_number_from_label(
                pairs[idx].question_number if idx < len(pairs) else idx + 1
            )
            section_topic = topic_for_question_number(qnum, sections) if sections else None
            if section_topic:
                question_pdf_topics[str(q.id)] = section_topic
                question_concepts[str(q.id)] = section_topic

        assessment_service.add_questions(assessment_id, created_ids)

        if pairs and not created_ids:
            detail = "MCQ conversion failed for all extracted questions."
            if batch.failed_conversions:
                detail += " " + "; ".join(batch.failed_conversions[:4])
            raise ValueError(detail)

        overall = (
            sum(confidences) / len(confidences) * 0.5 + extraction.confidence * 0.5
            if confidences
            else extraction.confidence
        )

        raw_json = json.dumps(
            {
                "extraction": extraction.model_dump(),
                "pair_count": len(pairs),
                "converted_count": len(batch.questions),
                "failed_conversions": batch.failed_conversions,
            },
            ensure_ascii=False,
        )
        warnings = list(extraction.warnings) + batch.failed_conversions

        assessment_service.save_pdf_extraction(
            source.id,
            raw_json=raw_json,
            pair_count=len(pairs),
            warnings_json=json.dumps(warnings, ensure_ascii=False) if warnings else None,
            overall_confidence=overall,
        )

        assessment = assessment_service.get_assessment(assessment_id)
        assessment.creation_mode = "pdf_qa_auto"
        if extraction.title and assessment.title.startswith("Untitled"):
            assessment.title = extraction.title
        if question_pdf_topics:
            meta = {}
            if assessment.description:
                try:
                    meta = json.loads(assessment.description)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
                if not isinstance(meta, dict):
                    meta = {}
            meta["question_pdf_topics"] = question_pdf_topics
            meta["question_concepts"] = question_concepts
            assessment.description = json.dumps(meta, ensure_ascii=False)
        db.commit()

        if assessment.assessment_type == "diagnostic":
            try:
                from app.services.lms.diagnostic_timer_service import estimate_question_times
                estimate_question_times(assessment_id)
            except Exception as timer_exc:
                logger.warning("Diagnostic timer estimation failed: %s", timer_exc)

        return {
            "assessment_id": assessment_id,
            "source_id": source.id,
            "thread_id": rag_thread_id,
            "pair_count": len(pairs),
            "question_count": len(created_ids),
            "failed_conversions": batch.failed_conversions,
            "overall_confidence": round(overall, 3),
            "requires_review": overall < 0.85,
            "extraction_status": "completed",
        }
    except Exception as exc:
        logger.exception("PDF quiz pipeline failed for assessment %s", assessment_id)
        _set_pdf_source_status(source.id, "failed", error_message=str(exc))
        raise
