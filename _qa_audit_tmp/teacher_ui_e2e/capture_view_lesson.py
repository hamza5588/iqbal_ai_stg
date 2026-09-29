"""Screenshot every state of the redesigned View Lesson modal and check its wiring still works."""
import os
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:5055").rstrip("/")
PASSWORD = os.environ.get("E2E_PASSWORD", "E2eTeacher!2026")
EMAIL = "e2e.teacher@iqbalai.local"
CHROME = os.environ.get("CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
SHOTS = Path(__file__).resolve().parents[1] / "teacher_ui_screenshots"
CONTENT = """# Lesson Draft: Quadratic Equations

## Short Summary
This lesson introduces students to quadratic equations, covering their definition, methods of solving them, and their applications in real-world problems.

## Objectives
After studying this unit, students will be able to:
- Define quadratic equations.
- Solve quadratic equations by factorization and completing the square.
- Derive the quadratic formula $x = \\frac{-b \\pm \\sqrt{b^2-4ac}}{2a}$ and use it.

## Key Concepts
1. Standard form $ax^2 + bx + c = 0$, $a \\neq 0$.
2. The discriminant $b^2 - 4ac$ decides the nature of the roots.

## Practice
- Solve $x^2 - 5x + 6 = 0$.
- Solve $x^2 + 4x - 32 = 0$ by completing the square.
"""
errors = []


def main():
    with sync_playwright() as pw:
        req = pw.request.new_context(base_url=BASE)
        req.post("/auth/login", form={"useremail": EMAIL, "password": PASSWORD})
        title = f"Quadratic Equations {time.strftime('%H%M%S')}"
        r = req.post("/api/lessons/create", data={"title": title, "content": CONTENT, "focus_area": "Math", "grade_level": "8",
                                                  "summary": "Intro to quadratic equations"})
        assert r.ok, r.text()[:300]
        b = pw.chromium.launch(executable_path=CHROME)
        page = b.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(BASE + "/auth/login")
        page.fill('input[name="useremail"]', EMAIL)
        page.fill('input[name="password"]', PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")
        row = page.locator(".td-lesson-row", has_text=title)
        row.locator("text=View").click()
        modal = page.locator("#viewLessonModal")
        expect(modal).to_be_visible()
        expect(page.locator("#viewLessonTitle")).to_have_text(title)
        page.wait_for_timeout(1500)
        page.screenshot(path=str(SHOTS / "vl_01_default.png"))
        # Improve tab + quick chip + save draft
        page.click('[data-vl-tab="improve"]')
        page.wait_for_timeout(600)
        page.click(".td-vl-chips >> text=Add examples")
        assert "worked examples" in page.input_value("#viewLessonPrompt")
        page.locator("#viewLessonDraft").fill("Edited draft: add two worked examples.")
        style = page.evaluate("""() => { const d = document.getElementById('viewLessonDraft');
          const w = document.createTreeWalker(d, NodeFilter.SHOW_TEXT); const n = w.nextNode();
          const s = getComputedStyle(n ? n.parentElement : d); return s.fontStyle + ' ' + s.color; }""")
        assert style.startswith("normal"), f"typed draft text still styled as placeholder: {style}"
        page.screenshot(path=str(SHOTS / "vl_02_improve.png"))
        page.click("#tdVlImprove >> text=Save Draft")
        page.wait_for_timeout(1500)
        # Summaries
        page.click('[data-vl-tab="summaries"]')
        page.click("#viewLessonSummaryTimeline .vl-tl-item >> nth=1")
        expect(page.locator("#viewLessonLessonPanel")).to_be_visible()
        page.wait_for_timeout(1500)
        page.screenshot(path=str(SHOTS / "vl_03_summaries.png"))
        page.click("#viewLessonSummaryTimeline .vl-tl-item >> nth=0")
        expect(page.locator("#viewLessonConvPanel")).to_be_visible()
        expect(page.locator("#viewLessonLessonPanel")).to_be_hidden()
        # Version dropdown
        page.click("#versionDropdownBtn")
        expect(page.locator("#viewLessonVersions")).to_be_visible()
        page.screenshot(path=str(SHOTS / "vl_04_versions.png"))
        page.click("#versionDropdownBtn")
        # Maximize + full screen reader
        # The modal opens maximized (existing behaviour); the button toggles window size.
        is_max = lambda: "is-maximized" in page.evaluate("document.querySelector('#viewLessonModal .view-lesson-modal-panel').className")
        before = is_max()
        page.click("#expandMainLessonModalBtn")
        assert is_max() != before, "expand toggle did not change window size"
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "vl_05_window_toggled.png"))
        page.click("#expandMainLessonModalBtn")
        assert is_max() == before
        page.click("#expandLessonPreviewBtn")
        page.wait_for_timeout(800)
        page.screenshot(path=str(SHOTS / "vl_06_fullscreen_reader.png"))
        page.evaluate("closeExpandedLessonPreview()")
        page.evaluate("closeViewLessonModal()")
        expect(modal).to_be_hidden()
        # PDF-as-lesson variant (if one exists)
        pdf_row = page.locator(".td-lesson-row", has_text="Legacy check").first
        if pdf_row.count():
            pdf_row.locator("text=View").click()
            page.wait_for_timeout(2500)
            page.screenshot(path=str(SHOTS / "vl_07_pdf_lesson.png"))
            assert page.locator("#tdVlImprove").is_hidden()
            page.evaluate("closeViewLessonModal()")
        # Mobile
        m = b.new_page(viewport={"width": 390, "height": 844})
        m.goto(BASE + "/auth/login")
        m.fill('input[name="useremail"]', EMAIL)
        m.fill('input[name="password"]', PASSWORD)
        m.click('button[type="submit"]')
        m.wait_for_load_state("networkidle")
        m.locator(".td-lesson-row", has_text=title).locator("text=View").click()
        m.wait_for_timeout(1500)
        m.screenshot(path=str(SHOTS / "vl_08_mobile.png"))
        over = m.evaluate("document.querySelector('#viewLessonModal .view-lesson-modal-panel').scrollWidth - document.querySelector('#viewLessonModal .view-lesson-modal-panel').clientWidth")
        print("mobile horizontal overflow:", over)
        b.close()
    print("errors:", errors)


main()
