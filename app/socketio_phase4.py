"""Flask-SocketIO: group presence and collaborative notes broadcast."""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

socketio = None


def init_socketio(app):
    global socketio
    try:
        from flask_socketio import SocketIO

        socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")

        @socketio.on("join_group")
        def handle_join(data):
            from flask import request, session

            from app.utils.db import get_db

            gid = (data or {}).get("group_id")
            if not gid or "user_id" not in session:
                return
            db = get_db()
            from app.services.phase4 import group_study_v2_service

            if not group_study_v2_service._is_member(db, group_id=int(gid), user_id=int(session["user_id"])):
                logger.warning("Socket join denied group=%s user=%s", gid, session.get("user_id"))
                return
            from flask_socketio import join_room

            join_room(f"group_{gid}")
            socketio.emit(
                "presence",
                {"user_id": session["user_id"], "event": "join", "group_id": int(gid)},
                room=f"group_{gid}",
                skip_sid=request.sid,
            )

        @socketio.on("leave_group")
        def handle_leave(data):
            from flask import request, session
            from flask_socketio import leave_room

            gid = (data or {}).get("group_id")
            if not gid:
                return
            leave_room(f"group_{gid}")
            socketio.emit(
                "presence",
                {"user_id": session.get("user_id"), "event": "leave", "group_id": int(gid)},
                room=f"group_{gid}",
                skip_sid=request.sid,
            )

        @socketio.on("notes_update")
        def handle_notes(data):
            from flask import request, session

            from app.utils.db import get_db

            gid = (data or {}).get("group_id")
            body = (data or {}).get("body", "")
            version = (data or {}).get("version", 1)
            if not gid or "user_id" not in session:
                return
            db = get_db()
            from app.services.phase4 import group_study_v2_service

            try:
                notes = group_study_v2_service.update_notes(
                    db,
                    group_id=int(gid),
                    user_id=int(session["user_id"]),
                    body_text=str(body),
                    expected_version=int(version),
                )
                socketio.emit(
                    "notes_saved",
                    {"group_id": int(gid), "version": notes.version, "body": notes.body_text},
                    room=f"group_{gid}",
                )
            except ValueError as exc:
                socketio.emit("notes_error", {"error": str(exc)}, to=request.sid)

        logger.info("Flask-SocketIO initialized (threading mode)")
        return socketio
    except ImportError as exc:
        logger.warning("Flask-SocketIO not installed: %s", exc)
        return None
