#!/usr/bin/env python3
"""Deep E2E: Admin diagnostic (multi-target PDF) → multiple students → learning path + chat."""
from __future__ import annotations

import json
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
PDF_DIR = ROOT / "_qa_audit_tmp" / "qa_run" / "pdfs"
OUT_DIR = ROOT / "_qa_audit_tmp" / "e2e_diagnostic"
BASE_URL = "https://209.23.10.34"
ADMIN_EMAIL = "admin@iqbalai.com"
ADMIN_PASS = "Hamzakhanswati12@"
STUDENT_PASS = "E2eTest2026!"
NUM_STUDENTS = 3
RUN_ID = int(time.time())


@dataclass
class StepResult:
    phase: str
    step: str
    status: str  # pass | fail | skip
    detail: str = ""
    data: str = ""


@dataclass
class E2EReport:
    steps: list[StepResult] = field(default_factory=list)
    students: list[dict] = field(default_factory=list)
    assessment_id: Optional[int] = None

    def log(self, phase: str, step: str, ok: bool, detail: str = "", data: Any = None):
        self.steps.append(
            StepResult(phase, step, "pass" if ok else "fail", detail, json.dumps(data, default=str)[:600] if data else "")
        )
        icon = "OK" if ok else "FAIL"
        line = f"  [{icon}] {phase} / {step}: {detail}"
        print(line.encode("ascii", "replace").decode("ascii"))


report = E2EReport()


class Api:
    def __init__(self):
        self.s = requests.Session()
        self.s.verify = False

    def login(self, email: str, password: str) -> bool:
        r = self.s.post(
            f"{BASE_URL}/auth/login",
            data={"useremail": email, "password": password},
            headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            timeout=60,
        )
        return r.status_code == 200 and r.json().get("success")

    def get(self, path: str, **kw) -> tuple[int, Any]:
        r = self.s.get(f"{BASE_URL}{path}", timeout=kw.pop("timeout", 60), **kw)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text[:400]

    def post_json(self, path: str, body=None, timeout: int = 60, **kw) -> tuple[int, Any]:
        r = self.s.post(f"{BASE_URL}{path}", json=body, timeout=timeout, **kw)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text[:400]

    def put_json(self, path: str, body=None, timeout: int = 60) -> tuple[int, Any]:
        r = self.s.put(f"{BASE_URL}{path}", json=body, timeout=timeout)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text[:400]

    def delete(self, path: str) -> tuple[int, Any]:
        r = self.s.delete(f"{BASE_URL}{path}", timeout=60)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text[:400]


def unwrap(body: Any) -> Any:
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def ensure_pdfs() -> dict[str, Path]:
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    import pymupdf

    def make(name: str, lines: list[str]) -> Path:
        p = PDF_DIR / name
        if p.exists() and p.stat().st_size > 100:
            return p
        doc = pymupdf.open()
        page = doc.new_page()
        y = 50
        for line in lines:
            page.insert_text((50, y), line, fontsize=10)
            y += 14
        doc.save(str(p))
        doc.close()
        return p

    pdfs = {
        "diag": make(
            "diag_qa_small.pdf",
            [
                "E2E Diagnostic Q&A",
                "1. Solve 2+2? A)3 B)4 C)5 D)6 Answer:B",
                "2. Capital UK? A)Paris B)London C)Berlin D)Rome Answer:B",
                "3. Water formula? A)CO2 B)H2O C)O2 D)NaCl Answer:B",
                "4. 3*4=? A)10 B)11 C)12 D)13 Answer:C",
                "5. Sun is a? A)planet B)star C)moon D)comet Answer:B",
                "6. 10-4=? A)5 B)6 C)7 D)8 Answer:B",
                "7. Largest ocean? A)Atlantic B)Indian C)Pacific D)Arctic Answer:C",
                "8. Triangle sides? A)2 B)3 C)4 D)5 Answer:B",
            ],
        ),
        "t1": make("target_content_1.pdf", ["Algebra Unit", "Linear equations ax+b=c", "Solve using inverse operations."]),
        "t2": make("target_content_2.pdf", ["Geometry Unit", "Area of rectangle = length times width", "Pythagorean theorem a^2+b^2=c^2"]),
        "t3": make("target_content_3.pdf", ["Science Unit", "Photosynthesis uses sunlight and CO2", "Plants produce glucose and oxygen."]),
    }
    return pdfs


def poll_progress(admin: Api, job_id: str, timeout: int = 900) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        code, body = admin.get(f"/api/lms/diagnostics/upload-progress/{job_id}")
        last = unwrap(body) if code == 200 else {}
        if last.get("done"):
            return last
        if last.get("error"):
            return last
        time.sleep(4)
    return {"error": "timeout", **last}


def admin_setup_diagnostic(admin: Api, pdfs: dict[str, Path]) -> Optional[int]:
    print("\n=== PHASE 1: Admin diagnostic upload (3 target PDFs) ===")
    code, body = admin.get("/api/lms/admin/diagnostics")
    items = unwrap(body) if code == 200 else []
    for d in items or []:
        if d.get("status") in ("published", "draft"):
            admin.delete(f"/api/lms/admin/diagnostics/{d['id']}")

    job_id = str(uuid.uuid4())
    files = [
        ("title", (None, f"E2E Deep Diagnostic {RUN_ID}")),
        ("progress_job_id", (None, job_id)),
        ("diagnostic_file", ("diag_qa_small.pdf", pdfs["diag"].read_bytes(), "application/pdf")),
        ("target_files", ("target_content_1.pdf", pdfs["t1"].read_bytes(), "application/pdf")),
        ("target_files", ("target_content_2.pdf", pdfs["t2"].read_bytes(), "application/pdf")),
        ("target_files", ("target_content_3.pdf", pdfs["t3"].read_bytes(), "application/pdf")),
    ]
    r = admin.s.post(f"{BASE_URL}/api/lms/diagnostics/from-pdf", files=files, timeout=120)
    ok_upload = r.status_code in (200, 201)
    upload_body = r.json() if ok_upload else {"error": r.text[:200]}
    report.log("Admin", "Upload Q&A + 3 targets", ok_upload, f"HTTP {r.status_code}", upload_body)

    prog = poll_progress(admin, job_id)
    report.log("Admin", "Upload progress complete", prog.get("done") and not prog.get("error"), str(prog.get("message", prog)))

    data = unwrap(upload_body) if ok_upload else {}
    aid = data.get("assessment_id") or data.get("id")
    if not aid:
        return None

    code, pub = admin.post_json(f"/api/lms/diagnostics/{aid}/publish", {})
    pub_data = unwrap(pub) if code == 200 else pub
    published = code == 200 and (pub_data or {}).get("status") == "published"
    report.log("Admin", "Publish diagnostic", published, f"id={aid}", pub_data)

    code, body = admin.get(f"/api/lms/diagnostics/{aid}/target-pdfs")
    targets = unwrap(body) if code == 200 else []
    report.log("Admin", "Target PDFs count = 3", len(targets) == 3, f"count={len(targets)}", targets)

    code, body = admin.get("/api/lms/admin/diagnostics")
    listed = unwrap(body) if code == 200 else []
    active = [d for d in (listed or []) if d.get("status") == "published"]
    report.log("Admin", "Single published diagnostic", len(active) == 1, f"published={len(active)}")

    report.assessment_id = aid
    return aid


def create_students(admin: Api, n: int) -> list[dict]:
    print(f"\n=== PHASE 2: Create {n} fresh student accounts ===")
    students = []
    for i in range(1, n + 1):
        email = f"e2e_student_{RUN_ID}_{i}@iqbalai.com"
        username = f"e2e_s{RUN_ID}_{i}"
        r = admin.s.post(
            f"{BASE_URL}/admin/users",
            json={
                "username": username,
                "useremail": email,
                "password": STUDENT_PASS,
                "role": "student",
                "class_standard": "8",
                "medium": "English",
            },
            timeout=30,
        )
        body = r.json()
        ok = r.status_code == 200 and body.get("success")
        uid = body.get("user_id")
        if not ok:
            report.log("Setup", f"Create student {i}", False, body.get("error", r.text[:100]))
            continue
        students.append({"email": email, "username": username, "id": uid, "index": i})
        report.log("Setup", f"Create student {i}", True, email, {"user_id": uid})
    report.students = students
    return students


def run_student_diagnostic(student: Api, assessment_id: int, student_label: str, wrong_ratio: float) -> Optional[dict]:
    """wrong_ratio: 0=all correct, 1=all wrong, 0.5=half wrong for varied weak topics."""
    code, body = student.get("/api/lms/diagnostics/default")
    diag = unwrap(body) if code == 200 else {}
    ok = code == 200 and diag.get("id") == assessment_id
    report.log(student_label, "See published diagnostic", ok, f"id={diag.get('id')}", diag)

    code, body = student.post_json(f"/api/lms/quizzes/{assessment_id}/start", {})
    start = unwrap(body) if code in (200, 201) else {}
    attempt_id = start.get("attempt_id")
    has_timer = "remaining_seconds" in start
    report.log(student_label, "Start diagnostic + timer", bool(attempt_id) and has_timer, json.dumps({k: start.get(k) for k in ['attempt_id','remaining_seconds','expires_at']})[:200])

    if not attempt_id:
        return None

    code, body = student.get(f"/api/lms/attempts/{attempt_id}/questions")
    questions = unwrap(body) if code == 200 else []
    if isinstance(questions, dict):
        questions = questions.get("questions", [])
    report.log(student_label, "Load questions", len(questions) > 0, f"count={len(questions)}")

    for idx, q in enumerate(questions):
        if not isinstance(q, dict):
            continue
        qid = q.get("id") or q.get("question_id")
        correct_idx = q.get("correct_option_index", 0) or 0
        opts = q.get("options") or []
        if wrong_ratio > 0 and (idx % 2 == 0 if wrong_ratio >= 0.5 else idx < len(questions) * wrong_ratio):
            pick = (correct_idx + 1) % max(len(opts), 4)
        else:
            pick = correct_idx
        student.post_json(f"/api/lms/attempts/{attempt_id}/answer", {"question_id": qid, "selected_option_index": pick})

    code, body = student.post_json(f"/api/lms/attempts/{attempt_id}/submit", {})
    result = unwrap(body) if code == 200 else {}
    report.log(
        student_label,
        "Submit diagnostic",
        code == 200 and result.get("diagnostic_completed"),
        f"score={result.get('score_percent')}% weak={len(result.get('weak_topics') or [])}",
        result,
    )

    code, body = student.post_json(f"/api/lms/quizzes/{assessment_id}/start", {})
    blocked = code in (400, 403, 409, 422)
    report.log(student_label, "Retake blocked", blocked, f"code={code}")

    return result


def run_learning_path(student: Api, student_label: str) -> None:
    code, body = student.get("/api/lms/students/me/learning-path")
    path = unwrap(body) if code == 200 else {}
    items = (path or {}).get("items") or []
    report.log(student_label, "Learning path generated", len(items) > 0, f"steps={len(items)}", path)

    if not items:
        code, body = student.post_json("/api/lms/students/me/learning-path", {})
        path = unwrap(body) if code in (200, 201) else path
        items = (path or {}).get("items") or []
        report.log(student_label, "Force-generate learning path", len(items) > 0, f"steps={len(items)}")

    current = (path or {}).get("current_step")
    report.log(student_label, "Has current step", bool(current), (current or {}).get("title", "none"))

    if items:
        first = items[0]
        item_id = first.get("id")
        if item_id and first.get("status") != "completed":
            code, body = student.put_json("/api/lms/students/me/learning-path", {"item_id": item_id})
            done = code == 200
            report.log(student_label, "Mark first path step complete", done, f"item_id={item_id}")

    code, body = student.get("/api/lms/students/me/progress")
    progress = unwrap(body) if code == 200 else {}
    report.log(student_label, "Student progress API", code == 200, f"keys={list((progress or {}).keys())[:5]}")


def run_deficiency_chat(student: Api, student_label: str) -> None:
    code, body = student.post_json("/api/lms/deficiency/sessions", {"force_new": True}, timeout=90)
    sess = unwrap(body) if code in (200, 201) else {}
    sid = sess.get("session_id") or sess.get("id")
    has_q = bool(sess.get("current_question"))
    report.log(student_label, "Start Learning Chat", bool(sid) and has_q, f"session={sid}", sess)

    if not sid:
        return

    q = sess.get("current_question") or {}
    opts = q.get("options") or []
    if opts:
        code, ans = student.post_json(
            f"/api/lms/deficiency/sessions/{sid}/answer",
            {"selected_option_index": 0},
            timeout=60,
        )
        report.log(student_label, "Answer practice question", code == 200, str(ans)[:150])

    code, expl = student.post_json(
        f"/api/lms/deficiency/sessions/{sid}/explain",
        {"message": "Explain this topic using the study material from my target PDFs"},
        timeout=120,
    )
    reply = unwrap(expl) if code == 200 else {}
    has_reply = bool((reply or {}).get("reply") or (reply or {}).get("explanation"))
    report.log(student_label, "Tutor explain (PDF-grounded)", has_reply, (reply or {}).get("reply", "")[:120])


def run_playwright_journey(email: str, password: str) -> None:
    print("\n=== PHASE 5: Browser E2E (end-user UI) ===")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        report.log("Browser", "Playwright available", False, "not installed")
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    label = f"Browser-{email.split('@')[0]}"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(ignore_https_errors=True)
            page = ctx.new_page()

            page.goto(f"{BASE_URL}/auth/login", wait_until="domcontentloaded", timeout=60000)
            page.fill('input[name="useremail"], input#useremail', email)
            page.fill('input[name="password"], input#password', password)
            page.click('button[type="submit"]')
            page.wait_for_timeout(3000)

            page.goto(f"{BASE_URL}/student-dashboard", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)
            report.log(label, "Student dashboard loads", "student" in page.content().lower() or page.url.find("student") >= 0)

            # Learning path visible
            has_path = "Learning Path" in page.content() or "learning path" in page.content().lower()
            report.log(label, "Learning Path section visible", has_path)
            page.screenshot(path=str(OUT_DIR / "student_dashboard.png"))

            # Open Learning Chat via JS if diagnostic done
            page.evaluate("() => { if (typeof openDeficiencyChat === 'function') openDeficiencyChat(); }")
            page.wait_for_timeout(4000)
            modal = page.locator("#lmsDeficiencyModal")
            chat_visible = modal.count() > 0 and modal.is_visible()
            report.log(label, "Learning Chat modal opens", chat_visible)
            if chat_visible:
                page.screenshot(path=str(OUT_DIR / "learning_chat.png"))
                # Click first MCQ option if present
                opt = page.locator(".lms-mcq-option, .lms-def-option, button[data-option-index='0']").first
                if opt.count() > 0:
                    opt.click()
                    page.wait_for_timeout(1500)

            browser.close()
    except Exception as exc:
        report.log(label, "Browser journey", False, str(exc)[:300])


def write_report() -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    passed = sum(1 for s in report.steps if s.status == "pass")
    failed = sum(1 for s in report.steps if s.status == "fail")
    summary = {
        "run_id": RUN_ID,
        "base_url": BASE_URL,
        "assessment_id": report.assessment_id,
        "students": report.students,
        "passed": passed,
        "failed": failed,
        "total": len(report.steps),
    }
    payload = {"summary": summary, "steps": [s.__dict__ for s in report.steps]}
    (OUT_DIR / "e2e_report.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Diagnostic E2E Deep Test Report",
        f"\n**Server:** {BASE_URL}  \n**Run ID:** {RUN_ID}  \n**Assessment ID:** {report.assessment_id}",
        f"\n**Result:** {passed} pass / {failed} fail / {len(report.steps)} total\n",
    ]
    for s in report.steps:
        icon = "✅" if s.status == "pass" else "❌"
        lines.append(f"- {icon} **{s.phase}** — {s.step}: {s.detail}")
    (OUT_DIR / "e2e_report.md").write_text("\n".join(lines), encoding="utf-8")
    return summary


def main() -> int:
    print(f"Deep E2E diagnostic test -> {BASE_URL}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    pdfs = ensure_pdfs()
    admin = Api()
    if not admin.login(ADMIN_EMAIL, ADMIN_PASS):
        print("Admin login failed")
        return 1

    aid = admin_setup_diagnostic(admin, pdfs)
    if not aid:
        write_report()
        return 1

    students = create_students(admin, NUM_STUDENTS)
    if not students:
        write_report()
        return 1

    print("\n=== PHASE 3: Students take diagnostic ===")
    ratios = [0.0, 0.6, 0.9]  # strong, mixed, weak
    for st, ratio in zip(students, ratios):
        label = f"Student-{st['index']}"
        s = Api()
        s.login(st["email"], STUDENT_PASS)
        result = run_student_diagnostic(s, aid, label, ratio)
        st["score_percent"] = (result or {}).get("score_percent")
        st["weak_topics"] = len((result or {}).get("weak_topics") or [])

    print("\n=== PHASE 4: Learning path + deficiency chat ===")
    for st in students:
        label = f"Student-{st['index']}"
        s = Api()
        s.login(st["email"], STUDENT_PASS)
        try:
            run_learning_path(s, label)
            run_deficiency_chat(s, label)
        except Exception as exc:
            report.log(label, "Learning flow error", False, str(exc)[:200])

    if students:
        try:
            run_playwright_journey(students[0]["email"], STUDENT_PASS)
        except Exception as exc:
            report.log("Browser", "Journey error", False, str(exc)[:200])

    summary = write_report()
    print(f"\n{'='*50}")
    print(f"DONE: {summary['passed']} pass, {summary['failed']} fail")
    print(f"Report: {OUT_DIR / 'e2e_report.md'}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
