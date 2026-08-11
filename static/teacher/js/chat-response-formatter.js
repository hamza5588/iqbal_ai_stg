/**
 * Chat Response Formatter for Teacher Dashboard
 * - Markdown → HTML via marked
 * - XSS-safe rendering via DOMPurify
 * - Math formulas via KaTeX auto-render (run on container after insert)
 * - Code highlighting via highlight.js (run on container after insert)
 */
(function (global) {
  'use strict';

  function decodeHtmlEntities(str) {
    if (!str || typeof str !== 'string') return '';
    var textarea = document.createElement('textarea');
    textarea.innerHTML = str;
    return textarea.value;
  }

  /**
   * Remove internal lesson-finalization lines so they are never shown.
   */
  function stripLessonFinalizationFields(str) {
    if (!str || typeof str !== 'string') return str;
    return str
      .replace(/^\s*Lesson\s+Finalized\s*$/gim, '')
      .replace(/^\s*lesson_finalized\s*=\s*(?:true|false)\s*$/gm, '')
      .replace(/^\s*lesson_title\s*=\s*["'][^"']*["']\s*$/gm, '')
      .replace(/^\s*last_lesson_text\s*=\s*.*$/gm, '')
      .replace(/\n\s*\n\s*\n+/g, '\n\n')
      .trim();
  }

  /**
   * Ensure GFM table separator exists so marked parses tables.
   * If we see a line like "| A | B |" followed by "| x | y |" with no "| --- | --- |" in between, insert separator.
   */
  /**
   * Temporarily replace LaTeX/math segments so line-based structure fixes never touch equations.
   * Supports $$...$$, \\[...\\], \\(...\\), and single-line $...$ (inline).
   */
  function protectMathSegments(str) {
    if (!str || typeof str !== 'string') return { text: '', stash: [] };
    var stash = [];
    var text = str;
    function push(m) {
      stash.push(m);
      return '<<__MATH_' + (stash.length - 1) + '__>>';
    }
    text = text.replace(/\$\$[\s\S]*?\$\$/g, function (m) {
      return '\n\n' + push(m) + '\n\n';
    });
    text = text.replace(/\\\[[\s\S]*?\\\]/g, function (m) {
      return '\n\n' + push(m) + '\n\n';
    });
    text = text.replace(/\\\([\s\S]*?\\\)/g, push);
    text = text.replace(/\$[^$\n]+\$/g, push);
    return { text: text, stash: stash };
  }

  function restoreMathSegments(str, stash) {
    if (!stash || !stash.length) return str;
    var out = str;
    for (var i = 0; i < stash.length; i++) {
      out = out.split('<<__MATH_' + i + '__>>').join(stash[i]);
    }
    return out;
  }

  /**
   * Defense-in-depth: the model is instructed to always use \( \) / \[ \] for math, but if it
   * still emits malformed bare-bracket math like "[\text{New Length} = 20 - 2x]", the renderer
   * never recognizes it (brackets aren't a math delimiter). Detect brackets whose content looks
   * like LaTeX (backslash commands, ^{}, _{}) and convert them to \[ ... \]. Deliberately does
   * NOT touch markdown links "[text](url)" or reference-style definitions "[id]: url".
   */
  function convertBareBracketMathToLatex(str) {
    if (!str || typeof str !== 'string') return str;
    return str.replace(/\[([^\[\]\n]{1,400})\]/g, function (match, inner, offset, full) {
      var after = full.slice(offset + match.length, offset + match.length + 1);
      if (after === '(' || after === ':') return match;
      var looksLikeMath = /\\(text|frac|sqrt|sum|int|alpha|beta|gamma|theta|pi|pm|times|cdot|leq|geq|neq|approx|left|right|begin|end|overline|infty|partial|Delta|delta)\b/.test(inner)
        || /\^\{|_\{|\\[a-zA-Z]+\{/.test(inner);
      if (!looksLikeMath) return match;
      return '\\[ ' + inner.trim() + ' \\]';
    });
  }

  /**
   * Convert common AI "bold-only" lecture layout into real Markdown (headings + lists)
   * so marked emits <h1>–<h3>, <ul>, <li> instead of flat <p><strong>.
   * Runs only on text; math must already be protected.
   */
  /**
   * Display math split across lines becomes multiple Markdown paragraphs; marked then emits
   * separate <p>$$</p><p>tex</p><p>$$</p> and MathJax/KaTeX never see a matching pair.
   * Collapse whitespace inside $$...$$ for simple blocks (no \\begin{...}) onto one line before parsing.
   */
  function normalizeBlockMathForMarkdown(str) {
    if (!str || typeof str !== 'string') return str;
    return str.replace(/\$\$([\s\S]*?)\$\$/g, function (match, inner) {
      if (/\\begin\s*\{/.test(inner)) {
        return '$$' + inner + '$$';
      }
      var oneLine = inner.replace(/\s+/g, ' ').trim();
      return '$$ ' + oneLine + ' $$';
    });
  }

  /**
   * Merge <p>$$</p> + <p>formula</p> + <p>$$</p> (marked output) into one paragraph before typesetting.
   */
  function fixBrokenDisplayMathInDOM(root) {
    if (!root || !root.getElementsByTagName) return;
    var changed = true;
    var guard = 0;
    while (changed && guard < 50) {
      guard++;
      changed = false;
      var ps = root.getElementsByTagName('p');
      var list = [];
      for (var j = 0; j < ps.length; j++) {
        list.push(ps[j]);
      }
      for (var i = 0; i < list.length - 2; i++) {
        var p0 = list[i];
        var p1 = list[i + 1];
        var p2 = list[i + 2];
        if (!p0.parentNode || !p1.parentNode || !p2.parentNode) continue;
        var t0 = (p0.textContent || '').replace(/\u00a0/g, ' ').trim();
        var t2 = (p2.textContent || '').replace(/\u00a0/g, ' ').trim();
        if (t0 !== '$$' || t2 !== '$$') continue;
        var mid = (p1.textContent || '').trim();
        if (!mid) continue;
        p0.textContent = '$$ ' + mid + ' $$';
        p1.remove();
        p2.remove();
        changed = true;
        break;
      }
    }
  }

  function normalizePseudoLectureMarkdown(str) {
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
      // Already Markdown list / heading — do not double-transform
      if (/^[-*+]\s+/.test(t) || /^#{1,6}\s/.test(t)) {
        out.push(line);
        continue;
      }
      // Skip lines that still contain only math placeholders (no structural fix needed)
      if (/^<<__MATH_\d+__>>$/.test(t)) {
        out.push(line);
        continue;
      }

      // Main lecture title: **Lecture: ...** / **Lesson: ...**
      var lec = t.match(/^\*\*((?:Lecture|Lesson|Topic):\s*.+)\*\*$/i);
      if (lec) {
        out.push(lead + '# ' + lec[1].trim());
        continue;
      }

      // Numbered sections: **1. Title** or **2) Title**
      var num = t.match(/^\*\*(\d+[\.)]\s+.+)\*\*$/);
      if (num && !/\$\$|<<__MATH_/.test(t)) {
        out.push(lead + '## ' + num[1].trim());
        continue;
      }

      // Letter subsections: **A. Title** or **B) Title**
      var letter = t.match(/^\*\*([A-Z][\.)]\s+.+)\*\*$/);
      if (letter && !/\$\$|<<__MATH_/.test(t)) {
        out.push(lead + '### ' + letter[1].trim());
        continue;
      }

      // Definition / term lines: **Term:** rest  →  list item (bullets)
      var term = t.match(/^\*\*([^*]{1,120}):\*\*(\s*(?:\S[\s\S]*)?)$/);
      if (term) {
        var rest = term[2] || '';
        if (/^\s*$/.test(rest)) {
          out.push(lead + '- **' + term[1].trim() + ':**');
        } else {
          out.push(lead + '- **' + term[1].trim() + ':**' + rest);
        }
        continue;
      }

      out.push(line);
    }
    return out.join('\n');
  }

  /**
   * CommonMark: 4+ leading spaces on a line start an indented code block, so
   * "    ## Section" shows literal ## in <pre>. Strip excess indent only for
   * ATX headings that use two or more # (avoids touching "    # python comment").
   */
  function dedentAccidentalCodeBlockHeadings(str) {
    if (!str || typeof str !== 'string') return str;
    return str.replace(/^([ \t]{4,})(#{2,6})([^\r\n]*)$/gm, function (_full, _ind, hashes, rest) {
      return hashes + rest;
    });
  }

  /**
   * ATX headings require a space after the hash run (e.g. "##Title" is not a heading).
   */
  function ensureSpaceAfterHeadingHashes(str) {
    if (!str || typeof str !== 'string') return str;
    return str.replace(/^(\s{0,3})(#{2,6})([^\s#\r\n])([^\r\n]*)$/gm, function (full, ind, hashes, c1, rest) {
      return ind + hashes + ' ' + c1 + rest;
    });
  }

  /**
   * When a section title is jammed after punctuation on the same line, insert a
   * blank line so the ## line is parsed as a heading, not paragraph text.
   */
  function splitHeadingsAfterPunctuation(str) {
    if (!str || typeof str !== 'string') return str;
    return str.replace(/([.!?:;)])([ \t\u00a0]*)(#{2,6}[ \t\u00a0]+[^\r\n]+)/g, function (full, punct, sp, heading) {
      return punct + '\n\n' + heading.replace(/^[ \t\u00a0]+/, '');
    });
  }

  /**
   * Strip invisible chars that break "start of line" heading detection (BOM/ZWSP).
   */
  function stripLeadingInvisibleOnLines(str) {
    if (!str || typeof str !== 'string') return str;
    return str.split('\n').map(function (line) {
      return line.replace(/^[\uFEFF\u200B-\u200D\u2060]+/, '');
    }).join('\n');
  }

  /**
   * "Some intro text ## Next Section" on one line — insert a block break before ##.
   * Conservative: only when ## is preceded by alnum or closing paren and whitespace.
   */
  function splitMidParagraphAtxHeadings(str) {
    if (!str || typeof str !== 'string') return str;
    return str.replace(/([A-Za-z0-9\)])(\s+)(#{2,6}[ \t\u00a0]+[^\r\n]+)/g, function (_f, before, _sp, heading) {
      return before + '\n\n' + heading.replace(/^[ \t\u00a0]+/, '');
    });
  }

  function normalizeMarkdownAtxHeadings(str) {
    if (!str || typeof str !== 'string') return str;
    var s = stripLeadingInvisibleOnLines(str);
    s = splitHeadingsAfterPunctuation(s);
    s = splitMidParagraphAtxHeadings(s);
    s = dedentAccidentalCodeBlockHeadings(s);
    s = ensureSpaceAfterHeadingHashes(s);
    return s;
  }

  function normalizeTableMarkdown(str) {
    var lines = str.split('\n');
    var out = [];
    var i = 0;
    while (i < lines.length) {
      var line = lines[i];
      out.push(line);
      var trimmed = line.trim();
      var next = lines[i + 1];
      var nextTrimmed = next && lines[i + 1].trim();
      var looksLikeSeparator = /^\|[\s\-:|]+\|/.test(trimmed);
      if (!looksLikeSeparator && trimmed.indexOf('|') === 0 && trimmed.indexOf('|', 1) !== -1 && nextTrimmed && nextTrimmed.indexOf('|') === 0) {
        var nextIsSeparator = /^\|[\s\-:|]+\|/.test(nextTrimmed);
        if (!nextIsSeparator) {
          var pipeCount = (trimmed.match(/\|/g) || []).length;
          var sep = '|';
          for (var p = 1; p < pipeCount; p++) {
            sep += ' --- |';
          }
          sep += ' --- |';
          out.push(sep);
        }
      }
      i++;
    }
    return out.join('\n');
  }

  /**
   * Format raw AI response: Markdown → HTML (marked), then sanitize (DOMPurify).
   * Call processRenderedContent(containerElement) after inserting in DOM for KaTeX + highlight.js.
   */
  function formatChatResponse(rawText) {
    if (rawText == null || typeof rawText !== 'string') return '';
    var text = rawText.trim();
    if (!text) return '';

    text = decodeHtmlEntities(text);
    text = stripLessonFinalizationFields(text);
    text = convertBareBracketMathToLatex(text);
    text = normalizeTableMarkdown(text);
    text = normalizeBlockMathForMarkdown(text);

    var mathProtected = protectMathSegments(text);
    text = normalizePseudoLectureMarkdown(mathProtected.text);
    text = restoreMathSegments(text, mathProtected.stash);

    text = normalizeMarkdownAtxHeadings(text);

    var html;
    if (typeof marked !== 'undefined' && marked.parse) {
      try {
        marked.setOptions({
          gfm: true,
          breaks: true
        });
        html = marked.parse(text);
      } catch (e) {
        console.warn('marked.parse failed:', e);
        html = escapeAndBreaks(text);
      }
    } else {
      html = escapeAndBreaks(text);
    }

    if (typeof DOMPurify !== 'undefined' && DOMPurify.sanitize) {
      try {
        html = DOMPurify.sanitize(html, {
          ALLOWED_TAGS: ['p', 'br', 'strong', 'em', 'u', 's', 'a', 'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'pre', 'code', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'hr', 'span', 'div'],
          ALLOWED_ATTR: ['href', 'target', 'rel', 'class']
        });
      } catch (e) {
        console.warn('DOMPurify.sanitize failed:', e);
      }
    }
    return html;
  }

  function escapeAndBreaks(str) {
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML.replace(/\n/g, '<br>');
  }

  /**
   * Two animation-frame ticks so the browser has actually painted the post-typeset layout
   * before a caller measures scrollHeight (one rAF only guarantees "before next paint").
   */
  function _settleLayout() {
    return new Promise(function (resolve) {
      if (typeof requestAnimationFrame === 'function') {
        requestAnimationFrame(function () {
          requestAnimationFrame(resolve);
        });
      } else {
        setTimeout(resolve, 0);
      }
    });
  }

  /**
   * Run on the message content element after it's in the DOM:
   * - Prefer MathJax typesetting (better compatibility with AI-generated TeX)
   * - Fallback to KaTeX auto-render when MathJax is unavailable
   * - highlight.js on <pre><code>
   *
   * Returns a Promise that resolves once typesetting AND layout have settled — MathJax
   * typesetting is async and can change the element's height (e.g. display equations),
   * so callers that scroll-to-bottom right after inserting a message must wait on this
   * promise first, otherwise they scroll against a stale (pre-typeset) height and the
   * next appended message can land in the wrong position.
   */
  function processRenderedContent(containerElement) {
    if (!containerElement) return Promise.resolve();
    try {
      fixBrokenDisplayMathInDOM(containerElement);

      var usedMathJax = false;
      var typesetPromise = Promise.resolve();
      if (
        typeof window !== 'undefined' &&
        window.MathJax &&
        typeof window.MathJax.typesetPromise === 'function'
      ) {
        usedMathJax = true;
        // Clear previous math state for dynamic content updates
        if (typeof window.MathJax.typesetClear === 'function') {
          window.MathJax.typesetClear([containerElement]);
        }
        typesetPromise = window.MathJax.typesetPromise([containerElement]).catch(function (err) {
          console.warn('MathJax typeset failed:', err);
        });
      }

      if (!usedMathJax && typeof renderMathInElement === 'function') {
        try {
          renderMathInElement(containerElement, {
            delimiters: [
              { left: '$$', right: '$$', display: true },
              { left: '$', right: '$', display: false },
              { left: '\\[', right: '\\]', display: true },
              { left: '\\(', right: '\\)', display: false }
            ],
            throwOnError: false
          });
        } catch (err) {
          console.warn('KaTeX render failed:', err);
        }
      }

      if (typeof hljs !== 'undefined' && hljs.highlightAll) {
        containerElement.querySelectorAll('pre code').forEach(function (block) {
          try { hljs.highlightElement(block); } catch (e) { /* ignore */ }
        });
      }

      return typesetPromise.then(_settleLayout);
    } catch (e) {
      console.warn('processRenderedContent failed:', e);
      return Promise.resolve();
    }
  }

  global.TeacherChatFormatter = {
    formatChatResponse: formatChatResponse,
    processRenderedContent: processRenderedContent
  };
})(typeof window !== 'undefined' ? window : this);
