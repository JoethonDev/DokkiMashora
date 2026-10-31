(function () {
  "use strict";

  if (window.DokkiAssessmentTimer) return;

  var active = null;

  function findElement(root, selector) {
    if (!root || !root.isConnected) {
      var replacement = root && root.id ? document.getElementById(root.id) : null;
      root = replacement || document;
    }
    if (root.nodeType === 1 && root.matches(selector)) return root;
    return root.querySelector(selector);
  }

  function destroyActive() {
    if (!active) return;
    window.clearInterval(active.interval);
    document.removeEventListener("visibilitychange", active.visibilityHandler);
    active.timer.removeAttribute("data-initialized");
    active = null;
  }

  function initAssessmentTimer(root) {
    var timer = findElement(root, "#assessment-timer");
    if (!timer) {
      if (active && !active.timer.isConnected) destroyActive();
      return;
    }
    if (active && active.timer === timer) return;

    destroyActive();

    var value = timer.querySelector("#assessment-timer-value");
    var message = timer.querySelector("#assessment-timer-message");
    var Core = window.CountdownCore;
    if (!value || !Core) return;
    timer.dataset.initialized = "true";

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
      if (active && active.timer === timer) {
        window.clearInterval(active.interval);
        active.interval = null;
      }
      value.classList.add("text-red");
      timer.classList.add("border-red", "bg-red-soft");
      if (message) message.textContent = timer.dataset.expiredLabel || "Time expired.";
      var submit = document.querySelector("#assessment-take-form button[type='submit']");
      if (submit) submit.disabled = true;
    }

    var visibilityHandler = render;

    active = {
      timer: timer,
      interval: null,
      visibilityHandler: visibilityHandler,
    };
    active.interval = window.setInterval(render, 1000);
    document.addEventListener("visibilitychange", visibilityHandler);
    render();
  }

  window.DokkiAssessmentTimer = {
    init: initAssessmentTimer,
    destroy: destroyActive,
  };

  document.addEventListener("htmx:after:swap", function (event) {
    var ctx = event.detail && event.detail.ctx;
    initAssessmentTimer(ctx && ctx.target);
  });
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { initAssessmentTimer(document); });
  } else {
    initAssessmentTimer(document);
  }
}());
