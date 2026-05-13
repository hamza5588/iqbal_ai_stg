"""Create/update mini-lectures as child lessons + Phase 3 targeting."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.database_models import Lesson as DBLesson
from app.models.phase1_models import Notification
from app.models.phase3_models import MiniLectureTarget


def _split_display_name(username: str) -> tuple[str, str]:
    u = (username or "").strip()
    if not u:
        return ("Student", "")
    parts = u.replace("_", " ").split()
    if len(parts) == 1:
        return (parts[0], "")
    return (parts[0], " ".join(parts[1:]))


def upsert_mini_lecture(
    db: Session,
    *,
    teacher_user_id: int,
    title: str,
    content: str,
    objective: Optional[str],
    related_lesson_id: Optional[int],
    target_student_ids: List[int],
    status: str,
    hide_from_others: bool,
    notify_students: bool,
    mini_lesson_id: Optional[int] = None,
) -> DBLesson:
    """Create or update a mini-lesson row; refresh MiniLectureTarget rows."""
    teacher_user_id = int(teacher_user_id)
    parent_id = int(related_lesson_id) if related_lesson_id else None

    status_db = "finalized" if status == "published" else status
    if status_db not in ("draft", "finalized"):
        status_db = "draft"

    lesson: Optional[DBLesson] = None
    if mini_lesson_id:
        lesson = db.query(DBLesson).filter(DBLesson.id == int(mini_lesson_id)).first()
        if lesson and int(lesson.teacher_id) != teacher_user_id:
            raise PermissionError("Not your mini-lecture")

    if lesson is None and parent_id:
        lesson = (
            db.query(DBLesson)
            .filter(
                DBLesson.teacher_id == teacher_user_id,
                DBLesson.parent_lesson_id == parent_id,
                DBLesson.status == "draft",
            )
            .order_by(DBLesson.id.desc())
            .first()
        )

    if lesson is None:
        lesson = DBLesson(
            teacher_id=teacher_user_id,
            title=title[:2000],
            content=content or "",
            summary=(objective or "")[:5000] if objective else None,
            parent_lesson_id=parent_id,
            status=status_db,
            is_public=not bool(hide_from_others),
            focus_area=None,
            grade_level=None,
        )
        db.add(lesson)
        db.flush()
    else:
        lesson.title = title[:2000]
        lesson.content = content or ""
        lesson.summary = (objective or "")[:5000] if objective else lesson.summary
        lesson.parent_lesson_id = parent_id
        lesson.status = status_db
        lesson.is_public = not bool(hide_from_others)

    db.query(MiniLectureTarget).filter(MiniLectureTarget.mini_lesson_id == lesson.id).delete()
    for sid in target_student_ids or []:
        db.add(
            MiniLectureTarget(
                mini_lesson_id=lesson.id,
                student_user_id=int(sid),
                source_review_id=None,
            )
        )

    db.commit()
    db.refresh(lesson)

    if notify_students and status == "published" and target_student_ids:
        for sid in target_student_ids:
            db.add(
                Notification(
                    recipient_id=int(sid),
                    title="New mini-lecture",
                    message=f"Your teacher shared a mini-lecture: {lesson.title[:120]}",
                    type="action",
                    action_link=f"/lecture-reader/{lesson.id}",
                )
            )
        db.commit()

    return lesson


def mini_lecture_to_api_dict(db: Session, *, mini_lesson_id: int) -> Dict[str, Any]:
    row = db.query(DBLesson).filter(DBLesson.id == mini_lesson_id).first()
    if not row:
        return {}
    tids = [
        r.student_user_id
        for r in db.query(MiniLectureTarget).filter(MiniLectureTarget.mini_lesson_id == mini_lesson_id).all()
    ]
    return {
        "mini_lesson_id": row.id,
        "title": row.title,
        "content": row.content,
        "objective": row.summary,
        "related_lesson_id": row.parent_lesson_id,
        "target_student_ids": tids,
        "status": row.status,
    }
