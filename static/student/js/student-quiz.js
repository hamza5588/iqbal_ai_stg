/* Quiz taking (class assignments and learning-path quizzes) in #lmsStudentModal.
   Moved from the old student page's inline script — same endpoints and rules:
   POST /api/lms/quizzes/:id/start {assignment_id} → GET /attempts/:id/questions → POST /attempts/:id/answer
   → POST /attempts/:id/submit (unanswered confirm). Supports teacher-configured countdown timer. */
(function () {
  function fmtText(text, inline) {
    if (typeof lmsFormatRichText === 'function') {
      return lmsFormatRichText(text, inline ? { inline: true, quiz: true } : { quiz: true });
    }
    return escapeHtml(text == null ? '' : String(text));
  }
  function fmtOption(opt) {
    var raw = (typeof lmsOptionText === 'function') ? lmsOptionText(opt) : ((opt && (opt.text || opt.latex)) || '');
    return fmtText(raw, true);
  }
  function fmtQuestion(q) {
    var raw = (typeof lmsQuestionText === 'function') ? lmsQuestionText(q) : ((q && (q.question_text || q.question_latex)) || '');
    return fmtText(raw, false);
  }
  function typeset(el) {
    if (!el) return Promise.resolve();
    if (typeof lmsTypesetMath === 'function') return lmsTypesetMath(el);
    el.classList.add('tex2jax_process');
    if (window.MathJax && window.MathJax.typesetPromise) return window.MathJax.typesetPromise([el]).catch(function () {});
    return Promise.resolve();
  }

  var attemptId = null, questions = [], currentQ = 0, savedAnswers = {}, assignmentId = null, quizTitle = '';
  var remainingSeconds = null, timerInterval = null, submitting = false;
  var quizDwell = (typeof lmsQuestionDwell === 'function') ? lmsQuestionDwell() : null;

  function quizQid(q) {
    return q ? (q.question_id || q.id) : null;
  }
  function quizDwellSeconds(qi) {
    if (!quizDwell) return 0;
    var q = questions[qi];
    var id = quizQid(q);
    return id != null ? quizDwell.seconds(id) : 0;
  }
  function syncQuizDwell(qi) {
    if (!quizDwell || !attemptId) return;
    var q = questions[qi];
    var id = quizQid(q);
    if (id == null) return;
    var sec = quizDwell.seconds(id);
    if (sec <= 0) return;
    var payload = { question_id: id, time_spent_seconds: sec };
    if (savedAnswers[qi] !== undefined) payload.selected_option_index = savedAnswers[qi];
    fetch('/api/lms/attempts/' + attemptId + '/answer', {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).catch(function () {});
  }
  function startQuizDwell(qi) {
    if (!quizDwell) return;
    var q = questions[qi];
    var id = quizQid(q);
    if (id == null) return;
    quizDwell.switchTo(id);
  }
  function onQuizVisChange() {
    if (!quizDwell) return;
    if (document.hidden) quizDwell.pause();
    else quizDwell.resume();
  }
  document.addEventListener('visibilitychange', onQuizVisChange);

  function show(which) {
    ['lmsAssignmentList', 'lmsQuizTaking', 'lmsQuizResult'].forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      el.classList.toggle('hidden', id !== which);
      // The student page has no CSS rule for ".hidden", so the class alone hid nothing: after
      // submit the questions stayed on screen with the score underneath them.
      el.style.display = id === which ? '' : 'none';
    });
  }
  function setTitle(t) {
    var h = document.getElementById('lmsStudentModalTitle');
    if (h) h.textContent = t || 'Quiz';
  }
  function clearQuizTimer() {
    if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
  }
  function formatCountdown(sec) {
    sec = Math.max(0, Math.floor(Number(sec) || 0));
    var m = Math.floor(sec / 60), s = sec % 60;
    return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
  }
  function updateQuizTimerDisplay() {
    var el = document.getElementById('lmsQuizTimer');
    if (!el || remainingSeconds == null) return;
    var urgent = remainingSeconds <= 60;
    el.classList.toggle('urgent', urgent);
    el.innerHTML = '<i class="far fa-clock"></i> ' + formatCountdown(remainingSeconds);
    el.style.display = 'inline-flex';
  }
  function startQuizTimer() {
    clearQuizTimer();
    if (remainingSeconds == null) return;
    updateQuizTimerDisplay();
    timerInterval = setInterval(function () {
      if (remainingSeconds != null && remainingSeconds > 0) {
        remainingSeconds = Math.max(0, remainingSeconds - 1);
      }
      updateQuizTimerDisplay();
      if (remainingSeconds <= 0) {
        clearQuizTimer();
        submitLmsQuiz(true);
      }
    }, 1000);
  }

  /* "My Quizzes" now lives in My Classes (quizzes per class). */
  window.openLmsStudentHub = function () {
    if (window.lmsStudentNeedsDiagnostic && window.lmsStudentNeedsDiagnostic()) {
      if (typeof lmsShowToast === 'function') lmsShowToast('Complete your diagnostic assessment first.', 'error');
      if (typeof openLmsDiagnostic === 'function') openLmsDiagnostic();
      return;
    }
    window.sdShowView('classes');
  };
  /* Once a quiz has started the student can only submit it (or close the whole browser,
     which locks a strict exam). No cross button, no backdrop-click close. */
  /* Fullscreen for the whole quiz (DIL feedback 25-11-2026). Browsers never let a page
     forbid leaving fullscreen, so: Keyboard Lock makes a single Esc press do nothing
     (Chrome/Edge/ChromeOS need Esc held down), and if fullscreen is left anyway a gate
     covers the quiz until the student presses "Return to fullscreen". */
  function isFullscreen() {
    return !!(document.fullscreenElement || document.webkitFullscreenElement || document.msFullscreenElement);
  }
  function lockEscKey() {
    try {
      if (navigator.keyboard && typeof navigator.keyboard.lock === 'function') {
        navigator.keyboard.lock(['Escape']).catch(function () {});
      }
    } catch (e) { /* unsupported */ }
  }
  function unlockEscKey() {
    try { if (navigator.keyboard && navigator.keyboard.unlock) navigator.keyboard.unlock(); } catch (e) { /* ignore */ }
  }
  function enterQuizFullscreen() {
    var root = document.documentElement;
    var req = root.requestFullscreen || root.webkitRequestFullscreen || root.msRequestFullscreen;
    var p;
    if (isFullscreen()) p = Promise.resolve();
    else if (req) {
      try { p = Promise.resolve(req.call(root)); } catch (e) { p = Promise.reject(e); }
    } else p = Promise.reject(new Error('Fullscreen API unavailable'));
    return p.then(function () { lockEscKey(); }).catch(function () {}).then(updateFullscreenGate);
  }
  function fullscreenGate() {
    var gate = document.getElementById('lmsFullscreenGate');
    if (gate) return gate;
    gate = document.createElement('div');
    gate.id = 'lmsFullscreenGate';
    gate.setAttribute('role', 'alertdialog');
    gate.innerHTML = '<div class="lms-fs-card"><h2>Quiz is in progress</h2>' +
      '<p>The quiz must stay in full screen until you submit it.</p>' +
      '<button type="button">Return to fullscreen</button></div>';
    gate.querySelector('button').addEventListener('click', function () { enterQuizFullscreen(); });
    document.body.appendChild(gate);
    return gate;
  }
  function updateFullscreenGate() {
    var locked = typeof window.lmsExamSecurityIsLocked === 'function' && window.lmsExamSecurityIsLocked();
    var show = !!window.lmsQuizInProgress && !isFullscreen() && !locked;
    if (show) fullscreenGate().classList.add('open');
    else {
      var gate = document.getElementById('lmsFullscreenGate');
      if (gate) gate.classList.remove('open');
    }
    if (window.lmsQuizInProgress && isFullscreen()) lockEscKey();
  }
  document.addEventListener('fullscreenchange', updateFullscreenGate);
  document.addEventListener('webkitfullscreenchange', updateFullscreenGate);
  window.lmsQuizUpdateFullscreenGate = updateFullscreenGate;

  function setQuizActive(on) {
    window.lmsQuizInProgress = !!on;
    var modal = document.getElementById('lmsStudentModal');
    if (modal) modal.classList.toggle('lms-quiz-active', !!on);
    if (on) {
      enterQuizFullscreen();
    } else {
      unlockEscKey();
      updateFullscreenGate();
      if (isFullscreen() && document.exitFullscreen) {
        try { document.exitFullscreen().catch(function () {}); } catch (e) { /* ignore */ }
      }
    }
  }
  window.closeLmsStudentHub = function (force) {
    if (window.lmsQuizInProgress && force !== true) {
      if (typeof lmsShowToast === 'function') lmsShowToast('Please finish and submit the quiz first.', 'error');
      return;
    }
    setQuizActive(false);
    clearQuizTimer();
    if (quizDwell && questions.length) syncQuizDwell(currentQ);
    if (quizDwell) quizDwell.stop();
    if (typeof window.lmsSetAssessmentCopyGuard === 'function') window.lmsSetAssessmentCopyGuard(false);
    lmsCloseModal('lmsStudentModal');
    if (typeof window.lmsExamSecurityStop === 'function') window.lmsExamSecurityStop();
    attemptId = null; questions = []; currentQ = 0; savedAnswers = {};
    remainingSeconds = null; submitting = false;
    show('lmsAssignmentList');
    if (typeof window.sdReloadClasses === 'function') window.sdReloadClasses();
  };
  window.exitLmsQuizTaking = window.closeLmsStudentHub;

  window.lmsExamCurrentQuestionIndex = function () { return currentQ; };

  window.lmsExamOnLocked = function () {
    var el = document.getElementById('lmsQuizTaking');
    if (el) el.setAttribute('data-exam-locked', '1');
    // Re-render so locked resume never shows answer UI under a stale paint.
    if (questions && questions.length) render();
    updateFullscreenGate();
  };
  window.lmsExamOnUnlocked = async function () {
    var el = document.getElementById('lmsQuizTaking');
    if (el) el.removeAttribute('data-exam-locked');
    if (typeof window.lmsExamSecurityRequestFullscreen === 'function') {
      try { await window.lmsExamSecurityRequestFullscreen(); } catch (e) { /* user may need gesture */ }
    }
    render();
    updateFullscreenGate();
  };

  window.lmsExamSecurityOnHeartbeat = function (d) {
    if (!d) return;
    if (d.remaining_seconds != null) {
      remainingSeconds = Math.max(0, Math.floor(Number(d.remaining_seconds)));
      var tEl = document.getElementById('lmsQuizTimer');
      if (tEl && remainingSeconds != null) {
        var m = Math.floor(remainingSeconds / 60), s = remainingSeconds % 60;
        tEl.textContent = m + ':' + (s < 10 ? '0' : '') + s;
      }
    }
    if (d.status === 'submitted' || d.is_expired) {
      submitLmsQuiz(true);
    }
  };

  window.startLmsQuiz = async function (quizId, asgId, title) {
    assignmentId = asgId;
    quizTitle = title || 'Quiz';
    setTitle(quizTitle);
    document.getElementById('lmsQuizResult').classList.add('hidden');
    clearQuizTimer();
    remainingSeconds = null;
    if (typeof showWaitOverlay === 'function') showWaitOverlay('Starting quiz...');
    try {
      var res = await fetch('/api/lms/quizzes/' + quizId + '/start', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ assignment_id: asgId })
      });
      var body = await res.json();
      if (!res.ok) {
        var errObj = body.error;
        var msg = (errObj && typeof errObj === 'object' && errObj.message) ? errObj.message : (body.error || body.message || 'Start failed');
        throw new Error(msg);
      }
      var d = body.data || body;
      attemptId = d.attempt_id;
      var rem = d.remaining_seconds;
      if (rem == null && d.time_limit_minutes != null) {
        rem = Math.max(0, Math.floor(Number(d.time_limit_minutes) * 60));
      }
      remainingSeconds = rem != null ? Math.max(0, Math.floor(Number(rem))) : null;
      var sec = d.security || {};
      var strict = !!(sec.is_strict || sec.lock_on_leave || sec.security_mode === 'strict');
      if (strict && !(d.is_locked || d.status === 'locked')) {
        if (typeof window.lmsExamSecurityRequestFullscreen === 'function') {
          try {
            await window.lmsExamSecurityRequestFullscreen();
          } catch (fsErr) {
            throw new Error('Fullscreen is required for Strict Exam Mode. Allow fullscreen and try again.');
          }
          var fs = document.fullscreenElement || document.webkitFullscreenElement;
          if (!fs) {
            throw new Error('Fullscreen is required for Strict Exam Mode. Allow fullscreen and try again.');
          }
        }
      }
      var attemptLocked = !!(d.is_locked || d.status === 'locked' || d.locked);
      if (d.resumed && !attemptLocked && typeof lmsShowToast === 'function') {
        lmsShowToast('Resuming where you left off', 'success');
      }
      if (d.resumed && attemptLocked && typeof lmsShowToast === 'function') {
        lmsShowToast('Exam is locked — waiting for your teacher to unlock.', 'error');
      }
      if (remainingSeconds != null && remainingSeconds <= 0) {
        await submitLmsQuiz(true);
        return;
      }
      // Apply lock state BEFORE loading questions so render() never paints unlocked UI.
      if (typeof window.lmsSetAssessmentCopyGuard === 'function') window.lmsSetAssessmentCopyGuard(true);
      setQuizActive(true);
      if (typeof window.lmsExamSecurityStart === 'function') {
        window.lmsExamSecurityStart({
          attemptId: attemptId,
          resumed: !!d.resumed,
          sessionToken: d.session_token,
          security: sec,
          requestFullscreen: !attemptLocked,
          locked: attemptLocked,
          is_locked: attemptLocked,
          status: d.status,
          lock_reason: d.lock_reason,
          getQuestionIndex: function () { return currentQ; }
        });
      }
      await loadQuestions();
      lmsOpenModal('lmsStudentModal');
      startQuizTimer();
    } catch (err) {
      setQuizActive(false);
      var msg = err.message || 'Could not start quiz';
      if (typeof lmsShowToast === 'function') lmsShowToast(msg, 'error'); else alert(msg);
    } finally {
      if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
    }
  };

  async function loadQuestions() {
    var d = await lmsApi('/api/lms/attempts/' + attemptId + '/questions');
    questions = d.questions || [];
    savedAnswers = {};
    var saved = d.saved_answers || {};
    Object.keys(saved).forEach(function (k) { savedAnswers[parseInt(k, 10)] = saved[k]; });
    if (quizDwell) {
      quizDwell.stop();
      var seed = {};
      var times = d.saved_times || {};
      Object.keys(times).forEach(function (k) {
        var qi = parseInt(k, 10);
        var q = questions[qi];
        var id = quizQid(q);
        if (id != null) seed[id] = times[k];
      });
      quizDwell.seed(seed);
    }
    currentQ = d.current_question_index != null ? d.current_question_index : 0;
    if (currentQ < 0) currentQ = 0;
    if (currentQ >= questions.length) currentQ = Math.max(0, questions.length - 1);
    show('lmsQuizTaking');
    render();
    startQuizDwell(currentQ);
  }

  function mapHtml() {
    var total = questions.length;
    if (total <= 1) return '';
    var chips = '';
    for (var i = 0; i < total; i++) {
      var st = (i === currentQ) ? 'current' : (savedAnswers[i] !== undefined ? 'answered' : 'unanswered');
      chips += '<button type="button" class="lms-qmap-cell ' + st + '" onclick="goLmsQuizQuestion(' + i + ')">' + (i + 1) + '</button>';
    }
    return '<div class="lms-qmap"><div class="lms-qmap-head">Jump to question</div><div class="lms-qmap-grid">' + chips + '</div></div>';
  }

  function render() {
    var el = document.getElementById('lmsQuizTaking');
    if (!questions.length) {
      el.innerHTML = '<p class="lms-error">No questions in this quiz.</p>' +
        '<button type="button" class="sd-btn sd-btn-outline" onclick="exitLmsQuizTaking(true)">Close</button>';
      return;
    }
    if (typeof window.lmsExamSecurityIsLocked === 'function' && window.lmsExamSecurityIsLocked()) {
      el.innerHTML =
        '<div class="lms-quiz-taking-head"><span class="lms-quiz-taking-title">' + escapeHtml(quizTitle) + '</span>' +
        (remainingSeconds != null ? '<span id="lmsQuizTimer" class="sd-timer" style="margin-left:auto;"></span>' : '') +
        '</div>' +
        '<div class="lms-exam-locked-inline"><h3>EXAM LOCKED</h3>' +
        '<p>You left the quiz (closed the browser or opened another tab). Your teacher has been notified.</p>' +
        '<p>Please wait for your teacher to unlock your exam.</p></div>';
      return;
    }
    var idx = currentQ, total = questions.length, q = questions[idx], savedIdx = savedAnswers[idx];
    var answered = Object.keys(savedAnswers).length;
    var answeredPct = Math.round(100 * answered / total);
    var opts = (q.options || []).map(function (o, oi) {
      return '<button type="button" class="sd-q-option lms-quiz-option' + (savedIdx === oi ? ' selected' : '') + '" onclick="selectLmsQuizOption(' + oi + ')">' +
        '<span class="sd-radio"></span><span class="sd-opt-label">' + escapeHtml(o.label || String.fromCharCode(65 + oi)) + '</span>' +
        '<span class="sd-opt-body">' + fmtOption(o) + '</span></button>';
    }).join('');
    var nextBtn = idx < total - 1
      ? '<button type="button" class="sd-btn sd-btn-primary" onclick="nextLmsQuizQuestion()">' + (savedIdx === undefined ? 'Skip' : 'Next') + ' <i class="fas fa-chevron-right"></i></button>' : '';
    // Submit only goes through once every question is answered; until then the button says how many are left.
    var left = total - answered;
    var submitBtn = '<button type="button" data-quiz-submit class="sd-btn lms-qz-submit ' + (left === 0 ? 'sd-btn-primary ready' : 'sd-btn-outline blue') + '"' +
      (left ? ' title="Answer all questions to submit"' : '') +
      ' onclick="submitLmsQuiz()"><i class="fas fa-paper-plane"></i> Submit Quiz' + (left ? ' (' + left + ' left)' : '') + '</button>';
    var timerCard = remainingSeconds != null
      ? '<div class="lms-qz-side-card lms-qz-timer-card"><div class="lms-qz-side-label">Time left</div>' +
        '<span id="lmsQuizTimer" class="sd-timer"></span></div>' : '';
    el.innerHTML =
      '<div class="lms-qz">' +
        '<div class="lms-qz-main">' +
          '<div class="lms-quiz-taking-head lms-qz-head">' +
            '<span class="lms-qz-chip">Question ' + (idx + 1) + ' of ' + total + '</span>' +
            (savedIdx !== undefined ? '<span class="lms-qz-chip done"><i class="fas fa-check"></i> Answered</span>' : '') +
          '</div>' +
          '<div class="lms-qz-card">' +
            '<div class="sd-q-label">Question ' + (idx + 1) + '</div>' +
            '<div class="sd-q-text lms-quiz-stem">' + fmtQuestion(q) + '</div>' +
            '<div class="lms-qz-options">' + opts + '</div>' +
          '</div>' +
          '<div class="sd-q-nav"><button type="button" class="sd-btn sd-btn-outline" onclick="prevLmsQuizQuestion()"' + (idx > 0 ? '' : ' disabled') + '><i class="fas fa-chevron-left"></i> Previous</button>' +
          '<div class="sd-q-nav-end">' + submitBtn + nextBtn + '</div></div>' +
        '</div>' +
        '<aside class="lms-qz-side">' +
          timerCard +
          '<div class="lms-qz-side-card">' +
            '<div class="lms-qz-side-label">Progress</div>' +
            '<div class="lms-qz-progress-num"><b>' + answered + '</b> / ' + total + ' answered</div>' +
            '<div class="sd-qprogress"><div class="sd-track"><div class="sd-fill" style="width:' + answeredPct + '%;"></div></div></div>' +
          '</div>' +
          (total > 1 ? '<div class="lms-qz-side-card">' + mapHtml() +
            '<div class="lms-qz-legend"><span><i class="cur"></i>Current</span><span><i class="ans"></i>Answered</span><span><i class="una"></i>Not answered</span></div>' +
          '</div>' : '') +
        '</aside>' +
      '</div>';
    updateQuizTimerDisplay();
    typeset(el);
  }

  window.selectLmsQuizOption = function (optIdx) {
    if (typeof window.lmsExamSecurityIsLocked === 'function' && window.lmsExamSecurityIsLocked()) return;
    savedAnswers[currentQ] = optIdx;
    render();
    var q = questions[currentQ];
    if (!q || !attemptId) return;
    if (typeof window.lmsExamSecurityMarkSaving === 'function') window.lmsExamSecurityMarkSaving();
    fetch('/api/lms/attempts/' + attemptId + '/answer', {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question_id: q.question_id || q.id,
        selected_option_index: optIdx,
        time_spent_seconds: quizDwellSeconds(currentQ)
      })
    }).then(function (r) {
      if (r && r.ok) {
        if (typeof window.lmsExamSecurityMarkSaved === 'function') window.lmsExamSecurityMarkSaved();
      } else if (typeof window.lmsExamSecurityMarkOffline === 'function') {
        window.lmsExamSecurityMarkOffline();
      }
    }).catch(function () {
      if (typeof window.lmsExamSecurityMarkOffline === 'function') window.lmsExamSecurityMarkOffline();
    });
  };
  window.goLmsQuizQuestion = function (i) {
    if (i < 0 || i >= questions.length || i === currentQ) return;
    syncQuizDwell(currentQ);
    currentQ = i;
    render();
    startQuizDwell(currentQ);
  };
  window.nextLmsQuizQuestion = function () {
    if (currentQ >= questions.length - 1) return;
    syncQuizDwell(currentQ);
    currentQ++;
    render();
    startQuizDwell(currentQ);
  };
  window.prevLmsQuizQuestion = function () {
    if (currentQ <= 0) return;
    syncQuizDwell(currentQ);
    currentQ--;
    render();
    startQuizDwell(currentQ);
  };

  function unansweredIndexes() {
    var out = [];
    for (var i = 0; i < questions.length; i++) if (savedAnswers[i] === undefined) out.push(i);
    return out;
  }

  // Tell the student which questions are left and take them to the first one. Returns true when blocked.
  function blockIfUnanswered(missing) {
    if (!missing.length) return false;
    var list = missing.slice(0, 12).map(function (i) { return i + 1; }).join(', ') + (missing.length > 12 ? '…' : '');
    var msg = 'Please answer every question before you submit. ' + missing.length + ' question' +
      (missing.length === 1 ? ' is' : 's are') + ' still unanswered: ' + list + '.';
    currentQ = missing[0];
    render();
    if (typeof lmsShowToast === 'function') lmsShowToast(msg, 'error'); else alert(msg);
    return true;
  }

  // Selections are saved as they are made, but a save can fail silently on a weak connection.
  // Send them all again so the server's "everything answered" check sees what the student sees.
  async function flushAnswers() {
    if (quizDwell) syncQuizDwell(currentQ);
    for (var i = 0; i < questions.length; i++) {
      var q = questions[i];
      if (savedAnswers[i] === undefined || !q) continue;
      try {
        await lmsApi('/api/lms/attempts/' + attemptId + '/answer', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            question_id: q.question_id || q.id,
            selected_option_index: savedAnswers[i],
            time_spent_seconds: quizDwellSeconds(i)
          })
        });
      } catch (e) { /* the submit below reports anything still missing */ }
    }
  }

  function quizTimesPayload() {
    var out = {};
    if (!quizDwell) return out;
    for (var i = 0; i < questions.length; i++) {
      var id = quizQid(questions[i]);
      if (id == null) continue;
      var sec = quizDwellSeconds(i);
      if (sec > 0) out[id] = sec;
    }
    return out;
  }

  window.submitLmsQuiz = async function (timeExpired) {
    if (submitting) return;
    // Every question must be answered before a quiz can be submitted. Only the timer
    // running out submits an unfinished quiz (the server applies the same rule).
    if (!timeExpired && blockIfUnanswered(unansweredIndexes())) return;
    submitting = true;
    clearQuizTimer();
    // Make it obvious the click registered: the button itself turns into a spinner, every
    // control locks, and the full-screen spinner stays up long enough to be seen (a fast
    // submit used to flash it for a split second and then swap in the score with no cue).
    var takingEl = document.getElementById('lmsQuizTaking');
    if (takingEl) {
      takingEl.querySelectorAll('button').forEach(function (b) { b.disabled = true; });
      var sBtn = takingEl.querySelector('[data-quiz-submit]');
      if (sBtn) sBtn.innerHTML = '<span class="sd-btn-spin" aria-hidden="true"></span> Submitting…';
    }
    var startedAt = Date.now();
    if (typeof showWaitOverlay === 'function') {
      showWaitOverlay(timeExpired ? 'Time is up — submitting quiz...' : 'Submitting your quiz...', { sticky: true });
    }
    try {
      if (!timeExpired) await flushAnswers();
      else if (quizDwell) syncQuizDwell(currentQ);
      var payload = timeExpired ? { time_expired: true } : {};
      var times = quizTimesPayload();
      if (Object.keys(times).length) payload.question_times = times;
      var d = await lmsApi('/api/lms/attempts/' + attemptId + '/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (quizDwell) quizDwell.stop();
      if (typeof window.lmsExamSecurityStop === 'function') window.lmsExamSecurityStop();
      setQuizActive(false);
      var shownFor = Date.now() - startedAt;
      if (shownFor < 700) await new Promise(function (r) { setTimeout(r, 700 - shownFor); });
      if (typeof window.lmsSetAssessmentCopyGuard === 'function') window.lmsSetAssessmentCopyGuard(false);
      // Close the question screen for good (as the diagnostic does); the score gets its own screen.
      if (takingEl) takingEl.innerHTML = '';
      show('lmsQuizResult');
      setTitle(timeExpired ? 'Time\'s Up — Quiz Submitted' : 'Quiz Submitted');
      document.getElementById('lmsQuizResult').innerHTML =
        '<div class="sd-submit-ok" role="status" data-testid="quiz-submitted"><i class="fas fa-check-circle"></i> ' +
        (timeExpired ? 'Time ran out — your quiz was submitted automatically.' : 'Your quiz was submitted successfully.') + '</div>' +
        '<div style="text-align:center;font-size:2rem;">' + (timeExpired ? '⏰' : '🎉') + '</div>' +
        '<h3 style="text-align:center;margin:6px 0 14px;color:var(--sd-blue-900);">' +
        (timeExpired ? 'Time\'s Up — Quiz Submitted' : 'Quiz Complete') + '</h3>' +
        '<div class="sd-result">' + window.sdRenderResultBody(d) + '</div>' +
        '<p class="sd-desc" style="text-align:center;margin-top:12px;">Your learning path may update based on results.</p>' +
        '<div style="display:flex;justify-content:center;margin-top:14px;"><button type="button" class="sd-btn sd-btn-primary" onclick="exitLmsQuizTaking()">Back to My Classes</button></div>';
      // The result replaces the question in place - bring it into view.
      var modalBody = document.querySelector('#lmsStudentModal .lms-modal-body');
      if (modalBody) modalBody.scrollTop = 0;
      if (typeof lmsShowToast === 'function') lmsShowToast('Quiz submitted', 'success');
      window.loadLmsStudentDashboard();
    } catch (err) {
      submitting = false;
      var serverMissing = err && err.code === 'unanswered_questions' && err.details && err.details.unanswered_questions;
      if (serverMissing && serverMissing.length) {
        // The server has no answer for these (a save did not reach it): let the student pick them again.
        serverMissing.forEach(function (n) { delete savedAnswers[n - 1]; });
        if (!timeExpired) startQuizTimer();
        blockIfUnanswered(unansweredIndexes());
      } else {
        if (typeof lmsShowToast === 'function') lmsShowToast((err.message || 'Submit failed') + ' — your quiz was NOT submitted. Please try again.', 'error');
        if (!timeExpired) startQuizTimer();
        // unlock the controls again (render() rebuilds them) so Submit can be pressed again
        if (questions.length) render();
      }
    } finally {
      if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
    }
  };
})();
