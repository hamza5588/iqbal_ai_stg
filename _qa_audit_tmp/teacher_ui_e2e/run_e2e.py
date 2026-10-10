"""Teacher Dashboard (new UI) — end-to-end verification.

Drives the real UI as a teacher (Playwright + installed Chrome); students act through the real LMS APIs
(join class, take the assigned quiz) so analytics have genuine data. Every checklist item from the
integration brief is recorded as PASS / FAIL with evidence in report.json + report.md.

    BASE_URL=http://127.0.0.1:5055 python _qa_audit_tmp/teacher_ui_e2e/run_e2e.py

Accounts come from seed_users.py (local DB only). Set E2E_HEADED=1 to watch.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:5055").rstrip("/")
PASSWORD = os.environ.get("E2E_PASSWORD", "E2eTeacher!2026")
TEACHER = os.environ.get("E2E_TEACHER_EMAIL", "e2e.teacher@iqbalai.local")
STUDENTS = [
    e.strip()
    for e in os.environ.get(
        "E2E_STUDENTS",
        "e2e.student.a@iqbalai.local,e2e.student.b@iqbalai.local,e2e.student.c@iqbalai.local",
    ).split(",")
    if e.strip()
]
CHROME = os.environ.get("CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SHOTS = ROOT / "_qa_audit_tmp" / "teacher_ui_screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)
QA_PDF = ROOT / "diagnostic_qa_math_ix_sindh.pdf"
LESSON_PDF = ROOT / "sample_pdfs"
RUN = time.strftime("%H%M%S")

results: list[dict] = []
console_errors: list[str] = []
bad_responses: list[str] = []
ctx: dict = {}


def check(section: str, name: str):
    def deco(fn):
        def run(*a, **kw):
            t0 = time.time()
            try:
                evidence = fn(*a, **kw)
                results.append({"section": section, "item": name, "status": "PASS", "evidence": evidence or "", "secs": round(time.time() - t0, 1)})
                print(f"PASS  [{section}] {name}  {evidence or ''}")
                return True
            except Exception as exc:  # noqa: BLE001
                results.append({"section": section, "item": name, "status": "FAIL", "evidence": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-1500:], "secs": round(time.time() - t0, 1)})
                print(f"FAIL  [{section}] {name}  {type(exc).__name__}: {str(exc)[:300]}")
                return False
        return run
    return deco


def login_page(page: Page, email: str):
    page.goto(BASE + "/auth/login", wait_until="domcontentloaded")
    page.fill('input[name="useremail"]', email)
    page.fill('input[name="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")


def api_session(pw, email: str):
    req = pw.request.new_context(base_url=BASE)
    r = req.post("/auth/login", form={"useremail": email, "password": PASSWORD}, max_redirects=0)
    assert r.status in (200, 302), f"login {email} -> {r.status}"
    return req


def api_json(req, method: str, url: str, body=None):
    r = req.fetch(url, method=method, data=json.dumps(body) if body is not None else None,
                  headers={"Content-Type": "application/json"} if body is not None else None)
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        raise AssertionError(f"{method} {url} -> {r.status} non-JSON: {r.text()[:200]}")
    if not r.ok:
        raise AssertionError(f"{method} {url} -> {r.status}: {json.dumps(data)[:300]}")
    return data.get("data", data) if isinstance(data, dict) else data


def nav(page: Page, view: str):
    page.click(f'.td-navbtn[data-view="{view}"]')
    expect(page.locator(f"#view-{view}")).to_have_class(re.compile(r"\bactive\b"))
    page.wait_for_load_state("networkidle")


def shot(page: Page, name: str):
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


def toast_or_status(page: Page) -> str:
    return page.locator("body").inner_text()[:0]


# ───────────────────────── Auth / shell ─────────────────────────
@check("Auth/shell", "Teacher login lands on new teacher dashboard")
def t_login(page: Page):
    login_page(page, TEACHER)
    assert "/teacher-dashboard" in page.url, page.url
    expect(page.locator(".td-topbar")).to_be_visible()
    expect(page.locator("#view-lessons")).to_have_class(re.compile(r"\bactive\b"))
    assert page.locator("#chatMessages").count() == 1
    return page.url


@check("Auth/shell", "Top nav shows Lessons, Classes, Quizzes, Analytics, AI Tutor with correct icons")
def t_nav(page: Page):
    items = page.eval_on_selector_all(".td-navbtn", """els => els.map(e => ({
        view: e.dataset.view, label: e.innerText.trim(),
        src: e.querySelector('img').getAttribute('src'),
        ok: e.querySelector('img').complete && e.querySelector('img').naturalWidth > 0 }))""")
    expected = [("lessons", "My Lessons", "my-lessons.png"), ("classes", "Classes", "classes.png"),
                ("quizzes", "Quizzes", "quizzes.png"), ("analytics", "Analytics", "analytics.png"),
                ("tutor", "AI Tutor", "ai-tutor.png")]
    assert [(i["view"], i["label"]) for i in items] == [(v, l) for v, l, _ in expected], items
    for i, (_, _, icon) in zip(items, expected):
        assert i["src"].endswith("/teacher/icons/" + icon) and i["ok"], i
    for view in ("classes", "quizzes", "analytics", "tutor", "lessons"):
        nav(page, view)
        assert page.url.endswith("#" + view), page.url
    return ", ".join(i["label"] for i in items)


@check("Auth/shell", "Logo/branding matches new UI")
def t_logo(page: Page):
    ok = page.eval_on_selector(".td-brand img", "i => i.complete && i.naturalWidth > 0 && i.getAttribute('src')")
    assert ok and ok.endswith("iqbal-ai-logo.png"), ok
    assert page.title().startswith("Teacher Dashboard"), page.title()
    return ok


@check("Auth/shell", "Non-teacher cannot access teacher routes")
def t_rbac(pw):
    student = api_session(pw, STUDENTS[0])
    out = []
    teacher = ctx["teacher_api"]
    r = teacher.get("/legacy/teacher-dashboard", max_redirects=0)
    assert r.status == 404, f"old monolith route still served: {r.status}"
    out.append("legacy route removed (404)")
    for path in ("/teacher-dashboard",):
        r = student.get(path, max_redirects=0)
        body = r.text() if r.status == 200 else ""
        assert r.status in (302, 303, 401, 403) or ("td-topbar" not in body and "lmsQuizHubModal" not in body), (path, r.status)
        out.append(f"student {path} -> {r.status} {r.headers.get('location', '')}")
    anon = pw.request.new_context(base_url=BASE)
    r = anon.get("/teacher-dashboard", max_redirects=0)
    assert r.status in (302, 303, 401), r.status
    out.append(f"anonymous -> {r.status} {r.headers.get('location', '')}")
    r = student.get("/api/lms/classes/mine")
    out.append(f"student /api/lms/classes/mine -> {r.status}")
    return "; ".join(out)


@check("Auth/shell", "Deep link #analytics opens that view")
def t_deeplink(page: Page):
    page.goto(BASE + "/teacher-dashboard#analytics", wait_until="networkidle")
    expect(page.locator("#view-analytics")).to_have_class(re.compile(r"\bactive\b"))
    return page.url


# ───────────────────────── Classes ─────────────────────────
@check("Classes", "Create class form works and persists")
def t_create_class(page: Page, pw):
    nav(page, "classes")
    page.click("text=Create Class")
    expect(page.locator("#tdCreateClassCard")).to_be_visible()
    shot(page, "03_create_class_form")
    page.fill("#tdTeachingGrades", "8")
    page.click("#tdTeachingGradesField >> text=Save")
    expect(page.locator("#tdTeachingGradesHint")).to_contain_text("8")
    name = f"E2E Class 8 Math {RUN}"
    page.fill("#tdClassName", name)
    page.select_option("#tdClassGrade", "8")
    page.fill("#tdClassDescription", "Created by teacher UI E2E")
    page.click("#tdCreateClassSubmit")
    card = page.locator(".td-class-card", has_text=name)
    expect(card).to_be_visible(timeout=15000)
    expect(page.locator("#tdCreateClassCard")).to_be_hidden()
    code = card.locator(".td-code").inner_text().strip()
    classes = api_json(ctx["teacher_api"], "GET", "/api/lms/classes/mine")
    match = [c for c in classes if c["name"] == name]
    assert match and match[0]["join_code"] == code and str(match[0]["grade_level"]) == "8", match
    ctx.update(class_id=match[0]["id"], class_name=name, join_code=code)
    page.reload(wait_until="networkidle")
    nav(page, "classes")
    expect(page.locator(".td-class-card", has_text=name)).to_be_visible()
    return f"class #{ctx['class_id']} code {code} (persists after reload)"


@check("Classes", "Add students (eligible list, multi-select + single add)")
def t_add_students(page: Page):
    card = page.locator(f'.td-class-card[data-class-id="{ctx["class_id"]}"]')
    body = page.locator(f"#tdClassBody-{ctx['class_id']}")
    if not body.is_visible():
        card.locator(".td-class-head h3").click()
    expect(body).to_be_visible()
    body.locator(".td-inner-tabs >> text=Add Students").click()
    rows = body.locator("tbody tr")
    expect(rows.filter(has_text="e2e_student_a")).to_have_count(1, timeout=15000)
    shot(page, "02_classes_add_students")
    rows.filter(has_text="e2e_student_a").locator("input[type=checkbox]").check()
    rows.filter(has_text="e2e_student_b").locator("input[type=checkbox]").check()
    expect(body.locator('[data-slot="count"]')).to_have_text("2 students selected")
    body.locator('[data-slot="addSelected"]').click()
    expect(body.locator("tbody tr", has_text="e2e_student_c")).to_have_count(1, timeout=15000)
    expect(body.locator("tbody tr", has_text="e2e_student_a")).to_have_count(0)
    body.locator("tbody tr", has_text="e2e_student_c").locator("text=Add").click()
    # Other grade-8 students may exist in the DB, so the eligible list need not become empty.
    expect(body.locator("tbody tr", has_text="e2e_student_c")).to_have_count(0, timeout=15000)
    roster = api_json(ctx["teacher_api"], "GET", f"/api/lms/classes/{ctx['class_id']}/students")
    names = sorted(s["username"] for s in roster)
    assert names == ["e2e_student_a", "e2e_student_b", "e2e_student_c"], names
    expect(card.locator(".td-pill", has_text="3 STUDENTS")).to_be_visible()
    return "added a+b (multi-select) and c (row Add); API roster = " + ", ".join(names)


@check("Classes", "Roster displays enrolled students; remove + join-code flow")
def t_roster(page: Page, pw):
    body = page.locator(f"#tdClassBody-{ctx['class_id']}")
    body.locator(".td-inner-tabs >> text=Roster").click()
    rows = body.locator("tbody tr")
    expect(rows).to_have_count(3, timeout=10000)
    shot(page, "04_classes_roster")
    page.once("dialog", lambda d: d.accept())
    rows.filter(has_text="e2e_student_c").locator("text=Remove").click()
    expect(body.locator("tbody tr")).to_have_count(2, timeout=15000)
    # Student C re-joins with the class join code (student-side join flow).
    stu = api_session(pw, STUDENTS[2])
    joined = api_json(stu, "POST", "/api/lms/classes/join", {"join_code": ctx["join_code"]})
    assert joined["class_id"] == ctx["class_id"], joined
    nav(page, "lessons")
    nav(page, "classes")
    body = page.locator(f"#tdClassBody-{ctx['class_id']}")
    if not body.is_visible():  # the expanded class stays expanded across views
        page.locator(f'.td-class-card[data-class-id="{ctx["class_id"]}"] .td-class-head h3').click()
    expect(body.locator("tbody tr")).to_have_count(3, timeout=15000)
    return "3 rows → removed C → 2 rows → C joined via code → 3 rows"


# ───────────────────────── Quizzes ─────────────────────────
@check("Quizzes", "Quiz upload/create path (PDF → MCQ generate → preview → publish)")
def t_quiz_pdf(page: Page):
    nav(page, "quizzes")
    page.click("#view-quizzes >> text=Create Quiz")
    expect(page.locator("#tdCreateQuizCard")).to_be_visible()
    title = f"E2E PDF Quiz {RUN}"
    page.fill("#lmsQuizTitle", title)
    page.set_input_files("#lmsQuizPdfFile", str(QA_PDF))
    expect(page.locator("#tdQuizFileName")).to_have_text(QA_PDF.name)
    page.fill("#lmsQuizMcqCount", "5")
    shot(page, "05_quizzes_create")
    page.click("#lmsQuizSubmitBtn")
    status = page.locator("#lmsQuizStatus")
    deadline = time.time() + 240
    while time.time() < deadline:
        txt = status.inner_text()
        if page.locator("#lmsQuizActions").is_visible() or txt.startswith("Failed") or txt.startswith("Error"):
            break
        page.wait_for_timeout(2000)
    txt = status.inner_text()
    ctx["pdf_quiz_status"] = txt
    if not page.locator("#lmsQuizActions").is_visible():
        raise AssertionError(f"PDF quiz did not complete: {txt!r}")
    expect(page.locator("#lmsQuizPreview .lms-quiz-preview-card").first).to_be_visible()
    n = page.locator("#lmsQuizPreview .lms-quiz-preview-card").count()
    shot(page, "05_quizzes_preview")
    page.click("#lmsQuizActions >> text=Publish Quiz")
    expect(page.locator("#tdCreateQuizCard")).to_be_hidden(timeout=20000)
    card = page.locator(".td-quiz-card", has_text=title)
    expect(card).to_be_visible(timeout=15000)
    expect(card.locator("text=Published")).to_be_visible()
    ctx["pdf_quiz_title"] = title
    return f"{n} MCQs generated; {txt}"


def seed_manual_quiz(teacher_api) -> dict:
    """Existing manual quiz APIs (question bank → quiz → publish): used for analytics data when PDF generation is unavailable."""
    topics = api_json(teacher_api, "GET", "/api/lms/topics?subject=Math")
    topic_ids = [t["id"] for t in topics[:2]] if topics else [None, None]
    qids = []
    bank = [
        ("What is 5 + 3?", ["6", "7", "8", "9"], 2), ("Which of the following is a prime number?", ["4", "6", "7", "9"], 2),
        ("What is 12 ÷ 3?", ["2", "3", "4", "5"], 2), ("Which shape has 4 equal sides?", ["Rectangle", "Square", "Triangle", "Circle"], 1),
    ]
    for i, (text, opts, correct) in enumerate(bank):
        q = api_json(teacher_api, "POST", "/api/lms/questions", {
            "question_text": text, "options": [{"label": chr(65 + j), "text": o} for j, o in enumerate(opts)],
            "correct_option_index": correct, "topic_id": topic_ids[i % 2] if topic_ids[0] else None})
        qids.append(q["id"])
    quiz = api_json(teacher_api, "POST", "/api/lms/quizzes", {"title": f"E2E Manual Quiz {RUN}"})
    api_json(teacher_api, "PUT", f"/api/lms/quizzes/{quiz['id']}/questions", {"question_ids": qids})
    api_json(teacher_api, "POST", f"/api/lms/quizzes/{quiz['id']}/publish")
    return quiz


@check("Quizzes", "Quizzes list loads (tabs, expand shows MCQs)")
def t_quiz_list(page: Page):
    if not ctx.get("pdf_quiz_title"):
        ctx["manual_quiz"] = seed_manual_quiz(ctx["teacher_api"])
    nav(page, "lessons")
    nav(page, "quizzes")
    quizzes = api_json(ctx["teacher_api"], "GET", "/api/lms/quizzes")
    expect(page.locator(".td-quiz-card")).to_have_count(len(quizzes), timeout=15000)
    expect(page.locator("#tdQuizTabAll")).to_have_text(f"All Quizzes ({len(quizzes)})")
    title = ctx.get("pdf_quiz_title") or ctx["manual_quiz"]["title"]
    card = page.locator(".td-quiz-card", has_text=title)
    card.locator("text=View").click()
    expect(card.locator(".td-mcq").first).to_be_visible(timeout=15000)
    n = card.locator(".td-mcq").count()
    expect(card.locator(".td-mcq-opt.correct").first).to_be_visible()
    shot(page, "05_quizzes_list")
    page.click("#tdQuizTabDrafts")
    drafts = sum(1 for q in quizzes if q["status"] != "published")
    expect(page.locator(".td-quiz-card")).to_have_count(drafts)
    page.click("#tdQuizTabAll")
    ctx["assign_quiz_title"] = title
    ctx["assign_quiz_id"] = next(q["id"] for q in quizzes if q["title"] == title)
    return f"{len(quizzes)} quizzes; '{title}' expanded with {n} MCQs"


@check("Quizzes", "Assign quiz to class works end-to-end")
def t_assign(page: Page):
    card = page.locator(".td-quiz-card", has_text=ctx["assign_quiz_title"])
    card.locator("text=Assign").click()
    expect(page.locator("#tdAssignQuizCard")).to_be_visible()
    expect(page.locator("#lmsAssignQuiz")).to_have_value(str(ctx["assign_quiz_id"]), timeout=10000)
    atitle = f"E2E Assignment {RUN}"
    page.fill("#lmsAssignTitle", atitle)
    page.select_option("#lmsAssignClass", str(ctx["class_id"]))
    shot(page, "06_assign_quiz")
    page.click("#lmsAssignSubmitBtn")
    expect(page.locator("#lmsAssignStatus")).to_contain_text("Assignment published", timeout=20000)
    expect(page.locator("#tdAssignQuizCard")).to_be_hidden(timeout=5000)
    stu = ctx["student_api"][0]
    mine = api_json(stu, "GET", "/api/lms/students/me/assignments")
    items = mine if isinstance(mine, list) else (mine.get("assignments") or mine.get("items") or [])
    hit = [a for a in items if (a.get("title") == atitle)]
    assert hit, f"student does not see assignment: {json.dumps(items)[:400]}"
    ctx["assignment_id"] = hit[0].get("id") or hit[0].get("assignment_id")
    ctx["assignment_title"] = atitle
    return f"assignment #{ctx['assignment_id']} visible to student A"


def answer_key(quiz_id: int) -> dict:
    prev = api_json(ctx["teacher_api"], "GET", f"/api/lms/quizzes/{quiz_id}/preview")
    key = {}
    for item in prev.get("questions", []):
        q = item.get("question") or {}
        qid = q.get("id") or item.get("question_id")
        if qid is not None:
            key[int(qid)] = q.get("correct_option_index")
    return key


def take_quiz(req, quiz_id: int, assignment_id: int, correct: bool):
    """Answer every question right (correct=True) or wrong, using the teacher's answer key (quiz options are not shuffled)."""
    key = answer_key(quiz_id)
    att = api_json(req, "POST", f"/api/lms/quizzes/{quiz_id}/start", {"assignment_id": assignment_id})
    aid = att["attempt_id"]
    qs = api_json(req, "GET", f"/api/lms/attempts/{aid}/questions")["questions"]
    for q in qs:
        qid = int(q.get("question_id") or q.get("id"))
        nopts = len(q.get("options") or []) or 4
        right = key.get(qid) if key.get(qid) is not None else 0
        choice = right if correct else (right + 1) % nopts
        api_json(req, "POST", f"/api/lms/attempts/{aid}/answer", {"question_id": qid, "selected_option_index": choice})
    res = api_json(req, "POST", f"/api/lms/attempts/{aid}/submit", {})
    return res


@check("Quizzes", "Students submit the assigned quiz (data for analytics)")
def t_students_take():
    out = []
    for i, req in enumerate(ctx["student_api"][:2]):
        res = take_quiz(req, ctx["assign_quiz_id"], ctx["assignment_id"], correct=(i == 1))
        out.append(f"{STUDENTS[i].split('@')[0]}: {res.get('score')}/{res.get('max_score')}")
    # PDF-generated questions carry no topic_id, so they never move topic mastery / overall_progress.
    # A topic-tagged quiz (existing question-bank APIs) exercises the non-zero progress path.
    tagged = seed_manual_quiz(ctx["teacher_api"])
    asg = api_json(ctx["teacher_api"], "POST", "/api/lms/assignments",
                   {"title": f"E2E Tagged Assignment {RUN}", "class_id": ctx["class_id"], "quiz_id": tagged["id"], "due_date": None})
    api_json(ctx["teacher_api"], "POST", f"/api/lms/assignments/{asg['id']}/publish")
    res = take_quiz(ctx["student_api"][1], tagged["id"], asg["id"], correct=True)
    out.append(f"tagged quiz e2e.student.b: {res.get('score')}/{res.get('max_score')}")
    return "; ".join(out) + "; student C not submitted"


# ───────────────────────── Analytics ─────────────────────────
def open_analytics(page: Page):
    nav(page, "lessons")
    nav(page, "analytics")
    page.select_option("#tdAnaClass", str(ctx["class_id"]))
    page.wait_for_load_state("networkidle")


@check("Analytics", "Topic progress renders real data")
def t_topic_progress(page: Page):
    open_analytics(page)
    rows = page.locator("#tdAnaProgressRows tr[data-sid]")
    expect(rows).to_have_count(3, timeout=15000)
    roster = api_json(ctx["teacher_api"], "GET", f"/api/lms/classes/{ctx['class_id']}/students")
    a = next(s for s in roster if s["username"] == "e2e_student_a")
    row = rows.filter(has_text="e2e_student_a")
    ui_pct = row.locator("td").nth(2).inner_text().strip().split("%")[0]
    exp = round(a["overall_progress"]) if a["overall_progress"] is not None else None
    assert exp is None or ui_pct == str(exp), (ui_pct, a["overall_progress"])
    latest = row.locator("td").nth(3).inner_text().strip()
    assert latest != "—", "latest score missing for student who submitted"
    row.click()
    detail = page.locator(f"#tdProgDetail-{a['student_id']}")
    expect(detail).to_be_visible()
    expect(detail.locator(".td-topic-row, .td-empty").first).to_be_visible(timeout=20000)
    has_topics = detail.locator(".td-topic-row").count()
    # Student B answered the topic-tagged quiz correctly → must show topic bars + score-over-time chart.
    b = next(s for s in roster if s["username"] == "e2e_student_b")
    rows.filter(has_text="e2e_student_b").click()
    bdetail = page.locator(f"#tdProgDetail-{b['student_id']}")
    expect(bdetail.locator(".td-topic-row").first).to_be_visible(timeout=20000)
    expect(bdetail.locator("canvas")).to_be_visible()
    b_topics = bdetail.locator(".td-topic-row").count()
    assert page.evaluate(f"!!(tdAnalytics._state.charts[{b['student_id']}])"), "chart not drawn"
    shot(page, "07_analytics_topic_progress")
    return (f"3 students; A overall {ui_pct}% (API {a['overall_progress']}), latest {latest}, {has_topics} topic rows; "
            f"B {round(b['overall_progress'])}% with {b_topics} topic rows + chart")


@check("Analytics", "Quiz results collapsed ↔ expanded states work")
def t_quiz_results(page: Page):
    page.click('.td-ana-tab[data-ana-tab="quizzes"]')
    row = page.locator("#tdAnaQuizRows tr.td-clickable", has_text=ctx["assignment_title"])
    expect(row).to_be_visible(timeout=15000)
    expect(row).to_contain_text("2")
    expect(row).to_contain_text("/ 3 submitted")
    shot(page, "08_analytics_quiz_results_collapsed")
    row.click()
    inner = page.locator("#tdAnaQuizRows table.td-data tbody tr")
    expect(inner).to_have_count(3, timeout=5000)
    expect(inner.filter(has_text="e2e_student_c")).to_contain_text("Not submitted")
    shot(page, "09_analytics_quiz_results_expanded")
    page.locator("#tdAnaQuizRows tr.td-clickable", has_text=ctx["assignment_title"]).click()
    expect(page.locator("#tdAnaQuizRows table.td-data")).to_have_count(0)
    return "collapsed (2 / 3 submitted) → expanded (3 student rows) → collapsed"


@check("Analytics", "Struggling students view loads")
def t_struggling(page: Page):
    page.click('.td-ana-tab[data-ana-tab="struggling"]')
    api = api_json(ctx["teacher_api"], "GET", f"/api/lms/classes/{ctx['class_id']}/analytics/struggling")
    if api:
        rows = page.locator("#tdAnaStrugglingRows tr.td-clickable")
        expect(rows).to_have_count(len(api), timeout=15000)
        rows.first.click()
        expect(page.locator("#tdAnaStrugglingRows .td-detail-card").first).to_be_visible()
        expect(page.locator('#tdAnaStrugglingRows [data-slot="recent"] .td-spinner')).to_have_count(0, timeout=20000)
        assert page.locator("#tdAnaStrugglingRows th", has_text="Progress").count() == 0, "Progress column must stay hidden"
    else:
        expect(page.locator("#tdAnaStrugglingEmpty")).to_contain_text("No struggling students")
    shot(page, "10_analytics_struggling_students")
    return f"{len(api)} struggling (matches API)"


@check("Analytics", "Analytics roster progress matches API (no all-zeros when data exists)")
def t_ana_roster(page: Page):
    page.click('.td-ana-tab[data-ana-tab="roster"]')
    expect(page.locator("#tdAnaRosterRows tr")).to_have_count(3, timeout=15000)
    roster = api_json(ctx["teacher_api"], "GET", f"/api/lms/classes/{ctx['class_id']}/students")
    expect(page.locator("#tdAnaStatTotal")).to_have_text("3")
    mism = []
    for s in roster:
        row = page.locator("#tdAnaRosterRows tr", has_text=s["username"])
        cell = row.locator("td").nth(3).inner_text().strip()
        exp = f"{round(s['overall_progress'])}%" if s["overall_progress"] is not None else "—"
        if not cell.startswith(exp):
            mism.append((s["username"], cell, exp))
    assert not mism, mism
    nonzero = [s for s in roster if (s["overall_progress"] or 0) > 0]
    assert nonzero, "all students have 0 overall_progress even after submitting quizzes"
    parts = [page.locator(f"#{i}").inner_text() for i in ("tdAnaStatOnTrack", "tdAnaStatHelp", "tdAnaStatNone")]
    assert sum(int(p) for p in parts) == 3, parts
    quizzes = api_json(ctx["teacher_api"], "GET", f"/api/lms/classes/{ctx['class_id']}/analytics/quizzes")
    submitted = {r["student_id"] for a in quizzes for r in a["student_results"] if r["status"] == "submitted"}
    for s in roster:
        if not s["overall_progress"] and not s.get("weak_topic_count") and s["student_id"] not in submitted:
            exp = "NOT ATTEMPTED"
        else:
            exp = "NEEDS HELP" if s["is_struggling"] else "ON TRACK"
        expect(page.locator("#tdAnaRosterRows tr", has_text=s["username"]).locator("td").nth(4)).to_have_text(exp)
    shot(page, "11_analytics_roster")
    row = page.locator("#tdAnaRosterRows tr", has_text="e2e_student_a")
    row.locator("text=View Details").click()
    expect(page.locator('.td-ana-tab[data-ana-tab="progress"]')).to_have_class(re.compile(r"\bactive\b"))
    return "UI progress == API for all 3; on-track/help/not-attempted = " + "/".join(parts)


# ───────────────────────── Lessons ─────────────────────────
def lesson_pdf() -> Path:
    pdfs = sorted(LESSON_PDF.glob("*.pdf"), key=lambda p: p.stat().st_size) if LESSON_PDF.exists() else []
    return pdfs[0] if pdfs else QA_PDF


def create_lesson_via_ui(page: Page, title: str, mode: str):
    nav(page, "lessons")
    page.click("#view-lessons >> text=Create Lesson")
    expect(page.locator("#tdCreateLessonCard")).to_be_visible()
    page.set_input_files("#pdfFileInput", str(lesson_pdf()))
    expect(page.locator("#selectedFileDisplay")).to_be_visible()
    page.fill("#lessonTitle", title)
    page.select_option("#lessonSubject", "Math")
    page.select_option("#lessonGrade", "8")
    page.fill("#lessonContext", "E2E lesson context")
    page.check(f'input[name="lessonOutputMode"][value="{mode}"]')
    shot(page, f"01_lessons_create_{mode}")
    page.click("#nextStepButton")
    expect(page.locator("#createLessonModalContent")).to_be_visible(timeout=15000)


@check("Lessons", "Create lesson (Use PDF as lesson) → appears in list")
def t_lesson_as_is(page: Page):
    title = f"E2E Lesson AsIs {RUN}"
    create_lesson_via_ui(page, title, "as_is")
    expect(page.locator("#createLessonModal")).to_have_count(0, timeout=300000)
    row = page.locator(".td-lesson-row", has_text=title)
    expect(row).to_be_visible(timeout=30000)
    expect(page.locator("#tdCreateLessonCard #lessonTitle")).to_have_value("")
    ctx["lesson_title"] = title
    ctx["lesson_id"] = row.get_attribute("data-lesson-id")
    shot(page, "01_lessons")
    return f"lesson #{ctx['lesson_id']}"


@check("Lessons", "List lessons loads from API (search, tabs, filters, pagination)")
def t_lessons_list(page: Page):
    nav(page, "lessons")
    data = ctx["teacher_api"].get("/api/lessons/my_lessons?page=1&per_page=10").json()
    expect(page.locator(".td-lesson-row")).to_have_count(len(data.get("lessons", [])), timeout=15000)
    expect(page.locator("#tdLessonTabAll")).to_have_text(f"All lessons ({data.get('total')})")
    page.fill("#lessonSearch", ctx["lesson_title"])
    page.wait_for_timeout(700)
    page.wait_for_load_state("networkidle")
    expect(page.locator(".td-lesson-row")).to_have_count(1)
    page.fill("#lessonSearch", "")
    page.wait_for_timeout(700)
    page.wait_for_load_state("networkidle")
    page.select_option("#tdLessonSubjectFilter", "Math")
    assert page.locator(".td-lesson-row").count() >= 1
    page.select_option("#tdLessonSubjectFilter", "")
    expect(page.locator("#pageInfo")).to_contain_text("Page 1 of")
    return f"{data.get('total')} lessons; search + subject filter OK"


@check("Lessons", "View / edit / publish / Word / PowerPoint / FAQ / delete flows")
def t_lesson_actions(page: Page):
    row = page.locator(f'.td-lesson-row[data-lesson-id="{ctx["lesson_id"]}"]')
    out = []
    # "Use PDF as lesson" views stream the source PDF lazily (pdf.js); wait for it so a quick delete
    # later in this test can't race that request.
    with page.expect_response(lambda r: "/source_pdf" in r.url, timeout=30000) as src:
        row.locator("text=View").click()
    assert src.value.ok, f"source_pdf -> {src.value.status}"
    expect(page.locator("#viewLessonModal")).to_be_visible(timeout=15000)
    expect(page.locator("#viewLessonTitle")).to_contain_text(ctx["lesson_title"])
    shot(page, "01_lessons_view_modal")
    page.keyboard.press("Escape")
    page.evaluate("closeViewLessonModal()")
    expect(page.locator("#viewLessonModal")).to_be_hidden()
    out.append("view")
    row.locator("text=Edit").click()
    modal = page.locator(f'#editLessonModal-{ctx["lesson_id"]}')
    expect(modal).to_be_visible(timeout=10000)
    new_title = ctx["lesson_title"] + " edited"
    modal.locator("#editLessonTitle").fill(new_title)
    modal.locator(".elm-btn-save").click()
    expect(modal).to_have_count(0, timeout=15000)
    expect(page.locator(f'.td-lesson-row[data-lesson-id="{ctx["lesson_id"]}"] h3')).to_have_text(new_title, timeout=15000)
    ctx["lesson_title"] = new_title
    row = page.locator(f'.td-lesson-row[data-lesson-id="{ctx["lesson_id"]}"]')
    out.append("edit+save")
    pub = row.locator("a", has_text=re.compile(r"^\s*(Publish|Unpublish)\s*$"))
    before = pub.inner_text().strip()
    pub.click()
    after_expected = "Unpublish" if before == "Publish" else "Publish"
    expect(page.locator(f'.td-lesson-row[data-lesson-id="{ctx["lesson_id"]}"] a', has_text=re.compile(rf"^\s*{after_expected}\s*$"))).to_be_visible(timeout=15000)
    out.append(f"{before}→{after_expected}")
    with page.expect_download(timeout=60000) as dl:
        page.locator(f'.td-lesson-row[data-lesson-id="{ctx["lesson_id"]}"]').locator("text=Word").click()
    out.append("word:" + dl.value.suggested_filename)
    with page.expect_download(timeout=90000) as dl2:
        page.locator(f'.td-lesson-row[data-lesson-id="{ctx["lesson_id"]}"]').locator("text=PowerPoint").click()
    out.append("ppt:" + dl2.value.suggested_filename)
    page.locator(f'.td-lesson-row[data-lesson-id="{ctx["lesson_id"]}"]').locator("text=FAQ").click()
    expect(page.locator("#faqModalOverlay")).to_be_visible(timeout=20000)
    page.evaluate("document.getElementById('faqModalOverlay') && document.getElementById('faqModalOverlay').remove()")
    out.append("faq")
    page.once("dialog", lambda d: d.accept())
    page.locator(f'.td-lesson-row[data-lesson-id="{ctx["lesson_id"]}"]').locator("text=Delete").click()
    expect(page.locator(f'.td-lesson-row[data-lesson-id="{ctx["lesson_id"]}"]')).to_have_count(0, timeout=20000)
    out.append("delete")
    return ", ".join(out)


@check("Lessons", "Create lesson (Generate from PDF) → opens lesson chat in AI Tutor")
def t_lesson_generate(page: Page):
    title = f"E2E Lesson Gen {RUN}"
    create_lesson_via_ui(page, title, "generate")
    expect(page.locator("#createLessonModal")).to_have_count(0, timeout=300000)
    expect(page.locator("#view-tutor")).to_have_class(re.compile(r"\bactive\b"), timeout=20000)
    expect(page.locator("#chatMessages")).to_contain_text(title, timeout=20000)
    ctx["gen_lesson_title"] = title
    shot(page, "12_ai_tutor_after_upload")
    return "switched to AI Tutor with the new PDF conversation"


# ───────────────────────── AI Tutor ─────────────────────────
@check("AI Tutor", "Lesson chat: send message → response or graceful error")
def t_chat_send(page: Page):
    nav(page, "tutor")
    before = page.locator("#chatMessages .chat-message").count()
    page.fill("#messageInput", "Summarize this document in two sentences.")
    expect(page.locator("#sendBtn")).to_be_enabled()
    page.click("#sendBtn")
    page.wait_for_function(
        "n => document.querySelectorAll('#chatMessages .chat-message').length >= n + 2 && !document.getElementById('typing-indicator')",
        arg=before, timeout=180000)
    last = page.locator("#chatMessages .chat-message").last.inner_text()[:200].replace("\n", " ")
    shot(page, "12_ai_tutor_chat")
    return f"assistant: {last!r}"


@check("AI Tutor", "Chat history dropdown lists and re-opens conversations")
def t_chat_history(page: Page):
    page.click("#chatHistoryBtn")
    dd = page.locator("#chatHistoryDropdown")
    expect(dd).to_be_visible()
    items = dd.locator(".chat-history-item")
    expect(items.first).to_be_visible(timeout=15000)
    n = items.count()
    # Not full_page: the legacy chat code closes dropdowns on window resize.
    page.screenshot(path=str(SHOTS / "12_ai_tutor_chat_history.png"))
    # Re-open the conversation created by the "Generate from PDF" lesson (it has messages from t_chat_send).
    target = items.filter(has_text=ctx["gen_lesson_title"]).first
    expect(target).to_be_visible()
    page.evaluate("document.getElementById('chatMessages').innerHTML = ''")
    target.click()
    expect(page.locator("#view-tutor")).to_have_class(re.compile(r"\bactive\b"))
    page.wait_for_function("() => document.querySelectorAll('#chatMessages .chat-message').length >= 2", timeout=20000)
    k = page.locator("#chatMessages .chat-message").count()
    return f"{n} conversations listed; '{ctx['gen_lesson_title']}' re-opened with {k} messages"


@check("AI Tutor", "Teaching Assistant: history load + send → reply or graceful error")
def t_assistant(page: Page):
    page.click('[data-tutor-mode="assistant"]')
    expect(page.locator("#lmsTutorModal")).to_be_visible()
    expect(page.locator("#tdTutorChatPane")).to_be_hidden()
    expect(page.locator("#lmsTutorInput")).to_be_visible(timeout=15000)
    page.fill("#lmsTutorInput", "Give one tip for teaching fractions.")
    page.click("#lmsTutorModalBody >> text=Send")
    page.wait_for_function("() => document.querySelectorAll('#lmsTutorMessages .lms-chat-msg.bot').length >= 1 && !document.querySelector('#lmsTutorModalBody .lms-spinner')", timeout=120000)
    reply = page.locator("#lmsTutorMessages .lms-chat-msg.bot").last.inner_text()[:160].replace("\n", " ")
    assert page.evaluate("document.body.style.overflow") != "hidden", "body scroll locked by inline assistant"
    shot(page, "12_ai_tutor_assistant")
    hist = api_json(ctx["teacher_api"], "GET", "/api/lms/tutor/history?mode=teacher")
    page.click('[data-tutor-mode="chat"]')
    expect(page.locator("#tdTutorChatPane")).to_be_visible()
    return f"reply: {reply!r}; persisted history messages: {len(hist.get('messages', []))}"


@check("AI Tutor", "Set Prompt modal opens from AI Tutor")
def t_set_prompt(page: Page):
    page.click("#view-tutor >> text=Set Prompt")
    expect(page.locator("#ragPromptModal")).to_be_visible(timeout=10000)
    page.evaluate("closeRAGPromptModal()")
    expect(page.locator("#ragPromptModal")).to_be_hidden()
    return "opened + closed"


# ───────────────────────── Cross-cutting ─────────────────────────
@check("Cross-cutting", "Mobile width: no horizontal overflow, nav usable")
def t_mobile(browser):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    login_page(page, TEACHER)
    out = []
    for view in ("lessons", "classes", "quizzes", "analytics", "tutor"):
        page.click(f'.td-navbtn[data-view="{view}"]')
        page.wait_for_load_state("networkidle")
        over = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        page.screenshot(path=str(SHOTS / f"mobile_{view}.png"), full_page=True)
        out.append(f"{view}:{over}px")
        assert over <= 1, f"{view} overflows by {over}px"
    page.close()
    return " ".join(out)


@check("Cross-cutting", "No console-breaking JS errors on primary paths")
def t_console():
    # Transient network failures of third-party CDNs (fonts etc.) are environment noise, not app JS errors.
    third_party_net = [e for e in console_errors if "net::ERR_" in e and "127.0.0.1" not in e and "localhost" not in e]
    real = [e for e in console_errors if "favicon" not in e and e not in third_party_net]
    assert not real, real[:10]
    return "0 errors" + (f" ({len(third_party_net)} third-party network warnings: {third_party_net[0][:120]})" if third_party_net else "")


@check("Cross-cutting", "No missing static assets (icons/css/js 404s)")
def t_assets():
    static_bad = [r for r in bad_responses if "/static/" in r or "/teacher-static/" in r or r.split(" ")[1].endswith((".js", ".css", ".png", ".svg"))]
    assert not static_bad, static_bad
    return f"0 static failures ({len(bad_responses)} non-static 4xx/5xx logged)"


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=CHROME, headless=os.environ.get("E2E_HEADED") != "1")
        context = browser.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(20000)
        page.on("console", lambda m: console_errors.append(f"{m.text} @ {m.location.get('url', '')}") if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(f"pageerror: {e} | {(e.stack or '')[:600]}"))
        page.on("response", lambda r: bad_responses.append(f"{r.status} {r.url}") if r.status >= 400 else None)

        ctx["teacher_api"] = api_session(pw, TEACHER)
        ctx["student_api"] = [api_session(pw, e) for e in STUDENTS]

        t_login(page); t_nav(page); t_logo(page); t_rbac(pw); t_deeplink(page)
        if t_create_class(page, pw):
            t_add_students(page)
            t_roster(page, pw)
        t_quiz_pdf(page)
        if t_quiz_list(page) and ctx.get("class_id") and t_assign(page):
            t_students_take()
        if ctx.get("assignment_id"):
            t_topic_progress(page); t_quiz_results(page); t_struggling(page); t_ana_roster(page)
        if t_lesson_as_is(page):
            t_lessons_list(page); t_lesson_actions(page)
        t_lesson_generate(page)
        t_chat_send(page); t_chat_history(page); t_assistant(page); t_set_prompt(page)
        t_mobile(browser)
        t_console(); t_assets()
        browser.close()

    passed = sum(r["status"] == "PASS" for r in results)
    report = {"base_url": BASE, "run": RUN, "passed": passed, "total": len(results), "results": results,
              "console_errors": console_errors, "bad_responses": bad_responses, "pdf_quiz_status": ctx.get("pdf_quiz_status")}
    (HERE / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = [f"# Teacher UI E2E — {passed}/{len(results)} passed", "", f"Base URL: {BASE} · run {RUN}", "",
             "| Section | Item | Status | Evidence |", "|---|---|---|---|"]
    lines += [f"| {r['section']} | {r['item']} | {r['status']} | {str(r['evidence']).replace('|', '/')[:220]} |" for r in results]
    (HERE / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n{passed}/{len(results)} passed — report: {HERE / 'report.md'}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
