"""Multi-year data archival routes — admin only."""
from flask import Blueprint, jsonify, request, session
from app.utils.db import get_db
from app.utils.auth import login_required
from app.rbac.decorators import admin_only
import app.services.archival_service as archival_svc
from app.services.school.errors import SchoolServiceError

archival_bp = Blueprint("archival_bp", __name__)


def _json_error(exc: SchoolServiceError):
    return jsonify({"error": exc.message, "code": exc.code}), exc.http_status


@archival_bp.route("/api/admin/archival/class-sections/<int:section_id>/archive", methods=["POST"])
@login_required
@admin_only
def archive_section(section_id):
    """Archive a class section."""
    db = get_db()
    try:
        section = archival_svc.archive_class_section(db, actor_id=session["user_id"], class_section_id=section_id)
        db.commit()
        return jsonify({"id": section.id, "status": section.status})
    except SchoolServiceError as exc:
        return _json_error(exc)


@archival_bp.route("/api/admin/archival/schools/<int:school_id>/rollover", methods=["POST"])
@login_required
@admin_only
def rollover_year(school_id):
    """Rollover academic year — clone active sections to new year."""
    db = get_db()
    data = request.get_json(silent=True) or {}
    from_year = data.get("from_year", "")
    to_year = data.get("to_year", "")
    if not from_year or not to_year:
        return jsonify({"error": "from_year and to_year are required", "code": "validation_error"}), 400
    try:
        result = archival_svc.rollover_academic_year(
            db, actor_id=session["user_id"], school_id=school_id, from_year=from_year, to_year=to_year
        )
        db.commit()
        return jsonify(result)
    except SchoolServiceError as exc:
        return _json_error(exc)


@archival_bp.route("/api/admin/archival/schools/<int:school_id>/archive", methods=["POST"])
@login_required
@admin_only
def archive_school(school_id):
    """Archive a school and all its active class sections."""
    db = get_db()
    try:
        school = archival_svc.archive_school(db, actor_id=session["user_id"], school_id=school_id)
        db.commit()
        return jsonify({"id": school.id, "status": school.status})
    except SchoolServiceError as exc:
        return _json_error(exc)
