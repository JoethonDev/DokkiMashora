(function () {
  "use strict";

  var PAGE_SIZE = 50;

  function initImportPreview() {
    var dataScript = document.getElementById("import-preview-data");
    var container = document.getElementById("import-preview");
    if (!dataScript || !container) return;
    if (container.dataset.initialized === "true") return;
    container.dataset.initialized = "true";

    var rows;
    try {
      rows = JSON.parse(dataScript.textContent);
    } catch (err) {
      return;
    }

    var searchInput = document.getElementById("import-preview-search");
    var tbody = container.querySelector("[data-import-preview-tbody]");
    var cards = container.querySelector("[data-import-preview-cards]");
    var prevBtn = container.querySelector("[data-import-preview-prev]");
    var nextBtn = container.querySelector("[data-import-preview-next]");
    var pageEl = container.querySelector("[data-import-preview-page]");
    var rangeStart = container.querySelector("[data-import-preview-range-start]");
    var rangeEnd = container.querySelector("[data-import-preview-range-end]");
    var totalEl = container.querySelector("[data-import-preview-total]");
    var emptyLabel = container.dataset.emptyLabel || "No rows found.";
    var rowLabel = container.dataset.labelRow || "Row";

    var query = "";
    var page = 1;

    function filteredRows() {
      var q = query.trim().toLowerCase();
      if (!q) return rows;
      return rows.filter(function (r) {
        return (
          (r.email && r.email.toLowerCase().indexOf(q) !== -1) ||
          (r.error && r.error.toLowerCase().indexOf(q) !== -1)
        );
      });
    }

    function buildPill(row) {
      var pill = document.createElement("span");
      pill.className = "status-pill-" + (row.tone || "neutral");
      var icon = document.createElement("span");
      icon.className = "status-icon";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = row.icon || "\u2022";
      pill.appendChild(icon);
      var label = document.createElement("span");
      label.textContent = row.status_label || "";
      pill.appendChild(label);
      return pill;
    }

    function appendTd(rowEl, text, className) {
      var td = document.createElement("td");
      if (className) td.className = className;
      td.textContent = text;
      rowEl.appendChild(td);
    }

    function render() {
      var filtered = filteredRows();
      var totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
      if (page > totalPages) page = totalPages;
      var start = (page - 1) * PAGE_SIZE;
      var slice = filtered.slice(start, start + PAGE_SIZE);

      tbody.replaceChildren();
      cards.replaceChildren();

      if (!slice.length) {
        var emptyTd = document.createElement("td");
        emptyTd.colSpan = 4;
        emptyTd.className = "text-muted";
        emptyTd.textContent = emptyLabel;
        var emptyTr = document.createElement("tr");
        emptyTr.appendChild(emptyTd);
        tbody.appendChild(emptyTr);

        var emptyCard = document.createElement("div");
        emptyCard.className = "border border-line rounded-sm p-4 text-center";
        emptyCard.textContent = emptyLabel;
        cards.appendChild(emptyCard);
      }

      slice.forEach(function (r) {
        var tr = document.createElement("tr");
        appendTd(tr, String(r.row), "");
        appendTd(tr, r.email || "", "break-words");
        var statusTd = document.createElement("td");
        statusTd.appendChild(buildPill(r));
        tr.appendChild(statusTd);
        appendTd(tr, r.error || "\u2014", "break-words");
        tbody.appendChild(tr);

        var card = document.createElement("div");
        card.className = "mobile-card";
        var body = document.createElement("div");
        body.className = "flex-1 min-w-0";
        var strong = document.createElement("strong");
        strong.className = "block break-words";
        strong.textContent = rowLabel + " " + r.row + " \u00b7 " + (r.email || "\u2014");
        body.appendChild(strong);
        body.appendChild(buildPill(r));
        if (r.error) {
          var small = document.createElement("small");
          small.className = "block text-muted break-words";
          small.textContent = r.error;
          body.appendChild(small);
        }
        card.appendChild(body);
        cards.appendChild(card);
      });

      prevBtn.disabled = page <= 1;
      nextBtn.disabled = page >= totalPages;
      pageEl.textContent = page + " / " + totalPages;
      rangeStart.textContent = filtered.length ? start + 1 : 0;
      rangeEnd.textContent = start + slice.length;
      totalEl.textContent = filtered.length;
    }

    if (searchInput) {
      searchInput.addEventListener("input", function () {
        query = searchInput.value;
        page = 1;
        render();
      });
    }
    prevBtn.addEventListener("click", function () {
      if (page > 1) {
        page -= 1;
        render();
      }
    });
    nextBtn.addEventListener("click", function () {
      page += 1;
      render();
    });

    render();
  }

  document.addEventListener("DOMContentLoaded", initImportPreview);
}());