"""Admin Console redesign check: screenshots + functional smoke of every admin screen.

    python _qa_audit_tmp/redesign_consistency/admin_console_check.py [BASE_URL]

Logs in as the throwaway local admin (seeded by capture_pages.py), then for desktop (1440) and
mobile (390): every sidebar section, the Load Testing tabs, LLM Telemetry, the three modals, a
deep link (/admin/#users), role filter from the overview, and the mobile drawer. Records console
errors, page errors and horizontal overflow. Output: _qa_audit_tmp/redesign_consistency/admin_console/
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5057").rstrip("/")
OUT = ROOT / "_qa_audit_tmp" / "redesign_consistency" / "admin_console"
OUT.mkdir(parents=True, exist_ok=True)
EMAIL, PW = "e2e.stu.admredesign@iqbalai.local", "E2eTeacher!2026"
SECTIONS = ["dashboard", "users", "lessons", "documents", "diagnostic", "settings", "prompts", "coupons", "theme"]

results = {"checks": [], "console_errors": [], "overflow": []}


def check(name, ok, detail=""):
    results["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


def overflow(page, label):
    w = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    if w > 1:
        results["overflow"].append({"page": label, "px": w})
    return w


def run(b, vp, tag):
    ctx = b.new_context(viewport=vp)
    page = ctx.new_page()
    page.on("console", lambda m: m.type == "error" and results["console_errors"].append(f"[{tag}] {m.text[:200]}"))
    page.on("pageerror", lambda e: results["console_errors"].append(f"[{tag}] PAGEERROR {str(e)[:200]}"))
    page.goto(BASE + "/auth/login")
    page.fill('input[name="useremail"]', EMAIL)
    page.fill('input[name="password"]', PW)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    page.goto(BASE + "/admin/", wait_until="networkidle")
    page.wait_for_timeout(600)
    mobile = vp["width"] < 800

    for i, sec in enumerate(SECTIONS):
        if mobile:
            page.click(".ad-menu-btn")
            page.wait_for_timeout(250)
            check(f"{tag} drawer opens for {sec}", page.locator("#adSidebar.open").count() == 1)
        page.click(f'.ad-side-link[data-section="{sec}"]')
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(700)
        vis = page.locator(f"#{sec}-section").is_visible()
        active = page.locator(f'.ad-side-link.active[data-section="{sec}"]').count() == 1
        check(f"{tag} section {sec} visible + active", vis and active)
        check(f"{tag} hash #{sec}", page.evaluate("location.hash") == "#" + sec)
        overflow(page, f"{tag}/{sec}")
        page.screenshot(path=str(OUT / f"{tag}_{i:02d}_{sec}.png"), full_page=True)

    # data actually rendered
    page.goto(BASE + "/admin/#users", wait_until="networkidle")
    page.wait_for_timeout(900)
    rows = page.locator("#users-tbody tr").count()
    check(f"{tag} deep link /admin/#users renders users", page.locator("#users-section").is_visible() and rows > 1, f"{rows} rows")
    check(f"{tag} role pills rendered", page.locator("#users-tbody .ad-pill").count() > 0)
    page.fill("#user-search", "admredesign")
    page.wait_for_timeout(900)
    check(f"{tag} user search filters", page.locator("#users-tbody tr").count() >= 1)

    # overview -> teachers filter
    page.goto(BASE + "/admin/", wait_until="networkidle")
    page.wait_for_timeout(500)
    page.click("text=View teachers")
    page.wait_for_timeout(1000)
    roles = page.locator("#users-tbody .ad-pill.green").count()
    check(f"{tag} overview 'View teachers' filters users", page.eval_on_selector("#user-role-filter", "e => e.value") == "teacher" and roles > 0, f"{roles} teacher rows")
    mix = page.evaluate("document.getElementById('adMixStudentsPct').textContent")
    check(f"{tag} community mix filled", "%" in mix and mix != "0%", mix)

    # modals
    for trigger, modal, shot in (("showCreateUserModal()", "#create-user-modal", "modal_create_user"),
                                  ("showCreateCouponModal()", "#create-coupon-modal", "modal_create_coupon")):
        page.evaluate(trigger)
        page.wait_for_timeout(300)
        check(f"{tag} {shot} opens", page.locator(modal).is_visible())
        page.screenshot(path=str(OUT / f"{tag}_{shot}.png"))
        page.locator(f"{modal} .ad-btn-outline").click()
        page.wait_for_timeout(200)
        check(f"{tag} {shot} closes", not page.locator(modal).is_visible())
    page.goto(BASE + "/admin/#users", wait_until="networkidle")
    page.wait_for_timeout(900)
    page.locator("#users-tbody .ad-icon-btn").first.click()
    page.wait_for_timeout(700)
    check(f"{tag} edit user modal opens prefilled", page.locator("#edit-user-modal").is_visible() and page.input_value("#edit-username") != "")
    page.screenshot(path=str(OUT / f"{tag}_modal_edit_user.png"))
    page.locator("#edit-user-modal .ad-btn-outline").click()

    # toast
    page.evaluate("showNotification('Platform theme updated for all users', 'success')")
    page.wait_for_timeout(200)
    check(f"{tag} toast shows", page.locator("#notification-toast.ad-toast.success").is_visible())
    page.screenshot(path=str(OUT / f"{tag}_toast.png"))

    # theme picker
    page.goto(BASE + "/admin/#theme", wait_until="networkidle")
    page.wait_for_timeout(900)
    n = page.locator("#adminThemePresets .admin-theme-preset").count()
    check(f"{tag} theme presets rendered", n >= 10, f"{n} presets")
    page.locator('#adminThemePresets .admin-theme-preset[data-preset="iqbal_blue"]').click()
    check(f"{tag} theme preset select", page.input_value("#adminThemePresetInput") == "iqbal_blue"
          and page.locator('.admin-theme-preset.is-active[data-preset="iqbal_blue"]').count() == 1)

    # account menu
    page.click("#adProfile")
    page.wait_for_timeout(200)
    check(f"{tag} avatar menu opens", page.locator("#adAvatarMenu").is_visible())
    page.screenshot(path=str(OUT / f"{tag}_avatar_menu.png"))
    page.keyboard.press("Escape")

    # load testing
    page.goto(BASE + "/admin/load-testing", wait_until="networkidle")
    page.wait_for_timeout(800)
    check(f"{tag} load testing sidebar active", page.locator('.ad-side-link.active[data-page="load_testing"]').count() == 1)
    for tab in ("tests", "assets", "results", "settings"):
        page.click(f"""a[onclick="showTab('{tab}'); return false;"]""")
        page.wait_for_timeout(700)
        check(f"{tag} load testing tab {tab}", page.locator(f"#{tab}-tab").is_visible()
              and page.locator(f"""a.active[onclick="showTab('{tab}'); return false;"]""").count() == 1)
        overflow(page, f"{tag}/load_testing_{tab}")
        page.screenshot(path=str(OUT / f"{tag}_lt_{tab}.png"), full_page=True)
    if mobile:
        page.click(".ad-menu-btn")
        page.wait_for_timeout(250)
    page.click('.ad-side-link[data-section="users"]')
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(900)
    check(f"{tag} load testing -> sidebar Users deep link", page.url.endswith("/admin/#users") and page.locator("#users-section").is_visible())

    # telemetry
    page.goto(BASE + "/admin/llm-telemetry", wait_until="networkidle")
    page.wait_for_timeout(1200)
    check(f"{tag} telemetry sidebar active", page.locator('.ad-side-link.active[data-page="llm_telemetry"]').count() == 1)
    overflow(page, f"{tag}/llm_telemetry")
    page.screenshot(path=str(OUT / f"{tag}_llm_telemetry.png"), full_page=True)
    ctx.close()


with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    run(b, {"width": 1440, "height": 900}, "desktop")
    run(b, {"width": 390, "height": 844}, "mobile")
    b.close()

(OUT / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
fails = [c for c in results["checks"] if not c["ok"]]
print(f"\n{len(results['checks']) - len(fails)}/{len(results['checks'])} checks passed")
print("overflow:", results["overflow"])
print("console errors:", len(results["console_errors"]))
for e in results["console_errors"][:25]:
    print("  ", e)
