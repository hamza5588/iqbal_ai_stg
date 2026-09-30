"""Targeted re-checks for the final run's suspected test-side failures (same accounts/ids).

    python recheck_final.py <run_dir>
1. Teacher Topic Progress detail: wait for the loading spinner to clear, then compare rows ↔ by-topic API ↔ DB.
2. Student guided practice: open from the Learning Path view.
3. Retake/back link: fresh student C takes the diagnostic in-session, then reopens it → Back link must be visible.
Appends results to <run_dir>/rechecks.json and screenshots to <run_dir>/screenshots/.
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

run = Path(sys.argv[1])
ROOT = Path(__file__).resolve().parents[3]
BASE = "http://127.0.0.1:5001"
PW = "QaPass!2026x"
data = json.loads((run / "results.json").read_text(encoding="utf-8"))
acc, ids = data["ctx"]["accounts"], data["ctx"]["ids"]
out = {}


def db(sql, *a):
    c = sqlite3.connect(f"file:{ROOT / 'instance' / 'iqbalai_local.db'}?mode=ro", uri=True)
    try:
        return c.execute(sql, a).fetchall()
    finally:
        c.close()


def login(b, email):
    p = b.new_page(viewport={"width": 1440, "height": 900})
    p.set_default_timeout(300_000)
    p.goto(BASE + "/auth/login")
    p.fill('input[name="useremail"]', email)
    p.fill('input[name="password"]', PW)
    p.click('button[type="submit"]')
    p.wait_for_url(re.compile(r"/(student|teacher)-dashboard"))
    p.wait_for_load_state("networkidle")
    return p


with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe")

    # 1. teacher topic progress
    t = login(b, acc["teacher"])
    t.click('.td-navbtn[data-view="analytics"]')
    t.select_option("#tdAnaClass", str(ids["class_id"]))
    t.wait_for_load_state("networkidle")
    sid = ids["student_A"]
    t.locator(f'#tdAnaProgressRows tr[data-sid="{sid}"]').click()
    det = t.locator(f"#tdProgDetail-{sid}")
    expect(det.locator(".td-spinner")).to_have_count(0, timeout=300_000)
    ui = [r.split("\n")[0].strip() for r in det.locator(".td-topic-row").all_inner_texts()]
    treq = pw.request.new_context(base_url=BASE)
    treq.post("/auth/login", form={"useremail": acc["teacher"], "password": PW}, max_redirects=0)
    api = treq.get(f"/api/lms/classes/{ids['class_id']}/students/{sid}/progress/by-topic").json()["data"]["topics"]
    api_names = [x["topic_name"] for x in api]
    dbn = [r[0] for r in db("select t.name from student_topic_scores s join topics t on t.id=s.topic_id where s.student_id=? order by t.name", sid)]
    t.screenshot(path=str(run / "screenshots" / "R1_teacher_topic_progress_recheck.png"), full_page=True)
    out["topic_progress"] = {"ui": ui, "api": api_names, "db": dbn, "ui_eq_api": sorted(ui) == sorted(api_names)}

    # 2. guided practice from the Learning Path view
    a = login(b, acc["A"])
    a.wait_for_function("() => !document.getElementById('loading-overlay')")
    a.click('.sd-navbtn[data-view="learning-path"]')
    btn = a.locator("#sdPathTopics >> text=/Guided practice|Practice again/").first
    tid = int(re.search(r"\((\d+)\)", btn.get_attribute("onclick")).group(1))
    btn.click()
    a.wait_for_function("() => { const x = document.getElementById('lmsPracticeModalBody'); return x && !x.querySelector('.lms-spinner') && x.innerText.trim(); }")
    txt = a.locator("#lmsPracticeModalBody").inner_text()[:160]
    a.screenshot(path=str(run / "screenshots" / "R2_guided_practice_recheck.png"))
    out["guided_practice"] = {"topic_id": tid, "ui": txt, "db_active_questions_for_topic": db("select count(*) from questions where topic_id=? and is_active=1", tid)[0][0]}

    # 3. back link after completing the diagnostic in the same session (student C)
    c = login(b, acc["C"])
    c.wait_for_function("() => !document.getElementById('loading-overlay')")
    expect(c.locator("#lmsDiagBody")).to_contain_text("Before you begin")
    c.click("#lmsDiagBody >> text=Start Diagnostic")
    c.locator("#lmsDiagBody .sd-q-option").first.click()
    c.click("#lmsDiagBody >> text=Submit Diagnostic")
    c.locator("#inAppConfirmOk").click()
    expect(c.locator("#lmsDiagBody .sd-result-body")).to_be_visible(timeout=300_000)
    vis_after_submit = c.locator("#lmsDiagCloseBtn").is_visible()
    c.click("#lmsDiagBody >> text=Continue")
    expect(c.locator("#view-diagnostic")).to_have_class(re.compile(r"\bactive\b"))
    c.evaluate("openLmsDiagnostic()")
    expect(c.locator("#lmsDiagBody")).to_contain_text("already completed", timeout=300_000)
    state = c.evaluate("() => ({needs: window._lmsNeedsDiagnostic, mandatory: window._lmsDiagnosticMandatory, allow: window._lmsDiagnosticAllowClose, btn: getComputedStyle(document.getElementById('lmsDiagCloseBtn')).display})")
    c.screenshot(path=str(run / "screenshots" / "R3_reopen_after_submit_recheck.png"))
    out["backlink"] = {"visible_after_submit": vis_after_submit, "visible_on_reopen": c.locator("#lmsDiagCloseBtn").is_visible(), "js_state": state}
    b.close()

(run / "rechecks.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
print(json.dumps(out, indent=2))
