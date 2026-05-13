"""Student data export and account deletion routes."""
import io
from flask import Blueprint, jsonify, request, session, send_file
from app.utils.db import get_db
from app.utils.auth import login_required
from app.models.database_models import User as DBUser
import app.services.export_service as export_svc
import app.services.personality_service as personality_svc
from app.services.school.errors import SchoolServiceError

export_bp = Blueprint("export_bp", __name__)


def _json_error(exc: SchoolServiceError):
    return jsonify({"error": exc.message, "code": exc.code}), exc.http_status


@export_bp.route("/api/student/me", methods=["GET"])
@login_required
def get_current_user():
    """Return current user's profile info for settings page."""
    db = get_db()
    user = db.query(DBUser).filter(DBUser.id == int(session["user_id"])).first()
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify({
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "preferred_language": getattr(user, "preferred_language", "en"),
        "personality_id": getattr(user, "personality_id", None),
        "terms_accepted_version": getattr(user, "terms_accepted_version", None),
    })


@export_bp.route("/api/student/<int:uid>/personality", methods=["POST"])
@login_required
def set_personality(uid):
    """Set the student's AI teaching personality."""
    if int(session["user_id"]) != uid:
        return jsonify({"error": "Forbidden"}), 403
    db = get_db()
    data = request.get_json(silent=True) or {}
    personality_id = data.get("personality_id")
    if not personality_id:
        return jsonify({"error": "personality_id is required"}), 400
    try:
        personality_svc.set_student_personality(db, student_id=uid, personality_id=personality_id)
        db.commit()
        return jsonify({"ok": True, "personality_id": personality_id})
    except SchoolServiceError as exc:
        return jsonify({"error": exc.message, "code": exc.code}), exc.http_status


@export_bp.route("/api/student/<int:uid>/export", methods=["GET"])
@login_required
def export_data(uid):
    """Download own data as a ZIP archive."""
    db = get_db()
    try:
        zip_bytes = export_svc.export_student_data(
            db, student_user_id=uid, requesting_user_id=session["user_id"]
        )
        return send_file(
            io.BytesIO(zip_bytes),
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"iqbalai_data_{uid}.zip",
        )
    except SchoolServiceError as exc:
        return _json_error(exc)


@export_bp.route("/api/student/<int:uid>/account", methods=["DELETE"])
@login_required
def delete_account(uid):
    """Anonymise account PII (GDPR-style deletion)."""
    db = get_db()
    try:
        export_svc.anonymize_student(db, actor_id=session["user_id"], student_user_id=uid)
        db.commit()
        return jsonify({"ok": True, "message": "Account data has been anonymised."})
    except SchoolServiceError as exc:
        return _json_error(exc)
