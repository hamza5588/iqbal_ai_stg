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
    if item_type == "enrichment":
        return "Challenge activity — stretch questions"
    return f"{item_type} #{item_id}"


def _weak_area_progress(student_id: int) -> dict:
    """Topic-level Learning Path progress (not the single chat step).

    Completing Inequality practice must not show 100% while other topics
    are still Weak in the mastery pie.
    """
    from app.services.lms import performance_service

    mastery = performance_service.get_student_mastery(student_id)
    if not mastery:
        return {
            "cleared": 0,
            "total": 0,
            "weak_remaining": 0,
            "percent": 0.0,
        }
    weak_n = sum(1 for m in mastery if performance_service.is_weak_mastery(m))
    total = len(mastery)
    cleared = max(0, total - weak_n)
    percent = round(100.0 * cleared / total, 1) if total else 0.0
    return {
        "cleared": cleared,
        "total": total,
        "weak_remaining": weak_n,
        "percent": percent,
    }


def _path_has_chat_practice(path: LearningPath) -> bool:
    return any(
        i.item_type == "practice" and int(i.item_id or 0) == 0 for i in (path.items or [])
    )


def _sync_chat_practice_path(student_id: int, path: LearningPath) -> LearningPath:
    """Keep chat-practice path open until Weak topics are cleared."""
    if not path or not _path_has_chat_practice(path):
        return path

    progress = _weak_area_progress(student_id)
    db = get_db()
    practice_items = [
        i for i in path.items if i.item_type == "practice" and int(i.item_id or 0) == 0
    ]
    if not practice_items:
        return path

    if progress["weak_remaining"] <= 0 and progress["total"] > 0:
        changed = False
        for item in practice_items:
            if item.status != "completed":
                item.status = "completed"
                item.completed_at = datetime.utcnow()
                changed = True
        if path.status != "completed":
            path.status = "completed"
            changed = True
        if changed:
            path.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(path)
        return path

    # Still have Weak topics — path must stay active (never false 100%).
    changed = False
    for item in practice_items:
        if item.status == "completed":
            item.status = "in_progress"
            item.completed_at = None
            changed = True
        elif item.status == "pending":
            item.status = "in_progress"
            changed = True
    if path.status == "completed":
        path.status = "active"
        changed = True
    if changed:
        path.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(path)
    return path


def _path_to_dict(path: LearningPath, student_id: Optional[int] = None) -> dict:
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
    total_count = len(items_out)
    percent = round(100.0 * completed_count / total_count, 1) if total_count else 0.0
    weak_progress = None

    # Single Learning Chat step → progress is "topics cleared of Weak", not 0/1 item.
    if student_id and _path_has_chat_practice(path):
        weak_progress = _weak_area_progress(student_id)
        completed_count = weak_progress["cleared"]
        total_count = weak_progress["total"] or total_count
        percent = weak_progress["percent"]

    return {
        "id": path.id,
        "title": path.title,
        "status": path.status,
        "items": items_out,
        "current_step": current_step,
        "completed_count": completed_count,
        "total_count": total_count,
        "percent": percent,
        "weak_area_progress": weak_progress,
    }


def get_path_with_items(student_id: int) -> Optional[dict]:
    path = get_active_path_for_student(student_id)
    if not path:
        # Fall back to newest visible path (may be completed).
        db = get_db()
        path = (
            db.query(LearningPath)
            .filter(
                LearningPath.student_id == student_id,
                LearningPath.status != "archived",
            )
            .order_by(LearningPath.id.desc())
            .first()
        )
    if not path:
        return None
    path = _sync_chat_practice_path(student_id, path)
    return _path_to_dict(path, student_id=student_id)


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


def _has_enrichment_path_since_last_attempt(student_id: int) -> bool:
    """True if an enrichment/challenge path already exists for this student
    since their most recent submitted assessment - so we don't mint a fresh
    one on every refresh."""
    db = get_db()
    newest = _newest_submitted_attempt_at(student_id)
    paths = (
        db.query(LearningPath)
        .filter(LearningPath.student_id == student_id)
        .order_by(LearningPath.id.desc())
        .all()
    )
    for path in paths:
        if not any(i.item_type == "enrichment" for i in path.items):
            continue
        if newest is None or (path.created_at and path.created_at >= newest):
            return True
    return False


def refresh_learning_path(student_id: int) -> Optional[LearningPath]:
    """Regenerate path after reassessment / quiz / diagnostic submit (P-405)."""
    weak = path_generator.get_weak_topics(student_id)
    if weak:
        return generate_learning_path(student_id, force=True)

    if not path_generator.has_mastery_data(student_id):
        return get_active_path_for_student(student_id)

    # No weak areas: give the student an enrichment / challenge activity
    # instead of an empty practice section (DIL feedback #4). Only once per
    # diagnostic - don't regenerate it every refresh.
    if not _has_enrichment_path_since_last_attempt(student_id):
        return generate_learning_path(student_id, force=True)

    active = get_active_path_for_student(student_id)
    if active and not list(active.items):
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
        (
            p
            for p in all_paths
            if p.status == "completed"
            and any(i.item_type == "practice" for i in p.items)
        ),
        None,
    )
    newest_attempt = _newest_submitted_attempt_at(student_id)

    # Heal: untouched auto-regen on top of another path → archive the empty regen.
    if (
        latest.status == "active"
        and completed_practice
        and latest.id != completed_practice.id
        and _is_untouched_single_practice(latest)
        and (
            newest_attempt is None
            or (latest.created_at and newest_attempt <= latest.created_at)
        )
    ):
        latest.status = "archived"
        student_profile_service.set_current_learning_path(student_id, completed_practice.id)
        db.commit()
        latest = completed_practice

    # Re-open falsely "100% complete" chat paths while Weak topics remain,
    # and mark truly done when Weak count hits zero.
    latest = _sync_chat_practice_path(student_id, latest)
    d = _path_to_dict(latest, student_id=student_id)

    # Do NOT auto-mint a fresh practice path just because Learning Chat
    # finished and topics are still below ~100%. Progress is topic-based.
    # New paths are created only when a diagnostic/quiz is submitted.

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
