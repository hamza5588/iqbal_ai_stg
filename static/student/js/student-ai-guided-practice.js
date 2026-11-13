/* 05 AI Guided Practice. Independent top-level tab: active Target PDF -> main topics -> subtopics.
   Same row/card layout as My Learning Path (student-learning-path.js) and the same question UI as
   Learning Chat (sd-q-option + lmsFeedback). The answer key and explanation are only sent by the
   server after an answer is locked in. API: /api/lms/ai-guided-practice/*. */
(function () {
  var state = { dash: null, error: null, open: {}, loading: false, history: null, pollTimer: null };
  var sess = { data: null, position: 0, busy: false, startedFor: null };
  var dwell = (typeof lmsQuestionDwell === 'function') ? lmsQuestionDwell() : null;

  function esc(s) { return escapeHtml(s == null ? '' : String(s)); }
  // Server *_render strings are MathJax-ready TEXT, not HTML: always escape them (same
  // pipeline as Learning Chat), so a PDF/LLM string like "a<b" or "<img onerror>" stays text.
  function rich(text, inline) {
    if (typeof window.lmsFormatRichText === 'function') return window.lmsFormatRichText(text == null ? '' : String(text), { inline: !!inline, quiz: true });
    return esc(text);
  }
  function typeset(el) { if (el && typeof window.lmsTypesetMath === 'function') window.lmsTypesetMath(el); }
  function pct(v) { return v == null ? '—' : Math.round(v) + '%'; }
  function pages(a, b) {
    if (!a) return '';
    return b && b !== a ? 'pp. ' + a + '–' + b : 'p. ' + a;
  }
  function newKey() {
    return 'k' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  }

  /* ── Dashboard ─────────────────────────────────────────────────────────── */

  async function loadDashboard(silent) {
    var box = document.getElementById('sdAigpTopics');
    if (!silent && box && !state.dash) box.innerHTML = '<div class="sd-spinner"></div>';
    state.loading = true;
    try {
      state.dash = await lmsApi('/api/lms/ai-guided-practice/dashboard');
      state.error = null;
    } catch (err) {
      state.error = err.message || 'Could not load AI Guided Practice.';
    }
    state.loading = false;
    window.sdAigpRender();
    schedulePoll();
  }

  function schedulePoll() {
    if (state.pollTimer) { clearTimeout(state.pollTimer); state.pollTimer = null; }
    var d = state.dash;
    var processing = d && (d.state === 'processing' || (d.processing || []).length);
    if (processing && window.sdCurrentView && window.sdCurrentView() === 'ai-guided-practice') {
      state.pollTimer = setTimeout(function () { loadDashboard(true); }, 8000);
    }
  }

  async function loadHistory() {
    var box = document.getElementById('sdAigpHistory');
    if (!box) return;
    try {
      state.history = await lmsApi('/api/lms/ai-guided-practice/history');
    } catch (err) {
      box.innerHTML = '<div class="sd-empty"><b>Could not load your practice history.</b>' + esc(err.message) + '</div>';
      return;
    }
    if (!state.history.length) {
      box.innerHTML = '<div class="sd-empty"><b>No practice sessions yet</b>Pick a subtopic above to start your first session.</div>';
      return;
    }
    box.innerHTML = '<table class="sd-mini"><thead><tr><th>Subtopic</th><th>Score</th><th>Correct</th><th>Completed</th></tr></thead><tbody>' +
      state.history.map(function (h) {
        return '<tr><td>' + esc(h.subtopic_title) +
          (h.main_topic_title && h.main_topic_title !== h.subtopic_title ? '<br><small class="sd-note">' + esc(h.main_topic_title) + '</small>' : '') +
          (h.is_current_pdf ? '' : '<br><small class="sd-note">Previous Target PDF (' + esc(h.pdf_filename || '') + ')</small>') +
          '</td><td><b class="' + window.sdPctClass(h.score_percent) + '">' + pct(h.score_percent) + '</b></td>' +
          '<td>' + (h.correct_count || 0) + ' / ' + (h.question_count || 0) + '</td>' +
          '<td>' + esc(h.completed_at ? window.sdFmtDateTime(h.completed_at) : '—') + '</td></tr>';
      }).join('') + '</tbody></table>';
  }

  function stateCard(icon, title, text, action) {
    return '<div class="sd-locked"><div class="sd-lock-ic"><i class="fas ' + icon + '"></i></div><h3>' + esc(title) + '</h3>' +
      '<p class="sd-desc">' + esc(text) + '</p>' + (action || '') + '</div>';
  }

  function summaryHtml(d) {
    var s = d.summary || {};
    var acc = s.questions_answered ? Math.round(100 * s.questions_correct / s.questions_answered) : null;
    var notes = '';
    if (d.using_previous_version) {
      notes += '<p class="sd-note"><i class="fas fa-info-circle"></i> A new Target PDF is being prepared; you are practising from the previous one for now.</p>';
    }
    (d.processing || []).forEach(function (p) {
      if (!d.using_previous_version && (d.sources || []).length) {
        notes += '<p class="sd-note"><i class="fas fa-sync-alt"></i> Preparing topics from ' + esc(p.filename || 'a new Target PDF') + '...</p>';
      }
    });
    return '<div class="sd-meta-line">' +
      '<div><span class="sd-label">Sessions completed</span><b>' + (s.sessions_completed || 0) + '</b></div>' +
      '<div><span class="sd-label">Questions answered</span><b>' + (s.questions_answered || 0) + '</b></div>' +
      '<div><span class="sd-label">Accuracy</span><b>' + (acc == null ? '—' : acc + '%') + '</b></div>' +
      (s.last_practiced_at ? '<div><span class="sd-label">Last practice</span><b><i class="far fa-calendar"></i> ' + esc(window.sdFmtDate(s.last_practiced_at)) + '</b></div>' : '') +
      '</div>' + notes;
  }

  function subtopicMatches(sub, topic, q, st) {
    if (st && sub.status !== st) return false;
    if (!q) return true;
    return String(sub.title || '').toLowerCase().indexOf(q) !== -1 ||
      String(topic.title || '').toLowerCase().indexOf(q) !== -1;
  }

  function subRowHtml(sub) {
    var p = sub.progress || {};
    var pill;
    if (sub.status === 'in_progress') pill = '<span class="sd-pill amber">In progress</span>';
    else if (sub.status === 'completed') pill = '<span class="sd-pill green">Practised</span>';
    else pill = '<span class="sd-pill grey">Not started</span>';
    var action;
    if (sub.insufficient_content && !sub.pool_size) {
      action = '<span class="sd-pill red" title="The Target PDF has too little material on this subtopic">Not enough PDF content</span>';
    } else {
      var label = sub.status === 'in_progress' ? 'Resume' : (sub.status === 'completed' ? 'Practice again' : 'Start practice');
      action = '<button type="button" class="sd-btn sd-btn-primary sd-btn-sm" data-testid="aigp-start" onclick="sdAigpStart(' + Number(sub.subtopic_id) + ')">' +
        label + ' <i class="fas fa-chevron-right"></i></button>';
    }
    var meta = '<div><span class="sd-label">Source</span><b>' + esc(pages(sub.page_start, sub.page_end) || 'Target PDF') + '</b></div>' +
      '<div><span class="sd-label">Sessions</span><b>' + (p.sessions_completed || 0) + '</b></div>' +
      '<div><span class="sd-label">Last score</span><b>' + pct(p.last_score) + '</b></div>' +
      '<div><span class="sd-label">Best</span><b>' + pct(p.best_score) + '</b></div>' +
      (p.last_practiced_at ? '<div><span class="sd-label">Last practice</span><b>' + esc(window.sdFmtDate(p.last_practiced_at)) + '</b></div>' : '');
    return '<div class="sd-list-row" data-aigp-subtopic="' + Number(sub.subtopic_id) + '" style="margin:8px 0 0;">' +
      '<div class="sd-num-bar ' + (sub.status === 'completed' ? 'grey' : 'purple') + '"><i class="fas fa-dumbbell"></i></div>' +
      '<div class="sd-row-body"><div class="sd-row-top"><div class="sd-title-chip"><div>' +
      '<h3>' + esc(sub.is_whole_section ? 'Whole topic' : sub.title) + '</h3>' +
      (sub.is_whole_section ? '<p class="sd-note" style="margin:2px 0 0;">The PDF lists no subtopics here, so practice covers the whole section.</p>' : '') +
      '<div class="sd-meta-line">' + meta + '</div></div></div>' +
      '<div class="sd-side-actions">' + pill + action + '</div></div></div></div>';
  }

  function topicHtml(topic, subs, forceOpen) {
    var open = forceOpen || !!state.open[topic.topic_id];
    var done = topic.subtopics_practised || 0;
    var total = topic.subtopics_total || 0;
    var progress = total ? Math.round(100 * done / total) : 0;
    return '<div class="sd-list-row" data-aigp-topic="' + Number(topic.topic_id) + '">' +
      '<div class="sd-num-bar ' + (done && done === total ? 'grey' : 'purple') + '"><i class="fas fa-book-open"></i></div>' +
      '<div class="sd-row-body"><div class="sd-row-top"><div class="sd-title-chip"><div class="sd-avatar-sm purple"><i class="fas fa-book-open"></i></div>' +
      '<div><h3>' + esc(topic.title) + '</h3><div class="sd-meta-line">' +
      '<div><span class="sd-label">Subtopics</span><b>' + total + '</b></div>' +
      '<div><span class="sd-label">Practised</span><b>' + done + ' / ' + total + '</b></div>' +
      (topic.page_start ? '<div><span class="sd-label">Pages</span><b>' + esc(pages(topic.page_start, topic.page_end)) + '</b></div>' : '') +
      '</div><span class="sd-progress-track" style="margin-top:8px;"><span class="sd-progress-fill' + (progress >= 70 ? ' green' : '') + '" style="width:' + progress + '%;"></span></span></div></div>' +
      '<div class="sd-side-actions"><button type="button" class="sd-btn sd-btn-outline blue sd-btn-sm" aria-expanded="' + open + '" onclick="sdAigpToggle(' + Number(topic.topic_id) + ')">' +
      (open ? 'Hide subtopics' : 'View subtopics') + ' <i class="fas fa-chevron-' + (open ? 'up' : 'down') + '"></i></button></div></div>' +
      (open ? '<div class="sd-subtopics">' + (subs.length ? subs.map(subRowHtml).join('') : '<p class="sd-note">No subtopics match your filter.</p>') + '</div>' : '') +
      '</div></div>';
  }

  window.sdAigpRender = function () {
    var box = document.getElementById('sdAigpTopics');
    var sumBox = document.getElementById('sdAigpSummary');
    if (!box) return;
    if (state.error && !state.dash) {
      box.innerHTML = stateCard('fa-wifi', 'Could not load AI Guided Practice', state.error,
        '<button type="button" class="sd-btn sd-btn-primary" style="margin-top:12px;" onclick="sdAigpReload()">Try again</button>');
      if (sumBox) sumBox.innerHTML = '';
      return;
    }
    var d = state.dash;
    if (!d) { box.innerHTML = '<div class="sd-spinner"></div>'; return; }
    if (sumBox) sumBox.innerHTML = d.state === 'ready' ? summaryHtml(d) : '';
    if (d.state === 'no_active_pdf') {
      box.innerHTML = stateCard('fa-file-pdf', 'No study material yet', 'Your teacher has not uploaded a Target PDF for your grade. Topics appear here as soon as they do.');
      return;
    }
    if (d.state === 'processing') {
      box.innerHTML = stateCard('fa-cog fa-spin', 'Preparing your topics', 'The Target PDF is being read to find its topics and subtopics. This page refreshes automatically.');
      return;
    }
    if (d.state === 'failed') {
      box.innerHTML = stateCard('fa-exclamation-triangle', 'Topics are not available yet', 'The Target PDF could not be processed. Your teacher has been notified in the admin panel.');
      return;
    }
    if (d.state === 'no_topics') {
      box.innerHTML = stateCard('fa-list', 'No topics found', 'No topics could be identified in the Target PDF yet.');
      return;
    }
    var q = ((document.getElementById('sdAigpSearch') || {}).value || '').trim().toLowerCase();
    var st = (document.getElementById('sdAigpStatus') || {}).value || '';
    var html = '';
    var multi = (d.sources || []).length > 1;
    (d.sources || []).forEach(function (src) {
      var rows = [];
      (src.topics || []).forEach(function (t) {
        var subs = (t.subtopics || []).filter(function (s) { return subtopicMatches(s, t, q, st); });
        if (!subs.length && (q || st)) return;
        rows.push(topicHtml(t, subs, !!(q || st)));
      });
      if (!rows.length) return;
      if (multi) html += '<div class="sd-block-title" style="margin:14px 0 6px;"><i class="fas fa-file-pdf"></i> ' + esc(src.filename || 'Target PDF') + '</div>';
      html += rows.join('');
    });
    box.innerHTML = html || '<div class="sd-empty"><b>No topics match your search.</b></div>';
  };

  window.sdAigpToggle = function (topicId) {
    state.open[topicId] = !state.open[topicId];
    window.sdAigpRender();
  };

  window.sdAigpReload = function () { loadDashboard(); loadHistory(); };

  /* ── Session ───────────────────────────────────────────────────────────── */

  function sessionBody() { return document.getElementById('sdAigpSessionBody'); }

  window.sdAigpStart = async function (subtopicId) {
    if (sess.busy) return;
    sess.busy = true;
    sess.data = null;
    if (typeof window.sdOpenAigpSessionView === 'function') window.sdOpenAigpSessionView();
    var body = sessionBody();
    if (body) {
      body.innerHTML = '<div class="sd-split-left" style="border:0;"><div class="sd-spinner"></div>' +
        '<p class="sd-desc" style="text-align:center;">Preparing your questions from the Target PDF... this can take a little while the first time.</p></div>';
    }
    // One key per click: a retried/double request returns the same session instead of a second one.
    var key = newKey();
    try {
      var data = await lmsApi('/api/lms/ai-guided-practice/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ subtopic_id: subtopicId, request_key: key })
      });
      if (!data || data.session === null || !data.items) {
        renderUnavailable(data || {});
        return;
      }
      sess.data = data;
      sess.position = data.current_position != null ? data.current_position : 0;
      if (data.resumed) showToast('Resuming your practice session', 'success', 2500);
      if (data.reused_count) showToast('You have practised most questions here — some are repeated, shuffled.', 'info', 4000);
      renderSession();
    } catch (err) {
      if (body) {
        body.innerHTML = '<div class="sd-split-left" style="border:0;"><div class="sd-empty"><b>Could not start practice</b>' + esc(err.message) + '</div>' +
          '<div class="sd-q-nav"><button type="button" class="sd-btn sd-btn-outline" onclick="sdAigpCloseSession()">Back</button>' +
          '<button type="button" class="sd-btn sd-btn-primary" onclick="sdAigpStart(' + Number(subtopicId) + ')">Try again</button></div></div>';
      }
    } finally {
      sess.busy = false;
    }
  };

  function renderUnavailable(data) {
    var body = sessionBody();
    if (!body) return;
    var title = data.reason === 'insufficient_content' ? 'Not enough content in the PDF' : 'Questions are not ready';
    body.innerHTML = '<div class="sd-split-left" style="border:0;">' +
      stateCard(data.reason === 'insufficient_content' ? 'fa-file-alt' : 'fa-exclamation-triangle', title, data.message || 'Please try again later.',
        '<button type="button" class="sd-btn sd-btn-primary" style="margin-top:12px;" onclick="sdAigpCloseSession()">Back to topics</button>') +
      '</div>';
    loadDashboard(true);
  }

  function currentItem() {
    var d = sess.data;
    return d && d.items ? d.items[sess.position] : null;
  }

  function optionHtml(item, o) {
    var cls = 'sd-q-option lms-quiz-option';
    if (item.answered) {
      if (o.position === item.correct_position) cls += ' selected lms-opt-correct';
      else if (o.position === item.selected_position) cls += ' lms-opt-wrong';
    }
    var attrs = item.answered ? ' disabled' : ' onclick="sdAigpAnswer(' + o.position + ')"';
    return '<button type="button" class="' + cls + '" data-position="' + o.position + '"' + attrs + '>' +
      '<span class="sd-radio"></span>' +
      '<span class="sd-opt-label lms-quiz-option-label">' + esc(o.label) + '.</span>' +
      '<span class="sd-opt-body lms-quiz-option-body">' + rich(o.render || o.text, true) + '</span></button>';
  }

  function feedbackHtml(item) {
    if (!item.answered) return '';
    var head = item.correct
      ? '<strong><i class="fas fa-check-circle"></i> Correct!</strong>'
      : '<strong><i class="fas fa-times-circle"></i> Not quite.</strong> The correct answer is ' + esc(item.correct_label || '') + '.';
    return '<div class="lms-card ' + (item.correct ? 'lms-card-ok' : 'lms-card-bad') + '" style="margin-top:12px;">' + head +
      (item.explanation ? '<div style="margin-top:8px;">' + rich(item.explanation_render || item.explanation, false) + '</div>' : '') +
      (item.source_page ? '<p class="sd-note" style="margin:8px 0 0;"><i class="fas fa-file-pdf"></i> Source: Target PDF, page ' + esc(item.source_page) + '</p>' : '') +
      '</div>';
  }

  function renderSession() {
    var d = sess.data;
    var body = sessionBody();
    if (!d || !body) return;
    var titleEl = document.getElementById('sdAigpSessionTitle');
    if (titleEl) titleEl.textContent = 'AI Guided Practice — ' + (d.is_whole_section ? d.main_topic_title : d.subtopic_title);
    if (d.status === 'completed') { renderCompletion(); return; }
    var item = currentItem();
    if (!item) { renderCompletion(); return; }
    var total = d.question_count || d.items.length;
    var progress = Math.round(100 * (d.answered_count || 0) / Math.max(total, 1));
    var nav = d.items.map(function (it) {
      var cls = 'sd-btn sd-btn-xs ' + (it.position === sess.position ? 'sd-btn-primary' : 'sd-btn-outline');
      var mark = it.answered ? (it.correct ? ' ✓' : ' ✗') : '';
      return '<button type="button" class="' + cls + '" aria-label="Question ' + (it.position + 1) + '" onclick="sdAigpGo(' + it.position + ')">' + (it.position + 1) + mark + '</button>';
    }).join(' ');
    var unanswered = d.items.filter(function (it) { return !it.answered; }).length;
    var isLast = sess.position >= d.items.length - 1;
    body.innerHTML =
      '<div class="sd-split-left" style="border:0;">' +
      '<div class="sd-track"><div class="sd-fill" style="width:' + progress + '%"></div></div>' +
      '<p class="sd-desc">Question ' + (sess.position + 1) + ' of ' + total +
      ' &nbsp;•&nbsp; <b style="color:var(--sd-blue-700);">' + esc(d.main_topic_title || '') + '</b>' +
      (d.is_whole_section ? '' : ' &rsaquo; <b>' + esc(d.subtopic_title || '') + '</b>') +
      ' &nbsp;•&nbsp; ' + (d.correct_count || 0) + ' correct</p>' +
      '<p class="sd-desc" style="font-size:12.5px;">Questions generated only from your teacher\'s Target PDF' + (d.pdf_filename ? ' (' + esc(d.pdf_filename) + ')' : '') + '.</p>' +
      '<div class="sd-q-box">' +
      '<div class="sd-q-text lms-quiz-stem">' + rich(item.question_render || item.question_text, false) + '</div>' +
      item.options.map(function (o) { return optionHtml(item, o); }).join('') +
      '<div id="sdAigpFeedback">' + feedbackHtml(item) + '</div></div>' +
      '<div class="sd-chip-row" style="margin-top:14px;flex-wrap:wrap;gap:6px;display:flex;">' + nav + '</div>' +
      '<div class="sd-q-nav">' +
      '<button type="button" class="sd-btn sd-btn-outline" ' + (sess.position === 0 ? 'disabled' : '') + ' onclick="sdAigpGo(' + (sess.position - 1) + ')"><i class="fas fa-chevron-left"></i> Previous</button>' +
      '<div class="sd-q-nav-end">' +
      (!isLast ? '<button type="button" class="sd-btn sd-btn-primary" onclick="sdAigpGo(' + (sess.position + 1) + ')">Next <i class="fas fa-chevron-right"></i></button>' : '') +
      (unanswered === 0 || isLast ? '<button type="button" class="sd-btn ' + (unanswered ? 'sd-btn-outline' : 'sd-btn-primary') + '" onclick="sdAigpFinish()">Finish session</button>' : '') +
      '</div></div></div>';
    typeset(body);
    if (dwell) {
      if (item.answered) dwell.stop();
      else dwell.switchTo('aigp-' + d.session_id + '-' + item.position);
    }
  }

  function renderCompletion() {
    var d = sess.data;
    var body = sessionBody();
    if (dwell) dwell.stop();
    if (!d || !body) return;
    var total = d.question_count || 0;
    var got = d.correct_count || 0;
    var answered = d.answered_count || 0;
    var aced = total > 0 && got === total;
    body.innerHTML =
      '<div class="sd-done-card" data-testid="aigp-complete">' +
      '<div class="sd-done-emoji">' + (aced ? '🏆' : '🎉') + '</div>' +
      '<h3>' + (aced ? 'Perfect score!' : 'Practice complete') + '</h3>' +
      '<p class="sd-desc">' + esc(d.main_topic_title || '') + (d.is_whole_section ? '' : ' › ' + esc(d.subtopic_title || '')) + '</p>' +
      '<div class="sd-score-big" style="margin:12px auto;"><b class="' + window.sdPctClass(d.score_percent) + '">' + pct(d.score_percent) + '</b><span>Score</span></div>' +
      '<table class="sd-mini" style="max-width:420px;margin:0 auto;"><tbody>' +
      '<tr><td>Questions attempted</td><td><b>' + answered + ' / ' + total + '</b></td></tr>' +
      '<tr><td>Correct</td><td><b>' + got + '</b></td></tr>' +
      '<tr><td>Incorrect</td><td><b>' + (d.incorrect_count || 0) + '</b></td></tr>' +
      '<tr><td>Status</td><td><b>Completed</b></td></tr></tbody></table>' +
      '<div class="sd-q-nav" style="justify-content:center;">' +
      '<button type="button" class="sd-btn sd-btn-outline" onclick="sdAigpCloseSession()">Back to topics</button>' +
      '<button type="button" class="sd-btn sd-btn-primary" data-testid="aigp-retry" onclick="sdAigpStart(' + Number(d.subtopic_id) + ')">Retry Practice</button>' +
      '</div></div>';
    if (!sess._celebrated) {
      sess._celebrated = d.session_id;
      try { lmsFeedback.celebrate(aced ? 'Perfect score!' : 'Nice work!'); } catch (e) { /* ignore */ }
    }
  }

  window.sdAigpGo = function (position) {
    var d = sess.data;
    if (!d || position < 0 || position >= d.items.length) return;
    sess.position = position;
    renderSession();
  };

  window.sdAigpAnswer = async function (optionPosition) {
    var d = sess.data;
    var item = currentItem();
    if (!d || !item || item.answered || sess.busy) return;
    sess.busy = true;
    var buttons = document.querySelectorAll('#sdAigpSessionBody .lms-quiz-option');
    buttons.forEach(function (b) { b.disabled = true; });
    var fb = document.getElementById('sdAigpFeedback');
    if (fb) fb.innerHTML = '<div class="sd-spinner" style="width:20px;height:20px"></div>';
    try {
      var r = await lmsApi('/api/lms/ai-guided-practice/sessions/' + d.session_id + '/answer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          position: item.position,
          option_position: optionPosition,
          time_spent_seconds: dwell ? dwell.seconds() : undefined
        })
      });
      Object.assign(item, {
        answered: true, correct: r.correct, selected_position: r.selected_position,
        correct_position: r.correct_position, correct_label: r.correct_label,
        explanation: r.explanation, explanation_render: r.explanation_render, source_page: r.source_page
      });
      Object.assign(d, r.session || {});
      if (typeof lmsFeedback !== 'undefined') {
        if (r.correct) lmsFeedback.celebrate(lmsFeedback.praise());
        else lmsFeedback.cue('error');
      }
      renderSession();
    } catch (err) {
      buttons.forEach(function (b) { b.disabled = false; });
      if (fb) fb.innerHTML = '<p class="lms-error">' + esc(err.message) + '</p>';
    } finally {
      sess.busy = false;
    }
  };

  window.sdAigpFinish = async function () {
    var d = sess.data;
    if (!d || sess.busy) return;
    var left = d.items.filter(function (it) { return !it.answered; }).length;
    if (left) {
      var msg = left + ' question(s) are unanswered. Finish anyway?';
      var ok = typeof showInAppConfirm === 'function'
        ? await showInAppConfirm(msg, { title: 'Finish session', confirmLabel: 'Finish', cancelLabel: 'Keep practising', iconClass: 'fas fa-flag-checkered' })
        : confirm(msg);
      if (!ok) return;
    }
    sess.busy = true;
    try {
      sess.data = await lmsApi('/api/lms/ai-guided-practice/sessions/' + d.session_id + '/complete', { method: 'POST' });
      renderCompletion();
    } catch (err) {
      showToast(err.message || 'Could not finish the session', 'error', 3500);
    } finally {
      sess.busy = false;
    }
  };

  window.sdAigpCloseSession = function () {
    if (dwell) dwell.stop();
    sess.data = null;
    sess._celebrated = null;
    // Returning to the tab re-runs its onShow, which reloads topics + history.
    if (typeof window.sdCloseAigpSessionView === 'function') window.sdCloseAigpSessionView();
  };

  document.addEventListener('visibilitychange', function () {
    if (!dwell || !sess.data) return;
    if (document.hidden) dwell.pause(); else dwell.resume();
  });

  window.sdViews = window.sdViews || {};
  window.sdViews['ai-guided-practice'] = {
    onShow: function () { loadDashboard(); loadHistory(); }
  };
  window.sdViews['ai-guided-session'] = {
    onShow: function () { if (!sess.data && !sess.busy) window.sdAigpCloseSession(); }
  };
})();
