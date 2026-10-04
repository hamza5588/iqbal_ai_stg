"""Lesson → class assignment (mirrors quiz Assignment flow)."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from app.models.database_models import Lesson
from app.models.lms_models import LessonAssignment
from app.services.lms.class_service import get_class_by_id, list_student_classes
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError
from app.utils.db import get_db


def _get_lesson_row(lesson_id: int) -> Lesson:
    db = get_db()
    lesson = db.query(Lesson).filter(Lesson.id == lesson_id).first()
    if not lesson:
        raise LMSNotFoundError(f"Lesson {lesson_id} not found")
    return lesson


def get_assignment(assignment_id: int) -> LessonAssignment:
    db = get_db()
    row = db.query(LessonAssignment).filter(LessonAssignment.id == assignment_id).first()
    if not row:
        raise LMSNotFoundError(f"Lesson assignment {assignment_id} not found")
    return row


def create_assignment(
    teacher_id: int,
    class_id: int,
    lesson_id: int,
    title: str,
    instructions: Optional[str] = None,
    due_date: Optional[datetime] = None,
) -> LessonAssignment:
    title = (title or "").strip()
    if not title:
        raise LMSValidationError("Assignment title is required")

    lesson = _get_lesson_row(lesson_id)
    if lesson.teacher_id != teacher_id:
        raise LMSValidationError("You can only assign your own lessons")
    if lesson.has_child_version:
        raise LMSValidationError("Assign the latest lesson version, not an archived parent")

    school_class = get_class_by_id(class_id)
    if school_class.teacher_id != teacher_id:
        raise LMSValidationError("Teacher does not own this class")

    db = get_db()
    existing = (
        db.query(LessonAssignment)
        .filter(
            LessonAssignment.lesson_id == lesson_id,
            LessonAssignment.class_id == class_id,
        )
        .first()
    )
    if existing:
        existing.title = title
        existing.instructions = instructions
        existing.due_date = due_date
        if existing.status == "closed":
            existing.status = "draft"
        db.commit()
        db.refresh(existing)
        return existing

    row = LessonAssignment(
        teacher_id=teacher_id,
        class_id=class_id,
        lesson_id=lesson_id,
        title=title,
        instructions=instructions,
        due_date=due_date,
        status="draft",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def publish_assignment(assignment_id: int, teacher_id: int) -> LessonAssignment:
    db = get_db()
    assignment = get_assignment(assignment_id)
    if assignment.teacher_id != teacher_id:
        raise LMSValidationError("Not authorized")

    lesson = _get_lesson_row(assignment.lesson_id)
    if lesson.teacher_id != teacher_id:
        raise LMSValidationError("Not authorized")

    # Publishing to a class also makes the lesson content public for assigned students
    lesson.is_public = True
    assignment.status = "published"
    db.commit()
    db.refresh(assignment)
    return assignment


def publish_lesson_to_class(
    teacher_id: int,
    lesson_id: int,
    class_id: int,
    title: Optional[str] = None,
    due_date: Optional[datetime] = None,
) -> dict:
    """One-shot: create (or reuse) assignment and publish — quiz 'Create & Publish' parity."""
    lesson = _get_lesson_row(lesson_id)
    assign_title = (title or "").strip() or (lesson.title or "Lesson")
    assignment = create_assignment(
        teacher_id=teacher_id,
        class_id=class_id,
        lesson_id=lesson_id,
        title=assign_title,
        due_date=due_date,
    )
    published = publish_assignment(assignment.id, teacher_id)
    return {
        "id": published.id,
        "lesson_id": published.lesson_id,
        "class_id": published.class_id,
        "title": published.title,
        "status": published.status,
        "is_public": True,
    }


def close_assignments_for_lesson(lesson_id: int, teacher_id: int) -> int:
    """When unpublishing a lesson, close its class assignments."""
    db = get_db()
    lesson = _get_lesson_row(lesson_id)
    if lesson.teacher_id != teacher_id:
        raise LMSValidationError("Not authorized")
    rows = (
        db.query(LessonAssignment)
        .filter(
            LessonAssignment.lesson_id == lesson_id,
            LessonAssignment.status == "published",
        )
        .all()
    )
    for row in rows:
        row.status = "closed"
    lesson.is_public = False
    db.commit()
    return len(rows)


def list_assignments_for_lesson(lesson_id: int, teacher_id: int) -> List[dict]:
    db = get_db()
    lesson = _get_lesson_row(lesson_id)
    if lesson.teacher_id != teacher_id:
        raise LMSValidationError("Not authorized")
    rows = (
        db.query(LessonAssignment)
        .filter(LessonAssignment.lesson_id == lesson_id)
        .order_by(LessonAssignment.updated_at.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "class_id": r.class_id,
            "lesson_id": r.lesson_id,
            "title": r.title,
            "status": r.status,
            "due_date": r.due_date.isoformat() if r.due_date else None,
        }
        for r in rows
    ]


def list_published_lesson_ids_for_student(
    student_id: int, class_id: Optional[int] = None
) -> set[int]:
    """Lesson IDs assigned (published) to any of the student's classes."""
    classes = list_student_classes(student_id)
    class_ids = [c.id for c in classes]
    if class_id is not None:
        if class_id not in class_ids:
            return set()
        class_ids = [class_id]
    if not class_ids:
        return set()
    db = get_db()
    rows = (
        db.query(LessonAssignment.lesson_id)
        .filter(
            LessonAssignment.class_id.in_(class_ids),
            LessonAssignment.status == "published",
        )
        .all()
    )
    return {r[0] for r in rows}


def student_has_lesson_assignment(
    student_id: int, lesson_id: int, class_id: Optional[int] = None
) -> bool:
    return lesson_id in list_published_lesson_ids_for_student(student_id, class_id=class_id)


def lesson_has_any_assignments(lesson_id: int) -> bool:
    db = get_db()
    return (
        db.query(LessonAssignment.id)
        .filter(LessonAssignment.lesson_id == lesson_id)
        .first()
        is not None
    )
