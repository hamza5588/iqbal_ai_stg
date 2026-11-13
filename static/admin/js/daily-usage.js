(function () {
  'use strict';
  let sequence = 0;
  function today() {
    const parts = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Karachi', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
    const value = type => parts.find(p => p.type === type).value;
    return value('year') + '-' + value('month') + '-' + value('day');
  }
  function params() {
    const start = document.getElementById('usage-start'), end = document.getElementById('usage-end');
    if (!start.value) start.value = today();
    if (!end.value) end.value = start.value;
    start.max = end.max = today();
    const query = new URLSearchParams({ start: start.value, end: end.value, search: document.getElementById('usage-search').value.trim() });
    selectedSchools().forEach(school => query.append('school', school));
    return query;
  }
  function schoolBoxes() { return Array.from(document.querySelectorAll('#usage-school-options input')); }
  function selectedSchools() { return schoolBoxes().filter(box => box.checked).map(box => box.value); }
  function updateSchoolLabel() {
    const chosen = selectedSchools();
    document.getElementById('usage-school-all').checked = !chosen.length;
    document.getElementById('usage-school-label').textContent = !chosen.length ? 'All schools'
      : chosen.length === 1 ? chosen[0] : chosen.length + ' schools selected';
    document.getElementById('usage-school-toggle').title = chosen.join(', ');
  }
  function setSchoolMenu(open) {
    document.getElementById('usage-school-menu').hidden = !open;
    document.getElementById('usage-school-toggle').setAttribute('aria-expanded', String(open));
  }
  // Re-synced on every load so schools added after the page opened appear; ticks are kept.
  function syncSchoolOptions(schools) {
    const list = document.getElementById('usage-school-options'), chosen = new Set(selectedSchools());
    const names = Array.from(new Set(schools.concat(Array.from(chosen)))).sort((a, b) => a.localeCompare(b));
    if (names.join('|') === schoolBoxes().map(box => box.value).join('|')) return;
    list.replaceChildren();
    names.forEach(school => {
      const label = document.createElement('label'), box = document.createElement('input');
      label.className = 'usage-multi-opt';
      box.type = 'checkbox'; box.value = school; box.checked = chosen.has(school);
      label.append(box, document.createTextNode(school));
      list.appendChild(label);
    });
  }
  document.getElementById('usage-school-toggle').addEventListener('click', () =>
    setSchoolMenu(document.getElementById('usage-school-menu').hidden));
  document.getElementById('usage-school-menu').addEventListener('change', event => {
    if (event.target.id === 'usage-school-all') schoolBoxes().forEach(box => { box.checked = false; });
    updateSchoolLabel();
  });
  document.addEventListener('click', event => {
    if (!document.getElementById('usage-school').contains(event.target)) setSchoolMenu(false);
  });
  document.getElementById('usage-school').addEventListener('keydown', event => {
    if (event.key === 'Escape' && !document.getElementById('usage-school-menu').hidden) {
      setSchoolMenu(false); document.getElementById('usage-school-toggle').focus();
    }
  });
  function duration(seconds) {
    if (seconds === null) return 'Not tracked';
    return [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60].map(n => String(n).padStart(2, '0')).join(':');
  }
  window.loadDailyUsage = async function loadDailyUsage(page = 1) {
    const requestSequence = ++sequence, query = params();
    query.set('page', page);
    document.getElementById('usage-error').textContent = '';
    document.getElementById('usage-summary').textContent = 'Loading usage…';
    document.getElementById('usage-tbody').innerHTML = '<tr><td colspan="5">Loading…</td></tr>';
    document.getElementById('usage-pagination').innerHTML = '';
    try {
      const response = await fetch('/admin/daily-usage?' + query, { cache: 'no-store' });
      const data = await response.json();
      if (requestSequence !== sequence) return;
      if (!response.ok || !data.success) throw new Error(data.error || 'Unable to load daily usage.');
      syncSchoolOptions(data.schools);
      updateSchoolLabel();
      document.getElementById('usage-summary').textContent = data.active_students + ' active students · ' + duration(data.total_active_seconds) + ' total tutor time · ' + data.student_count + ' students';
      document.getElementById('usage-coverage').textContent = data.tracking_started_at
        ? 'Tracking started ' + new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Karachi', dateStyle: 'medium', timeStyle: 'short' }).format(new Date(data.tracking_started_at)) + ' PKT. Earlier dates are not tracked; the first day is partial.'
        : 'Tracking has not started yet.';
      document.getElementById('usage-tbody').innerHTML = data.rows.length ? data.rows.map(row =>
        '<tr><td>' + adEsc(row.date) + '</td><td>' + adEsc(row.student_name) + '</td><td>' + adEsc(row.school_name) + '</td><td>' + duration(row.active_seconds) + '</td><td>' + adEsc(row.coverage) + '</td></tr>'
      ).join('') : adEmptyRow(5, 'No students found', 'Try other schools or another date range.');
      renderPagination('usage-pagination', data, window.loadDailyUsage);
    } catch (error) {
      if (requestSequence !== sequence) return;
      document.getElementById('usage-error').textContent = error.message;
      document.getElementById('usage-summary').textContent = 'Usage could not be loaded.';
      document.getElementById('usage-tbody').innerHTML = '<tr><td colspan="5">Apply filters to try again.</td></tr>';
    }
  };
  window.downloadDailyUsage = async function () {
    const button = document.getElementById('usage-download');
    button.disabled = true;
    document.getElementById('usage-error').textContent = '';
    try {
      const query = params();
      const response = await fetch('/admin/daily-usage/export.xlsx?' + query, { cache: 'no-store' });
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || 'Unable to download report.');
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url; link.download = 'student-daily-usage-' + query.get('start') + '-to-' + query.get('end') + '.xlsx';
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch (error) { document.getElementById('usage-error').textContent = error.message; }
    finally { button.disabled = false; }
  };
})();
