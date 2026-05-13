"""
Multi-Year Data Archival service.

Archives class sections, schools, and supports academic year rollover.
Data is never deleted — only marked as archived.
"""
from __future__ import annotations

from typing import Optional

from app.models.school_org_models import ClassEnrollment, ClassSection, School
from app.services.notification_service import bulk_notify, create_notification
from app.services.school.errors import SchoolServiceError


def archive_class_section(db, *, actor_id: int, class_section_id: int) -> ClassSection:
    """
    Archive a class section.
    - Sets status='archived'.
    - Notifies enrolled students and the teacher.
    - Does NOT delete any data.
    """
    section = db.query(ClassSection).filter_by(id=class_section_id).first()
    if not section:
        raise SchoolServiceError("Class section not found", "not_found", 404)

    if section.status == "archived":
        raise SchoolServiceError("Class section is already archived", "already_archived", 400)

    section.status = "archived"
    db.flush()

    # Collect enrolled students and notify them
    active_enrollments = (
        db.query(ClassEnrollment)
        .filter_by(class_section_id=class_section_id, status="active")
        .all()
    )
    student_ids = [e.student_user_id for e in active_enrollments]

    section_name = section.display_name or f"Grade {section.grade_level} - {section.section}"

    if student_ids:
        bulk_notify(
            db,
            recipient_ids=student_ids,
            title="Class Section Archived",
            message=(
                f"The class section '{section_name}' has been archived for the "
                f"{section.academic_year} academic year. Please contact your school for more information."
            ),
            type="info",
        )

    # Notify the teacher
    if section.teacher_user_id:
        create_notification(
            db,
            recipient_id=section.teacher_user_id,
            title="Class Section Archived",
            message=(
                f"Your class section '{section_name}' ({section.academic_year}) "
                "has been archived."
            ),
            type="info",
        )

    return section


def rollover_academic_year(
    db,
    *,
    actor_id: int,
    school_id: int,
    from_year: str,
    to_year: str,
) -> dict:
    """
    Create new ClassSection rows for the next academic year by cloning
    active sections from from_year. Enrollments are NOT copied (students
    must re-enroll for the new year). Returns the count of sections created.
    """
    if from_year == to_year:
        raise SchoolServiceError("from_year and to_year must differ", "validation_error", 400)

    school = db.query(School).filter_by(id=school_id).first()
    if not school:
        raise SchoolServiceError("School not found", "not_found", 404)

    source_sections = (
        db.query(ClassSection)
        .filter_by(school_id=school_id, academic_year=from_year, status="active")
        .all()
    )

    created = 0
    for src in source_sections:
        # Avoid creating a duplicate if a section for to_year already exists
        existing = (
            db.query(ClassSection)
            .filter_by(
                school_id=src.school_id,
                subject_id=src.subject_id,
                grade_level=src.grade_level,
                section=src.section,
                academic_year=to_year,
            )
            .first()
        )
        if existing:
            continue

        new_section = ClassSection(
            school_id=src.school_id,
            subject_id=src.subject_id,
            teacher_user_id=src.teacher_user_id,
            co_teacher_user_id=src.co_teacher_user_id,
            grade_level=src.grade_level,
            section=src.section,
            academic_year=to_year,
            display_name=src.display_name,
            max_capacity=src.max_capacity,
            status="active",
        )
        db.add(new_section)
        created += 1

    db.flush()
    return {"sections_created": created, "from_year": from_year, "to_year": to_year, "school_id": school_id}


def archive_school(db, *, actor_id: int, school_id: int) -> School:
    """
    Archive a school. Cascades to all active class sections (marks them archived).
    Does NOT delete data.
    """
    school = db.query(School).filter_by(id=school_id).first()
    if not school:
        raise SchoolServiceError("School not found", "not_found", 404)

    if school.status == "archived":
        raise SchoolServiceError("School is already archived", "already_archived", 400)

    # Archive all active class sections
    active_sections = (
        db.query(ClassSection).filter_by(school_id=school_id, status="active").all()
    )
    for section in active_sections:
        section.status = "archived"

    school.status = "archived"
    db.flush()

    return school
