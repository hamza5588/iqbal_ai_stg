"""
Parent-Student Linking service.

Parents request to link to a student account using the student's user ID.
The student must approve the link. Parent access is view-only (enforced here).
"""
from __future__ import annotations

from datetime import datetime
from typing import List

from app.models.database_models import User
from app.models.phase1_models import ParentStudentLink
from app.services.notification_service import create_notification
from app.services.school.errors import SchoolServiceError


def request_link(db, *, parent_id: int, student_id: int) -> ParentStudentLink:
    """
    Parent requests to link with a student.
    Creates a ParentStudentLink with status='pending'.
    Notifies the student.
    """
    parent = db.query(User).filter_by(id=parent_id).first()
    if not parent or parent.role != "parent":
        raise SchoolServiceError("Only parent accounts can request links", "forbidden", 403)

    student = db.query(User).filter_by(id=student_id).first()
    if not student or student.role != "student":
        raise SchoolServiceError("Target user is not a student", "not_found", 404)

    existing = db.query(ParentStudentLink).filter_by(parent_id=parent_id, student_id=student_id).first()
    if existing:
        if existing.status == "approved":
            raise SchoolServiceError("Already linked to this student", "already_linked", 409)
        if existing.status == "pending":
            raise SchoolServiceError("Link request already pending", "already_pending", 409)
        # If rejected, allow re-request by updating status back to pending
        existing.status = "pending"
        existing.requested_at = datetime.utcnow()
        existing.resolved_at = None
        existing.resolved_by_id = None
        db.flush()
        link = existing
    else:
        link = ParentStudentLink(parent_id=parent_id, student_id=student_id)
        db.add(link)
        db.flush()

    create_notification(
        db,
        recipient_id=student_id,
        title="Parent Link Request",
        message=(
            f"A parent account ({parent.username}) has requested to link with your account. "
            "Please approve or reject this request."
        ),
        type="parent_link",
        action_link="/api/parent/link-requests",
    )

    return link


def resolve_link(
    db,
    *,
    resolver_id: int,
    link_id: int,
    approved: bool,
) -> ParentStudentLink:
    """
    Student (or admin) approves or rejects a parent link request.
    Only the linked student or a super-admin can resolve.
    """
    link = db.query(ParentStudentLink).filter_by(id=link_id).first()
    if not link:
        raise SchoolServiceError("Link request not found", "not_found", 404)

    if link.status != "pending":
        raise SchoolServiceError("Link request is no longer pending", "not_pending", 400)

    resolver = db.query(User).filter_by(id=resolver_id).first()
    if not resolver:
        raise SchoolServiceError("Resolver not found", "not_found", 404)

    # Only the student or a super-admin can resolve
    from app.rbac.roles import SUPER_ADMIN_ROLES
    if resolver.role not in SUPER_ADMIN_ROLES and resolver_id != link.student_id:
        raise SchoolServiceError("Only the linked student can approve or reject", "forbidden", 403)

    link.status = "approved" if approved else "rejected"
    link.resolved_at = datetime.utcnow()
    link.resolved_by_id = resolver_id
    db.flush()

    # Notify the parent
    status_text = "approved" if approved else "rejected"
    create_notification(
        db,
        recipient_id=link.parent_id,
        title=f"Parent Link Request {status_text.capitalize()}",
        message=f"Your request to link with the student has been {status_text}.",
        type="parent_link",
    )

    return link


def get_children(db, *, parent_id: int) -> List[User]:
    """Return the list of approved-linked students for a parent."""
    links = (
        db.query(ParentStudentLink)
        .filter_by(parent_id=parent_id, status="approved")
        .all()
    )
    student_ids = [lnk.student_id for lnk in links]
    if not student_ids:
        return []
    return db.query(User).filter(User.id.in_(student_ids)).all()


def get_parent_links_for_student(db, *, student_id: int) -> List[ParentStudentLink]:
    """Return all parent link requests for a student (any status)."""
    return (
        db.query(ParentStudentLink)
        .filter_by(student_id=student_id)
        .order_by(ParentStudentLink.requested_at.desc())
        .all()
    )


def remove_link(db, *, actor_id: int, link_id: int) -> None:
    """Remove an approved parent-student link (actor must be parent, student, or admin)."""
    link = db.query(ParentStudentLink).filter_by(id=link_id).first()
    if not link:
        raise SchoolServiceError("Link not found", "not_found", 404)

    actor = db.query(User).filter_by(id=actor_id).first()
    from app.rbac.roles import SUPER_ADMIN_ROLES
    if actor.role not in SUPER_ADMIN_ROLES and actor_id not in (link.parent_id, link.student_id):
        raise SchoolServiceError("Not authorized to remove this link", "forbidden", 403)

    db.delete(link)
    db.flush()


def get_child_summary(db, *, parent_id: int, student_id: int) -> dict:
    """
    Return a view-only summary of a linked child.
    Raises 403 if no approved link exists.
    """
    link = (
        db.query(ParentStudentLink)
        .filter_by(parent_id=parent_id, student_id=student_id, status="approved")
        .first()
    )
    if not link:
        raise SchoolServiceError("No approved link to this student", "forbidden", 403)

    from app.models.school_org_models import ClassEnrollment, ClassSection
    student = db.query(User).filter_by(id=student_id).first()
    enrollments = (
        db.query(ClassEnrollment)
        .filter_by(student_user_id=student_id, status="active")
        .all()
    )
    section_ids = [e.class_section_id for e in enrollments]
    sections = (
        db.query(ClassSection).filter(ClassSection.id.in_(section_ids)).all()
        if section_ids else []
    )

    return {
        "student_id": student.id,
        "username": student.username,
        "class_standard": student.class_standard,
        "medium": student.medium,
        "preferred_language": student.preferred_language,
        "enrolled_sections": [
            {
                "id": s.id,
                "display_name": s.display_name or f"Grade {s.grade_level} - {s.section}",
                "academic_year": s.academic_year,
            }
            for s in sections
        ],
        "phase4_learning": _phase4_parent_view(db, student_id=student_id),
    }


def _phase4_parent_view(db, *, student_id: int) -> dict:
    """View-only Phase 4 aggregates for linked parent."""
    try:
        from app.services.phase4 import intelligence_service

        snap = intelligence_service.latest_snapshot_dict(db, student_user_id=student_id)
        ret = intelligence_service.retention_map(db, student_user_id=student_id)
        return {
            "pass_probability": (snap or {}).get("pass_probability"),
            "exam_confidence": (snap or {}).get("exam_confidence"),
            "marks_low": (snap or {}).get("marks_low"),
            "marks_high": (snap or {}).get("marks_high"),
            "prediction_disclaimer": intelligence_service.disclaimer(),
            "retention_summary": ret[:20],
        }
    except Exception:
        return {"prediction_disclaimer": "Predictions are not a guarantee."}
