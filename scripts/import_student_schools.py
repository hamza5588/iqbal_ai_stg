"""Import school names by exact email, checking student names before any write.

Input: JSON list with student_name, email, school_name (exported from the workbook).
Default is a dry run. Call import_schools with apply=True and a private backup path
to add the nullable users.school_name column and update all matched rows atomically.
The caller supplies a SQLAlchemy engine; this module never loads credentials.
"""
import json
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import inspect, text


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def prepare_matches(rows, users):
    by_email = defaultdict(list)
    for user in users:
        by_email[normalized(user["useremail"])].append(user)
    seen = set()
    matches = []
    for number, row in enumerate(rows, start=2):
        email = normalized(row.get("email"))
        name = normalized(row.get("student_name"))
        school = str(row.get("school_name") or "").strip()
        if not email or not name or not school or len(school) > 255:
            raise ValueError(f"Invalid required value at worksheet row {number}")
        if email in seen:
            raise ValueError(f"Duplicate email at worksheet row {number}")
        seen.add(email)
        candidates = by_email[email]
        if len(candidates) != 1:
            raise ValueError(f"Expected one exact email match at worksheet row {number}")
        user = candidates[0]
        if user["role"] != "student":
            raise ValueError(f"Matched account is not a student at worksheet row {number}")
        if name not in {normalized(user.get("full_name")), normalized(user.get("username"))}:
            raise ValueError(f"Student name mismatch at worksheet row {number}")
        matches.append({"id": user["id"], "school_name": school,
                        "previous_school_name": user.get("school_name")})
    if not matches:
        raise ValueError("No student rows supplied")
    return matches


def import_schools(engine, rows, *, apply=False, backup_path=None):
    if apply and not backup_path:
        raise ValueError("A private backup path is required before applying changes")
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("SET LOCAL lock_timeout = '10s'"))
            conn.execute(text("SET LOCAL statement_timeout = '60s'"))
            if apply:
                conn.execute(text("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE"))
        existed = "school_name" in {c["name"] for c in inspect(conn).get_columns("users")}
        school_column = "school_name" if existed else "NULL AS school_name"
        users = [dict(r) for r in conn.execute(text(
            "SELECT id, username, full_name, useremail, role, " + school_column + " FROM users"
        )).mappings()]
        matches = prepare_matches(rows, users)
        changed = [m for m in matches if m["previous_school_name"] != m["school_name"]]
        summary = {"matched": len(matches), "changed": len(changed), "applied": apply,
                   "schools": dict(Counter(m["school_name"] for m in matches))}
        if not apply:
            return summary
        # Exclusive creation prevents overwriting the recovery record on a rerun.
        backup = Path(backup_path)
        with backup.open("x", encoding="utf-8") as handle:
            json.dump({"column_existed": existed, "matches": matches}, handle, indent=2)
        backup.chmod(0o600)
        if not existed:
            conn.execute(text("ALTER TABLE users ADD COLUMN school_name VARCHAR(255)"))
        for row in changed:
            result = conn.execute(text(
                "UPDATE users SET school_name = :school_name WHERE id = :id AND role = 'student'"
            ), {"id": row["id"], "school_name": row["school_name"]})
            if result.rowcount != 1:
                raise RuntimeError("Student changed during import; transaction rolled back")
        actual = dict(conn.execute(text("SELECT id, school_name FROM users")).fetchall())
        if any(actual[m["id"]] != m["school_name"] for m in matches):
            raise RuntimeError("Import verification failed; transaction rolled back")
        # Validate that every account outside this workbook remains unchanged.
        matched_ids = {m["id"] for m in matches}
        if any(actual[u["id"]] != u["school_name"] for u in users if u["id"] not in matched_ids):
            raise RuntimeError("Unexpected school change outside workbook; rolled back")
        return summary
