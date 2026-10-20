/* Popup with the example certificate on the landing. No libraries: it opens a <dialog> with the certificate image and the link to the PDF.
   It closes with the X, with Esc (native to <dialog>) or by clicking outside. */
(function () {
  "use strict";
  var dialog = document.getElementById("cert-dialog");
  if (!dialog) return;

  function open() {
    if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", "");
    document.documentElement.classList.add("cert-open");
  }
  function close() {
    if (typeof dialog.close === "function") dialog.close(); else dialog.removeAttribute("open");
    document.documentElement.classList.remove("cert-open");
  }

  Array.prototype.forEach.call(document.querySelectorAll("[data-cert-open]"), function (button) { button.addEventListener("click", open); });
  Array.prototype.forEach.call(document.querySelectorAll("[data-cert-close]"), function (button) { button.addEventListener("click", close); });
  dialog.addEventListener("click", function (event) { if (event.target === dialog) close(); });
  dialog.addEventListener("close", function () { document.documentElement.classList.remove("cert-open"); });
})();
