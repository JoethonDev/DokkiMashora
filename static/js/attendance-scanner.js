(function () {
  "use strict";

  // Browser-camera attendance scanner (P7-01).
  // Camera states: request-camera -> ready -> scanning -> detected ->
  // submitting -> result/error -> ready-again. Never navigates; all outcomes
  // come from the authoritative server endpoint and are inserted as text only.
  var scanner = document.getElementById("attendance-scanner");
  if (!scanner) return;

  var video = scanner.querySelector("#scanner-video");
  var startButton = scanner.querySelector("[data-scanner-start]");
  var retryButton = scanner.querySelector("[data-scanner-retry]");
  var statusEl = scanner.querySelector("[data-scanner-status]");
  var resultEl = scanner.querySelector("[data-scanner-result]");
  var sessionSelect = scanner.querySelector("[data-scanner-session]");
  var scanApiUrl = scanner.dataset.scanApiUrl;
  var messages = {
    ready: scanner.dataset.msgReady,
    libraryMissing: scanner.dataset.msgLibraryMissing,
    requesting: scanner.dataset.msgRequesting,
    cameraDenied: scanner.dataset.msgCameraDenied,
    submitting: scanner.dataset.msgSubmitting,
    recorded: scanner.dataset.msgRecorded,
    attendanceRecorded: scanner.dataset.msgAttendanceRecorded,
    duplicate: scanner.dataset.msgDuplicate,
    alreadyRecorded: scanner.dataset.msgAlreadyRecorded,
    rejected: scanner.dataset.msgRejected,
    tryAgain: scanner.dataset.msgTryAgain,
    networkError: scanner.dataset.msgNetworkError,
  };

  var html5Qr = null;
  var scanning = false;
  var submitting = false;

  function setStatus(message, tone) {
    if (!statusEl) return;
    statusEl.textContent = message;
    statusEl.className = "text-sm mt-2";
    if (tone === "error") statusEl.classList.add("text-red");
    else if (tone === "success") statusEl.classList.add("text-success-text");
    else statusEl.classList.add("text-muted");
  }

  function setResult(message, tone) {
    if (!resultEl) return;
    resultEl.textContent = message;
    resultEl.className = "status-pill";
    if (tone === "success") resultEl.classList.add("status-pill-success");
    else if (tone === "warning") resultEl.classList.add("status-pill-warning");
    else resultEl.classList.add("status-pill-danger");
  }

  function getCsrf() {
    var el = document.querySelector('[name="csrfmiddlewaretoken"]');
    return el ? el.value : "";
  }

  function clearScan() {
    if (html5Qr && scanning) {
      scanning = false;
      try { return html5Qr.stop().catch(function () {}); } catch (e) { return Promise.resolve(); }
    }
    scanning = false;
    return Promise.resolve();
  }

  function resetToReady() {
    clearScan().then(function () {
      setStatus(messages.ready, "muted");
      startScanner();
    });
  }

  function startScanner() {
    if (scanning || submitting) return;
    if (!window.Html5Qrcode) {
      setStatus(messages.libraryMissing, "error");
      return;
    }
    scanning = true;
    if (!html5Qr) {
      html5Qr = new Html5Qrcode("scanner-video");
    }
    setStatus(messages.requesting, "muted");
    html5Qr.start(
      { facingMode: "environment" },
      { fps: 10, qrbox: { width: 220, height: 220 } },
      function onDetected(decodedText) {
        if (submitting) return;
        submitting = true;
        clearScan().then(function () { submitPayload(decodedText); });
      },
      function onError() {
        // Decode errors are expected between QR detections; keep scanning.
      }
    ).catch(function (err) {
      scanning = false;
      setStatus(messages.cameraDenied, "error");
      if (retryButton) retryButton.classList.remove("hidden");
    });
  }

  function submitPayload(payload) {
    submitting = true;
    setStatus(messages.submitting, "muted");
    var sessionId = sessionSelect ? sessionSelect.value : "";
    var form = new FormData();
    form.append("session_id", sessionId);
    form.append("qr_payload", payload);
    fetch(scanApiUrl, {
      method: "POST",
      headers: { "X-CSRFToken": getCsrf() },
      body: form,
      credentials: "same-origin",
    })
      .then(function (r) {
        return r.json().then(function (body) { return { status: r.status, body: body }; });
      })
      .then(function (res) {
        var body = res.body || {};
        if (body.code === "recorded") {
          setResult(messages.recorded + ": " + (body.student_name || ""), "success");
          setStatus(messages.attendanceRecorded, "success");
        } else if (body.code === "duplicate") {
          setResult(messages.duplicate + ": " + (body.student_name || ""), "warning");
          setStatus(messages.alreadyRecorded, "muted");
        } else {
          setResult((body.message || messages.rejected), "error");
          setStatus(messages.tryAgain, "error");
        }
        submitting = false;
        resetToReady();
      })
      .catch(function () {
        submitting = false;
        setResult(messages.networkError, "error");
        setStatus(messages.tryAgain, "error");
        resetToReady();
      });
  }

  if (startButton) {
    startButton.addEventListener("click", function (e) {
      e.preventDefault();
      if (retryButton) retryButton.classList.add("hidden");
      startScanner();
    });
  }
  if (retryButton) {
    retryButton.addEventListener("click", function (e) {
      e.preventDefault();
      retryButton.classList.add("hidden");
      startScanner();
    });
  }

  window.addEventListener("pagehide", clearScan);
})();
