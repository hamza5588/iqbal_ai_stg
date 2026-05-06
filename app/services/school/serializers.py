"""JSON-friendly serializers (ISO datetimes) for school + learning APIs."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def school_to_dict(row) -> Dict[str, Any]:
    return {
        "id": row.id,
        "district_id": row.district_id,
        "name": row.name,
        "code": row.code,
        "principal_user_id": row.principal_user_id,
        "status": row.status,
        "created_at": _iso(row.created_at),
    }


def school_subject_to_dict(row) -> Dict[str, Any]:
    return {
        "id": row.id,
        "school_id": row.school_id,
        "name": row.name,
        "short_code": row.short_code,
        "grade_band": row.grade_band,
        "created_at": _iso(row.created_at),
    }


def class_section_to_dict(row) -> Dict[str, Any]:
    return {
        "id": row.id,
        "school_id": row.school_id,
        "subject_id": row.subject_id,
        "teacher_user_id": row.teacher_user_id,
        "grade_level": row.grade_level,
        "section": row.section,
        "academic_year": row.academic_year,
        "display_name": row.display_name,
        "status": row.status,
        "created_at": _iso(row.created_at),
    }


def roster_entry_to_dict(row) -> Dict[str, Any]:
    return {
        "id": row.id,
        "class_section_id": row.class_section_id,
        "roll_number": row.roll_number,
        "first_name": row.first_name,
        "last_name": row.last_name,
        "linked_student_user_id": row.linked_student_user_id,
        "status": row.status,
        "created_at": _iso(row.created_at),
    }


def class_enrollment_to_dict(row) -> Dict[str, Any]:
    return {
        "id": row.id,
        "class_section_id": row.class_section_id,
        "student_user_id": row.student_user_id,
        "status": row.status,
        "created_at": _iso(row.created_at),
    }


def quiz_session_to_dict(row) -> Dict[str, Any]:
    return {
        "id": row.id,
        "lesson_id": row.lesson_id,
        "class_section_id": row.class_section_id,
        "teacher_user_id": row.teacher_user_id,
        "delivery_mode": row.delivery_mode,
        "status": row.status,
        "created_at": _iso(row.created_at),
        "closed_at": _iso(row.closed_at),
    }


def quiz_submission_to_dict(row) -> Dict[str, Any]:
    return {
        "id": row.id,
        "quiz_session_id": row.quiz_session_id,
        "student_user_id": row.student_user_id,
        "answers_json": row.answers_json,
        "score": float(row.score) if row.score is not None else None,
        "max_score": float(row.max_score) if row.max_score is not None else None,
        "submitted_at": _iso(row.submitted_at),
    }
