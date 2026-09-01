/** Deficiency learning chat — post-diagnostic weak-area practice (separate from main lesson chat). */
(function () {
  function fmtText(text, inline) {
    if (typeof window.lmsFormatRichText === 'function') {
      return window.lmsFormatRichText(text, inline ? { inline: true } : undefined);
    }
    return escapeHtml(text == null ? '' : String(text));
  }
  function fmtOption(opt) {
    return fmtText((opt && (opt.latex || opt.text)) || '', true);
  }
  function fmtQuestion(q) {
    return fmtText((q && (q.question_latex || q.question_text)) || '', false);
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

  var defState = {
    sessionId: null,
    selectedOption: null,
    tutorOpen: false,
    tutorHistory: [],
    tutorLoading: false
  };

  function ensureDeficiencyModal() {
    if (document.getElementById('lmsDeficiencyModal')) return;
    var html =
      '<div id="lmsDeficiencyModal" class="lms-modal-backdrop" onclick="if(event.target===this)closeDeficiencyChat()">' +
      '<div class="lms-modal lms-modal-lg">' +
      '<div class="lms-modal-header"><h2>Learning Chat</h2>' +
      '<button type="button" class="lms-modal-close" onclick="closeDeficiencyChat()">&times;</button></div>' +
      '<div class="lms-modal-body" id="lmsDeficiencyBody"><div class="lms-spinner"></div></div></div></div>';
    document.body.insertAdjacentHTML('beforeend', html);
  }

  function scrollDeficiencyChatToBottom() {
    var box = document.getElementById('lmsDeficiencyTutorMessages');
    if (!box) return;
    requestAnimationFrame(function () {
      box.scrollTop = box.scrollHeight;
    });
  }

  function renderTypingIndicator() {
    return '<div class="lms-chat-msg bot" id="lmsDeficiencyTyping">' +
      '<div class="lms-chat-avatar">AI</div>' +
      '<div class="lms-chat-bubble">' +
      '<div class="lms-chat-typing"><span></span><span></span><span></span></div>' +
      '<span style="font-size:.75rem;color:#64748b;margin-left:8px;">Thinking...</span>' +
      '</div></div>';
  }

  function renderTutorMessages() {
    var html = defState.tutorHistory.map(function (m) {
      var role = m.role === 'user' ? 'user' : 'bot';
      var levelTag = m.levelLabel
        ? '<div style="font-size:.7rem;color:#64748b;margin-bottom:4px;">' + escapeHtml(m.levelLabel) + '</div>'
        : '';
      return '<div class="lms-chat-msg ' + role + '">' +
        '<div class="lms-chat-avatar">' + (role === 'user' ? 'You' : 'AI') + '</div>' +
        '<div class="lms-chat-bubble">' + levelTag +
        (role === 'user' ? escapeHtml(m.text) : fmtText(m.text || '')) + '</div></div>';
    }).join('');
    if (defState.tutorLoading) {
      html += renderTypingIndicator();
    }
    return html;
  }

  function bindDeficiencyTutorInput() {
    var input = document.getElementById('lmsDeficiencyTutorInput');
    if (!input) return;
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendDeficiencyTutorMessage();
      }
    });
    if (!defState.tutorLoading) {
      input.focus();
    }
  }

  function renderDeficiencyView(data) {
    window._lmsDeficiencyLastState = data;
    var body = document.getElementById('lmsDeficiencyBody');
    if (!body) return;

    if (data.completed || !data.current_question) {
      body.innerHTML =
        '<div style="text-align:center;padding:24px;">' +
        '<div style="font-size:2rem;font-weight:800;color:var(--primary-color);">Done!</div>' +
        '<p class="lms-status">You completed ' + (data.correct_count || 0) + ' / ' + (data.total_questions || 0) + ' correctly.</p>' +
        '<button type="button" class="lms-btn lms-btn-primary" onclick="closeDeficiencyChat()">Close</button></div>';
      if (typeof loadLmsStudentDashboard === 'function') loadLmsStudentDashboard();
      return;
    }

    var q = data.current_question;
    var pct = data.total_questions
      ? Math.round(100 * (data.current_index + 1) / data.total_questions)
      : 0;
    var opts = (q.options || []).map(function (o, oi) {
      var sel = defState.selectedOption === oi ? ' selected' : '';
      return '<button type="button" class="lms-quiz-option' + sel + '" onclick="selectDeficiencyOption(' + oi + ')">' +
        '<strong>' + escapeHtml(o.label || String.fromCharCode(65 + oi)) + '.</strong> ' +
        fmtOption(o) + '</button>';
    }).join('');

    var tutorLevelHint = data.tutor_assist_label
      ? '<p class="lms-status" style="font-size:.75rem;margin-bottom:8px;">Next help: <strong>' + escapeHtml(data.tutor_assist_label) + '</strong> — I won\'t give the answer right away.</p>'
      : '';

    var sendDisabled = defState.tutorLoading ? ' disabled' : '';
    var tutorSection = defState.tutorOpen
      ? tutorLevelHint +
        '<div id="lmsDeficiencyTutorMessages" class="lms-chat-messages">' +
        (renderTutorMessages() || '<p class="lms-status">Ask a question — the tutor guides you step by step using your teacher\'s PDF.</p>') +
        '</div>' +
        '<div class="lms-chat-input-row">' +
        '<textarea id="lmsDeficiencyTutorInput" class="lms-textarea" rows="2" placeholder="I\'m stuck on this step... (Enter to send)"' +
        (defState.tutorLoading ? ' disabled' : '') + '></textarea>' +
        '<button type="button" id="lmsDeficiencyTutorSend" class="lms-btn lms-btn-secondary"' + sendDisabled +
        ' onclick="sendDeficiencyTutorMessage()">Send</button></div>' +
        '<p class="lms-status" style="font-size:.7rem;margin:6px 0 0;">Press Enter to send · Shift+Enter for new line</p>' +
        '<button type="button" class="lms-btn lms-btn-secondary" style="margin-top:8px;" onclick="requestDeficiencyMoreHelp"' +
        (defState.tutorLoading ? ' disabled' : '') + '>Need more help</button>'
      : '';

    body.innerHTML =
      '<div class="lms-quiz-progress"><div class="lms-quiz-progress-bar" style="width:' + pct + '%"></div></div>' +
      '<p class="lms-status">Question ' + (data.current_index + 1) + ' of ' + data.total_questions +
      (q.topic_name ? ' · <strong>' + escapeHtml(q.topic_name) + '</strong>' : '') + '</p>' +
      (data.has_pdf ? '<p class="lms-status" style="font-size:.75rem;">Questions from teacher target PDF · weak area: ' + escapeHtml((q && q.topic_name) || '') + '</p>' : '<p class="lms-status" style="font-size:.75rem;color:#991b1b;">Teacher has not uploaded target content PDF yet.</p>') +
      '<h3 style="font-size:1rem;font-weight:700;margin:12px 0;">' + fmtQuestion(q) + '</h3>' +
      opts +
      '<div class="lms-modal-footer" style="border:none;padding:16px 0 0;margin:0;display:flex;flex-wrap:wrap;gap:8px;">' +
      '<button type="button" class="lms-btn lms-btn-primary" onclick="submitDeficiencyAnswer()"' +
      (defState.selectedOption === null ? ' disabled' : '') + '>Submit Answer</button>' +
      '<button type="button" class="lms-btn lms-btn-secondary" onclick="toggleDeficiencyTutor()">Ask Tutor</button>' +
      '<button type="button" class="lms-btn lms-btn-secondary" onclick="pauseDeficiencyChat()">Pause &amp; Exit</button></div>' +
      tutorSection;

    if (defState.tutorOpen) {
      bindDeficiencyTutorInput();
      scrollDeficiencyChatToBottom();
    }

    typeset(body);
  }

  window.openDeficiencyChat = async function () {
    ensureDeficiencyModal();
    lmsOpenModal('lmsDeficiencyModal');
    defState = { sessionId: null, selectedOption: null, tutorOpen: false, tutorHistory: [], tutorLoading: false };
    var body = document.getElementById('lmsDeficiencyBody');
    body.innerHTML = '<div class="lms-spinner"></div><p class="lms-status" style="text-align:center">Preparing your personalized questions...</p>';
    try {
      var data = await lmsApi('/api/lms/deficiency/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ force_new: true })
      });
      defState.sessionId = data.session_id;
      renderDeficiencyView(data);
    } catch (err) {
      body.innerHTML = '<p class="lms-error">' + escapeHtml(err.message) + '</p>';
    }
  };

  window.closeDeficiencyChat = function () {
    lmsCloseModal('lmsDeficiencyModal');
    defState = { sessionId: null, selectedOption: null, tutorOpen: false, tutorHistory: [], tutorLoading: false };
  };

  window.selectDeficiencyOption = function (idx) {
    defState.selectedOption = idx;
    renderDeficiencyView(window._lmsDeficiencyLastState);
  };

  window.submitDeficiencyAnswer = async function () {
    if (defState.sessionId === null || defState.selectedOption === null) return;
    var body = document.getElementById('lmsDeficiencyBody');
    try {
      var data = await lmsApi('/api/lms/deficiency/sessions/' + defState.sessionId + '/answer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ selected_option_index: defState.selectedOption })
      });
      window._lmsDeficiencyLastState = data;
      defState.selectedOption = null;
      defState.tutorHistory = [];
      defState.tutorLoading = false;
      if (data.last_answer && !data.last_answer.correct) {
        defState.tutorOpen = true;
        lmsShowToast('Incorrect — ask the tutor for help or continue to the next question.', 'error');
      } else if (data.last_answer && data.last_answer.correct) {
        lmsShowToast('Correct!', 'success');
      }
      renderDeficiencyView(data);
    } catch (err) {
      if (body) body.innerHTML = '<p class="lms-error">' + escapeHtml(err.message) + '</p>';
    }
  };

  window.toggleDeficiencyTutor = function () {
    defState.tutorOpen = !defState.tutorOpen;
    if (defState.tutorOpen && !defState.tutorHistory.length) {
      defState.tutorHistory.push({
        role: 'bot',
        text: 'Ask me anything about this question. I\'ll help step by step — starting with a small prompt, not the full answer.',
        levelLabel: ''
      });
    }
    renderDeficiencyView(window._lmsDeficiencyLastState || { current_question: {}, current_index: 0, total_questions: 1, completed: false });
  };

  window.requestDeficiencyMoreHelp = function () {
    if (defState.tutorLoading) return;
    var input = document.getElementById('lmsDeficiencyTutorInput');
    if (input) {
      input.value = 'I still need help with this question.';
      sendDeficiencyTutorMessage();
    }
  };

  window.sendDeficiencyTutorMessage = async function () {
    if (defState.tutorLoading) return;
    var input = document.getElementById('lmsDeficiencyTutorInput');
    var msg = (input && input.value || '').trim();
    if (!msg || !defState.sessionId) return;
    defState.tutorHistory.push({ role: 'user', text: msg });
    if (input) input.value = '';
    defState.tutorLoading = true;
    renderDeficiencyView(window._lmsDeficiencyLastState);
    scrollDeficiencyChatToBottom();
    try {
      var data = await lmsApi('/api/lms/deficiency/sessions/' + defState.sessionId + '/explain', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg })
      });
      defState.tutorHistory.push({
        role: 'bot',
        text: data.reply || 'No response',
        levelLabel: data.assist_level_label || ''
      });
      if (window._lmsDeficiencyLastState) {
        window._lmsDeficiencyLastState.tutor_assist_level = data.next_assist_level;
        window._lmsDeficiencyLastState.tutor_assist_label = data.next_assist_level_label;
      }
    } catch (err) {
      defState.tutorHistory.push({ role: 'bot', text: 'Error: ' + err.message });
    } finally {
      defState.tutorLoading = false;
      renderDeficiencyView(window._lmsDeficiencyLastState);
      scrollDeficiencyChatToBottom();
    }
  };

  window.pauseDeficiencyChat = async function () {
    if (defState.sessionId) {
      try {
        await lmsApi('/api/lms/deficiency/sessions/' + defState.sessionId + '/pause', { method: 'POST' });
      } catch (e) { /* ignore */ }
    }
    closeDeficiencyChat();
    lmsShowToast('Progress saved — resume anytime from Learning Path');
  };
})();
