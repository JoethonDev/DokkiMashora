(function () {
  "use strict";

  var containers = document.querySelectorAll("[data-youtube-progress]");
  if (!containers.length) return;

  var Core = window.MediaProgressCore;

  // Set callback BEFORE loading the API script
  window.onYouTubeIframeAPIReady = function () {
    containers.forEach(initPlayer);
  };

  var tag = document.createElement("script");
  tag.src = "https://www.youtube.com/iframe_api";
  document.head.appendChild(tag);

  function initPlayer(container) {
    var videoId = container.dataset.youtubeProgress;
    var startUrl = container.dataset.watchStartUrl;
    var checkpointUrl = container.dataset.watchCheckpointUrl;

    var progressEl = container.querySelector(".yt-progress-bar");
    var progressText = container.querySelector(".yt-progress-text");
    var statusEl = container.querySelector(".yt-status");
    var violationEl = container.querySelector(".yt-violation");

    var sessionId = null;
    var seq = 0;

    var labels = {
      refresh: container.dataset.msgRefresh || "",
      paused: container.dataset.msgPaused || "",
      violationPrefix: container.dataset.msgViolationPrefix || "",
      accessDenied: container.dataset.msgAccessDenied || "",
      completed: container.dataset.msgCompleted || "",
      startFailed: container.dataset.msgStartFailed || "",
      tryAgain: container.dataset.msgTryAgain || "",
    };

    var elements = {
      progressEl: progressEl,
      progressText: progressText,
      statusEl: statusEl,
      violationEl: violationEl,
    };

    var heartbeat = Core.createHeartbeat(sendCheckpoint);
    var showError = Core.createErrorDisplay(elements, labels, heartbeat);
    var inFlight = false;
    var lastSentAt = 0;
    var blocked = false;

    function startHeartbeat() { heartbeat.start(); }
    function stopHeartbeat() { heartbeat.stop(); }

    var ytPlayer = new YT.Player(container.querySelector(".yt-iframe"), {
      videoId: videoId,
      playerVars: { rel: 0, modestbranding: 1 },
      events: {
        onReady: function () {
          Core.postJSON(startUrl, {}).then(function (res) {
            if (res.status === 200 && res.body.session_id) {
              sessionId = res.body.session_id;
              if (res.body.resume_position > 0) {
                ytPlayer.seekTo(res.body.resume_position, true);
              }
            } else {
              showError(labels.startFailed);
            }
          });
        },
        onStateChange: function (e) {
          if (!sessionId) return;
          if (e.data === YT.PlayerState.PLAYING) {
            startHeartbeat();
          } else {
            stopHeartbeat();
            if (e.data === YT.PlayerState.PAUSED || e.data === YT.PlayerState.ENDED) {
              sendFinalCheckpoint();
            }
          }
        },
      },
    });

    function sendCheckpoint(options) {
      if (!sessionId || inFlight || blocked) return;
      var pos = ytPlayer.getCurrentTime();
      var rate = ytPlayer.getPlaybackRate();
      var dur = ytPlayer.getDuration() || 0;
      seq++;
      inFlight = true;
      lastSentAt = Date.now();
      Core.postJSON(checkpointUrl, {
        session_id: sessionId,
        sequence: seq,
        position: pos,
        player_state: (options && options.keepalive) ? "paused" : "playing",
        rate: rate,
        duration: dur,
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
  }
})();
