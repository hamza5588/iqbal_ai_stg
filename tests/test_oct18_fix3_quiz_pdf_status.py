"""Fix 3 (18 Oct): quiz-from-PDF errors render as a persistent red alert.

Static checks plus a small Node `vm` harness (no jsdom; a hand-rolled DOM stub) that
loads static/teacher/js/teacher-lms-hubs.js and drives _setLmsQuizStatus,
submitLmsPdfQuiz and pollLmsQuizStatus.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HUBS = ROOT / "static" / "teacher" / "js" / "teacher-lms-hubs.js"
QUIZZES = ROOT / "static" / "teacher" / "js" / "teacher-quizzes.js"
CREATE_FORM = ROOT / "templates" / "teacher" / "quizzes" / "create_form.html"
DASH_CSS = ROOT / "static" / "teacher" / "css" / "teacher-dashboard.css"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _read(p):
    return p.read_text(encoding="utf-8")


def _fn_src(src, name, next_marker):
    start = src.index(name)
    end = src.index(next_marker, start + len(name))
    return src[start:end]


# ---------------------------------------------------------------- static checks

def test_set_lms_quiz_status_helper_defined_and_exported():
    src = _read(HUBS)
    assert "function _setLmsQuizStatus(msg, isError)" in src
    assert "window._setLmsQuizStatus = _setLmsQuizStatus" in src
    helper = _fn_src(src, "function _setLmsQuizStatus(", "window._setLmsQuizStatus")
    assert "'alert'" in helper and "'is-error'" in helper and "td-status-dismiss" in helper
    assert "setTimeout" not in helper, "errors must not auto-hide"


@pytest.mark.parametrize(
    "fn, nxt",
    [
        ("async function submitLmsPdfQuiz(", "function startLmsPoll("),
        ("async function pollLmsQuizStatus(", "async function loadLmsQuizPreview("),
        ("async function publishLmsQuiz(", "\n    }\n"),
    ],
)
def test_no_raw_textcontent_writes_left_for_lms_quiz_status(fn, nxt):
    body = _fn_src(_read(HUBS), fn, nxt)
    assert "status.textContent" not in body
    assert "statusEl.textContent" not in body, (
        f"{fn} still writes statusEl.textContent (statusEl no longer declared there)"
    )
    assert not re.search(r"setTimeout\([^)]*_setLmsQuizStatus\(''\)", body)


def test_no_lingering_statusEl_reference_without_declaration_in_poll():
    """Every function that uses `statusEl` must declare it (const/let/var) itself."""
    body = _fn_src(_read(HUBS), "async function pollLmsQuizStatus(", "async function loadLmsQuizPreview(")
    uses = "statusEl" in body
    declares = re.search(r"\b(const|let|var)\s+statusEl\b", body) is not None
    assert not uses or declares, "pollLmsQuizStatus references undeclared `statusEl` -> ReferenceError"


@pytest.mark.parametrize(
    "fn, nxt",
    [
        ("async function submitLmsPdfQuiz(", "function startLmsPoll("),
        ("async function pollLmsQuizStatus(", "async function loadLmsQuizPreview("),
        ("async function publishLmsQuiz(", "\n    }\n"),
    ],
)
def test_quiz_functions_have_no_statusEl_reference_at_all(fn, nxt):
    body = _fn_src(_read(HUBS), fn, nxt)
    assert not re.search(r"\bstatusEl\b", body), f"{fn} references `statusEl`"


def test_statusEl_declared_only_in_publishLmsDiagnostic():
    src = _read(HUBS)
    decls = [m.start() for m in re.finditer(r"\b(?:const|let|var)\s+statusEl\b", src)]
    assert len(decls) == 1
    diag = _fn_src(src, "async function publishLmsDiagnostic(", "\n    }\n")
    start = src.index("async function publishLmsDiagnostic(")
    assert start < decls[0] < start + len(diag)
    # every statusEl use in the file sits inside publishLmsDiagnostic
    for m in re.finditer(r"\bstatusEl\b", src):
        assert start <= m.start() < start + len(diag), f"statusEl used outside publishLmsDiagnostic at offset {m.start()}"


def test_create_form_status_has_live_region():
    html = _read(CREATE_FORM)
    m = re.search(r'<div id="lmsQuizStatus"[^>]*>', html)
    assert m and 'role="status"' in m.group(0) and 'aria-live="polite"' in m.group(0)


def test_dashboard_css_has_red_error_status_style():
    css = _read(DASH_CSS)
    assert ".td-status-line.is-error" in css
    block = css[css.index(".td-status-line.is-error {"):]
    block = block[: block.index("}")]
    assert "#991b1b" in block and "--td-red" in block
    assert ".td-status-line.is-error .td-status-dismiss" in css


def test_teacher_quizzes_on_file_chosen_clears_error_and_checks_pdf_header():
    src = _read(QUIZZES)
    assert "window._setLmsQuizStatus('')" in src
    assert "f.slice(0, 1024)" in src and "'%PDF'" in src
    assert "This file is not a valid PDF" in src


@needs_node
@pytest.mark.parametrize("path", [HUBS, QUIZZES])
def test_changed_js_parses(path):
    r = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------- node harness

HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const scenario = JSON.parse(process.argv[3]);
const src = fs.readFileSync(process.argv[2], 'utf8');
function mkEl(tag) {
  const e = {
    tagName: tag, style: {}, attrs: {}, children: [], _text: '', innerHTML: '', value: '', hidden: false,
    disabled: false, files: [], checked: false,
    classList: { _s: new Set(), add(c){this._s.add(c)}, remove(c){this._s.delete(c)},
      toggle(c,on){ (on===undefined ? !this._s.has(c) : on) ? this._s.add(c) : this._s.delete(c); },
      contains(c){return this._s.has(c)} },
    setAttribute(k,v){this.attrs[k]=String(v)}, getAttribute(k){return this.attrs[k]},
    removeAttribute(k){delete this.attrs[k]},
    append(...a){ this.children.push(...a) },
    querySelector(){ return null }, querySelectorAll(){ return [] },
    addEventListener(){}, focus(){}, scrollIntoView(){},
  };
  Object.defineProperty(e, 'textContent', {
    get(){ return this._text + this.children.map(c => c.textContent).join('') },
    set(v){ this._text = String(v); this.children = [] },
  });
  return e;
}
const els = {};
const document = {
  getElementById(id){ return els[id] || (els[id] = mkEl('div')) },
  createElement(t){ return mkEl(t) }, querySelector(){ return null }, querySelectorAll(){ return [] },
  addEventListener(){},
};
const log = { timeouts: [], hideWaitOverlay: 0, fetches: [], unhandled: [] };
process.on('unhandledRejection', (r) => { log.unhandled.push(String(r && r.message || r)); });
const ctx = {
  document, console, FormData: class { append(){} },
  setTimeout(fn, ms){ log.timeouts.push(String(fn).slice(0, 120)); return 1; }, clearTimeout(){},
  setInterval(){ return 1 }, clearInterval(){},
  hideWaitOverlay(){ log.hideWaitOverlay++ }, showWaitOverlay(){}, lmsShowToast(){},
  fetch: async (url, opts) => {
    log.fetches.push({ url, method: (opts && opts.method) || 'GET' });
    const r = scenario.responses.shift() || { status: 200, body: {} };
    return { ok: r.status >= 200 && r.status < 300, status: r.status,
      json: async () => { if (scenario.badJson) throw new SyntaxError('Unexpected token <'); return r.body; } };
  },
};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(src, ctx);
vm.runInContext(`
  _setLmsQuizProgress = function(){}; _resetLmsQuizProgress = function(){};
  _startLmsQuizProgressPulse = function(){}; loadLmsQuizPreview = async function(){};
  startLmsPoll = function(){}; stopLmsPoll = function(){};
`, ctx);
function snapshot() {
  const s = els['lmsQuizStatus'] || mkEl('div');
  return {
    text: s.textContent, isError: s.classList.contains('is-error'), role: s.attrs.role || null,
    childClasses: s.children.map(c => c.className || ''),
  };
}
(async () => {
  let threw = null;
  try {
    if (scenario.action === 'set') {
      ctx._setLmsQuizStatus(scenario.msg, scenario.isError);
      if (scenario.thenDismiss) {
        const s = els['lmsQuizStatus'];
        const btn = s.children.find(c => c.className === 'td-status-dismiss');
        btn.onclick();
      }
    } else if (scenario.action === 'poll') {
      vm.runInContext('_lmsCurrentAssessmentId = 42;', ctx);
      await ctx.pollLmsQuizStatus();
    } else if (scenario.action === 'submit') {
      document.getElementById('lmsQuizTitle').value = 'T';
      document.getElementById('lmsQuizDuration').value = scenario.duration || '10';
      document.getElementById('lmsQuizMcqCount').value = '10';
      document.getElementById('lmsQuizPdfFile').files = [{ name: scenario.filename || 'scan.pdf' }];
      await ctx.submitLmsPdfQuiz({ preventDefault(){} });
    } else if (scenario.action === 'publish') {
      vm.runInContext('_lmsCurrentAssessmentId = 42;', ctx);
      await ctx.publishLmsQuiz();
    }
  } catch (e) { threw = String(e && e.message || e); }
  await new Promise(r => setImmediate(r));
  console.log(JSON.stringify({ threw, status: snapshot(), log }));
})();
"""


def _run(scenario, tmp_path):
    h = tmp_path / "harness.js"
    h.write_text(HARNESS, encoding="utf-8")
    r = subprocess.run(
        ["node", str(h), str(HUBS), json.dumps(scenario)], capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@needs_node
def test_error_status_renders_alert_with_dismiss_and_never_auto_hides(tmp_path):
    out = _run({"action": "set", "msg": "This PDF contains scanned images", "isError": True, "responses": []}, tmp_path)
    st = out["status"]
    assert st["isError"] and st["role"] == "alert"
    assert "This PDF contains scanned images" in st["text"]
    assert st["childClasses"] == ["td-status-ic", "td-status-text", "td-status-dismiss"]
    assert out["log"]["timeouts"] == [], "no timer may clear the error"


@needs_node
def test_dismiss_button_clears_error(tmp_path):
    out = _run({"action": "set", "msg": "Bad file", "isError": True, "thenDismiss": True, "responses": []}, tmp_path)
    assert out["status"] == {"text": "", "isError": False, "role": "status", "childClasses": []}


@needs_node
def test_non_error_status_is_plain_text(tmp_path):
    out = _run({"action": "set", "msg": "Publishing quiz...", "isError": False, "responses": []}, tmp_path)
    assert out["status"]["text"] == "Publishing quiz..."
    assert not out["status"]["isError"] and out["status"]["role"] == "status"


@needs_node
def test_submit_server_validation_error_shows_specific_message_as_alert(tmp_path):
    msg = "This PDF contains scanned images without readable text. Please upload a supported text-based PDF."
    out = _run(
        {
            "action": "submit",
            "responses": [{"status": 400, "body": {"success": False, "error": {"code": "validation_error", "message": msg, "details": {"reason": "PDF_SCANNED"}}}}],
        },
        tmp_path,
    )
    assert out["threw"] is None
    assert out["status"]["isError"] and out["status"]["role"] == "alert"
    assert msg in out["status"]["text"]
    assert out["log"]["timeouts"] == []


@needs_node
def test_submit_non_pdf_name_shows_error_without_upload(tmp_path):
    out = _run({"action": "submit", "filename": "notes.docx", "responses": []}, tmp_path)
    assert out["status"]["isError"]
    assert out["log"]["fetches"] == []


@needs_node
def test_submit_bad_duration_shows_error(tmp_path):
    out = _run({"action": "submit", "duration": "1.5", "responses": []}, tmp_path)
    assert out["status"]["isError"] and "whole number" in out["status"]["text"]


@needs_node
def test_publish_error_uses_alert_and_reads_nested_error_message(tmp_path):
    out = _run(
        {"action": "publish", "responses": [{"status": 400, "body": {"success": False, "error": {"message": "Quiz has no questions"}}}]},
        tmp_path,
    )
    assert out["threw"] is None
    assert out["status"]["isError"]
    assert "Quiz has no questions" in out["status"]["text"]


@needs_node
def test_poll_failed_status_surfaces_error_message(tmp_path):
    """Async extraction failure (e.g. vision step failed) must reach the teacher."""
    out = _run(
        {"action": "poll", "responses": [{"status": 200, "body": {"data": {"extraction_status": "failed", "error_message": "Vision extraction failed", "question_count": 0}}}]},
        tmp_path,
    )
    assert out["log"]["unhandled"] == [] and out["threw"] is None, out
    assert "Vision extraction failed" in out["status"]["text"], out
    assert out["log"]["hideWaitOverlay"] >= 1, out


@needs_node
def test_poll_processing_status_does_not_throw(tmp_path):
    out = _run(
        {"action": "poll", "responses": [{"status": 200, "body": {"data": {"extraction_status": "processing", "progress": 30}}}]},
        tmp_path,
    )
    assert out["log"]["unhandled"] == [] and out["threw"] is None, out


@needs_node
def test_poll_completed_hides_wait_overlay(tmp_path):
    out = _run(
        {"action": "poll", "responses": [{"status": 200, "body": {"data": {"extraction_status": "completed", "question_count": 5}}}]},
        tmp_path,
    )
    assert out["log"]["unhandled"] == [] and out["threw"] is None, out
    assert out["log"]["hideWaitOverlay"] >= 1, out


@needs_node
def test_poll_failed_without_message_uses_fallback_error(tmp_path):
    out = _run(
        {"action": "poll", "responses": [{"status": 200, "body": {"data": {"extraction_status": "failed", "question_count": 0}}}]},
        tmp_path,
    )
    assert out["threw"] is None and out["status"]["isError"]
    assert "Quiz import failed" in out["status"]["text"]
    # empty draft is cleaned up after a failed import
    assert any(f["method"] == "DELETE" and f["url"] == "/api/lms/quizzes/42" for f in out["log"]["fetches"])


@needs_node
def test_poll_failed_status_is_red_alert(tmp_path):
    out = _run(
        {"action": "poll", "responses": [{"status": 200, "body": {"data": {"extraction_status": "failed", "error_message": "Vision extraction failed"}}}]},
        tmp_path,
    )
    assert out["status"]["isError"] and out["status"]["role"] == "alert"


@needs_node
def test_poll_transient_error_shows_non_error_reconnecting_line(tmp_path):
    # a non-JSON body makes res.json() throw -> catch branch
    out = _run({"action": "poll", "responses": [{"status": 502, "body": None}], "badJson": True}, tmp_path)
    assert out["threw"] is None and out["log"]["unhandled"] == []
    assert "reconnecting" in out["status"]["text"].lower()
    assert out["status"]["isError"] is False
