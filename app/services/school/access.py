"""
Centralized authorization for school-scoped operations.

Super-admin roles (admin, platform_admin, district_admin, school_admin) may
access all schools until district/school scoping is bound to rows.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, List, Set

from sqlalchemy.orm import Session

from app.models.database_models import User as DBUser
from app.models.school_org_models import (
    ClassEnrollment,
    ClassSection,
    School,
    SchoolCoordinator,
    TeacherSchoolAffiliation,
)
from app.rbac.roles import Role, is_super_admin_role


@dataclass(frozen=True)
class SchoolAccess:
    """Resolved access flags for one actor."""

    user_id: int
    role: Role
    school_ids: FrozenSet[int]
    super_admin: bool


def _role_of(user: DBUser | None) -> Role:
    if not user:
        return Role.STUDENT
    return Role.from_string(user.role or "student")


def user_manageable_school_ids(db: Session, user_id: int) -> SchoolAccess:
    """
    Schools the user may manage (principal, coordinator, or super-admin).
    Teachers are NOT included here — use class-section checks for teaching.
    """
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    role = _role_of(user)

    if is_super_admin_role(role):
        ids = {r[0] for r in db.query(School.id).all()}
        return SchoolAccess(user_id=user_id, role=role, school_ids=frozenset(ids), super_admin=True)

    ids: Set[int] = set()
    if role == Role.PRINCIPAL:
        ids.update(
            r[0]
            for r in db.query(School.id).filter(School.principal_user_id == user_id).all()
        )
    if role == Role.COORDINATOR:
        ids.update(
            r[0]
            for r in db.query(SchoolCoordinator.school_id).filter(SchoolCoordinator.user_id == user_id).all()
        )

    return SchoolAccess(user_id=user_id, role=role, school_ids=frozenset(ids), super_admin=False)


def can_manage_school(db: Session, user_id: int, school_id: int) -> bool:
    acc = user_manageable_school_ids(db, user_id)
    if acc.super_admin:
        return True
    return school_id in acc.school_ids


def can_coordinate_school(db: Session, user_id: int, school_id: int) -> bool:
    """Principal or coordinator (or super-admin) for this school."""
    return can_manage_school(db, user_id, school_id)


def user_teaching_class_section_ids(db: Session, user_id: int) -> List[int]:
    """Class section PKs where this user is the assigned teacher."""
    rows = (
        db.query(ClassSection.id)
        .filter(ClassSection.teacher_user_id == user_id, ClassSection.status == "active")
        .all()
    )
    return [r[0] for r in rows]


def user_enrolled_class_section_ids(db: Session, user_id: int) -> List[int]:
    rows = (
        db.query(ClassSection.id)
        .join(ClassEnrollment, ClassEnrollment.class_section_id == ClassSection.id)
        .filter(
            ClassEnrollment.student_user_id == user_id,
            ClassEnrollment.status == "active",
            ClassSection.status == "active",
        )
        .distinct()
        .all()
    )
    return [r[0] for r in rows]


def can_teacher_use_class_section(db: Session, user_id: int, class_section_id: int) -> bool:
    sec = db.query(ClassSection).filter(ClassSection.id == class_section_id).first()
    if not sec:
        return False
    if sec.teacher_user_id == user_id:
        return True
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if user and is_super_admin_role(_role_of(user)):
        return True
    return False


def teacher_affiliated_with_school(db: Session, teacher_user_id: int, school_id: int) -> bool:
    row = (
        db.query(TeacherSchoolAffiliation)
        .filter(
            TeacherSchoolAffiliation.school_id == school_id,
            TeacherSchoolAffiliation.teacher_user_id == teacher_user_id,
            TeacherSchoolAffiliation.is_active.is_(True),
        )
        .first()
    )
    return row is not None


def can_student_access_class_section(db: Session, student_user_id: int, class_section_id: int) -> bool:
    row = (
        db.query(ClassEnrollment)
        .filter(
            ClassEnrollment.class_section_id == class_section_id,
            ClassEnrollment.student_user_id == student_user_id,
            ClassEnrollment.status == "active",
        )
        .first()
    )
    return row is not None
