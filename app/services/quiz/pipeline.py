"""End-to-end PDF → MCQ quiz pipeline."""
from __future__ import annotations

import json
import logging
from typing import List, Optional

from app.models.lms_models import AssessmentQuestion, QuizPdfSource
from app.services.lms import assessment_service, question_bank_service
from app.services.quiz.assessment_doc_validation import (
    AssessmentDocValidationError,
    assert_nonempty_extracted_text,
    assert_topic_or_key_area_available,
    assert_valid_extraction,
    assert_valid_mcqs,
    assert_valid_pairs,
    public_error_message,
)
from app.services.quiz.diagnostic_generator import generate_mcqs_from_content
from app.services.quiz.mcq_converter import convert_pairs_batch, mcq_to_question_fields
from app.services.quiz.models import MCQQuestion
from app.services.quiz.section_topics import (
    parse_section_topics,
    question_number_from_label,
    topic_for_question_number,
)
from app.services.quiz.pdf_extractor import extract_qa_from_text, pair_questions_answers
from app.services.quiz.thread_text import get_thread_full_text
from app.utils.db import get_db

logger = logging.getLogger(__name__)

_MAX_CONTENT_MCQS = 40


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


def _clamp_question_count(value: Optional[int], default: int = 10) -> int:
    try:
        n = int(value) if value is not None else default
    except (TypeError, ValueError):
        n = default
    return max(1, min(_MAX_CONTENT_MCQS, n))


def _generate_mcqs_from_any_pdf(text: str, topic: str, count: int) -> List[MCQQuestion]:
    """Generate N MCQs from general PDF content (batched; works without an answer key)."""
    content = (text or "").strip()
    if not content:
        raise ValueError("No readable text found in the PDF")
    # Keep prompt size manageable while still covering the document.
    snippet = content[:14000]
    wanted = _clamp_question_count(count)
    out: List[MCQQuestion] = []
    exclude: List[str] = []
    while len(out) < wanted:
        batch_n = min(10, wanted - len(out))
        batch = generate_mcqs_from_content(
            snippet,
            topic or "General",
            batch_n,
            exclude_question_texts=exclude,
        )
        if not batch:
            break
        out.extend(batch)
        exclude.extend([(q.question_text or "")[:200] for q in batch])
        if len(batch) < batch_n:
            break
    return out[:wanted]


def run_pdf_quiz_pipeline(
    assessment_id: int,
    rag_thread_id: str,
    user_id: int,
    topic_id: Optional[int] = None,
    pdf_text: Optional[str] = None,
    question_count: Optional[int] = None,
) -> dict:
    """
    Build MCQs from a PDF.

    Diagnostics require a valid Q&A / answer-key layout (strict validation).
    Quizzes prefer that layout when present; otherwise generate the requested
    number of MCQs from general PDF content.
    """
    assessment = assessment_service.get_assessment(assessment_id)
    source = assessment_service.link_pdf_source(
        assessment_id,
        rag_thread_id=rag_thread_id,
        original_filename=getattr(assessment, "title", None),
    )
    _set_pdf_source_status(source.id, "processing")

    # None = keep all Q&A pairs (diagnostic path). Quiz UI always passes a count.
    wanted: Optional[int] = None
    if question_count is not None:
        wanted = _clamp_question_count(question_count)

    is_diagnostic = assessment.assessment_type == "diagnostic"

    try:
        text = pdf_text or get_thread_full_text(rag_thread_id, user_id)
        assert_nonempty_extracted_text(text)

        creation_mode = "pdf_qa_auto"
        source_type = "pdf_qa_converted"
        pairs = []
        extraction = None
        batch_questions: List[MCQQuestion] = []
        failed_conversions: List[str] = []
        warnings: List[str] = []
        pair_answer_texts: List[Optional[str]] = []

        if is_diagnostic:
            # Strict Q&A path for diagnostics (BUG-06 validation).
            extraction = extract_qa_from_text(text)
            assert_valid_extraction(extraction)
            pairs = pair_questions_answers(extraction)
            assert_valid_pairs(pairs)
            if wanted is not None and len(pairs) > wanted:
                matched_total = len(pairs)
                pairs = pairs[:wanted]
                warnings.append(
                    f"Using first {wanted} of {matched_total} matched Q&A pairs"
                )
            converted = convert_pairs_batch(
                pairs, quiz_title=extraction.title or assessment.title
            )
            assert_valid_mcqs(converted)
            assert_topic_or_key_area_available(
                text,
                assessment_type=assessment.assessment_type,
                topic_id=topic_id,
                mcq_questions=converted.questions,
            )
            batch_questions = list(converted.questions or [])
            failed_conversions = list(converted.failed_conversions or [])
            warnings.extend(list(extraction.warnings or []))
            pair_answer_texts = [
                pairs[i].answer_text if i < len(pairs) else None
                for i in range(len(batch_questions))
            ]
        else:
            # Quizzes: prefer Q&A when present, else generate from content.
            try:
                extraction = extract_qa_from_text(text)
                pairs = pair_questions_answers(extraction) or []
                if pairs:
                    matched_total = len(pairs)
                    if wanted is not None and matched_total > wanted:
                        pairs = pairs[:wanted]
                        warnings.append(
                            f"Using first {wanted} of {matched_total} matched Q&A pairs"
                        )
                    converted = convert_pairs_batch(
                        pairs, quiz_title=extraction.title or assessment.title
                    )
                    batch_questions = list(converted.questions or [])
                    failed_conversions = list(converted.failed_conversions or [])
                    warnings.extend(list(extraction.warnings or []))
                    pair_answer_texts = [
                        pairs[i].answer_text if i < len(pairs) else None
                        for i in range(len(batch_questions))
                    ]
            except Exception as qa_exc:  # noqa: BLE001 - fall back to content MCQs
                logger.info(
                    "Q&A extraction unavailable for assessment %s (%s); using content MCQs",
                    assessment_id,
                    qa_exc,
                )
                warnings.append(f"Q&A extraction skipped: {qa_exc}")
                pairs = []
                batch_questions = []
                extraction = None

            if not batch_questions:
                creation_mode = "pdf_ai"
                source_type = "pdf_ai"
                topic_name = (assessment.title or "Quiz").strip() or "Quiz"
                gen_count = wanted if wanted is not None else 10
                batch_questions = _generate_mcqs_from_any_pdf(text, topic_name, gen_count)
                pair_answer_texts = [None] * len(batch_questions)
                warnings.append(
                    f"Generated {len(batch_questions)} MCQs from PDF content "
                    f"(requested {gen_count})."
                )
                if not batch_questions:
                    raise AssessmentDocValidationError(
                        detail=(
                            "Could not create MCQs from this PDF. "
                            "Try a clearer document or a different file."
                        )
                    )

        sections = parse_section_topics(text)
        question_pdf_topics: dict[str, str] = {}
        question_concepts: dict[str, str] = {}

        db = get_db()
        db.query(AssessmentQuestion).filter(
            AssessmentQuestion.assessment_id == assessment_id
        ).delete()
        db.commit()

        created_ids = []
        confidences = []
        for idx, mcq in enumerate(batch_questions):
            fields = mcq_to_question_fields(mcq)
            q = question_bank_service.create_question(
                created_by=user_id,
                topic_id=topic_id,
                source_type=source_type,
                source_pdf_thread_id=rag_thread_id,
                source_question_number=idx + 1,
                correct_answer_raw=pair_answer_texts[idx] if idx < len(pair_answer_texts) else None,
                **fields,
            )
            created_ids.append(q.id)
            confidences.append(fields.get("extraction_confidence") or 0.85)
            if pairs and idx < len(pairs):
                qnum = question_number_from_label(pairs[idx].question_number)
                section_topic = topic_for_question_number(qnum, sections) if sections else None
                if section_topic:
                    question_pdf_topics[str(q.id)] = section_topic
                    question_concepts[str(q.id)] = section_topic
            concept = getattr(mcq, "learning_concept", None)
            if concept and str(q.id) not in question_concepts:
                question_concepts[str(q.id)] = str(concept).strip()

        assessment_service.add_questions(assessment_id, created_ids)

        if not created_ids:
            raise AssessmentDocValidationError(
                detail="MCQ conversion failed — no questions were saved."
            )

        extract_conf = (
            float(getattr(extraction, "confidence", 0.75) or 0.75) if extraction else 0.75
        )
        overall = (
            sum(confidences) / len(confidences) * 0.5 + extract_conf * 0.5
            if confidences
            else extract_conf
        )

        raw_json = json.dumps(
            {
                "mode": creation_mode,
                "requested_question_count": wanted,
                "extraction": extraction.model_dump() if extraction else None,
                "pair_count": len(pairs),
                "converted_count": len(batch_questions),
                "failed_conversions": failed_conversions,
                "warnings": warnings,
            },
            ensure_ascii=False,
        )

        assessment_service.save_pdf_extraction(
            source.id,
            raw_json=raw_json,
            pair_count=len(pairs) or len(created_ids),
            warnings_json=json.dumps(warnings, ensure_ascii=False) if warnings else None,
            overall_confidence=overall,
        )

        assessment = assessment_service.get_assessment(assessment_id)
        assessment.creation_mode = creation_mode
        if extraction and extraction.title and assessment.title.startswith("Untitled"):
            assessment.title = extraction.title
        if question_pdf_topics or question_concepts:
            meta = {}
            if assessment.description:
                try:
                    meta = json.loads(assessment.description)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
                if not isinstance(meta, dict):
                    meta = {}
            if question_pdf_topics:
                meta["question_pdf_topics"] = question_pdf_topics
            if question_concepts:
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
            "requested_question_count": wanted,
            "mode": creation_mode,
            "failed_conversions": failed_conversions,
            "overall_confidence": round(overall, 3),
            "requires_review": overall < 0.85,
            "extraction_status": "completed",
        }
    except AssessmentDocValidationError as exc:
        logger.warning(
            "PDF quiz format validation failed for assessment %s: %s",
            assessment_id,
            exc.detail or str(exc),
        )
        _set_pdf_source_status(source.id, "failed", error_message=public_error_message(exc))
        raise
    except Exception as exc:
        logger.exception("PDF quiz pipeline failed for assessment %s", assessment_id)
        _set_pdf_source_status(source.id, "failed", error_message=str(exc))
        raise
