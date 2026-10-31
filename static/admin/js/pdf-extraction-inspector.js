/**
 * Admin PDF Extraction Inspector — upload, extract, side-by-side compare.
 */
(function () {
  'use strict';

  const form = document.getElementById('peiForm');
  const fileInput = document.getElementById('peiFile');
  const dropzone = document.getElementById('peiDropzone');
  const fileNameEl = document.getElementById('peiFileName');
  const submitBtn = document.getElementById('peiSubmit');
  const resetBtn = document.getElementById('peiReset');
  const loadingEl = document.getElementById('peiLoading');
  const errorEl = document.getElementById('peiError');
  const resultsEl = document.getElementById('peiResults');
  const textView = document.getElementById('peiTextView');
  const pdfFrame = document.getElementById('peiPdfFrame');
  const pdfFallback = document.getElementById('peiPdfFallback');
  const pdfOpen = document.getElementById('peiPdfOpen');
  const pageSelect = document.getElementById('peiPageSelect');
  const pagePrev = document.getElementById('peiPagePrev');
  const pageNext = document.getElementById('peiPageNext');
  const showAllBtn = document.getElementById('peiShowAll');
  const copyBtn = document.getElementById('peiCopy');
  const downloadBtn = document.getElementById('peiDownload');
  const warningsEl = document.getElementById('peiWarnings');

  let state = {
    fullText: '',
    pages: [],
    filename: 'extracted.txt',
    pageMode: 'all', // 'all' | number
    pdfUrl: '',
  };

  function showError(msg) {
    errorEl.textContent = msg || 'Something went wrong.';
    errorEl.classList.remove('hidden');
  }

  function clearError() {
    errorEl.textContent = '';
    errorEl.classList.add('hidden');
  }

  function setLoading(on) {
    loadingEl.classList.toggle('hidden', !on);
    submitBtn.disabled = on || !fileInput.files.length;
    if (on) {
      resultsEl.classList.add('hidden');
      clearError();
    }
  }

  function formatChars(n) {
    return Number(n || 0).toLocaleString();
  }

  function setFileLabel(file) {
    if (!file) {
      fileNameEl.hidden = true;
      fileNameEl.textContent = '';
      submitBtn.disabled = true;
      return;
    }
    fileNameEl.hidden = false;
    fileNameEl.textContent = file.name + ' · ' + (file.size / (1024 * 1024)).toFixed(2) + ' MB';
    submitBtn.disabled = false;
  }

  function renderText() {
    if (state.pageMode === 'all') {
      textView.textContent = state.fullText || '';
      showAllBtn.classList.add('active');
    } else {
      const page = state.pages.find((p) => p.page_number === state.pageMode);
      textView.textContent = page ? page.text : '';
      showAllBtn.classList.remove('active');
    }
  }

  function populatePageSelect() {
    pageSelect.innerHTML = '';
    const allOpt = document.createElement('option');
    allOpt.value = 'all';
    allOpt.textContent = 'All';
    pageSelect.appendChild(allOpt);
    state.pages.forEach((p) => {
      const opt = document.createElement('option');
      opt.value = String(p.page_number);
      opt.textContent = String(p.page_number);
      pageSelect.appendChild(opt);
    });
    pageSelect.value = state.pageMode === 'all' ? 'all' : String(state.pageMode);
  }

  function showResults(data) {
    state.fullText = data.full_text || '';
    state.pages = Array.isArray(data.pages) ? data.pages : [];
    state.filename = (data.filename || 'document').replace(/\.pdf$/i, '') + '_extracted.txt';
    state.pageMode = 'all';
    state.pdfUrl = data.pdf_url || '';

    document.getElementById('peiStatName').textContent = data.filename || '—';
    document.getElementById('peiStatPages').textContent = data.num_pages != null ? String(data.num_pages) : '—';
    document.getElementById('peiStatChars').textContent = formatChars(data.char_count);
    document.getElementById('peiStatTime').textContent =
      data.processing_time_seconds != null ? data.processing_time_seconds + 's' : '—';
    document.getElementById('peiStatMethod').textContent = data.extraction_method || '—';

    if (data.warnings && data.warnings.length) {
      warningsEl.hidden = false;
      warningsEl.classList.remove('hidden');
      warningsEl.innerHTML =
        '<strong><i class="fas fa-exclamation-triangle"></i> Warnings</strong><ul>' +
        data.warnings.map((w) => '<li>' + escapeHtml(w) + '</li>').join('') +
        '</ul>';
    } else {
      warningsEl.classList.add('hidden');
      warningsEl.innerHTML = '';
    }

    populatePageSelect();
    renderText();

    if (state.pdfUrl) {
      pdfFrame.src = state.pdfUrl;
      pdfOpen.href = state.pdfUrl;
      pdfFallback.hidden = true;
      pdfFrame.hidden = false;
    }

    resultsEl.classList.remove('hidden');
    resetBtn.disabled = false;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function resetAll() {
    form.reset();
    setFileLabel(null);
    clearError();
    setLoading(false);
    resultsEl.classList.add('hidden');
    pdfFrame.removeAttribute('src');
    textView.textContent = '';
    warningsEl.classList.add('hidden');
    resetBtn.disabled = true;
    state = { fullText: '', pages: [], filename: 'extracted.txt', pageMode: 'all', pdfUrl: '' };
  }

  // Drag & drop
  ['dragenter', 'dragover'].forEach((evt) => {
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.add('pei-dragover');
    });
  });
  ['dragleave', 'drop'].forEach((evt) => {
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.remove('pei-dragover');
    });
  });
  dropzone.addEventListener('drop', (e) => {
    const files = e.dataTransfer && e.dataTransfer.files;
    if (!files || !files.length) return;
    const file = files[0];
    if (!/\.pdf$/i.test(file.name)) {
      showError('Only PDF files are supported.');
      return;
    }
    const dt = new DataTransfer();
    dt.items.add(file);
    fileInput.files = dt.files;
    setFileLabel(file);
    clearError();
  });

  fileInput.addEventListener('change', () => {
    const file = fileInput.files && fileInput.files[0];
    setFileLabel(file || null);
    clearError();
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const file = fileInput.files && fileInput.files[0];
    if (!file) {
      showError('Choose a PDF file first.');
      return;
    }
    setLoading(true);
    const body = new FormData();
    body.append('file', file, file.name);
    try {
      const res = await fetch('/admin/pdf-extraction-inspector/extract', {
        method: 'POST',
        body,
        credentials: 'same-origin',
      });
      let data = null;
      try {
        data = await res.json();
      } catch (_) {
        data = null;
      }
      if (!res.ok || !data || !data.success) {
        const msg =
          (data && data.error) ||
          (res.status === 413 ? 'File too large.' : 'Extraction failed (' + res.status + ').');
        showError(msg);
        resultsEl.classList.add('hidden');
        return;
      }
      if (!data.full_text && !(data.pages && data.pages.length)) {
        showError('Extraction returned no text. The PDF may be empty or image-only.');
        return;
      }
      showResults(data);
    } catch (err) {
      showError(err && err.message ? err.message : 'Network error during extraction.');
    } finally {
      setLoading(false);
    }
  });

  pageSelect.addEventListener('change', () => {
    const v = pageSelect.value;
    state.pageMode = v === 'all' ? 'all' : parseInt(v, 10);
    renderText();
  });

  pagePrev.addEventListener('click', () => {
    if (!state.pages.length) return;
    if (state.pageMode === 'all') {
      state.pageMode = 1;
    } else {
      state.pageMode = Math.max(1, state.pageMode - 1);
    }
    pageSelect.value = String(state.pageMode);
    renderText();
  });

  pageNext.addEventListener('click', () => {
    if (!state.pages.length) return;
    const max = state.pages.length;
    if (state.pageMode === 'all') {
      state.pageMode = 1;
    } else {
      state.pageMode = Math.min(max, state.pageMode + 1);
    }
    pageSelect.value = String(state.pageMode);
    renderText();
  });

  showAllBtn.addEventListener('click', () => {
    state.pageMode = 'all';
    pageSelect.value = 'all';
    renderText();
  });

  copyBtn.addEventListener('click', async () => {
    const text = state.pageMode === 'all' ? state.fullText : textView.textContent;
    try {
      await navigator.clipboard.writeText(text || '');
      const prev = copyBtn.innerHTML;
      copyBtn.innerHTML = '<i class="fas fa-check"></i>Copied';
      setTimeout(() => {
        copyBtn.innerHTML = prev;
      }, 1400);
    } catch (_) {
      showError('Could not copy to clipboard. Select the text and copy manually.');
    }
  });

  downloadBtn.addEventListener('click', () => {
    const text = state.pageMode === 'all' ? state.fullText : textView.textContent;
    const blob = new Blob([text || ''], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = state.filename || 'extracted.txt';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  });

  resetBtn.addEventListener('click', resetAll);

  pdfFrame.addEventListener('error', () => {
    pdfFallback.hidden = false;
  });
})();
