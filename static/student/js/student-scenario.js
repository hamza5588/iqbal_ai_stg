/* Student → Scenario Assessments: assigned (published, class-enrolled) assessments, written answers
   with auto-saved drafts, submission, and released results with criterion-level feedback.
   API: /api/lms/scenario/student/*. Server strings are always escaped. */
(function () {
  var API = '/api/lms/scenario/student';
  var state = { list: null, detail: null, poll: null, saveTimers: {}, dirty: {} };

  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function toast(m, t) { if (typeof window.showToast === 'function') window.showToast(m, t || 'success'); }
  function badge(s, label) { return '<span class="sba-badge sba-b-' + esc(s) + '">' + esc(label) + '</span>'; }

  async function loadList() {
    stopPoll();
    $('sdSbaListCard').style.display = '';
    $('sdSbaDetail').style.display = 'none';
    state.detail = null;
    var box = $('sdSbaList');
    try {
      state.list = await lmsApi(API + '/assessments');
    } catch (e) {
      box.innerHTML = '<div class="sd-empty"><b>Could not load your assessments.</b>' + esc(e.message) + '</div>';
      return;
    }
    if (!state.list.length) {
      box.innerHTML = '<div class="sd-empty"><b>No scenario assessments yet</b>When your teacher publishes one for your class it will appear here.</div>';
      return;
    }
    box.innerHTML = '<table class="sba-table" id="sdSbaTable"><thead><tr><th>Assessment</th><th>Class</th><th>Progress</th><th></th></tr></thead><tbody>' +
      state.list.map(function (a) {
        var done = a.submitted_count >= a.question_count && a.question_count > 0;
        return '<tr><td><b>' + esc(a.title) + '</b>' + (a.topic ? '<br><small class="sd-note">' + esc(a.topic) + '</small>' : '') + '</td>' +
          '<td>' + esc(a.class_name || '') + '</td><td>' + a.submitted_count + ' / ' + a.question_count + ' answered</td>' +
          '<td><button class="sd-btn sd-btn-primary sd-btn-sm" data-sba-open="' + a.id + '">' + (done ? 'View results' : (a.submitted_count ? 'Continue' : 'Start')) + '</button></td></tr>';
      }).join('') + '</tbody></table>';
    box.querySelectorAll('[data-sba-open]').forEach(function (b) {
      b.addEventListener('click', function () { open(+b.getAttribute('data-sba-open')); });
    });
  }

  async function open(id, silent) {
    $('sdSbaListCard').style.display = 'none';
    var box = $('sdSbaDetail');
    box.style.display = '';
    if (!silent) box.innerHTML = '<div class="sd-card"><div class="sd-spinner"></div></div>';
    try {
      state.detail = await lmsApi(API + '/assessments/' + id);
    } catch (e) {
      box.innerHTML = '<div class="sd-card"><div class="sd-empty"><b>Could not open this assessment.</b>' + esc(e.message) + '</div></div>';
      return;
    }
    render();
    schedulePoll();
  }

  function stopPoll() { if (state.poll) { clearTimeout(state.poll); state.poll = null; } }
  function schedulePoll() {
    stopPoll();
    var d = state.detail;
    var waiting = d && d.items.some(function (i) { return i.submission && (i.submission.status === 'submitted' || i.submission.status === 'evaluating'); });
    if (waiting && window.sdCurrentView && window.sdCurrentView() === 'scenario') {
      state.poll = setTimeout(function () { if (state.detail && !Object.keys(state.dirty).length) open(state.detail.id, true); else schedulePoll(); }, 3000);
    }
  }

  function render() {
    var d = state.detail;
    $('sdSbaDetail').innerHTML =
      '<div class="sd-card"><a href="#scenario" class="sd-btn sd-btn-outline sd-btn-sm" id="sdSbaBack"><i class="fas fa-arrow-left"></i> All assessments</a>' +
      '<h2 style="margin:12px 0 4px;" id="sdSbaTitle">' + esc(d.title) + '</h2><p class="sd-desc">' + esc(d.class_name || '') + (d.topic ? ' · ' + esc(d.topic) : '') + '</p>' +
      (d.instructions ? '<p>' + esc(d.instructions) + '</p>' : '') +
      '<p class="sd-note">Graded on: ' + d.rubric.map(function (c) { return esc(c.label) + ' (' + c.weight + '%)'; }).join(', ') + '. Answer in your own words; you can submit each answer once.</p></div>' +
      d.items.map(itemCard).join('');
    $('sdSbaBack').addEventListener('click', function (e) { e.preventDefault(); loadList(); });
    $('sdSbaDetail').querySelectorAll('textarea[data-q]').forEach(function (ta) {
      ta.addEventListener('input', function () { queueSave(+ta.getAttribute('data-q'), ta.value); });
    });
    $('sdSbaDetail').querySelectorAll('[data-submit]').forEach(function (b) {
      b.addEventListener('click', function () { submit(+b.getAttribute('data-submit'), b); });
    });
  }

  function itemCard(item, i) {
    var sub = item.submission;
    var editable = !sub || sub.status === 'draft';
    var html = '<div class="sd-card sba-q" data-sba-item="' + item.id + '"><div class="sba-q-head"><b>Question ' + (i + 1) + '</b>' +
      '<span class="sd-note">' + esc(item.max_score) + ' marks · ' + esc(item.difficulty) + '</span></div>' +
      '<div class="sba-q-scenario">' + esc(item.scenario) + '</div><div class="sba-q-text">' + esc(item.question_text) + '</div>';
    if (editable) {
      html += '<label class="sd-note" for="sdSbaAns' + item.id + '">Your answer</label>' +
        '<textarea id="sdSbaAns' + item.id + '" data-q="' + item.id + '" maxlength="8000" style="min-height:160px;">' + esc(sub ? sub.answer_text : '') + '</textarea>' +
        '<div class="sba-actions"><button class="sd-btn sd-btn-primary sd-btn-sm" data-submit="' + item.id + '">Submit answer</button>' +
        '<span class="sd-note" id="sdSbaSave' + item.id + '">' + (sub && sub.answer_text ? 'Draft saved' : 'Drafts save automatically') + '</span></div>';
      return html + '</div>';
    }
    html += '<h4 style="margin:12px 0 4px;">Your answer</h4><div class="sba-excerpt" style="max-height:none;">' + esc(sub.answer_text || '(empty)') + '</div>';
    if (sub.status === 'submitted' || sub.status === 'evaluating') {
      html += '<p class="sd-note" data-sba-state="evaluating"><i class="fas fa-spinner fa-spin"></i> Your answer is being evaluated…</p>';
    } else if (sub.status === 'failed') {
      html += '<p class="sd-note" data-sba-state="failed">Your answer was submitted. Grading is delayed; your teacher will re-run it.</p>';
    } else if (sub.result) {
      html += resultBlock(sub.result);
    }
    return html + '</div>';
  }

  function resultBlock(r) {
    if (r.state === 'awaiting_release') return '<p class="sd-note" data-sba-state="awaiting_release">' + badge('awaiting_release', 'Submitted') + ' Your teacher will release results soon.</p>';
    if (r.state === 'under_review') return '<p class="sd-note" data-sba-state="under_review">' + badge('under_review', 'Under teacher review') + ' Your teacher is checking this grade.</p>';
    var html = '<div data-sba-state="released" style="margin-top:12px;"><div class="sba-score" data-sba-score>' + esc(r.score) + ' / ' + esc(r.max_score) + '</div>' +
      (r.teacher_adjusted ? '<div class="sd-note">Score set by your teacher.</div>' : '') +
      (r.teacher_feedback ? '<div class="sba-warn" style="background:#eff6ff;border-color:#bfdbfe;color:#1e3a8a;"><b>Teacher feedback:</b> ' + esc(r.teacher_feedback) + '</div>' : '') +
      r.criteria.map(function (c) {
        return '<div class="sba-crit"><span>' + esc(c.label) + ' (' + c.weight + '%)</span><b>' + c.score + ' / ' + c.max + '</b>' +
          '<div class="sba-bar"><span style="width:' + (c.max ? Math.round(100 * c.score / c.max) : 0) + '%"></span></div><small>' + esc(c.justification || '') + '</small></div>';
      }).join('') +
      (r.reasoning_feedback ? '<h4>Feedback</h4><p>' + esc(r.reasoning_feedback) + '</p>' : '') +
      li('What you got right', r.correct_concepts) + li('Concepts to work on', r.missing_concepts) + li('Statements to correct', r.incorrect_claims) +
      li('How to improve', r.suggested_improvements) +
      (r.references && r.references.length ? '<p class="sd-note">Review in your course material: ' + r.references.map(function (x) {
        return esc((x.page_start === x.page_end || !x.page_end ? 'p. ' + x.page_start : 'pp. ' + x.page_start + '–' + x.page_end) + (x.heading ? ' (' + x.heading + ')' : ''));
      }).join('; ') + '</p>' : '') + '</div>';
    return html;
  }
  function li(title, items) {
    if (!items || !items.length) return '';
    return '<h4>' + esc(title) + '</h4><ul class="sba-list">' + items.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>';
  }

  function queueSave(qid, text) {
    state.dirty[qid] = true;
    var el = $('sdSbaSave' + qid);
    if (el) el.textContent = 'Saving…';
    clearTimeout(state.saveTimers[qid]);
    state.saveTimers[qid] = setTimeout(function () { saveDraft(qid, text); }, 900);
  }
  async function saveDraft(qid, text) {
    var el = $('sdSbaSave' + qid);
    try {
      await lmsApi(API + '/questions/' + qid + '/draft', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ answer_text: text }) });
      delete state.dirty[qid];
      if (el) el.textContent = 'Draft saved';
    } catch (e) { if (el) el.textContent = 'Not saved: ' + e.message; }
  }

  async function submit(qid, btn) {
    var ta = $('sdSbaAns' + qid);
    var text = ta ? ta.value : '';
    if (!text.trim() && !confirm('Your answer is empty. Submit anyway? You will receive no marks.')) return;
    if (text.trim() && !confirm('Submit this answer? You cannot change it afterwards.')) return;
    clearTimeout(state.saveTimers[qid]);
    btn.disabled = true;
    try {
      await lmsApi(API + '/questions/' + qid + '/submit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ answer_text: text }) });
      delete state.dirty[qid];
      toast('Answer submitted.');
      await open(state.detail.id, true);
    } catch (e) { toast(e.message, 'error'); btn.disabled = false; }
  }

  window.sdViews = window.sdViews || {};
  window.sdViews.scenario = { onShow: function () { loadList(); } };
  window.sdScenario = { load: loadList, open: open };
})();
