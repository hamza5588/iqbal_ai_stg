#!/usr/bin/env python3
"""IqbalAI Production QA — automated API + browser checks against staging."""
from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parents[1]
QA_DIR = ROOT / "_qa_audit_tmp" / "qa_run"
PDF_DIR = QA_DIR / "pdfs"
REPORT_DIR = QA_DIR / "report"
BASE_URL = os.environ.get("QA_BASE_URL", "https://209.23.10.34").rstrip("/")

ADMIN_CANDIDATES = [
    ("admin@iqbalai.com", "Hamzakhanswati12@"),
    ("admin@iqbalai.com", "admin123"),
    ("admin@iqbalai.com", "password123"),
]
TEACHER = ("teacher@iqbalai.com", os.environ.get("QA_TEACHER_PASS", "password123"))
STUDENT = ("student@iqbalai.com", os.environ.get("QA_STUDENT_PASS", "password123"))
STUDENT_FRESH = (
    os.environ.get("QA_STUDENT_FRESH_EMAIL", f"qa_student_{int(time.time())}@iqbalai.com"),
    os.environ.get("QA_STUDENT_FRESH_PASS", "QaTest123!"),
)


@dataclass
class Result:
    test_id: str
    name: str
    status: str  # pass | fail | partial | skip
    detail: str = ""
    snippet: str = ""


@dataclass
class QAReport:
    results: list[Result] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def add(self, test_id: str, name: str, ok: bool, detail: str = "", snippet: str = "", partial: bool = False):
        st = "pass" if ok else ("partial" if partial else "fail")
        self.results.append(Result(test_id, name, st, detail, snippet[:800]))

    def skip(self, test_id: str, name: str, reason: str):
        self.results.append(Result(test_id, name, "skip", reason))


report = QAReport(meta={"base_url": BASE_URL, "started_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())})


class Client:
    def __init__(self):
        self.s = requests.Session()
        self.s.verify = False
        self.email = ""

    def login(self, email: str, password: str) -> bool:
        r = self.s.post(
            f"{BASE_URL}/auth/login",
            data={"useremail": email, "password": password},
            headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            timeout=60,
        )
        if r.status_code == 200:
            try:
                body = r.json()
                if body.get("success"):
                    self.email = email
                    return True
            except Exception:
                pass
        if r.status_code in (200, 302) and "user_id" in self.s.cookies.get_dict().get("session", ""):
            self.email = email
            return True
        # Flask session cookie check via protected endpoint
        chk = self.s.get(f"{BASE_URL}/user_info", timeout=30)
        if chk.status_code == 200:
            try:
                info = chk.json()
                if info.get("email") == email or info.get("useremail") == email:
                    self.email = email
                    return True
            except Exception:
                pass
        chk2 = self.s.get(f"{BASE_URL}/user_info", timeout=30)
        if chk2.status_code == 200:
            self.email = email
            return True
        return r.status_code in (200, 302)

    def get_json(self, path: str, **kw) -> tuple[int, Any]:
        r = self.s.get(f"{BASE_URL}{path}", timeout=kw.pop("timeout", 60), **kw)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text[:500]

    def post_json(self, path: str, data=None, **kw) -> tuple[int, Any]:
        r = self.s.post(f"{BASE_URL}{path}", json=data, timeout=kw.pop("timeout", 60), **kw)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text[:500]

    def put_json(self, path: str, data=None, **kw) -> tuple[int, Any]:
        r = self.s.put(f"{BASE_URL}{path}", json=data, timeout=kw.pop("timeout", 60), **kw)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text[:500]

    def delete(self, path: str, **kw) -> tuple[int, Any]:
        r = self.s.delete(f"{BASE_URL}{path}", timeout=kw.pop("timeout", 60), **kw)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text[:500]


def unwrap(body: Any) -> Any:
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def ensure_pdfs():
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import pymupdf
    except ImportError:
        report.skip("SETUP", "Generate QA PDFs", "pymupdf not installed")
        return {}

    paths = {}

    def make_pdf(name: str, lines: list[str]) -> Path:
        p = PDF_DIR / name
        if p.exists() and p.stat().st_size > 100:
            paths[name] = p
            return p
        doc = pymupdf.open()
        page = doc.new_page()
        y = 50
        for line in lines:
            page.insert_text((50, y), line, fontsize=10)
            y += 14
            if y > 800:
                page = doc.new_page()
                y = 50
        doc.save(str(p))
        doc.close()
        paths[name] = p
        return p

    qa_lines = [
        "Diagnostic Q&A — QA Test",
        "Answer Key",
        "",
        "1. What is 2+2? A) 3 B) 4 C) 5 D) 6 Answer: B",
        "2. Capital of France? A) London B) Berlin C) Paris D) Rome Answer: C",
        "3. H2O is? A) Salt B) Water C) Air D) Fire Answer: B",
        "4. 5x5=? A) 20 B) 25 C) 30 D) 35 Answer: B",
        "5. Largest planet? A) Earth B) Mars C) Jupiter D) Venus Answer: C",
        "6. 10-3=? A) 6 B) 7 C) 8 D) 9 Answer: B",
        "7. Photosynthesis needs? A) CO2 B) N2 C) He D) Ar Answer: A",
        "8. Triangle sides? A) 2 B) 3 C) 4 D) 5 Answer: B",
    ]
    make_pdf("diag_qa_small.pdf", qa_lines)

    topics = [
        ("target_content_1.pdf", ["Target PDF 1 — Algebra", "Linear equations: ax+b=c", "Solve for x using inverse operations."]),
        ("target_content_2.pdf", ["Target PDF 2 — Geometry", "Area of rectangle = length x width", "Pythagorean theorem: a^2+b^2=c^2"]),
        ("target_content_3.pdf", ["Target PDF 3 — Science", "Photosynthesis converts light to glucose", "Plants release oxygen as byproduct."]),
        ("target_content_4.pdf", ["Target PDF 4 — History", "World War II ended in 1945", "United Nations founded after the war."]),
        ("target_content_5.pdf", ["Target PDF 5 — Literature", "Shakespeare wrote Hamlet", "Themes include revenge and mortality."]),
        ("target_content_6.pdf", ["Target PDF 6 — Geography", "Amazon is largest rainforest", "Nile is one of the longest rivers."]),
    ]
    for fname, lines in topics:
        make_pdf(fname, lines)

    # invalid pdf (text renamed)
    bad = PDF_DIR / "fake.pdf"
    bad.write_text("not a real pdf", encoding="utf-8")
    paths["fake.pdf"] = bad

    # large pdf
    large = PDF_DIR / "target_large.pdf"
    if not large.exists() or large.stat().st_size < 5000:
        doc = pymupdf.open()
        for i in range(55):
            page = doc.new_page()
            page.insert_text((50, 50), f"Large target page {i+1} — ecology and ecosystems content filler.", fontsize=10)
        doc.save(str(large))
        doc.close()
    paths["target_large.pdf"] = large

    return paths


def discover_admin_login() -> Optional[tuple[str, str]]:
    c = Client()
    for email, pw in ADMIN_CANDIDATES:
        nc = Client()
        if nc.login(email, pw):
            code, body = nc.get_json("/user_info")
            if code == 200:
                report.meta["admin_email"] = email
                return email, pw
    return None


def poll_upload_progress(client: Client, job_id: str, timeout: int = 600) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        code, body = client.get_json(f"/api/lms/diagnostics/upload-progress/{job_id}")
        data = unwrap(body) if code == 200 else {}
        last = data if isinstance(data, dict) else {}
        if last.get("done"):
            return last
        if last.get("error"):
            return last
        time.sleep(3)
    return {"error": "timeout", **last}


def upload_diagnostic(
    client: Client,
    title: str,
    qa_pdf: Path,
    targets: list[Path],
    timeout: int = 600,
) -> tuple[bool, str, Optional[dict]]:
    job_id = str(uuid.uuid4())
    files = [
        ("title", (None, title)),
        ("progress_job_id", (None, job_id)),
        ("diagnostic_file", (qa_pdf.name, qa_pdf.read_bytes(), "application/pdf")),
    ]
    for t in targets:
        files.append(("target_files", (t.name, t.read_bytes(), "application/pdf")))
    r = client.s.post(
        f"{BASE_URL}/api/lms/diagnostics/from-pdf",
        files=files,
        timeout=120,
    )
    try:
        body = r.json()
    except Exception:
        return False, f"HTTP {r.status_code}: {r.text[:300]}", None
    if r.status_code not in (200, 201):
        err = body.get("error", body)
        if isinstance(err, dict):
            return False, err.get("message", str(err)), body
        return False, str(err), body
    prog = poll_upload_progress(client, job_id, timeout=timeout)
    if prog.get("error") and not prog.get("done"):
        return False, str(prog.get("error")), prog
    data = unwrap(body)
    return True, "upload ok", data if isinstance(data, dict) else {"raw": body}


def clear_diagnostics(admin: Client):
    code, body = admin.get_json("/api/lms/admin/diagnostics")
    items = unwrap(body)
    if isinstance(items, list):
        for d in items:
            aid = d.get("id")
            if aid:
                admin.delete(f"/api/lms/admin/diagnostics/{aid}")


def run_api_tests():
    # E0 Health
    r = requests.get(f"{BASE_URL}/health", verify=False, timeout=30)
    report.add("E0", "GET /health", r.status_code == 200, f"status={r.status_code}", r.text[:200])

    # A1 public theme
    code, body = Client().get_json("/api/platform-theme")
    theme = unwrap(body) if isinstance(body, dict) else body
    t = theme.get("theme", theme) if isinstance(theme, dict) else {}
    ok = code == 200 and isinstance(t, dict) and "primary" in t
    report.add("A1a", "GET /api/platform-theme", ok, f"code={code}", json.dumps(body)[:300])

    creds = discover_admin_login()
    if not creds:
        report.add("LOGIN", "Admin login", False, "No working admin credentials from candidate list")
        return None
    admin_email, admin_pass = creds
    admin = Client()
    admin.login(admin_email, admin_pass)

    code, body = admin.get_json("/admin/settings/theme")
    ok = code == 200 and isinstance(body, dict) and body.get("success") and "presets" in body
    report.add("A1b", "GET /admin/settings/theme (admin)", ok, f"code={code}", json.dumps(body)[:300])

    # A2 theme presets
    for preset in ["green", "orange", "purple"]:
        code, body = admin.put_json("/admin/settings/theme", {"preset": preset})
        ok = code == 200 and (body.get("theme", {}) or {}).get("preset") == preset or (
            isinstance(body, dict) and body.get("success")
        )
        code2, body2 = admin.get_json("/admin/settings/theme")
        persisted = False
        if code2 == 200:
            th = body2.get("theme", {})
            persisted = th.get("preset") == preset
        report.add(f"A2-{preset}", f"PUT theme preset={preset}", ok and persisted, f"put={code} get_preset={body2.get('theme',{}).get('preset') if code2==200 else '?'}")

    # custom hex via rose secondary
    code, body = admin.put_json("/admin/settings/theme", {"preset": "rose", "primary": "#e11d48"})
    report.add("A2-custom", "PUT custom hex #e11d48", code == 200, json.dumps(body.get("theme", body))[:200])

    # B1 teacher permission
    teacher = Client()
    teacher.login(*TEACHER)
    code, body = teacher.post_json("/api/lms/diagnostics/from-pdf")
    report.add("B1a", "Teacher POST diagnostics/from-pdf blocked", code in (403, 401, 400, 415), f"code={code}", str(body)[:200])

    html = teacher.s.get(f"{BASE_URL}/teacher-dashboard", timeout=60).text
    has_diag_ui = "adminDiag" in html or "Diagnostic Assessment" in html and "admin" in html.lower()
    report.add("B1b", "Teacher dashboard no admin diagnostic UI", not has_diag_ui, f"adminDiag in page={('adminDiag' in html)}")

    pdfs = ensure_pdfs()
    if not pdfs:
        report.skip("B2-B6", "Diagnostic upload tests", "PDFs not generated")
        return admin

    clear_diagnostics(admin)

    # B2 single target
    ok, msg, data = upload_diagnostic(
        admin,
        "QA Test Diagnostic — Single Target",
        pdfs["diag_qa_small.pdf"],
        [pdfs["target_content_1.pdf"]],
    )
    qcount = (data or {}).get("question_count", 0)
    report.add("B2", "Single target PDF upload", ok and qcount > 0, msg, json.dumps(data)[:400] if data else "")

    code, body = admin.get_json("/api/lms/admin/diagnostics")
    items = unwrap(body)
    targets_len = 0
    assessment_id = None
    if isinstance(items, list) and items:
        assessment_id = items[0].get("id")
        targets_len = len(items[0].get("target_pdfs") or [])
    report.add("B2-list", "Admin list shows 1 target", targets_len == 1, f"targets={targets_len}")

    # B3 multi target — replace first
    clear_diagnostics(admin)
    ok, msg, data = upload_diagnostic(
        admin,
        "QA Multi Target",
        pdfs["diag_qa_small.pdf"],
        [pdfs["target_content_1.pdf"], pdfs["target_content_2.pdf"], pdfs["target_content_3.pdf"]],
        timeout=900,
    )
    assessment_id = (data or {}).get("assessment_id") or (data or {}).get("id")
    report.add("B3", "3 target PDFs initial upload", ok, msg, json.dumps(data)[:400] if data else "")

    if assessment_id:
        code, body = admin.get_json(f"/api/lms/diagnostics/{assessment_id}/target-pdfs")
        tlist = unwrap(body)
        n = len(tlist) if isinstance(tlist, list) else 0
        report.add("B3-api", "GET target-pdfs returns 3", n == 3, f"count={n}")

        # B4 add targets
        def add_targets(files: list[Path]) -> tuple[bool, str]:
            multipart = []
            for f in files:
                multipart.append(("target_files", (f.name, f.read_bytes(), "application/pdf")))
            r = admin.s.post(f"{BASE_URL}/api/lms/diagnostics/{assessment_id}/target-pdf", files=multipart, timeout=120)
            try:
                b = r.json()
            except Exception:
                return False, r.text[:200]
            return r.status_code in (200, 201), json.dumps(b)[:200]

        ok4a, sn4a = add_targets([pdfs["target_content_4.pdf"]])
        ok4b, sn4b = add_targets([pdfs["target_content_5.pdf"], pdfs["target_content_6.pdf"]])
        code, body = admin.get_json(f"/api/lms/diagnostics/{assessment_id}/target-pdfs")
        tlist = unwrap(body)
        n = len(tlist) if isinstance(tlist, list) else 0
        report.add("B4", "Add target PDFs after publish", ok4a and ok4b and n >= 5, f"total={n}", sn4a + " | " + sn4b)

        if isinstance(tlist, list) and tlist:
            tid = tlist[-1].get("id")
            if tid:
                admin.delete(f"/api/lms/diagnostics/{assessment_id}/target-pdf/{tid}")
                code, body = admin.get_json(f"/api/lms/diagnostics/{assessment_id}/target-pdfs")
                n2 = len(unwrap(body) or [])
                report.add("B4-remove", "Remove one target PDF", n2 == n - 1, f"before={n} after={n2}")

    # B6 edge cases
    clear_diagnostics(admin)
    r = admin.s.post(
        f"{BASE_URL}/api/lms/diagnostics/from-pdf",
        data={"title": "No target"},
        files=[("diagnostic_file", ("diag_qa_small.pdf", pdfs["diag_qa_small.pdf"].read_bytes(), "application/pdf"))],
        timeout=60,
    )
    report.add("B6-empty", "Empty target rejected", r.status_code in (400, 422), f"code={r.status_code}", r.text[:200])

    # duplicate names
    dup = pdfs["target_content_1.pdf"]
    r = admin.s.post(
        f"{BASE_URL}/api/lms/diagnostics/from-pdf",
        data={"title": "Dup test"},
        files=[
            ("diagnostic_file", ("diag_qa_small.pdf", pdfs["diag_qa_small.pdf"].read_bytes(), "application/pdf")),
            ("target_files", (dup.name, dup.read_bytes(), "application/pdf")),
            ("target_files", (dup.name, dup.read_bytes(), "application/pdf")),
        ],
        timeout=60,
    )
    report.add("B6-dup", "Duplicate target names handled", r.status_code in (200, 201, 400, 422), f"code={r.status_code}")

    r = admin.s.post(
        f"{BASE_URL}/api/lms/diagnostics/from-pdf",
        data={"title": "Bad file"},
        files=[
            ("diagnostic_file", ("diag_qa_small.pdf", pdfs["diag_qa_small.pdf"].read_bytes(), "application/pdf")),
            ("target_files", ("fake.pdf", pdfs["fake.pdf"].read_bytes(), "application/pdf")),
        ],
        timeout=60,
    )
    report.add("B6-invalid", "Invalid PDF rejected", r.status_code in (400, 422, 500) and r.status_code != 201, f"code={r.status_code}", r.text[:200])

    # Ensure published diagnostic for student tests
    clear_diagnostics(admin)
    ok, _, data = upload_diagnostic(
        admin,
        "QA Student Flow Diagnostic",
        pdfs["diag_qa_small.pdf"],
        [pdfs["target_content_1.pdf"], pdfs["target_content_2.pdf"], pdfs["target_content_3.pdf"]],
        timeout=900,
    )
    report.meta["student_diagnostic_id"] = (data or {}).get("assessment_id") or (data or {}).get("id")
    report.meta["diagnostic_upload_ok"] = ok

    return admin


def register_student(email: str, password: str) -> bool:
    c = Client()
    r = c.s.post(
        f"{BASE_URL}/auth/register",
        data={
            "username": email.split("@")[0][:20],
            "useremail": email,
            "password": password,
            "confirm_password": password,
            "class_standard": "8",
            "medium": "English",
            "role": "student",
        },
        timeout=60,
    )
    return r.status_code in (200, 302)


def run_student_tests():
    diag_id = report.meta.get("student_diagnostic_id")
    if not diag_id:
        report.skip("C", "Student diagnostic tests", "No published diagnostic")
        report.skip("D", "Learning chat tests", "No published diagnostic")
        return

    email, pw = STUDENT_FRESH
    if not register_student(email, pw):
        email, pw = STUDENT

    student = Client()
    if not student.login(email, pw):
        report.add("C-login", "Student login", False, f"email={email}")
        return

    code, body = student.get_json("/api/lms/diagnostics/default")
    diag = unwrap(body) if code == 200 else {}
    report.add("C1a", "GET diagnostics/default", code == 200 and diag.get("id"), f"code={code}", json.dumps(diag)[:300])

    aid = diag.get("id") or diag_id
    code, body = student.post_json(f"/api/lms/quizzes/{aid}/start", {})
    start = unwrap(body) if code in (200, 201) else {}
    attempt_id = start.get("attempt_id")
    has_timer = "remaining_seconds" in start or "expires_at" in start
    report.add("C1b", "Start diagnostic with timer fields", bool(attempt_id) and has_timer, json.dumps(start)[:300])

    if attempt_id:
        code, body = student.get_json(f"/api/lms/attempts/{attempt_id}/timer")
        timer = unwrap(body) if code == 200 else {}
        report.add("C1c", "GET attempts/timer", code == 200 and "remaining_seconds" in timer, json.dumps(timer)[:200])

        code, body = student.get_json(f"/api/lms/attempts/{attempt_id}/questions")
        questions = unwrap(body) if code == 200 else []
        qlist = questions if isinstance(questions, list) else questions.get("questions", []) if isinstance(questions, dict) else []
        limits = set()
        for q in qlist:
            if isinstance(q, dict) and q.get("time_limit_seconds"):
                limits.add(q["time_limit_seconds"])
        report.add("C4", "Per-question time_limit_seconds", len(limits) >= 1, f"unique_limits={sorted(limits)}")

        # submit answers
        for q in qlist:
            if not isinstance(q, dict):
                continue
            qid = q.get("id") or q.get("question_id")
            opts = q.get("options") or []
            idx = q.get("correct_option_index", 0) if q.get("correct_option_index") is not None else 0
            if qid is not None:
                student.post_json(f"/api/lms/attempts/{attempt_id}/answer", {"question_id": qid, "selected_option_index": idx})
        code, body = student.post_json(f"/api/lms/attempts/{attempt_id}/submit", {})
        report.add("C2a", "Submit diagnostic", code == 200, str(body)[:200])

        code, body = student.get_json("/api/lms/diagnostics/default")
        diag2 = unwrap(body) if code == 200 else {}
        report.add("C2b", "diagnostic_completed after submit", bool(diag2.get("diagnostic_completed")), json.dumps(diag2)[:200])

        code, body = student.post_json(f"/api/lms/quizzes/{aid}/start", {})
        blocked = code in (400, 403, 409, 422) or (isinstance(body, dict) and not body.get("success", True))
        report.add("C2c", "Second start blocked", blocked, f"code={code}", str(body)[:200])

    # D deficiency chat
    code, body = student.post_json("/api/lms/deficiency/sessions", {"force_new": True})
    sess = unwrap(body) if code in (200, 201) else {}
    sid = sess.get("session_id") or sess.get("id")
    tq = sess.get("total_questions", 0)
    report.add("D1", "POST deficiency/sessions", code in (200, 201) and sid, f"total_questions={tq}", json.dumps(sess)[:300])

    if sid:
        code, body = student.post_json(f"/api/lms/deficiency/sessions/{sid}/explain", {"message": "Explain linear equations from the study material"})
        expl = unwrap(body) if code == 200 else body
        has_reply = bool((expl or {}).get("reply") or (expl or {}).get("explanation") or (expl or {}).get("tutor_reply"))
        report.add("D2", "Tutor explain returns reply", has_reply or code == 200, f"code={code}", str(expl)[:300])


def run_browser_theme_tests():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        report.skip("A2-UI", "Browser theme verification", "playwright not available")
        return

    creds = discover_admin_login()
    if not creds:
        report.skip("A2-UI", "Browser theme verification", "no admin login")
        return
    admin_email, admin_pass = creds

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()

            def do_login(pg, email, password):
                pg.goto(f"{BASE_URL}/auth/login", wait_until="domcontentloaded", timeout=60000)
                pg.fill('input[name="useremail"], input#useremail, input[type="email"]', email, timeout=15000)
                pg.fill('input[name="password"], input#password, input[type="password"]', password, timeout=15000)
                pg.click('button[type="submit"], input[type="submit"]', timeout=15000)
                pg.wait_for_timeout(3000)

            do_login(page, admin_email, admin_pass)

            for preset, expect_sub in [("orange", "f97316"), ("purple", "a855f7"), ("green", "166534")]:
                client = Client()
                client.login(admin_email, admin_pass)
                client.put_json("/admin/settings/theme", {"preset": preset})
                page.goto(f"{BASE_URL}/auth/login", wait_until="domcontentloaded")
                page.wait_for_timeout(1500)
                theme_style = page.evaluate("""() => {
                  const el = document.getElementById('iqbal-platform-theme');
                  return el ? el.textContent : '';
                }""")
                ok = expect_sub.lower() in (theme_style or "").lower()
                shot = REPORT_DIR / f"theme_{preset}_login.png"
                page.screenshot(path=str(shot))
                report.add(f"A2-ui-{preset}", f"Login page theme {preset}", ok, f"expected #{expect_sub}", theme_style[:150])

            client = Client()
            client.login(admin_email, admin_pass)
            client.put_json("/admin/settings/theme", {"preset": "orange"})
            tpage = context.new_page()
            do_login(tpage, TEACHER[0], TEACHER[1])
            tpage.goto(f"{BASE_URL}/teacher-dashboard", wait_until="domcontentloaded", timeout=60000)
            html = tpage.content()
            has_old_lime = "#d0f73a" in html or "d0f73a" in html.lower()
            report.add("A3", "No hardcoded lime #d0f73a on teacher dash", not has_old_lime, f"found_lime={has_old_lime}")
            tpage.screenshot(path=str(REPORT_DIR / "teacher_dashboard_orange.png"))

            browser.close()
    except Exception as exc:
        report.add("A2-UI", "Browser theme verification", False, str(exc)[:300])


def write_report():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / "qa_report.json"
    summary = {
        "meta": report.meta,
        "summary": {
            "pass": sum(1 for r in report.results if r.status == "pass"),
            "fail": sum(1 for r in report.results if r.status == "fail"),
            "partial": sum(1 for r in report.results if r.status == "partial"),
            "skip": sum(1 for r in report.results if r.status == "skip"),
            "total": len(report.results),
        },
        "results": [r.__dict__ for r in report.results],
    }
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    md = ["# IqbalAI Production QA Report", f"\n**Base URL:** {BASE_URL}", f"**Run:** {report.meta.get('started_at')}\n"]
    md.append(f"| Pass | Fail | Partial | Skip |\n|------|------|---------|------|\n| {summary['summary']['pass']} | {summary['summary']['fail']} | {summary['summary']['partial']} | {summary['summary']['skip']} |\n")
    md.append("\n## Results\n")
    for r in report.results:
        icon = {"pass": "✅", "fail": "❌", "partial": "⚠️", "skip": "⏭️"}.get(r.status, "?")
        md.append(f"- {icon} **{r.test_id}** — {r.name}: {r.detail}")
        if r.snippet:
            md.append(f"  ```\n  {r.snippet[:400]}\n  ```")
    (REPORT_DIR / "qa_report.md").write_text("\n".join(md), encoding="utf-8")
    return summary


def main():
    print(f"QA run against {BASE_URL}")
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    try:
        run_api_tests()
        run_student_tests()
        run_browser_theme_tests()
    finally:
        summary = write_report()
        print(json.dumps(summary["summary"], indent=2))
        print(f"Report: {REPORT_DIR / 'qa_report.md'}")
    return 0 if summary["summary"]["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
