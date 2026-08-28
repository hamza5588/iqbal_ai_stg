"""Class and enrollment service."""
from __future__ import annotations

import secrets
import string
from typing import List, Optional

from app.models.lms_models import ClassEnrollment, SchoolClass
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError
from app.utils.db import get_db


def _generate_join_code(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def create_class(
    teacher_id: int,
    name: str,
    description: Optional[str] = None,
    grade_level: Optional[str] = None,
) -> SchoolClass:
    db = get_db()
    for _ in range(10):
        join_code = _generate_join_code()
        exists = db.query(SchoolClass).filter(SchoolClass.join_code == join_code).first()
        if not exists:
            break
    else:
        raise LMSValidationError("Could not generate unique join code")

    school_class = SchoolClass(
        teacher_id=teacher_id,
        name=name,
        description=description,
        grade_level=grade_level,
        join_code=join_code,
    )
    db.add(school_class)
    db.commit()
    db.refresh(school_class)
    return school_class


def get_class_by_id(class_id: int) -> SchoolClass:
    db = get_db()
    school_class = db.query(SchoolClass).filter(SchoolClass.id == class_id).first()
    if not school_class:
        raise LMSNotFoundError(f"Class {class_id} not found")
    return school_class


def list_teacher_classes(teacher_id: int, active_only: bool = True) -> List[SchoolClass]:
    db = get_db()
    q = db.query(SchoolClass).filter(SchoolClass.teacher_id == teacher_id)
    if active_only:
        q = q.filter(SchoolClass.is_active.is_(True))
    return q.order_by(SchoolClass.name).all()


def list_student_classes(student_id: int) -> List[SchoolClass]:
    db = get_db()
    return (
        db.query(SchoolClass)
        .join(ClassEnrollment, ClassEnrollment.class_id == SchoolClass.id)
        .filter(
            ClassEnrollment.student_id == student_id,
            ClassEnrollment.status == "active",
            SchoolClass.is_active.is_(True),
        )
        .order_by(SchoolClass.name)
        .all()
    )


def enroll_student(join_code: str, student_id: int) -> ClassEnrollment:
    db = get_db()
    school_class = (
        db.query(SchoolClass)
        .filter(SchoolClass.join_code == join_code.upper(), SchoolClass.is_active.is_(True))
        .first()
    )
    if not school_class:
        raise LMSNotFoundError("Invalid join code")

    existing = (
        db.query(ClassEnrollment)
        .filter(
            ClassEnrollment.class_id == school_class.id,
            ClassEnrollment.student_id == student_id,
        )
        .first()
    )
    if existing:
        if existing.status != "active":
            existing.status = "active"
            db.commit()
            db.refresh(existing)
        return existing

    enrollment = ClassEnrollment(
        class_id=school_class.id,
        student_id=student_id,
        status="active",
    )
    db.add(enrollment)
    db.commit()
    db.refresh(enrollment)
    return enrollment


def list_class_students(class_id: int) -> List[ClassEnrollment]:
    db = get_db()
    return (
        db.query(ClassEnrollment)
        .filter(ClassEnrollment.class_id == class_id, ClassEnrollment.status == "active")
        .all()
    )


def teacher_owns_class(teacher_id: int, class_id: int) -> bool:
    db = get_db()
    return (
        db.query(SchoolClass)
        .filter(
            SchoolClass.id == class_id,
            SchoolClass.teacher_id == teacher_id,
            SchoolClass.is_active.is_(True),
        )
        .first()
        is not None
    )


def student_in_class(student_id: int, class_id: int) -> bool:
    db = get_db()
    return (
        db.query(ClassEnrollment)
        .filter(
            ClassEnrollment.class_id == class_id,
            ClassEnrollment.student_id == student_id,
            ClassEnrollment.status == "active",
        )
        .first()
        is not None
    )
