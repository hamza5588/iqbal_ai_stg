"""Diagnostic assessment helpers — platform admin diagnostic only."""
from __future__ import annotations

from typing import List, Optional

from app.models.lms_models import Assessment
from app.services.lms import assessment_service
from app.utils.db import get_db
from app.services.lms import class_service

DEFAULT_DIAGNOSTIC_TITLE = "Platform Math Diagnostic"


def get_default_diagnostic(grade_level: Optional[str] = None) -> Optional[Assessment]:
    """Return the published platform diagnostic."""
    return assessment_service.get_active_platform_diagnostic(grade_level=grade_level)


def get_teacher_diagnostic_for_student(student_id: int) -> Optional[Assessment]:
    """Deprecated — diagnostics are admin-only; always returns None."""
    return None


def get_student_diagnostic(student_id: int) -> Optional[Assessment]:
    """Diagnostic shown to a student: the published diagnostic for their grade."""
    return get_default_diagnostic(class_service.get_student_grade(student_id))


def get_default_diagnostic_dict() -> Optional[dict]:
    diag = get_default_diagnostic()
    if not diag:
        return None
    return _assessment_to_diagnostic_dict(diag, source="platform")


def get_student_diagnostic_dict(student_id: int) -> Optional[dict]:
    diag = get_student_diagnostic(student_id)
    if not diag:
        return None
    return _assessment_to_diagnostic_dict(diag, source="platform")


def list_admin_diagnostics() -> List[dict]:
    """List all diagnostics for admin management."""
    db = get_db()
    rows = (
        db.query(Assessment)
        .filter(Assessment.assessment_type == "diagnostic")
        .order_by(Assessment.updated_at.desc(), Assessment.id.desc())
        .all()
    )
    result = []
    for a in rows:
        target_pdfs = assessment_service.list_target_pdfs(a.id)
        result.append(
            {
                "id": a.id,
                "title": a.title,
                "status": a.status,
                "grade_level": a.grade_level,
                "question_count": len(a.questions),
                "time_limit_minutes": a.time_limit_minutes,
                "target_pdf_count": len(target_pdfs),
                "target_pdfs": target_pdfs,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
        )
    return result


def _assessment_to_diagnostic_dict(assessment: Assessment, source: str) -> dict:
    time_limit_minutes = assessment.time_limit_minutes
    if assessment.assessment_type == "diagnostic":
        from app.services.lms.diagnostic_timer_service import compute_attempt_deadline

        try:
            time_limit_minutes = max(1, round(compute_attempt_deadline(assessment.id) / 60))
        except Exception:  # noqa: BLE001
            pass
    return {
        "id": assessment.id,
        "title": assessment.title,
        "description": assessment.description,
        "question_count": len(assessment.questions),
        "status": assessment.status,
        "grade_level": assessment.grade_level,
        "source": source,
        "creation_mode": assessment.creation_mode,
        "time_limit_minutes": time_limit_minutes,
    }
