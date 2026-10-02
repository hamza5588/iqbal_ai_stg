/* Extracted verbatim from the former templates/teacher_dashboard.html (removed after the new-UI migration) (LMS inline scripts). Do not add Jinja here; use window.TEACHER_CFG. */
    function _showLmsModal(id) {
      const el = document.getElementById(id);
      if (!el) return;
      el.classList.remove('hidden');
      el.style.display = 'flex';
      el.style.alignItems = 'center';
      el.style.justifyContent = 'center';
    }
    function _hideLmsModal(id) {
      const el = document.getElementById(id);
      if (!el) return;
      el.classList.add('hidden');
      el.style.display = 'none';
    }
    function openLmsClassHub() {
      _showLmsModal('lmsClassHubModal');
      loadLmsTeacherClasses();
    }
    function closeLmsClassHub() {
      _hideLmsModal('lmsClassHubModal');
    }
    async function loadLmsTeacherClasses() {
      const el = document.getElementById('lmsClassList');
      try {
        const res = await fetch('/api/lms/classes/mine', { credentials: 'include' });
        const body = await res.json();
        const classes = body.data || body || [];
        if (!classes.length) {
          el.innerHTML = '<p class="lms-status">No classes yet. Create one above.</p>';
          return;
        }
        el.innerHTML = await Promise.all(classes.map(async function (c) {
          let roster = [];
          try {
            const rRes = await fetch('/api/lms/classes/' + c.id + '/students', { credentials: 'include' });
            const rBody = await rRes.json();
            roster = rBody.data || rBody || [];
          } catch (e) { roster = []; }
          const rosterHtml = roster.length
            ? roster.map(function (s) {
              return '<li>' + escapeHtml(s.username || s.email || ('Student #' + s.student_id)) + '</li>';
            }).join('')
            : '<li>No students enrolled</li>';
          return '<div class="lms-card">' +
            '<strong>' + escapeHtml(c.name) + '</strong>' +
            (c.grade_level ? ' <span class="lms-status" style="display:inline;margin:0;">(' + escapeHtml(c.grade_level) + ')</span>' : '') +
            '<div class="lms-status">Join code: <code class="lms-code">' + escapeHtml(c.join_code) + '</code></div>' +
            '<div class="lms-status">Students (' + roster.length + '):</div>' +
            '<ul class="lms-class-roster-preview">' + rosterHtml + '</ul></div>';
        })).then(function (html) { return html.join(''); });
      } catch (err) {
        el.innerHTML = '<p class="lms-error">Failed to load classes.</p>';
      }
    }
    async function submitLmsCreateClass(e) {
      e.preventDefault();
      const name = document.getElementById('lmsClassName').value.trim();
      const gradeEl = document.getElementById('lmsClassGrade');
      const grade = gradeEl ? gradeEl.value : '';
      try {
        await lmsApi('/api/lms/classes', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name, grade_level: grade || null })
        });
        document.getElementById('lmsCreateClassForm').reset();
        lmsShowToast('Class created!');
        loadLmsTeacherClasses();
      } catch (err) {
        lmsShowToast(err.message, 'error');
      }
    }

    function openLmsAssignModal() {
      _showLmsModal('lmsAssignModal');
      loadLmsAssignOptions();
    }
    function closeLmsAssignModal() {
      _hideLmsModal('lmsAssignModal');
    }
    async function loadLmsAssignOptions() {
      const classEl = document.getElementById('lmsAssignClass');
      const quizEl = document.getElementById('lmsAssignQuiz');
      classEl.innerHTML = '<option value="">Loading...</option>';
      quizEl.innerHTML = '<option value="">Loading...</option>';
      try {
        const [cRes, qRes] = await Promise.all([
          fetch('/api/lms/classes/mine', { credentials: 'include' }),
          fetch('/api/lms/quizzes', { credentials: 'include' })
        ]);
        const classes = (await cRes.json()).data || [];
        const quizzes = (await qRes.json()).data || [];
        classEl.innerHTML = classes.length
          ? classes.map(function (c) { return '<option value="' + c.id + '">' + escapeHtml(c.name) + '</option>'; }).join('')
          : '<option value="">No classes — create one first</option>';
        const published = quizzes.filter(function (q) { return q.status === 'published'; });
        quizEl.innerHTML = published.length
          ? published.map(function (q) { return '<option value="' + q.id + '">' + escapeHtml(q.title) + '</option>'; }).join('')
          : '<option value="">No published quizzes — publish one first</option>';
      } catch (err) {
        classEl.innerHTML = '<option value="">Error loading</option>';
        quizEl.innerHTML = '<option value="">Error loading</option>';
      }
    }
    async function submitLmsAssignment(e) {
      e.preventDefault();
      const btn = document.getElementById('lmsAssignSubmitBtn');
      const status = document.getElementById('lmsAssignStatus');
      const classId = document.getElementById('lmsAssignClass').value;
      const quizId = document.getElementById('lmsAssignQuiz').value;
      if (!classId || !quizId) { status.textContent = 'Select class and quiz.'; return; }
      btn.disabled = true;
      status.textContent = 'Creating assignment...';
      try {
        const dueRaw = document.getElementById('lmsAssignDue').value;
        const body = {
          title: document.getElementById('lmsAssignTitle').value.trim(),
          class_id: parseInt(classId, 10),
          quiz_id: parseInt(quizId, 10),
          due_date: dueRaw ? new Date(dueRaw).toISOString() : null
        };
        const createRes = await fetch('/api/lms/assignments', {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });
        const createBody = await createRes.json();
        if (!createRes.ok) throw new Error((createBody.error && createBody.error.message) || 'Create failed');
        const assignmentId = (createBody.data || createBody).id;
        const pubRes = await fetch('/api/lms/assignments/' + assignmentId + '/publish', {
          method: 'POST', credentials: 'include'
        });
        const pubBody = await pubRes.json();
        if (!pubRes.ok) throw new Error((pubBody.error && pubBody.error.message) || 'Publish failed');
        status.textContent = 'Assignment published! Students can see it in My Quizzes.';
        document.getElementById('lmsAssignForm').reset();
        if (typeof lmsShowToast === 'function') {
          lmsShowToast('Assignment published! Students can see it in My Quizzes.');
        }
        setTimeout(function () {
          closeLmsAssignModal();
          status.textContent = '';
        }, 900);
      } catch (err) {
        status.textContent = 'Error: ' + err.message;
      } finally {
        btn.disabled = false;
      }
    }

    function _lmsFmtText(text, inline) {
      if (typeof lmsFormatRichText === 'function') {
        return lmsFormatRichText(text, inline ? { inline: true, quiz: true } : { quiz: true });
      }
      return escapeHtml(text == null ? '' : String(text));
    }
    function _lmsFmtOption(opt) {
      var text = (typeof lmsOptionText === 'function')
        ? lmsOptionText(opt)
        : ((opt && (opt.text || opt.latex)) || '');
      return _lmsFmtText(text, true);
    }
    function _lmsFmtQuestion(q) {
      var text = (typeof lmsQuestionText === 'function')
        ? lmsQuestionText(q)
        : ((q && (q.question_text || q.question_latex)) || '');
      return _lmsFmtText(text, false);
    }
    function _lmsTypeset(el) {
      if (!el) return Promise.resolve();
      // Single root typeset via shared helper (MathJax → KaTeX fallback).
      // Do not pass nested parent+children to MathJax — that left raw $...$ in quiz preview.
      if (typeof window.lmsTypesetMath === 'function') return window.lmsTypesetMath(el);
      el.classList.add('tex2jax_process');
      var ready = (window.MathJax && window.MathJax.startup && window.MathJax.startup.promise)
        ? window.MathJax.startup.promise.catch(function () {})
        : Promise.resolve();
      return ready.then(function () {
        if (window.MathJax && window.MathJax.typesetPromise) {
          try {
            if (typeof window.MathJax.typesetClear === 'function') window.MathJax.typesetClear([el]);
          } catch (e) { /* ignore */ }
          return window.MathJax.typesetPromise([el]).catch(function () {});
        }
        if (typeof renderMathInElement === 'function') {
          try {
            renderMathInElement(el, {
              delimiters: [
                { left: '$$', right: '$$', display: true },
                { left: '$', right: '$', display: false },
                { left: '\\[', right: '\\]', display: true },
                { left: '\\(', right: '\\)', display: false }
              ],
              throwOnError: false,
              ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code']
            });
          } catch (e2) { /* ignore */ }
        }
      });
    }

    let _lmsCurrentAssessmentId = null;
    let _lmsQuizTaskId = null;
    let _lmsPollTimer = null;
    let _lmsQuizProgressTick = null;
    let _lmsQuizFriendlyStep = 0;
    let _lmsDiagAssessmentId = null;
    let _lmsDiagThreadId = null;
    let _lmsDiagTopicsPoll = null;
    let _lmsCurriculumTopics = [];

    const LMS_DIAG_MCQ_OPTIONS = [1, 2, 3, 4, 5, 6, 8, 10];

    /** Map backend step/message/status → simple user step 1–4 (or 5 = done). */
    function _lmsQuizFriendlyStepFrom(meta) {
      meta = meta || {};
      var step = String(meta.step || meta.progress_step || '').toLowerCase();
      var msg = String(meta.message || meta.progress_message || '').toLowerCase();
      var status = String(meta.status || meta.extraction_status || '').toLowerCase();
      var pct = Number(meta.progress);
      if (status === 'completed' || step === 'done' || pct >= 100) return 5;
      if (status === 'failed') return _lmsQuizFriendlyStep || 1;
      if (step === 'save' || /sav(e|ing)|review|finish|almost|ready|putt?ing/.test(msg)) return 4;
      if (step === 'convert' || step === 'generate' || /mcq|question|generat|convert|writ/.test(msg)) return 3;
      if (step === 'extract' || /read|extract|pars|layout|hybrid|content|document|page/.test(msg)) return 2;
      if (step === 'upload' || status === 'pending' || status === 'none' || /upload|queue|start|receiv|file/.test(msg)) return 1;
      if (Number.isFinite(pct)) {
        if (pct >= 88) return 4;
        if (pct >= 68) return 3;
        if (pct >= 30) return 2;
        return 1;
      }
      return Math.max(1, _lmsQuizFriendlyStep || 1);
    }

    function _setLmsQuizProgress(pct, text, meta) {
      var wrap = document.getElementById('lmsQuizProgressWrap');
      if (wrap) {
        wrap.hidden = false;
        wrap.classList.add('active');
      }
      var step = _lmsQuizFriendlyStepFrom(Object.assign({
        progress: pct,
        progress_message: text,
        message: text,
      }, meta || {}));
      _lmsQuizFriendlyStep = Math.max(_lmsQuizFriendlyStep || 0, step === 5 ? 5 : step);
      if (step < 5) _lmsQuizFriendlyStep = Math.max(_lmsQuizFriendlyStep, step);

      var titles = {
        1: 'Getting your file',
        2: 'Reading your document',
        3: 'Writing quiz questions',
        4: 'Finishing up',
        5: 'Your quiz is ready',
      };
      var subtitles = {
        1: 'We received your PDF and are starting.',
        2: 'Looking through the pages to understand the content.',
        3: 'Creating clear multiple-choice questions for students.',
        4: 'Double-checking the quiz before we show it to you.',
        5: 'You can review the questions below.',
      };
      var active = step >= 5 ? 4 : step;
      var titleEl = document.getElementById('lmsQuizWaitTitle');
      var subEl = document.getElementById('lmsQuizWaitSubtitle');
      if (titleEl) titleEl.textContent = titles[step] || titles[active];
      if (subEl) subEl.textContent = subtitles[step] || subtitles[active];

      document.querySelectorAll('#lmsQuizSteps .td-quiz-step').forEach(function (li) {
        var n = parseInt(li.getAttribute('data-step'), 10);
        li.classList.remove('is-active', 'is-done');
        if (step >= 5 || n < active) li.classList.add('is-done');
        else if (n === active) li.classList.add('is-active');
      });
    }
    function _resetLmsQuizProgress() {
      if (_lmsQuizProgressTick) { clearInterval(_lmsQuizProgressTick); _lmsQuizProgressTick = null; }
      _lmsQuizFriendlyStep = 0;
      var wrap = document.getElementById('lmsQuizProgressWrap');
      if (wrap) {
        wrap.hidden = true;
        wrap.classList.remove('active');
      }
      document.querySelectorAll('#lmsQuizSteps .td-quiz-step').forEach(function (li) {
        li.classList.remove('is-active', 'is-done');
      });
      var titleEl = document.getElementById('lmsQuizWaitTitle');
      var subEl = document.getElementById('lmsQuizWaitSubtitle');
      if (titleEl) titleEl.textContent = 'Working on your quiz';
      if (subEl) subEl.textContent = 'This usually takes a short while — hang tight.';
    }
    function _startLmsQuizProgressPulse(startPct, label) {
      if (_lmsQuizProgressTick) clearInterval(_lmsQuizProgressTick);
      _setLmsQuizProgress(startPct || 8, label || 'Getting your file', { step: 'upload', message: label });
      // Soft step crawl while waiting for real backend updates (no %).
      var soft = startPct || 8;
      _lmsQuizProgressTick = setInterval(function () {
        if (soft < 85) {
          soft += soft < 40 ? 3 : soft < 70 ? 1.5 : 0.6;
          _setLmsQuizProgress(soft);
        }
      }, 1400);
    }

    function lmsDiagMcqSelectHtml(selected) {
      return LMS_DIAG_MCQ_OPTIONS.map(function (n) {
        return '<option value="' + n + '"' + (String(n) === String(selected) ? ' selected' : '') + '>' + n + ' MCQ' + (n > 1 ? 's' : '') + '</option>';
      }).join('');
    }

    function lmsDiagCurriculumSelectHtml(selectedId) {
      if (!_lmsCurriculumTopics.length) {
        return '<option value="">— Auto-detect —</option>';
      }
      var html = '<option value="">— Auto-detect —</option>';
      _lmsCurriculumTopics.forEach(function (t) {
        html += '<option value="' + t.id + '"' + (String(t.id) === String(selectedId) ? ' selected' : '') + '>' + escapeHtml(t.name) + '</option>';
      });
      return html;
    }

    async function loadLmsCurriculumTopics() {
      if (_lmsCurriculumTopics.length) return _lmsCurriculumTopics;
      try {
        const res = await fetch('/api/lms/topics?subject=Math', { credentials: 'include' });
        const body = await res.json();
        _lmsCurriculumTopics = body.data || body || [];
      } catch (e) {
        _lmsCurriculumTopics = [];
      }
      return _lmsCurriculumTopics;
    }

    function lmsDiagSelectAllTopics(checked) {
      document.querySelectorAll('.lms-diag-topic-cb').forEach(function (cb) {
        cb.checked = checked;
        var row = cb.closest('.lms-diag-topic-row');
        if (row) {
          row.querySelectorAll('select').forEach(function (sel) { sel.disabled = !checked; });
        }
      });
    }

    function lmsDiagApplyBulkCount(val) {
      if (!val) return;
      document.querySelectorAll('.lms-diag-topic-count').forEach(function (sel) {
        if (!sel.disabled) sel.value = val;
      });
    }

    function _setLmsDiagProgress(pct, text) {
      const wrap = document.getElementById('lmsDiagProgressWrap');
      const bar = document.getElementById('lmsDiagProgressBar');
      const pctEl = document.getElementById('lmsDiagProgressPct');
      const textEl = document.getElementById('lmsDiagProgressText');
      if (wrap) wrap.classList.add('active');
      if (bar) bar.style.width = Math.min(100, Math.max(0, pct)) + '%';
      if (pctEl) pctEl.textContent = Math.round(pct) + '%';
      if (textEl && text) textEl.textContent = text;
    }
    function _resetLmsDiagProgress() {
      const wrap = document.getElementById('lmsDiagProgressWrap');
      const bar = document.getElementById('lmsDiagProgressBar');
      if (wrap) wrap.classList.remove('active');
      if (bar) bar.style.width = '0%';
      const pctEl = document.getElementById('lmsDiagProgressPct');
      if (pctEl) pctEl.textContent = '0%';
    }
    function _resetLmsDiagnosticModal() {
      _lmsDiagAssessmentId = null;
      _lmsDiagThreadId = null;
      const form = document.getElementById('lmsDiagnosticPdfForm');
      if (form) form.reset();
      const status = document.getElementById('lmsDiagStatus');
      if (status) status.textContent = '';
      const preview = document.getElementById('lmsDiagPreview');
      if (preview) preview.innerHTML = '';
      const banner = document.getElementById('lmsDiagSuccessBanner');
      if (banner) banner.classList.remove('show');
      const topics = document.getElementById('lmsDiagStepTopics');
      if (topics) topics.style.display = 'none';
      const actions = document.getElementById('lmsDiagActions');
      if (actions) actions.style.display = 'none';
      _resetLmsDiagProgress();
      const btn = document.getElementById('lmsDiagUploadBtn');
      if (btn) { btn.disabled = false; }
    }
    function openLmsDiagnosticHub() {
      _resetLmsDiagnosticModal();
      _showLmsModal('lmsDiagnosticHubModal');
    }
    function closeLmsDiagnosticHub() {
      _hideLmsModal('lmsDiagnosticHubModal');
      stopLmsDiagTopicsPoll();
    }
    function stopLmsDiagTopicsPoll() {
      if (_lmsDiagTopicsPoll) { clearInterval(_lmsDiagTopicsPoll); _lmsDiagTopicsPoll = null; }
    }
    function submitLmsDiagnosticPdf(e) {
      e.preventDefault();
      const title = document.getElementById('lmsDiagTitle').value.trim();
      const grade = document.getElementById('lmsDiagGrade').value;
      const file = document.getElementById('lmsDiagPdfFile').files[0];
      const targetFile = document.getElementById('lmsDiagTargetPdfFile').files[0];
      if (!grade) {
        alert('Select a grade for this diagnostic.');
        if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
        return;
      }
      if (!file || !targetFile) {
        alert('Both Diagnostic Q&A PDF and Target content PDF are required.');
        if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
        return;
      }
      var diagName = (file.name || '').toLowerCase();
      var targetName = (targetFile.name || '').toLowerCase();
      if (!diagName.endsWith('.pdf') || !targetName.endsWith('.pdf')) {
        document.getElementById('lmsDiagStatus').textContent =
          'Error: This document does not match the required assessment format. Please upload a valid document.';
        if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
        return;
      }
      const btn = document.getElementById('lmsDiagUploadBtn');
      const status = document.getElementById('lmsDiagStatus');
      const banner = document.getElementById('lmsDiagSuccessBanner');
      if (banner) banner.classList.remove('show');
      btn.disabled = true;
      status.textContent = '';
      _setLmsDiagProgress(5, 'Preparing upload...');
      const fd = new FormData();
      fd.append('title', title);
      fd.append('grade_level', grade);
      fd.append('diagnostic_file', file);
      fd.append('target_file', targetFile);
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/lms/diagnostics/from-pdf');
      xhr.withCredentials = true;
      xhr.upload.onprogress = function (ev) {
        if (ev.lengthComputable) {
          const uploadPct = 10 + Math.round((ev.loaded / ev.total) * 55);
          _setLmsDiagProgress(uploadPct, 'Uploading PDFs...');
        }
      };
      xhr.onload = function () {
        _setLmsDiagProgress(75, 'Extracting questions...');
        let body;
        try { body = JSON.parse(xhr.responseText); } catch (parseErr) {
          status.textContent = 'Error: Invalid server response';
          _resetLmsDiagProgress();
          btn.disabled = false;
          if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
          return;
        }
        if (xhr.status < 200 || xhr.status >= 300) {
          status.textContent = 'Error: ' + ((body.error && body.error.message) || body.message || 'Upload failed');
          _resetLmsDiagProgress();
          btn.disabled = false;
          if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
          return;
        }
        _setLmsDiagProgress(100, 'Review generated questions...');
        const d = body.data || body;
        _lmsDiagAssessmentId = d.assessment_id;
        _lmsDiagThreadId = d.thread_id;
        status.textContent = 'Draft ready — ' + (d.question_count || '?') +
          ' questions generated. Review below, then Approve/Publish for students.';
        document.getElementById('lmsDiagStepTopics').style.display = 'block';
        document.getElementById('lmsDiagActions').style.display = 'flex';
        if (_lmsDiagAssessmentId) loadLmsDiagPreview();
        setTimeout(_resetLmsDiagProgress, 600);
        btn.disabled = false;
        if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
      };
      xhr.onerror = function () {
        status.textContent = 'Error: Network error during upload';
        _resetLmsDiagProgress();
        btn.disabled = false;
        if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
      };
      xhr.send(fd);
    }
    function startLmsDiagTopicsPoll() {
      stopLmsDiagTopicsPoll();
      if (!_lmsDiagThreadId) return;
      _lmsDiagTopicsPoll = setInterval(loadLmsDiagTopics, 2500);
      loadLmsDiagTopics();
    }
    async function loadLmsDiagTopics() {
      if (!_lmsDiagThreadId) return;
      const status = document.getElementById('lmsDiagStatus');
      const listEl = document.getElementById('lmsDiagTopicList');
      try {
        const res = await fetch('/api/lms/diagnostics/pdf/' + encodeURIComponent(_lmsDiagThreadId) + '/topics', { credentials: 'include' });
        const body = await res.json();
        if (!res.ok) throw new Error((body.error && body.error.message) || 'Failed to load topics');
        const d = body.data || body;
        const topics = d.topics || [];
        if (!topics.length) {
          status.textContent = d.message || 'Topics still processing — retrying...';
          return;
        }
        stopLmsDiagTopicsPoll();
        status.textContent = 'Found ' + topics.length + ' topic(s). Select topics, curriculum map, and MCQ counts.';
        await loadLmsCurriculumTopics();
        listEl.innerHTML = topics.map(function (t, idx) {
          const topic = t.topic || t.heading || ('Topic ' + (idx + 1));
          const page = t.page != null ? t.page : '';
          const suggested = t.suggested_topic_id || '';
          return '<div class="lms-diag-topic-row" style="display:grid;grid-template-columns:auto 1fr auto auto;gap:8px;align-items:center;padding:10px 8px;border-bottom:1px solid #f1f5f9;">' +
            '<input type="checkbox" class="lms-diag-topic-cb" data-topic="' + escapeHtml(topic) + '" data-page="' + page + '">' +
            '<div><div style="font-weight:600;font-size:.875rem;">' + escapeHtml(topic) + '</div>' +
            (page !== '' ? '<div style="color:#64748b;font-size:.75rem;">Page ' + page + '</div>' : '') + '</div>' +
            '<select class="lms-diag-curriculum-topic" disabled title="Curriculum topic" style="border:1px solid #cbd5e1;border-radius:6px;padding:4px 6px;font-size:.8125rem;min-width:140px;">' +
            lmsDiagCurriculumSelectHtml(suggested) + '</select>' +
            '<select class="lms-diag-topic-count" disabled style="border:1px solid #cbd5e1;border-radius:6px;padding:4px 6px;font-size:.8125rem;">' +
            lmsDiagMcqSelectHtml(2) + '</select></div>';
        }).join('');
        listEl.querySelectorAll('.lms-diag-topic-cb').forEach(function (cb) {
          cb.addEventListener('change', function () {
            var row = cb.closest('.lms-diag-topic-row');
            row.querySelectorAll('select').forEach(function (sel) { sel.disabled = !cb.checked; });
          });
        });
      } catch (err) {
        status.textContent = 'Topic error: ' + err.message;
      }
    }
    async function generateLmsDiagnostic() {
      if (!_lmsDiagAssessmentId) return;
      const status = document.getElementById('lmsDiagStatus');
      const btn = document.getElementById('lmsDiagGenerateBtn');
      const selections = [];
      document.querySelectorAll('.lms-diag-topic-cb:checked').forEach(function (cb) {
        var row = cb.closest('.lms-diag-topic-row');
        var countEl = row.querySelector('.lms-diag-topic-count');
        var topicEl = row.querySelector('.lms-diag-curriculum-topic');
        var payload = {
          topic: cb.getAttribute('data-topic'),
          page: cb.getAttribute('data-page') ? parseInt(cb.getAttribute('data-page'), 10) : null,
          question_count: parseInt(countEl.value, 10) || 2
        };
        if (topicEl && topicEl.value) payload.topic_id = parseInt(topicEl.value, 10);
        selections.push(payload);
      });
      if (!selections.length) {
        status.textContent = 'Select at least one topic.';
        return;
      }
      btn.disabled = true;
      status.textContent = 'Generating diagnostic questions with AI...';
      if (typeof showWaitOverlay === 'function') showWaitOverlay('Generating questions...');
      try {
        const res = await fetch('/api/lms/diagnostics/' + _lmsDiagAssessmentId + '/generate', {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ topics: selections })
        });
        const body = await res.json();
        if (!res.ok) throw new Error((body.error && body.error.message) || 'Generation failed');
        const d = body.data || body;
        status.textContent = 'Generated ' + (d.question_count || 0) + ' question(s)' +
          (d.overall_confidence != null ? ' | Confidence: ' + Math.round(d.overall_confidence * 100) + '%' : '') + '.';
        await loadLmsDiagPreview();
        document.getElementById('lmsDiagActions').style.display = 'flex';
      } catch (err) {
        status.textContent = 'Error: ' + err.message;
      } finally {
        btn.disabled = false;
        if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
      }
    }
    async function loadLmsDiagPreview() {
      const previewEl = document.getElementById('lmsDiagPreview');
      const res = await fetch('/api/lms/diagnostics/' + _lmsDiagAssessmentId + '/preview', { credentials: 'include' });
      const body = await res.json();
      const d = body.data || body;
      if (!d.questions || !d.questions.length) {
        previewEl.innerHTML = '<p class="lms-status">No questions yet.</p>';
        return;
      }
      previewEl.innerHTML = d.questions.map(function (item, idx) {
        const q = item.question || {};
        const opts = (q.options || []).map(function (o, oidx) {
          var mark = (q.correct_option_index === oidx) ? ' ✓' : '';
          return '<li>' + o.label + '. ' + _lmsFmtOption(o) + mark + '</li>';
        }).join('');
        return '<div class="lms-diag-preview-card">' +
          '<strong class="lms-preview-qnum">Q' + (idx + 1) + '.</strong> ' + _lmsFmtQuestion(q) +
          '<ul>' + opts + '</ul></div>';
      }).join('');
      _lmsTypeset(previewEl);
    }
    async function publishLmsDiagnostic() {
      if (!_lmsDiagAssessmentId) return;
      const statusEl = document.getElementById('lmsDiagStatus');
      const successBanner = document.getElementById('lmsDiagSuccessBanner');
      const actionsEl = document.getElementById('lmsDiagActions');
      const publishBtn = actionsEl ? actionsEl.querySelector('.lms-btn-primary') : null;
      if (publishBtn) publishBtn.disabled = true;
      if (successBanner) successBanner.classList.remove('show');
      statusEl.textContent = 'Approving diagnostic for students...';
      if (typeof showWaitOverlay === 'function') showWaitOverlay('Approving diagnostic...');
      try {
        const res = await fetch('/api/lms/diagnostics/' + _lmsDiagAssessmentId + '/publish', {
          method: 'POST', credentials: 'include'
        });
        const body = await res.json();
        if (!res.ok) throw new Error((body.error && body.error.message) || 'Publish failed');
        statusEl.textContent = '';
        const successText = document.getElementById('lmsDiagSuccessText');
        if (successText) {
          successText.textContent = 'Approved! Students can now see and start this diagnostic.';
        }
        if (successBanner) successBanner.classList.add('show');
        if (typeof lmsShowToast === 'function') {
          lmsShowToast('Diagnostic approved for students');
        }
        setTimeout(function () {
          closeLmsDiagnosticHub();
          _resetLmsDiagnosticModal();
        }, 2200);
      } catch (err) {
        statusEl.textContent = 'Publish error: ' + err.message;
        if (publishBtn) publishBtn.disabled = false;
      } finally {
        if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
      }
    }

    function _resetLmsQuizModal() {
      stopLmsPoll();
      _resetLmsQuizProgress();
      _lmsCurrentAssessmentId = null;
      _lmsQuizTaskId = null;
      const form = document.getElementById('lmsPdfQuizForm');
      if (form) {
        form.reset();
        form.style.display = '';
      }
      const status = document.getElementById('lmsQuizStatus');
      if (status) status.textContent = '';
      const preview = document.getElementById('lmsQuizPreview');
      if (preview) preview.innerHTML = '';
      const actions = document.getElementById('lmsQuizActions');
      if (actions) {
        actions.style.display = 'none';
        const publishBtn = actions.querySelector('button');
        if (publishBtn) publishBtn.disabled = false;
      }
      const btn = document.getElementById('lmsQuizSubmitBtn');
      if (btn) btn.disabled = false;
    }

    function openLmsQuizHub() {
      _resetLmsQuizModal();
      _showLmsModal('lmsQuizHubModal');
    }
    function closeLmsQuizHub() {
      _hideLmsModal('lmsQuizHubModal');
      stopLmsPoll();
      _resetLmsQuizProgress();
      if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
    }
    function stopLmsPoll() {
      if (_lmsPollTimer) { clearInterval(_lmsPollTimer); _lmsPollTimer = null; }
    }
    async function submitLmsPdfQuiz(e) {
      e.preventDefault();
      const title = document.getElementById('lmsQuizTitle').value.trim();
      const file = document.getElementById('lmsQuizPdfFile').files[0];
      const countEl = document.getElementById('lmsQuizMcqCount');
      const durationEl = document.getElementById('lmsQuizDuration');
      const btn = document.getElementById('lmsQuizSubmitBtn');
      const status = document.getElementById('lmsQuizStatus');
      let questionCount = parseInt(countEl && countEl.value, 10);
      if (!Number.isFinite(questionCount) || questionCount < 1) questionCount = 10;
      if (questionCount > 40) questionCount = 40;
      const durationRaw = durationEl ? String(durationEl.value || '').trim() : '';
      if (!/^[1-9]\d*$/.test(durationRaw)) {
        if (status) {
          status.textContent = 'Error: Duration must be a whole number of minutes (1 or more). Decimals, zero, and negative values are not allowed.';
        }
        if (durationEl) durationEl.focus();
        if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
        return;
      }
      const timeLimitMinutes = parseInt(durationRaw, 10);
      if (!file) {
        if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
        return;
      }
      var name = (file.name || '').toLowerCase();
      if (!name.endsWith('.pdf')) {
        if (status) status.textContent = 'Error: This document does not match the required assessment format. Please upload a valid document.';
        if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
        return;
      }
      btn.disabled = true;
      status.textContent = '';
      _startLmsQuizProgressPulse(6, 'Getting your file');
      const fd = new FormData();
      fd.append('title', title);
      fd.append('file', file);
      fd.append('question_count', String(questionCount));
      fd.append('time_limit_minutes', String(timeLimitMinutes));
      try {
        const res = await fetch('/api/lms/quizzes/from-pdf', { method: 'POST', body: fd, credentials: 'include' });
        const data = await res.json();
        if (!res.ok) {
          var errMsg = (data.error && data.error.message) || data.message || 'Upload failed';
          if (typeof errMsg !== 'string') errMsg = 'Upload failed';
          throw new Error(errMsg);
        }
        const payload = data.data || data;
        _lmsCurrentAssessmentId = payload.assessment_id;
        _lmsQuizTaskId = payload.task_id || null;
        _setLmsQuizProgress(18, 'Reading your document', {
          step: payload.async ? 'extract' : 'generate',
          message: payload.async ? 'Reading your document' : 'Writing quiz questions',
        });
        status.textContent = '';
        startLmsPoll();
      } catch (err) {
        status.textContent = 'Error: ' + err.message;
        btn.disabled = false;
        _resetLmsQuizProgress();
        if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
      }
    }
    function startLmsPoll() {
      stopLmsPoll();
      if (!_lmsCurrentAssessmentId) return;
      _lmsPollTimer = setInterval(pollLmsQuizStatus, 2000);
      pollLmsQuizStatus();
    }
    async function pollLmsQuizStatus() {
      if (!_lmsCurrentAssessmentId) return;
      const statusEl = document.getElementById('lmsQuizStatus');
      const actionsEl = document.getElementById('lmsQuizActions');
      try {
        var url = '/api/lms/quizzes/' + _lmsCurrentAssessmentId + '/pdf-status';
        if (_lmsQuizTaskId) url += '?task_id=' + encodeURIComponent(_lmsQuizTaskId);
        const res = await fetch(url, { credentials: 'include' });
        const body = await res.json();
        const d = body.data || body;
        const rawStatus = d.extraction_status || 'unknown';
        if (d.progress != null || d.progress_message || d.progress_step || rawStatus) {
          _setLmsQuizProgress(
            d.progress != null ? Number(d.progress) : undefined,
            d.progress_message || '',
            {
              step: d.progress_step,
              message: d.progress_message,
              progress_step: d.progress_step,
              progress_message: d.progress_message,
              extraction_status: rawStatus,
              progress: d.progress,
            }
          );
        }
        // Keep status line quiet during success path — steps UI carries the story.
        if (rawStatus !== 'failed' && rawStatus !== 'completed') {
          statusEl.textContent = '';
        }
        if (rawStatus === 'completed') {
          stopLmsPoll();
          if (_lmsQuizProgressTick) { clearInterval(_lmsQuizProgressTick); _lmsQuizProgressTick = null; }
          _setLmsQuizProgress(100, 'Your quiz is ready', { step: 'done', extraction_status: 'completed' });
          setTimeout(_resetLmsQuizProgress, 900);
          const form = document.getElementById('lmsPdfQuizForm');
          if (form) form.style.display = 'none';
          await loadLmsQuizPreview();
          actionsEl.style.display = 'flex';
          statusEl.textContent = '';
          if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
        } else if (rawStatus === 'failed') {
          stopLmsPoll();
          _resetLmsQuizProgress();
          const form = document.getElementById('lmsPdfQuizForm');
          if (form) form.style.display = '';
          const submitBtn = document.getElementById('lmsQuizSubmitBtn');
          if (submitBtn) submitBtn.disabled = false;
          statusEl.textContent = 'Failed: ' + (d.error_message || 'Unknown error');
          if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
        }
      } catch (err) {
        statusEl.textContent = 'Poll error: ' + err.message;
      }
    }
    async function loadLmsQuizPreview() {
      const previewEl = document.getElementById('lmsQuizPreview');
      const res = await fetch('/api/lms/quizzes/' + _lmsCurrentAssessmentId + '/preview', { credentials: 'include' });
      const body = await res.json();
      const d = body.data || body;
      if (!d.questions || !d.questions.length) {
        var failMsg = (d.pdf_source && d.pdf_source.error_message) ? d.pdf_source.error_message : '';
        if (!failMsg && d.warnings && d.warnings.length) {
          failMsg = d.warnings.join('; ');
        }
        previewEl.innerHTML = '<p class="lms-error" style="font-weight:600;">No questions generated.</p>' +
          (failMsg ? '<p class="lms-status">' + escapeHtml(failMsg) + '</p>' : '');
        return;
      }
      window._lmsQuizPreviewQuestions = d.questions;
      var mathKeyboardHtml = _lmsMathKeyboardHtml();
      previewEl.innerHTML = d.questions.map(function (item, idx) {
        const q = item.question || {};
        const qid = q.id;
        const opts = (q.options || []).map(function (o, oidx) {
          var isCorrect = q.correct_option_index === oidx;
          return '<div class="lms-preview-opt' + (isCorrect ? ' is-correct' : '') + '">' +
            '<span class="lms-preview-opt-label">' + escapeHtml(o.label || String.fromCharCode(65 + oidx)) + '.</span>' +
            '<span class="lms-preview-opt-body">' + _lmsFmtOption(o) +
            (isCorrect ? ' <span class="lms-preview-correct-mark">✓</span>' : '') +
            '</span></div>';
        }).join('');
        var editOpts = (q.options || []).map(function (o, oidx) {
          var lab = o.label || String.fromCharCode(65 + oidx);
          return '<label class="lms-q-edit-opt">' +
            '<span>' + escapeHtml(lab) + '</span>' +
            '<textarea class="lms-q-edit-option lms-q-edit-field" data-oidx="' + oidx + '" rows="2">' +
            escapeHtml(o.text || o.latex || '') + '</textarea></label>';
        }).join('');
        var correctSel = [0,1,2,3].map(function (i) {
          var lab = String.fromCharCode(65 + i);
          return '<option value="' + i + '"' + (q.correct_option_index === i ? ' selected' : '') + '>' + lab + '</option>';
        }).join('');
        return '<div class="lms-diag-preview-card lms-quiz-preview-card" data-qid="' + qid + '" data-qidx="' + idx + '">' +
          '<div class="lms-preview-head">' +
            '<div class="lms-preview-stem"><strong class="lms-preview-qnum">Q' + (idx + 1) + '.</strong> ' +
            _lmsFmtText(
              (typeof lmsQuestionText === 'function' ? lmsQuestionText(q) : (q.question_text || q.question_latex || '')),
              true
            ) + '</div>' +
            '<button type="button" class="lms-btn lms-btn-ghost lms-q-edit-toggle" data-qid="' + qid + '">Edit</button>' +
          '</div>' +
          '<div class="lms-preview-opts">' + opts + '</div>' +
          '<div class="lms-q-edit-panel" id="lmsQEdit-' + qid + '" hidden>' +
            '<div class="lms-math-keyboard" data-qid="' + qid + '">' +
              '<div class="lms-math-keyboard-title">Math keyboard — click a symbol to insert into the focused box</div>' +
              mathKeyboardHtml +
            '</div>' +
            '<label class="lms-q-edit-stem">Question text' +
              '<textarea class="lms-q-edit-stem-input lms-q-edit-field" rows="3">' + escapeHtml(q.question_text || '') + '</textarea>' +
            '</label>' +
            '<div class="lms-q-edit-options">' + editOpts + '</div>' +
            '<label class="lms-q-edit-correct">Correct answer <select class="lms-q-edit-correct-sel">' + correctSel + '</select></label>' +
            '<div class="lms-q-edit-actions">' +
              '<button type="button" class="lms-btn lms-btn-primary lms-q-edit-save" data-qid="' + qid + '">Save</button>' +
              '<button type="button" class="lms-btn lms-btn-ghost lms-q-edit-cancel" data-qid="' + qid + '">Cancel</button>' +
              '<span class="lms-q-edit-status" id="lmsQEditStatus-' + qid + '"></span>' +
            '</div>' +
          '</div></div>';
      }).join('');
      _lmsTypeset(previewEl);
      _bindLmsQuizEditHandlers(previewEl);
    }

    function _lmsMathKeyboardHtml() {
      // label, insert template (| = cursor after insert)
      var rows = [
        {
          title: 'Basics',
          keys: [
            ['+', '+'], ['−', '-'], ['×', '\\times'], ['÷', '\\div'],
            ['=', '='], ['≠', '\\neq'], ['≈', '\\approx'], ['±', '\\pm'],
            ['(', '('], [')', ')'], ['[', '['], [']', ']']
          ]
        },
        {
          title: 'Fractions & roots',
          keys: [
            ['a/b', '\\frac{|}{}'], ['1/2', '\\frac{1}{2}'], ['3/2', '\\frac{3}{2}'],
            ['√', '\\sqrt{|}'], ['∛', '\\sqrt[3]{|}'], ['n√', '\\sqrt[n]{|}']
          ]
        },
        {
          title: 'Powers & index',
          keys: [
            ['x²', '^{2}'], ['x³', '^{3}'], ['xⁿ', '^{|}'], ['x₀', '_{|}'],
            ['10ⁿ', '10^{|}'], ['aˣ', 'a^{|}'], ['eˣ', 'e^{|}']
          ]
        },
        {
          title: 'Log / trig',
          keys: [
            ['log', '\\log'], ['logₐ', '\\log_{|}'], ['ln', '\\ln'],
            ['sin', '\\sin'], ['cos', '\\cos'], ['tan', '\\tan']
          ]
        },
        {
          title: 'Symbols',
          keys: [
            ['∞', '\\infty'], ['π', '\\pi'], ['θ', '\\theta'], ['α', '\\alpha'],
            ['β', '\\beta'], ['°', '^\\circ'], ['·', '\\cdot'], ['…', '\\ldots']
          ]
        },
        {
          title: 'Wrap',
          keys: [
            ['( math )', '\\(|\\)'], ['[ math ]', '\\[|\\]'],
            ['left( )', '\\left(|\\right)'], ['abs', '\\left||\\right|']
          ]
        }
      ];
      return rows.map(function (row) {
        var btns = row.keys.map(function (k) {
          return '<button type="button" class="lms-math-key" data-insert="' + escapeHtml(k[1]) + '" title="' + escapeHtml(k[1]) + '">' +
            escapeHtml(k[0]) + '</button>';
        }).join('');
        return '<div class="lms-math-row"><span class="lms-math-row-label">' + escapeHtml(row.title) + '</span>' +
          '<div class="lms-math-keys">' + btns + '</div></div>';
      }).join('');
    }

    function _lmsInsertMathToken(textarea, template) {
      if (!textarea || template == null) return;
      var start = typeof textarea.selectionStart === 'number' ? textarea.selectionStart : textarea.value.length;
      var end = typeof textarea.selectionEnd === 'number' ? textarea.selectionEnd : start;
      var selected = textarea.value.slice(start, end);
      var cursorMark = template.indexOf('|');
      var insert = template;
      var caret = start;
      if (cursorMark >= 0) {
        insert = template.replace('|', selected || '');
        caret = start + cursorMark + (selected ? selected.length : 0);
      } else {
        insert = selected ? (template + selected) : template;
        caret = start + insert.length;
      }
      textarea.value = textarea.value.slice(0, start) + insert + textarea.value.slice(end);
      textarea.focus();
      textarea.selectionStart = textarea.selectionEnd = caret;
    }

    function _bindLmsQuizEditHandlers(root) {
      if (!root || root._lmsEditBound) return;
      root._lmsEditBound = true;
      root.addEventListener('focusin', function (ev) {
        var field = ev.target && ev.target.classList && ev.target.classList.contains('lms-q-edit-field') ? ev.target : null;
        if (!field) return;
        var panel = field.closest('.lms-q-edit-panel');
        if (panel) panel._lmsActiveField = field;
      });
      root.addEventListener('click', async function (ev) {
        var t = ev.target;
        if (!t) return;
        var key = t.closest && t.closest('.lms-math-key');
        if (key) {
          ev.preventDefault();
          var insert = key.getAttribute('data-insert') || '';
          var panel = key.closest('.lms-q-edit-panel');
          var active = (panel && panel._lmsActiveField) ||
            (panel && panel.querySelector('textarea:focus')) ||
            (panel && panel.querySelector('.lms-q-edit-field'));
          _lmsInsertMathToken(active, insert);
          return;
        }
        var toggle = t.closest && t.closest('.lms-q-edit-toggle');
        if (toggle) {
          var qidT = toggle.getAttribute('data-qid');
          var panelT = document.getElementById('lmsQEdit-' + qidT);
          if (panelT) panelT.hidden = !panelT.hidden;
          return;
        }
        var cancel = t.closest && t.closest('.lms-q-edit-cancel');
        if (cancel) {
          var panelC = document.getElementById('lmsQEdit-' + cancel.getAttribute('data-qid'));
          if (panelC) panelC.hidden = true;
          return;
        }
        var save = t.closest && t.closest('.lms-q-edit-save');
        if (!save) return;
        var qid = save.getAttribute('data-qid');
        var card = root.querySelector('.lms-quiz-preview-card[data-qid="' + qid + '"]');
        var panel = document.getElementById('lmsQEdit-' + qid);
        var status = document.getElementById('lmsQEditStatus-' + qid);
        if (!card || !panel) return;
        var stem = (panel.querySelector('.lms-q-edit-stem-input') || {}).value || '';
        var correctIdx = parseInt((panel.querySelector('.lms-q-edit-correct-sel') || {}).value, 10);
        if (isNaN(correctIdx)) correctIdx = 0;
        var options = [];
        panel.querySelectorAll('.lms-q-edit-option').forEach(function (ta) {
          var oidx = parseInt(ta.getAttribute('data-oidx'), 10);
          options.push({
            label: String.fromCharCode(65 + oidx),
            text: ta.value || '',
            latex: null
          });
        });
        if (options.length !== 4) {
          if (status) status.textContent = 'Need 4 options.';
          return;
        }
        save.disabled = true;
        if (status) status.textContent = 'Saving...';
        try {
          var res = await fetch('/api/lms/quizzes/' + _lmsCurrentAssessmentId + '/questions/' + qid, {
            method: 'PUT',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              question_text: stem,
              question_latex: null,
              options: options,
              correct_option_index: correctIdx
            })
          });
          var body = await res.json();
          if (!res.ok) throw new Error((body.error && body.error.message) || body.message || 'Save failed');
          if (status) status.textContent = 'Saved.';
          await loadLmsQuizPreview();
        } catch (err) {
          if (status) status.textContent = 'Error: ' + err.message;
          save.disabled = false;
        }
      });
    }
    async function publishLmsQuiz() {
      if (!_lmsCurrentAssessmentId) return;
      const statusEl = document.getElementById('lmsQuizStatus');
      const actionsEl = document.getElementById('lmsQuizActions');
      const publishBtn = actionsEl ? actionsEl.querySelector('button') : null;
      if (publishBtn) publishBtn.disabled = true;
      statusEl.textContent = 'Publishing quiz...';
      if (typeof showWaitOverlay === 'function') showWaitOverlay('Publishing quiz...');
      try {
        const res = await fetch('/api/lms/quizzes/' + _lmsCurrentAssessmentId + '/publish', {
          method: 'POST', credentials: 'include'
        });
        const body = await res.json();
        if (!res.ok) throw new Error(body.error || body.message || 'Publish failed');
        statusEl.textContent = '';
        if (typeof lmsShowToast === 'function') {
          lmsShowToast('Quiz published successfully!');
        }
        closeLmsQuizHub();
        _resetLmsQuizModal();
      } catch (err) {
        statusEl.textContent = 'Publish error: ' + err.message;
        if (publishBtn) publishBtn.disabled = false;
      } finally {
        if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
      }
    }
    document.addEventListener('DOMContentLoaded', function () {
      _hideLmsModal('lmsClassHubModal');
      _hideLmsModal('lmsQuizHubModal');
      _hideLmsModal('lmsAssignModal');
      _hideLmsModal('lmsDiagnosticHubModal');
    });
