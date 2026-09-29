/* Teacher → Class Analytics (screens 07–11). Same endpoints as the legacy analytics modal (lms-panels.js):
   GET /api/lms/classes/mine
   GET /api/lms/classes/:id/students                         (roster: overall_progress, is_struggling, weak_topics…)
   GET /api/lms/classes/:id/students/:sid/progress/by-topic  (per-topic diagnostic + quiz series)
   GET /api/lms/classes/:id/analytics/quizzes                (assignments + student_results)
   GET /api/lms/classes/:id/analytics/struggling
   Topic Performance (/analytics/topics) stays hidden per product decision d10856c. */
(function () {
  var U = window.tdUtil;
  var state = {
    classes: null, classId: null, tab: 'progress',
    roster: null, quizzes: null, struggling: null,
    topicCache: {}, expanded: {}, chartTopic: {}, charts: {}, seq: 0
  };

  function $(id) { return document.getElementById(id); }

  function destroyCharts() {
    Object.keys(state.charts).forEach(function (k) { try { state.charts[k].destroy(); } catch (e) { /* ignore */ } });
    state.charts = {};
  }

  async function open() {
    var sel = $('tdAnaClass');
    if (!sel) return;
    try {
      state.classes = (await lmsApi('/api/lms/classes/mine')) || [];
    } catch (err) {
      sel.innerHTML = '<option value="">Error loading classes</option>';
      showAllEmpty(err.message || 'Could not load classes.', true);
      return;
    }
    if (!state.classes.length) {
      sel.innerHTML = '<option value="">No classes</option>';
      showAllEmpty('Create a class first to view analytics.');
      return;
    }
    var keep = state.classId != null && state.classes.some(function (c) { return String(c.id) === String(state.classId); });
    sel.innerHTML = state.classes.map(function (c) {
      return '<option value="' + c.id + '">' + U.esc(c.name) + (c.grade_level ? ' (' + U.esc(c.grade_level) + 'th)' : '') + '</option>';
    }).join('');
    sel.value = keep ? String(state.classId) : String(state.classes[0].id);
    load();
  }

  function showAllEmpty(msg, isError) {
    ['Progress', 'Quiz', 'Struggling', 'Roster'].forEach(function (k) {
      var rows = $('tdAna' + k + 'Rows');
      if (rows) rows.innerHTML = '';
      var e = $('tdAna' + k + 'Empty');
      if (e) { e.hidden = false; e.innerHTML = isError ? '<p class="td-error">' + U.esc(msg) + '</p>' : '<p>' + U.esc(msg) + '</p>'; }
    });
  }

  // Load (or reload) the data for the selected class; each tab fetches what it needs lazily.
  function load() {
    var sel = $('tdAnaClass');
    state.classId = sel ? sel.value : null;
    state.roster = state.quizzes = state.struggling = null;
    state.topicCache = {};
    state.expanded = {};
    destroyCharts();
    loadTab();
  }

  function switchTab(tab) {
    state.tab = tab;
    document.querySelectorAll('.td-ana-tab').forEach(function (b) { b.classList.toggle('active', b.getAttribute('data-ana-tab') === tab); });
    document.querySelectorAll('.td-ana-panel').forEach(function (p) { p.hidden = p.getAttribute('data-ana-panel') !== tab; });
    var search = $('tdAnaSearch');
    if (search) search.placeholder = tab === 'quizzes' ? 'Search quizzes…' : 'Search students…';
    destroyCharts();
    loadTab();
  }

  async function loadTab() {
    if (!state.classId) return;
    var seq = ++state.seq;
    var classId = state.classId;
    var tab = state.tab;
    var emptyEl = $({ progress: 'tdAnaProgressEmpty', quizzes: 'tdAnaQuizEmpty', struggling: 'tdAnaStrugglingEmpty', roster: 'tdAnaRosterEmpty' }[tab]);
    var rowsEl = $({ progress: 'tdAnaProgressRows', quizzes: 'tdAnaQuizRows', struggling: 'tdAnaStrugglingRows', roster: 'tdAnaRosterRows' }[tab]);
    var needs = {
      progress: ['roster', 'quizzes'], quizzes: ['quizzes'], struggling: ['struggling'], roster: ['roster', 'quizzes']
    }[tab];
    if (needs.some(function (k) { return state[k] == null; })) {
      if (rowsEl) rowsEl.innerHTML = '';
      if (emptyEl) { emptyEl.hidden = false; emptyEl.innerHTML = '<div class="td-spinner"></div>'; }
    }
    try {
      var base = '/api/lms/classes/' + classId;
      await Promise.all(needs.map(async function (k) {
        if (state[k] != null) return;
        var url = { roster: base + '/students', quizzes: base + '/analytics/quizzes', struggling: base + '/analytics/struggling' }[k];
        var data = await lmsApi(url);
        if (seq === state.seq && classId === state.classId) state[k] = data || [];
      }));
      if (seq !== state.seq) return;
      rerender();
    } catch (err) {
      if (seq !== state.seq) return;
      if (rowsEl) rowsEl.innerHTML = '';
      if (emptyEl) { emptyEl.hidden = false; emptyEl.innerHTML = '<p class="td-error">' + U.esc(err.message) + '</p>'; }
    }
  }

  function rerender() {
    destroyCharts();
    if (state.tab === 'progress') renderProgress();
    else if (state.tab === 'quizzes') renderQuizzes();
    else if (state.tab === 'struggling') renderStruggling();
    else renderRoster();
  }

  /* ── shared ── */
  function searchTerm() { return (($('tdAnaSearch') || {}).value || '').trim().toLowerCase(); }
  function sortStudents(list) {
    var sort = ($('tdAnaSort') || {}).value || 'latest';
    var out = list.slice();
    var prog = function (s) { var p = U.pct(s.overall_progress); return p == null ? -1 : p; };
    if (sort === 'name') out.sort(function (a, b) { return U.studentName(a).localeCompare(U.studentName(b)); });
    else if (sort === 'progress_asc') out.sort(function (a, b) { return prog(a) - prog(b); });
    else if (sort === 'progress_desc') out.sort(function (a, b) { return prog(b) - prog(a); });
    else out.sort(function (a, b) { return String(b.enrolled_at || '').localeCompare(String(a.enrolled_at || '')); });
    return out;
  }
  function filterStudents(list) {
    var q = searchTerm();
    return list.filter(function (s) {
      return !q || [s.username, s.email].some(function (v) { return v && String(v).toLowerCase().indexOf(q) >= 0; });
    });
  }
  // Not attempted = no mastery rows (progress 0, no weak topics) AND no submitted quiz in this class.
  // Anyone else keeps the backend's is_struggling verdict.
  function notAttempted(s) {
    return !Number(s.overall_progress) && !(s.weak_topic_count || (s.weak_topics || []).length) &&
      latestQuizScore(s.student_id) == null;
  }
  function studentStatus(s) {
    if (notAttempted(s)) return 'not_attempted';
    return s.is_struggling ? 'needs_help' : 'on_track';
  }
  function setEmpty(id, html) {
    var e = $(id);
    if (!e) return;
    e.hidden = !html;
    e.innerHTML = html || '';
  }
  function latestQuizScore(studentId) {
    var best = null;
    (state.quizzes || []).forEach(function (a) {
      (a.student_results || []).forEach(function (r) {
        if (r.student_id === studentId && r.status === 'submitted' && r.score_percent != null) {
          if (!best || a.assignment_id > best.id) best = { id: a.assignment_id, pct: r.score_percent };
        }
      });
    });
    return best ? best.pct : null;
  }
  async function topicProgress(studentId) {
    var key = state.classId + ':' + studentId;
    if (!state.topicCache[key]) {
      state.topicCache[key] = lmsApi('/api/lms/classes/' + state.classId + '/students/' + studentId + '/progress/by-topic')
        .then(function (p) { return (p && p.topics) || []; });
    }
    return state.topicCache[key];
  }
  function latestPoint(topic) {
    var s = (topic.series || []).filter(function (p) { return p.score_percent != null; });
    return s.length ? s[s.length - 1] : null;
  }
  function fmtDate(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    return isNaN(d.getTime()) ? '—' : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  }

  /* ── 07 Topic Progress ── */
  function renderProgress() {
    var body = $('tdAnaProgressRows');
    var roster = state.roster || [];
    if (!roster.length) { body.innerHTML = ''; setEmpty('tdAnaProgressEmpty', '<p>No students enrolled.</p>'); return; }
    var rows = sortStudents(filterStudents(roster));
    setEmpty('tdAnaProgressEmpty', rows.length ? '' : '<p>No students match your search.</p>');
    body.innerHTML = rows.map(function (s) {
      var sid = s.student_id;
      var open = !!state.expanded['p' + sid];
      var latest = latestQuizScore(sid);
      var st = studentStatus(s);
      var badge;
      if (st === 'not_attempted') badge = '<span class="td-status-badge td-status-muted">Not Attempted</span>';
      else if (st === 'needs_help') badge = '<span class="td-status-badge td-status-help">Needs Help</span>';
      else if ((U.pct(s.overall_progress) || 0) >= 70) badge = '<span class="td-status-badge td-status-good">Good</span>';
      else badge = '<span class="td-status-badge td-status-avg">Average</span>';
      return '<tr class="td-clickable' + (open ? ' td-expanded' : '') + '" data-sid="' + sid + '" onclick="tdAnalytics.toggleProgress(' + sid + ')">' +
        '<td>' + U.studentCell(U.studentName(s)) + '</td>' +
        '<td>' + U.esc(s.grade_label || '—') + '</td>' +
        '<td>' + U.progress(s.overall_progress, '') + '</td>' +
        '<td><b>' + (latest != null ? U.pct(latest) + '%' : '—') + '</b></td>' +
        '<td>' + badge + '</td>' +
        '<td style="text-align:right;"><span class="td-chev"><i class="fas fa-chevron-down"></i></span></td></tr>' +
        (open ? '<tr><td colspan="6" class="td-detail-cell"><div class="td-detail" id="tdProgDetail-' + sid + '"><div class="td-empty" style="grid-column:1/-1;padding:20px;"><div class="td-spinner"></div></div></div></td></tr>' : '');
    }).join('');
    Object.keys(state.expanded).forEach(function (k) {
      if (k.charAt(0) === 'p' && state.expanded[k]) fillProgressDetail(Number(k.slice(1)));
    });
  }

  function toggleProgress(sid) {
    state.expanded['p' + sid] = !state.expanded['p' + sid];
    rerender();
  }

  async function fillProgressDetail(sid) {
    var el = $('tdProgDetail-' + sid);
    if (!el) return;
    var topics;
    try { topics = await topicProgress(sid); } catch (err) {
      el.innerHTML = '<p class="td-error" style="grid-column:1/-1;">' + U.esc(err.message || 'Could not load topic progress.') + '</p>';
      return;
    }
    el = $('tdProgDetail-' + sid);
    if (!el) return;
    if (!topics.length) {
      el.innerHTML = '<div class="td-empty" style="grid-column:1/-1;padding:18px;"><p>No diagnostic or quiz data for this student yet.</p></div>';
      return;
    }
    var bars = topics.map(function (t) {
      var lp = latestPoint(t);
      var p = lp ? U.pct(lp.score_percent) : null;
      return '<div class="td-topic-row"><span>' + U.esc(t.topic_name || ('Topic #' + t.topic_id)) + '</span>' +
        '<span class="td-progress-track"><span class="td-progress-fill' + (p != null && p < 40 ? ' red' : '') + '" style="width:' + (p || 0) + '%;"></span></span>' +
        '<b>' + (p != null ? p + '%' : '—') + '</b></div>';
    }).join('');
    var idx = state.chartTopic[sid] || 0;
    if (idx >= topics.length) idx = 0;
    var opts = topics.map(function (t, i) {
      return '<option value="' + i + '"' + (i === idx ? ' selected' : '') + '>' + U.esc(t.topic_name || ('Topic #' + t.topic_id)) + '</option>';
    }).join('');
    el.innerHTML =
      '<div class="td-detail-card"><h4>Topic Progress</h4><p class="td-desc">Latest diagnostic / quiz score per topic.</p>' + bars + '</div>' +
      '<div class="td-detail-card"><div style="display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:6px;">' +
        '<h4 style="margin:0;">Score Over Time</h4>' +
        '<select class="td-select" style="min-width:0;padding:6px 30px 6px 10px;font-size:13px;" aria-label="Topic" onclick="event.stopPropagation()" onchange="tdAnalytics.setChartTopic(' + sid + ', this.value)">' + opts + '</select></div>' +
        '<p class="td-desc" id="tdProgStats-' + sid + '" style="margin-bottom:6px;"></p>' +
        '<div class="td-chart-box"><canvas id="tdProgChart-' + sid + '"></canvas></div></div>';
    drawTopicChart(sid, topics[idx]);
  }

  function setChartTopic(sid, idx) {
    state.chartTopic[sid] = Number(idx) || 0;
    topicProgress(sid).then(function (topics) { drawTopicChart(sid, topics[state.chartTopic[sid]]); });
  }

  function drawTopicChart(sid, topic) {
    var canvas = $('tdProgChart-' + sid);
    if (!canvas || typeof Chart === 'undefined' || !topic) return;
    if (state.charts[sid]) { state.charts[sid].destroy(); delete state.charts[sid]; }
    var series = topic.series || [];
    var data = series.map(function (s) { return s.score_percent != null ? Number(s.score_percent) : null; });
    var scores = data.filter(function (v) { return v != null; });
    var stats = $('tdProgStats-' + sid);
    if (stats) {
      if (scores.length) {
        var first = Math.round(scores[0]), last = Math.round(scores[scores.length - 1]), d = last - first;
        stats.textContent = series.length + ' assessment' + (series.length === 1 ? '' : 's') + ' · First ' + first + '% → Latest ' + last + '% (' +
          (d > 0 ? '+' + d + ' pts' : (d < 0 ? d + ' pts' : 'no change')) + ')';
      } else stats.textContent = '';
    }
    state.charts[sid] = new Chart(canvas, {
      type: 'line',
      data: {
        labels: series.map(function (s) { return s.label || 'Assessment'; }),
        datasets: [{
          data: data, borderColor: '#2563eb', backgroundColor: 'rgba(37, 99, 235, .10)', fill: true, tension: .3,
          pointBackgroundColor: series.map(function (s) { return s.assessment_type === 'diagnostic' ? '#7c3aed' : '#2563eb'; }),
          pointRadius: 5, pointHoverRadius: 6, spanGaps: true
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        scales: {
          y: { beginAtZero: true, max: 100, ticks: { stepSize: 25, callback: function (v) { return v + '%'; }, color: '#64748b' }, grid: { color: 'rgba(148,163,184,.25)' } },
          x: { ticks: { color: '#475569', maxRotation: 0, autoSkip: true, callback: function (v) { var l = this.getLabelForValue(v) || ''; return l.length > 14 ? l.slice(0, 12) + '…' : l; } }, grid: { display: false } }
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function (ctx) { return 'Score: ' + Math.round(ctx.parsed.y) + '%'; },
              afterLabel: function (ctx) {
                var p = series[ctx.dataIndex];
                if (!p) return '';
                var lines = [p.assessment_type === 'diagnostic' ? 'Diagnostic' : 'Quiz'];
                if (p.correct != null && p.total != null) lines.push(p.correct + '/' + p.total + ' correct');
                return lines;
              }
            }
          }
        }
      }
    });
  }

  /* ── 08/09 Quiz Results ── */
  function renderQuizzes() {
    var body = $('tdAnaQuizRows');
    var list = (state.quizzes || []).slice();
    if (!list.length) { body.innerHTML = ''; setEmpty('tdAnaQuizEmpty', '<p>No published assignments yet.</p>'); return; }
    var q = searchTerm();
    var sort = ($('tdAnaSort') || {}).value || 'latest';
    list = list.filter(function (a) { return !q || String(a.title || '').toLowerCase().indexOf(q) >= 0; });
    if (sort === 'name') list.sort(function (a, b) { return String(a.title).localeCompare(String(b.title)); });
    else if (sort === 'progress_asc') list.sort(function (a, b) { return (a.avg_score_percent || 0) - (b.avg_score_percent || 0); });
    else if (sort === 'progress_desc') list.sort(function (a, b) { return (b.avg_score_percent || 0) - (a.avg_score_percent || 0); });
    else list.sort(function (a, b) { return b.assignment_id - a.assignment_id; });
    setEmpty('tdAnaQuizEmpty', list.length ? '' : '<p>No assignments match your search.</p>');
    body.innerHTML = list.map(function (a) {
      var open = !!state.expanded['q' + a.assignment_id];
      var results = a.student_results || [];
      var submitted = results.filter(function (r) { return r.status === 'submitted'; }).length;
      var head = '<tr class="td-clickable' + (open ? ' td-expanded' : '') + '" onclick="tdAnalytics.toggleQuiz(' + a.assignment_id + ')">' +
        '<td><span style="display:inline-flex;gap:10px;align-items:center;"><i class="fas fa-chevron-' + (open ? 'down' : 'right') + '" style="color:var(--td-blue-700);font-size:12px;"></i><b style="font-weight:' + (open ? 700 : 500) + ';">' + U.esc(a.title) + '</b></span></td>' +
        '<td><b>' + (a.avg_score_percent != null ? U.pct(a.avg_score_percent) + '%' : '—') + '</b></td>' +
        '<td><b>' + submitted + '</b> <span style="color:var(--td-muted);">/ ' + results.length + ' submitted - click to view</span></td>' +
        '<td style="text-align:right;"><span class="td-chev"><i class="fas fa-chevron-down"></i></span></td></tr>';
      if (!open) return head;
      var inner = results.length ? results.map(function (r) {
        var p = r.score_percent != null ? U.pct(r.score_percent) : null;
        var statusHtml;
        if (r.status === 'submitted' && p != null) statusHtml = U.scoreBadge(p);
        else if (r.status === 'in_progress') statusHtml = '<span class="td-status-badge" style="background:var(--td-blue-50);color:var(--td-blue-700);">In progress</span>';
        else if (r.status === 'overdue') statusHtml = '<span class="td-status-badge td-status-help">Overdue</span>';
        else statusHtml = '<span class="td-status-badge td-status-muted">Not submitted</span>';
        var scoreTxt = r.status === 'submitted' && r.score != null && r.max_score != null ? (r.score + ' / ' + r.max_score + (p != null ? ' (' + p + '%)' : '')) : (p != null ? p + '%' : '—');
        return '<tr><td>' + U.studentCell(r.username || ('#' + r.student_id)) + '</td><td><b>' + U.esc(scoreTxt) + '</b></td>' +
          '<td>' + (r.status === 'submitted' && r.score != null ? U.esc(r.score) : '—') + '</td>' +
          '<td>' + (r.max_score != null ? U.esc(r.max_score) : '—') + '</td><td>' + statusHtml + '</td>' +
          '<td style="text-align:right;"><button type="button" class="td-btn td-btn-outline-blue td-btn-sm" onclick="tdAnalytics.viewStudent(' + r.student_id + ')">View Details</button></td></tr>';
      }).join('') : '<tr><td colspan="6" style="color:var(--td-muted);">No students enrolled.</td></tr>';
      return head + '<tr><td colspan="4" class="td-detail-cell"><div style="padding:14px 16px 16px;"><h4 style="margin:0 0 10px;">Student Results</h4>' +
        '<div class="td-table-wrap"><table class="td-data"><thead><tr><th>Student</th><th>Score</th><th>Correct Answers</th><th>Total Questions</th><th>Status</th><th style="text-align:right;">Actions</th></tr></thead>' +
        '<tbody>' + inner + '</tbody></table></div></div></td></tr>';
    }).join('');
  }

  function toggleQuiz(id) {
    state.expanded['q' + id] = !state.expanded['q' + id];
    rerender();
  }

  /* ── 10 Struggling Students ── */
  function renderStruggling() {
    var body = $('tdAnaStrugglingRows');
    var list = state.struggling || [];
    if (!list.length) { body.innerHTML = ''; setEmpty('tdAnaStrugglingEmpty', '<p>No struggling students detected — great job!</p>'); return; }
    var rows = sortStudents(filterStudents(list));
    setEmpty('tdAnaStrugglingEmpty', rows.length ? '' : '<p>No students match your search.</p>');
    body.innerHTML = rows.map(function (s) {
      var sid = s.student_id;
      var open = !!state.expanded['s' + sid];
      var count = s.weak_topic_count || (s.weak_topics || []).length || 0;
      return '<tr class="td-clickable' + (open ? ' td-expanded' : '') + '" onclick="tdAnalytics.toggleStruggling(' + sid + ')">' +
        '<td>' + U.studentCell(U.studentName(s)) + '</td>' +
        '<td>' + (count ? '<span class="td-count-badge">' + count + '</span><span style="color:var(--td-muted);">Click to view topics</span>' : '<span style="color:var(--td-muted);">—</span>') + '</td>' +
        '<td style="text-align:right;"><span class="td-chev"><i class="fas fa-chevron-down"></i></span></td></tr>' +
        (open ? '<tr><td colspan="3" class="td-detail-cell"><div class="td-detail" id="tdStrugDetail-' + sid + '">' + weakTopicsCard(s) +
          '<div class="td-detail-card" data-slot="recent"><h4><i class="far fa-clock" style="color:var(--td-blue-700);"></i> Recent Performance</h4><p class="td-desc">Latest quiz/topic scores in weak areas.</p><div class="td-spinner"></div></div>' +
          '</div></td></tr>' : '');
    }).join('');
    rows.forEach(function (s) { if (state.expanded['s' + s.student_id]) fillRecent(s); });
  }

  function weakTopicsCard(s) {
    var topics = s.weak_topics || [];
    var html = topics.length ? topics.map(function (t) {
      var p = U.pct(t.score_percent);
      return '<div class="td-weak-row"><span>' + U.esc(t.topic_name || ('Topic #' + t.topic_id)) + '</span>' +
        '<span class="td-progress-track"><span class="td-progress-fill red" style="width:' + (p || 0) + '%;"></span></span>' +
        '<span>' + (p != null ? p + '%' : '—') + '</span></div>';
    }).join('') : '<p class="td-desc">No weak topics recorded; flagged by low overall progress (' + (U.pct(s.overall_progress) || 0) + '%).</p>';
    return '<div class="td-detail-card"><h4><i class="fas fa-chart-column" style="color:var(--td-blue-700);"></i> Weak Topics</h4><p class="td-desc">Topics where the student needs improvement.</p>' + html + '</div>';
  }

  async function fillRecent(s) {
    var sid = s.student_id;
    var card = document.querySelector('#tdStrugDetail-' + sid + ' [data-slot="recent"]');
    if (!card) return;
    var head = '<h4><i class="far fa-clock" style="color:var(--td-blue-700);"></i> Recent Performance</h4><p class="td-desc">Latest quiz/topic scores in weak areas.</p>';
    try {
      var topics = await topicProgress(sid);
      var weakIds = {};
      (s.weak_topics || []).forEach(function (t) { weakIds[t.topic_id] = true; });
      var points = [];
      topics.forEach(function (t) {
        if (Object.keys(weakIds).length && !weakIds[t.topic_id]) return;
        var lp = latestPoint(t);
        if (lp) points.push({ topic: t.topic_name, p: lp });
      });
      points.sort(function (a, b) { return String(b.p.submitted_at || '').localeCompare(String(a.p.submitted_at || '')); });
      card = document.querySelector('#tdStrugDetail-' + sid + ' [data-slot="recent"]');
      if (!card) return;
      card.innerHTML = head + (points.length
        ? '<div class="td-table-wrap"><table class="td-data"><thead><tr><th>Quiz / Topic</th><th>Score</th><th>Date</th></tr></thead><tbody>' +
          points.slice(0, 6).map(function (x) {
            var pct = U.pct(x.p.score_percent);
            var score = (x.p.correct != null && x.p.total != null ? x.p.correct + ' / ' + x.p.total + ' ' : '') + '(' + pct + '%)';
            return '<tr><td>' + U.esc(x.p.label || 'Assessment') + '<br><small style="color:var(--td-muted);">' + U.esc(x.topic || '') + '</small></td>' +
              '<td style="color:' + (pct < 50 ? 'var(--td-red)' : 'var(--td-ink)') + ';font-weight:700;">' + U.esc(score) + '</td><td>' + U.esc(fmtDate(x.p.submitted_at)) + '</td></tr>';
          }).join('') + '</tbody></table></div>'
        : '<p class="td-desc">No diagnostic or quiz attempts yet.</p>');
    } catch (err) {
      card.innerHTML = head + '<p class="td-error">' + U.esc(err.message) + '</p>';
    }
  }

  function toggleStruggling(sid) {
    state.expanded['s' + sid] = !state.expanded['s' + sid];
    rerender();
  }

  /* ── 11 Roster ── */
  function renderRoster() {
    var roster = state.roster || [];
    var counts = { on_track: 0, needs_help: 0, not_attempted: 0 };
    roster.forEach(function (s) { counts[studentStatus(s)]++; });
    var total = roster.length;
    var pct = function (n) { return total ? Math.round(100 * n / total) : 0; };
    $('tdAnaDonutTotal').textContent = total;
    $('tdAnaStatTotal').textContent = total;
    $('tdAnaStatOnTrack').textContent = counts.on_track;
    $('tdAnaStatHelp').textContent = counts.needs_help;
    $('tdAnaStatNone').textContent = counts.not_attempted;
    var a = total ? 100 * counts.on_track / total : 0;
    var b = total ? a + 100 * counts.needs_help / total : 0;
    $('tdAnaDonut').style.background = total
      ? 'conic-gradient(#60a5fa 0% ' + a + '%, #f87171 ' + a + '% ' + b + '%, #e2e8f0 ' + b + '% 100%)'
      : '#e2e8f0';
    $('tdAnaLegend').innerHTML =
      '<span><span class="td-legend-dot" style="background:#60a5fa;"></span>On Track</span><span>' + counts.on_track + ' (' + pct(counts.on_track) + '%)</span>' +
      '<span><span class="td-legend-dot" style="background:#f87171;"></span>Needs Help</span><span>' + counts.needs_help + ' (' + pct(counts.needs_help) + '%)</span>' +
      '<span><span class="td-legend-dot" style="background:#e2e8f0;"></span>Not Attempted</span><span>' + counts.not_attempted + ' (' + pct(counts.not_attempted) + '%)</span>';

    var body = $('tdAnaRosterRows');
    if (!total) { body.innerHTML = ''; setEmpty('tdAnaRosterEmpty', '<p>No students enrolled.</p>'); return; }
    var statusFilter = ($('tdAnaRosterStatus') || {}).value || '';
    var rows = sortStudents(filterStudents(roster)).filter(function (s) { return !statusFilter || studentStatus(s) === statusFilter; });
    setEmpty('tdAnaRosterEmpty', rows.length ? '' : '<p>No students match these filters.</p>');
    var BADGE = {
      on_track: '<span class="td-status-badge td-status-good">ON TRACK</span>',
      needs_help: '<span class="td-status-badge td-status-help">NEEDS HELP</span>',
      not_attempted: '<span class="td-status-badge td-status-muted">NOT ATTEMPTED</span>'
    };
    body.innerHTML = rows.map(function (s, i) {
      var st = studentStatus(s);
      return '<tr><td>' + (i + 1) + '</td><td>' + U.studentCell(U.studentName(s)) + '</td><td>' + U.esc(s.grade_label || '—') + '</td>' +
        '<td>' + U.progress(s.overall_progress, st === 'needs_help' ? 'red' : '') + '</td><td>' + BADGE[st] + '</td>' +
        '<td style="text-align:right;"><button type="button" class="td-btn td-btn-outline-blue td-btn-sm" onclick="tdAnalytics.viewStudent(' + s.student_id + ')">View Details</button></td></tr>';
    }).join('');
  }

  // "View Details" → Topic Progress tab with that student expanded.
  function viewStudent(sid) {
    state.expanded['p' + sid] = true;
    var search = $('tdAnaSearch');
    if (search) search.value = '';
    switchTab('progress');
    setTimeout(function () {
      var row = document.querySelector('#tdAnaProgressRows tr[data-sid="' + sid + '"]');
      if (row) row.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 250);
  }

  // Legacy entry point (was the analytics modal) now opens this view.
  document.addEventListener('DOMContentLoaded', function () {
    window.openLmsTeacherAnalytics = function (preselectedClassId) {
      if (preselectedClassId != null) state.classId = String(preselectedClassId);
      window.tdShowView('analytics');
    };
  });

  window.tdAnalytics = {
    open: open, load: load, switchTab: switchTab, rerender: rerender,
    toggleProgress: toggleProgress, setChartTopic: setChartTopic, toggleQuiz: toggleQuiz,
    toggleStruggling: toggleStruggling, viewStudent: viewStudent, _state: state
  };
})();
