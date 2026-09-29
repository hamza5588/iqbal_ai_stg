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
      var count = prev && prev.questions ? prev.questions.length : null;
      return '<div class="td-quiz-card" data-quiz-id="' + z.id + '">' +
        '<div class="td-quiz-head">' +
          '<div class="td-avatar-sm"><i class="far fa-clipboard"></i></div>' +
          '<h3 onclick="tdQuizzes.toggle(' + z.id + ')">' + U.esc(z.title || 'Untitled quiz') + '</h3>' +
          (count != null ? '<span class="td-pill">' + count + ' MCQ' + (count === 1 ? '' : 's') + '</span>' : '') +
          '<div class="td-side-meta">' +
            (isPub ? '<span class="td-pill green"><i class="fas fa-check-circle"></i> Published</span>' : '<span class="td-pill orange">Draft</span>') +
            (isPub ? '<button type="button" class="td-btn td-btn-outline td-btn-sm" onclick="tdQuizzes.openAssign(' + z.id + ')"><i class="fas fa-users"></i> Assign</button>' : '') +
            '<button type="button" class="td-btn td-btn-outline-blue td-btn-sm" onclick="tdQuizzes.toggle(' + z.id + ')" aria-expanded="' + open + '"><i class="fas fa-eye"></i> View</button>' +
            '<span class="td-chev" style="cursor:pointer;transform:rotate(' + (open ? 180 : 0) + 'deg);" onclick="tdQuizzes.toggle(' + z.id + ')"><i class="fas fa-chevron-down"></i></span>' +
          '</div>' +
        '</div>' +
        (open ? '<div class="td-quiz-body" id="tdQuizBody-' + z.id + '">' + previewHtml(z, prev) + '</div>' : '') +
      '</div>';
    }).join('');
    if (state.expandedId != null) {
      var body = $('tdQuizBody-' + state.expandedId);
      if (body && typeof window._lmsTypeset === 'function') window._lmsTypeset(body);
    }
  }

  function previewHtml(z, prev) {
    if (!prev) return '<div class="td-empty" style="padding:20px;"><div class="td-spinner"></div></div>';
    if (prev.error) return '<p class="td-error">' + U.esc(prev.error) + '</p>';
    var qs = prev.questions || [];
    var head = '<div style="display:flex;justify-content:space-between;align-items:center;margin:4px 0 10px;gap:10px;flex-wrap:wrap;">' +
      '<h4 style="margin:0;">MCQs (' + qs.length + ')</h4>' +
      (z.status !== 'published' && qs.length ? '<button type="button" class="td-btn td-btn-primary td-btn-sm" onclick="tdQuizzes.publish(' + z.id + ', this)">Publish Quiz</button>' : '') +
      '</div>';
    if (!qs.length) return head + '<p class="td-desc">No questions generated for this quiz.</p>';
    return head + '<div class="td-mcq-strip">' + qs.map(function (item, idx) {
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
      U.toast('Quiz published successfully!');
      await load();
    } catch (err) {
      U.toast(err.message, 'error');
      if (btn) btn.disabled = false;
    }
  }

  function openCreate() {
    var card = $('tdCreateQuizCard');
    if (!card) return;
    if (typeof window._resetLmsQuizModal === 'function') window._resetLmsQuizModal();
    var fn = $('tdQuizFileName');
    if (fn) { fn.hidden = true; fn.textContent = ''; }
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
    load: load, render: render, setTab: setTab, toggle: toggle, publish: publish,
    openCreate: openCreate, closeCreate: closeCreate, onFileChosen: onFileChosen, openAssign: openAssign,
    _state: state
  };
})();
