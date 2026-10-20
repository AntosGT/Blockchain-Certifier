/* Static frontend of prima-certified (served in front of the API; talks to it through /api/).
   Three pages, depending on what the HTML contains: the landing (#registro-form), the form (#solicitud-form) and the status (#estado-root).
   Texts come from /api/i18n in the client's language. No libraries and no build. */
(function () {
  "use strict";
  var strings = {};

  function $(selector) { return document.querySelector(selector); }
  function each(selector, fn) { Array.prototype.forEach.call(document.querySelectorAll(selector), fn); }
  function fill(text, params) {
    return String(text).replace(/\{(\w+)\}/g, function (all, name) { return params && params[name] != null ? params[name] : all; });
  }
  function t(key, params) { return strings.hasOwnProperty(key) ? fill(strings[key], params) : key; }
  function api(path, options) {
    return fetch("/api" + path, options || {}).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (body) { return { ok: response.ok, status: response.status, body: body }; });
    });
  }

  function setLang(lang) {
    document.documentElement.lang = lang;
    each("[data-only]", function (element) { element.hidden = element.getAttribute("data-only") !== lang; });
  }
  function loadStrings(lang) {
    setLang(lang);
    return api("/i18n?lang=" + lang).then(function (r) { strings = r.body || {}; applyStrings(); });
  }
  function applyStrings() {
    each("[data-i18n]", function (element) {
      var params = {};
      try { params = JSON.parse(element.getAttribute("data-params") || "{}"); } catch (e) { params = {}; }
      element.textContent = t(element.getAttribute("data-i18n"), params);
    });
    var title = document.documentElement.getAttribute("data-title-key");
    if (title) document.title = t(title) + " · prima-certified";
  }

  function show(box, message) { if (!box) return; box.textContent = message; box.hidden = false; }
  function hide(box) { if (box) box.hidden = true; }
  function resetTurnstile() { if (window.turnstile && typeof window.turnstile.reset === "function") { try { window.turnstile.reset(); } catch (e) { /* no widget */ } } }
  function setConfig(config) {
    each("[data-cfg]", function (element) {
      var name = element.getAttribute("data-cfg");
      if (config[name] != null) element.textContent = config[name];
    });
    each('input[name="form_token"]', function (input) { input.value = config.form_token || ""; });
  }

  // Landing --------------------------------------------------------------------------------------
  function landing(form) {
    var lang = document.documentElement.lang === "es" ? "es" : "en";
    var error = $("#form-error"), sent = $("#sent-notice"), button = form.querySelector('button[type="submit"]');
    var original = button ? button.textContent : "";
    var ready = Promise.all([loadStrings(lang), api("/config?lang=" + lang)]).then(function (results) {
      if (results[1].ok) setConfig(results[1].body);
    });
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      hide(error);
      ready.then(function () {
        if (button) { button.disabled = true; button.textContent = t("common.sending"); }
        var data = new FormData(form);
        data.set("lang", lang);
        return api("/registro", { method: "POST", body: data }).then(function (r) {
          if (r.ok) {
            form.hidden = true;
            show(sent, "");
            sent.innerHTML = "";
            var strong = document.createElement("strong");
            strong.textContent = t("landing.sent", { email: r.body.sent_to });
            sent.appendChild(strong);
            sent.appendChild(document.createTextNode(" " + t("landing.sent_detail", { hours: r.body.link_hours })));
            return;
          }
          show(error, r.body && r.body.error ? r.body.error.message : t("common.network_error"));
          resetTurnstile();
        });
      }).catch(function () {
        show(error, t("common.network_error"));
      }).then(function () {
        if (button) { button.disabled = false; button.textContent = original; }
      });
    });
  }

  // Form -----------------------------------------------------------------------------------------
  function option(value, label) { var o = document.createElement("option"); o.value = value; o.textContent = label; return o; }

  function formulario(form) {
    var token = new URLSearchParams(location.search).get("t") || "";
    var error = $("#form-error"), loading = $("#form-loading");
    function failLink(lang, message) {
      hide(loading);
      $("#form-head").hidden = true;
      var box = $("#link-error");
      loadStrings(lang).then(function () {
        $("#link-error-title").textContent = t("link.invalid.title");
        $("#link-error-message").textContent = message || t("link.invalid.message");
        $("#link-error-back").textContent = t("common.back_home");
        $("#link-error-back").setAttribute("href", lang === "es" ? "/es" : "/");
        box.hidden = false;
      });
    }
    api("/formulario?t=" + encodeURIComponent(token)).then(function (opened) {
      if (!opened.ok) {
        var lang = "en";
        failLink(lang, opened.body && opened.body.error ? opened.body.error.message : "");
        return;
      }
      var lang = opened.body.lang === "es" ? "es" : "en";
      return Promise.all([loadStrings(lang), api("/config?lang=" + lang)]).then(function (results) {
        var config = results[1].body;
        setConfig(config);
        form.querySelector('input[name="t"]').value = token;
        $("#field-email").value = opened.body.email;
        var category = $("#category"), country = $("#country");
        category.appendChild(option("", t("form.choose_option")));
        config.categories.forEach(function (c) { category.appendChild(option(c.value, c.label)); });
        country.appendChild(option("", t("form.choose_country")));
        config.countries.forEach(function (c) { country.appendChild(option(c.code, c.name)); });
        $("#declaration-text").textContent = config.declaration.text;
        form.querySelector('input[name="declaration_version"]').value = config.declaration.version;
        $("#declaration-accept").textContent = t("form.accept_declaration", { version: config.declaration.version });
        $("#files-hint").textContent = "(" + t("form.files_hint", { extensions: config.allowed_extensions, max_files: config.max_files, max_mb: config.max_file_mb }) + ")";
        $("#video-hint").textContent = t("form.video_limit", { max_video_mb: config.max_video_mb });
        var bypass = config.payment_mode === "bypass";
        $("#summary-title").textContent = t(bypass ? "form.s5" : "form.s5_price");
        $("#price-row").hidden = bypass;
        if (!bypass) $("#price-value").textContent = "USD " + Number(config.price_usd).toFixed(2);
        category.setAttribute("data-dropdown", "");
        country.setAttribute("data-dropdown", "");
        if (window.primaBuildDropdowns) window.primaBuildDropdowns();
        hide(loading);
        form.hidden = false;

        var button = form.querySelector('button[type="submit"]');
        form.addEventListener("submit", function (event) {
          event.preventDefault();
          hide(error);
          button.disabled = true;
          var label = button.textContent;
          button.textContent = t("common.sending");
          api("/solicitudes", { method: "POST", body: new FormData(form) }).then(function (r) {
            if (r.ok) { location.href = r.body.status_path; return; }
            if (r.status === 404) { form.hidden = true; failLink(lang, r.body.error && r.body.error.message); return; }
            // Server busy: it is a notice to wait a few minutes, not an error ("warn" style)
            var busy = r.status === 503 && r.body && r.body.error && r.body.error.code === "busy";
            error.className = "notice " + (busy ? "warn" : "error");
            show(error, r.body && r.body.error ? r.body.error.message : t("common.network_error"));
            error.scrollIntoView({ behavior: "smooth", block: "center" });
          }).catch(function () {
            error.className = "notice error";
            show(error, t("common.network_error"));
          }).then(function () { button.disabled = false; button.textContent = label; });
        });
      });
    }).catch(function () { hide(loading); show(error, strings["common.network_error"] || "Network error"); });
  }

  // Status ---------------------------------------------------------------------------------------
  function estado() {
    var match = location.pathname.match(/\/solicitudes\/([^/]+)/);
    var id = match ? match[1] : "";
    var token = new URLSearchParams(location.search).get("t") || "";
    api("/solicitudes/" + encodeURIComponent(id) + "?t=" + encodeURIComponent(token)).then(function (r) {
      if (!r.ok) {
        return loadStrings("en").then(function () {
          $("#st-notfound-title").textContent = t("request.notfound.title");
          $("#st-notfound-message").textContent = t("request.notfound.message");
          $("#st-notfound-back").textContent = t("common.back_home");
          $("#st-loading").hidden = true;
          $("#st-notfound").hidden = false;
        });
      }
      var d = r.body, lang = d.lang === "es" ? "es" : "en";
      return loadStrings(lang).then(function () {
        $("#st-loading").hidden = true;
        $("#st-root").hidden = false;
        $("#st-title").textContent = d.title;
        $("#st-home").setAttribute("href", lang === "es" ? "/es" : "/");
        $("#st-type").textContent = d.category_label;
        $("#st-type2").textContent = d.category_label;
        $("#st-received").textContent = d.created_at.slice(0, 16).replace("T", " ") + " UTC";
        var status = d.status;
        $("#st-expired").hidden = !(status === "PENDING_EMAIL" || status === "EXPIRED");
        $("#st-processing").hidden = !(status === "PROCESSING" || status === "AWAITING_PAYMENT");
        $("#st-failed").hidden = status !== "FAILED";
        $("#st-processed").hidden = status !== "PROCESSED";
        if (status === "PROCESSED") {
          $("#st-evidence-text").textContent = d.evidence_text;
          $("#st-wait").textContent = t("status.wait", { email: d.email });
          $("#st-evidence-id").textContent = d.evidence_id;
          $("#st-by").textContent = d.name + " (" + d.email + ")";
          $("#st-declaration").textContent = t("status.version", { version: d.declaration_version });
          if (d.manifest_hash) { $("#st-hash").textContent = d.manifest_hash; $("#st-hash-row").hidden = false; }
        }
      });
    }).catch(function () { loadStrings("en").then(function () { $("#st-loading").textContent = t("common.network_error"); }); });
  }

  var form;
  if ((form = $("#registro-form"))) landing(form);
  else if ((form = $("#solicitud-form"))) formulario(form);
  else if ($("#estado-root")) estado();
})();
