(function () {
  "use strict";

  function getHtmxSwapRoot(event) {
    var ctx = event && event.detail && event.detail.ctx;
    var target = ctx && ctx.target;
    if (target && target.isConnected) return target;
    if (target && target.id) {
      var replacement = document.getElementById(target.id);
      if (replacement) return replacement;
    }
    return document.body;
  }

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
        var language = (document.documentElement.lang || "").toLowerCase();
        var separator = language.indexOf("ar") === 0 ? "، " : ", ";
        name.textContent = files.length ? files.join(separator) : (name.dataset.emptyLabel || "");
      }

      input.addEventListener("change", updateName);
      updateName();
      root.dataset.filePickerInitialized = "true";
    });
  }

  document.addEventListener("DOMContentLoaded", function () { initFilePickers(); });
  document.addEventListener("htmx:after:swap", function (e) { initFilePickers(getHtmxSwapRoot(e)); });

  // --- Native form submit guard ---
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (form.matches("form[hx-get], form[hx-post], form[hx-put], form[hx-patch], form[hx-delete]")) {
      return;
    }
    if (!form.checkValidity()) return;
    var submitter = e.submitter || null;
    var buttons = form.querySelectorAll("button[type='submit']");
    Array.from(buttons).forEach(function (btn) {
      // A disabled control is not successful, so disabling the clicked submit
      // button here would silently drop its name/value from the POST (that is
      // how the Accept/Reject name="action" buttons submitted no action at
      // all). The browser builds the form data set right after this event, so
      // leave the submitter enabled for now and disable it on the next task to
      // still block double submits.
      if (btn === submitter) return;
      btn.disabled = true;
    });
    if (submitter) {
      window.setTimeout(function () { submitter.disabled = true; }, 0);
    }
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

  var htmxEvents = ["htmx:error", "htmx:response:error"];

  htmxEvents.forEach(function (eventName) {
    document.addEventListener(eventName, function (e) {
      if (e.defaultPrevented) return;
      var ctx = e.detail && e.detail.ctx;
      var status = ctx && ctx.response && ctx.response.status;
      console.error("HTMX request failed", { event: e.type, status: status });
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
  document.addEventListener("htmx:after:swap", function (e) { initAssessmentChoices(getHtmxSwapRoot(e)); });

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
  document.addEventListener("htmx:after:swap", function (e) { initEntranceChoices(getHtmxSwapRoot(e)); });

  // --- Normal assessment autosave ---
  var activeAssessmentAutosave = null;

  function destroyAssessmentAutosave() {
    var state = activeAssessmentAutosave;
    if (!state) return;

    window.clearInterval(state.interval);
    state.form.removeEventListener("change", state.onAnswerChange);
    state.form.removeEventListener("input", state.onAnswerChange);
    state.form.removeEventListener("submit", state.onSubmit);
    // Let an already scheduled one-shot save finish, but stop the recurring
    // timer and listeners once the form is no longer part of the page.
    state.form.removeAttribute("data-autosave-initialized");
    activeAssessmentAutosave = null;
  }

  function initAssessmentAutosave(container) {
    if (!container) container = document;
    if (activeAssessmentAutosave && !activeAssessmentAutosave.form.isConnected) {
      destroyAssessmentAutosave();
    }
    var form = container.querySelector("#assessment-take-form");
    if (!form || (activeAssessmentAutosave && activeAssessmentAutosave.form === form)) return;
    if (activeAssessmentAutosave) destroyAssessmentAutosave();
    if (form.dataset.autosaveInitialized === "true") return;
    var autosaveUrl = form.getAttribute("data-autosave-url");
    var csrf = form.querySelector("input[name='csrfmiddlewaretoken']");
    var status = form.querySelector("#assessment-autosave-status");
    if (!autosaveUrl || !csrf || !status) return;
    form.dataset.autosaveInitialized = "true";
    var dirty = new Map();
    var hasBaseline = false;
    var state = {
      form: form,
      interval: null,
      pendingTimer: null,
      isSubmitting: false,
      onAnswerChange: null,
      onSubmit: null,
    };

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
      if (state.isSubmitting) return;
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
      window.clearTimeout(state.pendingTimer);
      state.pendingTimer = window.setTimeout(function () {
        state.pendingTimer = null;
        save();
      }, 1200);
    }

    function markDirty(event) {
      if (event.target && event.target.name) {
        dirty.set(event.target.name, (dirty.get(event.target.name) || 0) + 1);
      }
      scheduleSave();
    }
    state.onAnswerChange = markDirty;
    state.onSubmit = function () { state.isSubmitting = true; };
    state.interval = window.setInterval(save, 30000);
    form.addEventListener("change", state.onAnswerChange);
    form.addEventListener("input", state.onAnswerChange);
    form.addEventListener("submit", state.onSubmit);
    activeAssessmentAutosave = state;
  }

  document.addEventListener("DOMContentLoaded", function () { initAssessmentAutosave(); });
  document.addEventListener("htmx:after:swap", function (e) { initAssessmentAutosave(getHtmxSwapRoot(e)); });

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
  document.addEventListener("htmx:after:swap", function (e) { initIntakeChoices(getHtmxSwapRoot(e)); });

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
  var pendingSubmitter = null;
  var approvedSubmission = null;
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
    if (confirmBtn) {
      confirmBtn.textContent = label || confirmBtn.getAttribute("data-default-label") || "";
      confirmBtn.disabled = false;
    }
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
    if (confirmBtn) confirmBtn.disabled = false;
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
    pendingSubmitter = null;
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
        var formToSubmit = pendingForm;
        var submitterToSubmit = pendingSubmitter;
        if (confirmReasonRequired) {
          var reason = reasonInput ? reasonInput.value.trim() : "";
          if (!reason) {
            if (reasonError) reasonError.classList.remove("hidden");
            if (reasonInput) reasonInput.focus();
            return;
          }
          var reasonName = (submitterToSubmit && submitterToSubmit.getAttribute("data-confirm-reason-name"))
            || formToSubmit.getAttribute("data-confirm-reason-name")
            || "reason";
          var reasonField = Array.prototype.find.call(
            formToSubmit.querySelectorAll("input, textarea"),
            function (field) { return field.name === reasonName; }
          );
          if (reasonField) reasonField.value = reason;
        }
        confirmSubmitting = true;
        confirmBtn.disabled = true;
        if (submitterToSubmit && submitterToSubmit.form === formToSubmit) {
          // The first submit attempt ran the global guard before opening this
          // modal, so its original submitter may currently be disabled.
          submitterToSubmit.disabled = false;
        }
        hideConfirmModal(false);

        if (!formToSubmit.isConnected || typeof formToSubmit.requestSubmit !== "function") {
          showFeedback();
          return;
        }

        var submission = {
          form: formToSubmit,
          submitter: submitterToSubmit && submitterToSubmit.form === formToSubmit ? submitterToSubmit : null,
        };
        approvedSubmission = submission;
        try {
          if (submission.submitter) formToSubmit.requestSubmit(submission.submitter);
          else formToSubmit.requestSubmit();
        } catch (error) {
          console.error("Confirmed form submission could not resume its submit lifecycle.", error);
          showFeedback();
        } finally {
          // requestSubmit dispatches its submit event synchronously. If browser
          // validation blocks that event, discard this one-shot approval too.
          if (approvedSubmission === submission) approvedSubmission = null;
        }
        return;
      }
      hideConfirmModal(false);
    });
  }

  function questionBlockAnswered(block) {
    var controls = block.querySelectorAll("input[type='radio'], input[type='checkbox']");
    if (controls.length) {
      return Array.prototype.some.call(controls, function (control) { return control.checked; });
    }
    return Array.prototype.some.call(
      block.querySelectorAll("textarea, input:not([type='hidden']), select"),
      function (control) { return String(control.value || "").trim() !== ""; }
    );
  }

  // Show unanswered questions in the danger tone and keep the inline alert in
  // sync while the student answers them. Returns the still-unanswered blocks.
  function updateUnansweredHighlights(form, scrollIntoSight) {
    var blocks = Array.prototype.slice.call(form.querySelectorAll("[data-assessment-question]"));
    var unanswered = [];
    blocks.forEach(function (block) {
      var answered = questionBlockAnswered(block);
      block.classList.toggle("!border-red", !answered);
      block.classList.toggle("!bg-red-soft", !answered);
      if (!answered) unanswered.push(block);
    });

    var alert = form.querySelector("[data-unanswered-alert]");
    if (!unanswered.length) {
      if (alert) alert.remove();
      return [];
    }

    var numbers = unanswered.map(function (block) {
      return block.getAttribute("data-assessment-question") || "";
    }).filter(Boolean);

    if (!alert) {
      alert = document.createElement("div");
      alert.className = "alert-error space-y-1 mb-4";
      alert.setAttribute("data-unanswered-alert", "");
      alert.setAttribute("role", "alert");
      alert.setAttribute("tabindex", "-1");
      var title = document.createElement("p");
      title.className = "m-0 font-bold";
      title.setAttribute("data-unanswered-title", "");
      alert.appendChild(title);
      var body = document.createElement("p");
      body.className = "m-0";
      body.setAttribute("data-unanswered-body", "");
      alert.appendChild(body);

      var questionContainer = form.querySelector("#take-chunks");
      if (questionContainer && questionContainer.parentNode === form) {
        form.insertBefore(alert, questionContainer);
      } else {
        form.insertBefore(alert, form.firstChild);
      }
    }

    var title = alert.querySelector("[data-unanswered-title]");
    var prefix = form.getAttribute("data-unanswered-prefix") || "";
    if (title.textContent !== prefix) title.textContent = prefix;
    var detail = numbers.join(", ");
    var suffix = form.getAttribute("data-unanswered-suffix") || "";
    if (suffix) detail += ". " + suffix;
    var body = alert.querySelector("[data-unanswered-body]");
    if (body.textContent !== detail) body.textContent = detail;

    // Collapsed lecture groups must reveal their unanswered questions.
    unanswered.forEach(function (block) {
      var group = block.closest("details");
      if (group) group.open = true;
    });

    if (scrollIntoSight && alert.scrollIntoView) {
      alert.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    return unanswered;
  }

  function keepUnansweredHighlightsCurrent(e) {
    var form = e.target.closest && e.target.closest("form[data-confirm-unanswered]");
    if (!form || !form.querySelector("[data-unanswered-alert]")) return;
    updateUnansweredHighlights(form, false);
  }

  document.addEventListener("change", keepUnansweredHighlightsCurrent);
  document.addEventListener("input", keepUnansweredHighlightsCurrent);

  // Intercept form submits with data-confirm
  document.addEventListener("submit", function (e) {
    var form = e.target;
    var submitter = e.submitter || null;
    if (approvedSubmission && approvedSubmission.form === form && approvedSubmission.submitter === submitter) {
      approvedSubmission = null;
      // The global submit guard disables controls earlier in the event path.
      // Keep the actual submitter enabled so its name/value remains successful.
      if (submitter && submitter.form === form) submitter.disabled = false;
      return;
    }

    var msg = (submitter && submitter.getAttribute("data-confirm")) || form.getAttribute("data-confirm");
    if (!msg && form.hasAttribute("data-confirm-unanswered")) {
      var unansweredBlocks = updateUnansweredHighlights(form, true);
      if (unansweredBlocks.length) {
        e.preventDefault();
        pendingForm = form;
        pendingSubmitter = submitter;
        var unanswered = unansweredBlocks.map(function (block) {
          return block.getAttribute("data-assessment-question") || "";
        }).filter(Boolean);
        var prefix = form.getAttribute("data-unanswered-prefix") || "";
        var suffix = form.getAttribute("data-unanswered-suffix") || "";
        // Make the inline alert the focus-return target so canceling the
        // confirmation leaves the student at the warning, not at the submit
        // button below the questions.
        var warning = form.querySelector("[data-unanswered-alert]");
        if (warning && warning.focus) warning.focus({ preventScroll: true });
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
    pendingSubmitter = submitter;
    var defaultLabel = confirmBtn ? confirmBtn.getAttribute("data-default-label") : "";
    var submitterLabel = submitter && (
      submitter.getAttribute("data-confirm-label")
      || submitter.textContent?.trim()
      || submitter.value
    );
    var firstSubmitControl = form.querySelector("button[type='submit'], input[type='submit']");
    var label = form.getAttribute("data-confirm-label")
      || submitterLabel
      || firstSubmitControl?.textContent?.trim()
      || firstSubmitControl?.value
      || defaultLabel;
    var reasonRequired = form.getAttribute("data-confirm-reason-required") === "true"
      || (submitter && submitter.getAttribute("data-confirm-reason-required") === "true");
    showConfirmModal(msg, label, reasonRequired);
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
        pendingSubmitter = null;
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
  document.addEventListener("htmx:after:swap", function (e) { initProgramSelection(getHtmxSwapRoot(e)); });

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
  document.addEventListener("htmx:after:swap", function (e) { initStudentApplySelection(getHtmxSwapRoot(e)); });

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
  document.addEventListener("htmx:after:swap", function (e) { initRegistrationWizard(getHtmxSwapRoot(e)); });

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
  document.addEventListener("htmx:after:swap", function (e) { initDecisionTabs(getHtmxSwapRoot(e)); });

  // --- Lucide Icons (loaded via CDN) ---
  function initLucide() {
    if (typeof lucide !== "undefined") {
      lucide.createIcons();
    }
  }
  document.addEventListener("DOMContentLoaded", initLucide);
  document.addEventListener("htmx:after:settle", initLucide);

  // --- Alpine init for HTMX-inserted content ---
  // Inline x-data components (e.g. _expandable_text.html) must initialize when
  // HTMX swaps new fragments in, not only on initial page load (P8-01).
  document.addEventListener("htmx:after:swap", function (e) {
    var target = getHtmxSwapRoot(e);
    if (window.Alpine && target) window.Alpine.initTree(target);
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
  document.addEventListener("htmx:after:swap", syncContentNavigation);
  document.addEventListener("htmx:after:history:push", syncContentNavigation);

})();
