"""Wire mini_lecture.html to backend (draft + publish)."""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request, session

from app.services.phase3.mini_lecture_service import upsert_mini_lecture
from app.utils.auth import login_required
from app.utils.db import get_db
from app.utils.decorators import teacher_required

logger = logging.getLogger(__name__)

mini_lecture_api_bp = Blueprint("mini_lecture_api", __name__, url_prefix="/api")


def _uid() -> int:
    return int(session["user_id"])


@mini_lecture_api_bp.route("/mini-lectures", methods=["POST"])
@login_required
@teacher_required
def post_mini_lectures():
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        lesson = upsert_mini_lecture(
            db,
            teacher_user_id=_uid(),
            title=str(data.get("title") or "").strip(),
            content=str(data.get("content") or ""),
            objective=(data.get("objective") or "").strip() or None,
            related_lesson_id=data.get("related_lesson_id"),
            target_student_ids=[int(x) for x in (data.get("target_student_ids") or [])],
            status=str(data.get("status") or "draft"),
            hide_from_others=bool(data.get("hide_from_others", True)),
            notify_students=False,
            mini_lesson_id=data.get("mini_lesson_id"),
        )
        return jsonify({"ok": True, "mini_lesson_id": lesson.id}), 201
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except Exception as e:
        logger.exception("mini-lectures save")
        return jsonify({"error": str(e)}), 500


@mini_lecture_api_bp.route("/mini-lectures/publish", methods=["POST"])
@login_required
@teacher_required
def post_mini_lectures_publish():
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        lesson = upsert_mini_lecture(
            db,
            teacher_user_id=_uid(),
            title=str(data.get("title") or "").strip(),
            content=str(data.get("content") or ""),
            objective=(data.get("objective") or "").strip() or None,
            related_lesson_id=data.get("related_lesson_id"),
            target_student_ids=[int(x) for x in (data.get("target_student_ids") or [])],
            status="published",
            hide_from_others=bool(data.get("hide_from_others", True)),
            notify_students=bool(data.get("notify_students", True)),
            mini_lesson_id=data.get("mini_lesson_id"),
        )
        return jsonify({"ok": True, "mini_lesson_id": lesson.id}), 200
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except Exception as e:
        logger.exception("mini-lectures publish")
        return jsonify({"error": str(e)}), 500
