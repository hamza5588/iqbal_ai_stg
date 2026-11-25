/* Teacher → Scenario Assessments. List, setup (config + three separate document uploads),
   AI question drafts (review / edit / approve / reject / regenerate / publish) and submission
   review (AI breakdown, evidence, override, retry). All text from the server is escaped. */
(function () {
  var API = '/api/lms/scenario';
  var state = { list: [], detail: null, tab: 'setup', subs: null, sub: null, classes: [], poll: null, editing: {} };
  var DOC_TYPES = [
    ['learning', 'Learning content', 'Course material. The AI only uses this for facts and concepts.'],
    ['sample_questions', 'Sample questions', 'Instructor examples. Used for question style and difficulty, never copied.'],
    ['reference_answers', 'General reference answers', 'Conceptual explanations used only for grading. Never shown to students.']
  ];

  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function badge(s, label) { return '<span class="sba-badge sba-b-' + esc(s) + '">' + esc(label || String(s).replace(/_/g, ' ')) + '</span>'; }
  function toast(msg, type) { if (typeof window.showToast === 'function') window.showToast(msg, type || 'success'); }
  function fmt(d) { if (!d) return '—'; try { return new Date(d + (d.endsWith('Z') ? '' : 'Z')).toLocaleString(); } catch (e) { return d; } }
  function pages(r) { return r.page_start === r.page_end || !r.page_end ? 'p. ' + r.page_start : 'pp. ' + r.page_start + '–' + r.page_end; }

  async function api(url, opts) { return lmsApi(API + url, opts); }
  function jsonOpts(method, body) { return { method: method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }; }

  /* ── list ─────────────────────────────────────────────────────────── */
  async function load() {
    stopPoll();
    $('tdSbaList').style.display = '';
    $('tdSbaDetail').style.display = 'none';
    state.detail = null;
    var box = $('tdSbaTable');
    box.innerHTML = '<div class="td-empty"><div class="td-spinner"></div></div>';
    try {
      state.list = await api('/assessments');
    } catch (e) {
      box.innerHTML = '<div class="td-empty"><p class="td-error">' + esc(e.message) + '</p></div>';
      return;
    }
    if (!state.list.length) {
      box.innerHTML = '<div class="td-empty"><p>No scenario assessments yet. Create one to get started.</p></div>';
      return;
    }
    box.innerHTML = '<div class="td-card td-card-flush"><table class="sba-table" id="tdSbaTableEl"><thead><tr><th>Title</th><th>Class</th><th>Status</th>' +
      '<th>Questions</th><th>Submissions</th><th>Needs review</th></tr></thead><tbody>' +
      state.list.map(function (a) {
        return '<tr class="sba-click" data-sba-open="' + a.id + '"><td><b>' + esc(a.title) + '</b>' + (a.topic ? '<br><span class="sba-note">' + esc(a.topic) + '</span>' : '') + '</td>' +
          '<td>' + esc(a.class_name || '—') + '</td><td>' + badge(a.status) + '</td>' +
          '<td>' + a.questions.approved + ' approved · ' + a.questions.draft + ' draft</td>' +
          '<td>' + a.submissions + '</td><td>' + (a.pending_review ? badge('review', a.pending_review + ' to review') : '—') + '</td></tr>';
      }).join('') + '</tbody></table></div>';
    box.querySelectorAll('[data-sba-open]').forEach(function (tr) {
      tr.addEventListener('click', function () { open(+tr.getAttribute('data-sba-open')); });
    });
  }

  async function loadClasses() {
    try { state.classes = await lmsApi('/api/lms/classes'); } catch (e) { state.classes = []; }
    return state.classes;
  }
  function classOptions(selected) {
    return '<option value="">— choose a class —</option>' + state.classes.map(function (c) {
      return '<option value="' + c.id + '"' + (c.id === selected ? ' selected' : '') + '>' + esc(c.name) + '</option>';
    }).join('');
  }

  async function openCreate() {
    $('tdSbaCreate').style.display = '';
    await loadClasses();
    $('tdSbaNewClass').innerHTML = classOptions(null);
    $('tdSbaNewTitle').focus();
  }
  function closeCreate() { $('tdSbaCreate').style.display = 'none'; }
  async function create() {
    var title = $('tdSbaNewTitle').value.trim();
    var cls = $('tdSbaNewClass').value;
    var msg = $('tdSbaCreateMsg');
    if (!title) { msg.textContent = 'Enter a title.'; return; }
    $('tdSbaCreateBtn').disabled = true;
    try {
      var a = await api('/assessments', jsonOpts('POST', { title: title, class_id: cls ? +cls : null }));
      closeCreate();
      $('tdSbaNewTitle').value = '';
      msg.textContent = '';
      state.tab = 'setup';
      await open(a.id);
    } catch (e) { msg.textContent = e.message; }
    $('tdSbaCreateBtn').disabled = false;
  }

  /* ── detail ───────────────────────────────────────────────────────── */
  async function open(id, keepTab) {
    if (!keepTab) { state.tab = 'setup'; state.sub = null; state.subs = null; }
    $('tdSbaList').style.display = 'none';
    var box = $('tdSbaDetail');
    box.style.display = '';
    if (!state.detail || state.detail.id !== id) box.innerHTML = '<div class="td-empty"><div class="td-spinner"></div></div>';
    if (!state.classes.length) await loadClasses();
    try {
      state.detail = await api('/assessments/' + id);
    } catch (e) {
      box.innerHTML = '<div class="td-empty"><p class="td-error">' + esc(e.message) + '</p></div>';
      return;
    }
    if (state.tab === 'submissions') await loadSubs(true);
    render();
    schedulePoll();
  }

  function needsPoll() {
    var d = state.detail;
    if (!d) return false;
    if (d.generation_status === 'running') return true;
    if (d.documents.some(function (x) { return x.is_active && (x.status === 'pending' || x.status === 'processing'); })) return true;
    if (state.tab === 'submissions' && state.subs && state.subs.some(function (s) { return s.status === 'submitted' || s.status === 'evaluating'; })) return true;
    return false;
  }
  function stopPoll() { if (state.poll) { clearTimeout(state.poll); state.poll = null; } }
  function schedulePoll() {
    stopPoll();
    if (needsPoll() && window.tdCurrentView && window.tdCurrentView() === 'assessments') {
      state.poll = setTimeout(function () { if (state.detail && !Object.keys(state.editing).length) open(state.detail.id, true); else schedulePoll(); }, 3000);
    }
  }

  function render() {
    var d = state.detail;
    var box = $('tdSbaDetail');
    var tabs = [['setup', 'Setup & documents'], ['questions', 'Questions (' + d.items.filter(function (q) { return q.status !== 'rejected'; }).length + ')'], ['submissions', 'Submissions (' + d.submissions + ')']];
    box.innerHTML =
      '<a href="#assessments" class="td-btn td-btn-outline td-btn-sm" onclick="tdScenario.load(); return false;"><i class="fas fa-arrow-left"></i> All scenario assessments</a>' +
      '<div class="td-card" style="margin-top:12px;"><div class="sba-q-head"><div><h2 style="margin:0;" id="tdSbaTitle">' + esc(d.title) + '</h2>' +
      '<div class="sba-note">' + esc(d.class_name || 'No class selected') + (d.topic ? ' · ' + esc(d.topic) : '') + '</div></div>' +
      '<div class="sba-actions" style="margin:0;">' + badge(d.status) +
      (d.status === 'published'
        ? '<button class="td-btn td-btn-outline td-btn-sm" id="tdSbaUnpublish">Unpublish</button>'
        : '<button class="td-btn td-btn-green td-btn-sm" id="tdSbaPublish">Publish to class</button>') +
      (d.release_mode === 'manual' && !d.results_released ? '<button class="td-btn td-btn-outline td-btn-sm" id="tdSbaRelease">Release results</button>' : '') +
      '</div></div>' +
      '<div class="sba-tabs">' + tabs.map(function (t) { return '<button type="button" data-sba-tab="' + t[0] + '" class="' + (state.tab === t[0] ? 'active' : '') + '">' + esc(t[1]) + '</button>'; }).join('') + '</div>' +
      '<div id="tdSbaTabBody"></div></div>';
    box.querySelectorAll('[data-sba-tab]').forEach(function (b) {
      b.addEventListener('click', async function () {
        state.tab = b.getAttribute('data-sba-tab');
        state.sub = null;
        if (state.tab === 'submissions') await loadSubs(true);
        render();
        schedulePoll();
      });
    });
    bind('tdSbaPublish', async function () {
      try {
        await act('/assessments/' + d.id + '/publish', 'Published. Students in the class can now answer.');
      } catch (e) {
        var msg = (e && e.message) || '';
        if (/approve at least one/i.test(msg)) {
          state.tab = 'questions';
          render();
          toast('Open each question below and click Approve (or Approve all drafts), then Publish again.', 'error');
        } else {
          throw e;
        }
      }
    });
    bind('tdSbaUnpublish', function () { return act('/assessments/' + d.id + '/unpublish', 'Unpublished.'); });
    bind('tdSbaRelease', function () { return act('/assessments/' + d.id + '/release-results', 'Results released to students.'); });
    if (state.tab === 'setup') renderSetup();
    else if (state.tab === 'questions') renderQuestions();
    else renderSubs();
  }

  function bind(id, fn) {
    var el = $(id);
    if (!el) return;
    el.addEventListener('click', async function () {
      el.disabled = true;
      try { await fn(); } catch (e) { toast(e.message, 'error'); }
      el.disabled = false;
    });
  }
  async function act(url, okMsg, body) {
    await api(url, jsonOpts('POST', body));
    if (okMsg) toast(okMsg);
    await open(state.detail.id, true);
  }

  /* ── setup tab ────────────────────────────────────────────────────── */
  function renderSetup() {
    var d = state.detail;
    var body = $('tdSbaTabBody');
    var docsBy = {};
    d.documents.forEach(function (x) { (docsBy[x.doc_type] = docsBy[x.doc_type] || []).push(x); });
    body.innerHTML =
      '<h3>Assessment settings</h3><div class="sba-form" id="tdSbaConfig">' +
      '<div><label for="sbaTitle">Title</label><input id="sbaTitle" maxlength="255" value="' + esc(d.title) + '"></div>' +
      '<div><label for="sbaClass">Class</label><select id="sbaClass">' + classOptions(d.class_id) + '</select></div>' +
      '<div><label for="sbaTopic">Topic / learning unit</label><input id="sbaTopic" maxlength="500" value="' + esc(d.topic || '') + '"></div>' +
      '<div><label for="sbaCount">Questions to generate</label><input id="sbaCount" type="number" min="1" max="20" value="' + d.question_count + '"></div>' +
      '<div><label for="sbaDiff">Difficulty</label><select id="sbaDiff">' + ['easy', 'medium', 'hard'].map(function (x) { return '<option' + (d.difficulty === x ? ' selected' : '') + '>' + x + '</option>'; }).join('') + '</select></div>' +
      '<div><label for="sbaMax">Marks per question</label><input id="sbaMax" type="number" min="1" max="100" value="' + d.max_score_per_question + '"></div>' +
      '<div><label for="sbaRelease">Show results to students</label><select id="sbaRelease"><option value="immediate"' + (d.release_mode === 'immediate' ? ' selected' : '') + '>As soon as graded</option><option value="manual"' + (d.release_mode === 'manual' ? ' selected' : '') + '>After I release them</option></select></div>' +
      '<div class="sba-wide"><label for="sbaObjectives">Learning objectives (one per line)</label><textarea id="sbaObjectives">' + esc((d.learning_objectives || []).join('\n')) + '</textarea></div>' +
      '<div class="sba-wide"><label for="sbaInstr">Instructions for the AI question writer</label><textarea id="sbaInstr">' + esc(d.instructions || '') + '</textarea></div>' +
      '<div class="sba-wide"><label>Grading rubric (weights must total 100) · version ' + d.rubric_version + '</label><table class="sba-rubric"><thead><tr><th>Criterion</th><th>Weight %</th></tr></thead><tbody>' +
      d.rubric.map(function (c, i) { return '<tr><td>' + esc(c.label) + '</td><td><input type="number" min="0" max="100" step="1" data-rubric="' + i + '" value="' + c.weight + '" aria-label="Weight for ' + esc(c.label) + '"></td></tr>'; }).join('') +
      '</tbody></table><div class="sba-note">Default weights are a starting point, not an approved grading policy. Adjust them to your course.</div></div>' +
      '</div><div class="sba-actions"><button class="td-btn td-btn-primary" id="tdSbaSave">Save settings</button><span class="sba-note" id="tdSbaSaveMsg"></span></div>' +
      '<h3 style="margin-top:22px;">Documents</h3>' +
      '<p class="sba-note">Choose a PDF under each type you need, then use the single <b>Upload selected PDFs</b> button below.</p>' +
      '<div class="sba-grid">' +
      DOC_TYPES.map(function (t) {
        var list = (docsBy[t[0]] || []).filter(function (x) { return x.is_active; });
        var old = (docsBy[t[0]] || []).filter(function (x) { return !x.is_active; });
        return '<div class="sba-upload" data-doc-type="' + t[0] + '"><h4>' + esc(t[1]) + '</h4><p>' + esc(t[2]) + '</p>' +
          '<input type="file" accept="application/pdf,.pdf" id="sbaFile_' + t[0] + '" aria-label="' + esc(t[1]) + ' PDF">' +
          '<div style="margin-top:10px;">' + (list.length ? list.map(docRow).join('') : '<div class="sba-note">No document uploaded.</div>') + '</div>' +
          (old.length ? '<details style="margin-top:6px;"><summary class="sba-note">' + old.length + ' earlier version(s)</summary>' + old.map(docRow).join('') + '</details>' : '') +
          '</div>';
      }).join('') + '</div>' +
      '<div class="sba-actions" style="margin-top:14px;">' +
        '<button type="button" class="td-btn td-btn-primary" id="tdSbaUploadAll"><i class="fas fa-upload"></i> Upload selected PDFs</button>' +
        '<span class="sba-note" id="tdSbaUploadMsg"></span></div>' +
      '<h3 style="margin-top:22px;">Extracted sample questions (' + d.sample_questions.length + ')</h3>' +
      (d.sample_questions.length ? d.sample_questions.map(function (s) {
        return '<div class="sba-q"><div class="sba-q-head"><span class="sba-note">p. ' + esc(s.page || '?') + (s.question_type ? ' · ' + esc(s.question_type) : '') + (s.difficulty ? ' · ' + esc(s.difficulty) : '') + '</span>' +
          '<button class="td-btn td-btn-danger-outline td-btn-sm" data-sample-del="' + s.id + '">Remove</button></div><div>' + esc(s.text) + '</div></div>';
      }).join('') : '<p class="sba-note">Upload a sample-questions PDF to see the questions the AI will learn the style from.</p>');

    bind('tdSbaSave', saveSettings);
    bind('tdSbaUploadAll', uploadSelected);
    body.querySelectorAll('[data-doc-del]').forEach(function (b) {
      b.addEventListener('click', async function () {
        if (!confirm('Remove this document? It stays in history for answers already graded.')) return;
        try { await api('/documents/' + b.getAttribute('data-doc-del'), { method: 'DELETE' }); toast('Document removed.'); await open(state.detail.id, true); } catch (e) { toast(e.message, 'error'); }
      });
    });
    body.querySelectorAll('[data-doc-reprocess]').forEach(function (b) {
      b.addEventListener('click', async function () {
        try { await api('/documents/' + b.getAttribute('data-doc-reprocess') + '/reprocess', jsonOpts('POST')); await open(state.detail.id, true); } catch (e) { toast(e.message, 'error'); }
      });
    });
    body.querySelectorAll('[data-doc-replace]').forEach(function (b) {
      b.addEventListener('click', function () {
        var input = document.createElement('input');
        input.type = 'file';
        input.accept = 'application/pdf,.pdf';
        input.onchange = async function () {
          if (!input.files[0]) return;
          var fd = new FormData();
          fd.append('file', input.files[0]);
          try { await api('/documents/' + b.getAttribute('data-doc-replace') + '/replace', { method: 'POST', body: fd }); toast('New version uploaded. Processing…'); await open(state.detail.id, true); } catch (e) { toast(e.message, 'error'); }
        };
        input.click();
      });
    });
    body.querySelectorAll('[data-sample-del]').forEach(function (b) {
      b.addEventListener('click', async function () {
        try { await api('/samples/' + b.getAttribute('data-sample-del'), { method: 'DELETE' }); await open(state.detail.id, true); } catch (e) { toast(e.message, 'error'); }
      });
    });
  }

  function docRow(x) {
    var st = x.is_active ? badge(x.status) : badge('old', 'replaced');
    return '<div class="sba-doc" data-doc-id="' + x.id + '"><div class="sba-doc-name">' + esc(x.filename) + ' ' + st + '</div>' +
      '<div class="sba-doc-meta">v' + x.version + (x.page_count ? ' · ' + x.page_count + ' pages' : '') + (x.chunk_count ? ' · ' + x.chunk_count + ' sections' : '') +
      (x.status === 'ready' ? (x.embedded ? ' · semantic search' : ' · keyword search') : '') + ' · ' + esc(fmt(x.created_at)) + '</div>' +
      (x.error_message ? '<div class="sba-err">' + esc(x.error_message) + '</div>' : '') +
      ((x.warnings || []).length ? '<div class="sba-warn">' + x.warnings.map(esc).join('<br>') + '</div>' : '') +
      (x.is_active ? '<div class="sba-doc-actions"><button class="td-btn td-btn-outline td-btn-sm" data-doc-replace="' + x.id + '">Replace</button>' +
        (x.status === 'failed' ? '<button class="td-btn td-btn-outline td-btn-sm" data-doc-reprocess="' + x.id + '">Retry</button>' : '') +
        '<button class="td-btn td-btn-danger-outline td-btn-sm" data-doc-del="' + x.id + '">Remove</button></div>' : '') +
      '</div>';
  }

  async function uploadSelected() {
    var btn = $('tdSbaUploadAll');
    var msg = $('tdSbaUploadMsg');
    var pending = DOC_TYPES.map(function (t) {
      var input = $('sbaFile_' + t[0]);
      return { type: t[0], label: t[1], file: input && input.files && input.files[0] ? input.files[0] : null };
    }).filter(function (x) { return !!x.file; });
    if (!pending.length) {
      toast('Choose at least one PDF under Learning content, Sample questions, or Reference answers.', 'error');
      return;
    }
    if (btn) btn.disabled = true;
    if (msg) { msg.textContent = 'Uploading…'; msg.className = 'sba-note'; }
    var ok = 0;
    var errors = [];
    for (var i = 0; i < pending.length; i++) {
      var item = pending[i];
      var fd = new FormData();
      fd.append('doc_type', item.type);
      fd.append('file', item.file);
      try {
        await api('/assessments/' + state.detail.id + '/documents', { method: 'POST', body: fd });
        ok += 1;
      } catch (e) {
        errors.push(item.label + ': ' + (e.message || 'failed'));
      }
    }
    if (ok) toast(ok === 1 ? 'Uploaded. Processing…' : ok + ' PDFs uploaded. Processing…');
    if (errors.length) toast(errors.join(' · '), 'error');
    if (btn) btn.disabled = false;
    if (msg) msg.textContent = errors.length ? errors.join(' · ') : '';
    if (ok) await open(state.detail.id, true);
  }

  async function saveSettings() {
    var d = state.detail;
    var rubric = d.rubric.map(function (c, i) { return { key: c.key, label: c.label, weight: Number(document.querySelector('[data-rubric="' + i + '"]').value) }; });
    var payload = {
      title: $('sbaTitle').value, class_id: $('sbaClass').value ? +$('sbaClass').value : null, topic: $('sbaTopic').value,
      question_count: +$('sbaCount').value, difficulty: $('sbaDiff').value, max_score_per_question: +$('sbaMax').value,
      release_mode: $('sbaRelease').value, learning_objectives: $('sbaObjectives').value, instructions: $('sbaInstr').value, rubric: rubric
    };
    try {
      state.detail = await api('/assessments/' + d.id, jsonOpts('PUT', payload));
      toast('Settings saved.');
      render();
    } catch (e) { $('tdSbaSaveMsg').textContent = e.message; $('tdSbaSaveMsg').className = 'td-error'; }
  }

  /* ── questions tab ────────────────────────────────────────────────── */
  function renderQuestions() {
    var d = state.detail;
    var body = $('tdSbaTabBody');
    var ready = d.documents.some(function (x) { return x.is_active && x.doc_type === 'learning' && x.status === 'ready'; });
    var running = d.generation_status === 'running';
    var drafts = d.items.filter(function (q) { return q.status === 'draft'; });
    var approved = d.items.filter(function (q) { return q.status === 'approved'; });
    body.innerHTML =
      '<div class="sba-warn" style="margin-top:0;">' +
        '<b>How to publish:</b> 1) Generate questions → 2) Click <b>Approve</b> on each question you want students to see (or <b>Approve all drafts</b>) → 3) Click <b>Publish to class</b> at the top.' +
        (approved.length ? ' <span class="sba-note">' + approved.length + ' approved.</span>' : ' <b>None approved yet.</b>') +
      '</div>' +
      '<div class="sba-actions"><button class="td-btn td-btn-primary" id="tdSbaGenerate"' + (ready && !running ? '' : ' disabled') + '>' +
      (running ? '<i class="fas fa-spinner fa-spin"></i> Generating…' : '<i class="fas fa-wand-magic-sparkles"></i> Generate ' + d.question_count + ' questions') + '</button>' +
      (drafts.length ? '<button class="td-btn td-btn-green" id="tdSbaApproveAll"><i class="fas fa-check-double"></i> Approve all drafts (' + drafts.length + ')</button>' : '') +
      (ready ? '' : '<span class="sba-note">Upload and process a learning-content PDF first.</span>') +
      '<span class="sba-note">Drafts stay teacher-only until you approve them.</span></div>' +
      (d.generation_error ? '<div class="' + (d.generation_status === 'failed' ? 'sba-err' : 'sba-warn') + '">' + esc(d.generation_error) + '</div>' : '') +
      '<div id="tdSbaQs" style="margin-top:14px;">' + (d.items.length ? d.items.map(questionCard).join('') : '<p class="sba-note">No questions yet. Click Generate first.</p>') + '</div>';
    bind('tdSbaGenerate', async function () {
      await api('/assessments/' + d.id + '/generate', jsonOpts('POST', {}));
      toast('Generating questions… this can take a minute.');
      await open(d.id, true);
    });
    bind('tdSbaApproveAll', async function () {
      var btn = $('tdSbaApproveAll');
      if (btn) btn.disabled = true;
      var ok = 0;
      for (var i = 0; i < drafts.length; i++) {
        try {
          await api('/questions/' + drafts[i].id, jsonOpts('PUT', { status: 'approved' }));
          ok += 1;
        } catch (e) {
          toast((drafts[i].question_text || 'Question').slice(0, 40) + '…: ' + e.message, 'error');
        }
      }
      toast(ok ? (ok + ' question(s) approved. You can Publish to class now.') : 'No questions approved.');
      await open(d.id, true);
    });
    body.querySelectorAll('[data-q-act]').forEach(function (b) {
      b.addEventListener('click', function () { questionAction(+b.getAttribute('data-q'), b.getAttribute('data-q-act'), b); });
    });
  }

  function questionCard(q, i) {
    var meta = q.generation_meta || {};
    var issues = (meta.validation && meta.validation.issues) || [];
    if (state.editing[q.id]) {
      return '<div class="sba-q" data-q-card="' + q.id + '"><div class="sba-q-head"><b>Question ' + (i + 1) + ' (editing)</b></div>' +
        '<label class="sba-note" for="sbaEdS' + q.id + '">Scenario</label><textarea id="sbaEdS' + q.id + '">' + esc(q.scenario) + '</textarea>' +
        '<label class="sba-note" for="sbaEdQ' + q.id + '">Question</label><textarea id="sbaEdQ' + q.id + '" style="min-height:60px;">' + esc(q.question_text) + '</textarea>' +
        '<label class="sba-note" for="sbaEdC' + q.id + '">Grading points (teacher-only, one per line)</label><textarea id="sbaEdC' + q.id + '" style="min-height:60px;">' + esc((q.evaluation_criteria || []).join('\n')) + '</textarea>' +
        '<div class="sba-actions"><button class="td-btn td-btn-primary td-btn-sm" data-q="' + q.id + '" data-q-act="save">Save</button>' +
        '<button class="td-btn td-btn-outline td-btn-sm" data-q="' + q.id + '" data-q-act="cancel">Cancel</button></div></div>';
    }
    var locked = q.has_submissions;
    return '<div class="sba-q" data-q-card="' + q.id + '" data-q-status="' + q.status + '"><div class="sba-q-head"><b>Question ' + (i + 1) + '</b><span>' + badge(q.status) + ' ' + badge('old', q.difficulty) +
      (q.edited_by_teacher ? ' ' + badge('old', 'edited') : '') + (locked ? ' ' + badge('old', 'answered') : '') + '</span></div>' +
      '<div class="sba-q-scenario">' + esc(q.scenario) + '</div><div class="sba-q-text">' + esc(q.question_text) + '</div>' +
      '<div class="sba-q-meta"><b>Concepts:</b> ' + esc((q.concepts || []).join(', ') || '—') + '</div>' +
      '<div class="sba-q-meta"><b>Sources:</b> ' + ((q.source_refs || []).map(function (r) { return esc(pages(r) + (r.heading ? ' (' + r.heading + ')' : '')); }).join('; ') || '—') + '</div>' +
      '<details class="sba-q-meta"><summary>Grading points (teacher-only)</summary><ul class="sba-list">' + (q.evaluation_criteria || []).map(function (c) { return '<li>' + esc(c) + '</li>'; }).join('') + '</ul></details>' +
      (issues.length ? '<div class="sba-warn">Quality check: ' + issues.map(esc).join('; ') + '</div>' : '') +
      '<div class="sba-actions">' +
      (q.status !== 'approved' ? '<button class="td-btn td-btn-green td-btn-sm" data-q="' + q.id + '" data-q-act="approve"><i class="fas fa-check"></i> Approve for students</button>' : '<span class="sba-badge sba-b-approved">Approved for students</span>') +
      (q.status !== 'rejected' && !locked ? '<button class="td-btn td-btn-danger-outline td-btn-sm" data-q="' + q.id + '" data-q-act="reject">Reject</button>' : '') +
      (q.status === 'approved' && !locked ? '<button class="td-btn td-btn-outline td-btn-sm" data-q="' + q.id + '" data-q-act="draft">Back to draft</button>' : '') +
      (!locked ? '<button class="td-btn td-btn-outline td-btn-sm" data-q="' + q.id + '" data-q-act="edit">Edit</button>' : '') +
      (!locked ? '<button class="td-btn td-btn-outline td-btn-sm" data-q="' + q.id + '" data-q-act="regenerate"><i class="fas fa-rotate"></i> Regenerate</button>' : '') +
      '</div></div>';
  }

  async function questionAction(id, action, btn) {
    btn.disabled = true;
    try {
      if (action === 'edit') { state.editing[id] = true; renderQuestions(); return; }
      if (action === 'cancel') { delete state.editing[id]; renderQuestions(); return; }
      if (action === 'save') {
        await api('/questions/' + id, jsonOpts('PUT', { scenario: $('sbaEdS' + id).value, question_text: $('sbaEdQ' + id).value, evaluation_criteria: $('sbaEdC' + id).value }));
        delete state.editing[id];
        toast('Question saved.');
      } else if (action === 'regenerate') {
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Regenerating…';
        await api('/questions/' + id + '/regenerate', jsonOpts('POST'));
        toast('Question regenerated.');
      } else {
        var status = { approve: 'approved', reject: 'rejected', draft: 'draft' }[action];
        await api('/questions/' + id, jsonOpts('PUT', { status: status }));
      }
      await open(state.detail.id, true);
    } catch (e) { toast(e.message, 'error'); btn.disabled = false; }
  }

  /* ── submissions tab ──────────────────────────────────────────────── */
  async function loadSubs(silent) {
    try { state.subs = await api('/assessments/' + state.detail.id + '/submissions'); } catch (e) { state.subs = []; if (!silent) toast(e.message, 'error'); }
    if (state.sub) { try { state.sub = await api('/submissions/' + state.sub.id); } catch (e) { state.sub = null; } }
  }

  function renderSubs() {
    var body = $('tdSbaTabBody');
    var subs = state.subs || [];
    var table = subs.length ? '<table class="sba-table" id="tdSbaSubsTable"><thead><tr><th>Student</th><th>Q</th><th>Status</th><th>AI score</th><th>Final</th><th>Submitted</th></tr></thead><tbody>' +
      subs.map(function (s) {
        var final = s.teacher_score != null ? s.teacher_score : s.ai_score;
        return '<tr class="sba-click" data-sub="' + s.id + '"><td>' + esc(s.student_name) + '</td><td>' + esc(s.question_no || '?') + '</td><td>' + badge(s.status) +
          (s.review_required ? ' ' + badge('review', 'needs review') : (s.reviewed ? ' ' + badge('ready', 'reviewed') : '')) + '</td>' +
          '<td>' + (s.ai_score != null ? s.ai_score + ' / ' + s.max_score : '—') + '</td><td>' + (final != null ? '<b>' + final + '</b>' : '—') + '</td><td>' + esc(fmt(s.submitted_at)) + '</td></tr>';
      }).join('') + '</tbody></table>' : '<p class="sba-note">No submissions yet.</p>';
    body.innerHTML = '<div class="sba-row"><div>' + table + '</div><div id="tdSbaSubDetail">' + (state.sub ? subDetail(state.sub) : '<p class="sba-note">Select a submission to review it.</p>') + '</div></div>';
    body.querySelectorAll('[data-sub]').forEach(function (tr) {
      tr.addEventListener('click', async function () {
        try { state.sub = await api('/submissions/' + tr.getAttribute('data-sub')); } catch (e) { toast(e.message, 'error'); return; }
        renderSubs();
      });
    });
    bind('tdSbaSaveReview', saveReview);
    bind('tdSbaRetry', async function () {
      await api('/submissions/' + state.sub.id + '/retry', jsonOpts('POST'));
      toast('Re-evaluating…');
      await loadSubs(true);
      renderSubs();
      schedulePoll();
    });
  }

  function subDetail(s) {
    var ev = (s.evaluations || [])[0];
    var html = '<div class="td-card" id="tdSbaSubCard"><h3 style="margin-top:0;">' + esc(s.student_name) + '</h3>' +
      '<div class="sba-q-scenario">' + esc(s.question.scenario) + '</div><div class="sba-q-text">' + esc(s.question.question_text) + '</div>' +
      '<h4>Student answer</h4><div class="sba-excerpt" style="max-height:220px;" id="tdSbaAnswer">' + esc(s.answer_text || '(empty)') + '</div>';
    if (s.status === 'failed') html += '<div class="sba-err">Evaluation failed: ' + esc(s.error || 'unknown error') + '</div>';
    if (s.status === 'submitted' || s.status === 'evaluating') html += '<p class="sba-note"><i class="fas fa-spinner fa-spin"></i> AI evaluation in progress…</p>';
    if (ev) {
      var r = ev.result || {};
      html += '<h4>AI evaluation <span class="sba-note">(' + esc(ev.model_name || '') + ', prompt ' + esc(ev.prompt_version || '') + ', rubric v' + esc((ev.rubric_snapshot || {}).version) + ')</span></h4>' +
        '<div class="sba-score" id="tdSbaAiScore">' + ev.total_score + ' / ' + ev.max_score + '</div>' +
        (ev.review_reasons && ev.review_reasons.length ? '<div class="' + (ev.review_required ? 'sba-warn' : 'sba-note') + '">' + ev.review_reasons.map(esc).join('<br>') + '</div>' : '') +
        ev.criteria.map(function (c) {
          return '<div class="sba-crit"><span>' + esc(c.label) + ' (' + c.weight + '%) · level ' + c.level + '/4</span><b>' + c.score + ' / ' + c.max + '</b>' +
            '<div class="sba-bar"><span style="width:' + (c.max ? Math.round(100 * c.score / c.max) : 0) + '%"></span></div><small>' + esc(c.justification || '') + '</small></div>';
        }).join('') +
        list('Correct concepts', r.correct_concepts) + list('Missing concepts', r.missing_concepts) + list('Incorrect claims', r.incorrect_claims) +
        (r.reasoning_feedback ? '<h4>Feedback to student</h4><p>' + esc(r.reasoning_feedback) + '</p>' : '') +
        list('Suggested improvements', r.suggested_improvements) +
        '<h4>Supporting evidence</h4>' + ((r.supporting_references || []).length ? r.supporting_references.map(function (x) {
          return '<div class="sba-q-meta"><b>' + (x.doc_type === 'reference_answers' ? 'Reference answer' : 'Learning content') + '</b> v' + esc(x.doc_version) + ', ' + esc(pages(x)) +
            (x.heading ? ' — ' + esc(x.heading) : '') + (x.excerpt ? '<div class="sba-excerpt">' + esc(x.excerpt) + '</div>' : '') + '</div>';
        }).join('') : '<p class="sba-note">None cited.</p>') +
        '<h4>Teacher review</h4><div class="sba-form"><div><label for="tdSbaOverride">Final score (blank = keep AI score)</label><input id="tdSbaOverride" type="number" min="0" max="' + ev.max_score + '" step="0.5" value="' + (ev.teacher_score != null ? ev.teacher_score : '') + '"></div>' +
        '<div class="sba-wide"><label for="tdSbaTFeedback">Feedback to the student</label><textarea id="tdSbaTFeedback">' + esc(ev.teacher_feedback || '') + '</textarea></div></div>' +
        '<div class="sba-actions"><button class="td-btn td-btn-primary td-btn-sm" id="tdSbaSaveReview">' + (ev.reviewed_at ? 'Update review' : 'Save review') + '</button>' +
        (ev.reviewed_at ? '<span class="sba-note">Reviewed ' + esc(fmt(ev.reviewed_at)) + '</span>' : '') + '</div>' +
        (s.evaluations.length > 1 ? '<p class="sba-note">' + (s.evaluations.length - 1) + ' earlier evaluation(s) kept in history.</p>' : '');
    }
    if (s.status === 'failed' || s.status === 'evaluated') html += '<div class="sba-actions"><button class="td-btn td-btn-outline td-btn-sm" id="tdSbaRetry"><i class="fas fa-rotate"></i> Re-run AI evaluation</button></div>';
    return html + '</div>';
  }
  function list(title, items) {
    if (!items || !items.length) return '';
    return '<h4>' + esc(title) + '</h4><ul class="sba-list">' + items.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>';
  }
  async function saveReview() {
    var v = $('tdSbaOverride').value;
    await api('/submissions/' + state.sub.id + '/review', jsonOpts('POST', { teacher_score: v === '' ? null : Number(v), teacher_feedback: $('tdSbaTFeedback').value }));
    toast('Review saved. The student can now see the result.');
    await loadSubs(true);
    renderSubs();
  }

  window.tdScenario = { load: load, open: open, openCreate: openCreate, closeCreate: closeCreate, create: create };
})();
