"""Students visible to a teacher via active class enrollments, with quiz performance."""
from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.database_models import User as DBUser
from app.models.school_learning_models import QuizSession, QuizSubmission
from app.models.school_org_models import ClassEnrollment, ClassSection


def list_teacher_students(db: Session, *, teacher_user_id: int) -> List[Dict[str, Any]]:
    teacher_user_id = int(teacher_user_id)

    agg_rows = (
        db.query(
            QuizSubmission.student_user_id.label("sid"),
            func.coalesce(func.sum(QuizSubmission.score), 0).label("sum_s"),
            func.coalesce(func.sum(QuizSubmission.max_score), 0).label("sum_m"),
        )
        .join(QuizSession, QuizSession.id == QuizSubmission.quiz_session_id)
        .filter(QuizSession.teacher_user_id == teacher_user_id)
        .group_by(QuizSubmission.student_user_id)
        .all()
    )
    score_by_student: Dict[int, float] = {}
    for row in agg_rows:
        sid = int(row.sid)
        sm = float(row.sum_m or 0)
        ss = float(row.sum_s or 0)
        if sm > 0:
            score_by_student[sid] = round(ss / sm, 4)

    rows = (
        db.query(DBUser, ClassEnrollment, ClassSection)
        .join(ClassEnrollment, ClassEnrollment.student_user_id == DBUser.id)
        .join(ClassSection, ClassSection.id == ClassEnrollment.class_section_id)
        .filter(
            ClassSection.teacher_user_id == teacher_user_id,
            ClassEnrollment.status == "active",
            ClassSection.status == "active",
            DBUser.role == "student",
        )
        .all()
    )
    out: List[Dict[str, Any]] = []
    seen: set[int] = set()
    for user, _enr, _sec in rows:
        if user.id in seen:
            continue
        seen.add(user.id)
        un = user.username or user.useremail or str(user.id)
        parts = un.replace("_", " ").split()
        score = score_by_student.get(user.id)
        out.append(
            {
                "id": user.id,
                "first_name": parts[0] if parts else un,
                "last_name": " ".join(parts[1:]) if len(parts) > 1 else "",
                "score": score if score is not None else None,
                "quiz_avg_percent": round(score * 100, 1) if score is not None else None,
            }
        )
    return out
