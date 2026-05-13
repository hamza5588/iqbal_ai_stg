"""Celery tasks for Phase 4: nightly intelligence, reminders, pedagogy scan."""
from __future__ import annotations

import logging

from app.celery_app import celery
from app.models.database_models import User as DBUser
from app.models.phase4_models import ScheduledNotification, StudentExamTarget
from app.services.notification_service import create_notification
from app.services.phase4 import intelligence_service, parent_risk_service, pedagogy_service
from app.utils.db import get_db

logger = logging.getLogger(__name__)


@celery.task(name="phase4.deliver_scheduled_notification")
def deliver_scheduled_notification_task(scheduled_id: int) -> dict:
    db: Session = get_db()
    row = db.query(ScheduledNotification).filter(ScheduledNotification.id == int(scheduled_id)).first()
    if not row or row.status != "pending":
        return {"ok": False, "reason": "not_found_or_sent"}
    try:
        create_notification(
            db,
            recipient_id=row.recipient_user_id,
            title=row.title,
            message=row.message,
            type=row.notif_type or "phase4_reminder",
            action_link=row.action_link,
        )
        row.status = "sent"
        db.commit()
        return {"ok": True}
    except Exception as exc:
        logger.warning("Scheduled notification %s failed: %s", scheduled_id, exc)
        row.status = "failed"
        db.commit()
        return {"ok": False, "error": str(exc)}


@celery.task(name="phase4.nightly_intelligence_batch")
def nightly_intelligence_batch(chunk_size: int = 200) -> dict:
    db: Session = get_db()
    students = (
        db.query(DBUser.id)
        .filter(DBUser.role == "student", DBUser.is_active.is_(True))
        .limit(5000)
        .all()
    )
    processed = 0
    for (uid,) in students[:chunk_size]:
        try:
            target = (
                db.query(StudentExamTarget)
                .filter(StudentExamTarget.student_user_id == uid)
                .order_by(StudentExamTarget.exam_date.asc())
                .first()
            )
            et_id = target.id if target else None
            intelligence_service.compute_intelligence_snapshot(db, student_user_id=uid, exam_target_id=et_id)
            intelligence_service.persist_cognitive_snapshot(db, student_user_id=uid)
            snap = intelligence_service.latest_snapshot_dict(db, student_user_id=uid)
            if snap and snap.get("exam_confidence") is not None:
                intelligence_service.upsert_exam_confidence_daily(
                    db,
                    student_user_id=uid,
                    exam_target_id=et_id,
                    score=float(snap["exam_confidence"]),
                    factors={"source": "nightly_batch"},
                )
            parent_risk_service.maybe_alert_parents(db, student_user_id=uid)
            processed += 1
        except Exception as exc:
            logger.warning("nightly intelligence uid=%s: %s", uid, exc)
    return {"processed": processed}


@celery.task(name="phase4.weekly_pedagogy_scan")
def weekly_pedagogy_scan() -> dict:
    """Create stub proposals for topics with many wrong attempts (simplified)."""
    db: Session = get_db()
    from app.models.phase3_models import QuestionBankItem
    from app.models.phase4_models import QuestionPracticeAttempt
    from sqlalchemy import func

    q = (
        db.query(
            QuestionBankItem.syllabus_topic_id,
            func.count(QuestionPracticeAttempt.id).label("cnt"),
        )
        .select_from(QuestionPracticeAttempt)
        .join(QuestionBankItem, QuestionBankItem.id == QuestionPracticeAttempt.question_bank_item_id)
        .filter(
            QuestionPracticeAttempt.is_correct.is_(False),
            QuestionBankItem.syllabus_topic_id.isnot(None),
        )
        .group_by(QuestionBankItem.syllabus_topic_id)
        .having(func.count(QuestionPracticeAttempt.id) >= 8)
        .limit(20)
        .all()
    )
    created = 0
    for topic_id, _cnt in q:
        if not topic_id:
            continue
        pedagogy_service.create_proposal_from_scan(
            db,
            syllabus_topic_id=int(topic_id),
            proposed_body=(
                "Improved explanation template (auto-draft): break the idea into a short analogy, "
                "one exam-style checkpoint question, and a real-world hook."
            ),
            critique={"reason": "aggregated_wrong_attempts"},
        )
        created += 1
    return {"proposals_created": created}


@celery.task(name="phase4.teacher_group_suggestions_scan")
def teacher_group_suggestions_scan() -> dict:
    from app.models.school_org_models import ClassSection
    from app.services.phase4 import group_study_v2_service

    db: Session = get_db()
    teachers = db.query(ClassSection.teacher_user_id).distinct().limit(100).all()
    n = 0
    for (tid,) in teachers:
        if not tid:
            continue
        secs = db.query(ClassSection).filter(ClassSection.teacher_user_id == tid).limit(3).all()
        for sec in secs:
            group_study_v2_service.create_teacher_suggestion(
                db,
                teacher_user_id=int(tid),
                class_section_id=sec.id,
                payload={
                    "summary": "Cluster students with overlapping weak topics (auto-draft).",
                    "class_section_id": sec.id,
                    "suggested_name": f"Remediation — {sec.display_name or sec.id}",
                },
            )
            n += 1
    return {"suggestions": n}
