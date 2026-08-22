(function () {
  "use strict";

  // Entrance exam countdown + answer recovery.
  // The server provides the authoritative deadline on page load; the browser
  // counts down locally and never polls. Recovery priority: localStorage,
  // backend snapshot (server-rendered), empty state. At zero the client stops
  // editing and calls the idempotent finalization endpoint once.

  function initExamTimer() {
    var form = document.getElementById("exam-take-form");
    if (!form || form.dataset.examTimerInitialized === "true") return;
    var timer = document.getElementById("exam-timer");
    var value = document.getElementById("exam-timer-value");
    var message = document.getElementById("exam-timer-message");
    if (!timer || !value) return;
    form.dataset.examTimerInitialized = "true";

    var snapshotUrl = form.getAttribute("data-snapshot-url");
    var finalizeUrl = form.getAttribute("data-finalize-url");
    var attemptId = form.getAttribute("data-attempt-id");
    var examVersion = form.getAttribute("data-exam-version");
    var csrf = form.querySelector("input[name='csrfmiddlewaretoken']");
    var storageKey = "entrance-exam:" + attemptId + ":" + examVersion;
    var expiredLabel = timer.getAttribute("data-expired-label") || "Time expired.";
    var serverNow = Date.parse(timer.getAttribute("data-server-now") || "");
    var deadline = Date.parse(timer.getAttribute("data-deadline") || "");
    var initialSeconds = Number(timer.getAttribute("data-remaining-seconds") || 0);
    var loadedAt = Date.now();
    var clockOffset = Number.isFinite(serverNow) ? Date.now() - serverNow : 0;
    var expired = false;
    var finalizing = false;
    var saveTimer = null;
    var saveInFlight = false;
    var periodicTimer = null;

    function pad(number) {
      return String(number).padStart(2, "0");
    }

    function getRemainingSeconds() {
      if (Number.isFinite(deadline)) {
        return Math.max(0, Math.ceil((deadline + clockOffset - Date.now()) / 1000));
      }
      return Math.max(0, initialSeconds - Math.floor((Date.now() - loadedAt) / 1000));
    }

    function format(seconds) {
      var hours = Math.floor(seconds / 3600);
      var minutes = Math.floor((seconds % 3600) / 60);
      var remainder = seconds % 60;
      return pad(hours) + ":" + pad(minutes) + ":" + pad(remainder);
    }

    function collectAnswers() {
      var answers = {};
      var fields = form.querySelectorAll("[name^='question_']");
      Array.prototype.forEach.call(fields, function (el) {
        var qid = el.name.replace("question_", "");
        if (el.type === "radio") {
          if (el.checked) answers[qid] = { choice: el.value };
        } else if (el.tagName === "TEXTAREA") {
          answers[qid] = { text: el.value };
        }
      });
      return answers;
    }

    function readLocalStorage() {
      try {
        var raw = window.localStorage.getItem(storageKey);
        return raw ? JSON.parse(raw) : null;
      } catch (e) {
        return null;
      }
    }

    function persistLocalStorage() {
      try {
        window.localStorage.setItem(storageKey, JSON.stringify(collectAnswers()));
      } catch (e) {
        /* storage unavailable — the backend snapshot remains the fallback */
      }
    }

    function hydrateFrom(data) {
      if (!data) return;
      Object.keys(data).forEach(function (qid) {
        var entry = data[qid];
        if (!entry || typeof entry !== "object") return;
        if (entry.choice) {
          var radio = form.querySelector('input[name="question_' + qid + '"][value="' + entry.choice + '"]');
          if (radio) radio.checked = true;
        }
        if (typeof entry.text === "string") {
          var area = form.querySelector('textarea[name="question_' + qid + '"]');
          if (area) area.value = entry.text;
        }
      });
    }

    function saveSnapshot() {
      if (expired || saveInFlight || !snapshotUrl || !csrf) return;
      saveInFlight = true;
      fetch(snapshotUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrf.value,
        },
        body: JSON.stringify({ answers: collectAnswers() }),
        credentials: "same-origin",
      }).catch(function () { /* best-effort autosave; failures are ignored */ })
        .then(function () { saveInFlight = false; });
    }

    function scheduleSnapshot() {
      window.clearTimeout(saveTimer);
      saveTimer = window.setTimeout(saveSnapshot, 2000);
    }

    function onAnswerChange() {
      persistLocalStorage();
      scheduleSnapshot();
    }

    function disableEditing() {
      var controls = form.querySelectorAll("input, textarea, select, button");
      Array.prototype.forEach.call(controls, function (el) { el.disabled = true; });
    }

    function showExpired() {
      timer.classList.add("border-red", "bg-red-soft");
      value.classList.add("text-red");
      if (message) message.textContent = expiredLabel;
    }

    function finalizeExpired() {
      if (finalizing || !finalizeUrl || !csrf) {
        showExpired();
        return;
      }
      finalizing = true;
      fetch(finalizeUrl, {
        method: "POST",
        headers: { "X-CSRFToken": csrf.value },
        credentials: "same-origin",
      }).then(function (response) {
        return response.json().catch(function () { return {}; });
      }).then(function (body) {
        if (body && body.ok && body.redirect) {
          window.location.assign(body.redirect);
          return;
        }
        // Not finalized server-side yet (clock skew / network): reload the
        // refresh-safe canonical URL to re-sync the authoritative deadline.
        // If truly expired, the reloaded page finalizes immediately; if already
        // terminal, it redirects to the results page.
        window.location.reload();
      }).catch(function () {
        window.location.reload();
      });
    }

    function expire() {
      if (expired) return;
      expired = true;
      window.clearTimeout(saveTimer);
      window.clearInterval(periodicTimer);
      disableEditing();
      showExpired();
      finalizeExpired();
    }

    function render() {
      var seconds = getRemainingSeconds();
      value.textContent = format(seconds);
      if (seconds > 0 || expired) return;
      expire();
    }

    // Recovery priority 1: localStorage. Priority 2 is the server-rendered
    // snapshot already present in the DOM, so only override when local data
    // is available and valid.
    var local = readLocalStorage();
    if (local && Object.keys(local).length) hydrateFrom(local);

    form.addEventListener("change", onAnswerChange);
    form.addEventListener("input", onAnswerChange);

    render();
    window.setInterval(render, 1000);
    periodicTimer = window.setInterval(saveSnapshot, 60000);
    document.addEventListener("visibilitychange", render);
  }

  document.addEventListener("DOMContentLoaded", initExamTimer);
}());