"""
School organization UI + JSON API.

HTML: /school/hub
API:  /api/school/...
"""
from __future__ import annotations

import logging
from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from app.models.database_models import User as DBUser
from app.models.school_org_models import ClassSection, School, SchoolSubject
from app.services.school.access import (
    can_manage_school,
    user_enrolled_class_section_ids,
    user_manageable_school_ids,
    user_teaching_class_section_ids,
)
from app.services.school.errors import SchoolServiceError
from app.services.school import school_service
from app.services.school import quiz_service
from app.rbac.roles import Role, is_super_admin_role
from app.utils.auth import login_required
from app.utils.db import get_db

logger = logging.getLogger(__name__)

school_ui_bp = Blueprint("school_ui", __name__, url_prefix="/school")
school_api_bp = Blueprint("school_api", __name__, url_prefix="/api/school")


def _uid() -> int:
    return int(session["user_id"])


def _json_error(exc: SchoolServiceError):
    return jsonify({"error": exc.message, "code": exc.code}), exc.http_status


@school_ui_bp.route("/coordinator-dashboard")
@login_required
def coordinator_dashboard():
    """Dedicated coordinator dashboard for roster and class section management."""
    from flask import abort
    db = get_db()
    user = db.query(DBUser).filter(DBUser.id == int(session["user_id"])).first()
    role = Role.from_string((user.role if user else "student") or "student")
    allowed = {Role.COORDINATOR, Role.SCHOOL_ADMIN, Role.DISTRICT_ADMIN, Role.PLATFORM_ADMIN}
    if role not in allowed and not is_super_admin_role(role):
        abort(403)
    return render_template("coordinator_dashboard.html")


@school_ui_bp.route("/principal-dashboard")
@login_required
def principal_dashboard():
    """Principal dashboard showing school-level aggregate metrics."""
    from flask import abort
    db = get_db()
    user = db.query(DBUser).filter(DBUser.id == int(session["user_id"])).first()
    role = Role.from_string((user.role if user else "student") or "student")
    allowed = {Role.PRINCIPAL, Role.SCHOOL_ADMIN, Role.DISTRICT_ADMIN, Role.PLATFORM_ADMIN}
    if role not in allowed and not is_super_admin_role(role):
        abort(403)
    return render_template("principal_dashboard.html")


@school_ui_bp.route("/notifications")
@login_required
def notifications_page():
    """Notification center — all roles."""
    return render_template("notifications.html")


@school_ui_bp.route("/student/settings")
@login_required
def student_settings():
    """Student personal settings: personality, language, terms, data export."""
    return render_template("student_settings.html")


@school_ui_bp.route("/parent/portal")
@login_required
def parent_portal():
    """Parent portal: child linking and monitoring."""
    db = get_db()
    user = db.query(DBUser).filter(DBUser.id == int(session["user_id"])).first()
    role = Role.from_string((user.role if user else "student") or "student")
    if role not in (Role.PARENT,) and not is_super_admin_role(role):
        from flask import abort
        abort(403)
    return render_template("parent_portal.html")


@school_ui_bp.route("/hub")
@login_required
def school_hub():
    """
    Role-specific lightweight hub. Full create-school + API smoke tools live at
    /admin/school-operations (admin-family roles only).
    """
    db = get_db()
    user = db.query(DBUser).filter(DBUser.id == int(session["user_id"])).first()
    role = Role.from_string((user.role if user else "student") or "student")
    if is_super_admin_role(role):
        return redirect(url_for("admin.school_operations"))
    if role == Role.TEACHER:
        hub_mode = "teacher"
    elif role in (Role.STUDENT, Role.PARENT):
        hub_mode = "student"
    else:
        hub_mode = "coordinator"
    return render_template("school_hub.html", hub_mode=hub_mode)


# ---------------------------------------------------------------------------
# API: context
# ---------------------------------------------------------------------------


@school_api_bp.route("/my-teaching-sections", methods=["GET"])
@login_required
def api_my_teaching_sections():
    """Class sections where the current user is the assigned teacher (for publish picker)."""
    db = get_db()
    uid = _uid()
    ids = user_teaching_class_section_ids(db, uid)
    if not ids:
        return jsonify({"sections": []})
    rows = (
        db.query(ClassSection, School, SchoolSubject)
        .join(School, School.id == ClassSection.school_id)
        .join(SchoolSubject, SchoolSubject.id == ClassSection.subject_id)
        .filter(ClassSection.id.in_(ids), ClassSection.status == "active")
        .order_by(School.name, ClassSection.grade_level, ClassSection.section)
        .all()
    )
    out = []
    for sec, school, subj in rows:
        out.append(
            {
                "id": sec.id,
                "school_id": school.id,
                "school_code": school.code,
                "school_name": school.name,
                "grade_level": sec.grade_level,
                "section": sec.section,
                "academic_year": sec.academic_year,
                "display_name": sec.display_name,
                "subject_name": subj.name,
                "subject_code": subj.short_code,
            }
        )
    return jsonify({"sections": out})


@school_api_bp.route("/context", methods=["GET"])
@login_required
def api_context():
    db = get_db()
    uid = _uid()
    user = db.query(DBUser).filter(DBUser.id == uid).first()
    acc = user_manageable_school_ids(db, uid)
    teaching = user_teaching_class_section_ids(db, uid)
    enrolled = user_enrolled_class_section_ids(db, uid)
    return jsonify(
        {
            "user": {"id": uid, "role": user.role if user else "student"},
            "managed_school_ids": list(acc.school_ids),
            "super_admin": acc.super_admin,
            "teaching_class_section_ids": teaching,
            "enrolled_class_section_ids": enrolled,
        }
    )


@school_api_bp.route("/schools/<int:school_id>/summary", methods=["GET"])
@login_required
def api_school_summary(school_id: int):
    db = get_db()
    uid = _uid()
    if not can_manage_school(db, uid, school_id):
        tids = user_teaching_class_section_ids(db, uid)
        if tids:
            ok = (
                db.query(ClassSection.id)
                .filter(ClassSection.id.in_(tids), ClassSection.school_id == school_id)
                .first()
            )
        else:
            ok = None
        if not ok:
            return jsonify({"error": "Forbidden", "code": "forbidden"}), 403
    try:
        s = school_service.school_summary(db, school_id)
        return jsonify(s)
    except SchoolServiceError as e:
        return _json_error(e)


@school_api_bp.route("/schools", methods=["GET"])
@login_required
def api_list_schools():
    db = get_db()
    uid = _uid()
    acc = user_manageable_school_ids(db, uid)
    q = db.query(School)
    if not acc.super_admin:
        if not acc.school_ids:
            ids = set()
        else:
            ids = set(acc.school_ids)
        teaching_section_ids = user_teaching_class_section_ids(db, uid)
        if teaching_section_ids:
            extra_schools = (
                db.query(ClassSection.school_id)
                .filter(ClassSection.id.in_(teaching_section_ids))
                .distinct()
                .all()
            )
            ids |= {r[0] for r in extra_schools}
        if not ids:
            return jsonify({"schools": []})
        q = q.filter(School.id.in_(ids))
    schools = q.order_by(School.name).all()
    out = []
    for s in schools:
        try:
            summary = school_service.school_summary(db, s.id)
        except SchoolServiceError:
            summary = {"id": s.id, "name": s.name, "code": s.code}
        out.append(summary)
    return jsonify({"schools": out})


@school_api_bp.route("/districts", methods=["POST"])
@login_required
def api_create_district():
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        d = school_service.create_district(
            db,
            name=str(data.get("name", "")),
            code=data.get("code"),
            actor_id=_uid(),
        )
        return jsonify({"id": d.id, "name": d.name, "code": d.code}), 201
    except SchoolServiceError as e:
        return _json_error(e)


@school_api_bp.route("/schools", methods=["POST"])
@login_required
def api_create_school():
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        s = school_service.create_school(
            db,
            name=str(data.get("name", "")),
            code=str(data.get("code", "")),
            district_id=data.get("district_id"),
            principal_user_id=data.get("principal_user_id"),
            actor_id=_uid(),
        )
        return jsonify({"id": s.id, "name": s.name, "code": s.code}), 201
    except SchoolServiceError as e:
        return _json_error(e)


@school_api_bp.route("/schools/<int:school_id>/principal", methods=["PATCH"])
@login_required
def api_set_principal(school_id: int):
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        s = school_service.set_principal(
            db,
            school_id=school_id,
            principal_user_id=data.get("principal_user_id"),
            actor_id=_uid(),
        )
        return jsonify({"id": s.id, "principal_user_id": s.principal_user_id})
    except SchoolServiceError as e:
        return _json_error(e)


@school_api_bp.route("/schools/<int:school_id>/coordinators", methods=["POST"])
@login_required
def api_add_coordinator(school_id: int):
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        school_service.add_coordinator(
            db,
            school_id=school_id,
            coordinator_user_id=int(data["user_id"]),
            actor_id=_uid(),
        )
        return jsonify({"ok": True}), 201
    except (SchoolServiceError, KeyError, TypeError, ValueError) as e:
        if isinstance(e, SchoolServiceError):
            return _json_error(e)
        return jsonify({"error": "user_id required", "code": "bad_request"}), 400


@school_api_bp.route("/schools/<int:school_id>/coordinators/<int:user_id>", methods=["DELETE"])
@login_required
def api_remove_coordinator(school_id: int, user_id: int):
    db = get_db()
    try:
        school_service.remove_coordinator(db, school_id=school_id, coordinator_user_id=user_id, actor_id=_uid())
        return jsonify({"ok": True})
    except SchoolServiceError as e:
        return _json_error(e)


@school_api_bp.route("/schools/<int:school_id>/subjects", methods=["POST"])
@login_required
def api_add_subject(school_id: int):
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        subj = school_service.upsert_subject(
            db,
            school_id=school_id,
            name=str(data.get("name", "")),
            short_code=str(data.get("short_code", "")),
            grade_band=data.get("grade_band"),
            actor_id=_uid(),
        )
        return jsonify({"id": subj.id, "name": subj.name, "short_code": subj.short_code}), 201
    except SchoolServiceError as e:
        return _json_error(e)


@school_api_bp.route("/schools/<int:school_id>/teachers", methods=["POST"])
@login_required
def api_add_teacher(school_id: int):
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        school_service.add_teacher_affiliation(
            db,
            school_id=school_id,
            teacher_user_id=int(data["teacher_user_id"]),
            actor_id=_uid(),
        )
        return jsonify({"ok": True}), 201
    except (SchoolServiceError, KeyError, TypeError, ValueError) as e:
        if isinstance(e, SchoolServiceError):
            return _json_error(e)
        return jsonify({"error": "teacher_user_id required"}), 400


@school_api_bp.route("/class-sections", methods=["POST"])
@login_required
def api_create_class_section():
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        sec = school_service.create_class_section(
            db,
            school_id=int(data["school_id"]),
            subject_id=int(data["subject_id"]),
            teacher_user_id=int(data["teacher_user_id"]),
            grade_level=str(data.get("grade_level", "")),
            section=str(data.get("section", "")),
            academic_year=str(data.get("academic_year", "")),
            display_name=data.get("display_name"),
            actor_id=_uid(),
        )
        return (
            jsonify(
                {
                    "id": sec.id,
                    "school_id": sec.school_id,
                    "subject_id": sec.subject_id,
                    "teacher_user_id": sec.teacher_user_id,
                    "grade_level": sec.grade_level,
                    "section": sec.section,
                    "academic_year": sec.academic_year,
                }
            ),
            201,
        )
    except (SchoolServiceError, KeyError, TypeError, ValueError) as e:
        if isinstance(e, SchoolServiceError):
            return _json_error(e)
        return jsonify({"error": str(e)}), 400


@school_api_bp.route("/class-sections/<int:section_id>/roster", methods=["POST"])
@login_required
def api_import_roster(section_id: int):
    db = get_db()
    raw = request.get_data(as_text=True) or ""
    if not raw.strip():
        body = request.get_json(silent=True) or {}
        raw = str(body.get("csv", ""))
    try:
        result = school_service.import_roster_csv(db, class_section_id=section_id, csv_text=raw, actor_id=_uid())
        return jsonify(result), 201
    except SchoolServiceError as e:
        return _json_error(e)


@school_api_bp.route("/class-sections/<int:section_id>/detail", methods=["GET"])
@login_required
def api_class_section_detail(section_id: int):
    db = get_db()
    sec = db.query(ClassSection).filter(ClassSection.id == section_id).first()
    if not sec:
        return jsonify({"error": "Not found"}), 404
    subj = db.query(SchoolSubject).filter(SchoolSubject.id == sec.subject_id).first()
    return jsonify(
        {
            "id": sec.id,
            "school_id": sec.school_id,
            "subject": {"id": subj.id, "name": subj.name, "short_code": subj.short_code} if subj else None,
            "teacher_user_id": sec.teacher_user_id,
            "grade_level": sec.grade_level,
            "section": sec.section,
            "academic_year": sec.academic_year,
            "display_name": sec.display_name,
        }
    )


@school_api_bp.route("/student/link-roster", methods=["POST"])
@login_required
def api_student_link_roster():
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        out = school_service.link_student_to_roster(
            db,
            student_user_id=_uid(),
            school_code=str(data.get("school_code", "")),
            class_section_id=int(data["class_section_id"]),
            roll_number=str(data.get("roll_number", "")),
            first_name=str(data.get("first_name", "")),
            last_name=str(data.get("last_name", "")),
        )
        return jsonify(out)
    except (SchoolServiceError, KeyError, TypeError, ValueError) as e:
        if isinstance(e, SchoolServiceError):
            return _json_error(e)
        return jsonify({"error": str(e)}), 400


@school_api_bp.route("/lessons/<int:lesson_id>/publish", methods=["POST"])
@login_required
def api_publish_lesson(lesson_id: int):
    db = get_db()
    data = request.get_json(silent=True) or {}
    ids = data.get("class_section_ids") or []
    from app.models.database_models import User as DBUser
    from app.rbac.roles import Role, is_super_admin_role

    u = db.query(DBUser).filter(DBUser.id == _uid()).first()
    admin_override = bool(data.get("admin_override")) and is_super_admin_role(
        Role.from_string(u.role if u else "student")
    )
    try:
        n = quiz_service.publish_lesson_to_sections(
            db,
            lesson_id=lesson_id,
            teacher_user_id=_uid(),
            class_section_ids=ids,
            admin_override=admin_override,
        )
        return jsonify({"linked_sections": n})
    except SchoolServiceError as e:
        return _json_error(e)


@school_api_bp.route("/quiz-sessions", methods=["POST"])
@login_required
def api_create_quiz():
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        sess = quiz_service.create_quiz_session(
            db,
            lesson_id=int(data["lesson_id"]),
            class_section_id=int(data["class_section_id"]),
            teacher_user_id=_uid(),
            delivery_mode=str(data.get("delivery_mode", "device")),
            num_questions=int(data.get("num_questions", 5)),
        )
        return jsonify({"id": sess.id, "lesson_id": sess.lesson_id, "class_section_id": sess.class_section_id}), 201
    except (SchoolServiceError, KeyError, TypeError, ValueError) as e:
        if isinstance(e, SchoolServiceError):
            return _json_error(e)
        return jsonify({"error": str(e)}), 400


@school_api_bp.route("/quiz-sessions/active", methods=["GET"])
@login_required
def api_active_quizzes():
    db = get_db()
    rows = quiz_service.list_active_quizzes_for_student(db, student_user_id=_uid())
    return jsonify({"items": rows})


@school_api_bp.route("/quiz-sessions/<int:session_id>", methods=["GET"])
@login_required
def api_get_quiz(session_id: int):
    db = get_db()
    try:
        payload = quiz_service.get_quiz_session_for_student(
            db, session_id=session_id, student_user_id=_uid()
        )
        return jsonify(payload)
    except SchoolServiceError as e:
        return _json_error(e)


@school_api_bp.route("/quiz-sessions/<int:session_id>/submit", methods=["POST"])
@login_required
def api_submit_quiz(session_id: int):
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        out = quiz_service.submit_quiz(
            db,
            session_id=session_id,
            student_user_id=_uid(),
            answers=[int(x) for x in (data.get("answers") or [])],
        )
        return jsonify(out)
    except (SchoolServiceError, ValueError, TypeError) as e:
        if isinstance(e, SchoolServiceError):
            return _json_error(e)
        return jsonify({"error": str(e)}), 400


@school_api_bp.route("/quiz-sessions/<int:session_id>/close", methods=["POST"])
@login_required
def api_close_quiz(session_id: int):
    db = get_db()
    try:
        quiz_service.close_quiz_session(db, session_id=session_id, actor_user_id=_uid())
        return jsonify({"ok": True})
    except SchoolServiceError as e:
        return _json_error(e)


@school_api_bp.route("/student/scoped-lessons", methods=["GET"])
@login_required
def api_scoped_lessons():
    db = get_db()
    rows = quiz_service.list_student_scoped_lessons(db, student_user_id=_uid())
    return jsonify({"lessons": rows})


@school_api_bp.route("/ai/roster-column-hints", methods=["POST"])
@login_required
def api_roster_ai_hints():
    """
    Optional: suggest CSV column mapping from a header line using the configured LLM.
    """
    db = get_db()
    data = request.get_json(silent=True) or {}
    header_line = str(data.get("header_line", "")).strip()
    if not header_line:
        return jsonify({"error": "header_line required"}), 400
    acc = user_manageable_school_ids(db, _uid())
    if not acc.super_admin and not acc.school_ids:
        return jsonify({"error": "Forbidden"}), 403
    try:
        from langchain_core.messages import HumanMessage

        from app.utils.llm_factory import get_chat_model

        model = get_chat_model(None, temperature=0)
        msg = (
            "Given this CSV header line, reply ONLY compact JSON: "
            '{"roll_column": <0-based index or null>, "first_name_column": <int or null>, '
            '"last_name_column": <int or null>, "delimiter": "," or ";" or "\\t"}. '
            f"Header: {header_line[:500]}"
        )
        raw = model.invoke([HumanMessage(content=msg)])
        text = raw.content if hasattr(raw, "content") else str(raw)
        return jsonify({"raw": text})
    except Exception as e:
        logger.exception("roster AI hints failed")
        return jsonify({"error": "ai_unavailable", "detail": str(e)}), 503



# ==================== WAITLIST ENDPOINTS (Phase 1) ====================

@school_api_bp.route("/class-sections/<int:section_id>/enroll", methods=["POST"])
@login_required
def api_enroll_or_waitlist(section_id):
    """Enroll a student or add to waitlist if section is full."""
    from app.services.waitlist_service import enroll_or_waitlist
    from app.services.school.errors import SchoolServiceError
    db = get_db()
    data = request.get_json(silent=True) or {}
    student_id = data.get("student_user_id", _uid())
    try:
        result = enroll_or_waitlist(db, student_id=student_id, class_section_id=section_id)
        db.commit()
        return jsonify(result)
    except SchoolServiceError as exc:
        return jsonify({"error": exc.message, "code": exc.code}), exc.http_status


@school_api_bp.route("/class-sections/<int:section_id>/waitlist", methods=["GET"])
@login_required
def api_get_waitlist(section_id):
    """Get the waitlist for a class section."""
    from app.services.waitlist_service import get_waitlist
    db = get_db()
    entries = get_waitlist(db, class_section_id=section_id)
    return jsonify([
        {
            "id": e.id,
            "student_user_id": e.student_user_id,
            "position": e.position,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in entries
    ])


@school_api_bp.route("/class-sections/<int:section_id>/waitlist/<int:student_id>", methods=["DELETE"])
@login_required
def api_remove_from_waitlist(section_id, student_id):
    """Remove a student from the waitlist."""
    from app.services.waitlist_service import remove_from_waitlist
    from app.services.school.errors import SchoolServiceError
    db = get_db()
    try:
        remove_from_waitlist(db, student_id=student_id, class_section_id=section_id)
        db.commit()
        return jsonify({"ok": True})
    except SchoolServiceError as exc:
        return jsonify({"error": exc.message, "code": exc.code}), exc.http_status
