/* Admin shell: avatar menu, mobile navigation drawer, active-link state (shared by all admin pages). */
(function () {
  function menu() { return document.getElementById('adAvatarMenu'); }

  window.adCloseAvatarMenu = function () {
    var m = menu();
    if (!m) return;
    m.hidden = true;
    var p = document.getElementById('adProfile');
    if (p) p.setAttribute('aria-expanded', 'false');
  };

  window.adToggleAvatarMenu = function (ev) {
    if (ev) ev.stopPropagation();
    var m = menu();
    if (!m) return;
    m.hidden = !m.hidden;
    var p = document.getElementById('adProfile');
    if (p) p.setAttribute('aria-expanded', String(!m.hidden));
  };

  window.adToggleSidebar = function (open) {
    var side = document.getElementById('adSidebar');
    if (!side) return;
    var next = typeof open === 'boolean' ? open : !side.classList.contains('open');
    side.classList.toggle('open', next);
    var scrim = document.querySelector('.ad-scrim');
    if (scrim) scrim.hidden = !next;
    var btn = document.querySelector('.ad-menu-btn');
    if (btn) btn.setAttribute('aria-expanded', String(next));
  };

  window.adSetActiveNav = function (section) {
    document.querySelectorAll('.ad-side-link[data-section]').forEach(function (a) {
      var on = a.getAttribute('data-section') === section;
      a.classList.toggle('active', on);
      if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
  };

  document.addEventListener('click', function (e) {
    var m = menu();
    if (m && !m.hidden && !m.contains(e.target)) adCloseAvatarMenu();
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    adCloseAvatarMenu();
    adToggleSidebar(false);
  });
})();
