"""
Teacher Capacity & Enrollment Waitlist service.

When a ClassSection has max_capacity set and is full, new enrollment
requests are placed on the waitlist. When a spot opens, the student
at position 1 is automatically promoted and notified.
"""
from __future__ import annotations

from typing import List, Optional

from app.models.phase1_models import EnrollmentWaitlist
from app.models.school_org_models import ClassEnrollment, ClassSection
from app.services.notification_service import create_notification
from app.services.school.errors import SchoolServiceError


def _active_enrollment_count(db, *, class_section_id: int) -> int:
    return (
        db.query(ClassEnrollment)
        .filter_by(class_section_id=class_section_id, status="active")
        .count()
    )


def _next_waitlist_position(db, *, class_section_id: int) -> int:
    max_pos = (
        db.query(EnrollmentWaitlist)
        .filter_by(class_section_id=class_section_id)
        .order_by(EnrollmentWaitlist.position.desc())
        .first()
    )
    return (max_pos.position + 1) if max_pos else 1


def enroll_or_waitlist(
    db,
    *,
    student_id: int,
    class_section_id: int,
) -> dict:
    """
    Attempt to enroll a student in a class section.
    - If max_capacity is None (unlimited) or section is not full: enroll directly.
    - If full: add to waitlist.
    Returns {"status": "enrolled" | "waitlisted", "position": int | None}
    """
    section = db.query(ClassSection).filter_by(id=class_section_id).first()
    if not section:
        raise SchoolServiceError("Class section not found", "not_found", 404)

    if section.status != "active":
        raise SchoolServiceError("Class section is not active", "section_archived", 400)

    # Check if already enrolled or on waitlist
    existing_enroll = (
        db.query(ClassEnrollment)
        .filter_by(class_section_id=class_section_id, student_user_id=student_id)
        .first()
    )
    if existing_enroll and existing_enroll.status == "active":
        raise SchoolServiceError("Student is already enrolled", "already_enrolled", 409)

    existing_waitlist = (
        db.query(EnrollmentWaitlist)
        .filter_by(class_section_id=class_section_id, student_user_id=student_id)
        .first()
    )
    if existing_waitlist:
        raise SchoolServiceError(
            f"Student is already on waitlist at position {existing_waitlist.position}",
            "already_waitlisted",
            409,
        )

    # Check capacity
    if section.max_capacity is None:
        # Unlimited — enroll directly
        return _do_enroll(db, student_id=student_id, class_section_id=class_section_id)

    active_count = _active_enrollment_count(db, class_section_id=class_section_id)
    if active_count < section.max_capacity:
        return _do_enroll(db, student_id=student_id, class_section_id=class_section_id)

    # Section is full — add to waitlist
    position = _next_waitlist_position(db, class_section_id=class_section_id)
    entry = EnrollmentWaitlist(
        class_section_id=class_section_id,
        student_user_id=student_id,
        position=position,
    )
    db.add(entry)
    db.flush()

    create_notification(
        db,
        recipient_id=student_id,
        title="Added to Waitlist",
        message=(
            f"The class section is currently full. You have been placed on the waitlist "
            f"at position {position}. You will be notified when a spot opens."
        ),
        type="waitlist",
    )

    return {"status": "waitlisted", "position": position}


def _do_enroll(db, *, student_id: int, class_section_id: int) -> dict:
    """Create or reactivate an enrollment row."""
    existing = (
        db.query(ClassEnrollment)
        .filter_by(class_section_id=class_section_id, student_user_id=student_id)
        .first()
    )
    if existing:
        existing.status = "active"
    else:
        enrollment = ClassEnrollment(
            class_section_id=class_section_id,
            student_user_id=student_id,
            status="active",
        )
        db.add(enrollment)
    db.flush()
    return {"status": "enrolled", "position": None}


def promote_from_waitlist(db, *, class_section_id: int) -> int:
    """
    After a dropout, promote the position-1 student from the waitlist.
    Returns the count of students promoted (0 or 1).
    """
    section = db.query(ClassSection).filter_by(id=class_section_id).first()
    if not section or section.max_capacity is None:
        return 0

    active_count = _active_enrollment_count(db, class_section_id=class_section_id)
    if active_count >= section.max_capacity:
        return 0  # Still full

    next_in_line = (
        db.query(EnrollmentWaitlist)
        .filter_by(class_section_id=class_section_id)
        .order_by(EnrollmentWaitlist.position)
        .first()
    )
    if not next_in_line:
        return 0

    student_id = next_in_line.student_user_id
    db.delete(next_in_line)
    db.flush()

    # Reorder remaining positions
    remaining = (
        db.query(EnrollmentWaitlist)
        .filter_by(class_section_id=class_section_id)
        .order_by(EnrollmentWaitlist.position)
        .all()
    )
    for i, entry in enumerate(remaining, start=1):
        entry.position = i
    db.flush()

    _do_enroll(db, student_id=student_id, class_section_id=class_section_id)

    create_notification(
        db,
        recipient_id=student_id,
        title="Spot Available — You've Been Enrolled",
        message=(
            "A spot has opened in your requested class section. "
            "You have been automatically enrolled from the waitlist."
        ),
        type="waitlist",
    )
    return 1


def remove_from_waitlist(db, *, student_id: int, class_section_id: int) -> None:
    """Remove a student from the waitlist and reorder positions."""
    entry = (
        db.query(EnrollmentWaitlist)
        .filter_by(class_section_id=class_section_id, student_user_id=student_id)
        .first()
    )
    if not entry:
        raise SchoolServiceError("Student not on waitlist", "not_found", 404)

    removed_position = entry.position
    db.delete(entry)
    db.flush()

    # Shift positions down
    later = (
        db.query(EnrollmentWaitlist)
        .filter(
            EnrollmentWaitlist.class_section_id == class_section_id,
            EnrollmentWaitlist.position > removed_position,
        )
        .all()
    )
    for e in later:
        e.position -= 1
    db.flush()


def get_waitlist(db, *, class_section_id: int) -> List[EnrollmentWaitlist]:
    """Return the waitlist for a class section ordered by position."""
    return (
        db.query(EnrollmentWaitlist)
        .filter_by(class_section_id=class_section_id)
        .order_by(EnrollmentWaitlist.position)
        .all()
    )
