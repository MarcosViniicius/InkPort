/* Progressive enhancements: mobile drawer, flash dismissal, drag & drop
   uploads, library view toggle. Everything works without this file. */

(function () {
  "use strict";

  /* --- mobile drawer --------------------------------------------------- */
  var scrim = document.querySelector(".scrim");
  var navPanel = document.querySelector(".nav");
  var lastToggle = null;

  function focusables(container) {
    if (!container) return [];
    return Array.prototype.filter.call(
      container.querySelectorAll('a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])'),
      function (el) { return el.offsetParent !== null; }
    );
  }

  function setNav(open) {
    document.body.classList.toggle("nav-open", open);
    document.body.style.overflow = open ? "hidden" : "";
    document.querySelectorAll("[data-nav-toggle]").forEach(function (button) {
      button.setAttribute("aria-expanded", open ? "true" : "false");
    });
    if (scrim) scrim.classList.toggle("on", open);

    // Keep the keyboard inside the drawer while it is open (it covers the page).
    if (open) {
      var items = focusables(navPanel);
      if (items.length) items[0].focus();
    } else if (lastToggle) {
      lastToggle.focus();
    }
  }

  // Single source of truth for the drawer: base.html must not bind this too,
  // otherwise the menu opens and closes on the same tap.
  window.__opdsNavBound = true;
  document.querySelectorAll("[data-nav-toggle]").forEach(function (button) {
    button.addEventListener("click", function (event) {
      event.preventDefault();
      lastToggle = button;
      setNav(!document.body.classList.contains("nav-open"));
    });
  });
  if (scrim) scrim.addEventListener("click", function () { setNav(false); });
  document.querySelectorAll("[data-nav-close]").forEach(function (el) {
    el.addEventListener("click", function () { setNav(false); });
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") setNav(false);
    if (event.key !== "Tab" || !document.body.classList.contains("nav-open")) return;
    var items = focusables(navPanel);
    if (!items.length) return;
    var first = items[0];
    var last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
  // Close the drawer after navigating.
  document.querySelectorAll(".nav a").forEach(function (link) {
    link.addEventListener("click", function () { setNav(false); });
  });

  /* --- copy to clipboard ------------------------------------------------ */
  document.querySelectorAll("[data-copy]").forEach(function (button) {
    button.addEventListener("click", function () {
      var text = button.getAttribute("data-copy") || "";
      var original = button.innerHTML;
      function feedback(ok) {
        button.textContent = ok ? "Copiado!" : "Copie manualmente";
        setTimeout(function () { button.innerHTML = original; }, 1800);
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () { feedback(true); }, function () { feedback(false); });
      } else {
        feedback(false);
      }
    });
  });

  /* --- remember the last device profile -------------------------------- */
  (function () {
    var KEY = "opds-device";
    var selects = document.querySelectorAll("[data-device-select]");
    if (!selects.length) return;
    var saved = null;
    try { saved = localStorage.getItem(KEY); } catch (e) { /* private mode */ }
    selects.forEach(function (select) {
      if (saved) {
        Array.prototype.forEach.call(select.options, function (option) {
          if (option.value === saved) select.value = saved;
        });
      }
      select.addEventListener("change", function () {
        try { localStorage.setItem(KEY, select.value); } catch (e) { /* ignore */ }
      });
    });
  })();

  /* --- do not submit twice (and say what is happening) ------------------- */
  document.querySelectorAll("form").forEach(function (form) {
    // GET forms (searches, filters) are harmless to repeat; the confirm dialog
    // must stay interactive.
    if (form.method === "get" || form.method === "dialog") return;
    form.addEventListener("submit", function (event) {
      // Wait for the other listeners: a confirmation dialog sets defaultPrevented
      // and the form must stay clickable until the user answers it.
      setTimeout(function () {
        if (event.defaultPrevented || form.dataset.submitting === "1") return;
        form.dataset.submitting = "1";
        var button = event.submitter || form.querySelector('button[type="submit"], button:not([type])');
        if (button && !button.hasAttribute("data-no-busy")) {
          button.disabled = true;
          if (!button.classList.contains("link") && button.textContent.length < 40) {
            button.textContent = "Enviando…";
          }
        }
      }, 0);
    });
  });

  /* --- flash dismissal ------------------------------------------------- */
  document.querySelectorAll("[data-flash-close]").forEach(function (button) {
    button.addEventListener("click", function () {
      var flash = button.closest("[data-flash]");
      if (flash) flash.remove();
    });
  });

  /* --- drag & drop upload ---------------------------------------------- */
  document.querySelectorAll("[data-drop]").forEach(function (zone) {
    var input = zone.querySelector('input[type="file"]');
    if (!input) return;

    ["dragenter", "dragover"].forEach(function (type) {
      zone.addEventListener(type, function (event) {
        event.preventDefault();
        zone.classList.add("dragging");
      });
    });
    ["dragleave", "drop"].forEach(function (type) {
      zone.addEventListener(type, function () {
        zone.classList.remove("dragging");
      });
    });
    zone.addEventListener("drop", function (event) {
      event.preventDefault();
      if (event.dataTransfer && event.dataTransfer.files.length) {
        input.files = event.dataTransfer.files;
        var name = zone.querySelector("[data-drop-name]");
        if (name) name.textContent = input.files.length + " arquivo(s) selecionado(s)";
      }
    });
    input.addEventListener("change", function () {
      var name = zone.querySelector("[data-drop-name]");
      if (name && input.files.length) {
        name.textContent = input.files.length + " arquivo(s) selecionado(s)";
      }
    });
  });

  /* --- reveal/disable fields behind a switch --------------------------- */
  /* Progressive enhancement for the import/conversion forms: with the switch
     off, the target options get out of the way (and are not submitted). Without
     JS they simply stay visible and submitted, so nothing breaks. */
  document.querySelectorAll("[data-toggle-target]").forEach(function (control) {
    var target = document.querySelector(control.getAttribute("data-toggle-target"));
    if (!target) return;
    function sync() {
      target.hidden = !control.checked;
      target.querySelectorAll("input, select, textarea").forEach(function (el) {
        el.disabled = !control.checked;
      });
    }
    control.addEventListener("change", sync);
    sync();
  });

  /* --- library view toggle (grid/list) --------------------------------- */
  var toggle = document.querySelector("[data-view-toggle]");
  if (toggle) {
    var KEY = "opds-view";
    function apply(view) {
      document.querySelectorAll("[data-view]").forEach(function (block) {
        var hidden = block.getAttribute("data-view") !== view;
        block.hidden = hidden;
        // Disabled inputs are not submitted: avoids sending the same book twice
        // when the grid and the list both contain the selection checkboxes.
        block.querySelectorAll("input, select, textarea, button").forEach(function (el) {
          if (el.hasAttribute("data-keep-enabled")) return;
          el.disabled = hidden;
        });
      });
      toggle.querySelectorAll("button").forEach(function (button) {
        var on = button.getAttribute("data-set-view") === view;
        button.classList.toggle("on", on);
        button.setAttribute("aria-pressed", on ? "true" : "false");
      });
      try { localStorage.setItem(KEY, view); } catch (e) { /* private mode */ }
    }
    var saved = "grid";
    try { saved = localStorage.getItem(KEY) || "grid"; } catch (e) { /* ignore */ }
    apply(saved);
    toggle.querySelectorAll("button").forEach(function (button) {
      button.addEventListener("click", function () {
        apply(button.getAttribute("data-set-view"));
      });
    });
  }

  /* --- collapsible panels (opened on desktop, closed on phones) --------- */
  document.querySelectorAll("details[data-collapse-sm]").forEach(function (panel) {
    if (window.matchMedia("(max-width: 720px)").matches) {
      panel.removeAttribute("open");
    }
  });

  /* --- deep link into a collapsed section (e.g. /settings#ferramentas) --- */
  (function () {
    if (!location.hash || location.hash.length < 2) return;
    var target = document.getElementById(location.hash.slice(1));
    if (target && target.tagName === "DETAILS") target.open = true;
  })();

  /* --- settings: sections that remember, and unsaved-change feedback ----- */
  (function () {
    var onPhone = window.matchMedia("(max-width: 720px)").matches;
    function read(key) {
      try { return localStorage.getItem(key); } catch (e) { return null; }
    }
    function write(key, value) {
      try { localStorage.setItem(key, value); } catch (e) { /* private mode */ }
    }

    document.querySelectorAll("details[data-sect]").forEach(function (panel) {
      var key = "opds-sect-" + panel.getAttribute("data-sect");
      var saved = read(key);
      if (saved === "1") panel.open = true;
      else if (saved === "0") panel.open = false;
      // No saved choice on a phone: the page reads as a short menu.
      else if (onPhone) panel.open = false;
      panel.addEventListener("toggle", function () {
        write(key, panel.open ? "1" : "0");
      });
    });

    var form = document.getElementById("settings-form");
    if (!form) return;
    var state = form.querySelector("[data-save-state]");
    var reset = form.querySelector("[data-save-reset]");

    function snapshot() {
      var parts = [];
      Array.prototype.forEach.call(form.elements, function (el) {
        if (!el.name) return;
        if (el.type === "checkbox") parts.push(el.name + "=" + (el.checked ? "1" : "0"));
        else if (el.type === "radio") { if (el.checked) parts.push(el.name + "=" + el.value); }
        else parts.push(el.name + "=" + el.value);
      });
      return parts.join("&");
    }
    var baseline = snapshot();

    function sync() {
      var dirty = snapshot() !== baseline;
      if (state) {
        state.textContent = dirty ? "Alterações não salvas" : "Tudo salvo";
        state.classList.toggle("dirty", dirty);
      }
    }
    form.addEventListener("input", sync);
    form.addEventListener("change", sync);
    if (reset) {
      reset.addEventListener("click", function () {
        form.reset();
        sync();
      });
    }
    sync();
  })();

  /* --- selection counter + select-all (library) ------------------------- */
  (function () {
    var bar = document.querySelector("[data-selection-bar]");
    if (!bar) return;
    var form = bar.closest("form");
    if (!form) return;
    var counter = document.getElementById("selection-count");

    function boxes() {
      return form.querySelectorAll(".row-check");
    }

    function sync() {
      var all = boxes();
      var checked = 0;
      all.forEach(function (box) { if (box.checked) checked += 1; });
      if (counter) {
        counter.textContent = checked === 0 ? "nenhum selecionado"
          : checked === 1 ? "1 selecionado"
          : checked + " selecionados";
      }
      // Keeps the grid checkboxes visible while a selection exists.
      document.body.classList.toggle("pick", checked > 0);
      document.querySelectorAll("[data-check-all]").forEach(function (master) {
        master.checked = checked > 0 && checked === all.length;
        master.indeterminate = checked > 0 && checked < all.length;
      });
    }

    form.addEventListener("change", function (event) {
      var master = event.target.closest("[data-check-all]");
      if (master) {
        boxes().forEach(function (box) { box.checked = master.checked; });
      }
      sync();
    });

    sync();
  })();
  /* --- layout debug (only with ?debug=layout) --------------------------- */
  if (location.search.indexOf("debug=layout") !== -1) {
    var worst = null;
    document.querySelectorAll("body *").forEach(function (el) {
      if (el.id === "layout-debug") return;
      var box = el.getBoundingClientRect();
      if (box.width > 0 && box.right > window.innerWidth + 1) {
        if (!worst || box.right > worst.box.right) worst = { el: el, box: box };
      }
    });
    var lines = [];
    if (worst) {
      var node = worst.el;
      while (node && node !== document.body) {
        var b = node.getBoundingClientRect();
        var cls = String(node.className || "").split(" ").slice(0, 2).join(".");
        lines.push(
          node.tagName.toLowerCase() + (cls ? "." + cls : "") +
          "  left=" + Math.round(b.left) + " w=" + Math.round(b.width)
        );
        node = node.parentElement;
      }
    }
    var out = document.createElement("pre");
    out.id = "layout-debug";
    out.setAttribute("style", "position:fixed;left:0;top:0;right:0;z-index:9999;background:#111;color:#0f0;font:10px/1.3 monospace;padding:5px;margin:0;white-space:pre-wrap;max-height:70vh;overflow:auto");
    out.textContent =
      "innerWidth=" + window.innerWidth + "  scrollWidth=" + document.documentElement.scrollWidth +
      (lines.length ? "\nPILHA (do que estoura):\n" + lines.slice(0, 9).join("\n") : "\nsem estouro");
    document.body.insertBefore(out, document.body.firstChild);
  }
})();
