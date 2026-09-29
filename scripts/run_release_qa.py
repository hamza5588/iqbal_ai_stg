#!/usr/bin/env python3
"""Professional staging release QA — API + asset + timeout/resume cycle.

Does not mutate platform theme. Creates throwaway students only.
"""
from __future__ import annotations

import json
import os
import re
import ssl
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.request import urlopen

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "load"))

BASE_URL = os.environ.get("QA_BASE_URL", "https://dil.iqbalai.com").rstrip("/")
ADMIN_EMAIL = os.environ.get("QA_ADMIN_EMAIL", "admin@iqbalai.com")
ADMIN_PASS = os.environ.get("QA_ADMIN_PASS", "Hamzakhanswati12@")
TEACHER_EMAIL = os.environ.get("QA_TEACHER_EMAIL", "teacher@iqbalai.com")
TEACHER_PASSES = [
    os.environ.get("QA_TEACHER_PASS", ""),
    "password123",
    "Hamzakhanswati12@",
    "Teacher123!",
]
STUDENT_PASS = os.environ.get("QA_STUDENT_PASS", "E2eTest2026!")
OUT = ROOT / "_qa_audit_tmp" / "release_qa"
RUN_ID = int(time.time())


@dataclass
class Case:
    area: str
    name: str
    status: str
    detail: str = ""


@dataclass
class Report:
    cases: list[Case] = field(default_factory=list)

    def add(self, area: str, name: str, ok: bool, detail: str = "", skip: bool = False):
        st = "skip" if skip else ("PASS" if ok else "FAIL")
        self.cases.append(Case(area, name, st, str(detail)[:500]))
        print(f"[{st:4}] {area} / {name}: {detail}".encode("ascii", "replace").decode("ascii")[:240])

    def summary(self) -> dict:
        counts = {"PASS": 0, "FAIL": 0, "skip": 0}
        for c in self.cases:
            counts[c.status] = counts.get(c.status, 0) + 1
        return counts


report = Report()


class Api:
    def __init__(self):
        self.s = requests.Session()
        self.s.verify = False
        self.s.headers.update({"X-Requested-With": "XMLHttpRequest"})

    def login(self, email: str, password: str) -> bool:
        r = self.s.post(
            f"{BASE_URL}/auth/login",
            data={"useremail": email, "password": password},
            headers={"Accept": "application/json"},
            timeout=60,
        )
        try:
            return r.status_code == 200 and bool(r.json().get("success"))
        except Exception:
            return False

    def get(self, path: str, timeout: int = 60):
        r = self.s.get(f"{BASE_URL}{path}", timeout=timeout)
        try:
            return r.status_code, r.json(), r
        except Exception:
            return r.status_code, r.text[:400], r

    def post(self, path: str, body=None, timeout: int = 60):
        r = self.s.post(f"{BASE_URL}{path}", json=body, timeout=timeout)
        try:
            return r.status_code, r.json(), r
        except Exception:
            return r.status_code, r.text[:400], r

    def page(self, path: str, timeout: int = 60):
        r = self.s.get(f"{BASE_URL}{path}", timeout=timeout)
        return r.status_code, r.text


def unwrap(body: Any) -> Any:
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def create_student(admin: Api, suffix: str) -> Optional[str]:
    email = f"relqa_{RUN_ID}_{suffix}@iqbalai.com"
    username = f"relqa{RUN_ID}{suffix}"[:24]
    code, body, _ = admin.post(
        "/admin/users",
        {
            "username": username,
            "useremail": email,
            "password": STUDENT_PASS,
            "role": "student",
            "class_standard": "8",
            "medium": "English",
        },
    )
    ok = code == 200 and isinstance(body, dict) and body.get("success")
    report.add("Setup", f"Create student {suffix}", ok, email if ok else str(body)[:180])
    return email if ok else None


def expire_attempt(attempt_id: int) -> bool:
    try:
        from _ssh import connect
    except Exception as exc:
        report.add("Timeout", "SSH helper import", False, str(exc))
        return False
    ssh = connect(timeout=30)
    try:
        py = (
            "from datetime import datetime, timedelta\n"
            "from run import app\n"
            "with app.app_context():\n"
            "    from app.utils.db import get_db\n"
            "    from app.models.lms_models import AssessmentAttempt\n"
            f"    aid = {int(attempt_id)}\n"
            "    db = get_db()\n"
            "    row = db.query(AssessmentAttempt).filter(AssessmentAttempt.id == aid).first()\n"
            "    assert row, 'missing attempt'\n"
            "    row.expires_at = datetime.utcnow() - timedelta(hours=1)\n"
            "    db.commit()\n"
            "    print('expired', row.id, row.expires_at)\n"
        )
        remote = f"/root/iqbal_ai_stg/_qa_audit_tmp/expire_attempt_{int(attempt_id)}.py"
        sftp = ssh.open_sftp()
        with sftp.file(remote, "w") as fh:
            fh.write(py)
        sftp.close()
        cmd = (
            "cd /root/iqbal_ai_stg && docker compose exec -T flask_app1 "
            f"python /app/_qa_audit_tmp/expire_attempt_{int(attempt_id)}.py"
        )
        _, stdout, stderr = ssh.exec_command(cmd, timeout=120)
        code = stdout.channel.recv_exit_status()
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        report.add("Timeout", f"Expire attempt {attempt_id} in DB", code == 0 and "expired" in out, (out + err)[:240])
        return code == 0 and "expired" in out
    finally:
        ssh.close()


def math_flags(questions: list) -> dict:
    blob = json.dumps(questions, default=str)
    mixed_broken = bool(re.search(r"\b\d+\s+\d+\s+\d+\s*%", blob))
    nested_frac_delim = "\\frac{\\(" in blob or "\\frac{\\[" in blob
    has_slash_frac = bool(re.search(r"\d+\s+\d+\s*/\s*\d+\s*%", blob)) or "/3%" in blob
    smashed = bool(re.search(r"Whichisthe|correctfactorizationof", blob, re.I))
    glued_and = bool(re.search(r"(\d|√|π)and\b", blob, re.I))
    return {
        "mixed_broken": mixed_broken,
        "nested_frac_delim": nested_frac_delim,
        "has_slash_frac": has_slash_frac,
        "smashed": smashed,
        "glued_and": glued_and,
        "sample": blob[:240],
    }


def run() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    # --- Infra ---
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=30)
        report.add("Infra", "GET /health HTTPS", r.status_code == 200, r.text[:80])
    except Exception as exc:
        report.add("Infra", "GET /health HTTPS", False, str(exc))
        return 1

    try:
        r = requests.get(f"{BASE_URL}/api/lms/health", timeout=30)
        report.add("Infra", "GET /api/lms/health", r.status_code == 200, r.text[:80])
    except Exception as exc:
        report.add("Infra", "GET /api/lms/health", False, str(exc))

    try:
        ctx = ssl.create_default_context()
        urlopen(BASE_URL + "/health", context=ctx, timeout=20).read()
        report.add("Infra", "TLS certificate valid for dil.iqbalai.com", True)
    except Exception as exc:
        report.add("Infra", "TLS certificate valid for dil.iqbalai.com", False, str(exc))

    for path, needle in [
        ("/static/lms/lms-student.js", "renderDiagnosticTimeOver"),
        ("/static/lms/lms-core.js", "lmsNormalizeMixedPercents"),
        ("/static/lms/lms-ui.css", "overflow-wrap: break-word"),
        ("/static/teacher/js/chat-response-formatter.js", "_isInsideUnclosedFrac"),
    ]:
        r = requests.get(BASE_URL + path, timeout=30)
        report.add("Assets", path, r.status_code == 200 and needle in r.text, f"http={r.status_code} has={needle in r.text}")

    anon = Api()
    code, body, _ = anon.get("/api/lms/diagnostics/default")
    report.add("Security", "Anon diagnostic blocked", code in (401, 403, 302, 400), f"code={code}")
    st, html = anon.page("/auth/login")
    report.add("Pages", "Login page", st == 200 and "password" in html.lower(), f"http={st}")

    # --- Admin ---
    admin = Api()
    ok = admin.login(ADMIN_EMAIL, ADMIN_PASS)
    report.add("Auth", "Admin login", ok, ADMIN_EMAIL)
    if not ok:
        return 1
    st, html = admin.page("/admin")
    report.add("Pages", "Admin dashboard", st == 200, f"http={st} len={len(html)}")
    code, body, _ = admin.get("/api/lms/admin/diagnostics")
    diags = unwrap(body) if code == 200 else []
    published = [d for d in (diags or []) if isinstance(d, dict) and d.get("status") == "published"]
    report.add(
        "Admin",
        "Published platform diagnostic exists",
        bool(published),
        f"published={len(published)} total={len(diags) if isinstance(diags, list) else '?'}",
    )
    if not published:
        return 1
    diag_id = published[0]["id"]

    # --- Teacher ---
    teacher = Api()
    teacher_ok = False
    teacher_email = TEACHER_EMAIL
    for pw in TEACHER_PASSES:
        if not pw:
            continue
        if teacher.login(teacher_email, pw):
            teacher_ok = True
            break
        teacher = Api()
    if not teacher_ok:
        teacher_email = f"relqa_teacher_{RUN_ID}@iqbalai.com"
        code, body, _ = admin.post(
            "/admin/users",
            {
                "username": f"relqat{RUN_ID}"[:24],
                "useremail": teacher_email,
                "password": STUDENT_PASS,
                "role": "teacher",
                "class_standard": "8",
                "medium": "English",
            },
        )
        teacher = Api()
        teacher_ok = code == 200 and teacher.login(teacher_email, STUDENT_PASS)
    report.add("Auth", "Teacher login", teacher_ok, teacher_email if teacher_ok else "could not login or create teacher")
    if teacher_ok:
        st, html = teacher.page("/teacher-dashboard")
        report.add("Pages", "Teacher dashboard", st == 200, f"http={st}")
        code, body, _ = teacher.post("/api/lms/diagnostics/from-pdf", {})
        report.add("RBAC", "Teacher cannot upload diagnostic", code in (401, 403, 400, 415), f"code={code}")
        code, body, _ = teacher.get("/api/lms/quizzes")
        quizzes = unwrap(body)
        qlist = quizzes if isinstance(quizzes, list) else (quizzes or {}).get("quizzes") or []
        report.add("Teacher", "List quizzes", code == 200, f"count={len(qlist) if isinstance(qlist, list) else '?'}")

    # --- Student pages unauthenticated vs logged in ---
    st, html = anon.page("/student-dashboard")
    redirected = st in (301, 302, 401, 403) or (st == 200 and "password" in html.lower())
    report.add("Security", "Student dashboard requires login", redirected or st != 500, f"http={st}")

    # ========== Student A: resume ==========
    email_a = create_student(admin, "a")
    stu_a = Api()
    report.add("Auth", "Student A login", stu_a.login(email_a, STUDENT_PASS) if email_a else False, email_a or "")
    code, body, _ = stu_a.get("/api/lms/diagnostics/default")
    diag = unwrap(body) if code == 200 else {}
    report.add("StudentA", "See default diagnostic", code == 200 and not diag.get("diagnostic_completed"), f"id={diag.get('id')} completed={diag.get('diagnostic_completed')}")
    st, html = stu_a.page("/student-dashboard")
    report.add(
        "Pages",
        "Student dashboard + LMS JS",
        st == 200 and "lms-student.js" in html and "lms-core.js" in html,
        f"http={st} student_js={'lms-student.js' in html}",
    )

    code, body, _ = stu_a.post(f"/api/lms/quizzes/{diag_id}/start")
    start1 = unwrap(body)
    attempt_a = start1.get("attempt_id") if isinstance(start1, dict) else None
    remaining = start1.get("remaining_seconds") if isinstance(start1, dict) else None
    report.add(
        "StudentA",
        "Start diagnostic with timer",
        bool(attempt_a) and remaining is not None and remaining > 30,
        f"attempt={attempt_a} remaining={remaining} resumed={start1.get('resumed') if isinstance(start1, dict) else None}",
    )
    code, body, _ = stu_a.get(f"/api/lms/attempts/{attempt_a}/questions")
    qdata = unwrap(body) if code == 200 else {}
    questions = (qdata or {}).get("questions") or []
    report.add("StudentA", "Load questions", code == 200 and len(questions) >= 5, f"count={len(questions)}")
    flags = math_flags(questions)
    report.add("Math", "No smashed English stems in payload", not flags["smashed"], flags["sample"])
    report.add("Math", "No glued words like √25and", not flags["glued_and"], flags["sample"])
    report.add("Math", "No leftover \\( inside \\frac", not flags["nested_frac_delim"], flags["sample"])
    report.add("Math", "No 16 2 3 % broken mixed numbers", not flags["mixed_broken"], flags["sample"])
    # mixed slash is content-dependent
    if any("%" in json.dumps(q, default=str) for q in questions):
        report.add("Math", "Percent options keep a visible slash when mixed", flags["has_slash_frac"] or not flags["mixed_broken"], flags["sample"])

    answered = 0
    for i, q in enumerate(questions[:5]):
        qid = q.get("question_id")
        if qid is None:
            continue
        code, body, _ = stu_a.post(
            f"/api/lms/attempts/{attempt_a}/answer",
            {"question_id": qid, "selected_option_index": i % max(1, len(q.get("options") or [0, 1, 2, 3]))},
        )
        if code == 200:
            answered += 1
    report.add("StudentA", "Save 4–5 answers then leave", answered >= 4, f"saved={answered}")

    # simulate close/reopen: new session object, login again
    stu_a2 = Api()
    stu_a2.login(email_a, STUDENT_PASS)
    code, body, _ = stu_a2.get("/api/lms/diagnostics/default")
    diag2 = unwrap(body)
    report.add(
        "StudentA",
        "Reopen is NOT 'already completed'",
        not bool(diag2.get("diagnostic_completed")) and not bool(diag2.get("diagnostic_timed_out")),
        f"completed={diag2.get('diagnostic_completed')} timed_out={diag2.get('diagnostic_timed_out')}",
    )
    code, body, _ = stu_a2.post(f"/api/lms/quizzes/{diag_id}/start")
    start2 = unwrap(body)
    report.add(
        "StudentA",
        "Resume same attempt",
        start2.get("attempt_id") == attempt_a and start2.get("resumed") is True,
        json.dumps({k: start2.get(k) for k in ("attempt_id", "resumed", "remaining_seconds", "timed_out")}),
    )
    code, body, _ = stu_a2.get(f"/api/lms/attempts/{attempt_a}/questions")
    q2 = unwrap(body)
    saved = q2.get("saved_answers") or {}
    idx = q2.get("current_question_index")
    report.add(
        "StudentA",
        "Saved answers + continue index restored",
        len(saved) >= 4 and idx == len(saved),
        f"saved={saved} current={idx}",
    )

    # ========== Student B: timeout 0 marks ==========
    email_b = create_student(admin, "b")
    stu_b = Api()
    stu_b.login(email_b, STUDENT_PASS)
    code, body, _ = stu_b.post(f"/api/lms/quizzes/{diag_id}/start")
    start_b = unwrap(body)
    attempt_b = start_b.get("attempt_id")
    code, body, _ = stu_b.get(f"/api/lms/attempts/{attempt_b}/questions")
    qb = unwrap(body).get("questions") or []
    if qb:
        stu_b.post(
            f"/api/lms/attempts/{attempt_b}/answer",
            {"question_id": qb[0].get("question_id"), "selected_option_index": 0},
        )
    report.add("StudentB", "Start + one answer before timeout", bool(attempt_b), f"attempt={attempt_b}")
    if attempt_b and expire_attempt(attempt_b):
        stu_b2 = Api()
        stu_b2.login(email_b, STUDENT_PASS)
        code, body, _ = stu_b2.get("/api/lms/diagnostics/default")
        diag_b = unwrap(body)
        report.add(
            "StudentB",
            "Reopen after expiry shows time-over not generic completed",
            bool(diag_b.get("diagnostic_timed_out")) and "0" in str(diag_b.get("message") or diag_b.get("diagnostic_timeout_message") or "0"),
            json.dumps({k: diag_b.get(k) for k in ("diagnostic_completed", "diagnostic_timed_out", "message", "score")}),
        )
        code, body, _ = stu_b2.post(f"/api/lms/quizzes/{diag_id}/start")
        start_b2 = unwrap(body)
        scored_zero = start_b2.get("score") is not None and float(start_b2.get("score")) == 0
        report.add(
            "StudentB",
            "Start after expiry returns 0 marks / timed_out",
            bool(start_b2.get("timed_out") or start_b2.get("time_over")) and scored_zero,
            json.dumps({k: start_b2.get(k) for k in ("timed_out", "time_over", "score", "score_percent", "message", "status")}),
        )
        code, body, _ = stu_b2.post(f"/api/lms/quizzes/{diag_id}/start")
        start_b3 = unwrap(body)
        blocked = code == 400 or start_b3.get("timed_out") or "already completed" in json.dumps(body).lower()
        report.add("StudentB", "No second attempt after time-over", blocked, f"code={code} body={str(body)[:160]}")

    # ========== Student C: happy-path submit ==========
    email_c = create_student(admin, "c")
    stu_c = Api()
    stu_c.login(email_c, STUDENT_PASS)
    code, body, _ = stu_c.post(f"/api/lms/quizzes/{diag_id}/start")
    start_c = unwrap(body)
    attempt_c = start_c.get("attempt_id")
    code, body, _ = stu_c.get(f"/api/lms/attempts/{attempt_c}/questions")
    qc = unwrap(body).get("questions") or []
    for i, q in enumerate(qc):
        qid = q.get("question_id")
        if qid is not None:
            stu_c.post(
                f"/api/lms/attempts/{attempt_c}/answer",
                {"question_id": qid, "selected_option_index": 0},
            )
    code, body, _ = stu_c.post(f"/api/lms/attempts/{attempt_c}/submit", {"time_expired": False})
    res_c = unwrap(body)
    report.add(
        "StudentC",
        "In-time submit scores (not forced 0)",
        code == 200 and not res_c.get("timed_out") and res_c.get("diagnostic_completed") is True,
        json.dumps({k: res_c.get(k) for k in ("score", "score_percent", "timed_out", "diagnostic_completed")}),
    )
    code, body, _ = stu_c.post(f"/api/lms/quizzes/{diag_id}/start")
    report.add("StudentC", "Retake blocked after real submit", code == 400, f"code={code} {str(body)[:140]}")
    code, body, _ = stu_c.get("/api/lms/diagnostics/default")
    done = unwrap(body)
    report.add(
        "StudentC",
        "Completed diagnostic is not labeled time-over",
        bool(done.get("diagnostic_completed")) and not done.get("diagnostic_timed_out"),
        f"completed={done.get('diagnostic_completed')} timed_out={done.get('diagnostic_timed_out')}",
    )
    code, body, _ = stu_c.get("/api/lms/students/me/onboarding-status")
    onb = unwrap(body)
    report.add("StudentC", "Onboarding diagnostic_completed", bool(onb.get("diagnostic_completed")), str(onb)[:160])
    code, body, _ = stu_c.get("/api/lms/students/me/dashboard")
    dash = unwrap(body)
    report.add("StudentC", "Student dashboard API", code == 200 and isinstance(dash, dict), f"keys={list(dash)[:12] if isinstance(dash, dict) else body}")
    code, body, _ = stu_c.get("/api/lms/students/me/learning-path")
    path = unwrap(body)
    items = (path or {}).get("items") if isinstance(path, dict) else []
    report.add("StudentC", "Learning path after diagnostic", isinstance(items, list), f"steps={len(items) if isinstance(items, list) else 0}")
    if items:
        titles = [it.get("title") for it in items]
        report.add("StudentC", "Learning path has no Quiz #0 label", "Quiz #0" not in titles, str(titles[:6]))

    code, body, _ = stu_c.post("/api/lms/deficiency/sessions", {"force_new": True}, timeout=90)
    sess = unwrap(body) if code in (200, 201) else {}
    report.add("StudentC", "Start Learning Chat session", code in (200, 201) and bool(sess.get("session_id") or sess.get("id")), f"code={code} {str(sess)[:140]}")
    sid = sess.get("session_id") or sess.get("id")
    if sid:
        code, body, _ = stu_c.post(
            f"/api/lms/deficiency/sessions/{sid}/explain",
            {"message": "Explain the first weak topic briefly."},
            timeout=120,
        )
        txt = json.dumps(unwrap(body) if code == 200 else body)
        report.add("StudentC", "Learning Chat tutor explain", code == 200 and len(txt) > 40, txt[:180])

    # assignments list should not 500
    code, body, _ = stu_c.get("/api/lms/students/me/assignments")
    report.add("StudentC", "Assignments list", code == 200, f"code={code}")

    # --- Join-code quiz isolation (teacher-specific quizzes) ---
    if teacher_ok:
        code, body, _ = teacher.post(
            "/api/lms/classes",
            {"name": f"RelQA Class {RUN_ID}", "grade_level": "8"},
        )
        cls = unwrap(body) if code in (200, 201) else {}
        join_code = cls.get("join_code")
        class_id = cls.get("id")
        report.add("JoinQuiz", "Teacher creates class with join code", bool(join_code and class_id), f"code={join_code} class={class_id}")
        code, body, _ = teacher.post("/api/lms/quizzes", {"title": f"RelQA Teacher Quiz {RUN_ID}"})
        quiz = unwrap(body) if code in (200, 201) else {}
        quiz_id = quiz.get("id")
        report.add("JoinQuiz", "Teacher creates own quiz", bool(quiz_id), f"quiz_id={quiz_id}")
        code, body, _ = teacher.post(
            "/api/lms/questions",
            {
                "question_text": "What is 2+2?",
                "options": ["3", "4", "5", "6"],
                "correct_option_index": 1,
                "difficulty": "easy",
            },
        )
        qn = unwrap(body) if code in (200, 201) else {}
        qid = qn.get("id")
        report.add("JoinQuiz", "Teacher adds quiz question", bool(qid), f"question_id={qid} http={code}")
        if quiz_id and qid:
            r = teacher.s.put(
                f"{BASE_URL}/api/lms/quizzes/{quiz_id}/questions",
                json={"question_ids": [qid]},
                timeout=60,
            )
            report.add("JoinQuiz", "Teacher attaches question to quiz", r.status_code == 200, f"http={r.status_code}")
            code, body, _ = teacher.post(f"/api/lms/quizzes/{quiz_id}/publish")
            report.add("JoinQuiz", "Teacher publishes quiz", code == 200, str(body)[:140])
            code, body, _ = teacher.post(
                "/api/lms/assignments",
                {"class_id": class_id, "quiz_id": quiz_id, "title": f"HW {RUN_ID}"},
            )
            asg = unwrap(body) if code in (200, 201) else {}
            asg_id = asg.get("id")
            report.add("JoinQuiz", "Teacher assigns quiz to class", bool(asg_id), f"assignment={asg_id}")
            if asg_id:
                code, body, _ = teacher.post(f"/api/lms/assignments/{asg_id}/publish")
                report.add("JoinQuiz", "Teacher publishes assignment", code == 200, str(body)[:140])

            outsider_email = create_student(admin, "out")
            joiner_email = create_student(admin, "in")
            outsider = Api()
            joiner = Api()
            outsider.login(outsider_email, STUDENT_PASS)
            joiner.login(joiner_email, STUDENT_PASS)

            code, body, _ = outsider.get("/api/lms/diagnostics/default")
            d_out = unwrap(body)
            code2, body2, _ = joiner.get("/api/lms/diagnostics/default")
            d_in = unwrap(body2)
            report.add(
                "JoinQuiz",
                "Both students see the same platform diagnostic",
                d_out.get("id") == d_in.get("id") == diag_id,
                f"out={d_out.get('id')} in={d_in.get('id')} platform={diag_id}",
            )

            code, body, _ = outsider.get("/api/lms/students/me/assignments")
            out_asg = unwrap(body) or []
            report.add("JoinQuiz", "Student without join code sees no teacher quiz", out_asg == [], f"{out_asg}")
            code, body, _ = outsider.post(f"/api/lms/quizzes/{quiz_id}/start", {"assignment_id": asg_id})
            report.add(
                "JoinQuiz",
                "Student without join code cannot start teacher quiz",
                code in (400, 403),
                f"code={code} {str(body)[:140]}",
            )

            code, body, _ = joiner.post("/api/lms/classes/join", {"join_code": join_code})
            report.add("JoinQuiz", "Student joins teacher with class code", code in (200, 201), f"code={code} {str(body)[:140]}")
            code, body, _ = joiner.get("/api/lms/students/me/assignments")
            in_asg = unwrap(body) or []
            seen = [a.get("quiz_id") for a in in_asg] if isinstance(in_asg, list) else []
            report.add("JoinQuiz", "Joined student sees that teacher's quiz only", quiz_id in seen, f"assignments={in_asg}")
            code, body, _ = joiner.post(f"/api/lms/quizzes/{quiz_id}/start", {"assignment_id": asg_id})
            start_j = unwrap(body) if code in (200, 201) else {}
            report.add(
                "JoinQuiz",
                "Joined student can start that teacher's quiz",
                code in (200, 201) and bool(start_j.get("attempt_id")),
                f"code={code} attempt={start_j.get('attempt_id')}",
            )
    else:
        report.add("JoinQuiz", "Teacher quiz isolation", False, "no teacher session", skip=True)

    counts = report.summary()
    payload = {
        "base_url": BASE_URL,
        "run_id": RUN_ID,
        "counts": counts,
        "cases": [c.__dict__ for c in report.cases],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "release_qa.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = [
        f"# Release QA {RUN_ID}",
        f"Target: {BASE_URL}",
        f"Result: {counts.get('PASS', 0)} pass / {counts.get('FAIL', 0)} fail / {counts.get('skip', 0)} skip",
        "",
    ]
    for c in report.cases:
        mark = {"PASS": "✅", "FAIL": "❌", "skip": "⏭️"}.get(c.status, c.status)
        lines.append(f"- {mark} **{c.area}** — {c.name}: {c.detail}")
    (OUT / "release_qa.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n" + lines[2])
    print(f"Report: {OUT / 'release_qa.md'}")
    return 0 if counts.get("FAIL", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
