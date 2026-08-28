(function () {
  "use strict";

  function initAssessmentTimer() {
    var timer = document.getElementById("assessment-timer");
    if (!timer || timer.dataset.initialized === "true") return;
    var value = document.getElementById("assessment-timer-value");
    var message = document.getElementById("assessment-timer-message");
    if (!value) return;
    timer.dataset.initialized = "true";

    var Core = window.CountdownCore;

    var initialSeconds = Number(timer.dataset.remainingSeconds || 0);
    var serverNow = Date.parse(timer.dataset.serverNow || "");
    var deadline = Date.parse(timer.dataset.deadline || "");
    var loadedAt = Date.now();
    var clockOffset = Core.computeClockOffset(serverNow);
    var expired = false;

    function render() {
      var seconds = Core.getRemainingSeconds({
        deadline: deadline,
        clockOffset: clockOffset,
        initialSeconds: initialSeconds,
        loadedAt: loadedAt,
        serverNow: serverNow,
        requireServerNow: true,
      });
      value.textContent = Core.formatAdaptive(seconds);
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
