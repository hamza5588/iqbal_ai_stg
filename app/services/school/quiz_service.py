"""Publish lectures to sections; create quiz sessions; record submissions and scores."""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Sequence

from sqlalchemy.orm import Session

from app.models.database_models import Lesson as DBLesson
from app.models.school_learning_models import LectureClassSection, QuizSession, QuizSubmission
from app.models.school_org_models import ClassEnrollment, ClassSection
from app.services.school.access import can_coordinate_school, can_student_access_class_section, can_teacher_use_class_section
from app.services.school.errors import SchoolServiceError
from app.services.school.quiz_ai_service import generate_mcq_payload


def publish_lesson_to_sections(
    db: Session,
    *,
    lesson_id: int,
    teacher_user_id: int,
    class_section_ids: Sequence[int],
) -> int:
    lesson = db.query(DBLesson).filter(DBLesson.id == lesson_id).first()
    if not lesson:
        raise SchoolServiceError("Lesson not found", "not_found", 404)
    if lesson.teacher_id != teacher_user_id:
        raise SchoolServiceError("You can only publish your own lessons", "forbidden", 403)

    linked = 0
    for csid in class_section_ids:
        sec = db.query(ClassSection).filter(ClassSection.id == int(csid)).first()
        if not sec:
            continue
        if sec.teacher_user_id != teacher_user_id:
            raise SchoolServiceError(f"Not assigned teacher for section {csid}", "forbidden", 403)
        row = (
            db.query(LectureClassSection)
            .filter(
                LectureClassSection.lesson_id == lesson_id,
                LectureClassSection.class_section_id == int(csid),
            )
            .first()
        )
        if row:
            continue
        db.add(LectureClassSection(lesson_id=lesson_id, class_section_id=int(csid)))
        linked += 1
    db.commit()
    return linked


def create_quiz_session(
    db: Session,
    *,
    lesson_id: int,
    class_section_id: int,
    teacher_user_id: int,
    delivery_mode: str,
    num_questions: int,
) -> QuizSession:
    if delivery_mode not in ("device", "screen"):
        raise SchoolServiceError("delivery_mode must be device or screen", "invalid_mode", 400)
    if not can_teacher_use_class_section(db, teacher_user_id, class_section_id):
        raise SchoolServiceError("Not your class section", "forbidden", 403)

    lesson = db.query(DBLesson).filter(DBLesson.id == lesson_id).first()
    if not lesson:
        raise SchoolServiceError("Lesson not found", "not_found", 404)
    if lesson.teacher_id != teacher_user_id:
        raise SchoolServiceError("Lesson does not belong to this teacher", "forbidden", 403)

    questions = generate_mcq_payload(lesson.content or "", num_questions=num_questions)
    payload = json.dumps(questions)

    sess = QuizSession(
        lesson_id=lesson_id,
        class_section_id=class_section_id,
        teacher_user_id=teacher_user_id,
        delivery_mode=delivery_mode,
        questions_json=payload,
        status="active",
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return sess


def list_active_quizzes_for_student(db: Session, *, student_user_id: int) -> List[Dict[str, Any]]:
    section_ids = _student_section_ids(db, student_user_id)
    if not section_ids:
        return []
    rows = (
        db.query(QuizSession)
        .filter(
            QuizSession.class_section_id.in_(section_ids),
            QuizSession.status == "active",
        )
        .order_by(QuizSession.created_at.desc())
        .all()
    )
    out: List[Dict[str, Any]] = []
    for r in rows:
        sub = (
            db.query(QuizSubmission)
            .filter(
                QuizSubmission.quiz_session_id == r.id,
                QuizSubmission.student_user_id == student_user_id,
            )
            .first()
        )
        out.append(
            {
                "id": r.id,
                "lesson_id": r.lesson_id,
                "class_section_id": r.class_section_id,
                "delivery_mode": r.delivery_mode,
                "already_submitted": sub is not None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )
    return out


def _student_section_ids(db: Session, student_user_id: int) -> List[int]:
    return [
        r[0]
        for r in db.query(ClassEnrollment.class_section_id)
        .filter(ClassEnrollment.student_user_id == student_user_id, ClassEnrollment.status == "active")
        .all()
    ]


def get_quiz_session_for_student(db: Session, *, session_id: int, student_user_id: int) -> Dict[str, Any]:
    sess = db.query(QuizSession).filter(QuizSession.id == session_id).first()
    if not sess or sess.status != "active":
        raise SchoolServiceError("Quiz not found", "not_found", 404)
    if not can_student_access_class_section(db, student_user_id, sess.class_section_id):
        raise SchoolServiceError("Not enrolled in this class", "forbidden", 403)
    questions = json.loads(sess.questions_json)
    # Strip correct answers for client
    stripped = []
    for q in questions:
        stripped.append(
            {
                "id": q.get("id"),
                "prompt": q.get("prompt"),
                "choices": q.get("choices"),
            }
        )
    return {"session_id": sess.id, "delivery_mode": sess.delivery_mode, "questions": stripped}


def submit_quiz(
    db: Session,
    *,
    session_id: int,
    student_user_id: int,
    answers: Sequence[int],
) -> Dict[str, Any]:
    sess = db.query(QuizSession).filter(QuizSession.id == session_id).first()
    if not sess or sess.status != "active":
        raise SchoolServiceError("Quiz not found", "not_found", 404)
    if not can_student_access_class_section(db, student_user_id, sess.class_section_id):
        raise SchoolServiceError("Not enrolled", "forbidden", 403)

    questions: List[Dict[str, Any]] = json.loads(sess.questions_json)
    if len(answers) != len(questions):
        raise SchoolServiceError("Answer count does not match question count", "invalid_answers", 400)

    correct = 0
    for q, ans in zip(questions, answers):
        try:
            ai = int(q.get("correct_index", -1))
            if int(ans) == ai:
                correct += 1
        except (TypeError, ValueError):
            continue

    max_score = Decimal(len(questions))
    score = Decimal(correct)
    answers_json = json.dumps([int(a) for a in answers])

    existing = (
        db.query(QuizSubmission)
        .filter(
            QuizSubmission.quiz_session_id == session_id,
            QuizSubmission.student_user_id == student_user_id,
        )
        .first()
    )
    if existing:
        existing.answers_json = answers_json
        existing.score = score
        existing.max_score = max_score
    else:
        db.add(
            QuizSubmission(
                quiz_session_id=session_id,
                student_user_id=student_user_id,
                answers_json=answers_json,
                score=score,
                max_score=max_score,
            )
        )
    db.commit()
    return {
        "score": float(score),
        "max": float(max_score),
        "correct_indices": [int(q.get("correct_index", 0)) for q in questions],
    }


def list_student_scoped_lessons(db: Session, *, student_user_id: int) -> List[Dict[str, Any]]:
    """
    Lessons published to any class section the student is actively enrolled in.
    Fixes 'school-wide pile' when clients use this endpoint instead of unscoped lists.
    """
    from app.models.database_models import User as DBUser

    section_ids = _student_section_ids(db, student_user_id)
    if not section_ids:
        return []
    rows = (
        db.query(DBLesson, DBUser.username)
        .join(LectureClassSection, LectureClassSection.lesson_id == DBLesson.id)
        .outerjoin(DBUser, DBUser.id == DBLesson.teacher_id)
        .filter(LectureClassSection.class_section_id.in_(section_ids))
        .all()
    )
    out: List[Dict[str, Any]] = []
    seen: set[int] = set()
    for lesson, teacher_username in rows:
        if lesson.id in seen:
            continue
        seen.add(lesson.id)
        out.append(
            {
                "id": lesson.id,
                "title": lesson.title,
                "teacher_id": lesson.teacher_id,
                "teacher_name": teacher_username or "Teacher",
                "grade_level": lesson.grade_level,
                "focus_area": lesson.focus_area,
                "subject": lesson.focus_area or "General",
                "grade": lesson.grade_level or "General",
                "created_at": lesson.created_at.isoformat() if lesson.created_at else None,
                "updated_at": lesson.updated_at.isoformat() if lesson.updated_at else None,
                "status": lesson.status or "finalized",
                "version": lesson.version_number or lesson.version or 1,
                "is_public": bool(lesson.is_public),
            }
        )
    return out


def close_quiz_session(db: Session, *, session_id: int, actor_user_id: int) -> None:
    sess = db.query(QuizSession).filter(QuizSession.id == session_id).first()
    if not sess:
        raise SchoolServiceError("Not found", "not_found", 404)
    if sess.teacher_user_id != actor_user_id:
        sec = db.query(ClassSection).filter(ClassSection.id == sess.class_section_id).first()
        if not sec or not can_coordinate_school(db, actor_user_id, sec.school_id):
            raise SchoolServiceError("Not allowed", "forbidden", 403)

    sess.status = "closed"
    sess.closed_at = datetime.utcnow()
    db.commit()
