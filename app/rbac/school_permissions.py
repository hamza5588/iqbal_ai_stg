"""
Reusable school-scoped permission checks (roles align with users.role CHECK constraint).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.database_models import Lesson as DBLesson, User as DBUser
from app.models.school_learning_models import LectureClassSection
from app.models.school_org_models import ClassEnrollment, ClassSection, SchoolCoordinator
from app.rbac.roles import Role, is_super_admin_role
from app.services.school.access import (
    can_coordinate_school,
    can_manage_school as access_can_manage_school,
    can_student_access_class_section,
    can_teacher_use_class_section,
    teacher_affiliated_with_school,
    user_enrolled_class_section_ids,
)


def is_platform_admin(user: DBUser | None) -> bool:
    return bool(user) and Role.from_string(user.role or "") == Role.PLATFORM_ADMIN


def is_district_admin(user: DBUser | None) -> bool:
    return bool(user) and Role.from_string(user.role or "") == Role.DISTRICT_ADMIN


def is_school_admin(user: DBUser | None) -> bool:
    return bool(user) and Role.from_string(user.role or "") == Role.SCHOOL_ADMIN


def is_principal(user: DBUser | None) -> bool:
    return bool(user) and Role.from_string(user.role or "") == Role.PRINCIPAL


def is_coordinator(user: DBUser | None) -> bool:
    return bool(user) and Role.from_string(user.role or "") == Role.COORDINATOR


def is_teacher(user: DBUser | None) -> bool:
    return bool(user) and Role.from_string(user.role or "") == Role.TEACHER


def is_student(user: DBUser | None) -> bool:
    return bool(user) and Role.from_string(user.role or "") == Role.STUDENT


def can_manage_school(user_id: int, school_id: int, db: Session) -> bool:
    """True if user may assign principals/coordinators or edit school-level settings."""
    return access_can_manage_school(db, user_id, school_id)


def can_manage_roster(db: Session, user_id: int, school_id: int) -> bool:
    """
    Coordinators and school-level admin roles may edit rosters; principals do not
    (per product flow: coordinators operate rosters).
    """
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        return False
    role = Role.from_string(user.role or "student")
    if is_super_admin_role(role):
        return True
    if role == Role.COORDINATOR:
        row = (
            db.query(SchoolCoordinator)
            .filter(SchoolCoordinator.school_id == school_id, SchoolCoordinator.user_id == user_id)
            .first()
        )
        return row is not None
    return False


def can_view_class_section(db: Session, user_id: int, class_section_id: int) -> bool:
    sec = db.query(ClassSection).filter(ClassSection.id == class_section_id).first()
    if not sec:
        return False
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        return False
    role = Role.from_string(user.role or "student")
    if is_super_admin_role(role):
        return True
    if access_can_manage_school(db, user_id, sec.school_id):
        return True
    if sec.teacher_user_id == user_id:
        return True
    if can_student_access_class_section(db, user_id, class_section_id):
        return True
    return False


def can_publish_to_class_section(
    db: Session,
    user_id: int,
    class_section_id: int,
    *,
    admin_override: bool = False,
) -> bool:
    sec = db.query(ClassSection).filter(ClassSection.id == class_section_id).first()
    if not sec or sec.status != "active":
        return False
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        return False
    role = Role.from_string(user.role or "student")
    if admin_override and is_super_admin_role(role):
        return True
    if sec.teacher_user_id == user_id:
        return True
    return False


def can_view_student_lecture(db: Session, user_id: int, lesson_id: int) -> bool:
    """Whether a student may view lesson content via school publish graph."""
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        return False
    role = Role.from_string(user.role or "student")
    if is_super_admin_role(role):
        return True
    if role != Role.STUDENT:
        return False
    section_ids = user_enrolled_class_section_ids(db, user_id)
    if not section_ids:
        return False
    row = (
        db.query(LectureClassSection.lesson_id)
        .filter(
            LectureClassSection.lesson_id == lesson_id,
            LectureClassSection.class_section_id.in_(section_ids),
        )
        .first()
    )
    return row is not None


def can_coordinate_school_public(user_id: int, school_id: int, db: Session) -> bool:
    """Thin wrapper for imports that prefer (user_id, school_id, db) argument order."""
    return can_coordinate_school(db, user_id, school_id)


def teacher_may_publish_to_sections(
    db: Session, teacher_user_id: int, class_section_ids: list[int], *, admin_override: bool = False
) -> bool:
    for csid in class_section_ids:
        if not can_publish_to_class_section(db, teacher_user_id, int(csid), admin_override=admin_override):
            return False
        if not admin_override:
            sec = db.query(ClassSection).filter(ClassSection.id == int(csid)).first()
            if sec and not teacher_affiliated_with_school(db, teacher_user_id, sec.school_id):
                return False
    return True


def lesson_owned_by_teacher(db: Session, lesson_id: int, teacher_user_id: int) -> bool:
    les = db.query(DBLesson).filter(DBLesson.id == lesson_id).first()
    return bool(les and les.teacher_id == teacher_user_id)
