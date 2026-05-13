"""
User administration service — hierarchical user creation, audit logging,
and suspend / reactivate flows.

Role hierarchy (higher number = higher authority):
  platform_admin / admin  → 6
  district_admin          → 5
  school_admin            → 4
  principal               → 3
  coordinator             → 2
  teacher / student / parent → 1 / 0 / 0
"""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime
from typing import List, Optional

from app.models.database_models import User
from app.models.phase1_models import AdminCreationLog
from app.models.school_org_models import ClassEnrollment
from app.services.notification_service import bulk_notify, create_notification
from app.services.school.errors import SchoolServiceError

ROLE_HIERARCHY: dict[str, int] = {
    "platform_admin": 6,
    "admin": 6,
    "district_admin": 5,
    "school_admin": 4,
    "principal": 3,
    "coordinator": 2,
    "teacher": 1,
    "student": 0,
    "parent": 0,
}

VALID_ROLES = set(ROLE_HIERARCHY.keys())


_MIN_INVITE_RANK = ROLE_HIERARCHY["coordinator"]  # coordinator (2) or above can invite


def _actor_can_create(actor_role: str, target_role: str) -> bool:
    """Actor must rank strictly higher than target AND be at least coordinator-level."""
    actor_rank = ROLE_HIERARCHY.get(actor_role, -1)
    target_rank = ROLE_HIERARCHY.get(target_role, -1)
    return actor_rank >= _MIN_INVITE_RANK and actor_rank > target_rank


def _hash_password(password: str) -> str:
    """Simple SHA-256 hash (mirrors existing pattern if no bcrypt in project)."""
    # Import the project's existing password hashing utility if available
    try:
        from app.utils.auth import hash_password
        return hash_password(password)
    except ImportError:
        return hashlib.sha256(password.encode()).hexdigest()


def invite_create_user(
    db,
    *,
    actor_id: int,
    email: str,
    username: str,
    role: str,
    scope_school_id: Optional[int] = None,
    class_standard: str = "",
    medium: str = "",
) -> User:
    """
    Create a new user on behalf of a higher-role admin.

    - Enforces role hierarchy: actor must outrank target role.
    - Writes an AdminCreationLog row.
    - Sends an invite email (best-effort; failure does not roll back the user).
    """
    if role not in VALID_ROLES:
        raise SchoolServiceError(f"Invalid role: {role}", "invalid_role", 400)

    actor = db.query(User).filter_by(id=actor_id).first()
    if not actor:
        raise SchoolServiceError("Actor not found", "not_found", 404)

    if not _actor_can_create(actor.role, role):
        raise SchoolServiceError(
            f"A {actor.role} cannot create a {role}",
            "forbidden",
            403,
        )

    if db.query(User).filter_by(useremail=email).first():
        raise SchoolServiceError(f"Email already registered: {email}", "duplicate_email", 409)

    if db.query(User).filter_by(username=username).first():
        raise SchoolServiceError(f"Username already taken: {username}", "duplicate_username", 409)

    # Generate a temporary random password; user will reset via email
    temp_password = secrets.token_urlsafe(12)
    hashed = _hash_password(temp_password)

    new_user = User(
        useremail=email,
        username=username,
        password=hashed,
        role=role,
        class_standard=class_standard,
        medium=medium,
        groq_api_key="",
        is_active=True,
    )
    db.add(new_user)
    db.flush()  # get new_user.id

    # Audit log
    log = AdminCreationLog(
        creator_id=actor_id,
        created_user_id=new_user.id,
        role_assigned=role,
        scope_school_id=scope_school_id,
    )
    db.add(log)
    db.flush()

    # Send invite email (best-effort)
    _send_invite_email(email=email, username=username, temp_password=temp_password)

    return new_user


def _send_invite_email(*, email: str, username: str, temp_password: str) -> None:
    """Send invite email with temporary credentials. Non-blocking on failure."""
    try:
        from flask_mail import Message
        from app import mail
        msg = Message(
            subject="Welcome to IqbalAI — Your Account Has Been Created",
            recipients=[email],
            body=(
                f"Hello {username},\n\n"
                "An administrator has created an account for you on IqbalAI.\n\n"
                f"Email: {email}\n"
                f"Temporary Password: {temp_password}\n\n"
                "Please log in and change your password immediately.\n\n"
                "The IqbalAI Team"
            ),
        )
        mail.send(msg)
    except Exception:
        pass  # Email failure must not block user creation


def suspend_user(db, *, actor_id: int, target_user_id: int) -> User:
    """
    Suspend a user account.

    - Sets is_active=False, suspended_at, suspended_by_id.
    - Notifies the suspended user.
    - If the user is a teacher, notifies all their currently enrolled students.
    """
    actor = db.query(User).filter_by(id=actor_id).first()
    if not actor:
        raise SchoolServiceError("Actor not found", "not_found", 404)

    target = db.query(User).filter_by(id=target_user_id).first()
    if not target:
        raise SchoolServiceError("User not found", "not_found", 404)

    if not _actor_can_create(actor.role, target.role):
        raise SchoolServiceError(
            f"A {actor.role} cannot suspend a {target.role}",
            "forbidden",
            403,
        )

    if not target.is_active:
        raise SchoolServiceError("User is already suspended", "already_suspended", 400)

    target.is_active = False
    target.suspended_at = datetime.utcnow()
    target.suspended_by_id = actor_id
    db.flush()

    # Notify the suspended user
    create_notification(
        db,
        recipient_id=target_user_id,
        title="Your Account Has Been Suspended",
        message=(
            "Your account has been temporarily suspended by an administrator. "
            "Please contact support if you believe this is an error."
        ),
        type="suspension",
    )

    # If teacher, notify all enrolled students
    if target.role == "teacher":
        _notify_students_of_teacher_suspension(db, teacher_id=target_user_id)

    return target


def _notify_students_of_teacher_suspension(db, *, teacher_id: int) -> None:
    """Find all students enrolled in sections taught by this teacher and notify them."""
    from app.models.school_org_models import ClassSection

    section_ids = [
        s.id
        for s in db.query(ClassSection).filter_by(teacher_user_id=teacher_id, status="active").all()
    ]
    if not section_ids:
        return

    student_ids = [
        row.student_user_id
        for row in db.query(ClassEnrollment)
        .filter(ClassEnrollment.class_section_id.in_(section_ids), ClassEnrollment.status == "active")
        .all()
    ]
    if not student_ids:
        return

    bulk_notify(
        db,
        recipient_ids=list(set(student_ids)),
        title="Teacher Account Temporarily Unavailable",
        message=(
            "One of your teachers is temporarily unavailable. "
            "Your school administration will provide further information."
        ),
        type="warning",
    )


def reactivate_user(db, *, actor_id: int, target_user_id: int) -> User:
    """Reactivate a suspended user account."""
    actor = db.query(User).filter_by(id=actor_id).first()
    if not actor:
        raise SchoolServiceError("Actor not found", "not_found", 404)

    target = db.query(User).filter_by(id=target_user_id).first()
    if not target:
        raise SchoolServiceError("User not found", "not_found", 404)

    if not _actor_can_create(actor.role, target.role):
        raise SchoolServiceError(
            f"A {actor.role} cannot reactivate a {target.role}",
            "forbidden",
            403,
        )

    if target.is_active:
        raise SchoolServiceError("User is not suspended", "not_suspended", 400)

    target.is_active = True
    target.suspended_at = None
    target.suspended_by_id = None
    db.flush()

    create_notification(
        db,
        recipient_id=target_user_id,
        title="Your Account Has Been Reactivated",
        message="Your account has been reactivated. You can now log in to IqbalAI.",
        type="info",
    )

    return target


def get_creation_log(
    db,
    *,
    creator_id: Optional[int] = None,
    page: int = 1,
    per_page: int = 50,
) -> List[AdminCreationLog]:
    """Return paginated admin creation audit log, newest first."""
    q = db.query(AdminCreationLog)
    if creator_id:
        q = q.filter_by(creator_id=creator_id)
    q = q.order_by(AdminCreationLog.created_at.desc())
    return q.offset((page - 1) * per_page).limit(per_page).all()
