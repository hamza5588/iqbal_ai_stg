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

  function show(which) {
    ['lmsAssignmentList', 'lmsQuizTaking', 'lmsQuizResult'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.classList.toggle('hidden', id !== which);
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
  window.closeLmsStudentHub = function () {
    clearQuizTimer();
    if (typeof window.lmsSetAssessmentCopyGuard === 'function') window.lmsSetAssessmentCopyGuard(false);
    lmsCloseModal('lmsStudentModal');
    attemptId = null; questions = []; currentQ = 0; savedAnswers = {};
    remainingSeconds = null; submitting = false;
    show('lmsAssignmentList');
    if (typeof window.sdReloadClasses === 'function') window.sdReloadClasses();
  };
  window.exitLmsQuizTaking = window.closeLmsStudentHub;

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
        throw new Error((errObj && typeof errObj === 'object' && errObj.message) ? errObj.message : (body.error || body.message || 'Start failed'));
      }
      var d = body.data || body;
      attemptId = d.attempt_id;
      var rem = d.remaining_seconds;
      if (rem == null && d.time_limit_minutes != null) {
        rem = Math.max(0, Math.floor(Number(d.time_limit_minutes) * 60));
      }
      remainingSeconds = rem != null ? Math.max(0, Math.floor(Number(rem))) : null;
      if (d.resumed && typeof lmsShowToast === 'function') lmsShowToast('Resuming where you left off', 'success');
      if (remainingSeconds != null && remainingSeconds <= 0) {
        await submitLmsQuiz(true);
        return;
      }
      await loadQuestions();
      if (typeof window.lmsSetAssessmentCopyGuard === 'function') window.lmsSetAssessmentCopyGuard(true);
      lmsOpenModal('lmsStudentModal');
      startQuizTimer();
    } catch (err) {
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
    currentQ = d.current_question_index != null ? d.current_question_index : 0;
    if (currentQ < 0) currentQ = 0;
    if (currentQ >= questions.length) currentQ = Math.max(0, questions.length - 1);
    show('lmsQuizTaking');
    render();
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
        '<button type="button" class="sd-btn sd-btn-outline" onclick="exitLmsQuizTaking()">Close</button>';
      return;
    }
    var idx = currentQ, total = questions.length, q = questions[idx], savedIdx = savedAnswers[idx];
    var pct = Math.round(100 * (idx + 1) / total);
    var answered = Object.keys(savedAnswers).length;
    var opts = (q.options || []).map(function (o, oi) {
      return '<button type="button" class="sd-q-option lms-quiz-option' + (savedIdx === oi ? ' selected' : '') + '" onclick="selectLmsQuizOption(' + oi + ')">' +
        '<span class="sd-radio"></span><span class="sd-opt-label">' + escapeHtml(o.label || String.fromCharCode(65 + oi)) + '.</span>' +
        '<span class="sd-opt-body">' + fmtOption(o) + '</span></button>';
    }).join('');
    var nextBtn = idx < total - 1
      ? '<button type="button" class="sd-btn sd-btn-primary" onclick="nextLmsQuizQuestion()">' + (savedIdx === undefined ? 'Skip' : 'Next') + ' <i class="fas fa-chevron-right"></i></button>' : '';
    var submitBtn = '<button type="button" data-quiz-submit class="sd-btn ' + (idx === total - 1 ? 'sd-btn-primary' : 'sd-btn-outline blue') + '" onclick="submitLmsQuiz()">Submit Quiz</button>';
    var timerHtml = remainingSeconds != null
      ? '<span id="lmsQuizTimer" class="sd-timer" style="margin-left:auto;"></span>' : '';
    el.innerHTML =
      '<div class="lms-quiz-taking-head"><span class="lms-quiz-taking-title">' + escapeHtml(quizTitle) + '</span>' + timerHtml + '</div>' +
      '<div class="sd-qprogress" style="width:100%;margin-bottom:16px;"><div class="sd-qp-top"><span>Question ' + (idx + 1) + ' of ' + total +
      (answered ? ' · ' + answered + ' answered' : '') + '</span><b>' + pct + '%</b></div>' +
      '<div class="sd-track"><div class="sd-fill" style="width:' + pct + '%;"></div></div></div>' +
      '<div class="sd-q-label">Question ' + (idx + 1) + '</div>' +
      '<div class="sd-q-text lms-quiz-stem">' + fmtQuestion(q) + '</div>' +
      opts + mapHtml() +
      '<div class="sd-q-nav"><button type="button" class="sd-btn sd-btn-outline" onclick="prevLmsQuizQuestion()"' + (idx > 0 ? '' : ' disabled') + '>Previous</button>' +
      '<div class="sd-q-nav-end">' + submitBtn + nextBtn + '</div></div>';
    updateQuizTimerDisplay();
    typeset(el);
  }

  window.selectLmsQuizOption = function (optIdx) {
    savedAnswers[currentQ] = optIdx;
    render();
    var q = questions[currentQ];
    if (!q || !attemptId) return;
    fetch('/api/lms/attempts/' + attemptId + '/answer', {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question_id: q.question_id || q.id, selected_option_index: optIdx })
    }).catch(function () { /* keep local selection */ });
  };
  window.goLmsQuizQuestion = function (i) { if (i >= 0 && i < questions.length) { currentQ = i; render(); } };
  window.nextLmsQuizQuestion = function () { if (currentQ < questions.length - 1) { currentQ++; render(); } };
  window.prevLmsQuizQuestion = function () { if (currentQ > 0) { currentQ--; render(); } };

  window.submitLmsQuiz = async function (timeExpired) {
    if (submitting) return;
    if (!timeExpired) {
      var missing = questions.length - Object.keys(savedAnswers).length;
      if (missing > 0) {
        var msg = 'You have ' + missing + ' unanswered question' + (missing === 1 ? '' : 's') +
          '. Are you sure you want to submit?\n\nUnanswered questions score 0. Your answered questions still count.';
        var ok = typeof showInAppConfirm === 'function'
          ? await showInAppConfirm(msg, { title: 'Submit quiz?', confirmLabel: 'Submit', cancelLabel: 'Go back', iconClass: 'fas fa-exclamation-circle' })
          : window.confirm(msg);
        if (!ok) return;
      }
    }
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
      var payload = timeExpired ? { time_expired: true } : {};
      var d = await lmsApi('/api/lms/attempts/' + attemptId + '/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      var shownFor = Date.now() - startedAt;
      if (shownFor < 700) await new Promise(function (r) { setTimeout(r, 700 - shownFor); });
      if (typeof window.lmsSetAssessmentCopyGuard === 'function') window.lmsSetAssessmentCopyGuard(false);
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
      if (typeof lmsShowToast === 'function') lmsShowToast((err.message || 'Submit failed') + ' — your quiz was NOT submitted. Please try again.', 'error');
      submitting = false;
      // unlock the controls again (render() rebuilds them) so Submit can be pressed again
      if (questions.length) render();
    } finally {
      if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
    }
  };
})();
