(function () {
  "use strict";

  // --- Global search dialog ---
  var dialog = document.getElementById("global-search-dialog");
  var trigger = document.getElementById("global-search-trigger");
  var searchInput = document.getElementById("global-search-input");
  var lastFocus;

  function openSearch() {
    if (!dialog) return;
    lastFocus = document.activeElement;
    dialog.showModal();
    dialog.classList.add("open");
    if (searchInput) {
      searchInput.focus();
      searchInput.select();
    }
  }

  function closeSearch() {
    if (!dialog) return;
    dialog.classList.remove("open");
    dialog.close();
    if (lastFocus && lastFocus.focus) {
      lastFocus.focus();
    }
  }

  if (trigger) {
    trigger.addEventListener("click", openSearch);
  }

  if (dialog) {
    dialog.addEventListener("click", function (e) {
      var rect = dialog.getBoundingClientRect();
      var isInDialog =
        rect.top <= e.clientY &&
        e.clientY <= rect.top + rect.height &&
        rect.left <= e.clientX &&
        e.clientX <= rect.left + rect.width;
      if (!isInDialog) {
        closeSearch();
      }
    });

    dialog.addEventListener("close", function () {
      dialog.classList.remove("open");
      if (lastFocus && lastFocus.focus) {
        lastFocus.focus();
      }
    });

    // Keyboard navigation within results
    if (searchInput) {
      var resultsContainer = document.getElementById("global-search-results");

      dialog.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && e.isComposing) return;

        var active = dialog.querySelector(".search-result-item.active");
        var items = Array.from(dialog.querySelectorAll(".search-result-item"));

        if (e.key === "ArrowDown") {
          e.preventDefault();
          var nextIdx = active ? items.indexOf(active) + 1 : 0;
          if (nextIdx < items.length) {
            if (active) active.classList.remove("active");
            items[nextIdx].classList.add("active");
            items[nextIdx].focus();
          }
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          if (active) {
            var prevIdx = items.indexOf(active) - 1;
            if (prevIdx >= 0) {
              active.classList.remove("active");
              items[prevIdx].classList.add("active");
              items[prevIdx].focus();
            }
          }
        } else if (e.key === "Enter") {
          if (active && active.tagName === "A") {
            e.preventDefault();
            closeSearch();
            window.location.href = active.getAttribute("href");
          }
        }
      });

      if (resultsContainer) {
        resultsContainer.addEventListener("mouseover", function (e) {
          var item = e.target.closest(".search-result-item");
          if (item) {
            items = dialog.querySelectorAll(".search-result-item");
            items.forEach(function (i) { i.classList.remove("active"); });
            item.classList.add("active");
          }
        });
      }
    }
  }

  // Dialog close button
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("#search-dialog-close-btn");
    if (!btn) return;
    closeSearch();
  });

  // Search retry button
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("#search-retry-btn");
    if (!btn) return;
    var input = document.getElementById("global-search-input");
    if (input) input.dispatchEvent(new Event("search", { bubbles: true }));
  });

  // Keyboard shortcuts
  document.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key === "k") {
      e.preventDefault();
      openSearch();
      return;
    }
    if (
      e.key === "/" &&
      !e.ctrlKey &&
      !e.metaKey &&
      document.activeElement &&
      document.activeElement.tagName !== "INPUT" &&
      document.activeElement.tagName !== "TEXTAREA" &&
      document.activeElement.tagName !== "SELECT" &&
      !document.activeElement.isContentEditable
    ) {
      e.preventDefault();
      openSearch();
    }
  });

  // --- Native form submit guard ---
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (form.matches("form[hx-get], form[hx-post], form[hx-put], form[hx-patch], form[hx-delete]")) {
      return;
    }
    if (!form.checkValidity()) return;
    var buttons = form.querySelectorAll("button[type='submit']");
    Array.from(buttons).forEach(function (btn) {
      btn.disabled = true;
      btn.classList.add("disabled");
    });
  });

  // --- HTMX error handling ---
  var feedbackEl = document.getElementById("request-feedback");
  var feedbackTimer = null;

  function showFeedback() {
    if (!feedbackEl) return;
    if (feedbackTimer) clearTimeout(feedbackTimer);
    var msg = feedbackEl.getAttribute("data-msg-error") || "Request failed. Please try again.";
    feedbackEl.textContent = msg;
    feedbackEl.removeAttribute("hidden");
    feedbackEl.classList.add("visible");
    feedbackTimer = setTimeout(function () {
      feedbackEl.classList.remove("visible");
      feedbackEl.setAttribute("hidden", "");
      feedbackTimer = null;
    }, 6000);
  }

  var htmxEvents = [
    "htmx:sendError",
    "htmx:timeout",
    "htmx:responseError",
    "htmx:targetError",
    "htmx:swapError",
    "htmx:onLoadError"
  ];

  htmxEvents.forEach(function (eventName) {
    document.addEventListener(eventName, function (e) {
      if (e.defaultPrevented) return;
      var indicator = document.getElementById("global-loading");
      if (indicator) indicator.classList.remove("htmx-request");
      var target = e.detail && e.detail.target;
      if (target) target.removeAttribute("aria-busy");
      var path = (e.detail && (e.detail.path || (e.detail.requestConfig && e.detail.requestConfig.path))) || "unknown";
      var status = e.detail && e.detail.xhr && e.detail.xhr.status;
      console.error(eventName, path, status);
      showFeedback();
    });
  });

  // --- Assessment choice rows ---
  var choiceIdx = 100;

  function initChoiceButtons(container) {
    if (!container) container = document;
    var addBtn = container.querySelector("#add-assessment-choice-btn");
    var choicesEl = container.querySelector("#assessment-choices");
    if (!addBtn || !choicesEl) return;
    // Re-read idx from existing rows
    var existing = choicesEl.querySelectorAll(".choice-row");
    choiceIdx = 100;
    existing.forEach(function (r) {
      var cid = r.getAttribute("data-choice-id");
      if (cid && cid.indexOf("new_") === 0) {
        var num = parseInt(cid.replace("new_", ""), 10);
        if (num >= choiceIdx) choiceIdx = num + 1;
      }
    });
    addBtn.addEventListener("click", function () {
      var tpl = container.querySelector("#assessment-choice-template");
      if (!tpl) return;
      var html = tpl.innerHTML.replace(/__IDX__/g, "new_" + choiceIdx);
      var wrapper = document.createElement("div");
      wrapper.innerHTML = html;
      choicesEl.appendChild(wrapper.firstElementChild);
      choiceIdx++;
    });
  }

  // Remove choice row
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".remove-choice-btn");
    if (!btn) return;
    var row = btn.closest(".choice-row");
    var container = row && row.closest("#assessment-choices");
    if (row && container && container.querySelectorAll(".choice-row").length > 1) {
      row.remove();
    }
  });

  // Init on page load and after HTMX swaps
  document.addEventListener("DOMContentLoaded", function () { initChoiceButtons(); });
  document.addEventListener("htmx:afterSwap", function (e) { initChoiceButtons(e.detail.target); });

  // --- Entrance exam choice add button ---
  function initEntranceChoices(container) {
    if (!container) container = document;
    var btn = container.querySelector("#add-choice-btn");
    var formContainer = container.querySelector("#choice-forms");
    var tpl = container.querySelector("#choice-template");
    var totalInput = container.querySelector("[name$='-TOTAL_FORMS']");
    if (!btn || !formContainer || !tpl || !totalInput) return;
    btn.addEventListener("click", function () {
      var idx = parseInt(totalInput.value, 10);
      var html = tpl.innerHTML.replace(/__prefix__/g, idx);
      var wrapper = document.createElement("div");
      wrapper.innerHTML = html;
      formContainer.appendChild(wrapper.firstElementChild);
      totalInput.value = idx + 1;
    });
  }

  document.addEventListener("DOMContentLoaded", function () { initEntranceChoices(); });
  document.addEventListener("htmx:afterSwap", function (e) { initEntranceChoices(e.detail.target); });

})();
