"""Diagnostic assessment helpers (A-305, A-307)."""
from __future__ import annotations

from typing import List, Optional

from app.models.lms_models import Assessment
from app.services.lms import class_service
from app.utils.db import get_db

DEFAULT_DIAGNOSTIC_TITLE = "Platform Math Diagnostic"


def _teacher_ids_for_student(student_id: int) -> List[int]:
    classes = class_service.list_student_classes(student_id)
    return list({c.teacher_id for c in classes})


def get_default_diagnostic() -> Optional[Assessment]:
    """Return the published platform default diagnostic, if seeded."""
    db = get_db()
    return (
        db.query(Assessment)
        .filter(
            Assessment.assessment_type == "diagnostic",
            Assessment.status == "published",
            Assessment.title == DEFAULT_DIAGNOSTIC_TITLE,
        )
        .first()
    )


def get_teacher_diagnostic_for_student(student_id: int) -> Optional[Assessment]:
    """Latest published diagnostic from one of the student's class teachers."""
    teacher_ids = _teacher_ids_for_student(student_id)
    if not teacher_ids:
        return None

    db = get_db()
    candidates = (
        db.query(Assessment)
        .filter(
            Assessment.assessment_type == "diagnostic",
            Assessment.status == "published",
            Assessment.created_by.in_(teacher_ids),
        )
        .order_by(Assessment.updated_at.desc(), Assessment.id.desc())
        .all()
    )
    for assessment in candidates:
        if assessment.questions:
            return assessment
    return None


def get_student_diagnostic(student_id: int) -> Optional[Assessment]:
    """
    Diagnostic shown to a student: teacher-published diagnostic for their class(es),
    otherwise the platform default onboarding diagnostic.
    """
    return get_teacher_diagnostic_for_student(student_id) or get_default_diagnostic()


def get_default_diagnostic_dict() -> Optional[dict]:
    diag = get_default_diagnostic()
    if not diag:
        return None
    return _assessment_to_diagnostic_dict(diag, source="platform")


def get_student_diagnostic_dict(student_id: int) -> Optional[dict]:
    teacher_diag = get_teacher_diagnostic_for_student(student_id)
    if teacher_diag:
        return _assessment_to_diagnostic_dict(teacher_diag, source="teacher")
    return get_default_diagnostic_dict()


def _assessment_to_diagnostic_dict(assessment: Assessment, source: str) -> dict:
    return {
        "id": assessment.id,
        "title": assessment.title,
        "description": assessment.description,
        "question_count": len(assessment.questions),
        "status": assessment.status,
        "source": source,
        "creation_mode": assessment.creation_mode,
    }
