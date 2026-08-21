(function () {
  "use strict";

  function initExceptionalAccessPicker() {
    var dialog = document.getElementById("exceptional-access-enrollment-dialog");
    var picker = document.getElementById("exceptional-access-enrollment-picker");
    var form = document.getElementById("exceptional-access-form");
    var selectedContainer = document.getElementById("exceptional-access-selected-enrollments");
    var openButton = document.getElementById("exceptional-access-open-picker");
    var closeButton = document.getElementById("exceptional-access-close-picker");
    var doneButton = document.getElementById("exceptional-access-done-picker");
    if (!dialog || !picker || !form || !selectedContainer || !openButton) return;
    if (dialog.dataset.initialized === "true") return;
    dialog.dataset.initialized = "true";

    var selected = new Map();
    var fieldName = picker.dataset.pickerField;

    function renderSelected() {
      form.querySelectorAll("[data-exceptional-access-hidden]").forEach(function (input) {
        input.remove();
      });
      selectedContainer.replaceChildren();
      if (!selected.size) {
        var empty = document.createElement("p");
        empty.className = "text-sm text-muted";
        empty.textContent = selectedContainer.dataset.emptyLabel || "No students selected yet.";
        selectedContainer.appendChild(empty);
        return;
      }

      var title = document.createElement("p");
      title.className = "text-sm font-bold";
      title.textContent = selected.size + " " + (selectedContainer.dataset.selectedLabel || "students selected");
      selectedContainer.appendChild(title);

      selected.forEach(function (label, id) {
        var row = document.createElement("div");
        row.className = "flex items-center justify-between gap-3 rounded-sm border border-line bg-surface-soft px-3 py-2";

        var hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.name = fieldName;
        hidden.value = id;
        hidden.dataset.exceptionalAccessHidden = "true";
        form.appendChild(hidden);

        var name = document.createElement("span");
        name.className = "min-w-0 truncate";
        name.textContent = label;
        row.appendChild(name);

        var remove = document.createElement("button");
        remove.type = "button";
        remove.className = "btn-ghost";
        remove.textContent = selectedContainer.dataset.removeLabel || "Remove";
        remove.dataset.removeEnrollmentId = id;
        row.appendChild(remove);
        selectedContainer.appendChild(row);
      });
    }

    function syncCheckboxes() {
      picker.querySelectorAll("[data-enrollment-picker-checkbox]").forEach(function (checkbox) {
        checkbox.checked = selected.has(checkbox.dataset.enrollmentId);
      });
    }

    function closeDialog() {
      if (dialog.open) dialog.close();
    }

    openButton.addEventListener("click", function () {
      dialog.showModal();
      syncCheckboxes();
      var search = picker.querySelector("input[name='q']");
      if (search) search.focus();
    });
    if (closeButton) closeButton.addEventListener("click", closeDialog);
    if (doneButton) doneButton.addEventListener("click", closeDialog);
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) closeDialog();
    });

    picker.addEventListener("change", function (event) {
      var checkbox = event.target.closest("[data-enrollment-picker-checkbox]");
      if (!checkbox) return;
      if (checkbox.checked) {
        selected.set(checkbox.dataset.enrollmentId, checkbox.dataset.enrollmentLabel);
      } else {
        selected.delete(checkbox.dataset.enrollmentId);
      }
      renderSelected();
    });
    selectedContainer.addEventListener("click", function (event) {
      var remove = event.target.closest("[data-remove-enrollment-id]");
      if (!remove) return;
      selected.delete(remove.dataset.removeEnrollmentId);
      renderSelected();
      syncCheckboxes();
    });
    document.body.addEventListener("htmx:afterSwap", function (event) {
      if (event.detail && event.detail.target === picker) syncCheckboxes();
    });

    renderSelected();
  }

  document.addEventListener("DOMContentLoaded", initExceptionalAccessPicker);
}());
