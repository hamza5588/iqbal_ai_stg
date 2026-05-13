"""Terms of Service acceptance routes."""
from flask import Blueprint, jsonify, request, session
from app.utils.db import get_db
from app.utils.auth import login_required
import app.services.terms_service as terms_svc

terms_bp = Blueprint("terms_bp", __name__)


@terms_bp.route("/api/terms/current", methods=["GET"])
def get_current_terms():
    """Return the current terms version and pass-probability disclaimer (public)."""
    return jsonify({
        "version": terms_svc.CURRENT_TERMS_VERSION,
        "pass_probability_disclaimer": terms_svc.PASS_PROBABILITY_DISCLAIMER,
    })


@terms_bp.route("/api/terms/accept", methods=["POST"])
@login_required
def accept_terms():
    """Record that the current user has accepted the current terms."""
    db = get_db()
    user_id = session["user_id"]
    ip = request.remote_addr
    record = terms_svc.record_acceptance(db, user_id=user_id, ip_address=ip)
    db.commit()
    return jsonify({
        "accepted": True,
        "terms_version": record.terms_version,
        "accepted_at": record.accepted_at.isoformat() if record.accepted_at else None,
    }), 201


@terms_bp.route("/api/terms/status", methods=["GET"])
@login_required
def terms_status():
    """Return whether the current user has accepted the current terms."""
    db = get_db()
    user_id = session["user_id"]
    accepted = terms_svc.has_accepted(db, user_id=user_id)
    return jsonify({
        "accepted": accepted,
        "current_version": terms_svc.CURRENT_TERMS_VERSION,
    })
