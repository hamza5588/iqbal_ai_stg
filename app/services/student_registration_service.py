"""
Student self-registration bound to coordinator roster (roll number + name + school).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.database_models import User as DBUser
from app.models.school_org_models import ClassEnrollment, ClassSection, RosterEntry, School
from app.rbac.roles import Role
from app.services.school.school_service import normalize_person_name_key, normalize_roll_number
from app.services.school.errors import SchoolServiceError

logger = logging.getLogger(__name__)

_ROSTER_SAFE = "Could not validate school roll number against roster."


def _resolve_school(db: Session, *, school_id: Optional[int], school_code: Optional[str]) -> School:
    if school_id is not None:
        s = db.query(School).filter(School.id == int(school_id)).first()
    elif school_code:
        s = db.query(School).filter(School.code == (school_code or "").strip()).first()
    else:
        s = None
    if not s:
        raise SchoolServiceError(_ROSTER_SAFE, "roster_mismatch", 400)
    return s


def register_student_with_roll_number(
    db: Session,
    *,
    first_name: str,
    last_name: str,
    roll_number: str,
    school_id: Optional[int],
    school_code: Optional[str],
    email: str,
    username: str,
    password: str,
    class_standard: str = "",
    medium: str = "",
    groq_api_key: str = "",
) -> Dict[str, Any]:
    """
    Create a student user, link all matching roster rows in the school, and enroll.

    Matching roster rows: same normalized roll + normalized first/last name,
    status ``expected`` with no linked student, or idempotent retry for the same email
    after partial failure (handled via duplicate email path).
    """
    email_n = (email or "").strip().lower()
    username_n = (username or "").strip()
    if not email_n or not username_n or not password:
        raise SchoolServiceError("email, username, and password are required", "bad_request", 400)

    if db.query(DBUser).filter(DBUser.useremail == email_n).first():
        raise ValueError("Username or email already exists")
    if db.query(DBUser).filter(DBUser.username == username_n).first():
        raise ValueError("Username or email already exists")

    school = _resolve_school(db, school_id=school_id, school_code=school_code)
    roll = normalize_roll_number(roll_number)
    fn_key = normalize_person_name_key(first_name)
    ln_key = normalize_person_name_key(last_name)
    if not roll or not fn_key or not ln_key:
        raise SchoolServiceError(_ROSTER_SAFE, "roster_mismatch", 400)

    q = (
        db.query(RosterEntry)
        .join(ClassSection, ClassSection.id == RosterEntry.class_section_id)
        .filter(
            ClassSection.school_id == school.id,
            RosterEntry.roll_number == roll,
            RosterEntry.status.in_(("expected",)),
            RosterEntry.linked_student_user_id.is_(None),
        )
        .all()
    )
    matches: List[RosterEntry] = []
    for row in q:
        if normalize_person_name_key(row.first_name) != fn_key:
            continue
        if normalize_person_name_key(row.last_name) != ln_key:
            continue
        matches.append(row)

    if not matches:
        logger.info("Student registration roster miss for school_id=%s roll=%s", school.id, roll)
        raise SchoolServiceError(_ROSTER_SAFE, "roster_mismatch", 400)

    user = DBUser(
        username=username_n,
        useremail=email_n,
        password=password,
        class_standard=class_standard or "",
        medium=medium or "school",
        groq_api_key=groq_api_key or "",
        role=Role.STUDENT.value,
        subscription_tier="free",
    )
    db.add(user)
    try:
        db.flush()
        uid = int(user.id)
        section_ids: List[int] = []
        for row in matches:
            row.linked_student_user_id = uid
            row.status = "linked"
            section_ids.append(int(row.class_section_id))
            ex = (
                db.query(ClassEnrollment)
                .filter(
                    ClassEnrollment.class_section_id == row.class_section_id,
                    ClassEnrollment.student_user_id == uid,
                )
                .first()
            )
            if not ex:
                db.add(
                    ClassEnrollment(
                        class_section_id=row.class_section_id,
                        student_user_id=uid,
                        status="active",
                    )
                )
            else:
                ex.status = "active"
        db.commit()
    except IntegrityError as e:
        db.rollback()
        msg = str(e.orig) if getattr(e, "orig", None) else str(e)
        if "unique" in msg.lower() or "UNIQUE" in msg:
            logger.info("Duplicate user on registration: %s", msg)
            raise ValueError("Username or email already exists") from e
        logger.error("Registration transaction failed: %s", e)
        raise SchoolServiceError("Registration could not be completed", "conflict", 409) from e

    return {
        "user_id": uid,
        "school_id": school.id,
        "linked_class_section_ids": list({int(s) for s in section_ids}),
        "roster_rows_linked": len(matches),
    }
