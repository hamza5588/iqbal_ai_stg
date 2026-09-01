"""Learning path service."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from app.models.database_models import Lesson as DBLesson
from app.models.lms_models import Assessment, LearningPath, LearningPathItem, StudentProfile
from app.services.lms import path_generator, student_profile_service
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError
from app.utils.db import get_db


def create_path(student_id: int, title: str) -> LearningPath:
    db = get_db()
    path = LearningPath(student_id=student_id, title=title, status="active")
    db.add(path)
    db.commit()
    db.refresh(path)
    return path


def get_path(path_id: int) -> LearningPath:
    db = get_db()
    path = db.query(LearningPath).filter(LearningPath.id == path_id).first()
    if not path:
        raise LMSNotFoundError(f"Learning path {path_id} not found")
    return path


def get_active_path_for_student(student_id: int) -> Optional[LearningPath]:
    db = get_db()
    profile_path_id = None
    profile = db.query(StudentProfile).filter(StudentProfile.user_id == student_id).first()
    if profile and profile.current_learning_path_id:
        profile_path_id = profile.current_learning_path_id

    if profile_path_id:
        path = (
            db.query(LearningPath)
            .filter(
                LearningPath.id == profile_path_id,
                LearningPath.student_id == student_id,
                LearningPath.status == "active",
            )
            .first()
        )
        if path:
            return path

    return (
        db.query(LearningPath)
        .filter(LearningPath.student_id == student_id, LearningPath.status == "active")
        .order_by(LearningPath.updated_at.desc())
        .first()
    )


def archive_active_paths(student_id: int) -> None:
    db = get_db()
    rows = (
        db.query(LearningPath)
        .filter(LearningPath.student_id == student_id, LearningPath.status == "active")
        .all()
    )
    for path in rows:
        path.status = "archived"
    db.commit()


def add_items(path_id: int, items: List[dict]) -> LearningPath:
    db = get_db()
    path = get_path(path_id)
    for item in items:
        db.add(
            LearningPathItem(
                learning_path_id=path_id,
                item_type=item["item_type"],
                item_id=item["item_id"],
                sort_order=item.get("sort_order", 0),
                label=item.get("label"),
            )
        )
    db.commit()
    db.refresh(path)
    return path


def _resolve_item_title(item_type: str, item_id: int) -> str:
    db = get_db()
    if item_type == "lesson":
        lesson = db.query(DBLesson).filter(DBLesson.id == item_id).first()
        return lesson.title if lesson and lesson.title else f"Lesson #{item_id}"
    if item_type in ("quiz", "practice"):
        quiz = db.query(Assessment).filter(Assessment.id == item_id).first()
        return quiz.title if quiz and quiz.title else f"Quiz #{item_id}"
    if item_type == "reassessment":
        from app.services.lms import curriculum_service

        try:
            topic = curriculum_service.get_topic_by_id(item_id)
            return f"Reassessment: {topic.name}"
        except LMSNotFoundError:
            return f"Reassessment (topic #{item_id})"
    return f"{item_type} #{item_id}"


def get_path_with_items(student_id: int) -> Optional[dict]:
    path = get_active_path_for_student(student_id)
    if not path:
        return None

    items_out = []
    for i in sorted(path.items, key=lambda x: x.sort_order):
        items_out.append(
            {
                "id": i.id,
                "item_type": i.item_type,
                "item_id": i.item_id,
                "sort_order": i.sort_order,
                "status": i.status,
                "label": i.label or _resolve_item_title(i.item_type, i.item_id),
                "title": _resolve_item_title(i.item_type, i.item_id),
                "completed_at": i.completed_at.isoformat() if i.completed_at else None,
            }
        )

    current_step = next((it for it in items_out if it["status"] != "completed"), None)
    completed_count = sum(1 for it in items_out if it["status"] == "completed")

    return {
        "id": path.id,
        "title": path.title,
        "status": path.status,
        "items": items_out,
        "current_step": current_step,
        "completed_count": completed_count,
        "total_count": len(items_out),
    }


def mark_item_complete(path_id: int, item_id: int, student_id: int) -> LearningPathItem:
    db = get_db()
    path = get_path(path_id)
    if path.student_id != student_id:
        raise LMSValidationError("Not authorized")

    row = (
        db.query(LearningPathItem)
        .filter(
            LearningPathItem.learning_path_id == path_id,
            LearningPathItem.id == item_id,
        )
        .first()
    )
    if not row:
        raise LMSNotFoundError("Learning path item not found")

    row.status = "completed"
    row.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(row)

    remaining = (
        db.query(LearningPathItem)
        .filter(
            LearningPathItem.learning_path_id == path_id,
            LearningPathItem.status != "completed",
        )
        .count()
    )
    if remaining == 0:
        path.status = "completed"
        db.commit()

    return row


def generate_learning_path(student_id: int, force: bool = False) -> Optional[LearningPath]:
    """Generate a new path from weak topics (P-402)."""
    items = path_generator.build_path_items(student_id)
    if not items:
        if not force:
            return get_active_path_for_student(student_id)
        return None

    archive_active_paths(student_id)
    path = create_path(student_id, "Personalized Learning Path")
    add_items(path.id, items)
    student_profile_service.set_current_learning_path(student_id, path.id)
    return path


def refresh_learning_path(student_id: int) -> Optional[LearningPath]:
    """Regenerate path after reassessment / quiz submit (P-405)."""
    if not path_generator.has_mastery_data(student_id):
        return None
    weak = path_generator.get_weak_topics(student_id)
    if not weak:
        active = get_active_path_for_student(student_id)
        if active:
            active.status = "completed"
            get_db().commit()
        return active
    return generate_learning_path(student_id, force=True)
