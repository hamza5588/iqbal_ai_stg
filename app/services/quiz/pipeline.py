"""End-to-end PDF → MCQ quiz pipeline."""
from __future__ import annotations

import json
import logging
import re
from typing import Callable, List, Optional

from app.models.lms_models import AssessmentQuestion, QuizPdfSource
from app.services.lms import assessment_service, question_bank_service
from app.services.lms.exceptions import LMSValidationError
from app.services.quiz.assessment_doc_validation import (
    AssessmentDocValidationError,
    assert_topic_or_key_area_available,
    public_error_message,
)
from app.services.quiz.mcq_converter import mcq_to_question_fields
from app.services.quiz.models import MCQQuestion
from app.services.quiz.hybrid_mcq_extractor import (
    extract_mcqs_hybrid_vision,
    hybrid_vision_enabled,
)
from app.services.quiz.thread_text import get_thread_full_text
from app.utils.db import get_db

logger = logging.getLogger(__name__)

_MAX_CONTENT_MCQS = 40
ProgressCallback = Optional[Callable[[str, int, str], None]]


def _question_categories(
    questions: List[MCQQuestion],
    source_numbers: List[int],
    *,
    domain_mapping_expected: bool = False,
) -> tuple[List[MCQQuestion], List[str], bool]:
    """Return complete categories, failing closed on partial printed domains.

    If even one printed Domain is present, every question must have a real
    printed Domain. Papers with no Domain column use the question's printed
    Topic and then the existing concept labeler only for genuine gaps.
    """
    from app.services.quiz.concept_labeler import (
        assign_learning_concepts,
        is_generic_concept,
    )

    def clean(value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip())

    domains = [clean(getattr(question, "domain", None)) for question in questions]
    has_printed_domains = any(domains)

    if has_printed_domains or domain_mapping_expected:
        invalid = [
            source_numbers[index] if index < len(source_numbers) else index + 1
            for index, domain in enumerate(domains)
            if not domain or is_generic_concept(domain)
        ]
        if invalid:
            joined = ", ".join(f"Q{number}" for number in invalid)
            raise AssessmentDocValidationError(
                detail=f"Printed Domain mapping is incomplete or generic for {joined}",
                public_message=(
                    "The PDF question categories could not be verified for "
                    f"{joined}. Please correct the Domain mapping and upload again."
                ),
                reason="INCOMPLETE_DOMAIN_MAPPING",
            )
        normalized = [
            question.model_copy(
                update={"domain": domains[index], "learning_concept": domains[index]}
            )
            for index, question in enumerate(questions)
        ]
        return normalized, domains, True

    labeled = assign_learning_concepts(list(questions))
    categories = [
        clean(getattr(question, "learning_concept", None)) for question in labeled
    ]
    invalid = [
        source_numbers[index] if index < len(source_numbers) else index + 1
        for index, category in enumerate(categories)
        if not category or is_generic_concept(category)
    ]
    if invalid:
        joined = ", ".join(f"Q{number}" for number in invalid)
        raise AssessmentDocValidationError(
            detail=f"No non-generic question category could be resolved for {joined}",
            public_message=(
                "The PDF question categories could not be verified for "
                f"{joined}. Add a Domain or Topic for every question and upload again."
            ),
            reason="UNRESOLVED_QUESTION_CATEGORY",
        )
    return labeled, categories, False


def _source_declares_question_domains(text: str) -> bool:
    """Detect an explicit per-question Domain label/table in source text."""
    source = text or ""
    if re.search(r"(?i)\bdomain\s*:", source):
        return True
    return bool(
        re.search(
            r"(?is)\banswer\b.{0,200}\bdomain\b.{0,200}\btopic\b",
            source,
        )
    )


def _apply_source_domain_mapping(
    questions: List[MCQQuestion],
    source_numbers: List[int],
    text: str,
) -> List[MCQQuestion]:
    """Overlay deterministic answer-key Domain rows onto extracted questions."""
    if not _source_declares_question_domains(text):
        return questions
    from app.services.quiz.hybrid_vision_pipeline import parse_answer_key_from_text

    enrichment = parse_answer_key_from_text(
        {"pages": [{"page_number": 1, "plain_text": text or ""}]}
    )
    by_number = {
        int(entry.question_no): re.sub(r"\s+", " ", (entry.domain or "").strip())
        for entry in enrichment.entries
        if (entry.domain or "").strip()
    }
    if not by_number:
        return questions
    updated: List[MCQQuestion] = []
    for index, question in enumerate(questions):
        number = source_numbers[index] if index < len(source_numbers) else index + 1
        domain = by_number.get(int(number))
        if domain:
            question = question.model_copy(
                update={"domain": domain, "learning_concept": domain}
            )
        updated.append(question)
    return updated


def _category_metadata(
    description: Optional[str],
    question_concepts: dict[str, str],
    question_pdf_topics: dict[str, str],
    *,
    categories_are_pdf_domains: bool,
    is_diagnostic: bool,
) -> str:
    """Replace category maps so a reprocessed quiz cannot retain stale IDs."""
    try:
        parsed = json.loads(description or "{}")
    except (json.JSONDecodeError, TypeError):
        parsed = {}
    meta = parsed if isinstance(parsed, dict) else {}
    meta.pop("question_pdf_topics", None)
    meta.pop("question_concepts", None)
    meta.pop("concept_source", None)
    meta["question_concepts"] = dict(question_concepts)
    if categories_are_pdf_domains:
        meta["question_pdf_topics"] = dict(question_pdf_topics)
        meta["concept_source"] = "pdf_domain"
    else:
        meta["concept_source"] = "question_concept"
    if is_diagnostic:
        meta["awaiting_admin_approval"] = True
    return json.dumps(meta, ensure_ascii=False)


def _run_hybrid_vision_extraction(
    pdf_bytes: bytes,
    *,
    is_diagnostic: bool,
    wanted: Optional[int],
    progress_callback: ProgressCallback,
    user_id: Optional[int] = None,
) -> tuple[List[MCQQuestion], List[int], List[Optional[str]], List[str], float, Optional[str]]:
    """PDF bytes → dual-evidence (text + page images) → structured MCQs."""
    from app.utils.llm_gateway import llm_workflow

    with llm_workflow(
        "hybrid_vision_mcq",
        user_id=user_id,
        user_role="teacher" if user_id else None,
        traffic_source="production",
    ):
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
                user_id=user_id,
            )
        except AssessmentDocValidationError:
            raise
        except Exception as hybrid_exc:  # noqa: BLE001
            logger.exception(
                "Hybrid vision extraction failed for assessment %s",
                assessment_id,
            )
            msg = str(hybrid_exc) or "Hybrid vision extraction failed"
            # Config / infra failures must not be masked as "bad PDF format".
            if "GROQ_API_KEY" in msg or "API key" in msg.lower():
                raise LMSValidationError(
                    "Hybrid vision is not configured: missing Groq API key. "
                    "Set GROQ_API_KEY on the server or save it in Admin → Settings."
                ) from hybrid_exc
            raise AssessmentDocValidationError(
                detail=f"Hybrid vision extraction failed. {msg}"
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

        if wanted is not None and len(batch_questions) < wanted:
            warnings.append(
                f"Hybrid vision produced {len(batch_questions)} of {wanted} "
                "requested MCQs (no content top-up — hybrid only)."
            )

        canonical_used = bool(canonical_source_numbers)
        question_pdf_topics: dict[str, str] = {}
        question_concepts: dict[str, str] = {}

        _report_progress(progress_callback, "save", 88, "Reviewing display format...")
        try:
            from app.services.quiz.display_format_qa import review_mcqs_for_display

            batch_questions = review_mcqs_for_display(list(batch_questions))
        except Exception as exc:
            logger.warning("Display format review before save skipped: %s", exc)

        batch_questions = _apply_source_domain_mapping(
            list(batch_questions), canonical_source_numbers, text
        )
        batch_questions, category_labels, categories_are_pdf_domains = _question_categories(
            list(batch_questions),
            canonical_source_numbers,
            domain_mapping_expected=_source_declares_question_domains(text),
        )
        if is_diagnostic:
            assert_topic_or_key_area_available(
                text,
                assessment_type=assessment.assessment_type,
                topic_id=topic_id,
                mcq_questions=batch_questions,
            )

        from app.services.lms.topic_resolver import get_or_create_topic_from_pdf_label

        category_topic_ids: List[int] = []
        for label in category_labels:
            category_topic = get_or_create_topic_from_pdf_label(label)
            if not category_topic:
                raise AssessmentDocValidationError(
                    detail=f"Could not resolve category topic for {label!r}",
                    public_message=(
                        "The PDF question categories could not be saved. "
                        "Please review the Domain mapping and upload again."
                    ),
                    reason="CATEGORY_TOPIC_UNRESOLVED",
                )
            category_topic_ids.append(int(category_topic.id))

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
                topic_id=category_topic_ids[idx],
                source_type=q_source,
                source_pdf_thread_id=rag_thread_id,
                source_question_number=q_source_number,
                correct_answer_raw=raw_ans,
                **fields,
            )
            created_ids.append(q.id)
            confidences.append(fields.get("extraction_confidence") or 0.85)
            category = category_labels[idx]
            question_concepts[str(q.id)] = category
            if categories_are_pdf_domains:
                question_pdf_topics[str(q.id)] = category

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
        assessment.description = _category_metadata(
            assessment.description,
            question_concepts,
            question_pdf_topics,
            categories_are_pdf_domains=categories_are_pdf_domains,
            is_diagnostic=assessment.assessment_type == "diagnostic",
        )
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
