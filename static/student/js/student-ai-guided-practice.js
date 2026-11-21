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
        '<p class="sd-desc" style="text-align:center;">Getting your practice questions ready... Please wait a moment.</p></div>';
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
    var title = data.reason === 'insufficient_content' ? 'Not enough content in the PDF' :
      (data.reason === 'no_bank_questions' ? 'No questions in the question bank yet' : 'Questions are not ready');
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
    // Learning-Chat style: a wrong pick is marked and the student tries again; the
    // correct option is only revealed once they find it themselves.
    var cls = 'sd-q-option lms-quiz-option';
    var tag = '';
    var wrong = (item.wrong_positions || []).indexOf(o.position) !== -1;
    var clickable = !item.solved && !wrong;
    if (item.solved && o.position === item.correct_position) {
      cls += ' selected lms-opt-correct';
      tag = 'Correct ✓';
    } else if (wrong) {
      cls += ' lms-opt-wrong';
      tag = 'Try again';
    }
    var attrs = clickable ? ' onclick="sdAigpAnswer(' + o.position + ')"' : ' disabled';
    return '<button type="button" class="' + cls + '" data-position="' + o.position + '"' + attrs + '>' +
      '<span class="sd-radio"></span>' +
      '<span class="sd-opt-label lms-quiz-option-label">' + esc(o.label) + '.</span>' +
      '<span class="sd-opt-body lms-quiz-option-body">' + (item.render_html ? (o.render || esc(o.text)) : rich(o.render || o.text, true)) + '</span>' +
      (tag ? '<span class="sd-pill ' + (item.solved ? 'green' : 'red') +
        ' round" style="margin-left:auto;white-space:nowrap;">' + esc(tag) + '</span>' : '') +
      '</button>';
  }

  function feedbackHtml(item) {
    if (item.solved) {
      return '<div class="lms-card lms-card-ok" style="margin-top:12px;"><strong><i class="fas fa-check-circle"></i> Correct!</strong>' +
        ((item.wrong_positions || []).length ? ' You found it - well done for sticking with it.' : '') +
        (item.explanation ? '<div style="margin-top:8px;">' + rich(item.explanation_render || item.explanation, false) + '</div>' : '') +
        '<p class="sd-note" style="margin:8px 0 0;">Ask the AI helper for the complete solution.</p></div>';
    }
    if ((item.wrong_positions || []).length) {
      return '<div class="lms-card lms-card-bad" style="margin-top:12px;"><strong><i class="fas fa-times-circle"></i> Not quite.</strong> ' +
        'Take another look, or ask the AI helper for a hint.</div>';
    }
    return '';
  }

  function sourceNote(d) {
    var file = d.pdf_filename ? ' (' + esc(d.pdf_filename) + ')' : '';
    if (d.question_source === 'bank') return 'All questions are taken from your teacher\'s question bank' + file + '.';
    if (d.question_source === 'mixed') return 'Questions from your teacher\'s question bank' + file + ', plus practice questions based on it.';
    return 'Practice questions made from your teacher\'s Target PDF' + file + '.';
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
    var firstOpen = d.items.findIndex(function (it) { return !it.solved; });
    var reachable = firstOpen === -1 ? d.items.length - 1 : firstOpen;
    var nav = d.items.map(function (it) {
      var cls = 'sd-btn sd-btn-xs ' + (it.position === sess.position ? 'sd-btn-primary' : 'sd-btn-outline');
      var mark = it.solved ? ' ✓' : '';
      var locked = it.position > reachable;  // solve the current question first
      return '<button type="button" class="' + cls + '" aria-label="Question ' + (it.position + 1) + '"' +
        (locked ? ' disabled title="Solve the current question first"' : ' onclick="sdAigpGo(' + it.position + ')"') + '>' +
        (it.position + 1) + mark + '</button>';
    }).join(' ');
    var unanswered = d.items.filter(function (it) { return !it.solved; }).length;
    var isLast = sess.position >= d.items.length - 1;
    body.innerHTML =
      '<div class="sd-split-body"><div class="sd-split-left">' +
      '<div class="sd-track"><div class="sd-fill" style="width:' + progress + '%"></div></div>' +
      '<p class="sd-desc">Question ' + (sess.position + 1) + ' of ' + total +
      ' &nbsp;•&nbsp; <b style="color:var(--sd-blue-700);">' + esc(d.main_topic_title || '') + '</b>' +
      (d.is_whole_section ? '' : ' &rsaquo; <b>' + esc(d.subtopic_title || '') + '</b>') +
      ' &nbsp;•&nbsp; ' + (d.correct_count || 0) + ' correct</p>' +
      '<p class="sd-desc" style="font-size:12.5px;">' + sourceNote(d) + '</p>' +
      '<div class="sd-q-box">' +
      '<div class="sd-q-text lms-quiz-stem">' + (item.render_html ? (item.question_render || esc(item.question_text)) : rich(item.question_render || item.question_text, false)) + '</div>' +
      item.options.map(function (o) { return optionHtml(item, o); }).join('') +
      '<div id="sdAigpFeedback">' + feedbackHtml(item) + '</div></div>' +
      '<div class="sd-chip-row" style="margin-top:14px;flex-wrap:wrap;gap:6px;display:flex;">' + nav + '</div>' +
      '<div class="sd-q-nav">' +
      '<button type="button" class="sd-btn sd-btn-outline" ' + (sess.position === 0 ? 'disabled' : '') + ' onclick="sdAigpGo(' + (sess.position - 1) + ')"><i class="fas fa-chevron-left"></i> Previous</button>' +
      '<div class="sd-q-nav-end">' +
      (!isLast ? '<button type="button" class="sd-btn sd-btn-primary"' +
        (item.solved ? ' onclick="sdAigpGo(' + (sess.position + 1) + ')"' : ' disabled title="Choose the correct option to continue"') +
        '>Next <i class="fas fa-chevron-right"></i></button>' : '') +
      (unanswered === 0 ? '<button type="button" class="sd-btn sd-btn-primary" onclick="sdAigpFinish()">Finish session</button>' : '') +
      '</div></div></div>' +
      '<div class="sd-split-right">' + tutorPanelHtml(item) + '</div></div>';
    typeset(body);
    bindTutor(item);
    if (dwell) {
      if (item.solved) dwell.stop();
      else dwell.switchTo('aigp-' + d.session_id + '-' + item.position);
    }
  }

  /* ── AI tutor chat beside the question (same look + tutor as Learning Chat) ── */
  var TUTOR_PROMPTS = [
    ['Simpler words', 'Explain this in simpler words'],
    ['Break into steps', 'Break it into small steps'],
    ['Give a hint', 'Give me a hint'],
    ['Show example', 'Show me a similar example for this question'],
    ["I don't understand", "I still don't understand"],
    ['اردو میں', 'Please explain this in Urdu']
  ];
  var TUTOR_ANSWERED_PROMPTS = [
    ['Explain again', 'Explain the complete solution again'],
    ['Why not others?', 'Why are the other options wrong?'],
    ['Key rule', 'What key rule did I use here?'],
    ['اردو میں', 'Please explain this in Urdu']
  ];
  var MODE_B_KICKOFF = 'Why is my answer correct?';
  sess.tutorLoading = {};
  sess.drafts = {};

  function botHtml(text) {
    if (typeof window.lmsFormatRichText === 'function') return window.lmsFormatRichText(text || '', {});
    return esc(text).replace(/\n/g, '<br>');
  }

  function tutorPanelHtml(item) {
    var msgs = item.tutor || [];
    var loading = !!sess.tutorLoading[item.position];
    var greeting = item.solved
      ? 'Well done! Ask me for the complete solution, or why the other options are wrong.'
      : "Tell me what you think about this question. I'll respond to your idea and help you work it out - without giving away the answer.";
    var html = msgs.length ? msgs.map(function (m) {
      var user = m.role === 'user';
      return '<div class="sd-ai-bubble' + (user ? ' user' : '') + '"><div class="sd-bot-ic">' + (user ? 'You' : 'AI') + '</div>' +
        '<div class="sd-msg"' + (user ? ' dir="auto"' : '') + '>' + (user ? esc(m.content) : botHtml(m.content)) +
        (m.failed ? '<div class="sd-msg-tools"><button type="button" onclick="sdAigpTutorRetry(' + item.position + ')">Try again</button></div>' : '') +
        '</div></div>';
    }).join('') : '<div class="sd-ai-bubble"><div class="sd-bot-ic">AI</div><div class="sd-msg">' + esc(greeting) + '</div></div>';
    if (loading) {
      html += '<div class="sd-ai-bubble"><div class="sd-bot-ic">AI</div><div class="sd-msg"><span class="sd-typing"><span></span><span></span><span></span></span>' +
        '<span class="sd-hint-line" style="margin-left:8px;">Thinking...</span></div></div>';
    }
    var prompts = (item.solved ? TUTOR_ANSWERED_PROMPTS : TUTOR_PROMPTS).map(function (p) {
      return '<button type="button" class="sd-chip"' + (loading ? ' disabled' : '') + ' data-prompt="' + esc(p[1]) + '">' + esc(p[0]) + '</button>';
    }).join('');
    var dis = loading ? ' disabled' : '';
    return '<div class="sd-chat-scroll" id="sdAigpTutorMessages">' + html + '</div>' +
      '<div class="sd-chip-row" id="sdAigpTutorPrompts">' + prompts + '</div>' +
      '<div class="sd-chat-inputbar"><div class="sd-chat-field">' +
      '<textarea id="sdAigpTutorInput" rows="1" maxlength="1000" dir="auto" placeholder="' +
      (item.solved ? 'Ask about this question...' : "I'm stuck on this step... (Enter to send)") + '"' + dis + '>' +
      esc(sess.drafts[item.position] || '') + '</textarea>' +
      '<button type="button" class="sd-field-ic bulb" title="Need more help" aria-label="Need more help" data-prompt="I still need help with this question."' + dis + '><i class="far fa-lightbulb"></i></button>' +
      '</div><button type="button" class="sd-send-btn" id="sdAigpTutorSend"' + dis + '>Send</button></div>' +
      '<p class="sd-hint-line">Press Enter to send · Shift+Enter for new line</p>';
  }

  function bindTutor(item) {
    var box = document.getElementById('sdAigpTutorMessages');
    if (box) { box.scrollTop = box.scrollHeight; typeset(box); }
    var input = document.getElementById('sdAigpTutorInput');
    if (input) {
      input.addEventListener('input', function () { sess.drafts[item.position] = input.value; });
      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); window.sdAigpTutorSend(); }
      });
    }
    var send = document.getElementById('sdAigpTutorSend');
    if (send) send.addEventListener('click', function () { window.sdAigpTutorSend(); });
    document.querySelectorAll('#view-ai-guided-session [data-prompt]').forEach(function (b) {
      b.addEventListener('click', function () { window.sdAigpTutorSend(b.getAttribute('data-prompt')); });
    });
  }

  window.sdAigpTutorSend = async function (text) {
    var d = sess.data;
    var item = currentItem();
    if (!d || !item || sess.tutorLoading[item.position]) return;
    var input = document.getElementById('sdAigpTutorInput');
    var msg = (text != null ? String(text) : (input ? input.value : '')).trim();
    if (!msg) return;
    var pos = item.position;
    item.tutor = (item.tutor || []).filter(function (m) { return !m.failed; });
    item.tutor.push({ role: 'user', content: msg });
    sess.drafts[pos] = '';
    sess.tutorLoading[pos] = true;
    sess.lastTutorMsg = msg;
    renderSession();
    try {
      var r = await lmsApi('/api/lms/ai-guided-practice/sessions/' + d.session_id + '/tutor', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ position: pos, message: msg })
      });
      item.tutor = r.messages || item.tutor.concat([{ role: 'assistant', content: r.reply }]);
    } catch (err) {
      item.tutor.push({ role: 'assistant', content: err.message || 'Something went wrong. Please try again.', failed: true });
    } finally {
      sess.tutorLoading[pos] = false;
      if (sess.data === d && currentItem() && currentItem().position === pos) renderSession();
    }
  };

  window.sdAigpTutorRetry = function (pos) {
    var item = currentItem();
    if (!item || item.position !== pos) return;
    item.tutor = (item.tutor || []).filter(function (m) { return !m.failed; });
    var last = null;
    if (item.tutor.length && item.tutor[item.tutor.length - 1].role === 'user') last = item.tutor.pop().content;
    window.sdAigpTutorSend(last || sess.lastTutorMsg);
  };

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
      '<tr><td>Correct on first try</td><td><b>' + got + '</b></td></tr>' +
      '<tr><td>Needed another try</td><td><b>' + (d.incorrect_count || 0) + '</b></td></tr>' +
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
    var firstOpen = d.items.findIndex(function (it) { return !it.solved; });
    if (firstOpen !== -1 && position > firstOpen) return;
    sess.position = position;
    renderSession();
  };

  window.sdAigpAnswer = async function (optionPosition) {
    var d = sess.data;
    var item = currentItem();
    if (!d || !item || item.solved || sess.busy) return;
    if ((item.wrong_positions || []).indexOf(optionPosition) !== -1) return;
    sess.busy = true;
    var buttons = document.querySelectorAll('#sdAigpSessionBody .lms-quiz-option');
    buttons.forEach(function (b) { b.disabled = true; });
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
      Object.assign(d, r.session || {});
      if (!item.answered) item.first_try_correct = !!r.correct;
      item.answered = true;
      if (r.correct) {
        Object.assign(item, {
          solved: true, correct: true, correct_position: r.correct_position, correct_label: r.correct_label,
          explanation: r.explanation, explanation_render: r.explanation_render, source_page: r.source_page
        });
        if (typeof lmsFeedback !== 'undefined') lmsFeedback.celebrate(lmsFeedback.praise());
        renderSession();
        // Like Learning Chat: once solved, the AI helper explains the complete solution.
        if (!(item.tutor || []).some(function (m) { return m.content === MODE_B_KICKOFF; })) {
          window.sdAigpTutorSend(MODE_B_KICKOFF);
        }
      } else {
        item.wrong_positions = r.wrong_positions || (item.wrong_positions || []).concat([optionPosition]);
        if (typeof lmsFeedback !== 'undefined') lmsFeedback.cue('error');
        if (typeof showToast === 'function') showToast('Not quite — take another look, or ask the AI helper for a hint.', 'error', 3000);
        renderSession();
        var wrongBtn = document.querySelector('#sdAigpSessionBody .lms-quiz-option[data-position="' + optionPosition + '"]');
        if (wrongBtn) {
          wrongBtn.classList.add('lms-shake');
          setTimeout(function () { wrongBtn.classList.remove('lms-shake'); }, 500);
        }
      }
    } catch (err) {
      buttons.forEach(function (b) { b.disabled = false; });
      var fb = document.getElementById('sdAigpFeedback');
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
