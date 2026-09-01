#!/usr/bin/env python3
"""Seed Math curriculum topics for LMS."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts.load_env import describe_database_target, load_project_env

load_project_env()

from app import create_app
from app.services.lms import curriculum_service

MATH_TOPICS = [
    ("Algebra", "algebra", 1),
    ("Fractions", "fractions", 2),
    ("Geometry", "geometry", 3),
    ("Word Problems", "word-problems", 4),
    ("Quadratic Equations", "quadratic-equations", 5),
]


def main():
    print(f"Database target: {describe_database_target()}")
    app = create_app()
    with app.app_context():
        created = 0
        for name, slug, order in MATH_TOPICS:
            existing = curriculum_service.get_topic_by_slug("Math", slug)
            if existing:
                continue
            curriculum_service.create_topic(
                name=name,
                slug=slug,
                subject="Math",
                grade_level="8-10",
                sort_order=order,
            )
            created += 1
        print(f"Seeded {created} Math topics ({len(MATH_TOPICS) - created} already existed)")


if __name__ == "__main__":
    main()
