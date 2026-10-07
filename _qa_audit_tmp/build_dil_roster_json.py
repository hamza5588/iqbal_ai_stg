"""Build dil_saathi JSON files from Students and Teachers list 6-10-2026.xlsx."""
from __future__ import annotations

import collections
import json
from pathlib import Path

import openpyxl

XLSX = Path(r"C:\Users\user\Desktop\iqbalai-v1.1\Students and Teachers list 6-10-2026.xlsx")
OUT_DIR = Path(__file__).resolve().parent
SCHOOL_MAP = {"Dil Junior 6": "DIL Junior 6"}


def main() -> None:
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    ws = wb["9th Grade Students 2026"]
    mode = "students"
    rows = []
    for vals in ws.iter_rows(values_only=True):
        vals = list(vals)
        if vals[0] == "S. No" or (isinstance(vals[0], str) and vals[0].strip() == "S. No"):
            continue
        title = str(vals[0]).strip().lower() if vals[0] else ""
        if "teacher" in title:
            mode = "teachers"
            continue
        if "student" in title and "grade" in title:
            mode = "students"
            continue
        sno = vals[0]
        if not (
            isinstance(sno, (int, float))
            or (isinstance(sno, str) and sno.strip().isdigit())
        ):
            continue
        email = str(vals[8]).strip() if vals[8] else ""
        if not email or "@" not in email:
            continue
        first = str(vals[1]).strip() if vals[1] else ""
        last = str(vals[2]).strip() if vals[2] else ""
        school = str(vals[7]).strip() if vals[7] else ""
        school = SCHOOL_MAP.get(school, school)
        rows.append(
            {
                "first_name": first,
                "last_name": last,
                "full_name": f"{first} {last}".strip(),
                "gender": str(vals[3]).strip() if vals[3] else "",
                "email": email,
                "username": email.split("@")[0],
                "password": str(vals[9]).strip() if vals[9] else "",
                "role": "teacher" if mode == "teachers" else "student",
                "class_standard": "9th",
                "medium": "English",
                "school": school,
                "project": str(vals[6]).strip() if vals[6] else "",
                "section": str(vals[5]).strip() if vals[5] else "",
                "tag": str(vals[10]).strip() if vals[10] else "",
            }
        )

    seen: collections.Counter[str] = collections.Counter()
    for r in rows:
        base = r["username"]
        seen[base] += 1
        if seen[base] > 1:
            r["username"] = f"{base}_{seen[base]}"

    users_path = OUT_DIR / "dil_saathi_users_2026_10_06.json"
    users_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    by_school: collections.OrderedDict[str, dict] = collections.OrderedDict()
    for r in rows:
        g = by_school.setdefault(
            r["school"],
            {
                "school": r["school"],
                "teacher_email": None,
                "teacher_name": None,
                "student_emails": [],
            },
        )
        if r["role"] == "teacher":
            g["teacher_email"] = r["email"]
            g["teacher_name"] = r["full_name"]
        else:
            g["student_emails"].append(r["email"])

    map_path = OUT_DIR / "dil_saathi_class_mapping_2026_10_06.json"
    map_path.write_text(json.dumps(list(by_school.values()), indent=2), encoding="utf-8")

    print(
        "users",
        len(rows),
        "students",
        sum(1 for r in rows if r["role"] == "student"),
        "teachers",
        sum(1 for r in rows if r["role"] == "teacher"),
    )
    for m in by_school.values():
        print(
            m["school"],
            "teacher=",
            m["teacher_email"],
            "students=",
            len(m["student_emails"]),
        )
    print("wrote", users_path)
    print("wrote", map_path)


if __name__ == "__main__":
    main()
