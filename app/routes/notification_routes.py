"""Notification inbox routes — all roles read their own notifications."""
from flask import Blueprint, jsonify, request, session
from app.utils.db import get_db
from app.utils.auth import login_required
from app.rbac.decorators import admin_only
import app.services.notification_service as notif_svc
from app.services.school.errors import SchoolServiceError

notification_bp = Blueprint("notification_bp", __name__)


def _json_error(exc: SchoolServiceError):
    return jsonify({"error": exc.message, "code": exc.code}), exc.http_status


@notification_bp.route("/api/notifications/", methods=["GET"])
@notification_bp.route("/api/notifications", methods=["GET"])
@login_required
def list_notifications():
    db = get_db()
    user_id = session["user_id"]
    unread_only = request.args.get("unread_only", "false").lower() == "true"
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 20))
    notifications = notif_svc.list_notifications(
        db, user_id=user_id, unread_only=unread_only, page=page, per_page=per_page
    )
    # Fetch one extra to detect if there are more pages
    total = len(notifications)
    has_more = total == per_page
    return jsonify({
        "notifications": [_serialize(n) for n in notifications],
        "has_more": has_more,
        "page": page,
    })


@notification_bp.route("/api/notifications/count", methods=["GET"])
@login_required
def unread_count():
    db = get_db()
    count = notif_svc.unread_count(db, user_id=session["user_id"])
    return jsonify({"count": count, "unread_count": count})


@notification_bp.route("/api/notifications/<int:notification_id>/read", methods=["POST"])
@login_required
def mark_read(notification_id):
    db = get_db()
    try:
        n = notif_svc.mark_read(db, user_id=session["user_id"], notification_id=notification_id)
        db.commit()
        return jsonify(_serialize(n))
    except SchoolServiceError as exc:
        return _json_error(exc)


@notification_bp.route("/api/notifications/read-all", methods=["POST"])
@login_required
def mark_all_read():
    db = get_db()
    count = notif_svc.mark_all_read(db, user_id=session["user_id"])
    db.commit()
    return jsonify({"marked_read": count})


@notification_bp.route("/api/notifications/admin/send", methods=["POST"])
@login_required
@admin_only
def admin_send():
    """Platform admin: send a notification to a specific user."""
    db = get_db()
    data = request.get_json(silent=True) or {}
    recipient_id = data.get("recipient_id")
    title = data.get("title", "")
    message = data.get("message", "")
    ntype = data.get("type", "info")
    action_link = data.get("action_link")
    if not recipient_id or not title or not message:
        return jsonify({"error": "recipient_id, title and message are required"}), 400
    try:
        n = notif_svc.create_notification(
            db,
            recipient_id=recipient_id,
            title=title,
            message=message,
            type=ntype,
            action_link=action_link,
        )
        db.commit()
        return jsonify(_serialize(n)), 201
    except SchoolServiceError as exc:
        return _json_error(exc)


def _serialize(n) -> dict:
    return {
        "id": n.id,
        "title": n.title,
        "message": n.message,
        "type": n.type,
        "is_read": n.is_read,
        "action_link": n.action_link,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }
