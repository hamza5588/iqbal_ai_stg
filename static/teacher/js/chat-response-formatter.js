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
    text = normalizeTableMarkdown(text);

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
   * Run on the message content element after it's in the DOM:
   * - Prefer MathJax typesetting (better compatibility with AI-generated TeX)
   * - Fallback to KaTeX auto-render when MathJax is unavailable
   * - highlight.js on <pre><code>
   */
  function processRenderedContent(containerElement) {
    if (!containerElement) return;
    try {
      var usedMathJax = false;
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
        window.MathJax.typesetPromise([containerElement]).catch(function (err) {
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
    } catch (e) {
      console.warn('processRenderedContent failed:', e);
    }
  }

  global.TeacherChatFormatter = {
    formatChatResponse: formatChatResponse,
    processRenderedContent: processRenderedContent
  };
})(typeof window !== 'undefined' ? window : this);
