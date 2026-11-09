/** Admin Diagnostic Assessment — Tabbed Upload Hub */

(function () {
  var state = {
    assessmentId: null,
    threadId: null,
    diagnostics: [],
    selectedTargetAssessmentId: null,
    listFilter: 'published',
    uploadTab: 'publish',
    reviewAssessmentId: null,
    reviewStatus: null,
    reviewQuestions: [],
    reviewTab: 'diag',
    bankDocs: [],
    bankReviewDocId: null,
    bankReviewStatus: '',
    bankReviewItems: [],
    bankReviewTotal: 0,
  };
  var selectedTargetFiles = {
    adminDiagTargetFiles: [],
    adminDiagAddTargetFiles: [],
  };

  function api(path, opts) {
    opts = opts || {};
    return fetch(path, Object.assign({ credentials: 'include' }, opts)).then(function (res) {
      return res.json().then(function (body) {
        if (!res.ok) {
          var msg = (body.error && body.error.message) || body.message || 'Request failed';
          throw new Error(msg);
        }
        return body.data !== undefined ? body.data : body;
      });
    });
  }

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
  }

  function gradeLabel(level) {
    if (level == null || level === '') return 'Not set';
    return 'Grade ' + level;
  }

  function publishedDiagnostics(items) {
    return (items || []).filter(function (d) {
      return d.status === 'published';
    });
  }

  function resolveSelectedAssessmentId() {
    var select = document.getElementById('adminDiagAddTargetGrade');
    var fromSelect = select && select.value ? Number(select.value) : null;
    if (fromSelect) return fromSelect;
    if (state.selectedTargetAssessmentId) return Number(state.selectedTargetAssessmentId);
    return null;
  }

  window.setAdminDiagUploadTab = function (tab) {
    state.uploadTab = tab === 'append' ? 'append' : 'publish';
    var publishPanel = document.getElementById('adminDiagUploadSection');
    var appendPanel = document.getElementById('adminDiagAddTargetsSection');
    var tabPublish = document.getElementById('adminDiagTabPublish');
    var tabAppend = document.getElementById('adminDiagTabAppend');

    if (publishPanel) {
      publishPanel.classList.toggle('is-active', state.uploadTab === 'publish');
      publishPanel.style.display = state.uploadTab === 'publish' ? 'block' : 'none';
    }
    if (appendPanel) {
      appendPanel.classList.toggle('is-active', state.uploadTab === 'append');
      appendPanel.style.display = state.uploadTab === 'append' ? 'block' : 'none';
    }
    if (tabPublish) tabPublish.classList.toggle('is-active', state.uploadTab === 'publish');
    if (tabAppend) tabAppend.classList.toggle('is-active', state.uploadTab === 'append');
  };

  function setAppendInputsEnabled(enabled) {
    var drop = document.getElementById('adminDiagAddTargetDrop');
    var input = document.getElementById('adminDiagAddTargetFiles');
    var btn = document.getElementById('adminDiagAddTargetBtn');
    var hint = document.getElementById('adminDiagAppendDropHint');
    if (drop) drop.classList.toggle('is-disabled', !enabled);
    if (input) input.disabled = !enabled;
    if (btn) btn.disabled = !enabled;
    if (hint) {
      hint.textContent = enabled
        ? 'Click or drop · PDF only'
        : 'Select a grade first';
    }
  }

  function updateDiagStats(items) {
    var published = publishedDiagnostics(items);
    var grades = {};
    var targetCount = 0;
    published.forEach(function (d) {
      if (d.grade_level != null && d.grade_level !== '') grades[String(d.grade_level)] = true;
      targetCount += (d.target_pdfs || []).length;
    });
    var pubEl = document.getElementById('adminDiagStatPublished');
    var gradeEl = document.getElementById('adminDiagStatGrades');
    var targetEl = document.getElementById('adminDiagStatTargets');
    if (pubEl) pubEl.textContent = String(published.length);
    if (gradeEl) gradeEl.textContent = String(Object.keys(grades).length);
    if (targetEl) targetEl.textContent = String(targetCount);
  }

  function syncTargetGradeUI(items) {
    var published = publishedDiagnostics(items);
    var select = document.getElementById('adminDiagAddTargetGrade');
    var pills = document.getElementById('adminDiagTargetGradePills');
    var hint = document.getElementById('adminDiagTargetGradeHint');
    var prev = resolveSelectedAssessmentId();

    if (!select) return;

    if (!published.length) {
      select.innerHTML = '<option value="">No live diagnostics yet</option>';
      if (pills) pills.innerHTML = '';
      state.selectedTargetAssessmentId = null;
      state.assessmentId = null;
      setAppendInputsEnabled(false);
      if (hint) {
        hint.textContent = 'Publish a diagnostic first, then return here to append study PDFs.';
      }
      return;
    }

    var stillValid = published.some(function (d) {
      return d.id === prev;
    });
    var selectedId = stillValid ? prev : null;

    var sorted = published.slice().sort(function (a, b) {
      var ga = Number(a.grade_level);
      var gb = Number(b.grade_level);
      if (!isNaN(ga) && !isNaN(gb) && ga !== gb) return ga - gb;
      return String(a.title || '').localeCompare(String(b.title || ''));
    });

    select.innerHTML =
      '<option value="">Select grade…</option>' +
      sorted
        .map(function (d) {
          var label =
            gradeLabel(d.grade_level) +
            ' — ' +
            (d.title || 'Diagnostic') +
            ' (' +
            (d.target_pdfs || []).length +
            ' PDF' +
            ((d.target_pdfs || []).length === 1 ? '' : 's') +
            ')';
          return (
            '<option value="' +
            d.id +
            '"' +
            (selectedId === d.id ? ' selected' : '') +
            '>' +
            esc(label) +
            '</option>'
          );
        })
        .join('');

    if (pills) {
      pills.innerHTML = sorted
        .map(function (d) {
          var selected = selectedId === d.id;
          return (
            '<button type="button" class="d-grade-pill' +
            (selected ? ' is-selected' : '') +
            '" data-assessment-id="' +
            d.id +
            '" onclick="selectAdminDiagTargetGrade(' +
            d.id +
            ')">' +
            esc(gradeLabel(d.grade_level)) +
            '</button>'
          );
        })
        .join('');
    }

    state.selectedTargetAssessmentId = selectedId;
    state.assessmentId = selectedId;
    setAppendInputsEnabled(!!selectedId);

    if (hint) {
      hint.textContent = selectedId
        ? 'PDFs will append only to the selected grade.'
        : 'Required — PDFs append only to the grade you select.';
    }
  }

  window.selectAdminDiagTargetGrade = function (assessmentId) {
    var select = document.getElementById('adminDiagAddTargetGrade');
    if (select) select.value = String(assessmentId);
    onAdminDiagTargetGradeChange();
  };

  window.onAdminDiagTargetGradeChange = function () {
    var select = document.getElementById('adminDiagAddTargetGrade');
    var id = select && select.value ? Number(select.value) : null;
    state.selectedTargetAssessmentId = id;
    state.assessmentId = id;
    setAppendInputsEnabled(!!id);

    var pills = document.querySelectorAll('#adminDiagTargetGradePills .d-grade-pill');
    Array.prototype.forEach.call(pills, function (btn) {
      var btnId = Number(btn.getAttribute('data-assessment-id'));
      if (id && btnId === id) btn.classList.add('is-selected');
      else btn.classList.remove('is-selected');
    });

    var hint = document.getElementById('adminDiagTargetGradeHint');
    if (!hint) return;
    if (!id) {
      hint.textContent = 'Required — PDFs append only to the grade you select.';
      return;
    }
    var match = (state.diagnostics || []).find(function (d) {
      return d.id === id;
    });
    hint.textContent = match
      ? 'Appending to ' + gradeLabel(match.grade_level) + ' — ' + (match.title || 'Diagnostic') + '.'
      : 'PDFs will append only to the selected grade.';
  };

  window.updateAdminDiagQaFileLabel = function () {
    var input = document.getElementById('adminDiagQaFile');
    var label = document.getElementById('adminDiagQaFileLabel');
    if (!label) return;
    if (input && input.files && input.files[0]) {
      var f = input.files[0];
      var mb = (f.size / (1024 * 1024)).toFixed(2);
      label.innerHTML =
        '<span class="text-slate-700 font-medium">' +
        esc(f.name) +
        '</span> <span class="text-slate-400">(' +
        mb +
        ' MB)</span>';
    } else {
      label.textContent = '';
    }
  };

  function renderFileList(inputId, listId, countId) {
    var listEl = document.getElementById(listId);
    var countEl = countId ? document.getElementById(countId) : null;
    if (!listEl) return;
    var files = selectedTargetFiles[inputId] || [];
    if (!files.length) {
      listEl.innerHTML = '';
      if (countEl) {
        countEl.style.display = 'none';
        countEl.textContent = '';
      }
      return;
    }
    listEl.innerHTML = files
      .map(function (f, idx) {
        var sizeMb = (f.size / (1024 * 1024)).toFixed(2);
        return (
          '<li>' +
          '<span class="text-[#1a56db] font-bold">' +
          (idx + 1) +
          '.</span>' +
          '<span class="flex-1 truncate" title="' +
          esc(f.name) +
          '">' +
          esc(f.name) +
          '</span>' +
          '<span class="text-gray-400 text-xs whitespace-nowrap">' +
          sizeMb +
          ' MB</span>' +
          '<button type="button" class="text-red-500 hover:underline text-xs whitespace-nowrap" onclick="removeAdminDiagTargetFile(\'' +
          inputId +
          '\',' +
          idx +
          ')">Remove</button>' +
          '</li>'
        );
      })
      .join('');
    if (countEl) {
      countEl.style.display = 'block';
      countEl.textContent =
        files.length + ' PDF' + (files.length === 1 ? '' : 's') + ' selected';
    }
  }

  function addTargetFiles(inputId, fileList) {
    var files = Array.prototype.slice.call(fileList || []);
    if (!files.length) return;
    var existing = selectedTargetFiles[inputId] || [];
    files.forEach(function (file) {
      var duplicate = existing.some(function (current) {
        return (
          current.name === file.name &&
          current.size === file.size &&
          current.lastModified === file.lastModified
        );
      });
      if (!duplicate) existing.push(file);
    });
    selectedTargetFiles[inputId] = existing;
  }

  window.removeAdminDiagTargetFile = function (inputId, index) {
    var files = selectedTargetFiles[inputId] || [];
    if (index < 0 || index >= files.length) return;
    files.splice(index, 1);
    selectedTargetFiles[inputId] = files;
    if (inputId === 'adminDiagTargetFiles') updateAdminDiagTargetFileList();
    else updateAdminDiagAddTargetFileList();
  };

  window.updateAdminDiagTargetFileList = function () {
    var input = document.getElementById('adminDiagTargetFiles');
    if (input && input.files && input.files.length) {
      addTargetFiles('adminDiagTargetFiles', input.files);
      input.value = '';
    }
    renderFileList('adminDiagTargetFiles', 'adminDiagTargetFileList', 'adminDiagTargetFileCount');
  };

  window.updateAdminDiagAddTargetFileList = function () {
    var input = document.getElementById('adminDiagAddTargetFiles');
    if (input && input.files && input.files.length) {
      addTargetFiles('adminDiagAddTargetFiles', input.files);
      input.value = '';
    }
    renderFileList('adminDiagAddTargetFiles', 'adminDiagAddTargetFileList', null);
  };

  function setProgress(pct, text) {
    var wrap = document.getElementById('adminDiagProgressWrap');
    var bar = document.getElementById('adminDiagProgressBar');
    var pctEl = document.getElementById('adminDiagProgressPct');
    var textEl = document.getElementById('adminDiagProgressText');
    if (!wrap) return;
    wrap.style.display = 'block';
    if (pct != null && !isNaN(pct)) {
      if (bar) bar.style.width = pct + '%';
      if (pctEl) pctEl.textContent = Math.round(pct) + '%';
    }
    if (textEl && text != null) textEl.textContent = text || '';
  }

  function resetProgress() {
    var wrap = document.getElementById('adminDiagProgressWrap');
    if (wrap) wrap.style.display = 'none';
    if (window._adminDiagProgressPoll) {
      clearInterval(window._adminDiagProgressPoll);
      window._adminDiagProgressPoll = null;
    }
  }

  function finishAdminDiagUploadSuccess(d, targetFiles) {
    setProgress(100, 'Draft ready — review before students see it');
    state.assessmentId = d.assessment_id;
    state.threadId = d.thread_id;
    var targetCount =
      (d.target_filenames || d.target_thread_ids || []).length ||
      (targetFiles && targetFiles.length) ||
      0;
    var status = document.getElementById('adminDiagStatus');
    var btn = document.getElementById('adminDiagUploadBtn');
    if (status) {
      status.textContent =
        'Draft saved with ' +
        (d.question_count || '?') +
        ' questions and ' +
        targetCount +
        ' target PDF(s). Review the MCQs below, then Approve for Students.';
    }
    var form = document.getElementById('adminDiagnosticPdfForm');
    if (form) form.reset();
    selectedTargetFiles.adminDiagTargetFiles = [];
    updateAdminDiagTargetFileList();
    updateAdminDiagQaFileLabel();
    state.selectedTargetAssessmentId = d.assessment_id;
    state.listFilter = 'draft';
    loadAdminDiagnostics();
    if (btn) btn.disabled = false;
    setTimeout(resetProgress, 800);
    if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
    if (d.assessment_id) {
      setTimeout(function () {
        adminPreviewDiagnostic(d.assessment_id);
      }, 200);
    }
  }

  function failAdminDiagUpload(message) {
    var status = document.getElementById('adminDiagStatus');
    var btn = document.getElementById('adminDiagUploadBtn');
    if (status) status.textContent = message || 'Upload failed';
    if (btn) btn.disabled = false;
    resetProgress();
    if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
  }

  function startServerProgressPoll(jobId, targetFiles, opts) {
    if (!jobId) return;
    opts = opts || {};
    var waitForDone = !!opts.waitForDone;
    var startedAt = Date.now();
    var maxWaitMs = 55 * 60 * 1000; // large multi-PDF jobs
    if (window._adminDiagProgressPoll) clearInterval(window._adminDiagProgressPoll);
    window._adminDiagProgressPoll = setInterval(function () {
      if (waitForDone && Date.now() - startedAt > maxWaitMs) {
        failAdminDiagUpload(
          'Error: Processing timed out. Check Diagnostics list — a draft may still appear shortly.'
        );
        return;
      }
      fetch('/api/lms/diagnostics/upload-progress/' + encodeURIComponent(jobId), {
        credentials: 'include',
      })
        .then(function (res) {
          return res.json();
        })
        .then(function (body) {
          var d = body.data || body;
          if (!d || d.percent == null) return;
          var serverPct = Math.max(0, Math.min(100, Number(d.percent) || 0));
          var uiPct = 20 + Math.round(serverPct * 0.73);
          if (d.done) uiPct = d.error ? uiPct : 100;
          else if (uiPct > 93) uiPct = 93;
          setProgress(uiPct, d.message || 'Processing on server...');
          if (!waitForDone || !d.done) return;
          if (window._adminDiagProgressPoll) {
            clearInterval(window._adminDiagProgressPoll);
            window._adminDiagProgressPoll = null;
          }
          if (d.error) {
            failAdminDiagUpload('Error: ' + d.error);
            return;
          }
          var result = d.result || {};
          if (!result.assessment_id && d.assessment_id) {
            result.assessment_id = d.assessment_id;
          }
          if (!result.assessment_id) {
            loadAdminDiagnostics();
            var statusEl = document.getElementById('adminDiagStatus');
            var btnEl = document.getElementById('adminDiagUploadBtn');
            if (statusEl) {
              statusEl.textContent =
                'Processing finished. Open the new draft in the Diagnostics list below.';
            }
            if (btnEl) btnEl.disabled = false;
            setTimeout(resetProgress, 800);
            if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
            return;
          }
          finishAdminDiagUploadSuccess(result, targetFiles);
        })
        .catch(function () {});
    }, 700);
  }

  function appendTargetFiles(fd, files) {
    // Append once only. Duplicating as target_files + target_files[] doubles
    // the multipart body (~57MB → ~114MB) and trips nginx 100M/200M limits.
    for (var i = 0; i < files.length; i++) {
      fd.append('target_files', files[i]);
    }
  }

  function bindDropZones() {
    var zones = document.querySelectorAll('#diagnostic-section .d-drop');
    Array.prototype.forEach.call(zones, function (zone) {
      if (zone._diagDropBound) return;
      zone._diagDropBound = true;
      ['dragenter', 'dragover'].forEach(function (evt) {
        zone.addEventListener(evt, function (e) {
          e.preventDefault();
          e.stopPropagation();
          if (zone.classList.contains('is-disabled')) return;
          zone.classList.add('is-dragover');
        });
      });
      ['dragleave', 'drop'].forEach(function (evt) {
        zone.addEventListener(evt, function (e) {
          e.preventDefault();
          e.stopPropagation();
          zone.classList.remove('is-dragover');
        });
      });
      zone.addEventListener('drop', function (e) {
        if (zone.classList.contains('is-disabled')) return;
        var input =
          zone.querySelector('input[type="file"]') ||
          document.getElementById(zone.getAttribute('data-file-input'));
        if (!input || !e.dataTransfer || !e.dataTransfer.files || !e.dataTransfer.files.length) {
          return;
        }
        try {
          var dt = new DataTransfer();
          Array.prototype.forEach.call(e.dataTransfer.files, function (f) {
            if ((f.name || '').toLowerCase().endsWith('.pdf')) dt.items.add(f);
          });
          input.files = dt.files;
        } catch (err) {}
        if (input.id === 'adminDiagQaFile') updateAdminDiagQaFileLabel();
        else if (input.id === 'adminDiagTargetFiles') updateAdminDiagTargetFileList();
        else if (input.id === 'adminDiagAddTargetFiles') updateAdminDiagAddTargetFileList();
      });
    });
  }

  function statusChip(status) {
    var s = (status || '').toLowerCase();
    if (s === 'published') return '<span class="d-chip live">Live</span>';
    if (s === 'draft') return '<span class="d-chip draft">Draft</span>';
    if (s === 'archived') return '<span class="d-chip archived">Archived</span>';
    return '<span class="d-chip draft">' + esc(status || 'unknown') + '</span>';
  }

  function sortDiagnostics(items) {
    var rank = { published: 0, draft: 1, archived: 2 };
    return (items || []).slice().sort(function (a, b) {
      var ra = rank[(a.status || '').toLowerCase()];
      var rb = rank[(b.status || '').toLowerCase()];
      if (ra == null) ra = 9;
      if (rb == null) rb = 9;
      if (ra !== rb) return ra - rb;
      var ga = Number(a.grade_level);
      var gb = Number(b.grade_level);
      if (!isNaN(ga) && !isNaN(gb) && ga !== gb) return ga - gb;
      return String(a.title || '').localeCompare(String(b.title || ''));
    });
  }

  function updateFilterTabCounts(items) {
    var all = items || [];
    var counts = { published: 0, draft: 0, archived: 0, all: all.length };
    all.forEach(function (d) {
      var s = (d.status || '').toLowerCase();
      if (counts[s] != null) counts[s] += 1;
    });
    var map = {
      adminDiagFilterCountPublished: counts.published,
      adminDiagFilterCountDraft: counts.draft,
      adminDiagFilterCountArchived: counts.archived,
      adminDiagFilterCountAll: counts.all,
    };
    Object.keys(map).forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.textContent = String(map[id]);
    });
    var tabs = document.querySelectorAll('#adminDiagFilterTabs .d-filter');
    Array.prototype.forEach.call(tabs, function (tab) {
      var f = tab.getAttribute('data-filter');
      if (f === state.listFilter) tab.classList.add('is-active');
      else tab.classList.remove('is-active');
    });
  }

  function filteredDiagnostics(items) {
    var filter = state.listFilter || 'published';
    var list = sortDiagnostics(items);
    if (filter === 'all') return list;
    return list.filter(function (d) {
      return (d.status || '').toLowerCase() === filter;
    });
  }

  window.setAdminDiagListFilter = function (filter) {
    state.listFilter = filter || 'published';
    updateFilterTabCounts(state.diagnostics);
    renderAdminDiagList(state.diagnostics);
  };

  window.focusAdminDiagAddTargets = function (assessmentId) {
    setAdminDiagUploadTab('append');
    selectAdminDiagTargetGrade(assessmentId);
    var hub = document.querySelector('#diagnostic-section .d-hub');
    if (hub) hub.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  // A question-bank Target PDF publishes nothing to students until its extracted
  // MCQs are approved; show that state (and the way to act on it) on the PDF itself.
  function bankDocFor(assessmentId, filename) {
    var name = String(filename || '').trim().toLowerCase();
    return (state.bankDocs || []).find(function (b) {
      return Number(b.assessment_id) === Number(assessmentId) && String(b.filename || '').trim().toLowerCase() === name;
    });
  }

  function bankStatusHtml(assessmentId, filename) {
    var b = bankDocFor(assessmentId, filename);
    if (!b) return '';
    var c = b.status_counts || {};
    var approved = c.approved || 0;
    var ready = c.auto_verified || 0;
    var review = c.needs_review || 0;
    var pending = ready + review;
    var tone = approved === 0 ? 'is-bad' : pending ? 'is-warn' : 'is-ok';
    var text = approved === 0
      ? 'Question bank: 0 approved — students get no questions from this PDF yet'
      : 'Question bank: ' + approved + ' approved';
    if (pending) text += ' · ' + ready + ' ready to approve · ' + review + ' need review';
    return (
      '<div class="d-bank-status ' + tone + '">' +
      '<span>' + esc(text) + '</span>' +
      (pending
        ? '<button type="button" class="d-action primary" onclick="adminPreviewDiagnostic(' +
          Number(assessmentId) +
          ',{tab:\'bank\',documentId:' +
          Number(b.document_id) +
          '})"><i class="fas fa-check-double"></i> Review &amp; approve questions</button>'
        : '<button type="button" class="d-bank-link" onclick="adminPreviewDiagnostic(' +
          Number(assessmentId) +
          ',{tab:\'bank\',documentId:' +
          Number(b.document_id) +
          ',status:\'approved\'})">View approved</button>') +
      '</div>'
    );
  }

  async function loadBankDocs() {
    try {
      state.bankDocs = (await api('/api/lms/target-bank/documents')) || [];
    } catch (err) {
      state.bankDocs = [];
    }
  }

  function renderAdminDiagList(items) {
    var listEl = document.getElementById('adminDiagList');
    if (!listEl) return;
    updateFilterTabCounts(items);

    if (!(items || []).length) {
      listEl.innerHTML =
        '<div class="d-empty">No diagnostics yet. Use <strong>Publish new</strong> above.</div>';
      return;
    }

    var visible = filteredDiagnostics(items);
    if (!visible.length) {
      listEl.innerHTML =
        '<div class="d-empty">Nothing in this filter. Try <strong>Active</strong> or <strong>All</strong>.</div>';
      return;
    }

    listEl.innerHTML =
      '<div class="d-rows">' +
      visible
        .map(function (d) {
          var status = (d.status || '').toLowerCase();
          var isPublished = status === 'published';
          var targetCount = (d.target_pdfs || []).length;
          var rowClass = 'd-row' + (status === 'archived' || status === 'draft' ? ' is-muted' : '');
          var gradeChip =
            d.grade_level != null && d.grade_level !== ''
              ? '<span class="d-chip grade">' + esc(gradeLabel(d.grade_level)) + '</span>'
              : '<span class="d-chip unset">Not set</span>';

          var pdfItems = (d.target_pdfs || [])
            .map(function (t) {
              return (
                '<div class="d-pdf-item">' +
                '<i class="fas fa-file-pdf"></i>' +
                '<span class="truncate" title="' +
                esc(t.original_filename || 'target.pdf') +
                '">' +
                esc(t.original_filename || 'target.pdf') +
                '</span>' +
                '<button type="button" class="d-pdf-x" title="Remove PDF" onclick="adminRemoveTargetPdf(' +
                d.id +
                ',' +
                t.id +
                ')"><i class="fas fa-times"></i></button>' +
                '</div>' +
                bankStatusHtml(d.id, t.original_filename)
              );
            })
            .join('');

          var pdfBlock =
            '<div class="d-pdfs"><div class="d-pdfs-label">Target PDFs (' +
            targetCount +
            ')</div>' +
            (pdfItems ||
              '<div class="d-pdf-empty">No study PDFs yet — Learning Chat needs these.</div>') +
            '</div>';

          var actions = '<div class="d-actions">';
          if (status === 'draft') {
            actions +=
              '<button type="button" class="d-action primary" onclick="adminPreviewDiagnostic(' +
              d.id +
              ')"><i class="fas fa-clipboard-check"></i> Review &amp; Edit</button>';
            actions +=
              '<button type="button" class="d-action" onclick="adminApproveDiagnostic(' +
              d.id +
              ')"><i class="fas fa-check"></i> Approve for Students</button>';
          }
          if (isPublished) {
            actions +=
              '<button type="button" class="d-action" onclick="adminPreviewDiagnostic(' +
              d.id +
              ')"><i class="fas fa-eye"></i> View / Edit</button>';
            actions +=
              '<button type="button" class="d-action primary" onclick="focusAdminDiagAddTargets(' +
              d.id +
              ')"><i class="fas fa-plus"></i> Add study PDFs</button>';
          }
          if (status !== 'archived') {
            actions +=
              '<button type="button" class="d-action danger" onclick="adminRemoveDiagnostic(' +
              d.id +
              ')"><i class="fas fa-trash-alt"></i> Remove</button>';
          }
          actions += '</div>';

          return (
            '<article class="' +
            rowClass +
            '">' +
            '<div class="d-row-top">' +
            '<div class="min-w-0 flex-1">' +
            '<div class="d-chips">' +
            gradeChip +
            statusChip(d.status) +
            (isPublished && !targetCount ? '<span class="d-chip warn">Needs targets</span>' : '') +
            '</div>' +
            '<div class="d-row-title truncate" title="' +
            esc(d.title) +
            '">' +
            esc(d.title || 'Untitled') +
            '</div>' +
            '<div class="d-row-meta">' +
            '<span>' +
            (d.question_count || 0) +
            ' questions</span>' +
            (d.time_limit_minutes ? '<span>~' + d.time_limit_minutes + ' min</span>' : '') +
            '<span>' +
            targetCount +
            ' PDF' +
            (targetCount === 1 ? '' : 's') +
            '</span>' +
            '</div></div>' +
            actions +
            '</div>' +
            pdfBlock +
            '</article>'
          );
        })
        .join('') +
      '</div>';
  }

  // Retake requests: teachers approve their own students'; the admin sees every pending one
  // (and is the only approver for students who are not in any class).
  async function loadAdminRetakeRequests() {
    var card = document.getElementById('adminDiagRetakeCard');
    var list = document.getElementById('adminDiagRetakeList');
    if (!card || !list) return;
    var rows = [];
    try { rows = (await api('/api/lms/retake-requests')) || []; } catch (e) { rows = []; }
    card.style.display = rows.length ? 'block' : 'none';
    list.innerHTML = rows.map(function (r) {
      return '<div class="d-row"><div class="d-row-top"><div>' +
        '<div class="d-row-title">' + esc(r.student_name || 'Student') + '</div>' +
        '<div class="d-row-meta"><span>' + esc(r.assessment_title || 'Diagnostic') + '</span>' +
        (r.student_grade ? '<span>Grade ' + esc(r.student_grade) + '</span>' : '') + '</div></div>' +
        '<div class="d-actions">' +
        '<button type="button" class="d-action danger" onclick="adminDecideRetake(' + r.id + ', false)">Deny</button>' +
        '<button type="button" class="d-action primary" onclick="adminDecideRetake(' + r.id + ', true)">Approve retake</button>' +
        '</div></div></div>';
    }).join('');
  }

  window.adminDecideRetake = async function (id, approve) {
    try {
      await api('/api/lms/retake-requests/' + id + '/' + (approve ? 'approve' : 'deny'), { method: 'POST' });
    } catch (err) {
      alert('Could not update the request: ' + err.message);
    }
    loadAdminRetakeRequests();
  };

  window.loadAdminDiagnostics = async function () {
    var listEl = document.getElementById('adminDiagList');
    if (!listEl) return;
    listEl.innerHTML = '<p class="text-slate-500 text-sm">Loading...</p>';
    bindDropZones();
    loadAdminRetakeRequests();
    setAdminDiagUploadTab(state.uploadTab || 'publish');

    try {
      var results = await Promise.all([api('/api/lms/admin/diagnostics'), loadBankDocs()]);
      var items = results[0];
      state.diagnostics = items || [];
      updateDiagStats(items);
      syncTargetGradeUI(items);

      var activeNote = document.getElementById('adminDiagActiveNote');
      var published = publishedDiagnostics(items);
      if (activeNote) activeNote.style.display = published.length ? 'block' : 'none';

      if (!state.listFilter) state.listFilter = 'published';
      renderAdminDiagList(items);
    } catch (err) {
      listEl.innerHTML = '<p class="text-red-600 text-sm">' + esc(err.message) + '</p>';
    }
  };

  window.adminApproveDiagnostic = async function (id) {
    if (
      !confirm(
        'Approve this diagnostic for students? They will see it on their dashboard and can start it.'
      )
    ) {
      return;
    }
    try {
      await api('/api/lms/diagnostics/' + id + '/publish', { method: 'POST' });
      state.listFilter = 'published';
      closeAdminDiagReview();
      loadAdminDiagnostics();
    } catch (err) {
      alert('Approve failed: ' + err.message);
    }
  };

  window.adminApproveFromReview = function () {
    if (!state.reviewAssessmentId) return;
    adminApproveDiagnostic(state.reviewAssessmentId);
  };

  function _diagFmtText(text) {
    var raw = String(text || '');
    if (typeof window.lmsEscapeHtml === 'function') {
      /* keep raw for math typeset — escape then let MathJax/KaTeX process $...$ */
    }
    var d = document.createElement('div');
    d.textContent = raw;
    return d.innerHTML;
  }

  function _diagQuestionText(q) {
    if (typeof window.lmsQuestionText === 'function') {
      try {
        return window.lmsQuestionText(q) || '';
      } catch (e) {}
    }
    return (q && (q.question_text || q.question_latex)) || '';
  }

  function _diagOptionText(o) {
    if (typeof window.lmsOptionText === 'function') {
      try {
        return window.lmsOptionText(o) || '';
      } catch (e) {}
    }
    return (o && (o.text || o.latex)) || '';
  }

  function _diagTypeset(el) {
    if (!el) return Promise.resolve();
    if (typeof window.lmsTypesetMath === 'function') return window.lmsTypesetMath(el);
    el.classList.add('tex2jax_process');
    var ready =
      window.MathJax && window.MathJax.startup && window.MathJax.startup.promise
        ? window.MathJax.startup.promise.catch(function () {})
        : Promise.resolve();
    return ready.then(function () {
      if (window.MathJax && window.MathJax.typesetPromise) {
        try {
          if (typeof window.MathJax.typesetClear === 'function') window.MathJax.typesetClear([el]);
        } catch (e) {}
        return window.MathJax.typesetPromise([el]).catch(function () {});
      }
      if (typeof renderMathInElement === 'function') {
        try {
          renderMathInElement(el, {
            delimiters: [
              { left: '$$', right: '$$', display: true },
              { left: '$', right: '$', display: false },
              { left: '\\[', right: '\\]', display: true },
              { left: '\\(', right: '\\)', display: false },
            ],
            throwOnError: false,
            ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code'],
          });
        } catch (e2) {}
      }
    });
  }

  function _diagEscapeTextMode(s) {
    // Shared single-pass escape; the chained replaces below double-escaped "\textbackslash{}".
    if (typeof window.lmsEscapeTextMode === 'function') return window.lmsEscapeTextMode(s);
    return String(s || '')
      .replace(/\\/g, '\\textbackslash{}')
      .replace(/[{}]/g, function (ch) {
        return '\\' + ch;
      })
      .replace(/#/g, '\\#')
      .replace(/%/g, '\\%')
      .replace(/&/g, '\\&')
      .replace(/\$/g, '\\$')
      .replace(/_/g, '\\_')
      .replace(/\^/g, '\\^{}');
  }

  function _diagTextToMathlive(src) {
    src = String(src == null ? '' : src).trim();
    if (!src) return '';
    if (typeof window.lmsRepairEscapedLatex === 'function') src = window.lmsRepairEscapedLatex(src);
    if (typeof window.lmsPrepareMathText === 'function') {
      try {
        src = window.lmsPrepareMathText(src) || src;
      } catch (e) {}
    }
    var whole =
      src.match(/^\$\$([\s\S]*)\$\$\s*$/) ||
      src.match(/^\$([^$]*)\$\s*$/) ||
      src.match(/^\\\(([\s\S]*)\\\)\s*$/) ||
      src.match(/^\\\[([\s\S]*)\\\]\s*$/);
    if (whole) return String(whole[1] || '').trim();
    if (/\$[^$]+\$|\\\([\s\S]+?\\\)|\\\[[\s\S]+?\\\]/.test(src)) {
      var out = '';
      var re = /\$\$([\s\S]+?)\$\$|\$([^$\n]+?)\$|\\\(([\s\S]+?)\\\)|\\\[([\s\S]+?)\\\]/g;
      var last = 0;
      var match;
      while ((match = re.exec(src))) {
        var plain = src.slice(last, match.index);
        if (plain) out += '\\text{' + _diagEscapeTextMode(plain) + '}';
        out += match[1] || match[2] || match[3] || match[4] || '';
        last = match.index + match[0].length;
      }
      var rest = src.slice(last);
      if (rest) out += '\\text{' + _diagEscapeTextMode(rest) + '}';
      return out;
    }
    if (/\\[a-zA-Z]+/.test(src)) return src;
    return '\\text{' + _diagEscapeTextMode(src) + '}';
  }

  function _diagFromMathlive(mf) {
    if (!mf) return '';
    var latex = '';
    try {
      latex =
        typeof mf.getValue === 'function'
          ? mf.getValue('latex-without-placeholders') || mf.getValue('latex') || ''
          : mf.value || '';
    } catch (e) {
      latex = mf.value || '';
    }
    latex = String(latex || '').trim();
    if (!latex) return '';
    // Shared with the teacher quiz editor: words stored as plain prose, math in $...$.
    if (typeof window.lmsMathliveToStorage === 'function') return window.lmsMathliveToStorage(latex);
    if (/^\$|\\\(|\\\[/.test(latex)) return latex;
    return '$' + latex + '$';
  }

  function _diagConfigureMathKeyboard() {
    var vk = window.mathVirtualKeyboard;
    if (!vk) return;
    try {
      vk.layouts = ['numeric', 'symbols', 'alphabetic', 'greek', 'functions'];
    } catch (e) {}
  }

  function _diagSetupMathField(mf, rawText, isStem) {
    if (!mf) return;
    try {
      mf.mathVirtualKeyboardPolicy = 'manual';
    } catch (e) {}
    try {
      mf.defaultMode = isStem ? 'text' : 'math';
    } catch (e) {}
    try {
      mf.smartFence = true;
    } catch (e) {}
    var val = _diagTextToMathlive(rawText);
    // The value is LaTeX: insert it in math mode. With no explicit mode a text-mode stem showed the
    // code itself ("\text{...}") and saved it back escaped (same fix as the teacher quiz editor).
    var setOpts = { silenceNotifications: true };
    if (val) setOpts.mode = 'math';
    try {
      if (typeof mf.setValue === 'function') mf.setValue(val, setOpts);
      else mf.value = val;
    } catch (e) {
      try {
        mf.value = val;
      } catch (e2) {}
    }
    // Words typed into a math box (an option like "5/8 edited ...") stay words: see lms-core.js.
    if (typeof window.lmsMakeMathFieldWordFriendly === 'function') window.lmsMakeMathFieldWordFriendly(mf);
    if (mf._diagVkBound) return;
    mf._diagVkBound = true;
    mf.addEventListener('focusin', function () {
      _diagConfigureMathKeyboard();
      if (window.mathVirtualKeyboard) {
        try {
          window.mathVirtualKeyboard.show();
        } catch (e) {}
      }
    });
  }

  function _diagHydrateMathFields(root, questions) {
    _diagConfigureMathKeyboard();
    (questions || []).forEach(function (item) {
      var q = item.question || item || {};
      var qid = q.id;
      if (qid == null) return;
      var card = root.querySelector('.lms-quiz-preview-card[data-qid="' + qid + '"]');
      if (!card) return;
      var stemMf = card.querySelector('math-field.lms-q-edit-stem-input');
      _diagSetupMathField(stemMf, _diagQuestionText(q), true);
      (q.options || []).forEach(function (o, oidx) {
        var mf = card.querySelector('math-field.lms-q-edit-option[data-oidx="' + oidx + '"]');
        _diagSetupMathField(mf, _diagOptionText(o), false);
      });
    });
  }

  function _bindDiagEditHandlers(root) {
    if (!root || root._diagEditBound) return;
    root._diagEditBound = true;
    root.addEventListener('click', async function (ev) {
      var t = ev.target;
      if (!t) return;
      var toggle = t.closest && t.closest('.lms-q-edit-toggle');
      if (toggle) {
        var qidT = toggle.getAttribute('data-qid');
        var panelT = document.getElementById('diagQEdit-' + qidT);
        if (panelT) {
          panelT.hidden = !panelT.hidden;
          if (!panelT.hidden) {
            var first = panelT.querySelector('math-field.lms-q-edit-field');
            if (first && typeof first.focus === 'function') {
              setTimeout(function () {
                try {
                  first.focus();
                  // Focused by script, MathLive sits at the end in math mode and typed words lose
                  // their spaces; continue in text mode when the stem ends in words.
                  if (first.mode !== 'text' && first.position === first.lastOffset &&
                      /\\text\{[^{}]*\}\s*\$?\s*$/.test(first.getValue('latex') || '')) first.mode = 'text';
                } catch (e) {}
              }, 50);
            }
          } else if (window.mathVirtualKeyboard) {
            try {
              window.mathVirtualKeyboard.hide();
            } catch (e) {}
          }
        }
        return;
      }
      var cancel = t.closest && t.closest('.lms-q-edit-cancel');
      if (cancel) {
        var panelC = document.getElementById('diagQEdit-' + cancel.getAttribute('data-qid'));
        if (panelC) panelC.hidden = true;
        if (window.mathVirtualKeyboard) {
          try {
            window.mathVirtualKeyboard.hide();
          } catch (e) {}
        }
        return;
      }
      var save = t.closest && t.closest('.lms-q-edit-save');
      if (!save) return;
      var qid = save.getAttribute('data-qid');
      var card = root.querySelector('.lms-quiz-preview-card[data-qid="' + qid + '"]');
      var panel = document.getElementById('diagQEdit-' + qid);
      var status = document.getElementById('diagQEditStatus-' + qid);
      if (!card || !panel || !state.reviewAssessmentId) return;
      var stemMf = panel.querySelector('math-field.lms-q-edit-stem-input');
      var stem = _diagFromMathlive(stemMf);
      var correctIdx = parseInt((panel.querySelector('.lms-q-edit-correct-sel') || {}).value, 10);
      if (isNaN(correctIdx)) correctIdx = 0;
      var options = [];
      panel.querySelectorAll('math-field.lms-q-edit-option').forEach(function (mf) {
        var oidx = parseInt(mf.getAttribute('data-oidx'), 10);
        options.push({
          label: String.fromCharCode(65 + oidx),
          text: _diagFromMathlive(mf),
          latex: null,
        });
      });
      if (options.length !== 4) {
        if (status) status.textContent = 'Need 4 options.';
        return;
      }
      save.disabled = true;
      if (status) status.textContent = 'Saving...';
      try {
        var res = await fetch(
          '/api/lms/diagnostics/' + state.reviewAssessmentId + '/questions/' + qid,
          {
            method: 'PUT',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              question_text: stem,
              question_latex: null,
              options: options,
              correct_option_index: correctIdx,
            }),
          }
        );
        var body = await res.json();
        if (!res.ok) throw new Error((body.error && body.error.message) || body.message || 'Save failed');
        if (status) status.textContent = 'Saved.';
        if (window.mathVirtualKeyboard) {
          try {
            window.mathVirtualKeyboard.hide();
          } catch (e) {}
        }
        await adminPreviewDiagnostic(state.reviewAssessmentId);
      } catch (err) {
        if (status) status.textContent = 'Error: ' + err.message;
        save.disabled = false;
      }
    });
  }

  function renderDiagReviewQuestions(questions) {
    var bodyEl = document.getElementById('adminDiagReviewBody');
    if (!bodyEl) return;
    if (!questions.length) {
      bodyEl.innerHTML =
        '<p class="text-slate-500 text-sm">No questions generated yet. Re-upload the Q&amp;A PDF or wait for generation to finish.</p>';
      return;
    }
    bodyEl.innerHTML = questions
      .map(function (item, idx) {
        var q = item.question || item || {};
        var qid = q.id;
        var opts = (q.options || [])
          .map(function (o, oidx) {
            var isCorrect = q.correct_option_index === oidx;
            return (
              '<div class="lms-preview-opt' +
              (isCorrect ? ' is-correct' : '') +
              '">' +
              '<span class="lms-preview-opt-label">' +
              esc(o.label || String.fromCharCode(65 + oidx)) +
              '.</span>' +
              '<span class="lms-preview-opt-body">' +
              _diagFmtText(_diagOptionText(o)) +
              (isCorrect ? ' <span class="lms-preview-correct-mark">✓</span>' : '') +
              '</span></div>'
            );
          })
          .join('');
        var editOpts = (q.options || [])
          .map(function (o, oidx) {
            var lab = o.label || String.fromCharCode(65 + oidx);
            return (
              '<label class="lms-q-edit-opt">' +
              '<span>' +
              esc(lab) +
              '</span>' +
              '<math-field class="lms-q-edit-option lms-q-edit-field" data-oidx="' +
              oidx +
              '"></math-field>' +
              '</label>'
            );
          })
          .join('');
        var correctSel = [0, 1, 2, 3]
          .map(function (i) {
            var lab = String.fromCharCode(65 + i);
            return (
              '<option value="' +
              i +
              '"' +
              (q.correct_option_index === i ? ' selected' : '') +
              '>' +
              lab +
              '</option>'
            );
          })
          .join('');
        return (
          '<div class="lms-diag-preview-card lms-quiz-preview-card" data-qid="' +
          qid +
          '" data-qidx="' +
          idx +
          '">' +
          '<div class="lms-preview-head">' +
          '<div class="lms-preview-stem"><strong class="lms-preview-qnum">Q' +
          (idx + 1) +
          '.</strong> ' +
          _diagFmtText(_diagQuestionText(q)) +
          '</div>' +
          '<button type="button" class="lms-btn lms-btn-ghost lms-q-edit-toggle" data-qid="' +
          qid +
          '">Edit</button>' +
          '</div>' +
          '<div class="lms-preview-opts">' +
          opts +
          '</div>' +
          '<div class="lms-q-edit-panel" id="diagQEdit-' +
          qid +
          '" hidden>' +
          '<p class="lms-math-keyboard-hint">Tap a field to edit. Use the scientific keyboard for symbols, fractions, and roots.</p>' +
          '<label class="lms-q-edit-stem">Question' +
          '<math-field class="lms-q-edit-stem-input lms-q-edit-field"></math-field>' +
          '</label>' +
          '<div class="lms-q-edit-options">' +
          editOpts +
          '</div>' +
          '<label class="lms-q-edit-correct">Correct answer <select class="lms-q-edit-correct-sel">' +
          correctSel +
          '</select></label>' +
          '<div class="lms-q-edit-actions">' +
          '<button type="button" class="lms-btn lms-btn-primary lms-q-edit-save" data-qid="' +
          qid +
          '">Save</button>' +
          '<button type="button" class="lms-btn lms-btn-ghost lms-q-edit-cancel" data-qid="' +
          qid +
          '">Cancel</button>' +
          '<span class="lms-q-edit-status" id="diagQEditStatus-' +
          qid +
          '"></span>' +
          '</div></div></div>'
        );
      })
      .join('');
    _diagHydrateMathFields(bodyEl, questions);
    _diagTypeset(bodyEl);
    _bindDiagEditHandlers(bodyEl);
  }

  window.closeAdminDiagReview = function () {
    var modal = document.getElementById('adminDiagReviewModal');
    if (modal) modal.classList.remove('is-open');
    state.reviewAssessmentId = null;
    state.reviewQuestions = [];
    state.reviewTab = 'diag';
    state.bankReviewDocId = null;
    state.bankReviewItems = [];
    if (window.mathVirtualKeyboard) {
      try {
        window.mathVirtualKeyboard.hide();
      } catch (e) {}
    }
  };

  function setReviewTabUi(tab) {
    var diagTab = document.getElementById('adminDiagReviewTabDiag');
    var bankTab = document.getElementById('adminDiagReviewTabBank');
    var approveBtn = document.getElementById('adminDiagReviewApproveBtn');
    var bulkBtn = document.getElementById('adminDiagReviewBankBulkBtn');
    if (diagTab) {
      diagTab.classList.toggle('is-active', tab === 'diag');
      diagTab.setAttribute('aria-selected', tab === 'diag' ? 'true' : 'false');
    }
    if (bankTab) {
      bankTab.classList.toggle('is-active', tab === 'bank');
      bankTab.setAttribute('aria-selected', tab === 'bank' ? 'true' : 'false');
    }
    if (approveBtn) {
      approveBtn.style.display =
        tab === 'diag' && (state.reviewStatus === 'draft' || !state.reviewStatus) ? 'inline-flex' : 'none';
    }
    if (bulkBtn) bulkBtn.style.display = tab === 'bank' ? 'inline-flex' : 'none';
  }

  function bankDocsForAssessment(assessmentId) {
    return (state.bankDocs || []).filter(function (b) {
      return Number(b.assessment_id) === Number(assessmentId);
    });
  }

  function renderBankReviewEmpty(assessmentId) {
    var bodyEl = document.getElementById('adminDiagReviewBody');
    if (!bodyEl) return;
    var match = (state.diagnostics || []).find(function (d) {
      return d.id === assessmentId;
    });
    var hasTargets = !!(match && (match.target_pdfs || []).length);
    bodyEl.innerHTML =
      '<div class="text-sm text-slate-600" style="line-height:1.5">' +
      '<p><b>No Target PDF question bank for this diagnostic yet.</b></p>' +
      (hasTargets
        ? '<p>Study PDFs are attached, but no printable MCQs were extracted into the bank (or extraction failed). Re-upload a Target PDF that contains printed questions with an answer key, or import from <a href="/admin/target-bank">/admin/target-bank</a>.</p>'
        : '<p>Add study PDFs with <b>Add study PDFs</b> on this diagnostic. Printed MCQs are extracted automatically, then you approve them on this tab.</p>') +
      '<p class="text-slate-500">Guided Practice stays empty until questions are approved here.</p></div>';
    var statusEl = document.getElementById('adminDiagReviewStatus');
    if (statusEl) statusEl.textContent = '0 bank questions';
    var bulkBtn = document.getElementById('adminDiagReviewBankBulkBtn');
    if (bulkBtn) bulkBtn.disabled = true;
  }

  function bankItemHtml(it) {
    var status = it.verification_status || '';
    var opts = (it.options || [])
      .map(function (o) {
        return (
          '<div class="opt' +
          (o.label === it.correct_label ? ' correct' : '') +
          '"><b>' +
          esc(o.label) +
          '.</b> ' +
          (o.render || esc(o.text || '')) +
          (o.label === it.correct_label ? ' <span class="text-slate-500">(key)</span>' : '') +
          '</div>'
        );
      })
      .join('');
    var actions = '<div class="d-actions" style="margin-top:0.6rem">';
    if (status !== 'approved') {
      actions +=
        '<button type="button" class="d-action primary" data-bank-act="approve" data-id="' +
        it.item_id +
        '">' +
        (status === 'needs_review' ? 'Checked — approve' : 'Approve') +
        '</button>';
    }
    if (status !== 'rejected') {
      actions +=
        '<button type="button" class="d-action danger" data-bank-act="reject" data-id="' +
        it.item_id +
        '">Reject</button>';
    }
    actions +=
      '<button type="button" class="d-action" data-bank-act="edit" data-id="' +
      it.item_id +
      '"><i class="fas fa-pen"></i> Edit</button></div>';
    var editPanel =
      '<div class="d-bank-edit" id="adminBankEdit-' +
      it.item_id +
      '" hidden>' +
      '<div class="d-bank-ai">' +
      '<label class="d-bank-edit-field"><b>Edit with AI</b> — plain English, no math keyboard' +
      '<textarea rows="2" data-f="ai_instruction" placeholder="Example: put n as the index of the radical (nth root of a), not beside the square root"></textarea></label>' +
      '<button type="button" class="d-action primary" data-bank-act="ai" data-id="' +
      it.item_id +
      '"><i class="fas fa-magic"></i> Fix with AI</button>' +
      '<span class="text-slate-500 text-sm" data-ai-status></span></div>' +
      '<hr class="d-bank-edit-hr">' +
      '<label class="d-bank-edit-field">Question<textarea rows="3" data-f="question_text">' +
      esc(it.question_text || '') +
      '</textarea></label>' +
      (it.options || [])
        .map(function (o, i) {
          return (
            '<label class="d-bank-edit-field">' +
            esc(o.label || String.fromCharCode(65 + i)) +
            '<input data-f="opt" data-i="' +
            i +
            '" value="' +
            esc(o.text || '') +
            '"></label>'
          );
        })
        .join('') +
      '<label class="d-bank-edit-field">Answer (A–D)<input data-f="correct_label" value="' +
      esc(it.correct_label || '') +
      '" maxlength="1"></label>' +
      '<label class="d-bank-edit-field">Printed Domain<input data-f="printed_domain" value="' +
      esc(it.printed_domain || '') +
      '"></label>' +
      '<label class="d-bank-edit-field">Printed Topic (subtopic)<input data-f="printed_topic" value="' +
      esc(it.printed_topic || '') +
      '"></label>' +
      '<p class="text-slate-500 text-sm">AI or manual save keeps history and returns the item to Needs review.</p>' +
      '<button type="button" class="d-action" data-bank-act="save" data-id="' +
      it.item_id +
      '">Save correction</button>' +
      '</div>';
    return (
      '<article class="d-bank-item" data-bank-item="' +
      it.item_id +
      '">' +
      '<div class="d-chips" style="margin-bottom:0.4rem">' +
      '<span class="d-bank-badge ' +
      esc(status) +
      '">' +
      esc(status.replace(/_/g, ' ')) +
      '</span>' +
      '<span class="text-slate-500 text-sm">Q' +
      esc(String(it.source_question_number)) +
      (it.source_page != null ? ' · page ' + esc(String(it.source_page)) : '') +
      '</span></div>' +
      '<div><b>' +
      (it.question_render || esc(it.question_text || '')) +
      '</b></div>' +
      opts +
      '<p class="text-slate-500 text-sm" style="margin-top:0.45rem"><b>Domain:</b> ' +
      esc(it.domain_name || it.printed_domain || '—') +
      ' · <b>Subtopic:</b> ' +
      esc(it.subtopic_name || it.printed_topic || '—') +
      '</p>' +
      actions +
      editPanel +
      '</article>'
    );
  }

  async function loadBankReviewItems() {
    var bodyEl = document.getElementById('adminDiagReviewBody');
    var statusEl = document.getElementById('adminDiagReviewStatus');
    var bulkBtn = document.getElementById('adminDiagReviewBankBulkBtn');
    if (!bodyEl || !state.reviewAssessmentId) return;

    var docs = bankDocsForAssessment(state.reviewAssessmentId);
    if (!docs.length) {
      renderBankReviewEmpty(state.reviewAssessmentId);
      return;
    }
    if (!state.bankReviewDocId || !docs.some(function (d) { return Number(d.document_id) === Number(state.bankReviewDocId); })) {
      state.bankReviewDocId = docs[0].document_id;
    }
    var current = docs.find(function (d) {
      return Number(d.document_id) === Number(state.bankReviewDocId);
    });
    var counts = (current && current.status_counts) || {};
    var autoN = counts.auto_verified || 0;
    if (bulkBtn) {
      bulkBtn.disabled = !autoN;
      bulkBtn.innerHTML =
        '<i class="fas fa-check-double"></i> Approve all auto-verified (' + autoN + ')';
    }

    var toolbar =
      '<div class="d-bank-toolbar">' +
      '<label class="text-sm text-slate-600">PDF <select id="adminBankDocSelect">' +
      docs
        .map(function (d) {
          return (
            '<option value="' +
            d.document_id +
            '"' +
            (Number(d.document_id) === Number(state.bankReviewDocId) ? ' selected' : '') +
            '>' +
            esc(d.filename || 'document') +
            '</option>'
          );
        })
        .join('') +
      '</select></label>' +
      '<label class="text-sm text-slate-600">Status <select id="adminBankStatusSelect">' +
      [
        ['', 'All'],
        ['auto_verified', 'Auto-verified'],
        ['needs_review', 'Needs review'],
        ['approved', 'Approved'],
        ['rejected', 'Rejected'],
      ]
        .map(function (pair) {
          return (
            '<option value="' +
            pair[0] +
            '"' +
            (state.bankReviewStatus === pair[0] ? ' selected' : '') +
            '>' +
            pair[1] +
            '</option>'
          );
        })
        .join('') +
      '</select></label></div>';

    bodyEl.innerHTML = toolbar + '<p class="text-slate-500 text-sm">Loading bank questions…</p>';
    try {
      var q =
        '/api/lms/target-bank/documents/' +
        Number(state.bankReviewDocId) +
        '/items?limit=100&offset=0' +
        (state.bankReviewStatus ? '&status=' + encodeURIComponent(state.bankReviewStatus) : '');
      var data = await api(q);
      state.bankReviewItems = data.items || [];
      state.bankReviewTotal = data.total || 0;
      if (statusEl) {
        statusEl.textContent =
          state.bankReviewTotal +
          ' bank question' +
          (state.bankReviewTotal === 1 ? '' : 's') +
          (autoN ? ' · ' + autoN + ' auto-verified ready' : '');
      }
      bodyEl.innerHTML =
        toolbar +
        (state.bankReviewItems.length
          ? state.bankReviewItems.map(bankItemHtml).join('')
          : '<p class="text-slate-500 text-sm">Nothing in this filter.</p>');
      if (window.MathJax && MathJax.typesetPromise) {
        MathJax.typesetPromise([bodyEl]).catch(function () {});
      }
      var docSel = document.getElementById('adminBankDocSelect');
      var stSel = document.getElementById('adminBankStatusSelect');
      if (docSel) {
        docSel.onchange = function () {
          state.bankReviewDocId = Number(docSel.value);
          loadBankReviewItems();
        };
      }
      if (stSel) {
        stSel.onchange = function () {
          state.bankReviewStatus = stSel.value;
          loadBankReviewItems();
        };
      }
      if (!bodyEl._bankActBound) {
        bodyEl._bankActBound = true;
        bodyEl.addEventListener('click', onBankReviewClick);
      }
    } catch (err) {
      bodyEl.innerHTML = toolbar + '<p class="text-red-600 text-sm">Bank load failed: ' + esc(err.message) + '</p>';
    }
  }

  function _bankCollectManualChanges(box) {
    var changes = { options: [] };
    box.querySelectorAll('[data-f]').forEach(function (el) {
      var f = el.getAttribute('data-f');
      if (f === 'ai_instruction') return;
      if (f === 'opt') changes.options[Number(el.getAttribute('data-i'))] = el.value;
      else changes[f] = el.value;
    });
    return changes;
  }

  async function onBankReviewClick(e) {
    var btn = e.target.closest('[data-bank-act]');
    if (!btn || state.reviewTab !== 'bank') return;
    var id = Number(btn.getAttribute('data-id'));
    var act = btn.getAttribute('data-bank-act');
    try {
      if (act === 'edit') {
        var ed = document.getElementById('adminBankEdit-' + id);
        if (ed) {
          ed.hidden = !ed.hidden;
          if (!ed.hidden) {
            var aiBox = ed.querySelector('[data-f="ai_instruction"]');
            if (aiBox) aiBox.focus();
          }
        }
        return;
      }
      btn.disabled = true;
      if (act === 'approve') {
        var r = await api('/api/lms/target-bank/items/approve', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ item_ids: [id], reviewed_individually: true }),
        });
        if (r.skipped && r.skipped.length) alert('Not approved: ' + r.skipped[0].reason);
      } else if (act === 'reject') {
        var reason = prompt('Reason for rejecting (optional):') || '';
        await api('/api/lms/target-bank/items/reject', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ item_ids: [id], reason: reason }),
        });
      } else if (act === 'ai') {
        var boxAi = document.getElementById('adminBankEdit-' + id);
        if (!boxAi) return;
        var instruction = ((boxAi.querySelector('[data-f="ai_instruction"]') || {}).value || '').trim();
        var statusEl = boxAi.querySelector('[data-ai-status]');
        if (!instruction) {
          alert('Write a short instruction for AI, e.g. "put n as the radical index".');
          btn.disabled = false;
          return;
        }
        if (statusEl) statusEl.textContent = 'AI is fixing…';
        var aiRes = await api('/api/lms/target-bank/items/' + id + '/ai-edit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ instruction: instruction, apply: true }),
        });
        if (statusEl) statusEl.textContent = (aiRes && aiRes.notes) || 'Saved. Review then Approve.';
      } else if (act === 'save') {
        var box = document.getElementById('adminBankEdit-' + id);
        if (!box) return;
        await api('/api/lms/target-bank/items/' + id, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(_bankCollectManualChanges(box)),
        });
      } else {
        btn.disabled = false;
        return;
      }
      await loadBankDocs();
      await loadBankReviewItems();
      renderAdminDiagList(state.diagnostics);
    } catch (err) {
      alert(err.message);
      btn.disabled = false;
      var failBox = document.getElementById('adminBankEdit-' + id);
      var failStatus = failBox && failBox.querySelector('[data-ai-status]');
      if (failStatus) failStatus.textContent = '';
    }
  }

  window.adminApproveBankAutoVerified = async function () {
    if (!state.bankReviewDocId) return;
    var ids = [];
    try {
      for (var off = 0; ; off += 200) {
        var page = await api(
          '/api/lms/target-bank/documents/' +
            Number(state.bankReviewDocId) +
            '/items?status=auto_verified&limit=200&offset=' +
            off
        );
        (page.items || []).forEach(function (i) {
          ids.push(i.item_id);
        });
        if (!(page.items || []).length || ids.length >= (page.total || 0)) break;
      }
    } catch (err) {
      alert(err.message);
      return;
    }
    if (!ids.length) {
      alert('No auto-verified items left to approve.');
      return;
    }
    if (!confirm('Approve ' + ids.length + ' auto-verified question(s)? Items needing review stay for one-by-one approval.')) {
      return;
    }
    try {
      var r = await api('/api/lms/target-bank/items/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item_ids: ids }),
      });
      alert('Approved ' + (r.approved || []).length + ((r.skipped || []).length ? ', skipped ' + r.skipped.length : ''));
      await loadBankDocs();
      await loadBankReviewItems();
      renderAdminDiagList(state.diagnostics);
    } catch (err) {
      alert(err.message);
    }
  };

  window.adminSetReviewTab = async function (tab) {
    state.reviewTab = tab === 'bank' ? 'bank' : 'diag';
    setReviewTabUi(state.reviewTab);
    var subEl = document.getElementById('adminDiagReviewSubtitle');
    if (state.reviewTab === 'bank') {
      if (subEl) {
        subEl.textContent =
          'Approve extracted Target PDF MCQs here (same as diagnostic review). Students only get Guided Practice from approved items.';
      }
      await loadBankReviewItems();
      return;
    }
    if (subEl) {
      subEl.textContent =
        state.reviewStatus === 'published'
          ? 'Live for students. You can still edit questions if needed.'
          : 'Draft — verify and edit MCQs. Students only see this after you Approve.';
    }
    if (state.reviewQuestions && state.reviewQuestions.length) {
      renderDiagReviewQuestions(state.reviewQuestions);
      var statusEl = document.getElementById('adminDiagReviewStatus');
      if (statusEl) {
        statusEl.textContent =
          state.reviewQuestions.length +
          ' question' +
          (state.reviewQuestions.length === 1 ? '' : 's') +
          (state.reviewStatus === 'draft' ? ' · awaiting approval' : '');
      }
    } else if (state.reviewAssessmentId) {
      await adminPreviewDiagnostic(state.reviewAssessmentId, { tab: 'diag', skipOpen: true });
    }
  };

  window.adminPreviewDiagnostic = async function (id, opts) {
    opts = opts || {};
    var modal = document.getElementById('adminDiagReviewModal');
    var bodyEl = document.getElementById('adminDiagReviewBody');
    var statusEl = document.getElementById('adminDiagReviewStatus');
    var titleEl = document.getElementById('adminDiagReviewTitle');
    var subEl = document.getElementById('adminDiagReviewSubtitle');
    if (!modal || !bodyEl) return;

    state.reviewAssessmentId = id;
    state.reviewTab = opts.tab === 'bank' ? 'bank' : 'diag';
    if (opts.documentId) state.bankReviewDocId = Number(opts.documentId);
    if (opts.status != null) state.bankReviewStatus = String(opts.status);
    if (!opts.skipOpen) modal.classList.add('is-open');
    bodyEl.innerHTML = '<p class="text-slate-500 text-sm">Loading questions…</p>';
    if (statusEl) statusEl.textContent = '';

    var match = (state.diagnostics || []).find(function (d) {
      return d.id === id;
    });
    var status = ((match && match.status) || '').toLowerCase();
    state.reviewStatus = status;
    if (titleEl) {
      titleEl.textContent = (match && match.title) || 'Review diagnostic questions';
    }
    setReviewTabUi(state.reviewTab);

    if (state.reviewTab === 'bank') {
      if (subEl) {
        subEl.textContent =
          'Approve extracted Target PDF MCQs here (same as diagnostic review). Students only get Guided Practice from approved items.';
      }
      await loadBankDocs();
      await loadBankReviewItems();
      return;
    }

    if (subEl) {
      subEl.textContent =
        status === 'published'
          ? 'Live for students. You can still edit questions if needed.'
          : 'Draft — verify and edit MCQs. Students only see this after you Approve.';
    }

    try {
      var data = await api('/api/lms/diagnostics/' + id + '/preview');
      var questions = (data && data.questions) || [];
      state.reviewQuestions = questions;
      if (statusEl) {
        statusEl.textContent =
          questions.length +
          ' question' +
          (questions.length === 1 ? '' : 's') +
          (status === 'draft' ? ' · awaiting approval' : '');
      }
      // The click handler is delegated on bodyEl, which outlives every re-render, so it is bound once
      // (see _bindDiagEditHandlers). Resetting the flag here added a second handler after each save:
      // the next Edit click then opened the panel and closed it again at once.
      renderDiagReviewQuestions(questions);
    } catch (err) {
      bodyEl.innerHTML = '<p class="text-red-600 text-sm">Preview failed: ' + esc(err.message) + '</p>';
    }
  };
  window.adminRemoveDiagnostic = async function (id) {
    if (
      !confirm(
        'Remove this diagnostic? Students will not see it until you upload and publish a new one.'
      )
    ) {
      return;
    }
    try {
      await api('/api/lms/admin/diagnostics/' + id, { method: 'DELETE' });
      if (state.selectedTargetAssessmentId === id) {
        state.selectedTargetAssessmentId = null;
        state.assessmentId = null;
      }
      loadAdminDiagnostics();
    } catch (err) {
      alert('Error: ' + err.message);
    }
  };

  window.adminRemoveTargetPdf = async function (assessmentId, targetPdfId) {
    if (!confirm('Remove this target PDF?')) return;
    try {
      await api('/api/lms/diagnostics/' + assessmentId + '/target-pdf/' + targetPdfId, {
        method: 'DELETE',
      });
      loadAdminDiagnostics();
    } catch (err) {
      alert('Error: ' + err.message);
    }
  };

  window.submitAdminAddTargetPdfs = async function (e) {
    e.preventDefault();
    var assessmentId = resolveSelectedAssessmentId();
    if (!assessmentId) {
      alert('Select a grade first — target PDFs must append to a specific class.');
      setAdminDiagUploadTab('append');
      var select = document.getElementById('adminDiagAddTargetGrade');
      if (select) select.focus();
      return;
    }

    var files = selectedTargetFiles.adminDiagAddTargetFiles || [];
    var status = document.getElementById('adminDiagAddTargetStatus');
    var btn = document.getElementById('adminDiagAddTargetBtn');
    if (!files || !files.length) {
      alert('Select at least one PDF file.');
      return;
    }

    var match = (state.diagnostics || []).find(function (d) {
      return d.id === assessmentId;
    });
    var gradeText = match ? gradeLabel(match.grade_level) : 'selected grade';

    btn.disabled = true;
    status.textContent = 'Uploading ' + files.length + ' PDF(s) to ' + gradeText + '...';

    var fd = new FormData();
    appendTargetFiles(fd, files);

    try {
      var result = await fetch('/api/lms/diagnostics/' + assessmentId + '/target-pdf', {
        method: 'POST',
        credentials: 'include',
        body: fd,
      });
      var body = await result.json();
      if (!result.ok) {
        throw new Error((body.error && body.error.message) || 'Upload failed');
      }
      var data = body.data || body;
      var uploaded = (data.uploaded || []).length;
      status.textContent =
        'Uploaded ' + uploaded + ' target PDF(s) to ' + gradeText + ' successfully.';
      document.getElementById('adminDiagAddTargetsForm').reset();
      selectedTargetFiles.adminDiagAddTargetFiles = [];
      state.selectedTargetAssessmentId = assessmentId;
      updateAdminDiagAddTargetFileList();
      loadAdminDiagnostics();
      setAdminDiagUploadTab('append');
      selectAdminDiagTargetGrade(assessmentId);
    } catch (err) {
      status.textContent = 'Error: ' + err.message;
      setAppendInputsEnabled(true);
    }
    btn.disabled = false;
  };

  window.submitAdminDiagnostic = function (e) {
    e.preventDefault();
    var title = document.getElementById('adminDiagTitle').value.trim();
    var grade = document.getElementById('adminDiagGrade').value;
    var diagFile = document.getElementById('adminDiagQaFile').files[0];
    var targetFiles = selectedTargetFiles.adminDiagTargetFiles || [];
    var status = document.getElementById('adminDiagStatus');
    var btn = document.getElementById('adminDiagUploadBtn');

    if (!diagFile) {
      alert('Diagnostic Q&A PDF is required.');
      if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
      return;
    }
    var diagName = (diagFile.name || '').toLowerCase();
    if (
      !diagName.endsWith('.pdf') ||
      targetFiles.some(function (f) {
        return !((f.name || '').toLowerCase().endsWith('.pdf'));
      })
    ) {
      status.textContent =
        'Error: This document does not match the required assessment format. Please upload a valid document.';
      if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
      return;
    }
    if (!grade) {
      alert('Select a grade for this diagnostic.');
      if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
      return;
    }
    if (!targetFiles || !targetFiles.length) {
      alert('At least one target content PDF is required.');
      if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
      return;
    }

    btn.disabled = true;
    status.textContent = '';
    setProgress(5, 'Preparing upload of ' + targetFiles.length + ' target PDF(s)...');
    var progressJobId =
      'diag-' + Date.now() + '-' + Math.random().toString(36).slice(2, 10);

    var fd = new FormData();
    fd.append('title', title);
    fd.append('grade_level', grade);
    fd.append('diagnostic_file', diagFile);
    fd.append('progress_job_id', progressJobId);
    appendTargetFiles(fd, targetFiles);

    var uploadBytesDone = false;
    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/lms/diagnostics/from-pdf');
    xhr.withCredentials = true;
    // Large PDFs can take many minutes; do not abort on a short browser timeout.
    xhr.timeout = 0;
    if (typeof window.bindUploadEtaGuard === 'function') {
      window.bindUploadEtaGuard(xhr, function (msg, info) {
        var uploadPct =
          info && info.pct != null ? 5 + Math.round((info.pct / 100) * 15) : null;
        setProgress(uploadPct, msg);
        status.textContent = msg;
      });
    } else {
      xhr.upload.onprogress = function (ev) {
        if (ev.lengthComputable) {
          var uploadPct = 5 + Math.round((ev.loaded / ev.total) * 15);
          setProgress(uploadPct, 'Uploading PDFs to server...');
        }
      };
    }
    xhr.upload.onload = function () {
      uploadBytesDone = true;
      setProgress(20, 'Upload complete — processing on server...');
      if (typeof window.updateWaitOverlay === 'function') {
        window.updateWaitOverlay('Upload complete — processing on server...');
      }
      startServerProgressPoll(progressJobId, targetFiles, { waitForDone: true });
    };
    xhr.onabort = function () {
      failAdminDiagUpload('Upload cancelled.');
    };
    xhr.onload = async function () {
      if (xhr.status === 413) {
        failAdminDiagUpload(
          'Error: Upload too large (max 200MB total for diagnostic + target PDFs). Compress or split the PDFs and try again.'
        );
        return;
      }
      var body;
      try {
        body = JSON.parse(xhr.responseText);
      } catch (err) {
        // Connection may drop while server still processes; keep polling.
        if (uploadBytesDone) {
          setProgress(22, 'Connection interrupted — still tracking server progress...');
          startServerProgressPoll(progressJobId, targetFiles, { waitForDone: true });
          return;
        }
        failAdminDiagUpload(
          'Invalid server response (HTTP ' + xhr.status + '). Try again or check server logs.'
        );
        return;
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        failAdminDiagUpload(
          'Error: ' + ((body.error && body.error.message) || 'Upload failed')
        );
        return;
      }

      var d = body.data || body;
      // Async accept (202): keep polling until Redis progress reports done.
      if (d.async || xhr.status === 202 || !d.assessment_id) {
        setProgress(22, d.message || 'Processing on server...');
        startServerProgressPoll(
          d.progress_job_id || progressJobId,
          targetFiles,
          { waitForDone: true }
        );
        return;
      }
      if (window._adminDiagProgressPoll) {
        clearInterval(window._adminDiagProgressPoll);
        window._adminDiagProgressPoll = null;
      }
      finishAdminDiagUploadSuccess(d, targetFiles);
    };
    xhr.onerror = function () {
      // After bytes are up, the server usually keeps processing; do not abort the job.
      if (uploadBytesDone) {
        setProgress(22, 'Connection interrupted — still tracking server progress...');
        startServerProgressPoll(progressJobId, targetFiles, { waitForDone: true });
        return;
      }
      failAdminDiagUpload('Network error during upload. Check your connection and try again.');
    };
    xhr.send(fd);
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      bindDropZones();
      setAdminDiagUploadTab('publish');
    });
  } else {
    bindDropZones();
    setAdminDiagUploadTab('publish');
  }
})();
