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

  /* ---- Answer feedback & motivation (shared by Learning Chat + practice) ---- */
  global.lmsFeedback = (function () {
    var PRAISE = [
      'Good job!', 'Well done!', 'You got it!', 'Nice work!', 'Excellent!',
      'Spot on!', 'Great thinking!', 'That\'s right!', 'Brilliant!', 'Perfect!'
    ];
    var STREAK = [
      '', '', '2 in a row!', '3 in a row — on fire!', '4 straight!',
      '5 in a row — unstoppable!'
    ];

    function reduceMotion() {
      try {
        return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      } catch (e) { return false; }
    }
    function soundEnabled() {
      try { return localStorage.getItem('lms_sound') !== 'off'; } catch (e) { return true; }
    }
    function toggleSound() {
      var on = !soundEnabled();
      try { localStorage.setItem('lms_sound', on ? 'on' : 'off'); } catch (e) { /* ignore */ }
      return on;
    }

    function praise(streak) {
      var base = PRAISE[Math.floor(Math.random() * PRAISE.length)];
      var s = streak && STREAK[Math.min(streak, STREAK.length - 1)];
      return s ? (base + ' ' + s) : base;
    }

    var _actx = null;
    function cue(kind) {
      if (!soundEnabled()) return;
      try {
        var Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) return;
        _actx = _actx || new Ctx();
        var now = _actx.currentTime;
        var notes = kind === 'error' ? [311.1, 246.9] : [587.3, 784.0];
        notes.forEach(function (f, i) {
          var osc = _actx.createOscillator();
          var g = _actx.createGain();
          osc.type = 'sine';
          osc.frequency.value = f;
          var t = now + i * 0.09;
          g.gain.setValueAtTime(0.0001, t);
          g.gain.exponentialRampToValueAtTime(0.16, t + 0.02);
          g.gain.exponentialRampToValueAtTime(0.0001, t + 0.16);
          osc.connect(g); g.connect(_actx.destination);
          osc.start(t); osc.stop(t + 0.18);
        });
      } catch (e) { /* audio is best-effort */ }
    }

    function celebrate(message) {
      cue('success');
      if (reduceMotion()) { if (message) global.lmsShowToast(message, 'success'); return; }
      var host = document.createElement('div');
      host.className = 'lms-celebrate';
      host.innerHTML =
        '<div class="lms-celebrate-badge">' +
        '<span class="lms-celebrate-icon">👍</span>' +
        (message ? '<span class="lms-celebrate-msg">' + global.escapeHtml(message) + '</span>' : '') +
        '</div>';
      document.body.appendChild(host);
      setTimeout(function () { host.classList.add('out'); }, 1100);
      setTimeout(function () { if (host.parentNode) host.parentNode.removeChild(host); }, 1600);
    }

    function markOption(el, kind) {
      if (!el) return;
      el.classList.add(kind === 'wrong' ? 'lms-opt-wrong' : 'lms-opt-correct');
      if (kind === 'wrong' && !reduceMotion()) {
        el.classList.add('lms-shake');
        setTimeout(function () { el.classList.remove('lms-shake'); }, 500);
      }
    }
    function clearOptionMarks(container) {
      var root = container || document;
      root.querySelectorAll('.lms-opt-wrong, .lms-opt-correct').forEach(function (n) {
        n.classList.remove('lms-opt-wrong', 'lms-opt-correct', 'lms-shake');
      });
    }

    return {
      praise: praise, cue: cue, celebrate: celebrate,
      markOption: markOption, clearOptionMarks: clearOptionMarks,
      soundEnabled: soundEnabled, toggleSound: toggleSound
    };
  })();

  /* ---- Reusable copy-to-clipboard (question text, tutor replies, ...) ---- */
  global.lmsCopyToClipboard = function (text, btn, doneLabel) {
    text = String(text == null ? '' : text);
    var done = function () {
      if (!btn) return;
      var prev = btn.textContent;
      btn.textContent = doneLabel || 'Copied';
      setTimeout(function () { btn.textContent = prev; }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(function () { done(); });
    } else {
      var ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); } catch (e) { /* ignore */ }
      document.body.removeChild(ta);
      done();
    }
  };

  /* ---- Adjustable text size for question/answer/tutor content (DIL
     feedback: readability). Persisted per browser, applied via a CSS
     custom property so every LMS surface (--lms-font-scale) picks it up
     without re-rendering. ---- */
  var _FONT_STEP_MIN = -2, _FONT_STEP_MAX = 3;
  function _fontStep() {
    try {
      var v = parseInt(localStorage.getItem('lms_font_step') || '0', 10);
      return isNaN(v) ? 0 : Math.max(_FONT_STEP_MIN, Math.min(_FONT_STEP_MAX, v));
    } catch (e) { return 0; }
  }
  function _applyFontScale() {
    var scale = (1 + _fontStep() * 0.1).toFixed(2);
    try { document.documentElement.style.setProperty('--lms-font-scale', scale); } catch (e) { /* ignore */ }
  }
  global.lmsStepFont = function (delta) {
    var step = Math.max(_FONT_STEP_MIN, Math.min(_FONT_STEP_MAX, _fontStep() + delta));
    try { localStorage.setItem('lms_font_step', String(step)); } catch (e) { /* ignore */ }
    _applyFontScale();
    return step;
  };
  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', _applyFontScale);
    } else {
      _applyFontScale();
    }
  }

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
    s = lmsUnsquashEnglish(String(s || '').trim());
    if (!s || s.length > 120) return false;
    if (/^(simplify|find|solve|evaluate|compute|expand|factor)\s*:?\s*$/i.test(s)) return false;
    if (lmsLooksLikeProse(s)) return false;
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

  var _MATH_WORD_RE = /^(log|ln|sin|cos|tan|sec|csc|cot|lim|max|min|frac|sqrt|cdot|simplify|left|right|text|over|times)$/i;
  var _FRAC_RE = /\\frac\{(?:[^{}]|\{[^{}]*\})*\}\{(?:[^{}]|\{[^{}]*\})*\}/g;
  var _ALG_TERM = '(?:-?\\d*(?:[A-Za-z](?:\\^\\{[^}]+\\}|\\^\\d+|\\d+)*)+|-?\\d+)';
  var _ALG_ISLAND_RE = new RegExp(_ALG_TERM + '(?:\\s*[+\\-×÷=]\\s*' + _ALG_TERM + ')+', 'g');
  // A plain English connective ("or", "and", ...) sitting between two math
  // bits - e.g. "x = 3 or x = -3". Must not be wrapped as one math span.
  var _MATH_CONNECTIVE_RE = /[0-9A-Za-z)}\]]\s+(?:or|and|nor|where|when|then)\s+[-(\\0-9A-Za-z]/i;
  var _EXAM_WORDS = [
    'factorization', 'factorisation', 'polynomials', 'polynomial',
    'expressions', 'expression', 'statements', 'statement',
    'coefficients', 'coefficient', 'identities', 'identity',
    'equations', 'equation', 'fractions', 'fraction', 'decimals',
    'decimal', 'integers', 'integer', 'numbers', 'number',
    'incorrect', 'correct', 'following', 'repeating', 'terminating',
    'irrational', 'rational', 'quadratic', 'standard', 'simplify',
    'simplified', 'evaluate', 'compute', 'expand', 'factor',
    'degree', 'product', 'difference', 'quotient', 'remainder',
    'equivalent', 'positive', 'negative', 'greatest', 'greater',
    'choose', 'select', 'between', 'without', 'linear', 'cubic',
    'prime', 'composite', 'complex', 'constant', 'variable',
    'which', 'what', 'where', 'when', 'this', 'that', 'these',
    'those', 'each', 'both', 'only', 'also', 'true', 'false',
    'none', 'find', 'solve', 'given', 'below', 'above', 'after',
    'before', 'over', 'under', 'into', 'onto', 'from', 'with',
    'than', 'then', 'such', 'must', 'does', 'have', 'has',
    'was', 'were', 'are', 'the', 'and', 'for', 'not', 'its',
    'of', 'is', 'in', 'or', 'an', 'to', 'if'
  ].sort(function (a, b) { return b.length - a.length || (a < b ? -1 : 1); });

  function lmsUnsquashEnglish(s) {
    return String(s || '').replace(/[A-Za-z]{10,}/g, function (blob) {
      var lower = blob.toLowerCase();
      var parts = [];
      var i = 0;
      while (i < lower.length) {
        var matched = null;
        for (var w = 0; w < _EXAM_WORDS.length; w++) {
          var word = _EXAM_WORDS[w];
          if (lower.slice(i, i + word.length) === word) {
            matched = word;
            break;
          }
        }
        if (!matched) {
          var rest = blob.slice(i);
          return parts.length ? parts.join(' ') + ' ' + rest : blob;
        }
        parts.push(blob.slice(i, i + matched.length));
        i += matched.length;
      }
      return parts.join(' ');
    }).replace(/([A-Za-z]{4,})(\d)/g, '$1 $2');
  }

  function lmsUnwrapOuterMathIfProse(s) {
    s = String(s || '').trim();
    var inner = null;
    if (s.indexOf('\\(') === 0 && s.slice(-2) === '\\)' && s.length > 4) inner = s.slice(2, -2).trim();
    else if (s.indexOf('\\[') === 0 && s.slice(-2) === '\\]' && s.length > 4) inner = s.slice(2, -2).trim();
    else if (s.indexOf('$$') === 0 && s.slice(-2) === '$$' && s.length > 4) inner = s.slice(2, -2).trim();
    else if (s.charAt(0) === '$' && s.charAt(s.length - 1) === '$' && s.length > 2 && s.indexOf('$$') !== 0) {
      inner = s.slice(1, -1).trim();
    }
    if (inner == null) return s;
    var expanded = lmsUnsquashEnglish(inner);
    return lmsLooksLikeProse(expanded) ? expanded : s;
  }

  function lmsNormalizeMixedPercents(s) {
    s = String(s || '');
    var fracs = {
      '½': '1/2', '⅓': '1/3', '⅔': '2/3', '¼': '1/4', '¾': '3/4',
      '⅕': '1/5', '⅖': '2/5', '⅗': '3/5', '⅘': '4/5',
      '⅙': '1/6', '⅚': '5/6', '⅛': '1/8', '⅜': '3/8', '⅝': '5/8', '⅞': '7/8'
    };
    Object.keys(fracs).forEach(function (g) {
      s = s.split(g).join(' ' + fracs[g]);
    });
    s = s.replace(/\\%/g, '%');
    s = s.replace(/(\d+)\s*\\frac\{(\d+)\}\{(\d+)\}\s*%/g, '$1 $2/$3%');
    s = s.replace(/(\d+)\s+(\d+)\s+(\d+)\s*%/g, '$1 $2/$3%');
    s = s.replace(/(^|[^\d/])(\d{2,})\/(\d+)\s*%/g, function (_m, pre, left, den) {
      var denI = parseInt(den, 10);
      if (!denI) return _m;
      for (var digits = 1; digits <= 2; digits++) {
        if (left.length <= digits) continue;
        var whole = left.slice(0, -digits);
        var num = left.slice(-digits);
        var numI = parseInt(num, 10);
        if (!whole || whole.charAt(0) === '0' || !numI) continue;
        if (numI < denI) return pre + whole + ' ' + num + '/' + den + '%';
      }
      return _m;
    });
    return s.replace(/[ \t]+/g, ' ').trim();
  }

  function lmsIsMixedPercent(s) {
    return /^\s*\d+(?:\s+\d+\s*\/\s*\d+)?\s*%?\s*$/.test(s) || /^\s*\d+\s*\/\s*\d+\s*%?\s*$/.test(s);
  }

  function lmsIsPlainPercent(s) {
    return /^\s*\d+(?:\s+\d+\s*\/\s*\d+)?\s*%\s*$/.test(String(s || ''));
  }

  function lmsLooksLikeProse(s) {
    var words = lmsUnsquashEnglish(String(s || '')).match(/[A-Za-z]{4,}/g) || [];
    var real = words.filter(function (w) { return !_MATH_WORD_RE.test(w); });
    return real.length >= 3;
  }

  function lmsLooksLikeMixedTextAndMath(s) {
    s = lmsUnsquashEnglish(String(s || '').trim());
    if (!s) return false;
    var words = s.match(/[A-Za-z]{4,}/g) || [];
    var real = words.filter(function (w) { return !_MATH_WORD_RE.test(w); });
    if (!real.length) return false;
    return /[A-Za-z]\s*\^\{?|[A-Za-z]\d|\\(frac|sqrt|pm|times|cdot)\b|[=+\-]\s*-?\d|\d\s*[=+\-]/.test(s);
  }

  function lmsStripMathDelims(s) {
    return String(s || '').replace(/\\\(|\\\)|\\\[|\\\]/g, '');
  }

  function lmsReadBraceGroup(s, start) {
    var depth = 0;
    var i = start;
    var n = s.length;
    while (i < n) {
      if (s.charAt(i) === '\\' && i + 1 < n) { i += 2; continue; }
      if (s.charAt(i) === '{') depth++;
      else if (s.charAt(i) === '}') {
        depth--;
        if (depth === 0) return { inner: s.slice(start + 1, i), next: i + 1 };
      }
      i++;
    }
    return { inner: s.slice(start + 1), next: n };
  }

  function lmsStripInnerMathDelims(s) {
    // Brace-walk \frac args so nested \( \) cannot leak as red KaTeX errors.
    s = String(s || '');
    var out = '';
    var i = 0;
    while (i < s.length) {
      if (s.slice(i, i + 5) === '\\frac') {
        var j = i + 5;
        while (j < s.length && /\s/.test(s.charAt(j))) j++;
        if (s.charAt(j) === '{') {
          var num = lmsReadBraceGroup(s, j);
          j = num.next;
          while (j < s.length && /\s/.test(s.charAt(j))) j++;
          if (s.charAt(j) === '{') {
            var den = lmsReadBraceGroup(s, j);
            out += '\\frac{' + lmsStripInnerMathDelims(lmsStripMathDelims(num.inner)) + '}{' +
              lmsStripInnerMathDelims(lmsStripMathDelims(den.inner)) + '}';
            i = den.next;
            continue;
          }
        }
      }
      out += s.charAt(i);
      i++;
    }
    return out;
  }

  function lmsEscapePercentInMathBody(s) {
    return String(s || '').replace(/\\%/g, '%').replace(/%/g, '\\%');
  }

  function lmsMapOutsideMath(s, fn) {
    var re = /\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|\$[^$\n]+\$/g;
    var out = [];
    var last = 0;
    var m;
    while ((m = re.exec(s))) {
      if (m.index > last) out.push(fn(s.slice(last, m.index)));
      out.push(m[0]);
      last = m.index + m[0].length;
    }
    if (last < s.length) out.push(fn(s.slice(last)));
    return out.join('');
  }

  function lmsWrapMathChunk(m, inline) {
    if (/^\\[\(\[]/.test(m) || /^\$/.test(m) || lmsIsMixedPercent(m)) return m;
    var inner = lmsEscapePercentInMathBody(m);
    if (!inline && /\\frac/.test(inner)) return '\\[' + inner + '\\]';
    return '\\(' + inner + '\\)';
  }

  function lmsWrapMathIslands(s, inline) {
    return lmsMapOutsideMath(s, function (chunk) {
      if (!chunk) return chunk;
      chunk = chunk.replace(_FRAC_RE, function (m) { return lmsWrapMathChunk(m, inline); });
      return lmsMapOutsideMath(chunk, function (c) {
        return c.replace(_ALG_ISLAND_RE, function (m) { return lmsWrapMathChunk(m, true); });
      });
    });
  }

  // Some LLM structured-output round trips mis-escape a literal backslash
  // before a LaTeX command as a JSON control-character escape - e.g. the
  // model writes \frac{1}{2} inside a JSON string field, but a spec-
  // compliant JSON parser reads "\f" as U+000C (form feed), eating the
  // backslash and the f: \frac -> "\x0Crac". Mirrors math_text.py's
  // _recover_eaten_backslash_commands (found live via E2E testing an
  // AI-generated diagnostic variant question).
  var _EATEN_BACKSLASH_COMMANDS = {
    '\x0Crac': '\\frac',
    '\x09imes': '\\times',
    '\x09heta': '\\theta',
    '\x09ext': '\\text',
    '\x08eta': '\\beta',
    '\x0Bec': '\\vec'
  };
  function lmsRecoverEatenBackslashCommands(s) {
    if (!s || !/[\x08\x09\x0B\x0C]/.test(s)) return s;
    var out = s;
    Object.keys(_EATEN_BACKSLASH_COMMANDS).forEach(function (bad) {
      out = out.split(bad).join(_EATEN_BACKSLASH_COMMANDS[bad]);
    });
    return out;
  }

  function lmsRecoverLatex(s) {
    // Repair BEFORE trim(): String.trim() treats a form-feed/tab artifact
    // at the very start/end as whitespace and would delete the very
    // character the repair needs to see.
    var t = lmsRecoverEatenBackslashCommands(String(s || '')).trim();
    if (!t) return '';
    if (t.normalize) t = t.normalize('NFKC');
    t = lmsUnwrapOuterMathIfProse(t);
    t = lmsUnsquashEnglish(t);
    t = t.replace(/−/g, '-');
    t = lmsUnicodeSupersToLatex(t);
    t = lmsImplicitExponents(t);
    t = lmsRecoverStackedFraction(t);
    t = lmsStripInnerMathDelims(t);
    t = lmsNormalizeMixedPercents(t);
    t = t.replace(/([0-9√π∞°}%])([A-Za-z]{3,})/g, '$1 $2');
    t = t.replace(/(\})([A-Za-z]{3,})/g, '$1 $2');
    return t.replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
  }

  function lmsLooksLikeMathExpression(str) {
    var s = String(str || '').trim();
    if (!s || s.length > 220) return false;
    if (lmsLooksLikeProse(s)) return false;
    if (lmsLooksLikeRawLatex(s) || /\\(dots|ldots|cdots)\b/.test(s) || /\^\{/.test(s) || /\\frac/.test(s)) return true;
    var words = s.match(/[A-Za-z]{4,}/g) || [];
    if (words.length && !words.every(function (w) { return _MATH_WORD_RE.test(w); })) return false;
    return /[a-zA-Z0-9]\^|\^{|[_^]|[=+\-*]|\\[a-zA-Z]+|[A-Za-z]\d/.test(s) || /\blog\b|\bsin\b|\bcos\b/.test(s);
  }

  function lmsMathAlreadyInStem(stem, math) {
    function compact(s) {
      return String(s || '').replace(/\\\(|\\\)|\\\[|\\\]|\$+/g, '').replace(/\s+/g, '');
    }
    var key = compact(math);
    if (!key) return true;
    var hay = compact(stem);
    if (hay.indexOf(key) !== -1) return true;
    var flat = key.replace(/\^\{([^{}]+)\}/g, '^$1');
    return flat !== key && hay.indexOf(flat) !== -1;
  }

  function lmsMergeProseAndMath(text, latex) {
    var t = String(text || '').trim();
    var l = String(latex || '').trim();
    if (!l) return t;
    if (!t) return l;
    if (lmsLooksLikeProse(l)) return t;
    if (lmsMathAlreadyInStem(t, l)) return t;
    var stemLike = lmsLooksLikeProse(t) || /^(simplify|find|solve|evaluate|compute|expand|factor)\s*:?\s*$/i.test(t) || /[:?]\s*$/.test(t);
    if (stemLike) return (t + ' ' + l).trim();
    if (/\\frac|\^\{|\^/.test(l)) return l;
    return t || l;
  }

  function lmsPickDisplayText(text, latex) {
    var t = lmsUnwrapOuterMathIfProse(lmsUnsquashEnglish(lmsStripOptionLabelPrefix(lmsRecoverEatenBackslashCommands(text || ''))));
    var l = lmsUnwrapOuterMathIfProse(lmsUnsquashEnglish(lmsStripOptionLabelPrefix(lmsRecoverEatenBackslashCommands(latex || ''))));
    if (lmsIsBrokenMathBlob(l)) l = '';
    // Prose stem + separate math latex (common after PDF normalize).
    if (t && l && !lmsLooksLikeProse(l) && (lmsLooksLikeProse(t) || /^(simplify|find|solve|evaluate|compute|expand|factor)\s*:?\s*$/i.test(t)) && !lmsMathAlreadyInStem(t, l)) {
      var recoveredL = lmsRecoverLatex(l) || l;
      var recoveredT = lmsRecoverLatex(t) || t;
      return lmsMergeProseAndMath(recoveredT, recoveredL);
    }
    if (lmsLooksLikeProse(t)) return lmsRecoverLatex(t) || t;
    var recovered = lmsRecoverLatex(l || t);
    if (recovered && (/\^\{/.test(recovered) || /\\frac/.test(recovered) || /\\times/.test(recovered)) && !lmsLooksLikeProse(recovered) && !lmsLooksLikeMixedTextAndMath(recovered)) {
      return recovered;
    }
    if (lmsIsLabelOnly(t) && l && !lmsIsLabelOnly(l) && !lmsIsBrokenMathBlob(l)) return lmsRecoverLatex(l) || l;
    if (t && !lmsIsLabelOnly(t)) return lmsRecoverLatex(t) || t;
    return recovered || l || t;
  }

  /** Wrap recovered LaTeX so MathJax/KaTeX render exponents and fractions. */
  function prepareMathText(text, inline) {
    if (text == null) return '';
    // Repair BEFORE trim(): see lmsRecoverLatex for why the order matters.
    var s = lmsRecoverEatenBackslashCommands(String(text)).trim();
    if (!s) return '';
    s = lmsUnwrapOuterMathIfProse(lmsUnsquashEnglish(s));
    s = lmsStripInnerMathDelims(lmsNormalizeMixedPercents(s));
    if (lmsIsPlainPercent(s) || lmsIsMixedPercent(s)) return s.replace(/\\%/g, '%');
    if (/\$[\s\S]*\$|\\\(|\\\[|\\begin\{/.test(s)) {
      var withoutDelims = lmsUnsquashEnglish(s.replace(/\\\(|\\\)|\\\[|\\\]|\$+/g, ' '));
      if (lmsLooksLikeProse(withoutDelims)) {
        return lmsWrapMathIslands(s, inline);
      }
      return lmsMapOutsideMath(s, function (c) { return c; }).replace(/\\\(([\s\S]*?)\\\)/g, function (m, inner) {
        return '\\(' + lmsEscapePercentInMathBody(inner) + '\\)';
      }).replace(/\\\[([\s\S]*?)\\\]/g, function (m, inner) {
        return '\\[' + lmsEscapePercentInMathBody(inner) + '\\]';
      });
    }
    var recovered = lmsStripInnerMathDelims(lmsRecoverLatex(s) || s);
    var colon = recovered.match(/^((?:Simplify|Find|Solve|Evaluate|Compute|Expand|Factor)\s*:)\s*(.+)$/i);
    if (colon && !lmsLooksLikeProse(colon[2]) && (lmsLooksLikeMathExpression(colon[2]) || /\\frac|\^\{/.test(colon[2]))) {
      var body = colon[2].trim();
      var wrap = (!inline && /\\frac/.test(body)) ? ['\\[', '\\]'] : ['\\(', '\\)'];
      return colon[1] + ' ' + wrap[0] + lmsEscapePercentInMathBody(body) + wrap[1];
    }
    if (lmsIsPlainPercent(recovered) || lmsIsMixedPercent(recovered)) return recovered.replace(/\\%/g, '%');
    if (lmsLooksLikeMixedTextAndMath(recovered)) return lmsWrapMathIslands(recovered, inline);
    if (lmsLooksLikeProse(recovered)) return lmsWrapMathIslands(recovered, inline);
    // "x = 3 or x = -3", "a < b and b < c": a plain English connective joins
    // two math bits. Wrapping the whole string makes MathJax eat the spaces
    // and render the word as italic letters ("3orx"). Wrap each bit instead.
    if (_MATH_CONNECTIVE_RE.test(recovered)) {
      return lmsWrapMathIslands(recovered, inline);
    }
    if (lmsLooksLikeRawLatex(recovered) || lmsLooksLikeMathExpression(recovered) || /\\frac|\^\{/.test(recovered)) {
      var math = lmsEscapePercentInMathBody(recovered);
      if (!inline && /\\frac/.test(math)) return '\\[' + math + '\\]';
      return '\\(' + math + '\\)';
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
    var useFormatter = global.TeacherChatFormatter && typeof global.TeacherChatFormatter.formatChatResponse === 'function';
    if (useFormatter && !opts.inline && !opts.quiz) {
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

  /* LMS action bar (teacher dashboard) — collapse/expand toggle, persisted per browser */
  global.toggleLmsActionBar = function () {
    var bar = document.getElementById('lmsActionBar');
    var btn = document.getElementById('lmsActionBarToggle');
    if (!bar || !btn) return;
    var collapsed = bar.classList.toggle('collapsed');
    btn.setAttribute('aria-expanded', String(!collapsed));
    var label = collapsed ? 'Show class actions' : 'Hide class actions';
    btn.title = label;
    btn.setAttribute('aria-label', label);
    try { localStorage.setItem('lmsActionBarCollapsed', collapsed ? '1' : '0'); } catch (e) { /* ignore */ }
  };

  document.addEventListener('DOMContentLoaded', function () {
    var bar = document.getElementById('lmsActionBar');
    if (!bar) return;
    var collapsed = false;
    try { collapsed = localStorage.getItem('lmsActionBarCollapsed') === '1'; } catch (e) { /* ignore */ }
    if (!collapsed) return;
    bar.classList.add('collapsed');
    var btn = document.getElementById('lmsActionBarToggle');
    if (btn) {
      btn.setAttribute('aria-expanded', 'false');
      btn.title = 'Show class actions';
      btn.setAttribute('aria-label', 'Show class actions');
    }
  });

  /** Topic-wise score table for diagnostic / quiz results (FEATURE-01/02). */
  global.lmsFormatTopicBreakdownHtml = function (result, opts) {
    opts = opts || {};
    var rows = (result && (result.topic_breakdown || result.all_topics)) || [];
    if (!rows.length) return '';
    var title = opts.title || 'Topic-wise results';
    var esc = typeof global.escapeHtml === 'function' ? global.escapeHtml : function (s) { return String(s || ''); };
    var html = '<div class="lms-topic-breakdown">' +
      '<h4 class="lms-topic-breakdown-title">' + esc(title) + '</h4>' +
      '<table class="lms-topic-table"><thead><tr><th>Topic</th><th>Score</th><th>%</th></tr></thead><tbody>';
    rows.forEach(function (t) {
      var name = t.topic_name || t.name || ('Topic #' + (t.topic_id || ''));
      var correct = t.correct;
      var total = t.total;
      if ((correct == null || total == null) && t.question_ids && t.question_ids.length && t.score_percent != null) {
        total = t.question_ids.length;
        correct = Math.round((Number(t.score_percent) * total) / 100);
      }
      var frac = (correct != null && total != null)
        ? (Math.round(Number(correct)) + '/' + Math.round(Number(total)))
        : '—';
      var pct = t.score_percent != null
        ? (Math.round(Number(t.score_percent) * 10) / 10) + '%'
        : '—';
      html += '<tr><td>' + esc(name) + '</td><td>' + esc(frac) + '</td><td>' + esc(pct) + '</td></tr>';
    });
    html += '</tbody></table></div>';
    return html;
  };
})(window);
