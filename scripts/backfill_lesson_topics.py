#!/usr/bin/env python3
"""Backfill lesson_topics from lessons.focus_area (S-204)."""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import create_app
from app.services.lms.lesson_topic_service import backfill_lesson_topics


def main():
    parser = argparse.ArgumentParser(description="Link lessons to topics via focus_area matching")
    parser.add_argument("--subject", default="Math", help="Curriculum subject (default: Math)")
    parser.add_argument("--dry-run", action="store_true", help="Report matches without writing")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        result = backfill_lesson_topics(subject=args.subject, dry_run=args.dry_run)
        print("Backfill complete:")
        for key, val in result.items():
            print(f"  {key}: {val}")


if __name__ == "__main__":
    main()
