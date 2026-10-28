"""Regression coverage for long-running teacher PDF ingestion polling."""

import json
import pathlib
import shutil
import subprocess

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
CORE_JS = ROOT / "static" / "teacher" / "js" / "teacher-core.js"
BASE_HTML = ROOT / "templates" / "teacher" / "base.html"


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _processing_functions():
    source = CORE_JS.read_text(encoding="utf-8")
    start = source.index("async function processCreateLessonStep2()")
    end = source.index("function showCreateLessonUploadComplete", start)
    return source[start:end]


def test_long_ingest_has_no_120_poll_false_timeout_and_is_cache_busted():
    body = _processing_functions()
    assert "pollMax = 120" not in body
    assert "while (true)" in body
    assert "pollCeilingMs = 105 * 60 * 1000" in body
    assert "showCreateLessonProcessingError" in body
    assert "20261028longpdf1" in BASE_HTML.read_text(encoding="utf-8")


NODE_HARNESS = r"""
const vm = require('vm');
const fs = require('fs');
const functions = fs.readFileSync(process.argv[2], 'utf8');

const result = { polls: 0, finalized: 0, chatOpened: 0, closed: 0, stored: {} };
const progress = { style: {}, textContent: '', setAttribute(){}, removeAttribute(){} };
const button = { removeAttribute(){}, textContent: '', onclick: null };
const modal = {
  isConnected: true,
  querySelector(selector) {
    if (selector === '.ppm-cancel-btn') return button;
    return progress;
  }
};

class FakeXHR {
  constructor() {
    this.upload = { addEventListener(){}, onprogress: null };
    this.status = 202;
    this.responseText = '';
  }
  open() {}
  abort() { if (this.onabort) this.onabort(); }
  send() {
    this.responseText = JSON.stringify({
      task_id: 'long-task', status: 'processing',
      thread_id: 'pending-thread', conversation_id: 186
    });
    this.onload();
  }
}

const ctx = {
  console,
  Date,
  Promise,
  FormData: class { append() {} },
  AbortController: class { constructor(){ this.signal = { addEventListener(){} }; } abort(){} },
  XMLHttpRequest: FakeXHR,
  setTimeout(fn) { fn(); return 1; },
  clearTimeout() {},
  document: {
    visibilityState: 'visible',
    getElementById(id) { return id === 'createLessonModal' ? modal : null; },
    addEventListener() {},
    removeEventListener() {}
  },
  localStorage: {
    setItem(key, value) { result.stored[key] = String(value); },
    removeItem() {}
  },
  fetch: async function() {
    result.polls += 1;
    const processing = result.polls <= 121;
    return {
      ok: true,
      status: 200,
      json: async function() {
        if (processing) return { status: 'processing', progress: 50, message: 'Embedding chunks' };
        return {
          status: 'success', progress: 100,
          thread_id: 'completed-thread', conversation_id: 187,
          num_pages: 2037
        };
      }
    };
  },
  updatePpmSteps() {},
  showToast() {},
  closeCreateLessonModal() { result.closed += 1; modal.isConnected = false; },
  showChatTab() { result.chatOpened += 1; },
  finalizeCreateLesson() { result.finalized += 1; ctx.showChatTab(); }
};
ctx.window = ctx;
ctx.window._createLessonFile = { name: 'large.pdf', size: 86000000 };

vm.createContext(ctx);
vm.runInContext(functions + '\nthis.processCreateLessonStep2 = processCreateLessonStep2;', ctx);

(async function () {
  await ctx.processCreateLessonStep2('large.pdf', 86000000, 'Tax lesson', '', 'General', '10', 'generate');
  result.threadId = ctx.currentRAGThreadId;
  result.conversationId = ctx.currentRAGConversationId;
  result.inFlight = ctx._ingestInFlight;
  console.log(JSON.stringify(result));
})().catch(function (error) {
  console.error(error && error.stack || error);
  process.exit(1);
});
"""


@needs_node
def test_121_processing_responses_then_success_finalize_once(tmp_path):
    functions_file = tmp_path / "processing-functions.js"
    functions_file.write_text(_processing_functions(), encoding="utf-8")
    harness_file = tmp_path / "long-ingest-harness.js"
    harness_file.write_text(NODE_HARNESS, encoding="utf-8")

    completed = subprocess.run(
        ["node", str(harness_file), str(functions_file)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip())

    assert result["polls"] == 122
    assert result["finalized"] == 1
    assert result["chatOpened"] == 1
    assert result["closed"] == 0
    assert result["threadId"] == "completed-thread"
    assert result["conversationId"] == 187
    assert result["stored"]["teacher_currentRAGThreadId"] == "completed-thread"
    assert result["stored"]["teacher_currentRAGConversationId"] == "187"
    assert result["inFlight"] is False
