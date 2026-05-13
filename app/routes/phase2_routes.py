"""
Phase 2 UI routes.

Serves all Phase 2 teacher / student / admin frontend pages.
All endpoints require login; role-specific pages abort(403) for wrong roles.
"""
from __future__ import annotations

import logging
from flask import Blueprint, abort, render_template, session
from app.models.database_models import User as DBUser
from app.rbac.roles import Role, is_super_admin_role
from app.utils.auth import login_required
from app.utils.db import get_db

logger = logging.getLogger(__name__)

phase2_bp = Blueprint("phase2", __name__)


def _current_role() -> Role:
    db = get_db()
    user = db.query(DBUser).filter(DBUser.id == int(session["user_id"])).first()
    return Role.from_string((user.role if user else "student") or "student")


def _require_student_or_super():
    role = _current_role()
    if role != Role.STUDENT and not is_super_admin_role(role):
        abort(403)


_TEACHER_ROLES = {Role.TEACHER, Role.SCHOOL_ADMIN, Role.DISTRICT_ADMIN, Role.PLATFORM_ADMIN}
_ADMIN_ROLES = {Role.SCHOOL_ADMIN, Role.DISTRICT_ADMIN, Role.PLATFORM_ADMIN}


# ---------------------------------------------------------------------------
# Teacher-facing pages
# ---------------------------------------------------------------------------

@phase2_bp.route("/teacher-profile")
@login_required
def teacher_profile():
    """Teacher profile setup / edit."""
    role = _current_role()
    if role not in _TEACHER_ROLES and not is_super_admin_role(role):
        abort(403)
    return render_template("teacher_profile.html")


@phase2_bp.route("/curriculum-editor")
@login_required
def curriculum_editor():
    """Curriculum PDF upload and topic tree editor."""
    role = _current_role()
    if role not in _TEACHER_ROLES and not is_super_admin_role(role):
        abort(403)
    return render_template("curriculum_editor.html")


@phase2_bp.route("/teacher-connections")
@login_required
def teacher_connections():
    """Connection requests — teacher view (pending / accept / reject)."""
    role = _current_role()
    if role not in _TEACHER_ROLES and not is_super_admin_role(role):
        abort(403)
    return render_template("teacher_connections.html")


@phase2_bp.route("/lecture-editor")
@phase2_bp.route("/lecture-editor/<int:lesson_id>")
@login_required
def lecture_editor(lesson_id: int = 0):
    """Full lecture editor with AI chat sidebar and version history."""
    role = _current_role()
    if role not in _TEACHER_ROLES and not is_super_admin_role(role):
        abort(403)
    return render_template("lecture_editor.html", lesson_id=lesson_id)


@phase2_bp.route("/next-day-review")
@login_required
def next_day_review():
    """Next-day review screen: AI summary, most-asked questions, mini-lecture actions."""
    role = _current_role()
    if role not in _TEACHER_ROLES and not is_super_admin_role(role):
        abort(403)
    return render_template("next_day_review.html")


@phase2_bp.route("/mini-lecture")
@phase2_bp.route("/mini-lecture/<int:lesson_id>")
@login_required
def mini_lecture(lesson_id: int = 0):
    """Create / edit a mini-lecture and publish to selected students."""
    role = _current_role()
    if role not in _TEACHER_ROLES and not is_super_admin_role(role):
        abort(403)
    return render_template("mini_lecture.html", lesson_id=lesson_id)


# ---------------------------------------------------------------------------
# Student-facing pages
# ---------------------------------------------------------------------------

@phase2_bp.route("/teacher-discovery")
@login_required
def teacher_discovery():
    """Teacher discovery / search page for students."""
    return render_template("teacher_discovery.html")


@phase2_bp.route("/student-connections")
@login_required
def student_connections():
    """Connection request status for students."""
    return render_template("student_connections.html")


# ---------------------------------------------------------------------------
# Phase 3 student learning UI
# ---------------------------------------------------------------------------

@phase2_bp.route("/student-learning")
@login_required
def student_learning_hub():
    """Dashboard hub: study plans, lecture reader, flashcards, calendar connections."""
    _require_student_or_super()
    return render_template("student_learning_hub.html")


@phase2_bp.route("/student-learning/flashcards")
@login_required
def student_learning_flashcards():
    _require_student_or_super()
    return render_template("student_learning_flashcards.html")


@phase2_bp.route("/student-learning/diagnostic")
@login_required
def student_learning_diagnostic():
    _require_student_or_super()
    return render_template("student_learning_diagnostic.html")


@phase2_bp.route("/student-learning/preferences")
@login_required
def student_learning_preferences_page():
    _require_student_or_super()
    return render_template("student_learning_preferences.html")


@phase2_bp.route("/student-learning/adherence")
@login_required
def student_learning_adherence():
    _require_student_or_super()
    return render_template("student_learning_adherence.html")


@phase2_bp.route("/student-learning/exam-targets")
@login_required
def student_learning_exam_targets():
    _require_student_or_super()
    return render_template("student_learning_exam_targets.html")


@phase2_bp.route("/student-learning/uploads")
@login_required
def student_learning_uploads():
    _require_student_or_super()
    return render_template("student_learning_uploads.html")


@phase2_bp.route("/student-learning/group-study")
@login_required
def student_learning_group_study():
    _require_student_or_super()
    return render_template("student_learning_group_study.html")


@phase2_bp.route("/student-learning/phase4-intelligence")
@login_required
def student_learning_phase4_intelligence():
    _require_student_or_super()
    return render_template("student_learning_phase4.html")


@phase2_bp.route("/student-learning/phase4-practice")
@login_required
def student_learning_phase4_practice():
    _require_student_or_super()
    return render_template("student_learning_phase4_practice.html")


@phase2_bp.route("/lecture-reader/<int:lesson_id>")
@login_required
def lecture_reader(lesson_id: int):
    """Rich reader + Phase 3 panels (highlights, AI side stack, voice hooks)."""
    role = _current_role()
    if role != Role.STUDENT and not is_super_admin_role(role):
        abort(403)
    return render_template("lecture_reader.html", lesson_id=lesson_id)


@phase2_bp.route("/delivery-tips-print/<int:lesson_id>")
@login_required
def delivery_tips_print(lesson_id: int):
    """Printable / mobile-friendly delivery guidance."""
    role = _current_role()
    if role not in _TEACHER_ROLES and not is_super_admin_role(role):
        abort(403)
    return render_template("delivery_tips_print.html", lesson_id=lesson_id)


# ---------------------------------------------------------------------------
# Admin / Coordinator pages
# ---------------------------------------------------------------------------

@phase2_bp.route("/admin/teacher-metrics")
@login_required
def teacher_metrics():
    """Admin / coordinator teacher metrics and benchmarking page."""
    role = _current_role()
    coordinator_roles = {
        Role.COORDINATOR, Role.PRINCIPAL,
        Role.SCHOOL_ADMIN, Role.DISTRICT_ADMIN, Role.PLATFORM_ADMIN,
    }
    if role not in coordinator_roles and not is_super_admin_role(role):
        abort(403)
    return render_template("admin/teacher_metrics.html")
