"""Student Dashboard (new UI) — end-to-end verification (Playwright + installed Chrome, LOCAL DB ONLY).

Accounts (fresh every run — the diagnostic is one-time per account):
  A  grade 7  real diagnostic → results → learning path → Learning Chat / guided practice
  B  grade 8  diagnostic marked done (no grade-8 diagnostic exists locally) → join class, lessons,
              lesson chat, quizzes, AI tutor
  C  grade 7  diagnostic attempt whose timer is forced into the past → timeout auto-submit path
The e2e teacher (teacher_ui_e2e/seed_users.py) creates a fresh grade-8 class + quiz assignment through the API.

    SKIP_EXTRA_STARTUP=false python _qa_audit_tmp/teacher_ui_e2e/serve.py 5056
    BASE_URL=http://127.0.0.1:5056 python _qa_audit_tmp/student_ui_e2e/run_e2e.py

Output: report.md / report.json here, screenshots in _qa_audit_tmp/student_ui_screenshots/.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import traceback
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:5056").rstrip("/")
PASSWORD = os.environ.get("E2E_PASSWORD", "E2eTeacher!2026")
TEACHER = "e2e.teacher@iqbalai.local"
QUIZ_ID = int(os.environ.get("E2E_QUIZ_ID", "46"))
CHROME = os.environ.get("CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SHOTS = ROOT / "_qa_audit_tmp" / "student_ui_screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)
DB = ROOT / "instance" / "iqbalai_local.db"
RUN = time.strftime("%H%M%S")
LLM_WAIT = 180_000

results: list[dict] = []
console_errors: list[str] = []
page_errors: list[str] = []
bad_static: list[str] = []
ctx: dict = {}
# Expected, not a UI defect: no grade-8 diagnostic exists locally (student B) + Chrome's automatic favicon probe.
EXPECTED_ERR = re.compile(r"favicon\.ico|/api/lms/diagnostics/default")


def check(section: str, name: str):
    def deco(fn):
        def run(*a, **kw):
            t0 = time.time()
            try:
                evidence = fn(*a, **kw)
                results.append({"section": section, "item": name, "status": "PASS", "evidence": evidence or "", "secs": round(time.time() - t0, 1)})
                print(f"PASS  [{section}] {name}  {evidence or ''}", flush=True)
                return True
            except Exception as exc:  # noqa: BLE001
                results.append({"section": section, "item": name, "status": "FAIL", "evidence": f"{type(exc).__name__}: {exc}",
                                "trace": traceback.format_exc()[-1500:], "secs": round(time.time() - t0, 1)})
                print(f"FAIL  [{section}] {name}  {type(exc).__name__}: {str(exc)[:300]}", flush=True)
                return False
        return run
    return deco


def seed(suffix: str, grade: str, extra: str = "") -> str:
    args = [sys.executable, str(HERE / "seed_student.py"), suffix, grade] + ([extra] if extra else [])
    return subprocess.check_output(args, cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip().splitlines()[-1]


def watch(page: Page):
    def on_console(m):
        if m.type == "error":
            url = (m.location or {}).get("url", "")
            if ctx.get("practice_400_ok") is None and "/api/lms/practice/sessions" in url:
                ctx.setdefault("practice_400_seen", True)
            if "/api/lms/practice/sessions" in url and "400" in m.text:
                return  # checked in t_practice (graceful "no practice question" message)
            if not EXPECTED_ERR.search(url + " " + m.text):
                console_errors.append(f"{m.text[:200]} @ {url}")
    page.on("console", on_console)
    page.on("pageerror", lambda e: page_errors.append(str(e)[:300]))
    page.on("response", lambda r: (r.status >= 400 and "/static/" in r.url) and bad_static.append(f"{r.status} {r.url}"))


def login_page(page: Page, email: str, password: str = PASSWORD):
    page.goto(BASE + "/auth/login", wait_until="domcontentloaded")
    page.fill('input[name="useremail"]', email)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    page.wait_for_function("() => !document.getElementById('loading-overlay')", timeout=60_000)


def api_session(pw, email: str, password: str = PASSWORD):
    req = pw.request.new_context(base_url=BASE)
    r = req.post("/auth/login", form={"useremail": email, "password": password}, max_redirects=0)
    assert r.status in (200, 302), f"login {email} -> {r.status}"
    return req


def api_json(req, method: str, url: str, body=None):
    r = req.fetch(url, method=method, data=json.dumps(body) if body is not None else None,
                  headers={"Content-Type": "application/json"} if body is not None else None, timeout=LLM_WAIT)
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        raise AssertionError(f"{method} {url} -> {r.status} non-JSON: {r.text()[:200]}")
    if not r.ok:
        raise AssertionError(f"{method} {url} -> {r.status}: {json.dumps(data)[:300]}")
    return data.get("data", data) if isinstance(data, dict) and "data" in data else data


def nav(page: Page, view: str):
    page.click(f'.sd-navbtn[data-view="{view}"]')
    expect(page.locator(f"#view-{view}")).to_have_class(re.compile(r"\bactive\b"))
    page.wait_for_load_state("networkidle")


def shot(page: Page, name: str):
    page.wait_for_timeout(400)
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


def confirm_in_app(page: Page):
    ok = page.locator("#inAppConfirmOk")
    ok.wait_for(state="visible", timeout=10_000)
    ok.click()


def no_h_overflow(page: Page) -> bool:
    return page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")


# ───────────────────────── Auth / shell ─────────────────────────
@check("Auth/shell", "Student login lands on new student dashboard")
def t_login(page: Page):
    login_page(page, ctx["A"])
    assert "/student-dashboard" in page.url, page.url
    expect(page.locator(".sd-topbar")).to_be_visible()
    assert page.locator("#chatArea, #lessonsTabBtn, .lms-fab-bar").count() == 0, "old student UI markup present"
    return page.url


@check("Auth/shell", "Top nav shows Diagnostic, My Learning Path, My Classes, AI Tutor with correct icons")
def t_nav(page: Page):
    items = page.eval_on_selector_all(".sd-navbtn", """els => els.map(e => ({view: e.dataset.view, label: e.innerText.trim(),
        src: e.querySelector('img').getAttribute('src'), ok: e.querySelector('img').complete && e.querySelector('img').naturalWidth > 0}))""")
    expected = [("diagnostic", "Diagnostic", "diagnostic.png"), ("learning-path", "My Learning Path", "my-learning-path.png"),
                ("classes", "My Classes", "classes.png"), ("tutor", "AI Tutor", "ai-tutor.png")]
    assert [(i["view"], i["label"]) for i in items] == [(v, l) for v, l, _ in expected], items
    for i, (_, _, icon) in zip(items, expected):
        assert i["src"].endswith("/static/student/icons/" + icon) and i["ok"], i
    return ", ".join(i["label"] for i in items)


@check("Auth/shell", "Logo/branding matches new UI")
def t_logo(page: Page):
    img = page.locator(".sd-brand img")
    assert img.get_attribute("src").endswith("/static/student/icons/iqbal-ai-logo.png")
    assert page.evaluate("() => { const i = document.querySelector('.sd-brand img'); return i.complete && i.naturalWidth > 0; }")
    return img.get_attribute("alt")


@check("Auth/shell", "Teacher/admin cannot stay on student dashboard (redirects)")
def t_redirects(pw):
    out = []
    for who, email, pwd, dest in (("teacher", TEACHER, PASSWORD, "/teacher-dashboard"), ("admin", ctx["ADMIN"], PASSWORD, "/admin")):
        req = api_session(pw, email, pwd)
        r = req.get("/student-dashboard", max_redirects=0)
        loc = r.headers.get("location", "")
        assert r.status in (301, 302) and dest in loc, f"{who}: {r.status} -> {loc}"
        out.append(f"{who}→{loc}")
        req.dispose()
    return "; ".join(out)


# ───────────────────────── Diagnostic ─────────────────────────
@check("Diagnostic", "Diagnostic gate blocks gated features until complete (fresh student)")
def t_gate(page: Page, req):
    ob = api_json(req, "GET", "/api/lms/students/me/onboarding-status")
    assert ob["diagnostic_completed"] is False
    expect(page.locator("#view-diagnostic-quiz")).to_have_class(re.compile(r"\bactive\b"))
    expect(page.locator("#lmsDiagBody")).to_contain_text("Before you begin", timeout=20_000)
    assert not page.locator("#lmsDiagCloseBtn").is_visible(), "Back link must be hidden while mandatory"
    for v in ("classes", "tutor", "learning-path"):
        page.click(f'.sd-navbtn[data-view="{v}"]')
        page.wait_for_timeout(300)
        assert page.evaluate("sdCurrentView()") == "diagnostic-quiz", f"nav to {v} not blocked"
    shot(page, "01a_diagnostic_orientation_gate")
    return "orientation forced; Learning Path / Classes / AI Tutor blocked"


@check("Diagnostic", "Start diagnostic → orientation → quiz loads questions from API (timer visible)")
def t_start(page: Page, req):
    diag = api_json(req, "GET", "/api/lms/diagnostics/default")
    ctx["diag"] = diag
    page.click("#lmsDiagBody >> text=Start Diagnostic")
    expect(page.locator("#lmsDiagBody .sd-q-option").first).to_be_visible(timeout=30_000)
    head = page.locator("#lmsDiagBody .sd-qprogress").inner_text()
    assert f"Question 1 of {diag['question_count']}" in head, head
    expect(page.locator("#lmsDiagTimer")).to_be_visible()
    timer = page.locator("#lmsDiagTimer").inner_text().strip()
    assert re.search(r"\d+:\d\d", timer), timer
    shot(page, "02_diagnostic_quiz")
    return f"{diag['question_count']} questions, timer {timer}"


@check("Diagnostic", "Math in questions/options renders (MathJax)")
def t_math(page: Page):
    n = 0
    for _ in range(8):
        n = page.locator("#lmsDiagBody mjx-container").count()
        if n:
            break
        page.click("#lmsDiagBody .sd-q-nav-end >> text=/Skip|Next/")
        page.wait_for_timeout(700)
    assert n > 0, "no MathJax output on the first 8 questions"
    assert page.locator("#lmsDiagBody .sd-q-text").inner_text().count("$") == 0, "raw $ delimiters visible"
    page.click("#lmsDiagBody .lms-qmap-cell >> nth=0")
    page.wait_for_timeout(500)
    return f"{n} typeset math elements"


@check("Diagnostic", "Tools: Explain this question, Workspace, question map")
def t_tools(page: Page):
    page.click("#lmsDiagBody >> text=Workspace")
    expect(page.locator("#lmsWsNotes")).to_be_visible()
    page.fill("#lmsWsNotes", "rough work")
    page.click("#lmsDiagBody >> text=Workspace")
    page.click("#lmsDiagBody >> text=Explain this question")
    expect(page.locator("#lmsDiagExplain")).to_be_visible()
    expect(page.locator("#lmsDiagExplain")).not_to_contain_text("Rephrasing", timeout=LLM_WAIT)
    txt = page.locator("#lmsDiagExplain").inner_text()[:80]
    return f"explain: {txt!r}"


@check("Diagnostic", "Answer questions (saved to API), submit with unanswered confirm, topic results shown")
def t_submit(page: Page, req):
    total = ctx["diag"]["question_count"]
    for i in range(total - 2):  # leave the last 2 unanswered → confirm dialog
        page.locator("#lmsDiagBody .sd-q-option").nth(i % 4).click()
        page.wait_for_timeout(120)
        if i < total - 1:
            page.click("#lmsDiagBody .sd-q-nav-end >> text=Next")
            page.wait_for_timeout(120)
    page.wait_for_timeout(800)
    assert "answered" in page.locator("#lmsDiagBody .lms-qmap-head").inner_text()
    page.click("#lmsDiagBody >> text=Submit Diagnostic")
    confirm_in_app(page)
    expect(page.locator("#lmsDiagBody .sd-result-body")).to_be_visible(timeout=LLM_WAIT)
    body = page.locator("#lmsDiagBody").inner_text()
    assert "Topic-wise performance" in body and "unanswered" in body, body[:400]
    rows = page.locator("#lmsDiagBody table.sd-mini tbody tr").count()
    assert rows > 0, "no topic rows"
    expect(page.locator("#lmsDiagCloseBtn")).to_be_visible()
    shot(page, "02b_diagnostic_results")
    score = page.locator("#lmsDiagBody .sd-score-big b").inner_text()
    return f"score {score}, {rows} topic rows, gate unlocked"


@check("Diagnostic", "Diagnostic hub loads real stats + Taken row with inline results (API match)")
def t_hub(page: Page, req):
    page.click("#lmsDiagBody >> text=Continue")
    expect(page.locator("#view-diagnostic")).to_have_class(re.compile(r"\bactive\b"))
    page.wait_for_load_state("networkidle")
    d = api_json(req, "GET", "/api/lms/students/me/dashboard")
    ctx["dashA"] = d
    card = page.locator("#view-diagnostic [data-stat-cards]")
    expect(card.locator('[data-stat="pending-diag"]')).to_have_text("0")
    expect(card.locator('[data-stat="weak-count"]')).to_have_text(str(len(d["weak_topics"])))
    page.click('[data-diag-tab="taken"]')
    row = page.locator('#sdDiagList [data-diag-row="attempt"]').first
    expect(row).to_be_visible()
    row.locator("text=View Results").click()
    expect(row.locator(".sd-result-body")).to_be_visible(timeout=LLM_WAIT)  # diagnostic results can take >10s server-side
    attempts = [a for a in api_json(req, "GET", "/api/lms/students/me/attempts") if a["assessment_type"] == "diagnostic" and a["status"] == "submitted"]
    api_pct = int(float(attempts[0]["score_percent"]) + 0.5)
    ui_pct = row.locator(".sd-score-big b").inner_text().strip()
    assert ui_pct == f"{api_pct}%", (ui_pct, api_pct)
    shot(page, "01_diagnostic_hub")
    return f"weak={len(d['weak_topics'])}, taken score {ui_pct} = API"


@check("Diagnostic", "Completed diagnostic is not restarted (existing rule: results shown; retake only via explicit button)")
def t_no_retake(page: Page, req):
    start = api_json(req, "POST", f"/api/lms/quizzes/{ctx['diag']['id']}/start", {})
    assert start.get("already_completed") is True, start
    page.evaluate("openLmsDiagnostic()")
    expect(page.locator("#lmsDiagBody")).to_contain_text("already completed", timeout=20_000)
    expect(page.locator("#lmsDiagBody >> text=Retake with new questions")).to_be_visible()
    page.click("#lmsDiagCloseBtn")
    expect(page.locator("#view-diagnostic")).to_have_class(re.compile(r"\bactive\b"))
    return "start → already_completed; UI shows previous results + explicit Retake button"


@check("Diagnostic", "Timeout auto-submit path (expired attempt is finalized and scored)")
def t_timeout(pw, browser):
    email = seed(f"c{RUN}", "7")
    req = api_session(pw, email)
    diag = api_json(req, "GET", "/api/lms/diagnostics/default")
    start = api_json(req, "POST", f"/api/lms/quizzes/{diag['id']}/start", {})
    qs = api_json(req, "GET", f"/api/lms/attempts/{start['attempt_id']}/questions")
    first = qs["questions"][0]
    api_json(req, "POST", f"/api/lms/attempts/{start['attempt_id']}/answer",
             {"question_id": first.get("question_id") or first["question"]["id"], "selected_option_index": 0})
    con = sqlite3.connect(DB)
    con.execute("update assessment_attempts set expires_at = datetime('now','-5 minutes') where id = ?", (start["attempt_id"],))
    con.commit()
    con.close()
    page = browser.new_page(viewport={"width": 1366, "height": 900})
    watch(page)
    page.set_default_timeout(LLM_WAIT)  # finalizing the expired attempt runs the LLM topic grouping in-request
    login_page(page, email)
    expect(page.locator("#lmsDiagBody")).to_contain_text(re.compile(r"Time (ran out|is up)"), timeout=LLM_WAIT)
    expect(page.locator("#lmsDiagCloseBtn")).to_be_visible()
    shot(page, "02c_diagnostic_timeout")
    ob = api_json(req, "GET", "/api/lms/students/me/onboarding-status")
    page.close()
    req.dispose()
    assert ob["diagnostic_completed"] is True, ob
    return "expired attempt auto-submitted, results + gate unlocked"


# ───────────────────────── Learning path ─────────────────────────
@check("Learning Path", "Path overview loads after diagnostic; weak topics / progress match API")
def t_path(page: Page, req):
    nav(page, "learning-path")
    d = api_json(req, "GET", "/api/lms/students/me/dashboard")
    expect(page.locator("#sdPathTopics .sd-list-row").first).to_be_visible(timeout=20_000)
    n_rows = page.locator("#sdPathTopics [data-topic-row]").count()
    n_weak = page.locator('#sdPathTopics [data-topic-row="weak"]').count()
    exp_rows = len(d["mastery"]) or len(d["weak_topics"])
    assert n_rows == exp_rows and n_weak == len(d["weak_topics"]), (n_rows, exp_rows, n_weak, len(d["weak_topics"]))
    card = page.locator("#view-learning-path [data-stat-cards]")
    p = d["learning_path_progress"]
    label = card.locator('[data-stat="path-label"]').inner_text()
    exp_label = f"{p['completed']} of {p['total']} topics" if p["total"] else "No path yet"
    assert label == exp_label, (label, exp_label)
    ui_pct = card.locator('[data-stat="path-pct"]').inner_text()
    assert ui_pct == f"{int(float(p['percent']) + 0.5)}%", (ui_pct, p["percent"])
    expect(page.locator("#sdAttemptHistory .sd-list-row").first).to_be_visible()
    shot(page, "03_learning_path")
    return f"{n_rows} topics ({n_weak} weak), progress '{label}' = API"


@check("Learning Path", "Learning Chat (deficiency) starts and accepts an answer")
def t_learning_chat(page: Page):
    btn = page.locator("#sdPathTopics >> text=Start Learning Path").first
    if btn.count() == 0:
        btn = page.locator("#sdPathSteps >> text=/Open Learning Chat|Start challenge/").first
    btn.click()
    expect(page.locator("#view-learning-chat")).to_have_class(re.compile(r"\bactive\b"))
    expect(page.locator("#lmsDeficiencyBody .sd-q-option, #lmsDeficiencyBody .sd-done-card").first).to_be_visible(timeout=LLM_WAIT)
    if page.locator("#lmsDeficiencyBody .sd-done-card").count():
        return "session had no open questions (completed card shown)"
    before = page.locator("#lmsDeficiencyBody .sd-desc").first.inner_text()
    page.locator("#lmsDeficiencyBody .sd-q-option").first.click()
    page.click("#lmsDeficiencyBody >> text=Submit Answer")
    page.wait_for_function(
        """(b) => { const el = document.querySelector('#lmsDeficiencyBody .sd-desc');
               return document.querySelector('#lmsDeficiencyBody .lms-opt-wrong') || document.querySelector('#lmsDeficiencyBody .sd-done-card') || (el && el.innerText !== b); }""",
        arg=before, timeout=60_000)
    wrong = page.locator("#lmsDeficiencyBody .lms-opt-wrong").count() > 0
    shot(page, "04_learning_chat")
    ctx["lc_wrong"] = wrong
    return "wrong → feedback + Next question" if wrong else "correct → advanced"


@check("Learning Path", "Tutor help (Need more help), advance, Pause & Exit still work")
def t_learning_chat_tools(page: Page):
    if page.locator("#lmsDeficiencyBody .sd-done-card").count():
        page.click("#lmsDeficiencyBody >> text=Back to Learning Path")
        return "session finished — skipped"
    users = page.locator("#lmsDeficiencyTutorMessages .sd-ai-bubble.user").count()
    page.click("#lmsDeficiencyBody button[title='Need more help']")
    page.wait_for_function("""(n) => !document.getElementById('lmsDeficiencyTyping')
        && document.querySelectorAll('#lmsDeficiencyTutorMessages .sd-ai-bubble.user').length > n
        && !document.querySelector('#lmsDeficiencyTutorMessages .sd-ai-bubble:last-child').classList.contains('user')""",
                           arg=users, timeout=LLM_WAIT)
    reply = page.locator("#lmsDeficiencyTutorMessages .sd-ai-bubble:not(.user) .sd-msg").last.inner_text()[:80]
    advanced = False
    if page.locator("#lmsDeficiencyBody >> text=Next question").count():
        page.click("#lmsDeficiencyBody >> text=Next question")
        page.wait_for_timeout(1500)
        advanced = True
    shot(page, "04b_learning_chat_tutor")
    page.click("#lmsDeficiencyBody >> text=Pause & Exit")
    expect(page.locator("#view-learning-path")).to_have_class(re.compile(r"\bactive\b"), timeout=15_000)
    return f"tutor reply {reply!r}; advance={'yes' if advanced else 'n/a'}; paused back to path"


@check("Learning Path", "Guided practice (per topic) opens with a question and hint")
def t_practice(page: Page):
    btn = page.locator("#sdPathTopics >> text=/Guided practice|Practice again/").first
    if btn.count() == 0:
        return "no topics with practice — skipped"
    btn.click()
    body = page.locator("#lmsPracticeModalBody")
    page.wait_for_function("() => { const b = document.getElementById('lmsPracticeModalBody'); return b && !b.querySelector('.lms-spinner') && b.innerText.trim().length > 0; }", timeout=LLM_WAIT)
    txt = body.inner_text()[:80]
    if body.locator(".lms-quiz-option").count():
        body.locator("text=Need a hint?").click()
        expect(page.locator("#lmsPracticeFeedback")).not_to_be_empty(timeout=LLM_WAIT)
        txt += " | hint: " + page.locator("#lmsPracticeFeedback").inner_text()[:60]
    else:
        # Backend has no bank question for this topic → it answers 400 and the panel shows that message
        # (same as the old modal). Graceful, so the 400 isn't counted as a console error.
        assert body.locator(".lms-error").count(), txt
        ctx["practice_400_ok"] = True
    page.evaluate("lmsCloseModal('lmsPracticeModal')")
    return txt.replace("\n", " ")


# ───────────────────────── Classes / quizzes ─────────────────────────
@check("My Classes", "Join class via code; enrolled class loads with teacher + lessons (API match)")
def t_join(page: Page, pw, reqB):
    t = api_session(pw, TEACHER)
    cls = api_json(t, "POST", "/api/lms/classes", {"name": f"E2E Student UI 8 {RUN}", "grade_level": "8"})
    asg = api_json(t, "POST", "/api/lms/assignments", {"title": f"E2E Student Quiz {RUN}", "class_id": cls["id"], "quiz_id": QUIZ_ID, "due_date": "2026-12-31T00:00:00"})
    api_json(t, "POST", f"/api/lms/assignments/{asg['id']}/publish")
    t.dispose()
    ctx["class_id"] = cls["id"]
    login_page(page, ctx["B"])
    nav(page, "classes")
    page.click("#view-classes >> text=Join Class")
    page.fill("#lmsJoinCodeInput", cls["join_code"])
    page.click("#lmsJoinClassModal .sd-btn-primary")
    card = page.locator(f'.sd-class-card[data-class-id="{cls["id"]}"]')
    expect(card).to_be_visible(timeout=20_000)
    if "open" not in (card.get_attribute("class") or ""):
        card.locator(".sd-class-header").click()
    expect(card).to_contain_text("Teacher: e2e_teacher")
    lessons = api_json(reqB, "GET", f"/api/lessons/browse_lessons?class_id={cls['id']}&per_page=50")
    expect(card.locator(".sd-lesson-row")).to_have_count(len(lessons["lessons"]), timeout=20_000)
    assert len(lessons["lessons"]) > 0, "teacher has no grade-8 lessons"
    shot(page, "05_my_classes")
    return f"class {cls['id']} joined, {len(lessons['lessons'])} lessons"


@check("My Classes", "Assigned quiz visible, taken end-to-end, Review matches API")
def t_quiz(page: Page, reqB):
    card = page.locator(f'.sd-class-card[data-class-id="{ctx["class_id"]}"]')
    row = card.locator(".sd-quiz-row", has_text=f"E2E Student Quiz {RUN}")
    expect(row).to_be_visible()
    row.locator("text=Start Quiz").click()
    expect(page.locator("#lmsQuizTaking .sd-q-option").first).to_be_visible(timeout=30_000)
    total = int(re.search(r"of (\d+)", page.locator("#lmsQuizTaking .sd-qp-top").inner_text()).group(1))
    for i in range(total):
        page.locator("#lmsQuizTaking .sd-q-option").first.click()
        page.wait_for_timeout(150)
        if i < total - 1:
            page.click("#lmsQuizTaking >> text=Next")
    page.click("#lmsQuizTaking >> text=Submit Quiz")
    expect(page.locator("#lmsQuizResult .sd-result-body")).to_be_visible(timeout=30_000)
    shot(page, "05b_quiz_result")
    page.click("#lmsQuizResult >> text=Back to My Classes")
    row = card.locator(".sd-quiz-row", has_text=f"E2E Student Quiz {RUN}")
    expect(row.locator("text=Completed")).to_be_visible(timeout=20_000)
    row.locator("text=Review").click()
    expect(row.locator(".sd-ring")).to_be_visible(timeout=20_000)
    asg = [a for a in api_json(reqB, "GET", "/api/lms/students/me/assignments") if a["title"] == f"E2E Student Quiz {RUN}"][0]
    res = api_json(reqB, "GET", f"/api/lms/attempts/{asg['attempt_id']}/results")
    ui_pct = row.locator(".sd-ring b").inner_text().strip()
    assert ui_pct == f"{int(float(res['score_percent']) + 0.5)}%", (ui_pct, res["score_percent"])
    shot(page, "05c_quiz_review")
    return f"{total} questions, score {ui_pct} = API"


# ───────────────────────── Lesson viewer ─────────────────────────
@check("Lesson viewer", "Open lesson: content renders; Word + PowerPoint download")
def t_lesson(page: Page):
    card = page.locator(f'.sd-class-card[data-class-id="{ctx["class_id"]}"]')
    first = card.locator(".sd-lesson-row").first
    title = first.locator(".sd-lname").inner_text().split("\n")[0]
    first.locator("text=View").click()
    expect(page.locator("#view-lesson")).to_have_class(re.compile(r"\bactive\b"))
    expect(page.locator("#viewLessonTitle")).to_have_text(title, timeout=20_000)
    page.wait_for_function("() => { const c = document.getElementById('viewLessonCurrent'); return c && !c.querySelector('.sd-spinner') && c.innerText.trim().length > 40 || (c && c.querySelector('canvas')); }", timeout=30_000)
    names = []
    for label in ("Word", "PowerPoint"):
        with page.expect_download(timeout=60_000) as dl:
            page.click(f"#view-lesson .sd-lh-actions >> text={label}")
        names.append(dl.value.suggested_filename)
    shot(page, "06_lesson_viewer")
    ctx["lesson_title"] = title
    return f"{title!r}; downloads {names}"


@check("Lesson viewer", "Lesson chat answers (or asks to use teacher PDF) and is saved as a conversation")
def t_lesson_chat(page: Page, reqB):
    page.fill("#messageInput", "Summarize this lesson in one sentence.")
    page.click("#sendBtn")
    page.wait_for_function("() => document.querySelectorAll('#sdLessonChatMessages .sd-ai-bubble:not(.user)').length >= 2 && !document.querySelector('#sdLessonChatMessages .sd-typing-row')", timeout=LLM_WAIT)
    last = page.locator("#sdLessonChatMessages .sd-ai-bubble:not(.user) .sd-msg").last
    if last.locator("[data-rag='yes']").count():
        last.locator("[data-rag='yes']").click()
        expect(last).not_to_contain_text("Searching PDF", timeout=LLM_WAIT)
    reply = last.inner_text()[:100]
    assert "went wrong" not in reply and "couldn't process" not in reply, reply
    convs = requests_convs(reqB)
    assert any(ctx["lesson_title"] in (c.get("title") or "") for c in convs), [c.get("title") for c in convs]
    shot(page, "06b_lesson_chat")
    return f"reply {reply!r}; conversation saved"


def requests_convs(req):
    r = req.get("/get_conversations?limit=200")
    return r.json().get("conversations", [])


# ───────────────────────── AI tutor ─────────────────────────
@check("AI Tutor", "Send message via /api/lms/tutor/chat and get a response")
def t_tutor(page: Page):
    nav(page, "tutor")
    page.fill("#lmsTutorInput", "What is 7 times 8?")
    page.click("#sdTutorSend")
    page.wait_for_function("() => document.querySelectorAll('#sdTutorMessages .sd-ai-bubble:not(.user)').length >= 2 && !document.querySelector('#sdTutorMessages .sd-typing')", timeout=LLM_WAIT)
    reply = page.locator("#sdTutorMessages .sd-ai-bubble:not(.user) .sd-msg").last.inner_text()[:100]
    assert not reply.startswith("Error:"), reply
    shot(page, "07_ai_tutor")
    return repr(reply)


@check("AI Tutor", "History restores on reload; Chat History lists lesson chat and opens it; clear works")
def t_tutor_history(page: Page, reqB):
    page.reload()
    page.wait_for_function("() => !document.getElementById('loading-overlay')")
    nav(page, "tutor")
    expect(page.locator("#sdTutorMessages")).to_contain_text("7 times 8", timeout=20_000)
    page.click("#sdHistToggle")
    item = page.locator("#sdHistList .sd-h-item[data-conv-id]", has_text=ctx["lesson_title"]).first
    expect(item).to_be_visible(timeout=20_000)
    shot(page, "07b_ai_tutor_history")
    item.click()
    expect(page.locator("#view-lesson")).to_have_class(re.compile(r"\bactive\b"))
    expect(page.locator("#sdLessonChatMessages")).to_contain_text("Summarize this lesson", timeout=20_000)
    nav(page, "tutor")
    page.click("#sdHistToggle")
    page.click("#sdHistPop >> text=Clear tutor chat")
    confirm_in_app(page)
    page.wait_for_timeout(1500)
    hist = api_json(reqB, "GET", "/api/lms/tutor/history?mode=student")
    assert not hist.get("messages"), hist
    return "restored, reopened lesson conversation, cleared (API empty)"


# ───────────────────────── Cross-cutting ─────────────────────────
@check("Cross-cutting", "Mobile (390px): every main view usable, no horizontal scroll")
def t_mobile(browser):
    page = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    watch(page)
    login_page(page, ctx["B"])
    out = []
    for v in ("diagnostic", "learning-path", "classes", "tutor"):
        nav(page, v)
        page.wait_for_timeout(600)
        assert no_h_overflow(page), f"horizontal overflow on {v}"
        shot(page, f"mobile_{v}")
        out.append(v)
    page.close()
    return ", ".join(out)


def write_report():
    passed = sum(r["status"] == "PASS" for r in results)
    (HERE / "report.json").write_text(json.dumps({"base": BASE, "run": RUN, "results": results, "console_errors": console_errors,
                                                   "page_errors": page_errors, "bad_static": bad_static}, indent=2), encoding="utf-8")
    lines = [f"# Student UI E2E — {passed}/{len(results)} PASS", "", f"Base: {BASE} · run {RUN}", "",
             "| Section | Item | Status | Evidence |", "|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['section']} | {r['item']} | {r['status']} | {str(r['evidence']).replace('|', '/')[:220]} |")
    (HERE / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n{passed}/{len(results)} PASS")


def main():
    ctx["A"] = seed(f"a{RUN}", "7")
    ctx["B"] = seed(f"b{RUN}", "8", "diag-done")
    ctx["ADMIN"] = seed(f"adm{RUN}", "8", "admin")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=CHROME, headless=not os.environ.get("E2E_HEADED"))
        page = browser.new_page(viewport={"width": 1366, "height": 900}, accept_downloads=True)
        watch(page)
        reqA = api_session(pw, ctx["A"])
        t_login(page)
        t_nav(page)
        t_logo(page)
        t_redirects(pw)
        t_gate(page, reqA)
        t_start(page, reqA) and t_math(page) and t_tools(page) and t_submit(page, reqA)
        t_hub(page, reqA)
        t_no_retake(page, reqA)
        t_timeout(pw, browser)
        t_path(page, reqA)
        if t_learning_chat(page):
            t_learning_chat_tools(page)
        t_practice(page)

        pageB = browser.new_page(viewport={"width": 1366, "height": 900}, accept_downloads=True)
        watch(pageB)
        reqB = api_session(pw, ctx["B"])
        if t_join(pageB, pw, reqB):
            t_quiz(pageB, reqB)
            if t_lesson(pageB):
                t_lesson_chat(pageB, reqB)
        t_tutor(pageB)
        t_tutor_history(pageB, reqB)
        t_mobile(browser)

        @check("Cross-cutting", "No JS errors (pageerror / console) on primary paths")
        def t_console():
            assert not page_errors, page_errors
            assert not console_errors, console_errors
            return "0 page errors, 0 unexpected console errors"

        @check("Cross-cutting", "No missing static assets (icons/css/js 404s)")
        def t_static():
            assert not bad_static, bad_static
            return "0 static 404s"

        t_console()
        t_static()
        browser.close()
    write_report()


if __name__ == "__main__":
    main()
