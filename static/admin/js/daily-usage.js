(function () {
  'use strict';
  let sequence = 0, schoolsLoaded = false;
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
    return new URLSearchParams({ start: start.value, end: end.value,
      school: document.getElementById('usage-school').value, search: document.getElementById('usage-search').value.trim() });
  }
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
      if (!schoolsLoaded) {
        const select = document.getElementById('usage-school');
        data.schools.forEach(school => { const option = document.createElement('option'); option.value = option.textContent = school; select.appendChild(option); });
        schoolsLoaded = true;
      }
      document.getElementById('usage-summary').textContent = data.active_students + ' active students · ' + duration(data.total_active_seconds) + ' total tutor time · ' + data.student_count + ' students';
      document.getElementById('usage-coverage').textContent = data.tracking_started_at
        ? 'Tracking started ' + new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Karachi', dateStyle: 'medium', timeStyle: 'short' }).format(new Date(data.tracking_started_at)) + ' PKT. Earlier dates are not tracked; the first day is partial.'
        : 'Tracking has not started yet.';
      document.getElementById('usage-tbody').innerHTML = data.rows.length ? data.rows.map(row =>
        '<tr><td>' + adEsc(row.date) + '</td><td>' + adEsc(row.student_name) + '</td><td>' + adEsc(row.school_name) + '</td><td>' + duration(row.active_seconds) + '</td><td>' + adEsc(row.coverage) + '</td></tr>'
      ).join('') : adEmptyRow(5, 'No students found', 'Try another school or date range.');
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
