/* Teacher dashboard shell: view switching (#lessons | #classes | #quizzes | #analytics | #tutor),
   avatar menu, inline Create Lesson card glue and AI Tutor mode toggle.
   Loaded before teacher-core.js; every function here is looked up lazily at call time. */
(function () {
  var VIEWS = ['lessons', 'classes', 'quizzes', 'analytics', 'tutor'];
  var currentView = null;

  function viewFromHash() {
    var h = (window.location.hash || '').replace('#', '');
    return VIEWS.indexOf(h) >= 0 ? h : null;
  }

  // Show a view without reloading its data (callers such as showMyLessonsPage() load their own data).
  window.tdShowView = function (view) {
    if (VIEWS.indexOf(view) < 0) view = 'lessons';
    var leavingTutor = currentView === 'tutor' && view !== 'tutor';
    currentView = view;
    document.querySelectorAll('.td-view').forEach(function (el) {
      el.classList.toggle('active', el.getAttribute('data-view') === view);
    });
    document.querySelectorAll('.td-navbtn').forEach(function (el) {
      var on = el.getAttribute('data-view') === view;
      el.classList.toggle('active', on);
      if (on) el.setAttribute('aria-current', 'page'); else el.removeAttribute('aria-current');
    });
    if (viewFromHash() !== view) {
      try { history.replaceState(null, '', '#' + view); } catch (e) { window.location.hash = view; }
    }
    if (leavingTutor && typeof window.stopTextToSpeechSession === 'function') {
      try { window.stopTextToSpeechSession(); } catch (e) { /* ignore */ }
    }
    if (typeof window.closeAllDropdowns === 'function') window.closeAllDropdowns();
    tdCloseAvatarMenu();
    if (view === 'classes' && window.tdClasses) window.tdClasses.load();
    if (view === 'quizzes' && window.tdQuizzes) window.tdQuizzes.load();
    if (view === 'analytics' && window.tdAnalytics) window.tdAnalytics.open();
    if (view === 'tutor') {
      setTimeout(function () {
        if (typeof window.scrollToBottom === 'function') window.scrollToBottom();
      }, 0);
    }
    window.scrollTo(0, 0);
  };

  window.tdCurrentView = function () { return currentView; };

  // Nav entry point: Lessons re-fetches its list (same as the legacy "My Lessons" tab).
  window.tdNavigate = function (view) {
    if (view === 'lessons' && typeof window.showMyLessonsPage === 'function') return window.showMyLessonsPage();
    return window.tdShowView(view);
  };

  window.addEventListener('hashchange', function () {
    var v = viewFromHash();
    if (v && v !== currentView) window.tdNavigate(v);
  });

  document.addEventListener('DOMContentLoaded', function () {
    // teacher-core.js opens My Lessons on load; afterwards honour a deep link such as /teacher-dashboard#analytics.
    var target = viewFromHash();
    setTimeout(function () {
      if (target && target !== 'lessons') window.tdShowView(target);
      else if (!currentView) window.tdShowView('lessons');
    }, 0);
    document.querySelectorAll('.td-navbtn').forEach(function (a) {
      a.onclick = function (e) { e.preventDefault(); window.tdNavigate(a.getAttribute('data-view')); };
    });
    document.addEventListener('click', function (e) {
      if (!e.target.closest('#tdAvatarMenu') && !e.target.closest('#userAvatar')) tdCloseAvatarMenu();
    });
    tdWireCreateLessonUpload();
  });

  /* ── Avatar menu ── */
  window.tdToggleAvatarMenu = function (e) {
    if (e) e.stopPropagation();
    var menu = document.getElementById('tdAvatarMenu');
    var btn = document.getElementById('userAvatar');
    if (!menu) return;
    menu.hidden = !menu.hidden;
    if (btn) btn.setAttribute('aria-expanded', String(!menu.hidden));
  };
  function tdCloseAvatarMenu() {
    var menu = document.getElementById('tdAvatarMenu');
    if (menu) menu.hidden = true;
    var btn = document.getElementById('userAvatar');
    if (btn) btn.setAttribute('aria-expanded', 'false');
  }
  window.tdCloseAvatarMenu = tdCloseAvatarMenu;

  window.tdFocusTeachingGrades = function () {
    if (window.tdClasses) window.tdClasses.openCreate();
    setTimeout(function () {
      var el = document.getElementById('tdTeachingGrades');
      if (el) { el.scrollIntoView({ behavior: 'smooth', block: 'center' }); el.focus(); }
    }, 50);
  };

  /* ── Inline Create Lesson card (step 1). Step 2 runs in the processing modal from teacher-core.js. ── */
  var uploadWired = false;
  function tdWireCreateLessonUpload() {
    if (uploadWired || !document.getElementById('dropZone') || typeof window.setupFileUpload !== 'function') return;
    uploadWired = true;
    window.setupFileUpload();
    var dz = document.getElementById('dropZone');
    dz.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); document.getElementById('pdfFileInput').click(); }
    });
    // Keep the design's blue focus ring instead of the legacy dropzone highlight.
    dz.addEventListener('dragover', function () { dz.classList.add('dragover'); });
    dz.addEventListener('dragleave', function () { dz.classList.remove('dragover'); });
    dz.addEventListener('drop', function () { dz.classList.remove('dragover'); });
  }

  window.tdOpenCreateLesson = function () {
    if (currentView !== 'lessons') {
      if (typeof window.showMyLessonsPage === 'function') window.showMyLessonsPage();
      else window.tdShowView('lessons');
    }
    var card = document.getElementById('tdCreateLessonCard');
    if (!card) return;
    card.hidden = false;
    tdWireCreateLessonUpload();
    setTimeout(function () {
      card.scrollIntoView({ behavior: 'smooth', block: 'start' });
      var t = document.getElementById('lessonTitle');
      if (t) t.focus({ preventScroll: true });
    }, 30);
  };

  window.tdResetCreateLessonForm = function () {
    ['lessonTitle', 'lessonContext'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.value = '';
    });
    var subj = document.getElementById('lessonSubject');
    if (subj) subj.value = 'General';
    var grade = document.getElementById('lessonGrade');
    if (grade) grade.value = '';
    var count = document.getElementById('lessonContextCount');
    if (count) count.textContent = '0';
    var gen = document.querySelector('input[name="lessonOutputMode"][value="generate"]');
    if (gen) gen.checked = true;
    if (typeof window.clearFileSelection === 'function') window.clearFileSelection();
    if (typeof window.clearLessonTitleFormError === 'function') window.clearLessonTitleFormError();
    if (typeof window.clearLessonGradeFormError === 'function') window.clearLessonGradeFormError();
  };

  window.tdCloseCreateLesson = function () {
    window.tdResetCreateLessonForm();
    var card = document.getElementById('tdCreateLessonCard');
    if (card) card.hidden = true;
  };

  // Step 2 (upload + ingest progress) re-renders #createLessonModalContent; create that shell when step 1 was inline.
  window.tdEnsureCreateLessonProcessingModal = function () {
    if (document.getElementById('createLessonModalContent')) return;
    document.body.insertAdjacentHTML('beforeend',
      '<div id="createLessonModal" class="fixed inset-0 bg-black bg-opacity-50 flex items-start md:items-center justify-center z-[9999] overflow-y-auto py-2 md:py-4">' +
      '<div id="createLessonModalContent" class="bg-white rounded-2xl md:rounded-3xl w-full max-w-5xl mx-2 md:mx-4 my-auto shadow-2xl overflow-hidden"></div></div>');
    document.body.style.overflow = 'hidden';
  };

  var searchTimer = null;
  window.tdDebouncedLessonSearch = function (value) {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () {
      if (typeof window.filterLessons === 'function') window.filterLessons(value);
    }, 300);
  };

  /* ── View Lesson modal: section tabs + quick AI instructions ── */
  window.tdVlTab = function (name) {
    document.querySelectorAll('#viewLessonModal .td-vl-tab').forEach(function (b) {
      b.classList.toggle('active', b.getAttribute('data-vl-tab') === name);
    });
    var body = document.querySelector('#viewLessonModal .td-vl-body');
    if (name === 'lesson') {
      if (body) body.scrollTo({ top: 0, behavior: 'smooth' });
    } else if (name === 'improve') {
      var side = document.getElementById('tdVlImprove');
      if (side) side.scrollIntoView({ behavior: 'smooth', block: 'start' });
      var prompt = document.getElementById('viewLessonPrompt');
      if (prompt) setTimeout(function () { prompt.focus({ preventScroll: true }); }, 250);
    } else if (name === 'summaries') {
      var sum = document.getElementById('viewLessonSummaryTimeline');
      if (sum) sum.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  };

  window.tdVlAddInstruction = function (text) {
    var prompt = document.getElementById('viewLessonPrompt');
    if (!prompt) return;
    var cur = prompt.value.trim();
    if (cur.indexOf(text) === -1) prompt.value = cur ? cur + ' ' + text : text;
    prompt.focus();
  };

  // The draft editor is seeded with a grey placeholder <span> as real content, so typed text landed inside it
  // (grey/italic). Clear that placeholder when the teacher starts editing; the CSS :empty placeholder takes over.
  document.addEventListener('focusin', function (e) {
    var el = e.target;
    if (!el || el.id !== 'viewLessonDraft') return;
    var only = el.children.length === 1 && el.firstElementChild;
    if (only && only.matches('span.italic') && el.textContent.trim() === only.textContent.trim() &&
        /^Draft will appear here/.test(only.textContent.trim())) {
      el.innerHTML = '';
    }
  });

  // Every time a lesson opens, start on the "Lesson" tab at the top.
  document.addEventListener('DOMContentLoaded', function () {
    var orig = window.showViewLessonModal;
    if (typeof orig !== 'function' || orig._td) return;
    window.showViewLessonModal = function () {
      var r = orig.apply(this, arguments);
      document.querySelectorAll('#viewLessonModal .td-vl-tab').forEach(function (b) {
        b.classList.toggle('active', b.getAttribute('data-vl-tab') === 'lesson');
      });
      var body = document.querySelector('#viewLessonModal .td-vl-body');
      if (body) body.scrollTop = 0;
      return r;
    };
    window.showViewLessonModal._td = true;
  });

  /* ── Small render helpers shared by teacher-classes / -quizzes / -analytics ── */
  var AVATAR_COLORS = [
    ['#dbeafe', '#1d4ed8'], ['#ede9fe', '#6d28d9'], ['#d1fae5', '#047857'], ['#fce7f3', '#be185d'],
    ['#ffedd5', '#c2410c'], ['#e0f2fe', '#0369a1'], ['#fef3c7', '#b45309'], ['#fee2e2', '#b91c1c']
  ];
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  window.tdUtil = {
    esc: esc,
    studentName: function (s) { return s.username || s.email || ('Student #' + s.student_id); },
    initialAvatar: function (name) {
      name = String(name || '?');
      var h = 0;
      for (var i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
      var c = AVATAR_COLORS[h % AVATAR_COLORS.length];
      return '<span class="td-initial" style="background:' + c[0] + ';color:' + c[1] + ';">' + esc(name.charAt(0).toUpperCase()) + '</span>';
    },
    studentCell: function (name) {
      return '<div class="td-student-cell">' + window.tdUtil.initialAvatar(name) + '<span>' + esc(name) + '</span></div>';
    },
    pct: function (v) {
      if (v == null || v === '' || isNaN(Number(v))) return null;
      return Math.round(Number(v));
    },
    progress: function (v, tone) {
      var p = window.tdUtil.pct(v);
      if (p == null) return '<span style="color:var(--td-muted);">—</span>';
      var cls = tone || (p < 40 ? 'red' : (p < 60 ? 'amber' : ''));
      return '<div class="td-progress"><b>' + p + '%</b><span class="td-progress-track"><span class="td-progress-fill ' + cls + '" style="width:' + Math.max(0, Math.min(100, p)) + '%;"></span></span></div>';
    },
    // Score band used across analytics (same thresholds as the legacy quiz-results colouring).
    scoreBadge: function (p) {
      if (p == null) return '<span class="td-status-badge td-status-muted">—</span>';
      if (p >= 70) return '<span class="td-status-badge td-status-good">Good</span>';
      if (p >= 50) return '<span class="td-status-badge td-status-avg">Average</span>';
      return '<span class="td-status-badge td-status-help">Needs Help</span>';
    },
    gradeLabel: function (g) {
      if (g == null || g === '') return 'All grades';
      var n = parseInt(g, 10);
      if (isNaN(n)) return String(g);
      var suf = (n % 100 >= 11 && n % 100 <= 13) ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] || 'th');
      return n + suf + ' Grade';
    },
    empty: function (title, body) {
      return '<h3>' + esc(title) + '</h3>' + (body ? '<p>' + esc(body) + '</p>' : '');
    },
    toast: function (msg, type) {
      if (typeof window.lmsShowToast === 'function') return window.lmsShowToast(msg, type);
      if (typeof window.showToast === 'function') return window.showToast(msg, type === 'error' ? 'error' : 'success');
    }
  };

  /* ── AI Tutor: Lesson Chat (RAG) ↔ AI Tutor (LMS tutor) ── */
  var tutorMode = 'chat';
  window.tdSetTutorMode = function (mode) {
    tutorMode = mode === 'assistant' ? 'assistant' : 'chat';
    document.querySelectorAll('[data-tutor-mode]').forEach(function (b) {
      var on = b.getAttribute('data-tutor-mode') === tutorMode;
      b.classList.toggle('active', on);
      b.setAttribute('aria-selected', String(on));
    });
    var chatPane = document.getElementById('tdTutorChatPane');
    var assistantPane = document.getElementById('lmsTutorModal');
    var tools = document.querySelector('[data-tutor-tools="chat"]');
    if (chatPane) chatPane.hidden = tutorMode !== 'chat';
    if (tools) tools.style.visibility = tutorMode === 'chat' ? '' : 'hidden';
    if (tutorMode === 'assistant') {
      if (typeof window.stopTextToSpeechSession === 'function') { try { window.stopTextToSpeechSession(); } catch (e) { /* ignore */ } }
      if (typeof window.openLmsTutorPanel === 'function') window.openLmsTutorPanel('teacher');
      if (assistantPane) {
        // lmsOpenModal() treats this element as a modal (centres it and locks body scroll); undo that for the inline pane.
        assistantPane.hidden = false;
        assistantPane.style.display = '';
        assistantPane.style.alignItems = '';
        assistantPane.style.justifyContent = '';
        document.body.style.overflow = '';
      }
    } else if (assistantPane) {
      assistantPane.hidden = true;
      assistantPane.style.display = 'none';
      setTimeout(function () {
        if (typeof window.scrollToBottom === 'function') window.scrollToBottom();
      }, 0);
    }
  };
})();
