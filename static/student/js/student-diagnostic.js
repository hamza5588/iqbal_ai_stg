/* 01 Diagnostic hub. There is one platform diagnostic per student (GET /api/lms/diagnostics/default);
   "Taken" rows are the student's submitted diagnostic attempts (GET /api/lms/students/me/attempts),
   expanded inline with GET /api/lms/attempts/:id/results. Taking it runs lms-student.js (#diagnostic-quiz). */
(function () {
  var state = { diag: null, diagError: '', attempts: [], tab: 'available', open: {}, results: {}, loaded: false };

  function subjectOf() { return (state.diag && state.diag.subject) || 'Math'; }
  function gradeOf() { return (state.diag && state.diag.grade_level) || window.sdGradeLabel || '—'; }

  function buildRows() {
    var rows = [];
    var d = state.diag;
    var inProgress = d && (d.in_progress_attempt_id || d.attempt_status === 'in_progress');
    var completed = d && (d.diagnostic_completed || d.any_diagnostic_completed);
    if (d && (!completed || inProgress)) {
      rows.push({ kind: 'current', status: inProgress ? 'in_progress' : 'available', title: 'Diagnostic Assessment' });
    }
    state.attempts
      .filter(function (a) { return a.assessment_type === 'diagnostic' && a.status === 'submitted'; })
      .forEach(function (a, i) {
        rows.push({ kind: 'attempt', status: 'taken', attempt: a, latest: i === 0, title: 'Diagnostic Assessment' });
      });
    // Completed per profile but no attempt row came back (e.g. history trimmed): still offer the results.
    if (d && completed && !inProgress && !rows.some(function (r) { return r.kind === 'attempt'; }) && d.latest_attempt_id) {
      rows.push({ kind: 'attempt', status: 'taken', latest: true, title: 'Diagnostic Assessment',
        attempt: { attempt_id: d.latest_attempt_id, score_percent: d.score_percent, submitted_at: null } });
    }
    return rows;
  }

  function matchesFilters(row) {
    var q = (document.getElementById('sdDiagSearch') || {}).value || '';
    var subj = (document.getElementById('sdDiagSubject') || {}).value || '';
    var grade = (document.getElementById('sdDiagGrade') || {}).value || '';
    var hay = (row.title + ' ' + subjectOf() + ' ' + gradeOf()).toLowerCase();
    if (q && hay.indexOf(q.trim().toLowerCase()) === -1) return false;
    if (subj && subj !== subjectOf()) return false;
    if (grade && grade !== String(gradeOf())) return false;
    return true;
  }

  function fillFilterOptions() {
    [['sdDiagSubject', subjectOf(), 'All subjects'], ['sdDiagGrade', String(gradeOf()), 'All grades']].forEach(function (x) {
      var sel = document.getElementById(x[0]);
      if (!sel || !state.diag) return;
      var cur = sel.value;
      sel.innerHTML = '<option value="">' + x[2] + '</option><option value="' + escapeHtml(x[1]) + '">' + escapeHtml(x[1]) + '</option>';
      sel.value = cur === x[1] ? cur : '';
    });
  }

  function rowHtml(row, idx) {
    var num = String(idx + 1).padStart(2, '0');
    var meta, pill, actions, barCls = '', avCls = '';
    if (row.kind === 'current') {
      var d = state.diag;
      meta = '<div><span class="sd-label">Subject</span><b>' + escapeHtml(subjectOf()) + '</b></div>' +
        '<div><span class="sd-label">Grade</span><b>' + escapeHtml(gradeOf()) + '</b></div>' +
        '<div><span class="sd-label">Assigned by</span><b>Admin</b></div>' +
        '<div><span class="sd-label">Questions</span><b>' + escapeHtml(String(d.question_count || '—')) + '</b></div>' +
        (d.time_limit_minutes ? '<div><span class="sd-label">Time limit</span><b><i class="far fa-clock"></i> ' + Math.round(d.time_limit_minutes) + ' min</b></div>' : '');
      if (row.status === 'in_progress') {
        pill = '<span class="sd-pill amber">In progress</span>';
        actions = '<button type="button" class="sd-btn sd-btn-primary sd-btn-sm" onclick="openLmsDiagnostic()">Continue Quiz <i class="fas fa-chevron-right"></i></button>';
      } else {
        pill = '<span class="sd-pill green">Available</span>';
        actions = '<button type="button" class="sd-btn sd-btn-primary sd-btn-sm" data-testid="diag-start" onclick="openLmsDiagnostic()">Start Quiz <i class="fas fa-chevron-right"></i></button>';
      }
    } else {
      var a = row.attempt;
      var open = !!state.open[a.attempt_id];
      barCls = ' grey'; avCls = ' grey';
      meta = '<div><span class="sd-label">Subject</span><b>' + escapeHtml(subjectOf()) + '</b></div>' +
        '<div><span class="sd-label">Grade</span><b>' + escapeHtml(gradeOf()) + '</b></div>' +
        '<div><span class="sd-label">Assigned by</span><b>Admin</b></div>' +
        '<div><span class="sd-label">Completed on</span><b><i class="far fa-calendar"></i> ' + escapeHtml(window.sdFmtDate(a.submitted_at)) + '</b></div>' +
        (a.score_percent != null ? '<div><span class="sd-label">Score</span><b>' + Math.round(a.score_percent) + '%</b></div>' : '');
      pill = '<span class="sd-pill grey">Taken</span>';
      actions = '<button type="button" class="sd-btn sd-btn-outline blue sd-btn-sm" aria-expanded="' + open + '" onclick="sdToggleDiagResult(' + a.attempt_id + ')">' +
        '<i class="fas fa-chart-bar"></i> View Results <i class="fas fa-chevron-' + (open ? 'up' : 'down') + '"></i></button>';
    }
    var result = '';
    if (row.kind === 'attempt' && state.open[row.attempt.attempt_id]) {
      var r = state.results[row.attempt.attempt_id];
      result = '<div class="sd-result"><div class="sd-result-banner"><span class="sd-title">Quiz Result</span>' +
        '<button type="button" class="sd-close-x" aria-label="Close result" onclick="sdToggleDiagResult(' + row.attempt.attempt_id + ')"><i class="fas fa-times"></i></button></div>' +
        (r === undefined ? '<div class="sd-spinner"></div>'
          : (r && r.__error ? '<p class="sd-empty">' + escapeHtml(r.__error) + '</p>'
            : window.sdRenderResultBody(r) +
              (row.latest ? '<div class="sd-result-actions">' +
                ((r.weak_topics || []).length
                  ? '<button type="button" class="sd-btn sd-btn-primary sd-btn-sm" onclick="openDeficiencyChat()">Start Learning Chat</button>'
                  : '<button type="button" class="sd-btn sd-btn-primary sd-btn-sm" onclick="openDeficiencyChat(false, \'enrichment\')">Start challenge</button>') +
                '<button type="button" class="sd-btn sd-btn-outline sd-btn-sm" onclick="sdRetakeDiagnostic()">Retake with new questions</button></div>' : ''))) +
        '</div>';
    }
    return '<div class="sd-list-row" data-diag-row="' + row.kind + '"><div class="sd-num-bar' + barCls + '">' + num + '</div><div class="sd-row-body">' +
      '<div class="sd-row-top"><div class="sd-title-chip"><div class="sd-avatar-sm' + avCls + '"><i class="far fa-file-alt"></i></div>' +
      '<div><h3>' + escapeHtml(row.title) + '</h3><div class="sd-meta-line">' + meta + '</div></div></div>' +
      '<div class="sd-side-actions">' + pill + actions + '</div></div>' + result + '</div></div>';
  }

  window.sdRenderDiagnosticList = function () {
    var list = document.getElementById('sdDiagList');
    if (!list) return;
    if (!state.loaded) { list.innerHTML = '<div class="sd-spinner"></div>'; return; }
    var rows = buildRows();
    var counts = { all: rows.length, available: 0, taken: 0 };
    rows.forEach(function (r) { if (r.status === 'taken') counts.taken++; else counts.available++; });
    document.querySelectorAll('[data-count]').forEach(function (el) { el.textContent = counts[el.getAttribute('data-count')]; });
    document.querySelectorAll('[data-diag-tab]').forEach(function (el) {
      el.classList.toggle('active', el.getAttribute('data-diag-tab') === state.tab);
      el.setAttribute('aria-selected', String(el.getAttribute('data-diag-tab') === state.tab));
    });
    var shown = rows.filter(function (r) {
      if (state.tab === 'available' && r.status === 'taken') return false;
      if (state.tab === 'taken' && r.status !== 'taken') return false;
      return matchesFilters(r);
    });
    if (!state.diag && state.diagError) {
      list.innerHTML = '<div class="sd-empty"><b>No diagnostic available</b>' +
        escapeHtml(state.diagError.replace(/^No diagnostic available\.?\s*/i, '')) + '</div>' +
        shown.map(rowHtml).join('');
      return;
    }
    if (!shown.length) {
      var msg = state.tab === 'available'
        ? '<b>Nothing waiting for you</b>You have completed the diagnostic. Open the <a href="#" onclick="sdSetDiagTab(\'taken\');return false;" style="color:var(--sd-blue-700);font-weight:700;">Taken</a> tab to see your results.'
        : (state.tab === 'taken' ? '<b>No results yet</b>Your diagnostic results appear here after you submit.' : '<b>No diagnostics match your search.</b>');
      list.innerHTML = '<div class="sd-empty">' + msg + '</div>';
      return;
    }
    list.innerHTML = shown.map(rowHtml).join('');
    if (window.lmsTypesetMath) list.querySelectorAll('.sd-result').forEach(function (el) { window.lmsTypesetMath(el); });
    // Any expanded row whose result isn't loaded (first open, or cleared by a refresh) fetches it now.
    shown.forEach(function (r) {
      if (r.kind === 'attempt' && state.open[r.attempt.attempt_id]) fetchResult(r.attempt.attempt_id);
    });
  };

  var inflight = {};
  function fetchResult(attemptId) {
    if (state.results[attemptId] !== undefined || inflight[attemptId]) return;
    inflight[attemptId] = true;
    lmsApi('/api/lms/attempts/' + attemptId + '/results')
      .then(function (r) { state.results[attemptId] = r; })
      .catch(function (e) { state.results[attemptId] = { __error: e.message || 'Could not load result' }; })
      .finally(function () { inflight[attemptId] = false; window.sdRenderDiagnosticList(); });
  }

  window.sdSetDiagTab = function (tab) { state.tab = tab; window.sdRenderDiagnosticList(); };

  window.sdToggleDiagResult = function (attemptId) {
    state.open[attemptId] = !state.open[attemptId];
    window.sdRenderDiagnosticList();
  };

  window.sdRetakeDiagnostic = function () {
    if (typeof window.sdOpenDiagnosticView === 'function') window.sdOpenDiagnosticView();
    if (typeof lmsRetakeDiagnostic === 'function') lmsRetakeDiagnostic();
  };

  async function load() {
    var diagP = lmsApi('/api/lms/diagnostics/default').then(function (d) { state.diag = d; state.diagError = ''; })
      .catch(function (e) { state.diag = null; state.diagError = e.message || 'Ask your admin to upload the diagnostic assessment.'; });
    var attP = lmsApi('/api/lms/students/me/attempts').then(function (a) { state.attempts = Array.isArray(a) ? a : []; })
      .catch(function () { state.attempts = []; });
    await Promise.all([diagP, attP]);
    state.loaded = true;
    // Default tab: Available while something is waiting, otherwise Taken.
    if (!state.touchedTab) {
      var rows = buildRows();
      state.tab = rows.some(function (r) { return r.status !== 'taken'; }) ? 'available' : (rows.length ? 'taken' : 'available');
    }
    fillFilterOptions();
    window.sdRenderDiagnosticList();
  }
  var _origSetTab = window.sdSetDiagTab;
  window.sdSetDiagTab = function (tab) { state.touchedTab = true; _origSetTab(tab); };

  window.sdRefreshDiagnostic = function () { state.results = {}; load(); };
  window.sdViews = window.sdViews || {};
  window.sdViews.diagnostic = { onShow: load };
})();
