/**
 * Consultant Chatbot Widget
 * =========================
 * Self-contained floating widget providing:
 *   1. Text chat (Groq / admin-selected LLM, with optional RAG context)
 *   2. Document (PDF) upload linked to the current session
 *   3. Real-time voice conversation via OpenAI Realtime API (WebRTC)
 *
 * UX features:
 *   - Page backdrop dims when panel is open so user focuses on the chatbot
 *   - Input + send button are locked during PDF processing; progress bar shown
 *
 * All API calls go to /api/consultant/* (never to existing chatbot endpoints).
 * Exposes  window.consultantWidget  for the inline onclick attributes in HTML.
 */

(function (global) {
  'use strict';

  /* ── Internal state ───────────────────────────────────────────────────── */

  const S = {
    isOpen:          false,
    uploading:       false,   // true while PDF is being processed
    sessionId:       null,
    threadId:        null,
    conversationId:  null,
    // Voice
    voiceActive:     false,
    voiceMuted:      false,
    peerConn:        null,
    dataChannel:     null,
    localStream:     null,
    audioEl:         null,
    // Transcript streaming
    pendingAsstDiv:  null,
    pendingAsstText: '',
    // Tool calling (search_document)
    pendingToolCall: null,   // { call_id, name, argsRaw }
  };

  /* ── Build widget DOM ─────────────────────────────────────────────────── */

  function buildWidget() {

    // ── Backdrop (dims page behind the panel) ──────────────────────────
    const backdrop = document.createElement('div');
    backdrop.id = 'consultant-backdrop';
    backdrop.addEventListener('click', toggle);
    document.body.appendChild(backdrop);

    // ── Floating trigger button ────────────────────────────────────────
    const btn = document.createElement('button');
    btn.id = 'consultant-btn';
    btn.setAttribute('aria-label', 'Open AI Consultant');
    btn.innerHTML =
      '<div class="consultant-icon-inner">' +
        '<svg xmlns="http://www.w3.org/2000/svg" width="17" height="17" viewBox="0 0 24 24"' +
          ' fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
          '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>' +
        '</svg>' +
        '<span>Consultant</span>' +
      '</div>';
    btn.addEventListener('click', toggle);
    document.body.appendChild(btn);

    // ── Sliding panel ──────────────────────────────────────────────────
    const panel = document.createElement('div');
    panel.id = 'consultant-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'AI Consultant Panel');
    panel.innerHTML = [

      // Header
      '<div class="consultant-header">',
        '<div class="consultant-header-title">',
          '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24"',
              ' fill="none" stroke="currentColor" stroke-width="2">',
            '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>',
          '</svg>',
          'AI Consultant',
          '<span class="consultant-header-badge">AI</span>',
        '</div>',
        '<button class="consultant-close-btn" aria-label="Close" onclick="consultantWidget.toggle()">&times;</button>',
      '</div>',

      // Tabs — Voice only (chat is always-on below)
      '<div class="consultant-tabs" role="tablist">',
        '<button class="consultant-tab" role="tab" data-tab="voice"',
            ' onclick="consultantWidget.switchTab(\'voice\')">',
          '<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24"',
              ' fill="none" stroke="currentColor" stroke-width="2">',
            '<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path>',
            '<path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>',
          '</svg>',
          ' Voice',
        '</button>',
      '</div>',

      // ── CHAT pane (always visible) ──────────────────────────────────
      '<div class="consultant-tab-pane" id="c-pane-chat" role="region">',

        // Doc upload bar
        '<div class="consultant-doc-bar">',
          '<span id="consultant-doc-name">No document</span>',
          '<label for="c-file-input" class="consultant-upload-label" id="c-upload-label" title="Upload PDF">',
            '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24"',
                ' fill="none" stroke="currentColor" stroke-width="2">',
              '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>',
              '<polyline points="17 8 12 3 7 8"></polyline>',
              '<line x1="12" y1="3" x2="12" y2="15"></line>',
            '</svg>',
            ' PDF',
          '</label>',
          '<input type="file" id="c-file-input" accept=".pdf" style="display:none"',
              ' onchange="consultantWidget.uploadDoc(this)">',
        '</div>',

        // Upload progress bar (hidden until processing)
        '<div id="consultant-upload-progress">',
          '<div class="consultant-progress-label">',
            '<span class="c-spinner"></span>',
            '<span id="c-progress-text">Processing document…</span>',
          '</div>',
          '<div class="consultant-progress-track">',
            '<div class="consultant-progress-fill"></div>',
          '</div>',
        '</div>',

        // Messages
        '<div class="consultant-messages" id="c-messages">',
          '<div class="consultant-msg bot">Hello! I\'m your AI consultant. Ask me anything, or upload a PDF for document-grounded answers.</div>',
        '</div>',

        // Input row
        '<div class="consultant-input-row">',
          '<textarea id="consultant-text-input" rows="1" placeholder="Type a message…"',
              ' onkeydown="consultantWidget.onKey(event)"></textarea>',
          '<button class="consultant-send-btn" id="c-send-btn" onclick="consultantWidget.send()" aria-label="Send">',
            '<svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24"',
                ' fill="none" stroke="currentColor" stroke-width="2">',
              '<line x1="22" y1="2" x2="11" y2="13"></line>',
              '<polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>',
            '</svg>',
          '</button>',
        '</div>',

      '</div>',  // end chat pane

      // ── VOICE pane ─────────────────────────────────────────────────
      '<div class="consultant-tab-pane" id="c-pane-voice" role="tabpanel" style="display:none">',
        '<div class="consultant-voice-pane">',

          '<div class="consultant-voice-status" id="c-voice-status">Voice assistant ready</div>',

          '<div class="consultant-voice-ring" id="c-voice-ring">',
            '<div class="consultant-voice-controls">',
              '<button class="c-voice-btn start" id="c-btn-start" onclick="consultantWidget.startVoice()">',
                '<svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24"',
                    ' fill="none" stroke="currentColor" stroke-width="2">',
                  '<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path>',
                  '<path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>',
                  '<line x1="12" y1="19" x2="12" y2="23"></line>',
                  '<line x1="8" y1="23" x2="16" y2="23"></line>',
                '</svg>',
                ' Start Voice',
              '</button>',
              '<button class="c-voice-btn mute" id="c-btn-mute" onclick="consultantWidget.toggleMute()" style="display:none">',
                '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24"',
                    ' fill="none" stroke="currentColor" stroke-width="2">',
                  '<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path>',
                  '<path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>',
                '</svg>',
                ' Mute',
              '</button>',
              '<button class="c-voice-btn stop" id="c-btn-stop" onclick="consultantWidget.stopVoice()" style="display:none">',
                '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24"',
                    ' fill="none" stroke="currentColor" stroke-width="2">',
                  '<rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>',
                '</svg>',
                ' Stop',
              '</button>',
            '</div>',
          '</div>',

          '<div class="consultant-voice-transcript" id="c-transcript">',
            '<div class="transcript-placeholder">Start voice to begin…</div>',
          '</div>',

        '</div>',
      '</div>',  // end voice pane

    ].join('');

    document.body.appendChild(panel);
  }

  /* ── Toggle panel + backdrop ──────────────────────────────────────────── */

  function toggle() {
    S.isOpen = !S.isOpen;
    const panel    = document.getElementById('consultant-panel');
    const backdrop = document.getElementById('consultant-backdrop');

    if (S.isOpen) {
      panel.classList.add('open');
      if (backdrop) backdrop.classList.add('visible');
      // Focus input after animation
      setTimeout(function () {
        var ta = document.getElementById('consultant-text-input');
        if (ta && !ta.disabled) ta.focus();
      }, 320);
    } else {
      panel.classList.remove('open');
      if (backdrop) backdrop.classList.remove('visible');
    }
  }

  /* ── Tab switching (Voice only — chat pane is always visible) ─────────── */

  function switchTab(tab) {
    var voiceTab  = document.querySelector('.consultant-tab[data-tab="voice"]');
    var voicePane = document.getElementById('c-pane-voice');
    var isActive  = voiceTab && voiceTab.classList.contains('active');

    if (tab === 'voice' && isActive) {
      voiceTab.classList.remove('active');
      if (voicePane) voicePane.style.display = 'none';
    } else if (tab === 'voice') {
      if (voiceTab) voiceTab.classList.add('active');
      if (voicePane) voicePane.style.display = '';
    }
  }

  /* ── PDF upload lock helpers ──────────────────────────────────────────── */

  function _lockInput(labelText) {
    S.uploading = true;
    var ta        = document.getElementById('consultant-text-input');
    var sendBtn   = document.getElementById('c-send-btn');
    var uploadLbl = document.getElementById('c-upload-label');
    var progressEl = document.getElementById('consultant-upload-progress');
    var progressTxt = document.getElementById('c-progress-text');

    if (ta)        { ta.disabled = true; ta.placeholder = 'Waiting for document…'; }
    if (sendBtn)   { sendBtn.disabled = true; }
    if (uploadLbl) { uploadLbl.classList.add('disabled'); }
    if (progressEl){ progressEl.classList.add('active'); }
    if (progressTxt && labelText) { progressTxt.textContent = labelText; }
  }

  function _unlockInput() {
    S.uploading = false;
    var ta        = document.getElementById('consultant-text-input');
    var sendBtn   = document.getElementById('c-send-btn');
    var uploadLbl = document.getElementById('c-upload-label');
    var progressEl = document.getElementById('consultant-upload-progress');

    if (ta)        { ta.disabled = false; ta.placeholder = 'Type a message…'; ta.focus(); }
    if (sendBtn)   { sendBtn.disabled = false; }
    if (uploadLbl) { uploadLbl.classList.remove('disabled'); }
    if (progressEl){ progressEl.classList.remove('active'); }
  }

  /* ── Doc upload ───────────────────────────────────────────────────────── */

  async function uploadDoc(input) {
    const file = input.files && input.files[0];
    if (!file) return;

    const docNameEl = document.getElementById('consultant-doc-name');
    docNameEl.textContent = file.name;

    if (!S.sessionId) {
      S.sessionId = Math.random().toString(36).slice(2, 10);
    }

    const fd = new FormData();
    fd.append('file', file);
    fd.append('session_id', S.sessionId);

    // Lock input and show progress bar
    _lockInput('Uploading ' + file.name + '…');

    try {
      const resp = await fetch('/api/consultant/ingest', { method: 'POST', body: fd });
      const data = await resp.json();

      if (data.success) {
        S.threadId = data.thread_id;
        docNameEl.textContent = '\uD83D\uDCC4 ' + data.filename + ' (' + data.num_pages + ' pages)';
        addMsg(
          'Document ready: **' + data.filename + '** (' + data.num_pages + ' pages).' +
          ' You can now ask document-specific questions.',
          'bot'
        );
      } else {
        docNameEl.textContent = 'No document';
        addMsg('Upload failed: ' + (data.error || 'Unknown error'), 'bot error');
      }
    } catch (err) {
      docNameEl.textContent = 'No document';
      addMsg('Upload error: ' + err.message, 'bot error');
    } finally {
      input.value = '';
      _unlockInput();
    }
  }

  /* ── Text chat ────────────────────────────────────────────────────────── */

  async function send() {
    if (S.uploading) return;

    const ta = document.getElementById('consultant-text-input');
    const msg = (ta.value || '').trim();
    if (!msg) return;

    addMsg(msg, 'user');
    ta.value = '';
    ta.style.height = 'auto';

    const typingId = showTyping();

    try {
      const resp = await fetch('/api/consultant/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message:         msg,
          thread_id:       S.threadId || null,
          conversation_id: S.conversationId || null,
        }),
      });
      const data = await resp.json();
      hideTyping(typingId);

      if (data.success) {
        if (data.conversation_id) S.conversationId = data.conversation_id;
        addMsg(data.message, 'bot');
      } else if (data.requires_login) {
        addMsg('Session expired — please refresh and log in again.', 'bot error');
      } else {
        addMsg('Error: ' + (data.error || 'Unknown error'), 'bot error');
      }
    } catch (err) {
      hideTyping(typingId);
      addMsg('Connection error: ' + err.message, 'bot error');
    }
  }

  function onKey(e) {
    if (S.uploading) { e.preventDefault(); return; }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
      return;
    }
    // Auto-resize textarea
    var ta = document.getElementById('consultant-text-input');
    setTimeout(function () {
      ta.style.height = 'auto';
      ta.style.height = Math.min(ta.scrollHeight, 120) + 'px';
    }, 0);
  }

  /* ── Message helpers ──────────────────────────────────────────────────── */

  function addMsg(text, role) {
    const container = document.getElementById('c-messages');
    const div = document.createElement('div');
    div.className = 'consultant-msg ' + role;
    div.innerHTML = (text || '')
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.*?)\*/g,     '<em>$1</em>')
      .replace(/\n/g,            '<br>');
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    return div;
  }

  function showTyping() {
    const container = document.getElementById('c-messages');
    const div = document.createElement('div');
    div.className = 'consultant-msg bot typing';
    div.innerHTML = '<span></span><span></span><span></span>';
    const id = 'ct-' + Date.now();
    div.id = id;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    return id;
  }

  function hideTyping(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
  }

  /* ── Voice: WebRTC + OpenAI Realtime ─────────────────────────────────── */

  async function startVoice() {
    if (S.voiceActive) return;

    const statusEl = document.getElementById('c-voice-status');
    const startBtn = document.getElementById('c-btn-start');
    const muteBtn  = document.getElementById('c-btn-mute');
    const stopBtn  = document.getElementById('c-btn-stop');
    const ringEl   = document.getElementById('c-voice-ring');

    /* ── Guard: browsers block mediaDevices on plain HTTP (non-localhost) ── */
    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      statusEl.textContent = 'Voice requires HTTPS. See instructions below.';
      statusEl.className = 'consultant-voice-status error';
      // Show inline help
      var helpEl = document.getElementById('c-https-help');
      if (helpEl) helpEl.style.display = '';
      return;
    }

    statusEl.textContent = 'Requesting session…';
    statusEl.className = 'consultant-voice-status';
    startBtn.disabled = true;

    try {
      /* 1 ── Ephemeral token from backend ─────────────────────────────── */
      const sessResp = await fetch('/api/consultant/voice/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thread_id: S.threadId || null }),
      });
      const sessData = await sessResp.json();

      if (!sessData.success || !sessData.client_secret) {
        let errMsg = sessData.error || 'Failed to obtain voice session';
        if (sessData.code === 'OPENAI_KEY_MISSING') {
          errMsg = 'Voice requires an OpenAI API key (not configured in Admin settings).';
        }
        throw new Error(errMsg);
      }

      const ephemeralKey = sessData.client_secret.value;
      const model = sessData.model || 'gpt-4o-mini-realtime-preview-2024-12-17';

      /* 2 ── Audio element ────────────────────────────────────────────── */
      if (!S.audioEl) {
        S.audioEl = new Audio();
        S.audioEl.autoplay = true;
        document.body.appendChild(S.audioEl);
      }

      /* 3 ── RTCPeerConnection ────────────────────────────────────────── */
      const pc = new RTCPeerConnection();
      S.peerConn = pc;

      pc.ontrack = function (e) {
        if (e.streams && e.streams[0]) S.audioEl.srcObject = e.streams[0];
      };

      /* 4 ── Microphone ───────────────────────────────────────────────── */
      statusEl.textContent = 'Accessing microphone…';
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw new Error('Microphone access requires a secure connection (HTTPS). Please access this page over HTTPS.');
      }
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      S.localStream = stream;
      stream.getTracks().forEach(function (t) { pc.addTrack(t, stream); });

      /* 5 ── Data channel ─────────────────────────────────────────────── */
      const dc = pc.createDataChannel('oai-events');
      S.dataChannel = dc;

      dc.addEventListener('message', function (e) {
        try { handleRealtimeEvent(JSON.parse(e.data)); } catch (_) {}
      });

      dc.addEventListener('open', function () {
        S.voiceActive = true;
        statusEl.textContent = 'Connected — speak now';
        statusEl.className = 'consultant-voice-status connected';
        ringEl.classList.add('active');
        startBtn.style.display = 'none';
        muteBtn.style.display  = '';
        stopBtn.style.display  = '';
        addVoiceEntry('system', 'Voice session started. Speak to begin.');
      });

      dc.addEventListener('close', function () {
        if (S.voiceActive) _teardownVoice(statusEl, startBtn, muteBtn, stopBtn, ringEl);
      });

      /* 6 ── SDP offer ────────────────────────────────────────────────── */
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);

      /* 7 ── Exchange SDP with OpenAI ─────────────────────────────────── */
      statusEl.textContent = 'Connecting to voice AI…';
      const sdpResp = await fetch(
        'https://api.openai.com/v1/realtime?model=' + encodeURIComponent(model),
        {
          method: 'POST',
          headers: {
            'Authorization': 'Bearer ' + ephemeralKey,
            'Content-Type':  'application/sdp',
          },
          body: offer.sdp,
        }
      );

      if (!sdpResp.ok) {
        throw new Error('OpenAI Realtime SDP exchange failed (' + sdpResp.status + ')');
      }

      /* 8 ── Set remote description ───────────────────────────────────── */
      const answerSdp = await sdpResp.text();
      await pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });

    } catch (err) {
      console.error('[consultant-widget] startVoice error:', err);
      let msg = 'Error: ' + err.message;
      if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
        msg = 'Microphone access denied. Please allow microphone access and try again.';
      }
      statusEl.textContent = msg;
      statusEl.className = 'consultant-voice-status error';
      startBtn.disabled = false;
      _cleanupVoiceResources();
    }
  }

  function stopVoice() {
    _teardownVoice(
      document.getElementById('c-voice-status'),
      document.getElementById('c-btn-start'),
      document.getElementById('c-btn-mute'),
      document.getElementById('c-btn-stop'),
      document.getElementById('c-voice-ring')
    );
  }

  function _teardownVoice(statusEl, startBtn, muteBtn, stopBtn, ringEl) {
    _cleanupVoiceResources();
    statusEl.textContent = 'Voice stopped.';
    statusEl.className = 'consultant-voice-status';
    startBtn.style.display = '';
    startBtn.disabled = false;
    muteBtn.style.display = 'none';
    stopBtn.style.display = 'none';
    if (ringEl) ringEl.classList.remove('active');
  }

  function _cleanupVoiceResources() {
    S.voiceActive    = false;
    S.voiceMuted     = false;
    if (S.peerConn)    { try { S.peerConn.close(); }   catch (_) {} S.peerConn = null; }
    if (S.localStream) { S.localStream.getTracks().forEach(function (t) { t.stop(); }); S.localStream = null; }
    S.dataChannel     = null;
    S.pendingAsstDiv  = null;
    S.pendingAsstText = '';
    S.pendingToolCall = null;
  }

  function toggleMute() {
    if (!S.localStream) return;
    S.voiceMuted = !S.voiceMuted;
    S.localStream.getAudioTracks().forEach(function (t) { t.enabled = !S.voiceMuted; });
    var btn = document.getElementById('c-btn-mute');
    btn.classList.toggle('muted', S.voiceMuted);
    btn.innerHTML = S.voiceMuted
      ? '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="1" y1="1" x2="23" y2="23"></line><path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V4a3 3 0 0 0-5.94-.6"></path><path d="M17 16.95A7 7 0 0 1 5 12v-2m14 0v2a7 7 0 0 1-.11 1.23"></path></svg> Unmute'
      : '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path><path d="M19 10v2a7 7 0 0 1-14 0v-2"></path></svg> Mute';
  }

  /* ─────────────────────────────────────────────────────────────────────
   * TOOL ROUTING INSTRUCTIONS
   * --------------------------
   * The OpenAI model picks the correct tool automatically based on these
   * rules (enforced by the system prompt sent at session creation):
   *
   *  User asks about…                 → Tool called
   *  ─────────────────────────────────────────────────────────────────
   *  Page content / "what's on page N" → get_page        (page: N)
   *  Headings / sections / TOC         → list_headings   (no args)
   *  Word count / how many words       → count_words     (optional page/range)
   *  General topic / keyword search    → search_document (query: "…")
   *  Arithmetic / calculations         → calculator      (a, b, op)
   * ───────────────────────────────────────────────────────────────── */

  /* ── Consultant console logger ────────────────────────────────────────── */

  var _LOG_PREFIX = '[ConsultantVoice]';

  function _log(level, section, msg, payload) {
    var ts = new Date().toISOString().slice(11, 23); // HH:MM:SS.mmm
    var badge = '%c' + _LOG_PREFIX + ' [' + ts + '] [' + section + ']';
    var styles = {
      info:    'color:#4a9eff;font-weight:bold',
      success: 'color:#22c55e;font-weight:bold',
      warn:    'color:#f59e0b;font-weight:bold',
      error:   'color:#ef4444;font-weight:bold',
      tool:    'color:#a855f7;font-weight:bold',
      result:  'color:#10b981;font-weight:bold',
    };
    var style = styles[level] || styles.info;
    if (payload !== undefined) {
      console[level === 'error' ? 'error' : level === 'warn' ? 'warn' : 'log'](
        badge + ' ' + msg, style, payload
      );
    } else {
      console[level === 'error' ? 'error' : level === 'warn' ? 'warn' : 'log'](
        badge + ' ' + msg, style
      );
    }
  }

  /* ── OpenAI Realtime event handler ───────────────────────────────────── */

  function handleRealtimeEvent(evt) {
    switch (evt.type) {

      /* ── User speech transcription ──────────────────────────────────── */
      case 'conversation.item.input_audio_transcription.completed':
        _log('info', 'TRANSCRIPT', 'User speech transcribed: "' + (evt.transcript || '') + '"');
        addVoiceEntry('user', evt.transcript || '');
        break;

      /* ── Assistant audio transcript streaming ───────────────────────── */
      case 'response.audio_transcript.delta':
        appendVoiceDelta(evt.delta || '');
        break;

      case 'response.audio_transcript.done':
        _log('info', 'TRANSCRIPT', 'Assistant response complete.');
        S.pendingAsstDiv  = null;
        S.pendingAsstText = '';
        break;

      /* ── Tool call: model decided to invoke a tool ───────────────────── */
      case 'response.output_item.added':
        if (evt.item && evt.item.type === 'function_call') {
          S.pendingToolCall = {
            call_id:  evt.item.call_id,
            name:     evt.item.name,
            argsRaw:  '',
          };

          // ── Log which tool was chosen and WHY (routing rule) ──────────
          var toolRouteReason = {
            search_document: 'Model chose search_document → general content query OR "which page is X on?" (result includes [Page N] labels)',
            get_page:        'Model chose get_page        → user mentioned a specific page number (e.g. "page 5")',
            list_headings:   'Model chose list_headings   → user asked about headings/sections/chapters/TOC/structure explicitly',
            count_words:     'Model chose count_words     → user asked about word count / how many words',
            calculator:      'Model chose calculator      → user asked for arithmetic',
          };
          var reason = toolRouteReason[evt.item.name] || ('Model chose ' + evt.item.name);

          _log('tool', 'TOOL SELECTED', '─────────────────────────────────────────');
          _log('tool', 'TOOL SELECTED', reason);
          _log('tool', 'TOOL SELECTED', 'call_id : ' + evt.item.call_id);
          _log('tool', 'TOOL SELECTED', '─────────────────────────────────────────');
          console.groupCollapsed(_LOG_PREFIX + ' [TOOL] Awaiting arguments for: ' + evt.item.name);
        }
        break;

      case 'response.function_call_arguments.delta':
        // Accumulate streaming JSON arguments (silently — logged at .done)
        if (S.pendingToolCall) {
          S.pendingToolCall.argsRaw += (evt.delta || '');
        }
        break;

      case 'response.function_call_arguments.done':
        // Arguments complete — parse, log, execute
        if (S.pendingToolCall) {
          var tc = S.pendingToolCall;
          S.pendingToolCall = null;

          var parsedArgs = {};
          try { parsedArgs = JSON.parse(tc.argsRaw || '{}'); } catch (_) {}

          console.groupEnd(); // close "Awaiting arguments" group
          _log('tool', 'TOOL ARGS', 'Tool: ' + tc.name + '  |  Raw args: ' + tc.argsRaw);
          _log('tool', 'TOOL ARGS', 'Parsed args →', parsedArgs);

          // ── Log the instruction that governs this call ─────────────────
          var toolInstructions = {
            search_document: [
              'INSTRUCTION: search_document',
              '  Triggered by:',
              '    • "which page is X on?" / "where is X in the document?"',
              '    • general content questions: "what does it say about X", "explain X"',
              '  Backend: _retrieve_with_pages() → hybrid_search() → returns text WITH [Page N] labels',
              '  ✔ Result includes source page numbers so model can answer "which page" correctly.',
              '  ✘ Root cause fixed: model no longer guesses page numbers with get_page.',
            ].join('\n'),
            get_page: [
              'INSTRUCTION: get_page',
              '  Triggered by: user says "page N" explicitly (e.g. "what is on page 7")',
              '  NOT triggered by: "which page is X on?" — that uses search_document.',
              '  Backend: get_page_tool(page=N, thread_id) → query_chunks_by_page()',
            ].join('\n'),
            list_headings: [
              'INSTRUCTION: list_headings',
              '  Triggered ONLY when user says heading/section/chapter/topic/outline/TOC/structure.',
              '  "how many" ALONE does not trigger this — the structural keyword must be present.',
              '  ✘ Root cause fixed: "how many is not present" no longer triggers list_headings.',
              '  Backend: list_topics_whole_doc_tool(thread_id) → RAGHeading table',
              '  Result format: "The document has N section heading(s). LIST OF HEADINGS: ..."',
              '  ✘ Root cause fixed: no longer says "126 units" — says "N section heading(s)".',
            ].join('\n'),
            count_words: [
              'INSTRUCTION: count_words',
              '  Triggered by: "how many words", "word count", "how long is the document".',
              '  Backend: count_pdf_words_tool(thread_id, page?, start_page?, end_page?)',
            ].join('\n'),
            calculator: [
              'INSTRUCTION: calculator',
              '  Triggered by: explicit arithmetic (add/subtract/multiply/divide).',
              '  Backend: calculator(first_num, second_num, operation)',
              '  No document access required.',
            ].join('\n'),
          };
          var instr = toolInstructions[tc.name] || ('INSTRUCTION: ' + tc.name + ' (no description)');
          _log('info', 'TOOL INSTRUCTION', instr);

          _executeToolCall(tc.call_id, tc.name, tc.argsRaw);
        }
        break;

      case 'error':
        var errMsg = evt.error && evt.error.message ? evt.error.message : JSON.stringify(evt.error);
        _log('error', 'OPENAI ERROR', errMsg, evt.error);
        addVoiceEntry('error', 'OpenAI error: ' + errMsg);
        break;

      default:
        // Log any unhandled event types at verbose level
        if (evt.type && !evt.type.startsWith('response.audio')) {
          _log('info', 'EVENT', evt.type, evt);
        }
        break;
    }
  }

  /* ── Execute any voice tool call via /api/consultant/tool ────────────── */

  async function _executeToolCall(callId, toolName, argsRaw) {
    var args = {};
    try { args = JSON.parse(argsRaw || '{}'); } catch (_) {}

    var t0 = performance.now();

    // Status labels shown in the transcript UI
    var labels = {
      search_document: 'Searching document…',
      get_page:        'Fetching page ' + (args.page || '') + '…',
      list_headings:   'Listing headings…',
      count_words:     'Counting words…',
      calculator:      'Calculating…',
    };
    var statusLabel = labels[toolName] || ('Running ' + toolName + '…');

    // Update or add the system transcript entry
    var container = document.getElementById('c-transcript');
    var lastSys = container ? container.querySelector('.transcript-entry.system:last-child') : null;
    if (lastSys) {
      var sp = lastSys.querySelector('.transcript-text');
      if (sp) sp.textContent = statusLabel;
    } else {
      addVoiceEntry('system', statusLabel);
    }

    _log('tool', 'TOOL CALL', '══ START ══  ' + toolName);
    _log('tool', 'TOOL CALL', 'POST /api/consultant/tool', {
      tool_name: toolName,
      thread_id: S.threadId || null,
      args:      args,
    });

    var resultText = 'Tool execution failed.';
    var httpStatus = null;

    try {
      var resp = await fetch('/api/consultant/tool', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tool_name: toolName,
          thread_id: S.threadId || null,
          args:      args,
        }),
      });

      httpStatus = resp.status;
      var data = await resp.json();
      var elapsed = (performance.now() - t0).toFixed(0);

      _log('info', 'TOOL CALL', 'HTTP ' + httpStatus + '  (' + elapsed + 'ms)', data);

      if (data.success && data.result) {
        resultText = data.result;
        _log('result', 'TOOL RESULT', '══ RESULT ══  ' + toolName + '  (' + elapsed + 'ms)');
        _log('result', 'TOOL RESULT', resultText);
      } else if (data.error) {
        resultText = 'Error: ' + data.error;
        _log('error', 'TOOL RESULT', 'Tool returned error → ' + data.error);
      }

    } catch (err) {
      var elapsed2 = (performance.now() - t0).toFixed(0);
      resultText = 'Network error: ' + err.message;
      _log('error', 'TOOL CALL', 'Fetch failed (' + elapsed2 + 'ms) → ' + err.message);
    }

    // ── Send result back to OpenAI so the model can speak the answer ──────
    if (S.dataChannel && S.dataChannel.readyState === 'open') {
      var outputPayload = {
        type: 'conversation.item.create',
        item: {
          type:    'function_call_output',
          call_id: callId,
          output:  resultText,
        },
      };
      _log('info', 'DATA CHANNEL', 'Sending function_call_output to OpenAI', outputPayload.item);
      S.dataChannel.send(JSON.stringify(outputPayload));

      _log('info', 'DATA CHANNEL', 'Sending response.create → model will now speak');
      S.dataChannel.send(JSON.stringify({ type: 'response.create' }));
    } else {
      _log('warn', 'DATA CHANNEL', 'Data channel not open — cannot send tool result back to OpenAI');
    }

    _log('tool', 'TOOL CALL', '══ END ══  ' + toolName);
  }

  function addVoiceEntry(role, text) {
    const container = document.getElementById('c-transcript');
    const ph = container.querySelector('.transcript-placeholder');
    if (ph) ph.remove();

    const div = document.createElement('div');
    div.className = 'transcript-entry ' + role;
    const label = role === 'user' ? 'You' : role === 'assistant' ? 'AI' : role === 'system' ? '•' : '!';
    const textSpan = document.createElement('span');
    textSpan.className = 'transcript-text';
    textSpan.textContent = text;
    div.innerHTML = '<span class="transcript-label">' + label + ':</span> ';
    div.appendChild(textSpan);
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;

    if (role === 'assistant') {
      S.pendingAsstDiv  = textSpan;
      S.pendingAsstText = text;
    }
    return div;
  }

  function appendVoiceDelta(delta) {
    if (!S.pendingAsstDiv) {
      addVoiceEntry('assistant', delta);
    } else {
      S.pendingAsstText += delta;
      S.pendingAsstDiv.textContent = S.pendingAsstText;
      const container = document.getElementById('c-transcript');
      container.scrollTop = container.scrollHeight;
    }
  }

  /* ── Initialise ───────────────────────────────────────────────────────── */

  function init() {
    buildWidget();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  /* ── Public API ───────────────────────────────────────────────────────── */

  global.consultantWidget = {
    toggle:     toggle,
    switchTab:  switchTab,
    send:       send,
    onKey:      onKey,
    uploadDoc:  uploadDoc,
    startVoice: startVoice,
    stopVoice:  stopVoice,
    toggleMute: toggleMute,
  };

}(window));
