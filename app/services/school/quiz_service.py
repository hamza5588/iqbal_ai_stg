"""Publish lectures to sections; quiz sessions (device + screen); submissions and aggregates."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import Float as SAFloat, cast, func, or_
from sqlalchemy.orm import Session, joinedload

from app.models.database_models import Lesson as DBLesson, User as DBUser
from app.models.school_learning_models import LectureClassSection, QuizSession, QuizSubmission
from app.models.school_org_models import ClassEnrollment, ClassSection, SchoolSubject
from app.rbac.roles import Role, is_super_admin_role
from app.rbac.school_permissions import can_manage_roster
from app.services.school.access import (
    can_student_access_class_section,
    can_teacher_use_class_section,
    teacher_affiliated_with_school,
)
from app.services.school.errors import SchoolServiceError
from app.services.school.quiz_ai_service import generate_mcq_payload

logger = logging.getLogger(__name__)


def publish_lesson_to_sections(
    db: Session,
    *,
    lesson_id: int,
    teacher_user_id: int,
    class_section_ids: Sequence[int],
    admin_override: bool = False,
) -> int:
    """
    Link a lesson to one or more class sections (deduplicated).
    Validates ownership, active sections, and teacher–school affiliation unless admin_override.
    """
    lesson = db.query(DBLesson).filter(DBLesson.id == lesson_id).first()
    if not lesson:
        raise SchoolServiceError("Lesson not found", "not_found", 404)
    actor = db.query(DBUser).filter(DBUser.id == teacher_user_id).first()
    role = Role.from_string(actor.role if actor else "student")
    if not admin_override and lesson.teacher_id != teacher_user_id:
        raise SchoolServiceError("You can only publish your own lessons", "forbidden", 403)
    if admin_override and not is_super_admin_role(role):
        raise SchoolServiceError("Admin override not allowed", "forbidden", 403)

    linked = 0
    for csid in class_section_ids:
        sec = db.query(ClassSection).filter(ClassSection.id == int(csid)).first()
        if not sec:
            raise SchoolServiceError(f"Class section {csid} not found", "not_found", 404)
        if sec.status != "active":
            raise SchoolServiceError(f"Class section {csid} is not active", "bad_request", 400)
        if not admin_override:
            if sec.teacher_user_id != teacher_user_id:
                raise SchoolServiceError(f"Not assigned teacher for section {csid}", "forbidden", 403)
            if not teacher_affiliated_with_school(db, teacher_user_id, sec.school_id):
                raise SchoolServiceError("Teacher is not affiliated with this school", "forbidden", 403)
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
    num_questions: int = 5,
    questions: Optional[List[Dict[str, Any]]] = None,
) -> QuizSession:
    """Create an active quiz session; questions may be supplied or AI-generated from lesson content."""
    if delivery_mode not in ("device", "screen"):
        raise SchoolServiceError("delivery_mode must be device or screen", "invalid_mode", 400)
    if not can_teacher_use_class_section(db, teacher_user_id, class_section_id):
        raise SchoolServiceError("Not your class section", "forbidden", 403)

    lesson = db.query(DBLesson).filter(DBLesson.id == lesson_id).first()
    if not lesson:
        raise SchoolServiceError("Lesson not found", "not_found", 404)

    actor = db.query(DBUser).filter(DBUser.id == teacher_user_id).first()
    actor_role = Role.from_string(actor.role if actor else "student")
    # Session row stores the lesson owner so teacher dashboards / results stay correct.
    quiz_teacher_user_id = int(lesson.teacher_id)
    if lesson.teacher_id != teacher_user_id:
        if not is_super_admin_role(actor_role):
            raise SchoolServiceError("Lesson does not belong to this teacher", "forbidden", 403)

    if questions is not None:
        payload = json.dumps(questions)
    else:
        payload = json.dumps(generate_mcq_payload(lesson.content or "", num_questions=num_questions))

    sess = QuizSession(
        lesson_id=lesson_id,
        class_section_id=class_section_id,
        teacher_user_id=quiz_teacher_user_id,
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
        .join(ClassSection, ClassSection.id == ClassEnrollment.class_section_id)
        .filter(
            ClassEnrollment.student_user_id == student_user_id,
            ClassEnrollment.status == "active",
            ClassSection.status == "active",
        )
        .all()
    ]


def get_quiz_session_for_student(db: Session, *, session_id: int, student_user_id: int) -> Dict[str, Any]:
    sess = db.query(QuizSession).filter(QuizSession.id == session_id).first()
    if not sess or sess.status != "active":
        raise SchoolServiceError("Quiz not found", "not_found", 404)
    if not can_student_access_class_section(db, student_user_id, sess.class_section_id):
        raise SchoolServiceError("Not enrolled in this class", "forbidden", 403)
    questions = json.loads(sess.questions_json)
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


def _score_answers(questions: List[Dict[str, Any]], answers: Sequence[int]) -> tuple[Decimal, Decimal, List[int]]:
    if len(answers) != len(questions):
        raise SchoolServiceError("Answer count does not match question count", "invalid_answers", 400)
    correct = 0
    correct_indices: List[int] = []
    for q, ans in zip(questions, answers):
        try:
            ai = int(q.get("correct_index", -1))
            correct_indices.append(ai)
            if int(ans) == ai:
                correct += 1
        except (TypeError, ValueError):
            correct_indices.append(int(q.get("correct_index", 0) or 0))
            continue
    max_score = Decimal(len(questions))
    score = Decimal(correct)
    return score, max_score, correct_indices


def submit_quiz(
    db: Session,
    *,
    session_id: int,
    student_user_id: int,
    answers: Sequence[int],
) -> Dict[str, Any]:
    """Device mode: enrolled student submits their own answers."""
    sess = db.query(QuizSession).filter(QuizSession.id == session_id).first()
    if not sess or sess.status != "active":
        raise SchoolServiceError("Quiz not found", "not_found", 404)
    if not can_student_access_class_section(db, student_user_id, sess.class_section_id):
        raise SchoolServiceError("Not enrolled", "forbidden", 403)
    questions: List[Dict[str, Any]] = json.loads(sess.questions_json)
    score, max_score, correct_indices = _score_answers(questions, answers)
    answers_json = json.dumps([int(a) for a in answers])
    return _persist_submission(
        db,
        session_id=session_id,
        student_user_id=student_user_id,
        answers_json=answers_json,
        score=score,
        max_score=max_score,
        questions=questions,
        correct_indices=correct_indices,
    )


def submit_quiz_answers_for_student_as_staff(
    db: Session,
    *,
    session_id: int,
    actor_user_id: int,
    student_user_id: int,
    answers: Sequence[int],
) -> Dict[str, Any]:
    """
    Screen / paper mode: teacher or coordinator records a student's answers into the same pipeline.
    """
    sess = db.query(QuizSession).filter(QuizSession.id == session_id).first()
    if not sess or sess.status != "active":
        raise SchoolServiceError("Quiz not found", "not_found", 404)
    sec = db.query(ClassSection).filter(ClassSection.id == sess.class_section_id).first()
    if not sec:
        raise SchoolServiceError("Class section missing", "not_found", 404)
    actor = db.query(DBUser).filter(DBUser.id == actor_user_id).first()
    role = Role.from_string(actor.role if actor else "student")
    allowed = False
    if sess.teacher_user_id == actor_user_id:
        allowed = True
    elif can_manage_roster(db, actor_user_id, sec.school_id):
        allowed = True
    elif is_super_admin_role(role):
        allowed = True
    if not allowed:
        raise SchoolServiceError("Not allowed to enter submissions for this quiz", "forbidden", 403)
    if not can_student_access_class_section(db, student_user_id, sess.class_section_id):
        raise SchoolServiceError("Target student is not enrolled in this class", "forbidden", 403)

    questions: List[Dict[str, Any]] = json.loads(sess.questions_json)
    score, max_score, correct_indices = _score_answers(questions, answers)
    answers_json = json.dumps([int(a) for a in answers])
    return _persist_submission(
        db,
        session_id=session_id,
        student_user_id=student_user_id,
        answers_json=answers_json,
        score=score,
        max_score=max_score,
        questions=questions,
        correct_indices=correct_indices,
    )


def _persist_submission(
    db: Session,
    *,
    session_id: int,
    student_user_id: int,
    answers_json: str,
    score: Decimal,
    max_score: Decimal,
    questions: List[Dict[str, Any]],
    correct_indices: List[int],
) -> Dict[str, Any]:
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
    explanations = [q.get("explanation") for q in questions]
    return {
        "score": float(score),
        "max": float(max_score),
        "correct_indices": correct_indices,
        "explanations": explanations,
    }


def grade_quiz_submission(db: Session, *, submission_id: int) -> Dict[str, Any]:
    """Re-grade a stored submission from answers_json vs questions_json (idempotent scoring)."""
    sub = db.query(QuizSubmission).filter(QuizSubmission.id == submission_id).first()
    if not sub:
        raise SchoolServiceError("Submission not found", "not_found", 404)
    sess = db.query(QuizSession).filter(QuizSession.id == sub.quiz_session_id).first()
    if not sess:
        raise SchoolServiceError("Quiz session not found", "not_found", 404)
    questions: List[Dict[str, Any]] = json.loads(sess.questions_json)
    answers = json.loads(sub.answers_json or "[]")
    score, max_score, correct_indices = _score_answers(questions, answers)
    sub.score = score
    sub.max_score = max_score
    db.commit()
    explanations = [q.get("explanation") for q in questions]
    return {
        "submission_id": sub.id,
        "score": float(score),
        "max": float(max_score),
        "correct_indices": correct_indices,
        "explanations": explanations,
    }


def get_quiz_results_for_teacher(db: Session, *, quiz_session_id: int, teacher_user_id: int) -> Dict[str, Any]:
    sess = db.query(QuizSession).filter(QuizSession.id == quiz_session_id).first()
    if not sess:
        raise SchoolServiceError("Not found", "not_found", 404)
    if sess.teacher_user_id != teacher_user_id:
        raise SchoolServiceError("Forbidden", "forbidden", 403)
    subs = (
        db.query(QuizSubmission)
        .options(joinedload(QuizSubmission.quiz_session))
        .filter(QuizSubmission.quiz_session_id == quiz_session_id)
        .all()
    )
    questions = json.loads(sess.questions_json)
    items = []
    for s in subs:
        items.append(
            {
                "student_user_id": s.student_user_id,
                "score": float(s.score) if s.score is not None else None,
                "max_score": float(s.max_score) if s.max_score is not None else None,
                "answers_json": s.answers_json,
                "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
            }
        )
    return {
        "quiz_session_id": quiz_session_id,
        "lesson_id": sess.lesson_id,
        "class_section_id": sess.class_section_id,
        "delivery_mode": sess.delivery_mode,
        "status": sess.status,
        "questions": questions,
        "submissions": items,
    }


def get_quiz_results_for_student(db: Session, *, quiz_session_id: int, student_user_id: int) -> Dict[str, Any]:
    sess = db.query(QuizSession).filter(QuizSession.id == quiz_session_id).first()
    if not sess:
        raise SchoolServiceError("Not found", "not_found", 404)
    sub = (
        db.query(QuizSubmission)
        .filter(
            QuizSubmission.quiz_session_id == quiz_session_id,
            QuizSubmission.student_user_id == student_user_id,
        )
        .first()
    )
    if not sub:
        raise SchoolServiceError("No submission", "not_found", 404)
    questions = json.loads(sess.questions_json)
    answers = json.loads(sub.answers_json or "[]")
    _, _, correct_indices = _score_answers(questions, answers)
    return {
        "quiz_session_id": quiz_session_id,
        "score": float(sub.score) if sub.score is not None else None,
        "max_score": float(sub.max_score) if sub.max_score is not None else None,
        "correct_indices": correct_indices,
        "explanations": [q.get("explanation") for q in questions],
    }


def get_quiz_results_for_coordinator_class(
    db: Session, *, class_section_id: int, actor_user_id: int
) -> Dict[str, Any]:
    sec = db.query(ClassSection).filter(ClassSection.id == class_section_id).first()
    if not sec:
        raise SchoolServiceError("Not found", "not_found", 404)
    if not can_manage_roster(db, actor_user_id, sec.school_id):
        user = db.query(DBUser).filter(DBUser.id == actor_user_id).first()
        if not user or not is_super_admin_role(Role.from_string(user.role or "student")):
            raise SchoolServiceError("Forbidden", "forbidden", 403)
    sessions = db.query(QuizSession).filter(QuizSession.class_section_id == class_section_id).all()
    out = []
    for sess in sessions:
        agg = (
            db.query(func.avg(QuizSubmission.score), func.avg(QuizSubmission.max_score))
            .filter(QuizSubmission.quiz_session_id == sess.id)
            .first()
        )
        out.append(
            {
                "quiz_session_id": sess.id,
                "lesson_id": sess.lesson_id,
                "delivery_mode": sess.delivery_mode,
                "status": sess.status,
                "avg_score": float(agg[0]) if agg and agg[0] is not None else None,
                "avg_max": float(agg[1]) if agg and agg[1] is not None else None,
            }
        )
    return {"class_section_id": class_section_id, "quiz_sessions": out}


def get_quiz_results_for_coordinator_school(db: Session, *, school_id: int, actor_user_id: int) -> Dict[str, Any]:
    if not can_manage_roster(db, actor_user_id, school_id):
        user = db.query(DBUser).filter(DBUser.id == actor_user_id).first()
        if not user or not is_super_admin_role(Role.from_string(user.role or "student")):
            raise SchoolServiceError("Forbidden", "forbidden", 403)
    ratio = cast(QuizSubmission.score, SAFloat) / cast(QuizSubmission.max_score, SAFloat)
    rows = (
        db.query(ClassSection.id, SchoolSubject.name, func.avg(ratio))
        .join(QuizSession, QuizSession.class_section_id == ClassSection.id)
        .join(QuizSubmission, QuizSubmission.quiz_session_id == QuizSession.id)
        .join(SchoolSubject, SchoolSubject.id == ClassSection.subject_id)
        .filter(ClassSection.school_id == school_id, QuizSubmission.max_score.isnot(None), QuizSubmission.max_score > 0)
        .group_by(ClassSection.id, SchoolSubject.name)
        .all()
    )
    return {
        "school_id": school_id,
        "by_class_section": [
            {"class_section_id": cid, "subject_name": sname, "avg_ratio": float(avg) if avg is not None else None}
            for cid, sname, avg in rows
        ],
    }


def list_student_scoped_lessons(db: Session, *, student_user_id: int) -> List[Dict[str, Any]]:
    """
    Lessons published to enrolled active class sections; includes section/subject metadata.
    """
    from app.models.database_models import User as DBUser

    section_ids = _student_section_ids(db, student_user_id)
    if not section_ids:
        return []
    rows = (
        db.query(DBLesson, DBUser.username, ClassSection, SchoolSubject)
        .join(LectureClassSection, LectureClassSection.lesson_id == DBLesson.id)
        .join(ClassSection, ClassSection.id == LectureClassSection.class_section_id)
        .join(SchoolSubject, SchoolSubject.id == ClassSection.subject_id)
        .outerjoin(DBUser, DBUser.id == DBLesson.teacher_id)
        .filter(
            LectureClassSection.class_section_id.in_(section_ids),
            or_(DBLesson.status.is_(None), DBLesson.status.in_(("finalized", "published"))),
        )
        .all()
    )
    out: List[Dict[str, Any]] = []
    seen: set[int] = set()
    for lesson, teacher_username, sec, subj in rows:
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
                "subject_name": subj.name,
                "class_section_id": sec.id,
                "section_code": sec.section,
                "academic_year": sec.academic_year,
                "cohort_grade_level": sec.grade_level,
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
    sec = db.query(ClassSection).filter(ClassSection.id == sess.class_section_id).first()
    actor = db.query(DBUser).filter(DBUser.id == actor_user_id).first()
    role = Role.from_string(actor.role if actor else "student")
    ok = sess.teacher_user_id == actor_user_id
    if not ok and sec and can_manage_roster(db, actor_user_id, sec.school_id):
        ok = True
    if not ok and is_super_admin_role(role):
        ok = True
    if not ok:
        raise SchoolServiceError("Not allowed", "forbidden", 403)

    sess.status = "closed"
    sess.closed_at = datetime.utcnow()
    db.commit()
