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

  /* --- import type switch (one panel at a time) ------------------------ */
  /* The unified import form shows only the fields for the chosen kind. Without
     JS every panel stays visible and the server validates by `kind`, so nothing
     breaks. `data-required` marks kind-specific fields: required only while
     their panel is active (a hidden required field would block the submit). */
  document.querySelectorAll("[data-import-form]").forEach(function (form) {
    var submitLabels = {
      files: "Importar", text: "Importar texto",
      url: "Baixar e importar", scan: "Escanear e importar"
    };
    function current() {
      var checked = form.querySelector('input[name="kind"]:checked');
      return checked ? checked.value : "files";
    }
    function sync() {
      var kind = current();
      form.querySelectorAll("[data-import-panel]").forEach(function (panel) {
        var on = panel.getAttribute("data-import-panel") === kind;
        panel.hidden = !on;
        panel.querySelectorAll("input, select, textarea").forEach(function (el) {
          if (el.name === "kind") return;
          el.disabled = !on;
          if (el.hasAttribute("data-required")) {
            if (on) el.setAttribute("required", "");
            else el.removeAttribute("required");
          }
        });
      });
      var label = form.querySelector("[data-import-submit-label]");
      if (label && submitLabels[kind]) label.textContent = submitLabels[kind];
    }
    form.querySelectorAll('input[name="kind"]').forEach(function (radio) {
      radio.addEventListener("change", sync);
    });
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
  /* --- category tree-select (import cards) ------------------------------- */
  /* Progressive enhancement: without JS the plain input + datalist is used.
     With JS the fallback becomes the submitted store and the dropdown tree
     is the visible control. New folders are client-side until the form is
     submitted (the server creates them via ensure_category). */
  (function () {
    var boxes = document.querySelectorAll("[data-catselect]");
    if (!boxes.length) return;
    var dataEl = document.getElementById("category-data");
    var globalPaths = [];
    if (dataEl) {
      try { globalPaths = JSON.parse(dataEl.textContent || "[]") || []; }
      catch (e) { globalPaths = []; }
    }
    var FOLDER_SVG = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none"'
      + ' stroke="currentColor" stroke-width="1.7" stroke-linecap="round"'
      + ' stroke-linejoin="round" aria-hidden="true"><path d="M3 6.5A2 2 0 0 1 5 4.5h3.6l1.8 2.2H19a2 2 0 0 1 2 2v8.3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';

    function normalizePath(raw) {
      if (!raw) return "";
      var parts = String(raw).split("/").map(function (s) { return s.trim(); })
        .filter(function (s) { return s.length > 0; });
      return parts.join("/");
    }
    function displayPath(path) {
      return String(path).split("/").map(function (s) { return s.trim(); })
        .filter(function (s) { return s.length > 0; }).join(" / ");
    }
    function cleanSegment(raw) {
      var s = String(raw || "").replace(/[\\/]+/g, " ").replace(/\s+/g, " ").trim();
      return s.slice(0, 80);
    }

    function closeAll(except) {
      boxes.forEach(function (box) {
        if (box === except) return;
        var m = box.querySelector("[data-catselect-menu]");
        var t = box.querySelector("[data-catselect-trigger]");
        if (m) m.hidden = true;
        if (t) t.setAttribute("aria-expanded", "false");
      });
    }

    boxes.forEach(function (box) {
      var fallback = box.querySelector(".catselect-fallback");
      var jsBox = box.querySelector("[data-catselect-js]");
      var trigger = box.querySelector("[data-catselect-trigger]");
      var menu = box.querySelector("[data-catselect-menu]");
      var labelEl = box.querySelector("[data-catselect-label]");
      var search = box.querySelector("[data-catselect-search]");
      var treeEl = box.querySelector("[data-catselect-tree]");
      var emptyEl = box.querySelector("[data-catselect-empty]");
      var newRootBtn = box.querySelector("[data-catselect-new-root]");
      if (!fallback || !jsBox || !trigger || !menu) return;

      var paths = new Set();
      globalPaths.forEach(function (p) {
        var n = normalizePath(p);
        if (n) paths.add(n);
      });
      // Datalist may carry fresher data than the JSON (same content normally).
      var list = document.getElementById(fallback.getAttribute("list") || "");
      if (list) {
        list.querySelectorAll("option").forEach(function (opt) {
          var n = normalizePath(opt.value);
          if (n) paths.add(n);
        });
      }
      var expanded = new Set();
      function markExpanded() {
        expanded = new Set();
        paths.forEach(function (p) {
          var parts = p.split("/");
          for (var i = 1; i < parts.length; i++) {
            expanded.add(parts.slice(0, i).join("/"));
          }
        });
      }

      // Become the visible control; the fallback stays as the submitted store.
      jsBox.hidden = false;
      fallback.hidden = true;
      fallback.tabIndex = -1;
      fallback.removeAttribute("required");
      fallback.value = normalizePath(fallback.value);
      if (fallback.value && !paths.has(fallback.value)) paths.add(fallback.value);
      markExpanded();
      syncLabel();

      function syncLabel() {
        var v = normalizePath(fallback.value);
        labelEl.textContent = v ? displayPath(v) : "Escolher categoria…";
        labelEl.classList.toggle("is-placeholder", !v);
        treeEl.querySelectorAll(".catselect-row").forEach(function (row) {
          row.classList.toggle("is-selected", normalizePath(row.getAttribute("data-path")) === v);
        });
      }

      function openMenu() {
        closeAll(box);
        menu.hidden = false;
        trigger.setAttribute("aria-expanded", "true");
        trigger.classList.remove("is-error");
        search.value = "";
        render();
        search.focus();
      }
      function closeMenu() {
        menu.hidden = true;
        trigger.setAttribute("aria-expanded", "false");
      }

      function makeRow(full, depth, hasKids) {
        var row = document.createElement("div");
        row.className = "catselect-row";
        row.setAttribute("data-path", full);
        row.style.paddingLeft = (depth * 16) + "px";

        var toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "catselect-toggle" + (hasKids ? "" : " no-kids");
        toggle.setAttribute("aria-label", hasKids ? "Expandir " + full : "");
        toggle.setAttribute("aria-expanded", hasKids && expanded.has(full) ? "true" : "false");
        toggle.tabIndex = hasKids ? 0 : -1;
        var tw = document.createElement("span");
        tw.className = "tw";
        tw.textContent = "›";
        toggle.appendChild(tw);
        if (hasKids) {
          toggle.addEventListener("click", function (event) {
            event.stopPropagation();
            if (expanded.has(full)) expanded.delete(full);
            else expanded.add(full);
            render();
          });
        }

        var name = document.createElement("button");
        name.type = "button";
        name.className = "catselect-name";
        name.setAttribute("role", "treeitem");
        name.setAttribute("aria-selected", normalizePath(fallback.value) === full ? "true" : "false");
        var fi = document.createElement("span");
        fi.className = "fi";
        fi.innerHTML = FOLDER_SVG;
        var nm = document.createElement("span");
        nm.className = "nm";
        var part = full.split("/").pop();
        nm.textContent = part;
        nm.title = displayPath(full);
        name.appendChild(fi);
        name.appendChild(nm);
        name.addEventListener("click", function () { select(full); });

        var add = document.createElement("button");
        add.type = "button";
        add.className = "catselect-add";
        add.textContent = "+";
        add.title = "Criar subpasta em " + displayPath(full);
        add.setAttribute("aria-label", "Criar subpasta em " + displayPath(full));
        add.addEventListener("click", function (event) {
          event.stopPropagation();
          openEditor(row, full);
        });

        row.appendChild(toggle);
        row.appendChild(name);
        row.appendChild(add);
        if (normalizePath(fallback.value) === full) row.classList.add("is-selected");
        return row;
      }

      function openEditor(afterRow, parentFull) {
        closeEditor();
        var wrap = document.createElement("div");
        wrap.className = "catselect-newline";
        wrap.setAttribute("data-catselect-editor", "1");
        var input = document.createElement("input");
        input.type = "text";
        input.placeholder = parentFull ? "Nome da subpasta…" : "Nome da nova pasta…";
        input.setAttribute("aria-label", parentFull
          ? "Nome da subpasta de " + displayPath(parentFull) : "Nome da nova pasta raiz");
        input.maxLength = 80;
        wrap.appendChild(input);
        if (afterRow && afterRow.parentNode) {
          afterRow.parentNode.insertBefore(wrap, afterRow.nextSibling);
        } else {
          treeEl.prepend(wrap);
        }
        input.focus();
        var done = false;
        function commit() {
          if (done) return;
          done = true;
          var clean = cleanSegment(input.value);
          closeEditor();
          if (!clean) return;
          var target = parentFull ? parentFull + "/" + clean : clean;
          createAndSelect(normalizePath(target));
        }
        function cancel() {
          if (done) return;
          done = true;
          closeEditor();
        }
        input.addEventListener("keydown", function (event) {
          if (event.key === "Enter") { event.preventDefault(); commit(); }
          else if (event.key === "Escape") { event.preventDefault(); cancel(); }
          event.stopPropagation();
        });
        input.addEventListener("blur", function () {
          setTimeout(function () {
            if (!done) {
              if (cleanSegment(input.value)) commit();
              else cancel();
            }
          }, 120);
        });
      }
      function closeEditor() {
        treeEl.querySelectorAll("[data-catselect-editor]").forEach(function (el) { el.remove(); });
      }

      function createAndSelect(path) {
        if (!path) return;
        var exists = Array.prototype.some.call(Array.from(paths), function (p) {
          return p.toLowerCase() === path.toLowerCase();
        });
        if (!exists) {
          window.dispatchEvent(new CustomEvent("catselect:created", { detail: { path: path } }));
        }
        select(path);
      }

      function select(path) {
        fallback.value = path;
        syncLabel();
        closeMenu();
        trigger.focus();
      }

      function render() {
        closeEditor();
        treeEl.innerHTML = "";
        var q = search.value.trim().toLowerCase();
        if (q) {
          var matches = Array.from(paths).filter(function (p) {
            return p.toLowerCase().indexOf(q) !== -1
              || displayPath(p).toLowerCase().indexOf(q) !== -1;
          }).sort(function (a, b) {
            return a.toLowerCase().localeCompare(b.toLowerCase());
          });
          emptyEl.hidden = matches.length > 0;
          matches.forEach(function (p) {
            var row = makeRow(p, 0, false);
            var nm = row.querySelector(".nm");
            if (nm) nm.textContent = displayPath(p);
            treeEl.appendChild(row);
          });
          return;
        }
        if (!paths.size) {
          emptyEl.hidden = false;
          emptyEl.textContent = "Nenhuma categoria ainda. Use o botão abaixo para criar a primeira.";
          return;
        }
        emptyEl.hidden = true;
        // Build a sorted hierarchy from the "/"-separated names.
        var root = new Map();
        Array.from(paths).sort(function (a, b) {
          return a.toLowerCase().localeCompare(b.toLowerCase());
        }).forEach(function (p) {
          var node = root;
          var acc = [];
          p.split("/").forEach(function (part) {
            acc.push(part);
            var full = acc.join("/");
            if (!node.has(part)) node.set(part, { full: full, kids: new Map() });
            node = node.get(part).kids;
          });
        });
        (function walk(node, depth, container) {
          node.forEach(function (entry) {
            var hasKids = entry.kids.size > 0;
            container.appendChild(makeRow(entry.full, depth, hasKids));
            if (hasKids && expanded.has(entry.full)) {
              var sub = document.createElement("div");
              sub.setAttribute("data-catselect-group", entry.full);
              container.appendChild(sub);
              walk(entry.kids, depth + 1, sub);
            }
          });
        })(root, 0, treeEl);
      }

      trigger.addEventListener("click", function () {
        if (menu.hidden) openMenu();
        else closeMenu();
      });
      search.addEventListener("input", render);
      search.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          closeMenu();
          trigger.focus();
        }
        event.stopPropagation();
      });
      menu.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
          event.preventDefault();
          closeMenu();
          trigger.focus();
        }
      });
      if (newRootBtn) {
        newRootBtn.addEventListener("click", function () {
          search.value = "";
          render();
          openEditor(null, "");
        });
      }
      window.addEventListener("catselect:created", function (event) {
        var p = event.detail && normalizePath(event.detail.path);
        if (!p || paths.has(p)) return;
        paths.add(p);
        markExpanded();
        if (list) {
          var opt = document.createElement("option");
          opt.value = p;
          list.appendChild(opt);
        }
        if (!menu.hidden) render();
        else syncLabel();
      });

      var form = box.closest("form");
      if (form && !form.dataset.catselectBound) {
        form.dataset.catselectBound = "1";
        form.addEventListener("submit", function (event) {
          var missing = [];
          form.querySelectorAll("[data-catselect]").forEach(function (b) {
            var fb = b.querySelector(".catselect-fallback");
            var tg = b.querySelector("[data-catselect-trigger]");
            if (fb && !normalizePath(fb.value)) missing.push(tg);
          });
          if (missing.length) {
            event.preventDefault();
            missing[0].classList.add("is-error");
            boxes.forEach(function (b) {
              var t = b.querySelector("[data-catselect-trigger]");
              if (t === missing[0]) openMenu();
            });
          }
        });
      }
    });

    document.addEventListener("click", function (event) {
      if (!event.target.closest("[data-catselect]")) closeAll(null);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closeAll(null);
    });
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
