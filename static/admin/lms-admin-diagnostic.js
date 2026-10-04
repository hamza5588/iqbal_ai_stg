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
    try {
      if (typeof mf.setValue === 'function') mf.setValue(val, { silenceNotifications: true });
      else mf.value = val;
    } catch (e) {
      try {
        mf.value = val;
      } catch (e2) {}
    }
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
    if (window.mathVirtualKeyboard) {
      try {
        window.mathVirtualKeyboard.hide();
      } catch (e) {}
    }
  };

  window.adminPreviewDiagnostic = async function (id) {
    var modal = document.getElementById('adminDiagReviewModal');
    var bodyEl = document.getElementById('adminDiagReviewBody');
    var statusEl = document.getElementById('adminDiagReviewStatus');
    var titleEl = document.getElementById('adminDiagReviewTitle');
    var subEl = document.getElementById('adminDiagReviewSubtitle');
    var approveBtn = document.getElementById('adminDiagReviewApproveBtn');
    if (!modal || !bodyEl) return;

    state.reviewAssessmentId = id;
    modal.classList.add('is-open');
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
    if (subEl) {
      subEl.textContent =
        status === 'published'
          ? 'Live for students. You can still edit questions if needed.'
          : 'Draft — verify and edit MCQs. Students only see this after you Approve.';
    }
    if (approveBtn) {
      approveBtn.style.display = status === 'draft' || !status ? 'inline-flex' : 'none';
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
      // Reset edit binding so re-render rebinds cleanly
      bodyEl._diagEditBound = false;
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

      setProgress(100, 'Draft ready — review before students see it');
      var d = body.data || body;
      state.assessmentId = d.assessment_id;
      state.threadId = d.thread_id;
      var targetCount =
        (d.target_filenames || d.target_thread_ids || []).length || targetFiles.length;

      status.textContent =
        'Draft saved with ' +
        (d.question_count || '?') +
        ' questions and ' +
        targetCount +
        ' target PDF(s). Review the MCQs below, then Approve for Students.';
      document.getElementById('adminDiagnosticPdfForm').reset();
      selectedTargetFiles.adminDiagTargetFiles = [];
      updateAdminDiagTargetFileList();
      updateAdminDiagQaFileLabel();
      state.selectedTargetAssessmentId = d.assessment_id;
      state.listFilter = 'draft';
      loadAdminDiagnostics();
      btn.disabled = false;
      setTimeout(resetProgress, 800);
      if (typeof hideWaitOverlay === 'function') hideWaitOverlay();
      // Show generated MCQs for verify/edit immediately (quiz-parity review gate)
      if (d.assessment_id) {
        setTimeout(function () {
          adminPreviewDiagnostic(d.assessment_id);
        }, 200);
      }
    };    xhr.onerror = function () {
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
