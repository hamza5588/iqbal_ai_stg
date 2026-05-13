"""Teacher-facing aggregates with self-study privacy."""
from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.models.phase3_models import StudentLearningPreferences, StudentLearningQuestion


def list_student_questions_for_teacher(
    db: Session,
    *,
    lesson_id: int,
    teacher_user_id: int,
) -> List[Dict[str, Any]]:
    from app.models.database_models import Lesson as DBLesson

    lesson = db.query(DBLesson).filter(DBLesson.id == lesson_id).first()
    if not lesson or int(lesson.teacher_id) != int(teacher_user_id):
        return []

    rows = (
        db.query(StudentLearningQuestion)
        .filter(StudentLearningQuestion.lesson_id == lesson_id)
        .order_by(StudentLearningQuestion.created_at.desc())
        .limit(500)
        .all()
    )
    out: List[Dict[str, Any]] = []
    for r in rows:
        if r.mode == "self_study":
            pref = (
                db.query(StudentLearningPreferences)
                .filter(StudentLearningPreferences.student_user_id == r.student_user_id)
                .first()
            )
            allow = True if pref is None else pref.allow_teacher_view_self_study
            if not allow:
                continue
        out.append(
            {
                "id": r.id,
                "student_user_id": r.student_user_id,
                "question": r.question_text[:1000],
                "mode": r.mode,
                "label": r.understanding_label,
                "critical": r.is_critical,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )
    return out
