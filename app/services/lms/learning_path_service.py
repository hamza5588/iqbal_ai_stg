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
    if item_type == "practice":
        if item_id == 0:
            return "Learning Chat — practice weak areas"
        quiz = db.query(Assessment).filter(Assessment.id == item_id).first()
        return quiz.title if quiz and quiz.title else f"Practice #{item_id}"
    if item_type == "quiz":
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


def _path_to_dict(path: LearningPath) -> dict:
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


def get_path_with_items(student_id: int) -> Optional[dict]:
    path = get_active_path_for_student(student_id)
    if not path:
        return None
    return _path_to_dict(path)


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
    """Regenerate path after reassessment / quiz / diagnostic submit (P-405)."""
    weak = path_generator.get_weak_topics(student_id)
    if weak:
        return generate_learning_path(student_id, force=True)

    if not path_generator.has_mastery_data(student_id):
        return get_active_path_for_student(student_id)

    active = get_active_path_for_student(student_id)
    if active:
        active.status = "completed"
        get_db().commit()
    return active


def _newest_submitted_attempt_at(student_id: int):
    from app.models.lms_models import AssessmentAttempt

    row = (
        get_db()
        .query(AssessmentAttempt.submitted_at)
        .filter(
            AssessmentAttempt.student_id == student_id,
            AssessmentAttempt.status == "submitted",
        )
        .order_by(AssessmentAttempt.submitted_at.desc())
        .first()
    )
    return row[0] if row and row[0] else None


def _is_untouched_single_practice(path: LearningPath) -> bool:
    items = list(path.items)
    return (
        len(items) == 1
        and items[0].item_type == "practice"
        and items[0].status != "completed"
    )


def ensure_learning_path(student_id: int) -> Optional[dict]:
    """Return the student's current learning path (read-only from the dashboard).

    The path is regenerated only when a new diagnostic/quiz is submitted
    (attempt_service -> refresh_learning_path). This function must NOT spin
    up a fresh "0% done" path just because the last one is finished and
    weak topics still exist - after a Learning Chat session the student's
    practice step IS done; further practice is driven by the live Weak
    Topics tile, not by resetting the path to zero.
    """
    from app.services.lms import assessment_service, performance_service

    profile = student_profile_service.get_or_create_profile(student_id)
    repaired = False
    if profile.diagnostic_assessment_id:
        try:
            assessment = assessment_service.get_assessment(profile.diagnostic_assessment_id)
            repaired = performance_service.repair_diagnostic_topic_meta(assessment)
        except Exception:
            pass

    if not path_generator.has_mastery_data(student_id) or repaired:
        performance_service.rebuild_student_mastery(student_id)

    db = get_db()
    all_paths = (
        db.query(LearningPath)
        .filter(LearningPath.student_id == student_id)
        .order_by(LearningPath.id.desc())
        .all()
    )

    if not all_paths:
        weak = path_generator.get_weak_topics(student_id)
        if weak or path_generator.has_mastery_data(student_id):
            refresh_learning_path(student_id)
        return get_path_with_items(student_id)

    visible = [p for p in all_paths if p.status != "archived"]
    latest = visible[0] if visible else all_paths[0]
    completed_practice = next(
        (p for p in all_paths if p.status == "completed" and any(i.item_type == "practice" for i in p.items)),
        None,
    )
    newest_attempt = _newest_submitted_attempt_at(student_id)

    # Heal accounts from before this fix: an untouched auto-regenerated
    # practice path stacked on top of a completed one, with no new
    # assessment since -> the completed path is the real state.
    if (
        latest.status == "active"
        and completed_practice
        and latest.id != completed_practice.id
        and _is_untouched_single_practice(latest)
        and (
            newest_attempt is None
            or (completed_practice.created_at and completed_practice.created_at >= newest_attempt)
        )
    ):
        latest.status = "archived"
        student_profile_service.set_current_learning_path(student_id, completed_practice.id)
        db.commit()
        latest = completed_practice

    d = _path_to_dict(latest)

    # Legacy multi-step / quiz-item path format -> regenerate once into the
    # current single-practice-step format.
    if d.get("items") and (
        len(d["items"]) > 1
        or any(it.get("item_type") in ("quiz", "reassessment") for it in d["items"])
    ):
        weak = path_generator.get_weak_topics(student_id)
        if weak or path_generator.has_mastery_data(student_id):
            refresh_learning_path(student_id)
            return get_path_with_items(student_id) or d

    return d
