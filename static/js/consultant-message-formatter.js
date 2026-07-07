/**
 * Consultant message formatter — same stack as TeacherChatFormatter:
 * marked (GFM) + DOMPurify, with preprocessing for AI-style inline lists.
 *
 * Vendor libs are self-hosted under /static/js/vendor/ (loaded from apiBase on embed sites).
 */
(function (global) {
  'use strict';

  var depsPromise = null;
  var apiBase = '';

  function setApiBase(base) {
    apiBase = (base || '').replace(/\/$/, '');
  }

  function vendorUrl(file) {
    var root = apiBase || '';
    return root + '/static/js/vendor/' + file;
  }

  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var existing = document.querySelector('script[src="' + src + '"]');
      if (existing) {
        if (existing.getAttribute('data-loaded') === '1') {
          resolve();
          return;
        }
        existing.addEventListener('load', function () { resolve(); });
        existing.addEventListener('error', reject);
        return;
      }
      var s = document.createElement('script');
      s.src = src;
      s.async = false;
      s.onload = function () {
        s.setAttribute('data-loaded', '1');
        resolve();
      };
      s.onerror = function () { reject(new Error('Failed to load ' + src)); };
      document.head.appendChild(s);
    });
  }

  function ensureReady() {
    if (typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined') {
      return Promise.resolve();
    }
    if (depsPromise) return depsPromise;

    depsPromise = loadScript(vendorUrl('marked.umd.js'))
      .then(function () { return loadScript(vendorUrl('purify.min.js')); })
      .catch(function (err) {
        console.warn('ConsultantMessageFormatter: vendor load failed, using HTML fallback', err);
      });
    return depsPromise;
  }

  function unescapeMarkdown(str) {
    if (!str) return '';
    return str
      .replace(/\\(\*|_|#|`|\[|\]|\(|\))/g, '$1')
      .replace(/&ast;/g, '*');
  }

  /** "text: 1. **A** 2. **B**" → separate lines for GFM ordered lists */
  function normalizeInlineNumberedLists(str) {
    if (!str || !/\d+\.\s+/.test(str)) return str;
    var out = str.replace(/:\s*(\d+)\.\s+/g, ':\n\n$1. ');
    out = out.replace(/\s+(\d+)\.\s+(?=\*\*|[A-Za-z])/g, '\n$1. ');
    return out;
  }

  /** " - item" mid-sentence → newline bullets */
  function normalizeInlineBullets(str) {
    if (!str) return str;
    return str.replace(/([.!?])\s+-\s+(?=\*\*|[A-Za-z])/g, '$1\n\n- ');
  }

  function normalizePseudoMarkdown(str) {
    if (!str || typeof str !== 'string') return str;
    var lines = str.split('\n');
    var out = [];
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      var lead = (line.match(/^(\s*)/) || ['', ''])[1];
      var t = line.trim();
      if (!t) {
        out.push(line);
        continue;
      }
      if (/^[-*+]\s+/.test(t) || /^#{1,6}\s/.test(t) || /^\d+\.\s+/.test(t)) {
        out.push(line);
        continue;
      }
      var lec = t.match(/^\*\*((?:Lecture|Lesson|Topic|Section):\s*.+)\*\*$/i);
      if (lec) {
        out.push(lead + '## ' + lec[1].trim());
        continue;
      }
      var num = t.match(/^\*\*(\d+[\.)]\s+.+)\*\*$/);
      if (num) {
        out.push(lead + '## ' + num[1].trim());
        continue;
      }
      var boldOnly = t.match(/^\*\*([^*]+)\*\*$/);
      if (boldOnly && boldOnly[1].length < 120) {
        out.push(lead + '## ' + boldOnly[1].trim());
        continue;
      }
      var term = t.match(/^\*\*([^*]{1,120}):\*\*(\s*(?:\S[\s\S]*)?)$/);
      if (term) {
        out.push(lead + '- **' + term[1].trim() + ':**' + (term[2] || ''));
        continue;
      }
      out.push(line);
    }
    return out.join('\n');
  }

  function normalizeHeadings(str) {
    if (!str || typeof str !== 'string') return str;
    return str
      .replace(/^([ \t]{4,})(#{1,6})([^\r\n]*)$/gm, function (_f, _ind, hashes, rest) {
        return hashes + rest;
      })
      .replace(/^(\s{0,3})(#{1,6})([^\s#\r\n])([^\r\n]*)$/gm, function (_f, ind, hashes, c1, rest) {
        return ind + hashes + ' ' + c1 + rest;
      });
  }

  function preprocess(text) {
    var s = unescapeMarkdown(text);
    s = normalizeInlineNumberedLists(s);
    s = normalizeInlineBullets(s);
    s = normalizePseudoMarkdown(s);
    s = normalizeHeadings(s);
    return s;
  }

  function escapeHtml(str) {
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  /** Works without marked — inline bold + line-based lists */
  function formatRichHtmlFallback(str) {
    var lines = str.split('\n');
    var html = [];
    var inUl = false;
    var inOl = false;

    function closeLists() {
      if (inUl) { html.push('</ul>'); inUl = false; }
      if (inOl) { html.push('</ol>'); inOl = false; }
    }

    function inlineFmt(s) {
      return escapeHtml(s)
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/\*([^*]+)\*/g, '<em>$1</em>');
    }

    for (var i = 0; i < lines.length; i++) {
      var t = lines[i].trim();
      if (!t) {
        closeLists();
        continue;
      }
      var h = t.match(/^(#{1,3})\s+(.+)$/);
      if (h) {
        closeLists();
        var level = h[1].length;
        html.push('<h' + level + '>' + inlineFmt(h[2]) + '</h' + level + '>');
        continue;
      }
      var ol = t.match(/^(\d+)\.\s+(.+)$/);
      if (ol) {
        if (inUl) { html.push('</ul>'); inUl = false; }
        if (!inOl) { html.push('<ol>'); inOl = true; }
        html.push('<li>' + inlineFmt(ol[2]) + '</li>');
        continue;
      }
      var ul = t.match(/^[-*+]\s+(.+)$/);
      if (ul) {
        if (inOl) { html.push('</ol>'); inOl = false; }
        if (!inUl) { html.push('<ul>'); inUl = true; }
        html.push('<li>' + inlineFmt(ul[1]) + '</li>');
        continue;
      }
      closeLists();
      html.push('<p>' + inlineFmt(t) + '</p>');
    }
    closeLists();
    return html.join('');
  }

  function sanitize(html) {
    if (typeof DOMPurify !== 'undefined' && DOMPurify.sanitize) {
      try {
        return DOMPurify.sanitize(html, {
          ALLOWED_TAGS: [
            'p', 'br', 'strong', 'em', 'u', 'a', 'ul', 'ol', 'li',
            'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'pre', 'code', 'hr', 'span'
          ],
          ALLOWED_ATTR: ['href', 'target', 'rel', 'class']
        });
      } catch (e) { /* fall through */ }
    }
    return html;
  }

  function format(text) {
    if (text == null || typeof text !== 'string') return '';
    var raw = text.trim();
    if (!raw) return '';

    if (global.TeacherChatFormatter && global.TeacherChatFormatter.formatChatResponse) {
      return global.TeacherChatFormatter.formatChatResponse(raw);
    }

    var normalized = preprocess(raw);
    var html;

    if (typeof marked !== 'undefined' && marked.parse) {
      try {
        marked.setOptions({ gfm: true, breaks: true });
        html = marked.parse(normalized);
      } catch (e) {
        html = formatRichHtmlFallback(normalized);
      }
    } else {
      html = formatRichHtmlFallback(normalized);
    }

    return sanitize(html);
  }

  global.ConsultantMessageFormatter = {
    setApiBase: setApiBase,
    ensureReady: ensureReady,
    format: format,
    preprocess: preprocess
  };
})(typeof window !== 'undefined' ? window : this);
