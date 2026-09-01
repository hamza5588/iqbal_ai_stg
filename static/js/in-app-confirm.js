/**
 * In-app confirm dialog that matches Iqbal AI dashboard UI.
 * Replaces native window.confirm() so prompts stay inside the app chrome.
 *
 * Usage: const ok = await showInAppConfirm(message, { title, confirmLabel, cancelLabel, iconClass });
 */
(function (global) {
  const STYLE_ID = 'in-app-confirm-styles-v2';
  const ROOT_ID = 'inAppConfirmBanner';

  const STYLES = `
    .in-app-confirm {
      position: fixed;
      inset: 0;
      z-index: 200010;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 16px;
      background: rgba(5, 20, 22, 0.55);
      backdrop-filter: blur(2px);
      -webkit-backdrop-filter: blur(2px);
      font-family: 'Segoe UI', Arial, sans-serif;
    }
    .in-app-confirm.hidden { display: none !important; }
    .in-app-confirm:not(.hidden) { display: flex !important; }
    .in-app-confirm-card {
      width: min(420px, 100%);
      background: #fff;
      border-radius: 20px;
      overflow: hidden;
      box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);
      display: flex;
      flex-direction: column;
    }
    .in-app-confirm-body {
      display: flex;
      align-items: flex-start;
      gap: 16px;
      padding: 28px 28px 20px;
    }
    .in-app-confirm-icon-wrap {
      width: 54px;
      height: 54px;
      border-radius: 14px;
      background: var(--primary-600, #1c7c3e);
      color: #fff;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      font-size: 20px;
    }
    .in-app-confirm-copy h3 {
      margin: 0 0 6px;
      font-size: 20px;
      font-weight: 800;
      color: #111;
      line-height: 1.25;
    }
    .in-app-confirm-copy p {
      margin: 0;
      font-size: 13.5px;
      font-weight: 500;
      color: #6b7280;
      line-height: 1.45;
    }
    .in-app-confirm-footer {
      background: var(--brand-bar, #051416);
      padding: 16px 28px;
      display: flex;
      justify-content: flex-end;
      gap: 10px;
    }
    .in-app-confirm-cancel,
    .in-app-confirm-ok {
      border-radius: 8px;
      font-size: 13px;
      padding: 10px 18px;
      cursor: pointer;
      font-family: inherit;
    }
    .in-app-confirm-cancel {
      background: #2b2b2b;
      border: none;
      color: #eee;
      font-weight: 600;
    }
    .in-app-confirm-ok {
      background: var(--primary-600, #1c7c3e);
      border: 1.5px solid var(--theme-accent-bright, #d0f73a);
      color: #fff;
      font-weight: 700;
    }
    .in-app-confirm-cancel:hover,
    .in-app-confirm-ok:hover { opacity: 0.9; }
  `;

  function ensureMarkup() {
    if (!document.getElementById(STYLE_ID)) {
      const style = document.createElement('style');
      style.id = STYLE_ID;
      style.textContent = STYLES;
      document.head.appendChild(style);
    }

    let root = document.getElementById(ROOT_ID);
    const needsRebuild = !root || !document.getElementById('inAppConfirmTitle');
    if (needsRebuild) {
      if (root) root.remove();
      root = document.createElement('div');
      root.id = ROOT_ID;
      root.className = 'in-app-confirm hidden';
      root.setAttribute('role', 'dialog');
      root.setAttribute('aria-modal', 'true');
      root.setAttribute('aria-labelledby', 'inAppConfirmTitle');
      root.setAttribute('aria-describedby', 'inAppConfirmMessage');
      root.innerHTML = `
        <div class="in-app-confirm-card">
          <div class="in-app-confirm-body">
            <div class="in-app-confirm-icon-wrap" aria-hidden="true">
              <i id="inAppConfirmIcon" class="fas fa-exclamation-circle"></i>
            </div>
            <div class="in-app-confirm-copy">
              <h3 id="inAppConfirmTitle">Confirm</h3>
              <p id="inAppConfirmMessage"></p>
            </div>
          </div>
          <div class="in-app-confirm-footer">
            <button type="button" id="inAppConfirmCancel" class="in-app-confirm-cancel">Cancel</button>
            <button type="button" id="inAppConfirmOk" class="in-app-confirm-ok">OK</button>
          </div>
        </div>
      `;
      document.body.appendChild(root);
    }
    return root;
  }

  function showInAppConfirm(message, options) {
    const root = ensureMarkup();
    const textEl = document.getElementById('inAppConfirmMessage');
    const titleEl = document.getElementById('inAppConfirmTitle');
    const iconWrap = root.querySelector('.in-app-confirm-icon-wrap');
    const okBtn = document.getElementById('inAppConfirmOk');
    const cancelBtn = document.getElementById('inAppConfirmCancel');

    if (!root || !textEl || !okBtn || !cancelBtn) {
      return Promise.resolve(window.confirm(message));
    }

    const opts = options || {};
    textEl.textContent = String(message || '');
    if (titleEl) titleEl.textContent = opts.title || 'Confirm';
    if (iconWrap) {
      iconWrap.innerHTML = '<i class="' + (opts.iconClass || 'fas fa-exclamation-circle') + '"></i>';
      if (global.FontAwesome && FontAwesome.dom && typeof FontAwesome.dom.i2svg === 'function') {
        FontAwesome.dom.i2svg({ node: iconWrap });
      }
    }
    okBtn.textContent = opts.confirmLabel || 'OK';
    cancelBtn.textContent = opts.cancelLabel || 'Cancel';

    root.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    okBtn.focus();

    return new Promise(function (resolve) {
      function finish(result) {
        root.classList.add('hidden');
        document.body.style.overflow = '';
        okBtn.removeEventListener('click', onOk);
        cancelBtn.removeEventListener('click', onCancel);
        root.removeEventListener('click', onBackdrop);
        document.removeEventListener('keydown', onKey);
        resolve(result);
      }
      function onOk() { finish(true); }
      function onCancel() { finish(false); }
      function onBackdrop(event) {
        if (event.target === root) finish(false);
      }
      function onKey(event) {
        if (event.key === 'Escape') finish(false);
        if (event.key === 'Enter') finish(true);
      }
      okBtn.addEventListener('click', onOk);
      cancelBtn.addEventListener('click', onCancel);
      root.addEventListener('click', onBackdrop);
      document.addEventListener('keydown', onKey);
    });
  }

  global.showInAppConfirm = showInAppConfirm;
})(window);
