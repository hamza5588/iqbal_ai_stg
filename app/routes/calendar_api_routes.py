"""JSON API for calendar connection status and Apple CalDAV credential capture."""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request, session

from app.services.calendar_connection_service import (
    delete_connection,
    list_connections_public,
    save_apple_caldav_credentials,
)
from app.utils.auth import login_required
from app.utils.db import get_db

logger = logging.getLogger(__name__)

calendar_api_bp = Blueprint("calendar_api", __name__, url_prefix="/api/calendar")


def _uid() -> int:
    return int(session["user_id"])


@calendar_api_bp.route("/connections", methods=["GET"])
@login_required
def get_calendar_connections():
    db = get_db()
    return jsonify({"connections": list_connections_public(db, user_id=_uid())})


@calendar_api_bp.route("/connections/apple", methods=["POST"])
@login_required
def post_apple_calendar():
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        save_apple_caldav_credentials(
            db,
            user_id=_uid(),
            apple_id=data.get("apple_id") or data.get("icloud_apple_id") or "",
            app_specific_password=data.get("app_password") or data.get("icloud_app_password") or "",
        )
        return jsonify({"ok": True}), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.exception("Apple calendar save failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@calendar_api_bp.route("/connections/<provider>", methods=["DELETE"])
@login_required
def delete_calendar_connection(provider: str):
    db = get_db()
    ok = delete_connection(db, user_id=_uid(), provider=provider)
    if not ok:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True})
