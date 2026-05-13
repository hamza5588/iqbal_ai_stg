"""Internationalisation routes — language strings and preference setting."""
from flask import Blueprint, jsonify, request, session
from app.utils.db import get_db
from app.utils.auth import login_required
import app.services.i18n_service as i18n_svc
from app.services.school.errors import SchoolServiceError

i18n_bp = Blueprint("i18n_bp", __name__)


@i18n_bp.route("/api/i18n/strings", methods=["GET"])
def get_strings():
    """Return all UI translation strings for the requested language."""
    lang = request.args.get("lang", "en")
    strings = i18n_svc.get_all_strings(lang)
    return jsonify({"lang": lang, "strings": strings})


@i18n_bp.route("/api/i18n/preference", methods=["POST"])
@login_required
def set_preference():
    """Set the current user's preferred language."""
    db = get_db()
    data = request.get_json(silent=True) or {}
    lang = data.get("lang", "")
    try:
        user = i18n_svc.set_user_language(db, user_id=session["user_id"], lang_code=lang)
        db.commit()
        return jsonify({"preferred_language": user.preferred_language})
    except SchoolServiceError as exc:
        return jsonify({"error": exc.message, "code": exc.code}), exc.http_status
