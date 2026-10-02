"""End-to-end PDF → MCQ quiz pipeline."""
from __future__ import annotations

import json
import logging
from typing import Callable, List, Optional

from app.models.lms_models import AssessmentQuestion, QuizPdfSource
from app.services.lms import assessment_service, question_bank_service
from app.services.quiz.assessment_doc_validation import (
    AssessmentDocValidationError,
    assert_topic_or_key_area_available,
    public_error_message,
)
from app.services.quiz.mcq_converter import mcq_to_question_fields
from app.services.quiz.models import MCQQuestion
from app.services.quiz.section_topics import (
    parse_section_topics,
    question_number_from_label,
    topic_for_question_number,
)
from app.services.quiz.hybrid_mcq_extractor import (
    extract_mcqs_hybrid_vision,
    hybrid_vision_enabled,
)
from app.services.quiz.thread_text import get_thread_full_text
from app.utils.db import get_db

logger = logging.getLogger(__name__)

_MAX_CONTENT_MCQS = 40
ProgressCallback = Optional[Callable[[str, int, str], None]]


def _run_hybrid_vision_extraction(
    pdf_bytes: bytes,
    *,
    is_diagnostic: bool,
    wanted: Optional[int],
    progress_callback: ProgressCallback,
) -> tuple[List[MCQQuestion], List[int], List[Optional[str]], List[str], float, Optional[str]]:
    """PDF bytes → dual-evidence (text + page images) → structured MCQs."""
    questions, source_numbers, answer_letters, warnings, confidence, title = (
        extract_mcqs_hybrid_vision(
            pdf_bytes,
            require_answers=is_diagnostic,
            progress_callback=progress_callback,
        )
    )
    if wanted is not None and len(questions) > wanted:
        questions = questions[:wanted]
        source_numbers = source_numbers[:wanted]
        answer_letters = answer_letters[:wanted]
        warnings.append(f"Using first {wanted} of hybrid-extracted questions")
    return questions, source_numbers, answer_letters, warnings, confidence, title


class _HybridExtractionShim:
    """Duck-type for pipeline confidence / title lift after hybrid vision."""

    def __init__(
        self,
        *,
        title: Optional[str],
        confidence: float,
        warnings: List[str],
    ):
        self.title = title
        self.confidence = confidence
        self._warnings = list(warnings)

    def model_dump(self):
        return {
            "parser": "hybrid_vision",
            "warnings": self._warnings,
            "confidence": self.confidence,
            "title": self.title,
        }


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


def _report_progress(
    callback: ProgressCallback,
    step: str,
    progress: int,
    message: str,
) -> None:
    if not callback:
        return
    try:
        callback(step, int(max(0, min(100, progress))), message)
    except Exception as exc:  # noqa: BLE001 - never fail pipeline on progress UI
        logger.debug("Quiz progress callback failed: %s", exc)


def _clamp_question_count(value: Optional[int], default: int = 10) -> int:
    try:
        n = int(value) if value is not None else default
    except (TypeError, ValueError):
        n = default
    return max(1, min(_MAX_CONTENT_MCQS, n))


def run_pdf_quiz_pipeline(
    assessment_id: int,
    rag_thread_id: str,
    user_id: int,
    topic_id: Optional[int] = None,
    pdf_text: Optional[str] = None,
    question_count: Optional[int] = None,
    progress_callback: ProgressCallback = None,
    pdf_bytes: Optional[bytes] = None,
) -> dict:
    """
    Build MCQs from a PDF via **hybrid vision only** (text + page images).

    No canonical / text-Q&A / content-generation fallback. Missing PDF bytes or
    a hybrid failure fails the job with a clear error.
    """
    assessment = assessment_service.get_assessment(assessment_id)
    source = assessment_service.link_pdf_source(
        assessment_id,
        rag_thread_id=rag_thread_id,
        original_filename=getattr(assessment, "title", None),
    )
    _set_pdf_source_status(source.id, "processing")
    _report_progress(progress_callback, "extract", 62, "Reading PDF with hybrid vision...")

    # None = keep all Q&A pairs (diagnostic path). Quiz UI always passes a count.
    wanted: Optional[int] = None
    if question_count is not None:
        wanted = _clamp_question_count(question_count)

    is_diagnostic = assessment.assessment_type == "diagnostic"

    try:
        if not pdf_bytes:
            raise AssessmentDocValidationError(
                detail=(
                    "PDF bytes are required for hybrid vision extraction. "
                    "Re-upload the PDF (text-only / thread-only processing is disabled)."
                )
            )
        # Optional text layer (LlamaParse / RAG) is advisory only — hybrid reads pages.
        text = (pdf_text or get_thread_full_text(rag_thread_id, user_id) or "").strip()

        creation_mode = "pdf_qa_auto"
        source_type = "pdf_qa_converted"
        pairs = []
        extraction = None
        batch_questions: List[MCQQuestion] = []
        failed_conversions: List[str] = []
        warnings: List[str] = []
        pair_answer_texts: List[Optional[str]] = []
        # Printed source numbers from hybrid vision (when available).
        canonical_source_numbers: List[int] = []

        if not text:
            warnings.append(
                "No extractable PDF text layer; hybrid vision is using page images only."
            )

        # Mandatory path: dual-evidence hybrid vision (same engine as persor.py CLI).
        # Failures are not swallowed — no canonical / text / LLM content fallback.
        _ = hybrid_vision_enabled()  # logs if QUIZ_HYBRID_VISION tries to disable
        try:
            (
                batch_questions,
                canonical_source_numbers,
                pair_answer_texts,
                hybrid_warnings,
                hybrid_conf,
                hybrid_title,
            ) = _run_hybrid_vision_extraction(
                pdf_bytes,
                is_diagnostic=is_diagnostic,
                wanted=wanted,
                progress_callback=progress_callback,
            )
        except AssessmentDocValidationError:
            raise
        except Exception as hybrid_exc:  # noqa: BLE001
            logger.exception(
                "Hybrid vision extraction failed for assessment %s",
                assessment_id,
            )
            raise AssessmentDocValidationError(
                detail=(
                    "Hybrid vision extraction failed. "
                    f"{hybrid_exc}"
                )
            ) from hybrid_exc

        warnings.extend(hybrid_warnings)
        extraction = _HybridExtractionShim(
            title=hybrid_title,
            confidence=hybrid_conf,
            warnings=hybrid_warnings,
        )

        if not batch_questions:
            raise AssessmentDocValidationError(
                detail=(
                    "Hybrid vision could not extract any MCQs from this PDF. "
                    "Check that the file is a readable MCQ / diagnostic paper."
                )
            )

        if is_diagnostic and not parse_section_topics(text):
            from app.services.quiz.concept_labeler import assign_learning_concepts

            batch_questions = assign_learning_concepts(list(batch_questions))
        if is_diagnostic:
            assert_topic_or_key_area_available(
                text,
                assessment_type=assessment.assessment_type,
                topic_id=topic_id,
                mcq_questions=batch_questions,
            )

        if wanted is not None and len(batch_questions) < wanted:
            warnings.append(
                f"Hybrid vision produced {len(batch_questions)} of {wanted} "
                "requested MCQs (no content top-up — hybrid only)."
            )

        canonical_used = bool(canonical_source_numbers)
        sections = parse_section_topics(text)
        question_pdf_topics: dict[str, str] = {}
        question_concepts: dict[str, str] = {}

        _report_progress(progress_callback, "save", 88, "Reviewing display format...")
        try:
            from app.services.quiz.display_format_qa import review_mcqs_for_display

            batch_questions = review_mcqs_for_display(list(batch_questions))
        except Exception as exc:
            logger.warning("Display format review before save skipped: %s", exc)

        _report_progress(progress_callback, "save", 90, "Saving questions...")
        db = get_db()
        db.query(AssessmentQuestion).filter(
            AssessmentQuestion.assessment_id == assessment_id
        ).delete()
        db.commit()

        created_ids = []
        confidences = []
        for idx, mcq in enumerate(batch_questions):
            fields = mcq_to_question_fields(mcq)
            raw_ans = pair_answer_texts[idx] if idx < len(pair_answer_texts) else None
            q_source = "pdf_qa_converted"
            # Prefer printed numbers from hybrid vision when present.
            if canonical_used:
                q_source_number = (
                    canonical_source_numbers[idx]
                    if idx < len(canonical_source_numbers)
                    else None
                )
            else:
                q_source_number = idx + 1
            q = question_bank_service.create_question(
                created_by=user_id,
                topic_id=topic_id,
                source_type=q_source,
                source_pdf_thread_id=rag_thread_id,
                source_question_number=q_source_number,
                correct_answer_raw=raw_ans,
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
        # Diagnostics stay draft until admin explicitly publishes/approves.
        # Students only see published diagnostics via get_active_platform_diagnostic.
        if assessment.assessment_type == "diagnostic":
            assessment.status = "draft"
            assessment.requires_review = True
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
            if assessment.assessment_type == "diagnostic":
                meta["awaiting_admin_approval"] = True
            assessment.description = json.dumps(meta, ensure_ascii=False)
        db.commit()

        if assessment.assessment_type == "diagnostic":
            try:
                from app.services.lms.diagnostic_timer_service import estimate_question_times
                estimate_question_times(assessment_id)
            except Exception as timer_exc:
                logger.warning("Diagnostic timer estimation failed: %s", timer_exc)

        _report_progress(progress_callback, "done", 100, "Quiz ready")
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
            "requires_review": True if assessment.assessment_type == "diagnostic" else (overall < 0.85),
            "status": assessment.status,
            "extraction_status": "completed",
            "warnings": warnings,
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
