/* Web Reader front-end.
 *
 * Loads the manifest and renders it according to its `kind`:
 *   - epub / text / fb2 / markup -> sanitised HTML in a sandboxed iframe
 *   - pdf                        -> native viewer or rendered page images
 *   - comic / image              -> page images with single/dual layout
 *   - audio                      -> <audio>
 *
 * Device presets, themes and the e-ink simulation are pure CSS/JS on top, so
 * every format benefits from them. No external resources, works offline. */
(function () {
  "use strict";

  var root = document.querySelector("[data-reader]");
  if (!root) return;

  var BOOK_ID = root.getAttribute("data-book-id") || "";
  var MANIFEST_URL = root.getAttribute("data-manifest") || "";
  var PREFS_KEY = "reader:prefs";
  var PROGRESS_KEY = "reader:progress:" + BOOK_ID;
  var POLL_MS = 1600;

  var ui = {
    viewport: root.querySelector("[data-viewport]"),
    frame: root.querySelector("[data-frame]"),
    content: root.querySelector("[data-content]"),
    loading: root.querySelector("[data-loading]"),
    loadingText: root.querySelector("[data-loading-text]"),
    state: root.querySelector("[data-state]"),
    stage: root.querySelector("[data-stage]"),
    toc: root.querySelector("[data-toc]"),
    side: root.querySelector("[data-side]"),
    sideTitle: root.querySelector("[data-side-title]"),
    device: root.querySelector("[data-device]"),
    width: root.querySelector("[data-width]"),
    height: root.querySelector("[data-height]"),
    zoom: root.querySelector("[data-zoom]"),
    zoomOut: root.querySelector("[data-zoom-out]"),
    counter: root.querySelector("[data-counter]"),
    slider: root.querySelector("[data-slider]"),
    footbar: root.querySelector("[data-footbar]"),
    rawLink: root.querySelector("[data-raw-link]"),
    rotate: root.querySelector("[data-rotate]"),
    eink: root.querySelector("[data-eink]"),
    sideOpen: root.querySelector("[data-side-open]"),
    sideClose: root.querySelector("[data-side-close]"),
    prev: root.querySelector("[data-prev]"),
    next: root.querySelector("[data-next]"),
    comicOnly: root.querySelectorAll(".reader-only-comic"),
    pdfOnly: root.querySelectorAll(".reader-only-pdf"),
    layout: root.querySelector("[data-layout]"),
    cover: root.querySelector("[data-cover]"),
    pdfImage: root.querySelector("[data-pdf-image]")
  };

  var THEMES = {
    light: { bg: "#fffdfa", fg: "#1b1917", link: "#1f6f5c" },
    sepia: { bg: "#f7f1e1", fg: "#4a3f2c", link: "#8a6d3b" },
    dark: { bg: "#17181a", fg: "#e7e6e1", link: "#63b39d" }
  };
  var FONTS = {
    serif: "Georgia, 'Iowan Old Style', 'Times New Roman', serif",
    sans: "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
  };

  var DEFAULTS = {
    theme: "auto", font: "serif", size: 100, line: 160, margin: 6,
    justify: false, prefs: false, eink: false, zoom: 100, device: null,
    layout: false, cover: false, pdfImage: false
  };

  var state = {
    manifest: null,
    kind: null,
    mode: null,
    pages: 0,
    chapters: [],
    page: 0,
    chapter: 0,
    dual: false,
    cover: false,
    device: { width: 390, height: 844, gray: 16, name: "Celular" },
    scale: 1,
    polling: null
  };

  var prefs = loadPrefs();
  var progress = loadProgress();

  init();

  // ---------------------------------------------------------------------
  function init() {
    applyQueryOverrides();
    bindPrefs();
    state.device = prefs.device || state.device;
    applyScale();
    if (ui.stage && window.ResizeObserver) {
      new ResizeObserver(applyScale).observe(ui.stage);
    }
    window.addEventListener("resize", applyScale);
    bindStageGestures();
    document.addEventListener("keydown", onKey);
    loadManifest();
  }

  function loadPrefs() {
    try {
      var stored = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}");
      return Object.assign({}, DEFAULTS, stored || {});
    } catch (err) {
      return Object.assign({}, DEFAULTS);
    }
  }

  /* Optional deep-links: ?eink=1, ?dual=1, ?image=1 (PDF image mode),
     ?size=480x800, ?zoom=150. Handy for bookmarks and for testing. */
  function applyQueryOverrides() {
    var params;
    try { params = new URLSearchParams(location.search); } catch (err) { return; }
    if (params.get("eink") === "1") prefs.eink = true;
    if (params.get("dual") === "1") prefs.layout = true;
    if (params.get("image") === "1") prefs.pdfImage = true;
    if (params.get("prefs") === "1") prefs.prefs = true;
    var zoom = Number(params.get("zoom"));
    if (zoom >= 50 && zoom <= 200) prefs.zoom = zoom;
    var size = params.get("size");
    if (size && /^\d{2,4}x\d{2,4}$/.test(size)) {
      var parts = size.split("x");
      prefs.device = { width: Number(parts[0]), height: Number(parts[1]), gray: 16 };
    }
  }

  function savePrefs() {
    try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch (err) { /* ignore */ }
  }

  function loadProgress() {
    try {
      return JSON.parse(localStorage.getItem(PROGRESS_KEY) || "null") || {};
    } catch (err) {
      return {};
    }
  }

  function saveProgress(patch) {
    progress = Object.assign({}, progress, patch);
    try { localStorage.setItem(PROGRESS_KEY, JSON.stringify(progress)); } catch (err) { /* ignore */ }
  }

  // --- manifest --------------------------------------------------------
  function loadManifest() {
    showLoader("Carregando o leitor…");
    fetch(MANIFEST_URL, { headers: { Accept: "application/json" } })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        // A VPS may sit behind a proxy/CDN that answers with a bot-check page
        // instead of JSON. Without this check JSON.parse failed with an opaque
        // message and the stage just stayed grey.
        var type = (response.headers.get("content-type") || "").toLowerCase();
        if (type.indexOf("json") === -1) {
          throw new Error(
            "O servidor respondeu em " + (type || "formato desconhecido") +
            " em vez de JSON. Se há um proxy/CDN na frente, libere /reader/… " +
            "para este endereço."
          );
        }
        return response.json();
      })
      .then(handleManifest)
      .catch(function (err) {
        showState(
          "error",
          "Não foi possível abrir este livro.",
          (err && err.message) ? err.message : String(err),
          null
        );
      });
  }

  function handleManifest(manifest) {
    state.manifest = manifest;
    state.kind = manifest.kind;
    state.pages = manifest.pages || 0;
    state.chapters = manifest.chapters || [];
    state.dual = !!prefs.layout;
    state.cover = !!prefs.cover;

    populateDevices(manifest);

    if (manifest.state === "preparing") {
      showLoader(manifest.message || "Convertendo para leitura…");
      schedulePoll();
      return;
    }
    hideLoader();

    if (manifest.state === "unavailable" || manifest.state === "error") {
      showState(manifest.state, "Abrir no Web Reader", manifest.message, manifest);
      return;
    }

    hideState();
    applyTheme();
    applyQuantize(state.device.gray);
    if (manifest.manga_rtl) state.device.direction = "rtl";
    if (manifest.defaults && manifest.defaults.width) {
      // keep the profile geometry as the starting point when nothing was chosen
      if (!prefs.device) setDevice(manifest.defaults.width, manifest.defaults.height,
        manifest.defaults.gray_levels, manifest.profile_slug);
    } else {
      setDevice(state.device.width, state.device.height, state.device.gray, state.device.name);
    }

    if (ui.rawLink) ui.rawLink.href = manifest.urls.raw;

    if (manifest.kind === "comic" || manifest.kind === "image" || manifest.kind === "pdf") {
      show(ui.comicOnly, manifest.kind === "comic");
      show(ui.pdfOnly, manifest.kind === "pdf");
    }

    if (manifest.kind === "pdf" && !prefs.pdfImage) {
      setupPdfNative();
    } else if (manifest.kind === "pdf") {
      setupPaged("pdf");
    } else if (manifest.kind === "comic") {
      setupPaged("comic");
    } else if (manifest.kind === "image") {
      setupPaged("image");
    } else if (manifest.kind === "audio") {
      setupAudio();
    } else {
      setupText();
    }
  }

  function schedulePoll() {
    if (state.polling) clearTimeout(state.polling);
    state.polling = setTimeout(loadManifest, POLL_MS);
  }

  // --- text (epub / txt / fb2 / markup) --------------------------------
  function setupText() {
    state.mode = "text";
    buildToc();
    if (ui.sideTitle) ui.sideTitle.textContent = "Índice";
    if (ui.footbar) ui.footbar.hidden = false;
    if (!state.chapters.length) {
      showState("unavailable", "Sem capítulos",
        "Este livro não expõe capítulos para navegação. Use a nova aba.", state.manifest);
      return;
    }
    state.chapter = clamp(progress.chapter || 0, 0, state.chapters.length - 1);
    loadChapter(state.chapter);
  }

  function loadChapter(index) {
    state.chapter = clamp(index, 0, state.chapters.length - 1);
    var chapter = state.chapters[state.chapter];
    var iframe = document.createElement("iframe");
    iframe.className = "reader-iframe";
    iframe.setAttribute("sandbox", "allow-same-origin");
    iframe.setAttribute("title", chapter.title || "Capítulo");
    iframe.addEventListener("load", function () {
      bindIframe(iframe);
      var saved = progress.chapter === state.chapter ? progress : null;
      if (saved && saved.scroll) {
        try { iframe.contentWindow.scrollTo(0, saved.scroll); } catch (err) { /* ignore */ }
      }
    });
    iframe.src = chapterUrl(chapter.key);
    replaceContent(iframe);
    updateNav();
    applyScale();
  }

  function bindIframe(iframe) {
    var doc = iframe.contentDocument;
    if (!doc) return;
    applyIframeStyles(doc);
    doc.addEventListener("keydown", onKey);
    var win = iframe.contentWindow;
    if (!win) return;
    var pending = false;
    win.addEventListener("scroll", function () {
      if (pending) return;
      pending = true;
      setTimeout(function () {
        pending = false;
        saveProgress({ chapter: state.chapter, scroll: win.scrollY || 0 });
      }, 400);
    });
  }

  function applyIframeStyles(doc) {
    if (!doc || !doc.head) return;
    var colors = resolvedTheme();
    var themeStyle = doc.getElementById("reader-theme");
    if (colors && colors.force) {
      if (!themeStyle) {
        themeStyle = doc.createElement("style");
        themeStyle.id = "reader-theme";
        doc.head.appendChild(themeStyle);
      }
      themeStyle.textContent =
        "html,body{background:" + colors.bg + " !important;color:" + colors.fg + ";}" +
        "a{color:" + colors.link + ";}";
    } else if (themeStyle) {
      themeStyle.remove();
    }

    var prefStyle = doc.getElementById("reader-prefs");
    if (!prefs.prefs) {
      if (prefStyle) prefStyle.remove();
      return;
    }
    if (!prefStyle) {
      prefStyle = doc.createElement("style");
      prefStyle.id = "reader-prefs";
      doc.head.appendChild(prefStyle);
    }
    var margin = Number(prefs.margin) || 0;
    var size = Number(prefs.size) || 100;
    var line = (Number(prefs.line) || 160) / 100;
    prefStyle.textContent =
      "body{font-family:" + (FONTS[prefs.font] || FONTS.serif) + " !important;" +
      "font-size:" + size + "% !important;line-height:" + line + " !important;" +
      "padding:" + margin + "px " + Math.max(margin, 8) + "px !important;" +
      "text-align:" + (prefs.justify ? "justify" : "start") + " !important;" +
      "hyphens:" + (prefs.justify ? "auto" : "manual") + ";}";
  }

  function resolvedTheme() {
    var name = prefs.theme;
    if (name === "auto") {
      name = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark" : "light";
    }
    var colors = THEMES[name] || THEMES.light;
    return { bg: colors.bg, fg: colors.fg, link: colors.link, force: name !== "light" };
  }

  // --- paged (comic / image / pdf-image) -------------------------------
  function setupPaged(mode) {
    state.mode = mode;
    buildToc();
    if (ui.sideTitle) ui.sideTitle.textContent = "Páginas";
    if (ui.footbar) ui.footbar.hidden = false;
    if (!state.pages) {
      showState("unavailable", "Sem páginas", "Nenhuma página para exibir.", state.manifest);
      return;
    }
    state.page = clamp(progress.page || 0, 0, state.pages - 1);
    renderPage();
  }

  function renderPage() {
    var dual = state.mode === "comic" && state.dual && state.pages > 1;
    var page = state.page;
    if (dual && state.cover && page === 0) dual = false;

    var box = document.createElement("div");
    box.className = "reader-pagebox" + (dual ? " is-dual" : "");
    var indices = dual ? [page, page + 1] : [page];
    indices = indices.filter(function (index) { return index < state.pages; });
    if (state.manifest.manga_rtl) indices.reverse();

    indices.forEach(function (index) {
      var img = document.createElement("img");
      img.className = "reader-page";
      img.alt = "Página " + (index + 1);
      img.loading = "eager";
      img.src = pageUrl(index);
      box.appendChild(img);
    });

    replaceContent(box);
    updateNav();
    preloadNeighbours(page, dual);
  }

  function preloadNeighbours(page, dual) {
    var step = dual ? 2 : 1;
    [page + step, page - step].forEach(function (index) {
      if (index < 0 || index >= state.pages) return;
      var img = new Image();
      img.src = pageUrl(index);
    });
  }

  function pageUrl(index) {
    var width = Math.min(1800, Math.round(state.device.width * (prefs.eink ? 1 : 1.6)));
    var url = state.manifest.urls.page.replace("{index}", String(index));
    var params = "?w=" + width;
    if (prefs.eink) params += "&gray=1";
    return url + params;
  }

  // --- pdf native ------------------------------------------------------
  function setupPdfNative() {
    state.mode = "pdf-native";
    buildToc();
    if (ui.sideTitle) ui.sideTitle.textContent = "Páginas";
    if (ui.footbar) ui.footbar.hidden = true;
    var iframe = document.createElement("iframe");
    iframe.className = "reader-iframe";
    iframe.setAttribute("title", state.manifest.book.title || "PDF");
    iframe.src = state.manifest.urls.raw;
    replaceContent(iframe);
  }

  // --- audio -----------------------------------------------------------
  function setupAudio() {
    state.mode = "audio";
    if (ui.footbar) ui.footbar.hidden = true;
    var box = document.createElement("div");
    box.className = "reader-audio";
    var audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "metadata";
    audio.src = state.manifest.urls.raw;
    box.appendChild(audio);
    replaceContent(box);
  }

  // --- navigation ------------------------------------------------------
  function updateNav() {
    var total, index, label;
    if (state.mode === "text") {
      total = state.chapters.length;
      index = state.chapter;
      var chapter = state.chapters[index] || {};
      label = "Cap. " + (index + 1) + "/" + total + " · " + (chapter.title || "");
      markToc(index);
    } else {
      total = state.pages;
      index = state.page;
      label = "Página " + (index + 1) + "/" + total;
    }
    if (ui.counter) ui.counter.textContent = label;
    if (ui.slider) {
      ui.slider.max = String(Math.max(0, total - 1));
      ui.slider.value = String(index);
    }
  }

  function go(delta) {
    if (state.mode === "text") {
      var next = clamp(state.chapter + delta, 0, state.chapters.length - 1);
      if (next !== state.chapter) {
        saveProgress({ chapter: next, scroll: 0 });
        loadChapter(next);
      }
      return;
    }
    if (state.mode === "pdf-native" || state.mode === "audio") return;
    var step = state.mode === "comic" && state.dual ? 2 : 1;
    if (state.mode === "comic" && state.dual && state.cover && state.page === 0) step = 1;
    var target = clamp(state.page + (delta > 0 ? step : -step), 0, state.pages - 1);
    if (target !== state.page) {
      state.page = target;
      saveProgress({ page: target });
      renderPage();
    }
  }

  function goto(index) {
    if (state.mode === "text") {
      if (index !== state.chapter) {
        saveProgress({ chapter: index, scroll: 0 });
        loadChapter(index);
      }
      return;
    }
    state.page = clamp(index, 0, state.pages - 1);
    saveProgress({ page: state.page });
    renderPage();
  }

  function onKey(event) {
    if (event.defaultPrevented) return;
    var tag = (event.target && event.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "select" || tag === "textarea") return;
    var rtl = state.manifest && state.manifest.manga_rtl;
    var forward = rtl ? "ArrowLeft" : "ArrowRight";
    var back = rtl ? "ArrowRight" : "ArrowLeft";
    if (event.key === forward || event.key === "PageDown" || event.key === " ") {
      event.preventDefault();
      go(1);
    } else if (event.key === back || event.key === "PageUp") {
      event.preventDefault();
      go(-1);
    } else if (event.key === "Home") {
      event.preventDefault();
      goto(0);
    } else if (event.key === "End") {
      event.preventDefault();
      goto((state.mode === "text" ? state.chapters.length : state.pages) - 1);
    }
  }

  // --- toc / side ------------------------------------------------------
  function buildToc() {
    if (!ui.toc) return;
    ui.toc.textContent = "";
    var items = state.mode === "text" ? state.chapters : [];
    if (!items.length) {
      var empty = document.createElement("p");
      empty.className = "muted small";
      empty.textContent = state.pages ? state.pages + " páginas." : "Sem índice.";
      ui.toc.appendChild(empty);
      return;
    }
    items.forEach(function (item, index) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "reader-toc__item";
      button.setAttribute("data-level", String(item.level || 0));
      button.textContent = item.title || ("Capítulo " + (index + 1));
      button.addEventListener("click", function () { goto(index); closeSide(); });
      ui.toc.appendChild(button);
    });
  }

  function markToc(index) {
    if (!ui.toc) return;
    var items = ui.toc.querySelectorAll(".reader-toc__item");
    items.forEach(function (item, position) {
      item.classList.toggle("on", position === index);
    });
  }

  function openSide() { if (ui.side) ui.side.classList.add("open"); }
  function closeSide() { if (ui.side) ui.side.classList.remove("open"); }

  // --- devices / scale -------------------------------------------------
  function populateDevices(manifest) {
    if (!ui.device) return;
    if (ui.device.options.length) return;
    var groups = {};
    (manifest.presets || []).forEach(function (preset) {
      addToGroup(groups, preset.group || "Telas", preset);
    });
    (manifest.profiles || []).forEach(function (profile) {
      addToGroup(groups, profile.group || "E-readers", profile);
    });
    Object.keys(groups).forEach(function (name) {
      var group = document.createElement("optgroup");
      group.label = name;
      groups[name].forEach(function (entry) { group.appendChild(entry); });
      ui.device.appendChild(group);
    });
    ui.device.addEventListener("change", function () {
      var chosen = deviceMap[ui.device.value];
      if (chosen) setDevice(chosen.width, chosen.height, chosen.gray_levels, chosen.name);
    });
  }

  var deviceMap = {};

  function addToGroup(groups, name, entry) {
    var option = document.createElement("option");
    var key = entry.slug || (entry.name + entry.width);
    option.value = key;
    option.textContent = entry.name + " · " + entry.width + "×" + entry.height;
    option.setAttribute("data-gray", String(entry.gray_levels || 16));
    groups[name] = groups[name] || [];
    groups[name].push(option);
    deviceMap[key] = entry;
  }

  function setDevice(width, height, gray, name) {
    width = clamp(Number(width) || 390, 120, 4000);
    height = clamp(Number(height) || 844, 120, 4000);
    state.device = { width: width, height: height, gray: Number(gray) || 16, name: name || "" };
    if (ui.viewport) {
      ui.viewport.style.setProperty("--rw", String(width));
      ui.viewport.style.setProperty("--rh", String(height));
    }
    if (ui.width) ui.width.value = String(width);
    if (ui.height) ui.height.value = String(height);
    applyQuantize(state.device.gray);
    applyScale();
  }

  function applyScale() {
    if (!ui.stage || !ui.viewport) return;
    var box = ui.stage.getBoundingClientRect();
    var padding = 32;
    var availableW = Math.max(120, box.width - padding);
    var availableH = Math.max(120, box.height - padding);
    var fit = Math.min(availableW / state.device.width, availableH / state.device.height, 1);
    var zoom = (Number(prefs.zoom) || 100) / 100;
    state.scale = Math.max(0.05, fit * zoom);
    ui.viewport.style.setProperty("--rz", state.scale.toFixed(4));
    if (ui.zoomOut) ui.zoomOut.textContent = Math.round(zoom * 100) + "%";
  }

  function applyQuantize(levels) {
    var steps = Math.max(2, Math.min(32, Number(levels) || 16));
    var values = [];
    for (var i = 0; i < steps; i += 1) values.push((i / (steps - 1)).toFixed(4));
    var table = values.join(" ");
    root.querySelectorAll("[data-func]").forEach(function (node) {
      node.setAttribute("tableValues", table);
    });
  }

  function applyTheme() {
    root.setAttribute("data-theme", prefs.theme);
    refreshIframeStyles();
  }

  function refreshIframeStyles() {
    var iframe = ui.content.querySelector("iframe.reader-iframe");
    if (iframe && iframe.contentDocument) applyIframeStyles(iframe.contentDocument);
  }

  // --- prefs / controls ------------------------------------------------
  function bindPrefs() {
    root.querySelectorAll("[data-pref]").forEach(function (control) {
      var key = control.getAttribute("data-pref");
      if (control.type === "checkbox") control.checked = !!prefs[key];
      else control.value = String(prefs[key]);
      control.addEventListener("input", function () {
        prefs[key] = control.type === "checkbox" ? control.checked : control.value;
        savePrefs();
        onPrefChange(key);
      });
    });

    if (ui.layout) ui.layout.checked = !!prefs.layout;
    if (ui.cover) ui.cover.checked = !!prefs.cover;
    if (ui.pdfImage) ui.pdfImage.checked = !!prefs.pdfImage;

    if (ui.zoom) {
      ui.zoom.value = String(prefs.zoom);
      ui.zoom.addEventListener("input", function () {
        prefs.zoom = Number(ui.zoom.value);
        savePrefs();
        applyScale();
      });
    }
    if (ui.eink) {
      ui.eink.setAttribute("aria-pressed", prefs.eink ? "true" : "false");
      ui.eink.addEventListener("click", function () {
        prefs.eink = !prefs.eink;
        savePrefs();
        ui.eink.setAttribute("aria-pressed", prefs.eink ? "true" : "false");
        root.classList.toggle("is-eink", prefs.eink);
        if (state.mode === "comic" || state.mode === "image" ||
            (state.mode === "pdf-native")) renderCurrent();
      });
    }
    if (prefs.eink) root.classList.add("is-eink");

    if (ui.rotate) ui.rotate.addEventListener("click", function () {
      setDevice(state.device.height, state.device.width, state.device.gray, state.device.name);
    });
    if (ui.width) ui.width.addEventListener("change", function () {
      setDevice(ui.width.value, state.device.height, state.device.gray, "Personalizado");
      persistDevice();
    });
    if (ui.height) ui.height.addEventListener("change", function () {
      setDevice(state.device.width, ui.height.value, state.device.gray, "Personalizado");
      persistDevice();
    });
    if (ui.sideOpen) ui.sideOpen.addEventListener("click", openSide);
    if (ui.sideClose) ui.sideClose.addEventListener("click", closeSide);
    if (ui.prev) ui.prev.addEventListener("click", function () { go(-1); });
    if (ui.next) ui.next.addEventListener("click", function () { go(1); });
    if (ui.slider) ui.slider.addEventListener("input", function () { goto(Number(ui.slider.value)); });
    if (ui.layout) ui.layout.addEventListener("change", function () {
      prefs.layout = ui.layout.checked; state.dual = ui.layout.checked; state.cover = false;
      if (ui.cover) ui.cover.checked = false;
      savePrefs();
      if (state.mode === "comic") { state.page = Math.floor(state.page / 2) * 2; renderPage(); }
    });
    if (ui.cover) ui.cover.addEventListener("change", function () {
      prefs.cover = ui.cover.checked; state.cover = ui.cover.checked; savePrefs();
      if (state.mode === "comic") renderPage();
    });
    if (ui.pdfImage) ui.pdfImage.addEventListener("change", function () {
      prefs.pdfImage = ui.pdfImage.checked; savePrefs();
      if (state.manifest && state.manifest.kind === "pdf") {
        if (prefs.pdfImage) setupPaged("pdf"); else setupPdfNative();
      }
    });
  }

  function persistDevice() {
    prefs.device = { width: state.device.width, height: state.device.height, gray: state.device.gray };
    savePrefs();
  }

  function onPrefChange(key) {
    if (key === "theme") applyTheme();
    else if (key === "prefs" || key === "font" || key === "size" ||
             key === "line" || key === "margin" || key === "justify") {
      refreshIframeStyles();
    }
  }

  function renderCurrent() {
    if (state.mode === "comic" || state.mode === "image" || state.mode === "pdf") renderPage();
  }

  // --- gestures --------------------------------------------------------
  function bindStageGestures() {
    if (!ui.stage) return;
    var startX = 0;
    var startY = 0;
    var active = false;
    ui.stage.addEventListener("touchstart", function (event) {
      if (event.touches.length !== 1) return;
      startX = event.touches[0].clientX;
      startY = event.touches[0].clientY;
      active = true;
    }, { passive: true });
    ui.stage.addEventListener("touchend", function (event) {
      if (!active) return;
      active = false;
      var touch = event.changedTouches[0];
      var dx = touch.clientX - startX;
      var dy = touch.clientY - startY;
      if (Math.abs(dx) < 45 || Math.abs(dx) < Math.abs(dy)) return;
      var rtl = state.manifest && state.manifest.manga_rtl;
      var forward = rtl ? dx > 0 : dx < 0;
      go(forward ? 1 : -1);
    }, { passive: true });
  }

  // --- helpers ---------------------------------------------------------
  function clamp(value, low, high) {
    return Math.max(low, Math.min(high, value));
  }

  function replaceContent(node) {
    ui.content.textContent = "";
    ui.content.appendChild(node);
    if (ui.loading) ui.loading.hidden = true;
  }

  function showLoader(text) {
    if (!ui.loading) return;
    ui.loading.hidden = false;
    if (ui.loadingText && text) ui.loadingText.textContent = text;
  }

  function hideLoader() {
    if (ui.loading) ui.loading.hidden = true;
  }

  function showState(kind, title, message, manifest) {
    if (!ui.state) return;
    hideLoader();
    ui.state.textContent = "";
    ui.state.hidden = false;
    var badge = document.createElement("span");
    badge.className = "reader-state__badge";
    badge.textContent = kind === "preparing" ? "preparando" : kind === "error" ? "erro" : "não disponível";
    var heading = document.createElement("h2");
    heading.textContent = title;
    var paragraph = document.createElement("p");
    paragraph.textContent = message || "";
    ui.state.appendChild(badge);
    ui.state.appendChild(heading);
    ui.state.appendChild(paragraph);

    var row = document.createElement("div");
    row.className = "button-row";
    var download = document.createElement("a");
    download.className = "button";
    download.href = root.getAttribute("data-download") || "#";
    download.textContent = "Baixar o arquivo";
    row.appendChild(download);
    if (manifest && manifest.urls && manifest.urls.raw) {
      var raw = document.createElement("a");
      raw.className = "button ghost";
      raw.href = manifest.urls.raw;
      raw.target = "_blank";
      raw.rel = "noopener";
      raw.textContent = "Abrir em nova aba";
      row.appendChild(raw);
    }
    ui.state.appendChild(row);
    if (ui.footbar) ui.footbar.hidden = true;
  }

  function hideState() {
    if (ui.state) { ui.state.hidden = true; ui.state.textContent = ""; }
  }

  function show(nodes, visible) {
    nodes.forEach(function (node) { node.hidden = !visible; });
  }

  function chapterUrl(key) {
    var encoded = String(key || "").split("/").map(encodeURIComponent).join("/");
    return state.manifest.urls.chapter.replace("{key}", encoded);
  }
})();
