/** Full-screen wait spinner for form submissions and long-running POSTs. */
(function (global) {
  var visible = false;
  var sticky = false;
  var pending = 0;
  var hideTimer = null;
  var maxTimer = null;
  var el = null;
  var msgEl = null;

  function ensure() {
    if (el) return;
    el = document.getElementById('iqbal-wait-overlay');
    if (!el) {
      el = document.createElement('div');
      el.id = 'iqbal-wait-overlay';
      el.setAttribute('role', 'alert');
      el.setAttribute('aria-live', 'assertive');
      el.innerHTML =
        '<div class="iqbal-wait-card">' +
        '<div class="iqbal-wait-spinner" aria-hidden="true"></div>' +
        '<p class="iqbal-wait-msg">Please wait...</p>' +
        '</div>';
      (document.body || document.documentElement).appendChild(el);
    }
    msgEl = el.querySelector('.iqbal-wait-msg');
  }

  function showNow(message, opts) {
    ensure();
    opts = opts || {};
    sticky = !!opts.sticky;
    visible = true;
    if (msgEl) msgEl.textContent = message || 'Please wait...';
    el.classList.add('is-visible');
    el.setAttribute('aria-busy', 'true');
    if (document.body) document.body.classList.add('iqbal-wait-lock');
    clearTimeout(maxTimer);
    // Sticky long uploads (diagnostic / large PDFs) must not auto-hide at 3 minutes.
    var maxMs = typeof opts.maxMs === 'number' ? opts.maxMs : sticky ? 55 * 60 * 1000 : 180000;
    if (maxMs > 0) maxTimer = setTimeout(hideNow, maxMs);
    if (!sticky) {
      clearTimeout(hideTimer);
      hideTimer = setTimeout(function () {
        if (pending === 0 && visible && !sticky) hideNow();
      }, 700);
    }
  }

  function hideNow() {
    sticky = false;
    pending = 0;
    visible = false;
    clearTimeout(hideTimer);
    clearTimeout(maxTimer);
    if (el) {
      el.classList.remove('is-visible');
      el.setAttribute('aria-busy', 'false');
    }
    if (document.body) document.body.classList.remove('iqbal-wait-lock');
  }

  global.showWaitOverlay = function (message, opts) {
    showNow(message || 'Please wait...', opts || {});
  };
  global.updateWaitOverlay = function (message) {
    ensure();
    if (msgEl && message) msgEl.textContent = message;
  };
  global.hideWaitOverlay = hideNow;

  /** Human-readable remaining time for large uploads. */
  global.formatUploadEta = function (sec) {
    sec = Math.max(0, Math.round(sec || 0));
    if (sec < 60) return sec + ' seconds';
    var m = Math.floor(sec / 60);
    var s = sec % 60;
    if (m < 60) return s ? m + ' min ' + s + ' sec' : m + ' min';
    var h = Math.floor(m / 60);
    m = m % 60;
    return m ? h + ' hr ' + m + ' min' : h + ' hr';
  };

  /**
   * After ~12s of measurable upload speed, surface ETA.
   * If remaining time is still long (>=90s), add a persistent "keeps uploading" notice
   * (never a blocking dialog). The ETA covers the upload only, not server processing.
   * onUpdate(msg, { pct, remainingSec, loaded, total, longUpload }) is optional.
   */
  global.bindUploadEtaGuard = function (xhr, onUpdate) {
    var startedAt = Date.now();
    var askedProceed = false;
    var lastLoaded = 0;
    var lastAt = startedAt;
    xhr.upload.addEventListener('progress', function (ev) {
      if (!ev.lengthComputable || !ev.total) return;
      var now = Date.now();
      var elapsedSec = (now - startedAt) / 1000;
      var pct = Math.round((ev.loaded / ev.total) * 100);
      var instantGap = Math.max(0.2, (now - lastAt) / 1000);
      var bytesPerSec =
        elapsedSec > 1
          ? ev.loaded / elapsedSec
          : (ev.loaded - lastLoaded) / instantGap;
      lastLoaded = ev.loaded;
      lastAt = now;
      var remaining = null;
      var msg = 'Uploading… ' + pct + '%';
      if (elapsedSec >= 12 && bytesPerSec >= 1024) {
        remaining = (ev.total - ev.loaded) / bytesPerSec;
        msg =
          'Uploading… ' +
          pct +
          '% — about ' +
          global.formatUploadEta(remaining) +
          ' remaining';
      }
      // Long upload: show a persistent, non-blocking notice instead of window.confirm().
      // A native confirm is dismissed (and returns false → aborted the upload) when the
      // teacher switches browser tabs; in-page text survives tab switches and the
      // existing Cancel controls still let the teacher stop the upload.
      if (!askedProceed && remaining != null && remaining >= 90) askedProceed = true;
      if (askedProceed && pct < 100) {
        msg += ' — large file: it keeps uploading if you switch tabs.';
      }
      if (typeof onUpdate === 'function') {
        onUpdate(msg, {
          pct: pct,
          remainingSec: remaining,
          loaded: ev.loaded,
          total: ev.total,
          longUpload: askedProceed,
        });
      }
      if (visible) global.updateWaitOverlay(msg);
    });
  };

  global.withWaitOverlay = function (message, fn, opts) {
    showNow(message || 'Please wait...', opts || {});
    var finished = false;
    function finish() {
      if (finished) return;
      finished = true;
      hideNow();
    }
    try {
      var result = typeof fn === 'function' ? fn() : fn;
    } catch (err) {
      finish();
      throw err;
    }
    if (result && typeof result.then === 'function') {
      return Promise.resolve(result).then(
        function (value) { finish(); return value; },
        function (err) { finish(); throw err; }
      );
    }
    finish();
    return result;
  };

  function isMutating(method) {
    var m = String(method || 'GET').toUpperCase();
    return m === 'POST' || m === 'PUT' || m === 'PATCH' || m === 'DELETE';
  }

  function beginMutation() {
    if (!visible || sticky) return;
    pending += 1;
    clearTimeout(hideTimer);
  }

  function endMutation() {
    if (!visible || sticky) return;
    pending = Math.max(0, pending - 1);
    if (pending === 0) {
      clearTimeout(hideTimer);
      hideTimer = setTimeout(function () {
        if (pending === 0 && visible && !sticky) hideNow();
      }, 400);
    }
  }

  var origFetch = global.fetch;
  if (typeof origFetch === 'function') {
    global.fetch = function (input, init) {
      var method = 'GET';
      if (init && init.method) method = init.method;
      else if (input && typeof input === 'object' && input.method) method = input.method;
      var mutating = isMutating(method);
      if (mutating) beginMutation();
      try {
        var p = origFetch.apply(this, arguments);
        if (!mutating) return p;
        return Promise.resolve(p).then(
          function (res) { endMutation(); return res; },
          function (err) { endMutation(); throw err; }
        );
      } catch (err) {
        if (mutating) endMutation();
        throw err;
      }
    };
  }

  if (typeof XMLHttpRequest !== 'undefined') {
    var origOpen = XMLHttpRequest.prototype.open;
    var origSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (method) {
      this._iqbalMutating = isMutating(method);
      return origOpen.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function () {
      var xhr = this;
      if (xhr._iqbalMutating) {
        beginMutation();
        xhr.addEventListener('loadend', function () { endMutation(); });
      }
      return origSend.apply(this, arguments);
    };
  }

  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.hasAttribute('data-no-wait')) return;
    var msg = form.getAttribute('data-wait-message') || 'Please wait...';
    var opts = form.hasAttribute('data-wait-sticky')
      ? { sticky: true, maxMs: 55 * 60 * 1000 }
      : {};
    showNow(msg, opts);
  }, true);

  window.addEventListener('pageshow', function (ev) {
    if (ev.persisted) hideNow();
  });
})(window);
