#!/usr/bin/env python3
"""Export Controlled / Non-Controlled diagnostic Excels with weak topics + wrong questions.

Reads roster emails from iqbal_ai_stg/result/*.xlsx, pulls ground-truth rows from the
DIL PostgreSQL DB over SSH, and writes enriched workbooks + an integrity report.

Nothing is invented: every weak topic and wrong question is derived from
assessment_attempts / attempt_answers / assessments.description.question_concepts
(and cross-checked against student_topic_scores when present).
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
RESULT_DIR = ROOT / "result"
SSH_HOST = "root@209.23.10.34"
SSH_KEY = Path.home() / ".ssh" / "iqbalai_server_209_34"
PSQL = (
    "docker exec -i iqbal_ai_stg-postgres-1 "
    "psql -U iqbalai_staging -d iqbalai_staging -At -F '|' -v ON_ERROR_STOP=1"
)

SOURCE_FILES = {
    "Controlled": RESULT_DIR / "Diagnostic Result - Controlled Group 8-10-2026.xlsx",
    "Non-Controlled": RESULT_DIR / "Diagnostic Result - Non-Controlled Group 8-10-2026.xlsx",
}

OPTION_LABELS = "ABCD"


def ssh_psql(sql: str, timeout: int = 180) -> list[list[str]]:
    if not SSH_KEY.exists():
        raise FileNotFoundError(f"SSH key not found: {SSH_KEY}")
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=25",
        "-i",
        str(SSH_KEY),
        SSH_HOST,
        PSQL,
    ]
    p = subprocess.run(
        cmd,
        input=sql,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )
    if p.returncode != 0:
        raise RuntimeError(f"psql failed:\n{p.stderr[-800:]}")
    lines = p.stdout.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    return [ln.split("|") for ln in lines]


def sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def option_label(idx) -> str:
    try:
        i = int(idx)
    except (TypeError, ValueError):
        return "?"
    if 0 <= i < len(OPTION_LABELS):
        return OPTION_LABELS[i]
    return str(i)


def parse_options(raw: str | None) -> dict[int, str]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    out = {}
    if isinstance(data, list):
        for i, item in enumerate(data):
            if isinstance(item, dict):
                out[i] = str(item.get("text") or item.get("label") or "")
            else:
                out[i] = str(item)
    elif isinstance(data, dict):
        for k, v in data.items():
            try:
                out[int(k)] = str(v.get("text") if isinstance(v, dict) else v)
            except (TypeError, ValueError):
                continue
    return out


def load_roster(path: Path) -> list[dict]:
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        return []
    header = [str(c or "").strip() for c in rows[0]]
    out = []
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        item = {header[i]: r[i] if i < len(r) else None for i in range(len(header))}
        email = str(item.get("Email") or "").strip().lower()
        if not email:
            continue
        out.append(
            {
                "student_name": str(item.get("Student Name") or "").strip(),
                "email": email,
                "school": str(item.get("School") or "").strip(),
                "excel_total": item.get("Total Questions"),
                "excel_correct": item.get("Correct Answers"),
                "excel_incorrect": item.get("Incorrect"),
                "excel_pct": item.get("Percentage"),
                "excel_minutes": item.get("No. of Minutes Taken to Finish the Exam"),
            }
        )
    return out


def fetch_assessment_meta(assessment_id: int) -> tuple[dict[str, str], dict]:
    rows = ssh_psql(f"SELECT description FROM assessments WHERE id = {int(assessment_id)};")
    if not rows or not rows[0] or not rows[0][0]:
        return {}, {}
    try:
        meta = json.loads(rows[0][0])
    except (json.JSONDecodeError, TypeError):
        return {}, {}
    concepts = {str(k): str(v) for k, v in (meta.get("question_concepts") or {}).items() if v}
    return concepts, meta


def fetch_students_by_email(emails: list[str]) -> dict[str, dict]:
    if not emails:
        return {}
    values = ", ".join(sql_quote(e.lower()) for e in emails)
    sql = f"""
SELECT id, full_name, useremail, username
FROM users
WHERE lower(useremail) IN ({values});
"""
    mapping = {}
    for row in ssh_psql(sql):
        if len(row) < 3:
            continue
        mapping[row[2].strip().lower()] = {
            "user_id": int(row[0]),
            "full_name": row[1],
            "email": row[2].strip().lower(),
            "username": row[3] if len(row) > 3 else "",
        }
    return mapping


def fetch_latest_attempts(user_ids: list[int], assessment_id: int | None = None) -> dict[int, dict]:
    if not user_ids:
        return {}
    ids = ", ".join(str(i) for i in user_ids)
    assess_filter = f"AND aa.assessment_id = {int(assessment_id)}" if assessment_id else ""
    sql = f"""
SELECT DISTINCT ON (aa.student_id)
  aa.student_id, aa.id, aa.assessment_id, aa.score, aa.max_score, aa.status,
  aa.started_at, aa.submitted_at, aa.timed_out, a.title
FROM assessment_attempts aa
JOIN assessments a ON a.id = aa.assessment_id
WHERE aa.student_id IN ({ids})
  AND aa.status = 'submitted'
  AND a.assessment_type = 'diagnostic'
  {assess_filter}
ORDER BY aa.student_id, aa.submitted_at DESC NULLS LAST, aa.id DESC;
"""
    out = {}
    for row in ssh_psql(sql):
        out[int(row[0])] = {
            "attempt_id": int(row[1]),
            "assessment_id": int(row[2]),
            "score": float(row[3] or 0),
            "max_score": float(row[4] or 0),
            "status": row[5],
            "started_at": row[6],
            "submitted_at": row[7],
            "timed_out": row[8] in ("t", "true", "True", "1"),
            "assessment_title": row[9],
        }
    return out


def fetch_answers(attempt_ids: list[int]) -> dict[int, list[dict]]:
    if not attempt_ids:
        return {}
    ids = ", ".join(str(i) for i in attempt_ids)
    sql = f"""
SELECT ans.attempt_id, ans.question_id, ans.selected_option_index, ans.is_correct,
       q.question_text, q.correct_option_index, q.options_json, q.correct_answer_raw,
       aq.sort_order
FROM attempt_answers ans
JOIN questions q ON q.id = ans.question_id
JOIN assessment_attempts aa ON aa.id = ans.attempt_id
LEFT JOIN assessment_questions aq
  ON aq.assessment_id = aa.assessment_id AND aq.question_id = ans.question_id
WHERE ans.attempt_id IN ({ids})
ORDER BY ans.attempt_id, aq.sort_order NULLS LAST, ans.question_id;
"""
    by_attempt: dict[int, list[dict]] = defaultdict(list)
    for row in ssh_psql(sql):
        opts = parse_options(row[6] if len(row) > 6 else None)
        sel = int(row[2]) if row[2] not in (None, "", "NULL") else None
        cor = int(row[5]) if row[5] not in (None, "", "NULL") else None
        is_correct = row[3] in ("t", "true", "True", "1")
        sort_order = int(row[8]) if row[8] not in (None, "", "NULL") else None
        by_attempt[int(row[0])].append(
            {
                "question_id": int(row[1]),
                "selected_option_index": sel,
                "is_correct": is_correct,
                "question_text": row[4] or "",
                "correct_option_index": cor,
                "correct_answer_raw": row[7] or "",
                "selected_label": option_label(sel) if sel is not None else "",
                "correct_label": option_label(cor) if cor is not None else (row[7] or ""),
                "selected_text": opts.get(sel, "") if sel is not None else "",
                "correct_text": opts.get(cor, "") if cor is not None else "",
                "question_no": (sort_order + 1) if sort_order is not None else None,
            }
        )
    return by_attempt


def fetch_topic_scores(user_ids: list[int]) -> dict[int, list[dict]]:
    if not user_ids:
        return {}
    ids = ", ".join(str(i) for i in user_ids)
    sql = f"""
SELECT sts.student_id, sts.topic_id, t.name, sts.score_percent, sts.sample_size, sts.mastery_status
FROM student_topic_scores sts
JOIN topics t ON t.id = sts.topic_id
WHERE sts.student_id IN ({ids})
ORDER BY sts.student_id, sts.score_percent ASC, t.name;
"""
    out: dict[int, list[dict]] = defaultdict(list)
    for row in ssh_psql(sql):
        out[int(row[0])].append(
            {
                "topic_id": int(row[1]),
                "topic": row[2],
                "score_percent": float(row[3] or 0),
                "sample_size": int(float(row[4] or 0)),
                "mastery_status": row[5],
            }
        )
    return out


def build_student_result(
    roster: dict,
    account: dict | None,
    attempt: dict | None,
    answers: list[dict],
    topic_scores: list[dict],
    concepts: dict[str, str],
) -> dict:
    issues: list[str] = []
    if not account:
        return {
            **roster,
            "db_found": False,
            "issues": ["EMAIL_NOT_IN_DB"],
            "warnings": [],
            "weak_topics": [],
            "wrong_by_topic": {},
            "db_total": None,
            "db_correct": None,
            "db_incorrect": None,
            "db_pct": None,
        }

    if not attempt:
        return {
            **roster,
            "db_found": True,
            "user_id": account["user_id"],
            "db_name": account["full_name"],
            "issues": ["NO_SUBMITTED_DIAGNOSTIC"],
            "warnings": [],
            "weak_topics": [],
            "wrong_by_topic": {},
            "db_total": 0,
            "db_correct": 0,
            "db_incorrect": 0,
            "db_pct": None,
        }

    total = len(answers)
    correct = sum(1 for a in answers if a["is_correct"])
    incorrect = total - correct
    pct = (correct / total) if total else 0.0

    # Soft warnings vs original Excel summary (often used max_score=20 even on timeouts).
    warnings: list[str] = []
    for key, excel_val, db_val in (
        ("excel_total", roster.get("excel_total"), total),
        ("excel_correct", roster.get("excel_correct"), correct),
        ("excel_incorrect", roster.get("excel_incorrect"), incorrect),
    ):
        if excel_val is None:
            continue
        try:
            if abs(float(excel_val) - float(db_val)) > 0.01:
                warnings.append(f"WARN_ORIGINAL_EXCEL_{key}:{excel_val}!=db:{db_val}")
        except (TypeError, ValueError):
            warnings.append(f"WARN_BAD_EXCEL_{key}:{excel_val}")

    wrong = [a for a in answers if not a["is_correct"]]
    # Group wrong answers by concept label (assessment question_concepts)
    wrong_by_topic: dict[str, list[dict]] = defaultdict(list)
    for a in wrong:
        topic = concepts.get(str(a["question_id"])) or "Unmapped"
        a = dict(a)
        a["weak_topic"] = topic
        wrong_by_topic[topic].append(a)

    # Weak topics = areas with any wrong answer (score < 100), matching product rule
    weak_from_answers = []
    topic_totals: dict[str, list[bool]] = defaultdict(list)
    for a in answers:
        topic = concepts.get(str(a["question_id"])) or "Unmapped"
        topic_totals[topic].append(bool(a["is_correct"]))
    for topic, flags in sorted(topic_totals.items()):
        score = 100.0 * sum(flags) / len(flags) if flags else 0.0
        if score < 100.0:
            weak_from_answers.append(
                {
                    "topic": topic,
                    "score_percent": round(score, 2),
                    "correct": sum(flags),
                    "total": len(flags),
                    "wrong_count": len(flags) - sum(flags),
                    "source": "attempt_answers+question_concepts",
                }
            )

    # Cross-check persisted student_topic_scores (names may match concepts)
    score_by_name = {t["topic"].strip().lower(): t for t in topic_scores}
    for w in weak_from_answers:
        persisted = score_by_name.get(w["topic"].strip().lower())
        if persisted:
            w["db_topic_score_percent"] = round(persisted["score_percent"], 2)
            w["db_mastery_status"] = persisted["mastery_status"]
            if abs(persisted["score_percent"] - w["score_percent"]) > 0.6:
                # Live mastery can improve after diagnostic via practice — not fake data.
                warnings.append(
                    f"WARN_MASTERY_UPDATED_AFTER_DIAG:{w['topic']}:diag={w['score_percent']}"
                    f":live_db={round(persisted['score_percent'], 2)}"
                )
        else:
            w["db_topic_score_percent"] = None

    # Every listed wrong question must appear under a weak topic
    for topic, qs in wrong_by_topic.items():
        if topic not in {w["topic"] for w in weak_from_answers}:
            issues.append(f"WRONG_WITHOUT_WEAK_TOPIC:{topic}:{len(qs)}")

    for w in weak_from_answers:
        qs = wrong_by_topic.get(w["topic"], [])
        if w["wrong_count"] != len(qs):
            issues.append(
                f"WRONG_COUNT_MISMATCH:{w['topic']}:expected={w['wrong_count']}:listed={len(qs)}"
            )
        # Integrity: listed questions must be incorrect in DB
        for q in qs:
            if q["is_correct"]:
                issues.append(f"FAKE_WRONG:{q['question_id']}")

    weak_summary = "; ".join(
        f"{w['topic']} ({w['score_percent']}% - {w['wrong_count']} wrong of {w['total']})"
        for w in weak_from_answers
    )
    wrong_summary_parts = []
    for w in weak_from_answers:
        qs = wrong_by_topic.get(w["topic"], [])
        qbits = []
        for q in qs:
            qno = q["question_no"] or q["question_id"]
            student_ans = q["selected_label"] or "-"
            correct_ans = q["correct_label"] or "-"
            qbits.append(f"Q{qno} student={student_ans} correct={correct_ans}")
        wrong_summary_parts.append(
            f"{w['topic']}: " + "; ".join(qbits) if qbits else f"{w['topic']}: (none)"
        )
    wrong_summary = " | ".join(wrong_summary_parts)

    return {
        **roster,
        "db_found": True,
        "user_id": account["user_id"],
        "db_name": account["full_name"],
        "attempt_id": attempt["attempt_id"],
        "assessment_id": attempt["assessment_id"],
        "assessment_title": attempt["assessment_title"],
        "submitted_at": attempt["submitted_at"],
        "db_total": total,
        "db_correct": correct,
        "db_incorrect": incorrect,
        "db_pct": round(pct, 4),
        "weak_topics": weak_from_answers,
        "weak_topics_summary": weak_summary,
        "wrong_by_topic": dict(wrong_by_topic),
        "wrong_questions_summary": wrong_summary,
        "issues": issues,
        "warnings": warnings,
        "answers": answers,
    }


def format_percentage(value) -> str:
    """Show 45% instead of 0.45 (also accepts already-scaled 0-100 values)."""
    if value is None or value == "":
        return ""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    if 0 <= n <= 1:
        n *= 100
    if abs(n - round(n)) < 0.05:
        return f"{int(round(n))}%"
    return f"{n:.1f}%".rstrip("0").rstrip(".") + "%"


def style_header(ws, row: int = 1):
    fill = PatternFill("solid", fgColor="1F4E79")
    font = Font(color="FFFFFF", bold=True)
    for cell in ws[row]:
        cell.fill = fill
        cell.font = font
        cell.alignment = Alignment(wrap_text=True, vertical="center")


def autosize(ws, max_width: int = 60):
    for col in ws.columns:
        letter = get_column_letter(col[0].column)
        width = 12
        for cell in col[:80]:
            if cell.value is None:
                continue
            width = min(max_width, max(width, len(str(cell.value)) + 2))
        ws.column_dimensions[letter].width = width


def write_workbook(group: str, results: list[dict], out_path: Path, concepts: dict[str, str]):
    wb = Workbook()
    ws = wb.active
    ws.title = "Results"
    headers = [
        "Student Name",
        "Email",
        "School",
        "Group",
        "Assessment",
        "Total Questions",
        "Correct Answers",
        "Incorrect",
        "Percentage",
        "No. of Minutes Taken to Finish the Exam",
        "Weak Topics",
        "Wrong Questions (by Weak Topic)",
    ]
    ws.append(headers)
    for r in results:
        pct_raw = r.get("db_pct") if r.get("db_pct") is not None else r.get("excel_pct")
        ws.append(
            [
                r.get("db_name") or r.get("student_name"),
                r.get("email"),
                r.get("school"),
                group,
                r.get("assessment_title"),
                r.get("db_total") if r.get("db_total") is not None else r.get("excel_total"),
                r.get("db_correct") if r.get("db_correct") is not None else r.get("excel_correct"),
                r.get("db_incorrect") if r.get("db_incorrect") is not None else r.get("excel_incorrect"),
                format_percentage(pct_raw),
                r.get("excel_minutes"),
                r.get("weak_topics_summary") or "",
                r.get("wrong_questions_summary") or "",
            ]
        )
    style_header(ws)
    ws.freeze_panes = "A2"
    autosize(ws, 70)

    detail = wb.create_sheet("Wrong Answers Detail")
    detail.append(
        [
            "Student Name",
            "Email",
            "Group",
            "Weak Topic",
            "Topic Score %",
            "Question No",
            "Student Selected",
            "Correct Answer",
        ]
    )
    for r in results:
        score_map = {w["topic"]: w["score_percent"] for w in r.get("weak_topics") or []}
        for topic, qs in (r.get("wrong_by_topic") or {}).items():
            for q in qs:
                detail.append(
                    [
                        r.get("db_name") or r.get("student_name"),
                        r.get("email"),
                        group,
                        topic,
                        format_percentage(score_map.get(topic)),
                        q.get("question_no"),
                        q.get("selected_label") or "-",
                        q.get("correct_label") or "-",
                    ]
                )
    style_header(detail)
    detail.freeze_panes = "A2"
    autosize(detail, 55)

    weak_sheet = wb.create_sheet("Weak Topics")
    weak_sheet.append(
        [
            "Student Name",
            "Email",
            "Group",
            "Weak Topic",
            "Score %",
            "Correct",
            "Total",
            "Wrong Count",
            "Wrong Q (No / Student / Correct)",
            "DB Topic Score %",
        ]
    )
    for r in results:
        for w in r.get("weak_topics") or []:
            qs = (r.get("wrong_by_topic") or {}).get(w["topic"], [])
            q_brief = "; ".join(
                f"Q{q.get('question_no') or q['question_id']} "
                f"student={q.get('selected_label') or '-'} "
                f"correct={q.get('correct_label') or '-'}"
                for q in qs
            )
            weak_sheet.append(
                [
                    r.get("db_name") or r.get("student_name"),
                    r.get("email"),
                    group,
                    w["topic"],
                    format_percentage(w["score_percent"]),
                    w["correct"],
                    w["total"],
                    w["wrong_count"],
                    q_brief,
                    format_percentage(w.get("db_topic_score_percent")),
                ]
            )
    style_header(weak_sheet)
    weak_sheet.freeze_panes = "A2"
    autosize(weak_sheet, 40)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


def verify_results(group: str, results: list[dict]) -> dict:
    """Independent integrity pass — no fabricated rows allowed."""
    report = {
        "group": group,
        "roster_count": len(results),
        "db_found": 0,
        "with_attempt": 0,
        "integrity_ok": 0,
        "failures": [],
        "checks": [],
    }
    for r in results:
        email = r["email"]
        if not r.get("db_found"):
            report["failures"].append({"email": email, "error": "EMAIL_NOT_IN_DB"})
            continue
        report["db_found"] += 1
        if not r.get("attempt_id"):
            report["failures"].append({"email": email, "error": "NO_SUBMITTED_DIAGNOSTIC"})
            continue
        report["with_attempt"] += 1

        answers = r.get("answers") or []
        wrong_ids = {a["question_id"] for a in answers if not a["is_correct"]}
        listed_ids = set()
        for qs in (r.get("wrong_by_topic") or {}).values():
            for q in qs:
                listed_ids.add(q["question_id"])
                if q["is_correct"]:
                    report["failures"].append(
                        {"email": email, "error": f"FAKE_WRONG_LISTED:{q['question_id']}"}
                    )
                if q["question_id"] not in wrong_ids:
                    report["failures"].append(
                        {"email": email, "error": f"LISTED_NOT_WRONG_IN_DB:{q['question_id']}"}
                    )

        if listed_ids != wrong_ids:
            report["failures"].append(
                {
                    "email": email,
                    "error": "WRONG_SET_MISMATCH",
                    "db_wrong": sorted(wrong_ids),
                    "listed": sorted(listed_ids),
                }
            )

        # Recompute totals
        if r["db_correct"] + r["db_incorrect"] != r["db_total"]:
            report["failures"].append({"email": email, "error": "TOTAL_ARITHMETIC"})

        if r.get("issues"):
            report["failures"].append({"email": email, "error": "ISSUES", "issues": r["issues"]})
        else:
            report["integrity_ok"] += 1

    report["checks"].append(
        f"{report['integrity_ok']}/{report['with_attempt']} submitted students passed all integrity checks"
    )
    report["checks"].append(
        f"{report['db_found']}/{report['roster_count']} roster emails found in DB"
    )
    report["passed"] = len(report["failures"]) == 0
    return report


def process_group(group: str, path: Path, preferred_assessment_id: int | None = 3) -> tuple[Path, dict]:
    print(f"\n=== {group}: reading {path.name} ===")
    roster = load_roster(path)
    print(f"Roster rows: {len(roster)}")
    emails = [r["email"] for r in roster]
    accounts = fetch_students_by_email(emails)
    print(f"DB accounts matched: {len(accounts)}")
    user_ids = [a["user_id"] for a in accounts.values()]
    attempts = fetch_latest_attempts(user_ids, preferred_assessment_id)
    # Fallback: any diagnostic if preferred missing
    missing = [uid for uid in user_ids if uid not in attempts]
    if missing:
        attempts.update(fetch_latest_attempts(missing, None))
    print(f"Submitted diagnostic attempts: {len(attempts)}")

    assessment_ids = {a["assessment_id"] for a in attempts.values()}
    # Prefer assessment 3 concept map; merge others if needed
    concepts: dict[str, str] = {}
    for aid in sorted(assessment_ids) or ([preferred_assessment_id] if preferred_assessment_id else []):
        c, _ = fetch_assessment_meta(aid)
        concepts.update(c)
    if not concepts and preferred_assessment_id:
        concepts, _ = fetch_assessment_meta(preferred_assessment_id)
    print(f"Question concept labels: {len(concepts)}")

    answers_by_attempt = fetch_answers([a["attempt_id"] for a in attempts.values()])
    topic_scores = fetch_topic_scores(user_ids)

    results = []
    for r in roster:
        acc = accounts.get(r["email"])
        att = attempts.get(acc["user_id"]) if acc else None
        ans = answers_by_attempt.get(att["attempt_id"], []) if att else []
        scores = topic_scores.get(acc["user_id"], []) if acc else []
        results.append(build_student_result(r, acc, att, ans, scores, concepts))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    short_names = {
        "Controlled": "controlled_group_weak_topic.xlsx",
        "Non-Controlled": "non_controlled_group_weak_topic.xlsx",
    }
    out_path = RESULT_DIR / short_names[group]
    write_workbook(group, results, out_path, concepts)
    print(f"Wrote {out_path}")

    report = verify_results(group, results)
    report["warnings_count"] = sum(len(r.get("warnings") or []) for r in results)
    report_path = RESULT_DIR / f"integrity_report_{group.lower().replace('-', '_')}_{stamp}.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"Integrity: passed={report['passed']} ok={report['integrity_ok']} "
        f"failures={len(report['failures'])} warnings={report['warnings_count']} "
        f"-> {report_path.name}"
    )
    return out_path, report


def main() -> int:
    reports = []
    outputs = []
    for group, path in SOURCE_FILES.items():
        if not path.exists():
            print(f"MISSING source: {path}", file=sys.stderr)
            return 2
        out, report = process_group(group, path)
        outputs.append(str(out))
        reports.append(report)

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "outputs": outputs,
        "reports": reports,
        "all_passed": all(r["passed"] for r in reports),
    }
    summary_path = RESULT_DIR / "integrity_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nSummary -> {summary_path}")
    print("ALL_PASSED" if summary["all_passed"] else "HAS_FAILURES")
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
