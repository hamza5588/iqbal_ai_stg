/** Deficiency learning chat — post-diagnostic weak-area practice (separate from main lesson chat). */
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
      : ((opt && (opt.text || opt.latex)) || '');
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
      '<span class="lms-status" style="font-size:.75rem;margin:0 0 0 8px;">Thinking...</span>' +
      '</div></div>';
  }

  var TUTOR_SUGGESTED_PROMPTS = [
    'Explain this in simpler words',
    'Break it into small steps',
    'Give me a hint',
    'Show me an example',
    'I still don\'t understand'
  ];

  function renderTutorMessages() {
    var html = defState.tutorHistory.map(function (m, i) {
      var role = m.role === 'user' ? 'user' : 'bot';
      var levelTag = m.levelLabel
        ? '<div class="lms-status" style="font-size:.7rem;margin:0 0 4px;">' + escapeHtml(m.levelLabel) + '</div>'
        : '';
      var extra = '';
      if (role === 'bot' && m.retry) {
        extra = '<button type="button" class="lms-btn lms-btn-secondary" style="margin-top:8px;" onclick="retryDeficiencyTutorMessage()">Try again</button>';
      } else if (role === 'bot') {
        extra = '<button type="button" class="lms-copy-btn" title="Copy" onclick="lmsCopyTutorMessage(' + i + ',this)">Copy</button>';
      }
      return '<div class="lms-chat-msg ' + role + '">' +
        '<div class="lms-chat-avatar">' + (role === 'user' ? 'You' : 'AI') + '</div>' +
        '<div class="lms-chat-bubble">' + levelTag +
        (role === 'user' ? escapeHtml(m.text) : fmtText(m.text || '')) + extra + '</div></div>';
    }).join('');
    if (defState.tutorLoading) {
      html += renderTypingIndicator();
    }
    return html;
  }

  function renderTutorSuggestedPrompts() {
    if (defState.tutorLoading) return '';
    return '<div class="lms-prompt-chips">' +
      TUTOR_SUGGESTED_PROMPTS.map(function (p) {
        return '<button type="button" class="lms-prompt-chip" onclick="sendDeficiencySuggestedPrompt(' +
          JSON.stringify(p).replace(/"/g, '&quot;') + ')">' + escapeHtml(p) + '</button>';
      }).join('') + '</div>';
  }

  window.lmsCopyTutorMessage = function (idx, btn) {
    var m = defState.tutorHistory[idx];
    if (!m) return;
    var text = String(m.text || '');
    var done = function () {
      if (!btn) return;
      var prev = btn.textContent;
      btn.textContent = 'Copied';
      setTimeout(function () { btn.textContent = prev; }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(function () { done(); });
    } else {
      var ta = document.createElement('textarea');
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); } catch (e) { /* ignore */ }
      document.body.removeChild(ta); done();
    }
  };

  window.sendDeficiencySuggestedPrompt = function (text) {
    if (defState.tutorLoading) return;
    var input = document.getElementById('lmsDeficiencyTutorInput');
    if (input) input.value = text;
    sendDeficiencyTutorMessage();
  };

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
      var got = data.correct_count || 0;
      var tot = data.total_questions || 0;
      var aced = tot > 0 && got === tot;
      body.innerHTML =
        '<div style="text-align:center;padding:24px;">' +
        '<div style="font-size:2.4rem;">' + (aced ? '🏆' : '🎉') + '</div>' +
        '<div style="font-size:1.6rem;font-weight:800;color:var(--primary-color);">' +
        (aced ? 'Perfect run!' : 'Great effort!') + '</div>' +
        '<p class="lms-status">You got ' + got + ' / ' + tot + ' right.</p>' +
        '<button type="button" class="lms-btn lms-btn-primary" onclick="closeDeficiencyChat()">Close</button></div>';
      if (!defState._celebratedDone) {
        defState._celebratedDone = true;
        try { lmsFeedback.celebrate(aced ? 'Perfect run!' : 'Nice work!'); } catch (e) { /* ignore */ }
      }
      if (typeof loadLmsStudentDashboard === 'function') loadLmsStudentDashboard();
      return;
    }

    var q = data.current_question;
    var pct = data.total_questions
      ? Math.round(100 * (data.current_index + 1) / data.total_questions)
      : 0;
    var opts = (q.options || []).map(function (o, oi) {
      var cls = 'lms-quiz-option';
      if (defState.selectedOption === oi) cls += ' selected';
      if (defState.lastWrongIndex === oi) cls += ' lms-opt-wrong';
      return '<button type="button" class="' + cls + '" onclick="selectDeficiencyOption(' + oi + ')">' +
        '<span class="lms-quiz-option-label">' + escapeHtml(o.label || String.fromCharCode(65 + oi)) + '.</span>' +
        '<span class="lms-quiz-option-body">' + fmtOption(o) + '</span></button>';
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
        renderTutorSuggestedPrompts() +
        '<div class="lms-chat-input-row">' +
        '<textarea id="lmsDeficiencyTutorInput" class="lms-textarea" rows="2" placeholder="I\'m stuck on this step... (Enter to send)"' +
        (defState.tutorLoading ? ' disabled' : '') + '></textarea>' +
        '<button type="button" id="lmsDeficiencyTutorSend" class="lms-btn lms-btn-secondary"' + sendDisabled +
        ' onclick="sendDeficiencyTutorMessage()">Send</button></div>' +
        '<p class="lms-status" style="font-size:.7rem;margin:6px 0 0;">Press Enter to send · Shift+Enter for new line</p>' +
        '<button type="button" class="lms-btn lms-btn-secondary" style="margin-top:8px;" onclick="requestDeficiencyMoreHelp()"' +
        (defState.tutorLoading ? ' disabled' : '') + '>Need more help</button>'
      : '';

    body.innerHTML =
      '<div class="lms-quiz-progress"><div class="lms-quiz-progress-bar" style="width:' + pct + '%"></div></div>' +
      '<p class="lms-status">Question ' + (data.current_index + 1) + ' of ' + data.total_questions +
      (q.topic_name ? ' · <strong>' + escapeHtml(q.topic_name) + '</strong>' : '') + '</p>' +
      (data.has_pdf ? '<p class="lms-status" style="font-size:.75rem;">Questions from teacher target PDF · weak area: ' + escapeHtml((q && q.topic_name) || '') + '</p>' : '<p class="lms-status" style="font-size:.75rem;color:#991b1b;">Teacher has not uploaded target content PDF yet.</p>') +
      '<h3 class="lms-quiz-stem">' + fmtQuestion(q) + '</h3>' +
      opts +
      '<div class="lms-modal-footer" style="border:none;padding:16px 0 0;margin:0;display:flex;flex-wrap:wrap;gap:8px;">' +
      '<button type="button" class="lms-btn lms-btn-primary" onclick="submitDeficiencyAnswer()"' +
      (defState.selectedOption === null ? ' disabled' : '') + '>Submit Answer</button>' +
      (data.last_answer && data.last_answer.correct === false
        ? '<button type="button" class="lms-btn lms-btn-secondary" onclick="advanceDeficiencyQuestion()">Next question</button>'
        : '') +
      '<button type="button" class="lms-btn lms-btn-secondary" onclick="toggleDeficiencyTutor()">Ask Tutor</button>' +
      '<button type="button" class="lms-btn lms-btn-secondary" onclick="pauseDeficiencyChat()">Pause &amp; Exit</button>' +
      '<button type="button" class="lms-btn lms-btn-ghost lms-sound-toggle" title="Sound cues" onclick="lmsToggleDeficiencySound(this)">' +
      (lmsFeedback.soundEnabled() ? '🔊' : '🔇') + '</button></div>' +
      tutorSection;

    if (defState.tutorOpen) {
      bindDeficiencyTutorInput();
      scrollDeficiencyChatToBottom();
    }

    typeset(body);
  }

  window.openDeficiencyChat = async function (forceNew, mode) {
    ensureDeficiencyModal();
    lmsOpenModal('lmsDeficiencyModal');
    defState = { sessionId: null, selectedOption: null, tutorOpen: false, tutorHistory: [], tutorLoading: false };
    var body = document.getElementById('lmsDeficiencyBody');
    var isChallenge = mode === 'enrichment';
    body.innerHTML = '<div class="lms-spinner"></div><p class="lms-status" style="text-align:center">' +
      (isChallenge ? 'Preparing your challenge questions...' : 'Preparing your personalized questions...') + '</p>';
    try {
      var data = await lmsApi('/api/lms/deficiency/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ force_new: forceNew === true, mode: isChallenge ? 'enrichment' : 'practice' })
      });
      defState.sessionId = data.session_id;
      if (data.tutor_messages && data.tutor_messages.length) {
        defState.tutorHistory = data.tutor_messages.slice();
        defState.tutorOpen = true;
      }
      if (data.current_index > 0 || data.status === 'paused' || data.resumed) {
        lmsShowToast('Resuming where you left off', 'success');
      }
      renderDeficiencyView(data);
    } catch (err) {
      body.innerHTML = '<p class="lms-error">' + escapeHtml(err.message) + '</p>';
    }
  };

  window.closeDeficiencyChat = async function () {
    var last = window._lmsDeficiencyLastState;
    if (defState.sessionId && last && !last.completed) {
      try {
        await lmsApi('/api/lms/deficiency/sessions/' + defState.sessionId + '/pause', { method: 'POST' });
      } catch (e) { /* ignore */ }
    }
    lmsCloseModal('lmsDeficiencyModal');
    defState = { sessionId: null, selectedOption: null, tutorOpen: false, tutorHistory: [], tutorLoading: false };
  };

  window.selectDeficiencyOption = function (idx) {
    defState.selectedOption = idx;
    defState.lastWrongIndex = null;
    renderDeficiencyView(window._lmsDeficiencyLastState);
  };

  window.lmsToggleDeficiencySound = function (btn) {
    var on = lmsFeedback.toggleSound();
    if (btn) btn.textContent = on ? '🔊' : '🔇';
    if (on) lmsFeedback.cue('success');
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
      var chosen = defState.selectedOption;
      defState.selectedOption = null;
      if (!data.tutor_messages || !data.tutor_messages.length) {
        defState.tutorHistory = [];
      } else {
        defState.tutorHistory = data.tutor_messages.slice();
      }
      defState.tutorLoading = false;
      if (data.last_answer && !data.last_answer.correct) {
        defState.streak = 0;
        defState.lastWrongIndex = chosen;
        defState.tutorOpen = true;
        lmsFeedback.cue('error');
        lmsShowToast('Not quite — take another look, or ask the tutor for a hint.', 'error');
      } else {
        defState.lastWrongIndex = null;
        defState.tutorOpen = false;
        defState.tutorHistory = [];
        if (data.last_answer && data.last_answer.correct) {
          defState.streak = (defState.streak || 0) + 1;
          lmsFeedback.celebrate(lmsFeedback.praise(defState.streak));
        }
      }
      renderDeficiencyView(data);
      if (data.last_answer && !data.last_answer.correct && chosen != null) {
        var wrongBtn = document.querySelectorAll('#lmsDeficiencyBody .lms-quiz-option')[chosen];
        if (wrongBtn) {
          wrongBtn.classList.add('lms-shake');
          setTimeout(function () { wrongBtn.classList.remove('lms-shake'); }, 500);
        }
      }
    } catch (err) {
      if (body) body.innerHTML = '<p class="lms-error">' + escapeHtml(err.message) + '</p>';
    }
  };

  window.advanceDeficiencyQuestion = async function () {
    if (!defState.sessionId) return;
    var body = document.getElementById('lmsDeficiencyBody');
    try {
      var data = await lmsApi('/api/lms/deficiency/sessions/' + defState.sessionId + '/advance', {
        method: 'POST'
      });
      defState.selectedOption = null;
      defState.lastWrongIndex = null;
      defState.tutorOpen = false;
      defState.tutorHistory = [];
      defState.tutorLoading = false;
      window._lmsDeficiencyLastState = data;
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
    defState._lastTutorMsg = msg;
    renderDeficiencyView(window._lmsDeficiencyLastState);
    scrollDeficiencyChatToBottom();
    try {
      var data = await lmsApi('/api/lms/deficiency/sessions/' + defState.sessionId + '/explain', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg })
      });
      var reply = data.reply || 'No response';
      // The server never throws for an LLM hiccup - it returns the text
      // "Tutor error: ..." / "Tutor unavailable: ...". Treat that as a
      // retryable failure, not a normal tutor turn.
      if (/^Tutor (error|unavailable)[:\s]/i.test(reply)) {
        throw new Error(reply);
      }
      defState.tutorHistory.push({
        role: 'bot',
        text: reply,
        levelLabel: data.assist_level_label || ''
      });
      defState._lastTutorMsg = null;
      if (window._lmsDeficiencyLastState) {
        window._lmsDeficiencyLastState.tutor_assist_level = data.next_assist_level;
        window._lmsDeficiencyLastState.tutor_assist_label = data.next_assist_level_label;
      }
    } catch (err) {
      // Drop the unanswered question from history and let them retry with
      // one tap - don't leave a dead "Error:" bubble that never recovers.
      if (defState.tutorHistory.length && defState.tutorHistory[defState.tutorHistory.length - 1].role === 'user') {
        defState.tutorHistory.pop();
      }
      defState.tutorHistory.push({
        role: 'bot',
        text: 'The tutor didn\'t answer that time. Tap **Try again** below.',
        retry: true
      });
    } finally {
      defState.tutorLoading = false;
      renderDeficiencyView(window._lmsDeficiencyLastState);
      scrollDeficiencyChatToBottom();
    }
  };

  window.retryDeficiencyTutorMessage = function () {
    if (defState.tutorLoading || !defState._lastTutorMsg) return;
    // remove the retry notice
    if (defState.tutorHistory.length && defState.tutorHistory[defState.tutorHistory.length - 1].retry) {
      defState.tutorHistory.pop();
    }
    var input = document.getElementById('lmsDeficiencyTutorInput');
    if (input) input.value = defState._lastTutorMsg;
    sendDeficiencyTutorMessage();
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
