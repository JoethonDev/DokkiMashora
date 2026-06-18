/* Placeholder for any global JS that we want bundled. HTMX + Alpine load from CDN. */
(function () {
  "use strict";

  // Disable htmx on pages that haven't loaded yet.
  document.addEventListener("DOMContentLoaded", function () {
    // Apply dark mode based on user preference.
    try {
      var stored = window.localStorage.getItem("dm-theme");
      if (stored === "dark" || (!stored && window.matchMedia("(prefers-color-scheme: dark)").matches)) {
        document.documentElement.classList.add("dark");
      }
    } catch (err) {
      // ignore - localStorage may be blocked
    }
  });
})();
