"""
Student Data Export & Deletion service.

Students can download their data as a ZIP archive.
Deletion anonymises PII but retains anonymous learning data.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime

from app.models.database_models import User, Conversation
from app.models.school_org_models import ClassEnrollment
from app.services.school.errors import SchoolServiceError


def export_student_data(db, *, student_user_id: int, requesting_user_id: int) -> bytes:
    """
    Build an in-memory ZIP containing the student's data.
    Only the student themselves or a super-admin can export.

    Returns raw bytes of the ZIP file.
    """
    requesting_user = db.query(User).filter_by(id=requesting_user_id).first()
    if not requesting_user:
        raise SchoolServiceError("Requesting user not found", "not_found", 404)

    from app.rbac.roles import SUPER_ADMIN_ROLES
    if requesting_user_id != student_user_id and requesting_user.role not in SUPER_ADMIN_ROLES:
        raise SchoolServiceError("Not authorized to export this student's data", "forbidden", 403)

    student = db.query(User).filter_by(id=student_user_id).first()
    if not student:
        raise SchoolServiceError("Student not found", "not_found", 404)

    # --- Profile JSON ---
    profile = {
        "id": student.id,
        "username": student.username,
        "useremail": student.useremail,
        "role": student.role,
        "class_standard": student.class_standard,
        "medium": student.medium,
        "preferred_language": student.preferred_language,
        "subscription_tier": student.subscription_tier,
        "created_at": student.created_at.isoformat() if student.created_at else None,
        "last_login": student.last_login.isoformat() if student.last_login else None,
        "is_active": student.is_active,
        "terms_accepted_version": student.terms_accepted_version,
    }

    # --- Enrollments JSON ---
    enrollments = (
        db.query(ClassEnrollment)
        .filter_by(student_user_id=student_user_id)
        .all()
    )
    enrollments_data = [
        {
            "class_section_id": e.class_section_id,
            "status": e.status,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in enrollments
    ]

    # --- Conversations JSON ---
    conversations = (
        db.query(Conversation)
        .filter_by(user_id=student_user_id)
        .all()
    )
    conversations_data = [
        {
            "id": c.id,
            "title": getattr(c, "title", None),
            "created_at": c.created_at.isoformat() if hasattr(c, "created_at") and c.created_at else None,
        }
        for c in conversations
    ]

    # Build ZIP in memory
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("profile.json", json.dumps(profile, indent=2))
        zf.writestr("enrollments.json", json.dumps(enrollments_data, indent=2))
        zf.writestr("conversations.json", json.dumps(conversations_data, indent=2))
        zf.writestr(
            "README.txt",
            (
                "IqbalAI Student Data Export\n"
                f"Generated: {datetime.utcnow().isoformat()} UTC\n"
                f"Student ID: {student_user_id}\n\n"
                "This archive contains your personal data held by IqbalAI.\n"
                "Files: profile.json, enrollments.json, conversations.json\n"
            ),
        )

    buf.seek(0)
    return buf.read()


def anonymize_student(db, *, actor_id: int, student_user_id: int) -> User:
    """
    Anonymise a student's PII while keeping anonymous learning data.

    Replaces: username, useremail, password.
    Sets: is_active=False.
    Does NOT delete rows — audit trail is preserved.
    """
    actor = db.query(User).filter_by(id=actor_id).first()
    if not actor:
        raise SchoolServiceError("Actor not found", "not_found", 404)

    from app.rbac.roles import SUPER_ADMIN_ROLES
    if actor_id != student_user_id and actor.role not in SUPER_ADMIN_ROLES:
        raise SchoolServiceError("Not authorized to delete this account", "forbidden", 403)

    student = db.query(User).filter_by(id=student_user_id).first()
    if not student:
        raise SchoolServiceError("Student not found", "not_found", 404)

    if student.role not in ("student", "parent"):
        raise SchoolServiceError(
            "Account deletion is only available for student and parent accounts",
            "forbidden",
            403,
        )

    # Anonymise PII
    student.username = f"deleted_{student_user_id}"
    student.useremail = f"deleted_{student_user_id}@deleted.invalid"
    student.password = "DELETED"
    student.groq_api_key = ""
    student.stripe_customer_id = None
    student.stripe_subscription_id = None
    student.is_active = False

    db.flush()
    return student
