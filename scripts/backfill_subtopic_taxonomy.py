"""Backfill the Domain -> Subtopic taxonomy and subtopic mastery from history.

Idempotent and additive:
* creates the new tables / columns (same migration the app runs at startup);
* classifies every question of every submitted attempt (printed ``Topic:`` ->
  Subtopic; papers without one are recorded as ``domain_only`` - no Subtopic
  score is ever fabricated for them);
* records one evidence row per historical answer (unique per student/source/
  question, so re-running adds nothing) and replays completed Learning Chat
  sessions whose items carry a subtopic;
* rebuilds StudentSubtopicScore from evidence.

Domain scores (student_topic_scores), attempts and sessions are never modified.
No LLM is called: diagnostic Domain hints come from the cached AI grouping only;
without a cache entry the printed Domain is used.

Usage (take a DB backup first - see docs/SUBTOPIC_LEARNING_SYSTEM.md):
    python scripts/backfill_subtopic_taxonomy.py --dry-run
    python scripts/backfill_subtopic_taxonomy.py [--student-id 123]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _cached_hints_only(attempt_id: int) -> dict:
    from app.models.lms_models import AssessmentAttempt
    from app.services.lms.assessment_service import get_assessment
    from app.services.lms.topic_resolver import get_or_create_topic_from_pdf_label
    from app.services.lms.weakness_analyzer import _get_cached
    from app.utils.db import get_db

    attempt = get_db().query(AssessmentAttempt).filter(AssessmentAttempt.id == attempt_id).first()
    if not attempt:
        return {}
    cached = _get_cached(get_assessment(attempt.assessment_id), attempt_id) or {}
    hints = {}
    for area in cached.get("all_topics") or []:
        name = (area.get("topic_name") or "").strip()
        topic = get_or_create_topic_from_pdf_label(name) if name else None
        tid = topic.id if topic else area.get("topic_id")
        for qid in area.get("question_ids") or []:
            if tid:
                hints[int(qid)] = int(tid)
    return hints


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="classify and count, then roll back")
    parser.add_argument("--student-id", type=int, default=None)
    args = parser.parse_args()

    from app import create_app

    app = create_app()
    with app.app_context():
        from app.migrations.subtopic_taxonomy import run_migration
        from app.models.lms_models import (
            AssessmentAttempt,
            DeficiencyChatSession,
            QuestionTaxonomy,
            StudentSubtopicScore,
            SubtopicEvidence,
        )
        from app.services.lms import subtopic_mastery_service
        from app.utils.db import get_db, get_engine
        from sqlalchemy import func

        run_migration(get_engine())
        subtopic_mastery_service._diagnostic_domain_hints = _cached_hints_only
        db = get_db()
        started = time.perf_counter()
        before = db.query(func.count(SubtopicEvidence.id)).scalar()

        q = db.query(AssessmentAttempt.id, AssessmentAttempt.student_id).filter(AssessmentAttempt.status == "submitted")
        if args.student_id:
            q = q.filter(AssessmentAttempt.student_id == args.student_id)
        attempts = q.order_by(AssessmentAttempt.submitted_at.asc()).all()
        failures = []
        if args.dry_run:
            # Classify only; nothing is committed.
            from app.services.lms import taxonomy_service
            from app.services.lms.assessment_service import get_assessment
            from app.services.lms.attempt_service import attempt_question_ids
            from app.models.lms_models import Question

            for attempt_id, _sid in attempts:
                att = db.query(AssessmentAttempt).filter(AssessmentAttempt.id == attempt_id).first()
                assessment = get_assessment(att.assessment_id)
                for question in db.query(Question).filter(Question.id.in_(attempt_question_ids(att))).all():
                    taxonomy_service.classify_question(question, assessment)
            counts = dict(
                db.query(QuestionTaxonomy.mapping_status, func.count(QuestionTaxonomy.question_id))
                .group_by(QuestionTaxonomy.mapping_status)
                .all()
            )
            db.rollback()
            print(json.dumps({"dry_run": True, "attempts": len(attempts), "question_mapping": counts}, indent=2))
            return 0

        for attempt_id, sid in attempts:
            try:
                subtopic_mastery_service.record_attempt(attempt_id)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                failures.append({"attempt_id": attempt_id, "student_id": sid, "error": str(exc)[:300]})

        sq = db.query(DeficiencyChatSession.id).filter(DeficiencyChatSession.status == "completed")
        if args.student_id:
            sq = sq.filter(DeficiencyChatSession.student_id == args.student_id)
        lc_sessions = [sid for (sid,) in sq.all()]
        for session_id in lc_sessions:
            try:
                subtopic_mastery_service.record_learning_chat_session(session_id)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                failures.append({"session_id": session_id, "error": str(exc)[:300]})

        mapping = dict(
            db.query(QuestionTaxonomy.mapping_status, func.count(QuestionTaxonomy.question_id))
            .group_by(QuestionTaxonomy.mapping_status)
            .all()
        )
        report = {
            "attempts_processed": len(attempts),
            "learning_chat_sessions_checked": len(lc_sessions),
            "evidence_rows_added": db.query(func.count(SubtopicEvidence.id)).scalar() - before,
            "students_with_subtopic_scores": db.query(func.count(func.distinct(StudentSubtopicScore.student_id))).scalar(),
            "question_mapping": mapping,
            "failures": failures[:50],
            "failure_count": len(failures),
            "seconds": round(time.perf_counter() - started, 1),
        }
        print(json.dumps(report, indent=2, default=str))
        return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
