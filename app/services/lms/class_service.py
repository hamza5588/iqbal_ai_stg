"""Class and enrollment service."""
from __future__ import annotations

import secrets
import string
from typing import List, Optional

from app.models.database_models import User as DBUser
from app.models.lms_models import ClassEnrollment, SchoolClass
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError
from app.services.lms.grade_utils import (
    GRADE_OPTIONS,
    format_grade_label,
    grades_match,
    normalize_grade,
    parse_teaching_grades,
    teacher_can_teach_grade,
)
from app.utils.db import get_db

def _generate_join_code(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _get_user(user_id: int) -> Optional[DBUser]:
    db = get_db()
    return db.query(DBUser).filter(DBUser.id == user_id).first()


def get_student_grade(student_id: int) -> Optional[str]:
    user = _get_user(student_id)
    if not user:
        return None
    return normalize_grade(user.class_standard)


def get_teacher_grades(teacher_id: int) -> List[str]:
    user = _get_user(teacher_id)
    if not user:
        return []
    return parse_teaching_grades(user.class_standard)


def set_user_grade(user_id: int, grade_level: str, role: Optional[str] = None) -> DBUser:
    """Set class_standard for a student or teaching grades for a teacher."""
    db = get_db()
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise LMSNotFoundError(f"User {user_id} not found")
    normalized = normalize_grade(grade_level)
    if not normalized:
        raise LMSValidationError(f"Invalid grade level: {grade_level}")
    if role and user.role != role:
        raise LMSValidationError(f"User is not a {role}")
    user.class_standard = normalized if user.role == "student" else grade_level.strip()
    db.commit()
    db.refresh(user)
    return user


def set_teacher_grades(teacher_id: int, grades: List[str]) -> DBUser:
    db = get_db()
    user = db.query(DBUser).filter(DBUser.id == teacher_id).first()
    if not user:
        raise LMSNotFoundError(f"Teacher {teacher_id} not found")
    if user.role not in ("teacher", "admin"):
        raise LMSValidationError("User is not a teacher")
    normalized = []
    for g in grades:
        ng = normalize_grade(g)
        if ng and ng not in normalized:
            normalized.append(ng)
    if not normalized:
        raise LMSValidationError("Provide at least one valid grade")
    user.class_standard = ",".join(normalized)
    db.commit()
    db.refresh(user)
    return user


def create_class(
    teacher_id: int,
    name: str,
    description: Optional[str] = None,
    grade_level: Optional[str] = None,
) -> SchoolClass:
    teacher = _get_user(teacher_id)
    if not teacher or teacher.role not in ("teacher", "admin"):
        raise LMSValidationError("Only teachers can create classes")

    normalized_grade = normalize_grade(grade_level)
    if not normalized_grade:
        raise LMSValidationError("Grade level is required (e.g. 8 for 8th grade)")

    if not teacher_can_teach_grade(teacher.class_standard, normalized_grade):
        assigned = parse_teaching_grades(teacher.class_standard)
        label = ", ".join(format_grade_label(g) for g in assigned) if assigned else "none"
        raise LMSValidationError(
            f"You are assigned to teach {label}. Cannot create a {format_grade_label(normalized_grade)} class."
        )

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
        grade_level=normalized_grade,
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

    student_grade = get_student_grade(student_id)
    if school_class.grade_level and not grades_match(student_grade, school_class.grade_level):
        raise LMSValidationError(
            f"This class is for {format_grade_label(school_class.grade_level)} students only. "
            f"Your profile grade is {format_grade_label(student_grade) or 'not set'}."
        )

    return _enroll_student_in_class(school_class.id, student_id)


def _enroll_student_in_class(class_id: int, student_id: int) -> ClassEnrollment:
    db = get_db()
    existing = (
        db.query(ClassEnrollment)
        .filter(
            ClassEnrollment.class_id == class_id,
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
        class_id=class_id,
        student_id=student_id,
        status="active",
    )
    db.add(enrollment)
    db.commit()
    db.refresh(enrollment)
    return enrollment


def teacher_add_student(class_id: int, student_id: int, teacher_id: int) -> ClassEnrollment:
    """Teacher manually adds a student who matches the class grade."""
    if not teacher_owns_class(teacher_id, class_id):
        raise LMSValidationError("Not authorized")
    school_class = get_class_by_id(class_id)
    student = _get_user(student_id)
    if not student or student.role != "student":
        raise LMSNotFoundError("Student not found")

    if school_class.grade_level and not grades_match(student.class_standard, school_class.grade_level):
        raise LMSValidationError(
            f"Student is {format_grade_label(student.class_standard)} — "
            f"class requires {format_grade_label(school_class.grade_level)}."
        )
    return _enroll_student_in_class(class_id, student_id)


def remove_student_from_class(class_id: int, student_id: int, teacher_id: int) -> None:
    if not teacher_owns_class(teacher_id, class_id):
        raise LMSValidationError("Not authorized")
    db = get_db()
    enr = (
        db.query(ClassEnrollment)
        .filter(
            ClassEnrollment.class_id == class_id,
            ClassEnrollment.student_id == student_id,
            ClassEnrollment.status == "active",
        )
        .first()
    )
    if not enr:
        raise LMSNotFoundError("Student not enrolled in this class")
    enr.status = "removed"
    db.commit()


def list_eligible_students(class_id: int, teacher_id: int) -> List[dict]:
    """Students matching class grade who are not yet enrolled."""
    if not teacher_owns_class(teacher_id, class_id):
        raise LMSValidationError("Not authorized")
    school_class = get_class_by_id(class_id)
    db = get_db()
    enrolled_ids = {
        e.student_id
        for e in db.query(ClassEnrollment)
        .filter(ClassEnrollment.class_id == class_id, ClassEnrollment.status == "active")
        .all()
    }
    students = db.query(DBUser).filter(DBUser.role == "student").order_by(DBUser.username).all()
    eligible = []
    for s in students:
        if s.id in enrolled_ids:
            continue
        if school_class.grade_level and not grades_match(s.class_standard, school_class.grade_level):
            continue
        eligible.append(
            {
                "student_id": s.id,
                "username": s.username,
                "email": s.useremail,
                "grade_level": normalize_grade(s.class_standard),
                "grade_label": format_grade_label(s.class_standard),
            }
        )
    return eligible


def list_class_students_detailed(class_id: int, teacher_id: int) -> List[dict]:
    if not teacher_owns_class(teacher_id, class_id):
        raise LMSValidationError("Not authorized")
    from app.services.lms.performance_service import get_overall_progress, get_student_mastery, WEAK_THRESHOLD

    enrollments = list_class_students(class_id)
    roster = []
    for enr in enrollments:
        user = _get_user(enr.student_id)
        mastery = get_student_mastery(enr.student_id)
        weak = [m for m in mastery if m.get("mastery_status") == "weak"]
        progress = get_overall_progress(enr.student_id)
        roster.append(
            {
                "student_id": enr.student_id,
                "username": user.username if user else None,
                "email": user.useremail if user else None,
                "grade_level": normalize_grade(user.class_standard) if user else None,
                "grade_label": format_grade_label(user.class_standard) if user else None,
                "enrolled_at": enr.enrolled_at.isoformat() if enr.enrolled_at else None,
                "overall_progress": progress,
                "weak_topic_count": len(weak),
                "is_struggling": len(weak) >= 2 or progress < WEAK_THRESHOLD,
            }
        )
    return roster

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


def update_class(
    class_id: int,
    teacher_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    grade_level: Optional[str] = None,
) -> SchoolClass:
    if not teacher_owns_class(teacher_id, class_id):
        raise LMSValidationError("Not authorized")
    db = get_db()
    school_class = get_class_by_id(class_id)
    if name is not None:
        school_class.name = name
    if description is not None:
        school_class.description = description
    if grade_level is not None:
        normalized = normalize_grade(grade_level)
        if not normalized:
            raise LMSValidationError(f"Invalid grade level: {grade_level}")
        teacher = _get_user(teacher_id)
        if teacher and not teacher_can_teach_grade(teacher.class_standard, normalized):
            raise LMSValidationError("You are not assigned to teach this grade level")
        school_class.grade_level = normalized
    db.commit()
    db.refresh(school_class)
    return school_class


def archive_class(class_id: int, teacher_id: int) -> SchoolClass:
    if not teacher_owns_class(teacher_id, class_id):
        raise LMSValidationError("Not authorized")
    db = get_db()
    school_class = get_class_by_id(class_id)
    school_class.is_active = False
    db.commit()
    db.refresh(school_class)
    return school_class


def get_grade_options() -> List[dict]:
    return [{"value": g, "label": format_grade_label(g)} for g in GRADE_OPTIONS]
