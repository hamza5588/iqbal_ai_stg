"""Periodic study-plan reminders (email; SMS/push hooks)."""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from typing import Any, Dict, List

from flask_mail import Message
from sqlalchemy.orm import Session

from app import mail
from app.celery_app import celery
from app.models.database_models import User as DBUser
from app.models.phase3_models import StudentLearningPreferences, StudentStudyPlan
from app.utils.db import get_db

logger = logging.getLogger(__name__)


def _today_focus_blocks(plan_blob: Dict[str, Any], today: date) -> List[str]:
    ts = today.isoformat()
    titles: List[str] = []
    for sec in plan_blob.get("sections") or []:
        for day in sec.get("days") or []:
            ds = str(day.get("date") or "")[:10]
            if ds != ts:
                continue
            titles.append(
                (day.get("focus_title") or day.get("title") or "Study block").strip()
            )
    return titles


def _load_reminder_state(raw: str | None) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        o = json.loads(raw)
        return o if isinstance(o, dict) else {}
    except Exception:
        return {}


def _maybe_send_sms_stub(phone_hint: str | None, body: str) -> None:
    sid = (os.getenv("TWILIO_ACCOUNT_SID") or "").strip()
    token = (os.getenv("TWILIO_AUTH_TOKEN") or "").strip()
    from_num = (os.getenv("TWILIO_FROM_NUMBER") or "").strip()
    to_num = (os.getenv("REMINDER_TEST_SMS_TO") or "").strip()
    if not (sid and token and from_num and to_num):
        logger.info("SMS reminder (stub): %s", body[:200])
        return
    try:
        from twilio.rest import Client  # type: ignore

        Client(sid, token).messages.create(to=to_num, from_=from_num, body=body[:1400])
    except Exception as exc:
        logger.warning("Twilio SMS failed: %s", exc)


@celery.task(name="phase3.study_plan_reminders")
def send_study_plan_reminders() -> Dict[str, Any]:
    """Called by Celery beat — sends at most one digest email per user per local day."""
    db: Session = get_db()
    prefs_rows = db.query(StudentLearningPreferences).all()
    sent = 0
    skipped = 0
    today = date.today()

    for pref in prefs_rows:
        ch: Dict[str, Any] = {}
        if pref.reminder_channels_json:
            try:
                ch = json.loads(pref.reminder_channels_json)
            except Exception:
                ch = {}
        if not ch.get("email"):
            skipped += 1
            continue

        state = _load_reminder_state(pref.reminder_state_json)
        if state.get("last_digest_date") == today.isoformat():
            skipped += 1
            continue

        user = db.query(DBUser).filter(DBUser.id == pref.student_user_id).first()
        if not user or not (user.useremail or "").strip():
            skipped += 1
            continue

        plan = (
            db.query(StudentStudyPlan)
            .filter(StudentStudyPlan.student_user_id == pref.student_user_id)
            .order_by(StudentStudyPlan.updated_at.desc())
            .first()
        )
        if not plan:
            skipped += 1
            continue
        try:
            blob = json.loads(plan.plan_json)
        except Exception:
            blob = {}
        blocks = _today_focus_blocks(blob, today)
        if not blocks:
            skipped += 1
            continue

        subject = f"Today's study plan — {today.isoformat()}"
        body_lines = [
            "Hi,",
            "",
            "Here is what your Iqbal AI study plan has lined up for today:",
            "",
            *[f"- {t}" for t in blocks[:40]],
            "",
            "Open your learning hub to track progress.",
            "",
            "— Iqbal AI",
        ]
        body = "\n".join(body_lines)

        try:
            msg = Message(subject, recipients=[user.useremail.strip()], body=body)
            mail.send(msg)
        except Exception as exc:
            logger.warning("Reminder email failed for user %s: %s", pref.student_user_id, exc)
            continue

        if ch.get("sms"):
            _maybe_send_sms_stub(None, subject + "\n" + body[:500])

        if ch.get("push"):
            logger.info(
                "Push reminder (stub) user=%s blocks=%s",
                pref.student_user_id,
                len(blocks),
            )

        state["last_digest_date"] = today.isoformat()
        pref.reminder_state_json = json.dumps(state, default=str)
        db.commit()
        sent += 1

    return {"sent": sent, "skipped": skipped, "day": today.isoformat()}
