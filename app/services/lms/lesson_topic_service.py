"""Link lessons to curriculum topics."""
from __future__ import annotations

import logging
from difflib import SequenceMatcher
from typing import List, Optional

from app.models.database_models import Lesson as DBLesson
from app.models.lms_models import LessonTopic, Topic
from app.services.lms import curriculum_service
from app.utils.db import get_db

logger = logging.getLogger(__name__)


def get_lesson_topic_ids(lesson_id: int) -> List[int]:
    db = get_db()
    rows = db.query(LessonTopic.topic_id).filter(LessonTopic.lesson_id == lesson_id).all()
    return [r[0] for r in rows]


def get_lesson_topics(lesson_id: int) -> List[Topic]:
    db = get_db()
    return (
        db.query(Topic)
        .join(LessonTopic, LessonTopic.topic_id == Topic.id)
        .filter(LessonTopic.lesson_id == lesson_id, Topic.is_active.is_(True))
        .order_by(Topic.sort_order, Topic.name)
        .all()
    )


def set_lesson_topics(lesson_id: int, topic_ids: List[int]) -> None:
    db = get_db()
    db.query(LessonTopic).filter(LessonTopic.lesson_id == lesson_id).delete()
    seen = set()
    for tid in topic_ids:
        if not tid or tid in seen:
            continue
        seen.add(tid)
        db.add(LessonTopic(lesson_id=lesson_id, topic_id=tid))
    db.commit()


def match_topic_by_focus_area(
    focus_area: str,
    subject: str = "Math",
    min_ratio: float = 0.6,
) -> Optional[Topic]:
    """Fuzzy-match lesson focus_area to a taxonomy topic name."""
    if not focus_area or not focus_area.strip():
        return None

    needle = focus_area.strip().lower()
    topics = curriculum_service.list_topics(subject)

    for topic in topics:
        name = topic.name.lower()
        slug = topic.slug.replace("-", " ").lower()
        if needle == name or needle == slug:
            return topic
        if needle in name or name in needle:
            return topic

    best: Optional[Topic] = None
    best_score = 0.0
    for topic in topics:
        score = SequenceMatcher(None, needle, topic.name.lower()).ratio()
        if score > best_score:
            best_score = score
            best = topic
    if best and best_score >= min_ratio:
        return best
    return None


def link_lesson_by_focus_area(lesson_id: int, focus_area: str, subject: str = "Math") -> Optional[int]:
    topic = match_topic_by_focus_area(focus_area, subject=subject)
    if not topic:
        return None
    existing = get_lesson_topic_ids(lesson_id)
    if topic.id in existing:
        return topic.id
    set_lesson_topics(lesson_id, existing + [topic.id])
    return topic.id


def backfill_lesson_topics(subject: str = "Math", dry_run: bool = False) -> dict:
    db = get_db()
    lessons = (
        db.query(DBLesson)
        .filter(DBLesson.focus_area.isnot(None), DBLesson.focus_area != "")
        .order_by(DBLesson.id)
        .all()
    )
    linked = 0
    skipped = 0
    unmatched: List[str] = []

    for lesson in lessons:
        existing = get_lesson_topic_ids(lesson.id)
        if existing:
            skipped += 1
            continue
        topic = match_topic_by_focus_area(lesson.focus_area or "", subject=subject)
        if not topic:
            key = f"{lesson.id}:{lesson.focus_area}"
            if key not in unmatched:
                unmatched.append(key)
            continue
        if not dry_run:
            set_lesson_topics(lesson.id, [topic.id])
        linked += 1

    return {
        "total_lessons": len(lessons),
        "linked": linked,
        "skipped_existing": skipped,
        "unmatched_count": len(unmatched),
        "unmatched_samples": unmatched[:20],
        "dry_run": dry_run,
    }


def enrich_lessons_with_topics(lessons: List[dict]) -> List[dict]:
    if not lessons:
        return lessons
    lesson_ids = [l["id"] for l in lessons if l.get("id")]
    if not lesson_ids:
        return lessons

    db = get_db()
    rows = (
        db.query(LessonTopic.lesson_id, Topic.id, Topic.name, Topic.slug)
        .join(Topic, Topic.id == LessonTopic.topic_id)
        .filter(LessonTopic.lesson_id.in_(lesson_ids))
        .all()
    )
    by_lesson: dict[int, list] = {}
    for lesson_id, topic_id, name, slug in rows:
        by_lesson.setdefault(lesson_id, []).append(
            {"id": topic_id, "name": name, "slug": slug}
        )

    enriched = []
    for lesson in lessons:
        copy = dict(lesson)
        copy["topics"] = by_lesson.get(lesson["id"], [])
        copy["topic_ids"] = [t["id"] for t in copy["topics"]]
        enriched.append(copy)
    return enriched
