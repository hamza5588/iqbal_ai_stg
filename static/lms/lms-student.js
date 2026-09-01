/** Student LMS — diagnostic, learning path actions */
(function () {
  var diagState = { assessmentId: null, attemptId: null, questions: [], current: 0, answers: {} };

  function ensureDiagnosticModal() {
    if (document.getElementById('lmsDiagnosticModal')) return;
    var html = '<div id="lmsDiagnosticModal" class="lms-modal-backdrop" onclick="if(event.target===this)closeLmsDiagnostic()">' +
      '<div class="lms-modal lms-modal-lg">' +
      '<div class="lms-modal-header"><h2>Diagnostic Assessment</h2>' +
      '<button type="button" class="lms-modal-close" onclick="closeLmsDiagnostic()">&times;</button></div>' +
      '<div class="lms-modal-body" id="lmsDiagBody"><div class="lms-spinner"></div></div>' +
      '</div></div>';
    document.body.insertAdjacentHTML('beforeend', html);
  }

  window.openLmsDiagnostic = async function () {
    ensureDiagnosticModal();
    lmsOpenModal('lmsDiagnosticModal');
    var body = document.getElementById('lmsDiagBody');
    body.innerHTML = '<div class="lms-spinner"></div><p class="lms-status" style="text-align:center">Loading diagnostic...</p>';
    try {
      var diag = await lmsApi('/api/lms/diagnostics/default');
      if (diag.diagnostic_completed) {
        body.innerHTML = '<div class="lms-card"><p>You already completed this diagnostic' +
          (diag.title ? ': <strong>' + escapeHtml(diag.title) + '</strong>' : '') + '.</p>' +
          '<button type="button" class="lms-btn lms-btn-primary" onclick="retakeLmsDiagnostic(' + diag.id + ')">Retake Diagnostic</button></div>';
        return;
      }
      var subtitle = diag.source === 'teacher'
        ? 'From your teacher'
        : 'Platform diagnostic';
      await startDiagnosticQuiz(diag.id, diag.title || 'Diagnostic Assessment', diag.question_count, subtitle);
    } catch (err) {
      body.innerHTML = '<p class="lms-error">' + escapeHtml(err.message) + '</p>' +
        '<p class="lms-status">Ask your teacher for a class assignment, or contact admin to seed the diagnostic.</p>';
    }
  };

  window.closeLmsDiagnostic = function () {
    lmsCloseModal('lmsDiagnosticModal');
    diagState = { assessmentId: null, attemptId: null, questions: [], current: 0, answers: {} };
  };

  window.retakeLmsDiagnostic = function (assessmentId) {
    startDiagnosticQuiz(assessmentId, 'Diagnostic Assessment', null);
  };

  async function startDiagnosticQuiz(assessmentId, title, qCount, subtitle) {
    diagState.assessmentId = assessmentId;
    var body = document.getElementById('lmsDiagBody');
    body.innerHTML = '<p class="lms-status">Starting ' + escapeHtml(title) + '...</p>' +
      (subtitle ? '<p class="lms-status" style="margin-top:4px;">' + escapeHtml(subtitle) + '</p>' : '');
    try {
      var start = await lmsApi('/api/lms/quizzes/' + assessmentId + '/start', { method: 'POST' });
      diagState.attemptId = start.attempt_id;
      var qData = await lmsApi('/api/lms/attempts/' + start.attempt_id + '/questions');
      diagState.questions = qData.questions || qData || [];
      diagState.current = 0;
      diagState.answers = {};
      if (!diagState.questions.length) {
        body.innerHTML = '<p class="lms-error">No questions in this diagnostic.</p>';
        return;
      }
      renderDiagnosticQuestion();
    } catch (err) {
      body.innerHTML = '<p class="lms-error">' + escapeHtml(err.message) + '</p>';
    }
  }

  function renderDiagnosticQuestion() {
    var body = document.getElementById('lmsDiagBody');
    var idx = diagState.current;
    var total = diagState.questions.length;
    var item = diagState.questions[idx];
    var q = item.question || item;
    var pct = Math.round(100 * (idx + 1) / total);
    var opts = (q.options || []).map(function (o, oi) {
      var sel = diagState.answers[idx] === oi ? ' selected' : '';
      return '<button type="button" class="lms-quiz-option' + sel + '" onclick="selectDiagOption(' + idx + ',' + oi + ')">' +
        '<strong>' + escapeHtml(o.label || String.fromCharCode(65 + oi)) + '.</strong> ' + escapeHtml(o.text || '') + '</button>';
    }).join('');
    var nav = '';
    if (idx > 0) nav += '<button type="button" class="lms-btn lms-btn-secondary" onclick="prevDiagQuestion()">Back</button> ';
    if (idx < total - 1) {
      nav += '<button type="button" class="lms-btn lms-btn-primary" onclick="nextDiagQuestion()"' +
        (diagState.answers[idx] === undefined ? ' disabled' : '') + '>Next</button>';
    } else {
      nav += '<button type="button" class="lms-btn lms-btn-primary" onclick="submitLmsDiagnostic()"' +
        (diagState.answers[idx] === undefined ? ' disabled' : '') + '>Submit Diagnostic</button>';
    }
    body.innerHTML =
      '<div class="lms-quiz-progress"><div class="lms-quiz-progress-bar" style="width:' + pct + '%"></div></div>' +
      '<p class="lms-status">Question ' + (idx + 1) + ' of ' + total + '</p>' +
      '<h3 style="font-size:1rem;font-weight:700;margin:12px 0;">' + escapeHtml(q.question_text || '') + '</h3>' +
      opts +
      '<div class="lms-modal-footer" style="border:none;padding:16px 0 0;margin:0;">' + nav + '</div>';
    if (window.MathJax && window.MathJax.typesetPromise) MathJax.typesetPromise([body]).catch(function () {});
  }

  window.selectDiagOption = function (qIdx, optIdx) {
    diagState.answers[qIdx] = optIdx;
    renderDiagnosticQuestion();
  };
  window.nextDiagQuestion = function () {
    if (diagState.current < diagState.questions.length - 1) { diagState.current++; renderDiagnosticQuestion(); }
  };
  window.prevDiagQuestion = function () {
    if (diagState.current > 0) { diagState.current--; renderDiagnosticQuestion(); }
  };

  window.submitLmsDiagnostic = async function () {
    var body = document.getElementById('lmsDiagBody');
    body.innerHTML = '<div class="lms-spinner"></div><p class="lms-status" style="text-align:center">Scoring your diagnostic...</p>';
    try {
      for (var i = 0; i < diagState.questions.length; i++) {
        var item = diagState.questions[i];
        var qid = item.question_id || (item.question && item.question.id);
        if (diagState.answers[i] !== undefined && qid) {
          await lmsApi('/api/lms/attempts/' + diagState.attemptId + '/answer', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question_id: qid, selected_option_index: diagState.answers[i] })
          });
        }
      }
      var result = await lmsApi('/api/lms/attempts/' + diagState.attemptId + '/submit', { method: 'POST' });
      renderDiagnosticResults(result);
      if (typeof loadLmsStudentDashboard === 'function') loadLmsStudentDashboard();
      if (typeof initLmsOnboardingGate === 'function') {
        document.getElementById('lmsOnboardingModal') && lmsCloseModal('lmsOnboardingModal');
      }
    } catch (err) {
      body.innerHTML = '<p class="lms-error">' + escapeHtml(err.message) + '</p>';
    }
  };

  function renderDiagnosticResults(result) {
    var body = document.getElementById('lmsDiagBody');
    var score = result.score_percent != null ? Math.round(result.score_percent) : '—';
    var weak = result.weak_topics || [];
    var strong = result.strong_topics || [];
    var weakHtml = weak.length
      ? weak.map(function (t) {
          return '<div class="lms-topic-chip weak"><strong>' + Math.round(t.score_percent || 0) + '%</strong>' + escapeHtml(t.topic_name || t.name || 'Topic') + '</div>';
        }).join('')
      : '<p class="lms-status">No weak topics detected yet.</p>';
    var strongHtml = strong.length
      ? strong.map(function (t) {
          return '<div class="lms-topic-chip strong"><strong>' + Math.round(t.score_percent || 0) + '%</strong>' + escapeHtml(t.topic_name || t.name || 'Topic') + '</div>';
        }).join('')
      : '';
    body.innerHTML =
      '<div style="text-align:center;margin-bottom:20px;">' +
      '<div style="font-size:2.5rem;font-weight:800;color:#166534;">' + score + '%</div>' +
      '<p class="lms-status">Overall diagnostic score</p></div>' +
      (weak.length ? '<h4 style="color:#991b1b;margin:0 0 8px;">Areas to improve</h4><div class="lms-topic-grid">' + weakHtml + '</div>' : '') +
      (strong.length ? '<h4 style="color:#166534;margin:16px 0 8px;">Strong areas</h4><div class="lms-topic-grid">' + strongHtml + '</div>' : '') +
      (weak.length ? '<p class="lms-status" style="margin-top:12px;">Practice weak areas in Learning Chat — questions from your teacher\'s PDF, one at a time.</p>' : '') +
      '<div class="lms-modal-footer" style="border:none;padding-top:20px;display:flex;gap:8px;flex-wrap:wrap;">' +
      (weak.length ? '<button type="button" class="lms-btn lms-btn-primary" onclick="closeLmsDiagnostic();openDeficiencyChat()">Start Learning Chat</button>' : '') +
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
    return '<div class="lms-path-panel"><h3>My Learning Path <span style="font-weight:400;color:#64748b;font-size:.875rem;">(' + pct + '% done)</span></h3>' +
      '<ol class="lms-path-steps">' + steps + '</ol></div>';
  };

  /* Patch renderLmsLearningPath if on student dashboard */
  document.addEventListener('DOMContentLoaded', function () {
    if (typeof renderLmsLearningPath === 'function') {
      window._renderLmsLearningPathOriginal = renderLmsLearningPath;
    }
    window.renderLmsLearningPath = renderLmsLearningPathEnhanced;
  });
})();
