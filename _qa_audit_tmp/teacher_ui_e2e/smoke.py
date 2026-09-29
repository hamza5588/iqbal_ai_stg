"""Quick smoke: login, visit every teacher view, dump console errors / failed requests, screenshot."""
import asyncio
import os
from pathlib import Path

from playwright.async_api import async_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:5055").rstrip("/")
EMAIL = os.environ.get("E2E_TEACHER_EMAIL", "e2e.teacher@iqbalai.local")
PASSWORD = os.environ.get("E2E_PASSWORD", "E2eTeacher!2026")
OUT = Path(__file__).resolve().parents[1] / "teacher_ui_screenshots"
OUT.mkdir(parents=True, exist_ok=True)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=os.environ.get("CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe"))
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        errors, failed = [], []
        page.on("console", lambda m: errors.append(f"[{m.type}] {m.text}") if m.type in ("error",) else None)
        page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))
        page.on("response", lambda r: failed.append(f"{r.status} {r.url}") if r.status >= 400 else None)
        await page.goto(BASE + "/auth/login", wait_until="domcontentloaded")
        await page.fill('input[name="useremail"], input#email', EMAIL)
        await page.fill('input[name="password"], input#password', PASSWORD)
        await page.click('button[type="submit"]')
        await page.wait_for_load_state("networkidle")
        print("after login:", page.url)
        for view in ["lessons", "classes", "quizzes", "analytics", "tutor"]:
            await page.click(f'.td-navbtn[data-view="{view}"]')
            await page.wait_for_timeout(1500)
            await page.screenshot(path=str(OUT / f"smoke_{view}.png"), full_page=True)
            active = await page.eval_on_selector(".td-view.active", "e => e.id")
            print(view, "->", active)
        print("ERRORS:", *errors, sep="\n  ")
        print("FAILED:", *failed, sep="\n  ")
        await browser.close()


asyncio.run(main())
