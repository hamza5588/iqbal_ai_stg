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
    if (opt && typeof opt.render === 'string' && opt.render) {
      return fmtText(opt.render, true, true);
    }
    var raw = (typeof window.lmsOptionText === 'function')
      ? window.lmsOptionText(opt)
      : ((opt && (opt.text || opt.latex)) || '');
    return fmtText(raw, true, true);
  }
  function fmtQuestion(q) {
    if (q && typeof q.question_render === 'string' && q.question_render) {
      return fmtText(q.question_render, false, true);
    }
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
    tutorLoading: false,
    lastCorrectIndex: null,
    solvedReview: false,
    _modeBStarted: false,
    _modeBForIndex: null
  };

  function isSolvedReviewState(data) {
    if (!data) return false;
    if (data.last_answer && data.last_answer.solved) return true;
    if (data.current_question && data.current_question.solved_review) return true;
    return !!defState.solvedReview;
  }

  /* The student dashboard hosts Learning Chat as a page view
     (#lmsDeficiencyModal[data-inline]) with sdOpen/CloseLearningChatView. */
  function isInlineDef() {
    var el = document.getElementById('lmsDeficiencyModal');
    return !!(el && el.hasAttribute('data-inline'));
  }
  function openDefSurface() {
    if (isInlineDef() && typeof window.sdOpenLearningChatView === 'function') window.sdOpenLearningChatView();
    else lmsOpenModal('lmsDeficiencyModal');
  }
  function closeDefSurface() {
    if (isInlineDef() && typeof window.sdCloseLearningChatView === 'function') window.sdCloseLearningChatView();
    else lmsCloseModal('lmsDeficiencyModal');
  }

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
    var label = defState._tutorReconnecting ? 'Connection dropped - reconnecting...' : 'Thinking...';
    return '<div class="sd-ai-bubble" id="lmsDeficiencyTyping">' +
      '<div class="sd-bot-ic">AI</div>' +
      '<div class="sd-msg"><span class="sd-typing"><span></span><span></span><span></span></span>' +
      '<span class="sd-hint-line" style="margin-left:8px;">' + escapeHtml(label) + '</span></div></div>';
  }

  // [label shown on the chip, text sent to the tutor]
  var TUTOR_SUGGESTED_PROMPTS = [
    ['Simpler words', 'Explain this in simpler words'],
    ['Break into steps', 'Break it into small steps'],
    ['Give a hint', 'Give me a hint'],
    ['Show example', 'Show me a similar example for this question'],
    ['I don\'t understand', 'I still don\'t understand']
  ];
  var TUTOR_SOLVED_PROMPTS = [
    ['Explain again', 'Explain why my answer is correct'],
    ['Why not others?', 'Why are the other options wrong?'],
    ['Key rule', 'What key rule did I use here?']
  ];
  var TUTOR_GREETING = 'Tell me what you think about this question. I\'ll respond to your idea and help you work it out.';
  var MODE_B_KICKOFF = 'Why is my answer correct?';

  function renderTutorMessages() {
    var html = defState.tutorHistory.map(function (m, i) {
      var role = m.role === 'user' ? 'user' : 'bot';
      var extra = '';
      if (role === 'bot' && m.retry) {
        extra = '<div class="sd-msg-tools"><button type="button" onclick="retryDeficiencyTutorMessage()">Try again</button></div>';
      } else if (role === 'bot') {
        extra = '<div class="sd-msg-tools"><button type="button" title="Copy" onclick="lmsCopyTutorMessage(' + i + ',this)">Copy</button></div>';
      }
      return '<div class="sd-ai-bubble' + (role === 'user' ? ' user' : '') + '">' +
        '<div class="sd-bot-ic">' + (role === 'user' ? 'You' : 'AI') + '</div>' +
        '<div class="sd-msg">' +
        (role === 'user' ? escapeHtml(m.text) : fmtText(m.text || '')) + extra + '</div></div>';
    }).join('');
    if (!defState.tutorHistory.length) {
      html = '<div class="sd-ai-bubble"><div class="sd-bot-ic">AI</div><div class="sd-msg">' + escapeHtml(TUTOR_GREETING) + '</div></div>';
    }
    if (defState.tutorLoading) {
      html += renderTypingIndicator();
    }
    return html;
  }

  function renderTutorSuggestedPrompts() {
    var prompts = isSolvedReviewState(window._lmsDeficiencyLastState)
      ? TUTOR_SOLVED_PROMPTS
      : TUTOR_SUGGESTED_PROMPTS;
    return '<div class="sd-chip-row">' +
      prompts.map(function (p) {
        return '<button type="button" class="sd-chip"' + (defState.tutorLoading ? ' disabled' : '') +
          ' onclick="sendDeficiencySuggestedPrompt(' + JSON.stringify(p[1]).replace(/"/g, '&quot;') + ')">' + escapeHtml(p[0]) + '</button>';
      }).join('') + '</div>';
  }

  window.lmsCopyTutorMessage = function (idx, btn) {
    var m = defState.tutorHistory[idx];
    if (!m) return;
    if (typeof window.lmsCopyToClipboard === 'function') {
      window.lmsCopyToClipboard(m.text || '', btn);
    }
  };

  window.lmsCopyDeficiencyQuestion = function (btn) {
    var data = window._lmsDeficiencyLastState;
    var q = data && data.current_question;
    if (!q) return;
    var lines = [(typeof window.lmsQuestionText === 'function' ? window.lmsQuestionText(q) : (q.question_text || '')).trim()];
    (q.options || []).forEach(function (o, oi) {
      var label = o.label || String.fromCharCode(65 + oi);
      var text = (typeof window.lmsOptionText === 'function' ? window.lmsOptionText(o) : (o.text || o.latex || ''));
      lines.push(label + '. ' + String(text).trim());
    });
    if (typeof window.lmsCopyToClipboard === 'function') {
      window.lmsCopyToClipboard(lines.join('\n'), btn);
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
    // The tutor panel is always visible now; only pull focus once the
    // student is actually using it (asked something / answered wrong).
    if (!defState.tutorLoading && defState.tutorOpen) {
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
        '<div class="sd-done-card">' +
        '<div class="sd-done-emoji">' + (aced ? '🏆' : '🎉') + '</div>' +
        '<h3>' + (aced ? 'Perfect run!' : 'Great effort!') + '</h3>' +
        '<p class="sd-desc">You got ' + got + ' / ' + tot + ' right.</p>' +
        '<button type="button" class="sd-btn sd-btn-primary" style="margin-top:14px;" onclick="closeDeficiencyChat()">Back to Learning Path</button></div>';
      if (!defState._celebratedDone) {
        defState._celebratedDone = true;
        try { lmsFeedback.celebrate(aced ? 'Perfect run!' : 'Nice work!'); } catch (e) { /* ignore */ }
      }
      if (typeof loadLmsStudentDashboard === 'function') loadLmsStudentDashboard();
      return;
    }

    var q = data.current_question;
    var solved = isSolvedReviewState(data);
    var pct = data.total_questions
      ? Math.round(100 * (data.current_index + 1) / data.total_questions)
      : 0;
    var opts = (q.options || []).map(function (o, oi) {
      var cls = 'sd-q-option lms-quiz-option';
      if (defState.selectedOption === oi) cls += ' selected';
      if (defState.lastWrongIndex === oi) cls += ' lms-opt-wrong';
      if (solved && defState.lastCorrectIndex === oi) cls += ' selected lms-opt-correct';
      var click = solved ? '' : ' onclick="selectDeficiencyOption(' + oi + ')"';
      var disabled = solved ? ' disabled' : '';
      return '<button type="button" class="' + cls + '"' + click + disabled + '>' +
        '<span class="sd-radio"></span>' +
        '<span class="sd-opt-label lms-quiz-option-label">' + escapeHtml(o.label || String.fromCharCode(65 + oi)) + '.</span>' +
        '<span class="sd-opt-body lms-quiz-option-body">' + fmtOption(o) + '</span></button>';
    }).join('');

    var loading = defState.tutorLoading ? ' disabled' : '';
    var tutorPlaceholder = solved
      ? 'Ask why this is right, or why another option fails...'
      : 'I\'m stuck on this step... (Enter to send)';
    var tutorSection =
      '<div id="lmsDeficiencyTutorMessages" class="sd-chat-scroll">' + renderTutorMessages() + '</div>' +
      renderTutorSuggestedPrompts() +
      '<div class="sd-chat-inputbar">' +
      '<div class="sd-chat-field">' +
      '<textarea id="lmsDeficiencyTutorInput" rows="1" placeholder="' + tutorPlaceholder + '"' + loading + '></textarea>' +
      '<button type="button" class="sd-field-ic bulb" title="Need more help" aria-label="Need more help" onclick="requestDeficiencyMoreHelp()"' + loading + '><i class="far fa-lightbulb"></i></button>' +
      '</div>' +
      '<button type="button" id="lmsDeficiencyTutorSend" class="sd-send-btn"' + loading + ' onclick="sendDeficiencyTutorMessage()">Send</button></div>' +
      '<p class="sd-hint-line">Press Enter to send · Shift+Enter for new line</p>';

    var showNext = solved || (data.last_answer && data.last_answer.correct === false);
    body.innerHTML =
      '<div class="sd-split-body">' +
      '<div class="sd-split-left">' +
      '<div class="sd-track"><div class="sd-fill" style="width:' + pct + '%"></div></div>' +
      '<p class="sd-desc">Question ' + (data.current_index + 1) + ' of ' + data.total_questions +
      (q.topic_name ? ' &nbsp;•&nbsp; <b style="color:var(--sd-blue-700);">' + escapeHtml(q.topic_name) + '</b>' : '') +
      (q.subtopic_name ? ' &rsaquo; <b>' + escapeHtml(q.subtopic_name) + '</b>' : '') + '</p>' +
      (data.refreshed_after_assessment
        ? '<p class="sd-desc" style="font-size:12.5px;color:var(--sd-blue-700);"><i class="fas fa-sync-alt"></i> Updated after your latest quiz: questions on subtopics you have now mastered were removed. Your earlier answers are kept.</p>'
        : '') +
      (data.has_pdf
        ? '<p class="sd-desc" style="font-size:12.5px;">AI questions based on the teacher target PDF - weak area: ' + escapeHtml((q && (q.subtopic_name || q.topic_name)) || '') + '</p>'
        : '<p class="sd-desc" style="font-size:12.5px;color:#991b1b;">Teacher has not uploaded target content PDF yet.</p>') +
      (solved
        ? '<p class="sd-desc" style="font-size:12.5px;color:var(--sd-blue-700);"><i class="fas fa-check-circle"></i> Correct — review the tutor explanation, then tap Next.</p>'
        : '') +
      '<div class="sd-q-text lms-quiz-stem">' + fmtQuestion(q) + '</div>' +
      opts +
      '<div class="sd-q-actions-row">' +
      (solved
        ? ''
        : '<button type="button" class="sd-btn sd-btn-primary sd-btn-sm" onclick="submitDeficiencyAnswer()"' +
          (defState.selectedOption === null ? ' disabled' : '') + '>Submit Answer</button>') +
      (showNext
        ? '<button type="button" class="sd-btn ' + (solved ? 'sd-btn-primary' : 'sd-btn-outline') +
          ' sd-btn-sm" onclick="advanceDeficiencyQuestion()">' +
          (solved ? 'Next question' : 'Next question') + '</button>'
        : '') +
      '<button type="button" class="sd-btn sd-btn-outline sd-btn-sm" onclick="pauseDeficiencyChat()">Pause &amp; Exit</button>' +
      '<button type="button" class="sd-icon-btn" title="Copy question text" onclick="lmsCopyDeficiencyQuestion(this)"><i class="far fa-clipboard"></i> Copy</button>' +
      // --lms-font-scale is a live CSS custom property - text resizes
      // immediately, no re-render needed.
      '<button type="button" class="sd-icon-btn lms-font-btn" title="Smaller text" onclick="lmsStepFont(-1)">A-</button>' +
      '<button type="button" class="sd-icon-btn lms-font-btn" title="Larger text" onclick="lmsStepFont(1)">A+</button>' +
      '<button type="button" class="sd-icon-btn lms-sound-toggle" title="Sound cues" onclick="lmsToggleDeficiencySound(this)">' +
      (lmsFeedback.soundEnabled() ? '🔊' : '🔇') + '</button></div>' +
      '</div>' +
      '<div class="sd-split-right" data-tutor-usage="learning-path">' + tutorSection + '</div>' +
      '</div>';

    bindDeficiencyTutorInput();
    scrollDeficiencyChatToBottom();

    typeset(body);
  }

  // topicId (practice only): the chat for that one weak area - each weak topic has its own.
  window.openDeficiencyChat = async function (forceNew, mode, topicId) {
    ensureDeficiencyModal();
    openDefSurface();
    defState = {
      sessionId: null, selectedOption: null, tutorOpen: false, tutorHistory: [], tutorLoading: false,
      lastCorrectIndex: null, solvedReview: false, _modeBStarted: false, _modeBForIndex: null
    };
    var body = document.getElementById('lmsDeficiencyBody');
    var isChallenge = mode === 'enrichment';
    body.innerHTML = '<div class="lms-spinner"></div><p class="lms-status" style="text-align:center">' +
      (isChallenge ? 'Preparing your challenge questions...' : 'Preparing your personalized questions...') + '</p>';
    try {
      var data = await lmsApi('/api/lms/deficiency/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ force_new: forceNew === true, mode: isChallenge ? 'enrichment' : 'practice',
          topic_id: (!isChallenge && topicId) ? Number(topicId) : null })
      });
      defState.sessionId = data.session_id;
      window._lmsDeficiencyLastState = data;
      defState.solvedReview = !!(data.current_question && data.current_question.solved_review);
      if (data.tutor_messages && data.tutor_messages.length) {
        defState.tutorHistory = data.tutor_messages.slice();
        defState.tutorOpen = true;
        if (defState.solvedReview) {
          defState._modeBStarted = true;
          defState._modeBForIndex = data.current_index;
        }
      }
      if (data.current_index > 0 || data.status === 'paused' || data.resumed) {
        lmsShowToast('Resuming where you left off', 'success');
      }
      renderDeficiencyView(data);
      if (defState.solvedReview && !defState._modeBStarted) {
        triggerModeBExplain(data);
      }
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
    closeDefSurface();
    defState = {
      sessionId: null, selectedOption: null, tutorOpen: false, tutorHistory: [], tutorLoading: false,
      lastCorrectIndex: null, solvedReview: false, _modeBStarted: false, _modeBForIndex: null
    };
  };

  window.selectDeficiencyOption = function (idx) {
    if (isSolvedReviewState(window._lmsDeficiencyLastState)) return;
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
    if (isSolvedReviewState(window._lmsDeficiencyLastState)) return;
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
        defState.lastCorrectIndex = null;
        defState.solvedReview = false;
        defState.tutorOpen = true;
        lmsFeedback.cue('error');
        lmsShowToast('Not quite — take another look, or ask the tutor for a hint.', 'error');
        renderDeficiencyView(data);
        if (chosen != null) {
          var wrongBtn = document.querySelectorAll('#lmsDeficiencyBody .lms-quiz-option')[chosen];
          if (wrongBtn) {
            wrongBtn.classList.add('lms-shake');
            setTimeout(function () { wrongBtn.classList.remove('lms-shake'); }, 500);
          }
        }
      } else if (data.last_answer && data.last_answer.correct) {
        defState.lastWrongIndex = null;
        defState.lastCorrectIndex = chosen;
        defState.solvedReview = true;
        defState.tutorOpen = true;
        defState._modeBStarted = false;
        defState.streak = (defState.streak || 0) + 1;
        lmsFeedback.celebrate(lmsFeedback.praise(defState.streak));
        renderDeficiencyView(data);
        triggerModeBExplain(data);
      } else {
        renderDeficiencyView(data);
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
      defState.lastCorrectIndex = null;
      defState.solvedReview = false;
      defState._modeBStarted = false;
      defState._modeBForIndex = null;
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
        text: TUTOR_GREETING
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

  var TUTOR_AUTO_RETRIES = 2;
  var TUTOR_RETRY_DELAY_MS = 1500;

  function _sleep(ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); }

  async function _requestTutorReply(msg) {
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
    return data;
  }

  async function _deliverTutorMessage(msg) {
    if (defState.tutorLoading || !msg || !defState.sessionId) return;
    defState.tutorOpen = true;
    defState.tutorHistory.push({ role: 'user', text: msg });
    defState.tutorLoading = true;
    defState._lastTutorMsg = msg;
    defState._tutorReconnecting = false;
    renderDeficiencyView(window._lmsDeficiencyLastState);
    scrollDeficiencyChatToBottom();

    // The connection can drop mid-chat (DIL feedback: "tutor should
    // reconnect in the background"). Retry a couple of times, quietly,
    // before asking the student to tap anything.
    var data = null;
    for (var attempt = 0; attempt <= TUTOR_AUTO_RETRIES; attempt++) {
      try {
        data = await _requestTutorReply(msg);
        break;
      } catch (err) {
        if (attempt < TUTOR_AUTO_RETRIES) {
          defState._tutorReconnecting = true;
          renderDeficiencyView(window._lmsDeficiencyLastState);
          await _sleep(TUTOR_RETRY_DELAY_MS * (attempt + 1));
        }
      }
    }
    defState._tutorReconnecting = false;

    if (data) {
      defState.tutorHistory.push({
        role: 'bot',
        text: data.reply || 'No response'
      });
      defState._lastTutorMsg = null;
    } else {
      // Drop the unanswered question from history and let them retry with
      // one tap - don't leave a dead "Error:" bubble that never recovers.
      // A reconnect ("online" event) also auto-resends this automatically.
      if (defState.tutorHistory.length && defState.tutorHistory[defState.tutorHistory.length - 1].role === 'user') {
        defState.tutorHistory.pop();
      }
      defState.tutorHistory.push({
        role: 'bot',
        text: 'The tutor lost connection. It will keep trying to reconnect, or tap **Try again**.',
        retry: true
      });
    }
    defState.tutorLoading = false;
    renderDeficiencyView(window._lmsDeficiencyLastState);
    scrollDeficiencyChatToBottom();
  }

  function triggerModeBExplain(data) {
    if (!data || !isSolvedReviewState(data)) return;
    if (defState._modeBStarted && defState._modeBForIndex === data.current_index) return;
    if (defState.tutorLoading) return;
    defState._modeBStarted = true;
    defState._modeBForIndex = data.current_index;
    _deliverTutorMessage(MODE_B_KICKOFF);
  }

  window.sendDeficiencyTutorMessage = async function () {
    if (defState.tutorLoading) return;
    var input = document.getElementById('lmsDeficiencyTutorInput');
    var msg = (input && input.value || '').trim();
    if (!msg || !defState.sessionId) return;
    if (input) input.value = '';
    await _deliverTutorMessage(msg);
  };

  // Background reconnect: as soon as the browser reports the connection is
  // back, auto-resend a tutor message that was left queued after a failure
  // - the student should not have to notice or tap anything.
  if (typeof window !== 'undefined' && window.addEventListener) {
    window.addEventListener('online', function () {
      if (!defState.tutorLoading && defState._lastTutorMsg && defState.sessionId) {
        retryDeficiencyTutorMessage();
      }
    });
  }

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
