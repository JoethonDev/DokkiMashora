(function () {
  "use strict";

  function optionList(select, items, selectedValue, label) {
    if (!select) return;
    select.replaceChildren(new Option(label, ""));
    (items || []).forEach(function (item) {
      var option = new Option(item.label, item.value);
      if (String(item.value) === String(selectedValue || "")) option.selected = true;
      select.add(option);
    });
  }

  function initAssessmentForm(form) {
    if (!form || form.dataset.assessmentFormReady === "true") return;
    form.dataset.assessmentFormReady = "true";

    var scopeSelect = form.querySelector('[name="scope_type"]');
    var typeSelect = form.querySelector('[name="assessment_type"]');
    var diplomaSelect = form.querySelector('[name="diploma"]');
    var semesterSelect = form.querySelector('[name="semester"]');
    var cohortSelect = form.querySelector('[name="cohort"]');
    var courseSelect = form.querySelector('[name="course"]');
    var versionSelect = form.querySelector('[name="course_version"]');
    var courseFields = ["course", "course_version"];
    var diplomaFields = ["diploma", "semester", "cohort"];
    var selectLabel = form.dataset.selectLabel || "";

    function toggleScope(scope) {
      form.querySelectorAll(".scope-field").forEach(function (el) {
        var name = el.dataset.fieldName;
        if (courseFields.indexOf(name) !== -1) el.classList.toggle("hidden", scope !== "course");
        if (diplomaFields.indexOf(name) !== -1) el.classList.toggle("hidden", scope !== "diploma");
      });
    }

    function toggleHomeworkFields() {
      var isHomework = typeSelect && typeSelect.value === "homework";
      form.querySelectorAll("[data-homework-only]").forEach(function (el) {
        el.classList.toggle("hidden", !isHomework);
      });
    }

    function updateOptions(preserveValues) {
      var url = form.dataset.scopeOptionsUrl;
      if (!url) return;
      var params = new URLSearchParams({
        format: "json",
        scope_type: scopeSelect ? scopeSelect.value : "",
        diploma: diplomaSelect ? diplomaSelect.value : "",
        course: courseSelect ? courseSelect.value : "",
      });
      var selectedSemester = semesterSelect && semesterSelect.value;
      var selectedCohort = cohortSelect && cohortSelect.value;
      var selectedVersion = versionSelect && versionSelect.value;
      fetch(url + "?" + params.toString(), { headers: { Accept: "application/json" } })
        .then(function (response) {
          if (!response.ok) throw new Error("scope options request failed");
          return response.json();
        })
        .then(function (data) {
          optionList(semesterSelect, data.semesters, preserveValues ? selectedSemester : "", selectLabel);
          optionList(cohortSelect, data.cohorts, preserveValues ? selectedCohort : "", selectLabel);
          optionList(versionSelect, data.course_versions, preserveValues ? selectedVersion : "", selectLabel);
        })
        .catch(function () { /* Keep server-rendered choices when the optional refresh fails. */ });
    }

    if (scopeSelect) {
      scopeSelect.addEventListener("change", function () {
        toggleScope(scopeSelect.value);
        if (scopeSelect.value === "course" && diplomaSelect) {
          diplomaSelect.value = "";
          if (semesterSelect) semesterSelect.value = "";
          if (cohortSelect) cohortSelect.value = "";
        }
        if (scopeSelect.value === "diploma" && courseSelect) {
          courseSelect.value = "";
          if (versionSelect) versionSelect.value = "";
        }
        updateOptions(false);
      });
    }
    if (diplomaSelect) diplomaSelect.addEventListener("change", function () {
      if (semesterSelect) semesterSelect.value = "";
      if (cohortSelect) cohortSelect.value = "";
      updateOptions(false);
    });
    if (courseSelect) courseSelect.addEventListener("change", function () {
      if (versionSelect) versionSelect.value = "";
      updateOptions(false);
    });
    if (typeSelect) typeSelect.addEventListener("change", toggleHomeworkFields);

    toggleScope(scopeSelect ? scopeSelect.value : "");
    toggleHomeworkFields();
    updateOptions(true);
  }

  function initAll() {
    document.querySelectorAll("[data-assessment-form]").forEach(initAssessmentForm);
  }

  document.addEventListener("DOMContentLoaded", initAll);
  document.addEventListener("htmx:afterSwap", initAll);
}());
