"""Teacher diagnostic flow — Q&A PDF for test + separate target PDF for Learning Chat."""

from __future__ import annotations



import json

import logging

import uuid

from datetime import datetime

from typing import Any, Dict, List, Optional



from app.models.lms_models import Assessment, QuizPdfSource

from app.services.lms import assessment_service, question_bank_service

from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError

from app.services.lms.topic_resolver import (

    get_or_create_topic_from_pdf_label,

    resolve_topic_id_from_label,

)

from app.services.quiz.diagnostic_generator import generate_mcqs_from_content, get_section_text

from app.services.quiz.mcq_converter import mcq_to_question_fields

from app.tasks.quiz_pdf_tasks import enqueue_or_run_pdf_quiz

from app.utils.db import get_db

from app.utils.diagnostic_upload_progress import set_progress as _set_upload_progress

from app.utils.rag_service import _get_thread_topics, extract_and_store_headings_for_thread, ingest_pdf



logger = logging.getLogger(__name__)





def _new_lms_thread_id(user_id: int) -> str:

    return f"user_{user_id}_lms_{int(datetime.utcnow().timestamp())}_{uuid.uuid4().hex[:8]}"





def _get_pdf_source_for_thread(thread_id: str, teacher_id: int) -> QuizPdfSource:

    db = get_db()

    source = (

        db.query(QuizPdfSource)

        .join(Assessment, Assessment.id == QuizPdfSource.assessment_id)

        .filter(

            QuizPdfSource.rag_thread_id == thread_id,

            Assessment.created_by == teacher_id,

            Assessment.assessment_type == "diagnostic",

        )

        .first()

    )

    if not source:

        raise LMSValidationError("Diagnostic PDF thread not found or access denied")

    return source





def _assert_diagnostic_owner(assessment_id: int, user_id: int, is_admin: bool = False) -> Assessment:

    assessment = assessment_service.get_assessment(assessment_id)

    if not is_admin and assessment.created_by != user_id:

        raise LMSValidationError("Not authorized")

    if assessment.assessment_type != "diagnostic":

        raise LMSValidationError("Assessment is not a diagnostic")

    return assessment





def _ingest_target_pdf(
    teacher_id: int,
    file_bytes: bytes,
    filename: str,
    *,
    progress_job_id: str | None = None,
    pdf_index: int = 0,
    pdf_total: int = 1,
) -> str:

    if not file_bytes:

        raise LMSValidationError("Target content PDF is empty")

    thread_id = _new_lms_thread_id(teacher_id)

    label = f"target PDF {pdf_index + 1}/{pdf_total}"
    base_pct = 10 + int((pdf_index / max(pdf_total, 1)) * 40)
    _set_upload_progress(
        progress_job_id,
        base_pct,
        f"Ingesting {label}: {filename}",
        stage="target_ingest",
    )

    def _ingest_progress(step: str, progress: int, message: str) -> None:
        mapped = base_pct + int(max(0, min(100, progress)) * 0.18)
        _set_upload_progress(progress_job_id, mapped, message or f"Ingesting {label}...", stage="target_ingest")

    ingest_pdf(

        file_bytes=file_bytes,

        thread_id=thread_id,

        filename=filename,

        user_id=teacher_id,

        progress_callback=_ingest_progress if progress_job_id else None,

    )

    _set_upload_progress(
        progress_job_id,
        base_pct + 20,
        f"Extracting headings from {label}...",
        stage="target_headings",
    )

    try:

        extract_and_store_headings_for_thread(

            thread_id=thread_id,

            user_id=teacher_id,

            max_wait_seconds=45,

            poll_interval_seconds=2.0,

        )

    except Exception as exc:

        logger.warning("Target PDF heading extraction failed: %s", exc)

    return thread_id





def upload_diagnostic_bundle(

    teacher_id: int,

    title: str,

    diagnostic_file_bytes: bytes,

    diagnostic_filename: str,

    target_file_bytes: bytes,

    target_filename: str,

    target_files: Optional[List[Dict[str, Any]]] = None,

    progress_job_id: Optional[str] = None,

) -> dict:

    """

    Upload diagnostic Q&A PDF (questions + answer key) and target content PDF(s) (Learning Chat).

    """

    if not diagnostic_file_bytes:

        raise LMSValidationError("Diagnostic Q&A PDF is empty")

    targets: List[Dict[str, Any]] = list(target_files or [])
    if not targets:
        if not target_file_bytes:
            raise LMSValidationError("At least one target content PDF is required for Learning Chat")
        targets = [{"bytes": target_file_bytes, "filename": target_filename}]
    elif target_file_bytes:
        targets.insert(0, {"bytes": target_file_bytes, "filename": target_filename})

    _set_upload_progress(progress_job_id, 5, "Creating diagnostic assessment...", stage="create")

    assessment = assessment_service.create_assessment(

        created_by=teacher_id,

        title=title.strip() or "Diagnostic Assessment",

        assessment_type="diagnostic",

        creation_mode="pdf_qa_auto",

    )

    target_thread_ids = []
    target_filenames = []
    total_targets = len([t for t in targets if t.get("bytes")])
    processed_targets = 0
    for idx, tgt in enumerate(targets):
        tbytes = tgt.get("bytes") or b""
        tfname = tgt.get("filename") or f"target_{idx + 1}.pdf"
        if not tbytes:
            continue
        thread_id = _ingest_target_pdf(
            teacher_id,
            tbytes,
            tfname,
            progress_job_id=progress_job_id,
            pdf_index=processed_targets,
            pdf_total=max(total_targets, 1),
        )
        processed_targets += 1
        assessment_service.add_target_pdf(
            assessment.id,
            target_rag_thread_id=thread_id,
            target_original_filename=tfname,
            sort_order=idx,
        )
        target_thread_ids.append(thread_id)
        target_filenames.append(tfname)

    if not target_thread_ids:
        raise LMSValidationError("At least one target content PDF is required for Learning Chat")

    _set_upload_progress(
        progress_job_id,
        55,
        f"Processing diagnostic Q&A PDF ({diagnostic_filename})...",
        stage="qa_pdf",
    )

    pipeline_result = enqueue_or_run_pdf_quiz(

        assessment_id=assessment.id,

        file_bytes=diagnostic_file_bytes,

        filename=diagnostic_filename,

        user_id=teacher_id,

        async_mode=False,

        progress_job_id=progress_job_id,

    )

    _set_upload_progress(progress_job_id, 92, "Finalizing diagnostic...", stage="finalize")



    assessment = assessment_service.get_assessment(assessment.id)

    existing_meta: dict = {}
    if assessment.description:
        try:
            parsed = json.loads(assessment.description)
            if isinstance(parsed, dict):
                existing_meta = parsed
        except (json.JSONDecodeError, TypeError):
            existing_meta = {}

    meta = {
        **existing_meta,
        "diagnostic_pdf_type": "qa",
        "target_rag_thread_id": target_thread_ids[0],
        "target_filename": target_filenames[0],
        "target_rag_thread_ids": target_thread_ids,
        "target_filenames": target_filenames,
    }
    assessment.description = json.dumps(meta, ensure_ascii=False)

    db = get_db()

    db.commit()

    _set_upload_progress(
        progress_job_id,
        100,
        "Diagnostic processing complete.",
        stage="complete",
        done=True,
    )

    return {

        "assessment_id": assessment.id,

        "thread_id": pipeline_result.get("thread_id"),

        "target_thread_id": target_thread_ids[0],

        "target_thread_ids": target_thread_ids,

        "target_filenames": target_filenames,

        "title": assessment.title,

        "status": assessment.status,

        "question_count": pipeline_result.get("question_count"),

        "overall_confidence": pipeline_result.get("overall_confidence"),

        "async": pipeline_result.get("async", False),

    }





def upload_diagnostic_pdf(

    teacher_id: int,

    title: str,

    file_bytes: bytes,

    filename: str,

) -> dict:

    """Legacy: content PDF only — prefer upload_diagnostic_bundle with both PDFs."""

    if not file_bytes:

        raise LMSValidationError("PDF file is empty")



    thread_id = _new_lms_thread_id(teacher_id)

    assessment = assessment_service.create_assessment(

        created_by=teacher_id,

        title=title.strip() or "Diagnostic Assessment",

        assessment_type="diagnostic",

        creation_mode="pdf_ai",

    )

    source = assessment_service.link_pdf_source(

        assessment.id,

        rag_thread_id=thread_id,

        original_filename=filename,

    )



    db = get_db()

    source.extraction_status = "processing"

    db.commit()



    ingest_pdf(

        file_bytes=file_bytes,

        thread_id=thread_id,

        filename=filename,

        user_id=teacher_id,

    )



    try:

        extract_and_store_headings_for_thread(

            thread_id=thread_id,

            user_id=teacher_id,

            max_wait_seconds=45,

            poll_interval_seconds=2.0,

        )

    except Exception as exc:

        logger.warning("Heading extraction after diagnostic upload failed: %s", exc)



    return {

        "assessment_id": assessment.id,

        "thread_id": thread_id,

        "title": assessment.title,

        "status": assessment.status,

    }





def upload_target_pdf(

    assessment_id: int,

    user_id: int,

    file_bytes: bytes,

    filename: str,

    is_admin: bool = False,

) -> dict:

    """Attach an additional target content PDF to an existing diagnostic."""

    assessment = _assert_diagnostic_owner(assessment_id, user_id, is_admin=is_admin)

    target_thread_id = _ingest_target_pdf(user_id, file_bytes, filename)

    entry = assessment_service.add_target_pdf(

        assessment_id,

        target_rag_thread_id=target_thread_id,

        target_original_filename=filename,

    )



    meta = {}
    if assessment.description:
        try:
            meta = json.loads(assessment.description)
            if not isinstance(meta, dict):
                meta = {}
        except (json.JSONDecodeError, TypeError):
            meta = {}

    all_targets = assessment_service.list_target_pdfs(assessment_id)
    meta["target_rag_thread_id"] = target_thread_id
    meta["target_filename"] = filename
    meta["target_rag_thread_ids"] = [t["rag_thread_id"] for t in all_targets]
    meta["target_filenames"] = [t.get("original_filename") or "target.pdf" for t in all_targets]
    assessment.description = json.dumps(meta, ensure_ascii=False)

    db = get_db()

    db.commit()



    return {

        "assessment_id": assessment_id,

        "target_pdf_id": entry.id,

        "target_thread_id": target_thread_id,

        "target_filename": filename,

        "target_pdfs": assessment_service.list_target_pdfs(assessment_id),

    }


def remove_target_pdf_entry(
    assessment_id: int,
    user_id: int,
    target_pdf_id: int,
    is_admin: bool = False,
) -> dict:
    """Remove a target content PDF from a diagnostic."""
    assessment = _assert_diagnostic_owner(assessment_id, user_id, is_admin=is_admin)
    assessment_service.remove_target_pdf(assessment_id, target_pdf_id)
    all_targets = assessment_service.list_target_pdfs(assessment_id)
    meta = {}
    if assessment.description:
        try:
            parsed = json.loads(assessment.description)
            if isinstance(parsed, dict):
                meta = parsed
        except (json.JSONDecodeError, TypeError):
            meta = {}
    if all_targets:
        meta["target_rag_thread_id"] = all_targets[0]["rag_thread_id"]
        meta["target_filename"] = all_targets[0].get("original_filename")
        meta["target_rag_thread_ids"] = [t["rag_thread_id"] for t in all_targets]
        meta["target_filenames"] = [t.get("original_filename") or "target.pdf" for t in all_targets]
    else:
        meta.pop("target_rag_thread_id", None)
        meta.pop("target_filename", None)
        meta["target_rag_thread_ids"] = []
        meta["target_filenames"] = []
    assessment.description = json.dumps(meta, ensure_ascii=False)
    get_db().commit()
    return {
        "assessment_id": assessment_id,
        "target_pdfs": all_targets,
    }


def list_diagnostic_target_pdfs(assessment_id: int, user_id: int, is_admin: bool = False) -> List[dict]:
    _assert_diagnostic_owner(assessment_id, user_id, is_admin=is_admin)
    return assessment_service.list_target_pdfs(assessment_id)





def list_pdf_topics(thread_id: str, teacher_id: int) -> dict:

    """List RAG headings from diagnostic or target PDF thread."""

    _get_pdf_source_for_thread(thread_id, teacher_id)

    result = _get_thread_topics(thread_id)

    if result.get("error"):

        raise LMSValidationError(result["error"])



    enriched = []

    for entry in result.get("topics") or []:

        heading = (entry.get("topic") or entry.get("heading") or "").strip()

        suggested = resolve_topic_id_from_label(heading)

        enriched.append({**entry, "suggested_topic_id": suggested})

    result["topics"] = enriched

    return result





def list_target_pdf_topics(assessment_id: int, user_id: int, is_admin: bool = False) -> dict:

    assessment = _assert_diagnostic_owner(assessment_id, user_id, is_admin=is_admin)

    targets = assessment_service.list_target_pdfs(assessment_id)
    if not targets:
        src = assessment.pdf_source
        if not src or not src.target_rag_thread_id:
            raise LMSValidationError("No target content PDF uploaded yet")
        result = _get_thread_topics(src.target_rag_thread_id)
        if result.get("error"):
            raise LMSValidationError(result["error"])
        return result

    combined_topics = []
    for tgt in targets:
        result = _get_thread_topics(tgt["rag_thread_id"])
        for entry in result.get("topics") or []:
            combined_topics.append({**entry, "source_filename": tgt.get("original_filename")})
    return {"topics": combined_topics}





def generate_diagnostic_questions(

    assessment_id: int,

    user_id: int,

    topic_selections: List[Dict[str, Any]],

    is_admin: bool = False,

) -> dict:

    """Legacy pdf_ai flow — generate MCQs from content PDF sections."""

    assessment = _assert_diagnostic_owner(assessment_id, user_id, is_admin=is_admin)

    if not assessment.pdf_source or not assessment.pdf_source.rag_thread_id:

        raise LMSValidationError("No PDF source linked to this diagnostic")



    if not topic_selections:

        raise LMSValidationError("Select at least one topic with a question count")



    thread_id = assessment.pdf_source.rag_thread_id

    db = get_db()

    source = assessment.pdf_source

    source.extraction_status = "processing"

    db.commit()



    question_ids: List[int] = []

    confidences: List[float] = []

    failed: List[str] = []

    source_topics_meta: List[dict] = []

    question_pdf_topics: dict[str, str] = {}

    question_concepts: dict[str, str] = {}



    for sel in topic_selections:

        topic_name = (sel.get("topic") or sel.get("heading") or "").strip()

        if not topic_name:

            continue

        count = int(sel.get("question_count") or sel.get("count") or 1)

        count = max(1, min(count, 10))

        page = sel.get("page")

        curriculum_topic_id = sel.get("topic_id") or resolve_topic_id_from_label(topic_name)

        pdf_topic = get_or_create_topic_from_pdf_label(topic_name)

        if pdf_topic:

            curriculum_topic_id = pdf_topic.id



        source_topics_meta.append(

            {

                "topic_id": curriculum_topic_id,

                "name": topic_name,

                "pdf_label": topic_name,

                "question_count": count,

            }

        )



        try:

            section_text = get_section_text(thread_id, user_id, topic_name, page)

            mcqs = generate_mcqs_from_content(section_text, topic_name, count)

            for mcq in mcqs:

                fields = mcq_to_question_fields(mcq)

                q = question_bank_service.create_question(

                    created_by=user_id,

                    question_text=fields["question_text"],

                    options=fields["options"],

                    correct_option_index=fields["correct_option_index"],

                    topic_id=curriculum_topic_id,

                    question_latex=fields.get("question_latex"),

                    explanation=fields.get("explanation"),

                    source_type="pdf_ai",

                    source_pdf_thread_id=thread_id,

                    extraction_confidence=fields.get("extraction_confidence"),

                )

                question_ids.append(q.id)

                question_pdf_topics[str(q.id)] = topic_name

                concept = (getattr(mcq, "learning_concept", None) or "").strip()

                if concept:

                    question_concepts[str(q.id)] = concept

                elif topic_name and not topic_name.isupper():

                    question_concepts[str(q.id)] = topic_name

                if fields.get("extraction_confidence") is not None:

                    confidences.append(float(fields["extraction_confidence"]))

        except Exception as exc:

            failed.append(f"{topic_name}: {exc}")

            logger.warning("Diagnostic MCQ generation failed for %s: %s", topic_name, exc)



    if not question_ids:

        source.extraction_status = "failed"

        source.error_message = failed[0] if failed else "No questions generated"

        db.commit()

        raise LMSValidationError(

            failed[0] if failed else "Could not generate questions from selected topics"

        )



    assessment_service.add_questions(assessment_id, question_ids)



    avg_conf = sum(confidences) / len(confidences) if confidences else 0.85

    assessment = assessment_service.get_assessment(assessment_id)

    assessment.overall_confidence = avg_conf

    assessment.requires_review = avg_conf < 0.85

    if source_topics_meta:

        assessment.description = json.dumps(

            {

                "source_topics": source_topics_meta,

                "question_pdf_topics": question_pdf_topics,

                "question_concepts": question_concepts,

            },

            ensure_ascii=False,

        )

    source.extraction_status = "completed"

    source.overall_confidence = avg_conf

    source.error_message = None

    db.commit()



    return {

        "assessment_id": assessment_id,

        "question_count": len(question_ids),

        "overall_confidence": avg_conf,

        "failed_topics": failed,

    }





def get_diagnostic_status(assessment_id: int, user_id: int, is_admin: bool = False) -> dict:

    _assert_diagnostic_owner(assessment_id, user_id, is_admin=is_admin)

    status = assessment_service.get_pdf_processing_status(assessment_id)

    status["assessment_type"] = "diagnostic"

    status["has_target_pdf"] = assessment_service.has_target_pdf(assessment_id)

    status["target_pdfs"] = assessment_service.list_target_pdfs(assessment_id)

    src = assessment_service.get_assessment(assessment_id).pdf_source

    if src:

        status["target_thread_id"] = src.target_rag_thread_id

        status["target_filename"] = src.target_original_filename

    return status


