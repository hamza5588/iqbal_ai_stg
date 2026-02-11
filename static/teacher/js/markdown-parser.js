/**
 * Markdown Parser & Renderer for Iqbal AI
 * Handles Markdown formatting including tables, headings, lists, and more
 * Uses a combination of regex patterns and DOM manipulation for safe rendering
 */

const MarkdownParser = (() => {
  /**
   * Parse and render Markdown content to HTML
   * @param {string} markdown - Markdown formatted text
   * @returns {string} HTML formatted content
   */
  function parse(markdown) {
    if (!markdown || typeof markdown !== 'string') {
      return '';
    }

    let html = markdown;

    // Process code blocks first (to protect from other processing)
    const codeBlocks = [];
    html = html.replace(/```([\s\S]*?)```/g, (match, code) => {
      codeBlocks.push(code.trim());
      return `__CODE_BLOCK_${codeBlocks.length - 1}__`;
    });

    // Process inline code
    const inlineCodeBlocks = [];
    html = html.replace(/`([^`]+)`/g, (match, code) => {
      inlineCodeBlocks.push(code);
      return `__INLINE_CODE_${inlineCodeBlocks.length - 1}__`;
    });

    // Process tables
    html = parseMarkdownTables(html);

    // Process headings (###, ## and # style)
    html = html.replace(/^### (.*?)$/gm, '<h3 class="markdown-h3">$1</h3>');
    html = html.replace(/^## (.*?)$/gm, '<h2 class="markdown-h2">$1</h2>');
    html = html.replace(/^# (.*?)$/gm, '<h1 class="markdown-h1">$1</h1>');

    // Process bold and italic
    html = html.replace(/\*\*\*(.*?)\*\*\*/g, '<strong><em>$1</em></strong>');
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/__(.*?)__/g, '<strong>$1</strong>');
    html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');
    html = html.replace(/_(.*?)_/g, '<em>$1</em>');

    // Process links
    html = html.replace(/\[(.*?)\]\((.*?)\)/g, '<a href="$2" target="_blank" class="markdown-link">$1</a>');

    // Process unordered lists
    html = parseUnorderedLists(html);

    // Process ordered lists
    html = parseOrderedLists(html);

    // Process line breaks
    html = html.replace(/\n/g, '<br>');

    // Restore code blocks
    html = html.replace(/__CODE_BLOCK_(\d+)__/g, (match, index) => {
      return `<pre class="markdown-code-block"><code>${escapeHtmlContent(codeBlocks[parseInt(index)])}</code></pre>`;
    });

    // Restore inline code
    html = html.replace(/__INLINE_CODE_(\d+)__/g, (match, index) => {
      return `<code class="markdown-inline-code">${escapeHtmlContent(inlineCodeBlocks[parseInt(index)])}</code>`;
    });

    // Process blockquotes
    html = html.replace(/^&gt; (.*?)$/gm, '<blockquote class="markdown-blockquote">$1</blockquote>');

    // Process horizontal rules
    html = html.replace(/^---$/gm, '<hr class="markdown-hr">');

    return html;
  }

  /**
   * Parse Markdown tables to HTML tables
   * Supports GFM (GitHub Flavored Markdown) table syntax
   */
  function parseMarkdownTables(text) {
    // Match table pattern: | header | header | ... \n | --- | --- | ... \n | row | row | ...
    const tableRegex = /\|(.+)\n\|[\s\-|:]+\n((?:\|.+\n?)*)/gm;
    
    return text.replace(tableRegex, (match, headerRow, bodyRows) => {
      try {
        // Parse header
        const headers = headerRow.split('|').filter(h => h.trim()).map(h => h.trim());
        
        // Parse body rows
        const rows = bodyRows.trim().split('\n').filter(r => r.trim()).map(row => {
          return row.split('|').filter(cell => cell.trim()).map(cell => cell.trim());
        });

        // Build HTML table
        let html = '<table class="markdown-table">';

        // Table header
        html += '<thead class="markdown-table-head">';
        html += '<tr>';
        headers.forEach(header => {
          // Support heading markup inside header cells (e.g. "# Main" or "## Sub")
          const headingMatch = header.match(/^(#{1,6})\s+(.*)$/);
          if (headingMatch) {
            const level = Math.min(6, headingMatch[1].length);
            const content = escapeHtmlContent(headingMatch[2]);
            html += `<th class="markdown-table-cell"><h${level} class=\"markdown-table-heading\">${content}</h${level}></th>`;
          } else {
            html += `<th class="markdown-table-cell">${escapeHtmlContent(header)}</th>`;
          }
        });
        html += '</tr>';
        html += '</thead>';

        // Table body
        html += '<tbody class="markdown-table-body">';
        rows.forEach(row => {
          html += '<tr>';
          row.forEach(cell => {
            // Allow heading-like content inside table cells to render as subheadings
            const cellHeadingMatch = cell.match(/^(#{1,6})\s+(.*)$/);
            if (cellHeadingMatch) {
              const level = Math.min(6, cellHeadingMatch[1].length);
              const content = escapeHtmlContent(cellHeadingMatch[2]);
              html += `<td class="markdown-table-cell"><h${level} class=\"markdown-table-subheading\">${content}</h${level}></td>`;
            } else {
              html += `<td class="markdown-table-cell">${escapeHtmlContent(cell)}</td>`;
            }
          });
          html += '</tr>';
        });
        html += '</tbody>';

        html += '</table>';
        return html;
      } catch (e) {
        console.error('Table parsing error:', e);
        return match; // Return original if parsing fails
      }
    });
  }

  /**
   * Parse unordered lists (- or * syntax)
   */
  function parseUnorderedLists(text) {
    const listRegex = /^[\s]*([-*])\s+(.*?)$/gm;
    let html = text;
    let inList = false;

    html = html.split('\n').map(line => {
      if (/^[\s]*([-*])\s+/.test(line)) {
        const item = line.replace(/^[\s]*([-*])\s+/, '').trim();
        if (!inList) {
          inList = true;
          return `<ul class="markdown-list"><li class="markdown-list-item">${escapeHtmlContent(item)}</li>`;
        }
        return `<li class="markdown-list-item">${escapeHtmlContent(item)}</li>`;
      } else {
        if (inList && line.trim() !== '') {
          inList = false;
          return `</ul>${line}`;
        }
        return line;
      }
    }).join('\n');

    // Close any open lists
    if (inList) {
      html += '</ul>';
    }

    return html;
  }

  /**
   * Parse ordered lists (1. 2. etc.)
   */
  function parseOrderedLists(text) {
    const listRegex = /^[\s]*(\d+)\.\s+(.*?)$/gm;
    let html = text;
    let inList = false;

    html = html.split('\n').map(line => {
      if (/^[\s]*(\d+)\.\s+/.test(line)) {
        const item = line.replace(/^[\s]*(\d+)\.\s+/, '').trim();
        if (!inList) {
          inList = true;
          return `<ol class="markdown-list"><li class="markdown-list-item">${escapeHtmlContent(item)}</li>`;
        }
        return `<li class="markdown-list-item">${escapeHtmlContent(item)}</li>`;
      } else {
        if (inList && line.trim() !== '') {
          inList = false;
          return `</ol>${line}`;
        }
        return line;
      }
    }).join('\n');

    // Close any open lists
    if (inList) {
      html += '</ol>';
    }

    return html;
  }

  /**
   * Escape HTML content to prevent XSS
   */
  function escapeHtmlContent(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  /**
   * Sanitize HTML - remove potentially dangerous scripts
   */
  function sanitizeHtml(html) {
    // Remove script tags
    html = html.replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, '');
    
    // Remove event handlers
    html = html.replace(/on\w+\s*=\s*["'][^"']*["']/gi, '');
    html = html.replace(/on\w+\s*=\s*[^\s>]*/gi, '');

    return html;
  }

  return {
    parse,
    escapeHtmlContent,
    sanitizeHtml
  };
})();

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
  module.exports = MarkdownParser;
}
