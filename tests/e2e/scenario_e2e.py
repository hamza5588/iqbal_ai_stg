"""Browser E2E for Scenario-Based Assessments (Playwright, real server, real LLM).

Prereqs: server on SBA_BASE_URL (default http://127.0.0.1:5077) backed by a throwaway DB
seeded with tests/e2e/seed_scenario_e2e.py. Writes screenshots + e2e_results.json to
SBA_E2E_OUT (default ./e2e_out).

Scenarios: A teacher setup, B generation/approval/publish, C student answers, E security,
F document replacement. (D grading accuracy: tests/e2e/scenario_eval_live.py.)
"""
import json
import os
import re
import sys
import time

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("SBA_BASE_URL", "http://127.0.0.1:5077")
OUT = os.environ.get("SBA_E2E_OUT", os.path.join(os.getcwd(), "e2e_out"))
FIX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "scenario")
PASSWORD = "Sba-E2E-pass1"
os.makedirs(OUT, exist_ok=True)

KNOWN_SCENARIO = (
    "FreshFarm Dairy supplies yoghurt to 40 supermarkets. Checkout data shows shoppers buy a steady 1,000 tubs a week. "
    "Each supermarket forecasts from its own past orders, adds safety stock, and orders in large fortnightly batches to fill "
    "trucks. FreshFarm also runs a 15% end-of-quarter discount. FreshFarm's weekly orders now swing between 300 and 2,600 "
    "tubs, it pays for overtime and throws away expired stock."
)
KNOWN_QUESTION = ("Explain why FreshFarm's orders fluctuate so much more than consumer demand and recommend how FreshFarm "
                  "and the supermarkets should change their practices. Justify each recommendation.")

ANSWERS = {
    "correct": (
        "This is the bullwhip effect: order variability grows as you move upstream even though end-customer demand is stable. "
        "Three causes are visible. First, demand-signal processing: each supermarket forecasts from orders rather than checkout "
        "sales and adds safety stock, so the distortion compounds. Second, order batching: large fortnightly orders to fill trucks "
        "create lumpy demand. Third, price fluctuation: the end-of-quarter discount makes stores forward-buy and then stop. "
        "Recommendations: share point-of-sale data with FreshFarm (or use CPFR/VMI) so both forecast from real demand; order "
        "smaller amounts more often, consolidating mixed loads to keep transport efficient; and replace the quarterly discount "
        "with everyday low pricing. Each remedy targets its cause, which reduces inventory, overtime and waste."
    ),
    "partial": (
        "The orders swing because of the bullwhip effect. The supermarkets should share their sales information with FreshFarm "
        "so it can plan production better."
    ),
    "incorrect_generic": (
        "Coordination simply means every company should order more inventory so it never runs out. Sharing information with "
        "partners is unnecessary and risky, and price promotions always make demand smoother, so the firm should run more of them."
    ),
    "irrelevant": (
        "Football is the most popular sport in the world. A team needs a good goalkeeper, strong defenders and fast strikers. "
        "Training sessions usually include passing drills, sprint work and tactical meetings before every match. " * 3
    ),
}


def log(results, name, ok, **detail):
    results.append({"step": name, "ok": bool(ok), **detail})
    print(("PASS " if ok else "FAIL ") + name, json.dumps(detail)[:400])


def login(page, email):
    page.goto(f"{BASE}/auth/login")
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.click("#submit-btn")
    page.wait_for_url(re.compile(r".*(dashboard|admin).*"), timeout=30000)


def api(page, method, path, body=None):
    fn = getattr(page.request, method)
    kw = {"data": body} if body is not None else {}
    r = fn(f"{BASE}/api/lms/scenario{path}", **kw)
    try:
        payload = r.json()
    except Exception:  # noqa: BLE001
        payload = None
    return r.status, payload


def teacher_list(page):
    page.goto(f"{BASE}/teacher-dashboard#assessments")
    page.wait_for_function("() => !!window.tdScenario")
    page.evaluate("() => { tdShowView('assessments'); return tdScenario.load(); }")


def wait_for(fn, timeout=300, every=3):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return None


def main():
    results = []
    ctx_info = {}
    with sync_playwright() as p:
        exe = os.environ.get("SBA_CHROMIUM_EXE") or None  # e.g. an already-installed ms-playwright chrome.exe
        browser = p.chromium.launch(headless=True, executable_path=exe)
        teacher = browser.new_context(viewport={"width": 1366, "height": 900}).new_page()
        teacher.on("dialog", lambda d: d.accept())

        # ── A: teacher setup ────────────────────────────────────────────────
        login(teacher, "sba.teacher@e2e.test")
        teacher.goto(f"{BASE}/teacher-dashboard#assessments")
        teacher.click("#tdSbaNewBtn")
        teacher.fill("#tdSbaNewTitle", "E2E Supply Chain Case Study")
        teacher.select_option("#tdSbaNewClass", label="SBA E2E Class")
        teacher.click("#tdSbaCreateBtn")
        expect(teacher.locator("#tdSbaTitle")).to_have_text("E2E Supply Chain Case Study", timeout=20000)
        teacher.fill("#sbaTopic", "Bullwhip effect, coordination and information sharing")
        teacher.fill("#sbaCount", "4")
        teacher.fill("#sbaObjectives", "Explain the bullwhip effect and its causes\nRecommend coordination and information-sharing remedies")
        teacher.click("#tdSbaSave")
        teacher.wait_for_timeout(1200)
        status, listing = api(teacher, "get", "/assessments")
        aid = listing["data"][0]["id"]
        ctx_info["assessment_id"] = aid
        log(results, "A1 teacher created assessment via UI", status == 200 and aid, assessment_id=aid)

        for doc_type, fname in [("learning", "learning_supply_chain.pdf"), ("sample_questions", "sample_questions.pdf"),
                                ("reference_answers", "reference_answers.pdf")]:
            teacher.set_input_files(f"#sbaFile_{doc_type}", os.path.join(FIX, fname))
            teacher.click(f"[data-upload='{doc_type}']")
            teacher.wait_for_timeout(1500)

        def docs_ready():
            s, d = api(teacher, "get", f"/assessments/{aid}")
            docs = [x for x in d["data"]["documents"] if x["is_active"]]
            if len(docs) == 3 and all(x["status"] in ("ready", "failed") for x in docs):
                return d["data"]
            return None

        detail = wait_for(docs_ready, timeout=600)
        docs = {x["doc_type"]: x for x in (detail or {}).get("documents", [])}
        ok = detail is not None and all(docs.get(t, {}).get("status") == "ready" for t in ("learning", "sample_questions", "reference_answers"))
        log(results, "A2 three documents uploaded separately and processed", ok,
            docs={k: {"status": v["status"], "chunks": v["chunk_count"], "pages": v["page_count"], "embedded": v["embedded"]} for k, v in docs.items()},
            sample_questions=len((detail or {}).get("sample_questions", [])))
        log(results, "A3 documents linked to the same assessment, typed, versioned",
            ok and {v["version"] for v in docs.values()} == {1} and len(detail["sample_questions"]) >= 3)
        teacher_list(teacher)
        teacher.click(f"[data-sba-open='{aid}']")
        teacher.wait_for_selector("[data-doc-type='learning'] .sba-b-ready", timeout=20000)
        teacher.screenshot(path=os.path.join(OUT, "A_teacher_setup.png"), full_page=True)

        # ── B: generation, approval, publish ───────────────────────────────
        student_b = browser.new_context().new_page()
        login(student_b, "sba.b@e2e.test")
        s_pre, _ = api(student_b, "get", f"/student/assessments/{aid}")
        log(results, "B0 unpublished assessment hidden from enrolled student", s_pre == 404, status=s_pre)

        teacher.click("[data-sba-tab='questions']")
        teacher.click("#tdSbaGenerate")
        t0 = time.time()

        def gen_done():
            s, d = api(teacher, "get", f"/assessments/{aid}")
            dd = d["data"]
            return dd if dd["generation_status"] != "running" and dd["items"] else (dd if dd["generation_status"] == "failed" else None)

        detail = wait_for(gen_done, timeout=600, every=4)
        gen_secs = round(time.time() - t0, 1)
        items = (detail or {}).get("items", [])
        log(results, "B1 scenario questions generated as drafts", len(items) >= 3 and all(q["status"] == "draft" for q in items),
            count=len(items), seconds=gen_secs, generation_error=(detail or {}).get("generation_error"))
        grounded = bool(items) and all(q["source_refs"] and q["evaluation_criteria"] and len(q["scenario"]) > 120 for q in items)
        log(results, "B2 grounded structured output (sources, criteria, concepts, scenario)", grounded,
            sample=[{"q": q["question_text"][:160], "sources": [r["page_start"] for r in q["source_refs"]], "concepts": q["concepts"]} for q in items])
        from difflib import SequenceMatcher

        samples = [s["text"] for s in detail["sample_questions"]]
        max_sim = max((SequenceMatcher(None, q["scenario"] + q["question_text"], s).ratio() for q in items for s in samples), default=0)
        pair_sim = max((SequenceMatcher(None, a["question_text"], b["question_text"]).ratio()
                        for i, a in enumerate(items) for b in items[i + 1:]), default=0)
        log(results, "B3 not copied from samples, no duplicates", bool(items) and max_sim < 0.6 and pair_sim < 0.8,
            max_similarity_to_sample=round(max_sim, 3), max_pairwise_similarity=round(pair_sim, 3))
        teacher.click(f"[data-sba-tab='questions']")
        teacher.wait_for_selector("[data-q-card]", timeout=20000)
        teacher.screenshot(path=os.path.join(OUT, "B_generated_drafts.png"), full_page=True)

        # Teacher edits Q1 into a known scenario (UI edit flow), approves all, publishes.
        q1 = items[0]["id"]
        teacher.click(f"[data-q='{q1}'][data-q-act='edit']")
        teacher.fill(f"#sbaEdS{q1}", KNOWN_SCENARIO)
        teacher.fill(f"#sbaEdQ{q1}", KNOWN_QUESTION)
        teacher.click(f"[data-q='{q1}'][data-q-act='save']")
        teacher.wait_for_selector(f"[data-q-card='{q1}'] .sba-q-text", timeout=20000)
        for q in items:
            btn = teacher.locator(f"[data-q='{q['id']}'][data-q-act='approve']")
            if btn.count():
                btn.click()
                teacher.wait_for_selector(f"[data-q-card='{q['id']}'][data-q-status='approved']", timeout=20000)
        teacher.click("#tdSbaPublish")
        teacher.wait_for_selector("#tdSbaUnpublish", timeout=20000)
        s, d = api(teacher, "get", f"/assessments/{aid}")
        approved = [q for q in d["data"]["items"] if q["status"] == "approved"]
        edited = next(q for q in approved if q["id"] == q1)
        log(results, "B4 teacher edited, approved and published", d["data"]["status"] == "published" and len(approved) == len(items)
            and edited["edited_by_teacher"] and edited["question_text"] == KNOWN_QUESTION, approved=len(approved))
        ctx_info["question_ids"] = [q["id"] for q in approved]

        # ── C: student answers ─────────────────────────────────────────────
        sa = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        sa.on("dialog", lambda dl: dl.accept())
        login(sa, "sba.a@e2e.test")
        sa.goto(f"{BASE}/student-dashboard#scenario")
        sa.wait_for_selector(f"[data-sba-open='{aid}']", timeout=30000)
        sa.click(f"[data-sba-open='{aid}']")
        sa.wait_for_selector("#sdSbaTitle", timeout=20000)
        qids = ctx_info["question_ids"]
        plan = [(qids[0], ANSWERS["correct"], "correct"), (qids[1], ANSWERS["incorrect_generic"], "incorrect"),
                (qids[2], ANSWERS["irrelevant"], "irrelevant")]
        # draft persistence: type, reload, text still there
        sa.fill(f"#sdSbaAns{qids[0]}", "draft text to persist")
        sa.wait_for_timeout(2500)
        sa.reload()
        sa.wait_for_selector(f"[data-sba-open='{aid}']", timeout=30000)
        sa.click(f"[data-sba-open='{aid}']")
        sa.wait_for_selector(f"#sdSbaAns{qids[0]}", timeout=20000)
        log(results, "C1 draft saved and restored after reload", sa.input_value(f"#sdSbaAns{qids[0]}") == "draft text to persist")
        for qid, text, _label in plan:
            sa.fill(f"#sdSbaAns{qid}", text)
            sa.click(f"[data-submit='{qid}']")
            sa.wait_for_selector(f"[data-sba-item='{qid}'] [data-sba-state]", timeout=30000)

        def results_ready():
            s, d = api(sa, "get", f"/student/assessments/{aid}")
            its = {i["id"]: i for i in d["data"]["items"]}
            subs = [its[q]["submission"] for q, _, _ in plan]
            if all(x and x["status"] in ("evaluated", "failed") for x in subs):
                return its
            return None

        its = wait_for(results_ready, timeout=600, every=4)
        scores = {}
        for qid, _t, label in plan:
            sub = (its or {}).get(qid, {}).get("submission") or {}
            r = sub.get("result") or {}
            scores[label] = {"status": sub.get("status"), "state": r.get("state"), "score": r.get("score"), "max": r.get("max_score")}
        log(results, "C2 submissions persisted and evaluated", its is not None and all(v["status"] == "evaluated" for v in scores.values()), scores=scores)
        c_score = scores["correct"]["score"] or 0
        ok = (scores["correct"]["state"] == "released" and c_score >= 7
              and all((scores[k]["score"] is None or scores[k]["score"] <= 3) for k in ("incorrect", "irrelevant")))
        log(results, "C3 correct >= 7/10, incorrect and irrelevant <= 3/10 (or held for review)", ok, scores=scores)
        # Student B: partial answer on the known question
        student_b.on("dialog", lambda dl: dl.accept())
        student_b.goto(f"{BASE}/student-dashboard#scenario")
        student_b.wait_for_selector(f"[data-sba-open='{aid}']", timeout=30000)
        student_b.click(f"[data-sba-open='{aid}']")
        student_b.fill(f"#sdSbaAns{qids[0]}", ANSWERS["partial"])
        student_b.click(f"[data-submit='{qids[0]}']")

        def b_ready():
            s, d = api(student_b, "get", f"/student/assessments/{aid}")
            sub = d["data"]["items"][0]["submission"]
            return sub if sub and sub["status"] in ("evaluated", "failed") else None

        bsub = wait_for(b_ready, timeout=300, every=4) or {}
        br = bsub.get("result") or {}
        log(results, "C4 partial answer gets partial credit (between incorrect and correct)",
            br.get("state") in ("released", "under_review") and (br.get("score") is None or 1 <= br["score"] < c_score),
            partial=br.get("score"), state=br.get("state"))
        sa.evaluate("() => sdScenario.load()")
        sa.wait_for_selector(f"[data-sba-open='{aid}']", timeout=30000)
        sa.click(f"[data-sba-open='{aid}']")
        sa.wait_for_selector("[data-sba-state='released']", timeout=30000)
        sa.screenshot(path=os.path.join(OUT, "C_student_results.png"), full_page=True)
        released = sa.locator("[data-sba-state='released']").first
        txt = released.inner_text()
        log(results, "C5 student sees criterion-level feedback and improvement concepts",
            "Conceptual accuracy" in txt and ("How to improve" in txt or "Concepts to work on" in txt or "Feedback" in txt))

        # ── E: security ────────────────────────────────────────────────────
        sub_list = api(teacher, "get", f"/assessments/{aid}/submissions")[1]["data"]
        a_sub = next(x for x in sub_list if x["student_name"] == "Student A" and x["question_id"] == qids[0])
        checks = {
            "studentA_teacher_submission_detail": api(sa, "get", f"/submissions/{a_sub['id']}")[0] == 403,
            "studentA_override_score": api(sa, "post", f"/submissions/{a_sub['id']}/review", {"teacher_score": 10})[0] == 403,
            "studentA_teacher_assessment_detail(ref docs)": api(sa, "get", f"/assessments/{aid}")[0] == 403,
            "studentB_cannot_see_A_answer": "bullwhip effect: order variability" not in json.dumps(api(student_b, "get", f"/student/assessments/{aid}")[1]),
        }
        sc = browser.new_context().new_page()
        login(sc, "sba.c@e2e.test")
        checks["other_class_student_404"] = api(sc, "get", f"/student/assessments/{aid}")[0] == 404
        checks["other_class_student_list_empty"] = api(sc, "get", "/student/assessments")[1]["data"] == []
        t2 = browser.new_context().new_page()
        login(t2, "sba.teacher2@e2e.test")
        checks["other_teacher_assessment_404"] = api(t2, "get", f"/assessments/{aid}")[0] == 404
        checks["other_teacher_edit_404"] = api(t2, "put", f"/questions/{qids[0]}", {"status": "rejected"})[0] == 404
        checks["other_teacher_submission_404"] = api(t2, "get", f"/submissions/{a_sub['id']}")[0] == 404
        student_view = json.dumps(api(sa, "get", f"/student/assessments/{aid}")[1]).lower()
        checks["no_hidden_material_in_student_payload"] = all(k not in student_view for k in ("evaluation_criteria", "reference_answers", "source_refs", "excerpt"))
        log(results, "E1 authorization and data isolation", all(checks.values()), checks=checks)

        # ── F: replace learning document ───────────────────────────────────
        teacher_list(teacher)
        teacher.click(f"[data-sba-open='{aid}']")
        learning_doc = next(x for x in api(teacher, "get", f"/assessments/{aid}")[1]["data"]["documents"] if x["doc_type"] == "learning" and x["is_active"])
        with teacher.expect_file_chooser() as fc:
            teacher.click(f"[data-doc-replace='{learning_doc['id']}']")
        fc.value.set_files(os.path.join(FIX, "learning_supply_chain_v2.pdf"))

        def v2_ready():
            d = api(teacher, "get", f"/assessments/{aid}")[1]["data"]
            act = [x for x in d["documents"] if x["doc_type"] == "learning" and x["is_active"]]
            return d if act and act[0]["version"] == 2 and act[0]["status"] == "ready" else None

        d = wait_for(v2_ready, timeout=300) or {}
        learn = [x for x in d.get("documents", []) if x["doc_type"] == "learning"]
        log(results, "F1 replacement processed: v2 active, v1 kept as history",
            sorted((x["version"], x["is_active"]) for x in learn) == [(1, False), (2, True)],
            versions=[(x["version"], x["is_active"], x["status"]) for x in learn])
        a_detail = api(teacher, "get", f"/submissions/{a_sub['id']}")[1]["data"]
        old_versions = a_detail["evaluations"][0]["doc_versions"]
        log(results, "F2 historical evaluation keeps the document version it used",
            any(v["doc_type"] == "learning" and v["version"] == 1 for v in old_versions), doc_versions=old_versions)
        teacher.screenshot(path=os.path.join(OUT, "F_replaced_document.png"), full_page=True)

        # Teacher review UI: open A's correct answer, override and confirm student sees it
        teacher.click("[data-sba-tab='submissions']")
        teacher.click(f"[data-sub='{a_sub['id']}']")
        teacher.wait_for_selector("#tdSbaAiScore", timeout=20000)
        teacher.screenshot(path=os.path.join(OUT, "E_teacher_review.png"), full_page=True)
        teacher.fill("#tdSbaOverride", "9.5")
        teacher.fill("#tdSbaTFeedback", "Strong answer - well linked causes and remedies.")
        teacher.click("#tdSbaSaveReview")
        teacher.wait_for_timeout(1500)
        r = api(sa, "get", f"/student/assessments/{aid}")[1]["data"]
        r1 = next(i for i in r["items"] if i["id"] == a_sub["question_id"])["submission"]["result"]
        log(results, "F3 teacher override visible to the student", r1.get("score") == 9.5 and r1.get("teacher_adjusted"), result_score=r1.get("score"))

        browser.close()
    summary = {"base": BASE, "context": ctx_info, "passed": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results), "steps": results}
    with open(os.path.join(OUT, "e2e_results.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    print(f"\nE2E: {summary['passed']} passed, {summary['failed']} failed")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
