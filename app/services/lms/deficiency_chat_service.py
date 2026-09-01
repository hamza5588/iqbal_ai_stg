"""Post-diagnostic deficiency chat — questions from teacher target PDF only."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import List, Optional, Tuple

from app.models.lms_models import AssessmentAttempt, DeficiencyChatSession, StudentProfile
from app.services.lms import assessment_service, tutor_service
from app.services.lms.diagnostic_service import get_teacher_diagnostic_for_student
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError
from app.services.lms.path_generator import get_weak_topics
from app.services.lms.performance_service import analyze_attempt
from app.services.quiz.diagnostic_generator import generate_mcqs_from_content, get_section_text
from app.utils.db import get_db
from app.utils.rag_service import _get_thread_topics

logger = logging.getLogger(__name__)

PRACTICE_QUESTIONS_PER_WEAK_AREA = 2


def _resolve_target_pdf_context(student_id: int) -> Tuple[Optional[str], Optional[int], Optional[int]]:
    """Return (target_rag_thread_id, teacher_id, diagnostic_assessment_id)."""
    db = get_db()
    profile = db.query(StudentProfile).filter(StudentProfile.user_id == student_id).first()
    if profile and profile.diagnostic_assessment_id:
        try:
            assessment = assessment_service.get_assessment(profile.diagnostic_assessment_id)
            src = assessment.pdf_source
            if src and src.target_rag_thread_id:
                return src.target_rag_thread_id, assessment.created_by, assessment.id
        except LMSNotFoundError:
            pass

    teacher_diag = get_teacher_diagnostic_for_student(student_id)
    if teacher_diag and teacher_diag.pdf_source and teacher_diag.pdf_source.target_rag_thread_id:
        return (
            teacher_diag.pdf_source.target_rag_thread_id,
            teacher_diag.created_by,
            teacher_diag.id,
        )
    return None, None, None


def _match_target_section(weak_area_name: str, target_thread_id: str) -> str:
    """Pick the best target PDF heading for a weak area label."""
    name = (weak_area_name or "").strip()
    if not name:
        return name
    topics = _get_thread_topics(target_thread_id).get("topics") or []
    if not topics:
        return name

    name_lower = name.lower()
    best = None
    best_score = 0
    for entry in topics:
        heading = (entry.get("topic") or entry.get("heading") or "").strip()
        if not heading:
            continue
        h_lower = heading.lower()
        if name_lower in h_lower or h_lower in name_lower:
            score = min(len(name_lower), len(h_lower))
            if score > best_score:
                best_score = score
                best = heading
        else:
            overlap = len(set(name_lower.split()) & set(h_lower.split()))
            if overlap > best_score:
                best_score = overlap
                best = heading
    return best or name


def _mcq_to_queue_item(mcq, topic_id: int, topic_name: str, pdf_section: str) -> dict:
    options = [{"label": o.label, "text": o.text} for o in mcq.options]
    return {
        "topic_id": topic_id,
        "topic_name": topic_name,
        "pdf_section": pdf_section,
        "question_text": mcq.question_text,
        "question_latex": mcq.question_latex,
        "options": options,
        "correct_option_index": next(
            (i for i, o in enumerate(mcq.options) if o.label == mcq.correct_option_label),
            0,
        ),
        "source": "target_pdf",
        "answered": False,
        "correct": None,
    }


def _build_question_queue(
    weak_topics: List[dict],
    target_thread_id: str,
    rag_owner_id: int,
) -> List[dict]:
    """Generate Learning Chat MCQs from the teacher's target content PDF only."""
    queue: List[dict] = []
    seen_texts: set[str] = set()

    for entry in weak_topics:
        topic_id = entry.get("topic_id") or 0
        topic_name = (entry.get("topic_name") or "Practice area").strip()
        pdf_section = _match_target_section(topic_name, target_thread_id)

        section_text = get_section_text(target_thread_id, rag_owner_id, pdf_section)
        if not section_text.strip():
            logger.warning("No target PDF text for weak area %s (section %s)", topic_name, pdf_section)
            continue

        try:
            mcqs = generate_mcqs_from_content(
                section_text, pdf_section, PRACTICE_QUESTIONS_PER_WEAK_AREA
            )
            for mcq in mcqs:
                item = _mcq_to_queue_item(mcq, topic_id, topic_name, pdf_section)
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
    """Start Learning Chat — questions generated from teacher target PDF."""
    target_thread_id, rag_owner_id, diag_id = _resolve_target_pdf_context(student_id)

    if not target_thread_id or not rag_owner_id:
        raise LMSValidationError(
            "Your teacher has not uploaded the target content PDF yet. "
            "Learning Chat uses a separate unit/chapter PDF with study material for weak areas."
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

    queue = _build_question_queue(weak, target_thread_id, rag_owner_id)
    if not queue:
        raise LMSValidationError(
            "Could not generate questions from the teacher's target PDF for your weak areas. "
            "Ask your teacher to check the target content PDF covers those topics."
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
    q["answered"] = True
    q["correct"] = is_correct
    if is_correct:
        session.correct_count += 1

    session.current_index = idx + 1
    if session.current_index >= len(questions):
        session.status = "completed"
        _mark_learning_path_chat_complete(student_id)

    _save_questions(session, questions)
    db.commit()

    result = _session_state(session)
    result["last_answer"] = {
        "correct": is_correct,
        "explanation_available": not is_correct,
        "topic_name": q.get("topic_name"),
    }
    return result


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
    if session.rag_thread_id and session.rag_owner_id and pdf_section:
        pdf_excerpt = get_section_text(
            session.rag_thread_id, session.rag_owner_id, pdf_section, max_chars=4000
        )

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
