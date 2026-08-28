"""LMS API routes — Phase 1 foundation."""
from flask import Blueprint, request, session

from app.rbac.permissions import Permissions, has_permission
from app.services.lms import (
    assessment_service,
    assignment_service,
    class_service,
    curriculum_service,
    learning_path_service,
    performance_service,
    question_bank_service,
)
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError, LMSError
from app.utils.auth import login_required
from app.utils.lms_api import json_error, json_success

bp = Blueprint("lms", __name__)


def _current_user_id() -> int:
    return int(session["user_id"])


def _current_role() -> str:
    return session.get("role", "student")


def _require_permission(permission: Permissions):
    if not has_permission(_current_role(), permission):
        return json_error("Forbidden", code="forbidden", status=403)
    return None


@bp.route("/health", methods=["GET"])
def health():
    return json_success({"status": "ok", "service": "lms"})


@bp.route("/topics", methods=["GET"])
@login_required
def list_topics():
    subject = request.args.get("subject", "Math")
    grade_level = request.args.get("grade_level")
    topics = curriculum_service.list_topics(subject, grade_level=grade_level)
    data = [
        {
            "id": t.id,
            "name": t.name,
            "slug": t.slug,
            "subject": t.subject,
            "grade_level": t.grade_level,
            "parent_id": t.parent_id,
            "sort_order": t.sort_order,
        }
        for t in topics
    ]
    return json_success(data)


@bp.route("/topics/<int:topic_id>/prerequisites", methods=["GET"])
@login_required
def topic_prerequisites(topic_id: int):
    prereqs = curriculum_service.get_prerequisites(topic_id)
    return json_success([{"id": p.id, "name": p.name, "slug": p.slug} for p in prereqs])


@bp.route("/questions", methods=["GET", "POST"])
@login_required
def questions():
    if request.method == "GET":
        topic_id = request.args.get("topic_id", type=int)
        if not topic_id:
            return json_error("topic_id is required")
        qs = question_bank_service.list_questions_by_topic(topic_id)
        return json_success([question_bank_service.question_to_dict(q) for q in qs])

    denied = _require_permission(Permissions.MANAGE_QUESTION_BANK)
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    try:
        q = question_bank_service.create_question(
            created_by=_current_user_id(),
            question_text=body["question_text"],
            options=body["options"],
            correct_option_index=int(body["correct_option_index"]),
            topic_id=body.get("topic_id"),
            question_latex=body.get("question_latex"),
            correct_answer_raw=body.get("correct_answer_raw"),
            explanation=body.get("explanation"),
            difficulty=body.get("difficulty", "medium"),
            source_type=body.get("source_type", "manual"),
        )
        return json_success(question_bank_service.question_to_dict(q), status=201)
    except (KeyError, TypeError, ValueError) as e:
        return json_error(str(e), code="validation_error")
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error")


@bp.route("/questions/<int:question_id>", methods=["GET", "PUT", "DELETE"])
@login_required
def question_detail(question_id: int):
    if request.method == "GET":
        try:
            q = question_bank_service.get_question(question_id)
            return json_success(question_bank_service.question_to_dict(q))
        except LMSNotFoundError as e:
            return json_error(str(e), code="not_found", status=404)

    denied = _require_permission(Permissions.MANAGE_QUESTION_BANK)
    if denied:
        return denied

    if request.method == "DELETE":
        question_bank_service.soft_delete_question(question_id)
        return json_success({"deleted": True})

    body = request.get_json(silent=True) or {}
    q = question_bank_service.update_question(question_id, **body)
    return json_success(question_bank_service.question_to_dict(q))


@bp.route("/classes", methods=["GET", "POST"])
@login_required
def classes():
    if request.method == "GET":
        role = _current_role()
        uid = _current_user_id()
        if role == "teacher":
            classes_list = class_service.list_teacher_classes(uid)
        else:
            classes_list = class_service.list_student_classes(uid)
        return json_success(
            [
                {
                    "id": c.id,
                    "name": c.name,
                    "description": c.description,
                    "grade_level": c.grade_level,
                    "join_code": c.join_code if role == "teacher" else None,
                }
                for c in classes_list
            ]
        )

    denied = _require_permission(Permissions.MANAGE_CLASS)
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    c = class_service.create_class(
        teacher_id=_current_user_id(),
        name=body["name"],
        description=body.get("description"),
        grade_level=body.get("grade_level"),
    )
    return json_success(
        {"id": c.id, "name": c.name, "join_code": c.join_code}, status=201
    )


@bp.route("/classes/join", methods=["POST"])
@login_required
def join_class():
    if _current_role() != "student":
        return json_error("Only students can join classes", code="forbidden", status=403)
    body = request.get_json(silent=True) or {}
    join_code = body.get("join_code", "").strip()
    if not join_code:
        return json_error("join_code is required")
    try:
        enr = class_service.enroll_student(join_code, _current_user_id())
        return json_success({"class_id": enr.class_id, "status": enr.status})
    except LMSNotFoundError as e:
        return json_error(str(e), code="not_found", status=404)


@bp.route("/classes/<int:class_id>/students", methods=["GET"])
@login_required
def class_students(class_id: int):
    denied = _require_permission(Permissions.VIEW_CLASS_PERFORMANCE)
    if denied:
        return denied
    if not class_service.teacher_owns_class(_current_user_id(), class_id):
        return json_error("Forbidden", code="forbidden", status=403)
    enrollments = class_service.list_class_students(class_id)
    return json_success([{"student_id": e.student_id, "enrolled_at": e.enrolled_at.isoformat()} for e in enrollments])


@bp.route("/quizzes", methods=["GET", "POST"])
@login_required
def quizzes():
    if request.method == "GET":
        qs = assessment_service.list_assessments_by_teacher(
            _current_user_id(), assessment_type="quiz"
        )
        return json_success([{"id": q.id, "title": q.title, "status": q.status} for q in qs])

    denied = _require_permission(Permissions.CREATE_QUIZ)
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    a = assessment_service.create_assessment(
        created_by=_current_user_id(),
        title=body["title"],
        assessment_type=body.get("assessment_type", "quiz"),
        description=body.get("description"),
        creation_mode=body.get("creation_mode", "manual"),
        time_limit_minutes=body.get("time_limit_minutes"),
    )
    return json_success({"id": a.id, "title": a.title, "status": a.status}, status=201)


@bp.route("/quizzes/<int:quiz_id>", methods=["GET"])
@login_required
def get_quiz(quiz_id: int):
    try:
        return json_success(assessment_service.get_assessment_with_questions(quiz_id))
    except LMSNotFoundError as e:
        return json_error(str(e), code="not_found", status=404)


@bp.route("/quizzes/<int:quiz_id>/questions", methods=["PUT"])
@login_required
def update_quiz_questions(quiz_id: int):
    denied = _require_permission(Permissions.CREATE_QUIZ)
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    question_ids = body.get("question_ids") or []
    assessment_service.add_questions(quiz_id, question_ids)
    if body.get("order"):
        assessment_service.reorder_questions(quiz_id, body["order"])
    return json_success(assessment_service.get_assessment_with_questions(quiz_id))


@bp.route("/quizzes/<int:quiz_id>/publish", methods=["POST"])
@login_required
def publish_quiz(quiz_id: int):
    denied = _require_permission(Permissions.CREATE_QUIZ)
    if denied:
        return denied
    try:
        a = assessment_service.publish_assessment(quiz_id)
        return json_success({"id": a.id, "status": a.status})
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error")


@bp.route("/assignments", methods=["GET", "POST"])
@login_required
def assignments():
    if request.method == "GET":
        if _current_role() == "student":
            return json_success(assignment_service.list_assignments_for_student(_current_user_id()))
        denied = _require_permission(Permissions.ASSIGN_QUIZ)
        if denied:
            return denied
        class_id = request.args.get("class_id", type=int)
        if not class_id:
            return json_error("class_id is required for teachers")
        rows = assignment_service.list_assignments_for_class(class_id)
        return json_success([{"id": a.id, "title": a.title, "quiz_id": a.quiz_id} for a in rows])

    denied = _require_permission(Permissions.ASSIGN_QUIZ)
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    due_date = body.get("due_date")
    from datetime import datetime

    parsed_due = datetime.fromisoformat(due_date) if due_date else None
    a = assignment_service.create_assignment(
        teacher_id=_current_user_id(),
        class_id=int(body["class_id"]),
        quiz_id=int(body["quiz_id"]),
        title=body["title"],
        instructions=body.get("instructions"),
        due_date=parsed_due,
    )
    return json_success({"id": a.id, "status": a.status}, status=201)


@bp.route("/assignments/<int:assignment_id>/publish", methods=["POST"])
@login_required
def publish_assignment(assignment_id: int):
    denied = _require_permission(Permissions.ASSIGN_QUIZ)
    if denied:
        return denied
    try:
        a = assignment_service.publish_assignment(assignment_id, _current_user_id())
        return json_success({"id": a.id, "status": a.status})
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error")


@bp.route("/students/me/progress", methods=["GET"])
@login_required
def my_progress():
    uid = _current_user_id()
    mastery = performance_service.get_student_mastery(uid)
    overall = performance_service.get_overall_progress(uid)
    path = learning_path_service.get_path_with_items(uid)
    return json_success({"overall_progress": overall, "topics": mastery, "learning_path": path})


@bp.errorhandler(LMSError)
def handle_lms_error(err):
    if isinstance(err, LMSNotFoundError):
        return json_error(str(err), code="not_found", status=404)
    if isinstance(err, LMSValidationError):
        return json_error(str(err), code="validation_error", status=400)
    return json_error(str(err), code="lms_error", status=500)
