(function () {
  "use strict";

  var containers = document.querySelectorAll("[data-youtube-progress]");
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
    var interval = null;

    function showError(msg) {
      stopHeartbeat();
      if (violationEl) {
        violationEl.textContent = msg;
        var refreshButton = document.createElement("button");
        refreshButton.type = "button";
        refreshButton.className = "ml-2 underline font-medium";
        refreshButton.textContent = "Refresh";
        refreshButton.addEventListener("click", function () { location.reload(); });
        violationEl.appendChild(refreshButton);
        violationEl.classList.remove("hidden");
      }
      if (statusEl) statusEl.textContent = "Paused";
    }

    var ytPlayer = new YT.Player(container.querySelector(".yt-iframe"), {
      videoId: videoId,
      playerVars: { rel: 0, modestbranding: 1 },
      events: {
        onReady: function () {
          postJSON(startUrl, {}).then(function (res) {
            if (res.status === 200 && res.body.session_id) {
              sessionId = res.body.session_id;
              if (res.body.resume_position > 0) {
                ytPlayer.seekTo(res.body.resume_position, true);
              }
            } else {
              showError("Could not start tracking.");
            }
          });
        },
        onStateChange: function (e) {
          if (!sessionId) return;
          if (e.data === YT.PlayerState.PLAYING) {
            startHeartbeat();
          } else {
            stopHeartbeat();
          }
        },
      },
    });

    function startHeartbeat() {
      if (interval) return;
      interval = setInterval(sendCheckpoint, 5000);
    }

    function stopHeartbeat() {
      if (interval) { clearInterval(interval); interval = null; }
    }

    function sendCheckpoint() {
      if (!sessionId) return;
      var pos = ytPlayer.getCurrentTime();
      var rate = ytPlayer.getPlaybackRate();
      var dur = ytPlayer.getDuration() || 0;
      seq++;
      postJSON(checkpointUrl, {
        session_id: sessionId,
        sequence: seq,
        position: pos,
        player_state: "playing",
        rate: rate,
        duration: dur,
      }).then(function (res) {
        var b = res.body;
        if (res.status === 409 || b.error) {
          var msg = b.violation ? ("Violation: " + b.violation) : (b.error || "Access denied");
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
          statusEl.textContent = "Completed";
          statusEl.classList.add("text-success-text");
        }
      });
    }

    window.addEventListener("pagehide", function () {
      stopHeartbeat();
    });
  }
})();
