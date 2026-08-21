(function () {
  "use strict";

  // Server-authoritative audio playback progress (P5-01).
  // Completion is computed ONLY from server-validated checkpoints against the
  // persisted transcode duration; the browser can never manufacture progress.
  var containers = document.querySelectorAll("[data-audio-progress]");
  if (!containers.length) return;

  function getCsrf() {
    var c = document.cookie.match(/csrftoken=([^;]+)/);
    return c ? c[1] : "";
  }

  function postJSON(url, data) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrf() },
      body: JSON.stringify(data),
      credentials: "same-origin",
    }).then(function (r) {
      return r.json().then(function (b) { return { status: r.status, body: b }; });
    });
  }

  containers.forEach(function (container) {
    var startUrl = container.dataset.audioStartUrl;
    var checkpointUrl = container.dataset.audioCheckpointUrl;
    var audio = container.querySelector("audio");
    if (!audio) return;

    // Translated strings come from template data attributes so no
    // user-facing text is hardcoded in JavaScript.
    var labels = {
      refresh: container.dataset.msgRefresh || "",
      paused: container.dataset.msgPaused || "",
      playbackError: container.dataset.msgPlaybackError || "",
      violationPrefix: container.dataset.msgViolationPrefix || "",
      accessDenied: container.dataset.msgAccessDenied || "",
      startFailed: container.dataset.msgStartFailed || "",
      completed: container.dataset.msgCompleted || "",
    };

    var progressEl = container.querySelector(".audio-progress-bar");
    var progressText = container.querySelector(".audio-progress-text");
    var statusEl = container.querySelector(".audio-status");
    var violationEl = container.querySelector(".audio-violation");

    var sessionId = null;
    var seq = 0;
    var interval = null;

    function showError(msg) {
      stopHeartbeat();
      if (violationEl) {
        violationEl.textContent = msg;
        var refreshButton = document.createElement("button");
        refreshButton.type = "button";
        refreshButton.className = "ml-2 underline font-medium";
        refreshButton.textContent = labels.refresh;
        refreshButton.addEventListener("click", function () { location.reload(); });
        violationEl.appendChild(refreshButton);
        violationEl.classList.remove("hidden");
      }
      if (statusEl) statusEl.textContent = labels.paused;
    }

    audio.addEventListener("play", function () {
      if (!sessionId) {
        postJSON(startUrl, {}).then(function (res) {
          if (res.status === 200 && res.body.session_id) {
            sessionId = res.body.session_id;
            if (res.body.resume_position > 0 && audio.currentTime < 1) {
              audio.currentTime = res.body.resume_position;
            }
            startHeartbeat();
          } else {
            showError(labels.startFailed);
          }
        });
        return;
      }
      startHeartbeat();
    });

    audio.addEventListener("pause", stopHeartbeat);
    audio.addEventListener("ended", stopHeartbeat);
    audio.addEventListener("error", function () {
      stopHeartbeat();
      if (statusEl) statusEl.textContent = labels.playbackError;
    });

    function startHeartbeat() {
      if (interval) return;
      interval = setInterval(sendCheckpoint, 5000);
    }

    function stopHeartbeat() {
      if (interval) { clearInterval(interval); interval = null; }
    }

    function sendCheckpoint() {
      if (!sessionId || !audio.duration) return;
      var pos = audio.currentTime;
      var rate = audio.playbackRate || 1;
      seq++;
      postJSON(checkpointUrl, {
        session_id: sessionId,
        sequence: seq,
        position: pos,
        player_state: "playing",
        rate: rate,
      }).then(function (res) {
        var b = res.body;
        if (res.status === 409 || b.error) {
          var msg = b.violation ? (labels.violationPrefix + ": " + b.violation) : (b.error || labels.accessDenied);
          showError(msg);
          return;
        }
        if (b.progress !== undefined && progressEl) {
          progressEl.style.setProperty("--progress", Math.min(b.progress, 100) + "%");
        }
        if (b.progress !== undefined && progressText) {
          progressText.textContent = Math.round(b.progress) + "%";
        }
        if (b.status === "completed" && statusEl) {
          statusEl.textContent = labels.completed;
          statusEl.classList.add("text-success-text");
        }
      });
    }

    window.addEventListener("pagehide", function () {
      stopHeartbeat();
    });
  });
})();
