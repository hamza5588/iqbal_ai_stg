"""Controlled/Uncontrolled assignments and template-compatible performance reports."""

from __future__ import annotations

import csv
import io
import json
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import zipfile

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.hyperlink import Hyperlink
from sqlalchemy import and_, func, or_

from app.models.database_models import SystemSettings, TutorDailyUsage, User
from app.models.lms_models import (
    Assessment,
    AssessmentAttempt,
    AssessmentQuestion,
    AssessmentReportClassification,
    AttemptAnswer,
    Question,
    QuestionTaxonomy,
    StudentStudyGroupAssignment,
    StudentStudyGroupAudit,
    Topic,
)
from app.services.lms import time_tracking_service


REPORT_ZONE = ZoneInfo("Asia/Karachi")
GROUPS = ("Controlled", "Uncontrolled")
TEST_TYPES = ("Baseline", "FollowUp")
COHORT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,99}$")
# One sheet · one row per student per question — matches login Analytics (IqbalAI_Dashboard_v23)
# and the Controlled_/Uncontrolled_ Baseline|FollowUp sample workbooks.
QUIZ_HEADERS = [
    "Student Name",
    "Email",
    "School",
    "Group",
    "Test Type",
    "Test Date",
    "Test Name",
    "Total Questions",
    "Correct Answers",
    "Incorrect",
    "Percentage",
    "Quiz Time Taken (Minutes)",
    "Quiz Page Opens",
    "Time Away from dil.iqbalai (Minutes)",
    "Learning Chat Time (since previous quiz)",
    "AI-Guided Practice Time (since previous quiz)",
    "Total Time on dil.iqbalai (Active Minutes, since previous quiz)",
    "Days Learned",
    "dil.iqbalai Student ID",
    "Daily Learning Detail",
    "Question No",
    "Domain",
    "Topic",
    "Question",
    "Student Selected",
    "Correct Answer",
    "Is Correct",
    "Question Time (Seconds)",
]
# Back-compat aliases used by older unit tests / imports.
RESULT_HEADERS = QUIZ_HEADERS[:12]
TOPIC_HEADERS = ["Student Name", "Email", "Domain", "Topic", "Correct", "Total", "Score %"]
ANSWER_HEADERS = [
    "Student Name", "Email", "Question No", "Domain", "Topic", "Question",
    "Student Selected", "Correct Answer", "Is Correct", "Question Time (Seconds)",
]
LEARNING_USAGE_HEADERS = [
    "Date", "Student ID", "Student Name", "School Name", "AI Tutor Time", "Active Minutes", "Coverage",
]


def normalize_cohort(value) -> str:
    cohort = str(value or "default").strip()
    if not COHORT_RE.fullmatch(cohort):
        raise ValueError("Cohort must be 1-100 letters, numbers, spaces, dots, underscores or dashes.")
    return cohort


def normalize_group(value, *, allow_unassigned: bool = False):
    if value is None or str(value).strip().lower() in {"", "unassigned", "none"}:
        if allow_unassigned:
            return None
        raise ValueError("Group must be Controlled or Uncontrolled.")
    key = str(value).strip().lower().replace("-", "")
    aliases = {"controlled": "Controlled", "uncontrolled": "Uncontrolled", "noncontrolled": "Uncontrolled"}
    if key not in aliases:
        raise ValueError("Group must be Controlled or Uncontrolled.")
    return aliases[key]


def normalize_test_type(value):
    key = str(value or "").strip().lower().replace("-", "")
    aliases = {"baseline": "Baseline", "followup": "FollowUp"}
    if key not in aliases:
        raise ValueError("Test type must be Baseline or FollowUp.")
    return aliases[key]


def _utc_now() -> datetime:
    return datetime.utcnow()


def _local_day(value: datetime) -> date:
    return value.replace(tzinfo=timezone.utc).astimezone(REPORT_ZONE).date()


def _utc_bounds(start_value, end_value) -> tuple[date, date, datetime, datetime]:
    try:
        start_day = date.fromisoformat(str(start_value))
        end_day = date.fromisoformat(str(end_value or start_value))
    except (TypeError, ValueError):
        raise ValueError("Use valid report dates in YYYY-MM-DD format.")
    if end_day < start_day:
        raise ValueError("End date cannot be before start date.")
    if (end_day - start_day).days > 366:
        raise ValueError("A report range cannot exceed 367 days.")
    start_local = datetime.combine(start_day, time.min, tzinfo=REPORT_ZONE)
    end_local = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=REPORT_ZONE)
    return (
        start_day,
        end_day,
        start_local.astimezone(timezone.utc).replace(tzinfo=None),
        end_local.astimezone(timezone.utc).replace(tzinfo=None),
    )


def parse_initial_effective_date(value) -> datetime | None:
    """Convert an optional Pakistan calendar date to the persisted UTC-naive form."""
    if value in (None, ""):
        return None
    try:
        effective_day = date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ValueError("Use a valid membership effective date in YYYY-MM-DD format.")
    today = datetime.now(REPORT_ZONE).date()
    if effective_day > today:
        raise ValueError("Membership effective date cannot be in the future.")
    local_midnight = datetime.combine(effective_day, time.min, tzinfo=REPORT_ZONE)
    return local_midnight.astimezone(timezone.utc).replace(tzinfo=None)


def _active_assignments(db, cohort: str, now: datetime | None = None):
    at = now or _utc_now()
    return db.query(StudentStudyGroupAssignment).filter(
        StudentStudyGroupAssignment.cohort_key == cohort,
        StudentStudyGroupAssignment.effective_from <= at,
        or_(StudentStudyGroupAssignment.effective_to.is_(None), StudentStudyGroupAssignment.effective_to > at),
    ).all()


def list_student_groups(db, args) -> dict:
    cohort = normalize_cohort(args.get("cohort"))
    try:
        page = max(1, int(args.get("page", 1)))
        per_page = min(100, max(1, int(args.get("per_page", 50))))
    except (TypeError, ValueError):
        raise ValueError("Invalid page value.")
    assignments = {item.student_id: item for item in _active_assignments(db, cohort)}
    query = db.query(User).filter(User.role == "student")
    search = str(args.get("search") or "").strip()
    school = str(args.get("school") or "").strip()
    group_filter = str(args.get("group") or "all").strip().lower()
    if search:
        like = f"%{search}%"
        query = query.filter(or_(User.full_name.ilike(like), User.useremail.ilike(like), User.username.ilike(like)))
    if school:
        query = query.filter(User.school_name == school)
    if group_filter in {"controlled", "uncontrolled"}:
        wanted = {sid for sid, item in assignments.items() if item.group_name.lower() == group_filter}
        query = query.filter(User.id.in_(wanted or {-1}))
    elif group_filter == "unassigned":
        query = query.filter(~User.id.in_(set(assignments) or {-1}))
    elif group_filter != "all":
        raise ValueError("Invalid group filter.")
    total = query.count()
    students = query.order_by(func.lower(func.coalesce(User.full_name, User.username)), User.id).offset((page - 1) * per_page).limit(per_page).all()
    all_students = db.query(User.id, User.school_name).filter(User.role == "student").all()
    counts = {"Controlled": 0, "Uncontrolled": 0, "Unassigned": 0}
    for student_id, _ in all_students:
        item = assignments.get(student_id)
        counts[item.group_name if item else "Unassigned"] += 1
    schools = sorted({row.school_name for row in db.query(User.school_name).filter(User.role == "student", User.school_name.isnot(None)).all() if row.school_name})
    return {
        "cohort": cohort,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "counts": counts,
        "schools": schools,
        "students": [
            {
                "id": student.id,
                "name": student.full_name or student.username,
                "email": student.useremail,
                "school": student.school_name or "",
                "group": assignments.get(student.id).group_name if student.id in assignments else None,
                "effective_from": assignments.get(student.id).effective_from.isoformat() if student.id in assignments else None,
            }
            for student in students
        ],
    }


def assign_students(
    db,
    student_ids: Iterable,
    group,
    cohort,
    actor_id,
    *,
    source="manual",
    note=None,
    effective_from: datetime | None = None,
) -> dict:
    cohort = normalize_cohort(cohort)
    new_group = normalize_group(group, allow_unassigned=True)
    if source not in {"manual", "import"}:
        raise ValueError("Invalid assignment source.")
    try:
        ids = sorted({int(value) for value in student_ids})
    except (TypeError, ValueError):
        raise ValueError("Student IDs must be integers.")
    if not ids:
        raise ValueError("Select at least one student.")
    if len(ids) > 1000:
        raise ValueError("At most 1,000 students can be assigned at once.")
    valid_ids = {row.id for row in db.query(User.id).filter(User.id.in_(ids), User.role == "student").all()}
    missing = sorted(set(ids) - valid_ids)
    if missing:
        raise ValueError(f"Unknown student IDs: {', '.join(map(str, missing[:10]))}.")
    now = _utc_now()
    if effective_from is not None:
        # Backfills apply multiple roster batches in one transaction. Make prior
        # batches visible even when the production session has autoflush=False.
        db.flush()
        if new_group is None:
            raise ValueError("A historical effective date can only be used for an initial group assignment.")
        if effective_from > now:
            raise ValueError("Membership effective date cannot be in the future.")
        history = db.query(StudentStudyGroupAssignment).filter(
            StudentStudyGroupAssignment.student_id.in_(ids),
            StudentStudyGroupAssignment.cohort_key == cohort,
        ).with_for_update().all()
        history_by_id = defaultdict(list)
        for row in history:
            history_by_id[row.student_id].append(row)
        exact_ids = set()
        conflicts = []
        for student_id in ids:
            rows = history_by_id[student_id]
            exact = any(
                row.group_name == new_group
                and row.effective_from == effective_from
                and row.effective_to is None
                for row in rows
            )
            if exact:
                exact_ids.add(student_id)
            elif rows:
                conflicts.append(student_id)
        if conflicts:
            raise ValueError(
                "Historical effective dates are only allowed for students with no group history. "
                f"Conflicting student IDs: {', '.join(map(str, conflicts[:10]))}."
            )
        changed = 0
        for student_id in ids:
            if student_id in exact_ids:
                continue
            db.add(StudentStudyGroupAssignment(
                student_id=student_id, cohort_key=cohort, group_name=new_group,
                effective_from=effective_from, assigned_by=actor_id, source=source,
            ))
            db.add(StudentStudyGroupAudit(
                student_id=student_id, cohort_key=cohort, old_group_name=None,
                new_group_name=new_group, changed_by=actor_id, changed_at=now,
                source=source, note=(str(note).strip()[:512] if note else None),
            ))
            changed += 1
        return {
            "changed": changed,
            "unchanged": len(ids) - changed,
            "group": new_group,
            "cohort": cohort,
            "effective_from": effective_from.isoformat(),
        }
    active = db.query(StudentStudyGroupAssignment).filter(
        StudentStudyGroupAssignment.student_id.in_(ids),
        StudentStudyGroupAssignment.cohort_key == cohort,
        StudentStudyGroupAssignment.effective_from <= now,
        or_(StudentStudyGroupAssignment.effective_to.is_(None), StudentStudyGroupAssignment.effective_to > now),
    ).with_for_update().all()
    active_by_id = {row.student_id: row for row in active}
    changed = 0
    for student_id in ids:
        old = active_by_id.get(student_id)
        old_group = old.group_name if old else None
        if old_group == new_group:
            continue
        if old:
            old.effective_to = now
        if new_group:
            db.add(StudentStudyGroupAssignment(
                student_id=student_id, cohort_key=cohort, group_name=new_group,
                effective_from=now, assigned_by=actor_id, source=source,
            ))
        db.add(StudentStudyGroupAudit(
            student_id=student_id, cohort_key=cohort, old_group_name=old_group,
            new_group_name=new_group, changed_by=actor_id, changed_at=now,
            source=source, note=(str(note).strip()[:512] if note else None),
        ))
        changed += 1
    return {"changed": changed, "unchanged": len(ids) - changed, "group": new_group, "cohort": cohort}


def _import_rows(file_storage) -> list[dict]:
    filename = str(getattr(file_storage, "filename", "") or "")
    content = file_storage.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024:
        raise ValueError("Import file must be 5 MB or smaller.")
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError("CSV must use UTF-8 encoding.")
        raw = list(csv.DictReader(io.StringIO(text)))
    elif suffix in {".xlsx", ".xlsm"}:
        try:
            ws = load_workbook(io.BytesIO(content), read_only=True, data_only=True).active
        except Exception as exc:
            raise ValueError("The Excel file could not be read.") from exc
        values = list(ws.iter_rows(values_only=True))
        if not values:
            return []
        headers = [str(value or "").strip() for value in values[0]]
        raw = [dict(zip(headers, row)) for row in values[1:] if any(value not in (None, "") for value in row)]
    else:
        raise ValueError("Upload a CSV or XLSX file.")
    if len(raw) > 5000:
        raise ValueError("Import files may contain at most 5,000 rows.")
    rows = []
    for number, raw_row in enumerate(raw, 2):
        normalized = {re.sub(r"[^a-z0-9]", "", str(key).lower()): value for key, value in raw_row.items()}
        rows.append({
            "row": number,
            "student_id": normalized.get("studentid") or normalized.get("id"),
            "email": str(normalized.get("email") or normalized.get("studentemail") or "").strip().lower(),
            "group": normalized.get("group") or normalized.get("studentgroup"),
        })
    return rows


def preview_group_import(db, file_storage) -> dict:
    rows = _import_rows(file_storage)
    ids = {int(row["student_id"]) for row in rows if str(row.get("student_id") or "").strip().isdigit()}
    emails = {row["email"] for row in rows if row["email"]}
    students = db.query(User).filter(User.role == "student", or_(User.id.in_(ids or {-1}), func.lower(User.useremail).in_(emails or {""}))).all()
    by_id = {student.id: student for student in students}
    by_email = defaultdict(list)
    for student in students:
        by_email[student.useremail.strip().lower()].append(student)
    seen_students = set()
    output = []
    for row in rows:
        errors = []
        student = None
        raw_id = str(row.get("student_id") or "").strip()
        if raw_id:
            if not raw_id.isdigit() or int(raw_id) not in by_id:
                errors.append("Student ID not found")
            else:
                student = by_id[int(raw_id)]
        if row["email"]:
            matches = by_email.get(row["email"], [])
            if len(matches) > 1:
                errors.append("Email is ambiguous")
            elif not matches:
                errors.append("Email not found")
            elif student and student.id != matches[0].id:
                errors.append("Student ID and email identify different students")
            elif not student:
                student = matches[0]
        if not raw_id and not row["email"]:
            errors.append("Student ID or email is required")
        try:
            group = normalize_group(row.get("group"))
        except ValueError as exc:
            group = None
            errors.append(str(exc))
        if student and student.id in seen_students:
            errors.append("Duplicate student in import")
        if student:
            seen_students.add(student.id)
        output.append({
            "row": row["row"], "student_id": student.id if student else None,
            "name": (student.full_name or student.username) if student else "",
            "email": student.useremail if student else row["email"], "group": group,
            "status": "ready" if student and group and not errors else "error", "errors": errors,
        })
    ready = sum(row["status"] == "ready" for row in output)
    return {"rows": output, "ready": ready, "errors": len(output) - ready, "total": len(output)}


def classify_assessment(db, assessment_id, cohort, test_type, followup_order, actor_id) -> dict:
    cohort = normalize_cohort(cohort)
    assessment = db.query(Assessment).filter(Assessment.id == int(assessment_id)).one_or_none()
    if not assessment:
        raise ValueError("Assessment not found.")
    existing = db.query(AssessmentReportClassification).filter_by(assessment_id=assessment.id, cohort_key=cohort).one_or_none()
    if test_type in (None, ""):
        if existing:
            db.delete(existing)
        return {"assessment_id": assessment.id, "title": assessment.title, "test_type": None, "followup_order": None}
    test_type = normalize_test_type(test_type)
    if test_type == "Baseline":
        duplicate = db.query(AssessmentReportClassification).filter(
            AssessmentReportClassification.cohort_key == cohort,
            AssessmentReportClassification.test_type == "Baseline",
            AssessmentReportClassification.assessment_id != assessment.id,
        ).first()
        if duplicate:
            raise ValueError("This cohort already has a Baseline assessment. Reclassify it first.")
        order = None
    else:
        if followup_order in (None, ""):
            if existing and existing.test_type == "FollowUp" and existing.followup_order:
                order = existing.followup_order
            else:
                maximum = db.query(func.max(AssessmentReportClassification.followup_order)).filter(
                    AssessmentReportClassification.cohort_key == cohort,
                    AssessmentReportClassification.test_type == "FollowUp",
                ).scalar() or 0
                order = maximum + 1
        else:
            try:
                order = int(followup_order)
            except (TypeError, ValueError):
                raise ValueError("FollowUp order must be a positive number.")
            if order < 1:
                raise ValueError("FollowUp order must be a positive number.")
    row = existing
    if not row:
        row = AssessmentReportClassification(assessment_id=assessment.id, cohort_key=cohort)
        db.add(row)
    row.test_type = test_type
    row.followup_order = order
    row.classified_by = actor_id
    row.updated_at = _utc_now()
    return {"assessment_id": assessment.id, "title": assessment.title, "test_type": test_type, "followup_order": order}


def reporting_options(db, cohort_value) -> dict:
    cohort = normalize_cohort(cohort_value)
    classifications = {row.assessment_id: row for row in db.query(AssessmentReportClassification).filter_by(cohort_key=cohort).all()}
    assessments = db.query(Assessment).order_by(Assessment.created_at.desc(), Assessment.id.desc()).all()
    active = {row.student_id: row.group_name for row in _active_assignments(db, cohort)}
    students = db.query(User).filter(User.role == "student", User.id.in_(set(active) or {-1})).order_by(func.lower(func.coalesce(User.full_name, User.username))).all()
    return {
        "cohort": cohort,
        "assessments": [
            {"id": item.id, "title": item.title, "status": item.status,
             "test_type": classifications[item.id].test_type if item.id in classifications else None,
             "followup_order": classifications[item.id].followup_order if item.id in classifications else None}
            for item in assessments
        ],
        "schools": sorted({item.school_name for item in students if item.school_name}),
        "students": [{"id": item.id, "name": item.full_name or item.username, "email": item.useremail,
                      "school": item.school_name or "", "group": active[item.id]} for item in students],
    }


def _group_at(assignments, student_id: int, when: datetime):
    matches = [row for row in assignments.get(student_id, []) if row.effective_from <= when and (row.effective_to is None or row.effective_to > when)]
    return max(matches, key=lambda row: (row.effective_from, row.id)).group_name if matches else None


def _option_value(question: Question, index):
    if index is None:
        return ""
    try:
        options = json.loads(question.options_json or "[]")
        option = options[int(index)]
        if isinstance(option, dict):
            return str(option.get("label") or option.get("text") or "")
        return str(option)
    except (ValueError, TypeError, IndexError, json.JSONDecodeError):
        return ""


def _domain_topic_labels(question: Question, topic: Topic | None, taxonomy: QuestionTaxonomy | None):
    """Prefer printed Domain/Topic labels; fall back to LMS topic name as Topic."""
    domain = ""
    topic_name = ""
    if taxonomy:
        domain = (taxonomy.printed_domain or "").strip()
        topic_name = (taxonomy.printed_topic or "").strip()
    if not topic_name and topic:
        topic_name = (topic.name or "").strip()
    if not domain and topic and topic_name and topic.name and topic.name.strip() != topic_name:
        domain = topic.name.strip()
    return domain, topic_name


def _tracking_start_day(db) -> date | None:
    row = db.query(SystemSettings).filter(SystemSettings.key == "tutor_usage_started_at").one_or_none()
    if not row or not row.value:
        return None
    try:
        started = datetime.fromisoformat(str(row.value).replace("Z", ""))
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return started.astimezone(REPORT_ZONE).date()


def _ms_to_hms(ms: int | None) -> str | None:
    if ms is None:
        return None
    total_sec = max(0, int(ms) // 1000)
    hours, rem = divmod(total_sec, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"


def _learning_usage_rows(db, students: list[User], start_day: date, end_day: date) -> list[list]:
    """Legacy daily AI Tutor rows (still returned in preview diagnostics / older consumers)."""
    if not students:
        return []
    ids = [student.id for student in students]
    usage_map = {
        (row.user_id, row.usage_date): int(row.active_ms or 0)
        for row in db.query(TutorDailyUsage).filter(
            TutorDailyUsage.user_id.in_(ids),
            TutorDailyUsage.usage_date >= start_day,
            TutorDailyUsage.usage_date <= end_day,
        ).all()
    }
    track_start = _tracking_start_day(db)
    rows = []
    day = start_day
    while day <= end_day:
        for student in sorted(students, key=lambda item: ((item.full_name or item.username or "").lower(), item.id)):
            tracked = track_start is not None and day >= track_start
            ms = usage_map.get((student.id, day)) if tracked else None
            if tracked and ms is None:
                ms = 0
            coverage = "Not tracked" if not tracked else ("Partial day" if day == track_start else "Tracked")
            rows.append([
                day,
                student.id,
                student.full_name or student.username,
                student.school_name or "Not provided",
                timedelta(seconds=ms // 1000) if ms is not None else None,
                round(ms / 60000, 2) if ms is not None else None,
                coverage,
            ])
        day += timedelta(days=1)
    return rows


def _usage_map_for_students(db, student_ids: Iterable[int], start_day: date, end_day: date) -> dict[tuple[int, date], int]:
    ids = list(student_ids)
    if not ids:
        return {}
    return {
        (row.user_id, row.usage_date): int(row.active_ms or 0)
        for row in db.query(TutorDailyUsage).filter(
            TutorDailyUsage.user_id.in_(ids),
            TutorDailyUsage.usage_date >= start_day,
            TutorDailyUsage.usage_date <= end_day,
        ).all()
    }


def _learning_since_previous(
    *,
    student_id: int,
    quiz_day: date,
    previous_day: date | None,
    usage_map: dict[tuple[int, date], int],
    track_start: date | None,
    learning_chat_seconds: int = 0,
    ai_guided_practice_seconds: int = 0,
) -> tuple[str | None, str | None, float | None, int | None, str | None]:
    """Aggregate dil.iqbalai usage between the previous quiz and this quiz day.

    Learning Chat / AI-Guided Practice H:M:S come from per-question dwell sums
    (``time_tracking_service``). Daily total minutes still come from ``tutor_daily_usage.active_ms``.
    """
    chat_hms = time_tracking_service.seconds_to_hms(learning_chat_seconds) or "0:00:00"
    practice_hms = time_tracking_service.seconds_to_hms(ai_guided_practice_seconds) or "0:00:00"
    if track_start is None:
        # Still expose dwell-based chat/practice even when heartbeat tracking has not started.
        return chat_hms, practice_hms, None, None, None
    window_start = previous_day + timedelta(days=1) if previous_day else track_start
    if window_start < track_start:
        window_start = track_start
    if window_start > quiz_day:
        return chat_hms, practice_hms, 0.0, 0, None

    detail_parts: list[str] = []
    counted_ms = 0
    days_learned = 0
    day = window_start
    while day <= quiz_day:
        tracked = day >= track_start
        if not tracked:
            day += timedelta(days=1)
            continue
        ms = int(usage_map.get((student_id, day), 0))
        minutes = round(ms / 60000, 2)
        if day == quiz_day:
            detail_parts.append(
                f"{day.isoformat()}: chat {chat_hms}, practice {practice_hms}, total {minutes} [quiz day, not counted]"
            )
        else:
            if minutes > 0:
                days_learned += 1
                counted_ms += ms
            suffix = " (partial day)" if day == track_start else ""
            if minutes or day == track_start:
                detail_parts.append(f"{day.isoformat()}: total {minutes}{suffix}")
            else:
                detail_parts.append(f"{day.isoformat()}: 0")
        day += timedelta(days=1)

    total_minutes = round(counted_ms / 60000, 2)
    return chat_hms, practice_hms, total_minutes, days_learned, "; ".join(detail_parts) if detail_parts else None


def build_report(db, filters) -> dict:
    cohort = normalize_cohort(filters.get("cohort"))
    start_day, end_day, start_utc, end_utc = _utc_bounds(filters.get("start"), filters.get("end"))
    group_scope = str(filters.get("group") or "all_assigned").strip().lower()
    if group_scope not in {"controlled", "uncontrolled", "both", "all_assigned"}:
        raise ValueError("Invalid student group selection.")
    type_scope = str(filters.get("test_type") or "all").strip().lower().replace("-", "")
    if type_scope not in {"all", "baseline", "followup"}:
        raise ValueError("Invalid test type selection.")
    assessment_value = filters.get("assessment_id")
    assessment_id = None
    assessment_title = None
    try:
        if isinstance(assessment_value, str) and assessment_value.startswith("title:"):
            assessment_title = assessment_value[6:].strip()
            if not assessment_title or len(assessment_title) > 512:
                raise ValueError
        elif assessment_value not in (None, "", "all"):
            assessment_id = int(assessment_value)
        student_ids = {int(value) for value in (filters.get("student_ids") or [])}
    except (TypeError, ValueError):
        raise ValueError("Invalid assessment or student selection.")
    school = str(filters.get("school") or "").strip()

    query = db.query(AssessmentAttempt, Assessment, User, AssessmentReportClassification).join(
        Assessment, Assessment.id == AssessmentAttempt.assessment_id
    ).join(User, User.id == AssessmentAttempt.student_id).outerjoin(
        AssessmentReportClassification,
        and_(AssessmentReportClassification.assessment_id == AssessmentAttempt.assessment_id,
             AssessmentReportClassification.cohort_key == cohort),
    ).filter(
        AssessmentAttempt.status == "submitted",
        AssessmentAttempt.submitted_at.isnot(None),
        AssessmentAttempt.submitted_at >= start_utc,
        AssessmentAttempt.submitted_at < end_utc,
        User.role == "student",
    )
    all_assessments = assessment_id is None and assessment_title is None
    if all_assessments:
        query = query.filter(
            Assessment.created_at >= start_utc,
            Assessment.created_at < end_utc,
        )
    if assessment_id:
        query = query.filter(AssessmentAttempt.assessment_id == assessment_id)
    elif assessment_title:
        query = query.filter(Assessment.title == assessment_title)
    if school:
        query = query.filter(User.school_name == school)
    if student_ids:
        query = query.filter(User.id.in_(student_ids))
    raw_candidates = query.order_by(AssessmentAttempt.submitted_at.desc(), AssessmentAttempt.id.desc()).all()
    unclassified_count = sum(classification is None for _, _, _, classification in raw_candidates)
    candidates = [
        row for row in raw_candidates
        if row[3] is not None and (
            type_scope == "all"
            or row[3].test_type == ("Baseline" if type_scope == "baseline" else "FollowUp")
        )
    ]
    candidate_student_ids = {attempt.student_id for attempt, _, _, _ in candidates}
    assignment_rows = db.query(StudentStudyGroupAssignment).filter(
        StudentStudyGroupAssignment.cohort_key == cohort,
        StudentStudyGroupAssignment.student_id.in_(candidate_student_ids or {-1}),
        StudentStudyGroupAssignment.effective_from < end_utc,
        or_(StudentStudyGroupAssignment.effective_to.is_(None), StudentStudyGroupAssignment.effective_to >= start_utc),
    ).all()
    assignments = defaultdict(list)
    for row in assignment_rows:
        assignments[row.student_id].append(row)

    selected = []
    seen = set()
    no_group_count = 0
    other_group_count = 0
    for attempt, assessment, student, classification in candidates:
        group = _group_at(assignments, attempt.student_id, attempt.submitted_at)
        if not group:
            no_group_count += 1
            continue
        if group_scope == "controlled" and group != "Controlled":
            other_group_count += 1
            continue
        if group_scope == "uncontrolled" and group != "Uncontrolled":
            other_group_count += 1
            continue
        # Latest submitted attempt per student and assessment in the chosen window.
        key = (attempt.student_id, attempt.assessment_id)
        if key in seen:
            continue
        seen.add(key)
        selected.append((attempt, assessment, student, classification, group))

    attempt_ids = [row[0].id for row in selected]
    answer_rows = db.query(AttemptAnswer).filter(AttemptAnswer.attempt_id.in_(attempt_ids or {-1})).all()
    answers_by_attempt = defaultdict(dict)
    for answer in answer_rows:
        answers_by_attempt[answer.attempt_id][answer.question_id] = answer
    assessment_ids = {row[0].assessment_id for row in selected}
    aq_rows = db.query(AssessmentQuestion).filter(AssessmentQuestion.assessment_id.in_(assessment_ids or {-1})).order_by(AssessmentQuestion.sort_order, AssessmentQuestion.id).all()
    default_question_ids = defaultdict(list)
    for row in aq_rows:
        default_question_ids[row.assessment_id].append(row.question_id)
    all_question_ids = set()
    ordered_question_ids = {}
    for attempt, *_ in selected:
        ids = None
        if attempt.question_ids_json:
            try:
                ids = [int(value) for value in json.loads(attempt.question_ids_json)]
            except (TypeError, ValueError, json.JSONDecodeError):
                ids = None
        ids = ids or default_question_ids[attempt.assessment_id] or list(answers_by_attempt[attempt.id])
        ordered_question_ids[attempt.id] = ids
        all_question_ids.update(ids)
    question_rows = db.query(Question, Topic).outerjoin(Topic, Topic.id == Question.topic_id).filter(Question.id.in_(all_question_ids or {-1})).all()
    questions = {question.id: (question, topic) for question, topic in question_rows}
    taxonomy_rows = db.query(QuestionTaxonomy).filter(QuestionTaxonomy.question_id.in_(all_question_ids or {-1})).all()
    taxonomies = {row.question_id: row for row in taxonomy_rows}

    warnings = set()
    if not raw_candidates:
        warnings.add("No submitted assessment attempts were found for the selected Pakistan-time date and filters.")
    if unclassified_count:
        warnings.add(
            f"{unclassified_count} submitted attempt(s) were excluded because their assessment is not configured as Baseline or FollowUp."
        )
    if no_group_count:
        warnings.add(
            f"{no_group_count} submitted attempt(s) were excluded because the student had no group assignment effective on the test date."
        )
    if other_group_count and not selected:
        warnings.add(f"{other_group_count} classified attempt(s) belonged to a different student group on the test date.")

    # Previous quiz day per student (for "since previous quiz" learning columns).
    prior_days: dict[int, list[date]] = defaultdict(list)
    for attempt, _, student, _, _ in selected:
        prior_days[student.id].append(_local_day(attempt.submitted_at))
    for student_id, days in prior_days.items():
        prior_days[student_id] = sorted(set(days))

    usage_student_ids = {student.id for _, _, student, _, _ in selected}
    usage_map = _usage_map_for_students(db, usage_student_ids, start_day - timedelta(days=60), end_day)
    track_start = _tracking_start_day(db)

    # Cache Learning Chat / AI-Guided Practice dwell by (student_id, previous_day, quiz_day).
    activity_cache: dict[tuple[int, date | None, date], dict] = {}
    quiz_timing_by_attempt: dict[int, dict] = {}
    time_tracking_students: dict[str, dict] = {}

    files = defaultdict(lambda: {
        "rows": [], "results": [], "topics": [], "answers": [], "assessment_id": None,
        "assessment_ids": [], "title": "", "time_tracking": [],
    })
    scores = []
    for attempt, assessment, student, classification, group in selected:
        submitted_day = _local_day(attempt.submitted_at)
        # One workbook per group + test type + day + quiz title.
        # Same-titled copies from different teachers/classes merge into one Excel
        # (e.g. four "Practice Quiz 4" FollowUp sections → one Uncontrolled_FollowUp_DATE.xlsx).
        title_key = (assessment.title or "").strip() or f"assessment:{assessment.id}"
        file_key = (group, classification.test_type, submitted_day, title_key)
        bucket = files[file_key]
        if bucket["assessment_id"] is None:
            bucket["assessment_id"] = assessment.id
        if assessment.id not in bucket["assessment_ids"]:
            bucket["assessment_ids"].append(assessment.id)
        bucket["title"] = assessment.title or bucket["title"] or ""
        answer_map = answers_by_attempt[attempt.id]
        graded = [answer for answer in answer_map.values() if answer.is_correct is not None]
        total = len(graded)
        correct = sum(answer.is_correct is True for answer in graded)
        incorrect = sum(answer.is_correct is False for answer in graded)
        percentage = correct / total if total else None
        if total == 0:
            warnings.add("Some attempts have no stored grading outcomes; their percentages are blank.")

        quiz_timing = time_tracking_service.quiz_attempt_timing(
            attempt, answer_map.values(), ordered_question_ids[attempt.id]
        )
        quiz_timing_by_attempt[attempt.id] = quiz_timing
        minutes = quiz_timing.get("wall_clock_minutes")
        if minutes is None:
            warnings.add("Some attempts do not have a reliable duration; Quiz Time Taken is blank.")
        time_away = None
        if getattr(attempt, "offline_credit_seconds", None):
            time_away = round(int(attempt.offline_credit_seconds or 0) / 60, 2)

        earlier = [day for day in prior_days.get(student.id, []) if day < submitted_day]
        previous_day = earlier[-1] if earlier else None
        activity_key = (student.id, previous_day, submitted_day)
        if activity_key not in activity_cache:
            activity_cache[activity_key] = time_tracking_service.activity_times_for_window(
                db, [student.id], previous_day, submitted_day
            ).get(student.id) or {
                "learning_chat_seconds": 0,
                "ai_guided_practice_seconds": 0,
                "learning_chat_hms": "0:00:00",
                "ai_guided_practice_hms": "0:00:00",
                "learning_chat": {"total_time_spent_seconds": 0, "sessions": [], "questions": []},
                "ai_guided_practice": {
                    "total_time_spent_seconds": 0,
                    "bank_questions": [],
                    "aigp_questions": [],
                    "questions": [],
                },
            }
        activity = activity_cache[activity_key]
        chat_hms, practice_hms, total_learn_min, days_learned, daily_detail = _learning_since_previous(
            student_id=student.id,
            quiz_day=submitted_day,
            previous_day=previous_day,
            usage_map=usage_map,
            track_start=track_start,
            learning_chat_seconds=int(activity.get("learning_chat_seconds") or 0),
            ai_guided_practice_seconds=int(activity.get("ai_guided_practice_seconds") or 0),
        )

        shared = [
            student.full_name or student.username,
            student.useremail,
            student.school_name or "",
            group,
            classification.test_type,
            submitted_day,
            assessment.title,
            total,
            correct,
            incorrect,
            percentage,
            minutes,
            None,  # Quiz Page Opens — not tracked per attempt yet
            time_away,
            chat_hms,
            practice_hms,
            total_learn_min,
            days_learned,
            student.id,
            daily_detail,
        ]
        # Keep a one-row-per-student summary for preview counts / older tests.
        bucket["results"].append(shared[:12])
        if percentage is not None:
            scores.append(percentage)

        student_tt = time_tracking_students.setdefault(
            str(student.id),
            {
                "student_id": student.id,
                "email": student.useremail,
                "name": student.full_name or student.username,
                "quizzes": [],
                "learning_chat_seconds": 0,
                "ai_guided_practice_seconds": 0,
            },
        )
        student_tt["quizzes"].append(
            {
                "attempt_id": attempt.id,
                "assessment_id": assessment.id,
                "test_type": classification.test_type,
                "test_date": submitted_day.isoformat(),
                "wall_clock_seconds": quiz_timing.get("wall_clock_seconds"),
                "wall_clock_minutes": quiz_timing.get("wall_clock_minutes"),
                "question_dwell_total_seconds": quiz_timing.get("question_dwell_total_seconds"),
                "questions": quiz_timing.get("questions") or [],
                "learning_chat_seconds": int(activity.get("learning_chat_seconds") or 0),
                "ai_guided_practice_seconds": int(activity.get("ai_guided_practice_seconds") or 0),
                "learning_chat": activity.get("learning_chat"),
                "ai_guided_practice": activity.get("ai_guided_practice"),
            }
        )
        student_tt["learning_chat_seconds"] = max(
            int(student_tt["learning_chat_seconds"] or 0),
            int(activity.get("learning_chat_seconds") or 0),
        )
        student_tt["ai_guided_practice_seconds"] = max(
            int(student_tt["ai_guided_practice_seconds"] or 0),
            int(activity.get("ai_guided_practice_seconds") or 0),
        )
        bucket["time_tracking"].append(student_tt["quizzes"][-1])

        topic_totals = defaultdict(lambda: [0, 0])
        question_rows_written = 0
        q_time_by_number = {
            int(item["question_number"]): int(item.get("time_spent_seconds") or 0)
            for item in (quiz_timing.get("questions") or [])
        }
        for number, question_id in enumerate(ordered_question_ids[attempt.id], 1):
            pair = questions.get(question_id)
            if not pair:
                warnings.add("Some attempt questions no longer exist; missing question rows were omitted.")
                continue
            question, topic = pair
            answer = answer_map.get(question_id)
            domain_name, topic_name = _domain_topic_labels(question, topic, taxonomies.get(question_id))
            if not topic_name and not domain_name:
                warnings.add("Some questions have no domain/topic metadata; Domain/Topic are blank and no topic score is invented.")
            if answer and answer.is_correct is not None and topic_name:
                topic_totals[(domain_name, topic_name)][1] += 1
                topic_totals[(domain_name, topic_name)][0] += int(answer.is_correct is True)
            selected_label = _option_value(question, answer.selected_option_index) if answer else ""
            correct_label = (
                _option_value(question, question.correct_option_index) or question.correct_answer_raw or ""
            )
            is_correct = (
                "Yes" if answer and answer.is_correct is True
                else "No" if answer and answer.is_correct is False
                else ""
            )
            q_text = question.question_text or question.question_latex or ""
            q_seconds = q_time_by_number.get(number, int(getattr(answer, "time_spent_seconds", 0) or 0) if answer else 0)
            bucket["answers"].append([
                student.full_name or student.username, student.useremail, f"Q{number}", domain_name, topic_name,
                q_text, selected_label, correct_label, is_correct, q_seconds,
            ])
            bucket["rows"].append(shared + [
                f"Q{number}", domain_name, topic_name, q_text, selected_label, correct_label, is_correct, q_seconds,
            ])
            question_rows_written += 1
        if question_rows_written == 0:
            # Still emit a student row so Analytics can see the attempt score.
            bucket["rows"].append(shared + ["", "", "", "", "", "", "", None])
        for (domain_name, topic_name), (topic_correct, topic_total) in sorted(topic_totals.items()):
            bucket["topics"].append([
                student.full_name or student.username, student.useremail, domain_name, topic_name,
                topic_correct, topic_total, topic_correct / topic_total if topic_total else None,
            ])

    file_list = []
    same_day_sequence = defaultdict(int)
    for key in sorted(files, key=lambda item: (item[2], item[0], item[1], item[3])):
        group, test_type, test_day, title_key = key
        sequence_key = (group, test_type, test_day)
        same_day_sequence[sequence_key] += 1
        # Suffix only when different quiz titles share the same group/type/day.
        suffix = f"_{same_day_sequence[sequence_key]}" if same_day_sequence[sequence_key] > 1 else ""
        data = files[key]
        sheet_kind = "Diagnostic" if test_type == "Baseline" else "Quiz"
        data.update({
            "group": group,
            "test_type": test_type,
            "test_date": test_day,
            "sheet_name": f"{sheet_kind} {test_day.strftime('%d-%b')}",
            "filename": f"{group}_{test_type}_{test_day.isoformat()}{suffix}.xlsx",
            "merge_key": title_key,
        })
        file_list.append(data)
    selected_students = []
    seen_student_ids = set()
    for attempt, _, student, _, _ in selected:
        if student.id in seen_student_ids:
            continue
        seen_student_ids.add(student.id)
        selected_students.append(student)
    learning_usage = _learning_usage_rows(db, selected_students, start_day, end_day)

    for student_data in time_tracking_students.values():
        student_data["learning_chat_hms"] = time_tracking_service.seconds_to_hms(
            student_data.get("learning_chat_seconds")
        )
        student_data["ai_guided_practice_hms"] = time_tracking_service.seconds_to_hms(
            student_data.get("ai_guided_practice_seconds")
        )

    return {
        "filters": {"cohort": cohort, "group": group_scope, "start": start_day.isoformat(), "end": end_day.isoformat(),
                    "test_type": type_scope, "assessment_id": assessment_id, "assessment_title": assessment_title,
                    "school": school,
                    "student_ids": sorted(student_ids)},
        "matching_students": len({row[0].student_id for row in selected}),
        "attempts": len(selected), "average_score": (sum(scores) / len(scores) if scores else None),
        "topic_rows": sum(len(item["topics"]) for item in file_list), "files": file_list,
        "learning_usage": learning_usage,
        "time_tracking": {
            "students": list(time_tracking_students.values()),
            "quiz_attempts": list(quiz_timing_by_attempt.values()),
            "sources": {
                "quiz_wall_clock": "assessment_attempts.started_at → submitted_at",
                "quiz_per_question": "attempt_answers.time_spent_seconds",
                "learning_chat": "deficiency_chat_sessions.questions_json[*].time_spent_seconds",
                "ai_guided_practice": (
                    "lms_guided_practice_attempts.time_spent_seconds + "
                    "lms_aigp_session_items.time_spent_seconds"
                ),
            },
        },
        "warnings": sorted(warnings),
        "diagnostics": {
            "submitted_candidates": len(raw_candidates),
            "unclassified": unclassified_count,
            "without_effective_group": no_group_count,
            "different_group": other_group_count,
        },
        "attempt_rule": (
            "All tests: assessment creation and attempt submission must both fall within the selected Pakistan-time date window."
            if all_assessments else
            "Selected assessment: latest submitted attempt per student within the selected Pakistan-time date window."
        ),
        "metric_rules": {
            "quiz_minutes": "Minutes from attempt started_at to submitted_at for this quiz.",
            "question_time_seconds": "Per-MCQ dwell from attempt_answers.time_spent_seconds.",
            "learning_chat_time": (
                "Sum of Learning Chat per-question dwell (questions_json.time_spent_seconds) "
                "since the student's previous quiz."
            ),
            "ai_guided_practice_time": (
                "Sum of bank Guided Practice + AI Guided Practice per-question dwell "
                "since the student's previous quiz."
            ),
            "learning_usage": (
                "Total Time on dil.iqbalai still comes from tutor_daily_usage.active_ms; "
                "Learning Chat / AI-Guided Practice columns use dwell sums above."
            ),
            "domain_topic": "Prefer printed Domain/Topic from question taxonomy; otherwise use the linked LMS topic name.",
            "export_format": (
                "One quiz title per workbook (same-day teacher/class copies merge), "
                "one sheet, one row per student per question — "
                "Analytics-compatible columns plus Question Time (Seconds)."
            ),
        },
    }


def _safe_text_cells(ws):
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = "s"


# ── Readable, linked sheets (Summary / Students / Answers / Topic Scores / Daily Learning) ──
# The long sheet above stays as-is (last tab) for the Analytics upload; the dashboard reads only
# that sheet. Everything below is derived from the same rows so the numbers always agree.
SUMMARY_SHEET = "Summary"
STUDENTS_SHEET = "Students"
ANSWERS_SHEET = "Answers"
TOPICS_SHEET = "Topic Scores"
DAILY_SHEET = "Daily Learning"
_HEADER_FILL = PatternFill("solid", fgColor="1F3864")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_LINK_FONT = Font(color="0563C1", underline="single")
_TITLE_FONT = Font(bold=True, size=14, color="1F3864")
_GOOD_FILL = PatternFill("solid", fgColor="E2EFDA")
_BAD_FILL = PatternFill("solid", fgColor="FCE4E4")
_DAY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s*:\s*(.*)$")
_DAY_TOTAL_RE = re.compile(r"total\s+([\d.]+)")


def _style_header(ws, row_number: int):
    for cell in ws[row_number]:
        if cell.value is not None:
            cell.fill = _HEADER_FILL
            cell.font = _HEADER_FONT
            cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[row_number].height = 32


def _link(cell, sheet: str, row: int, text=None):
    if text is not None:
        cell.value = text
    # In-workbook link (location, not an external target) so Excel/LibreOffice jump between tabs.
    cell.hyperlink = Hyperlink(ref=cell.coordinate, location=f"'{sheet}'!A{row}", display=str(cell.value or ""))
    cell.font = _LINK_FONT


def _widths(ws, widths: dict):
    for letter, width in widths.items():
        ws.column_dimensions[letter].width = width


def _back_link(ws, title: str):
    ws["A1"] = title
    ws["A1"].font = _TITLE_FONT
    _link(ws["A2"], SUMMARY_SHEET, 1, "← Back to Summary")


def _num(value):
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_daily_detail(text) -> list[tuple[str, float | None, str]]:
    """'2026-10-23: chat 0:20:23, practice 0:03:00, total 20.38 (partial day); 2026-10-24: 0' →
    [(day, minutes, note)]."""
    days = []
    for part in str(text or "").split(";"):
        match = _DAY_RE.match(part.strip())
        if not match:
            continue
        body = match.group(2)
        total = _DAY_TOTAL_RE.search(body)
        minutes = 0.0 if re.match(r"^0\b", body) else (float(total.group(1)) if total else None)
        notes = re.findall(r"\(([^)]*)\)|\[([^\]]*)\]", body)
        days.append((match.group(1), minutes, "; ".join(a or b for a, b in notes)))
    return days


def _collect_students(rows) -> list[dict]:
    """Group the long rows into one record per student (first appearance order)."""
    students: dict[str, dict] = {}
    for row in rows:
        values = list(row) + [None] * max(0, len(QUIZ_HEADERS) - len(row))
        name, email = values[0], values[1]
        if name in (None, "") and email in (None, ""):
            continue
        key = str(email or name).strip().lower()
        record = students.get(key)
        if record is None:
            record = students[key] = {"base": values[:20], "answers": []}
        if values[20] not in (None, ""):
            record["answers"].append(values[20:28])
    return list(students.values())


def _add_readable_sheets(workbook, raw_title: str, rows, file_data):
    students = _collect_students(rows or [])
    summary = workbook.create_sheet(SUMMARY_SHEET)
    st_ws = workbook.create_sheet(STUDENTS_SHEET)
    an_ws = workbook.create_sheet(ANSWERS_SHEET)
    tp_ws = workbook.create_sheet(TOPICS_SHEET)
    has_daily = any(s["base"][19] for s in students)
    dl_ws = workbook.create_sheet(DAILY_SHEET) if has_daily else None

    # Row positions are planned first so every sheet can link to every other.
    student_row = {i: 5 + i for i in range(len(students))}
    answer_row, topic_row, daily_row = {}, {}, {}
    next_answer, next_topic = 5, 5
    topic_rows_by_student = []
    for i, student in enumerate(students):
        answer_row[i] = next_answer
        next_answer += max(1, len(student["answers"]))
        agg: dict[tuple, list] = {}
        for answer in student["answers"]:
            key = (answer[1] or "", answer[2] or "")
            bucket = agg.setdefault(key, [0, 0, 0])
            bucket[1] += 1
            if str(answer[6] or "").strip().lower() in ("yes", "true", "1", "✓"):
                bucket[0] += 1
            bucket[2] += _num(answer[7]) or 0
        topic_rows_by_student.append(agg)
        topic_row[i] = next_topic
        next_topic += max(1, len(agg))
        daily_row[i] = 5 + i

    # ── Students ──
    _back_link(st_ws, "Students — one row per student")
    st_headers = [
        "Student Name", "Email", "School", "Test Name", "Total Questions", "Correct", "Incorrect",
        "Score %", "Quiz Time (Minutes)", "Quiz Page Opens", "Time Away (Minutes)",
        "Learning Chat Time", "AI-Guided Practice Time", "Total Active Minutes", "Days Learned",
        "Student ID", "Answers", "Topic Scores",
    ] + (["Daily Learning"] if dl_ws else [])
    st_ws.append([])
    st_ws.append(st_headers)
    _style_header(st_ws, 4)
    for i, student in enumerate(students):
        b = student["base"]
        r = student_row[i]
        st_ws.append([b[0], b[1], b[2], b[6], b[7], b[8], b[9], b[10], b[11], b[12], b[13],
                      b[14], b[15], b[16], b[17], b[18]])
        _link(st_ws.cell(r, 1), ANSWERS_SHEET, answer_row[i])
        _link(st_ws.cell(r, 17), ANSWERS_SHEET, answer_row[i], "View answers →")
        _link(st_ws.cell(r, 18), TOPICS_SHEET, topic_row[i], "View topics →")
        if dl_ws:
            _link(st_ws.cell(r, 19), DAILY_SHEET, daily_row[i], "View days →")
        st_ws.cell(r, 8).number_format = "0.0%"
        score = _num(b[10])
        if score is not None:
            st_ws.cell(r, 8).fill = _GOOD_FILL if score >= 0.5 else _BAD_FILL
    st_ws.freeze_panes = "B5"
    if students:
        st_ws.auto_filter.ref = f"A4:P{4 + len(students)}"
    _widths(st_ws, {"A": 24, "B": 32, "C": 18, "D": 28, "L": 14, "M": 14, "Q": 16, "R": 15, "S": 14})

    # ── Answers ──
    _back_link(an_ws, "Answers — every question each student answered")
    an_ws.append([])
    an_ws.append(["Student Name", "Question No", "Domain", "Topic", "Question", "Student Selected",
                  "Correct Answer", "Is Correct", "Question Time (Seconds)"])
    _style_header(an_ws, 4)
    for i, student in enumerate(students):
        name = student["base"][0]
        if not student["answers"]:
            an_ws.append([name, "(no answers recorded)"])
            _link(an_ws.cell(answer_row[i], 1), STUDENTS_SHEET, student_row[i])
            continue
        for offset, answer in enumerate(student["answers"]):
            r = answer_row[i] + offset
            an_ws.append([name] + list(answer))
            _link(an_ws.cell(r, 1), STUDENTS_SHEET, student_row[i])
            an_ws.cell(r, 5).alignment = Alignment(wrap_text=True, vertical="top")
            correct = str(answer[6] or "").strip().lower() in ("yes", "true", "1", "✓")
            an_ws.cell(r, 8).fill = _GOOD_FILL if correct else _BAD_FILL
    an_ws.freeze_panes = "B5"
    if next_answer > 5:
        an_ws.auto_filter.ref = f"A4:I{next_answer - 1}"
    _widths(an_ws, {"A": 24, "B": 11, "C": 18, "D": 30, "E": 60, "F": 12, "G": 12, "H": 10, "I": 12})

    # ── Topic Scores ──
    _back_link(tp_ws, "Topic Scores — per student, per topic")
    tp_ws.append([])
    tp_ws.append(["Student Name", "Domain", "Topic", "Correct", "Total", "Score %"])
    _style_header(tp_ws, 4)
    class_topics: dict[tuple, list] = {}
    for i, student in enumerate(students):
        name = student["base"][0]
        agg = topic_rows_by_student[i]
        if not agg:
            tp_ws.append([name, "(no answers recorded)"])
            _link(tp_ws.cell(topic_row[i], 1), STUDENTS_SHEET, student_row[i])
            continue
        for offset, ((domain, topic), (correct, total, _)) in enumerate(agg.items()):
            r = topic_row[i] + offset
            pct = correct / total if total else None
            tp_ws.append([name, domain or None, topic or None, correct, total, pct])
            _link(tp_ws.cell(r, 1), STUDENTS_SHEET, student_row[i])
            tp_ws.cell(r, 6).number_format = "0.0%"
            if pct is not None:
                tp_ws.cell(r, 6).fill = _GOOD_FILL if pct >= 0.5 else _BAD_FILL
            bucket = class_topics.setdefault((domain, topic), [0, 0, set()])
            bucket[0] += correct
            bucket[1] += total
            bucket[2].add(i)
    tp_ws.freeze_panes = "B5"
    if next_topic > 5:
        tp_ws.auto_filter.ref = f"A4:F{next_topic - 1}"
    _widths(tp_ws, {"A": 24, "B": 20, "C": 34})

    # ── Daily Learning (pivot: student × day, active minutes) ──
    if dl_ws:
        _back_link(dl_ws, "Daily Learning — active minutes on dil.iqbalai per day")
        parsed = [_parse_daily_detail(s["base"][19]) for s in students]
        days = sorted({d for p in parsed for d, _, _ in p})
        dl_ws.append([])
        dl_ws.append(["Student Name", "Days Learned", "Total Active Minutes"] + days)
        _style_header(dl_ws, 4)
        for i, student in enumerate(students):
            by_day = {d: (m, note) for d, m, note in parsed[i]}
            r = daily_row[i]
            dl_ws.append([student["base"][0], student["base"][17], student["base"][16]]
                         + [by_day.get(d, (None, ""))[0] for d in days])
            _link(dl_ws.cell(r, 1), STUDENTS_SHEET, student_row[i])
            for col, day in enumerate(days, start=4):
                minutes, note = by_day.get(day, (None, ""))
                cell = dl_ws.cell(r, col)
                if minutes:
                    cell.fill = _GOOD_FILL
                if note:
                    cell.comment = Comment(note, "IqbalAI")
        dl_ws.freeze_panes = "D5"
        _widths(dl_ws, {"A": 24, "B": 12, "C": 14})
        for col in range(4, 4 + len(days)):
            dl_ws.column_dimensions[get_column_letter(col)].width = 11

    # ── Summary (first tab) ──
    scores = [s for s in (_num(st["base"][10]) for st in students) if s is not None]
    test_names = sorted({str(st["base"][6]) for st in students if st["base"][6]})
    schools = sorted({str(st["base"][2]) for st in students if st["base"][2]})
    summary["A1"] = "Student Performance Report"
    summary["A1"].font = Font(bold=True, size=16, color="1F3864")
    test_day = file_data.get("test_date")
    info = [
        ("Group", file_data.get("group")),
        ("Test Type", file_data.get("test_type")),
        ("Test Date", test_day.isoformat() if hasattr(test_day, "isoformat") else test_day),
        ("Test Name", ", ".join(test_names) or None),
        ("Schools", ", ".join(schools) or None),
        ("Students", len(students)),
        ("Average Score", sum(scores) / len(scores) if scores else None),
        ("Students ≥ 50%", sum(1 for s in scores if s >= 0.5)),
        ("Students < 50%", sum(1 for s in scores if s < 0.5)),
    ]
    for offset, (label, value) in enumerate(info, start=3):
        summary.cell(offset, 1, label).font = Font(bold=True)
        summary.cell(offset, 2, value)
    summary.cell(9, 2).number_format = "0.0%"

    row = 14
    summary.cell(row, 1, "Sheets in this workbook").font = _TITLE_FONT
    sheet_index = [
        (STUDENTS_SHEET, "One row per student: score, time, learning usage"),
        (ANSWERS_SHEET, "Every question each student answered (green = correct)"),
        (TOPICS_SHEET, "Score per student per topic"),
    ]
    if dl_ws:
        sheet_index.append((DAILY_SHEET, "Active minutes per student per day"))
    sheet_index.append((raw_title, "Raw data in one sheet. Upload this file to Analytics as it is; do not edit this sheet."))
    for offset, (name, desc) in enumerate(sheet_index, start=row + 1):
        _link(summary.cell(offset, 1), name, 1, name)
        summary.cell(offset, 2, desc)

    row = row + len(sheet_index) + 3
    summary.cell(row, 1, "Topic performance (all students)").font = _TITLE_FONT
    header_row = row + 1
    for col, label in enumerate(["Domain", "Topic", "Students", "Correct", "Total", "Score %"], start=1):
        summary.cell(header_row, col, label)
    _style_header(summary, header_row)
    for offset, ((domain, topic), (correct, total, who)) in enumerate(
        sorted(class_topics.items(), key=lambda kv: (kv[1][0] / kv[1][1]) if kv[1][1] else 0), start=header_row + 1
    ):
        pct = correct / total if total else None
        for col, value in enumerate([domain or None, topic or None, len(who), correct, total, pct], start=1):
            summary.cell(offset, col, value)
        summary.cell(offset, 6).number_format = "0.0%"
        if pct is not None:
            summary.cell(offset, 6).fill = _GOOD_FILL if pct >= 0.5 else _BAD_FILL
    _widths(summary, {"A": 30, "B": 40, "C": 10, "D": 10, "E": 10, "F": 10})

    for sheet in (summary, st_ws, an_ws, tp_ws) + ((dl_ws,) if dl_ws else ()):
        for sheet_row in sheet.iter_rows():
            for cell in sheet_row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"  # neutralize formula injection in user text

    # Tab order: readable sheets first, raw Analytics sheet last; open on Summary.
    raw = workbook[raw_title]
    order = [summary, st_ws, an_ws, tp_ws] + ([dl_ws] if dl_ws else []) + [raw]
    workbook._sheets = order
    workbook.active = 0
    raw.sheet_properties.tabColor = "A6A6A6"
    summary.sheet_properties.tabColor = "1F3864"


def _sheet_title(file_data) -> str:
    name = str(file_data.get("sheet_name") or "Quiz").strip() or "Quiz"
    return name[:31]


def workbook_for_file(file_data) -> io.BytesIO:
    """Build one Analytics-compatible quiz workbook (single sheet, long rows)."""
    workbook = Workbook()
    ws = workbook.active
    ws.title = _sheet_title(file_data)
    ws.append(list(QUIZ_HEADERS))
    rows = file_data.get("rows")
    if rows is None:
        # Allow older callers that only supply results/answers to still export.
        rows = []
        by_email = {row[1]: row for row in (file_data.get("results") or [])}
        for answer in file_data.get("answers") or []:
            base = by_email.get(answer[1]) or [
                answer[0], answer[1], "", file_data.get("group") or "",
                file_data.get("test_type") or "", file_data.get("test_date"),
                "", None, None, None, None, None,
            ]
            pad = list(base[:12]) + [None] * (20 - 12)
            if len(pad) < 20:
                pad.extend([None] * (20 - len(pad)))
            answer_tail = list(answer[2:])
            # Expect QNo..Is Correct (+ optional Question Time).
            while len(answer_tail) < 8:
                answer_tail.append(None)
            rows.append(pad[:20] + answer_tail[:8])
    for row in rows:
        values = list(row)
        if len(values) < len(QUIZ_HEADERS):
            values.extend([None] * (len(QUIZ_HEADERS) - len(values)))
        ws.append(values[: len(QUIZ_HEADERS)])
    for row_number in range(2, ws.max_row + 1):
        ws.cell(row_number, 6).number_format = "yyyy-mm-dd"  # Test Date
        ws.cell(row_number, 11).number_format = "0.00%"  # Percentage
    _safe_text_cells(ws)
    _style_header(ws, 1)
    ws.freeze_panes = "B2"
    _add_readable_sheets(workbook, ws.title, rows, file_data)
    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


def export_report(report) -> tuple[io.BytesIO, str, str]:
    files = report["files"]
    if not files:
        raise ValueError("No matching submitted attempts were found. Nothing was exported.")
    if len(files) == 1:
        item = files[0]
        return (
            workbook_for_file(item),
            item["filename"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    # Analytics expects one file per quiz named Group_TestType_YYYY-MM-DD.xlsx
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        used_names: set[str] = set()
        for item in files:
            name = item["filename"]
            if name in used_names:
                stem = Path(name).stem
                name = f"{stem}_{len(used_names) + 1}.xlsx"
            used_names.add(name)
            archive.writestr(name, workbook_for_file(item).getvalue())
    buffer.seek(0)
    start = report["filters"]["start"]
    end = report["filters"]["end"]
    date_label = start if start == end else f"{start}_to_{end}"
    groups = sorted({item["group"] for item in files})
    group_label = groups[0] if len(groups) == 1 else "Both_Groups"
    filename = f"IqbalAI_Performance_{group_label}_{date_label}.zip"
    return buffer, filename, "application/zip"
