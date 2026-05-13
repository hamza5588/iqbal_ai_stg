"""Authorization helpers for Phase 3 student lesson access."""
from __future__ import annotations

from typing import Optional, Set

from sqlalchemy.orm import Session

from app.models.database_models import Lesson as DBLesson
from app.rbac.roles import Role, is_super_admin_role
from app.services.school import quiz_service
from app.services.school.student_lesson_access import student_may_view_lesson_via_school_placement


def student_allowed_lesson_ids(db: Session, student_user_id: int) -> Set[int]:
    scoped = quiz_service.list_student_scoped_lessons(db, student_user_id=student_user_id)
    return {int(r["id"]) for r in scoped}


def student_can_access_lesson(
    db: Session,
    *,
    student_user_id: int,
    lesson_id: int,
    user_role: Optional[str] = None,
) -> bool:
    """Public lesson, assigned cohort lesson, or super-admin."""
    if user_role and is_super_admin_role(Role.from_string(user_role)):
        return True
    lesson = db.query(DBLesson).filter(DBLesson.id == lesson_id).first()
    if not lesson:
        return False
    if getattr(lesson, "is_public", False):
        return True
    if student_may_view_lesson_via_school_placement(db, student_user_id, lesson_id):
        return True
    allowed = student_allowed_lesson_ids(db, student_user_id)
    return lesson_id in allowed


def teacher_owns_lesson(db: Session, *, teacher_user_id: int, lesson_id: int) -> bool:
    lesson = db.query(DBLesson).filter(DBLesson.id == lesson_id).first()
    return bool(lesson and int(lesson.teacher_id) == int(teacher_user_id))
