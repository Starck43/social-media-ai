/* Same-origin related editor. Normal SQLAdmin forms own all writes. */
(() => {
  "use strict";
  const read = (id) => {
    const node = document.getElementById(id);
    return node ? JSON.parse(node.textContent) : null;
  };
  const saved = read("admin-related-saved");
  if (saved) {
    document.getElementById("related-close").addEventListener("click", () => window.close());
    if (window.opener) {
      window.opener.postMessage(saved, window.location.origin);
      window.close();
    }
    return;
  }
  const controls = read("admin-related-controls");
  if (!controls) return;
  const token = read("admin-related-token") || new URL(window.location.href).searchParams.get("_related_token");
  if (token && /^[a-f0-9]{32}$/.test(token)) {
    document.querySelectorAll('form[method="POST" i]').forEach((form) => {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "_related_token";
      input.value = token;
      form.append(input);
    });
  }
  const pending = new Map();
  window.addEventListener("message", (event) => {
    if (event.origin !== window.location.origin || !event.data || typeof event.data !== "object") return;
    const data = event.data;
    const entry = pending.get(data.token);
    if (!entry || event.source !== entry.popup || data.type !== "admin-related-saved" ||
        data.identity !== entry.control.identity || typeof data.id !== "string" || !data.id ||
        typeof data.label !== "string" || (entry.mode === "edit" && data.id !== entry.pk)) return;
    pending.delete(data.token);
    const select = entry.select;
    let option = Array.from(select.options).find((item) => item.value === data.id);
    if (!option) {
      option = new Option(data.label, data.id, false, false);
      select.add(option);
    } else option.textContent = data.label;
    if (entry.mode === "create") {
      if (!select.multiple) Array.from(select.options).forEach((item) => { item.selected = false; });
      option.selected = true;
    }
    if (window.jQuery && window.jQuery(select).data("select2")) window.jQuery(select).trigger("change");
    else select.dispatchEvent(new Event("change", { bubbles: true }));
    entry.status.textContent = "Запись сохранена. Связь применится после сохранения основной формы.";
    entry.button.focus();
  });
  const icon = (mode) => {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    const path = document.createElementNS(svg.namespaceURI, "path");
    path.setAttribute("d", mode === "create" ? "M12 5v14M5 12h14" :
      "m16 3 5 5M4 15 16 3a2 2 0 0 1 5 5L9 20l-6 1 1-6Z");
    svg.append(path);
    return svg;
  };
  controls.forEach((control) => {
    const select = document.getElementById(control.field);
    const transfer = select?.nextElementSibling?.matches('[data-filtered-select-ui="plain"]')
      ? select.nextElementSibling : null;
    if (!select || select.tagName !== "SELECT" || select.disabled || (select.hidden && !transfer) ||
        select.hasAttribute("readonly") || select.dataset.permissionTransfer || select.parentElement.closest("[hidden]")) return;
    const bar = document.createElement("div");
    bar.className = "admin-related-actions";
    bar.setAttribute("role", "group");
    bar.setAttribute("aria-label", `Действия со связью: ${control.label}`);
    const status = document.createElement("small");
    status.className = "admin-related-status text-muted";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    const selected = () => Array.from(select.selectedOptions).filter((item) => item.value && item.value !== "__None");
    const addButton = (mode, title, base) => {
      if (!base) return;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "admin-related-icon";
      button.title = `${title} (отдельное окно)`;
      button.append(icon(mode));
      button.setAttribute("aria-label", `${title}: ${control.label} (отдельное окно)`);
      button.addEventListener("click", () => {
        const items = selected();
        if (mode === "edit" && items.length !== 1) return;
        const pk = mode === "edit" ? items[0].value : null;
        const bytes = new Uint8Array(16);
        window.crypto.getRandomValues(bytes);
        const nonce = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
        const url = new URL(mode === "edit" ? base.replace("__related_pk__", encodeURIComponent(pk)) : base);
        if (url.origin !== window.location.origin) return;
        url.searchParams.set("_related_token", nonce);
        const popup = window.open(url.href, `related_${nonce}`, "popup,width=1000,height=800,scrollbars=yes,resizable=yes");
        if (!popup) {
          status.textContent = "Окно заблокировано. Разрешите всплывающие окна для этого сайта и повторите.";
          return;
        }
        pending.set(nonce, { popup, select, control, mode, pk, status, button });
        status.textContent = "Сохраните запись в отдельном окне. Закрытие без сохранения не меняет связь.";
      });
      if (mode === "edit") {
        const refresh = () => {
          button.disabled = selected().length !== 1;
          button.title = button.disabled ? "Для редактирования выберите ровно одну запись" : "Редактировать выбранную запись";
        };
        select.addEventListener("change", refresh);
        if (window.jQuery) window.jQuery(select).on("change", refresh);
        refresh();
      }
      bar.append(button);
    };
    addButton("create", "+ Добавить", control.create);
    addButton("edit", "Редактировать выбранную", control.edit);
    if (bar.childElementCount) {
      // Keep native inputs and existing/later Select2 containers in the same
      // flexible slot. Never recreate the select or its event handlers.
      const row = document.createElement("div");
      row.className = "admin-related-field";
      const input = document.createElement("div");
      input.className = "admin-related-input";
      const select2 = select.nextElementSibling?.matches(".select2-container") ? select.nextElementSibling : null;
      select.before(row);
      input.append(select);
      if (select2) input.append(select2);
      if (transfer) input.append(transfer);
      row.append(input, bar);
      row.after(status);
    }
  });
})();
