/* Dropdowns with the Fragola Multiplayer design (chat-toggle + chat-menu).
   Progressive enhancement: the real <select> stays in the form (value, validation and submission do not change); without JavaScript the native one is shown. */
(function () {
  "use strict";

  var CHEVRON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"></path></svg>';
  var open = null;

  function closeOpen(focusToggle) {
    if (!open) return;
    open.menu.hidden = true;
    open.toggle.setAttribute("aria-expanded", "false");
    if (focusToggle) open.toggle.focus();
    open = null;
  }

  function build(select) {
    var wrap = document.createElement("div");
    wrap.className = "task-menu-picker";
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);
    select.classList.add("dd-native");
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");

    var label = select.id ? document.querySelector('label[for="' + select.id + '"]') : null;
    var toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "chat-toggle";
    toggle.setAttribute("aria-haspopup", "listbox");
    toggle.setAttribute("aria-expanded", "false");
    if (label) toggle.setAttribute("aria-label", label.firstChild ? label.firstChild.textContent.trim() : "");
    var text = document.createElement("span");
    toggle.appendChild(text);
    toggle.insertAdjacentHTML("beforeend", CHEVRON);

    var menu = document.createElement("div");
    menu.className = "chat-menu task-menu";
    menu.setAttribute("role", "listbox");
    menu.hidden = true;

    var placeholder = "Elegí una opción";
    var items = [];
    Array.prototype.forEach.call(select.options, function (option) {
      if (option.value === "") { placeholder = option.textContent; return; }
      var item = document.createElement("button");
      item.type = "button";
      item.setAttribute("role", "option");
      item.dataset.value = option.value;
      item.textContent = option.textContent;
      item.addEventListener("click", function () { choose(option.value); closeOpen(true); });
      menu.appendChild(item);
      items.push(item);
    });

    function refresh() {
      var current = select.options[select.selectedIndex];
      var chosen = current && current.value !== "";
      text.textContent = chosen ? current.textContent : placeholder;
      toggle.classList.toggle("placeholder", !chosen);
      items.forEach(function (item) {
        var selected = chosen && item.dataset.value === current.value;
        item.classList.toggle("selected", selected);
        item.setAttribute("aria-selected", selected ? "true" : "false");
      });
    }

    function choose(value) {
      select.value = value;
      toggle.classList.remove("invalid");
      select.dispatchEvent(new Event("change", { bubbles: true }));
      refresh();
    }

    function openMenu() {
      closeOpen(false);
      menu.hidden = false;
      toggle.setAttribute("aria-expanded", "true");
      open = { menu: menu, toggle: toggle };
      var target = menu.querySelector(".selected") || items[0];
      if (target) {
        target.focus({ preventScroll: true });
        menu.scrollTop = Math.max(0, target.offsetTop - menu.clientHeight / 2 + target.offsetHeight / 2);
      }
    }

    var typed = "";
    var typedTimer = null;
    function typeAhead(key) {
      typed += key.toLowerCase();
      clearTimeout(typedTimer);
      typedTimer = setTimeout(function () { typed = ""; }, 700);
      var match = items.filter(function (item) { return item.textContent.toLowerCase().indexOf(typed) === 0; })[0];
      if (!match) return;
      if (menu.hidden) openMenu();
      match.focus({ preventScroll: true });
      menu.scrollTop = Math.max(0, match.offsetTop - menu.clientHeight / 2 + match.offsetHeight / 2);
    }

    toggle.addEventListener("click", function () { if (menu.hidden) openMenu(); else closeOpen(false); });
    toggle.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); openMenu(); }
      else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey && event.key !== " ") typeAhead(event.key);
    });
    menu.addEventListener("keydown", function (event) {
      var index = items.indexOf(document.activeElement);
      if (event.key === "ArrowDown") { event.preventDefault(); (items[index + 1] || items[0]).focus(); }
      else if (event.key === "ArrowUp") { event.preventDefault(); (items[index - 1] || items[items.length - 1]).focus(); }
      else if (event.key === "Home") { event.preventDefault(); items[0].focus(); }
      else if (event.key === "End") { event.preventDefault(); items[items.length - 1].focus(); }
      else if (event.key === "Escape") { event.preventDefault(); closeOpen(true); }
      else if (event.key === "Tab") closeOpen(false);
      else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey && event.key !== " ") typeAhead(event.key);
    });
    select.addEventListener("invalid", function () { toggle.classList.add("invalid"); });
    if (label) label.addEventListener("click", function (event) { event.preventDefault(); toggle.focus(); });

    wrap.appendChild(toggle);
    wrap.appendChild(menu);
    refresh();
  }

  document.addEventListener("click", function (event) {
    if (open && !open.toggle.parentNode.contains(event.target)) closeOpen(false);
  });
  function buildAll() {
    Array.prototype.forEach.call(document.querySelectorAll("select[data-dropdown]:not(.dd-native)"), build);
  }
  window.primaBuildDropdowns = buildAll; // for the selects the frontend fills after loading
  buildAll();
})();
