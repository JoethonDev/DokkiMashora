(function () {
  "use strict";

  // Certificate placement editor (P7-03).
  // Loads protected preview dimensions, lets Admin place/move Student Name,
  // and submits a normalized mapping to the validated server endpoint.
  // Coordinates are stored as fractions of the preview so they survive scale.
  var editor = document.getElementById("certificate-editor");
  if (!editor) return;

  var preview = document.getElementById("editor-preview");
  var fieldBox = document.getElementById("editor-field");
  var pageSelect = document.getElementById("editor-page");
  var fontSizeInput = document.getElementById("editor-font-size");
  var alignSelect = document.getElementById("editor-align");
  var directionSelect = document.getElementById("editor-direction");
  var saveButton = editor.querySelector("[data-editor-save]");
  var generateButton = editor.querySelector("[data-editor-generate]");
  var statusEl = editor.querySelector("[data-editor-status]");
  var resultWrap = editor.querySelector("[data-editor-result]");
  var resultLink = editor.querySelector("[data-editor-result-link]");

  var saveUrl = editor.dataset.saveUrl;
  var generateUrl = editor.dataset.generateUrl;
  var pageCount = parseInt(editor.dataset.pageCount || "1", 10);
  // Translated status strings come from template data attributes so no
  // user-facing text is hardcoded in JavaScript. The attributes are always
  // rendered by the certificate_editor.html template.
  var statusText = {
    saving: editor.dataset.msgSaving || "…",
    saved: editor.dataset.msgSaved || "…",
    saveFailed: editor.dataset.msgSaveFailed || "…",
    generating: editor.dataset.msgGenerating || "…",
    generated: editor.dataset.msgGenerated || "…",
    generateFailed: editor.dataset.msgGenerateFailed || "…",
    networkError: editor.dataset.msgNetworkError || "…",
    placementLabel: editor.dataset.msgPlacementLabel || "…",
  };
  var currentMapping = {};
  try {
    var mappingScript = document.getElementById("certificate-current-mapping");
    currentMapping = JSON.parse(mappingScript ? mappingScript.textContent : "{}");
  } catch (e) { currentMapping = {}; }

  function getCsrf() {
    var el = document.querySelector('[name="csrfmiddlewaretoken"]');
    return el ? el.value : "";
  }

  function setStatus(msg, tone) {
    if (!statusEl) return;
    statusEl.textContent = msg;
    statusEl.className = "text-sm mt-2";
    if (tone === "error") statusEl.classList.add("text-red");
    else if (tone === "success") statusEl.classList.add("text-success-text");
    else statusEl.classList.add("text-muted");
  }

  // Populate page selector
  for (var i = 1; i <= pageCount; i++) {
    var opt = document.createElement("option");
    opt.value = String(i);
    opt.textContent = i;
    pageSelect.appendChild(opt);
  }

  function loadCurrentPlacement() {
    var fields = (currentMapping.fields || {}).student_name;
    if (!fields) {
      positionBox(0.5, 0.5);
      applyBoxStyle();
      return;
    }
    if (pageSelect) pageSelect.value = String(fields.page || 1);
    if (fontSizeInput) fontSizeInput.value = String(fields.font_size || 28);
    if (alignSelect) alignSelect.value = fields.align || "center";
    if (directionSelect) directionSelect.value = fields.direction || "rtl";
    positionBox(fields.x || 0.5, fields.y || 0.5);
    applyBoxStyle();
  }

  function positionBox(xFraction, yFraction) {
    if (!preview || !fieldBox) return;
    fieldBox.style.left = (xFraction * 100) + "%";
    fieldBox.style.top = (yFraction * 100) + "%";
    fieldBox.style.transform = "translate(-50%, -50%)";
    fieldBox.style.display = "block";
  }

  function applyBoxStyle() {
    if (!fieldBox || !fontSizeInput) return;
    fieldBox.style.fontSize = fontSizeInput.value + "px";
    fieldBox.style.textAlign = alignSelect ? alignSelect.value : "center";
    fieldBox.style.direction = directionSelect ? directionSelect.value : "rtl";
  }

  function boxPositionFraction() {
    var rect = preview.getBoundingClientRect();
    var box = fieldBox.getBoundingClientRect();
    if (!rect.width || !rect.height) return { x: 0.5, y: 0.5 };
    var x = (box.left + box.width / 2 - rect.left) / rect.width;
    var y = (box.top + box.height / 2 - rect.top) / rect.height;
    return {
      x: Math.min(1, Math.max(0, x)),
      y: Math.min(1, Math.max(0, y)),
    };
  }

  function readMapping() {
    var pos = boxPositionFraction();
    return {
      student_name: {
        page: parseInt(pageSelect ? pageSelect.value : "1", 10) || 1,
        x: Math.round(pos.x * 1000) / 1000,
        y: Math.round(pos.y * 1000) / 1000,
        font_size: parseInt(fontSizeInput ? fontSizeInput.value : "28", 10) || 28,
        align: alignSelect ? alignSelect.value : "center",
        direction: directionSelect ? directionSelect.value : "rtl",
        font_family: "",
      },
    };
  }

  // Dragging (pointer input)
  var dragging = false;
  fieldBox.addEventListener("pointerdown", function (e) {
    dragging = true;
    fieldBox.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  fieldBox.addEventListener("pointermove", function (e) {
    if (!dragging) return;
    var rect = preview.getBoundingClientRect();
    var x = (e.clientX - rect.left) / rect.width;
    var y = (e.clientY - rect.top) / rect.height;
    positionBox(Math.min(1, Math.max(0, x)), Math.min(1, Math.max(0, y)));
  });
  fieldBox.addEventListener("pointerup", function () { dragging = false; });
  fieldBox.addEventListener("pointercancel", function () { dragging = false; });

  // Keyboard placement: the field box is focusable (tabindex=0) and the arrow
  // keys move it by a small step (Shift for a larger step), so placement is
  // fully operable without a pointer. Coordinates are fractions of the preview.
  fieldBox.setAttribute("tabindex", "0");
  fieldBox.setAttribute("role", "button");
  fieldBox.setAttribute("aria-label", statusText.placementLabel);
  fieldBox.addEventListener("keydown", function (e) {
    if (![ "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown" ].includes(e.key)) return;
    e.preventDefault();
    var pos = boxPositionFraction();
    var step = e.shiftKey ? 0.02 : 0.005;
    if (e.key === "ArrowLeft") pos.x -= step;
    else if (e.key === "ArrowRight") pos.x += step;
    else if (e.key === "ArrowUp") pos.y -= step;
    else if (e.key === "ArrowDown") pos.y += step;
    positionBox(Math.min(1, Math.max(0, pos.x)), Math.min(1, Math.max(0, pos.y)));
  });

  if (fontSizeInput) fontSizeInput.addEventListener("input", applyBoxStyle);
  if (alignSelect) alignSelect.addEventListener("change", applyBoxStyle);
  if (directionSelect) directionSelect.addEventListener("change", applyBoxStyle);

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

  var generatedPreviewUrl = "";

  function postPreview(url, data) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrf() },
      body: JSON.stringify(data),
      credentials: "same-origin",
    }).then(function (response) {
      var contentType = response.headers.get("content-type") || "";
      if (response.ok && contentType.indexOf("application/pdf") === 0) {
        return response.blob().then(function (blob) { return { status: response.status, blob: blob }; });
      }
      return response.json().then(function (body) { return { status: response.status, body: body }; });
    });
  }

  saveButton.addEventListener("click", function () {
    var mapping = readMapping();
    setStatus(statusText.saving, "muted");
    postJSON(saveUrl, { mapping: mapping }).then(function (res) {
      if (res.status === 200 && res.body.ok) {
        currentMapping = res.body.mapping;
        setStatus(statusText.saved, "success");
      } else {
        setStatus((res.body && res.body.error) || statusText.saveFailed, "error");
      }
    }).catch(function () {
      setStatus(statusText.networkError, "error");
    });
  });

  generateButton.addEventListener("click", function () {
    setStatus(statusText.generating, "muted");
    if (resultWrap) resultWrap.classList.add("hidden");
    // Preview must reflect the CURRENT displayed mapping (server-validated),
    // not silently reuse stale persisted state.
    postPreview(generateUrl, { mapping: readMapping() }).then(function (res) {
      if (res.status === 200 && res.blob) {
        if (generatedPreviewUrl) URL.revokeObjectURL(generatedPreviewUrl);
        generatedPreviewUrl = URL.createObjectURL(res.blob);
        if (resultLink) resultLink.href = generatedPreviewUrl;
        if (resultWrap) resultWrap.classList.remove("hidden");
        setStatus(statusText.generated, "success");
      } else {
        setStatus((res.body && res.body.error) || statusText.generateFailed, "error");
      }
    }).catch(function () {
      setStatus(statusText.networkError, "error");
    });
  });

  if (preview && fieldBox) {
    if (preview.complete) loadCurrentPlacement();
    else preview.addEventListener("load", loadCurrentPlacement);
  }
  window.addEventListener("pagehide", function () {
    if (generatedPreviewUrl) URL.revokeObjectURL(generatedPreviewUrl);
  });
})();
