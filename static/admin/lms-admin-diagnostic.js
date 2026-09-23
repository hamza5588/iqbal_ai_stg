/** Admin Diagnostic Assessment — Tabbed Upload Hub */

(function () {
  var state = {
    assessmentId: null,
    threadId: null,
    diagnostics: [],
    selectedTargetAssessmentId: null,
    listFilter: 'published',
    uploadTab: 'publish',
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
          '<span class="text-[#05B0FC] font-bold">' +
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
    if (bar) bar.style.width = pct + '%';
    if (pctEl) pctEl.textContent = pct + '%';
    if (textEl) textEl.textContent = text || '';
  }

  function resetProgress() {
    var wrap = document.getElementById('adminDiagProgressWrap');
    if (wrap) wrap.style.display = 'none';
    if (window._adminDiagProgressPoll) {
      clearInterval(window._adminDiagProgressPoll);
      window._adminDiagProgressPoll = null;
    }
  }

  function startServerProgressPoll(jobId) {
    if (!jobId) return;
    if (window._adminDiagProgressPoll) clearInterval(window._adminDiagProgressPoll);
    window._adminDiagProgressPoll = setInterval(function () {
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
          if (uiPct > 93) uiPct = 93;
          setProgress(uiPct, d.message || 'Processing on server...');
        })
        .catch(function () {});
    }, 700);
  }

  function appendTargetFiles(fd, files) {
    for (var i = 0; i < files.length; i++) {
      fd.append('target_files', files[i]);
      fd.append('target_files[]', files[i]);
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
                '</div>'
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
          if (isPublished) {
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

  window.loadAdminDiagnostics = async function () {
    var listEl = document.getElementById('adminDiagList');
    if (!listEl) return;
    listEl.innerHTML = '<p class="text-slate-500 text-sm">Loading...</p>';
    bindDropZones();
    setAdminDiagUploadTab(state.uploadTab || 'publish');

    try {
      var items = await api('/api/lms/admin/diagnostics');
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

    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/lms/diagnostics/from-pdf');
    xhr.withCredentials = true;
    xhr.upload.onprogress = function (ev) {
      if (ev.lengthComputable) {
        var uploadPct = 5 + Math.round((ev.loaded / ev.total) * 15);
        setProgress(uploadPct, 'Uploading PDFs to server...');
      }
    };
    xhr.upload.onload = function () {
      setProgress(20, 'Upload complete — processing on server...');
      startServerProgressPoll(progressJobId);
    };
    xhr.onload = async function () {
      if (window._adminDiagProgressPoll) {
        clearInterval(window._adminDiagProgressPoll);
        window._adminDiagProgressPoll = null;
      }
      var body;
      try {
        body = JSON.parse(xhr.responseText);
      } catch (err) {
        status.textContent = 'Invalid server response';
        btn.disabled = false;
        resetProgress();
        if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
        return;
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        status.textContent =
          'Error: ' + ((body.error && body.error.message) || 'Upload failed');
        btn.disabled = false;
        resetProgress();
        if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
        return;
      }

      setProgress(94, 'Publishing diagnostic for students...');
      var d = body.data || body;
      state.assessmentId = d.assessment_id;
      state.threadId = d.thread_id;
      var targetCount =
        (d.target_filenames || d.target_thread_ids || []).length || targetFiles.length;

      try {
        await api('/api/lms/diagnostics/' + d.assessment_id + '/publish', {
          method: 'POST',
        });
        setProgress(100, 'Published!');
        status.textContent =
          'Diagnostic published with ' +
          (d.question_count || '?') +
          ' questions and ' +
          targetCount +
          ' target PDF(s).';
        document.getElementById('adminDiagnosticPdfForm').reset();
        selectedTargetFiles.adminDiagTargetFiles = [];
        updateAdminDiagTargetFileList();
        updateAdminDiagQaFileLabel();
        state.selectedTargetAssessmentId = d.assessment_id;
        loadAdminDiagnostics();
      } catch (pubErr) {
        status.textContent =
          'Uploaded (' + targetCount + ' target PDFs) but publish failed: ' + pubErr.message;
      }
      btn.disabled = false;
      setTimeout(resetProgress, 800);
      if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
    };
    xhr.onerror = function () {
      if (window._adminDiagProgressPoll) {
        clearInterval(window._adminDiagProgressPoll);
        window._adminDiagProgressPoll = null;
      }
      status.textContent = 'Network error during upload';
      btn.disabled = false;
      resetProgress();
      if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
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
