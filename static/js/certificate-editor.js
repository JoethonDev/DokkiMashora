(function () {
  "use strict";

  // Certificate placement editor (P7-03 v2).
  // Visual designer canvas: multiple dynamic fields from the field registry,
  // drag + resize boxes with normalized coordinates, per-field styling
  // (font size, font family, alignment, direction), per-field pages, v2
  // layout save, and shared-path preview generation. No framework — vanilla
  // JS within the existing module style; fetch-based with CSRF, no HTMX.

  var editor = document.getElementById("certificate-editor");
  if (!editor) return;

  var canvas = document.getElementById("editor-canvas");
  var preview = document.getElementById("editor-preview");
  var canvasPageEl = document.getElementById("editor-canvas-page");
  var emptyHint = document.getElementById("editor-empty-hint");

  var fieldPicker = document.getElementById("editor-field-picker");
  var addFieldBtn = document.getElementById("editor-add-field");
  var settingsWrap = document.getElementById("editor-field-settings");
  var fieldSelect = document.getElementById("editor-field-select");
  var fieldPageEl = document.getElementById("editor-field-page");
  var fontSizeInput = document.getElementById("editor-font-size");
  var fontFamilySelect = document.getElementById("editor-font-family");
  var alignSelect = document.getElementById("editor-align");
  var directionSelect = document.getElementById("editor-direction");
  var removeBtn = document.getElementById("editor-remove-field");
  var saveButton = editor.querySelector("[data-editor-save]");
  var generateButton = editor.querySelector("[data-editor-generate]");
  var statusEl = editor.querySelector("[data-editor-status]");
  var statusIconEl = editor.querySelector("[data-editor-status-icon]");
  var statusTextEl = editor.querySelector("[data-editor-status-text]");
  var resultWrap = editor.querySelector("[data-editor-result]");
  var resultLink = editor.querySelector("[data-editor-result-link]");
  var resultFrame = editor.querySelector("[data-editor-result-frame]");

  var saveUrl = editor.dataset.saveUrl;
  var generateUrl = editor.dataset.generateUrl;
  var pageCount = parseInt(editor.dataset.pageCount || "1", 10);
  var schemaVersion = parseInt(editor.dataset.schemaVersion || "0", 10);
  var previewWidth = parseInt(editor.dataset.previewWidth || "0", 10);

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
    noFields: editor.dataset.msgNoFields || "…",
    selectField: editor.dataset.msgSelectField || "…",
    reserved: editor.dataset.msgReserved || "…",
    emptyHint: editor.dataset.msgEmptyHint || "…",
    pageWord: editor.dataset.msgPageWord || "page",
    fieldAria: editor.dataset.msgFieldAria || "{label}",
    resizeAria: editor.dataset.msgResizeAria || "{label}",
    fieldAdded: editor.dataset.msgFieldAdded || "…",
    fieldRemoved: editor.dataset.msgFieldRemoved || "…",
  };

  var availableFields = parseScript("certificate-available-fields", []);
  var availableFonts = parseScript("certificate-available-fonts", []);
  var currentMapping = parseScript("certificate-current-mapping", {});

  var DEFAULT_WIDTH = 0.5;
  var DEFAULT_HEIGHT = 0.08;
  var MIN_WIDTH = 0.03;
  var MIN_HEIGHT = 0.02;
  var MOVE_STEP = 0.01;
  var FINE_STEP = 0.002;
  var DEFAULT_FONT = "cairo";
  var DEFAULT_FONT_SIZE = 28;

  var fieldLabelMap = {};
  availableFields.forEach(function (f) { fieldLabelMap[f.key] = f.label || f.key; });
  var availableFieldKeys = availableFields.map(function (f) { return f.key; });

  var state = {
    fields: {},
    boxes: {},
    selectedKey: null,
    canvasPage: 1,
  };
  var dragging = null;
  var resizing = null;
  var generatedPreviewUrl = "";

  function parseScript(id, fallback) {
    var el = document.getElementById(id);
    if (!el) return fallback;
    try { return JSON.parse(el.textContent); }
    catch (e) { return fallback; }
  }

  function getCsrf() {
    var el = document.querySelector('[name="csrfmiddlewaretoken"]');
    return el ? el.value : "";
  }

  function round3(v) { return Math.round(v * 1000) / 1000; }
  function clamp(v, min, max) { return Math.min(max, Math.max(min, v)); }
  function clampFont(v) { return Math.round(clamp(v, 4, 120)); }
  function labelFor(key) { return fieldLabelMap[key] || key; }
  function cssFont(key) {
    var map = {
      cairo: "'Cairo', sans-serif",
      "cairo-bold": "'Cairo', sans-serif",
      inter: "'Inter', sans-serif",
      "inter-bold": "'Inter', sans-serif",
    };
    return map[key] || "'Cairo', sans-serif";
  }
  function isBoldFont(key) { return key === "cairo-bold" || key === "inter-bold"; }

  function setStatus(msg, tone) {
    if (!statusEl) return;
    statusIconEl.textContent = tone === "success" ? "\u2713" : tone === "error" ? "\u2715" : "\u2022";
    statusTextEl.textContent = msg;
    statusEl.classList.remove("text-red", "text-success-text", "text-muted");
    statusEl.classList.add(tone === "error" ? "text-red" : tone === "success" ? "text-success-text" : "text-muted");
  }

  function showToast(message, type) {
    try {
      window.dispatchEvent(new CustomEvent("show-toast", { detail: { message: message, type: type || "info" } }));
    } catch (e) {}
  }

  function setBusy(busy) {
    if (saveButton) saveButton.disabled = busy;
    if (generateButton) generateButton.disabled = busy;
  }

  // --- Field picker / placed-field list -------------------------------------

  function refreshPicker() {
    fieldPicker.replaceChildren();
    var placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = statusText.selectField;
    fieldPicker.appendChild(placeholder);
    availableFields.forEach(function (f) {
      var opt = document.createElement("option");
      opt.value = f.key;
      opt.textContent = f.label;
      if (state.fields[f.key]) opt.disabled = true;
      fieldPicker.appendChild(opt);
    });
    fieldPicker.value = "";
  }

  function refreshFieldSelect() {
    fieldSelect.replaceChildren();
    var keys = Object.keys(state.fields);
    if (!keys.length) {
      var empty = document.createElement("option");
      empty.value = "";
      empty.textContent = statusText.noFields;
      empty.disabled = true;
      fieldSelect.appendChild(empty);
      fieldSelect.value = "";
      return;
    }
    keys.forEach(function (key) {
      var opt = document.createElement("option");
      opt.value = key;
      opt.textContent = labelFor(key) + (pageCount > 1 ? " \u2014 " + statusText.pageWord + " " + state.fields[key].page : "");
      fieldSelect.appendChild(opt);
    });
    if (state.selectedKey && state.fields[state.selectedKey]) fieldSelect.value = state.selectedKey;
  }

  function updateEmptyHint() {
    if (!emptyHint) return;
    emptyHint.textContent = Object.keys(state.fields).length ? "" : statusText.emptyHint;
  }

  // --- Selection / settings controls ----------------------------------------

  function selectField(key) {
    state.selectedKey = key && state.fields[key] ? key : null;
    syncControls();
    renderAll();
  }

  function syncControls() {
    var f = state.selectedKey ? state.fields[state.selectedKey] : null;
    var has = !!f;
    if (settingsWrap) settingsWrap.hidden = !has;
    if (removeBtn) removeBtn.disabled = !has;
    [fontSizeInput, fontFamilySelect, alignSelect, directionSelect].forEach(function (el) {
      if (el) el.disabled = !has;
    });
    if (fieldPageEl && fieldPageEl.tagName === "SELECT") fieldPageEl.disabled = !has;
    if (fieldSelect) fieldSelect.disabled = Object.keys(state.fields).length === 0;
    if (!has) return;
    fontFamilySelect.value = f.font_family || DEFAULT_FONT;
    alignSelect.value = f.align || "center";
    directionSelect.value = f.direction || "rtl";
    fontSizeInput.value = String(f.font_size || DEFAULT_FONT_SIZE);
    if (fieldPageEl) fieldPageEl.value = String(f.page || 1);
  }

  // --- Box rendering --------------------------------------------------------

  function createBox(key) {
    var box = document.createElement("div");
    box.className = "absolute select-none";
    box.setAttribute("tabindex", "0");
    box.setAttribute("role", "group");

    var label = document.createElement("span");
    label.setAttribute("aria-hidden", "true");
    label.style.cssText = "display:block;width:100%;height:100%;overflow:hidden;line-height:1.2;";
    box.appendChild(label);

    var handle = document.createElement("span");
    handle.setAttribute("data-resize-handle", "true");
    handle.setAttribute("role", "button");
    handle.setAttribute("tabindex", "0");
    handle.style.cssText = "position:absolute;right:-1px;bottom:-1px;width:14px;height:14px;cursor:nwse-resize;background:#8A2E1A;border:2px solid #ffffff;";
    box.appendChild(handle);

    canvas.appendChild(box);
    state.boxes[key] = box;

    box.addEventListener("pointerdown", function (e) { onBoxPointerDown(e, key); });
    box.addEventListener("pointermove", function (e) { onBoxPointerMove(e, key); });
    box.addEventListener("pointerup", function (e) { onBoxPointerUp(e, key); });
    box.addEventListener("pointercancel", function (e) { onBoxPointerUp(e, key); });
    box.addEventListener("keydown", function (e) { onBoxKeyDown(e, key); });
    box.addEventListener("focus", function () { if (state.selectedKey !== key) selectField(key); });

    handle.addEventListener("pointerdown", function (e) { onHandlePointerDown(e, key); });
    handle.addEventListener("pointermove", function (e) { onHandlePointerMove(e, key); });
    handle.addEventListener("pointerup", function (e) { onHandlePointerUp(e, key); });
    handle.addEventListener("pointercancel", function (e) { onHandlePointerUp(e, key); });
    handle.addEventListener("keydown", function (e) { onHandleKeyDown(e, key); });

    return box;
  }

  function previewScale() {
    if (!canvas || !previewWidth) return 1;
    var w = canvas.clientWidth;
    return w ? w / previewWidth : 1;
  }

  function renderBox(key) {
    var f = state.fields[key];
    var box = state.boxes[key];
    if (!f || !box) return;
    var onPage = f.page === state.canvasPage;
    var selected = key === state.selectedKey;
    box.style.display = onPage ? "block" : "none";
    box.style.left = (f.x * 100) + "%";
    box.style.top = (f.y * 100) + "%";
    box.style.width = (f.width * 100) + "%";
    box.style.height = (f.height * 100) + "%";
    var scale = previewScale();
    box.style.fontSize = (f.font_size * scale) + "px";
    box.style.fontFamily = cssFont(f.font_family);
    box.style.fontWeight = isBoldFont(f.font_family) ? "700" : "400";
    box.style.textAlign = f.align;
    box.style.direction = f.direction;
    box.style.border = selected ? "2px solid #C95A2A" : "2px dashed #8A2E1A";
    box.style.backgroundColor = "rgba(255,241,230,0.35)";
    box.style.boxShadow = selected ? "0 0 0 3px rgba(201,90,42,0.4)" : "0 1px 3px rgba(0,0,0,0.3)";
    box.style.overflow = "hidden";
    box.setAttribute("aria-label", fieldAriaLabel(key));
    var handle = box.querySelector("[data-resize-handle]");
    if (handle) handle.setAttribute("aria-label", statusText.resizeAria.replace("{label}", labelFor(key)));
    var label = box.firstElementChild;
    if (label) label.textContent = labelFor(key);
  }

  function fieldAriaLabel(key) {
    return statusText.fieldAria
      .replace("{label}", labelFor(key))
      .replace("{page}", String(state.fields[key].page));
  }

  function renderAll() {
    Object.keys(state.fields).forEach(renderBox);
  }

  // --- Add / remove ---------------------------------------------------------

  function addField() {
    var key = fieldPicker.value;
    if (!key || state.fields[key]) return;
    state.fields[key] = {
      page: state.canvasPage,
      x: round3((1 - DEFAULT_WIDTH) / 2),
      y: round3((1 - DEFAULT_HEIGHT) / 2),
      width: DEFAULT_WIDTH,
      height: DEFAULT_HEIGHT,
      font_size: DEFAULT_FONT_SIZE,
      font_family: DEFAULT_FONT,
      align: "center",
      direction: "rtl",
    };
    createBox(key);
    refreshPicker();
    refreshFieldSelect();
    updateEmptyHint();
    selectField(key);
    setStatus(statusText.fieldAdded, "muted");
    var box = state.boxes[key];
    if (box) box.focus();
  }

  function removeField(key) {
    if (!state.fields[key]) return;
    delete state.fields[key];
    if (state.boxes[key]) {
      state.boxes[key].remove();
      delete state.boxes[key];
    }
    if (state.selectedKey === key) {
      var remaining = Object.keys(state.fields);
      state.selectedKey = remaining.length ? remaining[0] : null;
    }
    refreshPicker();
    refreshFieldSelect();
    updateEmptyHint();
    syncControls();
    renderAll();
    setStatus(statusText.fieldRemoved, "muted");
  }

  // --- Drag (reposition) ----------------------------------------------------

  function onBoxPointerDown(e, key) {
    if (e.button !== 0) return;
    if (e.target && e.target.getAttribute("data-resize-handle") === "true") return;
    selectField(key);
    var r = canvas.getBoundingClientRect();
    var f = state.fields[key];
    if (!f || !r.width) return;
    dragging = {
      key: key,
      dx: e.clientX - r.left - f.x * r.width,
      dy: e.clientY - r.top - f.y * r.height,
    };
    state.boxes[key].setPointerCapture(e.pointerId);
    e.preventDefault();
  }

  function onBoxPointerMove(e, key) {
    if (!dragging || dragging.key !== key) return;
    var r = canvas.getBoundingClientRect();
    var f = state.fields[key];
    if (!r.width) return;
    var x = (e.clientX - r.left - dragging.dx) / r.width;
    var y = (e.clientY - r.top - dragging.dy) / r.height;
    f.x = round3(clamp(x, 0, 1 - f.width));
    f.y = round3(clamp(y, 0, 1 - f.height));
    renderBox(key);
  }

  function onBoxPointerUp(e, key) {
    if (dragging && dragging.key === key) dragging = null;
  }

  function onBoxKeyDown(e, key) {
    var f = state.fields[key];
    if (!f) return;
    if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      removeField(key);
      return;
    }
    var step = e.shiftKey ? FINE_STEP : MOVE_STEP;
    var moved = false;
    if (e.key === "ArrowLeft") { f.x = round3(clamp(f.x - step, 0, 1 - f.width)); moved = true; }
    else if (e.key === "ArrowRight") { f.x = round3(clamp(f.x + step, 0, 1 - f.width)); moved = true; }
    else if (e.key === "ArrowUp") { f.y = round3(clamp(f.y - step, 0, 1 - f.height)); moved = true; }
    else if (e.key === "ArrowDown") { f.y = round3(clamp(f.y + step, 0, 1 - f.height)); moved = true; }
    if (moved) { e.preventDefault(); renderBox(key); }
  }

  // --- Resize ---------------------------------------------------------------

  function onHandlePointerDown(e, key) {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    selectField(key);
    if (!state.fields[key]) return;
    resizing = { key: key };
    e.currentTarget.setPointerCapture(e.pointerId);
  }

  function onHandlePointerMove(e, key) {
    if (!resizing || resizing.key !== key) return;
    var r = canvas.getBoundingClientRect();
    var box = state.boxes[key];
    var f = state.fields[key];
    if (!r.width || !box) return;
    var boxRect = box.getBoundingClientRect();
    f.width = round3(clamp((e.clientX - boxRect.left) / r.width, MIN_WIDTH, 1 - f.x));
    f.height = round3(clamp((e.clientY - boxRect.top) / r.height, MIN_HEIGHT, 1 - f.y));
    renderBox(key);
  }

  function onHandlePointerUp(e, key) {
    if (resizing && resizing.key === key) resizing = null;
  }

  function onHandleKeyDown(e, key) {
    var f = state.fields[key];
    if (!f) return;
    var step = e.shiftKey ? FINE_STEP : MOVE_STEP;
    var moved = false;
    if (e.key === "ArrowLeft") { f.width = round3(clamp(f.width - step, MIN_WIDTH, 1 - f.x)); moved = true; }
    else if (e.key === "ArrowRight") { f.width = round3(clamp(f.width + step, MIN_WIDTH, 1 - f.x)); moved = true; }
    else if (e.key === "ArrowUp") { f.height = round3(clamp(f.height - step, MIN_HEIGHT, 1 - f.y)); moved = true; }
    else if (e.key === "ArrowDown") { f.height = round3(clamp(f.height + step, MIN_HEIGHT, 1 - f.y)); moved = true; }
    if (moved) { e.preventDefault(); renderBox(key); }
  }

  // --- Mapping read / hydrate ----------------------------------------------

  function readMapping() {
    var out = {};
    Object.keys(state.fields).forEach(function (key) {
      var f = state.fields[key];
      out[key] = {
        page: f.page,
        x: f.x,
        y: f.y,
        width: f.width,
        height: f.height,
        font_size: f.font_size,
        font_family: f.font_family,
        align: f.align,
        direction: f.direction,
      };
    });
    return out;
  }

  function extractFields(mapping) {
    if (!mapping || typeof mapping !== "object") return {};
    if (mapping.fields && typeof mapping.fields === "object") return mapping.fields;
    var out = {};
    Object.keys(mapping).forEach(function (k) {
      if (availableFieldKeys.indexOf(k) !== -1) out[k] = mapping[k];
    });
    return out;
  }

  function normalizeAlign(v) {
    return ["left", "center", "right"].indexOf(v) !== -1 ? v : "center";
  }
  function normalizeDirection(v) {
    return v === "ltr" ? "ltr" : "rtl";
  }
  function normalizeFont(v) {
    return availableFonts.some(function (f) { return f.key === v; }) ? v : DEFAULT_FONT;
  }

  function hydrate() {
    var fields = extractFields(currentMapping);
    var legacySchema = schemaVersion < 2;
    Object.keys(fields).forEach(function (key) {
      var p = fields[key];
      if (!p || typeof p !== "object") return;
      var hasBox = !legacySchema && Number(p.width) > 0 && Number(p.height) > 0;
      var width = hasBox ? clamp(Number(p.width), MIN_WIDTH, 1) : DEFAULT_WIDTH;
      var height = hasBox ? clamp(Number(p.height), MIN_HEIGHT, 1) : DEFAULT_HEIGHT;
      var x = Number(p.x);
      var y = Number(p.y);
      if (!hasBox) {
        // v1/point placement: center the default box on the stored point.
        x = x - width / 2;
        y = y - height / 2;
      }
      state.fields[key] = {
        page: clamp(parseInt(p.page, 10) || 1, 1, pageCount),
        x: round3(clamp(x, 0, 1 - width)),
        y: round3(clamp(y, 0, 1 - height)),
        width: round3(width),
        height: round3(height),
        font_size: clampFont(parseInt(p.font_size, 10) || DEFAULT_FONT_SIZE),
        font_family: normalizeFont(p.font_family),
        align: normalizeAlign(p.align),
        direction: normalizeDirection(p.direction),
      };
      createBox(key);
    });
    var keys = Object.keys(state.fields);
    if (keys.length) selectField(keys[0]);
    refreshPicker();
    refreshFieldSelect();
    updateEmptyHint();
    renderAll();
  }

  // --- Save / preview -------------------------------------------------------

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

  function saveMapping() {
    setStatus(statusText.saving, "muted");
    setBusy(true);
    postJSON(saveUrl, { mapping: readMapping() }).then(function (res) {
      if (res.status === 200 && res.body && res.body.ok) {
        currentMapping = res.body.mapping;
        setStatus(statusText.saved, "success");
        showToast(statusText.saved, "success");
      } else {
        var msg = (res.body && res.body.error) || statusText.saveFailed;
        setStatus(msg, "error");
        showToast(msg, "error");
      }
    }).catch(function () {
      setStatus(statusText.networkError, "error");
      showToast(statusText.networkError, "error");
    }).then(function () { setBusy(false); });
  }

  function generatePreview() {
    setStatus(statusText.generating, "muted");
    setBusy(true);
    if (resultWrap) resultWrap.classList.add("hidden");
    // Preview must reflect the CURRENT displayed mapping (server-validated),
    // not silently reuse stale persisted state.
    postPreview(generateUrl, { mapping: readMapping() }).then(function (res) {
      if (res.status === 200 && res.blob) {
        if (generatedPreviewUrl) URL.revokeObjectURL(generatedPreviewUrl);
        generatedPreviewUrl = URL.createObjectURL(res.blob);
        if (resultLink) resultLink.href = generatedPreviewUrl;
        if (resultFrame) resultFrame.src = generatedPreviewUrl;
        if (resultWrap) resultWrap.classList.remove("hidden");
        setStatus(statusText.generated, "success");
      } else {
        var msg = (res.body && res.body.error) || statusText.generateFailed;
        setStatus(msg, "error");
        showToast(msg, "error");
      }
    }).catch(function () {
      setStatus(statusText.networkError, "error");
      showToast(statusText.networkError, "error");
    }).then(function () { setBusy(false); });
  }

  // --- Init -----------------------------------------------------------------

  function populatePageSelect(el) {
    if (!el || el.tagName !== "SELECT") return;
    for (var i = 1; i <= pageCount; i++) {
      var opt = document.createElement("option");
      opt.value = String(i);
      opt.textContent = String(i);
      el.appendChild(opt);
    }
  }

  function populateFontFamily() {
    fontFamilySelect.replaceChildren();
    availableFonts.forEach(function (f) {
      var opt = document.createElement("option");
      opt.value = f.key;
      opt.textContent = f.label || f.key;
      fontFamilySelect.appendChild(opt);
    });
    fontFamilySelect.value = DEFAULT_FONT;
  }

  function wireEvents() {
    addFieldBtn.addEventListener("click", addField);
    fieldSelect.addEventListener("change", function () { selectField(fieldSelect.value); });
    removeBtn.addEventListener("click", function () { if (state.selectedKey) removeField(state.selectedKey); });

    if (canvasPageEl && canvasPageEl.tagName === "SELECT") {
      canvasPageEl.addEventListener("change", function () {
        state.canvasPage = clamp(parseInt(canvasPageEl.value, 10) || 1, 1, pageCount);
        renderAll();
      });
    }
    if (fieldPageEl && fieldPageEl.tagName === "SELECT") {
      fieldPageEl.addEventListener("change", function () {
        var f = state.fields[state.selectedKey];
        if (!f) return;
        f.page = clamp(parseInt(fieldPageEl.value, 10) || 1, 1, pageCount);
        refreshFieldSelect();
        renderAll();
      });
    }
    fontSizeInput.addEventListener("input", function () {
      var f = state.fields[state.selectedKey];
      if (!f) return;
      f.font_size = clampFont(parseInt(fontSizeInput.value, 10) || DEFAULT_FONT_SIZE);
      renderBox(state.selectedKey);
    });
    fontFamilySelect.addEventListener("change", function () {
      var f = state.fields[state.selectedKey];
      if (!f) return;
      f.font_family = fontFamilySelect.value || DEFAULT_FONT;
      renderBox(state.selectedKey);
    });
    alignSelect.addEventListener("change", function () {
      var f = state.fields[state.selectedKey];
      if (!f) return;
      f.align = alignSelect.value;
      renderBox(state.selectedKey);
    });
    directionSelect.addEventListener("change", function () {
      var f = state.fields[state.selectedKey];
      if (!f) return;
      f.direction = directionSelect.value;
      renderBox(state.selectedKey);
    });

    saveButton.addEventListener("click", saveMapping);
    generateButton.addEventListener("click", generatePreview);

    window.addEventListener("resize", renderAll);
    window.addEventListener("pagehide", function () {
      if (generatedPreviewUrl) URL.revokeObjectURL(generatedPreviewUrl);
    });
  }

  function init() {
    populatePageSelect(canvasPageEl);
    populatePageSelect(fieldPageEl);
    populateFontFamily();
    refreshPicker();
    refreshFieldSelect();
    updateEmptyHint();
    wireEvents();
    hydrate();
    if (canvasPageEl && canvasPageEl.tagName === "SELECT") canvasPageEl.value = String(state.canvasPage);
    syncControls();
    renderAll();
  }

  init();
})();
