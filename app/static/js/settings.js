/* Keep implicit Enter on numeric fields distinct from the adjacent model probe. */
(() => {
  const menu = document.querySelector(".settings-menu");
  if (menu) {
    const desktop = window.matchMedia("(min-width: 1024px)");
    const syncMenu = () => {
      menu.open = desktop.matches;
    };
    syncMenu();
    desktop.addEventListener("change", syncMenu);
  }
  const form = document.getElementById("agent-settings");
  if (!form) return;
  form.addEventListener("keydown", (event) => {
    if (
      event.key !== "Enter" ||
      event.isComposing ||
      !(event.target instanceof HTMLInputElement)
    )
      return;
    const save = form.querySelector("[data-agent-save]");
    if (!save || save.disabled) return;
    event.preventDefault();
    form.requestSubmit(save);
  });
})();
