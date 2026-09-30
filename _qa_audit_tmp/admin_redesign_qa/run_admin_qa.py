"""Admin Console redesign QA (Phase 5): screenshots + functional smoke of every admin destination.

    venv/Scripts/python.exe _qa_audit_tmp/admin_redesign_qa/run_admin_qa.py [BASE_URL]

Logs in as the throwaway local admin (e2e.stu.admredesign@iqbalai.local). Desktop 1440 + mobile 390.
Checks: every sidebar destination opens with the right active state + hash, data loads, no /static 4xx,
no horizontal overflow, no page errors. Safe writes only: creates + deletes a throwaway coupon and user.
Never saves LLM settings, RAG prompts or the platform theme (read/preview only).
Also captures the teacher + student dashboards for a side-by-side family check.
Output: _qa_audit_tmp/admin_redesign_qa/<timestamp>/{screenshots/, results.json}
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5057").rstrip("/")
RUN = ROOT / "_qa_audit_tmp" / "admin_redesign_qa" / datetime.now().strftime("%Y%m%d_%H%M%S")
SHOTS = RUN / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)
PW = "E2eTeacher!2026"
ADMIN = "e2e.stu.admredesign@iqbalai.local"
SECTIONS = [("dashboard", "Dashboard"), ("users", "Users"), ("lessons", "Lessons"), ("documents", "Documents"),
            ("diagnostic", "Diagnostic"), ("settings", "LLM Settings"), ("prompts", "RAG Prompts"),
            ("coupons", "Coupons"), ("theme", "Color Theme")]
R = {"base": BASE, "checks": [], "static_errors": [], "page_errors": [], "api_errors": [], "overflow": []}
STAMP = str(int(time.time()))[-6:]


def check(name, ok, detail=""):
    R["checks"].append({"name": name, "ok": bool(ok), "detail": str(detail)})
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


def watch(page, tag):
    def on_resp(r):
        if r.status >= 400:
            (R["static_errors"] if "/static/" in r.url else R["api_errors"]).append(f"[{tag}] {r.status} {r.url.replace(BASE, '')}")
    page.on("response", on_resp)
    page.on("pageerror", lambda e: R["page_errors"].append(f"[{tag}] {str(e)[:200]}"))
    page.on("dialog", lambda d: d.accept())


def login(page, email):
    page.goto(BASE + "/auth/login")
    page.fill('input[name="useremail"]', email)
    page.fill('input[name="password"]', PW)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")


def overflow(page, label):
    w = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    if w > 1:
        R["overflow"].append({"page": label, "px": w})


def nav(page, sec, mobile):
    if mobile:
        page.click(".ad-menu-btn")
        page.wait_for_timeout(250)
    page.click(f'.ad-side-link[data-section="{sec}"]')
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(800)


def admin_run(b, vp, tag):
    mobile = vp["width"] < 800
    ctx = b.new_context(viewport=vp)
    page = ctx.new_page()
    watch(page, tag)
    login(page, ADMIN)
    page.goto(BASE + "/admin/", wait_until="networkidle")
    page.wait_for_timeout(700)

    for i, (sec, label) in enumerate(SECTIONS):
        nav(page, sec, mobile)
        ok = page.locator(f"#{sec}-section").is_visible() and page.locator(f'.ad-side-link.active[data-section="{sec}"]').count() == 1
        check(f"{tag}: {label} opens (active + #{sec})", ok and page.evaluate("location.hash") == "#" + sec)
        overflow(page, f"{tag}/{sec}")
        page.screenshot(path=str(SHOTS / f"{tag}_{i:02d}_{sec}.png"), full_page=True)

    # data per section
    page.goto(BASE + "/admin/#users", wait_until="networkidle"); page.wait_for_timeout(900)
    check(f"{tag}: users load via deep link", page.locator("#users-tbody .ad-pill").count() > 0)
    page.click('#adUserRoleTabs .ad-stab[data-role="teacher"]'); page.wait_for_timeout(900)
    pills = page.locator("#users-tbody td:nth-child(4) .ad-pill")
    roles = {pills.nth(k).inner_text().strip().lower() for k in range(pills.count())}
    check(f"{tag}: Teachers sub-tab filters list", roles == {"teacher"} and page.eval_on_selector("#user-role-filter", "e=>e.value") == "teacher", roles)
    page.click('#adUserRoleTabs .ad-stab[data-role="all"]'); page.wait_for_timeout(700)
    page.fill("#user-search", "zz_no_such_user_zz"); page.wait_for_timeout(900)
    check(f"{tag}: user empty state", page.locator("#users-tbody .ad-empty").is_visible())
    page.screenshot(path=str(SHOTS / f"{tag}_users_empty_state.png"))
    page.fill("#user-search", ""); page.wait_for_timeout(600)

    for sec, sel in (("lessons", "#lessons-tbody tr"), ("documents", "#documents-tbody tr"), ("coupons", "#coupons-tbody tr")):
        page.goto(BASE + f"/admin/#{sec}", wait_until="networkidle"); page.wait_for_timeout(900)
        txt = page.locator(sel).first.inner_text()
        check(f"{tag}: {sec} table rendered (not stuck loading)", "Loading" not in txt, txt[:40].replace("\n", " "))

    page.goto(BASE + "/admin/#diagnostic", wait_until="networkidle"); page.wait_for_timeout(1400)
    check(f"{tag}: diagnostic library rendered", page.locator("#adminDiagList .d-row, #adminDiagList .d-empty").count() > 0)
    check(f"{tag}: diagnostic 4 workflow steps", page.locator("#diagnostic-section .ad-step").count() == 4)
    page.click("#adminDiagTabAppend"); page.wait_for_timeout(400)
    check(f"{tag}: diagnostic 'Add study PDFs' tab", page.locator("#adminDiagAddTargetsSection").is_visible())
    page.screenshot(path=str(SHOTS / f"{tag}_diagnostic_append_tab.png"), full_page=True)
    page.click("#adminDiagTabPublish"); page.wait_for_timeout(300)
    for f in ("archived", "all", "published"):
        page.click(f'#adminDiagFilterTabs .d-filter[data-filter="{f}"]'); page.wait_for_timeout(300)
    check(f"{tag}: diagnostic library filters", page.locator('#adminDiagFilterTabs .d-filter.is-active[data-filter="published"]').count() == 1)

    page.goto(BASE + "/admin/#settings", wait_until="networkidle"); page.wait_for_timeout(1200)
    check(f"{tag}: LLM settings loaded (provider selected)", page.locator('input[name="active-provider"]:checked').count() == 1)
    page.goto(BASE + "/admin/#prompts", wait_until="networkidle"); page.wait_for_timeout(1500)
    check(f"{tag}: RAG prompt loaded", len(page.input_value("#rag-prompt-with-pdf")) > 20)
    page.goto(BASE + "/admin/#theme", wait_until="networkidle"); page.wait_for_timeout(1000)
    check(f"{tag}: theme presets", page.locator("#adminThemePresets .admin-theme-preset").count() >= 10)
    page.locator('#adminThemePresets .admin-theme-preset[data-preset="iqbal_blue"]').click()
    check(f"{tag}: theme preview select (not saved)", page.input_value("#adminThemePresetInput") == "iqbal_blue")

    if not mobile:
        # safe writes: throwaway coupon + user, then delete both
        page.goto(BASE + "/admin/#coupons", wait_until="networkidle"); page.wait_for_timeout(800)
        code = f"QAADM{STAMP}"
        page.click("#coupons-section .ad-head-actions .ad-btn-primary"); page.wait_for_timeout(300)
        page.fill("#create-coupon-code", code)
        page.click("#create-coupon-modal button[type=submit]"); page.wait_for_timeout(1500)
        row = page.locator("#coupons-tbody tr", has_text=code)
        check(f"{tag}: create coupon", row.count() == 1)
        page.screenshot(path=str(SHOTS / f"{tag}_coupon_created_toast.png"))
        if row.count():
            row.locator(".ad-icon-btn.danger").click(); page.wait_for_timeout(1500)
            check(f"{tag}: delete coupon", page.locator("#coupons-tbody tr", has_text=code).count() == 0)

        page.goto(BASE + "/admin/#users", wait_until="networkidle"); page.wait_for_timeout(900)
        uname = f"qa_admin_ui_{STAMP}"
        page.click("#users-section .ad-head-actions .ad-btn-primary"); page.wait_for_timeout(300)
        page.fill("#create-username", uname)
        page.fill("#create-email", f"{uname}@test.local")
        page.fill("#create-password", "QaAdmin!2026x")
        page.select_option("#create-role", "student")
        page.screenshot(path=str(SHOTS / f"{tag}_modal_create_user_filled.png"))
        page.click("#create-user-modal button[type=submit]"); page.wait_for_timeout(1500)
        page.fill("#user-search", uname); page.wait_for_timeout(1000)
        row = page.locator("#users-tbody tr", has_text=uname)
        check(f"{tag}: create user", row.count() == 1)
        if row.count():
            row.locator(".ad-icon-btn").first.click(); page.wait_for_timeout(800)
            check(f"{tag}: edit modal prefilled", page.input_value("#edit-username") == uname)
            page.screenshot(path=str(SHOTS / f"{tag}_modal_edit_user.png"))
            page.locator("#edit-user-modal .ad-btn-outline").click()
            row.locator(".ad-icon-btn.danger").click(); page.wait_for_timeout(1500)
            page.fill("#user-search", uname); page.wait_for_timeout(1000)
            check(f"{tag}: delete user", page.locator("#users-tbody tr", has_text=uname).count() == 0)

    # account menu
    page.goto(BASE + "/admin/", wait_until="networkidle"); page.wait_for_timeout(500)
    page.click("#adProfile"); page.wait_for_timeout(200)
    check(f"{tag}: avatar menu (Back to App, Logout)", page.locator("#adAvatarMenu a[href='/auth/logout']").is_visible())
    page.screenshot(path=str(SHOTS / f"{tag}_avatar_menu.png"))
    page.keyboard.press("Escape")

    # monitoring pages on the shared shell
    page.goto(BASE + "/admin/load-testing", wait_until="networkidle"); page.wait_for_timeout(900)
    check(f"{tag}: Load Testing shell + active", page.locator('.ad-side-link.active[data-page="load_testing"]').count() == 1 and page.locator(".ad-page-head h2", has_text="Load Testing").count() == 1)
    for tab in ("tests", "assets", "results", "settings"):
        page.click(f"""a[onclick="showTab('{tab}'); return false;"]"""); page.wait_for_timeout(800)
        check(f"{tag}: Load Testing tab {tab}", page.locator(f"#{tab}-tab").is_visible())
        overflow(page, f"{tag}/load_testing/{tab}")
        page.screenshot(path=str(SHOTS / f"{tag}_load_testing_{tab}.png"), full_page=True)
    if mobile:
        page.click(".ad-menu-btn"); page.wait_for_timeout(250)
    page.click('.ad-side-link[data-section="diagnostic"]'); page.wait_for_load_state("networkidle"); page.wait_for_timeout(900)
    check(f"{tag}: Load Testing -> /admin/#diagnostic deep link", page.locator("#diagnostic-section").is_visible())

    page.goto(BASE + "/admin/llm-telemetry", wait_until="networkidle"); page.wait_for_timeout(1300)
    check(f"{tag}: LLM Telemetry shell + active", page.locator('.ad-side-link.active[data-page="llm_telemetry"]').count() == 1)
    overflow(page, f"{tag}/llm_telemetry")
    page.screenshot(path=str(SHOTS / f"{tag}_llm_telemetry.png"), full_page=True)
    ctx.close()


def family_refs(b):
    for email, name in (("e2e.teacher@iqbalai.local", "ref_teacher"), ("e2e.stu.smokeB@iqbalai.local", "ref_student")):
        ctx = b.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        try:
            login(page, email)
            page.wait_for_timeout(2500)
            page.screenshot(path=str(SHOTS / f"{name}.png"))
        except Exception as e:  # reference only
            print("ref capture skipped:", name, e)
        ctx.close()


with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    admin_run(b, {"width": 1440, "height": 900}, "desktop")
    admin_run(b, {"width": 390, "height": 844}, "mobile")
    family_refs(b)
    b.close()

(RUN / "results.json").write_text(json.dumps(R, indent=2), encoding="utf-8")
fails = [c for c in R["checks"] if not c["ok"]]
print(f"\n{len(R['checks']) - len(fails)}/{len(R['checks'])} checks passed -> {RUN}")
for k in ("static_errors", "api_errors", "page_errors", "overflow"):
    print(k, len(R[k]), R[k][:8])
