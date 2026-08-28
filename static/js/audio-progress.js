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
    };

    var progressEl = container.querySelector(".audio-progress-bar");
    var progressText = container.querySelector(".audio-progress-text");
    var statusEl = container.querySelector(".audio-status");
    var violationEl = container.querySelector(".audio-violation");

    var sessionId = null;
    var seq = 0;

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

    audio.addEventListener("pause", stopHeartbeat);
    audio.addEventListener("ended", stopHeartbeat);
    audio.addEventListener("error", function () {
      stopHeartbeat();
      if (statusEl) statusEl.textContent = labels.playbackError;
    });

    function sendCheckpoint() {
      if (!sessionId || !audio.duration) return;
      var pos = audio.currentTime;
      var rate = audio.playbackRate || 1;
      seq++;
      Core.postJSON(checkpointUrl, {
        session_id: sessionId,
        sequence: seq,
        position: pos,
        player_state: "playing",
        rate: rate,
      }).then(function (res) {
        Core.handleCheckpointResponse(res, elements, labels, heartbeat, showError);
      });
    }

    window.addEventListener("pagehide", function () {
      stopHeartbeat();
    });
  });
})();
