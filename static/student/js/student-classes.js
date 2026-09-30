/* 05 My Classes. Classes: GET /api/lms/classes/mine. Lessons per class: GET /api/lessons/browse_lessons?class_id
   (+ topic filter, as the old My Lessons list). Quizzes per class: GET /api/lms/students/me/assignments (class_id);
   Review expands GET /api/lms/attempts/:id/results. Quiz taking: student-quiz.js. */
(function () {
  var PER_PAGE = 50;
  var AV = ['purple', 'blue', 'red', '', 'grey'];
  var BAR = ['purple', '', '', 'green', 'grey'];
  var state = {
    classes: null, assignments: [], error: '',
    open: {}, collapsed: {}, lessons: {}, review: {}, results: {},
    topics: null, topicSlug: '', firstOpenDone: false
  };

  function q() { return ((document.getElementById('sdClassSearch') || {}).value || '').trim().toLowerCase(); }

  function loadLessons(classId, page) {
    var entry = state.lessons[classId] || { items: [], page: 0, totalPages: 1 };
    entry.loading = true;
    state.lessons[classId] = entry;
    var params = new URLSearchParams({ class_id: String(classId), page: String(page || 1), per_page: String(PER_PAGE) });
    if (state.topicSlug) params.set('topic_slug', state.topicSlug);
    return fetch('/api/lessons/browse_lessons?' + params.toString(), { credentials: 'include' })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (x) {
        if (!x.ok || !x.d || x.d.success !== true) throw new Error((x.d && (x.d.error || x.d.message)) || 'Failed to load lessons');
        entry.items = (page > 1 ? entry.items : []).concat(x.d.lessons || []);
        entry.page = x.d.page || page || 1;
        entry.totalPages = Math.max(1, Number(x.d.total_pages || 1));
        entry.total = x.d.total != null ? x.d.total : entry.items.length;
        entry.error = '';
      })
      .catch(function (e) { entry.error = e.message || 'Failed to load lessons'; })
      .finally(function () { entry.loading = false; window.sdRenderClasses(); });
  }

  function lessonRowsHtml(c) {
    var entry = state.lessons[c.id];
    if (!entry || (entry.loading && !entry.items.length)) return '<div class="sd-spinner"></div>';
    if (entry.error) return '<p class="sd-empty">' + escapeHtml(entry.error) + '</p>';
    var term = q();
    var items = entry.items.filter(function (l) { return !term || String(l.title || '').toLowerCase().indexOf(term) !== -1 || classMatches(c, term); });
    if (!items.length) return '<p class="sd-empty">' + (state.topicSlug ? 'No lessons for this topic yet.' : 'No lessons published for this class yet.') + '</p>';
    var html = items.map(function (l, i) {
      var id = Number(l.id);
      var title = l.title || 'Untitled Lesson';
      var topics = (l.topics || []).map(function (t) { return t.name; }).filter(Boolean);
      return '<div class="sd-lesson-row" data-lesson-id="' + id + '"><div class="sd-lesson-num">' + (i + 1) + '</div>' +
        '<div class="sd-lname">' + escapeHtml(title) + (topics.length ? '<div class="sd-hint-line" style="margin:2px 0 0;">' + escapeHtml(topics.join(', ')) + '</div>' : '') + '</div>' +
        '<div class="sd-lesson-actions">' +
        '<button type="button" class="sd-mini-btn play" data-title="' + escapeHtml(title) + '" onclick="viewLesson(' + id + ', {title: this.dataset.title})"><i class="fas fa-play-circle"></i> View</button>' +
        '<button type="button" class="sd-mini-btn word" onclick="downloadLessonDocx(' + id + ')"><i class="far fa-file-word"></i> Word</button>' +
        '<button type="button" class="sd-mini-btn ppt" onclick="downloadLessonPPT(' + id + ')"><i class="far fa-file-powerpoint"></i> PowerPoint</button>' +
        '<button type="button" class="sd-mini-btn chat" data-title="' + escapeHtml(title) + '" onclick="chatWithLesson(' + id + ', this.dataset.title)"><i class="far fa-comment"></i> Chat</button>' +
        '</div></div>';
    }).join('');
    if (entry.page < entry.totalPages) {
      html += '<div style="text-align:center;padding-top:10px;"><button type="button" class="sd-btn sd-btn-outline sd-btn-sm"' + (entry.loading ? ' disabled' : '') +
        ' onclick="sdMoreLessons(' + c.id + ')">' + (entry.loading ? 'Loading…' : 'Load more lessons') + '</button></div>';
    }
    return html;
  }

  function quizzesFor(c) { return state.assignments.filter(function (a) { return String(a.class_id) === String(c.id); }); }

  function quizRowsHtml(c) {
    var term = q();
    var items = quizzesFor(c).filter(function (a) { return !term || String(a.title || '').toLowerCase().indexOf(term) !== -1 || classMatches(c, term); });
    if (!items.length) return '<p class="sd-empty">No quizzes assigned in this class yet.</p>';
    return items.map(function (a, i) {
      var st = a.status || 'not_started';
      var done = st === 'submitted' || a.can_start === false;
      var pill = done ? '<span class="sd-pill green round">Completed</span>'
        : (st === 'in_progress' ? '<span class="sd-pill amber round">In progress</span>' : '<span class="sd-pill grey round">Not Attempted</span>');
      var pct = a.score_percent != null ? Math.round(a.score_percent) : null;
      var score = '<span class="sd-score-text">Score: ' + (pct != null ? '<b class="' + window.sdPctClass(pct) + '">' + pct + '%</b>' : '-') + '</span>';
      var open = !!state.review[a.assignment_id];
      var btn;
      if (done) {
        btn = a.attempt_id
          ? '<button type="button" class="sd-btn sd-btn-outline blue sd-btn-sm" aria-expanded="' + open + '" onclick="sdToggleQuizReview(' + a.assignment_id + ')"><i class="fas fa-chart-bar"></i> Review <i class="fas fa-chevron-' + (open ? 'up' : 'down') + '"></i></button>'
          : '';
      } else {
        btn = '<button type="button" class="sd-btn sd-btn-primary sd-btn-sm" data-title="' + escapeHtml(a.title || 'Quiz') + '" onclick="startLmsQuiz(' + a.quiz_id + ',' + a.assignment_id + ', this.dataset.title)">' +
          '<i class="fas fa-play"></i> ' + (st === 'in_progress' ? 'Continue' : 'Start Quiz') + '</button>';
      }
      var review = '';
      if (open && a.attempt_id) {
        var r = state.results[a.attempt_id];
        review = '<div class="sd-quiz-result"><div class="sd-result-banner"><span class="sd-title">' + escapeHtml(a.title || 'Quiz') + ' - Result</span>' +
          '<button type="button" class="sd-close-x" aria-label="Close" onclick="sdToggleQuizReview(' + a.assignment_id + ')"><i class="fas fa-times"></i></button></div>' +
          (r === undefined ? '<div class="sd-spinner"></div>' : (r.__error ? '<p class="sd-empty">' + escapeHtml(r.__error) + '</p>' : reviewBody(r, a))) + '</div>';
      }
      return '<div class="sd-quiz-row"><div class="sd-quiz-row-top"><div class="sd-lesson-num">' + (i + 1) + '</div>' +
        '<div class="sd-qname"><b>' + escapeHtml(a.title || 'Quiz') + '</b><span><i class="far fa-calendar"></i> Due: ' + (a.due_date ? escapeHtml(window.sdFmtDay(a.due_date)) : '—') + '</span></div>' +
        pill + score + btn + '</div>' + review + '</div>';
    }).join('');
  }

  function reviewBody(r, a) {
    var pct = r.score_percent != null ? Math.round(r.score_percent) : 0;
    var correct = r.score != null ? Math.round(r.score) : null;
    var max = r.max_score != null ? Math.round(r.max_score) : null;
    var answered = r.answered_count != null ? r.answered_count : max;
    var incorrect = (correct != null && answered != null) ? Math.max(0, answered - correct) : null;
    var color = pct >= 70 ? '#10b981' : (pct >= 40 ? '#d97706' : '#dc2626');
    return '<div class="sd-quiz-result-body">' +
      '<div class="sd-ring" style="--sd-ring:' + Math.max(0, Math.min(100, pct)) + ';--sd-ring-color:' + color + ';"><div><b>' + pct + '%</b>' +
      (correct != null && max != null ? '<span>' + correct + '/' + max + ' correct</span>' : '') + '</div></div>' +
      '<div class="sd-stat-lines">' +
      '<div><i class="fas fa-check-circle ok"></i><div>Correct Answers<b>' + (correct != null ? correct : '—') + '</b></div></div>' +
      '<div><i class="fas fa-times-circle bad"></i><div>Incorrect Answers<b>' + (incorrect != null ? incorrect : '—') + '</b></div></div>' +
      (r.unanswered_count > 0 ? '<div><i class="far fa-circle"></i><div>Unanswered<b>' + r.unanswered_count + '</b></div></div>' : '') +
      '</div>' +
      '<div class="sd-stat-lines"><div style="justify-content:flex-start;"><button type="button" class="sd-btn sd-btn-outline blue sd-btn-sm" onclick="viewLmsAttemptResult(' + a.attempt_id + ')"><i class="fas fa-chart-bar"></i> View Detailed Report</button></div>' +
      '<div><i class="far fa-calendar"></i><div>Attempted On<b>' + escapeHtml(window.sdFmtDateTime(r.submitted_at || a.submitted_at)) + '</b></div></div></div>' +
      '</div>';
  }

  function classMatches(c, term) {
    if (!term) return true;
    return [c.name, c.grade_level, c.teacher_name, c.description].some(function (v) { return String(v || '').toLowerCase().indexOf(term) !== -1; });
  }
  function classVisible(c, term) {
    if (classMatches(c, term)) return true;
    var entry = state.lessons[c.id];
    if (entry && entry.items.some(function (l) { return String(l.title || '').toLowerCase().indexOf(term) !== -1; })) return true;
    return quizzesFor(c).some(function (a) { return String(a.title || '').toLowerCase().indexOf(term) !== -1; });
  }

  window.sdRenderClasses = function () {
    var box = document.getElementById('sdClassList');
    if (!box) return;
    if (state.classes === null) { box.innerHTML = state.error ? '<p class="sd-empty">' + escapeHtml(state.error) + '</p>' : '<div class="sd-spinner"></div>'; return; }
    if (!state.classes.length) {
      box.innerHTML = '<div class="sd-empty"><b>You have not joined any classes yet</b>Ask your teacher for a class join code.' +
        '<br><button type="button" class="sd-btn sd-btn-primary" onclick="openLmsJoinClassModal()"><i class="fas fa-plus"></i> Join Class</button></div>';
      return;
    }
    var term = q();
    var shown = state.classes.filter(function (c) { return classVisible(c, term); });
    if (!shown.length) { box.innerHTML = '<div class="sd-empty"><b>No classes match your search.</b></div>'; return; }
    box.innerHTML = shown.map(function (c) {
      var i = state.classes.indexOf(c);
      var open = !!state.open[c.id];
      var nL = state.lessons[c.id] ? (state.lessons[c.id].total != null ? state.lessons[c.id].total : state.lessons[c.id].items.length) : null;
      var nQ = quizzesFor(c).length;
      var meta = [];
      if (c.teacher_name) meta.push('Teacher: ' + escapeHtml(c.teacher_name));
      if (c.grade_level) meta.push('Grade: ' + escapeHtml(c.grade_level));
      if (c.joined_at) meta.push('Joined on: ' + escapeHtml(window.sdFmtDate(c.joined_at)));
      return '<div class="sd-class-card' + (open ? ' open' : '') + '" data-class-id="' + c.id + '">' +
        '<div class="sd-num-bar ' + BAR[i % BAR.length] + '"></div><div class="sd-class-main">' +
        '<div class="sd-class-header" role="button" tabindex="0" aria-expanded="' + open + '" onclick="sdToggleClass(' + c.id + ')" onkeydown="if(event.key===\'Enter\')sdToggleClass(' + c.id + ')">' +
        '<div class="sd-title-chip"><div class="sd-avatar-sm ' + AV[i % AV.length] + '"><i class="fas fa-book-open"></i></div>' +
        '<div><h3>' + escapeHtml(c.name) + ' <span class="sd-pill green round">Active</span></h3>' +
        '<div class="sd-class-meta">' + meta.map(function (m) { return '<span>' + m + '</span>'; }).join('') + '</div></div></div>' +
        '<span class="sd-chevron"><i class="fas fa-chevron-down"></i></span></div>' +
        '<div class="sd-class-body">' +
        '<div class="sd-panel' + (state.collapsed[c.id + ':l'] ? ' collapsed' : '') + '"><div class="sd-panel-head" onclick="sdTogglePanel(' + c.id + ',\'l\')"><i class="fas fa-book-open"></i> Lessons' +
        (nL != null ? ' (' + nL + ')' : '') + '<span class="sd-chevron"><i class="fas fa-chevron-up"></i></span></div>' +
        '<div class="sd-panel-list">' + (open ? lessonRowsHtml(c) : '') + '</div></div>' +
        '<div class="sd-panel' + (state.collapsed[c.id + ':q'] ? ' collapsed' : '') + '"><div class="sd-panel-head" onclick="sdTogglePanel(' + c.id + ',\'q\')"><i class="far fa-file-alt"></i> Quizzes (' + nQ + ')' +
        '<span class="sd-chevron"><i class="fas fa-chevron-up"></i></span></div>' +
        '<div class="sd-panel-list">' + (open ? quizRowsHtml(c) : '') + '</div></div>' +
        '</div></div></div>';
    }).join('');
  };

  window.sdToggleClass = function (id) {
    state.open[id] = !state.open[id];
    if (state.open[id] && !state.lessons[id]) loadLessons(id, 1);
    window.sdRenderClasses();
  };
  window.sdTogglePanel = function (id, which) {
    state.collapsed[id + ':' + which] = !state.collapsed[id + ':' + which];
    window.sdRenderClasses();
  };
  window.sdMoreLessons = function (id) {
    var e = state.lessons[id];
    if (e && !e.loading) loadLessons(id, (e.page || 1) + 1);
  };
  window.sdToggleQuizReview = async function (assignmentId) {
    state.review[assignmentId] = !state.review[assignmentId];
    window.sdRenderClasses();
    var a = state.assignments.find(function (x) { return x.assignment_id === assignmentId; });
    if (state.review[assignmentId] && a && a.attempt_id && state.results[a.attempt_id] === undefined) {
      try { state.results[a.attempt_id] = await lmsApi('/api/lms/attempts/' + a.attempt_id + '/results'); }
      catch (e) { state.results[a.attempt_id] = { __error: e.message || 'Could not load result' }; }
      window.sdRenderClasses();
    }
  };

  window.sdOnLessonTopicChange = function (slug) {
    state.topicSlug = slug || '';
    state.lessons = {};
    Object.keys(state.open).forEach(function (id) { if (state.open[id]) loadLessons(Number(id), 1); });
    window.sdRenderClasses();
  };

  function loadTopics() {
    if (state.topics) return;
    lmsApi('/api/lms/topics?subject=Math').then(function (t) {
      state.topics = Array.isArray(t) ? t : [];
      var sel = document.getElementById('lmsTopicFilter');
      if (!sel) return;
      sel.innerHTML = '<option value="">All topics</option>' + state.topics.map(function (x) {
        return '<option value="' + escapeHtml(x.slug) + '">' + escapeHtml(x.name) + '</option>';
      }).join('');
      sel.value = state.topicSlug;
    }).catch(function () { state.topics = []; });
  }

  async function load() {
    loadTopics();
    try {
      var res = await Promise.all([
        lmsApi('/api/lms/classes/mine'),
        lmsApi('/api/lms/students/me/assignments').catch(function () { return []; })
      ]);
      state.classes = Array.isArray(res[0]) ? res[0] : [];
      state.assignments = Array.isArray(res[1]) ? res[1] : [];
      state.error = '';
      state.results = {};
      if (!state.firstOpenDone && state.classes.length) {
        state.firstOpenDone = true;
        state.open[state.classes[0].id] = true;
      }
      state.lessons = {};
      Object.keys(state.open).forEach(function (id) { if (state.open[id]) loadLessons(Number(id), 1); });
    } catch (e) {
      state.classes = null;
      state.error = e.message || 'Could not load your classes.';
    }
    window.sdRenderClasses();
  }

  window.sdReloadClasses = load;
  window.sdViews = window.sdViews || {};
  window.sdViews.classes = { onShow: load };
})();
