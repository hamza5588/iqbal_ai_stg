/* Active tutor-panel time only. Never reads or sends message contents. */
(function () {
  'use strict';
  const IDLE_MS = 120000, PING_MS = 15000;
  let sessionId = crypto.randomUUID();
  let sequence = 0, total = 0, acknowledged = -1;
  let scope = null, lastActivity = -Infinity, previousTick = performance.now();
  let active = false, inFlight = false, suspended = false;

  function visibleScope() {
    if (!scope || document.visibilityState !== 'visible' || !document.hasFocus() || suspended) return false;
    return Array.from(document.querySelectorAll('[data-tutor-usage]')).some(function (el) {
      if (el.dataset.tutorUsage !== scope || !el.getClientRects().length) return false;
      const rect = el.getBoundingClientRect();
      return rect.bottom > 0 && rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth;
    });
  }

  function send(flush) {
    const counter = Math.floor(total);
    if ((!active && counter === acknowledged) || (inFlight && !flush)) return;
    const payload = { session_id: sessionId, seq: ++sequence, total_ms: counter };
    inFlight = true;
    fetch('/api/tutor-usage/heartbeat', {
      method: 'POST', credentials: 'same-origin', keepalive: !!flush,
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
    }).then(function (response) {
      if (response.ok && payload.session_id === sessionId) acknowledged = Math.max(acknowledged, counter);
    }).catch(function () {
      // The next heartbeat retries the cumulative counter; no offline backlog.
    }).finally(function () { inFlight = false; });
  }

  function tick(forceInactive) {
    const now = performance.now(), elapsed = now - previousTick;
    if (active && elapsed >= 0 && elapsed <= 5000) {
      total += Math.max(0, Math.min(now, lastActivity + IDLE_MS) - previousTick);
    }
    // A sleeping/suspended browser must not count the missing wall time.
    if (elapsed > 5000) lastActivity = -Infinity;
    previousTick = now;
    const wasActive = active;
    active = !forceInactive && visibleScope() && now - lastActivity < IDLE_MS;
    if (wasActive !== active) send(!active);
  }

  function interaction(event) {
    // Ignore programmatic focus/events; genuine user input is required to start.
    if (!event.isTrusted) return;
    tick(false);
    const target = event.target instanceof Element ? event.target.closest('[data-tutor-usage]') : null;
    if (target) {
      scope = target.dataset.tutorUsage;
      lastActivity = performance.now();
    } else if (event.type === 'pointerdown' || event.type === 'keydown' || event.type === 'focusin') {
      scope = null;
    }
    tick(false);
  }
  ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart', 'focusin'].forEach(function (type) {
    document.addEventListener(type, interaction, { passive: true, capture: true });
  });
  document.addEventListener('visibilitychange', function () { tick(document.hidden); });
  window.addEventListener('blur', function () { tick(true); });
  window.addEventListener('focus', function () { tick(false); });
  window.addEventListener('pagehide', function () { suspended = true; tick(true); send(true); });
  window.addEventListener('pageshow', function () { suspended = false; previousTick = performance.now(); lastActivity = -Infinity; });
  window.addEventListener('offline', function () { suspended = true; tick(true); });
  window.addEventListener('online', function () {
    suspended = false;
    sessionId = crypto.randomUUID(); sequence = 0; total = 0; acknowledged = -1;
    previousTick = performance.now(); lastActivity = -Infinity;
  });
  setInterval(function () { tick(false); }, 1000);
  setInterval(function () { tick(false); if (active || total > acknowledged && total > 0) send(false); }, PING_MS);
})();
