"""Fix 7/8 (18 Oct): long PDF uploads / ingest no longer abort or silently close.

- wait-overlay.js bindUploadEtaGuard: no window.confirm / xhr.abort (Node vm harness)
- teacher-core.js: no 120-poll cap, visibility-aware polling, persistent error, in-flight guard
- rag_routes.get_ingest_status: STARTED is a live state; SUCCESS carrying {'success': False}
  is reported as failure (bare Flask app, Celery AsyncResult faked, no DB)."""
import os
import tempfile

if "iqbalai_local.db" in os.environ.get("DATABASE_URL", "iqbalai_local.db"):
    os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "oct18_fix78.db").replace("\\", "/")

import json
import pathlib
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from flask import Flask

ROOT = pathlib.Path(__file__).resolve().parents[1]
WAIT_JS = ROOT / "static" / "js" / "wait-overlay.js"
CORE_JS = ROOT / "static" / "teacher" / "js" / "teacher-core.js"
THEME_HEAD = ROOT / "templates" / "partials" / "platform_theme_head.html"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@pytest.fixture(autouse=True)
def _never_the_dev_db():
    from app.config import Config

    if "iqbalai_local.db" in str(getattr(Config, "SQLALCHEMY_DATABASE_URI", "")):
        pytest.skip("app configured against the dev DB; refusing to run")


def _read(p):
    return p.read_text(encoding="utf-8")


def _guard_src():
    src = _read(WAIT_JS)
    start = src.index("global.bindUploadEtaGuard = function")
    return src[start: src.index("global.withWaitOverlay", start)]


# ------------------------------------------------------------- static: wait-overlay.js

def test_upload_eta_guard_has_no_confirm_or_abort():
    body = _guard_src()
    code = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("//"))
    assert "confirm(" not in code
    assert ".abort(" not in code
    assert "longUpload: askedProceed" in code


def test_wait_overlay_cache_bumped():
    assert re.search(r"js/wait-overlay\.js'\) \}\}\?v=\w+", _read(THEME_HEAD))


@needs_node
@pytest.mark.parametrize("path", [WAIT_JS, CORE_JS])
def test_js_parses(path):
    r = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


ETA_HARNESS = r"""
const vm = require('vm');
const src = require('fs').readFileSync(process.argv[2], 'utf8');
let t = 1000000;
const log = { confirms: 0, aborts: 0, updates: [] };
const ctx = {
  document: { addEventListener(){}, getElementById(){ return null } },
  addEventListener(){}, setTimeout(){ return 1 }, clearTimeout(){},
  confirm(){ log.confirms++; return false },
  Date: { now(){ return t } },
};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(src, ctx);
let handler = null;
const xhr = { upload: { addEventListener(n, fn){ if (n === 'progress') handler = fn } }, abort(){ log.aborts++ } };
ctx.bindUploadEtaGuard(xhr, (msg, info) => log.updates.push({ msg, longUpload: info.longUpload, remainingSec: info.remainingSec }));
const total = 500 * 1024 * 1024;  // 500 MB at ~2 MB/s -> several minutes remaining
for (const [sec, loaded] of [[1, 1e6], [13, 26e6], [30, 60e6], [60, 120e6]]) {
  t = 1000000 + sec * 1000;
  handler({ lengthComputable: true, loaded, total });
}
t = 1000000 + 400 * 1000;
handler({ lengthComputable: true, loaded: total, total });
console.log(JSON.stringify(log));
"""


@needs_node
def test_long_upload_never_prompts_or_aborts_and_shows_notice(tmp_path):
    h = tmp_path / "eta.js"
    h.write_text(ETA_HARNESS, encoding="utf-8")
    r = subprocess.run(["node", str(h), str(WAIT_JS)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    log = json.loads(r.stdout.strip())
    assert log["confirms"] == 0 and log["aborts"] == 0
    ups = log["updates"]
    assert ups[0]["longUpload"] is False and ups[0]["remainingSec"] is None
    assert ups[1]["remainingSec"] >= 90 and ups[1]["longUpload"] is True
    assert "keeps uploading if you switch tabs" in ups[1]["msg"]
    assert "keeps uploading" not in ups[-1]["msg"], "notice dropped at 100%"


# ------------------------------------------------------------- static: teacher-core.js

def _step2_src():
    src = _read(CORE_JS)
    start = src.index("async function processCreateLessonStep2()")
    return src[start: src.index("async function _processCreateLessonStep2Impl", start) + 20000]


def test_no_fixed_poll_cap_and_visibility_aware_wait():
    src = _read(CORE_JS)
    assert "pollMax = 120" not in src
    body = _step2_src()
    assert "waitForNextPoll" in body and "visibilitychange" in body
    assert "document.visibilityState === 'visible'" in body
    assert "removeEventListener('visibilitychange'" in body


def test_in_flight_guard_on_step2():
    src = _read(CORE_JS)
    w = src[src.index("async function processCreateLessonStep2()"):src.index("function _formatElapsed")]
    m = re.search(r"if \(window\._ingestInFlight\)\s*(\{[^}]*?return;\s*\}|return;)", w)
    assert m, "second call while in flight must return early"
    assert "window._ingestInFlight = true;" in w
    assert "finally" in w and "window._ingestInFlight = false;" in w


def test_persistent_processing_error_and_gateway_message():
    src = _read(CORE_JS)
    assert "function showCreateLessonProcessingError(message)" in src
    fn = src[src.index("function showCreateLessonProcessingError(message)"):src.index("async function _processCreateLessonStep2Impl")]
    assert "setAttribute('role', 'alert')" in fn
    assert "xhr.status === 502 || xhr.status === 504" in src
    body = _step2_src()
    # failure from the status poll keeps the modal open with the error (no silent close)
    fail_branch = body[body.index("statusData.status === 'failure'"):]
    fail_branch = fail_branch[: fail_branch.index("return;")]
    assert "showCreateLessonProcessingError(" in fail_branch
    assert "closeCreateLessonModal" not in fail_branch


# ------------------------------------------------------------- route: get_ingest_status

class FakeTask:
    def __init__(self, state, result=None, info=None):
        self.state, self.result, self.info = state, result, info


class _NoCancelledRow:
    def query(self, *a):
        return self

    def filter_by(self, **kw):
        return self

    def first(self):
        return None


@pytest.fixture
def ingest(monkeypatch):
    from app.routes import rag_routes

    st = SimpleNamespace(task=FakeTask("PENDING"), warmups=0, validated=[], owner_ok=True)
    monkeypatch.setattr("app.utils.auth.sync_session_user", lambda: True)
    monkeypatch.setattr(rag_routes, "ingest_pdf_task", SimpleNamespace(AsyncResult=lambda tid: st.task))

    def warm():
        st.warmups += 1

    monkeypatch.setattr(rag_routes, "warmup_rag_embeddings", warm)

    def validate(thread_id, user_id):
        st.validated.append((thread_id, user_id))
        return st.owner_ok

    monkeypatch.setattr(rag_routes, "_validate_thread_id", validate)
    monkeypatch.setattr(rag_routes, "get_db", lambda: _NoCancelledRow())

    app = Flask(__name__)
    app.secret_key = "t"
    app.config["USE_CELERY_FOR_INGESTION"] = True
    app.register_blueprint(rag_routes.bp, url_prefix="/api/rag")
    from flask import Blueprint

    auth_stub = Blueprint("auth", "auth_stub")
    auth_stub.add_url_rule("/login", "login", lambda: "login")
    app.register_blueprint(auth_stub, url_prefix="/auth")
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 10
        s["role"] = "teacher"
    return st, c, app


def test_started_state_is_processing_not_404(ingest):
    st, c, _ = ingest
    st.task = FakeTask("STARTED", info={"pid": 1234, "hostname": "w1"})
    r = c.get("/api/rag/ingest/status/abc")
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "processing" and body["state"] == "STARTED"
    assert body["progress"] == 0 and body["message"] == "Processing PDF..."


def test_started_with_non_dict_info_does_not_crash(ingest):
    st, c, _ = ingest
    st.task = FakeTask("STARTED", info="worker-meta-string")
    r = c.get("/api/rag/ingest/status/abc")
    assert r.status_code == 200 and r.get_json()["status"] == "processing"


def test_processing_reports_progress(ingest):
    st, c, _ = ingest
    st.task = FakeTask("PROCESSING", info={"step": "embedding", "progress": 63, "message": "Embedding chunks"})
    body = c.get("/api/rag/ingest/status/abc").get_json()
    assert body["status"] == "processing" and body["progress"] == 63 and body["step"] == "embedding"


def test_success_with_success_false_is_reported_as_failure(ingest):
    st, c, _ = ingest
    st.task = FakeTask("SUCCESS", result={"success": False, "error": "PDF has no readable text", "thread_id": "thr-1"})
    r = c.get("/api/rag/ingest/status/abc")
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "failure" and body["state"] == "FAILURE"
    assert body["error"] == "PDF has no readable text"
    assert st.warmups == 0, "no embedding warm-up for a failed ingest"
    assert st.validated == [("thr-1", 10)], "ownership still checked first"


def test_success_false_with_message_only(ingest):
    st, c, _ = ingest
    st.task = FakeTask("SUCCESS", result={"success": False, "message": "Ingest crashed"})
    body = c.get("/api/rag/ingest/status/abc").get_json()
    assert body["status"] == "failure" and body["error"] == "Ingest crashed" and body["message"] == "Ingest crashed"


def test_success_false_other_users_task_still_403(ingest):
    st, c, _ = ingest
    st.owner_ok = False
    st.task = FakeTask("SUCCESS", result={"success": False, "error": "x", "thread_id": "someone-else"})
    assert c.get("/api/rag/ingest/status/abc").status_code == 403


def test_normal_success_still_success(ingest):
    st, c, _ = ingest
    st.task = FakeTask("SUCCESS", result={"success": True, "thread_id": "thr-1", "filename": "a.pdf", "num_pages": 3, "chunks": 9})
    r = c.get("/api/rag/ingest/status/abc")
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "success" and body["thread_id"] == "thr-1"
    assert body["num_pages"] == 3 and body["chunks"] == 9
    assert st.warmups == 1


def test_success_without_success_key_still_success(ingest):
    st, c, _ = ingest
    st.task = FakeTask("SUCCESS", result={"thread_id": "thr-1"})
    assert c.get("/api/rag/ingest/status/abc").get_json()["status"] == "success"


def test_failure_state_unchanged(ingest):
    st, c, _ = ingest
    st.task = FakeTask("FAILURE", result=None, info=RuntimeError("worker died"))
    body = c.get("/api/rag/ingest/status/abc").get_json()
    assert body["status"] == "failure" and "worker died" in body["error"]


def test_unknown_state_404(ingest):
    st, c, _ = ingest
    st.task = FakeTask("WEIRD")
    assert c.get("/api/rag/ingest/status/abc").status_code == 404


def test_celery_disabled_400(ingest):
    st, c, app = ingest
    app.config["USE_CELERY_FOR_INGESTION"] = False
    assert c.get("/api/rag/ingest/status/abc").status_code == 400


def test_unauthenticated_401(ingest):
    st, c, app = ingest
    with c.session_transaction() as s:
        s.clear()
    r = c.get("/api/rag/ingest/status/abc")
    assert r.status_code in (401, 302)
    assert st.task.state == "PENDING"  # nothing about the task was revealed


# ------------------------------------------------------------- follow-up teacher-core.js edits

def _extract_block(src, signature):
    start = src.index(signature)
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start: j + 1]
    raise AssertionError("unbalanced")


def test_close_create_lesson_modal_aborts_in_flight_upload():
    fn = _extract_block(_read(CORE_JS), "function closeCreateLessonModal(")
    assert "window._ingestXhr.abort()" in fn and "window._ingestXhr = null" in fn


def test_revoked_status_closes_modal():
    body = _step2_src()
    branch = body[body.index("statusData.status === 'revoked'"):]
    branch = branch[: branch.index("break;")]
    assert "closeCreateLessonModal()" in branch


def test_in_flight_early_return_tells_the_teacher():
    src = _read(CORE_JS)
    w = src[src.index("async function processCreateLessonStep2()"):src.index("function _formatElapsed")]
    guard = w[w.index("if (window._ingestInFlight)"): w.index("window._ingestInFlight = true;")]
    assert "showToast(" in guard and "return;" in guard


@pytest.mark.parametrize("fn", ["loadVersionIntoDraft", "editWithHumanLessonModal", "loadTemplateIntoLessonDraft"])
def test_draft_loaders_switch_to_improve_view(fn):
    src = _read(CORE_JS)
    m = re.search(r"(?:async\s+)?function " + fn + r"\s*\(", src)
    assert m
    body = _extract_block(src, m.group(0))
    assert "window.tdVlTab('improve')" in body


TOAST_HARNESS = r"""
const vm = require('vm');
const src = require('fs').readFileSync(process.argv[2], 'utf8');
function mk() {
  return { _t: '', innerHTML: '', style: {}, className: '', children: [],
    set textContent(v) { this._t = String(v); this.innerHTML = this._t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); },
    get textContent() { return this._t; }, appendChild(c) { this.children.push(c) }, remove() {} };
}
const container = mk();
const ctx = { setTimeout(){}, document: { createElement: mk, getElementById(id){ return id === 'toastContainer' ? container : null } } };
vm.createContext(ctx);
vm.runInContext(src, ctx);
ctx.showToast('<img src=x onerror=alert(1)> & "quotes"', 'error');
console.log(JSON.stringify({ html: container.children[0].innerHTML }));
"""


@needs_node
def test_show_toast_escapes_message(tmp_path):
    src = _read(CORE_JS)
    fns = _extract_block(src, "function escapeHtml(text)") + "\n" + _extract_block(src, "function showToast(message")
    f = tmp_path / "toast_fns.js"
    f.write_text(fns + "\nthis.showToast = showToast;", encoding="utf-8")
    h = tmp_path / "toast.js"
    h.write_text(TOAST_HARNESS, encoding="utf-8")
    r = subprocess.run(["node", str(h), str(f)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    html = json.loads(r.stdout.strip())["html"]
    assert "<img" not in html and "&lt;img src=x onerror=alert(1)&gt; &amp;" in html
