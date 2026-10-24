/* Provider UID autofill for the console form.
 *
 * Mirrors `slugify_uid` in `app/core/provider_uid.py`: the name up to the first
 * dot, lowercased, anything outside `a-z0-9_-` collapsed to a dash, edge dashes
 * trimmed. The server normalises the submitted value again, so this is a
 * convenience, never the rule.
 *
 * The field stops following the name as soon as a human types in it or the
 * page renders a stored row — a UID that links already point at must not
 * change just because the provider got renamed.
 */
(() => {
  "use strict";
  const slugify = (value) =>
    String(value || "")
      .split(".")[0]
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9_-]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 100);
  document.querySelectorAll("[data-uid-autofill]").forEach((uid) => {
    const source = document.getElementById(uid.dataset.uidAutofill);
    if (!source || uid.readOnly || uid.disabled) return;
    if (uid.value) return;
    let manual = false;
    uid.addEventListener("input", () => {
      manual = true;
    });
    source.addEventListener("input", () => {
      if (manual) return;
      uid.value = slugify(source.value);
    });
  });
})();
