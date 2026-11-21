/* In-app exam security (IqbalAI only — not OS lockdown).
   Soft lock: fullscreen + focus/tab logging + heartbeat.
   Strict: lock attempt on leave; teacher must unlock. No third-party clients.
*/
(function () {
  var state = {
    attemptId: null,
    sessionToken: null,
    security: null,
    heartbeatTimer: null,
    syncState: 'idle',
    listenersBound: false,
    locked: false,
    lockUiShown: false,
    /** Once server/start says locked, only teacher unlock may clear. Survives stale heartbeats. */
    requiresTeacherUnlock: false,
    eventQueue: [],
    lastLockPostAt: 0,
    getQuestionIndex: null,
  };

  function api(path, opts) {
    if (typeof lmsApi === 'function') return lmsApi(path, opts);
    opts = opts || {};
    opts.credentials = 'same-origin';
    opts.headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
    return fetch('/api/lms' + path, opts).then(function (r) {
      return r.json().then(function (j) {
        var payload = (j && j.data != null) ? j.data : j;
        if (!r.ok) {
          var msg = (j && j.error && j.error.message) || (typeof j.error === 'string' ? j.error : null) || 'Request failed';
          throw new Error(msg);
        }
        return payload;
      });
    });
  }

  function isStrict() {
    return !!(state.security && (state.security.is_strict || state.security.lock_on_leave || state.security.security_mode === 'strict'));
  }

  function currentQ() {
    if (typeof state.getQuestionIndex === 'function') {
      try { return state.getQuestionIndex(); } catch (e) { return null; }
    }
    if (typeof window.lmsExamCurrentQuestionIndex === 'function') {
      try { return window.lmsExamCurrentQuestionIndex(); } catch (e) { return null; }
    }
    return null;
  }

  function playBeep() {
    try {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      var ctx = new Ctx();
      var o = ctx.createOscillator();
      var g = ctx.createGain();
      o.type = 'square';
      o.frequency.value = 880;
      g.gain.value = 0.08;
      o.connect(g); g.connect(ctx.destination);
      o.start();
      setTimeout(function () { try { o.stop(); ctx.close(); } catch (e) {} }, 220);
    } catch (e) { /* autoplay policies */ }
  }

  function showLockedOverlay(reason) {
    state.locked = true;
    state.lockUiShown = true;
    var existing = document.getElementById('lmsExamLockedOverlay');
    if (existing) existing.style.display = 'flex';
    else {
      var box = document.createElement('div');
      box.id = 'lmsExamLockedOverlay';
      box.setAttribute('role', 'alertdialog');
      box.innerHTML =
        '<div class="lms-exam-locked-card">' +
          '<h2>EXAM LOCKED</h2>' +
          '<p>You left the exam window. Your teacher has been notified.</p>' +
          '<p>Please wait for your teacher to unlock your exam.</p>' +
          '<p class="lms-exam-locked-reason"></p>' +
        '</div>';
      document.body.appendChild(box);
    }
    var reasonEl = document.querySelector('#lmsExamLockedOverlay .lms-exam-locked-reason');
    if (reasonEl && reason) reasonEl.textContent = 'Reason: ' + reason;
    var taking = document.getElementById('lmsQuizTaking');
    if (taking) taking.setAttribute('data-exam-locked', '1');
    playBeep();
    if (typeof window.lmsExamOnLocked === 'function') window.lmsExamOnLocked({ reason: reason });
  }

  function hideLockedOverlay() {
    state.locked = false;
    state.lockUiShown = false;
    var el = document.getElementById('lmsExamLockedOverlay');
    if (el) el.style.display = 'none';
    var taking = document.getElementById('lmsQuizTaking');
    if (taking) taking.removeAttribute('data-exam-locked');
    if (typeof window.lmsExamOnUnlocked === 'function') window.lmsExamOnUnlocked();
  }

  function handleServerLock(data) {
    if (!data) return;
    if (data.session_token) state.sessionToken = data.session_token;
    var locked = data.is_locked || data.status === 'locked' || data.locked;
    if (locked) {
      state.requiresTeacherUnlock = true;
      if (!state.lockUiShown) showLockedOverlay(data.lock_reason || 'exam window left');
    } else if (state.lockUiShown || state.requiresTeacherUnlock) {
      // Strict: never clear on plain in_progress (stale heartbeat / Continue / resume).
      // Only an explicit teacher unlock flag may clear the UI.
      var teacherUnlock = data.unlocked === true || data.exam_unlocked === true;
      if (teacherUnlock && (data.status === 'in_progress' || data.is_locked === false)) {
        state.requiresTeacherUnlock = false;
        hideLockedOverlay();
        if (isStrict()) requestFullscreen();
      }
    }
    if (typeof window.lmsExamSecurityOnHeartbeat === 'function') {
      window.lmsExamSecurityOnHeartbeat(data);
    }
  }

  function postEvent(eventType, severity, message, context) {
    if (!state.attemptId) return Promise.resolve(null);
    if (state.locked && isStrict()) {
      state.eventQueue.push({ eventType: eventType, severity: severity, message: message, context: context });
      return Promise.resolve(null);
    }
    var ctx = Object.assign({}, context || {});
    var qi = currentQ();
    if (qi != null) ctx.current_question_index = qi;
    var now = Date.now();
    if (isStrict() && (eventType === 'tab_hidden' || eventType === 'window_blur' || eventType === 'fullscreen_exit')) {
      if (now - state.lastLockPostAt < 800) return Promise.resolve(null);
      state.lastLockPostAt = now;
      // Fail closed locally while waiting for server
      showLockedOverlay(eventType);
    }
    return api('/attempts/' + state.attemptId + '/security-events', {
      method: 'POST',
      body: JSON.stringify({
        event_type: eventType,
        severity: severity || 'info',
        message: message || null,
        context: ctx,
      }),
    }).then(function (d) {
      handleServerLock(d);
      return d;
    }).catch(function () {
      if (isStrict()) {
        state.eventQueue.push({ eventType: eventType, severity: severity, message: message, context: ctx });
        showLockedOverlay(eventType);
      }
    });
  }

  function flushQueue() {
    if (!state.eventQueue.length || !state.attemptId) return;
    var batch = state.eventQueue.splice(0, state.eventQueue.length);
    batch.forEach(function (item) {
      api('/attempts/' + state.attemptId + '/security-events', {
        method: 'POST',
        body: JSON.stringify({
          event_type: item.eventType,
          severity: item.severity || 'info',
          message: item.message || null,
          context: item.context || {},
        }),
      }).then(handleServerLock).catch(function () {});
    });
  }

  function heartbeat() {
    if (!state.attemptId) return;
    var body = { session_token: state.sessionToken };
    var qi = currentQ();
    if (qi != null) body.current_question_index = qi;
    api('/attempts/' + state.attemptId + '/heartbeat', {
      method: 'POST',
      body: JSON.stringify(body),
    }).then(function (d) {
      if (!d) return;
      state.syncState = 'saved';
      updateSyncBadge();
      flushQueue();
      handleServerLock(d);
    }).catch(function () {
      state.syncState = 'offline';
      updateSyncBadge();
    });
  }

  function updateSyncBadge() {
    var el = document.getElementById('lmsExamSyncBadge');
    if (!el) return;
    var label = state.locked ? 'LOCKED' :
      state.syncState === 'saved' ? 'Saved' :
      state.syncState === 'saving' ? 'Saving…' :
      state.syncState === 'offline' ? 'Offline — answers retry when online' : 'Ready';
    el.textContent = label;
    el.setAttribute('data-state', state.locked ? 'locked' : state.syncState);
  }

  function ensureSyncBadge() {
    if (document.getElementById('lmsExamSyncBadge')) return;
    var host = document.querySelector('#lmsQuizTaking .lms-quiz-taking-head, #lmsDiagBody, #lmsStudentModal .lms-modal-head');
    if (!host) return;
    var badge = document.createElement('span');
    badge.id = 'lmsExamSyncBadge';
    badge.className = 'lms-exam-sync-badge';
    badge.textContent = 'Ready';
    host.appendChild(badge);
  }

  function requestFullscreen() {
    var root = document.documentElement;
    var req = root.requestFullscreen || root.webkitRequestFullscreen || root.msRequestFullscreen;
    if (req) {
      try { return Promise.resolve(req.call(root)); } catch (e) { return Promise.reject(e); }
    }
    return Promise.reject(new Error('Fullscreen API unavailable'));
  }

  function onVisibility() {
    if (document.hidden) {
      postEvent('tab_hidden', 'warn', 'Examination tab hidden');
      if (!isStrict() && state.security && state.security.warn_on_focus_loss && typeof lmsShowToast === 'function') {
        lmsShowToast('Stay on the exam tab. This interruption was recorded for review.', 'error');
      }
    }
  }

  function onBlur() {
    if (document.hidden) return; // tab_hidden already covers
    postEvent('window_blur', 'warn', 'Examination window lost focus');
  }

  function onFullscreenChange() {
    var fs = document.fullscreenElement || document.webkitFullscreenElement || document.msFullscreenElement;
    if (!fs) {
      postEvent('fullscreen_exit', 'warn', 'Fullscreen exited');
      if (!isStrict() && state.security && state.security.warn_on_focus_loss && typeof lmsShowToast === 'function') {
        lmsShowToast('Fullscreen exited. Return to fullscreen if required by your teacher.', 'error');
      }
    }
  }

  function onBeforeUnload(e) {
    if (!state.attemptId) return;
    e.preventDefault();
    e.returnValue = '';
    postEvent('reload', 'info', 'beforeunload / navigation attempt');
  }

  function bindListeners() {
    if (state.listenersBound) return;
    state.listenersBound = true;
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('blur', onBlur);
    document.addEventListener('fullscreenchange', onFullscreenChange);
    document.addEventListener('webkitfullscreenchange', onFullscreenChange);
    window.addEventListener('beforeunload', onBeforeUnload);
  }

  function unbindListeners() {
    if (!state.listenersBound) return;
    state.listenersBound = false;
    document.removeEventListener('visibilitychange', onVisibility);
    window.removeEventListener('blur', onBlur);
    document.removeEventListener('fullscreenchange', onFullscreenChange);
    document.removeEventListener('webkitfullscreenchange', onFullscreenChange);
    window.removeEventListener('beforeunload', onBeforeUnload);
  }

  window.lmsExamSecurityRequestFullscreen = requestFullscreen;

  window.lmsExamSecurityStart = function (opts) {
    opts = opts || {};
    state.attemptId = opts.attemptId;
    state.sessionToken = opts.sessionToken || null;
    state.security = opts.security || null;
    state.getQuestionIndex = opts.getQuestionIndex || null;
    state.locked = !!(opts.locked || opts.is_locked || (opts.status === 'locked'));
    if (state.locked) state.requiresTeacherUnlock = true;
    ensureSyncBadge();
    updateSyncBadge();
    bindListeners();
    if (opts.requestFullscreen !== false && !state.locked) requestFullscreen();
    if (typeof window.lmsSetAssessmentCopyGuard === 'function') {
      window.lmsSetAssessmentCopyGuard(true);
    }
    if (state.locked) showLockedOverlay(opts.lock_reason || 'exam locked');
    if (state.heartbeatTimer) clearInterval(state.heartbeatTimer);
    state.heartbeatTimer = setInterval(heartbeat, 5000);
    heartbeat();
  };

  window.lmsExamSecurityStop = function () {
    if (state.heartbeatTimer) clearInterval(state.heartbeatTimer);
    state.heartbeatTimer = null;
    unbindListeners();
    state.attemptId = null;
    state.sessionToken = null;
    state.locked = false;
    state.requiresTeacherUnlock = false;
    hideLockedOverlay();
    if (typeof window.lmsSetAssessmentCopyGuard === 'function') {
      window.lmsSetAssessmentCopyGuard(false);
    }
    var fs = document.fullscreenElement || document.webkitFullscreenElement;
    if (fs && document.exitFullscreen) {
      try { document.exitFullscreen(); } catch (e) { /* ignore */ }
    }
  };

  window.lmsExamSecurityMarkSaving = function () {
    state.syncState = 'saving';
    updateSyncBadge();
  };
  window.lmsExamSecurityMarkSaved = function () {
    state.syncState = 'saved';
    updateSyncBadge();
  };
  window.lmsExamSecurityMarkOffline = function () {
    state.syncState = 'offline';
    updateSyncBadge();
  };
  window.lmsExamSecuritySessionToken = function () {
    return state.sessionToken;
  };
  window.lmsExamSecurityIsLocked = function () {
    return !!state.locked;
  };
  /** Test/debug hook: apply a server lock payload the same way heartbeats do. */
  window.lmsExamSecurityApplyServerLockState = handleServerLock;
})();
