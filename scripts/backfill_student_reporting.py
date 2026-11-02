"""Fail-closed one-time bootstrap for the DIL reporting pilot.

Dry-run is the default.  The two diagnostic export workbooks are treated as
the membership source of truth; no school- or activity-based inference is
performed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from openpyxl import load_workbook
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from app.config import Config
from app.models.database_models import User
from app.models.lms_models import (
    Assessment,
    AssessmentAttempt,
    AssessmentReportClassification,
    StudentStudyGroupAssignment,
)
from app.services.student_reporting_service import assign_students, classify_assessment


REPORT_ZONE = ZoneInfo("Asia/Karachi")
COHORT = "default"
EXPECTED_COUNTS = {"Controlled": 58, "Uncontrolled": 98}
CLASSIFICATIONS = {
    3: ("grade 9", "Baseline", None),
    25: ("Practice Quiz 1 - Mathematics IX", "FollowUp", 1),
    26: ("Practice Quiz 1 - Mathematics IX", "FollowUp", 1),
    27: ("Practice Quiz 1 - Mathematics IX", "FollowUp", 1),
    28: ("Practice Quiz 1 - Mathematics IX", "FollowUp", 1),
    43: ("Practice Quiz 2 - Mathematics IX", "FollowUp", 2),
    44: ("Practice Quiz 2 - Mathematics IX", "FollowUp", 2),
    45: ("Practice Quiz 2 - Mathematics IX", "FollowUp", 2),
    46: ("Practice Quiz 2 - Mathematics IX", "FollowUp", 2),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_roster(path: Path, expected_group: str, *, require_user_id: bool = True) -> list[dict]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    if "Results" not in workbook.sheetnames:
        raise ValueError(f"{path.name}: Results sheet is missing.")
    sheet = workbook["Results"]
    headers = [str(value or "").strip() for value in next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))]
    required = {"Email", "Group"}
    if require_user_id:
        required.add("DB User ID")
    if not required.issubset(headers):
        raise ValueError(f"{path.name}: required columns are missing.")
    email_index, group_index = (headers.index(name) for name in ("Email", "Group"))
    id_index = headers.index("DB User ID") if require_user_id else None
    rows = []
    accepted_groups = {expected_group.lower(), "non-controlled" if expected_group == "Uncontrolled" else expected_group.lower()}
    for row_number, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), 2):
        email = str(row[email_index] or "").strip().lower()
        if not email:
            continue
        source_group = str(row[group_index] or "").strip().lower()
        if source_group not in accepted_groups:
            raise ValueError(f"{path.name}: unexpected Group on row {row_number}.")
        try:
            user_id = int(row[id_index]) if id_index is not None else None
        except (TypeError, ValueError):
            raise ValueError(f"{path.name}: invalid DB User ID on row {row_number}.")
        rows.append({"email": email, "user_id": user_id})
    duplicates = [email for email, count in Counter(item["email"] for item in rows).items() if count > 1]
    if duplicates:
        raise ValueError(f"{path.name}: {len(duplicates)} duplicate email(s) found.")
    if len(rows) != EXPECTED_COUNTS[expected_group]:
        raise ValueError(
            f"{path.name}: expected {EXPECTED_COUNTS[expected_group]} students, found {len(rows)}."
        )
    return rows


def _effective_utc(value: str) -> datetime:
    try:
        effective_day = date.fromisoformat(value)
    except ValueError:
        raise ValueError("--effective-date must use YYYY-MM-DD.")
    local_value = datetime.combine(effective_day, time.min, tzinfo=REPORT_ZONE)
    return local_value.astimezone(timezone.utc).replace(tzinfo=None)


def _read_supplemental(path: Path, known_groups: dict[str, str]) -> tuple[dict[str, list[dict]], int]:
    """Use explicit groups only for emails absent from the canonical diagnostic rosters."""
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook.active
    headers = [str(value or "").strip() for value in next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))]
    required = {"Email", "Group"}
    if not required.issubset(headers):
        raise ValueError(f"{path.name}: Email/Group columns are missing.")
    email_index, group_index = headers.index("Email"), headers.index("Group")
    values = defaultdict(set)
    ignored_conflicts = set()
    for row in sheet.iter_rows(min_row=2, values_only=True):
        email = str(row[email_index] or "").strip().lower()
        group = str(row[group_index] or "").strip().lower().replace("-", "")
        if not email:
            continue
        if group not in {"controlled", "uncontrolled", "noncontrolled"}:
            raise ValueError(f"{path.name}: unsupported group for a supplemental email.")
        normalized_group = "Controlled" if group == "controlled" else "Uncontrolled"
        if email in known_groups:
            if known_groups[email] != normalized_group:
                ignored_conflicts.add(email)
            continue
        values[email].add(normalized_group)
    conflicts = [email for email, groups in values.items() if len(groups) != 1]
    if conflicts:
        raise ValueError(f"{path.name}: {len(conflicts)} supplemental email(s) have conflicting groups.")
    rows = {"Controlled": [], "Uncontrolled": []}
    for email, groups in values.items():
        rows[next(iter(groups))].append({"email": email, "user_id": None})
    return rows, len(ignored_conflicts)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--controlled", type=Path, required=True)
    parser.add_argument("--uncontrolled", type=Path, required=True)
    parser.add_argument("--controlled-check", type=Path)
    parser.add_argument("--uncontrolled-check", type=Path)
    parser.add_argument("--supplemental", type=Path, help="Newer explicit roster; only previously unseen emails are used")
    parser.add_argument("--effective-date", required=True, help="Initial membership date in Pakistan time")
    parser.add_argument("--apply", action="store_true", help="Commit the validated plan; default is dry-run")
    return parser


def main() -> int:
    args = _parser().parse_args()
    controlled_rows = _read_roster(args.controlled, "Controlled")
    uncontrolled_rows = _read_roster(args.uncontrolled, "Uncontrolled")
    # Keep canonical lists independent: supplemental merging must not mutate
    # their provenance batches used later during the transactional apply.
    rosters = {"Controlled": list(controlled_rows), "Uncontrolled": list(uncontrolled_rows)}
    controlled_emails = {item["email"] for item in controlled_rows}
    uncontrolled_emails = {item["email"] for item in uncontrolled_rows}
    if controlled_emails & uncontrolled_emails:
        raise ValueError("Controlled and Uncontrolled rosters overlap.")
    if args.controlled_check:
        check = {item["email"] for item in _read_roster(args.controlled_check, "Controlled", require_user_id=False)}
        if check != controlled_emails:
            raise ValueError("Controlled integrity-check workbook does not match the canonical roster.")
    if args.uncontrolled_check:
        check = {item["email"] for item in _read_roster(args.uncontrolled_check, "Uncontrolled", require_user_id=False)}
        if check != uncontrolled_emails:
            raise ValueError("Uncontrolled integrity-check workbook does not match the canonical roster.")
    supplemental = {"Controlled": [], "Uncontrolled": []}
    supplemental_counts = {"Controlled": 0, "Uncontrolled": 0}
    supplemental_ignored_conflicts = 0
    if args.supplemental:
        known_groups = {email: "Controlled" for email in controlled_emails}
        known_groups.update({email: "Uncontrolled" for email in uncontrolled_emails})
        supplemental, supplemental_ignored_conflicts = _read_supplemental(args.supplemental, known_groups)
        for group, rows in supplemental.items():
            rosters[group].extend(rows)
            supplemental_counts[group] = len(rows)
        controlled_emails.update(item["email"] for item in supplemental["Controlled"])
        uncontrolled_emails.update(item["email"] for item in supplemental["Uncontrolled"])
        if supplemental_counts != {"Controlled": 3, "Uncontrolled": 0}:
            raise ValueError(f"Unexpected supplemental roster counts: {supplemental_counts}.")
        if supplemental_ignored_conflicts != 44:
            raise ValueError(
                f"Expected 44 ignored supplemental conflicts with canonical rows, found {supplemental_ignored_conflicts}."
            )

    effective_from = _effective_utc(args.effective_date)
    all_emails = controlled_emails | uncontrolled_emails
    engine = create_engine(Config.SQLALCHEMY_DATABASE_URI)
    db = sessionmaker(bind=engine, autoflush=False)()
    try:
        users = db.query(User).filter(func.lower(User.useremail).in_(all_emails)).all()
        by_email = defaultdict(list)
        for user in users:
            by_email[user.useremail.strip().lower()].append(user)
        missing = all_emails - set(by_email)
        ambiguous = {email for email, values in by_email.items() if len(values) != 1}
        id_mismatches = []
        non_students = []
        for rows in rosters.values():
            for source in rows:
                values = by_email.get(source["email"], [])
                if len(values) == 1:
                    if source["user_id"] is not None and values[0].id != source["user_id"]:
                        id_mismatches.append(source["email"])
                    if values[0].role != "student":
                        non_students.append(source["email"])
        if missing or ambiguous or id_mismatches or non_students:
            raise ValueError(
                "Roster resolution failed: "
                f"missing={len(missing)}, ambiguous={len(ambiguous)}, "
                f"id_mismatches={len(id_mismatches)}, non_students={len(non_students)}."
            )

        user_ids = {by_email[email][0].id for email in all_emails}
        histories = db.query(StudentStudyGroupAssignment).filter(
            StudentStudyGroupAssignment.cohort_key == COHORT,
            StudentStudyGroupAssignment.student_id.in_(user_ids),
        ).all()
        history_by_student = defaultdict(list)
        for row in histories:
            history_by_student[row.student_id].append(row)
        assignment_new = assignment_exact = 0
        assignment_conflicts = []
        for group, rows in rosters.items():
            for source in rows:
                student_id = by_email[source["email"]][0].id
                history = history_by_student[student_id]
                exact_rows = [row for row in history if (
                    row.group_name == group and row.effective_from == effective_from and row.effective_to is None
                )]
                if len(history) == 1 and len(exact_rows) == 1:
                    assignment_exact += 1
                elif history:
                    assignment_conflicts.append(student_id)
                else:
                    assignment_new += 1

        assessments = {row.id: row for row in db.query(Assessment).filter(Assessment.id.in_(CLASSIFICATIONS)).all()}
        classification_conflicts = []
        classification_new = classification_exact = 0
        for assessment_id, (title, test_type, order) in CLASSIFICATIONS.items():
            assessment = assessments.get(assessment_id)
            if not assessment or assessment.title != title:
                classification_conflicts.append(assessment_id)
                continue
            existing = db.query(AssessmentReportClassification).filter_by(
                cohort_key=COHORT, assessment_id=assessment_id
            ).one_or_none()
            if existing and existing.test_type == test_type and existing.followup_order == order:
                classification_exact += 1
            elif existing:
                classification_conflicts.append(assessment_id)
            else:
                classification_new += 1
        other_baseline = db.query(AssessmentReportClassification).filter(
            AssessmentReportClassification.cohort_key == COHORT,
            AssessmentReportClassification.test_type == "Baseline",
            AssessmentReportClassification.assessment_id != 3,
        ).first()
        if other_baseline:
            classification_conflicts.append(other_baseline.assessment_id)
        if assignment_conflicts or classification_conflicts:
            raise ValueError(
                f"Backfill conflicts: assignments={len(assignment_conflicts)}, "
                f"classifications={sorted(set(classification_conflicts))}."
            )

        monday_start = datetime(2026, 11, 1, 19, 0)
        monday_end = datetime(2026, 11, 2, 19, 0)
        monday_rows = db.query(AssessmentAttempt.id, func.lower(User.useremail)).join(
            User, User.id == AssessmentAttempt.student_id
        ).filter(
            AssessmentAttempt.assessment_id.in_((43, 44, 45, 46)),
            AssessmentAttempt.status == "submitted",
            AssessmentAttempt.submitted_at >= monday_start,
            AssessmentAttempt.submitted_at < monday_end,
        ).all()
        monday_membership = Counter(
            "Uncontrolled" if email in uncontrolled_emails
            else "Controlled" if email in controlled_emails
            else "Not in supplied rosters"
            for _, email in monday_rows
        )

        plan = {
            "mode": "apply" if args.apply else "dry-run",
            "cohort": COHORT,
            "effective_date_pakistan": args.effective_date,
            "effective_from_utc": effective_from.isoformat(),
            "source_hashes": {
                "controlled": _sha256(args.controlled),
                "uncontrolled": _sha256(args.uncontrolled),
                **({"supplemental": _sha256(args.supplemental)} if args.supplemental else {}),
            },
            "rosters": {
                "Controlled": len(rosters["Controlled"]),
                "Uncontrolled": len(rosters["Uncontrolled"]),
                "supplemental_new": supplemental_counts,
                "supplemental_canonical_conflicts_ignored": supplemental_ignored_conflicts,
                "overlap": 0,
            },
            "user_resolution": {"matched": len(all_emails), "missing": 0, "ambiguous": 0, "id_mismatches": 0},
            "assignments": {"new": assignment_new, "exact": assignment_exact, "conflicts": 0},
            "classifications": {"new": classification_new, "exact": classification_exact, "conflicts": 0},
            "monday_practice_quiz_2_attempts": {
                "total": len(monday_rows),
                "by_canonical_membership": dict(sorted(monday_membership.items())),
            },
        }
        if len(monday_rows) != 88:
            raise ValueError(
                "Monday Practice Quiz 2 roster validation failed: "
                f"total={len(monday_rows)}, membership={dict(monday_membership)}."
            )
        print(json.dumps(plan, indent=2))
        if not args.apply:
            db.rollback()
            return 0

        admin = db.query(User).filter(User.role == "admin").order_by(User.id).first()
        if not admin:
            raise ValueError("No admin account exists for audit attribution.")
        apply_batches = [
            ("Controlled", controlled_rows, args.controlled.name),
            ("Uncontrolled", uncontrolled_rows, args.uncontrolled.name),
        ]
        if supplemental["Controlled"]:
            apply_batches.append(("Controlled", supplemental["Controlled"], args.supplemental.name))
        if supplemental["Uncontrolled"]:
            apply_batches.append(("Uncontrolled", supplemental["Uncontrolled"], args.supplemental.name))
        for group, rows, source_name in apply_batches:
            ids = [by_email[source["email"]][0].id for source in rows]
            assign_students(
                db, ids, group, COHORT, admin.id, source="import",
                note=(
                    f"DIL pilot roster backfill from {source_name}; "
                    f"initial membership effective {args.effective_date} Pakistan time"
                ),
                effective_from=effective_from,
            )
        for assessment_id, (_, test_type, order) in CLASSIFICATIONS.items():
            classify_assessment(db, assessment_id, COHORT, test_type, order, admin.id)
        db.commit()
        print("BACKFILL_APPLIED")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
