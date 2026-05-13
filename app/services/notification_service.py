"""
Notification service — create, list, and mark notifications for any role.

All platform events (suspension, waitlist promotion, parent link, etc.)
call into this service to write Notification rows.
"""
from __future__ import annotations

from typing import List, Optional

from app.models.phase1_models import Notification
from app.services.school.errors import SchoolServiceError


def create_notification(
    db,
    *,
    recipient_id: int,
    title: str,
    message: str,
    type: str = "info",
    action_link: Optional[str] = None,
) -> Notification:
    """Create a single notification for one recipient."""
    valid_types = {
        "info",
        "warning",
        "action",
        "suspension",
        "waitlist",
        "parent_link",
        "phase4_reminder",
        "phase4_parent_risk",
        "phase4_va",
        "phase4_group_invite",
        "phase4_mistake_alert",
    }
    if type not in valid_types:
        type = "info"

    notif = Notification(
        recipient_id=recipient_id,
        title=title,
        message=message,
        type=type,
        action_link=action_link,
    )
    db.add(notif)
    db.flush()
    return notif


def bulk_notify(
    db,
    *,
    recipient_ids: List[int],
    title: str,
    message: str,
    type: str = "info",
    action_link: Optional[str] = None,
) -> int:
    """Create notifications for multiple recipients. Returns count created."""
    if not recipient_ids:
        return 0
    valid_types = {
        "info",
        "warning",
        "action",
        "suspension",
        "waitlist",
        "parent_link",
        "phase4_reminder",
        "phase4_parent_risk",
        "phase4_va",
        "phase4_group_invite",
        "phase4_mistake_alert",
    }
    if type not in valid_types:
        type = "info"
    count = 0
    for uid in recipient_ids:
        n = Notification(
            recipient_id=uid,
            title=title,
            message=message,
            type=type,
            action_link=action_link,
        )
        db.add(n)
        count += 1
    db.flush()
    return count


def mark_read(db, *, user_id: int, notification_id: int) -> Notification:
    """Mark a single notification as read. Raises 404 if not found or not owned."""
    notif = db.query(Notification).filter_by(id=notification_id, recipient_id=user_id).first()
    if not notif:
        raise SchoolServiceError("Notification not found", "not_found", 404)
    notif.is_read = True
    db.flush()
    return notif


def mark_all_read(db, *, user_id: int) -> int:
    """Mark all notifications for a user as read. Returns count updated."""
    count = (
        db.query(Notification)
        .filter_by(recipient_id=user_id, is_read=False)
        .update({"is_read": True})
    )
    db.flush()
    return count


def list_notifications(
    db,
    *,
    user_id: int,
    unread_only: bool = False,
    page: int = 1,
    per_page: int = 20,
) -> List[Notification]:
    """Return paginated notifications for a user, newest first."""
    q = db.query(Notification).filter_by(recipient_id=user_id)
    if unread_only:
        q = q.filter_by(is_read=False)
    q = q.order_by(Notification.created_at.desc())
    offset = (page - 1) * per_page
    return q.offset(offset).limit(per_page).all()


def unread_count(db, *, user_id: int) -> int:
    """Return count of unread notifications for a user."""
    return db.query(Notification).filter_by(recipient_id=user_id, is_read=False).count()
