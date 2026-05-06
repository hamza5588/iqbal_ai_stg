"""
Canonical `/api/...` routes referenced by the school product spec.
Coexists with legacy paths (e.g. `/api/school/...`).
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request, session

from app.models.database_models import User as DBUser
from app.models.school_learning_models import QuizSession
from app.models.school_org_models import ClassSection
from app.rbac.roles import Role, is_super_admin_role
from app.rbac.school_permissions import can_manage_roster
from app.services.delivery_guidance_service import request_delivery_guidance
from app.services.school import quiz_service
from app.services.school.errors import SchoolServiceError
from app.services.school import school_service
from app.services.school.serializers import (
    class_section_to_dict,
    roster_entry_to_dict,
    school_subject_to_dict,
    school_to_dict,
)
from app.services.student_registration_service import register_student_with_roll_number
from app.utils.auth import login_required
from app.utils.db import get_db

logger = logging.getLogger(__name__)

api_schools_bp = Blueprint("canonical_api_schools", __name__, url_prefix="/api")
api_lesson_ext_bp = Blueprint("canonical_api_lesson_ext", __name__, url_prefix="/api/lessons")
api_teacher_bp = Blueprint("canonical_api_teacher", __name__, url_prefix="/api/teacher")
api_student_bp = Blueprint("canonical_api_student", __name__, url_prefix="/api/student")
api_quizzes_bp = Blueprint("canonical_api_quizzes", __name__, url_prefix="/api/quizzes")
api_coordinator_bp = Blueprint("canonical_api_coordinator", __name__, url_prefix="/api/coordinator")
api_principal_bp = Blueprint("canonical_api_principal", __name__, url_prefix="/api/principal")
auth_api_bp = Blueprint("canonical_auth_api", __name__, url_prefix="/api/auth")


def _uid() -> int:
    return int(session["user_id"])


def _json_error(exc: SchoolServiceError):
    return jsonify({"error": exc.message, "code": exc.code}), exc.http_status


# --- Schools / org ---
@api_schools_bp.route("/schools", methods=["POST"])
@login_required
def post_schools():
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
        return jsonify(school_to_dict(s)), 201
    except SchoolServiceError as e:
        return _json_error(e)


@api_schools_bp.route("/schools/<int:school_id>/coordinators", methods=["POST"])
@login_required
def post_school_coordinators(school_id: int):
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
        return jsonify({"error": "user_id required"}), 400


@api_schools_bp.route("/schools/<int:school_id>/subjects", methods=["POST"])
@login_required
def post_school_subjects(school_id: int):
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
        return jsonify(school_subject_to_dict(subj)), 201
    except SchoolServiceError as e:
        return _json_error(e)


@api_schools_bp.route("/schools/<int:school_id>/teachers", methods=["POST"])
@login_required
def post_school_teachers(school_id: int):
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


@api_schools_bp.route("/class-sections", methods=["POST"])
@login_required
def post_class_sections():
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
        return jsonify(class_section_to_dict(sec)), 201
    except (SchoolServiceError, KeyError, TypeError, ValueError) as e:
        if isinstance(e, SchoolServiceError):
            return _json_error(e)
        return jsonify({"error": str(e)}), 400


@api_schools_bp.route("/class-sections/<int:class_section_id>/roster", methods=["POST"])
@login_required
def post_roster_single(class_section_id: int):
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        row = school_service.add_roster_entry(
            db,
            class_section_id=class_section_id,
            first_name=str(data.get("first_name", "")),
            last_name=str(data.get("last_name", "")),
            roll_number=str(data.get("roll_number", "")),
            actor_id=_uid(),
        )
        return jsonify(roster_entry_to_dict(row)), 201
    except SchoolServiceError as e:
        return _json_error(e)


@api_schools_bp.route("/class-sections/<int:class_section_id>/roster/bulk", methods=["POST"])
@login_required
def post_roster_bulk(class_section_id: int):
    db = get_db()
    data = request.get_json(silent=True) or {}
    entries = data.get("entries") or []
    try:
        out = school_service.bulk_add_roster_entries(
            db, class_section_id=class_section_id, entries=entries, actor_id=_uid()
        )
        return jsonify(out), 201
    except SchoolServiceError as e:
        return _json_error(e)


@api_schools_bp.route("/class-sections/<int:class_section_id>/roster", methods=["GET"])
@login_required
def get_roster(class_section_id: int):
    db = get_db()
    try:
        rows = school_service.list_roster_entries_for_class_section(
            db, class_section_id=class_section_id, actor_id=_uid()
        )
        return jsonify({"items": [roster_entry_to_dict(r) for r in rows]})
    except SchoolServiceError as e:
        return _json_error(e)


# --- Auth: student registration ---
@auth_api_bp.route("/student/register-with-roll-number", methods=["POST"])
def post_register_roll():
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        out = register_student_with_roll_number(
            db,
            first_name=str(data.get("first_name", "")),
            last_name=str(data.get("last_name", "")),
            roll_number=str(data.get("roll_number", "")),
            school_id=data.get("school_id"),
            school_code=data.get("school_code"),
            email=str(data.get("email", "")),
            username=str(data.get("username", "")),
            password=str(data.get("password", "")),
            class_standard=str(data.get("class_standard", "") or ""),
            medium=str(data.get("medium", "") or ""),
            groq_api_key=str(data.get("groq_api_key", "") or ""),
        )
        return jsonify(out), 201
    except ValueError as e:
        return jsonify({"error": str(e), "code": "validation"}), 400
    except SchoolServiceError as e:
        return _json_error(e)


# --- Lessons extensions ---
@api_lesson_ext_bp.route("/<int:lesson_id>/publish-to-sections", methods=["POST"])
@login_required
def post_publish_sections(lesson_id: int):
    db = get_db()
    data = request.get_json(silent=True) or {}
    ids = data.get("class_section_ids") or []
    admin_override = bool(data.get("admin_override")) and is_super_admin_role(
        Role.from_string(
            (db.query(DBUser).filter(DBUser.id == _uid()).first().role or "student")
        )
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


@api_lesson_ext_bp.route("/<int:lesson_id>/delivery-guidance", methods=["GET"])
@login_required
def get_delivery_guidance(lesson_id: int):
    """Retrieve previously generated delivery guidance for a lesson (teacher-owned)."""
    from app.models.school_learning_models import LessonDeliveryGuidance
    import json as _json
    db = get_db()
    row = db.query(LessonDeliveryGuidance).filter(LessonDeliveryGuidance.lesson_id == lesson_id).first()
    if not row:
        return jsonify({"payload": None, "exists": False}), 200
    try:
        payload = _json.loads(row.payload_json) if row.payload_json else {}
    except (ValueError, TypeError):
        payload = {}
    return jsonify({
        "exists": True,
        "lesson_id": lesson_id,
        "payload": payload,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    })


@api_lesson_ext_bp.route("/<int:lesson_id>/delivery-guidance", methods=["POST"])
@login_required
def post_delivery_guidance(lesson_id: int):
    db = get_db()
    try:
        out = request_delivery_guidance(db, teacher_user_id=_uid(), lesson_id=lesson_id)
        return jsonify(out)
    except SchoolServiceError as e:
        return _json_error(e)


# --- Teacher ---
@api_teacher_bp.route("/class-sections", methods=["GET"])
@login_required
def get_teacher_sections():
    from app.models.school_org_models import ClassSection, School, SchoolSubject

    db = get_db()
    uid = _uid()
    rows = (
        db.query(ClassSection, School, SchoolSubject)
        .join(School, School.id == ClassSection.school_id)
        .join(SchoolSubject, SchoolSubject.id == ClassSection.subject_id)
        .filter(ClassSection.teacher_user_id == uid, ClassSection.status == "active")
        .order_by(School.name, ClassSection.grade_level, ClassSection.section)
        .all()
    )
    out = []
    for sec, school, subj in rows:
        d = class_section_to_dict(sec)
        d["school_name"] = school.name
        d["school_code"] = school.code
        d["subject_name"] = subj.name
        out.append(d)
    return jsonify({"sections": out})


# --- Quizzes ---
@api_quizzes_bp.route("", methods=["POST"])
@login_required
def post_quizzes():
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
            questions=data.get("questions"),
        )
        from app.services.school.serializers import quiz_session_to_dict

        return jsonify(quiz_session_to_dict(sess)), 201
    except (SchoolServiceError, KeyError, TypeError, ValueError) as e:
        if isinstance(e, SchoolServiceError):
            return _json_error(e)
        return jsonify({"error": str(e)}), 400


@api_quizzes_bp.route("/<int:quiz_session_id>/submit-for-student", methods=["POST"])
@login_required
def post_quiz_submit_for_student(quiz_session_id: int):
    """Screen/paper mode: teacher or coordinator records answers for an enrolled student."""
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        out = quiz_service.submit_quiz_answers_for_student_as_staff(
            db,
            session_id=quiz_session_id,
            actor_user_id=_uid(),
            student_user_id=int(data["student_user_id"]),
            answers=[int(x) for x in (data.get("answers") or [])],
        )
        return jsonify(out)
    except (SchoolServiceError, KeyError, ValueError, TypeError) as e:
        if isinstance(e, SchoolServiceError):
            return _json_error(e)
        return jsonify({"error": str(e)}), 400


@api_quizzes_bp.route("/<int:quiz_session_id>/close", methods=["POST"])
@login_required
def post_close_quiz(quiz_session_id: int):
    db = get_db()
    try:
        quiz_service.close_quiz_session(db, session_id=quiz_session_id, actor_user_id=_uid())
        return jsonify({"ok": True})
    except SchoolServiceError as e:
        return _json_error(e)


@api_quizzes_bp.route("/<int:quiz_session_id>/results", methods=["GET"])
@login_required
def get_quiz_results(quiz_session_id: int):
    db = get_db()
    uid = _uid()
    user = db.query(DBUser).filter(DBUser.id == uid).first()
    role = Role.from_string(user.role if user else "student")
    sess = db.query(QuizSession).filter(QuizSession.id == quiz_session_id).first()
    if not sess:
        return jsonify({"error": "Not found", "code": "not_found"}), 404
    sec = db.query(ClassSection).filter(ClassSection.id == sess.class_section_id).first()
    try:
        if role == Role.STUDENT:
            return jsonify(
                quiz_service.get_quiz_results_for_student(
                    db, quiz_session_id=quiz_session_id, student_user_id=uid
                )
            )
        if role == Role.TEACHER and sess.teacher_user_id == uid:
            return jsonify(
                quiz_service.get_quiz_results_for_teacher(
                    db, quiz_session_id=quiz_session_id, teacher_user_id=uid
                )
            )
        if is_super_admin_role(role):
            return jsonify(
                quiz_service.get_quiz_results_for_teacher(
                    db, quiz_session_id=quiz_session_id, teacher_user_id=sess.teacher_user_id
                )
            )
        if sec and can_manage_roster(db, uid, sec.school_id):
            return jsonify(
                quiz_service.get_quiz_results_for_teacher(
                    db, quiz_session_id=quiz_session_id, teacher_user_id=sess.teacher_user_id
                )
            )
    except SchoolServiceError as e:
        return _json_error(e)
    return jsonify({"error": "Forbidden", "code": "forbidden"}), 403


# --- Student ---
@api_student_bp.route("/lessons", methods=["GET"])
@login_required
def get_student_lessons():
    db = get_db()
    rows = quiz_service.list_student_scoped_lessons(db, student_user_id=_uid())
    return jsonify({"lessons": rows})


@api_student_bp.route("/quizzes/active", methods=["GET"])
@login_required
def get_student_active_quizzes():
    db = get_db()
    rows = quiz_service.list_active_quizzes_for_student(db, student_user_id=_uid())
    return jsonify({"items": rows})


@api_student_bp.route("/quizzes/<int:quiz_session_id>/submit", methods=["POST"])
@login_required
def post_student_quiz_submit(quiz_session_id: int):
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        out = quiz_service.submit_quiz(
            db,
            session_id=quiz_session_id,
            student_user_id=_uid(),
            answers=[int(x) for x in (data.get("answers") or [])],
        )
        return jsonify(out)
    except (SchoolServiceError, ValueError, TypeError) as e:
        if isinstance(e, SchoolServiceError):
            return _json_error(e)
        return jsonify({"error": str(e)}), 400


# --- Coordinator ---
@api_coordinator_bp.route("/classes/<int:class_section_id>/quiz-results", methods=["GET"])
@login_required
def get_coord_class_quiz(class_section_id: int):
    db = get_db()
    try:
        return jsonify(quiz_service.get_quiz_results_for_coordinator_class(db, class_section_id=class_section_id, actor_user_id=_uid()))
    except SchoolServiceError as e:
        return _json_error(e)


@api_coordinator_bp.route("/school/quiz-results", methods=["GET"])
@login_required
def get_coord_school_quiz():
    db = get_db()
    school_id = request.args.get("school_id", type=int)
    if not school_id:
        return jsonify({"error": "school_id required"}), 400
    try:
        return jsonify(quiz_service.get_quiz_results_for_coordinator_school(db, school_id=school_id, actor_user_id=_uid()))
    except SchoolServiceError as e:
        return _json_error(e)


# --- Principal ---
@api_principal_bp.route("/dashboard", methods=["GET"])
@login_required
def get_principal_dashboard():
    db = get_db()
    user = db.query(DBUser).filter(DBUser.id == _uid()).first()
    if not user or Role.from_string(user.role or "student") != Role.PRINCIPAL:
        return jsonify({"error": "Principal role required"}), 403
    return jsonify(school_service.principal_dashboard_metrics(db, principal_user_id=_uid()))