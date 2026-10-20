/* Entrance animation for the landing sections: each block appears (rises and fades in) when it enters the screen.
   Progressive enhancement: without JavaScript, without IntersectionObserver or with "reduce motion" on, everything is visible from the start.
   It lives in its own file because the portal's security policy (CSP) does not allow inline scripts. */
(function () {
  "use strict";
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce || !("IntersectionObserver" in window)) return;

  // Marked before the browser paints, so whatever is further down is not shown and then hidden
  var root = document.documentElement;
  root.classList.add("reveal-ready");

  // Each list is a group: its items come in staggered, one after another
  var GROUPS = [
    ".about .eyebrow, .about .section-title, .about .intro",
    ".about .feature",
    ".about .card:not(.feature)",
    ".how .eyebrow, .how .card",
    ".form-col > *",
    ".donate .card"
  ];

  function start() {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("in");
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.05, rootMargin: "0px 0px -6% 0px" });

    // Background image of the free block: fades in when it enters the screen (the layer is in the block's CSS)
    Array.prototype.forEach.call(document.querySelectorAll(".donate-test"), function (section) {
      var imageObserver = new IntersectionObserver(function (entries) {
        if (entries[0].isIntersecting) { section.classList.add("img-in"); imageObserver.disconnect(); }
      }, { threshold: 0.25 });
      imageObserver.observe(section);
    });

    GROUPS.forEach(function (selector) {
      Array.prototype.forEach.call(document.querySelectorAll(selector), function (element, index) {
        element.classList.add("reveal");
        element.style.setProperty("--d", Math.min(index * 0.12, 0.48) + "s");
        observer.observe(element);
      });
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();

// With "reduce motion" the hero video stays stopped on its first frame. Without that preference, if the browser did not start it on its own (WebKit), playback is requested; if it is refused (battery saving), the poster stays.
document.addEventListener('DOMContentLoaded', function () {
  var v = document.querySelector('.hero-video');
  if (!v) return;
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) { v.pause(); v.currentTime = 0; return; }
  var start = function () { if (v.paused) { var p = v.play(); if (p && p.catch) p.catch(function () {}); } };
  window.addEventListener('load', start);
  v.addEventListener('canplay', start, { once: true });
});
