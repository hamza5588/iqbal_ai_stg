/**
 * Iqbal AI Consultant — Embed Widget (B2B)
 * Text + voice chat for external client websites.
 */
(function (global) {
  'use strict';

  const VISITOR_KEY = 'iqbal_embed_visitor_id';
  const CONV_KEY = 'iqbal_embed_conversation_id';

  const state = {
    apiBase: '', clientKey: '', title: 'AI Consultant', primaryColor: '#05B0FC',
    visitorId: null, conversationId: null, isOpen: false, initialized: false,
    voiceActive: false, voiceConnecting: false, peerConn: null, dataChannel: null,
    localStream: null, audioEl: null,
  };

  function apiUrl(p) { return (state.apiBase || '').replace(/\/$/, '') + p; }
  function headers(json) {
    const h = { 'X-Client-Key': state.clientKey };
    if (json) h['Content-Type'] = 'application/json';
    return h;
  }

  function loadCss() {
    if (!document.querySelector('link[data-iqbal-font]')) {
      const f = document.createElement('link');
      f.rel = 'stylesheet';
      f.href = 'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap';
      f.setAttribute('data-iqbal-font', '1');
      document.head.appendChild(f);
    }
    if (document.querySelector('link[data-iqbal-embed]')) return;
    const l = document.createElement('link');
    l.rel = 'stylesheet';
    l.href = apiUrl('/static/css/consultant-widget.css');
    l.setAttribute('data-iqbal-embed', '1');
    document.head.appendChild(l);
  }

  function loadFormatter() {
    if (global.ConsultantMessageFormatter) {
      return global.ConsultantMessageFormatter.ensureReady();
    }
    return new Promise(function (resolve, reject) {
      const src = apiUrl('/static/js/consultant-message-formatter.js');
      if (document.querySelector('script[src="' + src + '"]')) {
        if (global.ConsultantMessageFormatter) {
          global.ConsultantMessageFormatter.ensureReady().then(resolve).catch(reject);
        } else {
          resolve();
        }
        return;
      }
      const s = document.createElement('script');
      s.src = src;
      s.async = true;
      s.onload = function () {
        if (global.ConsultantMessageFormatter) {
          global.ConsultantMessageFormatter.ensureReady().then(resolve).catch(reject);
        } else {
          resolve();
        }
      };
      s.onerror = reject;
      document.head.appendChild(s);
    });
  }

  function formatMsgHtml(text, role) {
    if (role === 'bot' && global.ConsultantMessageFormatter) {
      return '<div class="consultant-msg-content">' +
        global.ConsultantMessageFormatter.format(text) + '</div>';
    }
    if (role === 'bot') {
      return (text || '')
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.*?)\*/g, '<em>$1</em>')
        .replace(/\n/g, '<br>');
    }
    return null;
  }

  function applyTheme() {
    const panel = document.getElementById('consultant-panel');
    const btn = document.getElementById('consultant-btn');
    const color = state.primaryColor || '#05B0FC';
    if (panel) panel.style.setProperty('--iqbal-primary', color);
    if (btn) btn.style.setProperty('--iqbal-primary', color);
  }

  const SEND_ICON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13"/><path d="M22 2L15 22L11 13L2 9L22 2Z"/></svg>';
  const CHAT_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
  const VOICE_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>';
  const PHONE_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/></svg>';

  function buildWidget() {
    if (document.getElementById('consultant-btn')) return;
    const backdrop = document.createElement('div');
    backdrop.id = 'consultant-backdrop';
    backdrop.addEventListener('click', toggle);
    document.body.appendChild(backdrop);

    const btn = document.createElement('button');
    btn.id = 'consultant-btn';
    btn.innerHTML = '<div class="consultant-icon-inner"><span class="consultant-btn-dot"></span><span>Consultant</span></div>';
    btn.setAttribute('aria-label', 'Open AI Consultant');
    btn.addEventListener('click', toggle);
    document.body.appendChild(btn);

    const panel = document.createElement('div');
    panel.id = 'consultant-panel';
    panel.innerHTML = [
      '<div class="consultant-header">',
      '  <div class="consultant-header-left">',
      '    <div class="consultant-header-avatar" aria-hidden="true">AI</div>',
      '    <div class="consultant-header-title">' + state.title +
      '      <span class="consultant-header-badge">Online</span>',
      '    </div>',
      '  </div>',
      '  <button class="consultant-close-btn" type="button" aria-label="Close">&times;</button>',
      '</div>',
      '<div class="consultant-tabs consultant-tabs-embed">',
      '  <button class="consultant-tab active" type="button" id="embed-chat-tab">' + CHAT_ICON + ' Chat</button>',
      '  <button class="consultant-tab" type="button" id="embed-voice-tab">' + VOICE_ICON + ' Voice</button>',
      '</div>',
      '<div id="c-pane-chat" class="consultant-embed-pane">',
      '  <div class="consultant-messages" id="c-messages">',
      '    <div class="consultant-msg bot consultant-welcome">Hello! How can I help you today?</div>',
      '  </div>',
      '  <div class="consultant-embed-footer">',
      '    <button type="button" class="consultant-callback-btn" id="embed-callback-btn">' + PHONE_ICON + ' Request callback</button>',
      '    <div class="consultant-input-row">',
      '      <textarea id="consultant-text-input" rows="1" placeholder="Type a message…" aria-label="Message"></textarea>',
      '      <button class="consultant-send-btn" id="c-send-btn" type="button" aria-label="Send">' + SEND_ICON + '</button>',
      '    </div>',
      '  </div>',
      '</div>',
      '<div id="c-pane-voice" class="consultant-embed-pane" style="display:none">',
      '  <div class="consultant-voice-pane">',
      '    <div class="consultant-voice-ring" id="c-voice-ring">',
      '      <div class="consultant-voice-mic-icon" aria-hidden="true">' + VOICE_ICON + '</div>',
      '    </div>',
      '    <div class="consultant-voice-status" id="c-voice-status">Tap Start to speak with the AI</div>',
      '    <div class="consultant-voice-controls">',
      '      <button class="c-voice-btn start" type="button" id="c-btn-start">' + VOICE_ICON + ' Start Voice</button>',
      '      <button class="c-voice-btn stop" type="button" id="c-btn-stop" style="display:none">Stop</button>',
      '    </div>',
      '    <div class="consultant-voice-transcript" id="c-transcript">',
      '      <div class="transcript-placeholder">Your voice conversation will appear here</div>',
      '    </div>',
      '  </div>',
      '</div>',
    ].join('');
    document.body.appendChild(panel);

    panel.querySelector('.consultant-close-btn').addEventListener('click', toggle);
    document.getElementById('c-send-btn').addEventListener('click', send);
    document.getElementById('consultant-text-input').addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    });
    document.getElementById('embed-callback-btn').addEventListener('click', requestCallback);
    function switchTab(mode) {
      const v = document.getElementById('c-pane-voice');
      const c = document.getElementById('c-pane-chat');
      const chatTab = document.getElementById('embed-chat-tab');
      const voiceTab = document.getElementById('embed-voice-tab');
      const isVoice = mode === 'voice';
      v.style.display = isVoice ? '' : 'none';
      c.style.display = isVoice ? 'none' : '';
      if (chatTab) chatTab.classList.toggle('active', !isVoice);
      if (voiceTab) voiceTab.classList.toggle('active', isVoice);
      if (!isVoice) stopVoice();
    }
    document.getElementById('embed-chat-tab').addEventListener('click', function () { switchTab('chat'); });
    document.getElementById('embed-voice-tab').addEventListener('click', function () { switchTab('voice'); });
    document.getElementById('c-btn-start').addEventListener('click', startVoice);
    document.getElementById('c-btn-stop').addEventListener('click', stopVoice);
  }

  function toggle() {
    state.isOpen = !state.isOpen;
    const panel = document.getElementById('consultant-panel');
    const backdrop = document.getElementById('consultant-backdrop');
    if (state.isOpen) {
      panel.classList.add('open');
      if (backdrop) backdrop.classList.add('visible');
      ensureSession().catch(function () {});
    } else {
      panel.classList.remove('open');
      if (backdrop) backdrop.classList.remove('visible');
      stopVoice();
    }
  }

  async function ensureSession() {
    if (state.visitorId && state.conversationId) return state.conversationId;
    const body = { visitor_id: localStorage.getItem(VISITOR_KEY) };
    const sc = localStorage.getItem(CONV_KEY);
    if (sc) body.conversation_id = parseInt(sc, 10);
    const r = await fetch(apiUrl('/api/consultant/public/session'), { method: 'POST', headers: headers(true), body: JSON.stringify(body) });
    const d = await r.json();
    if (!r.ok || !d.success) throw new Error(d.error || 'Session failed');
    state.visitorId = d.visitor_id;
    state.conversationId = d.conversation_id;
    localStorage.setItem(VISITOR_KEY, state.visitorId);
    localStorage.setItem(CONV_KEY, String(state.conversationId));
    return state.conversationId;
  }

  function addMsg(text, role) {
    const c = document.getElementById('c-messages');
    const div = document.createElement('div');
    if (role === 'bot error') {
      div.className = 'consultant-msg bot error';
      div.innerHTML = '<span class="consultant-error-icon" aria-hidden="true">!</span><span class="consultant-error-text"></span>';
      div.querySelector('.consultant-error-text').textContent = text;
    } else {
      div.className = 'consultant-msg ' + role;
      const html = formatMsgHtml(text, role);
      if (html) {
        div.innerHTML = html;
      } else {
        div.textContent = text;
      }
    }
    c.appendChild(div);
    c.scrollTop = c.scrollHeight;
  }

  function showTyping() {
    const c = document.getElementById('c-messages');
    const div = document.createElement('div');
    div.className = 'consultant-msg bot typing';
    div.innerHTML = '<span></span><span></span><span></span>';
    const id = 'ct-' + Date.now();
    div.id = id;
    c.appendChild(div);
    c.scrollTop = c.scrollHeight;
    return id;
  }

  function hideTyping(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
  }

  function waitForIceGathering(pc) {
    if (pc.iceGatheringState === 'complete') return Promise.resolve();
    return new Promise(function (resolve) {
      function done() {
        if (pc.iceGatheringState === 'complete') {
          pc.removeEventListener('icegatheringstatechange', done);
          resolve();
        }
      }
      pc.addEventListener('icegatheringstatechange', done);
      setTimeout(resolve, 3000);
    });
  }

  async function connectVoiceSdp(sdp) {
    const r = await fetch(apiUrl('/api/consultant/public/voice/connect'), {
      method: 'POST',
      headers: headers(true),
      body: JSON.stringify({ sdp: sdp }),
    });
    const d = await r.json();
    if (r.status === 429) {
      const wait = d.retry_after || 60;
      const src = d.source === 'openai' ? 'OpenAI' : 'Server';
      throw new Error(src + ' rate limit. Wait ' + wait + 's and try again.' +
        (d.key_source ? ' (key: ' + d.key_source + ')' : ''));
    }
    if (d.code === 'OPENAI_KEY_INVALID') {
      throw new Error('OpenAI API key is invalid. Check .env OPENAI_API_KEY or Admin settings.');
    }
    if (!r.ok || !d.success || !d.sdp) {
      throw new Error(d.error || d.detail || 'Voice connection failed');
    }
    return d.sdp;
  }

  async function send() {
    const ta = document.getElementById('consultant-text-input');
    const sendBtn = document.getElementById('c-send-btn');
    const msg = (ta.value || '').trim();
    if (!msg) return;
    try { await ensureSession(); } catch (e) { addMsg('Connection error: ' + e.message, 'bot error'); return; }
    addMsg(msg, 'user');
    ta.value = '';
    if (sendBtn) sendBtn.disabled = true;
    const typingId = showTyping();
    try {
      const r = await fetch(apiUrl('/api/consultant/public/chat'), {
        method: 'POST', headers: headers(true),
        body: JSON.stringify({ message: msg, visitor_id: state.visitorId, conversation_id: state.conversationId }),
      });
      const d = await r.json();
      hideTyping(typingId);
      if (d.success) {
        if (d.conversation_id) state.conversationId = d.conversation_id;
        addMsg(d.message, 'bot');
      } else {
        addMsg(d.error || 'Error', 'bot error');
      }
    } catch (e) {
      hideTyping(typingId);
      addMsg('Connection error: ' + e.message, 'bot error');
    } finally {
      if (sendBtn) sendBtn.disabled = false;
    }
  }

  async function requestCallback() {
    try { await ensureSession(); } catch (e) { return; }
    const notes = prompt('Your email or phone (optional):') || '';
    await fetch(apiUrl('/api/consultant/public/callback'), {
      method: 'POST', headers: headers(true),
      body: JSON.stringify({ visitor_id: state.visitorId, conversation_id: state.conversationId, notes: notes }),
    });
    addMsg('Callback requested. Someone will follow up.', 'bot');
  }

  async function executeTool(callId, toolName, argsRaw) {
    let args = {};
    try { args = JSON.parse(argsRaw || '{}'); } catch (_) {}
    let resultText = 'Tool failed.';
    try {
      const r = await fetch(apiUrl('/api/consultant/public/tool'), {
        method: 'POST', headers: headers(true),
        body: JSON.stringify({ tool_name: toolName, args: args }),
      });
      const d = await r.json();
      if (d.success) resultText = d.result;
      else if (d.error) resultText = 'Error: ' + d.error;
    } catch (e) { resultText = 'Error: ' + e.message; }
    if (state.dataChannel && state.dataChannel.readyState === 'open') {
      state.dataChannel.send(JSON.stringify({
        type: 'conversation.item.create',
        item: { type: 'function_call_output', call_id: callId, output: resultText },
      }));
      state.dataChannel.send(JSON.stringify({ type: 'response.create' }));
    }
  }

  function handleRealtimeEvent(evt) {
    if (evt.type === 'response.function_call_arguments.done') {
      executeTool(evt.call_id, evt.name, evt.arguments);
    }
    if (evt.type === 'conversation.item.input_audio_transcription.completed') {
      const t = document.getElementById('c-transcript');
      if (t) { const d = document.createElement('div'); d.textContent = 'You: ' + (evt.transcript || ''); t.appendChild(d); }
    }
    if (evt.type === 'response.audio_transcript.done' || evt.type === 'response.output_audio_transcript.done') {
      const t = document.getElementById('c-transcript');
      if (t && evt.transcript) { const d = document.createElement('div'); d.textContent = 'AI: ' + evt.transcript; t.appendChild(d); }
    }
  }

  async function startVoice() {
    if (state.voiceActive || state.voiceConnecting) return;
    const statusEl = document.getElementById('c-voice-status');
    const startBtn = document.getElementById('c-btn-start');
    state.voiceConnecting = true;
    if (startBtn) startBtn.disabled = true;
    statusEl.textContent = 'Connecting…';
    try {
      await ensureSession();
      if (!state.audioEl) { state.audioEl = new Audio(); state.audioEl.autoplay = true; document.body.appendChild(state.audioEl); }
      const pc = new RTCPeerConnection();
      state.peerConn = pc;
      pc.ontrack = function (e) { if (e.streams[0]) state.audioEl.srcObject = e.streams[0]; };
      statusEl.textContent = 'Accessing microphone…';
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      state.localStream = stream;
      stream.getTracks().forEach(function (t) { pc.addTrack(t, stream); });
      const dc = pc.createDataChannel('oai-events');
      state.dataChannel = dc;
      dc.addEventListener('message', function (e) { try { handleRealtimeEvent(JSON.parse(e.data)); } catch (_) {} });
      dc.addEventListener('open', function () {
        state.voiceActive = true;
        statusEl.textContent = 'Connected — speak now';
        statusEl.className = 'consultant-voice-status connected';
        const ring = document.getElementById('c-voice-ring');
        if (ring) ring.classList.add('active');
        document.getElementById('c-btn-start').style.display = 'none';
        document.getElementById('c-btn-stop').style.display = '';
      });
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      await waitForIceGathering(pc);
      const localSdp = (pc.localDescription && pc.localDescription.sdp) || offer.sdp;
      if (!localSdp || localSdp.indexOf('v=0') === -1) {
        throw new Error('Failed to create WebRTC offer');
      }
      statusEl.textContent = 'Starting voice AI…';
      const answerSdp = await connectVoiceSdp(localSdp);
      await pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });
    } catch (err) {
      statusEl.textContent = err.message;
      stopVoice();
    } finally {
      state.voiceConnecting = false;
      if (startBtn && !state.voiceActive) startBtn.disabled = false;
    }
  }

  function stopVoice() {
    state.voiceActive = false;
    state.voiceConnecting = false;
    if (state.peerConn) { try { state.peerConn.close(); } catch (_) {} state.peerConn = null; }
    if (state.localStream) { state.localStream.getTracks().forEach(function (t) { t.stop(); }); state.localStream = null; }
    state.dataChannel = null;
    const s = document.getElementById('c-voice-status');
    if (s) { s.textContent = 'Tap Start to speak with the AI'; s.className = 'consultant-voice-status'; }
    const ring = document.getElementById('c-voice-ring');
    if (ring) ring.classList.remove('active');
    const st = document.getElementById('c-btn-start');
    const sp = document.getElementById('c-btn-stop');
    if (st) { st.style.display = ''; st.disabled = false; }
    if (sp) sp.style.display = 'none';
  }

  function init(opts) {
    if (!opts || !opts.apiBase || !opts.clientKey) return;
    state.apiBase = opts.apiBase;
    state.clientKey = opts.clientKey;
    state.title = opts.title || state.title;
    state.primaryColor = opts.primaryColor || state.primaryColor;
    loadCss();
    loadFormatter().catch(function () {});
    if (!state.initialized) { buildWidget(); state.initialized = true; }
    applyTheme();
  }

  global.IqbalConsultant = { init: init, toggle: toggle };
}(window));
