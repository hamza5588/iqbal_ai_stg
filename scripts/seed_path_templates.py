"""Seed remediation learning path templates per Math topic (P-401)."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts.load_env import load_project_env

load_project_env()

from app import create_app
from app.models.lms_models import LearningPathTemplate, LearningPathTemplateItem, Topic
from app.utils.db import get_db

DEFAULT_STEPS = [
    ("lesson", "Review lesson"),
    ("quiz", "Practice quiz"),
    ("reassessment", "Topic reassessment"),
]


def seed_templates(subject: str = "Math") -> int:
    db = get_db()
    topics = db.query(Topic).filter(Topic.subject == subject, Topic.is_active.is_(True)).all()
    created = 0

    for topic in topics:
        existing = (
            db.query(LearningPathTemplate)
            .filter(LearningPathTemplate.topic_id == topic.id)
            .first()
        )
        if existing:
            continue

        template = LearningPathTemplate(
            name=f"Remediation: {topic.name}",
            topic_id=topic.id,
            description=f"Default remediation path for weak performance in {topic.name}.",
            is_active=True,
        )
        db.add(template)
        db.flush()

        for sort_order, (item_type, label) in enumerate(DEFAULT_STEPS):
            db.add(
                LearningPathTemplateItem(
                    template_id=template.id,
                    item_type=item_type,
                    sort_order=sort_order,
                    label=label,
                )
            )
        created += 1

    db.commit()
    return created


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        n = seed_templates()
        print(f"Seeded {n} learning path templates.")
