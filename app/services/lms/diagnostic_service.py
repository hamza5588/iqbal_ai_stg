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


def _published_diagnostic_for_grade(grade: Optional[str]) -> Optional[Assessment]:
    """The published diagnostic of ``grade``.

    Diagnostics are per grade. With no grade to go on, one is offered only while a single
    diagnostic is published platform-wide: once several grades have their own, picking
    "the newest" would give the student another grade's paper.
    """
    if grade:
        return get_default_diagnostic(grade)
    published = (
        get_db()
        .query(Assessment)
        .filter(Assessment.assessment_type == "diagnostic", Assessment.status == "published")
        .order_by(Assessment.updated_at.desc(), Assessment.id.desc())
        .limit(2)
        .all()
    )
    return published[0] if len(published) == 1 else None


def get_student_diagnostic(student_id: int) -> Optional[Assessment]:
    """Diagnostic shown to a student: the published diagnostic for their grade."""
    return _published_diagnostic_for_grade(class_service.get_student_diagnostic_grade(student_id))


def get_student_diagnostic_queue(student_id: int) -> List[Assessment]:
    """Diagnostics this student is expected to take, in order.

    Normally just the published diagnostic for their grade. When the admin removed /
    replaced an earlier published one, a student who never submitted it still takes it
    first; a student who did submit it keeps it in the list as "taken". Nothing is
    offered while the grade has no published diagnostic.
    """
    grade = class_service.get_student_diagnostic_grade(student_id)
    current = _published_diagnostic_for_grade(grade)
    if not current:
        return []
    older = [a for a in assessment_service.list_superseded_diagnostics(grade) if a.id != current.id]
    return older + [current]


def _submitted_assessment_ids(student_id: int, assessment_ids: List[int]) -> set:
    from app.models.lms_models import AssessmentAttempt

    if not assessment_ids:
        return set()
    rows = (
        get_db()
        .query(AssessmentAttempt.assessment_id)
        .filter(
            AssessmentAttempt.student_id == student_id,
            AssessmentAttempt.assessment_id.in_(assessment_ids),
            AssessmentAttempt.status == "submitted",
        )
        .distinct()
        .all()
    )
    return {r[0] for r in rows}


def get_student_pending_diagnostics(student_id: int) -> List[Assessment]:
    """Queue entries the student has not submitted yet, in the order to take them."""
    queue = get_student_diagnostic_queue(student_id)
    done = _submitted_assessment_ids(student_id, [a.id for a in queue])
    return [a for a in queue if a.id not in done]


def get_next_student_diagnostic(student_id: int) -> Optional[Assessment]:
    """The diagnostic to show the student now: first pending one, else the published one."""
    pending = get_student_pending_diagnostics(student_id)
    if pending:
        return pending[0]
    return get_student_diagnostic(student_id)


def student_diagnostic_queue_summary(student_id: int) -> dict:
    """Queue with per-student status, for the student diagnostic hub."""
    from app.models.lms_models import AssessmentAttempt

    queue = get_student_diagnostic_queue(student_id)
    ids = [a.id for a in queue]
    done = _submitted_assessment_ids(student_id, ids)
    in_progress = set()
    if ids:
        in_progress = {
            r[0]
            for r in get_db()
            .query(AssessmentAttempt.assessment_id)
            .filter(
                AssessmentAttempt.student_id == student_id,
                AssessmentAttempt.assessment_id.in_(ids),
                AssessmentAttempt.status == "in_progress",
            )
            .all()
        }
    items = []
    next_found = False
    for pos, a in enumerate(queue, start=1):
        if a.id in done:
            status = "taken"
        elif not next_found:
            next_found = True
            status = "in_progress" if a.id in in_progress else "available"
        else:
            status = "locked"
        info = _assessment_to_diagnostic_dict(a, source="platform")
        items.append(
            {
                "id": a.id,
                "position": pos,
                "status": status,
                "question_count": info["question_count"],
                "time_limit_minutes": info["time_limit_minutes"],
            }
        )
    pending_count = sum(1 for i in items if i["status"] != "taken")
    message = None
    if pending_count > 1:
        message = (
            f"You have {pending_count} diagnostic assessments. "
            "Please complete the first one, and then complete the next one."
        )
    return {
        "items": items,
        "total_count": len(items),
        "pending_count": pending_count,
        "message": message,
    }


def get_default_diagnostic_dict() -> Optional[dict]:
    diag = get_default_diagnostic()
    if not diag:
        return None
    return _assessment_to_diagnostic_dict(diag, source="platform")


def get_student_diagnostic_dict(student_id: int) -> Optional[dict]:
    diag = get_next_student_diagnostic(student_id)
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
