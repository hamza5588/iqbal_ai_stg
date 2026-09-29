#!/usr/bin/env python3
"""
One-off backfill for students whose Learning Chat sessions completed before
the update_topic_scores_from_deficiency_session() fix existed - their
StudentTopicScore rows never got updated, so "Overall Progress"/"Weak
Topics" are stuck at whatever the initial diagnostic produced despite
possibly-perfect practice sessions since.

Processes every completed deficiency_chat_sessions row in chronological
order (per student), so the final StudentTopicScore state reflects each
student's most recent completed session - same effect as if the fix had
been live the whole time.

Run from inside the flask_app1 container:
    docker compose exec flask_app1 python _qa_audit_tmp/backfill_deficiency_topic_scores.py
"""
from __future__ import annotations

import logging

from app import create_app
from app.models.lms_models import DeficiencyChatSession
from app.services.lms import learning_path_service, performance_service
from app.utils.db import get_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    db = get_db()
    sessions = (
        db.query(DeficiencyChatSession)
        .filter(DeficiencyChatSession.status == "completed")
        .order_by(DeficiencyChatSession.student_id, DeficiencyChatSession.id)
        .all()
    )
    logger.info("Found %d completed session(s) to backfill", len(sessions))

    students_touched = set()
    for s in sessions:
        performance_service.update_topic_scores_from_deficiency_session(s.id)
        students_touched.add(s.student_id)
        logger.info(
            "Backfilled session %s (student %s, %s/%s correct)",
            s.id, s.student_id, s.correct_count, s.current_index,
        )

    # Regenerate each affected student's learning path so a fresh dashboard
    # load reflects the now-current (possibly no-longer-weak) topic list,
    # instead of waiting for their next natural path refresh trigger.
    for student_id in sorted(students_touched):
        learning_path_service.refresh_learning_path(student_id)
        logger.info("Refreshed learning path for student %s", student_id)

    logger.info("Done. Backfilled %d student(s).", len(students_touched))
    return 0


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        raise SystemExit(main())
