/* 03 My Learning Path. Topic rows = the student's assessed topics (dashboard `mastery`; Weak ones are the
   dashboard `weak_topics`). Start Learning Path opens Learning Chat (lms-deficiency-chat.js); Guided practice
   opens the per-topic practice panel (lms-panels.js). Path steps + Quiz History keep the old overview's actions. */
(function () {
  var state = { open: {}, attempts: null };
  var STATUS_LABEL = { mastered: 'Mastered', improving: 'Improving', needs_practice: 'Needs practice', weak: 'Weak' };

  function topicRows() {
    var d = window.sdDashboard || {};
    var weakIds = {};
    (d.weak_topics || []).forEach(function (t) { weakIds[t.topic_id] = true; });
    var rows = (d.mastery || []).map(function (m) {
      return Object.assign({}, m, { isWeak: !!weakIds[m.topic_id] });
    });
    // Diagnostic-only fallback (no live mastery yet): weak topics still get a row.
    if (!rows.length) rows = (d.weak_topics || []).map(function (t) { return Object.assign({}, t, { isWeak: true }); });
    rows.sort(function (a, b) {
      if (a.isWeak !== b.isWeak) return a.isWeak ? -1 : 1;
      return (a.score_percent || 0) - (b.score_percent || 0);
    });
    return rows;
  }

  function topicRowHtml(t) {
    var name = t.topic_name || ('Topic #' + t.topic_id);
    var pct = t.score_percent != null ? Math.round(t.score_percent) : null;
    var open = !!state.open[t.topic_id];
    var meta = '<div><span class="sd-label">Subject</span><b>Math</b></div>' +
      '<div><span class="sd-label">Mastery</span><b>' + escapeHtml(STATUS_LABEL[t.mastery_status] || t.mastery_status || '—') + '</b></div>' +
      '<div><span class="sd-label">Score</span><b>' + (pct != null ? pct + '%' : '—') + '</b></div>' +
      (t.last_assessed_at ? '<div><span class="sd-label">Last assessed</span><b><i class="far fa-calendar"></i> ' + escapeHtml(window.sdFmtDate(t.last_assessed_at)) + '</b></div>' : '');
    var side;
    if (t.isWeak) {
      side = '<span class="sd-pill green">Available</span>' +
        (t.topic_id ? '<button type="button" class="sd-btn sd-btn-outline sd-btn-sm" onclick="openLmsPracticePanel(' + Number(t.topic_id) + ')"><i class="fas fa-dumbbell"></i> Guided practice</button>' : '') +
        '<button type="button" class="sd-btn sd-btn-primary sd-btn-sm" onclick="openDeficiencyChat()">Start Learning Path <i class="fas fa-chevron-right"></i></button>';
    } else {
      side = '<span class="sd-pill grey">Completed</span>' +
        '<button type="button" class="sd-btn sd-btn-outline blue sd-btn-sm" aria-expanded="' + open + '" onclick="sdTogglePathResult(' + Number(t.topic_id) + ')">' +
        '<i class="fas fa-chart-bar"></i> View Results <i class="fas fa-chevron-' + (open ? 'up' : 'down') + '"></i></button>';
    }
    var result = '';
    if (open) {
      var n = t.sample_size || null;
      result = '<div class="sd-result"><div class="sd-result-banner soft"><span class="sd-title">Learning Path Result</span>' +
        '<button type="button" class="sd-close-x" aria-label="Close result" onclick="sdTogglePathResult(' + Number(t.topic_id) + ')"><i class="fas fa-times"></i></button></div>' +
        '<div class="sd-result-body">' +
        '<div class="sd-score-big"><b class="' + window.sdPctClass(pct) + '">' + (pct != null ? pct + '%' : '—') + '</b>' +
        '<span>' + (n ? 'Based on ' + n + ' question' + (n === 1 ? '' : 's') : 'Topic score') + '</span>' +
        '<span class="sd-progress-track"><span class="sd-progress-fill' + (pct >= 70 ? ' green' : '') + '" style="width:' + Math.max(0, Math.min(100, pct || 0)) + '%;"></span></span>' +
        (t.last_assessed_at ? '<div class="sd-note"><i class="far fa-calendar"></i> Last assessed<br><b style="color:var(--sd-ink);">' + escapeHtml(window.sdFmtDateTime(t.last_assessed_at)) + '</b></div>' : '') +
        '</div>' +
        '<div><div class="sd-block-title">Topic-wise performance</div><table class="sd-mini"><thead><tr><th>Topic</th><th>Questions</th><th>%</th></tr></thead><tbody>' +
        '<tr><td>' + escapeHtml(name) + '</td><td>' + (n || '—') + '</td><td>' + (pct != null ? pct + '%' : '—') + '</td></tr></tbody></table>' +
        '<p class="sd-note">Your live mastery score for this topic across the diagnostic, Learning Chat and quizzes.</p></div>' +
        '<div><div class="sd-block-title" style="color:var(--sd-green);">Status</div>' +
        '<div class="sd-improve-grid"><div class="sd-improve-box strong"><b>' + (pct != null ? pct + '%' : '—') + '</b><small>' +
        escapeHtml(STATUS_LABEL[t.mastery_status] || 'Cleared') + '</small><div class="sd-frac">Not a weak area any more</div></div></div>' +
        (t.topic_id ? '<button type="button" class="sd-btn sd-btn-outline sd-btn-sm" style="margin-top:12px;" onclick="openLmsPracticePanel(' + Number(t.topic_id) + ')">Practice again</button>' : '') +
        '</div></div></div>';
    }
    var bar = t.isWeak ? 'purple' : 'grey';
    return '<div class="sd-list-row" data-topic-row="' + (t.isWeak ? 'weak' : 'done') + '"><div class="sd-num-bar ' + bar + '"><i class="fas fa-book-open"></i></div>' +
      '<div class="sd-row-body"><div class="sd-row-top"><div class="sd-title-chip"><div class="sd-avatar-sm purple"><i class="fas fa-book-open"></i></div>' +
      '<div><h3>' + escapeHtml(name) + ' Learning Path</h3><div class="sd-meta-line">' + meta + '</div></div></div>' +
      '<div class="sd-side-actions">' + side + '</div></div>' + result + '</div></div>';
  }

  window.sdRenderPathTopics = function () {
    var box = document.getElementById('sdPathTopics');
    if (!box) return;
    var d = window.sdDashboard;
    if (!d) { box.innerHTML = '<div class="sd-spinner"></div>'; return; }
    var diagDone = !!(d.onboarding && d.onboarding.diagnostic_completed);
    var rows = topicRows();
    if (!rows.length) {
      box.innerHTML = diagDone
        ? '<div class="sd-empty"><b>No topics assessed yet</b>Your learning path appears here once your results are scored.</div>'
        : '<div class="sd-locked"><div class="sd-lock-ic"><i class="fas fa-lock"></i></div><h3>Take your diagnostic first</h3>' +
          '<p class="sd-desc">Your personalized learning path is built from your diagnostic results.</p>' +
          '<button type="button" class="sd-btn sd-btn-primary" style="margin-top:12px;" onclick="openLmsDiagnostic()">Take Diagnostic</button></div>';
      return;
    }
    var q = ((document.getElementById('sdPathSearch') || {}).value || '').trim().toLowerCase();
    var st = (document.getElementById('sdPathStatus') || {}).value || '';
    var shown = rows.filter(function (t) {
      if (st === 'available' && !t.isWeak) return false;
      if (st === 'completed' && t.isWeak) return false;
      return !q || String(t.topic_name || '').toLowerCase().indexOf(q) !== -1;
    });
    box.innerHTML = shown.length ? shown.map(topicRowHtml).join('') : '<div class="sd-empty"><b>No topics match your search.</b></div>';
  };

  window.sdTogglePathResult = function (topicId) {
    state.open[topicId] = !state.open[topicId];
    window.sdRenderPathTopics();
  };

  /* Path steps — same actions as the old student overview (Open Learning Chat, Start, Mark done, Practice again). */
  function renderSteps() {
    var box = document.getElementById('sdPathSteps');
    var card = document.getElementById('sdPathStepsCard');
    if (!box) return;
    var d = window.sdDashboard || {};
    var path = d.learning_path;
    if (!path || !path.items || !path.items.length) {
      if (card) card.hidden = true;
      return;
    }
    if (card) card.hidden = false;
    var weakProg = path.weak_area_progress || null;
    var sub = document.getElementById('sdPathStepsSub');
    var pct = path.percent != null ? Math.round(path.percent) : (path.total_count ? Math.round(100 * (path.completed_count || 0) / path.total_count) : 0);
    if (sub) {
      sub.textContent = weakProg && weakProg.total
        ? (weakProg.cleared + ' of ' + weakProg.total + ' topics cleared — ' + pct + '% done')
        : (pct + '% done');
    }
    box.innerHTML = path.items.map(function (item, i) {
      var isDone = item.status === 'completed';
      var isCurrent = !isDone && path.current_step && path.current_step.id === item.id;
      var action = '';
      if (item.item_type === 'practice' && item.item_id === 0 && !isDone) {
        var remain = weakProg && weakProg.weak_remaining != null ? weakProg.weak_remaining : null;
        action = (remain != null ? '<span class="sd-note" style="margin:0;">' + remain + ' weak topic' + (remain === 1 ? '' : 's') + ' still need practice</span>' : '') +
          '<button type="button" class="sd-btn sd-btn-primary sd-btn-sm" onclick="openDeficiencyChat()">Open Learning Chat</button>';
      } else if (isCurrent) {
        if (item.item_type === 'enrichment') {
          action = '<button type="button" class="sd-btn sd-btn-primary sd-btn-sm" onclick="openDeficiencyChat(false, \'enrichment\')">Start challenge</button>';
        } else {
          action = '<button type="button" class="sd-btn sd-btn-primary sd-btn-sm" onclick="lmsLaunchPathStep(\'' + escapeHtml(item.item_type) + '\',' + (item.item_id || 'null') + ',' + item.id + ')">Start</button>' +
            '<button type="button" class="sd-btn sd-btn-outline sd-btn-sm" onclick="markLmsPathItemComplete(' + item.id + ')">Mark done</button>';
        }
      } else if (isDone && item.item_type === 'practice' && item.item_id === 0) {
        action = '<span class="sd-note" style="margin:0;">All weak topics cleared</span>' +
          '<button type="button" class="sd-btn sd-btn-outline sd-btn-sm" onclick="openDeficiencyChat(true)">Practice again</button>';
      }
      var pill = isDone ? '<span class="sd-pill grey">Completed</span>' : (isCurrent ? '<span class="sd-pill green">Current</span>' : '<span class="sd-pill">Up next</span>');
      return '<div class="sd-list-row"><div class="sd-num-bar' + (isDone ? ' grey' : '') + '">' + String(i + 1).padStart(2, '0') + '</div>' +
        '<div class="sd-row-body"><div class="sd-row-top"><div class="sd-title-chip"><div>' +
        '<h3>' + escapeHtml(item.title || item.label || 'Step') + '</h3>' +
        '<div class="sd-meta-line"><div><span class="sd-label">Type</span><b>' + escapeHtml(String(item.item_type || '').replace(/_/g, ' ')) + '</b></div>' +
        (item.completed_at ? '<div><span class="sd-label">Completed on</span><b>' + escapeHtml(window.sdFmtDate(item.completed_at)) + '</b></div>' : '') +
        '</div></div></div><div class="sd-side-actions">' + pill + action + '</div></div></div></div>';
    }).join('');
  }

  /* Old overview: mark a path step done (PUT /api/lms/students/me/learning-path). Also used by lmsLaunchPathStep. */
  window.markLmsPathItemComplete = async function (itemId) {
    try {
      await lmsApi('/api/lms/students/me/learning-path', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ item_id: itemId })
      });
      window.loadLmsStudentDashboard();
    } catch (err) {
      window.showToast(err.message || 'Failed', 'error', 3000);
    }
  };

  /* Quiz History — same data and click-through as loadLmsAttemptHistory (lms-panels.js). */
  function renderHistory() {
    var box = document.getElementById('sdAttemptHistory');
    if (!box) return;
    var items = state.attempts;
    if (items === null) { box.innerHTML = '<div class="sd-spinner"></div>'; return; }
    if (!items.length) { box.innerHTML = '<div class="sd-empty">No attempts yet.</div>'; return; }
    box.innerHTML = items.map(function (a, i) {
      var isDiag = a.assessment_type === 'diagnostic';
      var label = a.title || (isDiag ? 'Diagnostic Assessment' : 'Quiz #' + a.assessment_id);
      var statusLabel, action = '';
      if (a.status === 'submitted' && a.score != null && a.max_score != null) {
        var pct = a.score_percent != null ? a.score_percent : Math.round(1000 * a.score / a.max_score) / 10;
        statusLabel = Math.round(a.score) + '/' + Math.round(a.max_score) + ' — ' + pct + '%';
      } else if (a.score_percent != null) statusLabel = a.score_percent + '%';
      else if (a.status === 'in_progress') statusLabel = 'In progress';
      else statusLabel = a.status;
      if (a.status === 'submitted') action = '<button type="button" class="sd-btn sd-btn-outline blue sd-btn-sm" onclick="viewLmsAttemptResult(' + a.attempt_id + ')"><i class="fas fa-chart-bar"></i> View Result</button>';
      else if (a.status === 'in_progress' && isDiag) action = '<button type="button" class="sd-btn sd-btn-primary sd-btn-sm" onclick="resumeLmsDiagnostic(' + a.assessment_id + ')">Continue</button>';
      return '<div class="sd-list-row"><div class="sd-num-bar' + (a.status === 'submitted' ? ' grey' : '') + '">' + String(i + 1).padStart(2, '0') + '</div>' +
        '<div class="sd-row-body"><div class="sd-row-top"><div class="sd-title-chip"><div class="sd-avatar-sm' + (isDiag ? '' : ' blue') + '"><i class="far fa-file-alt"></i></div>' +
        '<div><h3>' + escapeHtml(label) + '</h3><div class="sd-meta-line">' +
        '<div><span class="sd-label">Type</span><b>' + (isDiag ? 'Diagnostic' : 'Quiz') + '</b></div>' +
        '<div><span class="sd-label">Result</span><b>' + escapeHtml(String(statusLabel)) + '</b></div>' +
        (a.submitted_at ? '<div><span class="sd-label">Submitted</span><b>' + escapeHtml(window.sdFmtDateTime(a.submitted_at)) + '</b></div>' : '') +
        '</div></div></div><div class="sd-side-actions">' + action + '</div></div></div></div>';
    }).join('');
  }

  function loadHistory() {
    lmsApi('/api/lms/students/me/attempts').then(function (items) {
      state.attempts = Array.isArray(items) ? items : [];
      renderHistory();
    }).catch(function () { state.attempts = []; renderHistory(); });
  }

  window.sdOnDashboard(function () {
    window.sdRenderPathTopics();
    renderSteps();
  });

  window.sdViews = window.sdViews || {};
  window.sdViews['learning-path'] = {
    onShow: function () {
      window.sdRenderPathTopics();
      renderSteps();
      renderHistory();
      window.loadLmsStudentDashboard();
      loadHistory();
    }
  };
})();
