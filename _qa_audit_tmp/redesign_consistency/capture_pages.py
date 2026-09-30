"""Screenshot every standalone (non-dashboard) screen for the redesign-consistency pass.

    python _qa_audit_tmp/redesign_consistency/capture_pages.py <label> [BASE_URL]

Auth screens are rendered through Flask (render_template with sample context) and served on the
real server origin via a Playwright route, so /static assets, fonts and the platform theme load as in
production - this covers mid-flow pages (email sent, verify OTP, reset password) that need email.
Admin pages are captured by logging in as a throwaway local admin.
Output: _qa_audit_tmp/redesign_consistency/<label>/*.png
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("SKIP_EXTRA_STARTUP", "true")

from playwright.sync_api import sync_playwright  # noqa: E402

label = sys.argv[1] if len(sys.argv) > 1 else "before"
BASE = (sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:5057").rstrip("/")
OUT = ROOT / "_qa_audit_tmp" / "redesign_consistency" / label
OUT.mkdir(parents=True, exist_ok=True)

PAGES = {
    "auth_01_login": ("login/login.html", {}),
    "auth_01b_login_error": ("login/login.html", {"error": "Invalid credentials"}),
    "auth_02_register_email": ("register_email/register_email.html", {}),
    "auth_03_email_sent": ("email_sent/email_sent.html", {"email": "student@example.com"}),
    "auth_04_register": ("register/register.html", {"email": "student@example.com"}),
    "auth_05_forgot_password": ("forgot_password/forgot_password.html", {}),
    "auth_06_verify_otp": ("verify_otp/verify_otp.html", {"email": "student@example.com"}),
    "auth_07_reset_password": ("reset_password/reset_password.html", {"email": "student@example.com"}),
}


def render_all():
    from flask import render_template

    from app import create_app

    app = create_app()
    html = {}
    with app.test_request_context("/"):
        for key, (tpl, context) in PAGES.items():
            html[key] = render_template(tpl, **context)
    return html


LIVE = [  # (account, password, [(name, path)])
    ("e2e.stu.admredesign@iqbalai.local", "E2eTeacher!2026",
     [("admin_01_dashboard", "/admin/"), ("admin_02_load_testing", "/admin/load-testing"), ("admin_03_llm_telemetry", "/admin/llm-telemetry")]),
    ("e2e.stu.smokeB@iqbalai.local", "E2eTeacher!2026", [("other_01_settings", "/settings")]),
]


def capture_live(browser):
    import subprocess

    subprocess.run([sys.executable, str(ROOT / "_qa_audit_tmp/student_ui_e2e/seed_student.py"), "admredesign", "8", "admin"],
                   cwd=ROOT, capture_output=True)
    for email, pwd, pages in LIVE:
        for width, suffix in ((1440, ""), (390, "_mobile")):
            page = browser.new_page(viewport={"width": width, "height": 900})
            page.goto(BASE + "/auth/login")
            page.fill('input[name="useremail"]', email)
            page.fill('input[name="password"]', pwd)
            page.click('button[type="submit"]')
            page.wait_for_load_state("networkidle")
            for name, path in pages:
                page.goto(BASE + path, wait_until="networkidle")
                page.wait_for_timeout(800)
                page.screenshot(path=str(OUT / f"{name}{suffix}.png"), full_page=True)
                over = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                if over > 1:
                    print(f"OVERFLOW {name}{suffix}: {over}px")
            page.close()


def main():
    pages = render_all()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        for width, suffix in ((1440, ""), (390, "_mobile")):
            page = browser.new_page(viewport={"width": width, "height": 900})
            for key, content in pages.items():
                url = f"{BASE}/__preview/{key}"
                def _serve(route, _request=None, body=content):
                    route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)

                page.route(url, _serve)
                page.goto(url, wait_until="networkidle")
                page.wait_for_timeout(400)
                page.screenshot(path=str(OUT / f"{key}{suffix}.png"), full_page=True)
                over = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                if over > 1:
                    print(f"OVERFLOW {key}{suffix}: {over}px")
            page.close()
        capture_live(browser)
        browser.close()
    print("saved to", OUT)


if __name__ == "__main__":
    main()
