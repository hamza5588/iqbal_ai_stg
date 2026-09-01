/** Teacher class management — grade-based enrollment */
(function () {
  var gradeOptions = [];
  var teacherGrades = [];
  var selectedClassId = null;

  window.lmsEnhanceClassHub = async function () {
    try {
      gradeOptions = await lmsApi('/api/lms/classes/grade-options');
      var profile = await lmsApi('/api/lms/users/me/grade-profile');
      teacherGrades = profile.teaching_grades || [];
      renderTeacherGradeSetup(profile);
    } catch (e) { /* best effort */ }
    patchClassCreateForm();
  };

  function renderTeacherGradeSetup(profile) {
    var form = document.getElementById('lmsCreateClassForm');
    if (!form || document.getElementById('lmsTeacherGradeSetup')) return;
    var div = document.createElement('div');
    div.id = 'lmsTeacherGradeSetup';
    div.className = 'lms-card';
    div.style.marginBottom = '16px';
    div.style.background = '#fff';
    var labels = (profile.teaching_grade_labels || []).join(', ') || 'Not set';
    div.innerHTML = '<h3 style="font-weight:700;margin:0 0 8px;color:#166534;">Your Teaching Grades</h3>' +
      '<p class="lms-status">Currently assigned: <strong>' + escapeHtml(labels) + '</strong></p>' +
      '<div class="lms-field"><label class="lms-label">Update grades you teach (comma-separated, e.g. 8,9)</label>' +
      '<input id="lmsTeacherGradesInput" class="lms-input" placeholder="8" value="' + escapeHtml((profile.teaching_grades || []).join(',')) + '"></div>' +
      '<button type="button" class="lms-btn lms-btn-secondary" onclick="lmsSaveTeacherGrades()">Save Teaching Grades</button>';
    form.parentNode.insertBefore(div, form);
  }

  window.lmsSaveTeacherGrades = async function () {
    var val = (document.getElementById('lmsTeacherGradesInput').value || '').trim();
    var grades = val.split(',').map(function (g) { return g.trim(); }).filter(Boolean);
    try {
      await lmsApi('/api/lms/teachers/me/grades', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ grades: grades })
      });
      lmsShowToast('Teaching grades saved');
      lmsEnhanceClassHub();
      patchClassCreateForm();
    } catch (err) {
      lmsShowToast(err.message, 'error');
    }
  };
    if (typeof loadLmsTeacherClasses === 'function') {
      var orig = loadLmsTeacherClasses;
      window.loadLmsTeacherClasses = async function () {
        await orig.apply(this, arguments);
        enhanceClassListCards();
      };
    }
  };

  function patchClassCreateForm() {
    var gradeInput = document.getElementById('lmsClassGrade');
    if (!gradeInput || gradeInput.tagName === 'SELECT') return;
    var select = document.createElement('select');
    select.id = 'lmsClassGrade';
    select.className = 'lms-select';
    select.required = true;
    select.innerHTML = '<option value="">Select grade level *</option>' +
      gradeOptions.map(function (g) {
        var allowed = !teacherGrades.length || teacherGrades.indexOf(g.value) >= 0;
        return '<option value="' + g.value + '"' + (allowed ? '' : ' disabled') + '>' + escapeHtml(g.label) + '</option>';
      }).join('');
    gradeInput.parentNode.replaceChild(select, gradeInput);

    if (!teacherGrades.length) {
      var hint = document.createElement('p');
      hint.className = 'lms-status';
      hint.id = 'lmsTeacherGradeHint';
      hint.innerHTML = 'Set your teaching grades in profile, or ask admin to assign you (e.g. 8th grade).';
      select.parentNode.insertBefore(hint, select.nextSibling);
    } else {
      var hint = document.createElement('p');
      hint.className = 'lms-status';
      hint.textContent = 'You are assigned to teach: ' + (teacherGrades.join(', ') + (teacherGrades.length === 1 ? 'th' : '') + ' grade');
      select.parentNode.insertBefore(hint, select.nextSibling);
    }
  }

  function enhanceClassListCards() {
    document.querySelectorAll('[data-lms-class-id]').forEach(function (card) {
      /* already enhanced */
    });
  }

  window.lmsOpenClassDetail = async function (classId, className, joinCode, gradeLevel) {
    selectedClassId = classId;
    ensureClassDetailModal();
    document.getElementById('lmsClassDetailTitle').textContent = className;
    lmsOpenModal('lmsClassDetailModal');
    var body = document.getElementById('lmsClassDetailBody');
    body.innerHTML = '<div class="lms-spinner"></div>';
    try {
      var roster = await lmsApi('/api/lms/classes/' + classId + '/students');
      var eligible = await lmsApi('/api/lms/classes/' + classId + '/eligible-students');
      var gradeLabel = gradeLevel ? (gradeLevel + 'th Grade') : 'All grades';
      body.innerHTML =
        '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px;">' +
        '<span class="lms-badge lms-badge-green">' + escapeHtml(gradeLabel) + '</span>' +
        '<span class="lms-badge lms-badge-blue">Code: ' + escapeHtml(joinCode) + '</span>' +
        '<span class="lms-badge lms-badge-blue">' + roster.length + ' students</span></div>' +
        '<div class="lms-tabs">' +
        '<button type="button" class="lms-tab active" onclick="lmsClassDetailTab(\'roster\')">Roster</button>' +
        '<button type="button" class="lms-tab" onclick="lmsClassDetailTab(\'add\')">Add Students</button></div>' +
        '<div id="lmsClassDetailTabContent">' + renderRosterTable(roster) + '</div>';
      window._lmsClassDetailRoster = roster;
      window._lmsClassDetailEligible = eligible;
    } catch (err) {
      body.innerHTML = '<p class="lms-error">' + escapeHtml(err.message) + '</p>';
    }
  };

  function renderRosterTable(roster) {
    if (!roster.length) return '<p class="lms-status">No students yet. Share join code or add students below.</p>';
    return '<table class="lms-table"><thead><tr><th>Student</th><th>Grade</th><th>Progress</th><th></th></tr></thead><tbody>' +
      roster.map(function (s) {
        return '<tr><td>' + escapeHtml(s.username || s.email || '#' + s.student_id) + '</td>' +
          '<td>' + escapeHtml(s.grade_label || '—') + '</td>' +
          '<td>' + (s.overall_progress != null ? Math.round(s.overall_progress) + '%' : '—') +
          (s.is_struggling ? ' <span class="lms-badge lms-badge-red">!</span>' : '') + '</td>' +
          '<td><button type="button" class="lms-btn lms-btn-danger" style="padding:4px 10px;font-size:.75rem;" onclick="lmsRemoveStudent(' + s.student_id + ')">Remove</button></td></tr>';
      }).join('') + '</tbody></table>';
  }

  window.lmsClassDetailTab = function (tab) {
    var content = document.getElementById('lmsClassDetailTabContent');
    document.querySelectorAll('#lmsClassDetailModal .lms-tab').forEach(function (t, i) {
      t.classList.toggle('active', (tab === 'roster' && i === 0) || (tab === 'add' && i === 1));
    });
    if (tab === 'roster') {
      content.innerHTML = renderRosterTable(window._lmsClassDetailRoster || []);
    } else {
      var eligible = window._lmsClassDetailEligible || [];
      content.innerHTML = eligible.length
        ? '<p class="lms-status">Students matching this class grade who are not enrolled:</p>' +
          eligible.map(function (s) {
            return '<div class="lms-card lms-card-head"><div><strong>' + escapeHtml(s.username) + '</strong><br>' +
              '<span class="lms-status">' + escapeHtml(s.email) + ' · ' + escapeHtml(s.grade_label) + '</span></div>' +
              '<button type="button" class="lms-btn lms-btn-primary" onclick="lmsAddStudent(' + s.student_id + ')">Add</button></div>';
          }).join('')
        : '<p class="lms-status">No eligible students found for this grade.</p>';
    }
  };

  window.lmsAddStudent = async function (studentId) {
    try {
      await lmsApi('/api/lms/classes/' + selectedClassId + '/students', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ student_id: studentId })
      });
      lmsShowToast('Student added to class');
      var card = document.querySelector('[data-lms-class-id="' + selectedClassId + '"]');
      if (card) {
        var name = card.getAttribute('data-class-name');
        var code = card.getAttribute('data-join-code');
        var grade = card.getAttribute('data-grade');
        lmsOpenClassDetail(selectedClassId, name, code, grade);
      }
      if (typeof loadLmsTeacherClasses === 'function') loadLmsTeacherClasses();
    } catch (err) {
      lmsShowToast(err.message, 'error');
    }
  };

  window.lmsRemoveStudent = async function (studentId) {
    if (!confirm('Remove this student from the class?')) return;
    try {
      await lmsApi('/api/lms/classes/' + selectedClassId + '/students/' + studentId, { method: 'DELETE' });
      lmsShowToast('Student removed');
      lmsOpenClassDetail(selectedClassId,
        document.getElementById('lmsClassDetailTitle').textContent,
        '', '');
      if (typeof loadLmsTeacherClasses === 'function') loadLmsTeacherClasses();
    } catch (err) {
      lmsShowToast(err.message, 'error');
    }
  };

  function ensureClassDetailModal() {
    if (document.getElementById('lmsClassDetailModal')) return;
    var html = '<div id="lmsClassDetailModal" class="lms-modal-backdrop" onclick="if(event.target===this)lmsCloseModal(\'lmsClassDetailModal\')">' +
      '<div class="lms-modal lms-modal-lg"><div class="lms-modal-header">' +
      '<h2 id="lmsClassDetailTitle">Class</h2>' +
      '<button type="button" class="lms-modal-close" onclick="lmsCloseModal(\'lmsClassDetailModal\')">&times;</button></div>' +
      '<div class="lms-modal-body" id="lmsClassDetailBody"></div></div></div>';
    document.body.insertAdjacentHTML('beforeend', html);
  }

  /* Override loadLmsTeacherClasses rendering */
  document.addEventListener('DOMContentLoaded', function () {
    if (!document.getElementById('lmsClassList')) return;
    window.loadLmsTeacherClasses = async function () {
      var el = document.getElementById('lmsClassList');
      try {
        var classes = await lmsApi('/api/lms/classes/mine');
        if (!classes.length) {
          el.innerHTML = '<p class="lms-status">No classes yet. Create one above.</p>';
          return;
        }
        el.innerHTML = await Promise.all(classes.map(async function (c) {
          var roster = [];
          try { roster = await lmsApi('/api/lms/classes/' + c.id + '/students'); } catch (e) { roster = []; }
          return '<div class="lms-card" data-lms-class-id="' + c.id + '" data-class-name="' + escapeHtml(c.name) + '" data-join-code="' + escapeHtml(c.join_code) + '" data-grade="' + escapeHtml(c.grade_level || '') + '">' +
            '<div class="lms-card-head">' +
            '<div><span class="lms-card-title">' + escapeHtml(c.name) + '</span> ' +
            (c.grade_level ? '<span class="lms-badge lms-badge-green">' + c.grade_level + 'th Grade</span>' : '') +
            '<div class="lms-status" style="margin-top:6px;">Join code: <code style="background:#f0fdf4;padding:2px 8px;border-radius:4px;font-weight:700;color:#166534;">' + escapeHtml(c.join_code) + '</code></div></div>' +
            '<button type="button" class="lms-btn lms-btn-primary" onclick="lmsOpenClassDetail(' + c.id + ',\'' + escapeHtml(c.name).replace(/'/g, "\\'") + '\',\'' + escapeHtml(c.join_code) + '\',\'' + escapeHtml(c.grade_level || '') + '\')">Manage</button></div>' +
            '<p class="lms-status" style="margin-top:8px;">' + roster.length + ' student(s) enrolled</p></div>';
        })).then(function (html) { return html.join(''); });
      } catch (err) {
        el.innerHTML = '<p class="lms-error">Failed to load classes.</p>';
      }
    };
    lmsEnhanceClassHub();
  });
})();
