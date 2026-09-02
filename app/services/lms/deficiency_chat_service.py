"""Post-diagnostic deficiency chat — questions from teacher target PDF only."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import List, Optional, Tuple

from app.models.lms_models import AssessmentAttempt, DeficiencyChatSession, StudentProfile
from app.services.lms import assessment_service, tutor_service
from app.services.lms.mcq_utils import (
    is_label_only,
    normalize_options,
    pick_display_fields,
    resolve_correct_option_index,
)
from app.services.lms.diagnostic_service import get_default_diagnostic
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError
from app.services.lms.path_generator import get_weak_topics
from app.services.lms.performance_service import analyze_attempt
from app.services.quiz.diagnostic_generator import generate_mcqs_from_content, get_section_text
from app.utils.db import get_db
from app.utils.rag_service import _get_thread_topics

logger = logging.getLogger(__name__)

PRACTICE_QUESTIONS_PER_WEAK_AREA = 2


def _get_target_threads(assessment_id: int) -> List[dict]:
    """Return all target PDF threads for a diagnostic."""
    targets = assessment_service.list_target_pdfs(assessment_id)
    if targets:
        return targets
    assessment = assessment_service.get_assessment(assessment_id)
    src = assessment.pdf_source
    if src and src.target_rag_thread_id:
        return [{"rag_thread_id": src.target_rag_thread_id, "original_filename": src.target_original_filename}]
    return []


def _resolve_target_pdf_context(student_id: int) -> Tuple[Optional[str], Optional[int], Optional[int]]:
    """Return (primary target_rag_thread_id, owner_id, diagnostic_assessment_id)."""
    db = get_db()
    profile = db.query(StudentProfile).filter(StudentProfile.user_id == student_id).first()
    if profile and profile.diagnostic_assessment_id:
        try:
            assessment = assessment_service.get_assessment(profile.diagnostic_assessment_id)
            targets = _get_target_threads(assessment.id)
            if targets:
                return targets[0]["rag_thread_id"], assessment.created_by, assessment.id
        except LMSNotFoundError:
            pass

    platform_diag = get_default_diagnostic()
    if platform_diag:
        targets = _get_target_threads(platform_diag.id)
        if targets:
            return targets[0]["rag_thread_id"], platform_diag.created_by, platform_diag.id
    return None, None, None


def _resolve_all_target_threads(student_id: int) -> Tuple[List[str], Optional[int], Optional[int]]:
    """Return (all target thread ids, owner_id, diagnostic_assessment_id)."""
    db = get_db()
    profile = db.query(StudentProfile).filter(StudentProfile.user_id == student_id).first()
    if profile and profile.diagnostic_assessment_id:
        try:
            assessment = assessment_service.get_assessment(profile.diagnostic_assessment_id)
            targets = _get_target_threads(assessment.id)
            if targets:
                return [t["rag_thread_id"] for t in targets], assessment.created_by, assessment.id
        except LMSNotFoundError:
            pass
    platform_diag = get_default_diagnostic()
    if platform_diag:
        targets = _get_target_threads(platform_diag.id)
        if targets:
            return [t["rag_thread_id"] for t in targets], platform_diag.created_by, platform_diag.id
    return [], None, None


def _match_target_section(weak_area_name: str, target_thread_ids: List[str]) -> Tuple[str, str]:
    """Pick the best target PDF heading across all target PDFs. Returns (section, thread_id)."""
    name = (weak_area_name or "").strip()
    if not name:
        return name, target_thread_ids[0] if target_thread_ids else ""

    name_lower = name.lower()
    best = None
    best_score = 0
    best_thread = target_thread_ids[0] if target_thread_ids else ""

    for thread_id in target_thread_ids:
        topics = _get_thread_topics(thread_id).get("topics") or []
        for entry in topics:
            heading = (entry.get("topic") or entry.get("heading") or "").strip()
            if not heading:
                continue
            h_lower = heading.lower()
            score = 0
            if name_lower in h_lower or h_lower in name_lower:
                score = min(len(name_lower), len(h_lower))
            else:
                score = len(set(name_lower.split()) & set(h_lower.split()))
            if score > best_score:
                best_score = score
                best = heading
                best_thread = thread_id
    return (best or name), best_thread


def _mcq_to_queue_item(mcq, topic_id: int, topic_name: str, pdf_section: str, rag_thread_id: str) -> Optional[dict]:
    options = normalize_options(
        [{"label": o.label, "text": o.text, "latex": getattr(o, "latex", None)} for o in mcq.options]
    )
    if any(is_label_only(o.get("text") or "") and is_label_only(o.get("latex") or "") for o in options):
        return None
    correct_idx = resolve_correct_option_index(options, mcq.correct_option_label)
    if correct_idx is None:
        logger.warning("Skipping MCQ with unresolved correct label %s", mcq.correct_option_label)
        return None
    q_text, q_latex = pick_display_fields(mcq.question_text, mcq.question_latex)
    return {
        "topic_id": topic_id,
        "topic_name": topic_name,
        "pdf_section": pdf_section,
        "rag_thread_id": rag_thread_id,
        "question_text": q_text or mcq.question_text,
        "question_latex": q_latex,
        "options": options,
        "correct_option_index": correct_idx,
        "source": "target_pdf",
        "answered": False,
        "correct": None,
    }


def _build_question_queue(
    weak_topics: List[dict],
    target_thread_ids: List[str],
    rag_owner_id: int,
) -> List[dict]:
    """Generate Learning Chat MCQs from target content PDF(s)."""
    queue: List[dict] = []
    seen_texts: set[str] = set()

    for entry in weak_topics:
        topic_id = entry.get("topic_id") or 0
        topic_name = (entry.get("topic_name") or "Practice area").strip()
        pdf_section, thread_id = _match_target_section(topic_name, target_thread_ids)

        section_text = get_section_text(thread_id, rag_owner_id, pdf_section)
        if not section_text.strip():
            for alt_thread in target_thread_ids:
                if alt_thread == thread_id:
                    continue
                section_text = get_section_text(alt_thread, rag_owner_id, pdf_section)
                if section_text.strip():
                    thread_id = alt_thread
                    break
        if not section_text.strip():
            logger.warning("No target PDF text for weak area %s (section %s)", topic_name, pdf_section)
            continue

        try:
            mcqs = generate_mcqs_from_content(
                section_text, pdf_section, PRACTICE_QUESTIONS_PER_WEAK_AREA
            )
            for mcq in mcqs:
                item = _mcq_to_queue_item(mcq, topic_id, topic_name, pdf_section, thread_id)
                if not item:
                    continue
                key = (item.get("question_text") or "")[:120].lower()
                if key and key not in seen_texts:
                    seen_texts.add(key)
                    queue.append(item)
        except Exception as exc:
            logger.warning("Target PDF MCQ gen failed for %s: %s", pdf_section, exc)

    return queue


def _load_questions(session: DeficiencyChatSession) -> List[dict]:
    try:
        return json.loads(session.questions_json or "[]")
    except json.JSONDecodeError:
        return []


def _save_questions(session: DeficiencyChatSession, questions: List[dict]) -> None:
    session.questions_json = json.dumps(questions, ensure_ascii=False)


def _session_state(session: DeficiencyChatSession) -> dict:
    questions = _load_questions(session)
    total = len(questions)
    current = None
    if session.current_index < total:
        q = dict(questions[session.current_index])
        q.pop("correct_option_index", None)
        current = q
    weak = []
    try:
        weak = json.loads(session.weak_topics_json or "[]")
    except json.JSONDecodeError:
        pass
    tutor_state = _load_tutor_state(session)
    assist_level = tutor_state.get("assist_level", 1)
    tutor_messages = []
    for m in tutor_state.get("messages") or []:
        role = m.get("role", "")
        text = m.get("content") or m.get("text") or ""
        if role == "assistant":
            tutor_messages.append({"role": "bot", "text": text, "levelLabel": ""})
        elif role == "user":
            tutor_messages.append({"role": "user", "text": text})
    return {
        "session_id": session.id,
        "status": session.status,
        "current_index": session.current_index,
        "total_questions": total,
        "correct_count": session.correct_count,
        "weak_topics": weak,
        "current_question": current,
        "has_pdf": bool(session.rag_thread_id),
        "pdf_label": "Teacher target content PDF",
        "tutor_assist_level": assist_level,
        "tutor_assist_label": tutor_service.get_deficiency_assist_level_label(assist_level),
        "tutor_messages": tutor_messages,
        "completed": session.status == "completed",
    }


def _close_old_sessions(student_id: int) -> None:
    db = get_db()
    sessions = (
        db.query(DeficiencyChatSession)
        .filter(
            DeficiencyChatSession.student_id == student_id,
            DeficiencyChatSession.status.in_(("active", "paused")),
        )
        .all()
    )
    for session in sessions:
        session.status = "completed"
    db.commit()


def get_active_session(
    student_id: int, diagnostic_assessment_id: Optional[int] = None
) -> Optional[DeficiencyChatSession]:
    db = get_db()
    q = (
        db.query(DeficiencyChatSession)
        .filter(
            DeficiencyChatSession.student_id == student_id,
            DeficiencyChatSession.status.in_(("active", "paused")),
        )
        .order_by(DeficiencyChatSession.updated_at.desc())
    )
    if diagnostic_assessment_id:
        q = q.filter(DeficiencyChatSession.diagnostic_assessment_id == diagnostic_assessment_id)
    return q.first()


def _latest_diagnostic_attempt(student_id: int, assessment_id: int) -> Optional[AssessmentAttempt]:
    db = get_db()
    return (
        db.query(AssessmentAttempt)
        .filter(
            AssessmentAttempt.student_id == student_id,
            AssessmentAttempt.assessment_id == assessment_id,
            AssessmentAttempt.status == "submitted",
        )
        .order_by(AssessmentAttempt.submitted_at.desc())
        .first()
    )


def start_session(student_id: int, force_new: bool = False) -> dict:
    """Start Learning Chat — questions generated from target PDF(s)."""
    target_thread_ids, rag_owner_id, diag_id = _resolve_all_target_threads(student_id)
    target_thread_id = target_thread_ids[0] if target_thread_ids else None

    if not target_thread_id or not rag_owner_id:
        raise LMSValidationError(
            "No target content PDF has been uploaded yet. "
            "Learning Chat uses study material PDFs for weak areas — ask your admin."
        )

    if force_new:
        _close_old_sessions(student_id)

    existing = None if force_new else get_active_session(student_id, diag_id)
    if existing and existing.rag_thread_id == target_thread_id:
        if existing.status == "paused":
            existing.status = "active"
            get_db().commit()
        return _session_state(existing)

    weak = get_weak_topics(student_id)
    if not weak:
        raise LMSValidationError(
            "No weak areas found. Complete your diagnostic assessment first."
        )

    if diag_id:
        attempt = _latest_diagnostic_attempt(student_id, diag_id)
        if attempt:
            analysis = analyze_attempt(attempt.id)
            if analysis.get("weak_topics"):
                weak = analysis["weak_topics"]

    queue = _build_question_queue(weak, target_thread_ids, rag_owner_id)
    if not queue:
        raise LMSValidationError(
            "Could not generate questions from the target PDF(s) for your weak areas. "
            "Ask your admin to check the target content PDFs cover those topics."
        )

    weak_payload = [
        {
            "topic_id": w.get("topic_id", 0),
            "topic_name": w.get("topic_name") or "Practice area",
            "score_percent": w.get("score_percent", 0),
            "question_ids": w.get("question_ids") or [],
        }
        for w in weak
    ]

    db = get_db()
    session = DeficiencyChatSession(
        student_id=student_id,
        diagnostic_assessment_id=diag_id,
        rag_thread_id=target_thread_id,
        rag_owner_id=rag_owner_id,
        weak_topics_json=json.dumps(weak_payload, ensure_ascii=False),
        questions_json=json.dumps(queue, ensure_ascii=False),
        status="active",
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return _session_state(session)


def get_session(session_id: int, student_id: int) -> dict:
    db = get_db()
    session = db.query(DeficiencyChatSession).filter(DeficiencyChatSession.id == session_id).first()
    if not session:
        raise LMSNotFoundError("Session not found")
    if session.student_id != student_id:
        raise LMSValidationError("Not authorized")
    return _session_state(session)


def _reset_tutor_state(session: DeficiencyChatSession) -> None:
    session.chat_history_json = json.dumps(
        {
            "question_index": session.current_index,
            "assist_level": 1,
            "messages": [],
        },
        ensure_ascii=False,
    )


def submit_answer(session_id: int, student_id: int, selected_option_index: int) -> dict:
    db = get_db()
    session = db.query(DeficiencyChatSession).filter(DeficiencyChatSession.id == session_id).first()
    if not session or session.student_id != student_id:
        raise LMSValidationError("Not authorized")
    if session.status not in ("active", "paused"):
        raise LMSValidationError("Session is not active")

    questions = _load_questions(session)
    idx = session.current_index
    if idx >= len(questions):
        raise LMSValidationError("No more questions in this session")

    q = questions[idx]
    is_correct = selected_option_index == q.get("correct_option_index")
    already_correct = q.get("correct") is True

    if is_correct:
        q["answered"] = True
        q["correct"] = True
        if not already_correct:
            session.correct_count += 1
        session.current_index = idx + 1
        _reset_tutor_state(session)
        if session.current_index >= len(questions):
            session.status = "completed"
            _mark_learning_path_chat_complete(student_id)
    else:
        q["answered"] = True
        q["correct"] = False

    _save_questions(session, questions)
    db.commit()

    result = _session_state(session)
    result["last_answer"] = {
        "correct": is_correct,
        "explanation_available": not is_correct,
        "topic_name": q.get("topic_name"),
        "stay_on_question": not is_correct,
    }
    return result


def advance_session(session_id: int, student_id: int) -> dict:
    """Skip the current question (after an incorrect attempt) and close the tutor."""
    db = get_db()
    session = db.query(DeficiencyChatSession).filter(DeficiencyChatSession.id == session_id).first()
    if not session or session.student_id != student_id:
        raise LMSValidationError("Not authorized")
    if session.status not in ("active", "paused"):
        raise LMSValidationError("Session is not active")

    questions = _load_questions(session)
    idx = session.current_index
    if idx >= len(questions):
        session.status = "completed"
        _mark_learning_path_chat_complete(student_id)
        db.commit()
        return _session_state(session)

    q = questions[idx]
    if not q.get("answered"):
        q["answered"] = True
        q["correct"] = False
        _save_questions(session, questions)

    session.current_index = idx + 1
    _reset_tutor_state(session)
    if session.current_index >= len(questions):
        session.status = "completed"
        _mark_learning_path_chat_complete(student_id)
    db.commit()
    return _session_state(session)


def pause_session(session_id: int, student_id: int) -> dict:
    db = get_db()
    session = db.query(DeficiencyChatSession).filter(DeficiencyChatSession.id == session_id).first()
    if not session or session.student_id != student_id:
        raise LMSValidationError("Not authorized")
    session.status = "paused"
    db.commit()
    return _session_state(session)


def _load_tutor_state(session: DeficiencyChatSession) -> dict:
    """Per-question tutor history and assistance level (1–5)."""
    try:
        raw = json.loads(session.chat_history_json or "{}")
    except json.JSONDecodeError:
        raw = {}
    if isinstance(raw, list):
        raw = {"question_index": session.current_index, "assist_level": 1, "messages": raw}
    if raw.get("question_index") != session.current_index:
        return {"question_index": session.current_index, "assist_level": 1, "messages": []}
    return {
        "question_index": session.current_index,
        "assist_level": int(raw.get("assist_level") or 1),
        "messages": raw.get("messages") or [],
    }


def _save_tutor_state(session: DeficiencyChatSession, state: dict) -> None:
    session.chat_history_json = json.dumps(
        {
            "question_index": state.get("question_index", session.current_index),
            "assist_level": state.get("assist_level", 1),
            "messages": (state.get("messages") or [])[-20:],
        },
        ensure_ascii=False,
    )


def explain_with_tutor(
    session_id: int,
    student_id: int,
    message: str,
    api_key: str = "",
) -> dict:
    """PDF-grounded tutor with 5-level step-by-step assistance."""
    db = get_db()
    session = db.query(DeficiencyChatSession).filter(DeficiencyChatSession.id == session_id).first()
    if not session or session.student_id != student_id:
        raise LMSValidationError("Not authorized")

    questions = _load_questions(session)
    current_q = questions[session.current_index] if session.current_index < len(questions) else None

    pdf_excerpt = ""
    pdf_section = (current_q or {}).get("pdf_section") or (current_q or {}).get("topic_name") or ""
    thread_for_excerpt = (current_q or {}).get("rag_thread_id") or session.rag_thread_id
    if thread_for_excerpt and session.rag_owner_id and pdf_section:
        pdf_excerpt = get_section_text(
            thread_for_excerpt, session.rag_owner_id, pdf_section, max_chars=4000
        )
        if not pdf_excerpt.strip():
            diag_id = session.diagnostic_assessment_id
            if diag_id:
                for alt_thread in _get_target_threads(diag_id):
                    alt_id = alt_thread.get("rag_thread_id")
                    if not alt_id or alt_id == thread_for_excerpt:
                        continue
                    pdf_excerpt = get_section_text(alt_id, session.rag_owner_id, pdf_section, max_chars=4000)
                    if pdf_excerpt.strip():
                        break

    tutor_state = _load_tutor_state(session)
    assist_level = min(max(int(tutor_state.get("assist_level") or 1), 1), 5)
    history = tutor_state.get("messages") or []

    context = tutor_service.build_deficiency_context(
        weak_topics_json=session.weak_topics_json,
        current_question=current_q,
        pdf_excerpt=pdf_excerpt,
        assist_level=assist_level,
    )
    reply = tutor_service.tutor_chat(
        message,
        api_key=api_key,
        mode="deficiency",
        context=context,
        history=history,
        assist_level=assist_level,
    )

    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": reply})
    tutor_state["messages"] = history
    tutor_state["assist_level"] = min(assist_level + 1, 5)
    tutor_state["question_index"] = session.current_index
    _save_tutor_state(session, tutor_state)
    db.commit()

    return {
        "reply": reply,
        "session_id": session_id,
        "assist_level": assist_level,
        "assist_level_label": tutor_service.get_deficiency_assist_level_label(assist_level),
        "next_assist_level": tutor_state["assist_level"],
        "next_assist_level_label": tutor_service.get_deficiency_assist_level_label(
            tutor_state["assist_level"]
        ),
    }


def _mark_learning_path_chat_complete(student_id: int) -> None:
    from app.services.lms import learning_path_service

    path = learning_path_service.get_active_path_for_student(student_id)
    if not path:
        return
    db = get_db()
    for item in path.items:
        if item.item_type == "practice" and item.item_id == 0:
            item.status = "completed"
            item.completed_at = datetime.utcnow()
    path.status = "completed"
    db.commit()
