/* The teacher and student dashboards are light-only designs (white cards, dark text).
   Several shared stylesheets (lms-ui.css, markdown-styles.css, theme-colors.css,
   chat-enhanced.css …) still carry `@media (prefers-color-scheme: dark)` blocks from the
   old dark-capable pages. With the OS / browser in dark mode those blocks turned text
   near-white while the backgrounds stayed white, so questions and options disappeared.

   Loaded at the END of <head>, after every stylesheet (stylesheets block this script, so
   it runs before first paint): it removes those dark-only blocks, which makes these pages
   render exactly as they do in light mode. Pages that do not load this file are untouched. */
(function () {
  var DARK = /prefers-color-scheme\s*:\s*dark/i;

  function strip(owner) {
    var rules;
    try { rules = owner.cssRules; } catch (e) { return; } // cross-origin sheet (CDN): not ours
    if (!rules) return;
    for (var i = rules.length - 1; i >= 0; i--) {
      var rule = rules[i];
      var cond = rule.media ? rule.media.mediaText : '';
      if (cond && DARK.test(cond)) {
        try { owner.deleteRule(i); } catch (e) { /* ignore */ }
      } else if (rule.cssRules && rule.type !== 1) {
        strip(rule); // @supports / @layer / other @media wrappers
      } else if (rule.styleSheet) {
        strip(rule.styleSheet); // @import
      }
    }
  }

  function run() {
    for (var i = 0; i < document.styleSheets.length; i++) strip(document.styleSheets[i]);
  }

  run();
  // Stylesheets added later (lazy-loaded widgets) get the same treatment.
  document.addEventListener('DOMContentLoaded', run);
  window.addEventListener('load', run);
  window.iqbalForceLightTheme = run;
})();
