"""Map PDF headings / question text to curriculum topic IDs."""
from __future__ import annotations

import re
import uuid
from typing import Optional

from app.models.lms_models import Topic
from app.services.lms import curriculum_service
from app.utils.db import get_db

PDF_TOPIC_SUBJECT = "Content"

_KEYWORD_TOPIC_HINTS = (
    (("quadratic", "factorization", "completing the square"), "quadratic"),
    (("fraction", "numerator", "denominator"), "fractions"),
    (("geometry", "triangle", "angle", "area", "perimeter"), "geometry"),
    (("algebra", "variable", "equation", "linear"), "algebra"),
    (("word problem",), "word-problems"),
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def resolve_topic_id_from_label(label: str) -> Optional[int]:
    """Match a PDF heading or topic label to a curriculum topic."""
    if not label or not label.strip():
        return None

    normalized = _normalize(label)
    db = get_db()
    topics = db.query(Topic).filter(Topic.is_active.is_(True)).order_by(Topic.sort_order).all()
    if not topics:
        return None

    for topic in topics:
        name = _normalize(topic.name)
        slug = _normalize(topic.slug.replace("-", " "))
        if name and (name in normalized or normalized in name):
            return topic.id
        if slug and (slug in normalized or normalized in slug):
            return topic.id

    for keywords, slug_hint in _KEYWORD_TOPIC_HINTS:
        if any(kw in normalized for kw in keywords):
            for topic in topics:
                if topic.slug == slug_hint or slug_hint.replace("-", " ") in _normalize(topic.name):
                    return topic.id

    return None


def resolve_topic_id_from_text(text: str) -> Optional[int]:
    """Infer curriculum topic from free-form question or heading text."""
    return resolve_topic_id_from_label(text)


def slugify_label(label: str) -> str:
    """Stable slug for PDF-derived topic labels."""
    normalized = _normalize(label)
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    if not slug:
        slug = f"topic-{uuid.uuid4().hex[:8]}"
    return slug[:240]


def get_or_create_topic_from_pdf_label(label: str, subject: str = PDF_TOPIC_SUBJECT) -> Optional[Topic]:
    """Create or reuse a topic named after the teacher's PDF section heading."""
    clean = (label or "").strip()
    if not clean:
        return None

    db = get_db()
    topics = db.query(Topic).filter(Topic.subject == subject, Topic.is_active.is_(True)).all()
    normalized = _normalize(clean)
    for topic in topics:
        if _normalize(topic.name) == normalized:
            return topic

    slug_base = slugify_label(clean)
    slug = slug_base
    suffix = 1
    while curriculum_service.get_topic_by_slug(subject, slug):
        slug = f"{slug_base}-{suffix}"[:255]
        suffix += 1

    return curriculum_service.create_topic(
        name=clean[:255],
        slug=slug,
        subject=subject,
        description="Auto-created from teacher PDF diagnostic section",
        sort_order=1000,
    )
