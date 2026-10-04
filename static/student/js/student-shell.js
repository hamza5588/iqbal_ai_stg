/* Student dashboard shell — views (hash routing), diagnostic gate, top stat cards, toasts, avatar menu,
   join class, logout, and the shared result renderer. Loaded first: lms-core.js reuses escapeHtml and
   lmsShowToast() forwards to showToast(). */
(function () {
  var CFG = window.STUDENT_CFG || {};

  window.escapeHtml = function (text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  };

  /* ── Dates (API timestamps are naive UTC ISO strings) ── */
  function parseTs(iso) {
    if (!iso) return null;
    var s = String(iso);
    if (!/[zZ]|[+-]\d\d:?\d\d$/.test(s)) s += 'Z';
    var d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  }
  window.sdFmtDate = function (iso) {
    var d = parseTs(iso);
    return d ? d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : '—';
  };
  window.sdFmtDateTime = function (iso) {
    var d = parseTs(iso);
    return d ? d.toLocaleString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—';
  };
  window.sdParseTs = parseTs;
  /* Calendar dates (e.g. a due date) are stored without a time zone: show that day as-is. */
  window.sdFmtDay = function (iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
    if (!m) return '—';
    return new Date(+m[1], +m[2] - 1, +m[3]).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  };

  /* ── Toasts ── */
  window.showToast = function (message, type, duration) {
    type = type || 'success';
    var container = document.getElementById('toastContainer');
    if (!container) return;
    var icons = { success: 'fa-check-circle', error: 'fa-exclamation-circle', warning: 'fa-exclamation-triangle', info: 'fa-info-circle' };
    var toast = document.createElement('div');
    toast.className = 'sd-toast ' + (icons[type] ? type : 'success');
    toast.setAttribute('role', type === 'error' ? 'alert' : 'status');
    toast.innerHTML = '<i class="fas ' + (icons[type] || icons.success) + '"></i><span></span>' +
      '<button type="button" aria-label="Dismiss"><i class="fas fa-times"></i></button>';
    toast.querySelector('span').textContent = String(message == null ? '' : message);
    toast.querySelector('button').onclick = function () { toast.remove(); };
    container.appendChild(toast);
    setTimeout(function () { toast.remove(); }, duration || 4000);
  };
  // Native alerts become toasts (as on the old student page).
  (function () {
    var nativeAlert = window.alert;
    window.alert = function (msg) {
      try { window.showToast(String(msg), 'info', 4000); } catch (e) { nativeAlert(msg); }
    };
  })();

  /* ── Avatar menu / logout ── */
  window.sdToggleAvatarMenu = function (e) {
    if (e) e.stopPropagation();
    var menu = document.getElementById('sdAvatarMenu');
    var btn = document.getElementById('userAvatar');
    if (!menu) return;
    menu.hidden = !menu.hidden;
    if (btn) btn.setAttribute('aria-expanded', String(!menu.hidden));
  };
  window.sdCloseAvatarMenu = function () {
    var menu = document.getElementById('sdAvatarMenu');
    if (menu) menu.hidden = true;
    var btn = document.getElementById('userAvatar');
    if (btn) btn.setAttribute('aria-expanded', 'false');
  };
  document.addEventListener('click', function (e) {
    var menu = document.getElementById('sdAvatarMenu');
    if (menu && !menu.hidden && !menu.contains(e.target)) window.sdCloseAvatarMenu();
  });

  window.logout = async function () {
    var confirmed = typeof showInAppConfirm === 'function'
      ? await showInAppConfirm('Are you sure you want to logout?', { title: 'Logout', confirmLabel: 'Logout', cancelLabel: 'Cancel', iconClass: 'fas fa-sign-out-alt' })
      : window.confirm('Are you sure you want to logout?');
    if (!confirmed) return;
    try {
      var response = await fetch('/auth/logout', { method: 'POST', headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'include' });
      if (response.ok) {
        try {
          var data = await response.json();
          if (data && data.redirect_url) { window.location.href = data.redirect_url; return; }
        } catch (e) { /* fall through */ }
      }
    } catch (e) { /* fall through */ }
    window.location.href = '/auth/login';
  };

  /* ── Views ─────────────────────────────────────────────────────────────
     Top-level: diagnostic, learning-path, classes, tutor.
     Flows opened from them (need live state, never a landing target):
     diagnostic-quiz (lms-student.js), learning-chat (lms-deficiency-chat.js), lesson (student-lessons.js). */
  var TOP_VIEWS = ['diagnostic', 'learning-path', 'classes', 'tutor'];
  var FLOW_PARENT = { 'diagnostic-quiz': 'diagnostic', 'learning-chat': 'learning-path', 'lesson': 'classes' };
  var GATED = { 'learning-path': 1, 'classes': 1, 'tutor': 1, 'learning-chat': 1, 'lesson': 1 };
  var flows = { 'diagnostic-quiz': false, 'learning-chat': false, 'lesson': false };
  var pendingSurfaceClose = {};
  var current = null;
  window.sdViews = window.sdViews || {};

  function needsDiagnostic() { return !!window._lmsNeedsDiagnostic; }

  function activate(view) {
    document.querySelectorAll('.sd-view').forEach(function (el) {
      el.classList.toggle('active', el.getAttribute('data-view') === view);
    });
    var sec = document.getElementById('view-' + view);
    var navKey = (sec && sec.getAttribute('data-nav')) || view;
    document.querySelectorAll('.sd-navbtn').forEach(function (a) {
      var on = a.getAttribute('data-view') === navKey;
      a.classList.toggle('active', on);
      if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
    var prev = current;
    current = view;
    if (('#' + view) !== window.location.hash) {
      try { history.replaceState(null, '', '#' + view); } catch (e) { window.location.hash = view; }
    }
    window.scrollTo({ top: 0, behavior: 'auto' });
    var mod = window.sdViews[view];
    if (mod && typeof mod.onShow === 'function' && prev !== view) {
      try { mod.onShow(); } catch (e) { console.warn('view onShow failed', view, e); }
    }
  }

  /** Leave the current flow view. Returns false if the flow may not be left (mandatory diagnostic). */
  function leaveCurrent(target) {
    if (current === 'diagnostic-quiz' && target !== 'diagnostic-quiz' && flows['diagnostic-quiz']) {
      if (window._lmsDiagnosticMandatory && !window._lmsDiagnosticAllowClose) {
        window.showToast('Please complete and submit the diagnostic assessment first.', 'error');
        return false;
      }
      pendingSurfaceClose['diagnostic-quiz'] = true;
      if (typeof closeLmsDiagnostic === 'function') closeLmsDiagnostic();
    }
    if (current === 'learning-chat' && target !== 'learning-chat' && flows['learning-chat']) {
      pendingSurfaceClose['learning-chat'] = true;
      if (typeof closeDeficiencyChat === 'function') closeDeficiencyChat();
    }
    if (current === 'lesson' && target !== 'lesson') {
      flows.lesson = false;
      var mod = window.sdViews.lesson;
      if (mod && typeof mod.onLeave === 'function') mod.onLeave();
    }
    return true;
  }

  window.sdShowView = function (view, opts) {
    opts = opts || {};
    if (!document.getElementById('view-' + view)) view = 'diagnostic';
    if (FLOW_PARENT[view] && !flows[view] && !opts.flow) view = FLOW_PARENT[view];
    if (GATED[view] && needsDiagnostic()) {
      window.showToast('Complete your diagnostic assessment first.', 'error');
      if (current !== 'diagnostic-quiz' && typeof openLmsDiagnostic === 'function') openLmsDiagnostic();
      return false;
    }
    if (view === current) { window.scrollTo({ top: 0, behavior: 'auto' }); return true; }
    if (!leaveCurrent(view)) {
      if (current) { try { history.replaceState(null, '', '#' + current); } catch (e) { /* ignore */ } }
      return false;
    }
    activate(view);
    return true;
  };
  window.sdCurrentView = function () { return current; };

  window.addEventListener('hashchange', function () {
    var v = (window.location.hash || '').replace(/^#/, '');
    if (v && v !== current) window.sdShowView(v);
  });

  function openFlow(view) {
    flows[view] = true;
    // A close still in flight (e.g. awaiting the diagnostic answer re-save) is
    // abandoned by the flow itself when it reopens; don't let its flag swallow
    // the next real close.
    pendingSurfaceClose[view] = false;
    if (current === view) { window.scrollTo({ top: 0, behavior: 'auto' }); return; }
    if (!leaveCurrent(view)) return;
    activate(view);
  }
  function closeFlow(view) {
    flows[view] = false;
    if (pendingSurfaceClose[view]) { pendingSurfaceClose[view] = false; return; }
    if (current === view) activate(FLOW_PARENT[view]);
  }
  // Surface hooks used by lms-student.js / lms-deficiency-chat.js / student-lessons.js.
  window.sdOpenDiagnosticView = function () { openFlow('diagnostic-quiz'); };
  window.sdCloseDiagnosticView = function () { closeFlow('diagnostic-quiz'); if (window.sdRefreshDiagnostic) window.sdRefreshDiagnostic(); };
  window.sdOpenLearningChatView = function () { openFlow('learning-chat'); };
  window.sdCloseLearningChatView = function () { closeFlow('learning-chat'); };
  window.sdOpenLessonView = function () { openFlow('lesson'); };
  window.sdCloseLessonView = function () { closeFlow('lesson'); };

  window.sdOnDiagnosticGate = function (locked) {
    document.querySelectorAll('.sd-navbtn').forEach(function (a) {
      a.classList.toggle('locked', !!locked && !!GATED[a.getAttribute('data-view')]);
    });
  };

  /* ── Dashboard data → top stat cards ── */
  window.sdDashboard = null;
  var dashboardListeners = [];
  window.sdOnDashboard = function (fn) { dashboardListeners.push(fn); if (window.sdDashboard) fn(window.sdDashboard); };

  window.sdToggleWeakChips = function (btn) {
    var box = btn && btn.parentElement && btn.parentElement.querySelector('[data-stat="weak-chips"]');
    if (!box) return;
    var collapsed = box.classList.toggle('sd-chips-collapsed');
    btn.innerHTML = collapsed ? 'View All <i class="fas fa-chevron-right"></i>' : 'Show less <i class="fas fa-chevron-up"></i>';
  };

  function setAll(sel, fn) { document.querySelectorAll('[data-stat="' + sel + '"]').forEach(fn); }

  function renderStatCards(d) {
    var diagDone = !!(d.onboarding && d.onboarding.diagnostic_completed);
    // Usually 0 or 1; more when the admin replaced a diagnostic the student had not taken yet.
    var pendingDiag = d.onboarding && d.onboarding.pending_diagnostic_count;
    var pendingText = diagDone ? '0' : '1';
    if (pendingDiag != null) pendingText = String(diagDone ? pendingDiag : Math.max(1, pendingDiag));
    setAll('pending-diag', function (el) { el.textContent = pendingText; });
    var weak = d.weak_topics || [];
    setAll('weak-count', function (el) { el.textContent = String(weak.length); });
    setAll('weak-chips', function (el) {
      el.innerHTML = weak.length
        ? weak.map(function (t) {
            var name = t.topic_name || ('Topic #' + t.topic_id);
            var pct = t.score_percent != null ? Math.round(t.score_percent) + '%' : '';
            return '<span class="sd-tag-chip" title="' + escapeHtml(name + (pct ? ' — ' + pct : '')) + '">' + escapeHtml(name) + '</span>';
          }).join('')
        : '<small>' + (diagDone ? 'No weak topics right now.' : 'Take the diagnostic to find them.') + '</small>';
    });
    setAll('weak-toggle', function (el) { el.hidden = weak.length <= 10; });
    var prog = d.learning_path_progress || {};
    var total = prog.total || 0;
    var pct = prog.percent != null ? Math.round(prog.percent) : 0;
    setAll('path-label', function (el) { el.textContent = total ? (prog.completed + ' of ' + total + ' topics') : 'No path yet'; });
    setAll('path-fill', function (el) { el.style.width = Math.max(0, Math.min(100, pct)) + '%'; });
    setAll('path-pct', function (el) { el.textContent = pct + '%'; });
  }

  var _dashInFlight = null;
  /* Kept under the old name: lms-student.js, lms-deficiency-chat.js and the quiz flow call it to refresh. */
  window.loadLmsStudentDashboard = function () {
    if (_dashInFlight) return _dashInFlight;
    _dashInFlight = lmsApi('/api/lms/students/me/dashboard').then(function (d) {
      window.sdDashboard = d;
      if (d && d.onboarding) window._lmsNeedsDiagnostic = !d.onboarding.diagnostic_completed;
      renderStatCards(d);
      dashboardListeners.forEach(function (fn) { try { fn(d); } catch (e) { console.warn(e); } });
      return d;
    }).catch(function (err) {
      console.warn('Dashboard load failed', err);
    }).finally(function () { _dashInFlight = null; });
    return _dashInFlight;
  };

  /* ── Shared result renderer (diagnostic results, quiz review, diagnostic hub) ── */
  function pctClass(p) { return p == null ? '' : (p >= 70 ? '' : (p >= 40 ? 'mid' : 'low')); }
  window.sdPctClass = pctClass;
  function tile(t, cls) {
    var p = Math.round(t.score_percent || 0);
    var frac = '';
    if (t.correct != null && t.total != null) frac = Math.round(t.correct) + ' of ' + Math.round(t.total) + ' correct';
    else {
      var n = (t.question_ids && t.question_ids.length) || 0;
      if (n) frac = Math.round((p * n) / 100) + ' of ' + n + ' correct';
    }
    return '<div class="sd-improve-box' + (cls ? ' ' + cls : '') + '"><b>' + p + '%</b><small>' +
      escapeHtml(t.topic_name || t.name || ('Topic #' + t.topic_id)) + '</small>' +
      (frac ? '<div class="sd-frac">' + frac + '</div>' : '') + '</div>';
  }
  window.sdRenderResultBody = function (r) {
    r = r || {};
    var pct = r.score_percent != null ? Math.round(r.score_percent) : null;
    var hasCounts = r.score != null && r.max_score != null;
    var line = hasCounts
      ? Math.round(r.score) + '/' + Math.round(r.max_score) + ' — ' + (r.score_percent != null ? r.score_percent : pct) + '%'
      : (pct != null ? pct + '%' : '');
    if (r.submitted_at) line += ' · ' + window.sdFmtDateTime(r.submitted_at);
    var unanswered = r.unanswered_count > 0 ? '<div class="sd-note">' + Math.round(r.unanswered_count) + ' unanswered · scored as 0</div>' : '';
    var timeOver = (r.time_over || r.timed_out)
      ? '<div class="sd-note" style="color:var(--sd-amber);">' + escapeHtml(r.message || 'Time ran out — the questions you answered were scored.') + '</div>' : '';
    var col1 = '<div class="sd-score-big"><b class="' + pctClass(pct) + '">' + (pct != null ? pct + '%' : '—') + '</b>' +
      '<span>' + escapeHtml(line) + '</span>' + unanswered + timeOver +
      (pct != null ? '<span class="sd-progress-track"><span class="sd-progress-fill' + (pct >= 70 ? ' green' : '') + '" style="width:' + Math.max(0, Math.min(100, pct)) + '%;"></span></span>' : '') +
      '</div>';

    var rows = r.topic_breakdown || r.all_topics || [];
    var table = rows.length
      ? '<table class="sd-mini"><thead><tr><th>Topic</th><th>Score</th><th>%</th></tr></thead><tbody>' +
        rows.map(function (t) {
          var sp = t.score_percent != null ? Math.round(t.score_percent) + '%' : '—';
          var sc = (t.correct != null && t.total != null) ? Math.round(t.correct) + '/' + Math.round(t.total) : '—';
          return '<tr><td>' + escapeHtml(t.topic_name || ('Topic #' + t.topic_id)) + '</td><td>' + sc + '</td><td>' + sp + '</td></tr>';
        }).join('') + '</tbody></table>'
      : '<p class="sd-note">No topic breakdown for this attempt.</p>';
    var weak = r.weak_topics || [];
    var strong = r.strong_topics || [];
    var col2 = '<div><div class="sd-block-title">Topic-wise performance</div>' + table +
      ((weak.length || strong.length) ? '<p class="sd-note">Grouped by topic below — each tile is that topic\'s own score, already included in the number above.</p>' : '') +
      '</div>';
    var col3 = '<div><div class="sd-block-title red">Areas to improve</div>' +
      (weak.length ? '<div class="sd-improve-grid">' + weak.map(function (t) { return tile(t); }).join('') + '</div>'
        : '<p class="sd-note">No weak areas in this attempt. 🎉</p>') +
      (strong.length ? '<div class="sd-block-title" style="color:var(--sd-green);margin-top:14px;">Strong areas</div><div class="sd-improve-grid">' +
        strong.map(function (t) { return tile(t, 'strong'); }).join('') + '</div>' : '') +
      '</div>';
    return '<div class="sd-result-body">' + col1 + col2 + col3 + '</div>';
  };

  /* ── Join class (was in the old page; same API + diagnostic gate) ── */
  window.openLmsJoinClassModal = function () {
    if (window.lmsStudentNeedsDiagnostic && window.lmsStudentNeedsDiagnostic()) {
      if (typeof lmsShowToast === 'function') lmsShowToast('Complete your diagnostic assessment first.', 'error');
      if (typeof openLmsDiagnostic === 'function') openLmsDiagnostic();
      return;
    }
    lmsOpenModal('lmsJoinClassModal');
    document.getElementById('lmsJoinClassError').textContent = '';
    var input = document.getElementById('lmsJoinCodeInput');
    if (input) { input.value = ''; setTimeout(function () { input.focus(); }, 50); }
    lmsApi('/api/lms/users/me/grade-profile').then(function (p) {
      var hint = document.getElementById('lmsJoinGradeHint');
      if (p.grade_label) hint.textContent = 'Your grade: ' + p.grade_label + '. You can only join classes for your grade level.';
    }).catch(function () {});
  };
  window.closeLmsJoinClassModal = function () { lmsCloseModal('lmsJoinClassModal'); };
  window.submitLmsJoinClass = async function () {
    var code = (document.getElementById('lmsJoinCodeInput').value || '').trim().toUpperCase();
    var errEl = document.getElementById('lmsJoinClassError');
    if (!code) { errEl.textContent = 'Please enter a join code.'; return; }
    if (typeof showWaitOverlay === 'function') showWaitOverlay('Joining class...');
    try {
      var res = await fetch('/api/lms/classes/join', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ join_code: code })
      });
      var body = await res.json();
      if (!res.ok) throw new Error((body.error && body.error.message) || body.error || 'Invalid join code');
      window.closeLmsJoinClassModal();
      window.showToast('Joined class successfully!', 'success', 3000);
      window.loadLmsStudentDashboard();
      if (typeof window.sdReloadClasses === 'function') window.sdReloadClasses();
      window.sdShowView('classes');
    } catch (err) {
      errEl.textContent = err.message;
    } finally {
      if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
    }
  };

  /* ── Onboarding gate (same rule as the old page: diagnostic first) ── */
  async function initLmsOnboardingGate() {
    var ob = CFG.onboarding || {};
    try {
      ob = await lmsApi('/api/lms/students/me/onboarding-status');
    } catch (e) { /* fall back to the server-rendered status */ }
    window._lmsNeedsDiagnostic = ob && ob.diagnostic_completed === false;
    if (window._lmsNeedsDiagnostic) {
      if (typeof setLmsDiagnosticGate === 'function') setLmsDiagnosticGate(true);
      if (typeof openLmsDiagnostic === 'function') openLmsDiagnostic();
      return true;
    }
    return false;
  }

  function hideLoadingOverlay() {
    var el = document.getElementById('loading-overlay');
    if (!el) return;
    el.style.opacity = '0';
    setTimeout(function () { el.remove(); }, 300);
  }

  document.addEventListener('DOMContentLoaded', async function () {
    // Server-rendered status first so gated views never flash before the fetch returns.
    var ob = CFG.onboarding || {};
    window._lmsNeedsDiagnostic = ob.diagnostic_completed === false;
    window.sdOnDiagnosticGate(window._lmsNeedsDiagnostic);
    lmsApi('/api/lms/users/me/grade-profile').then(function (p) {
      window.sdGradeLabel = p && (p.grade_label || p.grade) || '';
    }).catch(function () {});

    var gated = await initLmsOnboardingGate();
    if (!gated) {
      var start = (window.location.hash || '').replace(/^#/, '');
      if (TOP_VIEWS.indexOf(start) === -1) start = FLOW_PARENT[start] || 'diagnostic';
      window.sdShowView(start);
    }
    window.loadLmsStudentDashboard();
    hideLoadingOverlay();
  });
})();
