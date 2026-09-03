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

  var _LABEL_ONLY_RE = /^\s*[\(\[]?[A-Da-d][\)\].:]?\s*$/;
  var _LABEL_PREFIX_RE = /^\s*[\(\[]?([A-Da-d])(?:[\)\].:\-]\s+|\s+(?=\())/;
  var _SUPER_MAP = { '⁰':'0','¹':'1','²':'2','³':'3','⁴':'4','⁵':'5','⁶':'6','⁷':'7','⁸':'8','⁹':'9','⁺':'+','⁻':'-','⁽':'(','⁾':')' };
  var _FUNC_NAMES = { log:1, ln:1, sin:1, cos:1, tan:1, sec:1, csc:1, cot:1, exp:1, lim:1, max:1, min:1 };

  function lmsIsLabelOnly(str) {
    return _LABEL_ONLY_RE.test(String(str || ''));
  }

  function lmsStripOptionLabelPrefix(str) {
    var s = String(str || '').trim();
    if (!s || lmsIsLabelOnly(s)) return s;
    var stripped = s.replace(_LABEL_PREFIX_RE, '').trim();
    return stripped || s;
  }

  function lmsIsBrokenMathBlob(str) {
    var s = String(str || '').trim();
    if (!s || /\s/.test(s)) return false;
    return s.length > 40 && /[A-Za-z]{8,}/.test(s);
  }

  function lmsLooksLikeRawLatex(str) {
    return /\\(frac|sqrt|text|pm|neq|leq|geq|cdot|times|left|right|alpha|beta|theta|pi|infty|partial|sum|int|dots|ldots|cdots|div|log|sin|cos|tan)\b/.test(str)
      || /\^\{/.test(str);
  }

  function lmsUnicodeSupersToLatex(s) {
    return String(s || '').replace(/[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁽⁾]+/g, function (chunk) {
      var body = '';
      for (var i = 0; i < chunk.length; i++) body += _SUPER_MAP[chunk[i]] || chunk[i];
      return '^{' + body + '}';
    });
  }

  function lmsImplicitExponents(s) {
    return String(s || '').replace(/([A-Za-z])(?!\^)(\d+)/g, function (m, letter, digits, offset, full) {
      var start = offset;
      while (start > 0 && /[A-Za-z]/.test(full.charAt(start - 1))) start--;
      var word = full.slice(start, offset + 1).toLowerCase();
      if (_FUNC_NAMES[word]) return m;
      return letter + '^{' + digits + '}';
    });
  }

  function lmsLooksLikeMathLine(s) {
    s = String(s || '').trim();
    if (!s || s.length > 120) return false;
    if (/^(simplify|find|solve|evaluate|compute|expand|factor)\s*:?\s*$/i.test(s)) return false;
    return /[A-Za-z]\d|[A-Za-z]\^|[=+\-×÷·/^_\\()]/.test(s);
  }

  function lmsRecoverStackedFraction(s) {
    var lines = String(s || '').replace(/\r\n/g, '\n').split('\n').map(function (ln) { return ln.trim(); }).filter(Boolean);
    if (lines.length < 2) return s;
    var prefix = '';
    var math = [];
    for (var i = 0; i < lines.length; i++) {
      var ln = lines[i];
      if (/^(simplify|find|solve|evaluate|compute|expand|factor)\s*:?\s*$/i.test(ln) && !math.length) {
        prefix = ln.replace(/:?\s*$/, ':');
        continue;
      }
      var pref = ln.match(/^((?:simplify|find|solve|evaluate|compute|expand|factor)\s*:)\s*(.*)$/i);
      if (pref && !math.length) {
        prefix = pref[1].replace(/:?\s*$/, ':');
        if (pref[2]) math.push(pref[2].trim());
        continue;
      }
      math.push(ln);
    }
    if (math.length === 2 && lmsLooksLikeMathLine(math[0]) && lmsLooksLikeMathLine(math[1]) && math[0].indexOf('\\frac') < 0) {
      var frac = '\\frac{' + math[0] + '}{' + math[1] + '}';
      return prefix ? (prefix + ' ' + frac) : frac;
    }
    return s;
  }

  function lmsRecoverLatex(s) {
    var     t = String(s || '').trim();
    if (!t) return '';
    if (t.normalize) t = t.normalize('NFKC');
    t = t.replace(/−/g, '-');
    t = lmsUnicodeSupersToLatex(t);
    t = lmsImplicitExponents(t);
    t = lmsRecoverStackedFraction(t);
    return t.replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
  }

  function lmsLooksLikeMathExpression(str) {
    var s = String(str || '').trim();
    if (!s || s.length > 220) return false;
    if (/\b(which|following|choose|statement|decimal|because|correct|except)\b/i.test(s)) {
      return lmsLooksLikeRawLatex(s) || /\^\{/.test(s);
    }
    if (lmsLooksLikeRawLatex(s) || /\\(dots|ldots|cdots)\b/.test(s) || /\^\{/.test(s) || /\\frac/.test(s)) return true;
    var words = s.match(/[A-Za-z]{4,}/g) || [];
    var mathWords = /^(log|ln|sin|cos|tan|sec|csc|cot|lim|max|min|frac|sqrt|cdot|simplify)$/i;
    if (words.length && !words.every(function (w) { return mathWords.test(w); })) return false;
    return /[a-zA-Z0-9]\^|\^{|[_^]|[=+\-*/]|\\[a-zA-Z]+|[A-Za-z]\d/.test(s) || /\blog\b|\bsin\b|\bcos\b/.test(s);
  }

  function lmsPickDisplayText(text, latex) {
    var t = lmsStripOptionLabelPrefix(text || '');
    var l = lmsStripOptionLabelPrefix(latex || '');
    if (lmsIsBrokenMathBlob(l)) l = '';
    var recovered = lmsRecoverLatex(l || t);
    if (recovered && (/\^\{/.test(recovered) || /\\frac/.test(recovered) || /\\times/.test(recovered))) {
      return recovered;
    }
    if (lmsIsLabelOnly(t) && l && !lmsIsLabelOnly(l) && !lmsIsBrokenMathBlob(l)) return lmsRecoverLatex(l) || l;
    if (t && !lmsIsLabelOnly(t)) return lmsRecoverLatex(t) || t;
    return recovered || l || t;
  }

  /** Wrap recovered LaTeX so MathJax/KaTeX render exponents and fractions. */
  function prepareMathText(text, inline) {
    if (text == null) return '';
    var s = String(text).trim();
    if (!s) return '';
    if (/\$[\s\S]*\$|\\\(|\\\[|\\begin\{/.test(s)) return s;
    var recovered = lmsRecoverLatex(s) || s;
    var colon = recovered.match(/^((?:Simplify|Find|Solve|Evaluate|Compute|Expand|Factor)\s*:)\s*(.+)$/i);
    if (colon && (lmsLooksLikeMathExpression(colon[2]) || /\\frac|\^\{/.test(colon[2]))) {
      var body = colon[2].trim();
      var wrap = (!inline && /\\frac/.test(body)) ? ['\\[', '\\]'] : ['\\(', '\\)'];
      return colon[1] + ' ' + wrap[0] + body + wrap[1];
    }
    if (lmsLooksLikeRawLatex(recovered) || lmsLooksLikeMathExpression(recovered) || /\\frac|\^\{/.test(recovered)) {
      if (!inline && /\\frac/.test(recovered)) return '\\[' + recovered + '\\]';
      return '\\(' + recovered + '\\)';
    }
    return recovered;
  }
  global.lmsPrepareMathText = prepareMathText;
  global.lmsPickDisplayText = lmsPickDisplayText;
  global.lmsRecoverLatex = lmsRecoverLatex;

  function optionText(opt) {
    if (!opt) return '';
    return lmsPickDisplayText(opt.text, opt.latex);
  }
  global.lmsOptionText = optionText;

  function questionText(q) {
    if (!q) return '';
    return lmsPickDisplayText(q.question_text, q.question_latex);
  }
  global.lmsQuestionText = questionText;

  /** Rich HTML for LMS questions, options, and tutor replies (same pipeline as main chatbot). */
  function formatRichText(raw, opts) {
    opts = opts || {};
    var text = prepareMathText(raw, !!opts.inline);
    var html;
    if (global.TeacherChatFormatter && typeof global.TeacherChatFormatter.formatChatResponse === 'function') {
      html = global.TeacherChatFormatter.formatChatResponse(text);
    } else {
      html = escapeHtml(text).replace(/\n/g, '<br>');
    }
    if (opts.inline) {
      html = html.replace(/^<p>([\s\S]*)<\/p>$/i, '$1');
      return '<span class="lms-math-content lms-math-inline tex2jax_process">' + html + '</span>';
    }
    return '<div class="lms-math-content tex2jax_process">' + html + '</div>';
  };
  global.lmsFormatRichText = formatRichText;

  function typesetMath(el) {
    if (!el) return Promise.resolve();
    el.classList.add('tex2jax_process');
    if (global.TeacherChatFormatter && typeof global.TeacherChatFormatter.processRenderedContent === 'function') {
      return global.TeacherChatFormatter.processRenderedContent(el);
    }
    if (global.MathJax && global.MathJax.typesetPromise) {
      return global.MathJax.typesetPromise([el]).catch(function () {});
    }
    return Promise.resolve();
  };
  global.lmsTypesetMath = typesetMath;

  /* Bridge legacy _showLmsModal */
  global._showLmsModal = global.lmsOpenModal;
  global._hideLmsModal = global.lmsCloseModal;
})(window);
