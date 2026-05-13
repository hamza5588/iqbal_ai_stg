"""
Platform content routes:
- /api/admin/platform/subjects  — platform-level subject catalog (admin only writes)
- /api/content/books            — content library (all logged-in users read; admin writes)
"""
import os
from flask import Blueprint, jsonify, request, session, send_file
from werkzeug.utils import secure_filename
from app.utils.db import get_db
from app.utils.auth import login_required
from app.rbac.decorators import admin_only
import app.services.content_service as content_svc
from app.models.phase1_models import PlatformSubject
from app.services.school.errors import SchoolServiceError

platform_admin_bp = Blueprint("platform_admin_bp", __name__)
content_library_bp = Blueprint("content_library_bp", __name__)


def _json_error(exc: SchoolServiceError):
    return jsonify({"error": exc.message, "code": exc.code}), exc.http_status


# ---------------------------------------------------------------------------
# Platform Subject Catalog
# ---------------------------------------------------------------------------

def _serialize_subject(s) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "short_code": s.short_code,
        "grade_bands": s.grade_bands,
        "is_active": s.is_active,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


@platform_admin_bp.route("/api/admin/platform/subjects", methods=["GET"])
@login_required
def list_platform_subjects():
    db = get_db()
    active_only = request.args.get("active_only", "true").lower() != "false"
    q = db.query(PlatformSubject)
    if active_only:
        q = q.filter_by(is_active=True)
    subjects = q.order_by(PlatformSubject.name).all()
    return jsonify([_serialize_subject(s) for s in subjects])


@platform_admin_bp.route("/api/admin/platform/subjects", methods=["POST"])
@login_required
@admin_only
def create_platform_subject():
    db = get_db()
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    short_code = data.get("short_code", "").strip().upper()
    if not name or not short_code:
        return jsonify({"error": "name and short_code are required", "code": "validation_error"}), 400
    existing = db.query(PlatformSubject).filter_by(short_code=short_code).first()
    if existing:
        return jsonify({"error": "short_code already exists", "code": "duplicate_code"}), 409
    subj = PlatformSubject(
        name=name,
        short_code=short_code,
        grade_bands=data.get("grade_bands"),
        created_by=session["user_id"],
    )
    db.add(subj)
    db.commit()
    return jsonify(_serialize_subject(subj)), 201


@platform_admin_bp.route("/api/admin/platform/subjects/<int:subject_id>", methods=["PUT"])
@login_required
@admin_only
def update_platform_subject(subject_id):
    db = get_db()
    subj = db.query(PlatformSubject).filter_by(id=subject_id).first()
    if not subj:
        return jsonify({"error": "Subject not found", "code": "not_found"}), 404
    data = request.get_json(silent=True) or {}
    for field in ("name", "grade_bands", "is_active"):
        if field in data:
            setattr(subj, field, data[field])
    db.commit()
    return jsonify(_serialize_subject(subj))


@platform_admin_bp.route("/api/admin/platform/subjects/<int:subject_id>", methods=["DELETE"])
@login_required
@admin_only
def delete_platform_subject(subject_id):
    db = get_db()
    subj = db.query(PlatformSubject).filter_by(id=subject_id).first()
    if not subj:
        return jsonify({"error": "Subject not found", "code": "not_found"}), 404
    subj.is_active = False
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Content Library Books
# ---------------------------------------------------------------------------

def _serialize_book(b) -> dict:
    return {
        "id": b.id,
        "title": b.title,
        "grade": b.grade,
        "platform_subject_id": b.platform_subject_id,
        "school_subject_id": b.school_subject_id,
        "mime_type": b.mime_type,
        "file_size": b.file_size,
        "is_active": b.is_active,
        "uploaded_by": b.uploaded_by,
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }


@content_library_bp.route("/api/content/books", methods=["GET"])
@login_required
def list_books():
    db = get_db()
    books = content_svc.list_content_books(
        db,
        platform_subject_id=request.args.get("platform_subject_id", type=int),
        school_subject_id=request.args.get("school_subject_id", type=int),
        grade=request.args.get("grade"),
    )
    return jsonify([_serialize_book(b) for b in books])


@content_library_bp.route("/api/content/books", methods=["POST"])
@login_required
@admin_only
def upload_book():
    """Upload a content book file. Expects multipart/form-data."""
    db = get_db()
    title = request.form.get("title", "").strip()
    grade = request.form.get("grade", "").strip()
    if not title or not grade:
        return jsonify({"error": "title and grade are required", "code": "validation_error"}), 400

    uploaded_file = request.files.get("file")
    if not uploaded_file:
        return jsonify({"error": "file is required", "code": "validation_error"}), 400

    # Save to a temp location first, then move after we have the book ID
    filename = secure_filename(uploaded_file.filename or "book")
    tmp_dir = content_svc.get_upload_dir(0)  # temp dir
    tmp_path = os.path.join(tmp_dir, filename)
    uploaded_file.save(tmp_path)
    file_size = os.path.getsize(tmp_path)

    try:
        book = content_svc.upload_content_book(
            db,
            actor_id=session["user_id"],
            title=title,
            grade=grade,
            file_path=tmp_path,
            file_size=file_size,
            mime_type=uploaded_file.content_type,
            platform_subject_id=request.form.get("platform_subject_id", type=int),
            school_subject_id=request.form.get("school_subject_id", type=int),
        )
        # Move to final location
        final_dir = content_svc.get_upload_dir(book.id)
        final_path = os.path.join(final_dir, filename)
        os.rename(tmp_path, final_path)
        book.file_path = final_path
        db.commit()
        return jsonify(_serialize_book(book)), 201
    except SchoolServiceError as exc:
        return _json_error(exc)


@content_library_bp.route("/api/content/books/<int:book_id>", methods=["GET"])
@login_required
def get_book(book_id):
    db = get_db()
    try:
        book = content_svc.get_content_book(db, book_id=book_id)
    except SchoolServiceError as exc:
        return _json_error(exc)
    if request.args.get("download") == "true" and os.path.exists(book.file_path):
        return send_file(book.file_path, as_attachment=True)
    return jsonify(_serialize_book(book))


@content_library_bp.route("/api/content/books/<int:book_id>", methods=["PUT"])
@login_required
@admin_only
def update_book(book_id):
    db = get_db()
    data = request.get_json(silent=True) or {}
    try:
        book = content_svc.update_content_book(db, book_id=book_id, **data)
        db.commit()
        return jsonify(_serialize_book(book))
    except SchoolServiceError as exc:
        return _json_error(exc)


@content_library_bp.route("/api/content/books/<int:book_id>", methods=["DELETE"])
@login_required
@admin_only
def delete_book(book_id):
    db = get_db()
    try:
        content_svc.delete_content_book(db, book_id=book_id)
        db.commit()
        return jsonify({"ok": True})
    except SchoolServiceError as exc:
        return _json_error(exc)
