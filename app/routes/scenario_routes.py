"""Scenario-Based Assessment API (/api/lms/scenario/...).

Teacher endpoints require the CREATE_QUIZ permission (teachers + admins) and ownership
of the assessment; student endpoints require TAKE_ASSESSMENT and class enrollment.
Every lookup goes through app.services.scenario.service, which returns 404 for
resources the caller may not see.
"""
from __future__ import annotations

from flask import Blueprint, request, session

from app.rbac.permissions import Permissions, has_permission
from app.services.lms.exceptions import LMSNotFoundError, LMSPermissionError, LMSValidationError
from app.services.scenario import documents, service
from app.utils.auth import login_required
from app.utils.lms_api import json_error, json_success

bp = Blueprint("scenario", __name__)


def _uid() -> int:
    return int(session["user_id"])


def _role() -> str:
    return session.get("role", "student")


def _teacher_guard():
    if not has_permission(_role(), Permissions.CREATE_QUIZ):
        return json_error("Forbidden", code="forbidden", status=403)
    return None


def _student_guard():
    if _role() != "student" or not has_permission(_role(), Permissions.TAKE_ASSESSMENT):
        return json_error("Forbidden", code="forbidden", status=403)
    return None


def _handle(fn):
    try:
        return fn()
    except LMSNotFoundError as e:
        return json_error(str(e), code="not_found", status=404)
    except LMSPermissionError as e:
        return json_error(str(e), code="forbidden", status=403)
    except LMSValidationError as e:
        return json_error(str(e), code="validation_error", status=400)
    except (TypeError, ValueError) as e:
        return json_error(f"Invalid request: {e}", code="validation_error", status=400)


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _file():
    f = request.files.get("file")
    if f is None or not f.filename:
        raise LMSValidationError("Choose a PDF file to upload.")
    return f.filename, f.read()


# ── teacher ─────────────────────────────────────────────────────────────────────

@bp.route("/assessments", methods=["GET", "POST"])
@login_required
def assessments():
    if (err := _teacher_guard()):
        return err
    if request.method == "GET":
        return _handle(lambda: json_success(service.list_teacher_assessments(_uid(), _role())))
    return _handle(lambda: json_success(
        service.teacher_detail(service.create_assessment(_uid(), _role(), _body()).id, _uid(), _role()), status=201
    ))


@bp.route("/assessments/<int:aid>", methods=["GET", "PUT"])
@login_required
def assessment_detail(aid: int):
    if (err := _teacher_guard()):
        return err
    if request.method == "PUT":
        return _handle(lambda: (service.update_assessment(aid, _uid(), _role(), _body()),
                                json_success(service.teacher_detail(aid, _uid(), _role())))[1])
    return _handle(lambda: json_success(service.teacher_detail(aid, _uid(), _role())))


@bp.route("/assessments/<int:aid>/documents", methods=["POST"])
@login_required
def upload_document(aid: int):
    if (err := _teacher_guard()):
        return err

    def go():
        doc_type = (request.form.get("doc_type") or "").strip()
        name, data = _file()
        doc = service.upload_document(aid, _uid(), _role(), doc_type, name, data)
        return json_success(documents.serialize_document(documents.get_document(doc.id)), status=202)

    return _handle(go)


@bp.route("/documents/<int:did>", methods=["DELETE"])
@login_required
def delete_document(did: int):
    if (err := _teacher_guard()):
        return err
    return _handle(lambda: (service.delete_document(did, _uid(), _role()), json_success({"id": did}))[1])


@bp.route("/documents/<int:did>/replace", methods=["POST"])
@login_required
def replace_document(did: int):
    if (err := _teacher_guard()):
        return err

    def go():
        name, data = _file()
        doc = service.replace_document(did, _uid(), _role(), name, data)
        return json_success(documents.serialize_document(documents.get_document(doc.id)), status=202)

    return _handle(go)


@bp.route("/documents/<int:did>/reprocess", methods=["POST"])
@login_required
def reprocess_document(did: int):
    if (err := _teacher_guard()):
        return err
    return _handle(lambda: json_success(documents.serialize_document(service.reprocess_document(did, _uid(), _role()))))


@bp.route("/samples/<int:sid>", methods=["PUT", "DELETE"])
@login_required
def sample(sid: int):
    if (err := _teacher_guard()):
        return err
    data = {"delete": True} if request.method == "DELETE" else _body()
    return _handle(lambda: json_success(service.update_sample(sid, _uid(), _role(), data)))


@bp.route("/assessments/<int:aid>/generate", methods=["POST"])
@login_required
def generate(aid: int):
    if (err := _teacher_guard()):
        return err
    count = _body().get("count")
    return _handle(lambda: (service.start_generation(aid, _uid(), _role(), int(count) if count else None),
                            json_success({"status": "running"}, status=202))[1])


@bp.route("/questions/<int:qid>", methods=["PUT"])
@login_required
def update_question(qid: int):
    if (err := _teacher_guard()):
        return err
    return _handle(lambda: json_success(service.serialize_question_teacher(
        service.update_question(qid, _uid(), _role(), _body()))))


@bp.route("/questions/<int:qid>/regenerate", methods=["POST"])
@login_required
def regenerate_question(qid: int):
    if (err := _teacher_guard()):
        return err
    return _handle(lambda: json_success(service.serialize_question_teacher(service.regenerate(qid, _uid(), _role()))))


@bp.route("/assessments/<int:aid>/publish", methods=["POST"])
@login_required
def publish(aid: int):
    if (err := _teacher_guard()):
        return err
    return _handle(lambda: (service.publish(aid, _uid(), _role()),
                            json_success(service.teacher_detail(aid, _uid(), _role())))[1])


@bp.route("/assessments/<int:aid>/unpublish", methods=["POST"])
@login_required
def unpublish(aid: int):
    if (err := _teacher_guard()):
        return err
    return _handle(lambda: (service.unpublish(aid, _uid(), _role()),
                            json_success(service.teacher_detail(aid, _uid(), _role())))[1])


@bp.route("/assessments/<int:aid>/release-results", methods=["POST"])
@login_required
def release(aid: int):
    if (err := _teacher_guard()):
        return err
    return _handle(lambda: (service.release_results(aid, _uid(), _role()), json_success({"released": True}))[1])


@bp.route("/assessments/<int:aid>/submissions", methods=["GET"])
@login_required
def submissions(aid: int):
    if (err := _teacher_guard()):
        return err
    return _handle(lambda: json_success(service.list_submissions(aid, _uid(), _role())))


@bp.route("/submissions/<int:sid>", methods=["GET"])
@login_required
def submission_detail(sid: int):
    if (err := _teacher_guard()):
        return err
    return _handle(lambda: json_success(service.submission_detail(sid, _uid(), _role())))


@bp.route("/submissions/<int:sid>/review", methods=["POST"])
@login_required
def review(sid: int):
    if (err := _teacher_guard()):
        return err
    return _handle(lambda: json_success(service.review_submission(sid, _uid(), _role(), _body())))


@bp.route("/submissions/<int:sid>/retry", methods=["POST"])
@login_required
def retry(sid: int):
    if (err := _teacher_guard()):
        return err
    return _handle(lambda: json_success(service.retry_evaluation(sid, _uid(), _role()), status=202))


# ── student ─────────────────────────────────────────────────────────────────────

@bp.route("/student/assessments", methods=["GET"])
@login_required
def student_assessments():
    if (err := _student_guard()):
        return err
    return _handle(lambda: json_success(service.list_student_assessments(_uid())))


@bp.route("/student/assessments/<int:aid>", methods=["GET"])
@login_required
def student_assessment(aid: int):
    if (err := _student_guard()):
        return err
    return _handle(lambda: json_success(service.student_detail(aid, _uid())))


@bp.route("/student/questions/<int:qid>/draft", methods=["PUT"])
@login_required
def student_draft(qid: int):
    if (err := _student_guard()):
        return err
    text = _body().get("answer_text", "")
    return _handle(lambda: (service.save_draft(qid, _uid(), str(text)), json_success({"saved": True}))[1])


@bp.route("/student/questions/<int:qid>/submit", methods=["POST"])
@login_required
def student_submit(qid: int):
    if (err := _student_guard()):
        return err
    body = _body()
    text = body.get("answer_text")
    return _handle(lambda: json_success(
        {"id": (s := service.submit_answer(qid, _uid(), None if text is None else str(text))).id, "status": s.status},
        status=202,
    ))
