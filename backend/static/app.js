/* Progressive enhancement only.
   Controls are real forms and work with JavaScript disabled; this file adds a confirmation
   prompt and the dashboard auto refresh. No framework, no CDN. */
(function () {
  "use strict";

  // Confirmation on destructive forms (Close all). Without JS the form still submits.
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.getAttribute("data-confirm"))) e.preventDefault();
    });
  });

  // Busy feedback while a control's form is submitting.
  document.querySelectorAll("form[action^='/ui/bot/']").forEach(function (form) {
    form.addEventListener("submit", function () {
      var btn = form.querySelector("button[type=submit]");
      if (!btn) return;
      btn.disabled = true;
      btn.dataset.label = btn.textContent;
      btn.textContent = "working";
    });
  });

  // Dashboard auto refresh: only while the tab is visible, so an idle tab costs nothing.
  var auto = document.querySelector("[data-autorefresh]");
  if (!auto) return;
  var every = (parseInt(auto.getAttribute("data-autorefresh"), 10) || 30) * 1000;
  var label = document.getElementById("refresh-label");
  var timer = null;
  var tick = function () {
    if (document.visibilityState === "visible") window.location.reload();
  };
  var arm = function () { if (!timer) timer = window.setInterval(tick, every); };
  var disarm = function () { if (timer) { window.clearInterval(timer); timer = null; } };
  arm();
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") arm(); else disarm();
  });
  if (label) {
    var left = Math.round(every / 1000);
    window.setInterval(function () {
      left -= 1;
      if (left <= 0) left = Math.round(every / 1000);
      label.textContent = "auto refresh " + left + "s";
    }, 1000);
  }
})();