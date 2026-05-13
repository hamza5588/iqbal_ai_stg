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


# ---------------------------------------------------------------------------
# Phase 1: Cross-school isolation guard
# ---------------------------------------------------------------------------

def assert_same_school_scope(db: Session, actor_id: int, target_user_id: int) -> None:
    """
    Verify that a school_admin / principal / coordinator can only manage users
    who are affiliated with one of their schools.

    For super-admin roles (platform_admin, district_admin, admin) this is a no-op.
    Raises SchoolServiceError(403) if the actor does not share a school with the target.
    """
    from app.models.database_models import User as DBUser
    from app.services.school.errors import SchoolServiceError

    actor = db.query(DBUser).filter_by(id=actor_id).first()
    if not actor:
        raise SchoolServiceError("Actor not found", "not_found", 404)

    # Super-admins bypass school-scope restriction
    from app.rbac.roles import SUPER_ADMIN_ROLES
    if actor.role in SUPER_ADMIN_ROLES:
        return

    # Gather the actor's affiliated school IDs
    actor_school_ids = set(user_manageable_school_ids(db, actor_id).school_ids)
    if not actor_school_ids:
        raise SchoolServiceError("Actor has no school affiliation", "forbidden", 403)

    # Gather the target's affiliated school IDs
    target_school_ids = set(user_manageable_school_ids(db, target_user_id).school_ids)
    # Also check teacher affiliation and enrollment
    from app.models.school_org_models import TeacherSchoolAffiliation, ClassEnrollment, ClassSection
    teacher_aff = db.query(TeacherSchoolAffiliation).filter_by(teacher_user_id=target_user_id).all()
    for ta in teacher_aff:
        target_school_ids.add(ta.school_id)
    enrolled = db.query(ClassEnrollment).filter_by(student_user_id=target_user_id).all()
    for e in enrolled:
        sec = db.query(ClassSection).filter_by(id=e.class_section_id).first()
        if sec:
            target_school_ids.add(sec.school_id)

    if not actor_school_ids.intersection(target_school_ids):
        raise SchoolServiceError(
            "Actor cannot manage users outside their school scope",
            "forbidden",
            403,
        )
