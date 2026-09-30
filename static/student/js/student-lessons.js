/* 06 Lesson viewer + lesson chat.
   Content: GET /api/lessons/lesson/:id/view (markdown/math, or the source PDF via pdf.js).
   Chat: one conversation per lesson (localStorage Student_chat_lesson_bindings chatId → lessonId, as before):
   /create_conversation, /update_conversation_title/:id, /get_messages/:id, /save_message, and
   POST /api/lessons/ask_question {lesson_id, question[, allow_rag]} incl. the teacher-PDF confirmation step.
   Formatting + download helpers are moved verbatim from the old student page. */
(function () {
  var BINDINGS_KEY = 'Student_chat_lesson_bindings';
  var CFG = window.STUDENT_CFG || {};
  var lesson = null;          // { id, title, subject, grade, version }
  var chatId = null;          // conversation bound to the open lesson
  var sending = false;
  var openSeq = 0;

  /* ── Formatting (from the old student page) ─────────────────────────── */
  function normalizeFormattingInput(content) {
    return String(content || '')
      .replace(/&lt;strong&gt;/gi, '**').replace(/&lt;\/strong&gt;/gi, '**')
      .replace(/<strong>/gi, '**').replace(/<\/strong>/gi, '**');
  }

  function normalizeLessonPlainTextToMarkdown(content) {
    var raw = String(content || '').replace(/\r\n/g, '\n');
    var sourceLines = raw.split('\n');
    var ALREADY_MD = /^(\s{0,3}(#{1,6}\s|[-*+]\s|\d+\.\s|>\s|\|)|\s*```)/;
    var HAS_MATH_DELIM = /\$\$?|\\\(|\\\[/;
    var BULLET_VARIANT = /^[•·▪▸▹◦‣⁃]\s*/;
    var SINGLE_GREEK = /^[α-ωΑ-ΩμσλδφψθξηζΦΨΩ∑∏√∫∂∇∞]$/u;
    var SINGLE_VAR = /^[a-zA-Z0-9]$/;
    var MATH_SYM_GUARD = /[=×÷°²³½¼¾^_]|[α-ωΑ-ΩΦΨ∑∏√∫∂∇∞]|\b[a-zA-Z]\s*[+\-*/^]\s*[a-zA-Z0-9]/u;
    var LOOKS_LIKE_NUM_OR_SYM_LIST = /^(\d+|[α-ωΑ-Ω])[\s.)]*$/u;

    function isEquationFragment(line, allowDigitOnly) {
      var t = (line || '').trim();
      if (!t) return false;
      if (HAS_MATH_DELIM.test(t)) return false;
      if (ALREADY_MD.test(t)) return false;
      if (/^\d$/.test(t) && !allowDigitOnly) return false;
      if (SINGLE_GREEK.test(t)) return true;
      if (SINGLE_VAR.test(t)) {
        if (!allowDigitOnly && /^[a-z]$/.test(t)) return false;
        return true;
      }
      if (t.length > 22) return false;
      if (/[:;]/.test(t)) return false;
      if (/[.!?]/.test(t) && /[a-z]{3,}/i.test(t)) return false;
      var words = t.split(/\s+/).filter(Boolean);
      if (words.length > 4) return false;
      return /[=+\-*/×÷^()°Δα-ωΑ-ΩΦΨ²³½¼¾\d]/.test(t);
    }
    function mergeEquationRun(run) {
      var joined = run.join(' ');
      var hasEquals = run.some(function (s) { return s.includes('='); });
      var allVeryShort = run.every(function (s) { return s.length <= 3; });
      var mostlySymbols = run.every(function (s) { return /^[\d.a-zA-Zα-ωΑ-ΩΦΨ+\-*/^()°²³½¼¾_=]+$/u.test(s); });
      if (hasEquals || run.length >= 3 || (run.length >= 2 && allVeryShort && mostlySymbols)) return '$$' + joined + '$$';
      return run.map(function (s) { return '$' + s + '$'; }).join(' ');
    }

    var lines = [];
    for (var i = 0; i < sourceLines.length; i++) {
      var t = (sourceLines[i] || '').trim();
      if (isEquationFragment(t, false)) {
        var run = [t];
        var j = i + 1;
        while (j < sourceLines.length && isEquationFragment((sourceLines[j] || '').trim(), true)) {
          run.push((sourceLines[j] || '').trim());
          j++;
        }
        if (run.length >= 2) { lines.push(mergeEquationRun(run)); i = j - 1; continue; }
        if (SINGLE_GREEK.test(t) || (SINGLE_VAR.test(t) && /[0-9]/.test(t))) { lines.push('$' + t + '$'); continue; }
      }
      lines.push(sourceLines[i] || '');
    }

    var out = [];
    var shortHeadingLike = /^[A-Z][A-Za-z0-9,&()\-/'\s]{2,90}$/;
    for (var k = 0; k < lines.length; k++) {
      var original = lines[k] || '';
      var line = original.trim();
      if (!line) { out.push(''); continue; }
      if (ALREADY_MD.test(line) || HAS_MATH_DELIM.test(line)) { out.push(original); continue; }
      var ord = line.match(/^(\d{1,3})[\.)]\s+(.+)$/);
      if (ord && !HAS_MATH_DELIM.test(line)) { out.push(ord[1] + '. ' + ord[2].trim()); continue; }
      var indBullet = original.match(/^(\s{2,})([-*+•·▪▸▹◦‣⁃])\s+(\S.*)$/);
      if (indBullet) {
        var depth = Math.min(8, Math.floor(indBullet[1].length / 2));
        out.push('  '.repeat(depth) + '- ' + indBullet[3].trim());
        continue;
      }
      if (BULLET_VARIANT.test(line)) { out.push('- ' + line.replace(BULLET_VARIANT, '').trim()); continue; }
      var wordsN = line.split(/\s+/).filter(Boolean).length;
      var next = (lines[k + 1] || '').trim();
      if (line.endsWith(':') && wordsN <= 10 && line.length <= 80 && !MATH_SYM_GUARD.test(line) && !LOOKS_LIKE_NUM_OR_SYM_LIST.test(line)) {
        out.push('### ' + line.slice(0, -1).trim());
        continue;
      }
      var titleLike = shortHeadingLike.test(line) && wordsN <= 8 && next && !MATH_SYM_GUARD.test(line) &&
        !LOOKS_LIKE_NUM_OR_SYM_LIST.test(line) && !/^[\d\s+*/^\-=().]+$/u.test(line);
      if (titleLike) { out.push('## ' + line); continue; }
      out.push(original);
    }
    return out.join('\n').replace(/\n{3,}/g, '\n\n').trim();
  }

  function formatRichContent(content) {
    var normalized = normalizeFormattingInput(content);
    if (typeof TeacherChatFormatter !== 'undefined' && TeacherChatFormatter.formatChatResponse) {
      return TeacherChatFormatter.formatChatResponse(normalized);
    }
    if (typeof MarkdownParser !== 'undefined' && MarkdownParser.parse && MarkdownParser.sanitizeHtml) {
      return MarkdownParser.sanitizeHtml(MarkdownParser.parse(normalized));
    }
    return escapeHtml(normalized).replace(/\n/g, '<br>');
  }
  function applyRenderedFormatting(el) {
    if (!el) return;
    if (typeof TeacherChatFormatter !== 'undefined' && TeacherChatFormatter.processRenderedContent) {
      TeacherChatFormatter.processRenderedContent(el);
      return;
    }
    if (window.typesetMathIn) { window.typesetMathIn(el).catch(console.warn); return; }
    if (window.MathJax && window.MathJax.typesetPromise) window.MathJax.typesetPromise([el]).catch(function () {});
  }
  window.formatRichContent = formatRichContent;
  window.applyRenderedFormatting = applyRenderedFormatting;

  function formatLessonContentForView(content) {
    if (!content || (typeof content === 'string' && content.trim() === '')) return '<p class="sd-desc"><em>No content available</em></p>';
    var raw = typeof content === 'string' ? content : String(content);
    if (raw.includes('"response_type"') || raw.includes('"answer"')) {
      try { var p = JSON.parse(raw); raw = p.answer || p.content || raw; } catch (e) { /* not JSON */ }
    }
    raw = raw
      .replace(/^Lesson Title:.*$/gm, '').replace(/^Grade Level:.*$/gm, '').replace(/^Focus Area:.*$/gm, '')
      .replace(/^Subject:.*$/gm, '').replace(/^Topic:.*$/gm, '').replace(/^Duration:.*$/gm, '').replace(/^Materials:.*$/gm, '')
      .replace(/\n\s*\n\s*\n+/g, '\n\n').trim();
    raw = normalizeLessonPlainTextToMarkdown(raw);
    return '<div class="markdown-content lesson-content-formatted">' + formatRichContent(raw) + '</div>';
  }

  async function renderStudentLessonPdf(container, pdfUrl) {
    if (!container || !pdfUrl) return;
    var safeUrl = String(pdfUrl).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
    container._pdfRenderGeneration = (container._pdfRenderGeneration || 0) + 1;
    var generation = container._pdfRenderGeneration;
    container.innerHTML = '<div class="source-pdf-viewer"><div class="source-pdf-toolbar"><span><i class="fas fa-file-pdf"></i> Original PDF view</span>' +
      '<a href="' + safeUrl + '" target="_blank" rel="noopener noreferrer">Open PDF</a></div><div class="sd-spinner"></div></div>';
    if (!window.pdfjsLib) {
      container.innerHTML = '<div class="source-pdf-viewer"><p style="color:#b91c1c;">PDF viewer library did not load.</p>' +
        '<a href="' + safeUrl + '" target="_blank" rel="noopener noreferrer">Open PDF directly</a></div>';
      return;
    }
    try {
      pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/2.16.105/pdf.worker.min.js';
      var viewer = container.querySelector('.source-pdf-viewer');
      var pdf = await pdfjsLib.getDocument({ url: pdfUrl, withCredentials: true }).promise;
      if (container._pdfRenderGeneration !== generation) return;
      viewer.innerHTML = '<div class="source-pdf-toolbar"><span><i class="fas fa-file-pdf"></i> Original PDF view &middot; ' +
        pdf.numPages + ' page' + (pdf.numPages === 1 ? '' : 's') + '</span><a href="' + safeUrl + '" target="_blank" rel="noopener noreferrer">Open PDF</a></div>';
      await new Promise(function (resolve) { requestAnimationFrame(resolve); });
      var measured = container.getBoundingClientRect().width || 700;
      var availableWidth = Math.max(260, Math.min(measured - 8, 1180));
      var outputScale = Math.min(window.devicePixelRatio || 1, 2);
      for (var n = 1; n <= pdf.numPages; n += 1) {
        if (container._pdfRenderGeneration !== generation) return;
        var page = await pdf.getPage(n);
        var base = page.getViewport({ scale: 1 });
        var viewport = page.getViewport({ scale: Math.min(1.55, Math.max(0.5, availableWidth / base.width)) });
        var wrap = document.createElement('div');
        wrap.className = 'source-pdf-page';
        wrap.style.width = viewport.width + 'px';
        wrap.style.height = viewport.height + 'px';
        var canvas = document.createElement('canvas');
        canvas.width = Math.floor(viewport.width * outputScale);
        canvas.height = Math.floor(viewport.height * outputScale);
        canvas.style.width = viewport.width + 'px';
        canvas.style.height = viewport.height + 'px';
        wrap.appendChild(canvas);
        var linkLayer = document.createElement('div');
        linkLayer.className = 'source-pdf-link-layer';
        wrap.appendChild(linkLayer);
        viewer.appendChild(wrap);
        await page.render({ canvasContext: canvas.getContext('2d'), viewport: viewport,
          transform: outputScale === 1 ? null : [outputScale, 0, 0, outputScale, 0, 0] }).promise;
        var annotations = await page.getAnnotations({ intent: 'display' });
        annotations.forEach(function (a) {
          var href = a.url || a.unsafeUrl || '';
          if (!href || !a.rect) return;
          var rect = viewport.convertToViewportRectangle(a.rect);
          var link = document.createElement('a');
          link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.title = href;
          link.style.left = Math.min(rect[0], rect[2]) + 'px';
          link.style.top = Math.min(rect[1], rect[3]) + 'px';
          link.style.width = Math.abs(rect[0] - rect[2]) + 'px';
          link.style.height = Math.abs(rect[1] - rect[3]) + 'px';
          linkLayer.appendChild(link);
        });
      }
    } catch (error) {
      console.warn('Student lesson PDF render failed', error);
      container.innerHTML = '<div class="source-pdf-viewer"><p style="color:#b91c1c;">Could not render this PDF preview.</p>' +
        '<a href="' + safeUrl + '" target="_blank" rel="noopener noreferrer">Open PDF directly</a></div>';
    }
  }

  /* ── Downloads (from the old student page) ── */
  function getFilenameFromContentDisposition(cd, fallbackName) {
    if (!cd) return fallbackName;
    var utf8 = cd.match(/filename\*=UTF-8''([^;\n]+)/i);
    if (utf8 && utf8[1]) {
      try { return decodeURIComponent(utf8[1].replace(/['"]/g, '').trim()); } catch (_) { return utf8[1].replace(/['"]/g, '').trim(); }
    }
    var simple = cd.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/i);
    if (simple && simple[1]) return simple[1].replace(/['"]/g, '').trim();
    return fallbackName;
  }
  async function download(url, fallbackName, okMsg) {
    var res = await fetch(url, { method: 'GET', credentials: 'include' });
    if (!res.ok) {
      var data = await res.json().catch(function () { return {}; });
      throw new Error(data.error || ('Download failed (' + res.status + ')'));
    }
    var blob = await res.blob();
    var href = window.URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = href;
    a.download = getFilenameFromContentDisposition(res.headers.get('Content-Disposition'), fallbackName);
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(href);
    window.showToast(okMsg, 'success', 2000);
  }
  window.downloadLessonPPT = function (lessonId) {
    if (!lessonId) { window.showToast('No lesson selected for download.', 'error', 3000); return; }
    window.showToast('Preparing PowerPoint...', 'info', 1500);
    download('/api/lessons/download_lesson_ppt/' + encodeURIComponent(String(lessonId)), 'lesson_' + lessonId + '.pptx', 'Lesson downloaded as PPT!')
      .catch(function (err) { window.showToast(err.message || 'Download failed', 'error', 3000); });
  };
  window.downloadLessonDocx = function (lessonId) {
    if (!lessonId) { window.showToast('No lesson selected for download.', 'error', 3000); return; }
    window.showToast('Preparing Word document...', 'info', 1500);
    download('/api/lessons/download_lesson/' + encodeURIComponent(String(lessonId)), 'lesson_' + lessonId + '.docx', 'Lesson downloaded as DOCX!')
      .catch(function (err) { window.showToast(err.message || 'Failed to download lesson.', 'error', 3000); });
  };

  /* ── Conversation ↔ lesson bindings (localStorage, same key as before) ── */
  function readBindings() {
    try { return JSON.parse(localStorage.getItem(BINDINGS_KEY) || '{}') || {}; } catch (e) { return {}; }
  }
  function writeBindings(map) { try { localStorage.setItem(BINDINGS_KEY, JSON.stringify(map)); } catch (e) { /* ignore */ } }
  window.sdLessonForConversation = function (id) { return readBindings()[String(id)] || null; };
  window.sdUnbindConversation = function (id) {
    var map = readBindings();
    if (map[String(id)] != null) { delete map[String(id)]; writeBindings(map); }
    if (String(chatId) === String(id)) chatId = null;
  };

  window.sdListConversations = async function () {
    var res = await fetch('/get_conversations?limit=200', { credentials: 'include' });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok) throw new Error(data.error || 'Failed to load conversations');
    return Array.isArray(data.conversations) ? data.conversations : [];
  };

  async function findConversationForLesson(lessonId) {
    var map = readBindings();
    var ids = Object.keys(map).filter(function (cid) { return String(map[cid]) === String(lessonId); });
    if (!ids.length) return null;
    try {
      var convs = await window.sdListConversations();
      var live = {};
      convs.forEach(function (c) { live[String(c.id)] = true; });
      for (var i = 0; i < ids.length; i++) if (live[ids[i]]) return ids[i];
    } catch (e) { /* fall through */ }
    return null;
  }

  async function ensureConversation() {
    if (chatId) return chatId;
    var res = await fetch('/create_conversation', {
      method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'New Conversation' })
    });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok || !data.conversation_id) throw new Error(data.error || 'Failed to start a chat');
    chatId = String(data.conversation_id);
    if (lesson) {
      var map = readBindings();
      map[chatId] = lesson.id;
      writeBindings(map);
      var title = 'Conversation with ' + (String(lesson.title || 'Lesson').replace(/\s+/g, ' ').trim() || 'Lesson');
      fetch('/update_conversation_title/' + encodeURIComponent(chatId), {
        method: 'PUT', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: title })
      }).catch(function () { /* title is cosmetic */ });
    }
    return chatId;
  }

  function persist(role, content) {
    if (!chatId) return;
    fetch('/save_message', {
      method: 'POST', credentials: 'include', keepalive: true, headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conversation_id: parseInt(chatId, 10), role: role === 'assistant' ? 'bot' : role, message: String(content || '') })
    }).catch(function (e) { console.warn('Failed to persist message', e); });
  }

  /* ── Chat rendering ── */
  function msgBox() { return document.getElementById('sdLessonChatMessages'); }
  function scrollChat() { var b = msgBox(); if (b) requestAnimationFrame(function () { b.scrollTop = b.scrollHeight; }); }
  function botIcon() { return '<div class="sd-bot-ic"><img src="' + escapeHtml((CFG.icons || {}).chatBot || '') + '" alt=""></div>'; }

  function appendBubble(role, html, opts) {
    var box = msgBox();
    if (!box) return null;
    var el = document.createElement('div');
    el.className = 'sd-ai-bubble' + (role === 'user' ? ' user' : '') + ((opts && opts.cls) ? ' ' + opts.cls : '');
    el.innerHTML = (role === 'user' ? '<div class="sd-bot-ic">' + escapeHtml((CFG.username || 'S').charAt(0).toUpperCase()) + '</div>' : botIcon()) +
      '<div class="sd-msg">' + html + '</div>';
    box.appendChild(el);
    applyRenderedFormatting(el.querySelector('.sd-msg'));
    scrollChat();
    return el;
  }
  function addAssistant(text, save) {
    var el = appendBubble('bot', formatRichContent(text || ''));
    if (save !== false) persist('assistant', text);
    if (tts.active && window.speechSynthesis) speak(String(text || '').replace(/<[^>]*>/g, ''));
    return el;
  }
  function welcomeHtml() {
    return 'Hello! 👋<br>How can I help you with this lesson today?' +
      (lesson ? '<div class="sd-hint-line">I answer using <b>' + escapeHtml(lesson.title) + '</b>.</div>' : '');
  }

  async function loadConversationMessages(id) {
    var box = msgBox();
    if (!box) return;
    box.innerHTML = '<div class="sd-spinner"></div>';
    var messages = [];
    try {
      var res = await fetch('/get_messages/' + parseInt(id, 10), { credentials: 'include' });
      var data = await res.json().catch(function () { return {}; });
      if (res.ok && Array.isArray(data.messages)) messages = data.messages;
    } catch (e) { /* show empty */ }
    box.innerHTML = '';
    appendBubble('bot', welcomeHtml());
    messages.forEach(function (m) {
      var content = m.message || m.content || '';
      if (typeof content === 'string' && content.indexOf('id="current-lesson-metadata"') !== -1) return; // legacy pinned block
      appendBubble(m.role === 'user' ? 'user' : 'bot', formatRichContent(content));
    });
  }

  /* ── Open a lesson ── */
  window.viewLesson = async function (lessonId, opts) {
    opts = opts || {};
    if (!lessonId) { window.showToast('No lesson selected', 'error', 2000); return; }
    var seq = ++openSeq;
    stopVoice(); stopSpeaking();
    window.currentViewLessonId = lessonId;
    lesson = { id: lessonId, title: opts.title || 'Lesson' };
    chatId = opts.chatId ? String(opts.chatId) : null;
    document.getElementById('viewLessonTitle').textContent = lesson.title;
    document.getElementById('sdLessonHeading').textContent = lesson.title;
    document.getElementById('viewLessonSubtitle').textContent = '';
    var contentEl = document.getElementById('viewLessonCurrent');
    contentEl.innerHTML = '<div class="sd-spinner"></div>';
    var box = msgBox();
    if (box) box.innerHTML = '';
    if (typeof window.sdOpenLessonView === 'function') window.sdOpenLessonView();

    try {
      var res = await fetch('/api/lessons/lesson/' + lessonId + '/view', { credentials: 'include' });
      if (!res.ok) throw new Error('Failed to load lesson');
      var data = await res.json();
      if (seq !== openSeq) return;
      var l = data.lesson || {};
      var versions = Array.isArray(data.versions) ? data.versions.slice() : [];
      versions.sort(function (a, b) { return (b.version_number || b.version || 0) - (a.version_number || a.version || 0); });
      var cur = versions[0] || l;
      lesson = {
        id: l.id || lessonId,
        title: l.title || 'Untitled Lesson',
        subject: l.focus_area || l.subject || 'General',
        grade: l.grade_level || l.grade || 'N/A',
        version: cur.version_number || cur.version || l.version_number || l.version || 1
      };
      document.getElementById('viewLessonTitle').textContent = lesson.title;
      document.getElementById('sdLessonHeading').textContent = lesson.title;
      document.getElementById('viewLessonSubtitle').textContent = lesson.subject + ' • Grade ' + lesson.grade + ' • v' + lesson.version;
      var pdfUrl = cur.source_pdf_url || l.source_pdf_url || null;
      if (pdfUrl) {
        renderStudentLessonPdf(contentEl, pdfUrl);
      } else {
        contentEl.innerHTML = formatLessonContentForView((cur.content || l.content || '').trim());
        applyRenderedFormatting(contentEl);
      }
    } catch (err) {
      if (seq !== openSeq) return;
      contentEl.innerHTML = '<p style="color:#b91c1c;">Failed to load lesson. Please try again.</p>';
      window.showToast(err.message || 'Failed to load lesson', 'error', 4000);
    }

    if (!chatId) chatId = await findConversationForLesson(lesson.id);
    if (seq !== openSeq) return;
    if (chatId) await loadConversationMessages(chatId);
    else if (box) { box.innerHTML = ''; appendBubble('bot', welcomeHtml()); }
    if (opts.focusChat) {
      var input = document.getElementById('messageInput');
      if (input) input.focus();
    }
  };
  /* Old "Chat" row action: open the lesson with its chat focused. */
  window.chatWithLesson = function (lessonId, title) { return window.viewLesson(lessonId, { focusChat: true, title: title }); };
  window.openLessonChat = function () { var i = document.getElementById('messageInput'); if (i) i.focus(); };

  /* Open a saved conversation from AI Tutor → Chat History. */
  window.sdOpenConversation = async function (id, title) {
    var lessonId = window.sdLessonForConversation(id);
    if (lessonId) return window.viewLesson(lessonId, { chatId: id, focusChat: true });
    // Conversation not linked to a lesson on this device: show it read-only in the viewer.
    ++openSeq;
    lesson = null;
    chatId = String(id);
    window.currentViewLessonId = null;
    document.getElementById('viewLessonTitle').textContent = title || 'Conversation';
    document.getElementById('sdLessonHeading').textContent = title || 'Conversation';
    document.getElementById('viewLessonSubtitle').textContent = '';
    document.getElementById('viewLessonCurrent').innerHTML =
      '<p class="sd-desc">This conversation is not linked to a lesson on this device. Open the lesson from <b>My Classes</b> to keep asking questions about it.</p>';
    if (typeof window.sdOpenLessonView === 'function') window.sdOpenLessonView();
    await loadConversationMessages(id);
  };

  window.closeViewLessonModal = function () {
    stopVoice(); stopSpeaking();
    window.currentViewLessonId = null;
    if (typeof window.sdCloseLessonView === 'function') window.sdCloseLessonView();
  };

  /* ── Ask a question ── */
  window.autoResizeTextarea = function (ta) { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 120) + 'px'; };
  window.handleKeyDown = function (event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      if (!event.repeat && document.getElementById('messageInput').value.trim()) window.sendMessage();
    }
  };

  function typingBubble() {
    return appendBubble('bot', '<span class="sd-typing"><span></span><span></span><span></span></span>', { cls: 'sd-typing-row' });
  }

  window.sendMessage = async function () {
    if (sending) return;
    var input = document.getElementById('messageInput');
    var message = (input.value || '').trim();
    if (!message) return;
    if (!lesson || !lesson.id) {
      window.showToast('Open a lesson first, then ask your question about it.', 'warning', 3500);
      return;
    }
    sending = true;
    var sendBtn = document.getElementById('sendBtn');
    if (sendBtn) sendBtn.disabled = true;
    var lessonId = lesson.id;
    try {
      try { await ensureConversation(); } catch (e) { window.showToast(e.message || 'Failed to start chat', 'error', 2500); return; }
      input.value = '';
      window.autoResizeTextarea(input);
      appendBubble('user', formatRichContent(message));
      persist('user', message);
      var typing = typingBubble();
      var data = {}, ok = false;
      try {
        var res = await fetch('/api/lessons/ask_question', {
          method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ lesson_id: lessonId, question: message })
        });
        ok = res.ok;
        data = await res.json().catch(function () { return {}; });
      } catch (e) {
        if (typing) typing.remove();
        addAssistant('Sorry, something went wrong while asking about this lesson. Please try again.');
        return;
      }
      if (typing) typing.remove();
      if (ok && data && data.status === 'needs_rag_confirmation' && data.permission_request) {
        askRagPermission(lessonId, message, data.permission_request.message);
      } else if (ok && data && data.answer != null) {
        addAssistant(data.answer);
      } else {
        addAssistant(data.error || 'Sorry, I couldn\'t process that question. Please try again.');
      }
    } finally {
      sending = false;
      if (sendBtn) sendBtn.disabled = false;
    }
  };

  /* The answer isn't in the lesson summary → ask before searching the teacher's PDF (as before). */
  function askRagPermission(lessonId, message, promptMsg) {
    var el = appendBubble('bot',
      '<p style="margin:0 0 6px;">' + escapeHtml(promptMsg || 'I could not find this fully in the lesson summary. Do you want me to search the teacher-uploaded PDF to answer this question?') + '</p>' +
      '<div class="sd-rag-actions"><button type="button" class="sd-btn sd-btn-primary sd-btn-xs" data-rag="yes">Yes, use teacher PDF</button>' +
      '<button type="button" class="sd-btn sd-btn-outline sd-btn-xs" data-rag="no">No</button></div>');
    if (!el) return;
    var bubble = el.querySelector('.sd-msg');
    var yes = el.querySelector('[data-rag="yes"]');
    var no = el.querySelector('[data-rag="no"]');
    yes.onclick = function () {
      yes.disabled = no.disabled = true;
      bubble.innerHTML = '<em class="sd-desc">Searching PDF...</em>';
      fetch('/api/lessons/ask_question', {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lesson_id: lessonId, question: message, allow_rag: true })
      }).then(function (r) { return r.json(); }).then(function (d) {
        var ans = (d && d.answer != null) ? d.answer : (d.error || 'Sorry, I couldn\'t process that question. Please try again.');
        bubble.innerHTML = formatRichContent(ans);
        applyRenderedFormatting(bubble);
        persist('assistant', ans);
        scrollChat();
      }).catch(function () {
        var err = 'Sorry, I couldn\'t process that question. Please try again.';
        bubble.innerHTML = '<p style="color:#b91c1c;margin:0;">' + err + '</p>';
        persist('assistant', err);
      });
    };
    no.onclick = function () {
      yes.disabled = no.disabled = true;
      var t = lesson && lesson.title ? escapeHtml(lesson.title) : 'this lesson';
      bubble.innerHTML = '<p style="margin:0 0 6px;">No problem — we will continue with the lecture flow without using the teacher PDF.</p>' +
        '<p style="margin:0 0 6px;">Let\'s stay on <strong>' + t + '</strong>. Try:</p>' +
        '<ul style="margin:0;padding-left:18px;"><li>Explain the key concept from this lesson.</li><li>Show one worked example from this lesson.</li><li>Summarize this lesson in 3 points.</li></ul>';
      persist('assistant', 'No problem — we\'ll continue with the lecture flow without using the teacher PDF.');
      scrollChat();
    };
  }

  /* ── Voice input (Web Speech) — shared with the AI Tutor ── */
  var Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  window.sdAttachVoice = function (inputId, btnId, statusId) {
    var state = { rec: null, listening: false };
    var btn = document.getElementById(btnId);
    if (!Rec) {
      if (btn) { btn.disabled = true; btn.title = 'Speech recognition not supported'; btn.innerHTML = '<i class="fas fa-microphone-slash"></i>'; }
      return { toggle: function () { alert('Speech recognition is not supported in your browser.'); }, stop: function () {} };
    }
    function setUi(on) {
      if (btn) { btn.classList.toggle('active', on); btn.innerHTML = on ? '<i class="fas fa-microphone-slash"></i>' : '<i class="fas fa-microphone"></i>'; }
      var st = statusId && document.getElementById(statusId);
      if (st) st.classList.toggle('sd-hidden', !on);
    }
    function stop() { if (state.rec && state.listening) { state.listening = false; state.rec.stop(); } setUi(false); }
    function toggle() {
      if (state.listening) { stop(); return; }
      state.rec = new Rec();
      state.rec.continuous = false;
      state.rec.interimResults = true;
      state.rec.lang = 'en-US';
      state.rec.onresult = function (event) {
        var input = document.getElementById(inputId);
        if (input) input.value = Array.from(event.results).map(function (r) { return r[0].transcript; }).join('');
      };
      state.rec.onend = function () { if (state.listening) { try { state.rec.start(); } catch (e) { stop(); } } else setUi(false); };
      state.rec.onerror = function (event) { stop(); window.showToast('Speech recognition error: ' + event.error, 'error', 3000); };
      state.listening = true;
      setUi(true);
      try { state.rec.start(); } catch (e) { stop(); }
    }
    return { toggle: toggle, stop: stop };
  };
  var voice = null;
  window.toggleVoiceInput = function () { if (!voice) voice = window.sdAttachVoice('messageInput', 'micBtn', 'voiceStatus'); voice.toggle(); };
  window.stopVoiceInput = function () { if (voice) voice.stop(); };
  function stopVoice() { window.stopVoiceInput(); }

  /* ── Read aloud (speechSynthesis) ── */
  var tts = { active: false };
  function setSpeakerUi(on) {
    var b = document.getElementById('speakerBtn');
    if (b) { b.classList.toggle('active', on); b.innerHTML = on ? '<i class="fas fa-volume-mute"></i>' : '<i class="fas fa-volume-up"></i>'; }
  }
  function speak(text) {
    var u = new SpeechSynthesisUtterance(text);
    u.onend = function () { tts.active = false; setSpeakerUi(false); };
    window.speechSynthesis.speak(u);
  }
  function stopSpeaking() {
    if (window.speechSynthesis && window.speechSynthesis.speaking) window.speechSynthesis.cancel();
    tts.active = false;
    setSpeakerUi(false);
  }
  window.toggleTextToSpeech = function () {
    if (!window.speechSynthesis) { alert('Text to speech is not supported in your browser.'); return; }
    if (tts.active) { stopSpeaking(); return; }
    var bubbles = document.querySelectorAll('#sdLessonChatMessages .sd-ai-bubble:not(.user):not(.sd-typing-row) .sd-msg');
    var last = bubbles.length > 1 ? bubbles[bubbles.length - 1] : null; // [0] is the greeting
    if (!last) { alert('No AI messages to read aloud.'); return; }
    tts.active = true;
    setSpeakerUi(true);
    speak(last.textContent);
  };

  window.sdViews = window.sdViews || {};
  window.sdViews.lesson = { onLeave: function () { stopVoice(); stopSpeaking(); } };
})();
