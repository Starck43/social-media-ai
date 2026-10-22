/* Personal catalog forms: no requests or credentials stored in the browser. */
(() => {
  const root = document.querySelector("[data-llm-catalog]");
  if (!root) return;
  const providers = JSON.parse(
    root.querySelector("[data-llm-providers]").textContent,
  );
  let serial = 0;
  root.querySelector("[data-llm-open]").addEventListener("click", () => {
    const editor = root.querySelector("#llm-add");
    editor.open = true;
    editor.querySelector("[data-popover-trigger]").focus();
  });
  root.querySelectorAll("[data-llm-bundle]").forEach((form) => {
    const rows = form.querySelector("[data-llm-rows]");
    form.querySelector("[data-llm-add]").addEventListener("click", () => {
      if (rows.children.length >= 50) return;
      const fragment = root
        .querySelector("[data-llm-row-template]")
        .content.cloneNode(true);
      const suffix = `added-${++serial}`;
      fragment.querySelectorAll("[id]").forEach((el) => {
        el.id = el.id.replace("INDEX", suffix);
      });
      fragment.querySelectorAll("[for]").forEach((el) => {
        el.htmlFor = el.htmlFor.replace("INDEX", suffix);
      });
      rows.append(fragment);
      rows.lastElementChild.querySelector("[data-model-api]").focus();
    });
    rows.addEventListener("click", (event) => {
      const button = event.target.closest("[data-llm-remove]");
      if (!button) return;
      button.closest("[data-llm-row]").remove();
      form.querySelector("[data-llm-add]").focus();
    });
    const syncRow = (row) => {
      const api = row.querySelector("[data-model-api]");
      const name = row.querySelector("[data-model-name]");
      const existing = row.querySelector("[name=model_db_id]").value;
      api.required = name.required = Boolean(
        existing || api.value.trim() || name.value.trim(),
      );
    };
    rows.addEventListener("input", (event) => {
      const row = event.target.closest("[data-llm-row]");
      if (row) syncRow(row);
    });
    rows.querySelectorAll("[data-llm-row]").forEach(syncRow);
    form.addEventListener("submit", (event) => {
      rows.querySelectorAll("[data-llm-row]").forEach(syncRow);
      if (!form.checkValidity()) {
        event.preventDefault();
        form.reportValidity();
      }
    });
    if (!form.hasAttribute("data-llm-new")) return;
    const name = form.elements.provider_name;
    const uid = form.elements.provider_uid;
    let manualUid = false;
    uid.addEventListener("input", () => {
      manualUid = true;
    });
    name.addEventListener("input", () => {
      if (!manualUid && !form.elements.provider_id.value) {
        uid.value = name.value
          .split(".")[0]
          .toLowerCase()
          .trim()
          .replace(/[^a-z0-9_-]+/g, "-")
          .replace(/^-+|-+$/g, "");
      }
    });
    form.elements.existing_provider.addEventListener("input", (event) => {
      const provider = providers.find(
        (item) => String(item.id) === event.target.value,
      );
      form.elements.provider_id.value = provider ? provider.id : "";
      name.value = provider ? provider.name : "";
      uid.value = provider ? provider.uid : "";
      form.elements.base_url.value = provider ? provider.base_url || "" : "";
      form.elements.api_key.value = "";
      form.querySelector("[data-llm-key-summary]").textContent =
        provider?.has_api_key ? "Заменить API-ключ" : "Добавить API-ключ";
      manualUid = Boolean(provider);
    });
  });
})();
