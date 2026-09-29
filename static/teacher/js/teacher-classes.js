/* Teacher → Classes (screens 02–04). Same endpoints as the former class hub + lms-teacher-class.js (both removed):
   GET /api/lms/classes/mine, /classes/grade-options, /users/me/grade-profile, PUT /teachers/me/grades,
   POST /api/lms/classes, GET /classes/:id/students, /classes/:id/eligible-students,
   POST /classes/:id/students {student_id}, DELETE /classes/:id/students/:sid. */
(function () {
  var U = window.tdUtil;
  var state = {
    classes: [],
    gradeOptions: [],
    teachingGrades: [],
    teachingGradeLabels: [],
    loaded: false,
    expandedId: null,
    tab: 'roster',
    roster: [],
    eligible: [],
    selected: {},
    loadSeq: 0
  };

  function $(id) { return document.getElementById(id); }

  async function load() {
    var list = $('tdClassList');
    if (!list) return;
    var seq = ++state.loadSeq;
    if (!state.loaded) list.innerHTML = '<div class="td-empty"><div class="td-spinner"></div><p>Loading classes…</p></div>';
    try {
      var results = await Promise.all([
        lmsApi('/api/lms/classes/mine'),
        state.gradeOptions.length ? Promise.resolve(state.gradeOptions) : lmsApi('/api/lms/classes/grade-options').catch(function () { return []; }),
        lmsApi('/api/lms/users/me/grade-profile').catch(function () { return {}; })
      ]);
      if (seq !== state.loadSeq) return;
      state.classes = results[0] || [];
      state.gradeOptions = results[1] || [];
      state.teachingGrades = (results[2] && results[2].teaching_grades) || [];
      state.teachingGradeLabels = (results[2] && results[2].teaching_grade_labels) || [];
      state.loaded = true;
      fillGradeControls();
      render();
      if (state.expandedId != null) loadDetail(state.expandedId, true);
    } catch (err) {
      list.innerHTML = '<div class="td-empty"><p class="td-error">' + U.esc(err.message || 'Failed to load classes.') + '</p></div>';
    }
  }

  function fillGradeControls() {
    // Class grade select — options outside the teacher's assigned grades are disabled (legacy patchClassCreateForm rule).
    var sel = $('tdClassGrade');
    if (sel) {
      var cur = sel.value;
      sel.innerHTML = '<option value="">Select grade</option>' + state.gradeOptions.map(function (g) {
        var allowed = !state.teachingGrades.length || state.teachingGrades.indexOf(g.value) >= 0;
        return '<option value="' + U.esc(g.value) + '"' + (allowed ? '' : ' disabled') + '>' + U.esc(g.label) + '</option>';
      }).join('');
      if (cur) sel.value = cur;
    }
    var input = $('tdTeachingGrades');
    if (input && document.activeElement !== input) input.value = state.teachingGrades.join(',');
    var hint = $('tdTeachingGradesHint');
    if (hint) {
      hint.textContent = state.teachingGrades.length
        ? 'Currently assigned: ' + (state.teachingGradeLabels.join(', ') || state.teachingGrades.join(', '))
        : 'Not set — set your teaching grades, or ask admin to assign you (e.g. 8th grade).';
    }
    var filter = $('tdClassGradeFilter');
    if (filter) {
      var fcur = filter.value;
      var grades = Array.from(new Set(state.classes.map(function (c) { return c.grade_level; }).filter(function (g) { return g != null && g !== ''; }).map(String)))
        .sort(function (a, b) { return a.localeCompare(b, undefined, { numeric: true }); });
      filter.innerHTML = '<option value="">All grades</option>' + grades.map(function (g) {
        return '<option value="' + U.esc(g) + '">' + U.esc(U.gradeLabel(g)) + '</option>';
      }).join('');
      if (grades.indexOf(fcur) >= 0) filter.value = fcur;
    }
  }

  function filteredClasses() {
    var q = (($('tdClassSearch') || {}).value || '').trim().toLowerCase();
    var g = ($('tdClassGradeFilter') || {}).value || '';
    var sort = ($('tdClassSort') || {}).value || 'latest';
    var rows = state.classes.filter(function (c) {
      if (g && String(c.grade_level) !== g) return false;
      if (!q) return true;
      return [c.name, c.join_code, c.description].some(function (v) { return v && String(v).toLowerCase().indexOf(q) >= 0; });
    });
    if (sort === 'name') rows.sort(function (a, b) { return String(a.name).localeCompare(String(b.name)); });
    else if (sort === 'grade') rows.sort(function (a, b) { return String(a.grade_level || '').localeCompare(String(b.grade_level || ''), undefined, { numeric: true }); });
    else rows.sort(function (a, b) { return b.id - a.id; });
    return rows;
  }

  var CLASS_ICONS = [['fa-book-open', '#dbeafe', '#1a56db'], ['fa-flask', '#dcfce7', '#16a34a'], ['fa-file-lines', '#ffedd5', '#ea580c'], ['fa-calculator', '#ede9fe', '#7c3aed']];

  function render() {
    var list = $('tdClassList');
    if (!list) return;
    var tab = $('tdClassTabAll');
    if (tab) tab.textContent = 'All Classes (' + state.classes.length + ')';
    if (!state.classes.length) {
      list.innerHTML = '<div class="td-empty"><img class="td-empty-ic" src="' + U.esc(window.TEACHER_CFG.icons.classes) + '" alt="">' +
        U.empty('No classes yet', 'Click Create Class above to create your first class.') + '</div>';
      return;
    }
    var rows = filteredClasses();
    if (!rows.length) {
      list.innerHTML = '<div class="td-empty">' + U.empty('No matching classes', 'Try a different search or grade.') + '</div>';
      return;
    }
    list.innerHTML = rows.map(function (c) {
      var ic = CLASS_ICONS[c.id % CLASS_ICONS.length];
      var open = state.expandedId === c.id;
      var count = c.student_count != null ? c.student_count : 0;
      return '<div class="td-class-card" data-class-id="' + c.id + '">' +
        '<div class="td-class-head" onclick="tdClasses.toggle(' + c.id + ')" role="button" aria-expanded="' + open + '">' +
          '<div class="td-avatar-sm" style="background:' + ic[1] + ';color:' + ic[2] + ';"><i class="fas ' + ic[0] + '"></i></div>' +
          '<div class="td-class-meta"><h3>' + U.esc(c.name) + '</h3>' +
            '<div class="td-pill-row">' +
              '<span class="td-pill green"><i class="fas fa-users"></i> ' + U.esc(U.gradeLabel(c.grade_level).toUpperCase()) + '</span>' +
              '<span class="td-pill">CODE: <span class="td-code">' + U.esc(c.join_code || '—') + '</span></span>' +
              '<span class="td-pill">' + count + ' STUDENT' + (count === 1 ? '' : 'S') + '</span>' +
            '</div>' +
            (c.description ? '<p class="td-desc" style="margin:8px 0 0;">' + U.esc(c.description) + '</p>' : '') +
          '</div>' +
          '<div class="td-side-meta">' +
            '<button type="button" class="td-btn td-btn-green td-btn-sm" onclick="event.stopPropagation(); tdClasses.toggle(' + c.id + ', true)">Manage</button>' +
            '<span class="td-chev" style="transform:rotate(' + (open ? 180 : 0) + 'deg);"><i class="fas fa-chevron-down"></i></span>' +
          '</div>' +
        '</div>' +
        (open ? '<div class="td-class-body" id="tdClassBody-' + c.id + '">' + detailShell() + '</div>' : '') +
      '</div>';
    }).join('');
    if (state.expandedId != null) renderDetail();
  }

  function detailShell() {
    return '<div class="td-inner-tabs">' +
      '<button type="button" class="td-stab' + (state.tab === 'roster' ? ' active' : '') + '" onclick="tdClasses.setTab(\'roster\')"><i class="fas fa-users"></i> Roster</button>' +
      '<button type="button" class="td-stab' + (state.tab === 'add' ? ' active' : '') + '" onclick="tdClasses.setTab(\'add\')"><i class="fas fa-user-plus"></i> Add Students</button>' +
      '</div><div data-slot="detail"><div class="td-empty"><div class="td-spinner"></div></div></div>';
  }

  function toggle(classId, forceOpen) {
    if (state.expandedId === classId && !forceOpen) {
      state.expandedId = null;
      render();
      return;
    }
    if (state.expandedId !== classId) {
      state.expandedId = classId;
      state.tab = 'roster';
      state.roster = [];
      state.eligible = [];
      state.selected = {};
      render();
      loadDetail(classId);
    }
  }

  async function loadDetail(classId, silent) {
    try {
      var r = await Promise.all([
        lmsApi('/api/lms/classes/' + classId + '/students'),
        lmsApi('/api/lms/classes/' + classId + '/eligible-students')
      ]);
      if (state.expandedId !== classId) return;
      state.roster = r[0] || [];
      state.eligible = r[1] || [];
      var c = state.classes.find(function (x) { return x.id === classId; });
      if (c && c.student_count !== state.roster.length) {
        c.student_count = state.roster.length;
        render();
        return;
      }
      renderDetail();
    } catch (err) {
      var slot = document.querySelector('#tdClassBody-' + classId + ' [data-slot="detail"]');
      if (slot && !silent) slot.innerHTML = '<p class="td-error">' + U.esc(err.message) + '</p>';
    }
  }

  function renderDetail() {
    var slot = document.querySelector('#tdClassBody-' + state.expandedId + ' [data-slot="detail"]');
    if (!slot) return;
    if (state.tab === 'roster') renderRoster(slot); else renderAddStudents(slot);
  }

  function renderRoster(slot) {
    var tpl = $('tdTplClassRoster');
    slot.innerHTML = '';
    slot.appendChild(tpl.content.cloneNode(true));
    var body = slot.querySelector('[data-slot="rows"]');
    if (!state.roster.length) {
      slot.querySelector('.td-table-wrap').hidden = true;
      slot.querySelector('[data-slot="empty"]').hidden = false;
      return;
    }
    body.innerHTML = state.roster.map(function (s) {
      var name = U.studentName(s);
      return '<tr><td>' + U.studentCell(name) + '</td>' +
        '<td>' + U.esc(s.grade_label || '—') + '</td>' +
        '<td>' + U.progress(s.overall_progress) + (s.is_struggling ? ' <span class="td-status-badge td-status-help" title="Struggling" style="margin-left:6px;">!</span>' : '') + '</td>' +
        '<td style="text-align:right;"><button type="button" class="td-btn td-btn-danger-outline td-btn-sm" onclick="tdClasses.removeStudent(' + s.student_id + ')">Remove</button></td></tr>';
    }).join('');
  }

  function renderAddStudents(slot) {
    var tpl = $('tdTplClassAddStudents');
    slot.innerHTML = '';
    slot.appendChild(tpl.content.cloneNode(true));
    var search = slot.querySelector('[data-slot="search"]');
    var body = slot.querySelector('[data-slot="rows"]');
    var all = slot.querySelector('[data-slot="all"]');
    var countEl = slot.querySelector('[data-slot="count"]');
    var addBtn = slot.querySelector('[data-slot="addSelected"]');

    function selectedIds() { return Object.keys(state.selected).filter(function (k) { return state.selected[k]; }).map(Number); }
    function syncCount() {
      var n = selectedIds().length;
      countEl.textContent = n + ' student' + (n === 1 ? '' : 's') + ' selected';
      addBtn.disabled = n === 0;
    }
    function paint() {
      var q = (search.value || '').trim().toLowerCase();
      var rows = state.eligible.filter(function (s) {
        return !q || [s.username, s.email].some(function (v) { return v && String(v).toLowerCase().indexOf(q) >= 0; });
      });
      if (!state.eligible.length) {
        slot.querySelector('.td-table-wrap').hidden = true;
        slot.querySelector('[data-slot="empty"]').hidden = false;
        search.parentElement.hidden = true;
        slot.querySelector('.td-select-bar').hidden = true;
        return;
      }
      body.innerHTML = rows.map(function (s) {
        var name = U.studentName(s);
        var checked = state.selected[s.student_id] ? ' checked' : '';
        return '<tr><td><input type="checkbox" data-sid="' + s.student_id + '"' + checked + ' aria-label="Select ' + U.esc(name) + '"></td>' +
          '<td>' + U.studentCell(name) + '</td><td>' + U.esc(s.email || '—') + '</td><td>' + U.esc(s.grade_label || '—') + '</td>' +
          '<td style="text-align:right;"><button type="button" class="td-btn td-btn-outline-blue td-btn-sm" onclick="tdClasses.addStudents([' + s.student_id + '])">Add</button></td></tr>';
      }).join('') || '<tr><td colspan="5" style="text-align:center;color:var(--td-muted);">No students match your search.</td></tr>';
      all.checked = rows.length > 0 && rows.every(function (s) { return state.selected[s.student_id]; });
      syncCount();
    }
    body.addEventListener('change', function (e) {
      var sid = e.target.getAttribute('data-sid');
      if (sid) { state.selected[sid] = e.target.checked; syncCount(); }
    });
    all.addEventListener('change', function () {
      body.querySelectorAll('input[data-sid]').forEach(function (cb) {
        cb.checked = all.checked;
        state.selected[cb.getAttribute('data-sid')] = all.checked;
      });
      syncCount();
    });
    search.addEventListener('input', paint);
    slot.querySelector('[data-slot="cancel"]').addEventListener('click', function () { state.selected = {}; paint(); });
    addBtn.addEventListener('click', function () { addStudents(selectedIds()); });
    paint();
  }

  function setTab(tab) {
    state.tab = tab;
    document.querySelectorAll('#tdClassBody-' + state.expandedId + ' .td-inner-tabs .td-stab').forEach(function (b, i) {
      b.classList.toggle('active', (tab === 'roster' && i === 0) || (tab === 'add' && i === 1));
    });
    renderDetail();
  }

  async function addStudents(ids) {
    if (!ids || !ids.length || state.expandedId == null) return;
    var classId = state.expandedId;
    var added = 0;
    var errors = [];
    for (var i = 0; i < ids.length; i++) {
      try {
        await lmsApi('/api/lms/classes/' + classId + '/students', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ student_id: ids[i] })
        });
        added++;
      } catch (err) {
        errors.push(err.message);
      }
    }
    state.selected = {};
    if (added) U.toast(added === 1 ? 'Student added to class' : added + ' students added to class');
    if (errors.length) U.toast(errors[0], 'error');
    await loadDetail(classId);
  }

  async function removeStudent(studentId) {
    if (state.expandedId == null) return;
    if (!confirm('Remove this student from the class?')) return;
    try {
      await lmsApi('/api/lms/classes/' + state.expandedId + '/students/' + studentId, { method: 'DELETE' });
      U.toast('Student removed');
      await loadDetail(state.expandedId);
    } catch (err) {
      U.toast(err.message, 'error');
    }
  }

  function openCreate() {
    var card = $('tdCreateClassCard');
    if (!card) return;
    card.hidden = false;
    fillGradeControls();
    setTimeout(function () {
      card.scrollIntoView({ behavior: 'smooth', block: 'start' });
      var n = $('tdClassName');
      if (n) n.focus({ preventScroll: true });
    }, 30);
  }

  function closeCreate() {
    var form = $('tdCreateClassForm');
    if (form) form.reset();
    fillGradeControls();
    var card = $('tdCreateClassCard');
    if (card) card.hidden = true;
  }

  async function submitCreate(e) {
    e.preventDefault();
    var btn = $('tdCreateClassSubmit');
    var name = ($('tdClassName').value || '').trim();
    var grade = $('tdClassGrade').value;
    var description = ($('tdClassDescription').value || '').trim();
    if (!name || !grade) return;
    if (btn) btn.disabled = true;
    try {
      var created = await lmsApi('/api/lms/classes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name, grade_level: grade || null, description: description || null })
      });
      U.toast('Class created!');
      closeCreate();
      if (created && created.id != null) state.expandedId = created.id;
      state.tab = 'roster';
      await load();
    } catch (err) {
      U.toast(err.message, 'error');
    } finally {
      if (btn) btn.disabled = false;
      if (typeof window.hideWaitOverlay === 'function') window.hideWaitOverlay();
    }
  }

  async function saveTeachingGrades() {
    var val = (($('tdTeachingGrades') || {}).value || '').trim();
    var grades = val.split(',').map(function (g) { return g.trim(); }).filter(Boolean);
    try {
      await lmsApi('/api/lms/teachers/me/grades', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ grades: grades })
      });
      U.toast('Teaching grades saved');
      var profile = await lmsApi('/api/lms/users/me/grade-profile').catch(function () { return {}; });
      state.teachingGrades = profile.teaching_grades || [];
      state.teachingGradeLabels = profile.teaching_grade_labels || [];
      fillGradeControls();
    } catch (err) {
      U.toast(err.message, 'error');
    }
  }

  window.tdClasses = {
    load: load, render: render, toggle: toggle, setTab: setTab,
    addStudents: addStudents, removeStudent: removeStudent,
    openCreate: openCreate, closeCreate: closeCreate, submitCreate: submitCreate,
    saveTeachingGrades: saveTeachingGrades,
    _state: state
  };
})();
