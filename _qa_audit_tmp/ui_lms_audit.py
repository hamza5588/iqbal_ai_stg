import asyncio
import json
import os
import re
from pathlib import Path

from playwright.async_api import async_playwright


BASE = os.environ.get("QA_BASE_URL", "https://dil.iqbalai.com").rstrip("/")
TEACHER_EMAIL = os.environ["QA_UI_TEACHER_EMAIL"]
STUDENT_EMAIL = os.environ["QA_UI_STUDENT_EMAIL"]
PASSWORD = os.environ["QA_UI_PASSWORD"]
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
OUT = Path(__file__).resolve().parent / "ui_audit"
OUT.mkdir(parents=True, exist_ok=True)


async def login(page, email):
    await page.goto(BASE + "/auth/login", wait_until="domcontentloaded", timeout=60000)
    await page.fill('input[name="useremail"], input#email', email)
    await page.fill('input[name="password"], input#password', PASSWORD)
    await page.click('button[type="submit"]')
    await page.wait_for_timeout(2500)


async def page_health(page):
    return await page.evaluate("""() => {
      const vw = document.documentElement.clientWidth;
      const overflow = document.documentElement.scrollWidth - vw;
      const visible = [...document.querySelectorAll('button, a, input, select, textarea')]
        .filter(el => {
          const r = el.getBoundingClientRect();
          const s = getComputedStyle(el);
          return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
        });
      const clippedText = visible.filter(el => {
        const t = (el.innerText || el.value || '').trim();
        return t && (el.scrollWidth > el.clientWidth + 3 || el.scrollHeight > el.clientHeight + 3);
      }).slice(0, 15).map(el => ({tag: el.tagName, id: el.id, text: (el.innerText || el.value || '').trim().slice(0, 80)}));
      return {viewport: vw, documentWidth: document.documentElement.scrollWidth, horizontalOverflow: overflow, clippedText};
    }""")


async def open_by_text(page, text):
    locator = page.get_by_text(text, exact=True).first
    await locator.click(timeout=7000)
    await page.wait_for_timeout(900)


async def goto_page(page, url):
    last_error = None
    for _ in range(3):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            return
        except Exception as exc:
            last_error = exc
            await page.wait_for_timeout(1200)
    raise last_error


async def audit_role(browser, role, email, viewport):
    context = await browser.new_context(viewport=viewport, ignore_https_errors=True)
    page = await context.new_page()
    console_errors = []
    failed_responses = []
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    page.on("response", lambda res: failed_responses.append({"status": res.status, "url": res.url}) if res.status >= 400 else None)
    await login(page, email)
    expected = "/teacher-dashboard" if role == "teacher" else "/student-dashboard"
    await goto_page(page, BASE + expected)
    await page.wait_for_timeout(3500)

    checks = {"role": role, "viewport": viewport, "url": page.url, "surfaces": {}}
    checks["dashboardLoaded"] = expected in page.url
    checks["layout"] = await page_health(page)
    shot_name = f"{role}_{viewport['width']}x{viewport['height']}_dashboard.png"
    await page.screenshot(path=str(OUT / shot_name), full_page=False)

    surfaces = ({
        "Manage Classes": 'button[onclick="openLmsClassHub()"]',
        "Create Quiz": 'button[onclick="openLmsQuizHub()"]',
        "Assign Quiz": 'button[onclick="openLmsAssignModal()"]',
        "Analytics": 'button[onclick="openLmsTeacherAnalytics()"]',
    } if role == "teacher" else {
        "Join Class": 'button[onclick="openLmsJoinClassModal()"]',
        "My Quizzes": 'button[onclick="openLmsStudentHub()"]',
        "Diagnostic": 'button[onclick="openLmsDiagnostic()"]',
        "Practice": 'button[onclick="openLmsPracticePanel()"]',
    })
    for label, selector in surfaces.items():
        try:
            await goto_page(page, BASE + expected)
            await page.wait_for_timeout(2200)
            await page.locator(selector).first.click(timeout=7000)
            await page.wait_for_timeout(900)
            checks["surfaces"][label] = {"opened": True, "layout": await page_health(page)}
            await page.screenshot(
                path=str(OUT / f"{role}_{viewport['width']}_{label.lower().replace(' ', '_')}.png"),
                full_page=False,
            )
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(400)
        except Exception as exc:
            checks["surfaces"][label] = {"opened": False, "error": str(exc)[:240]}

    if role == "teacher":
        try:
            await goto_page(page, BASE + expected)
            await page.wait_for_timeout(2200)
            await page.locator("#lessonsTabBtn").click(timeout=7000)
            await page.wait_for_timeout(1800)
            cards = await page.locator(".mll-lesson-card").count()
            checks["myLessons"] = {
                "opened": True,
                "cards": cards,
                "publishControls": await page.locator("a").filter(has_text=re.compile(r"^(Publish|Unpublish)$")).count(),
                "layout": await page_health(page),
            }
        except Exception as exc:
            checks["myLessons"] = {"opened": False, "error": str(exc)[:240]}
    else:
        try:
            await goto_page(page, BASE + expected)
            await page.wait_for_timeout(2200)
            await page.locator("#lessonsTabBtn").click(timeout=7000)
            await page.wait_for_timeout(1800)
            checks["myLessons"] = {
                "opened": True,
                "cards": await page.locator(".mll-lesson-card").count(),
                "layout": await page_health(page),
            }
        except Exception as exc:
            checks["myLessons"] = {"opened": False, "error": str(exc)[:240]}

    checks["consoleErrors"] = console_errors[-30:]
    checks["failedResponses"] = [r for r in failed_responses if not r["url"].endswith("favicon.ico")][-30:]
    await context.close()
    return checks


async def main():
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path=CHROME)
        for viewport in ({"width": 1440, "height": 900}, {"width": 390, "height": 844}):
            results.append(await audit_role(browser, "teacher", TEACHER_EMAIL, viewport))
            results.append(await audit_role(browser, "student", STUDENT_EMAIL, viewport))
        await browser.close()
    (OUT / "ui_audit.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
