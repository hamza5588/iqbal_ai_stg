"""LMS API routes — Phases 1–10."""
from flask import Blueprint, Response, request, session, current_app

from app.rbac.permissions import Permissions, has_permission
from app.services.lms import (
    analytics_service,
    assessment_service,
    assignment_service,
    attempt_service,
    class_service,
    curriculum_service,
    intervention_service,
    learning_path_service,
    path_generator,
    performance_service,
    practice_service,
    question_bank_service,
    student_profile_service,
    tutor_service,
)
from app.services.lms import deficiency_chat_service
from app.services.lms.diagnostic_pdf_service import (
    generate_diagnostic_questions,
    get_diagnostic_status,
    list_diagnostic_target_pdfs,
    list_pdf_topics,
    list_target_pdf_topics,
    remove_target_pdf_entry,
    upload_diagnostic_bundle,
    upload_diagnostic_pdf,
    upload_target_pdf,
)
from app.services.lms.diagnostic_service import get_student_diagnostic_dict, list_admin_diagnostics
from app.services.lms.exceptions import LMSNotFoundError, LMSValidationError, LMSError
from app.services.quiz.pipeline import run_pdf_quiz_pipeline
from app.tasks.quiz_pdf_tasks import enqueue_or_run_pdf_quiz
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


def _is_admin() -> bool:
    return _current_role() == "admin"


def _require_diagnostic_admin():
    return _require_permission(Permissions.CREATE_DIAGNOSTIC)


def _require_assessment_owner(assessment_id: int):
    """Same ownership rule publish_quiz already used: the assessment's
    creator (or an admin) may edit it; the platform diagnostic requires
    admin regardless of who created it. Returns (error_response_or_None,
    assessment_or_None).

    Without this, MANAGE_QUESTION_BANK / CREATE_QUIZ (granted to every
    teacher) was the only gate on the quiz-questions and question-bank
    write routes - any teacher could edit, reorder, or delete questions
    on ANY other teacher's quiz, or on the platform diagnostic itself.
    """
    try:
        assessment = assessment_service.get_assessment(assessment_id)
    except LMSNotFoundError as e:
        return json_error(str(e), code="not_found", status=404), None
    if assessment.assessment_type == "diagnostic":
        denied = _require_diagnostic_admin()
        if denied:
            return denied, None
    elif assessment.created_by != _current_user_id() and _current_role() != "admin":
        return json_error("Forbidden", code="forbidden", status=403), None
    return None, assessment


def _require_question_owner(question_id: int):
    """The question's creator (or an admin) may edit/delete it directly.
    A question with no recorded creator (legacy data) is admin-only -
    default-deny, not default-allow, for ownerless content."""
    try:
        q_row = question_bank_service.get_question(question_id)
    except LMSNotFoundError as e:
        return json_error(str(e), code="not_found", status=404), None
    if q_row.created_by != _current_user_id() and _current_role() != "admin":
        return json_error("Forbidden", code="forbidden", status=403), None
    return None, q_row


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
        difficulty = request.args.get("difficulty")
        if not topic_id:
            return json_error("topic_id is required")
        qs = question_bank_service.list_questions_by_topic(topic_id, difficulty=difficulty)
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
    denied, _owned = _require_question_owner(question_id)
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
    try:
        c = class_service.create_class(
            teacher_id=_current_user_id(),
            name=body["name"],
            description=body.get("description"),
            grade_level=body.get("grade_level"),
        )
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error", status=400)
    return json_success(
        {"id": c.id, "name": c.name, "join_code": c.join_code, "grade_level": c.grade_level},
        status=201,
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
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error", status=400)
    except LMSNotFoundError as e:
        return json_error(str(e), code="not_found", status=404)


@bp.route("/classes/grade-options", methods=["GET"])
@login_required
def grade_options():
    return json_success(class_service.get_grade_options())


@bp.route("/users/me/grade-profile", methods=["GET"])
@login_required
def my_grade_profile():
    from app.services.lms.grade_utils import format_grade_label

    uid = _current_user_id()
    role = _current_role()
    if role == "student":
        g = class_service.get_student_grade(uid)
        return json_success({"role": role, "grade_level": g, "grade_label": format_grade_label(g)})
    if role in ("teacher", "admin"):
        grades = class_service.get_teacher_grades(uid)
        return json_success({
            "role": role,
            "teaching_grades": grades,
            "teaching_grade_labels": [format_grade_label(g) for g in grades],
        })
    return json_success({"role": role})


@bp.route("/teachers/me/grades", methods=["PUT"])
@login_required
def set_my_teaching_grades():
    if _current_role() not in ("teacher", "admin"):
        return json_error("Teachers only", code="forbidden", status=403)
    body = request.get_json(silent=True) or {}
    grades = body.get("grades") or body.get("grade_levels") or []
    if isinstance(grades, str):
        grades = [g.strip() for g in grades.split(",") if g.strip()]
    try:
        user = class_service.set_teacher_grades(_current_user_id(), grades)
        from app.services.lms.grade_utils import format_grade_label
        assigned = class_service.get_teacher_grades(user.id)
        return json_success({
            "teaching_grades": assigned,
            "teaching_grade_labels": [format_grade_label(g) for g in assigned],
        })
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error", status=400)


@bp.route("/admin/users/<int:user_id>/grade", methods=["PUT"])
@login_required
def admin_set_user_grade(user_id: int):
    if _current_role() != "admin":
        return json_error("Admin only", code="forbidden", status=403)
    body = request.get_json(silent=True) or {}
    grade = body.get("grade_level") or body.get("grade")
    role = body.get("role")
    try:
        if role == "teacher" or body.get("teaching_grades"):
            grades = body.get("teaching_grades") or [grade]
            user = class_service.set_teacher_grades(user_id, grades)
            return json_success({"user_id": user.id, "class_standard": user.class_standard})
        user = class_service.set_user_grade(user_id, grade, role="student")
        return json_success({
            "user_id": user.id,
            "grade_level": class_service.get_student_grade(user.id),
        })
    except (LMSValidationError, LMSNotFoundError) as e:
        return json_error(str(e), code="validation_error", status=400)


@bp.route("/classes/<int:class_id>/eligible-students", methods=["GET"])
@login_required
def eligible_students(class_id: int):
    denied = _require_permission(Permissions.MANAGE_CLASS)
    if denied:
        return denied
    try:
        return json_success(class_service.list_eligible_students(class_id, _current_user_id()))
    except LMSValidationError as e:
        return json_error(str(e), code="forbidden", status=403)


@bp.route("/classes/<int:class_id>/students", methods=["GET", "POST"])
@login_required
def class_students_manage(class_id: int):
    denied = _require_permission(Permissions.VIEW_CLASS_PERFORMANCE)
    if denied:
        return denied
    if not class_service.teacher_owns_class(_current_user_id(), class_id):
        return json_error("Forbidden", code="forbidden", status=403)

    if request.method == "GET":
        detailed = request.args.get("detailed", "true").lower() != "false"
        if detailed:
            return json_success(class_service.list_class_students_detailed(class_id, _current_user_id()))
        enrollments = class_service.list_class_students(class_id)
        return json_success([
            {"student_id": e.student_id, "enrolled_at": e.enrolled_at.isoformat()}
            for e in enrollments
        ])

    denied = _require_permission(Permissions.MANAGE_CLASS)
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    student_id = body.get("student_id")
    if not student_id:
        return json_error("student_id is required", code="validation_error")
    try:
        enr = class_service.teacher_add_student(class_id, int(student_id), _current_user_id())
        return json_success({"class_id": enr.class_id, "student_id": enr.student_id, "status": enr.status}, status=201)
    except (LMSValidationError, LMSNotFoundError) as e:
        return json_error(str(e), code="validation_error", status=400)


@bp.route("/classes/<int:class_id>/students/<int:student_id>", methods=["DELETE"])
@login_required
def remove_class_student(class_id: int, student_id: int):
    denied = _require_permission(Permissions.MANAGE_CLASS)
    if denied:
        return denied
    try:
        class_service.remove_student_from_class(class_id, student_id, _current_user_id())
        return json_success({"removed": True})
    except (LMSValidationError, LMSNotFoundError) as e:
        return json_error(str(e), code="validation_error", status=400)


def _validate_thread_id(thread_id: str, user_id: int) -> bool:
    return bool(thread_id) and thread_id.startswith(f"user_{user_id}_")


@bp.route("/quizzes/from-pdf", methods=["POST"])
@login_required
def create_quiz_from_pdf():
    """Upload Q&A PDF and run PDF→MCQ pipeline (async when Celery available)."""
    denied = _require_permission(Permissions.CREATE_QUIZ)
    if denied:
        return denied

    title = (request.form.get("title") or "Untitled Quiz").strip()
    assessment_type = "quiz"
    topic_id = request.form.get("topic_id", type=int)
    async_mode = request.form.get("async", "true").lower() != "false"
    if not current_app.config.get("USE_CELERY_FOR_INGESTION", False):
        async_mode = False

    file = request.files.get("file")
    if not file or not file.filename:
        return json_error("PDF file is required", code="validation_error")

    a = assessment_service.create_assessment(
        created_by=_current_user_id(),
        title=title.strip(),
        assessment_type="quiz",
        creation_mode="pdf_qa_auto",
    )

    file_bytes = file.read()
    if not file_bytes:
        return json_error("Empty file", code="validation_error")

    try:
        result = enqueue_or_run_pdf_quiz(
            assessment_id=a.id,
            file_bytes=file_bytes,
            filename=file.filename,
            user_id=_current_user_id(),
            topic_id=topic_id,
            async_mode=async_mode,
        )
    except ValueError as e:
        return json_error(str(e), code="llm_config_error", status=503)
    except Exception as e:
        return json_error(str(e), code="pipeline_error", status=500)

    status = 202 if result.get("async") else 200
    return json_success({"assessment_id": a.id, **result}, status=status)


@bp.route("/quizzes/<int:quiz_id>/process-pdf", methods=["POST"])
@login_required
def process_quiz_pdf(quiz_id: int):
    """Re-run pipeline on an existing assessment using an ingested RAG thread."""
    denied = _require_permission(Permissions.CREATE_QUIZ)
    if denied:
        return denied

    body = request.get_json(silent=True) or {}
    thread_id = body.get("thread_id") or request.form.get("thread_id")
    topic_id = body.get("topic_id")
    if not thread_id or not _validate_thread_id(thread_id, _current_user_id()):
        return json_error("Valid thread_id is required", code="validation_error")

    try:
        result = run_pdf_quiz_pipeline(
            assessment_id=quiz_id,
            rag_thread_id=thread_id,
            user_id=_current_user_id(),
            topic_id=topic_id,
        )
        return json_success(result)
    except Exception as e:
        return json_error(str(e), code="pipeline_error", status=500)


@bp.route("/quizzes/<int:quiz_id>/pdf-status", methods=["GET"])
@login_required
def quiz_pdf_status(quiz_id: int):
    try:
        return json_success(assessment_service.get_pdf_processing_status(quiz_id))
    except LMSNotFoundError as e:
        return json_error(str(e), code="not_found", status=404)


@bp.route("/quizzes/<int:quiz_id>/preview", methods=["GET"])
@login_required
def quiz_preview(quiz_id: int):
    """Teacher preview with full MCQ details including correct answers."""
    denied = _require_permission(Permissions.CREATE_QUIZ)
    if denied:
        return denied
    try:
        data = assessment_service.get_assessment_with_questions(quiz_id, include_answers=True)
        assessment = assessment_service.get_assessment(quiz_id)
        if assessment.created_by != _current_user_id():
            return json_error("Forbidden", code="forbidden", status=403)
        return json_success(data)
    except LMSNotFoundError as e:
        return json_error(str(e), code="not_found", status=404)


@bp.route("/quizzes/from-pdf/<int:source_id>/finalize", methods=["POST"])
@login_required
def finalize_pdf_quiz(source_id: int):
    denied = _require_permission(Permissions.CREATE_QUIZ)
    if denied:
        return denied
    try:
        a = assessment_service.finalize_pdf_quiz(source_id, _current_user_id())
        return json_success({"id": a.id, "status": a.status, "creation_mode": a.creation_mode})
    except (LMSNotFoundError, LMSValidationError) as e:
        return json_error(str(e), code="validation_error")


@bp.route("/quizzes/<int:quiz_id>/questions/<int:question_id>/regenerate", methods=["POST"])
@login_required
def regenerate_quiz_question(quiz_id: int, question_id: int):
    """Regenerate distractors for a single PDF-sourced question."""
    denied = _require_permission(Permissions.CREATE_QUIZ)
    if denied:
        return denied
    denied, _assessment = _require_assessment_owner(quiz_id)
    if denied:
        return denied

    from app.services.quiz.mcq_converter import convert_pair_to_mcq, mcq_to_question_fields
    from app.services.quiz.models import QuestionAnswerPair

    q = question_bank_service.get_question(question_id)
    if not q.correct_answer_raw:
        return json_error("Question has no source answer to regenerate from", code="validation_error")

    pair = QuestionAnswerPair(
        question_number=q.source_question_number or question_id,
        question_text=q.question_text,
        question_latex=q.question_latex,
        answer_text=q.correct_answer_raw,
    )
    try:
        mcq = convert_pair_to_mcq(pair)
        fields = mcq_to_question_fields(mcq)
        updated = question_bank_service.update_question(
            question_id,
            question_text=fields["question_text"],
            question_latex=fields["question_latex"],
            options=fields["options"],
            correct_option_index=fields["correct_option_index"],
            explanation=fields["explanation"],
            extraction_confidence=fields["extraction_confidence"],
        )
        return json_success(question_bank_service.question_to_dict(updated))
    except Exception as e:
        return json_error(str(e), code="regenerate_error", status=500)


@bp.route("/quizzes/<int:quiz_id>/start", methods=["POST"])
@login_required
def start_quiz_attempt(quiz_id: int):
    if _current_role() != "student":
        return json_error("Only students can start quiz attempts", code="forbidden", status=403)

    body = request.get_json(silent=True) or {}
    assignment_id = body.get("assignment_id")
    try:
        attempt, resumed = attempt_service.start_attempt(
            student_id=_current_user_id(),
            assessment_id=quiz_id,
            assignment_id=assignment_id,
        )
        if assignment_id:
            assignment_service.link_attempt_to_submission(
                assignment_id, _current_user_id(), attempt.id
            )
        payload = {
            "attempt_id": attempt.id,
            "assessment_id": quiz_id,
            "status": attempt.status,
            "max_score": attempt.max_score,
            "resumed": resumed,
        }
        assessment = assessment_service.get_assessment(quiz_id)
        if assessment.assessment_type == "diagnostic":
            if getattr(attempt, "timed_out", False):
                return json_success(attempt_service.get_attempt_results(attempt.id))
            timer = attempt_service.get_attempt_timer_info(attempt.id)
            payload.update(timer)
        return json_success(payload, status=201)
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error")


@bp.route("/attempts/<int:attempt_id>/timer", methods=["GET"])
@login_required
def get_attempt_timer(attempt_id: int):
    try:
        attempt = attempt_service.get_attempt(attempt_id)
        if attempt.student_id != _current_user_id():
            return json_error("Forbidden", code="forbidden", status=403)
        return json_success(attempt_service.get_attempt_timer_info(attempt_id))
    except LMSNotFoundError as e:
        return json_error(str(e), code="not_found", status=404)


@bp.route("/attempts/<int:attempt_id>/questions", methods=["GET"])
@login_required
def get_attempt_questions(attempt_id: int):
    try:
        attempt = attempt_service.get_attempt(attempt_id)
        if attempt.student_id != _current_user_id() and _current_role() != "teacher":
            return json_error("Forbidden", code="forbidden", status=403)
        if attempt.status == "in_progress":
            state = attempt_service.get_attempt_delivery_state(attempt_id)
            return json_success({
                "attempt_id": attempt_id,
                "questions": state["questions"],
                "saved_answers": state.get("saved_answers", {}),
                "current_question_index": state.get("current_question_index", 0),
            })
        questions = attempt_service.get_delivery_questions(attempt_id)
        return json_success({
            "attempt_id": attempt_id,
            "questions": questions,
            "saved_answers": {},
            "current_question_index": 0,
        })
    except LMSNotFoundError as e:
        return json_error(str(e), code="not_found", status=404)
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error")


@bp.route("/attempts/<int:attempt_id>/answer", methods=["POST"])
@login_required
def save_attempt_answer(attempt_id: int):
    if _current_role() != "student":
        return json_error("Only students can submit answers", code="forbidden", status=403)

    body = request.get_json(silent=True) or {}
    try:
        attempt = attempt_service.get_attempt(attempt_id)
        if attempt.student_id != _current_user_id():
            return json_error("Forbidden", code="forbidden", status=403)
        answer = attempt_service.save_answer(
            attempt_id,
            int(body["question_id"]),
            int(body["selected_option_index"]),
        )
        return json_success({"saved": True, "question_id": answer.question_id})
    except (KeyError, TypeError, ValueError) as e:
        return json_error(str(e), code="validation_error")
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error")


@bp.route("/attempts/<int:attempt_id>/submit", methods=["POST"])
@login_required
def submit_attempt(attempt_id: int):
    if _current_role() != "student":
        return json_error("Only students can submit attempts", code="forbidden", status=403)

    try:
        attempt = attempt_service.get_attempt(attempt_id)
        if attempt.student_id != _current_user_id():
            return json_error("Forbidden", code="forbidden", status=403)
        body = request.get_json(silent=True) or {}
        time_expired = bool(body.get("time_expired") or body.get("timed_out"))
        from app.utils.llm_gateway import llm_workflow

        with llm_workflow(
            "diagnostic_weakness_analysis", user_id=_current_user_id(), user_role=_current_role()
        ):
            result = attempt_service.submit_attempt(attempt_id, time_expired=time_expired)
        return json_success(result)
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error")


@bp.route("/attempts/<int:attempt_id>/results", methods=["GET"])
@login_required
def get_attempt_results(attempt_id: int):
    try:
        attempt = attempt_service.get_attempt(attempt_id)
        if attempt.student_id != _current_user_id() and _current_role() != "teacher":
            return json_error("Forbidden", code="forbidden", status=403)
        from app.utils.llm_gateway import llm_workflow

        with llm_workflow(
            "diagnostic_weakness_analysis", user_id=_current_user_id(), user_role=_current_role()
        ):
            return json_success(attempt_service.get_attempt_results(attempt_id))
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error")


@bp.route("/students/me/assignments", methods=["GET"])
@login_required
def my_assignments():
    if _current_role() != "student":
        return json_error("Student only", code="forbidden", status=403)
    return json_success(assignment_service.list_assignments_for_student(_current_user_id()))


@bp.route("/diagnostics/default", methods=["GET"])
@login_required
def default_diagnostic():
    """Active platform diagnostic for the logged-in student."""
    if _current_role() != "student":
        return json_error("Students only", code="forbidden", status=403)
    diag = get_student_diagnostic_dict(_current_user_id())
    if not diag:
        return json_error(
            "No diagnostic available. Ask your admin to upload the diagnostic assessment.",
            code="not_found",
            status=404,
        )
    attempt_service.finalize_expired_diagnostic_if_needed(_current_user_id(), diag["id"])
    onboarding = student_profile_service.get_onboarding_status(_current_user_id())
    completed_id = onboarding.get("diagnostic_assessment_id")
    latest = attempt_service.get_latest_submitted_attempt(_current_user_id(), diag["id"])
    timed_out = bool(latest and getattr(latest, "timed_out", False))
    diag["diagnostic_completed"] = bool(onboarding.get("diagnostic_completed"))
    diag["any_diagnostic_completed"] = bool(onboarding.get("diagnostic_completed"))
    diag["diagnostic_timed_out"] = timed_out
    diag["diagnostic_timeout_message"] = (
        attempt_service.TIME_OVER_MESSAGE if timed_out else None
    )
    if timed_out:
        diag["score"] = 0
        diag["score_percent"] = 0
        diag["message"] = attempt_service.TIME_OVER_MESSAGE
    if diag["diagnostic_completed"] and completed_id:
        diag["completed_assessment_id"] = completed_id
    return json_success(diag)


@bp.route("/admin/diagnostics", methods=["GET"])
@login_required
def admin_list_diagnostics():
    denied = _require_diagnostic_admin()
    if denied:
        return denied
    return json_success(list_admin_diagnostics())


@bp.route("/admin/diagnostics/<int:assessment_id>", methods=["DELETE"])
@login_required
def admin_remove_diagnostic(assessment_id: int):
    """Archive a diagnostic so admin can upload a new one."""
    denied = _require_diagnostic_admin()
    if denied:
        return denied
    try:
        a = assessment_service.archive_diagnostic(assessment_id)
        return json_success({"id": a.id, "status": a.status, "title": a.title})
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error")


@bp.route("/diagnostics/from-pdf", methods=["POST"])
@login_required
def create_diagnostic_from_pdf():
    """Upload diagnostic Q&A PDF + target content PDF(s) (Learning Chat source). Admin only."""
    denied = _require_diagnostic_admin()
    if denied:
        return denied

    title = request.form.get("title") or "Diagnostic Assessment"
    diagnostic_file = request.files.get("diagnostic_file") or request.files.get("file")
    target_files = request.files.getlist("target_files") or request.files.getlist("target_files[]") or request.files.getlist("target_file")
    target_file = request.files.get("target_file")

    if not diagnostic_file:
        return json_error("Diagnostic Q&A PDF is required", code="validation_error")

    try:
        extra_targets = []
        seen_names = set()
        for tf in target_files:
            if not tf or not tf.filename:
                continue
            fname = tf.filename.strip()
            if fname.lower() in seen_names:
                continue
            seen_names.add(fname.lower())
            extra_targets.append({"bytes": tf.read(), "filename": fname})
        if target_file and target_file.filename:
            fname = target_file.filename.strip()
            if fname.lower() not in seen_names:
                extra_targets.append({"bytes": target_file.read(), "filename": fname})

        if not extra_targets:
            return json_error("At least one target content PDF is required", code="validation_error")

        first = extra_targets[0]
        rest = extra_targets[1:] if len(extra_targets) > 1 else None
        result = upload_diagnostic_bundle(
            teacher_id=_current_user_id(),
            title=title.strip(),
            diagnostic_file_bytes=diagnostic_file.read(),
            diagnostic_filename=diagnostic_file.filename or "diagnostic.pdf",
            target_file_bytes=first["bytes"],
            target_filename=first["filename"],
            target_files=rest,
            progress_job_id=(request.form.get("progress_job_id") or "").strip() or None,
        )
        return json_success(result, status=201)
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error")
    except Exception as e:
        return json_error(str(e), code="upload_error", status=500)


@bp.route("/diagnostics/upload-progress/<job_id>", methods=["GET"])
@login_required
def diagnostic_upload_progress(job_id: str):
    """Poll real processing progress for admin diagnostic upload."""
    denied = _require_diagnostic_admin()
    if denied:
        return denied
    from app.utils.diagnostic_upload_progress import get_progress

    progress = get_progress(job_id)
    if not progress:
        return json_success({"percent": 0, "message": "Waiting to start...", "stage": "pending", "done": False})
    return json_success(progress)


@bp.route("/diagnostics/<int:assessment_id>/target-pdf", methods=["POST"])
@login_required
def upload_diagnostic_target_pdf(assessment_id: int):
    """Upload additional target content PDF for an existing diagnostic. Admin only."""
    denied = _require_diagnostic_admin()
    if denied:
        return denied
    target_files = request.files.getlist("target_files") or request.files.getlist("target_files[]") or []
    single = request.files.get("target_file") or request.files.get("file")
    if single and single.filename:
        target_files.append(single)
    if not target_files:
        return json_error("At least one target content PDF is required", code="validation_error")
    try:
        results = []
        seen_names = set()
        for tf in target_files:
            if not tf or not tf.filename:
                continue
            fname = tf.filename.strip()
            if fname.lower() in seen_names:
                continue
            seen_names.add(fname.lower())
            results.append(
                upload_target_pdf(
                    assessment_id,
                    _current_user_id(),
                    tf.read(),
                    fname or "target.pdf",
                    is_admin=True,
                )
            )
        if not results:
            return json_error("No valid PDF files were uploaded", code="validation_error")
        return json_success({"uploaded": results, "target_pdfs": list_diagnostic_target_pdfs(assessment_id, _current_user_id(), is_admin=True)})
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error")
    except Exception as e:
        return json_error(str(e), code="upload_error", status=500)


@bp.route("/diagnostics/<int:assessment_id>/target-pdf/<int:target_pdf_id>", methods=["DELETE"])
@login_required
def delete_diagnostic_target_pdf(assessment_id: int, target_pdf_id: int):
    """Remove a target content PDF from a diagnostic. Admin only."""
    denied = _require_diagnostic_admin()
    if denied:
        return denied
    try:
        result = remove_target_pdf_entry(assessment_id, _current_user_id(), target_pdf_id, is_admin=True)
        return json_success(result)
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error")
    except LMSNotFoundError as e:
        return json_error(str(e), code="not_found", status=404)


@bp.route("/diagnostics/<int:assessment_id>/target-pdfs", methods=["GET"])
@login_required
def list_diagnostic_target_pdfs_route(assessment_id: int):
    denied = _require_diagnostic_admin()
    if denied:
        return denied
    try:
        return json_success(list_diagnostic_target_pdfs(assessment_id, _current_user_id(), is_admin=True))
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error")


@bp.route("/diagnostics/<int:assessment_id>/target-topics", methods=["GET"])
@login_required
def diagnostic_target_topics(assessment_id: int):
    """List headings from the target content PDF."""
    denied = _require_diagnostic_admin()
    if denied:
        return denied
    try:
        return json_success(list_target_pdf_topics(assessment_id, _current_user_id(), is_admin=True))
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error", status=403)


@bp.route("/diagnostics/pdf/<thread_id>/topics", methods=["GET"])
@login_required
def diagnostic_pdf_topics(thread_id: str):
    """List RAG headings from uploaded diagnostic PDF (A-316)."""
    denied = _require_diagnostic_admin()
    if denied:
        return denied
    try:
        return json_success(list_pdf_topics(thread_id, _current_user_id()))
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error", status=403)


@bp.route("/diagnostics/<int:assessment_id>/generate", methods=["POST"])
@login_required
def generate_diagnostic(assessment_id: int):
    """Generate MCQs from selected PDF topics (A-318)."""
    denied = _require_diagnostic_admin()
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    selections = body.get("topics") or body.get("selections") or []
    try:
        result = generate_diagnostic_questions(
            assessment_id, _current_user_id(), selections, is_admin=True
        )
        return json_success(result)
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error")
    except Exception as e:
        return json_error(str(e), code="generation_error", status=500)


@bp.route("/diagnostics/<int:assessment_id>/status", methods=["GET"])
@login_required
def diagnostic_status(assessment_id: int):
    denied = _require_diagnostic_admin()
    if denied:
        return denied
    try:
        return json_success(get_diagnostic_status(assessment_id, _current_user_id(), is_admin=True))
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error", status=403)
    except LMSNotFoundError as e:
        return json_error(str(e), code="not_found", status=404)


@bp.route("/diagnostics/<int:assessment_id>/preview", methods=["GET"])
@login_required
def diagnostic_preview(assessment_id: int):
    """Admin preview of generated diagnostic questions (A-319)."""
    denied = _require_diagnostic_admin()
    if denied:
        return denied
    try:
        assessment = assessment_service.get_assessment(assessment_id)
        if assessment.assessment_type != "diagnostic":
            return json_error("Not a diagnostic assessment", code="validation_error", status=400)
        data = assessment_service.get_assessment_with_questions(
            assessment_id, include_answers=True
        )
        return json_success(data)
    except LMSNotFoundError as e:
        return json_error(str(e), code="not_found", status=404)


@bp.route("/diagnostics/<int:assessment_id>/publish", methods=["POST"])
@login_required
def publish_diagnostic(assessment_id: int):
    denied = _require_diagnostic_admin()
    if denied:
        return denied
    try:
        assessment = assessment_service.get_assessment(assessment_id)
        if assessment.assessment_type != "diagnostic":
            return json_error("Not a diagnostic assessment", code="validation_error", status=400)
        a = assessment_service.publish_assessment(assessment_id)
        return json_success({
            "id": a.id,
            "status": a.status,
            "title": a.title,
            "time_limit_minutes": a.time_limit_minutes,
        })
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error")
    except LMSNotFoundError as e:
        return json_error(str(e), code="not_found", status=404)


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
    if body.get("assessment_type") == "diagnostic":
        return json_error(
            "Diagnostics are platform-wide and created by admin. Teachers create quizzes only.",
            code="validation_error",
        )
    a = assessment_service.create_assessment(
        created_by=_current_user_id(),
        title=body["title"],
        assessment_type="quiz",
        description=body.get("description"),
        creation_mode=body.get("creation_mode", "manual"),
        time_limit_minutes=body.get("time_limit_minutes"),
    )
    return json_success({"id": a.id, "title": a.title, "status": a.status}, status=201)


@bp.route("/quizzes/<int:quiz_id>", methods=["GET"])
@login_required
def get_quiz(quiz_id: int):
    try:
        assessment = assessment_service.get_assessment(quiz_id)
        role = _current_role()
        uid = _current_user_id()
        if role == "student":
            if assessment.assessment_type == "diagnostic":
                platform = assessment_service.get_active_platform_diagnostic()
                if not platform or platform.id != quiz_id:
                    return json_error("Forbidden", code="forbidden", status=403)
            elif assessment.assessment_type == "quiz":
                if not assignment_service.student_is_assigned_quiz(uid, quiz_id):
                    return json_error(
                        "Join your teacher's class with a class code to take this quiz.",
                        code="forbidden",
                        status=403,
                    )
            else:
                return json_error("Forbidden", code="forbidden", status=403)
        elif role == "teacher":
            if assessment.created_by != uid and assessment.assessment_type != "diagnostic":
                return json_error("Forbidden", code="forbidden", status=403)
        include_answers = role == "teacher" and has_permission(
            role, Permissions.CREATE_QUIZ
        )
        return json_success(
            assessment_service.get_assessment_with_questions(quiz_id, include_answers=include_answers)
        )
    except LMSNotFoundError as e:
        return json_error(str(e), code="not_found", status=404)


@bp.route("/quizzes/<int:quiz_id>/questions", methods=["PUT"])
@login_required
def update_quiz_questions(quiz_id: int):
    denied = _require_permission(Permissions.CREATE_QUIZ)
    if denied:
        return denied
    denied, _assessment = _require_assessment_owner(quiz_id)
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
        assessment = assessment_service.get_assessment(quiz_id)
        if assessment.assessment_type == "diagnostic":
            admin_denied = _require_diagnostic_admin()
            if admin_denied:
                return admin_denied
        elif assessment.created_by != _current_user_id() and _current_role() != "admin":
            return json_error("Forbidden", code="forbidden", status=403)
        a = assessment_service.publish_assessment(quiz_id)
        return json_success({"id": a.id, "status": a.status})
    except LMSNotFoundError as e:
        return json_error(str(e), code="not_found", status=404)
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


@bp.route("/classes/mine", methods=["GET"])
@login_required
def my_classes():
    uid = _current_user_id()
    role = _current_role()
    if role == "teacher":
        denied = _require_permission(Permissions.MANAGE_CLASS)
        if denied:
            return denied
        classes_list = class_service.list_teacher_classes(uid)
        return json_success(
            [
                {
                    "id": c.id,
                    "name": c.name,
                    "description": c.description,
                    "grade_level": c.grade_level,
                    "join_code": c.join_code,
                    "student_count": len(class_service.list_class_students(c.id)),
                }
                for c in classes_list
            ]
        )
    if role != "student":
        return json_error("Forbidden", code="forbidden", status=403)
    classes_list = class_service.list_student_classes(uid)
    return json_success(
        [
            {"id": c.id, "name": c.name, "description": c.description, "grade_level": c.grade_level}
            for c in classes_list
        ]
    )


@bp.route("/students/me/onboarding-status", methods=["GET"])
@login_required
def my_onboarding_status():
    if _current_role() != "student":
        return json_error("Student only", code="forbidden", status=403)
    return json_success(student_profile_service.get_onboarding_status(_current_user_id()))


@bp.route("/students/me/dashboard", methods=["GET"])
@login_required
def my_dashboard():
    if _current_role() != "student":
        return json_error("Student only", code="forbidden", status=403)
    return json_success(student_profile_service.get_student_dashboard(_current_user_id()))


@bp.route("/lessons/<int:lesson_id>/topics", methods=["GET", "PUT"])
@login_required
def lesson_topics(lesson_id: int):
    from app.models.models import LessonModel
    from app.services.lms import lesson_topic_service

    lesson = LessonModel.get_lesson_by_id(lesson_id)
    if not lesson:
        return json_error("Lesson not found", code="not_found", status=404)

    uid = _current_user_id()
    role = _current_role()
    if lesson.get("teacher_id") != uid and role != "admin":
        if role != "student" or not lesson.get("is_public"):
            return json_error("Forbidden", code="forbidden", status=403)

    if request.method == "GET":
        topics = lesson_topic_service.get_lesson_topics(lesson_id)
        return json_success(
            [{"id": t.id, "name": t.name, "slug": t.slug, "subject": t.subject} for t in topics]
        )

    if lesson.get("teacher_id") != uid:
        return json_error("Forbidden", code="forbidden", status=403)

    body = request.get_json(silent=True) or {}
    topic_ids = body.get("topic_ids") or []
    lesson_topic_service.set_lesson_topics(lesson_id, [int(t) for t in topic_ids])
    return json_success({"lesson_id": lesson_id, "topic_ids": topic_ids})


@bp.route("/students/me/progress", methods=["GET"])
@login_required
def my_progress():
    uid = _current_user_id()
    mastery = performance_service.get_student_mastery(uid)
    overall = performance_service.get_overall_progress(uid)
    # ensure_learning_path (not get_path_with_items) so this matches the
    # dashboard: after a Learning Chat session the finished practice path
    # is shown as complete, not as a missing/zeroed path.
    path = learning_path_service.ensure_learning_path(uid)
    return json_success({"overall_progress": overall, "topics": mastery, "learning_path": path})


@bp.route("/students/me/learning-path", methods=["GET", "PUT", "POST"])
@login_required
def my_learning_path():
    uid = _current_user_id()
    if _current_role() != "student":
        return json_error("Students only", code="forbidden", status=403)

    if request.method == "GET":
        path = learning_path_service.ensure_learning_path(uid)
        return json_success(path or {"items": [], "message": "No learning path yet. Complete a quiz to generate one."})

    if request.method == "POST":
        path = learning_path_service.generate_learning_path(uid, force=True)
        if not path:
            return json_success({"message": "No weak topics — you're caught up!", "items": []})
        return json_success(learning_path_service.get_path_with_items(uid))

    body = request.get_json(silent=True) or {}
    item_id = body.get("item_id")
    if not item_id:
        return json_error("item_id is required", code="validation_error")

    path = learning_path_service.get_active_path_for_student(uid)
    if not path:
        return json_error("No active learning path", code="not_found", status=404)

    try:
        row = learning_path_service.mark_item_complete(path.id, int(item_id), uid)
        return json_success(
            {
                "item_id": row.id,
                "status": row.status,
                "learning_path": learning_path_service.get_path_with_items(uid),
            }
        )
    except LMSValidationError as e:
        return json_error(str(e), code="forbidden", status=403)
    except LMSNotFoundError as e:
        return json_error(str(e), code="not_found", status=404)


@bp.route("/classes/<int:class_id>", methods=["PUT", "DELETE"])
@login_required
def class_detail(class_id: int):
    denied = _require_permission(Permissions.MANAGE_CLASS)
    if denied:
        return denied
    if request.method == "DELETE":
        try:
            c = class_service.archive_class(class_id, _current_user_id())
            return json_success({"id": c.id, "archived": True})
        except LMSValidationError as e:
            return json_error(str(e), code="forbidden", status=403)
    body = request.get_json(silent=True) or {}
    try:
        c = class_service.update_class(
            class_id,
            _current_user_id(),
            name=body.get("name"),
            description=body.get("description"),
            grade_level=body.get("grade_level"),
        )
        return json_success({"id": c.id, "name": c.name})
    except LMSValidationError as e:
        return json_error(str(e), code="forbidden", status=403)


@bp.route("/classes/<int:class_id>/roster", methods=["GET"])
@login_required
def class_roster(class_id: int):
    denied = _require_permission(Permissions.VIEW_CLASS_PERFORMANCE)
    if denied:
        return denied
    try:
        return json_success(analytics_service.get_class_roster_summary(class_id, _current_user_id()))
    except LMSValidationError as e:
        return json_error(str(e), code="forbidden", status=403)


@bp.route("/classes/<int:class_id>/analytics/topics", methods=["GET"])
@login_required
def class_topic_analytics(class_id: int):
    denied = _require_permission(Permissions.VIEW_CLASS_PERFORMANCE)
    if denied:
        return denied
    try:
        return json_success(analytics_service.aggregate_class_topics(class_id, _current_user_id()))
    except LMSValidationError as e:
        return json_error(str(e), code="forbidden", status=403)


@bp.route("/classes/<int:class_id>/analytics/quizzes", methods=["GET"])
@login_required
def class_quiz_analytics(class_id: int):
    denied = _require_permission(Permissions.VIEW_CLASS_PERFORMANCE)
    if denied:
        return denied
    try:
        return json_success(analytics_service.get_class_quiz_results(class_id, _current_user_id()))
    except LMSValidationError as e:
        return json_error(str(e), code="forbidden", status=403)


@bp.route("/classes/<int:class_id>/analytics/struggling", methods=["GET"])
@login_required
def class_struggling(class_id: int):
    denied = _require_permission(Permissions.VIEW_CLASS_PERFORMANCE)
    if denied:
        return denied
    try:
        return json_success(analytics_service.get_struggling_students(class_id, _current_user_id()))
    except LMSValidationError as e:
        return json_error(str(e), code="forbidden", status=403)


@bp.route("/classes/<int:class_id>/export.csv", methods=["GET"])
@login_required
def class_export_csv(class_id: int):
    denied = _require_permission(Permissions.VIEW_CLASS_PERFORMANCE)
    if denied:
        return denied
    try:
        csv_data = analytics_service.export_class_csv(class_id, _current_user_id())
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment;filename=class_roster.csv"},
        )
    except LMSValidationError as e:
        return json_error(str(e), code="forbidden", status=403)


@bp.route("/classes/<int:class_id>/students/<int:student_id>/report", methods=["GET"])
@login_required
def student_report(class_id: int, student_id: int):
    denied = _require_permission(Permissions.VIEW_CLASS_PERFORMANCE)
    if denied:
        return denied
    try:
        return json_success(
            analytics_service.get_student_report(student_id, _current_user_id(), class_id)
        )
    except (LMSValidationError, LMSNotFoundError) as e:
        return json_error(str(e), code="not_found", status=404)


@bp.route("/classes/<int:class_id>/assignments/<int:assignment_id>/submissions", methods=["GET"])
@login_required
def assignment_submissions(class_id: int, assignment_id: int):
    denied = _require_permission(Permissions.VIEW_CLASS_PERFORMANCE)
    if denied:
        return denied
    if not class_service.teacher_owns_class(_current_user_id(), class_id):
        return json_error("Forbidden", code="forbidden", status=403)
    try:
        return json_success(
            assignment_service.list_submissions_for_assignment(assignment_id, _current_user_id())
        )
    except LMSValidationError as e:
        return json_error(str(e), code="forbidden", status=403)


@bp.route("/students/me/attempts", methods=["GET"])
@login_required
def my_attempts():
    if _current_role() != "student":
        return json_error("Students only", code="forbidden", status=403)
    return json_success(attempt_service.list_student_attempts(_current_user_id()))


@bp.route("/students/me/progress/history", methods=["GET"])
@login_required
def my_progress_history():
    if _current_role() != "student":
        return json_error("Students only", code="forbidden", status=403)
    return json_success(analytics_service.get_progress_over_time(_current_user_id()))


@bp.route("/tutor/history", methods=["GET"])
@login_required
def get_tutor_history():
    mode = request.args.get("mode", "student")
    if mode == "teacher" and _current_role() != "teacher":
        return json_error("Teachers only", code="forbidden", status=403)
    if mode != "teacher" and _current_role() != "student":
        return json_error("Students only", code="forbidden", status=403)
    from app.services.lms import tutor_memory_service

    messages = tutor_memory_service.get_ui_messages(_current_user_id(), mode=mode)
    session = tutor_memory_service.get_session_for_user(_current_user_id(), mode=mode)
    return json_success(
        {
            "messages": messages,
            "has_long_term_memory": bool(session and session.summary_text),
            "message_count": len(messages),
        }
    )


@bp.route("/tutor/history", methods=["DELETE"])
@login_required
def clear_tutor_history():
    mode = request.args.get("mode", "student")
    if mode == "teacher" and _current_role() != "teacher":
        return json_error("Teachers only", code="forbidden", status=403)
    if mode != "teacher" and _current_role() != "student":
        return json_error("Students only", code="forbidden", status=403)
    from app.services.lms import tutor_memory_service

    tutor_memory_service.clear_session(_current_user_id(), mode=mode)
    return json_success({"cleared": True})


@bp.route("/tutor/chat", methods=["POST"])
@login_required
def student_tutor_chat():
    if _current_role() != "student":
        return json_error("Students only", code="forbidden", status=403)
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    if not message:
        return json_error("message is required", code="validation_error")
    context = tutor_service.build_student_context(
        _current_user_id(),
        topic_id=body.get("topic_id"),
        question_text=body.get("question_text"),
        attempt_count=int(body.get("attempt_count") or 0),
    )
    from app.services.lms import tutor_memory_service
    from app.utils.llm_gateway import llm_workflow

    with llm_workflow("lms_student_tutor_chat", user_id=_current_user_id(), user_role="student"):
        result = tutor_memory_service.chat_with_memory(
            _current_user_id(),
            message,
            mode="student",
            api_key=session.get("groq_api_key", ""),
            context=context,
            tutor_chat_fn=tutor_service.tutor_chat,
        )
    return json_success(result)


@bp.route("/teacher/tutor", methods=["POST"])
@login_required
def teacher_tutor_chat():
    denied = _require_permission(Permissions.CREATE_QUIZ)
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    if not message:
        return json_error("message is required", code="validation_error")
    from app.services.lms import tutor_memory_service
    from app.utils.llm_gateway import llm_workflow

    with llm_workflow("lms_teacher_tutor_chat", user_id=_current_user_id(), user_role="teacher"):
        result = tutor_memory_service.chat_with_memory(
            _current_user_id(),
            message,
            mode="teacher",
            api_key=session.get("groq_api_key", ""),
            context=None,
            tutor_chat_fn=tutor_service.tutor_chat,
        )
    return json_success(result)


@bp.route("/teacher/tutor/save-question", methods=["POST"])
@login_required
def teacher_tutor_save_question():
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
            difficulty=body.get("difficulty", "medium"),
            source_type="ai_tutor",
        )
        return json_success(question_bank_service.question_to_dict(q), status=201)
    except (KeyError, TypeError, ValueError) as e:
        return json_error(str(e), code="validation_error")


@bp.route("/practice/sessions", methods=["POST"])
@login_required
def start_practice():
    if _current_role() != "student":
        return json_error("Students only", code="forbidden", status=403)
    body = request.get_json(silent=True) or {}
    try:
        s, resumed = practice_service.start_session(
            _current_user_id(),
            topic_id=body.get("topic_id"),
            question_id=body.get("question_id"),
            force_new=bool(body.get("force_new")),
        )
        return json_success({"session_id": s.id, "resumed": resumed}, status=201)
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error")


@bp.route("/practice/sessions/<int:session_id>", methods=["GET"])
@login_required
def get_practice(session_id: int):
    try:
        return json_success(practice_service.get_session_question(session_id))
    except LMSNotFoundError as e:
        return json_error(str(e), code="not_found", status=404)


@bp.route("/practice/sessions/<int:session_id>/answer", methods=["POST"])
@login_required
def practice_answer(session_id: int):
    body = request.get_json(silent=True) or {}
    try:
        return json_success(
            practice_service.submit_answer(session_id, int(body["selected_option_index"]))
        )
    except (KeyError, TypeError, ValueError) as e:
        return json_error(str(e), code="validation_error")
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error")


@bp.route("/practice/sessions/<int:session_id>/hint", methods=["POST"])
@login_required
def practice_hint(session_id: int):
    try:
        return json_success(practice_service.request_hint(session_id))
    except LMSNotFoundError as e:
        return json_error(str(e), code="not_found", status=404)


@bp.route("/deficiency/sessions", methods=["POST"])
@login_required
def start_deficiency_chat():
    """Start post-diagnostic learning chat (weak-area questions one-by-one)."""
    if _current_role() != "student":
        return json_error("Students only", code="forbidden", status=403)
    try:
        body = request.get_json(silent=True) or {}
        force_new = bool(body.get("force_new"))
        from app.utils.llm_gateway import llm_workflow

        with llm_workflow(
            "lms_deficiency_chat_mcq_generation", user_id=_current_user_id(), user_role="student"
        ):
            result = deficiency_chat_service.start_session(_current_user_id(), force_new=force_new)
        return json_success(result, status=201)
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error")
    except Exception as e:
        return json_error(str(e), code="deficiency_error", status=500)


@bp.route("/deficiency/sessions/<int:session_id>", methods=["GET"])
@login_required
def get_deficiency_chat(session_id: int):
    if _current_role() != "student":
        return json_error("Students only", code="forbidden", status=403)
    try:
        return json_success(deficiency_chat_service.get_session(session_id, _current_user_id()))
    except LMSNotFoundError as e:
        return json_error(str(e), code="not_found", status=404)


@bp.route("/deficiency/sessions/<int:session_id>/answer", methods=["POST"])
@login_required
def deficiency_chat_answer(session_id: int):
    if _current_role() != "student":
        return json_error("Students only", code="forbidden", status=403)
    body = request.get_json(silent=True) or {}
    try:
        return json_success(
            deficiency_chat_service.submit_answer(
                session_id, _current_user_id(), int(body["selected_option_index"])
            )
        )
    except (KeyError, TypeError, ValueError):
        return json_error("selected_option_index is required", code="validation_error")
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error")


@bp.route("/deficiency/sessions/<int:session_id>/advance", methods=["POST"])
@login_required
def deficiency_chat_advance(session_id: int):
    """Move to the next Learning Chat question and close the tutor."""
    if _current_role() != "student":
        return json_error("Students only", code="forbidden", status=403)
    try:
        return json_success(deficiency_chat_service.advance_session(session_id, _current_user_id()))
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error")


@bp.route("/deficiency/sessions/<int:session_id>/pause", methods=["POST"])
@login_required
def deficiency_chat_pause(session_id: int):
    if _current_role() != "student":
        return json_error("Students only", code="forbidden", status=403)
    try:
        return json_success(deficiency_chat_service.pause_session(session_id, _current_user_id()))
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error")


@bp.route("/deficiency/sessions/<int:session_id>/explain", methods=["POST"])
@login_required
def deficiency_chat_explain(session_id: int):
    """Ask tutor about current weak-area question (PDF-grounded; not main lesson chat)."""
    if _current_role() != "student":
        return json_error("Students only", code="forbidden", status=403)
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    if not message:
        return json_error("message is required", code="validation_error")
    try:
        from app.utils.llm_gateway import llm_workflow

        with llm_workflow(
            "lms_deficiency_chat_tutor", user_id=_current_user_id(), user_role="student"
        ):
            result = deficiency_chat_service.explain_with_tutor(
                session_id,
                _current_user_id(),
                message,
                api_key=session.get("groq_api_key", ""),
            )
        return json_success(result)
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error")


@bp.route("/interventions", methods=["GET"])
@login_required
def interventions():
    class_id = request.args.get("class_id", type=int)
    student_id = request.args.get("student_id", type=int)
    topic_id = request.args.get("topic_id", type=int)
    if class_id and _current_role() == "teacher":
        try:
            return json_success(intervention_service.recommend_for_class(class_id, _current_user_id()))
        except LMSValidationError as e:
            return json_error(str(e), code="forbidden", status=403)
    if student_id:
        return json_success(intervention_service.recommend_for_student(student_id, topic_id=topic_id))
    if _current_role() == "student":
        return json_success(intervention_service.recommend_for_student(_current_user_id(), topic_id=topic_id))
    return json_error("class_id or student_id required", code="validation_error")


@bp.route("/interventions/auto-assign", methods=["POST"])
@login_required
def auto_assign():
    denied = _require_permission(Permissions.ASSIGN_QUIZ)
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    try:
        result = intervention_service.auto_assign_intervention(
            _current_user_id(),
            int(body["class_id"]),
            int(body["quiz_id"]),
            body.get("title") or "Intervention Quiz",
        )
        return json_success(result, status=201)
    except (KeyError, TypeError, ValueError) as e:
        return json_error(str(e), code="validation_error")
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error")


@bp.route("/teacher/analytics/pdf-sources", methods=["GET"])
@login_required
def pdf_analytics():
    denied = _require_permission(Permissions.CREATE_QUIZ)
    if denied:
        return denied
    return json_success(analytics_service.pdf_source_analytics(_current_user_id()))


@bp.errorhandler(LMSError)
def handle_lms_error(err):
    if isinstance(err, LMSNotFoundError):
        return json_error(str(err), code="not_found", status=404)
    if isinstance(err, LMSValidationError):
        return json_error(str(err), code="validation_error", status=400)
    return json_error(str(err), code="lms_error", status=500)
