"""Diagnostic retake requests: a student asks, a teacher (or admin) approves."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from app.models.database_models import User
from app.models.lms_models import (
    Assessment,
    AssessmentAttempt,
    ClassEnrollment,
    DiagnosticRetakeRequest,
    SchoolClass,
)
from app.services.lms import class_service
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError
from app.utils.db import get_db

_OPEN = ("pending", "approved")


def _latest(student_id: int, assessment_id: int) -> Optional[DiagnosticRetakeRequest]:
    return (
        get_db()
        .query(DiagnosticRetakeRequest)
        .filter(
            DiagnosticRetakeRequest.student_id == student_id,
            DiagnosticRetakeRequest.assessment_id == assessment_id,
        )
        .order_by(DiagnosticRetakeRequest.id.desc())
        .first()
    )


def status_for_student(student_id: int, assessment_id: int) -> Optional[dict]:
    """Latest request for this diagnostic, or None when the student never asked."""
    row = _latest(student_id, assessment_id)
    if not row or row.status == "used":
        return None
    return {"id": row.id, "status": row.status, "requested_at": row.requested_at.isoformat() if row.requested_at else None}


def request_retake(student_id: int, assessment_id: int) -> dict:
    db = get_db()
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment or assessment.assessment_type != "diagnostic":
        raise LMSNotFoundError("Diagnostic not found")
    if assessment.status != "published":
        raise LMSValidationError("This diagnostic is not available")
    submitted = (
        db.query(AssessmentAttempt.id)
        .filter(
            AssessmentAttempt.student_id == student_id,
            AssessmentAttempt.assessment_id == assessment_id,
            AssessmentAttempt.status == "submitted",
        )
        .first()
    )
    if not submitted:
        raise LMSValidationError("You have not completed this diagnostic yet, so there is nothing to retake.")
    row = _latest(student_id, assessment_id)
    if row and row.status in _OPEN:
        return {"id": row.id, "status": row.status, "created": False}
    row = DiagnosticRetakeRequest(student_id=student_id, assessment_id=assessment_id, status="pending")
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "status": row.status, "created": True}


def consume_approval(student_id: int, assessment_id: int) -> None:
    """Called when a retake attempt is about to be created. Raises unless approved."""
    row = _latest(student_id, assessment_id)
    if not row or row.status != "approved":
        if row and row.status == "pending":
            raise LMSValidationError("Your retake request is waiting for your teacher's approval.")
        if row and row.status == "denied":
            raise LMSValidationError("Your teacher did not approve the retake. You can send a new request.")
        raise LMSValidationError("A retake needs your teacher's approval. Send a retake request first.")
    row.status = "used"
    get_db().flush()


def _to_dict(row: DiagnosticRetakeRequest, student: Optional[User], assessment: Optional[Assessment]) -> dict:
    return {
        "id": row.id,
        "status": row.status,
        "requested_at": row.requested_at.isoformat() if row.requested_at else None,
        "student_id": row.student_id,
        "student_name": (student.full_name or student.username) if student else None,
        "student_grade": student.class_standard if student else None,
        "assessment_id": row.assessment_id,
        "assessment_title": assessment.title if assessment else None,
    }


def list_pending(user_id: int, role: str) -> List[dict]:
    """Admin: every pending request. Teacher: requests from students in their classes."""
    db = get_db()
    q = db.query(DiagnosticRetakeRequest).filter(DiagnosticRetakeRequest.status == "pending")
    if role != "admin":
        student_ids = (
            db.query(ClassEnrollment.student_id)
            .join(SchoolClass, SchoolClass.id == ClassEnrollment.class_id)
            .filter(SchoolClass.teacher_id == user_id, ClassEnrollment.status == "active")
        )
        q = q.filter(DiagnosticRetakeRequest.student_id.in_(student_ids))
    out = []
    for row in q.order_by(DiagnosticRetakeRequest.requested_at).all():
        student = db.query(User).filter(User.id == row.student_id).first()
        assessment = db.query(Assessment).filter(Assessment.id == row.assessment_id).first()
        out.append(_to_dict(row, student, assessment))
    return out


def decide(request_id: int, user_id: int, role: str, approve: bool) -> dict:
    db = get_db()
    row = db.query(DiagnosticRetakeRequest).filter(DiagnosticRetakeRequest.id == request_id).first()
    if not row:
        raise LMSNotFoundError("Retake request not found")
    if role != "admin" and not class_service.teacher_has_student(user_id, row.student_id):
        raise PermissionError("Not your student")
    if row.status != "pending":
        raise LMSValidationError(f"This request was already {row.status}.")
    row.status = "approved" if approve else "denied"
    row.decided_by = user_id
    row.decided_at = datetime.utcnow()
    db.commit()
    return {"id": row.id, "status": row.status}
