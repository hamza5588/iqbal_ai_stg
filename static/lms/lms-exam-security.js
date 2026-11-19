/* Mode A soft exam lockdown helpers (not OS lockdown).
   - Fullscreen request + exit detection
   - Tab/focus visibility logging to /attempts/:id/security-events
   - Heartbeat to /attempts/:id/heartbeat
   - beforeunload warning
   Safe Exam Browser (Mode B) is enforced server-side; this file only guides UX.
*/
(function () {
  var state = {
    attemptId: null,
    sessionToken: null,
    security: null,
    heartbeatTimer: null,
    syncState: 'idle',
    listenersBound: false,
  };

  function api(path, opts) {
    if (typeof lmsApi === 'function') return lmsApi(path, opts);
    opts = opts || {};
    opts.credentials = 'same-origin';
    opts.headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
    return fetch('/api/lms' + path, opts).then(function (r) { return r.json(); });
  }

  function postEvent(eventType, severity, message, context) {
    if (!state.attemptId) return;
    api('/attempts/' + state.attemptId + '/security-events', {
      method: 'POST',
      body: JSON.stringify({
        event_type: eventType,
        severity: severity || 'info',
        message: message || null,
        context: context || {},
      }),
    }).catch(function () { /* never block the exam UI on telemetry */ });
  }

  function heartbeat() {
    if (!state.attemptId) return;
    api('/attempts/' + state.attemptId + '/heartbeat', {
      method: 'POST',
      body: JSON.stringify({ session_token: state.sessionToken }),
    }).then(function (d) {
      if (!d) return;
      if (d.session_token) state.sessionToken = d.session_token;
      if (d.status && d.status !== 'in_progress') {
        postEvent('other', 'info', 'Attempt no longer in progress after heartbeat');
      }
      if (typeof window.lmsExamSecurityOnHeartbeat === 'function') {
        window.lmsExamSecurityOnHeartbeat(d);
      }
    }).catch(function () {
      state.syncState = 'offline';
      updateSyncBadge();
    });
  }

  function updateSyncBadge() {
    var el = document.getElementById('lmsExamSyncBadge');
    if (!el) return;
    var label = state.syncState === 'saved' ? 'Saved' :
      state.syncState === 'saving' ? 'Saving…' :
      state.syncState === 'offline' ? 'Offline — answers retry when online' : 'Ready';
    el.textContent = label;
    el.setAttribute('data-state', state.syncState);
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
      try { req.call(root); } catch (e) { /* user gesture may be required */ }
    }
  }

  function onVisibility() {
    if (document.hidden) {
      postEvent('tab_hidden', 'warn', 'Examination tab hidden');
      if (state.security && state.security.warn_on_focus_loss && typeof lmsShowToast === 'function') {
        lmsShowToast('Stay on the exam tab. This interruption was recorded for review.', 'error');
      }
    }
  }

  function onBlur() {
    postEvent('window_blur', 'warn', 'Examination window lost focus');
  }

  function onFullscreenChange() {
    var fs = document.fullscreenElement || document.webkitFullscreenElement || document.msFullscreenElement;
    if (!fs) {
      postEvent('fullscreen_exit', 'warn', 'Fullscreen exited');
      if (state.security && state.security.warn_on_focus_loss && typeof lmsShowToast === 'function') {
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

  window.lmsExamSecurityStart = function (opts) {
    opts = opts || {};
    state.attemptId = opts.attemptId;
    state.sessionToken = opts.sessionToken || null;
    state.security = opts.security || null;
    ensureSyncBadge();
    updateSyncBadge();
    bindListeners();
    if (opts.requestFullscreen !== false) requestFullscreen();
    if (typeof window.lmsSetAssessmentCopyGuard === 'function') {
      window.lmsSetAssessmentCopyGuard(true);
    }
    if (state.heartbeatTimer) clearInterval(state.heartbeatTimer);
    state.heartbeatTimer = setInterval(heartbeat, 15000);
    heartbeat();
    if (state.security && state.security.require_seb) {
      // Soft UX note only — real enforcement is server-side SEB header checks.
      if (typeof lmsShowToast === 'function') {
        lmsShowToast('This exam requires Safe Exam Browser. If you are not in SEB, the server will reject your session.', 'error');
      }
    }
  };

  window.lmsExamSecurityStop = function () {
    if (state.heartbeatTimer) clearInterval(state.heartbeatTimer);
    state.heartbeatTimer = null;
    unbindListeners();
    state.attemptId = null;
    state.sessionToken = null;
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
})();
