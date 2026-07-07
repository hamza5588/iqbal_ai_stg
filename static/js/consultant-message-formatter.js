/**
 * Markdown formatter for consultant embed + internal widget.
 * Loads marked + DOMPurify when needed (external client sites won't have them).
 */
(function (global) {
  'use strict';

  var depsPromise = null;

  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      if (document.querySelector('script[src="' + src + '"]')) {
        resolve();
        return;
      }
      var s = document.createElement('script');
      s.src = src;
      s.async = true;
      s.onload = function () { resolve(); };
      s.onerror = function () { reject(new Error('Failed to load ' + src)); };
      document.head.appendChild(s);
    });
  }

  function ensureReady() {
    if (typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined') {
      return Promise.resolve();
    }
    if (depsPromise) return depsPromise;
    depsPromise = loadScript('https://cdn.jsdelivr.net/npm/marked@14/lib/marked.umd.js')
      .then(function () {
        return loadScript('https://cdn.jsdelivr.net/npm/dompurify@3/dist/purify.min.js');
      });
    return depsPromise;
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
      if (/^[-*+]\s+/.test(t) || /^#{1,6}\s/.test(t)) {
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
      var letter = t.match(/^\*\*([A-Z][\.)]\s+.+)\*\*$/);
      if (letter) {
        out.push(lead + '### ' + letter[1].trim());
        continue;
      }
      var boldOnly = t.match(/^\*\*([^*]+)\*\*$/);
      if (boldOnly && boldOnly[1].length < 120) {
        out.push(lead + '## ' + boldOnly[1].trim());
        continue;
      }
      var term = t.match(/^\*\*([^*]{1,120}):\*\*(\s*(?:\S[\s\S]*)?)$/);
      if (term) {
        var rest = term[2] || '';
        out.push(lead + '- **' + term[1].trim() + ':**' + rest);
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
      })
      .replace(/([.!?:;)])([ \t]*)(#{1,6}[ \t]+[^\r\n]+)/g, function (_f, punct, _sp, heading) {
        return punct + '\n\n' + heading.replace(/^[ \t]+/, '');
      });
  }

  function escapeAndBreaks(str) {
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.*?)\*/g, '<em>$1</em>')
      .replace(/\n/g, '<br>');
  }

  function format(text) {
    if (text == null || typeof text !== 'string') return '';
    var raw = text.trim();
    if (!raw) return '';

    var normalized = normalizeHeadings(normalizePseudoMarkdown(raw));

    var html;
    if (typeof marked !== 'undefined' && marked.parse) {
      try {
        marked.setOptions({ gfm: true, breaks: true });
        html = marked.parse(normalized);
      } catch (e) {
        html = escapeAndBreaks(normalized);
      }
    } else {
      html = escapeAndBreaks(normalized);
    }

    if (typeof DOMPurify !== 'undefined' && DOMPurify.sanitize) {
      try {
        html = DOMPurify.sanitize(html, {
          ALLOWED_TAGS: [
            'p', 'br', 'strong', 'em', 'u', 'a', 'ul', 'ol', 'li',
            'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'pre', 'code', 'hr', 'span'
          ],
          ALLOWED_ATTR: ['href', 'target', 'rel', 'class']
        });
      } catch (e) { /* keep html */ }
    }
    return html;
  }

  global.ConsultantMessageFormatter = {
    ensureReady: ensureReady,
    format: format
  };
})(typeof window !== 'undefined' ? window : this);
