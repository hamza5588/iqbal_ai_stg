/** Student LMS — diagnostic, learning path actions */
(function () {
  function fmtText(text, inline) {
    if (typeof window.lmsFormatRichText === 'function') {
        return window.lmsFormatRichText(text, inline ? { inline: true, quiz: true } : { quiz: true });
    }
    return escapeHtml(text == null ? '' : String(text));
  }
  function fmtOption(opt) {
    // The server now sends a delimiter-wrapped `render` string for
    // diagnostic options - use it verbatim so MathJax always typesets it.
    if (opt && typeof opt.render === 'string' && opt.render) {
      return fmtText(opt.render, true);
    }
    var raw = (typeof window.lmsOptionText === 'function')
      ? window.lmsOptionText(opt)
      : ((opt && (opt.text || opt.latex)) || '');
    return fmtText(raw, true);
  }
  function fmtQuestion(q) {
    if (q && typeof q.question_render === 'string' && q.question_render) {
      return fmtText(q.question_render, false);
    }
    var raw = (typeof window.lmsQuestionText === 'function')
      ? window.lmsQuestionText(q)
      : ((q && (q.question_text || q.question_latex)) || '');
    return fmtText(raw, false);
  }
  function typeset(el) {
    if (!el) return Promise.resolve();
    if (typeof window.lmsTypesetMath === 'function') return window.lmsTypesetMath(el);
    el.classList.add('tex2jax_process');
    if (window.MathJax && window.MathJax.typesetPromise) {
      return window.MathJax.typesetPromise([el]).catch(function () {});
    }
    return Promise.resolve();
  }

  var diagState = {
    assessmentId: null,
    attemptId: null,
    questions: [],
    current: 0,
    answers: {},
    expiresAt: null,
    remainingSeconds: null,
    timerInterval: null
  };

  /* ---- Local answer backup (DIL feedback: don't lose answers when the
     connection drops). Every pick is written to localStorage synchronously,
     BEFORE the network call - so it survives a lost connection, a reload,
     or the tab being closed, even if the server call never lands. A queue
     of not-yet-synced answers is retried on reconnect, periodically while
     the diagnostic is open, and right before submit. ---- */
  function diagLsKey(attemptId) { return 'lmsDiagPending:' + attemptId; }

  function loadDiagLocal(attemptId) {
    try {
      var raw = localStorage.getItem(diagLsKey(attemptId));
      return raw ? JSON.parse(raw) : {};
    } catch (e) { return {}; }
  }

  function saveDiagLocal(attemptId, data) {
    try { localStorage.setItem(diagLsKey(attemptId), JSON.stringify(data)); } catch (e) { /* storage full/blocked - best effort only */ }
  }

  function persistAnswerLocally(attemptId, qid, idx) {
    if (!attemptId || qid == null) return;
    var data = loadDiagLocal(attemptId);
    data[qid] = { idx: idx, synced: false, ts: Date.now() };
    saveDiagLocal(attemptId, data);
  }

  function markDiagAnswerSynced(attemptId, qid) {
    if (!attemptId || qid == null) return;
    var data = loadDiagLocal(attemptId);
    if (data[qid]) {
      data[qid].synced = true;
      saveDiagLocal(attemptId, data);
    }
  }

  function clearDiagLocal(attemptId) {
    if (!attemptId) return;
    try { localStorage.removeItem(diagLsKey(attemptId)); } catch (e) { /* ignore */ }
  }

  var _diagFlushInFlight = false;
  async function flushDiagPendingAnswers() {
    var attemptId = diagState.attemptId;
    if (!attemptId || _diagFlushInFlight) return;
    var data = loadDiagLocal(attemptId);
    var pendingQids = Object.keys(data).filter(function (qid) { return data[qid] && !data[qid].synced; });
    if (!pendingQids.length) return;
    _diagFlushInFlight = true;
    try {
      for (var i = 0; i < pendingQids.length; i++) {
        var qid = pendingQids[i];
        try {
          await lmsApi('/api/lms/attempts/' + attemptId + '/answer', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question_id: parseInt(qid, 10), selected_option_index: data[qid].idx })
          });
          markDiagAnswerSynced(attemptId, qid);
        } catch (e) { /* still offline / server hiccup - stays queued, try again next time */ }
      }
    } finally {
      _diagFlushInFlight = false;
    }
  }
  // Reconnect trigger: as soon as the browser reports the connection back,
  // push anything that piled up locally while it was down.
  if (typeof window !== 'undefined' && window.addEventListener) {
    window.addEventListener('online', function () {
      if (diagState.attemptId) flushDiagPendingAnswers();
    });
  }

  function ensureDiagnosticModal() {
    if (document.getElementById('lmsDiagnosticModal')) return;
    var html = '<div id="lmsDiagnosticModal" class="lms-modal-backdrop">' +
      '<div class="lms-modal lms-modal-lg">' +
      '<div class="lms-modal-header"><h2>Diagnostic Assessment</h2>' +
      '<div id="lmsDiagTimer" class="lms-diag-timer" style="display:none;margin-left:auto;margin-right:12px;font-weight:700;font-variant-numeric:tabular-nums;color:var(--primary-color);"></div>' +
      '<button type="button" class="lms-modal-close" id="lmsDiagCloseBtn" onclick="closeLmsDiagnostic()">&times;</button></div>' +
      '<div class="lms-modal-body" id="lmsDiagBody"><div class="lms-spinner"></div></div>' +
      '</div></div>';
    document.body.insertAdjacentHTML('beforeend', html);
  }

  window.setLmsDiagnosticGate = function (locked) {
    window._lmsDiagnosticMandatory = !!locked;
    if (locked) window._lmsDiagnosticAllowClose = false;
    ensureDiagnosticModal();
    var modal = document.getElementById('lmsDiagnosticModal');
    var closeBtn = document.getElementById('lmsDiagCloseBtn');
    var fab = document.querySelector('.lms-fab-bar');
    if (modal) {
      modal.onclick = locked
        ? null
        : function (e) { if (e.target === modal) closeLmsDiagnostic(); };
    }
    if (closeBtn) closeBtn.style.display = locked ? 'none' : '';
    if (fab) fab.style.display = locked ? 'none' : '';
    document.body.classList.toggle('lms-diagnostic-gate-active', locked);
  };

  window.lmsStudentNeedsDiagnostic = function () {
    return !!window._lmsNeedsDiagnostic;
  };

  function unlockDiagnosticGate() {
    window._lmsNeedsDiagnostic = false;
    window._lmsDiagnosticAllowClose = true;
    if (typeof setLmsDiagnosticGate === 'function') setLmsDiagnosticGate(false);
    if (typeof loadLmsStudentDashboard === 'function') loadLmsStudentDashboard();
  }

  function renderDiagnosticTimeOver(result) {
    clearDiagTimer();
    // A timed-out attempt now keeps the score for every answered question -
    // if we have a real result, show the normal results screen with a
    // "time ran out" note instead of a dead-end "you scored 0" card.
    if (result && result.max_score != null && result.score != null) {
      renderDiagnosticResults(result, { timedOut: true });
      unlockDiagnosticGate();
      return;
    }
    var body = document.getElementById('lmsDiagBody');
    var timerEl = document.getElementById('lmsDiagTimer');
    if (timerEl) timerEl.style.display = 'none';
    var msg = (result && (result.message || result.diagnostic_timeout_message)) ||
      'Time is up. Your diagnostic was submitted automatically.';
    body.innerHTML =
      '<div class="lms-card lms-diag-timeover">' +
      '<p class="lms-diag-timeover-title">Time is up</p>' +
      '<p>' + escapeHtml(msg) + '</p>' +
      '<div class="lms-modal-footer" style="border:none;padding-top:16px;display:flex;gap:8px;flex-wrap:wrap;">' +
      '<button type="button" class="lms-btn lms-btn-primary" onclick="closeLmsDiagnostic()">Continue</button>' +
      '</div></div>';
    unlockDiagnosticGate();
  }

  function clearDiagTimer() {
    if (diagState.timerInterval) {
      clearInterval(diagState.timerInterval);
      diagState.timerInterval = null;
    }
  }

  function formatDiagCountdown(secs) {
    var total = Math.max(0, Math.floor(Number(secs) || 0));
    var m = Math.floor(total / 60);
    var s = total % 60;
    return m + ':' + (s < 10 ? '0' : '') + s;
  }

  function updateTimerDisplay() {
    var el = document.getElementById('lmsDiagTimer');
    if (!el || diagState.remainingSeconds == null) return;
    if (diagState.remainingSeconds <= 60) {
      el.style.color = '#dc2626';
    } else {
      el.style.color = 'var(--primary-color)';
    }
    el.textContent = formatDiagCountdown(diagState.remainingSeconds);
    el.style.display = 'block';
  }

  function startDiagTimer() {
    clearDiagTimer();
    if (diagState.remainingSeconds == null) return;
    updateTimerDisplay();
    var _diagTick = 0;
    diagState.timerInterval = setInterval(function () {
      if (diagState.remainingSeconds != null && diagState.remainingSeconds > 0) {
        diagState.remainingSeconds = Math.max(0, diagState.remainingSeconds - 1);
      }
      updateTimerDisplay();
      // Retry any answer still stuck in the local queue every ~15s, not
      // just on the browser's "online" event (some networks flap without
      // firing it reliably).
      _diagTick++;
      if (_diagTick % 15 === 0) flushDiagPendingAnswers();
      if (diagState.remainingSeconds <= 0) {
        clearDiagTimer();
        submitLmsDiagnostic(true);
      }
    }, 1000);
  }

  window.openLmsDiagnostic = async function () {
    ensureDiagnosticModal();
    if (window._lmsNeedsDiagnostic) setLmsDiagnosticGate(true);
    lmsOpenModal('lmsDiagnosticModal');
    var body = document.getElementById('lmsDiagBody');
    body.innerHTML = '<div class="lms-spinner"></div><p class="lms-status" style="text-align:center">Loading diagnostic...</p>';
    try {
      var diag = await lmsApi('/api/lms/diagnostics/default');
      if ((diag.diagnostic_completed || diag.any_diagnostic_completed || diag.diagnostic_timed_out) && diag.latest_attempt_id) {
        try {
          var prevResult = await lmsApi('/api/lms/attempts/' + diag.latest_attempt_id + '/results');
          renderDiagnosticResults(prevResult, { timedOut: !!prevResult.timed_out, alreadyDone: true });
          unlockDiagnosticGate();
          return;
        } catch (e) { /* fall through to the generic message */ }
      }
      if (diag.diagnostic_timed_out || diag.time_over) {
        renderDiagnosticTimeOver(diag);
        return;
      }
      if (diag.diagnostic_completed || diag.any_diagnostic_completed) {
        body.innerHTML = '<div class="lms-card"><p>You have already completed the diagnostic assessment' +
          (diag.title ? ': <strong>' + escapeHtml(diag.title) + '</strong>' : '') +
          '.</p><p class="lms-status" style="margin-top:8px;">Continue with your learning path or Learning Chat.</p></div>';
        unlockDiagnosticGate();
        return;
      }
      showDiagnosticOrientation(diag);
    } catch (err) {
      var loadMsg = err && err.message ? String(err.message) : '';
      if (/time over/i.test(loadMsg)) {
        renderDiagnosticTimeOver({ message: loadMsg });
        return;
      }
      body.innerHTML = '<p class="lms-error">' + escapeHtml(loadMsg) + '</p>' +
        '<p class="lms-status">Contact your admin to upload the diagnostic assessment.</p>';
    }
  };

  window.closeLmsDiagnostic = async function () {
    if (window._lmsDiagnosticMandatory && !window._lmsDiagnosticAllowClose) {
      if (typeof lmsShowToast === 'function') {
        lmsShowToast('Please complete and submit the diagnostic assessment first.', 'error');
      }
      return;
    }
    if (diagState.attemptId && diagState.questions.length) {
      try { await persistDiagAnswers(); } catch (e) { /* ignore */ }
      // Only drop the local backup once every answer actually made it to
      // the server - if some are still stuck (offline), keep them queued
      // so the next open / reconnect / periodic retry can finish the job.
      var stillPending = loadDiagLocal(diagState.attemptId);
      var allSynced = Object.keys(stillPending).every(function (qid) { return stillPending[qid].synced; });
      if (allSynced) clearDiagLocal(diagState.attemptId);
    }
    clearDiagTimer();
    try { localStorage.removeItem('lmsDiagWs:' + (diagState.attemptId || 'x')); } catch (e) { /* ignore */ }
    lmsCloseModal('lmsDiagnosticModal');
    diagState = { assessmentId: null, attemptId: null, questions: [], current: 0, answers: {}, expiresAt: null, remainingSeconds: null, timerInterval: null };
    window._lmsDiagWorkspaceOpen = false;
    window._lmsDiagExplainCache = {};
    wsState = { tab: 'notes', notes: '', canvas: null, drawing: false, color: '#111827', erasing: false, last: null };
  };

  async function persistDiagAnswers() {
    if (!diagState.attemptId) return;
    for (var i = 0; i < diagState.questions.length; i++) {
      if (diagState.answers[i] === undefined) continue;
      var item = diagState.questions[i];
      var qid = item.question_id || (item.question && item.question.id);
      if (!qid) continue;
      var idx = diagState.answers[i];
      persistAnswerLocally(diagState.attemptId, qid, idx);
      try {
        await lmsApi('/api/lms/attempts/' + diagState.attemptId + '/answer', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question_id: qid, selected_option_index: idx })
        });
        markDiagAnswerSynced(diagState.attemptId, qid);
      } catch (e) { /* offline / server hiccup - one answer failing must not stop the rest, and stays queued locally */ }
    }
  }

  async function saveDiagAnswerAtIndex(qIdx) {
    if (!diagState.attemptId || diagState.answers[qIdx] === undefined) return;
    var item = diagState.questions[qIdx];
    var qid = item.question_id || (item.question && item.question.id);
    if (!qid) return;
    var idx = diagState.answers[qIdx];
    // Save locally FIRST, synchronously - this is what makes the answer
    // survive a dropped connection, a reload, or the tab closing, even if
    // the network call below never lands.
    persistAnswerLocally(diagState.attemptId, qid, idx);
    try {
      await lmsApi('/api/lms/attempts/' + diagState.attemptId + '/answer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question_id: qid, selected_option_index: idx })
      });
      markDiagAnswerSynced(diagState.attemptId, qid);
    } catch (e) { /* offline / server hiccup - stays queued in localStorage, retried automatically */ }
  }

  window._lmsDiagOrientation = null;

  function showDiagnosticOrientation(diag) {
    clearDiagTimer();
    window._lmsDiagOrientation = diag;
    var body = document.getElementById('lmsDiagBody');
    var timerEl = document.getElementById('lmsDiagTimer');
    if (timerEl) timerEl.style.display = 'none';
    var mins = diag.time_limit_minutes ? Math.round(diag.time_limit_minutes) : 30;
    var qn = diag.question_count || '';
    var isRetake = !!diag._retake;
    body.innerHTML =
      '<div class="lms-diag-orient">' +
      '<h3 class="lms-diag-orient-title">' + (isRetake ? 'Retaking the diagnostic' : 'Before you begin') + '</h3>' +
      '<p class="lms-diag-orient-lead">' +
      (isRetake
        ? 'These are fresh questions on the same topics as before. Your Learning Path will update from this attempt.'
        : 'This is a diagnostic. It is not graded like a test — it just helps us find what you already know and where you need practice, so your Learning Path is built for you.') +
      '</p>' +
      '<ul class="lms-diag-orient-list">' +
      (qn ? '<li><strong>' + escapeHtml(String(qn)) + ' questions</strong>, multiple choice.</li>' : '') +
      '<li><strong>About ' + mins + ' minutes.</strong> The timer starts when you press Start and is shown at the top.</li>' +
      '<li>You can <strong>move between questions freely</strong>, skip any question, and change your answers until you submit.</li>' +
      '<li><strong>Unanswered questions score 0</strong>; questions you answered still count toward your score.</li>' +
      '<li>Use <strong>&ldquo;Explain this question&rdquo;</strong> if the wording is unclear — it rephrases the question without giving the answer.</li>' +
      '<li>Use the <strong>Workspace</strong> (rough sheet / notes) for any working out.</li>' +
      '<li>If time runs out it submits automatically, and <strong>every question you answered is still scored</strong>.</li>' +
      '</ul>' +
      '<div class="lms-modal-footer" style="border:none;padding-top:8px;display:flex;gap:8px;flex-wrap:wrap;">' +
      '<button type="button" class="lms-btn lms-btn-primary" onclick="lmsBeginDiagnostic()">Start Diagnostic</button>' +
      (window._lmsDiagnosticMandatory ? '' : '<button type="button" class="lms-btn lms-btn-secondary" onclick="closeLmsDiagnostic()">Not now</button>') +
      '</div></div>';
  }

  window.lmsBeginDiagnostic = function () {
    var diag = window._lmsDiagOrientation;
    if (!diag) return;
    startDiagnosticQuiz(diag.id, diag.title || 'Diagnostic Assessment', diag.question_count, 'Platform diagnostic', diag.time_limit_minutes, !!diag._retake);
  };

  window.lmsRetakeDiagnostic = async function () {
    var body = document.getElementById('lmsDiagBody');
    if (body) body.innerHTML = '<div class="lms-spinner"></div><p class="lms-status" style="text-align:center;">Loading a fresh set of questions…</p>';
    try {
      var diag = await lmsApi('/api/lms/diagnostics/default');
      diag._retake = true;
      showDiagnosticOrientation(diag);
    } catch (e) {
      if (body) body.innerHTML = '<p class="lms-error">Could not start a retake right now.</p>';
    }
  };

  async function startDiagnosticQuiz(assessmentId, title, qCount, subtitle, timeLimitMinutes, retake) {
    diagState.assessmentId = assessmentId;
    var body = document.getElementById('lmsDiagBody');
    body.innerHTML = '<div class="lms-spinner"></div><p class="lms-status" style="text-align:center;">Starting ' + escapeHtml(title) + '...</p>';
    try {
      var start = await lmsApi('/api/lms/quizzes/' + assessmentId + '/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ retake: !!retake })
      });
      if (!retake && (start.timed_out || start.time_over)) {
        renderDiagnosticTimeOver(start);
        return;
      }
      diagState.attemptId = start.attempt_id;
      diagState.expiresAt = start.expires_at || null;
      var rem = start.remaining_seconds;
      if (rem == null && start.time_limit_minutes != null) {
        rem = Math.max(0, Math.floor(Number(start.time_limit_minutes) * 60));
      }
      diagState.remainingSeconds = rem != null ? Math.max(0, Math.floor(Number(rem))) : null;
      var qData = await lmsApi('/api/lms/attempts/' + start.attempt_id + '/questions');
      diagState.questions = qData.questions || qData || [];
      diagState.answers = {};
      var saved = qData.saved_answers || {};
      Object.keys(saved).forEach(function (k) {
        diagState.answers[parseInt(k, 10)] = saved[k];
      });
      // Recover any answer that only ever reached this browser (picked
      // while offline, or a reload/tab-close before the server call
      // landed) - it must not silently disappear on resume.
      var localPending = loadDiagLocal(diagState.attemptId);
      diagState.questions.forEach(function (item, idx) {
        var qid = item.question_id || (item.question && item.question.id);
        if (qid != null && diagState.answers[idx] === undefined && localPending[qid] && localPending[qid].idx != null) {
          diagState.answers[idx] = localPending[qid].idx;
        }
      });
      flushDiagPendingAnswers();
      diagState.current = qData.current_question_index != null ? qData.current_question_index : 0;
      if (start.resumed) {
        lmsShowToast('Resuming where you left off', 'success');
      }
      if (diagState.remainingSeconds != null && diagState.remainingSeconds <= 0) {
        await submitLmsDiagnostic(true);
        return;
      }
      if (!diagState.questions.length) {
        body.innerHTML = '<p class="lms-error">No questions in this diagnostic.</p>';
        return;
      }
      renderDiagnosticQuestion();
      startDiagTimer();
    } catch (err) {
      var startMsg = err && err.message ? String(err.message) : '';
      if (/time over/i.test(startMsg)) {
        renderDiagnosticTimeOver({ message: startMsg });
        return;
      }
      body.innerHTML = '<p class="lms-error">' + escapeHtml(startMsg) + '</p>';
    }
  }

  function diagAnsweredCount() {
    var n = 0;
    for (var k = 0; k < diagState.questions.length; k++) {
      if (diagState.answers[k] !== undefined) n++;
    }
    return n;
  }

  function diagQuestionMapHtml() {
    var total = diagState.questions.length;
    var cells = '';
    for (var i = 0; i < total; i++) {
      var state = (i === diagState.current)
        ? 'current'
        : (diagState.answers[i] !== undefined ? 'answered' : 'unanswered');
      cells += '<button type="button" class="lms-qmap-cell ' + state + '" ' +
        'onclick="jumpToDiagQuestion(' + i + ')" aria-label="Question ' + (i + 1) + '">' + (i + 1) + '</button>';
    }
    var answered = diagAnsweredCount();
    return '<div class="lms-qmap">' +
      '<div class="lms-qmap-head"><span>' + answered + ' of ' + total + ' answered</span></div>' +
      '<div class="lms-qmap-grid">' + cells + '</div></div>';
  }

  function renderDiagnosticQuestion() {
    var body = document.getElementById('lmsDiagBody');
    var idx = diagState.current;
    var total = diagState.questions.length;
    var item = diagState.questions[idx];
    var q = item.question || item;
    var pct = Math.round(100 * (idx + 1) / total);
    var qSecs = item.time_limit_seconds || q.time_limit_seconds;
    var diff = item.difficulty || q.difficulty || '';
    var qid = item.question_id || (q && q.id);
    var opts = (q.options || []).map(function (o, oi) {
      var sel = diagState.answers[idx] === oi ? ' selected' : '';
      return '<button type="button" class="lms-quiz-option' + sel + '" onclick="selectDiagOption(' + idx + ',' + oi + ')">' +
        '<span class="lms-quiz-option-label">' + escapeHtml(o.label || String.fromCharCode(65 + oi)) + '.</span>' +
        '<span class="lms-quiz-option-body">' + fmtOption(o) + '</span></button>';
    }).join('');

    var backBtn = idx > 0
      ? '<button type="button" class="lms-btn lms-btn-secondary" onclick="prevDiagQuestion()">Back</button>'
      : '';
    var nextBtn = idx < total - 1
      ? '<button type="button" class="lms-btn lms-btn-primary" onclick="nextDiagQuestion()">' +
        (diagState.answers[idx] === undefined ? 'Skip &rarr;' : 'Next') + '</button>'
      : '';
    var submitBtn = '<button type="button" class="lms-btn ' + (idx === total - 1 ? 'lms-btn-primary' : 'lms-btn-secondary') +
      '" onclick="confirmSubmitDiagnostic()">Submit Diagnostic</button>';
    var nav =
      '<div class="lms-quiz-nav">' +
      '<div class="lms-quiz-nav-start">' + backBtn + '</div>' +
      '<div class="lms-quiz-nav-end">' + nextBtn + ' ' + submitBtn + '</div>' +
      '</div>';

    var tools =
      '<div class="lms-quiz-tools">' +
      (qid && typeof window.lmsExplainDiagQuestion === 'function'
        ? '<button type="button" class="lms-quiz-tool" onclick="lmsExplainDiagQuestion(' + idx + ')">&#128172; Explain this question</button>' : '') +
      (typeof window.toggleDiagWorkspace === 'function'
        ? '<button type="button" class="lms-quiz-tool" onclick="toggleDiagWorkspace()">&#9998; Workspace</button>' : '') +
      (diagState.answers[idx] !== undefined
        ? '<button type="button" class="lms-quiz-tool" onclick="clearDiagAnswer(' + idx + ')">Clear answer</button>'
        : '') +
      '<button type="button" class="lms-quiz-tool" title="Copy question text" onclick="lmsCopyDiagQuestion(this)">&#128203; Copy</button>' +
      // --lms-font-scale is a live CSS custom property - text resizes
      // immediately, no re-render needed.
      '<button type="button" class="lms-font-btn" title="Smaller text" onclick="lmsStepFont(-1)">A-</button>' +
      '<button type="button" class="lms-font-btn" title="Larger text" onclick="lmsStepFont(1)">A+</button>' +
      '</div>';

    var meta = '<p class="lms-status">Question ' + (idx + 1) + ' of ' + total;
    if (diff) meta += ' &middot; ' + escapeHtml(diff);
    if (qSecs) meta += ' &middot; ~' + qSecs + 's suggested';
    meta += '</p>';

    body.innerHTML =
      '<div class="lms-quiz-progress"><div class="lms-quiz-progress-bar" style="width:' + pct + '%"></div></div>' +
      meta +
      '<div class="lms-quiz-stem">' + fmtQuestion(q) + '</div>' +
      '<div id="lmsDiagExplain" class="lms-diag-explain" hidden></div>' +
      opts +
      tools +
      '<div id="lmsDiagWorkspaceHost"></div>' +
      diagQuestionMapHtml() +
      '<div class="lms-modal-footer" style="border:none;padding:16px 0 0;margin:0;">' + nav + '</div>';
    typeset(body);
    // Re-show a clarification the student already asked for on this question.
    if (qid && window._lmsDiagExplainCache && window._lmsDiagExplainCache[qid]) {
      var ep = document.getElementById('lmsDiagExplain');
      if (ep) {
        ep.hidden = false;
        ep.innerHTML = '<strong>In simpler words:</strong> ' + escapeHtml(window._lmsDiagExplainCache[qid]);
      }
    }
    if (window._lmsDiagWorkspaceOpen && typeof window.mountDiagWorkspace === 'function') window.mountDiagWorkspace();
  }

  window.lmsCopyDiagQuestion = function (btn) {
    var item = diagState.questions[diagState.current];
    if (!item) return;
    var q = item.question || item;
    var lines = [(typeof window.lmsQuestionText === 'function' ? window.lmsQuestionText(q) : (q.question_text || '')).trim()];
    (q.options || []).forEach(function (o, oi) {
      var label = o.label || String.fromCharCode(65 + oi);
      var text = (typeof window.lmsOptionText === 'function' ? window.lmsOptionText(o) : (o.text || o.latex || ''));
      lines.push(label + '. ' + String(text).trim());
    });
    if (typeof window.lmsCopyToClipboard === 'function') {
      window.lmsCopyToClipboard(lines.join('\n'), btn);
    }
  };

  window.selectDiagOption = function (qIdx, optIdx) {
    diagState.answers[qIdx] = optIdx;
    saveDiagAnswerAtIndex(qIdx);
    renderDiagnosticQuestion();
  };
  window.clearDiagAnswer = function (qIdx) {
    delete diagState.answers[qIdx];
    // Persist the clear as -1 so the server-side answer is neutralised.
    var item = diagState.questions[qIdx];
    var qid = item && (item.question_id || (item.question && item.question.id));
    if (diagState.attemptId && qid) {
      lmsApi('/api/lms/attempts/' + diagState.attemptId + '/answer', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question_id: qid, selected_option_index: -1 })
      }).catch(function () {});
    }
    renderDiagnosticQuestion();
  };
  window.jumpToDiagQuestion = function (i) {
    if (i < 0 || i >= diagState.questions.length) return;
    saveDiagAnswerAtIndex(diagState.current);
    diagState.current = i;
    renderDiagnosticQuestion();
  };
  window.nextDiagQuestion = function () {
    if (diagState.current < diagState.questions.length - 1) {
      saveDiagAnswerAtIndex(diagState.current);
      diagState.current++;
      renderDiagnosticQuestion();
    }
  };
  window.prevDiagQuestion = function () {
    if (diagState.current > 0) {
      saveDiagAnswerAtIndex(diagState.current);
      diagState.current--;
      renderDiagnosticQuestion();
    }
  };
  /* ---- Math workspace: scratch notes + freehand canvas ---- */
  var wsState = { tab: 'notes', notes: '', canvas: null, drawing: false, color: '#111827', erasing: false, last: null };

  function wsNotesKey() { return 'lmsDiagWs:' + (diagState.attemptId || 'x'); }

  window.toggleDiagWorkspace = function () {
    window._lmsDiagWorkspaceOpen = !window._lmsDiagWorkspaceOpen;
    var host = document.getElementById('lmsDiagWorkspaceHost');
    if (!host) return;
    if (window._lmsDiagWorkspaceOpen) window.mountDiagWorkspace();
    else host.innerHTML = '';
  };

  window.mountDiagWorkspace = function () {
    var host = document.getElementById('lmsDiagWorkspaceHost');
    if (!host) return;
    if (!wsState.notes) {
      try { wsState.notes = localStorage.getItem(wsNotesKey()) || ''; } catch (e) { /* ignore */ }
    }
    var colors = ['#111827', '#dc2626', '#2563eb', '#16a34a'];
    var swatches = colors.map(function (c) {
      return '<button type="button" class="lms-diag-workspace-swatch' + (wsState.color === c && !wsState.erasing ? ' active' : '') +
        '" style="background:' + c + '" onclick="lmsWsSetColor(\'' + c + '\')" aria-label="pen colour"></button>';
    }).join('');
    host.innerHTML =
      '<div class="lms-diag-workspace">' +
      '<div class="lms-diag-workspace-tabs">' +
      '<button type="button" class="lms-diag-workspace-tab' + (wsState.tab === 'notes' ? ' active' : '') + '" onclick="lmsWsTab(\'notes\')">Notes</button>' +
      '<button type="button" class="lms-diag-workspace-tab' + (wsState.tab === 'draw' ? ' active' : '') + '" onclick="lmsWsTab(\'draw\')">Rough sheet</button>' +
      '</div>' +
      '<div class="lms-diag-workspace-body">' +
      (wsState.tab === 'notes'
        ? '<textarea class="lms-diag-workspace-notes" id="lmsWsNotes" placeholder="Working / rough notes (only you see this)">' + escapeHtml(wsState.notes) + '</textarea>'
        : '<div class="lms-diag-workspace-toolbar">' + swatches +
          '<button type="button" class="lms-quiz-tool' + (wsState.erasing ? ' active' : '') + '" onclick="lmsWsErase()">Eraser</button>' +
          '<button type="button" class="lms-quiz-tool" onclick="lmsWsClear()">Clear</button></div>' +
          '<div class="lms-diag-workspace-canvas-wrap"><canvas class="lms-diag-workspace-canvas" id="lmsWsCanvas"></canvas></div>') +
      '</div></div>';
    if (wsState.tab === 'notes') {
      var ta = document.getElementById('lmsWsNotes');
      ta.addEventListener('input', function () {
        wsState.notes = ta.value;
        try { localStorage.setItem(wsNotesKey(), ta.value); } catch (e) { /* ignore */ }
      });
    } else {
      lmsWsInitCanvas();
    }
  };

  window.lmsWsTab = function (t) { wsState.tab = t; window.mountDiagWorkspace(); };
  window.lmsWsSetColor = function (c) { wsState.color = c; wsState.erasing = false; window.mountDiagWorkspace(); };
  window.lmsWsErase = function () { wsState.erasing = !wsState.erasing; window.mountDiagWorkspace(); };
  window.lmsWsClear = function () {
    wsState.canvasData = null;
    var c = document.getElementById('lmsWsCanvas');
    if (c) { var x = c.getContext('2d'); x.clearRect(0, 0, c.width, c.height); }
  };

  function lmsWsInitCanvas() {
    var canvas = document.getElementById('lmsWsCanvas');
    if (!canvas) return;
    var rect = canvas.getBoundingClientRect();
    var dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(rect.width * dpr));
    canvas.height = Math.max(1, Math.round(rect.height * dpr));
    var ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    ctx.lineJoin = ctx.lineCap = 'round';
    if (wsState.canvasData) {
      var img = new Image();
      img.onload = function () { ctx.drawImage(img, 0, 0, rect.width, rect.height); };
      img.src = wsState.canvasData;
    }
    function pos(e) {
      var r = canvas.getBoundingClientRect();
      var p = (e.touches && e.touches[0]) || e;
      return { x: p.clientX - r.left, y: p.clientY - r.top };
    }
    function start(e) { wsState.drawing = true; wsState.last = pos(e); e.preventDefault(); }
    function move(e) {
      if (!wsState.drawing) return;
      var p = pos(e);
      ctx.strokeStyle = wsState.erasing ? '#ffffff' : wsState.color;
      ctx.lineWidth = wsState.erasing ? 16 : 2.5;
      ctx.beginPath();
      ctx.moveTo(wsState.last.x, wsState.last.y);
      ctx.lineTo(p.x, p.y);
      ctx.stroke();
      wsState.last = p;
      e.preventDefault();
    }
    function end() {
      if (!wsState.drawing) return;
      wsState.drawing = false;
      try { wsState.canvasData = canvas.toDataURL('image/png'); } catch (e) { /* ignore */ }
    }
    canvas.addEventListener('pointerdown', start);
    canvas.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
    canvas.addEventListener('touchstart', start, { passive: false });
    canvas.addEventListener('touchmove', move, { passive: false });
    canvas.addEventListener('touchend', end);
  }

  window._lmsDiagExplainCache = {};
  window.lmsExplainDiagQuestion = async function (qIdx) {
    var panel = document.getElementById('lmsDiagExplain');
    if (!panel) return;
    var item = diagState.questions[qIdx];
    var q = item.question || item;
    var qid = item.question_id || (q && q.id);
    if (!qid || !diagState.attemptId) return;
    panel.hidden = false;
    if (window._lmsDiagExplainCache[qid]) {
      panel.innerHTML = '<strong>In simpler words:</strong> ' + escapeHtml(window._lmsDiagExplainCache[qid]);
      return;
    }
    panel.innerHTML = '<span class="lms-status">Rephrasing this question…</span>';
    try {
      var res = await lmsApi('/api/lms/attempts/' + diagState.attemptId + '/questions/' + qid + '/clarify', { method: 'POST' });
      var text = (res && res.clarification) || 'Read the question one part at a time and work out your own answer before choosing.';
      window._lmsDiagExplainCache[qid] = text;
      panel.innerHTML = '<strong>In simpler words:</strong> ' + escapeHtml(text);
    } catch (e) {
      panel.innerHTML = '<span class="lms-status">Could not rephrase right now. Read the question one part at a time and work out your own answer before choosing.</span>';
    }
  };

  window.confirmSubmitDiagnostic = async function () {
    var total = diagState.questions.length;
    var answered = diagAnsweredCount();
    var missing = total - answered;
    if (missing > 0) {
      var message =
        'You have ' + missing + ' unanswered question' + (missing === 1 ? '' : 's') +
        '. Are you sure you want to submit?\n\n' +
        'Unanswered questions score 0. Your answered questions still count.';
      var ok;
      if (typeof showInAppConfirm === 'function') {
        ok = await showInAppConfirm(message, {
          title: 'Submit diagnostic?',
          confirmLabel: 'Submit',
          cancelLabel: 'Go back',
          iconClass: 'fas fa-exclamation-circle'
        });
      } else {
        ok = window.confirm(message);
      }
      if (!ok) return;
    }
    submitLmsDiagnostic();
  };

  window.submitLmsDiagnostic = async function (autoSubmit) {
    clearDiagTimer();
    var body = document.getElementById('lmsDiagBody');
    body.innerHTML = '<div class="lms-spinner"></div><p class="lms-status" style="text-align:center">' +
      (autoSubmit ? 'Time over — closing your diagnostic...' : 'Scoring your diagnostic...') + '</p>';
    if (typeof showWaitOverlay === 'function') {
      showWaitOverlay(autoSubmit ? 'Time over — submitting...' : 'Submitting diagnostic...');
    }
    try {
      if (!autoSubmit) {
        // Best-effort flush of every selected answer. Answers are already
        // saved on selection; this is a safety net, so one failed save
        // must not block the submit itself.
        for (var i = 0; i < diagState.questions.length; i++) {
          var item = diagState.questions[i];
          var qid = item.question_id || (item.question && item.question.id);
          if (diagState.answers[i] !== undefined && qid) {
            persistAnswerLocally(diagState.attemptId, qid, diagState.answers[i]);
            try {
              await lmsApi('/api/lms/attempts/' + diagState.attemptId + '/answer', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ question_id: qid, selected_option_index: diagState.answers[i] })
              });
              markDiagAnswerSynced(diagState.attemptId, qid);
            } catch (flushErr) { /* keep going - submit will score what's saved, and this stays queued for a retry */ }
          }
        }
      } else {
        // Timed out: still try to flush whatever is queued locally one
        // last time before submit scores the attempt server-side.
        await flushDiagPendingAnswers();
      }
      var result = await lmsApi('/api/lms/attempts/' + diagState.attemptId + '/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ time_expired: !!autoSubmit })
      });
      // Attempt is finalized server-side now - the local backup has done its job.
      clearDiagLocal(diagState.attemptId);
      if ((result.timed_out || result.time_over) && !(result.max_score != null && result.score != null)) {
        renderDiagnosticTimeOver(result);
        return;
      }
      renderDiagnosticResults(result, { timedOut: !!(result.timed_out || result.time_over || autoSubmit) });
      unlockDiagnosticGate();
    } catch (err) {
      var msg = err && err.message ? String(err.message) : '';
      if (autoSubmit || /time over/i.test(msg) || /expired/i.test(msg)) {
        renderDiagnosticTimeOver({ message: msg || undefined });
        return;
      }
      body.innerHTML = '<p class="lms-error">' + escapeHtml(msg) + '</p>';
    } finally {
      if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
    }
  };

  function renderDiagnosticResults(result, opts) {
    opts = opts || {};
    clearDiagTimer();
    var body = document.getElementById('lmsDiagBody');
    var timerEl = document.getElementById('lmsDiagTimer');
    if (timerEl) timerEl.style.display = 'none';
    var pct = result.score_percent != null ? Math.round(result.score_percent) : null;
    var scoreLabel = pct != null ? pct + '%' : '—';
    var hasCounts = result.score != null && result.max_score != null;
    // Show the exact "N of M correct" the % is built from - this is the
    // single biggest source of "why is my score X and not Y" confusion,
    // because the topic tiles below are grouped scores, not a breakdown of
    // the overall number.
    var countLabel = hasCounts
      ? Math.round(result.score) + ' of ' + Math.round(result.max_score) + ' questions correct'
      : '';
    var unanswered = result.unanswered_count != null
      ? Math.round(result.unanswered_count)
      : (opts.unansweredCount != null ? Math.round(opts.unansweredCount) : 0);
    var unansweredLabel = unanswered > 0
      ? unanswered + ' unanswered · scored as 0'
      : '';
    var weak = result.weak_topics || [];
    var strong = result.strong_topics || [];

    function topicChip(t, cls) {
      var p = Math.round(t.score_percent || 0);
      var n = (t.question_ids && t.question_ids.length) || 0;
      var frac = n ? (Math.round((p * n) / 100) + ' of ' + n + ' correct') : '';
      return '<div class="lms-topic-chip ' + cls + '"><strong>' + p + '%</strong>' +
        escapeHtml(t.topic_name || t.name || 'Topic') +
        (frac ? '<span class="lms-topic-chip-frac">' + frac + '</span>' : '') + '</div>';
    }
    var weakHtml = weak.length
      ? weak.map(function (t) { return topicChip(t, 'weak'); }).join('')
      : '<p class="lms-status">No weak topics detected yet.</p>';
    var strongHtml = strong.length
      ? strong.map(function (t) { return topicChip(t, 'strong'); }).join('')
      : '';
    var timedOutBanner = opts.timedOut
      ? '<div class="lms-diag-timeout-note">&#9203; ' +
        escapeHtml(result.message || 'Time ran out — the questions you answered were scored.') +
        '</div>'
      : (opts.alreadyDone
        ? '<div class="lms-diag-timeout-note">You have already completed the diagnostic. Here is how you did.</div>'
        : '');
    var scoreMeta = [countLabel, unansweredLabel].filter(Boolean).join(' &middot; ');
    body.innerHTML =
      timedOutBanner +
      '<div class="lms-diag-score">' +
      '<div class="lms-diag-score-num">' + scoreLabel + '</div>' +
      '<p class="lms-status">Overall diagnostic score' +
      (scoreMeta ? ' &middot; <strong>' + scoreMeta + '</strong>' : '') + '</p>' +
      (pct != null ? '<div class="lms-diag-score-bar"><div class="lms-diag-score-fill" style="width:' + Math.max(0, Math.min(100, pct)) + '%;"></div></div>' : '') +
      '</div>' +
      ((weak.length || strong.length)
        ? '<p class="lms-status lms-diag-explainer">Below, your questions are grouped by topic. Each tile is that topic\'s own score - the number above already adds up every question, strong topics included.</p>'
        : '') +
      (weak.length ? '<h4 style="color:#991b1b;margin:0 0 8px;">Areas to improve</h4><div class="lms-topic-grid">' + weakHtml + '</div>' : '') +
      (strong.length ? '<h4 style="color:var(--lms-green);margin:16px 0 8px;">Strong areas</h4><div class="lms-topic-grid">' + strongHtml + '</div>' : '') +
      (weak.length
        ? '<p class="lms-status" style="margin-top:12px;">Practice weak areas in Learning Chat — one question at a time.</p>'
        : '<div class="lms-diag-clear-note"><strong>&#127881; No areas of improvement.</strong> ' +
          'You met the mark on every topic in this diagnostic. Keep it sharp with a short set of ' +
          'above-level challenge questions built from your strongest topics.</div>') +
      '<div class="lms-modal-footer" style="border:none;padding-top:20px;display:flex;gap:8px;flex-wrap:wrap;">' +
      (weak.length
        ? '<button type="button" class="lms-btn lms-btn-primary" onclick="closeLmsDiagnostic();openDeficiencyChat()">Start Learning Chat</button>'
        : '<button type="button" class="lms-btn lms-btn-primary" onclick="closeLmsDiagnostic();openDeficiencyChat(false, \'enrichment\')">Start challenge</button>') +
      '<button type="button" class="lms-btn lms-btn-secondary" onclick="lmsRetakeDiagnostic()">Retake with new questions</button>' +
      '<button type="button" class="lms-btn lms-btn-secondary" onclick="closeLmsDiagnostic();lmsShowToast(\'Learning path updated!\')">Continue</button></div>';
  }

  /* Learning path step actions */
  window.lmsLaunchPathStep = function (itemType, itemId, pathItemId) {
    if (itemType === 'lesson' && itemId) {
      if (typeof viewLesson === 'function') {
        viewLesson(itemId);
      } else {
        window.location.href = '/student_dashboard#lesson-' + itemId;
      }
      return;
    }
    if (itemType === 'quiz' && itemId) {
      if (typeof startLmsQuiz === 'function') {
        startLmsQuiz(itemId, null);
        lmsOpenModal('lmsStudentModal');
      } else {
        lmsShowToast('Open My Quizzes to take this quiz', 'error');
      }
      return;
    }
    if (itemType === 'practice') {
      if (itemId === 0 && typeof openDeficiencyChat === 'function') {
        openDeficiencyChat();
        return;
      }
      if (typeof openLmsPracticePanel === 'function') {
        openLmsPracticePanel(itemId);
      }
      return;
    }
    if (itemType === 'enrichment') {
      if (typeof openDeficiencyChat === 'function') {
        openDeficiencyChat(false, 'enrichment');
      }
      return;
    }
    if (itemType === 'reassessment') {
      if (typeof startLmsQuiz === 'function' && itemId && itemId > 5) {
        startLmsQuiz(itemId, null);
        if (typeof lmsOpenModal === 'function') lmsOpenModal('lmsStudentModal');
      } else {
        lmsShowToast('Complete the practice quiz for this topic first');
      }
      return;
    }
    if (pathItemId && typeof markLmsPathItemComplete === 'function') {
      markLmsPathItemComplete(pathItemId);
    }
  };

  window.renderLmsLearningPathEnhanced = function (path) {
    if (!path || !path.items || !path.items.length) {
      return '<div class="lms-path-panel"><h3>My Learning Path</h3><p class="lms-path-empty">Complete your diagnostic to unlock Learning Chat for weak areas.</p>' +
        '<button type="button" class="lms-btn lms-btn-primary" onclick="openLmsDiagnostic()">Take Diagnostic</button></div>';
    }
    var steps = path.items.map(function (item) {
      var isDone = item.status === 'completed';
      var isCurrent = !isDone && path.current_step && path.current_step.id === item.id;
      var cls = 'lms-path-step' + (isDone ? ' completed' : '') + (isCurrent ? ' current' : '');
      var check = isDone ? '&#10003;' : (isCurrent ? '&#9679;' : '');
      var action = '';
      if (isCurrent && !isDone) {
        if (item.item_type === 'practice' && item.item_id === 0) {
          action = '<div class="lms-path-action">' +
            '<button type="button" class="lms-btn lms-btn-primary" onclick="openDeficiencyChat()">Open Learning Chat</button></div>';
        } else if (item.item_type === 'enrichment') {
          action = '<div class="lms-path-action">' +
            '<button type="button" class="lms-btn lms-btn-primary" onclick="openDeficiencyChat(false, \'enrichment\')">Start challenge</button></div>';
        } else {
          action = '<div class="lms-path-action">' +
            '<button type="button" class="lms-btn lms-btn-primary" onclick="lmsLaunchPathStep(\'' + escapeHtml(item.item_type) + '\',' + (item.item_id || 'null') + ',' + item.id + ')">Start</button> ' +
            '<button type="button" class="lms-btn lms-btn-secondary" onclick="markLmsPathItemComplete(' + item.id + ')">Mark done</button></div>';
        }
      }
      return '<li class="' + cls + '">' +
        '<div class="lms-path-check">' + check + '</div>' +
        '<div class="lms-path-step-body">' +
        '<div class="lms-path-step-title">' + escapeHtml(item.title || item.label || 'Step') + '</div>' +
        '<div class="lms-path-step-meta">' + escapeHtml(item.item_type) + '</div>' + action + '</div></li>';
    }).join('');
    var pct = path.total_count ? Math.round(100 * (path.completed_count || 0) / path.total_count) : 0;
    return '<div class="lms-path-panel"><h3>My Learning Path <span class="lms-path-pct" style="font-weight:400;font-size:.875rem;">(' + pct + '% done)</span></h3>' +
      '<ol class="lms-path-steps">' + steps + '</ol></div>';
  };

  document.addEventListener('pagehide', function () {
    if (diagState.attemptId && diagState.questions.length) {
      persistDiagAnswers().catch(function () {});
    }
  });

  document.addEventListener('DOMContentLoaded', function () {
    if (typeof renderLmsLearningPath === 'function') {
      window._renderLmsLearningPathOriginal = renderLmsLearningPath;
    }
    window.renderLmsLearningPath = renderLmsLearningPathEnhanced;
  });
})();
