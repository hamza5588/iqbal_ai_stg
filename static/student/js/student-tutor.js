/* 07 AI Tutor — inline page instead of the lms-panels.js modal, same API:
   GET/DELETE /api/lms/tutor/history?mode=student, POST /api/lms/tutor/chat {message} → {reply}.
   Chat History popover: the tutor thread + saved lesson conversations (GET /get_conversations),
   which open in the lesson viewer; rename/delete as on the old page. */
(function () {
  var CFG = window.STUDENT_CFG || {};
  var state = { history: [], loading: false, loaded: false, convs: null, showAll: false };
  var SHORT_LIST = 5;

  function fmt(text) {
    if (typeof window.lmsFormatRichText === 'function') return window.lmsFormatRichText(text || '', {});
    return escapeHtml(text || '');
  }
  function bot(html) {
    return '<div class="sd-ai-bubble"><div class="sd-bot-ic"><img src="' + escapeHtml((CFG.icons || {}).chatBot || '') + '" alt=""></div><div class="sd-msg">' + html + '</div></div>';
  }
  function user(text) {
    return '<div class="sd-ai-bubble user"><div class="sd-bot-ic">' + escapeHtml((CFG.username || 'S').charAt(0).toUpperCase()) + '</div><div class="sd-msg">' + escapeHtml(text) + '</div></div>';
  }

  function render() {
    var box = document.getElementById('sdTutorMessages');
    if (!box) return;
    var html = '<div class="sd-tutor-welcome">' + bot('Hello! 👋<br>How can I help you today?') + '</div>';
    html += state.history.map(function (m) { return m.role === 'user' ? user(m.text) : bot(fmt(m.text)); }).join('');
    if (state.loading) html += bot('<span class="sd-typing"><span></span><span></span><span></span></span>');
    box.innerHTML = html;
    var input = document.getElementById('lmsTutorInput');
    var send = document.getElementById('sdTutorSend');
    if (input) input.disabled = state.loading;
    if (send) send.disabled = state.loading;
    requestAnimationFrame(function () { box.scrollTop = box.scrollHeight; });
    if (typeof window.lmsTypesetMath === 'function') window.lmsTypesetMath(box);
  }

  async function loadHistory() {
    state.loading = true;
    render();
    try {
      var data = await lmsApi('/api/lms/tutor/history?mode=student');
      state.history = (data.messages || []).map(function (m) { return { role: m.role === 'user' ? 'user' : 'bot', text: m.text || '' }; });
      state.loaded = true;
      if (state.history.length) window.showToast('Restored your tutor conversation', 'success', 2500);
    } catch (e) {
      state.history = [];
    } finally {
      state.loading = false;
      render();
    }
  }

  /* Overrides of the lms-panels.js modal versions (same endpoints). */
  window.openLmsTutorPanel = function () { window.sdShowView('tutor'); };
  window.sendLmsTutorMessage = async function () {
    if (state.loading) return;
    var input = document.getElementById('lmsTutorInput');
    var msg = (input && input.value || '').trim();
    if (!msg) return;
    input.value = '';
    state.history.push({ role: 'user', text: msg });
    state.loading = true;
    render();
    try {
      var data = await lmsApi('/api/lms/tutor/chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: msg })
      });
      state.history.push({ role: 'bot', text: data.reply || data.message || 'No response' });
    } catch (err) {
      state.history.push({ role: 'bot', text: 'Error: ' + err.message });
    } finally {
      state.loading = false;
      render();
      if (input) input.focus();
    }
  };
  window.clearLmsTutorHistory = async function () {
    var ok = typeof showInAppConfirm === 'function'
      ? await showInAppConfirm('Clear all AI Tutor chat history?', { title: 'Clear chat', confirmLabel: 'Clear', cancelLabel: 'Cancel', iconClass: 'fas fa-trash' })
      : window.confirm('Clear all AI Tutor chat history?');
    if (!ok) return;
    try {
      await lmsApi('/api/lms/tutor/history?mode=student', { method: 'DELETE' });
      state.history = [];
      render();
      window.sdCloseHistory();
      window.showToast('Chat history cleared', 'success');
    } catch (err) {
      window.showToast(err.message || 'Could not clear history', 'error');
    }
  };

  window.sdTutorPrompt = function (prefix) {
    var input = document.getElementById('lmsTutorInput');
    if (!input) return;
    input.value = prefix;
    input.focus();
    input.setSelectionRange(prefix.length, prefix.length);
  };
  var voice = null;
  window.sdTutorVoice = function () {
    if (!voice && window.sdAttachVoice) voice = window.sdAttachVoice('lmsTutorInput', 'sdTutorMic');
    if (voice) voice.toggle();
  };

  /* ── Chat History popover ── */
  function renderHistoryList() {
    var list = document.getElementById('sdHistList');
    var more = document.getElementById('sdHistMore');
    if (!list) return;
    var html = '<div class="sd-h-item active" onclick="sdCloseHistory()"><i class="far fa-comment-dots"></i><span class="sd-h-title">AI Tutor chat</span></div>';
    if (state.convs === null) {
      html += '<div class="sd-spinner" style="width:22px;height:22px;"></div>';
    } else if (!state.convs.length) {
      html += '<div class="sd-h-group">Lesson chats</div><div class="sd-h-empty">No lesson conversations yet. Open a lesson and ask a question.</div>';
    } else {
      var items = state.showAll ? state.convs : state.convs.slice(0, SHORT_LIST);
      html += '<div class="sd-h-group">Lesson chats</div>' + items.map(function (c) {
        return '<div class="sd-h-item" data-conv-id="' + c.id + '" onclick="sdOpenHistoryItem(' + c.id + ')">' +
          '<i class="far fa-comment-dots"></i><span class="sd-h-title">' + escapeHtml(c.title || 'New Conversation') + '</span>' +
          '<button type="button" class="sd-h-menu" aria-label="Conversation options" onclick="event.stopPropagation();sdHistoryMenu(event,' + c.id + ')"><i class="fas fa-ellipsis-v"></i></button></div>';
      }).join('');
    }
    list.innerHTML = html;
    if (more) {
      var n = state.convs ? state.convs.length : 0;
      more.hidden = n <= SHORT_LIST;
      more.innerHTML = state.showAll ? 'Show fewer <i class="fas fa-chevron-up"></i>' : 'View all conversations <i class="fas fa-chevron-right"></i>';
    }
  }

  async function loadConversations() {
    state.convs = null;
    renderHistoryList();
    try {
      var convs = await window.sdListConversations();
      convs.sort(function (a, b) {
        var ta = (window.sdParseTs(a.last_message || a.updated_at || a.created_at) || 0) - 0;
        var tb = (window.sdParseTs(b.last_message || b.updated_at || b.created_at) || 0) - 0;
        return tb - ta;
      });
      state.convs = convs;
    } catch (e) {
      state.convs = [];
    }
    renderHistoryList();
  }

  window.sdToggleHistory = function (e) {
    if (e) e.stopPropagation();
    var pop = document.getElementById('sdHistPop');
    if (!pop) return;
    if (pop.hidden) { pop.hidden = false; loadConversations(); }
    else pop.hidden = true;
    var btn = document.getElementById('sdHistToggle');
    if (btn) btn.setAttribute('aria-expanded', String(!pop.hidden));
  };
  window.sdCloseHistory = function () {
    var pop = document.getElementById('sdHistPop');
    if (pop) pop.hidden = true;
    var btn = document.getElementById('sdHistToggle');
    if (btn) btn.setAttribute('aria-expanded', 'false');
    closeCtx();
  };
  window.sdToggleAllConversations = function () { state.showAll = !state.showAll; renderHistoryList(); };
  window.sdOpenHistoryItem = function (id) {
    var c = (state.convs || []).find(function (x) { return String(x.id) === String(id); });
    window.sdCloseHistory();
    window.sdOpenConversation(id, c && c.title);
  };

  function closeCtx() { var m = document.getElementById('sdHistCtx'); if (m) m.remove(); }
  window.sdHistoryMenu = function (e, id) {
    closeCtx();
    var m = document.createElement('div');
    m.id = 'sdHistCtx';
    m.className = 'sd-h-ctx';
    m.innerHTML = '<button type="button" data-a="rename"><i class="fas fa-edit"></i> Rename</button>' +
      '<button type="button" class="danger" data-a="delete"><i class="fas fa-trash"></i> Delete</button>';
    document.body.appendChild(m);
    var r = e.currentTarget.getBoundingClientRect();
    m.style.left = Math.min(r.left, window.innerWidth - 170) + 'px';
    m.style.top = Math.max(8, r.top - m.offsetHeight - 4) + 'px';
    m.querySelector('[data-a="rename"]').onclick = function (ev) { ev.stopPropagation(); closeCtx(); renameConversation(id); };
    m.querySelector('[data-a="delete"]').onclick = function (ev) { ev.stopPropagation(); closeCtx(); deleteConversation(id); };
  };

  async function renameConversation(id) {
    var c = (state.convs || []).find(function (x) { return String(x.id) === String(id); });
    var title = window.prompt('Enter new chat title:', (c && c.title) || 'New Chat');
    if (!title || !title.trim()) return;
    try {
      var res = await fetch('/update_conversation_title/' + encodeURIComponent(id), {
        method: 'PUT', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: title.trim() })
      });
      if (!res.ok) { var d = await res.json().catch(function () { return {}; }); throw new Error(d.error || 'Failed to update title'); }
      if (c) c.title = title.trim();
      renderHistoryList();
    } catch (err) {
      window.showToast('Failed to rename chat: ' + err.message, 'error', 3000);
    }
  }
  async function deleteConversation(id) {
    var ok = typeof showInAppConfirm === 'function'
      ? await showInAppConfirm('Are you sure you want to delete this chat?', { title: 'Delete chat', confirmLabel: 'Delete', cancelLabel: 'Cancel', iconClass: 'fas fa-trash' })
      : window.confirm('Are you sure you want to delete this chat?');
    if (!ok) return;
    try {
      var res = await fetch('/delete_conversation/' + encodeURIComponent(id), { method: 'DELETE', credentials: 'include' });
      if (!res.ok) { var d = await res.json().catch(function () { return {}; }); throw new Error(d.error || 'Failed to delete conversation'); }
      window.sdUnbindConversation(id);
      state.convs = (state.convs || []).filter(function (x) { return String(x.id) !== String(id); });
      renderHistoryList();
      window.showToast('Chat deleted successfully', 'success', 2500);
    } catch (err) {
      window.showToast('Failed to delete chat: ' + err.message, 'error', 3000);
    }
  }

  document.addEventListener('click', function (e) {
    var pop = document.getElementById('sdHistPop');
    var ctx = document.getElementById('sdHistCtx');
    if (ctx && !ctx.contains(e.target)) closeCtx();
    if (pop && !pop.hidden && !pop.contains(e.target) && !(e.target.closest && e.target.closest('#sdHistToggle')) && !ctx) window.sdCloseHistory();
  });

  window.sdViews = window.sdViews || {};
  window.sdViews.tutor = {
    onShow: function () {
      if (!state.loaded) loadHistory(); else render();
      var input = document.getElementById('lmsTutorInput');
      if (input) setTimeout(function () { input.focus(); }, 50);
    }
  };
})();
