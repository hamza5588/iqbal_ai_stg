"""Screenshot every legacy (pre-redesign) screen state inside the new teacher shell, and report any
element whose computed colours are still in the old green family. Output: teacher_ui_screenshots/legacy_*.png"""
import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:5055").rstrip("/")
PASSWORD = os.environ.get("E2E_PASSWORD", "E2eTeacher!2026")
EMAIL = "e2e.teacher@iqbalai.local"
CHROME = os.environ.get("CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "_qa_audit_tmp" / "teacher_ui_screenshots"
PDF = ROOT / "sample_pdfs" / "math_ix"
MAKE_LESSON = os.environ.get("MAKE_LESSON", "1") == "1"

# Finds visible elements whose text / background / border colour is green-ish (hue 90–170°, saturated).
GREEN_SCAN = """() => {
  const green = c => {
    const m = c && c.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)(?:,\\s*([\\d.]+))?/);
    if (!m || (m[4] !== undefined && +m[4] < 0.15)) return false;
    let [r, g, b] = [m[1], m[2], m[3]].map(v => v / 255);
    const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
    if (d < 0.12 || max < 0.2) return false;
    let h = max === g ? 60 * ((b - r) / d + 2) : max === r ? 60 * (((g - b) / d) % 6) : 60 * ((r - g) / d + 4);
    if (h < 0) h += 360;
    return h >= 80 && h <= 185;
  };
  const out = [];
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || +s.opacity === 0) continue;
    const hits = [];
    if (green(s.backgroundColor)) hits.push('bg ' + s.backgroundColor);
    if (s.backgroundImage.includes('gradient') && /rgb\\((\\d+), (\\d+), (\\d+)\\)/.test(s.backgroundImage) &&
        s.backgroundImage.match(/rgb\\([^)]*\\)/g).some(green)) hits.push('gradient');
    if (el.childNodes.length && [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()) && green(s.color)) hits.push('text ' + s.color);
    if (+s.borderTopWidth.replace('px','') > 0 && green(s.borderTopColor)) hits.push('border ' + s.borderTopColor);
    if (el.tagName === 'svg' || el.closest('svg')) { if (green(s.fill) || green(s.color)) hits.push('svg'); }
    if (hits.length) {
      const id = el.id ? '#' + el.id : '';
      const cls = typeof el.className === 'string' ? '.' + el.className.trim().split(/\\s+/).slice(0, 3).join('.') : '';
      out.push(el.tagName.toLowerCase() + id + cls + ' :: ' + hits.join(', ') + ' :: ' + (el.innerText || '').trim().slice(0, 40).replace(/\\n/g, ' '));
    }
  }
  return out;
}"""

report = {}


def cap(page, name, full=False):
    page.wait_for_timeout(500)
    page.screenshot(path=str(SHOTS / f"legacy_{name}.png"), full_page=full)
    report[name] = page.evaluate(GREEN_SCAN)
    print(f"{name}: {len(report[name])} green elements")


def main():
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=CHROME)
        page = b.new_page(viewport={"width": 1440, "height": 900})
        page.set_default_timeout(30000)
        page.goto(BASE + "/auth/login")
        page.fill('input[name="useremail"]', EMAIL)
        page.fill('input[name="password"]', PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")

        # Create Lesson card with a file chosen (layout bug 1)
        page.click("#view-lessons >> text=Create Lesson")
        pdf = sorted(PDF.glob("*.pdf"))[0] if PDF.exists() else sorted((ROOT / "sample_pdfs").glob("*.pdf"))[0]
        page.set_input_files("#pdfFileInput", str(pdf))
        page.fill("#lessonTitle", f"Legacy check {time.strftime('%H%M%S')}")
        page.select_option("#lessonSubject", "Math")
        page.select_option("#lessonGrade", "8")
        cap(page, "01_create_lesson_file_selected")

        if MAKE_LESSON:
            page.check('input[name="lessonOutputMode"][value="as_is"]')
            page.click("#nextStepButton")
            page.wait_for_selector("#createLessonModalContent")
            page.wait_for_timeout(2500)
            cap(page, "02_processing_modal")
            page.wait_for_selector("#createLessonModal", state="detached", timeout=300000)
            page.wait_for_timeout(1500)

        cap(page, "03_lessons_list")
        row = page.locator(".td-lesson-row").first
        # version dropdown
        row.locator(".version-dropdown-btn").click()
        page.wait_for_timeout(1200)
        cap(page, "04_version_dropdown")
        page.mouse.click(5, 5)
        # view modal
        row.locator("text=View").click()
        page.wait_for_selector("#viewLessonModal:not(.hidden)")
        page.wait_for_timeout(2500)
        cap(page, "05_view_lesson_modal")
        page.evaluate("closeViewLessonModal()")
        # edit modal
        row.locator("text=Edit").click()
        page.wait_for_timeout(1000)
        cap(page, "06_edit_lesson_modal")
        page.evaluate("document.querySelectorAll('[id^=editLessonModal-]').forEach(m => m.remove())")
        # FAQ modal
        row.locator("text=FAQ").click()
        page.wait_for_timeout(2500)
        cap(page, "07_faq_modal")
        page.evaluate("document.getElementById('faqModalOverlay') && document.getElementById('faqModalOverlay').remove()")
        # delete confirm (cancel it)
        page.once("dialog", lambda d: d.dismiss())

        # AI Tutor: open a conversation with messages
        page.click('.td-navbtn[data-view="tutor"]')
        page.click("#chatHistoryBtn")
        page.wait_for_timeout(800)
        page.screenshot(path=str(SHOTS / "legacy_08_chat_history.png"))
        report["08_chat_history"] = page.evaluate(GREEN_SCAN)
        print(f"08_chat_history: {len(report['08_chat_history'])} green elements")
        items = page.locator("#chatHistoryDropdown .chat-history-item")
        target = items.filter(has_text="Gen").first if items.filter(has_text="Gen").count() else items.first
        target.click()
        page.wait_for_function("() => document.querySelectorAll('#chatMessages .chat-message').length >= 2", timeout=20000)
        page.wait_for_timeout(1500)
        cap(page, "09_chat_conversation")
        page.evaluate("document.getElementById('chatArea').scrollTop = 0")
        cap(page, "10_chat_conversation_top")
        # long prompt (layout bug 2)
        page.fill("#messageInput", "Create a complete lesson draft using the uploaded document as the source of truth. Include:\n- Lesson title\n- Short summary\n- Learning objectives\n- Prerequisites/background\n- Key concepts\n- Step-by-step lesson sections")
        page.evaluate("autoResizeTextarea(document.getElementById('messageInput'))")
        cap(page, "11_long_prompt")
        page.fill("#messageInput", "")
        # chat context menu
        page.click("#chatHistoryBtn")
        page.locator("#chatHistoryDropdown .chat-history-item button").first.click()
        page.wait_for_timeout(600)
        page.screenshot(path=str(SHOTS / "legacy_12_chat_context_menu.png"))
        report["12_chat_context_menu"] = page.evaluate(GREEN_SCAN)
        page.mouse.click(5, 5)
        # save-lesson guidance + Set Prompt
        page.evaluate("showLessonSaveFlowGuidance('help')")
        page.wait_for_timeout(700)
        cap(page, "13_save_guide")
        page.evaluate("document.querySelectorAll('#lessonSaveGuideModal').forEach(m => m.remove())")
        page.click("#view-tutor >> text=Set Prompt")
        page.wait_for_timeout(1500)
        cap(page, "14_set_prompt")
        page.evaluate("closeRAGPromptModal()")
        # Teaching assistant
        page.click('[data-tutor-mode="assistant"]')
        page.wait_for_timeout(2500)
        cap(page, "15_teaching_assistant")
        b.close()
    (SHOTS / "legacy_green_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
