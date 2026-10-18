"""Fix 2 (18 Oct): Improve with AI.

- service: improve_lesson_content(raise_on_error=...) both modes (LLM mocked)
- route:   POST /api/lessons/lesson/<id>/apply_prompt via a bare Flask app (no real DB:
           LessonModel / LessonService / UserModel are mocked)
- static:  template / CSS / JS structure
- JS:      applyPromptToLessonDraft and tdVlTab driven in a Node `vm` with a DOM stub
"""
import os
import tempfile

# DB safety: app.config reads DATABASE_URL once at first import of `app`. Never let it be the dev DB.
if "iqbalai_local.db" in os.environ.get("DATABASE_URL", "iqbalai_local.db"):
    os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "oct18_fix2.db").replace("\\", "/")

import json
import pathlib
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from flask import Flask

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODAL = ROOT / "templates" / "teacher" / "lessons" / "view_lesson_modal.html"
VL_CSS = ROOT / "static" / "teacher" / "css" / "teacher-view-lesson.css"
CORE_JS = ROOT / "static" / "teacher" / "js" / "teacher-core.js"
SHELL_JS = ROOT / "static" / "teacher" / "js" / "teacher-shell.js"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@pytest.fixture(autouse=True)
def _never_the_dev_db():
    from app.config import Config

    uri = str(getattr(Config, "SQLALCHEMY_DATABASE_URI", ""))
    if "iqbalai_local.db" in uri:
        pytest.skip("app configured against the dev DB; refusing to run")


# ------------------------------------------------------------------ service

@pytest.fixture
def svc(monkeypatch):
    from app.services.lesson import teacher_service as ts

    class _NoLLMTeacherService(ts.TeacherLessonService):
        llm = object()  # shadows the lazy LLM property: no real model is ever built

    s = _NoLLMTeacherService.__new__(_NoLLMTeacherService)
    monkeypatch.setattr(ts.LessonModel, "get_lesson_by_id", staticmethod(lambda lid: {"original_content": "ORIG"}))
    return s, ts


def test_service_success_returns_improved_content_in_both_modes(svc, monkeypatch):
    s, ts = svc
    monkeypatch.setattr(ts, "_invoke_llm_with_retry", lambda llm, prompt, **k: SimpleNamespace(content="  ## Better  "))
    assert s.improve_lesson_content(1, "CUR", "make it better") == "## Better"
    assert s.improve_lesson_content(1, "CUR", "make it better", raise_on_error=True) == "## Better"


def test_service_llm_exception_legacy_mode_returns_current_content(svc, monkeypatch):
    s, ts = svc

    def boom(*a, **k):
        raise RuntimeError("groq down")

    monkeypatch.setattr(ts, "_invoke_llm_with_retry", boom)
    assert s.improve_lesson_content(1, "CUR", "x") == "CUR"


def test_service_llm_exception_raise_mode_raises_enhancement_error(svc, monkeypatch):
    s, ts = svc

    def boom(*a, **k):
        raise RuntimeError("groq down")

    monkeypatch.setattr(ts, "_invoke_llm_with_retry", boom)
    with pytest.raises(ts.LessonEnhancementError) as ei:
        s.improve_lesson_content(1, "CUR", "x", raise_on_error=True)
    assert "groq down" in str(ei.value)
    assert isinstance(ei.value.__cause__, RuntimeError)


@pytest.mark.parametrize("empty", ["", "   ", "<think>reasoning only</think>"])
def test_service_empty_output_raise_mode_raises(svc, monkeypatch, empty):
    s, ts = svc
    monkeypatch.setattr(ts, "_invoke_llm_with_retry", lambda *a, **k: SimpleNamespace(content=empty))
    with pytest.raises(ts.LessonEnhancementError):
        s.improve_lesson_content(1, "CUR", "x", raise_on_error=True)


def test_service_empty_output_legacy_mode_does_not_raise(svc, monkeypatch):
    s, ts = svc
    monkeypatch.setattr(ts, "_invoke_llm_with_retry", lambda *a, **k: SimpleNamespace(content=""))
    assert s.improve_lesson_content(1, "CUR", "x") == ""  # legacy behaviour: unchanged


def test_lesson_service_facade_forwards_raise_on_error():
    from app.services.lesson_service import LessonService

    calls = []
    facade = LessonService.__new__(LessonService)
    facade.teacher_service = SimpleNamespace(
        improve_lesson_content=lambda *a, **k: calls.append((a, k)) or "ok"
    )
    assert facade.improve_lesson_content(3, "c", "p", raise_on_error=True) == "ok"
    assert calls == [((3, "c", "p"), {"raise_on_error": True})]
    facade.improve_lesson_content(3, "c", "p")
    assert calls[-1][1] == {"raise_on_error": False}


# ------------------------------------------------------------------ route

TEACHER, OTHER_TEACHER = 10, 11


@pytest.fixture
def route_env(monkeypatch):
    import app.models as models_pkg
    from app.routes import lesson_routes
    from app.services.lesson.teacher_service import LessonEnhancementError

    class FakeUser:
        def __init__(self, uid):
            self.uid = uid

        def is_teacher(self):
            return True

    monkeypatch.setattr(models_pkg, "UserModel", FakeUser)

    state = SimpleNamespace(
        lesson={"id": 5, "teacher_id": TEACHER, "content": "PUBLISHED", "original_content": None},
        draft=None,
        saved=[],
        improve_calls=[],
        improve_result="## AI draft",
        improve_raises=False,
    )
    monkeypatch.setattr(lesson_routes.LessonModel, "get_lesson_by_id", staticmethod(lambda lid: state.lesson if lid == 5 else None))
    monkeypatch.setattr(lesson_routes.LessonModel, "get_draft_content", staticmethod(lambda lid: state.draft))

    def save(lid, content):
        state.saved.append((lid, content))
        return True

    monkeypatch.setattr(lesson_routes.LessonModel, "save_draft_content", staticmethod(save))

    class FakeLessonService:
        def __init__(self, api_key=None):
            pass

        def improve_lesson_content(self, **kw):
            state.improve_calls.append(kw)
            if state.improve_raises:
                raise LessonEnhancementError("LLM failed")
            return state.improve_result

    monkeypatch.setattr(lesson_routes, "LessonService", FakeLessonService)

    app = Flask(__name__)
    app.secret_key = "t"
    app.register_blueprint(lesson_routes.bp, url_prefix="/api/lessons")

    def client(uid=TEACHER):
        c = app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = uid
            s["role"] = "teacher"
        return c

    return state, client


def test_apply_prompt_success_saves_draft_and_returns_it(route_env):
    state, client = route_env
    r = client().post("/api/lessons/lesson/5/apply_prompt", json={"prompt": "simplify"})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["success"] is True and body["draft_content"] == "## AI draft"
    assert state.saved == [(5, "## AI draft")]
    kw = state.improve_calls[0]
    assert kw["raise_on_error"] is True
    # original_content is NULL -> falls back to the published content (`or`, not .get default)
    assert kw["current_content"] == "PUBLISHED"
    assert kw["improvement_prompt"] == "simplify"


def test_apply_prompt_prefers_existing_draft_then_original(route_env):
    state, client = route_env
    state.draft = "MY DRAFT"
    client().post("/api/lessons/lesson/5/apply_prompt", json={"prompt": "p"})
    assert state.improve_calls[-1]["current_content"] == "MY DRAFT"
    state.draft = None
    state.lesson["original_content"] = "ORIGINAL"
    client().post("/api/lessons/lesson/5/apply_prompt", json={"prompt": "p"})
    assert state.improve_calls[-1]["current_content"] == "ORIGINAL"


def test_apply_prompt_ai_failure_returns_503_and_saves_nothing(route_env):
    state, client = route_env
    state.draft = "EXISTING DRAFT"
    state.improve_raises = True
    r = client().post("/api/lessons/lesson/5/apply_prompt", json={"prompt": "simplify"})
    assert r.status_code == 503
    body = r.get_json()
    assert body["success"] is False and body["code"] == "ai_enhancement_failed"
    assert "try again" in body["error"].lower()
    assert state.saved == [], "save_draft_content must NOT be called on AI failure"


def test_apply_prompt_other_teacher_gets_403_and_no_ai_call(route_env):
    state, client = route_env
    r = client(OTHER_TEACHER).post("/api/lessons/lesson/5/apply_prompt", json={"prompt": "x"})
    assert r.status_code == 403
    assert state.improve_calls == [] and state.saved == []


def test_apply_prompt_empty_prompt_400(route_env):
    state, client = route_env
    r = client().post("/api/lessons/lesson/5/apply_prompt", json={"prompt": "   "})
    assert r.status_code == 400 and state.improve_calls == []


def test_apply_prompt_missing_lesson_404(route_env):
    state, client = route_env
    assert client().post("/api/lessons/lesson/999/apply_prompt", json={"prompt": "x"}).status_code == 404


def test_apply_prompt_unexpected_error_does_not_leak_exception_text(route_env, monkeypatch):
    state, client = route_env
    from app.routes import lesson_routes

    def boom(lid):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(lesson_routes.LessonModel, "get_draft_content", staticmethod(boom))
    r = client().post("/api/lessons/lesson/5/apply_prompt", json={"prompt": "x"})
    assert r.status_code == 500
    assert "secret internal detail" not in r.get_data(as_text=True)
    assert state.saved == []


# ------------------------------------------------------------------ static template / CSS

def _soup():
    bs4 = pytest.importorskip("bs4")
    return bs4.BeautifulSoup(MODAL.read_text(encoding="utf-8"), "html.parser")


def test_draft_card_lives_in_main_column_and_holds_the_draft_editor():
    soup = _soup()
    card = soup.find(id="tdVlDraftCard")
    assert card is not None
    assert card.find_parent(class_="td-vl-main") is not None
    draft = soup.find(id="viewLessonDraft")
    assert draft is not None and draft.find_parent(id="tdVlDraftCard") is card
    assert draft.get("contenteditable") == "true"
    assert len(soup.find_all(id="viewLessonDraft")) == 1


def test_draft_editor_not_inside_improve_aside_but_apply_button_is():
    soup = _soup()
    aside = soup.find("aside", id="tdVlImprove")
    assert aside is not None
    assert aside.find(id="viewLessonDraft") is None
    assert aside.find("button", attrs={"onclick": "applyPromptToLessonDraft()"}) is not None
    assert aside.find(id="viewLessonPrompt") is not None


def test_draft_card_has_loading_and_error_regions():
    soup = _soup()
    card = soup.find(id="tdVlDraftCard")
    loading = card.find(id="tdVlDraftLoading")
    err = card.find(id="tdVlDraftError")
    assert loading is not None and loading.has_attr("hidden")
    assert err is not None and err.has_attr("hidden") and err.get("role") == "alert"


def test_css_shows_draft_only_on_improve_view_and_hides_lesson_there():
    css = VL_CSS.read_text(encoding="utf-8")
    assert "#viewLessonModal #tdVlDraftCard { display: none; }" in css
    assert '#viewLessonModal[data-vl-view="improve"] #tdVlDraftCard { display: block; }' in css
    assert '#viewLessonModal[data-vl-view="improve"] #tdVlLessonCard' in css
    assert "#viewLessonModal.vl-source-pdf-mode #tdVlDraftCard { display: none; }" in css


def test_shell_tdvltab_sets_data_vl_view():
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "modal.setAttribute('data-vl-view', name)" in src
    assert "modal.setAttribute('data-vl-view', 'lesson')" in src


@needs_node
@pytest.mark.parametrize("path", [CORE_JS, SHELL_JS])
def test_js_parses(path):
    r = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ------------------------------------------------------------------ JS behaviour (node vm)

def _extract_block(src, signature):
    """Return source from `signature` through its matching closing brace."""
    start = src.index(signature)
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        ch = src[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start : j + 1]
    raise AssertionError("unbalanced")


APPLY_HARNESS = r"""
const vm = require('vm');
const fnSrc = require('fs').readFileSync(process.argv[2], 'utf8');
const sc = JSON.parse(process.argv[3]);
function mkEl(id) {
  return { id, value: '', hidden: false, disabled: false, innerHTML: 'Apply', textContent: '',
    classList: { _s: new Set(), add(c){this._s.add(c)}, remove(c){this._s.delete(c)}, contains(c){return this._s.has(c)} } };
}
const els = {};
['viewLessonPrompt','tdVlDraftCard','tdVlDraftLoading','tdVlDraftError','viewLessonDraft'].forEach(i => els[i] = mkEl(i));
els.tdVlDraftLoading.hidden = true; els.tdVlDraftError.hidden = true;
els.viewLessonPrompt.value = sc.prompt;
const btn = mkEl('applyBtn');
const log = { fetches: [], toasts: [], rendered: [], tabs: [], loadingSeen: [] };
const pending = [];
const ctx = {
  console: { error(){}, log(){} },
  document: {
    getElementById(id){ return els[id] || null },
    querySelector(sel){ return sel.indexOf('applyPromptToLessonDraft') !== -1 ? btn : null },
    querySelectorAll(){ return [] },
  },
  showToast(m, t){ log.toasts.push([t, m]) },
  setViewLessonDraftMarkdown(md){ log.rendered.push(md) },
  setFinalizeButtonVisible(){},
  getViewLessonDraftMarkdown(){ return sc.manualDraft || '' },
  tdVlTab(n){ log.tabs.push(n) },
  fetch(url, opts) {
    log.fetches.push({ url, body: opts && opts.body });
    log.loadingSeen.push(els.tdVlDraftLoading.hidden === false && btn.disabled === true);
    const r = (sc.responses[url.indexOf('save_draft') !== -1 ? 'save' : 'apply']) || { status: 200, body: {} };
    return new Promise(res => {
      const go = () => {
        if (sc.switchLessonDuringFetch) ctx.window.currentViewLessonId = 999;
        res({ ok: r.status >= 200 && r.status < 300, status: r.status, json: async () => r.body });
      };
      if (sc.holdFetch) pending.push(go); else setImmediate(go);
    });
  },
};
ctx.window = ctx;
ctx.window.currentViewLessonId = 5;
ctx.window._viewLessonDraftDirty = !!sc.dirty;
vm.createContext(ctx);
vm.runInContext(fnSrc + '\nthis.applyPromptToLessonDraft = applyPromptToLessonDraft;', ctx);
(async () => {
  const p1 = ctx.applyPromptToLessonDraft();
  let p2 = null;
  if (sc.doubleClick) p2 = ctx.applyPromptToLessonDraft();
  await new Promise(r => setImmediate(r));
  pending.splice(0).forEach(f => f());
  await p1; if (p2) await p2;
  console.log(JSON.stringify({ log, prompt: els.viewLessonPrompt.value,
    err: { hidden: els.tdVlDraftError.hidden, text: els.tdVlDraftError.textContent },
    loadingHidden: els.tdVlDraftLoading.hidden, btnDisabled: btn.disabled,
    inFlight: !!ctx.window._vlApplyInFlight, promptDisabled: els.viewLessonPrompt.disabled }));
})();
"""


def _run_apply(tmp_path, scenario):
    fn = _extract_block(CORE_JS.read_text(encoding="utf-8"), "async function applyPromptToLessonDraft()")
    f = tmp_path / "apply_fn.js"
    f.write_text(fn, encoding="utf-8")
    h = tmp_path / "apply_harness.js"
    h.write_text(APPLY_HARNESS, encoding="utf-8")
    r = subprocess.run(["node", str(h), str(f), json.dumps(scenario)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


OK_APPLY = {"apply": {"status": 200, "body": {"success": True, "draft_content": "## New draft"}}}


@needs_node
def test_js_apply_success_renders_draft_on_improve_tab_and_resets_ui(tmp_path):
    out = _run_apply(tmp_path, {"prompt": "simplify", "responses": OK_APPLY})
    assert [f["url"] for f in out["log"]["fetches"]] == ["/api/lessons/lesson/5/apply_prompt"]
    assert out["log"]["tabs"] == ["improve"]
    assert out["log"]["rendered"] == ["## New draft"]
    assert out["log"]["loadingSeen"] == [True], "loading state visible while the request runs"
    assert out["prompt"] == "" and out["err"]["hidden"] is True
    assert out["loadingHidden"] is True and out["btnDisabled"] is False
    assert out["inFlight"] is False and out["promptDisabled"] is False


@needs_node
def test_js_apply_503_shows_inline_error_keeps_prompt_and_draft(tmp_path):
    out = _run_apply(
        tmp_path,
        {"prompt": "simplify", "responses": {"apply": {"status": 503, "body": {"success": False, "code": "ai_enhancement_failed", "error": "The AI could not generate an enhanced draft right now."}}}},
    )
    assert out["log"]["rendered"] == []
    assert out["err"]["hidden"] is False
    assert "The AI could not generate an enhanced draft right now." in out["err"]["text"]
    assert out["prompt"] == "simplify", "instructions are kept for retry"
    assert out["inFlight"] is False and out["btnDisabled"] is False


@needs_node
def test_js_apply_in_flight_guard_blocks_double_click(tmp_path):
    out = _run_apply(tmp_path, {"prompt": "simplify", "responses": OK_APPLY, "doubleClick": True, "holdFetch": True})
    assert len(out["log"]["fetches"]) == 1


@needs_node
def test_js_apply_saves_dirty_manual_draft_first(tmp_path):
    out = _run_apply(
        tmp_path,
        {"prompt": "p", "dirty": True, "manualDraft": "my manual edits",
         "responses": dict(OK_APPLY, save={"status": 200, "body": {"success": True}})},
    )
    urls = [f["url"] for f in out["log"]["fetches"]]
    assert urls == ["/api/lessons/lesson/5/save_draft", "/api/lessons/lesson/5/apply_prompt"]
    assert json.loads(out["log"]["fetches"][0]["body"]) == {"draft_content": "my manual edits"}


@needs_node
def test_js_apply_aborts_when_saving_manual_draft_fails(tmp_path):
    out = _run_apply(
        tmp_path,
        {"prompt": "p", "dirty": True, "manualDraft": "edits",
         "responses": dict(OK_APPLY, save={"status": 500, "body": {}})},
    )
    assert [f["url"] for f in out["log"]["fetches"]] == ["/api/lessons/lesson/5/save_draft"]
    assert out["err"]["hidden"] is False and "save your draft" in out["err"]["text"]


@needs_node
def test_js_apply_stale_lesson_result_not_rendered(tmp_path):
    out = _run_apply(tmp_path, {"prompt": "p", "responses": OK_APPLY, "switchLessonDuringFetch": True})
    assert out["log"]["rendered"] == []
    assert out["inFlight"] is False and out["btnDisabled"] is False


@needs_node
def test_js_apply_empty_prompt_makes_no_request(tmp_path):
    out = _run_apply(tmp_path, {"prompt": "  ", "responses": OK_APPLY})
    assert out["log"]["fetches"] == [] and out["log"]["tabs"] == []


TAB_HARNESS = r"""
const vm = require('vm');
const fnSrc = require('fs').readFileSync(process.argv[2], 'utf8');
const sc = JSON.parse(process.argv[3]);
function tab(name){ return { attrs: {'data-vl-tab': name}, classList: { _s: new Set(), toggle(c,on){ on ? this._s.add(c) : this._s.delete(c) } },
  getAttribute(k){ return this.attrs[k] }, setAttribute(k,v){ this.attrs[k]=v } }; }
const tabs = ['lesson','improve','summaries'].map(tab);
const modal = { attrs: {}, classList: { contains(c){ return !!sc.pdfMode && c === 'vl-source-pdf-mode' } },
  setAttribute(k,v){ this.attrs[k]=v } };
const ctx = { setTimeout(){}, window: null,
  document: { getElementById(id){ return id === 'viewLessonModal' ? modal : null },
    querySelectorAll(){ return tabs }, querySelector(){ return null } } };
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(fnSrc, ctx);
ctx.tdVlTab(sc.name);
console.log(JSON.stringify({ view: modal.attrs['data-vl-view'],
  active: tabs.filter(t => t.classList._s.has('active')).map(t => t.attrs['data-vl-tab']),
  selected: tabs.map(t => t.attrs['aria-selected']) }));
"""


@needs_node
@pytest.mark.parametrize(
    "name, pdf, view",
    [("improve", False, "improve"), ("lesson", False, "lesson"), ("summaries", False, "summaries"), ("improve", True, "lesson")],
)
def test_js_tdvltab_sets_view_attribute(tmp_path, name, pdf, view):
    fn = _extract_block(SHELL_JS.read_text(encoding="utf-8"), "window.tdVlTab = function (name)")
    f = tmp_path / "tab_fn.js"
    f.write_text(fn + ";", encoding="utf-8")
    h = tmp_path / "tab_harness.js"
    h.write_text(TAB_HARNESS, encoding="utf-8")
    r = subprocess.run(["node", str(h), str(f), json.dumps({"name": name, "pdfMode": pdf})], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip())
    assert out["view"] == view
    assert out["active"] == [view]
