/* Confirmation dialog for actions that can lose data.

Attributes (on the form or on the specific submit button):
  data-confirm="Pergunta curta"
  data-confirm-msg="Explicação do que vai acontecer"
  data-confirm-detail="Detalhe extra (aceita contagem automática)"
  data-confirm-count=".row-check:checked"   -> prefixa "N item(ns) selecionado(s)"
  data-confirm-ok="Rótulo do botão de confirmar"
  data-confirm-danger                        -> botão vermelho

The markup keeps `onsubmit="return confirm(...)"` as a no-JS fallback; this file
removes it and takes over. If <dialog> is unsupported, the native confirm stays.
*/

(function () {
  "use strict";

  var dialog = document.getElementById("confirm-dialog");
  if (!dialog || typeof dialog.showModal !== "function") return;

  var titleEl = document.getElementById("confirm-title");
  var messageEl = document.getElementById("confirm-message");
  var detailEl = document.getElementById("confirm-detail");
  var okButton = document.getElementById("confirm-ok");
  var pendingForm = null;
  var pendingButton = null;

  function attr(source, name, fallback) {
    if (!source) return fallback === undefined ? null : fallback;
    var value = source.getAttribute(name);
    return value === null ? (fallback === undefined ? null : fallback) : value;
  }

  var forms = [];
  function remember(form) {
    if (form && forms.indexOf(form) === -1) forms.push(form);
  }
  document.querySelectorAll("form[data-confirm]").forEach(remember);
  document.querySelectorAll("button[data-confirm]").forEach(function (button) {
    remember(button.form);
  });

  forms.forEach(function (form) {
    form.removeAttribute("onsubmit");
    form.querySelectorAll("button[data-confirm]").forEach(function (button) {
      button.removeAttribute("onclick");
    });

    form.addEventListener("submit", function (event) {
      if (form.dataset.confirmed === "1") return;
      var button = event.submitter || null;
      var question = attr(button, "data-confirm", attr(form, "data-confirm"));
      if (!question) return;

      event.preventDefault();
      titleEl.textContent = question;
      messageEl.textContent = attr(button, "data-confirm-msg", attr(form, "data-confirm-msg", ""));

      var detail = attr(button, "data-confirm-detail", attr(form, "data-confirm-detail", ""));
      var selector = attr(button, "data-confirm-count", attr(form, "data-confirm-count"));
      if (selector) {
        var total = document.querySelectorAll(selector).length;
        var counted = total === 0 ? "Nenhum item selecionado."
          : total === 1 ? "1 item selecionado."
          : total + " itens selecionados.";
        detail = counted + (detail ? " " + detail : "");
      }
      detailEl.textContent = detail;
      detailEl.hidden = !detail;

      okButton.textContent = attr(button, "data-confirm-ok", attr(form, "data-confirm-ok", "Confirmar"));
      var danger = (button && button.hasAttribute("data-confirm-danger")) ||
        (!button && form.hasAttribute("data-confirm-danger"));
      okButton.classList.toggle("danger", danger);

      pendingForm = form;
      pendingButton = button;
      dialog.showModal();
    });
  });

  function submitConfirmed(form, button) {
    form.dataset.confirmed = "1";
    if (button && form.requestSubmit) {
      form.requestSubmit(button); // keeps the button's name/value in the post
      return;
    }
    if (button && button.name) {
      var hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = button.name;
      hidden.value = button.value;
      form.appendChild(hidden);
    }
    form.submit();
  }

  okButton.addEventListener("click", function () {
    if (!pendingForm) return;
    var form = pendingForm;
    var button = pendingButton;
    pendingForm = null;
    pendingButton = null;
    dialog.close();
    submitConfirmed(form, button);
  });

  dialog.addEventListener("close", function () {
    pendingForm = null;
    pendingButton = null;
  });
})();
