#!/usr/bin/env python3
"""Seed platform default Math diagnostic (A-305)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts.load_env import load_project_env

load_project_env()

from app import create_app
from app.models.lms_models import Assessment
from app.services.lms import assessment_service, curriculum_service, question_bank_service
from app.utils.db import get_db

SAMPLE_QUESTIONS = [
    ("What is 2 + 2?", ["1", "2", "3", "4"], 3, "algebra"),
    ("What is 1/2 + 1/2?", ["1/4", "1", "2", "1/2"], 1, "fractions"),
    ("How many sides does a triangle have?", ["2", "3", "4", "5"], 1, "geometry"),
]


def seed_diagnostic(teacher_id: int = 1) -> int:
    db = get_db()
    existing = (
        db.query(Assessment)
        .filter(Assessment.assessment_type == "diagnostic", Assessment.title == "Platform Math Diagnostic")
        .first()
    )
    if existing:
        print(f"Diagnostic already exists (id={existing.id})")
        return existing.id

    a = assessment_service.create_assessment(
        created_by=teacher_id,
        title="Platform Math Diagnostic",
        assessment_type="diagnostic",
        creation_mode="manual",
        description="Baseline diagnostic for all students.",
    )
    q_ids = []
    for text, opts, correct, slug in SAMPLE_QUESTIONS:
        topic = curriculum_service.get_topic_by_slug("Math", slug)
        labels = ["A", "B", "C", "D"]
        options = [{"label": labels[i], "text": opts[i]} for i in range(4)]
        q = question_bank_service.create_question(
            created_by=teacher_id,
            question_text=text,
            options=options,
            correct_option_index=correct,
            topic_id=topic.id if topic else None,
            difficulty="easy",
            source_type="manual",
        )
        q_ids.append(q.id)
    assessment_service.add_questions(a.id, q_ids)
    assessment_service.publish_assessment(a.id)
    print(f"Created diagnostic id={a.id} with {len(q_ids)} questions")
    return a.id


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        seed_diagnostic()
