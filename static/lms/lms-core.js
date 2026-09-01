/** LMS shared utilities — modals, toast, API helpers */
(function (global) {
  if (typeof global.escapeHtml !== 'function') {
    global.escapeHtml = function (s) {
      return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    };
  }

  global.lmsShowToast = function (msg, type) {
    var existing = document.getElementById('lmsToast');
    if (!existing) {
      existing = document.createElement('div');
      existing.id = 'lmsToast';
      existing.className = 'lms-toast';
      document.body.appendChild(existing);
    }
    existing.textContent = msg;
    existing.className = 'lms-toast show' + (type === 'error' ? ' error' : '');
    clearTimeout(existing._timer);
    existing._timer = setTimeout(function () {
      existing.className = 'lms-toast' + (type === 'error' ? ' error' : '');
    }, 3200);
    if (typeof global.showToast === 'function' && type !== 'silent') {
      try { global.showToast(msg, type === 'error' ? 'error' : 'success', 3000); } catch (e) { /* ignore */ }
    }
  };

  global.lmsOpenModal = function (id) {
    var el = document.getElementById(id);
    if (!el) return;
    el.classList.add('open');
    el.classList.remove('hidden');
    el.style.display = 'flex';
    el.style.alignItems = 'center';
    el.style.justifyContent = 'center';
    document.body.style.overflow = 'hidden';
  };

  global.lmsCloseModal = function (id) {
    var el = document.getElementById(id);
    if (!el) return;
    el.classList.remove('open');
    el.classList.add('hidden');
    el.style.display = 'none';
    document.body.style.overflow = '';
  };

  global.lmsApi = async function (url, opts) {
    opts = opts || {};
    var res = await fetch(url, Object.assign({ credentials: 'include' }, opts));
    var contentType = res.headers.get('content-type') || '';
    if (!contentType.includes('application/json')) {
      var text = await res.text();
      throw new Error(res.status >= 500 ? 'Server error — try restarting the app and retry.' : (text.slice(0, 120) || 'Request failed'));
    }
    var body = await res.json();
    if (!res.ok) {
      var errMsg = (body.error && body.error.message) || body.message || body.error || 'Request failed';
      throw new Error(typeof errMsg === 'string' ? errMsg : JSON.stringify(errMsg));
    }
    return body.data !== undefined ? body.data : body;
  };

  global.lmsMasteryBadge = function (status) {
    var map = { mastered: 'green', improving: 'blue', needs_practice: 'yellow', weak: 'red' };
    var cls = map[status] || 'blue';
    return '<span class="lms-badge lms-badge-' + cls + '">' + escapeHtml(status || 'unknown') + '</span>';
  };

  function lmsLooksLikeRawLatex(str) {
    return /\\(frac|sqrt|text|pm|neq|leq|geq|cdot|times|left|right|alpha|beta|theta|pi|infty|partial|sum|int)\b/.test(str);
  }

  /** Wrap bare LaTeX / plain math notation so TeacherChatFormatter + MathJax can render it. */
  global.lmsPrepareMathText = function (text) {
    if (text == null) return '';
    var s = String(text).trim();
    if (!s) return '';
    if (/\$[\s\S]*\$|\\\(|\\\[|\\begin\{/.test(s)) return s;
    if (lmsLooksLikeRawLatex(s)) return '\\(' + s + '\\)';
    var colon = s.match(/^(.+?:\s*)(.+)$/);
    if (colon && (/[a-zA-Z0-9]\^/.test(colon[2]) || /\\[a-zA-Z]+/.test(colon[2]) || /[0-9]x[\^²]/.test(colon[2]))) {
      return colon[1] + '\\(' + colon[2].trim() + '\\)';
    }
    if ((/[a-zA-Z0-9]\^/.test(s) || /x²|x³/.test(s)) && /[=+\-*/]/.test(s) && s.length < 200) {
      return '\\(' + s + '\\)';
    }
    return s;
  };

  global.lmsOptionText = function (opt) {
    if (!opt) return '';
    return opt.latex || opt.text || '';
  };

  global.lmsQuestionText = function (q) {
    if (!q) return '';
    return q.question_latex || q.question_text || '';
  };

  /** Rich HTML for LMS questions, options, and tutor replies (same pipeline as main chatbot). */
  global.lmsFormatRichText = function (raw, opts) {
    opts = opts || {};
    var text = lmsPrepareMathText(raw);
    var html;
    if (global.TeacherChatFormatter && typeof global.TeacherChatFormatter.formatChatResponse === 'function') {
      html = global.TeacherChatFormatter.formatChatResponse(text);
    } else {
      html = escapeHtml(text).replace(/\n/g, '<br>');
    }
    if (opts.inline) {
      html = html.replace(/^<p>([\s\S]*)<\/p>$/i, '$1');
      return '<span class="lms-math-content lms-math-inline">' + html + '</span>';
    }
    return '<div class="lms-math-content">' + html + '</div>';
  };

  global.lmsTypesetMath = function (el) {
    if (!el) return Promise.resolve();
    if (global.TeacherChatFormatter && typeof global.TeacherChatFormatter.processRenderedContent === 'function') {
      return global.TeacherChatFormatter.processRenderedContent(el);
    }
    if (global.MathJax && global.MathJax.typesetPromise) {
      return global.MathJax.typesetPromise([el]).catch(function () {});
    }
    return Promise.resolve();
  };

  /* Bridge legacy _showLmsModal */
  global._showLmsModal = global.lmsOpenModal;
  global._hideLmsModal = global.lmsCloseModal;
})(window);
