(function () {
  "use strict";

  // --- Shared focus trap (P: drawers & modal) --------------------------------
  // One narrow focus-management implementation used by both the mobile sidebar
  // drawer and the confirm modal. Tab is contained while a container is active;
  // Escape and close handlers restore focus to the trigger separately.
  var focusTrap = (function () {
    var activeContainer = null;
    var focusableSelector =
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

    function getFocusable() {
      if (!activeContainer) return [];
      return Array.prototype.filter.call(
        activeContainer.querySelectorAll(focusableSelector),
        function (el) {
          return el.offsetParent !== null || el === document.activeElement;
        }
      );
    }

    function onKeydown(e) {
      if (e.key !== "Tab" || !activeContainer) return;
      var items = getFocusable();
      if (!items.length) {
        e.preventDefault();
        return;
      }
      var first = items[0];
      var last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      } else if (!activeContainer.contains(document.activeElement)) {
        e.preventDefault();
        first.focus();
      }
    }

    return {
      enable: function (container) {
        if (!container) return;
        if (activeContainer === container) return;
        if (activeContainer) this.disable();
        activeContainer = container;
        document.addEventListener("keydown", onKeydown, true);
      },
      disable: function () {
        if (!activeContainer) return;
        document.removeEventListener("keydown", onKeydown, true);
        activeContainer = null;
      },
    };
  })();

  // The mobile sidebar drawer dispatches these custom events from the layouts.
  window.addEventListener("focus-trap:enable", function (e) {
    focusTrap.enable(e.detail && e.detail.container);
  });
  window.addEventListener("focus-trap:disable", function () {
    focusTrap.disable();
  });

  // --- Global search dialog ---
  var dialog = document.getElementById("global-search-dialog");
  var trigger = document.getElementById("global-search-trigger");
  var searchInput = document.getElementById("global-search-input");
  var lastFocus;

  function openSearch() {
    if (!dialog) return;
    lastFocus = document.activeElement;
    dialog.showModal();
    if (searchInput) {
      searchInput.focus();
      searchInput.select();
    }
  }

  function closeSearch() {
    if (!dialog) return;
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
            var items = dialog.querySelectorAll(".search-result-item");
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

  // --- Accessible file pickers ----------------------------------------------
  // Native file controls expose browser/OS English such as "Choose Files" even
  // on an Arabic page. Keep the real input for keyboard and form semantics,
  // but render the visible trigger and selected-file status in the locale.
  function initFilePickers(container) {
    if (!container) container = document;
    var roots = [];
    if (container.nodeType === 1 && container.matches("[data-file-picker]")) {
      roots.push(container);
    }
    roots = roots.concat(Array.prototype.slice.call(container.querySelectorAll("[data-file-picker]")));
    roots.forEach(function (root) {
      if (root.dataset.filePickerInitialized === "true") return;
      var input = root.querySelector("[data-file-input]");
      var name = root.querySelector("[data-file-name]");
      if (!input || !name) return;

      function updateName() {
        var files = Array.prototype.map.call(input.files || [], function (file) {
          return file.name;
        });
        name.textContent = files.length ? files.join("، ") : (name.dataset.emptyLabel || "");
      }

      input.addEventListener("change", updateName);
      updateName();
      root.dataset.filePickerInitialized = "true";
    });
  }

  document.addEventListener("DOMContentLoaded", function () { initFilePickers(); });
  document.addEventListener("htmx:afterSwap", function (e) { initFilePickers(e.detail.target); });

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
    });
  });

  // --- HTMX error handling ---
  var feedbackEl = document.getElementById("request-feedback");
  var feedbackTimer = null;

  function showFeedback() {
    if (!feedbackEl) return;
    if (feedbackTimer) clearTimeout(feedbackTimer);
    // The translated message always comes from base.html's data-msg-error.
    var msg = feedbackEl.getAttribute("data-msg-error") || "";
    feedbackEl.textContent = msg;
    feedbackEl.classList.add("visible");
    feedbackTimer = setTimeout(function () {
      feedbackEl.classList.remove("visible");
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
  document.addEventListener("DOMContentLoaded", function () { initAssessmentChoices(); });  document.addEventListener("htmx:afterSwap", function (e) { initAssessmentChoices(e.detail.target); });

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

  // --- Normal assessment autosave ---
  function initAssessmentAutosave(container) {
    if (!container) container = document;
    var form = container.querySelector("#assessment-take-form");
    if (!form || form.dataset.autosaveInitialized === "true") return;
    var autosaveUrl = form.getAttribute("data-autosave-url");
    var csrf = form.querySelector("input[name='csrfmiddlewaretoken']");
    var status = form.querySelector("#assessment-autosave-status");
    if (!autosaveUrl || !csrf || !status) return;
    form.dataset.autosaveInitialized = "true";
    var pendingTimer = null;
    var isSubmitting = false;
    var dirty = new Map();
    var hasBaseline = false;

    function collectAnswers() {
      var answers = {};
      var fields = form.querySelectorAll("input[name^='q_'], input[name^='question_'], textarea[name^='q_'], textarea[name^='question_'], select[name^='q_'], select[name^='question_']");
      Array.prototype.forEach.call(fields, function (el) {
        if (el.type === "radio" || el.type === "checkbox") {
          if (el.checked) answers[el.name] = el.value;
        } else {
          answers[el.name] = el.value;
        }
      });
      return answers;
    }

    function setStatus(message) {
      status.textContent = message;
    }

    function save() {
      if (isSubmitting) return;
      var keys = hasBaseline ? Array.from(dirty.entries()) : null;
      if (hasBaseline && !keys.length) return;
      var answers = collectAnswers();
      if (keys) {
        answers = keys.reduce(function (delta, entry) {
          var key = entry[0];
          if (Object.prototype.hasOwnProperty.call(answers, key)) delta[key] = answers[key];
          return delta;
        }, {});
      }
      setStatus(form.dataset.autosaveSaving || "Saving answers…");
      return fetch(autosaveUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrf.value,
        },
        body: JSON.stringify({ answers: answers }),
        credentials: "same-origin",
      }).then(function (response) {
        if (!response.ok) throw new Error("autosave_failed");
        if (keys) {
          keys.forEach(function (entry) {
            var key = entry[0];
            var version = entry[1];
            if (dirty.get(key) === version) dirty.delete(key);
          });
        } else {
          hasBaseline = true;
        }
        setStatus(form.dataset.autosaveSaved || "Answers saved.");
      }).catch(function () {
        setStatus(form.dataset.autosaveFailed || "Autosave failed.");
      });
    }

    function scheduleSave() {
      window.clearTimeout(pendingTimer);
      pendingTimer = window.setTimeout(save, 1200);
    }

    function markDirty(event) {
      if (event.target && event.target.name) {
        dirty.set(event.target.name, (dirty.get(event.target.name) || 0) + 1);
      }
      scheduleSave();
    }
    form.addEventListener("change", markDirty);
    form.addEventListener("input", markDirty);
    form.addEventListener("submit", function () {
      isSubmitting = true;
    });
    window.setInterval(save, 30000);
  }

  document.addEventListener("DOMContentLoaded", function () { initAssessmentAutosave(); });
  document.addEventListener("htmx:afterSwap", function (e) { initAssessmentAutosave(e.detail.target); });

  // --- Intake form choices ---
  var intakeChoiceIdx = 1;

  function initIntakeChoices(container) {
    if (!container) container = document;
    var typeSelect = container.querySelector("[name='field_type']");
    var choicesSection = container.querySelector("#intake-choices-section");
    if (!typeSelect || !choicesSection) return;

    var syncVisibility = function () {
      var isChoice = typeSelect.value === "choice" || typeSelect.value === "multi_choice";
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
  var reasonWrap = document.getElementById("confirm-modal-reason-wrap");
  var reasonInput = document.getElementById("confirm-modal-reason");
  var reasonError = document.getElementById("confirm-modal-reason-error");
  var pendingForm = null;
  var confirmTrigger = null;
  var confirmSubmitting = false;
  var confirmReasonRequired = false;

  function showConfirmModal(message, label, reasonRequired) {
    if (!confirmModal) return;
    confirmTrigger = document.activeElement;
    confirmReasonRequired = reasonRequired === true;
    if (confirmMessage) confirmMessage.textContent = message;
    // Label comes from data-confirm-label or the trigger's own translated
    // text; the modal's default confirm button already renders {% trans "Delete" %}.
    if (confirmBtn) confirmBtn.textContent = label || confirmBtn.getAttribute("data-default-label") || "";
    if (reasonWrap) reasonWrap.classList.toggle("hidden", !confirmReasonRequired);
    if (reasonInput) {
      reasonInput.value = "";
      reasonInput.setCustomValidity("");
    }
    if (reasonError) reasonError.classList.add("hidden");
    confirmModal.classList.remove("hidden");
    // Isolate the background from keyboard + assistive tech while open.
    var shell = document.querySelector("[data-page-shell]");
    if (shell) shell.setAttribute("inert", "");
    focusTrap.enable(confirmModal);
    // Focus the safe (cancel) control first so Enter never triggers the
    // destructive confirm by accident.
    if (confirmReasonRequired && reasonInput) reasonInput.focus();
    else if (cancelBtn) cancelBtn.focus();
  }

  function hideConfirmModal(restoreButtons) {
    if (!confirmModal) return;
    if (confirmModal.classList.contains("hidden")) return;
    confirmModal.classList.add("hidden");
    confirmReasonRequired = false;
    focusTrap.disable();
    var shell = document.querySelector("[data-page-shell]");
    if (shell) shell.removeAttribute("inert");
    // Canceling must leave the original action usable: re-enable the submit
    // buttons the global submit-guard disabled before the confirmation prompt.
    if (restoreButtons !== false && pendingForm) {
      var buttons = pendingForm.querySelectorAll("button[type='submit']");
      Array.prototype.forEach.call(buttons, function (btn) { btn.disabled = false; });
    }
    pendingForm = null;
    confirmSubmitting = false;
    if (confirmTrigger && confirmTrigger.focus) {
      confirmTrigger.focus();
    }
    confirmTrigger = null;
  }

  if (cancelBtn) cancelBtn.addEventListener("click", function () { hideConfirmModal(true); });
  if (backdrop) backdrop.addEventListener("click", function () { hideConfirmModal(true); });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") hideConfirmModal(true);
  });

  if (confirmBtn) {
    confirmBtn.addEventListener("click", function () {
      if (confirmSubmitting) return;
      if (pendingForm) {
        if (confirmReasonRequired) {
          var reason = reasonInput ? reasonInput.value.trim() : "";
          if (!reason) {
            if (reasonError) reasonError.classList.remove("hidden");
            if (reasonInput) reasonInput.focus();
            return;
          }
          var reasonName = pendingForm.getAttribute("data-confirm-reason-name") || "reason";
          var reasonField = Array.prototype.find.call(
            pendingForm.querySelectorAll("input, textarea"),
            function (field) { return field.name === reasonName; }
          );
          if (reasonField) reasonField.value = reason;
        }
        confirmSubmitting = true;
        confirmBtn.disabled = true;
        var buttons = pendingForm.querySelectorAll("button[type='submit']");
        Array.prototype.forEach.call(buttons, function (btn) { btn.disabled = true; });
        pendingForm.submit();
      }
      hideConfirmModal(false);
    });
  }

  // Intercept form submits with data-confirm
  document.addEventListener("submit", function (e) {
    var form = e.target;
    var msg = form.getAttribute("data-confirm");
    if (!msg && form.hasAttribute("data-confirm-unanswered")) {
      var unanswered = [];
      var questionBlocks = form.querySelectorAll("[data-assessment-question]");
      Array.prototype.forEach.call(questionBlocks, function (block) {
        var controls = block.querySelectorAll("input[type='radio'], input[type='checkbox']");
        var answered = controls.length
          ? Array.prototype.some.call(controls, function (control) { return control.checked; })
          : Array.prototype.some.call(
              block.querySelectorAll("textarea, input:not([type='hidden']), select"),
              function (control) { return String(control.value || "").trim() !== ""; }
            );
        if (!answered) unanswered.push(block.getAttribute("data-assessment-question"));
      });
      if (unanswered.length) {
        e.preventDefault();
        pendingForm = form;
        var prefix = form.getAttribute("data-unanswered-prefix") || "";
        var suffix = form.getAttribute("data-unanswered-suffix") || "";
        showConfirmModal(
          prefix + ": " + unanswered.join(", ") + ". " + suffix,
          form.getAttribute("data-confirm-label") || "",
          false
        );
        return;
      }
    }
    if (!msg) return;
    e.preventDefault();
    pendingForm = form;
    var defaultLabel = confirmBtn ? confirmBtn.getAttribute("data-default-label") : "";
    var label = form.getAttribute("data-confirm-label") || form.querySelector("[type='submit']")?.textContent?.trim() || defaultLabel;
    showConfirmModal(msg, label, form.getAttribute("data-confirm-reason-required") === "true");
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
        var defaultLabel = confirmBtn ? confirmBtn.getAttribute("data-default-label") : "";
        var label = btn.textContent?.trim() || defaultLabel;
        showConfirmModal(msg, label, false);
      }
    }
  });

  // --- Registration program selection ---
  function initProgramSelection(container) {
    if (!container) container = document;
    var checkbox = container.querySelector("#id_apply_program");
    if (!checkbox) return;
    var details = container.querySelector("#program-details");
    if (!details) return;
    // Alpine-managed wizard (x-data + x-show with x-transition) owns visibility.
    // Keep only disabled-field semantics here to avoid fighting Alpine transitions.
    if (details.hasAttribute("x-show") || details.closest("[x-data]")) {
      var typeSelectA = container.querySelector("#id_program_type");
      var diplomaFieldA = container.querySelector("#diploma-field");
      var courseFieldA = container.querySelector("#course-field");
      var syncDisabled = function () {
        var apply = checkbox.checked;
        var isDiploma = typeSelectA && typeSelectA.value === "diploma";
        details.querySelectorAll("input, select, textarea").forEach(function (inp) { inp.disabled = !apply; });
        if (diplomaFieldA) diplomaFieldA.querySelectorAll("input, select, textarea").forEach(function (inp) { inp.disabled = !apply || !isDiploma; });
        if (courseFieldA) courseFieldA.querySelectorAll("input, select, textarea").forEach(function (inp) { inp.disabled = !apply || isDiploma; });
      };
      checkbox.addEventListener("change", syncDisabled);
      if (typeSelectA) typeSelectA.addEventListener("change", syncDisabled);
      syncDisabled();
      return;
    }

    var typeSelect = container.querySelector("#id_program_type");
    var diplomaField = container.querySelector("#diploma-field");
    var courseField = container.querySelector("#course-field");

    var sync = function () {
      var apply = checkbox.checked;
      details.classList.toggle("hidden", !apply);
      details.querySelectorAll("input, select, textarea").forEach(function (inp) {
        inp.disabled = !apply;
      });
      if (apply && typeSelect && diplomaField && courseField) {
        var isDiploma = typeSelect.value === "diploma";
        diplomaField.classList.toggle("hidden", !isDiploma);
        diplomaField.querySelectorAll("input, select, textarea").forEach(function (inp) {
          inp.disabled = !isDiploma;
        });
        courseField.classList.toggle("hidden", isDiploma);
        courseField.querySelectorAll("input, select, textarea").forEach(function (inp) {
          inp.disabled = isDiploma;
        });
      }
    };

    checkbox.removeEventListener("change", sync);
    checkbox.addEventListener("change", sync);
    if (typeSelect) {
      typeSelect.removeEventListener("change", sync);
      typeSelect.addEventListener("change", sync);
    }
    sync();
  }

  document.addEventListener("DOMContentLoaded", function () { initProgramSelection(); });
  document.addEventListener("htmx:afterSwap", function (e) { initProgramSelection(e.detail.target); });

  // Diploma applications capture attendance mode once. The server remains
  // authoritative; this only keeps the course form from showing a Diploma
  // field that does not apply to it.
  function initStudentApplySelection(container) {
    if (!container) container = document;
    var typeSelect = container.querySelector("#program-type");
    var modeField = container.querySelector("#delivery-mode-field");
    var modeSelect = container.querySelector("#delivery-mode");
    if (!typeSelect || !modeField || !modeSelect) return;
    var sync = function () {
      var isDiploma = typeSelect.value === "diploma";
      modeField.classList.toggle("hidden", !isDiploma);
      modeSelect.disabled = !isDiploma;
      modeSelect.required = isDiploma;
    };
    typeSelect.removeEventListener("change", sync);
    typeSelect.addEventListener("change", sync);
    sync();
  }

  document.addEventListener("DOMContentLoaded", function () { initStudentApplySelection(); });
  document.addEventListener("htmx:afterSwap", function (e) { initStudentApplySelection(e.detail.target); });

  // --- Registration wizard (multi-step) ---
  function initRegistrationWizard(container) {
    if (!container) container = document;
    var wizard = container.querySelector("#registration-wizard");
    if (!wizard) return;
    // Alpine wizard (x-data + x-show/x-transition) owns its own visibility; avoid fighting transitions.
    if (wizard.hasAttribute("x-data")) return;
    var steps = Array.prototype.slice.call(wizard.querySelectorAll(".form-step"));
    if (!steps.length) return;
    var indicators = Array.prototype.slice.call(wizard.querySelectorAll("[data-wizard-step-index]"));
    var prevBtn = wizard.querySelector("[data-wizard-prev]");
    var nextBtn = wizard.querySelector("[data-wizard-next]");
    var submitBtn = wizard.querySelector("[data-wizard-submit]");
    var initial = parseInt(wizard.getAttribute("data-initial-step") || "1", 10);
    if (isNaN(initial) || initial < 1) initial = 1;
    if (initial > steps.length) initial = steps.length;
    var current = initial - 1;

    function validateStep(step) {
      var fields = step.querySelectorAll("input, select, textarea");
      for (var i = 0; i < fields.length; i++) {
        var f = fields[i];
        if (f.disabled || f.readOnly) continue;
        if (!f.checkValidity()) {
          f.reportValidity();
          return false;
        }
      }
      return true;
    }

    function show(index) {
      current = index;
      steps.forEach(function (s, i) {
        s.classList.toggle("hidden", i !== current);
      });
      indicators.forEach(function (ind, i) {
        ind.classList.toggle("bg-brand-strong", i === current);
        ind.classList.toggle("text-white", i === current);
        ind.classList.toggle("bg-line", i !== current);
        ind.classList.toggle("text-muted", i !== current);
      });
      prevBtn.classList.toggle("hidden", current === 0);
      nextBtn.classList.toggle("hidden", current === steps.length - 1);
      submitBtn.classList.toggle("hidden", current !== steps.length - 1);
    }

    function next() {
      if (current >= steps.length - 1) return;
      if (!validateStep(steps[current])) return;
      show(current + 1);
      var first = steps[current].querySelector("input, select, textarea");
      if (first) first.focus();
    }

    function prev() {
      if (current <= 0) return;
      show(current - 1);
    }

    nextBtn.removeEventListener("click", next);
    nextBtn.addEventListener("click", next);
    prevBtn.removeEventListener("click", prev);
    prevBtn.addEventListener("click", prev);

    wizard.removeEventListener("keydown", onWizardKey);
    wizard.addEventListener("keydown", onWizardKey);
    function onWizardKey(e) {
      if (e.key !== "Enter" || e.isComposing) return;
      var tag = e.target && e.target.tagName;
      if (tag !== "INPUT") return;
      var type = e.target.type;
      if (type === "submit" || type === "button" || type === "checkbox" || type === "radio") return;
      e.preventDefault();
      next();
    }

    // With novalidate on the form, validate the visible step on the final submit.
    var form = wizard.closest("form");
    if (form) {
      form.removeEventListener("submit", onWizardSubmit);
      form.addEventListener("submit", onWizardSubmit);
    }
    function onWizardSubmit(e) {
      if (!validateStep(steps[current])) {
        e.preventDefault();
      }
    }

    show(current);
  }

  document.addEventListener("DOMContentLoaded", function () { initRegistrationWizard(); });
  document.addEventListener("htmx:afterSwap", function (e) { initRegistrationWizard(e.detail.target); });

  // --- Entrance decision tabs ---
  // The default panel is rendered without JavaScript. This initializer only
  // adds tab switching and therefore cannot make the decision actions vanish
  // when Alpine is delayed or unavailable.
  function initDecisionTabs(container) {
    if (!container) container = document;
    var roots = [];
    if (container.nodeType === 1 && container.matches("[data-decision-tabs]")) {
      roots.push(container);
    }
    roots = roots.concat(Array.prototype.slice.call(container.querySelectorAll("[data-decision-tabs]")));
    roots.forEach(function (root) {
      if (root.dataset.decisionTabsInitialized === "true") return;
      var buttons = Array.prototype.slice.call(root.querySelectorAll("[data-decision-tab]"));
      var panels = Array.prototype.slice.call(root.querySelectorAll("[data-decision-panel]"));
      if (!buttons.length || !panels.length) return;

      function select(key) {
        buttons.forEach(function (button) {
          var selected = button.getAttribute("data-decision-tab") === key;
          button.classList.toggle("tab-active", selected);
          button.setAttribute("aria-selected", selected ? "true" : "false");
        });
        panels.forEach(function (panel) {
          panel.hidden = panel.getAttribute("data-decision-panel") !== key;
        });
      }

      var defaultKey = root.getAttribute("data-decision-default") || "";
      var initialButton = buttons.find(function (button) {
        return button.getAttribute("aria-selected") === "true";
      }) || buttons.find(function (button) {
        return button.getAttribute("data-decision-tab") === defaultKey;
      }) || buttons[0];
      select(initialButton.getAttribute("data-decision-tab"));
      buttons.forEach(function (button) {
        button.addEventListener("click", function () {
          select(button.getAttribute("data-decision-tab"));
        });
      });
      root.dataset.decisionTabsInitialized = "true";
    });
  }

  document.addEventListener("DOMContentLoaded", function () { initDecisionTabs(); });
  document.addEventListener("htmx:afterSwap", function (e) { initDecisionTabs(e.detail.target); });

  // --- Lucide Icons (loaded via CDN) ---
  function initLucide() {
    if (typeof lucide !== "undefined") {
      lucide.createIcons();
    }
  }
  document.addEventListener("DOMContentLoaded", initLucide);
  document.addEventListener("htmx:afterSettle", initLucide);

  // --- Alpine init for HTMX-inserted content ---
  // Inline x-data components (e.g. _expandable_text.html) must initialize when
  // HTMX swaps new fragments in, not only on initial page load (P8-01).
  document.addEventListener("htmx:afterSwap", function (e) {
    if (window.Alpine && e.detail && e.detail.target) {
      window.Alpine.initTree(e.detail.target);
    }
  });

  // Keep the persistent sidebar's active state in sync after content-only
  // navigation replaces #main without rebuilding the shell. Only the longest
  // prefix match becomes active so two items are never active at once.
  function syncContentNavigation() {
    var currentPath = window.location.pathname.replace(/\/+$/, "") || "/";
    var links = document.querySelectorAll("[data-content-navigation] a[href]");
    var bestLink = null;
    var bestLen = -1;
    for (var i = 0; i < links.length; i++) {
      var link = links[i];
      var linkPath;
      try {
        linkPath = new URL(link.href, window.location.href).pathname.replace(/\/+$/, "") || "/";
      } catch (_error) {
        continue;
      }
      var isMatch = currentPath === linkPath ||
        (linkPath !== "/" && currentPath.indexOf(linkPath + "/") === 0);
      // Clear all first so stale server-rendered active does not linger.
      link.classList.remove("active");
      link.removeAttribute("aria-current");
      if (isMatch && linkPath.length > bestLen) {
        bestLen = linkPath.length;
        bestLink = link;
      }
    }
    if (bestLink) {
      bestLink.classList.add("active");
      bestLink.setAttribute("aria-current", "page");
    }
  }

  document.addEventListener("DOMContentLoaded", syncContentNavigation);
  document.addEventListener("htmx:afterSwap", syncContentNavigation);
  document.addEventListener("htmx:pushedIntoHistory", syncContentNavigation);

})();
