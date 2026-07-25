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

  function initAssessmentChoices(container) {
    if (!container) container = document;
    var addBtn = container.querySelector("#add-assessment-choice-btn");
    var choicesEl = container.querySelector("#assessment-choices");
    if (!addBtn || !choicesEl) return;

    // Remove stale listeners by cloning
    var newBtn = addBtn.cloneNode(true);
    addBtn.parentNode.replaceChild(newBtn, addBtn);

    var typeSelect = container.querySelector("[name='question_type']");
    if (typeSelect) {
      var syncVisibility = function () {
        var isMcq = typeSelect.value === "mcq";
        choicesEl.classList.toggle("hidden", !isMcq);
        var fieldset = choicesEl.closest("fieldset");
        if (fieldset) fieldset.classList.toggle("hidden", !isMcq);
        var inputs = choicesEl.querySelectorAll("input, select, textarea");
        inputs.forEach(function (inp) { inp.disabled = !isMcq; });
      };
      typeSelect.removeEventListener("change", syncVisibility);
      typeSelect.addEventListener("change", syncVisibility);
      syncVisibility();
    }

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

    newBtn.addEventListener("click", function () {
      var tpl = container.querySelector("#assessment-choice-template");
      if (!tpl) return;
      var html = tpl.innerHTML.replace(/__IDX__/g, "new_" + choiceIdx);
      var wrapper = document.createElement("div");
      wrapper.innerHTML = html;
      var row = wrapper.firstElementChild;
      choicesEl.appendChild(row);
      choiceIdx++;
    });
  }

  // Remove choice row (delegated, works for both systems)
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".remove-choice-btn");
    if (!btn) return;
    var row = btn.closest(".choice-row");
    if (!row) return;
    var container = row.closest("#assessment-choices") || row.closest("#choice-forms");
    if (!container) return;
    if (container.querySelectorAll(".choice-row:not(.hidden)").length <= 1) return;
    var deleteCheckbox = row.querySelector("[name$='-DELETE']");
    if (deleteCheckbox) {
      // Existing choice — mark for deletion, hide row, keep in DOM for form submission
      deleteCheckbox.checked = true;
      row.classList.add("hidden");
    } else {
      // New choice — remove from DOM and decrement TOTAL_FORMS
      var totalInput = container.closest("form") && container.closest("form").querySelector("[name$='-TOTAL_FORMS']");
      if (totalInput) totalInput.value = parseInt(totalInput.value, 10) - 1;
      row.remove();
    }
  });

  // Init on page load and after HTMX swaps
  document.addEventListener("DOMContentLoaded", function () { initAssessmentChoices(); });
  document.addEventListener("htmx:afterSwap", function (e) { initAssessmentChoices(e.detail.target); });

  // --- Entrance exam choices ---
  function initEntranceChoices(container) {
    if (!container) container = document;
    var btn = container.querySelector("#add-choice-btn");
    var formContainer = container.querySelector("#choice-forms");
    var tpl = container.querySelector("#choice-template");
    var totalInput = container.querySelector("[name$='-TOTAL_FORMS']");
    if (!btn || !formContainer || !tpl || !totalInput) return;

    // Remove stale listeners
    var newBtn = btn.cloneNode(true);
    btn.parentNode.replaceChild(newBtn, btn);

    // MCQ/Written toggle
    var typeSelect = container.querySelector("[name='question_type']");
    var fieldset = container.querySelector("#choices-fieldset");
    if (typeSelect && fieldset) {
      var syncVisibility = function () {
        var isMcq = typeSelect.value === "mcq";
        fieldset.classList.toggle("hidden", !isMcq);
        // Disable inputs when hidden
        var inputs = fieldset.querySelectorAll("input, select, textarea");
        inputs.forEach(function (inp) { inp.disabled = !isMcq; });
      };
      typeSelect.removeEventListener("change", syncVisibility);
      typeSelect.addEventListener("change", syncVisibility);
      syncVisibility();
    }

    newBtn.addEventListener("click", function () {
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

  // --- Intake form choices ---
  var intakeChoiceIdx = 1;

  function initIntakeChoices(container) {
    if (!container) container = document;
    var typeSelect = container.querySelector("[name='field_type']");
    var choicesSection = container.querySelector("#intake-choices-section");
    if (!typeSelect || !choicesSection) return;

    var syncVisibility = function () {
      var isChoice = typeSelect.value === "choice";
      choicesSection.classList.toggle("hidden", !isChoice);
    };
    typeSelect.removeEventListener("change", syncVisibility);
    typeSelect.addEventListener("change", syncVisibility);
    syncVisibility();

    var addBtn = container.querySelector("#add-intake-choice-btn");
    var choicesEl = container.querySelector("#intake-choices");
    var tpl = container.querySelector("#intake-choice-template");
    if (!addBtn || !choicesEl || !tpl) return;

    // Remove stale listeners
    var newBtn = addBtn.cloneNode(true);
    addBtn.parentNode.replaceChild(newBtn, addBtn);

    // Track existing choices
    var existing = choicesEl.querySelectorAll(".choice-row");
    intakeChoiceIdx = 1;
    existing.forEach(function (r) {
      var inp = r.querySelector("input");
      if (inp) {
        var match = inp.name.match(/intake_choice_(\d+)/);
        if (match) {
          var num = parseInt(match[1], 10);
          if (num >= intakeChoiceIdx) intakeChoiceIdx = num + 1;
        }
      }
    });

    newBtn.addEventListener("click", function () {
      var html = tpl.innerHTML.replace(/__IDX__/g, intakeChoiceIdx);
      var wrapper = document.createElement("div");
      wrapper.innerHTML = html;
      choicesEl.appendChild(wrapper.firstElementChild);
      intakeChoiceIdx++;
    });
  }

  document.addEventListener("DOMContentLoaded", function () { initIntakeChoices(); });
  document.addEventListener("htmx:afterSwap", function (e) { initIntakeChoices(e.detail.target); });

  // Intake choice remove
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".intake-remove-btn");
    if (!btn) return;
    var row = btn.closest(".choice-row");
    if (!row) return;
    var container = row.closest("#intake-choices");
    if (container && container.querySelectorAll(".choice-row").length > 1) {
      row.remove();
    }
  });

  // --- Auto-submit filter forms on change ---
  document.addEventListener("change", function (e) {
    var select = e.target.closest("select[data-auto-submit]");
    if (!select) return;
    var form = select.closest("form");
    if (form) form.submit();
  });

  // Debounced search input submission
  var searchTimers = {};
  document.addEventListener("input", function (e) {
    var input = e.target.closest("input[data-auto-search]");
    if (!input) return;
    var form = input.closest("form");
    if (!form) return;
    var key = form.id || Math.random();
    if (searchTimers[key]) clearTimeout(searchTimers[key]);
    searchTimers[key] = setTimeout(function () { form.submit(); }, 400);
  });

  // --- Confirm modal ---
  var confirmModal = document.getElementById("confirm-modal");
  var confirmMessage = document.getElementById("confirm-modal-message");
  var confirmBtn = document.getElementById("confirm-modal-confirm");
  var cancelBtn = document.getElementById("confirm-modal-cancel");
  var backdrop = document.getElementById("confirm-modal-backdrop");
  var pendingForm = null;

  function showConfirmModal(message, label) {
    if (!confirmModal) return;
    if (confirmMessage) confirmMessage.textContent = message;
    if (confirmBtn) confirmBtn.textContent = label || "Delete";
    confirmModal.classList.remove("hidden");
  }

  function hideConfirmModal() {
    if (!confirmModal) return;
    confirmModal.classList.add("hidden");
    pendingForm = null;
  }

  if (cancelBtn) cancelBtn.addEventListener("click", hideConfirmModal);
  if (backdrop) backdrop.addEventListener("click", hideConfirmModal);

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") hideConfirmModal();
  });

  if (confirmBtn) {
    confirmBtn.addEventListener("click", function () {
      if (pendingForm) {
        pendingForm.submit();
      }
      hideConfirmModal();
    });
  }

  // Intercept form submits with data-confirm
  document.addEventListener("submit", function (e) {
    var form = e.target;
    var msg = form.getAttribute("data-confirm");
    if (!msg) return;
    e.preventDefault();
    pendingForm = form;
    var label = form.getAttribute("data-confirm-label") || form.querySelector("[type='submit']")?.textContent?.trim() || "Delete";
    showConfirmModal(msg, label);
  });

  // Intercept button clicks with data-confirm (for non-form use)
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-confirm]");
    if (!btn) return;
    if (btn.tagName === "BUTTON" && btn.type !== "submit") {
      // Non-submit button with data-confirm - trigger via form
      var form = btn.closest("form");
      if (form) {
        e.preventDefault();
        var msg = btn.getAttribute("data-confirm") || form.getAttribute("data-confirm");
        if (!msg) return;
        e.stopPropagation();
        pendingForm = form;
        var label = btn.textContent?.trim() || "Delete";
        showConfirmModal(msg, label);
      }
    }
  });

  // --- Lucide Icons (loaded via CDN) ---
  function initLucide() {
    if (typeof lucide !== "undefined") {
      lucide.createIcons();
    }
  }
  document.addEventListener("DOMContentLoaded", initLucide);
  document.addEventListener("htmx:afterSettle", initLucide);

})();
