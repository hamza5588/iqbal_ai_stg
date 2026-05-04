"""Allow enrolled students to read lessons published to their class sections."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.school_learning_models import LectureClassSection
from app.models.school_org_models import ClassEnrollment, ClassSection


def student_may_view_lesson_via_school_placement(db: Session, student_user_id: int, lesson_id: int) -> bool:
    """True if lesson is linked to any class section the student is actively enrolled in."""
    if not student_user_id or not lesson_id:
        return False
    row = (
        db.query(LectureClassSection.lesson_id)
        .join(ClassSection, ClassSection.id == LectureClassSection.class_section_id)
        .join(ClassEnrollment, ClassEnrollment.class_section_id == ClassSection.id)
        .filter(
            LectureClassSection.lesson_id == lesson_id,
            ClassEnrollment.student_user_id == student_user_id,
            ClassEnrollment.status == "active",
            ClassSection.status == "active",
        )
        .first()
    )
    return row is not None
