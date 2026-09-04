/** Interactive LMS panels — tutor, analytics, practice (green theme) */
(function () {
  function fmtText(text, inline, quiz) {
    if (typeof window.lmsFormatRichText === 'function') {
      var opts = {};
      if (inline) opts.inline = true;
      if (quiz) opts.quiz = true;
      return window.lmsFormatRichText(text, opts);
    }
    return escapeHtml(text == null ? '' : String(text));
  }
  function fmtOption(opt) {
    var raw = (typeof window.lmsOptionText === 'function')
      ? window.lmsOptionText(opt)
      : ((opt && (opt.text || opt.latex || opt.label)) || '');
    return fmtText(raw, true, true);
  }
  function fmtQuestion(q) {
    var raw = (typeof window.lmsQuestionText === 'function')
      ? window.lmsQuestionText(q)
      : ((q && (q.question_text || q.question_latex)) || '');
    return fmtText(raw, false, true);
  }
  function typeset(el) {
    if (!el) return Promise.resolve();
    if (typeof window.lmsTypesetMath === 'function') return window.lmsTypesetMath(el);
    el.classList.add('tex2jax_process');
    if (window.MathJax && window.MathJax.typesetPromise) {
      return window.MathJax.typesetPromise([el]).catch(function () {});
    }
    return Promise.resolve();
  }

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
  var tutorLoading = false;

  function renderTutorChat() {
    var body = document.getElementById('lmsTutorModalBody');
    var msgs = tutorHistory.map(function (m) {
      var bubble = m.role === 'user'
        ? escapeHtml(m.text)
        : fmtText(m.text || '');
      return '<div class="lms-chat-msg ' + m.role + '">' +
        '<div class="lms-chat-avatar">' + (m.role === 'user' ? 'You' : 'AI') + '</div>' +
        '<div class="lms-chat-bubble">' + bubble + '</div></div>';
    }).join('');
    var emptyHint = tutorLoading
      ? '<div class="lms-spinner" style="margin:20px auto"></div>'
      : '<p class="lms-status">Ask me anything — I\'m your general IqbalAI tutor for any subject.</p>';
    var clearBtn = tutorHistory.length
      ? '<button type="button" class="lms-btn lms-btn-ghost" style="margin-bottom:8px;font-size:.8rem;" onclick="clearLmsTutorHistory()">Clear chat history</button>'
      : '';
    body.innerHTML =
      clearBtn +
      '<div class="lms-chat-messages" id="lmsTutorMessages">' + (msgs || emptyHint) + '</div>' +
      '<div class="lms-chat-input-row">' +
      '<textarea id="lmsTutorInput" class="lms-textarea" rows="2" placeholder="Type your question..."' +
      (tutorLoading ? ' disabled' : '') + '></textarea>' +
      '<button type="button" class="lms-btn lms-btn-primary" onclick="sendLmsTutorMessage()"' +
      (tutorLoading ? ' disabled' : '') + '>Send</button></div>';
    var ta = document.getElementById('lmsTutorInput');
    if (ta && !tutorLoading) {
      ta.focus();
      ta.onkeydown = function (e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendLmsTutorMessage(); } };
    }
    var box = document.getElementById('lmsTutorMessages');
    if (box) box.scrollTop = box.scrollHeight;
    typeset(document.getElementById('lmsTutorModalBody'));
  }

  async function loadTutorHistory() {
    tutorLoading = true;
    renderTutorChat();
    try {
      var data = await lmsApi('/api/lms/tutor/history?mode=' + encodeURIComponent(tutorRole));
      tutorHistory = (data.messages || []).map(function (m) {
        return { role: m.role === 'user' ? 'user' : 'bot', text: m.text || '' };
      });
      if (tutorHistory.length) {
        lmsShowToast('Restored your tutor conversation', 'success');
      }
    } catch (err) {
      tutorHistory = [];
    } finally {
      tutorLoading = false;
      renderTutorChat();
    }
  }

  function lmsBlockUntilDiagnostic() {
    if (typeof window.lmsStudentNeedsDiagnostic === 'function' && window.lmsStudentNeedsDiagnostic()) {
      if (typeof lmsShowToast === 'function') lmsShowToast('Complete your diagnostic assessment first.', 'error');
      if (typeof openLmsDiagnostic === 'function') openLmsDiagnostic();
      return true;
    }
    return false;
  }

  window.openLmsTutorPanel = function (role) {
    if (lmsBlockUntilDiagnostic()) return;
    tutorRole = role || 'student';
    document.getElementById('lmsTutorModalTitle').textContent = role === 'teacher' ? 'Teaching Assistant' : 'AI Tutor';
    tutorHistory = [];
    lmsOpenModal('lmsTutorModal');
    loadTutorHistory();
  };

  window.clearLmsTutorHistory = async function () {
    if (!confirm('Clear all AI Tutor chat history?')) return;
    try {
      await lmsApi('/api/lms/tutor/history?mode=' + encodeURIComponent(tutorRole), { method: 'DELETE' });
      tutorHistory = [];
      renderTutorChat();
      lmsShowToast('Chat history cleared', 'success');
    } catch (err) {
      lmsShowToast(err.message || 'Could not clear history', 'error');
    }
  };

  window.sendLmsTutorMessage = async function () {
    if (tutorLoading) return;
    var input = document.getElementById('lmsTutorInput');
    var msg = (input && input.value || '').trim();
    if (!msg) return;
    tutorHistory.push({ role: 'user', text: msg });
    tutorLoading = true;
    renderTutorChat();
    try {
      var url = tutorRole === 'teacher' ? '/api/lms/teacher/tutor' : '/api/lms/tutor/chat';
      var data = await lmsApi(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg })
      });
      tutorHistory.push({ role: 'bot', text: data.reply || data.message || 'No response' });
    } catch (err) {
      tutorHistory.push({ role: 'bot', text: 'Error: ' + err.message });
    } finally {
      tutorLoading = false;
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
            ? '<div class="lms-card lms-card-warn" style="margin-top:12px;"><strong>Insight:</strong> ' +
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
          ? '<div style="max-width:280px;margin:0 auto 16px;"><canvas id="lmsRosterPieChart" height="220"></canvas></div>' +
            '<table class="lms-table"><thead><tr><th>Student</th><th>Grade</th><th>Progress</th><th>Status</th></tr></thead><tbody>' +
            roster.map(function (s) {
              return '<tr><td>' + escapeHtml(s.username || s.email || ('#' + s.student_id)) + '</td>' +
                '<td>' + escapeHtml(s.grade_label || '—') + '</td>' +
                '<td>' + (s.overall_progress != null ? Math.round(s.overall_progress) + '%' : '—') + '</td>' +
                '<td>' + (s.is_struggling ? '<span class="lms-badge lms-badge-red">Needs help</span>' : '<span class="lms-badge lms-badge-green">On track</span>') + '</td></tr>';
            }).join('') + '</tbody></table>'
          : '<p class="lms-status">No students enrolled.</p>';
        if (roster.length) renderLmsRosterPieChart(roster);
      }
    } catch (err) {
      content.innerHTML = '<p class="lms-error">' + escapeHtml(err.message) + '</p>';
    }
  };

  var _lmsRosterChart = null;
  function renderLmsRosterPieChart(roster) {
    var canvas = document.getElementById('lmsRosterPieChart');
    if (!canvas || typeof Chart === 'undefined') return;
    if (_lmsRosterChart) { _lmsRosterChart.destroy(); _lmsRosterChart = null; }
    var onTrack = roster.filter(function (s) { return !s.is_struggling; }).length;
    var needsHelp = roster.length - onTrack;
    var labels = [], data = [], colors = [];
    if (onTrack > 0) { labels.push('On track (' + onTrack + ')'); data.push(onTrack); colors.push('#16a34a'); }
    if (needsHelp > 0) { labels.push('Needs help (' + needsHelp + ')'); data.push(needsHelp); colors.push('#dc2626'); }
    _lmsRosterChart = new Chart(canvas, {
      type: 'pie',
      data: { labels: labels, datasets: [{ data: data, backgroundColor: colors }] },
      options: { responsive: true, plugins: { legend: { position: 'bottom' } } },
    });
  }

  /* ── Guided practice ── */
  ensureModal('lmsPracticeModal', 'Guided Practice', 'lms-modal-md');
  var practiceSession = null;

  window.openLmsPracticePanel = async function (topicId) {
    if (lmsBlockUntilDiagnostic()) return;
    lmsOpenModal('lmsPracticeModal');
    var body = document.getElementById('lmsPracticeModalBody');
    body.innerHTML = '<div class="lms-spinner"></div>';
    try {
      practiceSession = await lmsApi('/api/lms/practice/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ topic_id: topicId || null, force_new: false })
      });
      if (practiceSession.resumed) {
        lmsShowToast('Resuming your practice session', 'success');
      }
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
        (fmtOption(o) || escapeHtml(String(i))) + '</button>';
    }).join('');
    body.innerHTML =
      '<p class="lms-badge lms-badge-green">' + escapeHtml(q.difficulty || 'medium') + '</p>' +
      '<h3 class="lms-quiz-stem">' + fmtQuestion(q) + '</h3>' +
      opts +
      '<div id="lmsPracticeFeedback" style="margin-top:12px;"></div>' +
      '<button type="button" class="lms-btn lms-btn-ghost" style="margin-top:10px;" onclick="requestLmsPracticeHint()">Need a hint?</button>';
    typeset(body);
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
          ? '<div class="lms-card lms-card-ok"><strong>Correct!</strong> ' + fmtText(result.feedback || '') + '</div>'
          : '<div class="lms-card lms-card-bad"><strong>Try again.</strong> ' + fmtText(result.feedback || result.hint || '') + '</div>';
        fb.innerHTML = msg;
        typeset(fb);
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
        fb.innerHTML = '<div class="lms-card lms-card-warn"><strong>Hint:</strong> ' + fmtText(hint.hint || hint.message || '') + '</div>';
        typeset(fb);
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
        // Only a submitted attempt has scored results to show - an
        // in-progress/abandoned one has nothing to click through to yet.
        var clickable = a.status === 'submitted';
        var style = 'padding:10px;margin-bottom:6px;' + (clickable ? 'cursor:pointer;' : '');
        var onclick = clickable ? ' onclick="viewLmsAttemptResult(' + a.attempt_id + ')"' : '';
        var label = a.title || (a.assessment_type === 'diagnostic' ? 'Diagnostic Assessment' : 'Quiz #' + a.assessment_id);
        return '<li class="lms-card" style="' + style + '"' + onclick + '>' + escapeHtml(label) +
          ' — <strong>' + (a.score_percent != null ? a.score_percent + '%' : a.status) + '</strong></li>';
      }).join('') + '</ul>';
    } catch (e) { el.innerHTML = ''; }
  };

  /* Attempt result viewer (Quiz History click-through) */
  window.viewLmsAttemptResult = async function (attemptId) {
    var body = document.getElementById('lmsAttemptResultBody');
    if (!body) return;
    body.innerHTML = '<p class="lms-status">Loading...</p>';
    if (typeof lmsOpenModal === 'function') lmsOpenModal('lmsAttemptResultModal');
    try {
      var r = await lmsApi('/api/lms/attempts/' + attemptId + '/results');
      var html = '<h3 style="font-weight:700;margin:0 0 8px;">Score: ' + r.score + ' / ' + r.max_score +
        ' (' + r.score_percent + '%)</h3>';
      if (r.submitted_at) {
        html += '<p class="lms-status" style="margin-bottom:12px;">Submitted ' + new Date(r.submitted_at).toLocaleString() + '</p>';
      }
      if (r.time_over) {
        html += '<p class="lms-status">' + escapeHtml(r.message || 'Time expired before submission.') + '</p>';
      }
      if (r.weak_topics && r.weak_topics.length) {
        html += '<h4 style="font-weight:600;margin:12px 0 6px;">Weak areas</h4><ul>' +
          r.weak_topics.map(function (t) {
            return '<li>' + escapeHtml(t.topic_name || ('Topic #' + t.topic_id)) + ' — ' + Math.round(t.score_percent) + '%</li>';
          }).join('') + '</ul>';
      }
      if (r.strong_topics && r.strong_topics.length) {
        html += '<h4 style="font-weight:600;margin:12px 0 6px;">Strong areas</h4><ul>' +
          r.strong_topics.map(function (t) {
            return '<li>' + escapeHtml(t.topic_name || ('Topic #' + t.topic_id)) + ' — ' + Math.round(t.score_percent) + '%</li>';
          }).join('') + '</ul>';
      }
      body.innerHTML = html;
    } catch (e) {
      body.innerHTML = '<p class="lms-error">' + escapeHtml(e.message || 'Could not load result') + '</p>';
    }
  };
  window.closeLmsAttemptResult = function () {
    if (typeof lmsCloseModal === 'function') lmsCloseModal('lmsAttemptResultModal');
  };
})();
