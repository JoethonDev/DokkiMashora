(function () {
  "use strict";

  // Server-authoritative audio playback progress (P5-01).
  // Completion is computed ONLY from server-validated checkpoints against the
  // persisted transcode duration; the browser can never manufacture progress.
  var containers = document.querySelectorAll("[data-audio-progress]");
  if (!containers.length) return;

  var Core = window.MediaProgressCore;

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
      tryAgain: container.dataset.msgTryAgain || "",
    };

    var progressEl = container.querySelector(".audio-progress-bar");
    var progressText = container.querySelector(".audio-progress-text");
    var statusEl = container.querySelector(".audio-status");
    var violationEl = container.querySelector(".audio-violation");

    var sessionId = null;
    var seq = 0;
    var inFlight = false;
    var lastSentAt = 0;
    var blocked = false;
    var resumeAt = parseFloat(container.dataset.audioResume || "0");

    var elements = {
      progressEl: progressEl,
      progressText: progressText,
      statusEl: statusEl,
      violationEl: violationEl,
    };

    var heartbeat = Core.createHeartbeat(sendCheckpoint);
    var showError = Core.createErrorDisplay(elements, labels, heartbeat);

    function startHeartbeat() { heartbeat.start(); }
    function stopHeartbeat() { heartbeat.stop(); }

    function applyResume() {
      if (!isFinite(resumeAt) || resumeAt <= 0) return;
      if (audio.currentTime >= 1) return;
      var max = audio.duration || 0;
      audio.currentTime = max > 0 ? Math.min(resumeAt, Math.max(max - 1, 0)) : resumeAt;
    }

    if (audio.readyState >= 1) {
      applyResume();
    } else {
      audio.addEventListener("loadedmetadata", applyResume, { once: true });
    }

    audio.addEventListener("play", function () {
      if (!sessionId) {
        Core.postJSON(startUrl, {}).then(function (res) {
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

    audio.addEventListener("pause", function () {
      stopHeartbeat();
      sendFinalCheckpoint();
    });
    audio.addEventListener("ended", function () {
      stopHeartbeat();
      sendFinalCheckpoint();
    });
    audio.addEventListener("error", function () {
      stopHeartbeat();
      if (statusEl) statusEl.textContent = labels.playbackError;
    });

    function sendCheckpoint(options) {
      if (!sessionId || !audio.duration || inFlight || blocked) return;
      var pos = audio.currentTime;
      var rate = audio.playbackRate || 1;
      seq++;
      inFlight = true;
      lastSentAt = Date.now();
      Core.postJSON(checkpointUrl, {
        session_id: sessionId,
        sequence: seq,
        position: pos,
        player_state: (options && options.keepalive) ? "paused" : "playing",
        rate: rate,
      }, { keepalive: !!(options && options.keepalive) }).then(function (res) {
        if (Core.handleCheckpointResponse(res, elements, labels, heartbeat, showError)) {
          blocked = true;
        }
      }).catch(function () {
        // Leave the last saved position in place; the next heartbeat retries.
      }).then(function () {
        inFlight = false;
      });
    }

    function sendFinalCheckpoint() {
      if (!sessionId || inFlight || blocked || Date.now() - lastSentAt < 3000) return;
      sendCheckpoint({ keepalive: true });
    }

    window.addEventListener("pagehide", function () {
      sendFinalCheckpoint();
      stopHeartbeat();
    });
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden") sendFinalCheckpoint();
    });
  });
})();
