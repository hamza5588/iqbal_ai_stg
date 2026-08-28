"""Curriculum / topic taxonomy service."""
from __future__ import annotations

from typing import List, Optional

from app.models.lms_models import Topic, TopicPrerequisite
from app.services.lms.exceptions import LMSNotFoundError
from app.utils.db import get_db


def list_topics(
    subject: str,
    grade_level: Optional[str] = None,
    active_only: bool = True,
) -> List[Topic]:
    db = get_db()
    q = db.query(Topic).filter(Topic.subject == subject)
    if grade_level:
        q = q.filter(Topic.grade_level == grade_level)
    if active_only:
        q = q.filter(Topic.is_active.is_(True))
    return q.order_by(Topic.sort_order, Topic.name).all()


def get_topic_by_id(topic_id: int) -> Topic:
    db = get_db()
    topic = db.query(Topic).filter(Topic.id == topic_id).first()
    if not topic:
        raise LMSNotFoundError(f"Topic {topic_id} not found")
    return topic


def get_topic_by_slug(subject: str, slug: str) -> Optional[Topic]:
    db = get_db()
    return db.query(Topic).filter(Topic.subject == subject, Topic.slug == slug).first()


def get_prerequisites(topic_id: int) -> List[Topic]:
    db = get_db()
    rows = (
        db.query(Topic)
        .join(TopicPrerequisite, TopicPrerequisite.prerequisite_topic_id == Topic.id)
        .filter(TopicPrerequisite.topic_id == topic_id)
        .all()
    )
    return rows


def create_topic(
    name: str,
    slug: str,
    subject: str,
    parent_id: Optional[int] = None,
    grade_level: Optional[str] = None,
    description: Optional[str] = None,
    sort_order: int = 0,
) -> Topic:
    db = get_db()
    topic = Topic(
        name=name,
        slug=slug,
        subject=subject,
        parent_id=parent_id,
        grade_level=grade_level,
        description=description,
        sort_order=sort_order,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


def add_prerequisite(topic_id: int, prerequisite_topic_id: int) -> TopicPrerequisite:
    db = get_db()
    link = TopicPrerequisite(topic_id=topic_id, prerequisite_topic_id=prerequisite_topic_id)
    db.add(link)
    db.commit()
    db.refresh(link)
    return link
