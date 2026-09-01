/** Admin Diagnostic Assessment management */

(function () {

  var state = { assessmentId: null, threadId: null };
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

    listEl.innerHTML = files.map(function (f, idx) {

      var sizeMb = (f.size / (1024 * 1024)).toFixed(2);

      return '<li class="flex items-center gap-2 bg-gray-50 border border-gray-200 rounded px-3 py-1.5">' +

        '<span class="text-[#05B0FC] font-bold">' + (idx + 1) + '.</span>' +

        '<span class="flex-1 truncate" title="' + esc(f.name) + '">' + esc(f.name) + '</span>' +

        '<span class="text-gray-400 text-xs whitespace-nowrap">' + sizeMb + ' MB</span>' +

        '<button type="button" class="text-red-500 hover:underline text-xs whitespace-nowrap" onclick="removeAdminDiagTargetFile(\'' +

        inputId + '\',' + idx + ')">Remove</button>' +

        '</li>';

    }).join('');

    if (countEl) {

      countEl.style.display = 'block';

      countEl.textContent = files.length + ' PDF' + (files.length === 1 ? '' : 's') + ' selected';

    }

  }

  function addTargetFiles(inputId, fileList) {

    var files = Array.prototype.slice.call(fileList || []);

    if (!files.length) return;

    var existing = selectedTargetFiles[inputId] || [];

    files.forEach(function (file) {

      var duplicate = existing.some(function (current) {

        return current.name === file.name &&

          current.size === file.size &&

          current.lastModified === file.lastModified;

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

    if (inputId === 'adminDiagTargetFiles') {

      updateAdminDiagTargetFileList();

    } else {

      updateAdminDiagAddTargetFileList();

    }

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

    if (window._adminDiagProgressPoll) {

      clearInterval(window._adminDiagProgressPoll);

    }

    window._adminDiagProgressPoll = setInterval(function () {

      fetch('/api/lms/diagnostics/upload-progress/' + encodeURIComponent(jobId), {

        credentials: 'include',

      })

        .then(function (res) { return res.json(); })

        .then(function (body) {

          var d = body.data || body;

          if (!d || d.percent == null) return;

          var serverPct = Math.max(0, Math.min(100, Number(d.percent) || 0));

          var uiPct = 20 + Math.round(serverPct * 0.73);

          if (uiPct > 93) uiPct = 93;

          setProgress(uiPct, d.message || 'Processing on server...');

        })

        .catch(function () { /* best-effort polling */ });

    }, 700);

  }



  function appendTargetFiles(fd, files) {

    for (var i = 0; i < files.length; i++) {

      fd.append('target_files', files[i]);

      fd.append('target_files[]', files[i]);

    }

  }



  window.loadAdminDiagnostics = async function () {

    var listEl = document.getElementById('adminDiagList');

    if (!listEl) return;

    listEl.innerHTML = '<p class="text-gray-500">Loading...</p>';

    try {

      var items = await api('/api/lms/admin/diagnostics');

      if (!items.length) {

        listEl.innerHTML = '<p class="text-gray-500">No diagnostic uploaded yet. Use the form below to create one.</p>';

        state.assessmentId = null;

      } else {

        listEl.innerHTML = items.map(function (d) {

          var statusCls = d.status === 'published' ? 'text-green-600' : 'text-gray-500';

          var targets = (d.target_pdfs || []).map(function (t) {

            return '<li class="text-sm text-gray-600 flex items-center gap-2">' +

              '<i class="fas fa-file-pdf text-red-400"></i>' +

              '<span>' + esc(t.original_filename || 'target.pdf') + '</span>' +

              '<button type="button" class="text-red-500 hover:underline ml-auto text-xs" onclick="adminRemoveTargetPdf(' +

              d.id + ',' + t.id + ')">Remove</button></li>';

          }).join('');

          var targetBlock = targets

            ? '<div class="mt-2"><p class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Target PDFs (' +

              (d.target_pdfs || []).length + ')</p><ul class="space-y-1">' + targets + '</ul></div>'

            : '<p class="text-xs text-amber-600 mt-2">No target content PDFs attached yet.</p>';

          return '<div class="border rounded-lg p-4 mb-3 bg-gray-50">' +

            '<div class="flex justify-between items-start gap-4">' +

            '<div class="flex-1 min-w-0"><h4 class="font-semibold text-gray-800">' + esc(d.title) + '</h4>' +

            '<p class="text-sm ' + statusCls + '">' + esc(d.status) + ' &middot; ' +

            (d.question_count || 0) + ' questions' +

            (d.time_limit_minutes ? ' &middot; ~' + d.time_limit_minutes + ' min timer' : '') + '</p>' +

            targetBlock + '</div>' +

            (d.status === 'published' ?

              '<button type="button" onclick="adminRemoveDiagnostic(' + d.id + ')" class="text-red-600 hover:underline text-sm whitespace-nowrap">Remove diagnostic</button>' : '') +

            '</div></div>';

        }).join('');

      }



      var active = items.find(function (d) { return d.status === 'published'; });

      var uploadSection = document.getElementById('adminDiagUploadSection');

      var activeNote = document.getElementById('adminDiagActiveNote');

      var addTargetsSection = document.getElementById('adminDiagAddTargetsSection');



      if (active) {

        state.assessmentId = active.id;

        if (uploadSection) uploadSection.style.display = 'none';

        if (activeNote) activeNote.style.display = 'block';

        if (addTargetsSection) addTargetsSection.style.display = 'block';

      } else {

        state.assessmentId = null;

        if (uploadSection) uploadSection.style.display = 'block';

        if (activeNote) activeNote.style.display = 'none';

        if (addTargetsSection) addTargetsSection.style.display = 'none';

      }

    } catch (err) {

      listEl.innerHTML = '<p class="text-red-600">' + esc(err.message) + '</p>';

    }

  };



  window.adminRemoveDiagnostic = async function (id) {

    if (!confirm('Remove this diagnostic? Students will not see it until you upload and publish a new one.')) return;

    try {

      await api('/api/lms/admin/diagnostics/' + id, { method: 'DELETE' });

      state.assessmentId = null;

      loadAdminDiagnostics();

    } catch (err) {

      alert('Error: ' + err.message);

    }

  };



  window.adminRemoveTargetPdf = async function (assessmentId, targetPdfId) {

    if (!confirm('Remove this target PDF?')) return;

    try {

      await api('/api/lms/diagnostics/' + assessmentId + '/target-pdf/' + targetPdfId, { method: 'DELETE' });

      loadAdminDiagnostics();

    } catch (err) {

      alert('Error: ' + err.message);

    }

  };



  window.submitAdminAddTargetPdfs = async function (e) {

    e.preventDefault();

    if (!state.assessmentId) {

      alert('No active diagnostic found.');

      return;

    }

    var files = selectedTargetFiles.adminDiagAddTargetFiles || [];

    var status = document.getElementById('adminDiagAddTargetStatus');

    var btn = document.getElementById('adminDiagAddTargetBtn');

    if (!files || !files.length) {

      alert('Select at least one PDF file.');

      return;

    }



    btn.disabled = true;

    status.textContent = 'Uploading ' + files.length + ' PDF(s)...';



    var fd = new FormData();

    appendTargetFiles(fd, files);



    try {

      var result = await fetch('/api/lms/diagnostics/' + state.assessmentId + '/target-pdf', {

        method: 'POST',

        credentials: 'include',

        body: fd

      });

      var body = await result.json();

      if (!result.ok) {

        throw new Error((body.error && body.error.message) || 'Upload failed');

      }

      var data = body.data || body;

      var uploaded = (data.uploaded || []).length;

      status.textContent = 'Uploaded ' + uploaded + ' target PDF(s) successfully.';

      document.getElementById('adminDiagAddTargetsForm').reset();

      selectedTargetFiles.adminDiagAddTargetFiles = [];

      updateAdminDiagAddTargetFileList();

      loadAdminDiagnostics();

    } catch (err) {

      status.textContent = 'Error: ' + err.message;

    }

    btn.disabled = false;

  };



  window.submitAdminDiagnostic = function (e) {

    e.preventDefault();

    var title = document.getElementById('adminDiagTitle').value.trim();

    var diagFile = document.getElementById('adminDiagQaFile').files[0];

    var targetFiles = selectedTargetFiles.adminDiagTargetFiles || [];

    var status = document.getElementById('adminDiagStatus');

    var btn = document.getElementById('adminDiagUploadBtn');



    if (!diagFile) { alert('Diagnostic Q&A PDF is required.'); return; }

    if (!targetFiles || !targetFiles.length) { alert('At least one target content PDF is required.'); return; }



    btn.disabled = true;

    status.textContent = '';

    setProgress(5, 'Preparing upload of ' + targetFiles.length + ' target PDF(s)...');

    var progressJobId = 'diag-' + Date.now() + '-' + Math.random().toString(36).slice(2, 10);



    var fd = new FormData();

    fd.append('title', title);

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

      try { body = JSON.parse(xhr.responseText); } catch (err) {

        status.textContent = 'Invalid server response';

        btn.disabled = false;

        resetProgress();

        return;

      }

      if (xhr.status < 200 || xhr.status >= 300) {

        status.textContent = 'Error: ' + ((body.error && body.error.message) || 'Upload failed');

        btn.disabled = false;

        resetProgress();

        return;

      }

      setProgress(94, 'Publishing diagnostic for students...');

      var d = body.data || body;

      state.assessmentId = d.assessment_id;

      state.threadId = d.thread_id;

      var targetCount = (d.target_filenames || d.target_thread_ids || []).length || targetFiles.length;

      try {

        await api('/api/lms/diagnostics/' + d.assessment_id + '/publish', { method: 'POST' });

        setProgress(100, 'Published!');

        status.textContent = 'Diagnostic published with ' + (d.question_count || '?') +

          ' questions and ' + targetCount + ' target PDF(s). AI-calculated timer is active.';

        document.getElementById('adminDiagnosticPdfForm').reset();

        selectedTargetFiles.adminDiagTargetFiles = [];

        updateAdminDiagTargetFileList();

        loadAdminDiagnostics();

      } catch (pubErr) {

        status.textContent = 'Uploaded (' + targetCount + ' target PDFs) but publish failed: ' + pubErr.message;

      }

      btn.disabled = false;

      setTimeout(resetProgress, 800);

    };

    xhr.onerror = function () {

      if (window._adminDiagProgressPoll) {

        clearInterval(window._adminDiagProgressPoll);

        window._adminDiagProgressPoll = null;

      }

      status.textContent = 'Network error during upload';

      btn.disabled = false;

      resetProgress();

    };

    xhr.send(fd);

  };

})();


