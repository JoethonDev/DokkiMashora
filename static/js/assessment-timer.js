(function () {
  "use strict";

  function initAssessmentTimer() {
    var timer = document.getElementById("assessment-timer");
    if (!timer || timer.dataset.initialized === "true") return;
    var value = document.getElementById("assessment-timer-value");
    var message = document.getElementById("assessment-timer-message");
    if (!value) return;
    timer.dataset.initialized = "true";

    var initialSeconds = Number(timer.dataset.remainingSeconds || 0);
    var serverNow = Date.parse(timer.dataset.serverNow || "");
    var deadline = Date.parse(timer.dataset.deadline || "");
    var loadedAt = Date.now();
    var clockOffset = Number.isFinite(serverNow) ? Date.now() - serverNow : 0;
    var expired = false;

    function pad(number) {
      return String(number).padStart(2, "0");
    }

    function getRemainingSeconds() {
      if (Number.isFinite(serverNow) && Number.isFinite(deadline)) {
        return Math.max(0, Math.ceil((deadline + clockOffset - Date.now()) / 1000));
      }
      return Math.max(0, initialSeconds - Math.floor((Date.now() - loadedAt) / 1000));
    }

    function format(seconds) {
      var hours = Math.floor(seconds / 3600);
      var minutes = Math.floor((seconds % 3600) / 60);
      var remainder = seconds % 60;
      return hours > 0
        ? pad(hours) + ":" + pad(minutes) + ":" + pad(remainder)
        : pad(minutes) + ":" + pad(remainder);
    }

    function render() {
      var seconds = getRemainingSeconds();
      value.textContent = format(seconds);
      if (seconds > 0 || expired) return;
      expired = true;
      value.classList.add("text-red");
      timer.classList.add("border-red", "bg-red-soft");
      if (message) message.textContent = timer.dataset.expiredLabel || "Time expired.";
      var submit = document.querySelector("#assessment-take-form button[type='submit']");
      if (submit) submit.disabled = true;
    }

    render();
    window.setInterval(render, 1000);
    document.addEventListener("visibilitychange", render);
  }

  document.addEventListener("DOMContentLoaded", initAssessmentTimer);
}());
