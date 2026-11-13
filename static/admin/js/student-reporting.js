(function () {
  'use strict';
  const state = { groupRows: [], importRows: [], options: null, groupsSequence: 0 };
  const esc = value => String(value == null ? '' : value).replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
  const jsonHeaders = { 'Content-Type': 'application/json', 'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest' };

  function pktToday() {
    const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Karachi', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
    const get = type => parts.find(part => part.type === type).value;
    return get('year') + '-' + get('month') + '-' + get('day');
  }
  async function readJson(response) {
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.success) throw new Error(data.error || 'The request could not be completed.');
    return data;
  }
  function selectedGroupIds() {
    return Array.from(document.querySelectorAll('.sg-row-check:checked')).map(box => Number(box.value));
  }
  function updateSelectedCount() {
    const count = selectedGroupIds().length;
    document.getElementById('sg-selected-count').textContent = count + ' selected';
    document.getElementById('sg-check-all').checked = !!state.groupRows.length && count === state.groupRows.length;
  }
  function groupQuery(page) {
    return new URLSearchParams({
      cohort: document.getElementById('sg-cohort').value.trim(),
      search: document.getElementById('sg-search').value.trim(),
      school: document.getElementById('sg-school').value,
      group: document.getElementById('sg-group').value,
      page: page || 1,
      per_page: 50
    });
  }
  function syncSchools(select, schools, emptyLabel) {
    const chosen = select.value;
    select.innerHTML = '<option value="">' + esc(emptyLabel) + '</option>' + schools.map(value => '<option value="' + esc(value) + '">' + esc(value) + '</option>').join('');
    if (schools.includes(chosen)) select.value = chosen;
  }
  window.loadStudentGroups = async function loadStudentGroups(page = 1) {
    const sequence = ++state.groupsSequence;
    const error = document.getElementById('sg-error');
    error.textContent = '';
    document.getElementById('sg-tbody').innerHTML = '<tr><td colspan="7">Loading students...</td></tr>';
    try {
      const data = await readJson(await fetch('/admin/student-groups?' + groupQuery(page), { cache: 'no-store', headers: { Accept: 'application/json' } }));
      if (sequence !== state.groupsSequence) return;
      state.groupRows = data.students;
      syncSchools(document.getElementById('sg-school'), data.schools, 'All schools');
      document.getElementById('sg-counts').innerHTML = ['Controlled', 'Uncontrolled', 'Unassigned'].map(label => '<div class="ad-card sr-stat"><span>' + esc(label) + '</span><strong>' + data.counts[label] + '</strong></div>').join('');
      document.getElementById('sg-tbody').innerHTML = data.students.length ? data.students.map(student =>
        '<tr><td><input class="sg-row-check" type="checkbox" value="' + student.id + '" aria-label="Select ' + esc(student.name) + '"></td>' +
        '<td>' + esc(student.name) + '</td><td>' + esc(student.email) + '</td><td>' + esc(student.school || '—') + '</td>' +
        '<td><span class="sr-badge sr-' + esc((student.group || 'unassigned').toLowerCase()) + '">' + esc(student.group || 'Unassigned') + '</span></td>' +
        '<td>' + (student.effective_from ? esc(new Date(student.effective_from + 'Z').toLocaleString()) : '—') + '</td>' +
        '<td><button type="button" class="ad-btn ad-btn-outline sr-small" onclick="studentGroupsHistory(' + student.id + ')">View</button></td></tr>'
      ).join('') : '<tr><td colspan="7">No students match these filters.</td></tr>';
      document.querySelectorAll('.sg-row-check').forEach(box => box.addEventListener('change', updateSelectedCount));
      updateSelectedCount();
      if (typeof renderPagination === 'function') renderPagination('sg-pagination', data, window.loadStudentGroups);
    } catch (err) {
      error.textContent = err.message;
      document.getElementById('sg-tbody').innerHTML = '<tr><td colspan="7">Students could not be loaded.</td></tr>';
    }
  };
  document.getElementById('sg-check-all').addEventListener('change', event => {
    document.querySelectorAll('.sg-row-check').forEach(box => { box.checked = event.target.checked; });
    updateSelectedCount();
  });
  window.studentGroupsAssign = async function studentGroupsAssign(group) {
    const ids = selectedGroupIds();
    const label = group || 'Unassigned';
    if (!ids.length) { document.getElementById('sg-error').textContent = 'Select at least one student.'; return; }
    if (!window.confirm('Change ' + ids.length + ' selected student(s) to ' + label + '? Assignment history will be preserved.')) return;
    try {
      const data = await readJson(await fetch('/admin/student-groups/assign', { method: 'POST', headers: jsonHeaders, body: JSON.stringify({ student_ids: ids, group: group, cohort: document.getElementById('sg-cohort').value }) }));
      document.getElementById('sg-error').textContent = data.changed + ' assignment(s) changed; ' + data.unchanged + ' already matched.';
      await window.loadStudentGroups(1);
    } catch (err) { document.getElementById('sg-error').textContent = err.message; }
  };
  window.studentGroupsHistory = async function studentGroupsHistory(studentId) {
    try {
      const query = new URLSearchParams({ cohort: document.getElementById('sg-cohort').value });
      const data = await readJson(await fetch('/admin/student-groups/' + studentId + '/history?' + query, { cache: 'no-store', headers: { Accept: 'application/json' } }));
      document.getElementById('sg-history-title').textContent = 'Assignment history — ' + data.student.name;
      document.getElementById('sg-history-tbody').innerHTML = data.history.length ? data.history.map(row => '<tr><td>' + esc(new Date(row.changed_at + 'Z').toLocaleString()) + '</td><td>' + esc(row.old_group || 'Unassigned') + '</td><td>' + esc(row.new_group || 'Unassigned') + '</td><td>' + esc(row.source) + '</td><td>' + esc(row.changed_by) + '</td></tr>').join('') : '<tr><td colspan="5">No assignment changes recorded.</td></tr>';
      document.getElementById('sg-history-panel').hidden = false;
    } catch (err) { document.getElementById('sg-error').textContent = err.message; }
  };
  window.studentGroupsToggleImport = function studentGroupsToggleImport(force) {
    const panel = document.getElementById('sg-import-panel');
    panel.hidden = force === false ? true : !panel.hidden;
  };
  window.studentGroupsPreviewImport = async function studentGroupsPreviewImport() {
    const file = document.getElementById('sg-import-file').files[0];
    if (!file) { document.getElementById('sg-import-summary').textContent = 'Choose a CSV or XLSX file.'; return; }
    const body = new FormData(); body.append('file', file);
    try {
      const data = await readJson(await fetch('/admin/student-groups/import/preview', { method: 'POST', body: body, headers: { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' } }));
      state.importRows = data.rows;
      document.getElementById('sg-import-summary').textContent = data.ready + ' ready · ' + data.errors + ' need attention · ' + data.total + ' total';
      document.getElementById('sg-import-apply').disabled = !data.ready;
      document.getElementById('sg-import-tbody').innerHTML = data.rows.length ? data.rows.map(row => '<tr><td>' + row.row + '</td><td>' + esc(row.name || 'Unmatched') + '</td><td>' + esc(row.email) + '</td><td>' + esc(row.group || '—') + '</td><td class="' + (row.status === 'ready' ? 'sr-ok' : 'sr-error') + '">' + esc(row.status === 'ready' ? 'Ready' : row.errors.join('; ')) + '</td></tr>').join('') : '<tr><td colspan="5">The file contains no data rows.</td></tr>';
    } catch (err) { document.getElementById('sg-import-summary').textContent = err.message; }
  };
  window.studentGroupsApplyImport = async function studentGroupsApplyImport() {
    const ready = state.importRows.filter(row => row.status === 'ready');
    if (!ready.length || !window.confirm('Apply ' + ready.length + ' reviewed group assignment(s)?')) return;
    try {
      const data = await readJson(await fetch('/admin/student-groups/import/apply', { method: 'POST', headers: jsonHeaders, body: JSON.stringify({ cohort: document.getElementById('sg-cohort').value, effective_date: document.getElementById('sg-effective-date').value, rows: ready }) }));
      document.getElementById('sg-import-summary').textContent = data.changed + ' changed; ' + data.unchanged + ' already matched.';
      document.getElementById('sg-import-apply').disabled = true;
      await window.loadStudentGroups(1);
    } catch (err) { document.getElementById('sg-import-summary').textContent = err.message; }
  };

  function reportPayload() {
    return {
      cohort: document.getElementById('sr-cohort').value.trim(), group: document.getElementById('sr-group').value,
      start: document.getElementById('sr-start').value, end: document.getElementById('sr-end').value,
      test_type: 'all', assessment_id: document.getElementById('sr-assessment').value,
      school: document.getElementById('sr-school').value,
      student_ids: Array.from(document.getElementById('sr-students').selectedOptions).map(option => Number(option.value))
    };
  }
  function renderClassifications(assessments) {
    document.getElementById('sr-classification-tbody').innerHTML = assessments.length ? assessments.map(item => {
      const id = item.id;
      return '<tr><td>' + esc(item.title) + ' <span class="ad-help">#' + id + '</span></td><td><select id="sr-class-' + id + '"><option value="">Not included</option><option value="Baseline"' + (item.test_type === 'Baseline' ? ' selected' : '') + '>Baseline</option><option value="FollowUp"' + (item.test_type === 'FollowUp' ? ' selected' : '') + '>FollowUp</option></select></td><td><button class="ad-btn ad-btn-outline sr-small" type="button" onclick="studentReportsClassify(' + id + ')">Save</button></td></tr>';
    }).join('') : '<tr><td colspan="3">No assessments exist.</td></tr>';
  }
  window.loadStudentReports = async function loadStudentReports() {
    const error = document.getElementById('sr-error'); error.textContent = '';
    if (!document.getElementById('sr-start').value) document.getElementById('sr-start').value = pktToday();
    if (!document.getElementById('sr-end').value) document.getElementById('sr-end').value = document.getElementById('sr-start').value;
    try {
      const query = new URLSearchParams({ cohort: document.getElementById('sr-cohort').value.trim() });
      const data = await readJson(await fetch('/admin/student-reporting/options?' + query, { cache: 'no-store', headers: { Accept: 'application/json' } }));
      state.options = data;
      const assessment = document.getElementById('sr-assessment'), chosen = assessment.value;
      const classified = data.assessments.filter(item => item.test_type);
      const grouped = new Map();
      classified.forEach(item => {
        const key = item.title + '\u0000' + item.test_type;
        if (!grouped.has(key)) grouped.set(key, []);
        grouped.get(key).push(item);
      });
      const reportOptions = Array.from(grouped.values()).map(items => ({
        value: items.length > 1 ? 'title:' + items[0].title : String(items[0].id),
        label: items[0].title + ' — ' + items[0].test_type + (items.length > 1 ? ' · ' + items.length + ' school tests' : ' · #' + items[0].id)
      }));
      assessment.innerHTML = '<option value="all">All tests created on selected date</option>' + reportOptions.map(item => '<option value="' + esc(item.value) + '">' + esc(item.label) + '</option>').join('');
      if (reportOptions.some(item => item.value === chosen)) assessment.value = chosen;
      syncSchools(document.getElementById('sr-school'), data.schools, 'All schools');
      document.getElementById('sr-students').innerHTML = data.students.map(item => '<option value="' + item.id + '">' + esc(item.name + ' · ' + item.group + (item.school ? ' · ' + item.school : '')) + '</option>').join('');
      renderClassifications(data.assessments);
    } catch (err) { error.textContent = err.message; }
  };
  document.getElementById('sr-start').addEventListener('change', event => {
    document.getElementById('sr-end').value = event.target.value;
  });
  window.studentReportsClassify = async function studentReportsClassify(assessmentId) {
    const testType = document.getElementById('sr-class-' + assessmentId).value;
    try {
      await readJson(await fetch('/admin/student-reporting/assessments/' + assessmentId + '/classification', { method: 'PUT', headers: jsonHeaders, body: JSON.stringify({ cohort: 'default', test_type: testType, followup_order: null }) }));
      document.getElementById('sr-error').textContent = 'Assessment classification saved.';
      await window.loadStudentReports();
    } catch (err) { document.getElementById('sr-error').textContent = err.message; }
  };
  window.studentReportsPreview = async function studentReportsPreview() {
    const target = document.getElementById('sr-preview'), error = document.getElementById('sr-error');
    error.textContent = ''; target.innerHTML = '<p>Building preview...</p>';
    try {
      const data = await readJson(await fetch('/admin/student-reporting/preview', { method: 'POST', headers: jsonHeaders, body: JSON.stringify(reportPayload()) }));
      const score = data.average_score == null ? 'Unavailable' : (data.average_score * 100).toFixed(2) + '%';
      const filters = data.filters;
      target.innerHTML = '<p><strong>Selection:</strong> ' + esc(filters.group.replace('_', ' ')) + ' · ' + esc(filters.start) + (filters.end !== filters.start ? ' to ' + esc(filters.end) : '') + '</p>' +
        '<p class="ad-help"><strong>Date rule:</strong> ' + esc(data.attempt_rule) + '</p>' +
        '<div class="sr-stat-grid"><div class="sr-stat"><span>Students</span><strong>' + data.matching_students + '</strong></div><div class="sr-stat"><span>Attempts</span><strong>' + data.attempts + '</strong></div><div class="sr-stat"><span>Average score</span><strong>' + score + '</strong></div></div>' +
        (data.files.length ? '<h4>Workbook contents</h4><p>All rows below will be merged into one Excel workbook.</p><ul>' + data.files.map(item => '<li>' + esc(item.group + ' · ' + item.test_type + ' · ' + item.test_date) + ' — ' + item.rows + ' result row(s)</li>').join('') + '</ul>' : '<p class="sr-empty">No matching submitted attempts. No workbook will be generated.</p>') +
        (data.warnings.length ? '<div class="sr-warning"><strong>Missing data warnings</strong><ul>' + data.warnings.map(item => '<li>' + esc(item) + '</li>').join('') + '</ul></div>' : '<p class="sr-ok">All requested report data is available.</p>');
    } catch (err) { error.textContent = err.message; target.innerHTML = '<p>Preview could not be generated.</p>'; }
  };
  window.studentReportsDownload = async function studentReportsDownload() {
    const button = document.getElementById('sr-download'), error = document.getElementById('sr-error');
    button.disabled = true; error.textContent = '';
    try {
      const response = await fetch('/admin/student-reporting/export', { method: 'POST', headers: jsonHeaders, body: JSON.stringify(reportPayload()) });
      if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.error || 'Report could not be generated.'); }
      const disposition = response.headers.get('Content-Disposition') || '';
      const match = disposition.match(/filename\*?=(?:UTF-8''|\")?([^";]+)/i);
      const filename = match ? decodeURIComponent(match[1].replace(/"/g, '')) : 'Student_Performance_Report.xlsx';
      const url = URL.createObjectURL(await response.blob()), link = document.createElement('a');
      link.href = url; link.download = filename; document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch (err) { error.textContent = err.message; }
    finally { button.disabled = false; }
  };
})();
