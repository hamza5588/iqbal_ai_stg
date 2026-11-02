"""Admin-only group management and student performance report APIs."""

from flask import Blueprint, jsonify, request, send_file, session

from app.models.database_models import User
from app.models.lms_models import StudentStudyGroupAudit
from app.rbac.decorators import admin_only
from app.services.student_reporting_service import (
    assign_students,
    build_report,
    classify_assessment,
    export_report,
    list_student_groups,
    normalize_cohort,
    preview_group_import,
    reporting_options,
)
from app.utils.db import get_db


bp = Blueprint("student_reporting", __name__, url_prefix="/admin")


@bp.after_request
def private_report_response(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _body():
    if not request.is_json:
        raise ValueError("JSON request required.")
    return request.get_json(silent=True) or {}


@bp.get("/student-groups")
@admin_only
def groups_list():
    try:
        return jsonify(success=True, **list_student_groups(get_db(), request.args))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@bp.post("/student-groups/assign")
@admin_only
def groups_assign():
    db = get_db()
    try:
        body = _body()
        result = assign_students(
            db, body.get("student_ids") or [], body.get("group"), body.get("cohort"),
            session["user_id"], source="manual", note=body.get("note"),
        )
        db.commit()
        return jsonify(success=True, **result)
    except ValueError as exc:
        db.rollback()
        return jsonify(error=str(exc)), 400
    except Exception:
        db.rollback()
        raise


@bp.get("/student-groups/<int:student_id>/history")
@admin_only
def group_history(student_id):
    db = get_db()
    try:
        cohort = normalize_cohort(request.args.get("cohort"))
        student = db.query(User).filter(User.id == student_id, User.role == "student").one_or_none()
        if not student:
            return jsonify(error="Student not found."), 404
        rows = db.query(StudentStudyGroupAudit).filter_by(student_id=student_id, cohort_key=cohort).order_by(
            StudentStudyGroupAudit.changed_at.desc(), StudentStudyGroupAudit.id.desc()
        ).limit(200).all()
        actor_ids = {row.changed_by for row in rows if row.changed_by}
        actors = {row.id: (row.full_name or row.username) for row in db.query(User).filter(User.id.in_(actor_ids or {-1})).all()}
        return jsonify(success=True, student={"id": student.id, "name": student.full_name or student.username}, history=[
            {"old_group": row.old_group_name, "new_group": row.new_group_name,
             "changed_at": row.changed_at.isoformat(), "source": row.source, "note": row.note,
             "changed_by": actors.get(row.changed_by, "System")}
            for row in rows
        ])
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@bp.post("/student-groups/import/preview")
@admin_only
def groups_import_preview():
    try:
        upload = request.files.get("file")
        if not upload or not upload.filename:
            raise ValueError("Select a CSV or XLSX file.")
        return jsonify(success=True, **preview_group_import(get_db(), upload))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@bp.post("/student-groups/import/apply")
@admin_only
def groups_import_apply():
    db = get_db()
    try:
        body = _body()
        rows = body.get("rows") or []
        if len(rows) > 5000:
            raise ValueError("At most 5,000 import rows can be applied.")
        by_group = {}
        seen_students = set()
        for row in rows:
            if row.get("status") != "ready" or not row.get("student_id"):
                continue
            try:
                student_id = int(row["student_id"])
            except (TypeError, ValueError):
                raise ValueError("Import preview contains an invalid student ID.")
            if student_id in seen_students:
                raise ValueError("The import contains a duplicate student. Preview it again before applying.")
            seen_students.add(student_id)
            by_group.setdefault(row.get("group"), []).append(student_id)
        if not by_group:
            raise ValueError("There are no valid preview rows to apply.")
        changed = unchanged = 0
        for group, student_ids in by_group.items():
            result = assign_students(
                db, student_ids, group, body.get("cohort"), session["user_id"],
                source="import", note="Applied from reviewed import preview",
            )
            changed += result["changed"]
            unchanged += result["unchanged"]
        db.commit()
        return jsonify(success=True, changed=changed, unchanged=unchanged)
    except ValueError as exc:
        db.rollback()
        return jsonify(error=str(exc)), 400
    except Exception:
        db.rollback()
        raise


@bp.get("/student-reporting/options")
@admin_only
def report_options():
    try:
        return jsonify(success=True, **reporting_options(get_db(), request.args.get("cohort")))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@bp.put("/student-reporting/assessments/<int:assessment_id>/classification")
@admin_only
def report_classification(assessment_id):
    db = get_db()
    try:
        body = _body()
        result = classify_assessment(
            db, assessment_id, body.get("cohort"), body.get("test_type"),
            body.get("followup_order"), session["user_id"],
        )
        db.commit()
        return jsonify(success=True, classification=result)
    except ValueError as exc:
        db.rollback()
        return jsonify(error=str(exc)), 400
    except Exception:
        db.rollback()
        raise


@bp.post("/student-reporting/preview")
@admin_only
def report_preview():
    try:
        report = build_report(get_db(), _body())
        return jsonify(
            success=True,
            filters=report["filters"], matching_students=report["matching_students"],
            attempts=report["attempts"], average_score=report["average_score"],
            topic_rows=report["topic_rows"], warnings=report["warnings"],
            attempt_rule=report["attempt_rule"], metric_rules=report["metric_rules"],
            files=[{"filename": item["filename"], "rows": len(item["results"]),
                    "group": item["group"], "test_type": item["test_type"],
                    "test_date": item["test_date"].isoformat()} for item in report["files"]],
        )
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@bp.post("/student-reporting/export")
@admin_only
def report_export():
    try:
        report = build_report(get_db(), _body())
        stream, filename, mimetype = export_report(report)
        return send_file(stream, as_attachment=True, download_name=filename, mimetype=mimetype, max_age=0)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
