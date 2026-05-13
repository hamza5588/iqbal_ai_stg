"""
Admin user management routes — hierarchical user creation, audit log, suspend/reactivate.
All endpoints require @admin_only (super-admin roles).
"""
from flask import Blueprint, jsonify, request, session
from app.utils.db import get_db
from app.rbac.decorators import admin_only
from app.utils.auth import login_required
import app.services.user_admin_service as ua_svc
from app.services.school.errors import SchoolServiceError

user_admin_bp = Blueprint("user_admin_bp", __name__)


def _json_error(exc: SchoolServiceError):
    return jsonify({"error": exc.message, "code": exc.code}), exc.http_status


def _serialize_user(u) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "useremail": u.useremail,
        "role": u.role,
        "is_active": u.is_active,
        "suspended_at": u.suspended_at.isoformat() if u.suspended_at else None,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


def _serialize_log(log) -> dict:
    return {
        "id": log.id,
        "creator_id": log.creator_id,
        "created_user_id": log.created_user_id,
        "role_assigned": log.role_assigned,
        "scope_school_id": log.scope_school_id,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


@user_admin_bp.route("/api/admin/users/invite", methods=["POST"])
@login_required
@admin_only
def invite_user():
    """Create/invite a new user from a higher-role admin."""
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        user = ua_svc.invite_create_user(
            db,
            actor_id=session["user_id"],
            email=data.get("email", ""),
            username=data.get("username", ""),
            role=data.get("role", ""),
            scope_school_id=data.get("scope_school_id"),
            class_standard=data.get("class_standard", ""),
            medium=data.get("medium", ""),
        )
        icloud_id = (data.get("icloud_apple_id") or "").strip()
        icloud_pw = (data.get("icloud_app_password") or "").strip()
        if icloud_id and icloud_pw:
            try:
                from app.services.calendar_connection_service import save_apple_caldav_credentials

                save_apple_caldav_credentials(
                    db,
                    user_id=user.id,
                    apple_id=icloud_id,
                    app_specific_password=icloud_pw,
                )
            except Exception:
                # Non-fatal: user is created; calendar can be linked later from settings.
                pass
        db.commit()
        return jsonify(_serialize_user(user)), 201
    except SchoolServiceError as exc:
        return _json_error(exc)


@user_admin_bp.route("/api/admin/users/creation-log", methods=["GET"])
@login_required
@admin_only
def creation_log():
    """Paginated admin creation audit log."""
    db = get_db()
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    creator_id = request.args.get("creator_id", type=int)
    logs = ua_svc.get_creation_log(db, creator_id=creator_id, page=page, per_page=per_page)
    return jsonify([_serialize_log(l) for l in logs])


@user_admin_bp.route("/api/admin/users/<int:uid>/suspend", methods=["POST"])
@login_required
@admin_only
def suspend_user(uid):
    """Suspend a user account."""
    db = get_db()
    try:
        user = ua_svc.suspend_user(db, actor_id=session["user_id"], target_user_id=uid)
        db.commit()
        return jsonify(_serialize_user(user))
    except SchoolServiceError as exc:
        return _json_error(exc)


@user_admin_bp.route("/api/admin/users/<int:uid>/reactivate", methods=["POST"])
@login_required
@admin_only
def reactivate_user(uid):
    """Reactivate a suspended user account."""
    db = get_db()
    try:
        user = ua_svc.reactivate_user(db, actor_id=session["user_id"], target_user_id=uid)
        db.commit()
        return jsonify(_serialize_user(user))
    except SchoolServiceError as exc:
        return _json_error(exc)
