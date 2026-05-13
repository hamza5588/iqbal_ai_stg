"""Exam Syllabus Engine routes."""
from flask import Blueprint, jsonify, request, session
from app.utils.db import get_db
from app.utils.auth import login_required
from app.rbac.decorators import admin_only
import app.services.syllabus_service as svc
from app.services.school.errors import SchoolServiceError

syllabus_bp = Blueprint("syllabus_bp", __name__)


def _json_error(exc: SchoolServiceError):
    return jsonify({"error": exc.message, "code": exc.code}), exc.http_status


def _et(e) -> dict:
    return {"id": e.id, "name": e.name, "code": e.code, "description": e.description, "is_active": e.is_active}


def _ch(c) -> dict:
    return {
        "id": c.id, "exam_type_id": c.exam_type_id, "platform_subject_id": c.platform_subject_id,
        "grade": c.grade, "title": c.title, "chapter_number": c.chapter_number, "is_active": c.is_active,
    }


def _tp(t) -> dict:
    return {"id": t.id, "chapter_id": t.chapter_id, "title": t.title, "order_index": t.order_index, "is_active": t.is_active}


def _st(s) -> dict:
    return {"id": s.id, "topic_id": s.topic_id, "title": s.title, "order_index": s.order_index, "is_active": s.is_active}


# Exam Types
@syllabus_bp.route("/api/syllabus/exam-types", methods=["GET"])
@login_required
def list_exam_types():
    db = get_db()
    return jsonify([_et(e) for e in svc.list_exam_types(db)])


@syllabus_bp.route("/api/syllabus/exam-types", methods=["POST"])
@login_required
@admin_only
def create_exam_type():
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        et = svc.create_exam_type(db, name=data.get("name", ""), code=data.get("code", ""), description=data.get("description"))
        db.commit()
        return jsonify(_et(et)), 201
    except SchoolServiceError as exc:
        return _json_error(exc)


@syllabus_bp.route("/api/syllabus/exam-types/<int:exam_type_id>", methods=["PUT"])
@login_required
@admin_only
def update_exam_type(exam_type_id):
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        et = svc.update_exam_type(db, exam_type_id=exam_type_id, **data)
        db.commit()
        return jsonify(_et(et))
    except SchoolServiceError as exc:
        return _json_error(exc)


@syllabus_bp.route("/api/syllabus/exam-types/<int:exam_type_id>", methods=["DELETE"])
@login_required
@admin_only
def delete_exam_type(exam_type_id):
    db = get_db()
    try:
        svc.delete_exam_type(db, exam_type_id=exam_type_id)
        db.commit()
        return jsonify({"ok": True})
    except SchoolServiceError as exc:
        return _json_error(exc)


# Chapters
@syllabus_bp.route("/api/syllabus/chapters", methods=["POST"])
@login_required
@admin_only
def create_chapter():
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        ch = svc.create_chapter(
            db,
            exam_type_id=data.get("exam_type_id"),
            platform_subject_id=data.get("platform_subject_id"),
            grade=data.get("grade", ""),
            title=data.get("title", ""),
            chapter_number=data.get("chapter_number"),
        )
        db.commit()
        return jsonify(_ch(ch)), 201
    except SchoolServiceError as exc:
        return _json_error(exc)


@syllabus_bp.route("/api/syllabus/chapters/<int:chapter_id>", methods=["PUT"])
@login_required
@admin_only
def update_chapter(chapter_id):
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        ch = svc.update_chapter(db, chapter_id=chapter_id, **data)
        db.commit()
        return jsonify(_ch(ch))
    except SchoolServiceError as exc:
        return _json_error(exc)


@syllabus_bp.route("/api/syllabus/chapters/<int:chapter_id>", methods=["DELETE"])
@login_required
@admin_only
def delete_chapter(chapter_id):
    db = get_db()
    try:
        svc.delete_chapter(db, chapter_id=chapter_id)
        db.commit()
        return jsonify({"ok": True})
    except SchoolServiceError as exc:
        return _json_error(exc)


# Topics
@syllabus_bp.route("/api/syllabus/topics", methods=["POST"])
@login_required
@admin_only
def create_topic():
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        t = svc.add_topic(db, chapter_id=data.get("chapter_id"), title=data.get("title", ""), order_index=data.get("order_index", 0))
        db.commit()
        return jsonify(_tp(t)), 201
    except SchoolServiceError as exc:
        return _json_error(exc)


@syllabus_bp.route("/api/syllabus/topics/<int:topic_id>", methods=["PUT"])
@login_required
@admin_only
def update_topic(topic_id):
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        t = svc.update_topic(db, topic_id=topic_id, **data)
        db.commit()
        return jsonify(_tp(t))
    except SchoolServiceError as exc:
        return _json_error(exc)


@syllabus_bp.route("/api/syllabus/topics/<int:topic_id>", methods=["DELETE"])
@login_required
@admin_only
def delete_topic(topic_id):
    db = get_db()
    try:
        svc.delete_topic(db, topic_id=topic_id)
        db.commit()
        return jsonify({"ok": True})
    except SchoolServiceError as exc:
        return _json_error(exc)


# Sub-topics
@syllabus_bp.route("/api/syllabus/sub-topics", methods=["POST"])
@login_required
@admin_only
def create_sub_topic():
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        st = svc.add_sub_topic(db, topic_id=data.get("topic_id"), title=data.get("title", ""), order_index=data.get("order_index", 0))
        db.commit()
        return jsonify(_st(st)), 201
    except SchoolServiceError as exc:
        return _json_error(exc)


@syllabus_bp.route("/api/syllabus/sub-topics/<int:sub_topic_id>", methods=["PUT"])
@login_required
@admin_only
def update_sub_topic(sub_topic_id):
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        st = svc.update_sub_topic(db, sub_topic_id=sub_topic_id, **data)
        db.commit()
        return jsonify(_st(st))
    except SchoolServiceError as exc:
        return _json_error(exc)


@syllabus_bp.route("/api/syllabus/sub-topics/<int:sub_topic_id>", methods=["DELETE"])
@login_required
@admin_only
def delete_sub_topic(sub_topic_id):
    db = get_db()
    try:
        svc.delete_sub_topic(db, sub_topic_id=sub_topic_id)
        db.commit()
        return jsonify({"ok": True})
    except SchoolServiceError as exc:
        return _json_error(exc)


# Full nested syllabus
@syllabus_bp.route("/api/syllabus/full", methods=["GET"])
@login_required
def get_full_syllabus():
    db = get_db()
    exam_type_id = request.args.get("exam_type_id", type=int)
    platform_subject_id = request.args.get("platform_subject_id", type=int)
    grade = request.args.get("grade", "")
    if not exam_type_id or not platform_subject_id or not grade:
        return jsonify({"error": "exam_type_id, platform_subject_id and grade are required", "code": "validation_error"}), 400
    syllabus = svc.get_syllabus(db, exam_type_id=exam_type_id, platform_subject_id=platform_subject_id, grade=grade)
    return jsonify(syllabus)
