"""Learning path service."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from app.models.lms_models import LearningPath, LearningPathItem
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
    return (
        db.query(LearningPath)
        .filter(LearningPath.student_id == student_id, LearningPath.status == "active")
        .order_by(LearningPath.updated_at.desc())
        .first()
    )


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
            )
        )
    db.commit()
    db.refresh(path)
    return path


def mark_item_complete(path_id: int, item_id: int) -> LearningPathItem:
    db = get_db()
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
    return row


def get_path_with_items(student_id: int) -> Optional[dict]:
    path = get_active_path_for_student(student_id)
    if not path:
        return None
    return {
        "id": path.id,
        "title": path.title,
        "status": path.status,
        "items": [
            {
                "id": i.id,
                "item_type": i.item_type,
                "item_id": i.item_id,
                "sort_order": i.sort_order,
                "status": i.status,
                "completed_at": i.completed_at.isoformat() if i.completed_at else None,
            }
            for i in sorted(path.items, key=lambda x: x.sort_order)
        ],
    }
