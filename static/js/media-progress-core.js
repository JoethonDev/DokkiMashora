(function () {
  "use strict";

  function getCsrf() {
    var c = document.cookie.match(/csrftoken=([^;]+)/);
    return c ? c[1] : "";
  }

  function postJSON(url, data, options) {
    options = options || {};
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrf() },
      body: JSON.stringify(data),
      credentials: "same-origin",
      keepalive: !!options.keepalive,
    }).then(function (r) {
      return r.json().then(function (b) { return { status: r.status, body: b }; });
    });
  }

  function createHeartbeat(sendFn) {
    var interval = null;
    function start() {
      if (interval) return;
      interval = setInterval(sendFn, 5000);
    }
    function stop() {
      if (interval) { clearInterval(interval); interval = null; }
    }
    return { start: start, stop: stop, getInterval: function () { return interval; } };
  }

  function formatViolationMessage(body, labels) {
    if (body.message) return body.message;
    if (body.violation) {
      var prefix = (labels && labels.violationPrefix) ? labels.violationPrefix : "";
      var message = prefix + ": " + body.violation;
      if (labels && labels.tryAgain) message += ". " + labels.tryAgain;
      return message;
    }
    return body.error || ((labels && labels.accessDenied) ? labels.accessDenied : "");
  }

  function isViolationResponse(res) {
    return res.status === 409 || !!(res.body && res.body.error);
  }

  function showErrorToast(message, labels) {
    if (typeof window.CustomEvent !== "function") return false;
    window.dispatchEvent(new CustomEvent("show-toast", {
      detail: {
        message: message,
        type: "error",
        sticky: true,
        actionLabel: (labels && labels.refresh) ? labels.refresh : "",
        action: "reload",
      },
    }));
    return true;
  }

  function updateProgressUI(body, elements, labels) {
    var progressEl = elements.progressEl;
    var progressText = elements.progressText;
    var statusEl = elements.statusEl;
    if (body.progress !== undefined && progressEl) {
      progressEl.style.setProperty("--progress", Math.min(body.progress, 100) + "%");
    }
    if (body.progress !== undefined && progressText) {
      progressText.textContent = Math.round(body.progress) + "%";
    }
    if (body.status === "completed" && statusEl) {
      statusEl.textContent = (labels && labels.completed) ? labels.completed : "";
      statusEl.classList.add("text-success-text");
    }
  }

  function createErrorDisplay(elements, labels, heartbeat) {
    return function showError(msg) {
      if (heartbeat) heartbeat.stop();
      var statusEl = elements.statusEl;
      if (statusEl) {
        statusEl.textContent = (labels && labels.paused) ? labels.paused : "";
      }
      if (showErrorToast(msg, labels)) return;
      // Fallback for pages without the shared toast region.
      var violationEl = elements.violationEl;
      if (violationEl) {
        violationEl.textContent = msg;
        var refreshButton = document.createElement("button");
        refreshButton.type = "button";
        refreshButton.className = "ms-2 underline font-medium";
        refreshButton.textContent = (labels && labels.refresh) ? labels.refresh : "";
        refreshButton.addEventListener("click", function () { location.reload(); });
        violationEl.appendChild(refreshButton);
        violationEl.classList.remove("hidden");
      }
    };
  }

  function handleCheckpointResponse(res, elements, labels, heartbeat, showError) {
    var body = res.body;
    if (isViolationResponse(res)) {
      var msg = formatViolationMessage(body, labels);
      showError(msg);
      return true;
    }
    updateProgressUI(body, elements, labels);
    return false;
  }

  window.MediaProgressCore = {
    getCsrf: getCsrf,
    postJSON: postJSON,
    createHeartbeat: createHeartbeat,
    formatViolationMessage: formatViolationMessage,
    isViolationResponse: isViolationResponse,
    updateProgressUI: updateProgressUI,
    createErrorDisplay: createErrorDisplay,
    handleCheckpointResponse: handleCheckpointResponse,
  };
})();
