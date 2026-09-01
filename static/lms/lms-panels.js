/** Interactive LMS panels — tutor, analytics, practice (green theme) */
(function () {
  function ensureModal(id, title, sizeClass) {
    if (document.getElementById(id)) return;
    var html = '<div id="' + id + '" class="lms-modal-backdrop" onclick="if(event.target===this)lmsCloseModal(\'' + id + '\')">' +
      '<div class="lms-modal ' + (sizeClass || 'lms-modal-lg') + '">' +
      '<div class="lms-modal-header"><h2 id="' + id + 'Title">' + escapeHtml(title) + '</h2>' +
      '<button type="button" class="lms-modal-close" onclick="lmsCloseModal(\'' + id + '\')">&times;</button></div>' +
      '<div class="lms-modal-body" id="' + id + 'Body"></div></div></div>';
    document.body.insertAdjacentHTML('beforeend', html);
  }

  /* ── Tutor chat ── */
  ensureModal('lmsTutorModal', 'AI Tutor', 'lms-modal-md');
  var tutorRole = 'student';
  var tutorHistory = [];

  function renderTutorChat() {
    var body = document.getElementById('lmsTutorModalBody');
    var msgs = tutorHistory.map(function (m) {
      var bubble = m.role === 'user'
        ? escapeHtml(m.text)
        : lmsFormatRichText(m.text || '');
      return '<div class="lms-chat-msg ' + m.role + '">' +
        '<div class="lms-chat-avatar">' + (m.role === 'user' ? 'You' : 'AI') + '</div>' +
        '<div class="lms-chat-bubble">' + bubble + '</div></div>';
    }).join('');
    body.innerHTML =
      '<div class="lms-chat-messages" id="lmsTutorMessages">' + (msgs || '<p class="lms-status">Ask a question — I\'ll guide you step-by-step without just giving the answer.</p>') + '</div>' +
      '<div class="lms-chat-input-row">' +
      '<textarea id="lmsTutorInput" class="lms-textarea" rows="2" placeholder="Type your question..."></textarea>' +
      '<button type="button" class="lms-btn lms-btn-primary" onclick="sendLmsTutorMessage()">Send</button></div>';
    var ta = document.getElementById('lmsTutorInput');
    if (ta) {
      ta.focus();
      ta.onkeydown = function (e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendLmsTutorMessage(); } };
    }
    var box = document.getElementById('lmsTutorMessages');
    if (box) box.scrollTop = box.scrollHeight;
    lmsTypesetMath(document.getElementById('lmsTutorModalBody'));
  }

  window.openLmsTutorPanel = function (role) {
    tutorRole = role || 'student';
    document.getElementById('lmsTutorModalTitle').textContent = role === 'teacher' ? 'Teaching Assistant' : 'AI Tutor';
    tutorHistory = [];
    renderTutorChat();
    lmsOpenModal('lmsTutorModal');
  };

  window.sendLmsTutorMessage = async function () {
    var input = document.getElementById('lmsTutorInput');
    var msg = (input && input.value || '').trim();
    if (!msg) return;
    tutorHistory.push({ role: 'user', text: msg });
    renderTutorChat();
    var box = document.getElementById('lmsTutorMessages');
    if (box) box.innerHTML += '<div class="lms-chat-msg bot"><div class="lms-chat-avatar">AI</div><div class="lms-chat-bubble"><div class="lms-spinner" style="width:18px;height:18px;margin:0"></div></div></div>';
    try {
      var url = tutorRole === 'teacher' ? '/api/lms/teacher/tutor' : '/api/lms/tutor/chat';
      var data = await lmsApi(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg })
      });
      tutorHistory.push({ role: 'bot', text: data.reply || data.message || 'No response' });
      renderTutorChat();
    } catch (err) {
      tutorHistory.push({ role: 'bot', text: 'Error: ' + err.message });
      renderTutorChat();
    }
  };

  function renderExpandableBadgeList(count, hint, itemsHtml, badgeClass) {
    if (!count) return '—';
    var badge = badgeClass || 'lms-badge-red';
    return '<details class="lms-expand-details">' +
      '<summary class="lms-expand-summary">' +
      '<span class="lms-badge ' + badge + '">' + count + '</span>' +
      '<span class="lms-expand-hint">' + escapeHtml(hint) + '</span>' +
      '</summary>' +
      '<ul class="lms-expand-list">' + itemsHtml + '</ul></details>';
  }

  function renderWeakTopicsCell(student) {
    var topics = student.weak_topics || [];
    var count = student.weak_topic_count || topics.length || 0;
    if (!count) return '—';
    var list = topics.map(function (t) {
      var score = t.score_percent != null ? ' · ' + Math.round(t.score_percent) + '%' : '';
      return '<li>' + escapeHtml(t.topic_name || ('Topic #' + t.topic_id)) + score + '</li>';
    }).join('');
    return renderExpandableBadgeList(count, 'Click to view topics', list);
  }

  function renderTopicStrugglingCell(topic) {
    var students = topic.weak_students || [];
    var count = topic.weak_student_count || students.length || 0;
    if (!count) return '—';
    var list = students.map(function (s) {
      var score = s.score_percent != null ? ' · ' + Math.round(s.score_percent) + '%' : '';
      return '<li>' + escapeHtml(s.username || ('#' + s.student_id)) + score + '</li>';
    }).join('');
    var hint = count === 1 ? '1 student · click to view' : count + ' students · click to view';
    return renderExpandableBadgeList(count, hint, list);
  }

  function renderQuizStudentScoresCell(quiz) {
    var students = quiz.student_results || [];
    if (!students.length) return '—';
    var submitted = students.filter(function (s) { return s.status === 'submitted'; }).length;
    var list = students.map(function (s) {
      var name = escapeHtml(s.username || ('#' + s.student_id));
      var itemClass = 'lms-expand-item-muted';
      var label = name + ': Not submitted';
      if (s.status === 'submitted' && s.score_percent != null) {
        label = name + ': ' + ((s.score != null && s.max_score != null)
          ? (s.score + '/' + s.max_score + ' · ' + s.score_percent + '%')
          : (s.score_percent + '%'));
        if (s.score_percent >= 70) itemClass = 'lms-expand-item-ok';
        else if (s.score_percent >= 50) itemClass = 'lms-expand-item-warn';
        else itemClass = 'lms-expand-item-bad';
      } else if (s.status === 'in_progress') {
        label = name + ': In progress';
        itemClass = 'lms-expand-item-info';
      } else if (s.status === 'overdue') {
        label = name + ': Overdue';
        itemClass = 'lms-expand-item-bad';
      }
      return '<li class="' + itemClass + '">' + label + '</li>';
    }).join('');
    var hint = submitted + '/' + students.length + ' submitted · click to view';
    return renderExpandableBadgeList(students.length, hint, list, 'lms-badge-blue');
  }

  ensureModal('lmsAnalyticsModal', 'Class Analytics', 'lms-modal-xl');

  window.openLmsTeacherAnalytics = async function (preselectedClassId) {
    ensureModal('lmsAnalyticsModal', 'Class Analytics', 'lms-modal-xl');
    lmsOpenModal('lmsAnalyticsModal');
    var body = document.getElementById('lmsAnalyticsModalBody');
    if (!body) return;
    body.innerHTML = '<div class="lms-spinner"></div>';
    try {
      var classes = await lmsApi('/api/lms/classes/mine');
      if (!classes.length) {
        body.innerHTML = '<p class="lms-status">Create a class first to view analytics.</p>';
        return;
      }
      var opts = classes.map(function (c) {
        var sel = (String(c.id) === String(preselectedClassId)) ? ' selected' : '';
        return '<option value="' + c.id + '"' + sel + '>' + escapeHtml(c.name) + (c.grade_level ? ' (' + c.grade_level + 'th)' : '') + '</option>';
      }).join('');
      body.innerHTML =
        '<div class="lms-field"><label class="lms-label">Select class</label>' +
        '<select id="lmsAnalyticsClassSelect" class="lms-select" onchange="loadLmsAnalyticsData()">' + opts + '</select></div>' +
        '<div class="lms-tabs">' +
        '<button type="button" class="lms-tab active" data-tab="topics" onclick="switchLmsAnalyticsTab(\'topics\')">Topic Performance</button>' +
        '<button type="button" class="lms-tab" data-tab="quizzes" onclick="switchLmsAnalyticsTab(\'quizzes\')">Quiz Results</button>' +
        '<button type="button" class="lms-tab" data-tab="struggling" onclick="switchLmsAnalyticsTab(\'struggling\')">Struggling Students</button>' +
        '<button type="button" class="lms-tab" data-tab="roster" onclick="switchLmsAnalyticsTab(\'roster\')">Roster</button></div>' +
        '<div id="lmsAnalyticsContent"><div class="lms-spinner"></div></div>';
      loadLmsAnalyticsData();
    } catch (err) {
      body.innerHTML = '<p class="lms-error">' + escapeHtml(err.message) + '</p>';
    }
  };

  window.switchLmsAnalyticsTab = function (tab) {
    document.querySelectorAll('#lmsAnalyticsModal .lms-tab').forEach(function (t) {
      t.classList.toggle('active', t.getAttribute('data-tab') === tab);
    });
    window._lmsAnalyticsTab = tab;
    loadLmsAnalyticsData();
  };

  window.loadLmsAnalyticsData = async function () {
    var sel = document.getElementById('lmsAnalyticsClassSelect');
    var content = document.getElementById('lmsAnalyticsContent');
    if (!sel || !content) return;
    var classId = sel.value;
    var tab = window._lmsAnalyticsTab || 'topics';
    content.innerHTML = '<div class="lms-spinner"></div>';
    try {
      if (tab === 'topics') {
        var topics = await lmsApi('/api/lms/classes/' + classId + '/analytics/topics');
        if (!topics.length) { content.innerHTML = '<p class="lms-status">No topic data yet — students need to complete assessments.</p>'; return; }
        content.innerHTML = '<table class="lms-table"><thead><tr><th>Topic</th><th>Avg Score</th><th>Struggling Students</th></tr></thead><tbody>' +
          topics.map(function (t) {
            return '<tr><td>' + escapeHtml(t.topic_name) + '</td><td>' + (t.avg_score != null ? t.avg_score + '%' : '—') + '</td>' +
              '<td class="lms-expand-cell">' + renderTopicStrugglingCell(t) + '</td></tr>';
          }).join('') + '</tbody></table>' +
          (topics.some(function (t) { return (t.weak_student_count || 0) >= 1; })
            ? '<div class="lms-card" style="margin-top:12px;background:#fef9c3;border-color:#fcd34d"><strong>Insight:</strong> ' +
              topics.filter(function (t) { return (t.weak_student_count || 0) >= 1; }).length +
              ' topic(s) have struggling students. Click the count in each row to see names and scores.</div>' : '');
      } else if (tab === 'quizzes') {
        var quizzes = await lmsApi('/api/lms/classes/' + classId + '/analytics/quizzes');
        content.innerHTML = quizzes.length
          ? '<table class="lms-table"><thead><tr><th>Assignment</th><th>Completion</th><th>Avg Score</th><th>Student Scores</th></tr></thead><tbody>' +
            quizzes.map(function (q) {
              return '<tr><td>' + escapeHtml(q.title) + '</td><td>' + (q.completion_percent != null ? q.completion_percent + '%' : '—') + '</td>' +
                '<td>' + (q.avg_score_percent != null ? q.avg_score_percent + '%' : '—') + '</td>' +
                '<td class="lms-expand-cell">' + renderQuizStudentScoresCell(q) + '</td></tr>';
            }).join('') + '</tbody></table>'
          : '<p class="lms-status">No published assignments yet.</p>';
      } else if (tab === 'struggling') {
        var struggling = await lmsApi('/api/lms/classes/' + classId + '/analytics/struggling');
        content.innerHTML = struggling.length
          ? '<table class="lms-table"><thead><tr><th>Student</th><th>Progress</th><th>Weak Topics</th></tr></thead><tbody>' +
            struggling.map(function (s) {
              return '<tr><td>' + escapeHtml(s.username || ('#' + s.student_id)) + '</td>' +
                '<td>' + (s.overall_progress != null ? Math.round(s.overall_progress) + '%' : '—') + '</td>' +
                '<td class="lms-expand-cell">' + renderWeakTopicsCell(s) + '</td></tr>';
            }).join('') + '</tbody></table>'
          : '<p class="lms-status">No struggling students detected — great job!</p>';
      } else if (tab === 'roster') {
        var roster = await lmsApi('/api/lms/classes/' + classId + '/students');
        content.innerHTML = roster.length
          ? '<table class="lms-table"><thead><tr><th>Student</th><th>Grade</th><th>Progress</th><th>Status</th></tr></thead><tbody>' +
            roster.map(function (s) {
              return '<tr><td>' + escapeHtml(s.username || s.email || ('#' + s.student_id)) + '</td>' +
                '<td>' + escapeHtml(s.grade_label || '—') + '</td>' +
                '<td>' + (s.overall_progress != null ? Math.round(s.overall_progress) + '%' : '—') + '</td>' +
                '<td>' + (s.is_struggling ? '<span class="lms-badge lms-badge-red">Needs help</span>' : '<span class="lms-badge lms-badge-green">On track</span>') + '</td></tr>';
            }).join('') + '</tbody></table>'
          : '<p class="lms-status">No students enrolled.</p>';
      }
    } catch (err) {
      content.innerHTML = '<p class="lms-error">' + escapeHtml(err.message) + '</p>';
    }
  };

  /* ── Guided practice ── */
  ensureModal('lmsPracticeModal', 'Guided Practice', 'lms-modal-md');
  var practiceSession = null;

  window.openLmsPracticePanel = async function (topicId) {
    lmsOpenModal('lmsPracticeModal');
    var body = document.getElementById('lmsPracticeModalBody');
    body.innerHTML = '<div class="lms-spinner"></div>';
    try {
      practiceSession = await lmsApi('/api/lms/practice/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ topic_id: topicId || null })
      });
      renderPracticeQuestion();
    } catch (err) {
      body.innerHTML = '<p class="lms-error">' + escapeHtml(err.message) + '</p>';
    }
  };

  window.startLmsPractice = function (topicId) { openLmsPracticePanel(topicId); };

  async function renderPracticeQuestion() {
    var body = document.getElementById('lmsPracticeModalBody');
    if (!practiceSession || !practiceSession.session_id) return;
    var q = await lmsApi('/api/lms/practice/sessions/' + practiceSession.session_id);
    if (q.completed) {
      body.innerHTML = '<div style="text-align:center;padding:20px;"><div style="font-size:2rem;color:var(--primary-color);">&#10003;</div>' +
        '<p><strong>Practice session complete!</strong></p>' +
        '<button type="button" class="lms-btn lms-btn-primary" onclick="lmsCloseModal(\'lmsPracticeModal\')">Done</button></div>';
      return;
    }
    var opts = (q.options || []).map(function (o, i) {
      return '<button type="button" class="lms-quiz-option" onclick="submitLmsPracticeAnswer(' + i + ')">' +
        lmsFormatRichText(lmsOptionText(o) || o.label || String(i), { inline: true }) + '</button>';
    }).join('');
    body.innerHTML =
      '<p class="lms-badge lms-badge-green">' + escapeHtml(q.difficulty || 'medium') + '</p>' +
      '<h3 style="font-size:1rem;margin:12px 0;">' + lmsFormatRichText(lmsQuestionText(q)) + '</h3>' +
      opts +
      '<div id="lmsPracticeFeedback" style="margin-top:12px;"></div>' +
      '<button type="button" class="lms-btn lms-btn-ghost" style="margin-top:10px;" onclick="requestLmsPracticeHint()">Need a hint?</button>';
    lmsTypesetMath(body);
  }

  window.submitLmsPracticeAnswer = async function (optIdx) {
    var fb = document.getElementById('lmsPracticeFeedback');
    if (fb) fb.innerHTML = '<div class="lms-spinner" style="width:20px;height:20px"></div>';
    try {
      var result = await lmsApi('/api/lms/practice/sessions/' + practiceSession.session_id + '/answer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ selected_option_index: optIdx })
      });
      if (fb) {
        var msg = result.correct
          ? '<div class="lms-card" style="background:#f0fdf4;border-color:#86efac"><strong>Correct!</strong> ' + lmsFormatRichText(result.feedback || '') + '</div>'
          : '<div class="lms-card" style="background:#fef2f2;border-color:#fecaca"><strong>Try again.</strong> ' + lmsFormatRichText(result.feedback || result.hint || '') + '</div>';
        fb.innerHTML = msg;
        lmsTypesetMath(fb);
      }
      if (result.correct && result.next_question) {
        setTimeout(renderPracticeQuestion, 1200);
      } else if (result.correct && result.completed) {
        setTimeout(renderPracticeQuestion, 1200);
      }
    } catch (err) {
      if (fb) fb.innerHTML = '<p class="lms-error">' + escapeHtml(err.message) + '</p>';
    }
  };

  window.requestLmsPracticeHint = async function () {
    var fb = document.getElementById('lmsPracticeFeedback');
    try {
      var hint = await lmsApi('/api/lms/practice/sessions/' + practiceSession.session_id + '/hint', { method: 'POST' });
      if (fb) {
        fb.innerHTML = '<div class="lms-card" style="background:#fef9c3;border-color:#fcd34d"><strong>Hint:</strong> ' + lmsFormatRichText(hint.hint || hint.message || '') + '</div>';
        lmsTypesetMath(fb);
      }
    } catch (err) {
      if (fb) fb.innerHTML = '<p class="lms-error">' + escapeHtml(err.message) + '</p>';
    }
  };

  /* Attempt history */
  window.loadLmsAttemptHistory = async function (containerId) {
    var el = document.getElementById(containerId);
    if (!el) return;
    try {
      var items = await lmsApi('/api/lms/students/me/attempts');
      if (!items.length) { el.innerHTML = '<p class="lms-path-empty">No attempts yet.</p>'; return; }
      el.innerHTML = '<ul class="lms-card-list">' + items.map(function (a) {
        return '<li class="lms-card" style="padding:10px;margin-bottom:6px;">Quiz #' + a.assessment_id +
          ' — <strong>' + (a.score_percent != null ? a.score_percent + '%' : a.status) + '</strong></li>';
      }).join('') + '</ul>';
    } catch (e) { el.innerHTML = ''; }
  };
})();
