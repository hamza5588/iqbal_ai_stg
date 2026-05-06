"""Create/update schools, subjects, class sections, roster, and student roster linking."""
from __future__ import annotations

import csv
import io
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import Float as SAFloat, cast, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.database_models import User as DBUser
from app.models.school_org_models import (
    ClassEnrollment,
    ClassSection,
    District,
    RosterEntry,
    School,
    SchoolCoordinator,
    SchoolSubject,
    TeacherSchoolAffiliation,
)
from app.rbac.roles import Role, is_super_admin_role
from app.rbac.school_permissions import can_manage_roster
from app.services.school.access import can_manage_school, teacher_affiliated_with_school
from app.services.school.errors import SchoolServiceError

logger = logging.getLogger(__name__)


def _norm(s: Optional[str]) -> str:
    return (s or "").strip()


def _norm_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _actor_may_create_school(db: Session, actor_id: int) -> bool:
    user = db.query(DBUser).filter(DBUser.id == actor_id).first()
    if not user:
        return False
    r = Role.from_string(user.role or "student")
    if is_super_admin_role(r):
        return True
    if r in (Role.PRINCIPAL, Role.DISTRICT_ADMIN, Role.PLATFORM_ADMIN):
        return True
    return False


def create_district(db: Session, *, name: str, code: Optional[str], actor_id: int) -> District:
    user = db.query(DBUser).filter(DBUser.id == actor_id).first()
    if not user or not is_super_admin_role(Role.from_string(user.role or "student")):
        raise SchoolServiceError("Only super administrators may create districts", "forbidden", 403)
    d = District(name=_norm(name)[:255], code=(code or None))
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def create_school(
    db: Session,
    *,
    name: str,
    code: str,
    district_id: Optional[int],
    principal_user_id: Optional[int],
    actor_id: int,
) -> School:
    user = db.query(DBUser).filter(DBUser.id == actor_id).first()
    if not user or not _actor_may_create_school(db, actor_id):
        raise SchoolServiceError("Not allowed to create schools", "forbidden", 403)
    school = School(
        name=_norm(name)[:255],
        code=_norm(code)[:64],
        district_id=district_id,
        principal_user_id=principal_user_id,
        status="active",
    )
    db.add(school)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise SchoolServiceError("School code must be unique", "duplicate_code", 409) from e
    db.refresh(school)
    return school


def set_principal(db: Session, *, school_id: int, principal_user_id: Optional[int], actor_id: int) -> School:
    if not can_manage_school(db, actor_id, school_id):
        raise SchoolServiceError("Not allowed to manage this school", "forbidden", 403)
    school = db.query(School).filter(School.id == school_id).first()
    if not school:
        raise SchoolServiceError("School not found", "not_found", 404)
    school.principal_user_id = principal_user_id
    db.commit()
    db.refresh(school)
    return school


def add_coordinator(db: Session, *, school_id: int, coordinator_user_id: int, actor_id: int) -> None:
    """Principal or scoped admins assign coordinators (not roster-only coordinators)."""
    if not can_manage_school(db, actor_id, school_id):
        raise SchoolServiceError("Not allowed to manage this school", "forbidden", 403)
    row = SchoolCoordinator(school_id=school_id, user_id=coordinator_user_id)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise SchoolServiceError("User is already a coordinator for this school", "duplicate", 409)


def remove_coordinator(db: Session, *, school_id: int, coordinator_user_id: int, actor_id: int) -> None:
    if not can_manage_school(db, actor_id, school_id):
        raise SchoolServiceError("Not allowed", "forbidden", 403)
    db.query(SchoolCoordinator).filter(
        SchoolCoordinator.school_id == school_id,
        SchoolCoordinator.user_id == coordinator_user_id,
    ).delete()
    db.commit()


def upsert_subject(
    db: Session,
    *,
    school_id: int,
    name: str,
    short_code: str,
    grade_band: Optional[str],
    actor_id: int,
) -> SchoolSubject:
    if not can_manage_roster(db, actor_id, school_id):
        raise SchoolServiceError("Not allowed to edit subjects for this school", "forbidden", 403)
    subj = SchoolSubject(
        school_id=school_id,
        name=_norm(name)[:255],
        short_code=_norm(short_code)[:64],
        grade_band=(grade_band or None),
    )
    db.add(subj)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise SchoolServiceError("Subject short_code already exists in this school", "duplicate", 409)
    db.refresh(subj)
    return subj


def add_teacher_affiliation(
    db: Session, *, school_id: int, teacher_user_id: int, actor_id: int
) -> TeacherSchoolAffiliation:
    if not can_manage_roster(db, actor_id, school_id):
        raise SchoolServiceError("Not allowed", "forbidden", 403)
    t = db.query(DBUser).filter(DBUser.id == teacher_user_id).first()
    if not t or Role.from_string(t.role or "student") != Role.TEACHER:
        raise SchoolServiceError("Target user must be a teacher", "invalid_teacher", 400)
    row = TeacherSchoolAffiliation(school_id=school_id, teacher_user_id=teacher_user_id, is_active=True)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise SchoolServiceError("Teacher already affiliated", "duplicate", 409)
    return row


def create_class_section(
    db: Session,
    *,
    school_id: int,
    subject_id: int,
    teacher_user_id: int,
    grade_level: str,
    section: str,
    academic_year: str,
    display_name: Optional[str],
    actor_id: int,
) -> ClassSection:
    if not can_manage_roster(db, actor_id, school_id):
        raise SchoolServiceError("Not allowed", "forbidden", 403)
    if not teacher_affiliated_with_school(db, teacher_user_id, school_id):
        raise SchoolServiceError(
            "Teacher must have an active affiliation with this school before class section creation",
            "teacher_not_affiliated",
            400,
        )
    subj = db.query(SchoolSubject).filter(SchoolSubject.id == subject_id, SchoolSubject.school_id == school_id).first()
    if not subj:
        raise SchoolServiceError("Subject not found in this school", "not_found", 404)
    sec = ClassSection(
        school_id=school_id,
        subject_id=subject_id,
        teacher_user_id=teacher_user_id,
        grade_level=_norm(grade_level)[:64],
        section=_norm(section)[:64],
        academic_year=_norm(academic_year)[:32],
        display_name=(display_name or None),
        status="active",
    )
    db.add(sec)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise SchoolServiceError(
            "Class section already exists for this subject/grade/section/year",
            "duplicate",
            409,
        ) from e
    db.refresh(sec)
    return sec


def _detect_csv_columns(header: Sequence[str]) -> Tuple[int, int, int]:
    """Map roll, first, last column indices from header row."""
    h = [_norm_key(x) for x in header]
    roll_i = first_i = last_i = -1
    for i, key in enumerate(h):
        if key in ("roll", "rollnumber", "rollno", "roll_no", "id"):
            roll_i = i
        if key in ("firstname", "first", "givenname", "given"):
            first_i = i
        if key in ("lastname", "last", "surname", "familyname", "family"):
            last_i = i
    if roll_i < 0 or first_i < 0 or last_i < 0:
        raise SchoolServiceError(
            "CSV must include columns: roll_number (or roll), first_name (or first), last_name (or last)",
            "invalid_csv",
            400,
        )
    return roll_i, first_i, last_i


def import_roster_csv(
    db: Session,
    *,
    class_section_id: int,
    csv_text: str,
    actor_id: int,
    skip_duplicates: bool = True,
) -> Dict[str, Any]:
    sec = db.query(ClassSection).filter(ClassSection.id == class_section_id).first()
    if not sec:
        raise SchoolServiceError("Class section not found", "not_found", 404)
    if not can_manage_roster(db, actor_id, sec.school_id):
        raise SchoolServiceError("Not allowed to import roster for this class", "forbidden", 403)

    reader = csv.reader(io.StringIO(csv_text))
    rows_list = list(reader)
    if not rows_list:
        raise SchoolServiceError("Empty CSV", "empty", 400)
    roll_i, first_i, last_i = _detect_csv_columns(rows_list[0])
    data_rows = rows_list[1:]

    created = 0
    skipped = 0
    errors: List[str] = []

    for lineno, parts in enumerate(data_rows, start=2):
        if not parts or all(not _norm(p) for p in parts):
            continue
        try:
            roll = normalize_roll_number(parts[roll_i])[:64]
            fn = _norm(parts[first_i])[:128]
            ln = _norm(parts[last_i])[:128]
        except IndexError:
            errors.append(f"Line {lineno}: not enough columns")
            continue
        if not roll or not fn or not ln:
            errors.append(f"Line {lineno}: missing data")
            continue
        exists = (
            db.query(RosterEntry)
            .filter(RosterEntry.class_section_id == class_section_id, RosterEntry.roll_number == roll)
            .first()
        )
        if exists:
            if skip_duplicates:
                skipped += 1
                continue
            errors.append(f"Line {lineno}: duplicate roll {roll}")
            continue
        db.add(
            RosterEntry(
                class_section_id=class_section_id,
                roll_number=roll,
                first_name=fn,
                last_name=ln,
                status="expected",
            )
        )
        created += 1

    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise SchoolServiceError("Roster import failed (integrity)", "import_failed", 409) from e

    return {"created": created, "skipped": skipped, "errors": errors}


def link_student_to_roster(
    db: Session,
    *,
    student_user_id: int,
    school_code: str,
    class_section_id: int,
    roll_number: str,
    first_name: str,
    last_name: str,
) -> Dict[str, Any]:
    """Bind a student account to coordinator roster + active enrollment."""
    st = db.query(DBUser).filter(DBUser.id == student_user_id).first()
    if not st or Role.from_string(st.role or "student") != Role.STUDENT:
        raise SchoolServiceError("Only student accounts can link to a roster", "invalid_role", 403)

    school = db.query(School).filter(School.code == _norm(school_code)).first()
    if not school:
        raise SchoolServiceError("Unknown school code", "not_found", 404)

    sec = (
        db.query(ClassSection)
        .filter(ClassSection.id == class_section_id, ClassSection.school_id == school.id)
        .first()
    )
    if not sec:
        raise SchoolServiceError("Class section not in this school", "not_found", 404)

    roll = normalize_roll_number(roll_number)[:64]
    fn_key = normalize_person_name_key(first_name)
    ln_key = normalize_person_name_key(last_name)

    roster = (
        db.query(RosterEntry)
        .filter(
            RosterEntry.class_section_id == class_section_id,
            RosterEntry.roll_number == roll,
        )
        .first()
    )
    if not roster:
        raise SchoolServiceError("No roster line matches this roll number in the class", "roster_mismatch", 400)
    if normalize_person_name_key(roster.first_name) != fn_key or normalize_person_name_key(roster.last_name) != ln_key:
        raise SchoolServiceError(
            "Name does not match school roster — check spelling with your coordinator",
            "name_mismatch",
            400,
        )
    if roster.linked_student_user_id and roster.linked_student_user_id != student_user_id:
        raise SchoolServiceError("This roll number is already linked to another account", "roll_taken", 409)

    roster.linked_student_user_id = student_user_id
    roster.status = "linked"

    en = (
        db.query(ClassEnrollment)
        .filter(
            ClassEnrollment.class_section_id == class_section_id,
            ClassEnrollment.student_user_id == student_user_id,
        )
        .first()
    )
    if not en:
        db.add(
            ClassEnrollment(
                class_section_id=class_section_id,
                student_user_id=student_user_id,
                status="active",
            )
        )
    else:
        en.status = "active"

    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise SchoolServiceError("Could not complete enrollment", "conflict", 409) from e

    return {"ok": True, "school_id": school.id, "class_section_id": class_section_id}


def school_summary(db: Session, school_id: int) -> Dict[str, Any]:
    school = db.query(School).filter(School.id == school_id).first()
    if not school:
        raise SchoolServiceError("School not found", "not_found", 404)
    n_teachers = (
        db.query(TeacherSchoolAffiliation.teacher_user_id)
        .filter(TeacherSchoolAffiliation.school_id == school_id, TeacherSchoolAffiliation.is_active.is_(True))
        .count()
    )
    n_sections = db.query(ClassSection).filter(ClassSection.school_id == school_id).count()
    n_roster = (
        db.query(RosterEntry)
        .join(ClassSection, ClassSection.id == RosterEntry.class_section_id)
        .filter(ClassSection.school_id == school_id)
        .count()
    )
    n_linked = (
        db.query(RosterEntry)
        .join(ClassSection, ClassSection.id == RosterEntry.class_section_id)
        .filter(ClassSection.school_id == school_id, RosterEntry.status == "linked")
        .count()
    )
    return {
        "id": school.id,
        "name": school.name,
        "code": school.code,
        "teachers": n_teachers,
        "class_sections": n_sections,
        "roster_rows": n_roster,
        "linked_students": n_linked,
    }


def normalize_roll_number(roll: str) -> str:
    """Normalize roll numbers for comparison (strip + uppercase)."""
    return (roll or "").strip().upper()


def normalize_person_name_key(name: str) -> str:
    """Collapse whitespace and lowercase for roster name matching."""
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def add_roster_entry(
    db: Session,
    *,
    class_section_id: int,
    first_name: str,
    last_name: str,
    roll_number: str,
    actor_id: int,
) -> RosterEntry:
    """Insert a single roster row; roll number must be unique within the class section."""
    sec = db.query(ClassSection).filter(ClassSection.id == class_section_id).first()
    if not sec:
        raise SchoolServiceError("Class section not found", "not_found", 404)
    if not can_manage_roster(db, actor_id, sec.school_id):
        raise SchoolServiceError("Not allowed", "forbidden", 403)
    roll = normalize_roll_number(roll_number)
    fn = _norm(first_name)[:128]
    ln = _norm(last_name)[:128]
    if not roll or not fn or not ln:
        raise SchoolServiceError("first_name, last_name, and roll_number are required", "bad_request", 400)
    row = RosterEntry(
        class_section_id=class_section_id,
        roll_number=roll,
        first_name=fn,
        last_name=ln,
        status="expected",
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        logger.info("Duplicate roster roll for section %s: %s", class_section_id, roll)
        raise SchoolServiceError(
            "Roll number already exists for this class section",
            "duplicate_roll",
            409,
        ) from e
    db.refresh(row)
    return row


def bulk_add_roster_entries(
    db: Session,
    *,
    class_section_id: int,
    entries: Sequence[Dict[str, Any]],
    actor_id: int,
) -> Dict[str, Any]:
    """
    Bulk-insert roster rows from JSON payloads: each item needs
    first_name, last_name, roll_number.
    """
    sec = db.query(ClassSection).filter(ClassSection.id == class_section_id).first()
    if not sec:
        raise SchoolServiceError("Class section not found", "not_found", 404)
    if not can_manage_roster(db, actor_id, sec.school_id):
        raise SchoolServiceError("Not allowed", "forbidden", 403)
    created = 0
    errors: List[str] = []
    for i, item in enumerate(entries):
        try:
            add_roster_entry(
                db,
                class_section_id=class_section_id,
                first_name=str(item.get("first_name", "")),
                last_name=str(item.get("last_name", "")),
                roll_number=str(item.get("roll_number", "")),
                actor_id=actor_id,
            )
            created += 1
        except SchoolServiceError as e:
            errors.append(f"row {i}: {e.message}")
    return {"created": created, "errors": errors}


def list_roster_entries_for_class_section(
    db: Session, *, class_section_id: int, actor_id: int
) -> List[RosterEntry]:
    sec = db.query(ClassSection).filter(ClassSection.id == class_section_id).first()
    if not sec:
        raise SchoolServiceError("Class section not found", "not_found", 404)
    allowed = can_manage_roster(db, actor_id, sec.school_id) or sec.teacher_user_id == actor_id
    if not allowed:
        user = db.query(DBUser).filter(DBUser.id == actor_id).first()
        if not user or not is_super_admin_role(Role.from_string(user.role or "student")):
            raise SchoolServiceError("Not allowed", "forbidden", 403)
    return (
        db.query(RosterEntry)
        .filter(RosterEntry.class_section_id == class_section_id)
        .order_by(RosterEntry.roll_number)
        .all()
    )


def update_roster_entry_status(
    db: Session, *, roster_entry_id: int, new_status: str, actor_id: int
) -> RosterEntry:
    if new_status not in ("expected", "linked", "withdrawn"):
        raise SchoolServiceError("Invalid status", "bad_request", 400)
    row = db.query(RosterEntry).filter(RosterEntry.id == roster_entry_id).first()
    if not row:
        raise SchoolServiceError("Roster entry not found", "not_found", 404)
    sec = db.query(ClassSection).filter(ClassSection.id == row.class_section_id).first()
    if not sec or not can_manage_roster(db, actor_id, sec.school_id):
        raise SchoolServiceError("Not allowed", "forbidden", 403)
    row.status = new_status
    db.commit()
    db.refresh(row)
    return row


def principal_dashboard_metrics(db: Session, *, principal_user_id: int) -> Dict[str, Any]:
    """
    Aggregate metrics for schools where the user is the assigned principal.
    Uses only real queries over existing tables (no synthetic at-risk scores).
    """
    from app.models.school_learning_models import QuizSession, QuizSubmission

    school_rows = db.query(School).filter(School.principal_user_id == principal_user_id).all()
    if not school_rows:
        return {"schools": []}
    out: List[Dict[str, Any]] = []
    for school in school_rows:
        sid = school.id
        enrollment_count = (
            db.query(ClassEnrollment)
            .join(ClassSection, ClassSection.id == ClassEnrollment.class_section_id)
            .filter(ClassSection.school_id == sid, ClassEnrollment.status == "active")
            .count()
        )
        active_teachers = (
            db.query(TeacherSchoolAffiliation.teacher_user_id)
            .filter(TeacherSchoolAffiliation.school_id == sid, TeacherSchoolAffiliation.is_active.is_(True))
            .count()
        )
        section_count = db.query(ClassSection).filter(ClassSection.school_id == sid).count()

        ratio = cast(QuizSubmission.score, SAFloat) / cast(QuizSubmission.max_score, SAFloat)
        rows = (
            db.query(SchoolSubject.name, func.avg(ratio))
            .select_from(QuizSubmission)
            .join(QuizSession, QuizSession.id == QuizSubmission.quiz_session_id)
            .join(ClassSection, ClassSection.id == QuizSession.class_section_id)
            .join(SchoolSubject, SchoolSubject.id == ClassSection.subject_id)
            .filter(
                ClassSection.school_id == sid,
                QuizSubmission.score.isnot(None),
                QuizSubmission.max_score.isnot(None),
                QuizSubmission.max_score > 0,
            )
            .group_by(SchoolSubject.id, SchoolSubject.name)
            .all()
        )
        quiz_avg_by_subject = [
            {"subject_name": name, "avg_score_ratio": float(avg) if avg is not None else None}
            for name, avg in rows
        ]
        out.append(
            {
                "school_id": sid,
                "school_name": school.name,
                "school_code": school.code,
                "enrollment_count": enrollment_count,
                "active_teachers": active_teachers,
                "class_section_count": section_count,
                "quiz_avg_by_subject": quiz_avg_by_subject,
                "at_risk": None,
            }
        )
    return {"schools": out}
