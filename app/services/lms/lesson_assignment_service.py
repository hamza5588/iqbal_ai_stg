"""Lesson → class assignment (mirrors quiz Assignment flow)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import or_

from app.models.database_models import Lesson
from app.models.lms_models import LessonAssignment
from app.services.lms.class_service import get_class_by_id, list_student_classes
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError
from app.utils.db import get_db


def to_naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Normalise to the naive-UTC convention used by every DateTime column here.
    Aware values are converted; naive values are assumed to already be UTC."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def parse_iso_datetime_utc(raw) -> Optional[datetime]:
    """Parse an ISO-8601 string from the client (e.g. Date.toISOString()) to naive UTC.
    Returns None for empty input; raises ValueError for malformed input."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if not isinstance(raw, str):
        raise ValueError("Expected an ISO date/time string")
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return to_naive_utc(datetime.fromisoformat(text))


def _iso_utc(value: Optional[datetime]) -> Optional[str]:
    return (value.isoformat() + "Z") if value else None


def _visible_now_conditions():
    """SQL conditions for a lesson assignment students can see right now: published,
    its scheduled release time (publish_at) reached, and its due date not yet passed.
    After the due date the lesson is hidden from students (the teacher still sees it)."""
    now = datetime.utcnow()
    return (
        LessonAssignment.status == "published",
        or_(LessonAssignment.publish_at.is_(None), LessonAssignment.publish_at <= now),
        or_(LessonAssignment.due_date.is_(None), LessonAssignment.due_date > now),
    )


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
    publish_at: Optional[datetime] = None,
) -> LessonAssignment:
    title = (title or "").strip()
    publish_at = to_naive_utc(publish_at)
    due_date = to_naive_utc(due_date)
    # A release time that has already passed means "publish now".
    if publish_at is not None and publish_at <= datetime.utcnow():
        publish_at = None
    # Students lose access once the due date passes, so a past due date would hide the
    # lesson the moment it is published.
    if due_date is not None and due_date <= datetime.utcnow():
        raise LMSValidationError(
            "Due date must be in the future. Students can no longer see the lesson after its due date."
        )
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
        existing.publish_at = publish_at
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
        publish_at=publish_at,
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
    publish_at: Optional[datetime] = None,
) -> dict:
    """One-shot: create (or reuse) assignment and publish — quiz 'Create & Publish' parity.

    With a future `publish_at` the assignment is published but students only see the
    lesson from that moment on (enforced in list_published_lesson_ids_for_student)."""
    lesson = _get_lesson_row(lesson_id)
    assign_title = (title or "").strip() or (lesson.title or "Lesson")
    assignment = create_assignment(
        teacher_id=teacher_id,
        class_id=class_id,
        lesson_id=lesson_id,
        title=assign_title,
        due_date=due_date,
        publish_at=publish_at,
    )
    published = publish_assignment(assignment.id, teacher_id)
    return {
        "id": published.id,
        "lesson_id": published.lesson_id,
        "class_id": published.class_id,
        "title": published.title,
        "status": published.status,
        "is_public": True,
        "publish_at": _iso_utc(published.publish_at),
        "scheduled": published.publish_at is not None,
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
            "publish_at": _iso_utc(r.publish_at),
        }
        for r in rows
    ]


def list_published_lesson_ids_for_student(
    student_id: int, class_id: Optional[int] = None
) -> set[int]:
    """Lesson IDs assigned (published) to any of the student's classes whose scheduled
    release time (publish_at) has been reached. Every student lesson gate builds on this."""
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
            *_visible_now_conditions(),
        )
        .all()
    )
    return {r[0] for r in rows}


def student_has_lesson_assignment(
    student_id: int, lesson_id: int, class_id: Optional[int] = None
) -> bool:
    return lesson_id in list_published_lesson_ids_for_student(student_id, class_id=class_id)


def unreleased_scheduled_lesson_ids(lesson_ids) -> set[int]:
    """Of `lesson_ids`, those that have class assignments but none visible right now (every
    assignment is a draft/closed, scheduled for a future publish_at, or past its due date).
    Keeps such lessons out of non-class-scoped lesson lookups."""
    ids = {int(i) for i in (lesson_ids or []) if i}
    if not ids:
        return set()
    db = get_db()
    assigned = {
        r[0]
        for r in db.query(LessonAssignment.lesson_id)
        .filter(LessonAssignment.lesson_id.in_(ids))
        .all()
    }
    if not assigned:
        return set()
    released = {
        r[0]
        for r in db.query(LessonAssignment.lesson_id)
        .filter(
            LessonAssignment.lesson_id.in_(assigned),
            *_visible_now_conditions(),
        )
        .all()
    }
    return assigned - released


def lesson_has_any_assignments(lesson_id: int) -> bool:
    db = get_db()
    return (
        db.query(LessonAssignment.id)
        .filter(LessonAssignment.lesson_id == lesson_id)
        .first()
        is not None
    )
