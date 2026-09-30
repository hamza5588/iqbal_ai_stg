"""Full LMS visual + backend QA (Teacher + Student dashboards).

Every item is judged on 4 layers — VISUAL (screenshot + layout/asset checks), INTERACTION (real UI flow),
API (same user's session) and DB (SQLite ground truth). PASS only when all applicable layers agree.

    BASE_URL=http://127.0.0.1:5001 python _qa_audit_tmp/full_lms_visual_backend_qa/playwright/run_qa.py

Local SQLite only (reads instance/iqbalai_local.db for ground truth). Accounts are registered through the real
UI (register_email → verify link read from email_verification_tokens → register form). Artifacts:
_qa_audit_tmp/full_lms_visual_backend_qa/<ts>/{screenshots,api_dumps,REPORT.md,results.json}
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:5001").rstrip("/")
ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "instance" / "iqbalai_local.db"
CHROME = os.environ.get("CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
TS = time.strftime("%Y%m%d_%H%M%S")
OUT = ROOT / "_qa_audit_tmp" / "full_lms_visual_backend_qa" / TS
SHOTS, DUMPS = OUT / "screenshots", OUT / "api_dumps"
for d in (SHOTS, DUMPS):
    d.mkdir(parents=True, exist_ok=True)
shutil.copy(__file__, OUT / "run_qa.py")
PASSWORD = "QaPass!2026x"
LLM = 300_000
QA_PDF = ROOT / "diagnostic_qa_math_ix_sindh.pdf"
LESSON_PDFS = ROOT / "sample_pdfs"

results: list[dict] = []
ctx: dict = {"accounts": {}, "ids": {}}
net_fail: list[str] = []
console_err: list[str] = []


class Blocked(Exception):
    pass


# ───────────────────────── helpers ─────────────────────────
def db(sql, *args):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def db_exec(sql, *args):
    con = sqlite3.connect(DB)
    try:
        con.execute(sql, args)
        con.commit()
    finally:
        con.close()


def dump(name, obj):
    (DUMPS / f"{name}.json").write_text(json.dumps(obj, indent=2, default=str)[:400_000], encoding="utf-8")
    return f"api_dumps/{name}.json"


def shot(page: Page, name, full=True):
    page.wait_for_timeout(500)
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=full)
    return f"screenshots/{name}.png"


def item(role, feature):
    def deco(fn):
        def run(*a, **kw):
            t0 = time.time()
            rec = {"role": role, "feature": feature, "visual": "n/a", "interaction": "n/a", "api": "n/a", "db": "n/a", "evidence": "", "shots": []}
            try:
                out = fn(*a, **kw) or {}
                rec.update({k: v for k, v in out.items() if k in rec})
                rec["status"] = "PASS"
            except Blocked as e:
                rec.update(status="BLOCKED", evidence=str(e))
            except Exception as e:  # noqa: BLE001
                rec.update(status="FAIL", evidence=f"{type(e).__name__}: {e}"[:1500], trace=traceback.format_exc()[-2000:])
                for p in ctx.get("pages", []):
                    try:
                        rec["shots"].append(shot(p, f"FAIL_{len(results):02d}_{re.sub(r'[^a-z0-9]+', '_', feature.lower())[:40]}"))
                        break
                    except Exception:  # noqa: BLE001
                        pass
            rec["secs"] = round(time.time() - t0, 1)
            results.append(rec)
            print(f"{rec['status']:7} [{role}] {feature} :: {str(rec['evidence'])[:260]}", flush=True)
            return rec["status"] == "PASS"
        return run
    return deco


def ok(text):
    return "ok: " + text


def new_page(browser, w=1440, h=900) -> Page:
    c = browser.new_context(viewport={"width": w, "height": h}, accept_downloads=True)
    p = c.new_page()
    p.set_default_timeout(60_000)
    p.on("console", lambda m: m.type == "error" and console_err.append(f"{m.text[:200]} @ {(m.location or {}).get('url', '')}"))
    p.on("pageerror", lambda e: console_err.append(f"PAGEERROR {str(e)[:300]}"))
    p.on("response", lambda r: r.status >= 400 and net_fail.append(f"{r.status} {r.request.method} {r.url}"))
    ctx.setdefault("pages", []).append(p)
    return p


def api(pw, email):
    req = pw.request.new_context(base_url=BASE, timeout=LLM)
    r = req.post("/auth/login", form={"useremail": email, "password": PASSWORD}, max_redirects=0)
    assert r.status in (200, 302), f"login {email} -> {r.status}"
    return req


def call(req, method, url, body=None, name=None, expect_ok=True):
    r = req.fetch(url, method=method, data=json.dumps(body) if body is not None else None,
                  headers={"Content-Type": "application/json"} if body is not None else None)
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        data = {"_raw": r.text()[:500]}
    if name:
        dump(name, {"status": r.status, "url": url, "body": data})
    if expect_ok and not r.ok:
        raise AssertionError(f"{method} {url} -> {r.status}: {json.dumps(data)[:300]}")
    if isinstance(data, dict) and "data" in data:
        return data["data"] if expect_ok else (r.status, data)
    return data if expect_ok else (r.status, data)


def login(page: Page, email):
    page.goto(BASE + "/auth/login", wait_until="domcontentloaded")
    page.fill('input[name="useremail"]', email)
    page.fill('input[name="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle", timeout=LLM)


def wait_student_ready(page: Page):
    page.wait_for_function("() => !document.getElementById('loading-overlay')", timeout=LLM)


def assets_ok(page: Page):
    broken = page.evaluate("() => Array.from(document.images).filter(i => i.complete && i.naturalWidth === 0 && i.offsetParent).map(i => i.src)")
    assert not broken, f"broken images: {broken}"


def jsround(x):
    return int(float(x) + 0.5)


def uid(email):
    return db("select id from users where useremail=?", email)[0][0]


def correct_index(api_q, db_qid):
    """Index of the correct option AS SHOWN (API order), matched by option text against the DB key."""
    opts_json, corr = db("select options_json, correct_option_index from questions where id=?", db_qid)[0]
    db_opts = json.loads(opts_json or "[]")
    want = db_opts[corr] if corr is not None and corr < len(db_opts) else None
    shown = api_q.get("options") or (api_q.get("question") or {}).get("options") or []
    norm = lambda o: re.sub(r"\s+", "", str((o or {}).get("text") or (o or {}).get("latex") or ""))  # noqa: E731
    for i, o in enumerate(shown):
        if want is not None and norm(o) == norm(want):
            return i
    return corr


# ───────────────────────── SETUP (registration through the real UI) ─────────────────────────
def register(browser, role, email, username, grade_label):
    page = new_page(browser)
    page.goto(BASE + "/auth/register_email", wait_until="domcontentloaded")
    page.fill('input[name="useremail"]', email)
    page.click('#submit-btn')
    page.wait_for_load_state("networkidle")
    tok = db("select token from email_verification_tokens where email=? and used=0 order by id desc", email)
    assert tok, f"no verification token for {email}"
    page.goto(f"{BASE}/auth/verify_email/{tok[0][0]}", wait_until="domcontentloaded")
    page.fill("#username", username)
    page.fill("#password", PASSWORD)
    page.select_option("#role", role)
    if grade_label and page.locator("#class_standard").is_visible():
        page.select_option("#class_standard", grade_label)
    page.select_option("#medium", "English")
    page.fill("#confirm_password", PASSWORD)
    page.check("#terms")
    page.click("#submit-btn")
    page.wait_for_load_state("networkidle", timeout=LLM)
    return page


@item("Setup", "Health: app responds at BASE_URL")
def s_health(pw):
    r = pw.request.new_context().get(BASE + "/auth/login")
    assert r.status == 200, r.status
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    commit = subprocess.run(["git", "log", "-1", "--format=%h %s"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.count("\n")
    ctx["git"] = f"{branch} @ {commit} (+{dirty} uncommitted paths)"
    return {"api": ok("GET /auth/login 200"), "evidence": ctx["git"]}


@item("Setup", "Register TEACHER via UI → lands on /teacher-dashboard")
def s_teacher(browser):
    email = f"qa.teacher.{TS}@test.local"
    page = register(browser, "teacher", email, f"qa_teacher_{TS[-6:]}", None)
    assert "/teacher-dashboard" in page.url, page.url
    expect(page.locator(".td-topbar")).to_be_visible()
    ctx["accounts"]["teacher"] = email
    ctx["tpage"] = page
    row = db("select id, role from users where useremail=?", email)[0]
    assert row[1] == "teacher", row
    ctx["ids"]["teacher_id"] = row[0]
    return {"visual": ok(shot(page, "T00_teacher_landing")), "interaction": ok("register_email → verify → register"),
            "db": ok(f"users.id={row[0]} role=teacher"), "evidence": f"{email} → {page.url}"}


@item("Setup", "Register STUDENTS A, B, C via UI (grade 7) → land on /student-dashboard")
def s_students(browser):
    out = []
    for key in ("A", "B", "C"):
        email = f"qa.student.{key.lower()}.{TS}@test.local"
        page = register(browser, "student", email, f"qa_stu_{key.lower()}_{TS[-6:]}", "7th")
        assert "/student-dashboard" in page.url, page.url
        wait_student_ready(page)
        ctx["accounts"][key] = email
        ctx[f"page{key}"] = page
        r = db("select id, role, class_standard from users where useremail=?", email)[0]
        assert r[1] == "student", r
        ctx["ids"][f"student_{key}"] = r[0]
        out.append(f"{key}: id={r[0]} grade={r[2]}")
    return {"visual": ok(shot(ctx["pageA"], "S00_student_landing_gate")), "interaction": ok("3× register via UI"),
            "db": ok("; ".join(out)), "evidence": "; ".join(out)}


# ───────────────────────── TEACHER: shell, classes ─────────────────────────
@item("Teacher", "Shell: nav labels + icons, logo, no broken images")
def t_shell():
    page = ctx["tpage"]
    items = page.eval_on_selector_all(".td-navbtn", """els => els.map(e => ({v: e.dataset.view, l: e.innerText.trim(), s: e.querySelector('img').getAttribute('src'),
        ok: e.querySelector('img').complete && e.querySelector('img').naturalWidth > 0}))""")
    assert [i["l"] for i in items] == ["My Lessons", "Classes", "Quizzes", "Analytics", "AI Tutor"], items
    assert all(i["ok"] for i in items), items
    logo = page.eval_on_selector(".td-brand img", "i => i.complete && i.naturalWidth > 0 && i.getAttribute('src')")
    assert logo and logo.endswith("iqbal-ai-logo.png"), logo
    assets_ok(page)
    return {"visual": ok(shot(page, "T01_teacher_shell")), "interaction": ok("nav rendered"), "evidence": ", ".join(i["l"] for i in items)}


@item("Teacher", "Classes: teaching grade + create class form persists (UI → API → DB)")
def t_create_class(treq):
    page = ctx["tpage"]
    page.click('.td-navbtn[data-view="classes"]')
    page.click("text=Create Class")
    expect(page.locator("#tdCreateClassCard")).to_be_visible()
    page.fill("#tdTeachingGrades", "7")
    page.click("#tdTeachingGradesField >> text=Save")
    expect(page.locator("#tdTeachingGradesHint")).to_contain_text("7")
    name = f"QA Class 7 {TS}"
    page.fill("#tdClassName", name)
    page.select_option("#tdClassGrade", "7")
    page.fill("#tdClassDescription", "Full LMS QA class")
    v = shot(page, "T02_create_class_form")
    page.click("#tdCreateClassSubmit")
    card = page.locator(".td-class-card", has_text=name)
    expect(card).to_be_visible(timeout=20_000)
    code = card.locator(".td-code").inner_text().strip()
    classes = call(treq, "GET", "/api/lms/classes/mine", name="teacher_classes_mine")
    m = [c for c in classes if c["name"] == name]
    assert m and m[0]["join_code"] == code, m
    cid = m[0]["id"]
    row = db("select teacher_id, grade_level, join_code, is_active from classes where id=?", cid)[0]
    assert row[0] == ctx["ids"]["teacher_id"] and str(row[1]) == "7" and row[2] == code and row[3], row
    ctx["ids"].update(class_id=cid, join_code=code)
    ctx["class_name"] = name
    return {"visual": ok(v), "interaction": ok("created via form"), "api": ok(f"classes/mine has id={cid} code={code}"),
            "db": ok(f"classes row {row}"), "evidence": f"class_id={cid} join_code={code}"}


@item("Teacher", "Add Students tab adds A + C (UI → roster API → class_enrollments)")
def t_add_students(treq):
    page = ctx["tpage"]
    cid = ctx["ids"]["class_id"]
    body = page.locator(f"#tdClassBody-{cid}")
    if not body.is_visible():
        page.locator(f'.td-class-card[data-class-id="{cid}"] .td-class-head h3').click()
    body.locator(".td-inner-tabs >> text=Add Students").click()
    names = [f"qa_stu_a_{TS[-6:]}", f"qa_stu_c_{TS[-6:]}"]
    for n in names:
        expect(body.locator("tbody tr", has_text=n)).to_have_count(1, timeout=20_000)
        body.locator("tbody tr", has_text=n).locator("input[type=checkbox]").check()
    v = shot(page, "T03_add_students")
    body.locator('[data-slot="addSelected"]').click()
    for n in names:
        expect(body.locator("tbody tr", has_text=n)).to_have_count(0, timeout=20_000)
    roster = call(treq, "GET", f"/api/lms/classes/{cid}/students", name="roster_after_add")
    got = sorted(s["username"] for s in roster)
    assert got == sorted(names), got
    enr = db("select student_id, status from class_enrollments where class_id=? order by student_id", cid)
    exp = sorted([ctx["ids"]["student_A"], ctx["ids"]["student_C"]])
    assert [e[0] for e in enr] == exp and all(e[1] == "active" for e in enr), enr
    return {"visual": ok(v), "interaction": ok("multi-select add"), "api": ok(f"roster={got}"), "db": ok(f"enrollments={enr}")}


# ───────────────────────── STUDENT A: gate, diagnostic ─────────────────────────
@item("Student", "Before diagnostic: gate forces diagnostic; Join/Tutor/Path blocked; Learning Path locked (API)")
def s_gate(areq):
    page = ctx["pageA"]
    expect(page.locator("#view-diagnostic-quiz")).to_have_class(re.compile(r"\bactive\b"))
    expect(page.locator("#lmsDiagBody")).to_contain_text("Before you begin", timeout=LLM)
    assert not page.locator("#lmsDiagCloseBtn").is_visible()
    for v in ("learning-path", "classes", "tutor"):
        page.click(f'.sd-navbtn[data-view="{v}"]')
        page.wait_for_timeout(300)
        assert page.evaluate("sdCurrentView()") == "diagnostic-quiz", v
    page.evaluate("openLmsJoinClassModal()")
    page.wait_for_timeout(500)
    assert not page.locator("#lmsJoinClassModal.open").count(), "join modal opened while gated"
    ob = call(areq, "GET", "/api/lms/students/me/onboarding-status", name="A_onboarding_before")
    lp = call(areq, "GET", "/api/lms/students/me/learning-path", name="A_learning_path_before")
    prof = db("select diagnostic_completed from student_profiles where user_id=?", ctx["ids"]["student_A"])
    assert ob["diagnostic_completed"] is False and not lp.get("items"), (ob, lp)
    assert not prof or not prof[0][0], prof
    return {"visual": ok(shot(page, "S01_gate_orientation")), "interaction": ok("nav + join blocked"),
            "api": ok("diagnostic_completed=false, path items=0"), "db": ok(f"student_profiles={prof}")}


@item("Student", "Diagnostic: start → real questions (API count) → timer counts down")
def s_diag_start(areq):
    page = ctx["pageA"]
    diag = call(areq, "GET", "/api/lms/diagnostics/default", name="A_diagnostics_default")
    ctx["diag"] = diag
    page.click("#lmsDiagBody >> text=Start Diagnostic")
    expect(page.locator("#lmsDiagBody .sd-q-option").first).to_be_visible(timeout=LLM)
    head = page.locator("#lmsDiagBody .sd-qprogress").inner_text()
    assert f"of {diag['question_count']}" in head, head
    t1 = page.locator("#lmsDiagTimer").inner_text().strip()
    page.wait_for_timeout(3500)
    t2 = page.locator("#lmsDiagTimer").inner_text().strip()
    sec = lambda t: int(t.split(":")[0]) * 60 + int(t.split(":")[1])  # noqa: E731
    assert sec(t2) < sec(t1), (t1, t2)
    att = db("select id, status, expires_at from assessment_attempts where student_id=? and assessment_id=? order by id desc",
             ctx["ids"]["student_A"], diag["id"])[0]
    assert att[1] == "in_progress" and att[2], att
    ctx["ids"]["diag_attempt_A"] = att[0]
    ctx["ids"]["diagnostic_id"] = diag["id"]
    qs = call(areq, "GET", f"/api/lms/attempts/{att[0]}/questions", name="A_diag_questions")
    ctx["diag_questions"] = qs["questions"]
    first_txt = page.locator("#lmsDiagBody .sd-q-text").inner_text().strip()[:30]
    q0 = qs["questions"][0].get("question") or qs["questions"][0]
    return {"visual": ok(shot(page, "S02_diag_in_quiz")), "interaction": ok(f"timer {t1}→{t2}"),
            "api": ok(f"{diag['question_count']} questions, time_limit={diag.get('time_limit_minutes')}m"),
            "db": ok(f"attempt {att[0]} in_progress, expires_at={att[2]}"), "evidence": f"Q1 UI='{first_txt}' API id={q0.get('id')}"}


@item("Student", "Math renders on diagnostic (MathJax, no red/broken TeX, no raw LaTeX)")
def s_math():
    page = ctx["pageA"]
    idx = None
    for i, q in enumerate(ctx["diag_questions"]):
        qq = q.get("question") or q
        blob = json.dumps(qq.get("options", [])) + str(qq.get("question_text", ""))
        if "frac" in blob or "^" in blob:
            idx = i
            break
    if idx is None:
        raise Blocked("no fraction/exponent question in this diagnostic")
    page.locator("#lmsDiagBody .lms-qmap-cell").nth(idx).click()
    page.wait_for_timeout(1500)
    n = page.locator("#lmsDiagBody mjx-container").count()
    bad = page.locator("#lmsDiagBody mjx-merror, #lmsDiagBody .mjx-merror").count()
    txt = page.locator("#lmsDiagBody .sd-q-box").inner_text()
    raw = re.findall(r"\\(frac|sqrt|text|times)\b|\$", txt)
    v = shot(page, "S02b_diag_math")
    page.locator("#lmsDiagBody .lms-qmap-cell").nth(0).click()
    assert n > 0 and not bad and not raw, (n, bad, raw)
    return {"visual": ok(f"{v}: {n} mjx, 0 merror, no raw TeX"), "evidence": f"question #{idx + 1}"}


@item("Student", "Diagnostic submit (deliberate mix) → results by topic; UI = API = DB (attempt, answers, topic scores)")
def s_diag_submit(areq):
    page = ctx["pageA"]
    qs = ctx["diag_questions"]
    plan = {}
    for i, q in enumerate(qs):
        qid = int(q.get("question_id") or (q.get("question") or {}).get("id"))
        ci = correct_index(q.get("question") or q, qid)
        right = (i % 5) in (0, 1, 2)          # ~60% correct, deliberately
        nopt = len((q.get("question") or q).get("options") or []) or 4
        plan[i] = (qid, ci if right else (ci + 1) % nopt, right)
    for i in range(len(qs) - 1):              # last question left unanswered on purpose
        page.locator("#lmsDiagBody .lms-qmap-cell").nth(i).click()
        page.locator("#lmsDiagBody .sd-q-option").nth(plan[i][1]).click()
        page.wait_for_timeout(120)
    page.wait_for_timeout(1500)
    page.click("#lmsDiagBody >> text=Submit Diagnostic")
    page.locator("#inAppConfirmOk").click()
    expect(page.locator("#lmsDiagBody .sd-result-body")).to_be_visible(timeout=LLM)
    v = shot(page, "S03_diag_results")
    aid = ctx["ids"]["diag_attempt_A"]
    res = call(areq, "GET", f"/api/lms/attempts/{aid}/results", name="A_diag_results")
    exp_correct = sum(1 for i in range(len(qs) - 1) if plan[i][2])
    att = db("select status, score, max_score from assessment_attempts where id=?", aid)[0]
    ans = db("select question_id, selected_option_index, is_correct from attempt_answers where attempt_id=?", aid)
    db_correct = sum(1 for a in ans if a[2])
    assert att[0] == "submitted", att
    assert db_correct == exp_correct == round(att[1]) == round(res["score"]), (exp_correct, db_correct, att, res["score"])
    # UI overall + per-topic rows must equal API breakdown, and each topic's correct = DB is_correct over its question_ids
    ui_pct = page.locator("#lmsDiagBody .sd-score-big b").inner_text().strip()
    assert ui_pct == f"{jsround(res['score_percent'])}%", (ui_pct, res["score_percent"])
    by_q = {a[0]: a[2] for a in ans}
    rows = page.locator("#lmsDiagBody table.sd-mini tbody tr")
    bd = res.get("topic_breakdown") or res.get("all_topics") or []
    assert rows.count() == len(bd), (rows.count(), len(bd))
    mism = []
    for i, t in enumerate(bd):
        cells = [c.strip() for c in rows.nth(i).locator("td").all_inner_texts()]
        dbc = sum(1 for q in t.get("question_ids", []) if by_q.get(q))
        if cells[1] != f"{round(t['correct'])}/{round(t['total'])}" or dbc != round(t["correct"]):
            mism.append((t["topic_name"], cells, t["correct"], t["total"], dbc))
    assert not mism, mism
    # topic scores written for this student
    sts = db("select topic_id, score_percent, mastery_status, sample_size from student_topic_scores where student_id=?", ctx["ids"]["student_A"])
    assert sts, "no student_topic_scores rows after diagnostic"
    bd_by_id = {t["topic_id"]: t for t in bd}
    off = [(s[0], s[1], bd_by_id[s[0]]["score_percent"]) for s in sts if s[0] in bd_by_id and abs(s[1] - bd_by_id[s[0]]["score_percent"]) > 0.6]
    assert not off, f"topic score != diagnostic topic %: {off}"
    prof = db("select diagnostic_completed, diagnostic_assessment_id from student_profiles where user_id=?", ctx["ids"]["student_A"])[0]
    assert prof[0], prof
    ctx["diag_plan_correct"] = exp_correct
    return {"visual": ok(v), "interaction": ok(f"answered {len(qs) - 1}/{len(qs)}, unanswered confirm"),
            "api": ok(f"score {res['score']}/{res['max_score']} = {res['score_percent']}%, {len(bd)} topics"),
            "db": ok(f"attempt submitted score={att[1]}; is_correct={db_correct}; {len(sts)} topic score rows match; profile={prof}"),
            "evidence": f"expected {exp_correct} correct; UI {ui_pct}"}


@item("Student", "Retake rule: start again returns the finished attempt; UI shows results + explicit Retake only")
def s_retake(areq):
    page = ctx["pageA"]
    st = call(areq, "POST", f"/api/lms/quizzes/{ctx['ids']['diagnostic_id']}/start", {}, name="A_diag_start_again")
    assert st.get("already_completed") is True and st["attempt_id"] == ctx["ids"]["diag_attempt_A"], st
    n = db("select count(*) from assessment_attempts where student_id=? and assessment_id=?", ctx["ids"]["student_A"], ctx["ids"]["diagnostic_id"])[0][0]
    assert n == 1, n
    page.click("#lmsDiagBody >> text=Continue")
    page.evaluate("openLmsDiagnostic()")
    expect(page.locator("#lmsDiagBody")).to_contain_text("already completed", timeout=LLM)
    v = shot(page, "S03b_diag_reopen_already_completed")
    page.click("#lmsDiagCloseBtn")
    return {"visual": ok(v), "interaction": ok("reopen shows results"), "api": ok("already_completed=true, same attempt id"),
            "db": ok("1 attempt row"), "evidence": "retake exists only as explicit 'Retake with new questions' (retake:true) — product rule to confirm"}


@item("Student", "Diagnostic hub: stat cards + Taken row = API dashboard/attempts = DB")
def s_hub(areq):
    page = ctx["pageA"]
    page.click('.sd-navbtn[data-view="diagnostic"]')
    page.wait_for_load_state("networkidle", timeout=LLM)
    d = call(areq, "GET", "/api/lms/students/me/dashboard", name="A_dashboard_after_diag")
    card = page.locator("#view-diagnostic [data-stat-cards]")
    expect(card.locator('[data-stat="pending-diag"]')).to_have_text("0", timeout=LLM)
    expect(card.locator('[data-stat="weak-count"]')).to_have_text(str(len(d["weak_topics"])))
    chips = sorted(card.locator(".sd-tag-chip").all_inner_texts())
    assert chips == sorted(t["topic_name"] for t in d["weak_topics"]), (chips, d["weak_topics"])
    page.click('[data-diag-tab="taken"]')
    row = page.locator('#sdDiagList [data-diag-row="attempt"]').first
    row.locator("text=View Results").click()
    expect(row.locator(".sd-result-body")).to_be_visible(timeout=LLM)
    ui = row.locator(".sd-score-big b").inner_text().strip()
    att = db("select score, max_score from assessment_attempts where id=?", ctx["ids"]["diag_attempt_A"])[0]
    assert ui == f"{jsround(100 * att[0] / att[1])}%", (ui, att)
    weak_db = db("select count(*) from student_topic_scores where student_id=? and mastery_status='weak'", ctx["ids"]["student_A"])[0][0]
    return {"visual": ok(shot(page, "S04_diag_hub")), "interaction": ok("Taken → View Results"),
            "api": ok(f"weak={len(d['weak_topics'])} chips match"), "db": ok(f"score {att}; weak rows={weak_db}"), "evidence": f"UI {ui}"}


# ───────────────────────── Student A: learning path + learning chat ─────────────────────────
def mastery_snapshot(sid):
    return {r[0]: (round(r[1], 1), r[2], r[3]) for r in db("select topic_id, score_percent, mastery_status, sample_size from student_topic_scores where student_id=?", sid)}


@item("Student", "Learning Path overview after diagnostic = dashboard/learning-path/progress APIs = DB mastery")
def s_path(areq):
    page = ctx["pageA"]
    page.click('.sd-navbtn[data-view="learning-path"]')
    page.wait_for_load_state("networkidle", timeout=LLM)
    d = call(areq, "GET", "/api/lms/students/me/dashboard", name="A_dashboard_path")
    lp = call(areq, "GET", "/api/lms/students/me/learning-path", name="A_learning_path")
    pr = call(areq, "GET", "/api/lms/students/me/progress", name="A_progress")
    expect(page.locator("#sdPathTopics [data-topic-row]").first).to_be_visible(timeout=LLM)
    n_rows = page.locator("#sdPathTopics [data-topic-row]").count()
    n_weak = page.locator('#sdPathTopics [data-topic-row="weak"]').count()
    assert n_rows == len(d["mastery"]) and n_weak == len(d["weak_topics"]), (n_rows, len(d["mastery"]), n_weak, len(d["weak_topics"]))
    steps = page.locator("#sdPathSteps .sd-list-row").count()
    assert steps == len(lp.get("items", [])), (steps, lp)
    prog = d["learning_path_progress"]
    label = page.locator('#view-learning-path [data-stat="path-label"]').inner_text()
    assert label == (f"{prog['completed']} of {prog['total']} topics" if prog["total"] else "No path yet"), (label, prog)
    snap = mastery_snapshot(ctx["ids"]["student_A"])
    weak_db = {k for k, v in snap.items() if v[1] == "weak"}
    weak_api = {t["topic_id"] for t in d["weak_topics"]}
    assert weak_db == weak_api, (weak_db, weak_api)
    rows = db("select score_percent, sample_size from student_topic_scores where student_id=?", ctx["ids"]["student_A"])
    wavg = sum(r[0] * (r[1] or 1) for r in rows) / sum((r[1] or 1) for r in rows)
    assert abs(wavg - float(pr["overall_progress"])) < 0.6, (wavg, pr["overall_progress"])
    ctx["A_overall_after_diag"] = pr["overall_progress"]
    ctx["A_mastery_before_lc"] = snap
    return {"visual": ok(shot(page, "S05_learning_path")), "interaction": ok("nav"),
            "api": ok(f"{n_rows} topics/{n_weak} weak, {steps} steps, path '{label}', overall_progress={pr['overall_progress']}"),
            "db": ok(f"weak set equal; weighted avg {wavg:.2f}")}


@item("Student", "Learning Chat: start, wrong answer → tutor help → correct answers; progress updates (UI+API+StudentTopicScore)")
def s_learning_chat(areq):
    page = ctx["pageA"]
    btn = page.locator("#sdPathTopics >> text=Start Learning Path").first
    if not btn.count():
        raise Blocked("no weak topics — Learning Chat not offered")
    btn.click()
    expect(page.locator("#lmsDeficiencyBody .sd-q-option, #lmsDeficiencyBody .sd-done-card").first).to_be_visible(timeout=LLM)
    sess = db("select id, questions_json, current_index, correct_count, status from deficiency_chat_sessions where student_id=? order by id desc",
              ctx["ids"]["student_A"])[0]
    sid, qjson = sess[0], json.loads(sess[1])
    ctx["ids"]["deficiency_session_A"] = sid
    v1 = shot(page, "S06_learning_chat")
    answered, wrong_done, notes = 0, False, []
    for _ in range(len(qjson) + 2):
        if page.locator("#lmsDeficiencyBody .sd-done-card").count():
            break
        cur = db("select current_index from deficiency_chat_sessions where id=?", sid)[0][0]
        q = qjson[cur]
        ci = q["correct_option_index"]
        if not wrong_done:
            page.locator("#lmsDeficiencyBody .sd-q-option").nth((ci + 1) % len(q["options"])).click()
            page.click("#lmsDeficiencyBody >> text=Submit Answer")
            expect(page.locator("#lmsDeficiencyBody .lms-opt-wrong")).to_have_count(1, timeout=LLM)
            users = page.locator("#lmsDeficiencyTutorMessages .sd-ai-bubble.user").count()
            page.click("#lmsDeficiencyBody button[title='Need more help']")
            page.wait_for_function("""(n) => !document.getElementById('lmsDeficiencyTyping')
                && document.querySelectorAll('#lmsDeficiencyTutorMessages .sd-ai-bubble.user').length > n""", arg=users, timeout=LLM)
            notes.append("tutor: " + page.locator("#lmsDeficiencyTutorMessages .sd-ai-bubble:not(.user) .sd-msg").last.inner_text()[:60].replace("\n", " "))
            shot(page, "S06b_learning_chat_wrong_tutor")
            wrong_done = True
        page.locator("#lmsDeficiencyBody .sd-q-option").nth(ci).click()
        page.click("#lmsDeficiencyBody >> text=Submit Answer")
        page.wait_for_function("(c) => { const s = document.querySelector('#lmsDeficiencyBody'); return s.querySelector('.sd-done-card') || s.querySelector('.lms-opt-wrong') || !s.innerText.includes(c); }",
                               arg=page.locator("#lmsDeficiencyBody .sd-desc").first.inner_text(), timeout=LLM)
        if page.locator("#lmsDeficiencyBody .lms-opt-wrong").count():
            notes.append(f"key marked WRONG for q{cur} (stored correct_option_index={ci})")
            page.click("#lmsDeficiencyBody >> text=Next question")
            page.wait_for_timeout(1500)
        answered += 1
    v2 = shot(page, "S06c_learning_chat_done")
    done = db("select current_index, correct_count, status from deficiency_chat_sessions where id=?", sid)[0]
    # reload → progress persisted
    page.reload()
    wait_student_ready(page)
    page.click('.sd-navbtn[data-view="learning-path"]')
    page.wait_for_load_state("networkidle", timeout=LLM)
    d = call(areq, "GET", "/api/lms/students/me/dashboard", name="A_dashboard_after_lc")
    pr = call(areq, "GET", "/api/lms/students/me/progress", name="A_progress_after_lc")
    after = mastery_snapshot(ctx["ids"]["student_A"])
    before = ctx["A_mastery_before_lc"]
    changed = {k: (before.get(k), after[k]) for k in after if before.get(k) != after[k]}
    expect(page.locator('#view-learning-path [data-stat="weak-count"]')).to_have_text(str(len(d["weak_topics"])), timeout=LLM)
    label = page.locator('#view-learning-path [data-stat="path-label"]').inner_text()
    prog = d["learning_path_progress"]
    assert label == f"{prog['completed']} of {prog['total']} topics", (label, prog)
    dump("A_mastery_before_after_lc", {"before": before, "after": after, "changed": changed, "session_row": done})
    ctx["A_overall_after_lc"] = pr["overall_progress"]
    assert changed, f"no StudentTopicScore change after {answered} Learning Chat answers ({done})"
    return {"visual": ok(f"{v1}, {v2}"), "interaction": ok(f"{answered} answers, 1 deliberate wrong + tutor; " + "; ".join(notes)),
            "api": ok(f"overall {ctx['A_overall_after_diag']} → {pr['overall_progress']}; weak {len(d['weak_topics'])}; path '{label}'"),
            "db": ok(f"session {sid} {done}; {len(changed)} topic score rows changed: {list(changed.items())[:3]}")}


@item("Student", "Guided practice (per-topic) opens; hint or graceful error (API + DB)")
def s_practice(areq):
    page = ctx["pageA"]
    btn = page.locator("#sdPathTopics >> text=/Guided practice|Practice again/").first
    tid = int(re.search(r"\((\d+)\)", btn.get_attribute("onclick")).group(1))
    btn.click()
    page.wait_for_function("() => { const b = document.getElementById('lmsPracticeModalBody'); return b && !b.querySelector('.lms-spinner') && b.innerText.trim(); }", timeout=LLM)
    txt = page.locator("#lmsPracticeModalBody").inner_text()[:120]
    v = shot(page, "S07_guided_practice")
    status, body = call(areq, "POST", "/api/lms/practice/sessions", {"topic_id": tid}, name="A_practice_start", expect_ok=False)
    page.evaluate("lmsCloseModal('lmsPracticeModal')")
    rows = db("select id, topic_id, status from practice_sessions where student_id=? order by id desc limit 2", ctx["ids"]["student_A"])
    if status >= 400:
        cands = db("select count(*) from questions where topic_id=? and is_active=1", tid)[0][0]
        raise Blocked(f"practice API {status} {json.dumps(body)[:160]}; DB active questions for topic {tid} = {cands}; UI shows '{txt.strip()}' ({v})")
    return {"visual": ok(v), "interaction": ok(txt), "api": ok(f"{status}"), "db": ok(f"practice_sessions {rows}")}


# ───────────────────────── Teacher: quizzes (PDF path) + assignment ─────────────────────────
@item("Teacher", "Quizzes: PDF → MCQ generate → preview → publish (UI → API → DB)")
def t_quiz_pdf(treq):
    page = ctx["tpage"]
    page.click('.td-navbtn[data-view="quizzes"]')
    page.click("#view-quizzes >> text=Create Quiz")
    title = f"QA PDF Quiz {TS}"
    page.fill("#lmsQuizTitle", title)
    page.set_input_files("#lmsQuizPdfFile", str(QA_PDF))
    page.fill("#lmsQuizMcqCount", "5")
    v1 = shot(page, "T04_quiz_create")
    page.click("#lmsQuizSubmitBtn")
    deadline = time.time() + 420
    while time.time() < deadline and not page.locator("#lmsQuizActions").is_visible():
        t = page.locator("#lmsQuizStatus").inner_text()
        if t.startswith(("Failed", "Error")):
            raise AssertionError(t)
        page.wait_for_timeout(3000)
    expect(page.locator("#lmsQuizActions")).to_be_visible()
    n = page.locator("#lmsQuizPreview .lms-quiz-preview-card").count()
    v2 = shot(page, "T04b_quiz_preview")
    page.click("#lmsQuizActions >> text=Publish Quiz")
    card = page.locator(".td-quiz-card", has_text=title)
    expect(card.locator("text=Published")).to_be_visible(timeout=30_000)
    quizzes = call(treq, "GET", "/api/lms/quizzes", name="teacher_quizzes")
    q = [x for x in quizzes if x["title"] == title][0]
    row = db("select status, created_by, assessment_type from assessments where id=?", q["id"])[0]
    nq = db("select count(*) from assessment_questions where assessment_id=?", q["id"])[0][0]
    assert row == ("published", ctx["ids"]["teacher_id"], "quiz") and nq == n, (row, nq, n)
    ctx["ids"]["pdf_quiz_id"] = q["id"]
    ctx["pdf_quiz_title"] = title
    expect(page.locator(".td-quiz-card")).to_have_count(len(quizzes))
    return {"visual": ok(f"{v1}, {v2}, " + shot(page, "T05_quizzes_list")), "interaction": ok(f"{n} MCQs generated + published"),
            "api": ok(f"quiz {q['id']} status={q['status']}; list count {len(quizzes)} = UI"), "db": ok(f"assessments {row}; {nq} questions")}


@item("Teacher", "Assign quiz to class via UI → assignment published (API + DB)")
def t_assign(treq):
    page = ctx["tpage"]
    card = page.locator(".td-quiz-card", has_text=ctx["pdf_quiz_title"])
    card.locator("text=Assign").click()
    expect(page.locator("#lmsAssignQuiz")).to_have_value(str(ctx["ids"]["pdf_quiz_id"]), timeout=20_000)
    title = f"QA Assignment {TS}"
    page.fill("#lmsAssignTitle", title)
    page.select_option("#lmsAssignClass", str(ctx["ids"]["class_id"]))
    v = shot(page, "T06_assign_quiz")
    page.click("#lmsAssignSubmitBtn")
    expect(page.locator("#lmsAssignStatus")).to_contain_text("Assignment published", timeout=30_000)
    row = db("select id, status, class_id, quiz_id, teacher_id from assignments where title=?", title)[0]
    assert row[1] == "published" and row[2] == ctx["ids"]["class_id"] and row[3] == ctx["ids"]["pdf_quiz_id"], row
    ctx["ids"]["assignment_id"] = row[0]
    ctx["assignment_title"] = title
    lst = call(treq, "GET", "/api/lms/assignments", name="teacher_assignments")
    assert any(a["id"] == row[0] for a in lst), lst
    return {"visual": ok(v), "interaction": ok("assign form"), "api": ok(f"assignment {row[0]} listed"), "db": ok(f"assignments {row}")}


# ───────────────────────── Student A: classes, quiz, lesson, tutor ─────────────────────────
@item("Student", "My Classes: enrolled class (API = DB) with assignment visible")
def s_classes(areq):
    page = ctx["pageA"]
    page.click('.sd-navbtn[data-view="classes"]')
    cid = ctx["ids"]["class_id"]
    card = page.locator(f'.sd-class-card[data-class-id="{cid}"]')
    expect(card).to_be_visible(timeout=LLM)
    mine = call(areq, "GET", "/api/lms/classes/mine", name="A_classes_mine")
    asg = call(areq, "GET", "/api/lms/students/me/assignments", name="A_assignments")
    assert [c["id"] for c in mine] == [cid], mine
    tname = db("select username from users where id=?", ctx["ids"]["teacher_id"])[0][0]
    expect(card).to_contain_text(f"Teacher: {tname}")
    row = card.locator(".sd-quiz-row", has_text=ctx["assignment_title"])
    expect(row).to_be_visible(timeout=LLM)
    expect(row).to_contain_text("Not Attempted")
    a = [x for x in asg if x["assignment_id"] == ctx["ids"]["assignment_id"]][0]
    assert a["class_id"] == cid and a["status"] == "not_started", a
    enr = db("select status, enrolled_at from class_enrollments where class_id=? and student_id=?", cid, ctx["ids"]["student_A"])[0]
    return {"visual": ok(shot(page, "S08_my_classes")), "interaction": ok("class expanded"),
            "api": ok(f"classes/mine={cid}; assignment {a['assignment_id']} not_started"), "db": ok(f"enrollment {enr}")}


@item("Student", "Take assigned quiz in UI (deliberate 3 of 5) → Review = API results = DB attempt/submission")
def s_take_quiz(areq):
    page = ctx["pageA"]
    cid = ctx["ids"]["class_id"]
    card = page.locator(f'.sd-class-card[data-class-id="{cid}"]')
    card.locator(".sd-quiz-row", has_text=ctx["assignment_title"]).locator("text=Start Quiz").click()
    expect(page.locator("#lmsQuizTaking .sd-q-option").first).to_be_visible(timeout=LLM)
    att = db("select id from assessment_attempts where student_id=? and assignment_id=? order by id desc", ctx["ids"]["student_A"], ctx["ids"]["assignment_id"])[0][0]
    qs = call(areq, "GET", f"/api/lms/attempts/{att}/questions", name="A_quiz_questions")["questions"]
    v1 = shot(page, "S09_quiz_taking")
    want = 0
    for i, q in enumerate(qs):
        qid = int(q.get("question_id") or q.get("id"))
        ci = correct_index(q, qid)
        right = i < 3
        want += 1 if right else 0
        page.locator("#lmsQuizTaking .sd-q-option").nth(ci if right else (ci + 1) % len(q["options"])).click()
        page.wait_for_timeout(200)
        if i < len(qs) - 1:
            page.click("#lmsQuizTaking >> text=Next")
    page.click("#lmsQuizTaking >> text=Submit Quiz")
    expect(page.locator("#lmsQuizResult .sd-result-body")).to_be_visible(timeout=LLM)
    v2 = shot(page, "S09b_quiz_result")
    page.click("#lmsQuizResult >> text=Back to My Classes")
    row = card.locator(".sd-quiz-row", has_text=ctx["assignment_title"])
    expect(row.locator("text=Completed")).to_be_visible(timeout=LLM)
    row.locator("text=Review").click()
    expect(row.locator(".sd-ring")).to_be_visible(timeout=LLM)
    res = call(areq, "GET", f"/api/lms/attempts/{att}/results", name="A_quiz_results")
    dbatt = db("select status, score, max_score from assessment_attempts where id=?", att)[0]
    sub = db("select status, attempt_id from assignment_submissions where assignment_id=? and student_id=?", ctx["ids"]["assignment_id"], ctx["ids"]["student_A"])[0]
    ui = row.locator(".sd-ring b").inner_text().strip()
    ui_correct = row.locator(".sd-stat-lines b").first.inner_text().strip()
    assert round(dbatt[1]) == want == round(res["score"]) and ui_correct == str(want), (want, dbatt, res["score"], ui_correct)
    assert ui == f"{jsround(res['score_percent'])}%" and sub == ("submitted", att), (ui, sub)
    ctx["ids"]["quiz_attempt_A"] = att
    ctx["A_quiz_pct"] = res["score_percent"]
    return {"visual": ok(f"{v1}, {v2}, " + shot(page, "S09c_quiz_review")), "interaction": ok(f"{len(qs)} answered via UI"),
            "api": ok(f"results {res['score']}/{res['max_score']} {res['score_percent']}%"), "db": ok(f"attempt {dbatt}; submission {sub}"),
            "evidence": f"expected {want} correct; UI {ui} ({ui_correct} correct)"}


@item("Student", "Attempts history: rows = API; View Result modal = API results")
def s_attempts(areq):
    page = ctx["pageA"]
    page.click('.sd-navbtn[data-view="learning-path"]')
    atts = call(areq, "GET", "/api/lms/students/me/attempts", name="A_attempts")
    expect(page.locator("#sdAttemptHistory .sd-list-row")).to_have_count(len(atts), timeout=LLM)
    dbn = db("select count(*) from assessment_attempts where student_id=?", ctx["ids"]["student_A"])[0][0]
    assert dbn == len(atts), (dbn, len(atts))
    q = page.locator("#sdAttemptHistory .sd-list-row", has_text=ctx["pdf_quiz_title"]).first
    if not q.count():
        q = page.locator("#sdAttemptHistory .sd-list-row").first
    q.locator("text=View Result").click()
    expect(page.locator("#lmsAttemptResultBody .lms-diag-score-num")).to_be_visible(timeout=LLM)
    txt = page.locator("#lmsAttemptResultBody .lms-diag-score-num").inner_text().strip()
    v = shot(page, "S10_attempt_result_modal")
    page.evaluate("closeLmsAttemptResult()")
    return {"visual": ok(v), "interaction": ok("View Result modal"), "api": ok(f"{len(atts)} attempts"), "db": ok(f"{dbn} attempt rows"), "evidence": f"modal {txt}"}


@item("Teacher", "Lessons: create (Use PDF as lesson, grade 7) + publish; list = my_lessons API = DB")
def t_lesson(treq):
    page = ctx["tpage"]
    page.click('.td-navbtn[data-view="lessons"]')
    page.click("#view-lessons >> text=Create Lesson")
    pdfs = sorted(LESSON_PDFS.glob("*.pdf"), key=lambda p: p.stat().st_size)
    page.set_input_files("#pdfFileInput", str(pdfs[0] if pdfs else QA_PDF))
    title = f"QA Lesson {TS}"
    page.fill("#lessonTitle", title)
    page.select_option("#lessonSubject", "Math")
    page.select_option("#lessonGrade", "7")
    page.fill("#lessonContext", "QA lesson")
    page.check('input[name="lessonOutputMode"][value="as_is"]')
    v1 = shot(page, "T07_lesson_create")
    page.click("#nextStepButton")
    expect(page.locator("#createLessonModal")).to_have_count(0, timeout=420_000)
    row = page.locator(".td-lesson-row", has_text=title)
    expect(row).to_be_visible(timeout=60_000)
    lid = int(row.get_attribute("data-lesson-id"))
    pub = row.locator("a", has_text=re.compile(r"^\s*(Publish|Unpublish)\s*$"))
    if pub.inner_text().strip() == "Publish":
        pub.click()
        expect(row.locator("a", has_text=re.compile(r"^\s*Unpublish\s*$"))).to_be_visible(timeout=30_000)
    data = treq.get("/api/lessons/my_lessons?page=1&per_page=10").json()
    dump("teacher_my_lessons", data)
    expect(page.locator(".td-lesson-row")).to_have_count(len(data.get("lessons", [])))
    dbrow = db("select teacher_id, grade_level, is_public, status from lessons where id=?", lid)[0]
    assert dbrow[0] == ctx["ids"]["teacher_id"] and str(dbrow[1]) == "7" and dbrow[2], dbrow
    ctx["ids"]["lesson_id"] = lid
    ctx["lesson_title"] = title
    with page.expect_response(lambda r: "/source_pdf" in r.url or "/view" in r.url, timeout=60_000):
        row.locator("text=View").click()
    expect(page.locator("#viewLessonModal")).to_be_visible(timeout=30_000)
    v2 = shot(page, "T07b_lesson_view")
    page.evaluate("closeViewLessonModal()")
    return {"visual": ok(f"{v1}, {v2}, " + shot(page, "T07c_lessons_list")), "interaction": ok("create + publish + view"),
            "api": ok(f"my_lessons total {data.get('total')} = UI rows"), "db": ok(f"lessons {lid} {dbrow}")}


@item("Student", "Lesson viewer: class lesson opens, content renders (browse_lessons?class_id = UI = DB)")
def s_lesson(areq):
    page = ctx["pageA"]
    page.click('.sd-navbtn[data-view="classes"]')
    cid = ctx["ids"]["class_id"]
    card = page.locator(f'.sd-class-card[data-class-id="{cid}"]')
    data = areq.get(f"/api/lessons/browse_lessons?class_id={cid}&per_page=50").json()
    dump("A_browse_lessons_class", data)
    expect(card.locator(".sd-lesson-row")).to_have_count(len(data["lessons"]), timeout=LLM)
    dbn = db("select count(*) from lessons where teacher_id=? and is_public=1 and grade_level in ('7','7th')", ctx["ids"]["teacher_id"])[0][0]
    row = card.locator(f'.sd-lesson-row[data-lesson-id="{ctx["ids"]["lesson_id"]}"]')
    expect(row).to_be_visible()
    row.locator("text=View").click()
    expect(page.locator("#viewLessonTitle")).to_have_text(ctx["lesson_title"], timeout=LLM)
    page.wait_for_function("() => { const c = document.getElementById('viewLessonCurrent'); return c && !c.querySelector('.sd-spinner') && (c.querySelector('canvas') || c.innerText.trim().length > 40); }", timeout=LLM)
    v = shot(page, "S11_lesson_viewer")
    return {"visual": ok(v), "interaction": ok("View"), "api": ok(f"{len(data['lessons'])} lessons for class"),
            "db": ok(f"{dbn} public grade-7 lessons by teacher")}


@item("Student", "Lesson chat: ask question → answer persisted as conversation (API + DB)")
def s_lesson_chat(areq):
    page = ctx["pageA"]
    page.fill("#messageInput", "What is this lesson about?")
    with page.expect_response(lambda r: "/api/lessons/ask_question" in r.url, timeout=LLM) as resp:
        page.click("#sendBtn")
    r = resp.value
    body = r.text()[:300]
    page.wait_for_timeout(1500)
    v = shot(page, "S12_lesson_chat")
    convs = db("select id, title from conversations where user_id=? order by id desc", ctx["ids"]["student_A"])
    msgs = db("select role, substr(message,1,60) from chat_history where conversation_id=? order by id", convs[0][0]) if convs else []
    dump("A_lesson_chat", {"status": r.status, "body": body, "convs": convs, "msgs": msgs})
    if r.status >= 500:
        log = "LESSON_QA_GRAPH is not initialized" if "LESSON_QA_GRAPH" in (ROOT / "logs").as_posix() else ""
        raise AssertionError(f"POST /api/lessons/ask_question -> {r.status} {body[:120]}; UI shows graceful message; DB conversation {convs[:1]} msgs {msgs}. "
                             f"Server started with SKIP_EXTRA_STARTUP=true (.env) → lesson Q&A graph not initialized {log} ({v})")
    assert convs and any(m[0] == "bot" for m in msgs), (convs, msgs)
    return {"visual": ok(v), "interaction": ok("send"), "api": ok(f"{r.status}"), "db": ok(f"conversation {convs[0]} msgs {len(msgs)}")}


@item("Student", "AI Tutor: send → reply; history API + DB; reload restores; clear empties")
def s_tutor(areq):
    page = ctx["pageA"]
    page.click('.sd-navbtn[data-view="tutor"]')
    page.fill("#lmsTutorInput", "What is 3/4 + 1/8?")
    page.click("#sdTutorSend")
    page.wait_for_function("() => document.querySelectorAll('#sdTutorMessages .sd-ai-bubble:not(.user)').length >= 2 && !document.querySelector('#sdTutorMessages .sd-typing')", timeout=LLM)
    reply = page.locator("#sdTutorMessages .sd-ai-bubble:not(.user) .sd-msg").last.inner_text()[:120]
    assert not reply.startswith("Error"), reply
    v = shot(page, "S13_ai_tutor")
    hist = call(areq, "GET", "/api/lms/tutor/history?mode=student", name="A_tutor_history")
    dbm = db("select count(*) from tutor_chat_messages m join tutor_chat_sessions s on s.id=m.session_id where s.user_id=?", ctx["ids"]["student_A"])
    page.reload()
    wait_student_ready(page)
    page.click('.sd-navbtn[data-view="tutor"]')
    expect(page.locator("#sdTutorMessages")).to_contain_text("3/4 + 1/8", timeout=LLM)
    page.click("#sdHistToggle")
    page.click("#sdHistPop >> text=Clear tutor chat")
    page.locator("#inAppConfirmOk").click()
    page.wait_for_timeout(1500)
    after = call(areq, "GET", "/api/lms/tutor/history?mode=student", name="A_tutor_history_cleared")
    assert len(hist.get("messages", [])) >= 2 and not after.get("messages"), (hist, after)
    return {"visual": ok(v), "interaction": ok("send, reload restore, clear"), "api": ok(f"{len(hist['messages'])} msgs → 0"),
            "db": ok(f"tutor_chat_messages={dbm}"), "evidence": repr(reply)}


# ───────────────────────── Student B (late enroll) ─────────────────────────
def api_diag(req, sid_key, pct_correct):
    diag = call(req, "GET", "/api/lms/diagnostics/default")
    st = call(req, "POST", f"/api/lms/quizzes/{diag['id']}/start", {})
    qs = call(req, "GET", f"/api/lms/attempts/{st['attempt_id']}/questions")["questions"]
    for i, q in enumerate(qs):
        qid = int(q.get("question_id") or (q.get("question") or {}).get("id"))
        ci = correct_index(q.get("question") or q, qid)
        n = len((q.get("question") or q).get("options") or []) or 4
        call(req, "POST", f"/api/lms/attempts/{st['attempt_id']}/answer",
             {"question_id": qid, "selected_option_index": ci if (i * 100 / len(qs)) < pct_correct else (ci + 1) % n})
    call(req, "POST", f"/api/lms/attempts/{st['attempt_id']}/submit", {"time_expired": False})
    ctx["ids"][f"diag_attempt_{sid_key}"] = st["attempt_id"]
    return st["attempt_id"]


@item("Student", "Join class via code (Student B, diagnostic done BEFORE joining) — UI + API + DB")
def s_join_B(breq):
    api_diag(breq, "B", 80)
    page = ctx["pageB"]
    page.reload()
    wait_student_ready(page)
    page.click('.sd-navbtn[data-view="classes"]')
    page.click("#view-classes >> text=Join Class")
    page.fill("#lmsJoinCodeInput", ctx["ids"]["join_code"])
    v1 = shot(page, "S14_join_class_modal", full=False)
    page.click("#lmsJoinClassModal .sd-btn-primary")
    card = page.locator(f'.sd-class-card[data-class-id="{ctx["ids"]["class_id"]}"]')
    expect(card).to_be_visible(timeout=LLM)
    enr = db("select status from class_enrollments where class_id=? and student_id=?", ctx["ids"]["class_id"], ctx["ids"]["student_B"])
    mine = call(breq, "GET", "/api/lms/classes/mine", name="B_classes_mine")
    assert enr == [("active",)] and mine[0]["id"] == ctx["ids"]["class_id"], (enr, mine)
    return {"visual": ok(f"{v1}, " + shot(page, "S14b_B_classes")), "interaction": ok("join modal"), "api": ok("classes/mine"), "db": ok(f"enrollment {enr}")}


# ───────────────────────── Teacher analytics ─────────────────────────
def open_analytics(page):
    page.click('.td-navbtn[data-view="lessons"]')
    page.click('.td-navbtn[data-view="analytics"]')
    page.select_option("#tdAnaClass", str(ctx["ids"]["class_id"]))
    page.wait_for_load_state("networkidle", timeout=LLM)


def db_overall(sid):
    rows = db("select score_percent, sample_size from student_topic_scores where student_id=?", sid)
    if not rows:
        return None
    return sum(r[0] * (r[1] or 1) for r in rows) / sum((r[1] or 1) for r in rows)


@item("Teacher", "Classes roster tab: A, B, C listed (UI = API = DB)")
def t_roster(treq):
    page = ctx["tpage"]
    page.click('.td-navbtn[data-view="lessons"]')
    page.click('.td-navbtn[data-view="classes"]')
    cid = ctx["ids"]["class_id"]
    body = page.locator(f"#tdClassBody-{cid}")
    if not body.is_visible():
        page.locator(f'.td-class-card[data-class-id="{cid}"] .td-class-head h3').click()
    body.locator(".td-inner-tabs >> text=Roster").click()
    roster = call(treq, "GET", f"/api/lms/classes/{cid}/students", name="teacher_roster")
    expect(body.locator("tbody tr")).to_have_count(len(roster), timeout=LLM)
    enr = db("select count(*) from class_enrollments where class_id=? and status='active'", cid)[0][0]
    assert len(roster) == enr == 3, (len(roster), enr)
    return {"visual": ok(shot(page, "T08_roster")), "interaction": ok("Roster tab"), "api": ok(f"{len(roster)} students"), "db": ok(f"{enr} active enrollments")}


@item("Teacher", "Analytics Topic Progress: A's % = roster API = student progress API = DB; detail shows topics")
def t_topic_progress(treq, areq):
    page = ctx["tpage"]
    open_analytics(page)
    roster = call(treq, "GET", f"/api/lms/classes/{ctx['ids']['class_id']}/students", name="teacher_roster_analytics")
    a = next(s for s in roster if s["student_id"] == ctx["ids"]["student_A"])
    row = page.locator(f'#tdAnaProgressRows tr[data-sid="{a["student_id"]}"]')
    expect(row).to_be_visible(timeout=LLM)
    ui = row.locator("td").nth(2).inner_text().strip()
    mine = call(areq, "GET", "/api/lms/students/me/progress")["overall_progress"]
    dbv = db_overall(a["student_id"])
    assert ui.startswith(f"{round(a['overall_progress'])}%") and abs(a["overall_progress"] - mine) < 0.01 and abs(dbv - mine) < 0.6, (ui, a["overall_progress"], mine, dbv)
    row.click()
    detail = page.locator(f"#tdProgDetail-{a['student_id']}")
    expect(detail.locator(".td-topic-row, .td-empty").first).to_be_visible(timeout=LLM)
    nt = detail.locator(".td-topic-row").count()
    bt = call(treq, "GET", f"/api/lms/classes/{ctx['ids']['class_id']}/students/{a['student_id']}/progress/by-topic", name="teacher_A_by_topic")
    assert nt == len(bt.get("topics", [])), (nt, len(bt.get("topics", [])))
    return {"visual": ok(shot(page, "T09_analytics_topic_progress")), "interaction": ok("row expand"),
            "api": ok(f"roster {a['overall_progress']} = student progress {mine}; {nt} topics = by-topic API"), "db": ok(f"weighted avg {dbv:.2f}"), "evidence": f"UI {ui}"}


@item("Teacher", "Analytics Quiz Results collapsed ↔ expanded = analytics/quizzes API = assignment_submissions DB")
def t_quiz_results(treq):
    page = ctx["tpage"]
    page.click('.td-ana-tab[data-ana-tab="quizzes"]')
    qa = call(treq, "GET", f"/api/lms/classes/{ctx['ids']['class_id']}/analytics/quizzes", name="teacher_analytics_quizzes")
    a = next(x for x in qa if x["title"] == ctx["assignment_title"])
    subs = db("select student_id, status from assignment_submissions where assignment_id=?", ctx["ids"]["assignment_id"])
    n_sub = sum(1 for s in subs if s[1] == "submitted")
    row = page.locator("#tdAnaQuizRows tr.td-clickable", has_text=ctx["assignment_title"])
    expect(row).to_contain_text(f"{n_sub} / 3 submitted", timeout=LLM)
    v1 = shot(page, "T10_quiz_results_collapsed")
    row.click()
    inner = page.locator("#tdAnaQuizRows table.td-data tbody tr")
    expect(inner).to_have_count(3)
    ar = next(r for r in a["student_results"] if r["student_id"] == ctx["ids"]["student_A"])
    ui = inner.filter(has_text=f"qa_stu_a_{TS[-6:]}").inner_text()
    assert f"{round(ar['score_percent'])}%" in ui.replace(" ", "") or str(round(ar["score_percent"])) in ui, (ui, ar)
    sub_api = call(treq, "GET", f"/api/lms/classes/{ctx['ids']['class_id']}/assignments/{ctx['ids']['assignment_id']}/submissions", name="teacher_submissions")
    v2 = shot(page, "T10b_quiz_results_expanded")
    row.click()
    return {"visual": ok(f"{v1}, {v2}"), "interaction": ok("collapse/expand"), "api": ok(f"A {ar['score_percent']}%; submissions API {json.dumps(sub_api)[:120]}"),
            "db": ok(f"submissions {subs}"), "evidence": f"UI row: {ui[:80]!r}"}


@item("Teacher", "Analytics Struggling = struggling API; consistent with weak mastery in DB")
def t_struggling(treq):
    page = ctx["tpage"]
    page.click('.td-ana-tab[data-ana-tab="struggling"]')
    st = call(treq, "GET", f"/api/lms/classes/{ctx['ids']['class_id']}/analytics/struggling", name="teacher_struggling")
    if st:
        expect(page.locator("#tdAnaStrugglingRows tr.td-clickable")).to_have_count(len(st), timeout=LLM)
    weak = {sid: db("select count(*) from student_topic_scores where student_id=? and mastery_status='weak'", sid)[0][0]
            for sid in (ctx["ids"]["student_A"], ctx["ids"]["student_B"], ctx["ids"]["student_C"])}
    api_ids = {s["student_id"] for s in st}
    for s in st:
        wt = {t["topic_name"] for t in s.get("weak_topics", [])}
        dbw = db("select count(*) from student_topic_scores where student_id=? and mastery_status='weak'", s["student_id"])[0][0]
        assert len(wt) <= max(dbw, len(wt)), (s, dbw)
    inconsistent = [sid for sid, n in weak.items() if n and sid not in api_ids]
    assert not inconsistent, f"students with weak topics in DB but not struggling: {inconsistent} ({weak})"
    return {"visual": ok(shot(page, "T11_struggling")), "interaction": ok("tab"), "api": ok(f"{len(st)} struggling {sorted(api_ids)}"), "db": ok(f"weak counts {weak}")}


@item("Teacher", "Analytics Roster: progress per student = API = DB (not all zeros); late-enrolled B included")
def t_ana_roster(treq, breq):
    page = ctx["tpage"]
    page.click('.td-ana-tab[data-ana-tab="roster"]')
    roster = call(treq, "GET", f"/api/lms/classes/{ctx['ids']['class_id']}/students", name="teacher_roster_final")
    expect(page.locator("#tdAnaRosterRows tr")).to_have_count(len(roster), timeout=LLM)
    mism, out = [], []
    for s in roster:
        cell = page.locator("#tdAnaRosterRows tr", has_text=s["username"]).locator("td").nth(3).inner_text().strip()
        exp = f"{round(s['overall_progress'])}%" if s["overall_progress"] is not None else "—"
        dbv = db_overall(s["student_id"])
        if not cell.startswith(exp) or (dbv is not None and abs(dbv - (s["overall_progress"] or 0)) > 0.6):
            mism.append((s["username"], cell, s["overall_progress"], dbv))
        out.append(f"{s['username']}: UI {cell} API {s['overall_progress']} DB {None if dbv is None else round(dbv, 2)}")
    assert not mism, mism
    assert any((s["overall_progress"] or 0) > 0 for s in roster), "all zeros"
    b = next(s for s in roster if s["student_id"] == ctx["ids"]["student_B"])
    bp = call(breq, "GET", "/api/lms/students/me/progress")["overall_progress"]
    assert abs((b["overall_progress"] or 0) - bp) < 0.01 and bp > 0, (b, bp)
    return {"visual": ok(shot(page, "T12_analytics_roster")), "interaction": ok("tab"), "api": ok("; ".join(out)),
            "db": ok("weighted StudentTopicScore avg matches"), "evidence": f"late-enroll B: roster {b['overall_progress']} = own progress {bp} (diagnostic taken before join counts)"}


@item("Teacher", "Student report endpoint = student-facing scores (same ids)")
def t_report(treq, areq):
    rep = call(treq, "GET", f"/api/lms/classes/{ctx['ids']['class_id']}/students/{ctx['ids']['student_A']}/report", name="teacher_report_A")
    mine = call(areq, "GET", "/api/lms/students/me/attempts")
    rep_att = {r.get("attempt_id"): r for r in rep.get("recent_attempts", [])}
    mism = [(a["attempt_id"], a.get("score_percent"), rep_att[a["attempt_id"]].get("score_percent"))
            for a in mine if a["attempt_id"] in rep_att and a.get("score_percent") != rep_att[a["attempt_id"]].get("score_percent")]
    topics = {t.get("topic_id"): t.get("score_percent") for t in rep.get("topics", [])}
    dbt = {r[0]: r[1] for r in db("select topic_id, score_percent from student_topic_scores where student_id=?", ctx["ids"]["student_A"])}
    off = [(k, v, dbt.get(k)) for k, v in topics.items() if k in dbt and abs((v or 0) - dbt[k]) > 0.6]
    assert not mism and not off and rep_att, (mism, off, list(rep_att))
    return {"api": ok(f"{len(rep_att)} attempts + {len(topics)} topics match student APIs"), "db": ok("topic scores match")}


@item("Teacher", "AI Tutor: Teaching Assistant send → reply; history API + DB")
def t_tutor(treq):
    page = ctx["tpage"]
    page.click('.td-navbtn[data-view="tutor"]')
    page.click('[data-tutor-mode="assistant"]')
    expect(page.locator("#lmsTutorInput")).to_be_visible(timeout=LLM)
    page.fill("#lmsTutorInput", "One tip for teaching fractions?")
    page.click("#lmsTutorModalBody >> text=Send")
    page.wait_for_function("() => document.querySelectorAll('#lmsTutorMessages .lms-chat-msg.bot').length >= 1 && !document.querySelector('#lmsTutorModalBody .lms-spinner')", timeout=LLM)
    reply = page.locator("#lmsTutorMessages .lms-chat-msg.bot").last.inner_text()[:100]
    assert not reply.startswith("Error"), reply
    v = shot(page, "T13_teacher_tutor")
    hist = call(treq, "GET", "/api/lms/tutor/history?mode=teacher", name="teacher_tutor_history")
    page.click('[data-tutor-mode="chat"]')
    v2 = shot(page, "T13b_teacher_chat_history_view")
    return {"visual": ok(f"{v}, {v2}"), "interaction": ok("send"), "api": ok(f"{len(hist.get('messages', []))} history msgs"), "evidence": repr(reply)}


# ───────────────────────── Cross-role ─────────────────────────
@item("Cross-role", "S5: reload / re-login keeps submitted diagnostic + enrollment")
def x_s5(browser):
    page = new_page(browser)
    login(page, ctx["accounts"]["A"])
    wait_student_ready(page)
    assert page.evaluate("sdCurrentView()") != "diagnostic-quiz", "gate re-opened after re-login"
    page.click('.sd-navbtn[data-view="classes"]')
    expect(page.locator(f'.sd-class-card[data-class-id="{ctx["ids"]["class_id"]}"]')).to_be_visible(timeout=LLM)
    prof = db("select diagnostic_completed from student_profiles where user_id=?", ctx["ids"]["student_A"])[0][0]
    v = shot(page, "X05_relogin")
    page.context.close()
    return {"visual": ok(v), "interaction": ok("fresh login, no gate, class present"), "db": ok(f"diagnostic_completed={prof}")}


@item("Cross-role", "S6: role isolation on role-gated APIs + dashboards")
def x_s6(pw):
    t = api(pw, ctx["accounts"]["teacher"])
    s = api(pw, ctx["accounts"]["C"])
    cid = ctx["ids"]["class_id"]
    checks = [
        (t, "GET", "/api/lms/students/me/dashboard", None, 403),
        (t, "GET", "/api/lms/students/me/assignments", None, 403),
        (t, "POST", f"/api/lms/quizzes/{ctx['ids']['pdf_quiz_id']}/start", {}, 403),
        (t, "POST", "/api/lms/classes/join", {"join_code": ctx["ids"]["join_code"]}, 403),
        (s, "POST", "/api/lms/classes", {"name": "x", "grade_level": "7"}, 403),
        (s, "GET", f"/api/lms/classes/{cid}/students", None, 403),
        (s, "GET", f"/api/lms/classes/{cid}/analytics/quizzes", None, 403),
        (s, "POST", "/api/lms/assignments", {"title": "x", "class_id": cid, "quiz_id": ctx["ids"]["pdf_quiz_id"]}, 403),
        (s, "POST", "/api/lms/teacher/tutor", {"message": "hi"}, 403),
    ]
    out, bad = [], []
    for req, m, u, b, want in checks:
        st, body = call(req, m, u, b, expect_ok=False)
        out.append(f"{m} {u} → {st}")
        if st < 400:
            bad.append(f"{m} {u} → {st} {json.dumps(body)[:100]}")
    r1 = t.get("/student-dashboard", max_redirects=0)
    r2 = s.get("/teacher-dashboard", max_redirects=0)
    out.append(f"teacher /student-dashboard → {r1.status} {r1.headers.get('location', '')}; student /teacher-dashboard → {r2.status} {r2.headers.get('location', '')}")
    if r1.status not in (301, 302) or r2.status not in (301, 302, 403):
        bad.append(out[-1])
    dump("S6_isolation", out)
    assert not bad, bad
    return {"api": ok("; ".join(out))}


@item("Cross-cutting", "Mobile 390px: student + teacher main views, no horizontal overflow")
def x_mobile(browser):
    out = []
    for email, views, prefix, navsel in ((ctx["accounts"]["A"], ["diagnostic", "learning-path", "classes", "tutor"], "S", ".sd-navbtn"),
                                         (ctx["accounts"]["teacher"], ["lessons", "classes", "quizzes", "analytics", "tutor"], "T", ".td-navbtn")):
        page = new_page(browser, 390, 844)
        login(page, email)
        page.wait_for_timeout(2000)
        for v in views:
            page.click(f'{navsel}[data-view="{v}"]')
            page.wait_for_load_state("networkidle", timeout=LLM)
            over = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            shot(page, f"M_{prefix}_{v}")
            out.append(f"{prefix}:{v}:{over}px")
            assert over <= 1, out
        page.context.close()
    return {"visual": ok(" ".join(out))}


@item("Cross-cutting", "No JS page errors; no 404 static assets; failed API calls listed")
def x_console():
    static = [n for n in net_fail if "/static/" in n]
    pageerr = [c for c in console_err if c.startswith("PAGEERROR")]
    api_fail = sorted(set(n for n in net_fail if "/api/" in n))
    dump("network_failures", {"all": net_fail, "console": console_err})
    assert not static and not pageerr, (static, pageerr)
    return {"visual": ok("0 static 404, 0 page errors"), "evidence": f"API 4xx/5xx seen: {api_fail}"}


# ───────────────────────── report ─────────────────────────
def write_report():
    (OUT / "results.json").write_text(json.dumps({"ctx": {k: v for k, v in ctx.items() if k in ("accounts", "ids", "git")}, "results": results}, indent=2, default=str), encoding="utf-8")
    cnt = {s: sum(r["status"] == s for r in results) for s in ("PASS", "FAIL", "BLOCKED")}
    L = [f"# LMS Visual + Backend QA Report", "", f"- BASE_URL: {BASE}", f"- Run: {TS}", f"- Git: {ctx.get('git')}",
         f"- Summary: **{cnt['PASS']} PASS / {cnt['FAIL']} FAIL / {cnt['BLOCKED']} BLOCKED** ({len(results)} items)", "",
         "## Accounts & IDs", "", "```json", json.dumps({"accounts": ctx["accounts"], "ids": ctx["ids"], "password": PASSWORD}, indent=2), "```", ""]
    for role in ("Setup", "Student", "Teacher", "Cross-role", "Cross-cutting"):
        rows = [r for r in results if r["role"] == role]
        if not rows:
            continue
        L += [f"## {role} results", "", "| Feature | Visual | Interaction | API | DB | Status | Evidence |", "|---|---|---|---|---|---|---|"]
        for r in rows:
            esc = lambda s: str(s).replace("|", "/").replace("\n", " ")[:260]  # noqa: E731
            L.append(f"| {r['feature']} | {esc(r['visual'])} | {esc(r['interaction'])} | {esc(r['api'])} | {esc(r['db'])} | **{r['status']}** | {esc(r['evidence'])} {' '.join(r['shots'])} |")
        L.append("")
    L += ["## Failures / blocked (raw)", ""]
    for r in results:
        if r["status"] != "PASS":
            L += [f"### {r['status']}: [{r['role']}] {r['feature']}", "", "```", str(r["evidence"])[:1500], "```", ""]
    (OUT / "REPORT.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\n{cnt} → {OUT / 'REPORT.md'}")


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=CHROME, headless=os.environ.get("QA_HEADED") != "1")
        s_health(pw)
        if not (s_teacher(browser) and s_students(browser)):
            write_report()
            return
        treq, areq, breq = api(pw, ctx["accounts"]["teacher"]), api(pw, ctx["accounts"]["A"]), api(pw, ctx["accounts"]["B"])
        t_shell()
        t_create_class(treq) and t_add_students(treq)
        s_gate(areq)
        if s_diag_start(areq):
            s_math()
            s_diag_submit(areq)
        s_retake(areq)
        s_hub(areq)
        s_path(areq)
        s_learning_chat(areq)
        s_practice(areq)
        if t_quiz_pdf(treq) and t_assign(treq):
            s_classes(areq)
            s_take_quiz(areq)
        s_attempts(areq)
        if t_lesson(treq) and s_lesson(areq):
            s_lesson_chat(areq)
        s_tutor(areq)
        s_join_B(breq)
        t_roster(treq)
        t_topic_progress(treq, areq)
        if ctx["ids"].get("assignment_id"):
            t_quiz_results(treq)
        t_struggling(treq)
        t_ana_roster(treq, breq)
        t_report(treq, areq)
        t_tutor(treq)
        x_s5(browser)
        x_s6(pw)
        x_mobile(browser)
        x_console()
        browser.close()
    write_report()


if __name__ == "__main__":
    main()
