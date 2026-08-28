(function () {
  "use strict";

  function pad(number) {
    return String(number).padStart(2, "0");
  }

  function computeClockOffset(serverNow) {
    return Number.isFinite(serverNow) ? Date.now() - serverNow : 0;
  }

  function getRemainingSecondsByDeadline(deadline, clockOffset) {
    return Math.max(0, Math.ceil((deadline + clockOffset - Date.now()) / 1000));
  }

  function getRemainingSecondsByInitial(initialSeconds, loadedAt) {
    return Math.max(0, initialSeconds - Math.floor((Date.now() - loadedAt) / 1000));
  }

  function getRemainingSeconds(opts) {
    var deadline = opts.deadline;
    var clockOffset = opts.clockOffset;
    var initialSeconds = opts.initialSeconds;
    var loadedAt = opts.loadedAt;
    var serverNow = opts.serverNow;
    var requireServerNow = opts.requireServerNow;
    var hasDeadline = Number.isFinite(deadline);
    var hasServerNow = Number.isFinite(serverNow);
    if (hasDeadline) {
      if (requireServerNow && !hasServerNow) {
        return getRemainingSecondsByInitial(initialSeconds, loadedAt);
      }
      return getRemainingSecondsByDeadline(deadline, clockOffset);
    }
    return getRemainingSecondsByInitial(initialSeconds, loadedAt);
  }

  function formatHMS(seconds) {
    var hours = Math.floor(seconds / 3600);
    var minutes = Math.floor((seconds % 3600) / 60);
    var remainder = seconds % 60;
    return pad(hours) + ":" + pad(minutes) + ":" + pad(remainder);
  }

  function formatAdaptive(seconds) {
    var hours = Math.floor(seconds / 3600);
    var minutes = Math.floor((seconds % 3600) / 60);
    var remainder = seconds % 60;
    return hours > 0
      ? pad(hours) + ":" + pad(minutes) + ":" + pad(remainder)
      : pad(minutes) + ":" + pad(remainder);
  }

  function format(seconds, opts) {
    if (opts && opts.alwaysShowHours) return formatHMS(seconds);
    return formatAdaptive(seconds);
  }

  window.CountdownCore = {
    pad: pad,
    computeClockOffset: computeClockOffset,
    getRemainingSecondsByDeadline: getRemainingSecondsByDeadline,
    getRemainingSecondsByInitial: getRemainingSecondsByInitial,
    getRemainingSeconds: getRemainingSeconds,
    formatHMS: formatHMS,
    formatAdaptive: formatAdaptive,
    format: format,
  };
})();
