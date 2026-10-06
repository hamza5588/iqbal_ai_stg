/* Teacher → Quizzes (screens 05–06).
   Create (PDF → MCQ), poll, preview and publish reuse teacher-lms-hubs.js unchanged via the same element IDs;
   assign reuses loadLmsAssignOptions / submitLmsAssignment. This file owns the list + card visibility.
   GET /api/lms/quizzes → [{id,title,status}], GET /api/lms/quizzes/:id/preview, POST /api/lms/quizzes/:id/publish. */
(function () {
  var U = window.tdUtil;
  var state = { quizzes: [], tab: 'all', expandedId: null, previews: {}, loaded: false };

  function $(id) { return document.getElementById(id); }

  async function load() {
    var list = $('tdQuizList');
    if (!list) return;
    if (!state.loaded) list.innerHTML = '<div class="td-empty"><div class="td-spinner"></div><p>Loading quizzes…</p></div>';
    try {
      state.quizzes = (await lmsApi('/api/lms/quizzes')) || [];
      state.loaded = true;
      render();
    } catch (err) {
      list.innerHTML = '<div class="td-empty"><p class="td-error">' + U.esc(err.message || 'Failed to load quizzes.') + '</p></div>';
    }
  }

  function setTab(tab) {
    state.tab = tab;
    render();
  }

  function render() {
    var list = $('tdQuizList');
    if (!list) return;
    var pub = state.quizzes.filter(function (q) { return q.status === 'published'; }).length;
    var setLabel = function (id, text) { var el = $(id); if (el) el.textContent = text; };
    setLabel('tdQuizTabAll', 'All Quizzes (' + state.quizzes.length + ')');
    setLabel('tdQuizTabPublished', 'Published (' + pub + ')');
    setLabel('tdQuizTabDrafts', 'Drafts (' + (state.quizzes.length - pub) + ')');
    document.querySelectorAll('[data-quiz-tab]').forEach(function (b) {
      b.classList.toggle('active', b.getAttribute('data-quiz-tab') === state.tab);
    });

    if (!state.quizzes.length) {
      list.innerHTML = '<div class="td-empty"><img class="td-empty-ic" src="' + U.esc(window.TEACHER_CFG.icons.quizzes) + '" alt="">' +
        U.empty('No quizzes yet', 'Click Create Quiz to generate MCQs from a PDF.') + '</div>';
      return;
    }
    var q = (($('tdQuizSearch') || {}).value || '').trim().toLowerCase();
    var sort = ($('tdQuizSort') || {}).value || 'latest';
    var rows = state.quizzes.filter(function (z) {
      if (state.tab === 'published' && z.status !== 'published') return false;
      if (state.tab === 'draft' && z.status === 'published') return false;
      return !q || String(z.title || '').toLowerCase().indexOf(q) >= 0;
    });
    if (sort === 'title') rows.sort(function (a, b) { return String(a.title).localeCompare(String(b.title)); });
    else rows.sort(function (a, b) { return b.id - a.id; });
    if (!rows.length) {
      list.innerHTML = '<div class="td-empty">' + U.empty('No matching quizzes', 'Try a different tab or search.') + '</div>';
      return;
    }

    list.innerHTML = rows.map(function (z) {
      var open = state.expandedId === z.id;
      var isPub = z.status === 'published';
      var prev = state.previews[z.id];
      var count = questionCount(z);
      var mins = z.time_limit_minutes;
      // Drafts are edited, published and deleted from here; published quizzes are view-only.
      var draftActions = isPub ? '' :
        '<button type="button" class="td-btn td-btn-outline-blue td-btn-sm" onclick="tdQuizzes.toggle(' + z.id + ')" aria-expanded="' + open + '"><i class="fas fa-pen"></i> ' + (open ? 'Close' : 'Review &amp; Edit') + '</button>' +
        (count ? '<button type="button" class="td-btn td-btn-primary td-btn-sm" onclick="tdQuizzes.publish(' + z.id + ', this)"><i class="fas fa-paper-plane"></i> Publish</button>' : '') +
        '<button type="button" class="td-btn td-btn-danger-outline td-btn-sm" onclick="tdQuizzes.remove(' + z.id + ', this)" title="Delete this draft quiz"><i class="fas fa-trash"></i> Delete</button>';
      return '<div class="td-quiz-card" data-quiz-id="' + z.id + '">' +
        '<div class="td-quiz-head">' +
          '<div class="td-avatar-sm"><i class="far fa-clipboard"></i></div>' +
          '<h3 onclick="tdQuizzes.toggle(' + z.id + ')">' + U.esc(z.title || 'Untitled quiz') + '</h3>' +
          (count != null ? '<span class="td-pill">' + (count ? count + ' MCQ' + (count === 1 ? '' : 's') : 'No questions') + '</span>' : '') +
          (mins ? '<span class="td-pill"><i class="far fa-clock"></i> ' + mins + ' min</span>' : '') +
          '<div class="td-side-meta">' +
            (isPub ? '<span class="td-pill green"><i class="fas fa-check-circle"></i> Published</span>' : '<span class="td-pill orange">Draft</span>') +
            (isPub ? '<button type="button" class="td-btn td-btn-outline td-btn-sm" onclick="tdQuizzes.openAssign(' + z.id + ')"><i class="fas fa-users"></i> Assign</button>' +
              '<button type="button" class="td-btn td-btn-outline-blue td-btn-sm" onclick="tdQuizzes.toggle(' + z.id + ')" aria-expanded="' + open + '"><i class="fas fa-eye"></i> View</button>' : draftActions) +
            '<span class="td-chev" style="cursor:pointer;transform:rotate(' + (open ? 180 : 0) + 'deg);" onclick="tdQuizzes.toggle(' + z.id + ')"><i class="fas fa-chevron-down"></i></span>' +
          '</div>' +
        '</div>' +
        (open ? '<div class="td-quiz-body" id="tdQuizBody-' + z.id + '">' + previewHtml(z, prev) + '</div>' : '') +
      '</div>';
    }).join('');
    if (state.expandedId != null) {
      var body = $('tdQuizBody-' + state.expandedId);
      if (body && typeof window._lmsTypeset === 'function') window._lmsTypeset(body);
      mountDraftEditor(state.expandedId);
    }
  }

  function questionCount(z) {
    var prev = state.previews[z.id];
    if (prev && prev.questions) return prev.questions.length;
    return z.question_count != null ? z.question_count : null;
  }

  // A draft opened from the list gets the same per-question editor as the create-quiz preview.
  function mountDraftEditor(id) {
    var host = $('tdQuizEditor-' + id);
    var prev = state.previews[id];
    if (!host || !prev || prev.error || typeof window.lmsRenderQuizEditor !== 'function') return;
    host._lmsOnChange = function (data) {
      // A saved edit reloads the quiz inside the editor; keep the cached copy and the header pill in step.
      state.previews[id] = data;
      var row = state.quizzes.find(function (z) { return z.id === id; });
      if (row) row.question_count = (data.questions || []).length;
    };
    window.lmsRenderQuizEditor(host, id, prev);
  }

  function previewHtml(z, prev) {
    if (!prev) return '<div class="td-empty" style="padding:20px;"><div class="td-spinner"></div></div>';
    if (prev.error) return '<p class="td-error">' + U.esc(prev.error) + '</p>';
    var qs = prev.questions || [];
    var mins = z.time_limit_minutes != null ? z.time_limit_minutes : '';
    var settings =
      '<div class="td-field" style="max-width:260px;margin:0 0 14px;">' +
        '<label for="tdQuizDuration-' + z.id + '">Duration (minutes) <span class="req">*</span></label>' +
        '<div style="display:flex;gap:8px;align-items:center;">' +
          '<input id="tdQuizDuration-' + z.id + '" type="number" min="1" step="1" inputmode="numeric" value="' + U.esc(String(mins)) + '" placeholder="e.g. 20">' +
          '<button type="button" class="td-btn td-btn-outline td-btn-sm" onclick="tdQuizzes.saveDuration(' + z.id + ')">Save</button>' +
        '</div>' +
        '<p class="td-help" style="margin:6px 0 0;">Whole minutes only (1+). Applies to the student countdown timer.</p>' +
      '</div>';
    var isDraft = z.status !== 'published';
    var head = '<div class="td-quiz-toolbar">' +
      '<h4>MCQs (' + qs.length + ')</h4>' +
      (isDraft ? '<div class="td-quiz-toolbar-actions">' +
        (qs.length ? '<button type="button" class="td-btn td-btn-primary td-btn-sm" onclick="tdQuizzes.publish(' + z.id + ', this)">Publish Quiz</button>' : '') +
        '<button type="button" class="td-btn td-btn-danger-outline td-btn-sm" onclick="tdQuizzes.remove(' + z.id + ', this)"><i class="fas fa-trash"></i> Delete draft</button>' +
        '</div>' : '') +
      '</div>';
    if (!qs.length) {
      return settings + head + '<p class="td-desc">This draft has no questions, so it cannot be published. ' +
        'Delete it and create the quiz again from a PDF that contains the questions.</p>';
    }
    if (isDraft) {
      // Filled by mountDraftEditor(): each question has an Edit button until the quiz is published.
      return settings + head +
        '<p class="td-desc" style="margin:0 0 10px;">Check each question, use <b>Edit</b> to correct the wording, options or answer, then <b>Publish Quiz</b>. Students only see a quiz after it is published and assigned to a class.</p>' +
        '<div class="lms-quiz-preview td-quiz-editor" id="tdQuizEditor-' + z.id + '"></div>';
    }
    return settings + head + '<div class="td-mcq-strip">' + qs.map(function (item, idx) {
      var q = item.question || {};
      var stem = typeof window.lmsQuestionText === 'function' ? window.lmsQuestionText(q) : (q.question_text || q.question_latex || '');
      var opts = (q.options || []).map(function (o, oi) {
        var ok = q.correct_option_index === oi;
        var label = U.esc(o.label || String.fromCharCode(65 + oi));
        var text = typeof window._lmsFmtOption === 'function' ? window._lmsFmtOption(o) : U.esc(o.text || '');
        return '<div class="td-mcq-opt' + (ok ? ' correct' : '') + '"><i class="' + (ok ? 'fas fa-circle-dot' : 'far fa-circle') + '"></i><span>' + label + ') ' + text + '</span></div>';
      }).join('');
      var stemHtml = typeof window._lmsFmtText === 'function' ? window._lmsFmtText(stem, true) : U.esc(stem);
      return '<div class="td-mcq"><div class="td-mcq-q"><b>Q' + (idx + 1) + '.</b> ' + stemHtml + '</div>' + opts + '</div>';
    }).join('') + '</div>';
  }

  async function saveDuration(id) {
    var input = $('tdQuizDuration-' + id);
    var raw = input ? String(input.value || '').trim() : '';
    if (!/^[1-9]\d*$/.test(raw)) {
      U.toast('Duration must be a whole number of minutes (1 or more). Decimals, zero, and negatives are not allowed.', 'error');
      if (input) input.focus();
      return;
    }
    try {
      var updated = await lmsApi('/api/lms/quizzes/' + id, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ time_limit_minutes: parseInt(raw, 10) })
      });
      var row = state.quizzes.find(function (z) { return z.id === id; });
      if (row) row.time_limit_minutes = updated.time_limit_minutes;
      U.toast('Quiz duration saved (' + raw + ' min)');
      render();
    } catch (err) {
      U.toast(err.message || 'Could not save duration', 'error');
    }
  }

  async function toggle(id) {
    if (state.expandedId === id) { state.expandedId = null; render(); return; }
    state.expandedId = id;
    render();
    if (!state.previews[id]) {
      try {
        state.previews[id] = (await lmsApi('/api/lms/quizzes/' + id + '/preview')) || { questions: [] };
      } catch (err) {
        state.previews[id] = { error: err.message || 'Could not load quiz preview.' };
      }
      render();
    }
  }

  async function publish(id, btn) {
    if (btn) btn.disabled = true;
    try {
      await lmsApi('/api/lms/quizzes/' + id + '/publish', { method: 'POST' });
      U.toast('Quiz published. Use Assign to give it to a class.');
      delete state.previews[id];
      if (state.expandedId === id) state.expandedId = null;
      await load();
    } catch (err) {
      U.toast(err.message, 'error');
      if (btn) btn.disabled = false;
    }
  }

  // Drafts only: the server refuses to delete a quiz that has been published.
  async function remove(id, btn) {
    var quiz = state.quizzes.find(function (z) { return z.id === id; });
    var name = quiz && quiz.title ? '"' + quiz.title + '"' : 'this draft quiz';
    if (!window.confirm('Delete ' + name + '? This cannot be undone.')) return;
    if (btn) btn.disabled = true;
    try {
      await lmsApi('/api/lms/quizzes/' + id, { method: 'DELETE' });
      U.toast('Draft quiz deleted.');
      delete state.previews[id];
      if (state.expandedId === id) state.expandedId = null;
      await load();
    } catch (err) {
      U.toast(err.message || 'Could not delete the quiz', 'error');
      if (btn) btn.disabled = false;
    }
  }

  // "Import all questions" ticked → the count box is not used, so grey it out.
  function syncImportAll() {
    var all = $('lmsQuizImportAll');
    var count = $('lmsQuizMcqCount');
    if (!all || !count) return;
    count.disabled = all.checked;
    if (!all.checked) count.focus();
  }

  function openCreate() {
    var card = $('tdCreateQuizCard');
    if (!card) return;
    if (typeof window._resetLmsQuizModal === 'function') window._resetLmsQuizModal();
    var fn = $('tdQuizFileName');
    if (fn) { fn.hidden = true; fn.textContent = ''; }
    // form.reset() re-ticks "Import all"; put the count box back in its matching disabled state.
    var all = $('lmsQuizImportAll');
    var count = $('lmsQuizMcqCount');
    if (all && count) count.disabled = all.checked;
    card.hidden = false;
    setTimeout(function () {
      card.scrollIntoView({ behavior: 'smooth', block: 'start' });
      var t = $('lmsQuizTitle');
      if (t) t.focus({ preventScroll: true });
    }, 30);
  }

  function closeCreate() {
    if (typeof window.closeLmsQuizHub === 'function') window.closeLmsQuizHub();
  }

  function onFileChosen(input) {
    var fn = $('tdQuizFileName');
    if (!fn) return;
    var f = input.files && input.files[0];
    fn.hidden = !f;
    fn.textContent = f ? f.name : '';
  }

  async function openAssign(quizId) {
    var card = $('tdAssignQuizCard');
    if (!card) return;
    card.hidden = false;
    var status = $('lmsAssignStatus');
    if (status) status.textContent = '';
    if (typeof window.loadLmsAssignOptions === 'function') await window.loadLmsAssignOptions();
    if (quizId != null) {
      var sel = $('lmsAssignQuiz');
      if (sel && sel.querySelector('option[value="' + quizId + '"]')) sel.value = String(quizId);
      var title = $('lmsAssignTitle');
      var quiz = state.quizzes.find(function (z) { return z.id === quizId; });
      if (title && !title.value && quiz) title.value = quiz.title || '';
    }
    card.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  // Legacy hub "close" hooks now close the inline cards and refresh the list.
  function wrapLegacyHooks() {
    var origCloseQuiz = window.closeLmsQuizHub;
    if (typeof origCloseQuiz === 'function' && !origCloseQuiz._td) {
      window.closeLmsQuizHub = function () {
        origCloseQuiz.apply(this, arguments);
        if (typeof window._resetLmsQuizModal === 'function') window._resetLmsQuizModal();
        var card = $('tdCreateQuizCard');
        if (card) card.hidden = true;
        state.previews = {};
        load();
      };
      window.closeLmsQuizHub._td = true;
    }
    var origCloseAssign = window.closeLmsAssignModal;
    if (typeof origCloseAssign === 'function' && !origCloseAssign._td) {
      window.closeLmsAssignModal = function () {
        origCloseAssign.apply(this, arguments);
        var card = $('tdAssignQuizCard');
        if (card) card.hidden = true;
      };
      window.closeLmsAssignModal._td = true;
    }
    // The legacy hubs open modals that no longer exist in this shell; route them to the inline cards.
    window.openLmsQuizHub = function () { window.tdShowView('quizzes'); openCreate(); };
    window.openLmsAssignModal = function () { window.tdShowView('quizzes'); openAssign(); };
  }
  document.addEventListener('DOMContentLoaded', wrapLegacyHooks);

  window.tdQuizzes = {
    load: load, render: render, setTab: setTab, toggle: toggle, publish: publish, remove: remove,
    saveDuration: saveDuration, syncImportAll: syncImportAll,
    openCreate: openCreate, closeCreate: closeCreate, onFileChosen: onFileChosen, openAssign: openAssign,
    _state: state
  };
})();
