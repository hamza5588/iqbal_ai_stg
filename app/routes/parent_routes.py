"""Parent-Student linking routes."""
from flask import Blueprint, jsonify, request, session
from app.utils.db import get_db
from app.utils.auth import login_required
import app.services.parent_service as parent_svc
from app.services.school.errors import SchoolServiceError

parent_bp = Blueprint("parent_bp", __name__)


def _json_error(exc: SchoolServiceError):
    return jsonify({"error": exc.message, "code": exc.code}), exc.http_status


def _serialize_link(lnk) -> dict:
    return {
        "id": lnk.id,
        "parent_id": lnk.parent_id,
        "student_id": lnk.student_id,
        "status": lnk.status,
        "requested_at": lnk.requested_at.isoformat() if lnk.requested_at else None,
        "resolved_at": lnk.resolved_at.isoformat() if lnk.resolved_at else None,
    }


def _serialize_user(u) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "class_standard": u.class_standard,
        "medium": u.medium,
    }


@parent_bp.route("/api/parent/link-request", methods=["POST"])
@login_required
def request_link():
    """Parent requests to link with a student."""
    db = get_db()
    data = request.get_json(silent=True) or {}
    student_id = data.get("student_id")
    if not student_id:
        return jsonify({"error": "student_id is required", "code": "validation_error"}), 400
    try:
        link = parent_svc.request_link(db, parent_id=session["user_id"], student_id=student_id)
        db.commit()
        return jsonify(_serialize_link(link)), 201
    except SchoolServiceError as exc:
        return _json_error(exc)


@parent_bp.route("/api/parent/children", methods=["GET"])
@login_required
def list_children():
    """List approved-linked children for the logged-in parent."""
    db = get_db()
    try:
        children = parent_svc.get_children(db, parent_id=session["user_id"])
        return jsonify([_serialize_user(c) for c in children])
    except SchoolServiceError as exc:
        return _json_error(exc)


@parent_bp.route("/api/parent/children/<int:student_id>/summary", methods=["GET"])
@login_required
def child_summary(student_id):
    """View-only child summary for an approved parent link."""
    db = get_db()
    try:
        summary = parent_svc.get_child_summary(db, parent_id=session["user_id"], student_id=student_id)
        return jsonify(summary)
    except SchoolServiceError as exc:
        return _json_error(exc)


@parent_bp.route("/api/parent/link-requests", methods=["GET"])
@login_required
def list_link_requests():
    """Student views their pending/resolved parent link requests."""
    db = get_db()
    links = parent_svc.get_parent_links_for_student(db, student_id=session["user_id"])
    return jsonify([_serialize_link(lnk) for lnk in links])


@parent_bp.route("/api/parent/link-requests/<int:link_id>/approve", methods=["POST"])
@login_required
def approve_link(link_id):
    """Student or admin approves a parent link request."""
    db = get_db()
    try:
        link = parent_svc.resolve_link(db, resolver_id=session["user_id"], link_id=link_id, approved=True)
        db.commit()
        return jsonify(_serialize_link(link))
    except SchoolServiceError as exc:
        return _json_error(exc)


@parent_bp.route("/api/parent/link-requests/<int:link_id>/reject", methods=["POST"])
@login_required
def reject_link(link_id):
    """Student or admin rejects a parent link request."""
    db = get_db()
    try:
        link = parent_svc.resolve_link(db, resolver_id=session["user_id"], link_id=link_id, approved=False)
        db.commit()
        return jsonify(_serialize_link(link))
    except SchoolServiceError as exc:
        return _json_error(exc)


@parent_bp.route("/api/parent/link-requests/<int:link_id>", methods=["DELETE"])
@login_required
def remove_link(link_id):
    """Remove a parent-student link."""
    db = get_db()
    try:
        parent_svc.remove_link(db, actor_id=session["user_id"], link_id=link_id)
        db.commit()
        return jsonify({"ok": True})
    except SchoolServiceError as exc:
        return _json_error(exc)
