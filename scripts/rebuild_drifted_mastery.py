#!/usr/bin/env python3
"""
Repair StudentTopicScore rows damaged by the diagnostic-results drift bug.

Before the fix, every GET /api/lms/attempts/<id>/results re-ran the AI topic
grouping for any diagnostic with an unanswered question and rewrote the
student's topic scores with it. Affected students have extra/overlapping topic
rows (e.g. "Geometry", "Geometry and Triangles"), inflated sample sizes, and
Weak Topics / Overall Progress that no longer match their answers.

This replays each student's submitted attempts and completed Learning Chat
sessions through performance_service.rebuild_student_mastery() (now stable:
the grouping is cached per attempt) and refreshes the learning path.

Dry run by default - prints what would change:
    python scripts/rebuild_drifted_mastery.py                 # all students with a submitted diagnostic
    python scripts/rebuild_drifted_mastery.py --student-id 39
Apply:
    python scripts/rebuild_drifted_mastery.py --apply
Docker:
    docker compose exec flask_app1 python scripts/rebuild_drifted_mastery.py --apply
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("SKIP_EXTRA_STARTUP", "true")

from app import create_app  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
logger = logging.getLogger("rebuild_drifted_mastery")


def _profile(student_id: int) -> dict:
    from app.models.lms_models import StudentTopicScore
    from app.utils.db import get_db

    rows = get_db().query(StudentTopicScore).filter(StudentTopicScore.student_id == student_id).all()
    total = sum((r.sample_size or 1) for r in rows)
    overall = round(sum(r.score_percent * (r.sample_size or 1) for r in rows) / total, 2) if total else 0.0
    return {
        "rows": len(rows),
        "samples": total,
        "overall": overall,
        "weak": sorted(r.topic_id for r in rows if r.mastery_status == "weak"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--student-id", type=int, action="append", help="limit to these students (repeatable)")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args()

    from app.models.lms_models import Assessment, AssessmentAttempt
    from app.services.lms import learning_path_service, performance_service
    from app.utils.db import get_db

    db = get_db()
    q = (
        db.query(AssessmentAttempt.student_id)
        .join(Assessment, Assessment.id == AssessmentAttempt.assessment_id)
        .filter(AssessmentAttempt.status == "submitted", Assessment.assessment_type == "diagnostic")
        .distinct()
    )
    if args.student_id:
        q = q.filter(AssessmentAttempt.student_id.in_(args.student_id))
    students = sorted(sid for (sid,) in q.all())
    logger.info("%d student(s) with a submitted diagnostic%s", len(students), "" if args.apply else " (dry run)")

    for sid in students:
        before = _profile(sid)
        if not args.apply:
            logger.info("student %s: %s", sid, before)
            continue
        try:
            performance_service.rebuild_student_mastery(sid)
            learning_path_service.refresh_learning_path(sid)
        except Exception as exc:  # noqa: BLE001 - keep going for the other students
            db.rollback()
            logger.error("student %s: rebuild failed: %s", sid, exc)
            continue
        logger.info("student %s: %s -> %s", sid, before, _profile(sid))
    if not args.apply:
        logger.info("Dry run only. Re-run with --apply to rebuild.")
    return 0


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        raise SystemExit(main())
