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

from openpyxl import load_workbook
from sqlalchemy import and_, func, or_

from app.models.database_models import TutorDailyUsage, User
from app.models.lms_models import (
    Assessment,
    AssessmentAttempt,
    AssessmentQuestion,
    AssessmentReportClassification,
    AttemptAnswer,
    Question,
    StudentStudyGroupAssignment,
    StudentStudyGroupAudit,
    Topic,
)


REPORT_ZONE = ZoneInfo("Asia/Karachi")
GROUPS = ("Controlled", "Uncontrolled")
TEST_TYPES = ("Baseline", "FollowUp")
COHORT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,99}$")
RESULT_HEADERS = [
    "Student Name", "Email", "School", "Group", "Test Type", "Test Date",
    "Test Name", "Total Questions", "Correct Answers", "Incorrect", "Percentage",
    "Minutes Taken", "System Usage Time (Minutes)", "Days Active", "Last Active Date",
    "Activities Completed",
]
TOPIC_HEADERS = ["Student Name", "Email", "Topic", "Correct", "Total", "Score %"]
ANSWER_HEADERS = [
    "Student Name", "Email", "Question No", "Topic", "Question",
    "Student Selected", "Correct Answer", "Is Correct",
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

    usage_rows = db.query(TutorDailyUsage).filter(
        TutorDailyUsage.user_id.in_(candidate_student_ids or {-1}),
        TutorDailyUsage.usage_date >= start_day,
        TutorDailyUsage.usage_date <= end_day,
    ).all()
    usage = defaultdict(list)
    for row in usage_rows:
        usage[row.user_id].append(row)
    activity_attempts = db.query(AssessmentAttempt.student_id, AssessmentAttempt.submitted_at).filter(
        AssessmentAttempt.student_id.in_(candidate_student_ids or {-1}),
        AssessmentAttempt.status == "submitted", AssessmentAttempt.submitted_at >= start_utc,
        AssessmentAttempt.submitted_at < end_utc,
    ).all()
    activity_dates = defaultdict(set)
    activity_counts = defaultdict(int)
    for student_id, submitted_at in activity_attempts:
        activity_dates[student_id].add(_local_day(submitted_at))
        activity_counts[student_id] += 1
    for student_id, rows in usage.items():
        activity_dates[student_id].update(row.usage_date for row in rows if row.active_ms > 0)

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
    files = defaultdict(lambda: {"results": [], "topics": [], "answers": [], "assessment_id": None})
    scores = []
    for attempt, assessment, student, classification, group in selected:
        submitted_day = _local_day(attempt.submitted_at)
        file_key = (group, classification.test_type, submitted_day, assessment.id)
        bucket = files[file_key]
        bucket["assessment_id"] = assessment.id
        answer_map = answers_by_attempt[attempt.id]
        graded = [answer for answer in answer_map.values() if answer.is_correct is not None]
        total = len(graded)
        correct = sum(answer.is_correct is True for answer in graded)
        incorrect = sum(answer.is_correct is False for answer in graded)
        percentage = correct / total if total else None
        if total == 0:
            warnings.add("Some attempts have no stored grading outcomes; their percentages are blank.")
        minutes = None
        if attempt.started_at and attempt.submitted_at and attempt.submitted_at >= attempt.started_at:
            minutes = round((attempt.submitted_at - attempt.started_at).total_seconds() / 60, 2)
        else:
            warnings.add("Some attempts do not have a reliable duration; Minutes Taken is blank.")
        usage_minutes = round(sum(row.active_ms for row in usage[student.id]) / 60000, 2) if usage[student.id] else None
        if not usage[student.id]:
            warnings.add("AI Tutor usage is not tracked for some students in this period; usage cells are blank, not zero.")
        dates = activity_dates[student.id]
        bucket["results"].append([
            student.full_name or student.username, student.useremail, student.school_name or "", group,
            classification.test_type, submitted_day, assessment.title, total, correct, incorrect,
            percentage, minutes, usage_minutes, len(dates) if dates else None, max(dates) if dates else None,
            activity_counts[student.id],
        ])
        if percentage is not None:
            scores.append(percentage)
        topic_totals = defaultdict(lambda: [0, 0])
        for number, question_id in enumerate(ordered_question_ids[attempt.id], 1):
            pair = questions.get(question_id)
            if not pair:
                warnings.add("Some attempt questions no longer exist; missing question rows were omitted.")
                continue
            question, topic = pair
            answer = answer_map.get(question_id)
            topic_name = topic.name if topic else ""
            if not topic:
                warnings.add("Some questions have no topic metadata; Topic is blank and no topic score is invented.")
            if answer and answer.is_correct is not None and topic:
                topic_totals[topic_name][1] += 1
                topic_totals[topic_name][0] += int(answer.is_correct is True)
            bucket["answers"].append([
                student.full_name or student.username, student.useremail, number, topic_name,
                question.question_text or question.question_latex or "",
                _option_value(question, answer.selected_option_index) if answer else "",
                _option_value(question, question.correct_option_index) or question.correct_answer_raw or "",
                "Yes" if answer and answer.is_correct is True else "No" if answer and answer.is_correct is False else "",
            ])
        for topic_name, (topic_correct, topic_total) in sorted(topic_totals.items()):
            bucket["topics"].append([
                student.full_name or student.username, student.useremail, topic_name,
                topic_correct, topic_total, topic_correct / topic_total if topic_total else None,
            ])

    file_list = []
    same_day_sequence = defaultdict(int)
    for key in sorted(files, key=lambda item: (item[2], item[0], item[1], item[3])):
        group, test_type, test_day, _ = key
        sequence_key = (group, test_type, test_day)
        same_day_sequence[sequence_key] += 1
        suffix = f"_{same_day_sequence[sequence_key]}" if same_day_sequence[sequence_key] > 1 else ""
        data = files[key]
        data.update({"group": group, "test_type": test_type, "test_date": test_day,
                     "filename": f"{group}_{test_type}_{test_day.isoformat()}{suffix}.xlsx"})
        file_list.append(data)
    return {
        "filters": {"cohort": cohort, "group": group_scope, "start": start_day.isoformat(), "end": end_day.isoformat(),
                    "test_type": type_scope, "assessment_id": assessment_id, "assessment_title": assessment_title,
                    "school": school,
                    "student_ids": sorted(student_ids)},
        "matching_students": len({row[0].student_id for row in selected}),
        "attempts": len(selected), "average_score": (sum(scores) / len(scores) if scores else None),
        "topic_rows": sum(len(item["topics"]) for item in file_list), "files": file_list,
        "warnings": sorted(warnings),
        "diagnostics": {
            "submitted_candidates": len(raw_candidates),
            "unclassified": unclassified_count,
            "without_effective_group": no_group_count,
            "different_group": other_group_count,
        },
        "attempt_rule": "Latest submitted attempt per student and assessment within the selected Pakistan-time date window.",
        "metric_rules": {
            "system_usage": "Sum of recorded AI Tutor heartbeat milliseconds in the selected date window; blank when no tracking row exists.",
            "days_active": "Distinct Pakistan dates with positive AI Tutor usage or a submitted assessment in the selected window.",
            "last_active": "Latest qualifying activity date in the selected window.",
            "activities_completed": "Count of submitted assessment attempts in the selected window.",
        },
    }


def _safe_text_cells(ws):
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = "s"


def workbook_for_file(file_data) -> io.BytesIO:
    template_name = "controlled.xlsx" if file_data["group"] == "Controlled" else "uncontrolled.xlsx"
    template_path = Path(__file__).resolve().parents[1] / "report_templates" / template_name
    if not template_path.exists():
        raise RuntimeError(f"Report template is missing: {template_name}")
    workbook = load_workbook(template_path)
    expected = {"Results": RESULT_HEADERS, "Topic Scores": TOPIC_HEADERS, "Answers": ANSWER_HEADERS}
    for sheet_name, headers in expected.items():
        ws = workbook[sheet_name]
        actual = [cell.value for cell in ws[1]]
        if actual != headers:
            raise RuntimeError(f"Packaged template has incompatible {sheet_name} columns.")
        if ws.max_row > 1:
            ws.delete_rows(2, ws.max_row - 1)
    for row in file_data["results"]:
        workbook["Results"].append(row)
    for row in file_data["topics"]:
        workbook["Topic Scores"].append(row)
    for row in file_data["answers"]:
        workbook["Answers"].append(row)
    for row_number in range(2, workbook["Results"].max_row + 1):
        workbook["Results"].cell(row_number, 6).number_format = "yyyy-mm-dd"
        workbook["Results"].cell(row_number, 11).number_format = "0.00%"
        workbook["Results"].cell(row_number, 15).number_format = "yyyy-mm-dd"
    for row_number in range(2, workbook["Topic Scores"].max_row + 1):
        workbook["Topic Scores"].cell(row_number, 6).number_format = "0.00%"
    for sheet_name in expected:
        _safe_text_cells(workbook[sheet_name])
    instructions = workbook["Instructions"]
    instructions["A2"] = "One workbook contains all selected tests and students."
    instructions["A3"] = "Results, Topic Scores, and Answers are merged into their matching worksheets."
    instructions["A6"] = "Use Group, Test Type, Test Date, Test Name, and School columns to filter each test."
    instructions["A10"] = "This workbook is ready to review, filter, or share as one report."
    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


def export_report(report) -> tuple[io.BytesIO, str, str]:
    files = report["files"]
    if not files:
        raise ValueError("No matching submitted attempts were found. Nothing was exported.")
    groups = sorted({item["group"] for item in files})
    merged = {
        "group": groups[0] if len(groups) == 1 else "Controlled",
        "results": [row for item in files for row in item["results"]],
        "topics": [row for item in files for row in item["topics"]],
        "answers": [row for item in files for row in item["answers"]],
    }
    group_label = groups[0] if len(groups) == 1 else "Both_Groups"
    start = report["filters"]["start"]
    end = report["filters"]["end"]
    date_label = start if start == end else f"{start}_to_{end}"
    filename = f"IqbalAI_Performance_{group_label}_{date_label}.xlsx"
    return workbook_for_file(merged), filename, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
