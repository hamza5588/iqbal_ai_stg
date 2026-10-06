/* Teacher → Classes (screens 02–04). Same endpoints as the former class hub + lms-teacher-class.js (both removed):
   GET /api/lms/classes/mine, /classes/grade-options, /users/me/grade-profile, PUT /teachers/me/grades,
   POST /api/lms/classes, GET /classes/:id/students, /classes/:id/eligible-students,
   POST /classes/:id/students {student_id}, DELETE /classes/:id/students/:sid. */
(function () {
  var U = window.tdUtil;
  var state = {
    classes: [],
    gradeOptions: [],
    teachingGrades: [],
    teachingGradeLabels: [],
    loaded: false,
    expandedId: null,
    tab: 'roster',
    roster: [],
    eligible: [],
    selected: {},
    quizzes: [],
    panel: null,       // { sid, kind } - the one open per-student progress panel
    topicCache: {},
    charts: [],
    loadSeq: 0
  };

  function $(id) { return document.getElementById(id); }

  async function load() {
    var list = $('tdClassList');
    if (!list) return;
    var seq = ++state.loadSeq;
    if (!state.loaded) list.innerHTML = '<div class="td-empty"><div class="td-spinner"></div><p>Loading classes…</p></div>';
    try {
      var results = await Promise.all([
        lmsApi('/api/lms/classes/mine'),
        state.gradeOptions.length ? Promise.resolve(state.gradeOptions) : lmsApi('/api/lms/classes/grade-options').catch(function () { return []; }),
        lmsApi('/api/lms/users/me/grade-profile').catch(function () { return {}; })
      ]);
      if (seq !== state.loadSeq) return;
      state.classes = results[0] || [];
      state.gradeOptions = results[1] || [];
      state.teachingGrades = (results[2] && results[2].teaching_grades) || [];
      state.teachingGradeLabels = (results[2] && results[2].teaching_grade_labels) || [];
      state.loaded = true;
      fillGradeControls();
      render();
      loadRetakeRequests();
      if (state.expandedId != null) loadDetail(state.expandedId, true);
    } catch (err) {
      list.innerHTML = '<div class="td-empty"><p class="td-error">' + U.esc(err.message || 'Failed to load classes.') + '</p></div>';
    }
  }

  function fillGradeControls() {
    // Class grade select — options outside the teacher's assigned grades are disabled (legacy patchClassCreateForm rule).
    var sel = $('tdClassGrade');
    if (sel) {
      var cur = sel.value;
      sel.innerHTML = '<option value="">Select grade</option>' + state.gradeOptions.map(function (g) {
        var allowed = !state.teachingGrades.length || state.teachingGrades.indexOf(g.value) >= 0;
        return '<option value="' + U.esc(g.value) + '"' + (allowed ? '' : ' disabled') + '>' + U.esc(g.label) + '</option>';
      }).join('');
      if (cur) sel.value = cur;
    }
    var input = $('tdTeachingGrades');
    if (input && document.activeElement !== input) input.value = state.teachingGrades.join(',');
    var hint = $('tdTeachingGradesHint');
    if (hint) {
      hint.textContent = state.teachingGrades.length
        ? 'Currently assigned: ' + (state.teachingGradeLabels.join(', ') || state.teachingGrades.join(', '))
        : 'Not set — set your teaching grades, or ask admin to assign you (e.g. 8th grade).';
    }
    var filter = $('tdClassGradeFilter');
    if (filter) {
      var fcur = filter.value;
      var grades = Array.from(new Set(state.classes.map(function (c) { return c.grade_level; }).filter(function (g) { return g != null && g !== ''; }).map(String)))
        .sort(function (a, b) { return a.localeCompare(b, undefined, { numeric: true }); });
      filter.innerHTML = '<option value="">All grades</option>' + grades.map(function (g) {
        return '<option value="' + U.esc(g) + '">' + U.esc(U.gradeLabel(g)) + '</option>';
      }).join('');
      if (grades.indexOf(fcur) >= 0) filter.value = fcur;
    }
  }

  function filteredClasses() {
    var q = (($('tdClassSearch') || {}).value || '').trim().toLowerCase();
    var g = ($('tdClassGradeFilter') || {}).value || '';
    var sort = ($('tdClassSort') || {}).value || 'latest';
    var rows = state.classes.filter(function (c) {
      if (g && String(c.grade_level) !== g) return false;
      if (!q) return true;
      return [c.name, c.join_code, c.description].some(function (v) { return v && String(v).toLowerCase().indexOf(q) >= 0; });
    });
    if (sort === 'name') rows.sort(function (a, b) { return String(a.name).localeCompare(String(b.name)); });
    else if (sort === 'grade') rows.sort(function (a, b) { return String(a.grade_level || '').localeCompare(String(b.grade_level || ''), undefined, { numeric: true }); });
    else rows.sort(function (a, b) { return b.id - a.id; });
    return rows;
  }

  var CLASS_ICONS = [['fa-book-open', '#dbeafe', '#1a56db'], ['fa-flask', '#dcfce7', '#16a34a'], ['fa-file-lines', '#ffedd5', '#ea580c'], ['fa-calculator', '#ede9fe', '#7c3aed']];

  function render() {
    var list = $('tdClassList');
    if (!list) return;
    var tab = $('tdClassTabAll');
    if (tab) tab.textContent = 'All Classes (' + state.classes.length + ')';
    if (!state.classes.length) {
      list.innerHTML = '<div class="td-empty"><img class="td-empty-ic" src="' + U.esc(window.TEACHER_CFG.icons.classes) + '" alt="">' +
        U.empty('No classes yet', 'Click Create Class above to create your first class.') + '</div>';
      return;
    }
    var rows = filteredClasses();
    if (!rows.length) {
      list.innerHTML = '<div class="td-empty">' + U.empty('No matching classes', 'Try a different search or grade.') + '</div>';
      return;
    }
    list.innerHTML = rows.map(function (c) {
      var ic = CLASS_ICONS[c.id % CLASS_ICONS.length];
      var open = state.expandedId === c.id;
      var count = c.student_count != null ? c.student_count : 0;
      return '<div class="td-class-card" data-class-id="' + c.id + '">' +
        '<div class="td-class-head" onclick="tdClasses.toggle(' + c.id + ')" role="button" aria-expanded="' + open + '">' +
          '<div class="td-avatar-sm" style="background:' + ic[1] + ';color:' + ic[2] + ';"><i class="fas ' + ic[0] + '"></i></div>' +
          '<div class="td-class-meta"><h3>' + U.esc(c.name) + '</h3>' +
            '<div class="td-pill-row">' +
              '<span class="td-pill green"><i class="fas fa-users"></i> ' + U.esc(U.gradeLabel(c.grade_level).toUpperCase()) + '</span>' +
              '<span class="td-pill">CODE: <span class="td-code">' + U.esc(c.join_code || '—') + '</span></span>' +
              '<span class="td-pill">' + count + ' STUDENT' + (count === 1 ? '' : 'S') + '</span>' +
            '</div>' +
            (c.description ? '<p class="td-desc" style="margin:8px 0 0;">' + U.esc(c.description) + '</p>' : '') +
          '</div>' +
          '<div class="td-side-meta">' +
            '<button type="button" class="td-btn td-btn-green td-btn-sm" onclick="event.stopPropagation(); tdClasses.toggle(' + c.id + ', true)">Manage</button>' +
            '<span class="td-chev" style="transform:rotate(' + (open ? 180 : 0) + 'deg);"><i class="fas fa-chevron-down"></i></span>' +
          '</div>' +
        '</div>' +
        (open ? '<div class="td-class-body" id="tdClassBody-' + c.id + '">' + detailShell() + '</div>' : '') +
      '</div>';
    }).join('');
    if (state.expandedId != null) renderDetail();
  }

  function detailShell() {
    return '<div class="td-inner-tabs">' +
      '<button type="button" class="td-stab' + (state.tab === 'roster' ? ' active' : '') + '" onclick="tdClasses.setTab(\'roster\')"><i class="fas fa-users"></i> Roster</button>' +
      '<button type="button" class="td-stab' + (state.tab === 'add' ? ' active' : '') + '" onclick="tdClasses.setTab(\'add\')"><i class="fas fa-user-plus"></i> Add Students</button>' +
      '</div><div data-slot="detail"><div class="td-empty"><div class="td-spinner"></div></div></div>';
  }

  function toggle(classId, forceOpen) {
    if (state.expandedId === classId && !forceOpen) {
      state.expandedId = null;
      render();
      return;
    }
    if (state.expandedId !== classId) {
      state.expandedId = classId;
      state.tab = 'roster';
      state.roster = [];
      state.eligible = [];
      state.selected = {};
      state.quizzes = [];
      state.panel = null;
      state.topicCache = {};
      destroyCharts();
      render();
      loadDetail(classId);
    }
  }

  async function loadDetail(classId, silent) {
    try {
      var r = await Promise.all([
        lmsApi('/api/lms/classes/' + classId + '/students'),
        lmsApi('/api/lms/classes/' + classId + '/eligible-students'),
        // only feeds the Status column ("Not attempted" needs to know about submitted quizzes)
        lmsApi('/api/lms/classes/' + classId + '/analytics/quizzes').catch(function () { return []; })
      ]);
      if (state.expandedId !== classId) return;
      state.roster = r[0] || [];
      state.eligible = r[1] || [];
      state.quizzes = r[2] || [];
      state.topicCache = {};
      var c = state.classes.find(function (x) { return x.id === classId; });
      if (c && c.student_count !== state.roster.length) {
        c.student_count = state.roster.length;
        render();
        return;
      }
      renderDetail();
    } catch (err) {
      var slot = document.querySelector('#tdClassBody-' + classId + ' [data-slot="detail"]');
      if (slot && !silent) slot.innerHTML = '<p class="td-error">' + U.esc(err.message) + '</p>';
    }
  }

  function renderDetail() {
    var slot = document.querySelector('#tdClassBody-' + state.expandedId + ' [data-slot="detail"]');
    if (!slot) return;
    if (state.tab === 'roster') renderRoster(slot); else renderAddStudents(slot);
  }

  /* ── Roster status + per-student progress panels ──
     Status is the same rule as Class Analytics → Roster (teacher-analytics.js studentStatus).
     Panels read GET /classes/:id/students/:sid/progress/by-topic: per topic, every diagnostic
     and quiz attempt that touched it, oldest first. */
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
  function studentStatus(s) {
    var untouched = !Number(s.overall_progress) && !(s.weak_topic_count || (s.weak_topics || []).length) &&
      latestQuizScore(s.student_id) == null;
    if (untouched) return 'not_attempted';
    return s.is_struggling ? 'needs_help' : 'on_track';
  }
  var STATUS_BADGE = {
    on_track: '<span class="td-status-badge td-status-good">MASTERED</span>',
    needs_help: '<span class="td-status-badge td-status-help">STILL LEARNING</span>',
    not_attempted: '<span class="td-status-badge td-status-muted">NOT ATTEMPTED</span>'
  };
  var PANELS = {
    diagnostic: { label: 'Diagnostic Progress', icon: 'fa-clipboard-check' },
    quiz: { label: 'Quiz Progress', icon: 'fa-list-check' },
    graph: { label: 'Graph', icon: 'fa-chart-line' }
  };

  function topicSeries(studentId) {
    var key = state.expandedId + ':' + studentId;
    if (!state.topicCache[key]) {
      state.topicCache[key] = lmsApi('/api/lms/classes/' + state.expandedId + '/students/' + studentId + '/progress/by-topic')
        .then(function (p) { return (p && p.topics) || []; })
        .catch(function (err) { delete state.topicCache[key]; throw err; });
    }
    return state.topicCache[key];
  }
  function fmtDate(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    return isNaN(d.getTime()) ? '—' : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  }
  function scored(points) { return points.filter(function (p) { return p.score_percent != null; }); }
  function changeText(first, last) {
    var d = Math.round(last) - Math.round(first);
    return d > 0 ? '+' + d + ' pts' : (d < 0 ? d + ' pts' : 'no change');
  }
  function changeHtml(first, last) {
    var d = Math.round(last) - Math.round(first);
    var color = d > 0 ? 'var(--td-green)' : (d < 0 ? 'var(--td-red)' : 'var(--td-muted)');
    return '<b style="color:' + color + ';">' + changeText(first, last) + '</b>';
  }
  function destroyCharts() {
    (state.charts || []).forEach(function (c) { try { c.destroy(); } catch (e) { /* ignore */ } });
    state.charts = [];
  }

  function renderRoster(slot) {
    destroyCharts();
    var tpl = $('tdTplClassRoster');
    slot.innerHTML = '';
    slot.appendChild(tpl.content.cloneNode(true));
    var body = slot.querySelector('[data-slot="rows"]');
    if (!state.roster.length) {
      slot.querySelector('.td-table-wrap').hidden = true;
      slot.querySelector('[data-slot="empty"]').hidden = false;
      return;
    }
    var open = state.panel;
    if (open && !state.roster.some(function (s) { return s.student_id === open.sid; })) open = state.panel = null;
    body.innerHTML = state.roster.map(function (s) {
      var name = U.studentName(s);
      var sid = s.student_id;
      var st = studentStatus(s);
      var isOpen = open && open.sid === sid;
      var btns = Object.keys(PANELS).map(function (kind) {
        var on = isOpen && open.kind === kind;
        return '<button type="button" class="td-btn td-btn-outline-blue td-btn-sm' + (on ? ' td-btn-on' : '') + '" aria-expanded="' + !!on + '" data-testid="roster-' + kind + '" ' +
          'onclick="tdClasses.openPanel(' + sid + ', \'' + kind + '\')"><i class="fas ' + PANELS[kind].icon + '"></i> ' + PANELS[kind].label + '</button>';
      }).join('');
      return '<tr' + (isOpen ? ' class="td-expanded"' : '') + '><td>' + U.studentCell(name) + '</td>' +
        '<td>' + U.esc(s.grade_label || '—') + '</td>' +
        '<td>' + U.progress(s.overall_progress, st === 'needs_help' ? 'red' : '') + '</td>' +
        '<td>' + STATUS_BADGE[st] + '</td>' +
        '<td><div class="td-row-actions">' + btns +
        '<button type="button" class="td-btn td-btn-danger-outline td-btn-sm" onclick="tdClasses.removeStudent(' + sid + ')">Remove</button></div></td></tr>' +
        (isOpen ? '<tr><td colspan="5" class="td-detail-cell"><div class="td-roster-panel" id="tdRosterPanel-' + sid + '"><div class="td-empty" style="padding:20px;"><div class="td-spinner"></div></div></div></td></tr>' : '');
    }).join('');
    if (open) fillPanel(open.sid, open.kind);
  }

  function openPanel(sid, kind) {
    var same = state.panel && state.panel.sid === sid && state.panel.kind === kind;
    state.panel = same ? null : { sid: sid, kind: kind };
    renderDetail();
  }

  async function fillPanel(sid, kind) {
    var student = state.roster.find(function (s) { return s.student_id === sid; });
    var name = student ? U.studentName(student) : 'Student';
    var topics;
    try { topics = await topicSeries(sid); } catch (err) {
      var errEl = $('tdRosterPanel-' + sid);
      if (errEl) errEl.innerHTML = '<p class="td-error">' + U.esc(err.message || 'Could not load progress.') + '</p>';
      return;
    }
    var el = $('tdRosterPanel-' + sid);
    if (!el || !state.panel || state.panel.sid !== sid || state.panel.kind !== kind) return;
    var head = '<div class="td-panel-head"><h4><i class="fas ' + PANELS[kind].icon + '" style="color:var(--td-blue-700);"></i> ' +
      PANELS[kind].label + ' — ' + U.esc(name) + '</h4>' +
      '<button type="button" class="td-btn td-btn-outline-blue td-btn-sm" onclick="tdClasses.openPanel(' + sid + ', \'' + kind + '\')">Close</button></div>';
    if (kind === 'graph') { el.innerHTML = head + graphHtml(sid, topics); drawGraphs(sid, topics); }
    else el.innerHTML = head + typeProgressHtml(topics, kind);
  }

  /* Diagnostic Progress / Quiz Progress: that assessment type only. */
  function typeProgressHtml(topics, type) {
    var word = type === 'diagnostic' ? 'diagnostic' : 'quiz';
    var attempts = {};
    var rows = [];
    topics.forEach(function (t) {
      var pts = scored(t.series || []).filter(function (p) { return p.assessment_type === type; });
      if (!pts.length) return;
      pts.forEach(function (p) {
        var a = attempts[p.attempt_id] || (attempts[p.attempt_id] = { label: p.label, at: p.submitted_at, correct: 0, total: 0, id: p.attempt_id });
        a.correct += Number(p.correct) || 0;
        a.total += Number(p.total) || 0;
      });
      rows.push({ name: t.topic_name || ('Topic #' + t.topic_id), n: pts.length, first: pts[0], last: pts[pts.length - 1] });
    });
    if (!rows.length) {
      return '<div class="td-empty" style="padding:18px;"><p>This student has not submitted a ' + word + ' yet.</p></div>';
    }
    var list = Object.keys(attempts).map(function (k) { return attempts[k]; })
      .sort(function (a, b) { return String(a.at || '').localeCompare(String(b.at || '')) || (a.id - b.id); });
    var attemptRows = list.map(function (a, i) {
      var pct = a.total ? Math.round(100 * a.correct / a.total) : null;
      return '<tr><td>' + (i + 1) + '</td><td>' + U.esc(a.label || 'Assessment') + '</td><td>' + U.esc(fmtDate(a.at)) + '</td>' +
        '<td><b>' + a.correct + ' / ' + a.total + '</b></td><td>' + (pct != null ? '<b>' + pct + '%</b> ' + U.scoreBadge(pct) : '—') + '</td></tr>';
    }).join('');
    rows.sort(function (a, b) { return a.last.score_percent - b.last.score_percent; });
    // First / Change only mean something once a topic has been assessed more than once.
    var repeated = rows.some(function (r) { return r.n > 1; });
    var topicRows = rows.map(function (r) {
      var last = U.pct(r.last.score_percent), first = U.pct(r.first.score_percent);
      return '<tr><td>' + U.esc(r.name) + '</td><td>' + r.n + '</td>' +
        (repeated ? '<td>' + (r.n > 1 ? first + '%' : '—') + '</td>' : '') +
        '<td>' + U.progress(r.last.score_percent, last < 40 ? 'red' : '') + '</td>' +
        (repeated ? '<td style="white-space:nowrap;">' + (r.n > 1 ? changeHtml(r.first.score_percent, r.last.score_percent) : '<span style="color:var(--td-muted);">first attempt</span>') + '</td>' : '') +
        '</tr>';
    }).join('');
    var firstA = list[0], lastA = list[list.length - 1];
    var fp = firstA.total ? 100 * firstA.correct / firstA.total : 0, lp = lastA.total ? 100 * lastA.correct / lastA.total : 0;
    var summary = list.length + ' ' + word + ' attempt' + (list.length === 1 ? '' : 's') + ' · ' +
      (list.length > 1 ? 'First ' + Math.round(fp) + '% → Latest ' + Math.round(lp) + '% (' + changeText(fp, lp) + ')' : 'Score ' + Math.round(lp) + '%');
    return '<p class="td-desc" style="margin:0 0 12px;">' + U.esc(summary) + '</p><div class="td-detail" style="padding:0;">' +
      '<div class="td-detail-card"><h4>Attempts</h4><p class="td-desc">Every submitted ' + word + ', oldest first.</p>' +
      '<div class="td-table-wrap"><table class="td-data"><thead><tr><th>#</th><th>' + (type === 'diagnostic' ? 'Diagnostic' : 'Quiz') + '</th><th>Date</th><th>Correct</th><th>Score</th></tr></thead><tbody>' + attemptRows + '</tbody></table></div></div>' +
      '<div class="td-detail-card"><h4>By topic</h4><p class="td-desc">Latest ' + word + ' score per topic, weakest first.</p>' +
      '<div class="td-table-wrap"><table class="td-data"><thead><tr><th>Topic</th><th>Attempts</th>' + (repeated ? '<th>First</th>' : '') + '<th>' + (repeated ? 'Latest' : 'Score') + '</th>' + (repeated ? '<th>Change</th>' : '') + '</tr></thead><tbody>' + topicRows + '</tbody></table></div></div></div>';
  }

  /* Graph: diagnostic + quiz together, one small chart per topic, plus an overall chart. */
  function graphCharts(topics) {
    var charts = topics.map(function (t) {
      return { title: t.topic_name || ('Topic #' + t.topic_id), points: scored(t.series || []) };
    }).filter(function (c) { return c.points.length; });
    charts.sort(function (a, b) { return a.points[a.points.length - 1].score_percent - b.points[b.points.length - 1].score_percent; });
    // Overall: after each attempt, every topic's most recent result so far, pooled by question
    // (total correct ÷ total questions). Weighting by questions matches the roster's Progress
    // column; a plain average of topic percentages let a 1-question topic count as much as a 10-question one.
    var events = {};
    topics.forEach(function (t) {
      scored(t.series || []).forEach(function (p) {
        var e = events[p.attempt_id] || (events[p.attempt_id] = { id: p.attempt_id, at: p.submitted_at, label: p.label, assessment_type: p.assessment_type, scores: {} });
        e.scores[t.topic_id] = p;
      });
    });
    var latest = {};
    var overall = Object.keys(events).map(function (k) { return events[k]; })
      .sort(function (a, b) { return String(a.at || '').localeCompare(String(b.at || '')) || (a.id - b.id); })
      .map(function (e) {
        Object.keys(e.scores).forEach(function (tid) { latest[tid] = e.scores[tid]; });
        var sum = poolLatest(Object.keys(latest).map(function (tid) { return latest[tid]; }));
        return { label: e.label, assessment_type: e.assessment_type, submitted_at: e.at,
          score_percent: sum.percent, correct: sum.weighted ? sum.correct : null, total: sum.weighted ? sum.total : null };
      });
    if (overall.length) charts.unshift({ title: 'Overall (all topics)', points: overall, overall: true });
    return charts;
  }

  // Pool one result per topic into a single score. Falls back to a plain average only when
  // a result carries no question counts.
  function poolLatest(points) {
    var correct = 0, total = 0, weighted = points.length > 0;
    points.forEach(function (p) {
      var c = Number(p.correct), t = Number(p.total);
      if (p.correct == null || p.total == null || !(t > 0) || isNaN(c)) { weighted = false; return; }
      correct += c; total += t;
    });
    if (weighted) return { weighted: true, correct: correct, total: total, percent: 100 * correct / total };
    var avg = points.reduce(function (a, p) { return a + Number(p.score_percent); }, 0) / (points.length || 1);
    return { weighted: false, percent: avg, count: points.length };
  }

  // The current "Now" figure of the Overall chart spelled out, e.g. "Now: 5 correct out of 10 questions across 5 topics = 50%."
  function overallExample(topics) {
    var latest = topics.map(function (t) { return scored(t.series || []); })
      .filter(function (pts) { return pts.length; })
      .map(function (pts) { return pts[pts.length - 1]; });
    if (!latest.length) return '';
    var sum = poolLatest(latest);
    var across = ' across ' + latest.length + ' topic' + (latest.length === 1 ? '' : 's');
    return sum.weighted
      ? 'Now: ' + sum.correct + ' correct out of ' + sum.total + ' questions' + across + ' = ' + Math.round(sum.percent) + '%.'
      : 'Now: average of the latest score' + across + ' = ' + Math.round(sum.percent) + '%.';
  }

  function graphHtml(sid, topics) {
    var charts = graphCharts(topics);
    if (!charts.length) return '<div class="td-empty" style="padding:18px;"><p>No diagnostic or quiz data for this student yet.</p></div>';
    return '<p class="td-desc" style="margin:0 0 12px;">Where the student started and where they are now, per topic — diagnostic and quiz results together, weakest topic first. ' +
      '<span class="td-legend-dot" style="background:#7c3aed;"></span> Diagnostic &nbsp; <span class="td-legend-dot" style="background:#2563eb;"></span> Quiz</p>' +
      '<div class="td-graph-grid">' + charts.map(function (c, i) {
        var first = c.points[0].score_percent, last = c.points[c.points.length - 1].score_percent;
        var stat = c.points.length > 1
          ? 'Started ' + Math.round(first) + '% → Now ' + Math.round(last) + '% (' + changeHtml(first, last) + ')'
          : 'Now ' + Math.round(last) + '% <span style="color:var(--td-muted);">(one assessment so far)</span>';
        var note = c.overall
          ? '<p class="td-graph-note"><i class="fas fa-circle-info"></i> How this is calculated: after each diagnostic or quiz, we take the student’s most recent result in every topic assessed so far, then divide the total correct answers by the total questions. A later quiz replaces the earlier result only for the topics it covers. ' +
            U.esc(overallExample(topics)) + '</p>'
          : '';
        return '<div class="td-detail-card' + (c.overall ? ' td-graph-overall' : '') + '"><h4>' + U.esc(c.title) + '</h4>' +
          '<p class="td-desc">' + stat + '</p><div class="td-mini-chart"><canvas id="tdRosterChart-' + sid + '-' + i + '"></canvas></div>' + note + '</div>';
      }).join('') + '</div>';
  }

  function drawGraphs(sid, topics) {
    if (typeof Chart === 'undefined') return;
    destroyCharts();
    graphCharts(topics).forEach(function (c, i) {
      var canvas = $('tdRosterChart-' + sid + '-' + i);
      if (!canvas) return;
      var pts = c.points;
      state.charts.push(new Chart(canvas, {
        type: 'line',
        data: {
          labels: pts.map(function (p) { return p.label || 'Assessment'; }),
          datasets: [{
            data: pts.map(function (p) { return Number(p.score_percent); }),
            borderColor: c.overall ? '#0f766e' : '#2563eb', backgroundColor: c.overall ? 'rgba(15,118,110,.10)' : 'rgba(37,99,235,.10)',
            fill: true, tension: .3, pointRadius: 5, pointHoverRadius: 6,
            // Dots at 0% and 100% sit on the chart edge; draw them whole instead of half-clipped.
            clip: false,
            pointBackgroundColor: pts.map(function (p) { return p.assessment_type === 'diagnostic' ? '#7c3aed' : '#2563eb'; })
          }]
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          layout: { padding: { top: 8, right: 8 } },
          scales: {
            y: { beginAtZero: true, max: 100, ticks: { stepSize: 50, callback: function (v) { return v + '%'; }, color: '#64748b' }, grid: { color: 'rgba(148,163,184,.25)' } },
            // offset centres each point in its own band, so a single assessment sits mid-chart instead of on the y-axis.
            x: { offset: true, ticks: { color: '#475569', maxRotation: 0, autoSkip: true, callback: function (v) { var l = this.getLabelForValue(v) || ''; return l.length > 12 ? l.slice(0, 10) + '…' : l; } }, grid: { display: false } }
          },
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                label: function (ctx) { return 'Score: ' + Math.round(ctx.parsed.y) + '%'; },
                afterLabel: function (ctx) {
                  var p = pts[ctx.dataIndex];
                  if (!p) return '';
                  var lines = [(p.assessment_type === 'diagnostic' ? 'Diagnostic' : 'Quiz') + ' · ' + fmtDate(p.submitted_at)];
                  if (p.correct != null && p.total != null) lines.push(p.correct + '/' + p.total + ' correct');
                  return lines;
                }
              }
            }
          }
        }
      }));
    });
  }

  function renderAddStudents(slot) {
    var tpl = $('tdTplClassAddStudents');
    slot.innerHTML = '';
    slot.appendChild(tpl.content.cloneNode(true));
    var search = slot.querySelector('[data-slot="search"]');
    var body = slot.querySelector('[data-slot="rows"]');
    var all = slot.querySelector('[data-slot="all"]');
    var countEl = slot.querySelector('[data-slot="count"]');
    var addBtn = slot.querySelector('[data-slot="addSelected"]');

    function selectedIds() { return Object.keys(state.selected).filter(function (k) { return state.selected[k]; }).map(Number); }
    function syncCount() {
      var n = selectedIds().length;
      countEl.textContent = n + ' student' + (n === 1 ? '' : 's') + ' selected';
      addBtn.disabled = n === 0;
    }
    function paint() {
      var q = (search.value || '').trim().toLowerCase();
      var rows = state.eligible.filter(function (s) {
        return !q || [s.username, s.email].some(function (v) { return v && String(v).toLowerCase().indexOf(q) >= 0; });
      });
      if (!state.eligible.length) {
        slot.querySelector('.td-table-wrap').hidden = true;
        slot.querySelector('[data-slot="empty"]').hidden = false;
        search.parentElement.hidden = true;
        slot.querySelector('.td-select-bar').hidden = true;
        return;
      }
      body.innerHTML = rows.map(function (s) {
        var name = U.studentName(s);
        var checked = state.selected[s.student_id] ? ' checked' : '';
        return '<tr><td><input type="checkbox" data-sid="' + s.student_id + '"' + checked + ' aria-label="Select ' + U.esc(name) + '"></td>' +
          '<td>' + U.studentCell(name) + '</td><td>' + U.esc(s.email || '—') + '</td><td>' + U.esc(s.grade_label || '—') + '</td>' +
          '<td style="text-align:right;"><button type="button" class="td-btn td-btn-outline-blue td-btn-sm" onclick="tdClasses.addStudents([' + s.student_id + '])">Add</button></td></tr>';
      }).join('') || '<tr><td colspan="5" style="text-align:center;color:var(--td-muted);">No students match your search.</td></tr>';
      all.checked = rows.length > 0 && rows.every(function (s) { return state.selected[s.student_id]; });
      syncCount();
    }
    body.addEventListener('change', function (e) {
      var sid = e.target.getAttribute('data-sid');
      if (sid) { state.selected[sid] = e.target.checked; syncCount(); }
    });
    all.addEventListener('change', function () {
      body.querySelectorAll('input[data-sid]').forEach(function (cb) {
        cb.checked = all.checked;
        state.selected[cb.getAttribute('data-sid')] = all.checked;
      });
      syncCount();
    });
    search.addEventListener('input', paint);
    slot.querySelector('[data-slot="cancel"]').addEventListener('click', function () { state.selected = {}; paint(); });
    addBtn.addEventListener('click', function () { addStudents(selectedIds()); });
    paint();
  }

  function setTab(tab) {
    state.tab = tab;
    document.querySelectorAll('#tdClassBody-' + state.expandedId + ' .td-inner-tabs .td-stab').forEach(function (b, i) {
      b.classList.toggle('active', (tab === 'roster' && i === 0) || (tab === 'add' && i === 1));
    });
    renderDetail();
  }

  async function addStudents(ids) {
    if (!ids || !ids.length || state.expandedId == null) return;
    var classId = state.expandedId;
    var added = 0;
    var errors = [];
    for (var i = 0; i < ids.length; i++) {
      try {
        await lmsApi('/api/lms/classes/' + classId + '/students', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ student_id: ids[i] })
        });
        added++;
      } catch (err) {
        errors.push(err.message);
      }
    }
    state.selected = {};
    if (added) U.toast(added === 1 ? 'Student added to class' : added + ' students added to class');
    if (errors.length) U.toast(errors[0], 'error');
    await loadDetail(classId);
  }

  async function removeStudent(studentId) {
    if (state.expandedId == null) return;
    if (!confirm('Remove this student from the class?')) return;
    try {
      await lmsApi('/api/lms/classes/' + state.expandedId + '/students/' + studentId, { method: 'DELETE' });
      U.toast('Student removed');
      await loadDetail(state.expandedId);
    } catch (err) {
      U.toast(err.message, 'error');
    }
  }

  function openCreate() {
    var card = $('tdCreateClassCard');
    if (!card) return;
    card.hidden = false;
    fillGradeControls();
    setTimeout(function () {
      card.scrollIntoView({ behavior: 'smooth', block: 'start' });
      var n = $('tdClassName');
      if (n) n.focus({ preventScroll: true });
    }, 30);
  }

  function closeCreate() {
    var form = $('tdCreateClassForm');
    if (form) form.reset();
    fillGradeControls();
    var card = $('tdCreateClassCard');
    if (card) card.hidden = true;
  }

  async function submitCreate(e) {
    e.preventDefault();
    var btn = $('tdCreateClassSubmit');
    var name = ($('tdClassName').value || '').trim();
    var grade = $('tdClassGrade').value;
    var description = ($('tdClassDescription').value || '').trim();
    if (!name || !grade) return;
    if (btn) btn.disabled = true;
    try {
      var created = await lmsApi('/api/lms/classes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name, grade_level: grade || null, description: description || null })
      });
      U.toast('Class created!');
      closeCreate();
      if (created && created.id != null) state.expandedId = created.id;
      state.tab = 'roster';
      await load();
    } catch (err) {
      U.toast(err.message, 'error');
    } finally {
      if (btn) btn.disabled = false;
      if (typeof window.hideWaitOverlay === 'function') window.hideWaitOverlay();
    }
  }

  async function saveTeachingGrades() {
    var val = (($('tdTeachingGrades') || {}).value || '').trim();
    var grades = val.split(',').map(function (g) { return g.trim(); }).filter(Boolean);
    try {
      await lmsApi('/api/lms/teachers/me/grades', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ grades: grades })
      });
      U.toast('Teaching grades saved');
      var profile = await lmsApi('/api/lms/users/me/grade-profile').catch(function () { return {}; });
      state.teachingGrades = profile.teaching_grades || [];
      state.teachingGradeLabels = profile.teaching_grade_labels || [];
      fillGradeControls();
    } catch (err) {
      U.toast(err.message, 'error');
    }
  }

  // Diagnostic retake requests (GET /api/lms/retake-requests, POST /retake-requests/:id/approve|deny).
  async function loadRetakeRequests() {
    var card = $('tdRetakeCard'), list = $('tdRetakeList');
    if (!card || !list) return;
    var rows = [];
    try { rows = (await lmsApi('/api/lms/retake-requests')) || []; } catch (err) { rows = []; }
    card.hidden = !rows.length;
    list.innerHTML = rows.map(function (r) {
      return '<div class="td-retake-row" data-retake-id="' + r.id + '">' +
        '<div><b>' + U.esc(r.student_name || 'Student') + '</b>' +
        '<span class="td-help">' + U.esc(r.assessment_title || 'Diagnostic') + (r.student_grade ? ' · Grade ' + U.esc(r.student_grade) : '') + '</span></div>' +
        '<div class="td-retake-actions">' +
        '<button type="button" class="td-btn td-btn-outline td-btn-sm" onclick="tdClasses.decideRetake(' + r.id + ', false)">Deny</button>' +
        '<button type="button" class="td-btn td-btn-primary td-btn-sm" onclick="tdClasses.decideRetake(' + r.id + ', true)">Approve retake</button>' +
        '</div></div>';
    }).join('');
  }

  async function decideRetake(id, approve) {
    try {
      await lmsApi('/api/lms/retake-requests/' + id + '/' + (approve ? 'approve' : 'deny'), { method: 'POST' });
      U.toast(approve ? 'Retake approved' : 'Retake request denied');
    } catch (err) {
      U.toast(err.message, 'error');
    }
    loadRetakeRequests();
  }

  window.tdClasses = {
    decideRetake: decideRetake,
    load: load, render: render, toggle: toggle, setTab: setTab,
    addStudents: addStudents, removeStudent: removeStudent, openPanel: openPanel,
    openCreate: openCreate, closeCreate: closeCreate, submitCreate: submitCreate,
    saveTeachingGrades: saveTeachingGrades,
    _state: state
  };
})();
