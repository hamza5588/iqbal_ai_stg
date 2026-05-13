"""Schedule precise notifications via DB + Celery eta."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.phase4_models import ScheduledNotification

logger = logging.getLogger(__name__)


def schedule_notification(
    db: Session,
    *,
    recipient_user_id: int,
    fire_at_utc: datetime,
    title: str,
    message: str,
    notif_type: str = "phase4_reminder",
    action_link: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> ScheduledNotification:
    row = ScheduledNotification(
        recipient_user_id=int(recipient_user_id),
        fire_at_utc=fire_at_utc,
        title=title[:500],
        message=message,
        notif_type=notif_type,
        action_link=action_link,
        payload_json=json.dumps(payload or {}, default=str),
        status="pending",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        from app.tasks.phase4_tasks import deliver_scheduled_notification_task

        async_res = deliver_scheduled_notification_task.apply_async(args=[row.id], eta=fire_at_utc)
        row.celery_task_id = async_res.id
        db.commit()
    except Exception as exc:
        logger.warning("Could not schedule Celery eta for notification %s: %s", row.id, exc)
    return row


def cancel_pending(db: Session, *, scheduled_id: int, recipient_user_id: int) -> bool:
    row = (
        db.query(ScheduledNotification)
        .filter(
            ScheduledNotification.id == scheduled_id,
            ScheduledNotification.recipient_user_id == int(recipient_user_id),
            ScheduledNotification.status == "pending",
        )
        .first()
    )
    if not row:
        return False
    row.status = "cancelled"
    db.commit()
    return True
