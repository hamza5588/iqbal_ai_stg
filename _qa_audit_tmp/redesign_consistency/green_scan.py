"""Find any remaining old-UI green on every screen (computed colours, not source grep).

    python _qa_audit_tmp/redesign_consistency/green_scan.py [BASE_URL]

Walks: auth pages (rendered via Flask), every admin sidebar section, every student + teacher dashboard view.
Reports visible elements whose text / background / border colour is green (hue 85-165deg, saturation >= 25%,
not near-white/near-black), with the element's text so semantic uses (success pills, Google logo) can be
told apart from leftover branding. Screens of each admin section are saved too.
Output: _qa_audit_tmp/redesign_consistency/green_scan.json + admin_sections/*.png
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("SKIP_EXTRA_STARTUP", "true")
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5057").rstrip("/")
OUT = ROOT / "_qa_audit_tmp" / "redesign_consistency"
(OUT / "admin_sections").mkdir(parents=True, exist_ok=True)
PW = "E2eTeacher!2026"

SCAN_JS = r"""
() => {
  const toRgb = s => { const m = (s || '').match(/rgba?\(([^)]+)\)/); if (!m) return null;
    const p = m[1].split(',').map(x => parseFloat(x)); if (p.length > 3 && p[3] === 0) return null; return p.slice(0, 3); };
  const isGreen = rgb => { if (!rgb) return false; const [r, g, b] = rgb.map(v => v / 255);
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2, d = mx - mn;
    if (d < 0.08 || l > 0.94 || l < 0.08) return false;
    const s = d / (1 - Math.abs(2 * l - 1)); let h;
    if (mx === r) h = 60 * (((g - b) / d) % 6); else if (mx === g) h = 60 * ((b - r) / d + 2); else h = 60 * ((r - g) / d + 4);
    if (h < 0) h += 360; return h >= 85 && h <= 165 && s >= 0.25; };
  const out = [];
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect(); if (!r.width || !r.height) continue;
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || cs.display === 'none' || parseFloat(cs.opacity) === 0) continue;
    const hits = [];
    if (isGreen(toRgb(cs.color)) && (el.innerText || '').trim() && [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) hits.push('color ' + cs.color);
    if (isGreen(toRgb(cs.backgroundColor))) hits.push('bg ' + cs.backgroundColor);
    if (/gradient/.test(cs.backgroundImage) && /rgb/.test(cs.backgroundImage)) {
      const cols = cs.backgroundImage.match(/rgba?\([^)]+\)/g) || []; if (cols.some(c => isGreen(toRgb(c)))) hits.push('gradient');
    }
    if (parseFloat(cs.borderLeftWidth) >= 2 && isGreen(toRgb(cs.borderLeftColor))) hits.push('border ' + cs.borderLeftColor);
    if (el.tagName === 'path' || el.tagName === 'circle' || el.tagName === 'rect') { if (isGreen(toRgb(cs.fill))) hits.push('svg-fill ' + cs.fill); }
    if (hits.length) out.push({ tag: el.tagName.toLowerCase(), id: el.id || '', cls: (el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className || '').toString().slice(0, 70),
      text: (el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 50), hits });
  }
  return out;
}
"""


def login(page, email):
    page.goto(BASE + "/auth/login")
    page.fill('input[name="useremail"]', email)
    page.fill('input[name="password"]', PW)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")


def main():
    from flask import render_template
    from app import create_app

    app = create_app()
    auth = {}
    with app.test_request_context("/"):
        for key, tpl, ctx in [("login", "login/login.html", {}), ("register_email", "register_email/register_email.html", {}),
                              ("email_sent", "email_sent/email_sent.html", {"email": "a@b.c"}), ("register", "register/register.html", {"email": "a@b.c"}),
                              ("forgot_password", "forgot_password/forgot_password.html", {}), ("verify_otp", "verify_otp/verify_otp.html", {"email": "a@b.c"}),
                              ("reset_password", "reset_password/reset_password.html", {"email": "a@b.c"})]:
            auth[key] = render_template(tpl, **ctx)

    report = {}
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        page = b.new_page(viewport={"width": 1440, "height": 900})
        for key, html in auth.items():
            url = f"{BASE}/__scan/{key}"

            def _serve(route, _req=None, body=html):
                route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)

            page.route(url, _serve)
            page.goto(url, wait_until="networkidle")
            report[f"auth/{key}"] = page.evaluate(SCAN_JS)
        page.close()

        # admin: every sidebar section
        page = b.new_page(viewport={"width": 1440, "height": 900})
        login(page, "e2e.stu.admredesign@iqbalai.local")
        page.goto(BASE + "/admin/", wait_until="networkidle")
        items = page.locator("aside .ad-side-link")
        for i in range(items.count()):
            it = items.nth(i)
            label = it.inner_text().strip().replace("\n", " ")
            href = it.get_attribute("href") or ""
            if href.startswith("/admin/") and href.rstrip("/") not in ("/admin", ""):
                page.goto(BASE + href, wait_until="networkidle")
            else:
                it.click()
                page.wait_for_timeout(900)
            report[f"admin/{label}"] = page.evaluate(SCAN_JS)
            page.screenshot(path=str(OUT / "admin_sections" / f"{i:02d}_{label.replace(' ', '_')}.png"), full_page=True)
            if not page.url.rstrip("/").endswith("/admin"):
                page.goto(BASE + "/admin/", wait_until="networkidle")
                items = page.locator("aside .ad-side-link")
        page.close()

        # dashboards
        for email, navsel, views, prefix in (
            ("e2e.stu.smokeB@iqbalai.local", ".sd-navbtn", ["diagnostic", "learning-path", "classes", "tutor"], "student"),
            ("e2e.teacher@iqbalai.local", ".td-navbtn", ["lessons", "classes", "quizzes", "analytics", "tutor"], "teacher"),
        ):
            page = b.new_page(viewport={"width": 1440, "height": 900})
            login(page, email)
            page.wait_for_timeout(2000)
            for v in views:
                page.click(f'{navsel}[data-view="{v}"]')
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(700)
                report[f"{prefix}/{v}"] = page.evaluate(SCAN_JS)
            page.close()
        b.close()

    (OUT / "green_scan.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    for k, hits in report.items():
        print(f"{len(hits):4d}  {k}")
        for h in hits[:12]:
            print(f"        {h['tag']}#{h['id']}.{h['cls'][:40]}  '{h['text'][:40]}'  {h['hits']}")


if __name__ == "__main__":
    main()
