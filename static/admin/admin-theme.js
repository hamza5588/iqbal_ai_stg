/** Admin platform theme settings */
(function () {
  var PRESETS = {};

  function api(path, opts) {
    opts = opts || {};
    return fetch(path, Object.assign({ credentials: 'include' }, opts)).then(function (res) {
      return res.json().then(function (body) {
        if (!res.ok) throw new Error((typeof body.error === 'string' ? body.error : (body.error && body.error.message)) || body.message || 'Request failed');
        return body.theme !== undefined ? body : (body.data || body);
      });
    });
  }

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
  }

  function renderPresets(presets, activePreset) {
    var grid = document.getElementById('adminThemePresets');
    if (!grid) return;
    grid.innerHTML = Object.keys(presets).map(function (key) {
      var p = presets[key];
      var active = key === activePreset ? ' ring-2 ring-offset-2 ring-[#05B0FC]' : '';
      return '<button type="button" class="admin-theme-preset text-left p-3 rounded-lg border-2 transition-all' + active + '"' +
        ' data-preset="' + esc(key) + '" data-primary="' + esc(p.primary) + '"' +
        ' style="border-color:' + esc(p.primary) + ';background:' + esc(p.muted || p.light) + ';">' +
        '<span class="inline-block w-8 h-8 rounded-full mb-2" style="background:' + esc(p.primary) + '"></span>' +
        '<span class="block text-sm font-semibold text-gray-800">' + esc(p.name || key) + '</span>' +
        '<span class="block text-xs text-gray-500">' + esc(p.primary) + '</span></button>';
    }).join('');

    grid.querySelectorAll('.admin-theme-preset').forEach(function (btn) {
      btn.addEventListener('click', function () {
        document.getElementById('adminThemePresetInput').value = btn.getAttribute('data-preset');
        document.getElementById('adminThemeCustomColor').value = btn.getAttribute('data-primary');
        grid.querySelectorAll('.admin-theme-preset').forEach(function (b) {
          b.classList.remove('ring-2', 'ring-offset-2', 'ring-[#05B0FC]');
        });
        btn.classList.add('ring-2', 'ring-offset-2', 'ring-[#05B0FC]');
        previewTheme(btn.getAttribute('data-primary'));
      });
    });
  }

  function previewTheme(primary) {
    document.documentElement.style.setProperty('--primary-color', primary);
    document.documentElement.style.setProperty('--iqbal-primary', primary);
    document.documentElement.style.setProperty('--lms-green', primary);
  }

  window.loadAdminThemeSettings = async function () {
    var status = document.getElementById('adminThemeStatus');
    try {
      var data = await api('/admin/settings/theme');
      PRESETS = data.presets || {};
      var theme = data.theme || {};
      renderPresets(PRESETS, theme.preset);
      document.getElementById('adminThemePresetInput').value = theme.preset || 'green';
      document.getElementById('adminThemeCustomColor').value = theme.primary || '#166534';
      if (status) status.textContent = 'Active theme: ' + (theme.name || theme.preset);
    } catch (err) {
      if (status) status.textContent = 'Error loading theme: ' + err.message;
    }
  };

  window.saveAdminTheme = async function () {
    var status = document.getElementById('adminThemeStatus');
    var preset = document.getElementById('adminThemePresetInput').value || 'green';
    var customColor = document.getElementById('adminThemeCustomColor').value;
    var useCustom = document.getElementById('adminThemeUseCustom').checked;
    try {
      var payload = useCustom
        ? { preset: 'custom', primary: customColor }
        : { preset: preset };
      var result = await api('/admin/settings/theme', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (status) status.textContent = 'Theme saved: ' + (result.theme && result.theme.name ? result.theme.name : preset);
      showNotification('Platform theme updated for all users', 'success');
      loadAdminThemeSettings();
    } catch (err) {
      if (status) status.textContent = 'Save failed: ' + err.message;
      showNotification('Failed to save theme: ' + err.message, 'error');
    }
  };

  document.addEventListener('DOMContentLoaded', function () {
    var customToggle = document.getElementById('adminThemeUseCustom');
    var customInput = document.getElementById('adminThemeCustomColor');
    if (customToggle && customInput) {
      customToggle.addEventListener('change', function () {
        customInput.disabled = !customToggle.checked;
      });
      customInput.addEventListener('input', function () {
        if (customToggle.checked) previewTheme(customInput.value);
      });
    }
  });
})();
