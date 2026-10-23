/* Settings platforms page and SQLAdmin bulk delete with real templates/styles.
 * Every request must stay on file:; no application, database or network call.
 *
 * Usage: node tests/web_settings_admin.cjs <fixture-dir> <offline-vendor-dir>
 */
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");

const fixtures = path.resolve(process.argv[2] || "");
const vendor = path.resolve(process.argv[3] || "");
const widths = [320, 390, 768, 1440];
const states = ["platforms", "empty", "no-workspace", "admin"];

async function contrast(locator) {
  return locator.evaluate((el) => {
    const css = getComputedStyle(el);
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    const rgba = (value) => {
      context.clearRect(0, 0, 1, 1);
      context.fillStyle = value;
      context.fillRect(0, 0, 1, 1);
      const [r, g, b, a] = context.getImageData(0, 0, 1, 1).data;
      return [r, g, b, a / 255];
    };
    const composite = (foreground, background) => {
      const alpha = foreground[3] + background[3] * (1 - foreground[3]);
      if (!alpha) return [0, 0, 0, 0];
      return [0, 1, 2].map((index) =>
        (foreground[index] * foreground[3] + background[index] * background[3] * (1 - foreground[3])) / alpha,
      ).concat(alpha);
    };
    let backdrop = [0, 0, 0, 0];
    const ancestors = [];
    for (let node = el; node; node = node.parentElement) ancestors.push(node);
    for (const node of ancestors.reverse()) backdrop = composite(rgba(getComputedStyle(node).backgroundColor), backdrop);
    const luminance = (value) =>
      value
        .slice(0, 3)
        .map(Number)
        .map((part) => {
          part /= 255;
          return part <= 0.04045 ? part / 12.92 : ((part + 0.055) / 1.055) ** 2.4;
        })
        .reduce((sum, part, index) => sum + part * [0.2126, 0.7152, 0.0722][index], 0);
    const foregroundColor = rgba(css.color);
    const visibleForeground = composite(foregroundColor, backdrop);
    const foreground = luminance(visibleForeground);
    const background = luminance(backdrop);
    return {
      ratio: (Math.max(foreground, background) + 0.05) / (Math.min(foreground, background) + 0.05),
      color: css.color,
      background: css.backgroundColor,
      effectiveColor: `rgb(${visibleForeground.slice(0, 3).map(Math.round).join(", ")})`,
      effectiveBackground: `rgb(${backdrop.slice(0, 3).map(Math.round).join(", ")})`,
    };
  });
}

/* Measure normal, hover and held-down states without letting the release fire a
 * submit, navigation or the modal delete request. */
async function stateContrast(page, locator) {
  await page.mouse.move(1, 1);
  await page.waitForTimeout(200);
  const normal = await contrast(locator);
  await locator.hover();
  await page.waitForTimeout(200);
  const hover = await contrast(locator);
  const box = await locator.boundingBox();
  assert.ok(box, "measured control must have a box");
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.waitForTimeout(200);
  const active = await contrast(locator);
  await page.mouse.move(1, 1);
  await page.mouse.up();
  return { normal, hover, active };
}

async function assertReadable(page, locator, label) {
  const states = await stateContrast(page, locator);
  for (const [state, value] of Object.entries(states)) {
    assert.ok(value.ratio >= 4.5, `${label} ${state} contrast ${JSON.stringify(value)}`);
  }
}

async function assertNoOverflow(page, label) {
  assert.ok(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
    `${label} page overflow`,
  );
  const outliers = await page.locator("button:visible, a.ui-control:visible, summary:visible").evaluateAll((els) =>
    els
      .filter((el) => {
        const box = el.getBoundingClientRect();
        return box.width && (box.right > innerWidth + 1 || box.left < -1);
      })
      .map((el) => el.outerHTML.slice(0, 120)),
  );
  assert.deepEqual(outliers, [], `${label} controls must stay inside the viewport`);
}

async function settingsCase(page, width, theme, state) {
  await page.goto("file://" + path.join(fixtures, `settings-${state}.html`));
  await page.waitForFunction(
    () => typeof window.Alpine !== "undefined" && typeof window.tailwind !== "undefined",
  );
  await page.waitForTimeout(80);
  assert.equal(
    await page.evaluate(() => document.documentElement.classList.contains("dark")),
    theme === "dark",
    "theme class must follow the stored preference",
  );
  assert.equal((await page.locator(".settings-heading h2").innerText()).trim(), "Платформы");
  assert.equal(await page.locator('a[href="/admin/platform/list"]').count(), 0);
  assert.equal(await page.locator("#page-content select").count(), 0);
  await assertNoOverflow(page, `${width}/${theme}/${state}`);

  const longHeading = page.locator(".ui-card h3").filter({ hasText: "ОченьДлинноеНазвание" });
  if (await longHeading.count()) {
    const box = await longHeading.first().boundingBox();
    assert.ok(box.x >= 0 && box.x + box.width <= width + 1, "long platform name must stay inside the viewport");
  }

  await page.locator("details.ui-card").first().locator("summary").click();
  await page.locator("#platform-new-type-trigger").click();
  const panel = await page.locator("#platform-new-type-popover").boundingBox();
  assert.ok(panel.x >= 0 && panel.x + panel.width <= width + 1, "type picker must fit horizontally");
  await page.locator('[data-popover-option="telegram"]').click();
  assert.equal(await page.locator('[name="platform_type"]').inputValue(), "telegram");

  await page.evaluate(() => {
    window.syntheticSubmissions = [];
    document.addEventListener("submit", (event) => {
      event.preventDefault();
      window.syntheticSubmissions.push(
        Object.fromEntries(new FormData(event.target, event.submitter)),
      );
    });
  });
  const createForm = page.locator("details.ui-card form");
  await createForm.locator('[name="name"]').fill("Новая платформа");
  await createForm.locator('[name="base_url"]').fill("https://api.example.invalid");
  await createForm.locator('button[type="submit"]').click();
  const submitted = await page.evaluate(() => window.syntheticSubmissions.at(-1));
  assert.equal(submitted.platform_type, "telegram", "selected type must be submitted");
  assert.equal(submitted._csrf, "fixture-csrf", "form must carry its CSRF token");

  for (const form of await page.locator('form[action="/app/settings/platforms"]').all()) {
    const hasId = (await form.locator('[name="platform_id"]').count()) === 1;
    if (hasId) {
      assert.equal(
        await form.locator('[name="platform_type"]').count(),
        0,
        "an existing platform form must not offer its immutable type",
      );
      const formId = await form.locator('[name="platform_id"]').inputValue();
      const nameInput = form.locator('[name="name"]');
      if ((await nameInput.inputValue()).length > 50) {
        // The fixture keeps one over-long name for layout coverage; a real edit
        // would shorten it before the browser allows submission.
        await nameInput.fill("Обновлённая платформа");
      }
      await form.locator('button[type="submit"]').click();
      const fields = await page.evaluate(() => window.syntheticSubmissions.at(-1));
      assert.equal(fields.platform_id, formId, "each update form must submit its own platform id");
      assert.equal(fields._csrf, "fixture-csrf");
    }
  }

  const controls = page.locator("#page-content button:visible, #page-content a.ui-control:visible");
  assert.ok((await controls.count()) >= 1, "catalog actions must be visible");
  for (const control of await controls.all()) {
    const kind = (await control.getAttribute("type")) || (await control.innerText()).trim() || "control";
    await assertReadable(page, control, `${width}/${theme}/${state}/${kind}`);
  }
  await assertNoOverflow(page, `${width}/${theme}/${state}/expanded`);
}

async function adminCase(page, width, theme) {
  await page.goto("file://" + path.join(fixtures, "admin.html"));
  await page.waitForTimeout(200);
  assert.equal(
    await page.evaluate(() => document.querySelector("main[data-ui-theme-surface]")?.dataset.theme),
    theme,
    "admin theme surface must follow the stored preference",
  );
  await assertNoOverflow(page, `${width}/${theme}/admin`);

  for (const button of await page.locator("[data-test-kind]").all()) {
    const kind = await button.getAttribute("data-test-kind");
    await assertReadable(page, button, `${width}/${theme}/admin/${kind}`);
  }
  await page.keyboard.press("Escape");

  const toggle = page.locator("#dropdownMenuButton");
  await assertReadable(page, toggle, `${width}/${theme}/admin/actions`);
  await toggle.click();
  const deleteAction = page.locator("#action-delete");
  await deleteAction.waitFor({ state: "visible" });
  await assertReadable(page, deleteAction, `${width}/${theme}/admin/delete-action`);
  const deleteUrl = await deleteAction.getAttribute("data-url");
  await deleteAction.click();
  await page.locator("#modal-delete.show").waitFor();
  /* The dialog is filled by SQLAdmin's own shown.bs.modal handler, which fires
   * only after the packaged slide/fade transition settles. Wait for that
   * completion instead of guessing a delay. */
  await page.waitForFunction(() => {
    const button = document.querySelector("#modal-delete-button");
    const content = document.querySelector("#modal-delete-text")?.textContent || "";
    return Boolean(button?.dataset.url) && (content.match(/\d+/g) || []).length === 200;
  });

  const text = page.locator("#modal-delete-text");
  const body = await text.evaluate((el) => {
    const style = getComputedStyle(el);
    return {
      content: el.textContent,
      scrollWidth: el.scrollWidth,
      clientWidth: el.clientWidth,
      scrollHeight: el.scrollHeight,
      clientHeight: el.clientHeight,
      overflowY: style.overflowY,
      dialog: el.closest(".modal-content").getBoundingClientRect().toJSON(),
    };
  });
  const selected = body.content.match(/\d+/g) || [];
  assert.equal(selected.length, 200, "the full selection must stay in the dialog");
  assert.equal(selected.at(0), "1276");
  assert.equal(selected.at(-1), "1077");
  assert.ok(body.scrollWidth <= body.clientWidth + 1, "selected ids must wrap inside the dialog");
  assert.equal(body.overflowY, "auto", "a tall selection must scroll inside the dialog");
  assert.ok(body.clientHeight <= 289, "selection body must stay bounded by its maximum height");
  assert.ok(body.scrollHeight > body.clientHeight, "the full selection must scroll instead of being clipped");
  assert.ok(body.dialog.x >= 0 && body.dialog.right <= width + 1, "dialog must stay inside the viewport");
  await page.waitForFunction(() => document.querySelector("#modal-delete-button")?.dataset.url);
  assert.equal(
    await page.locator("#modal-delete-button").getAttribute("data-url"),
    `${deleteUrl}?pks=${selected.join(",")}`,
    "the delete target must keep the full original URL and the whole selection",
  );

  for (const button of await page.locator("#modal-delete .modal-footer .btn").all()) {
    const box = await button.boundingBox();
    assert.ok(box && box.y + box.height <= 1000, "modal actions must stay visible");
    await assertReadable(page, button, `${width}/${theme}/admin/modal-${await button.innerText()}`);
  }
}

(async () => {
  for (const name of ["tailwind.js", "alpine.js", "htmx.js"]) {
    assert.ok(fs.existsSync(path.join(vendor, name)), `offline vendor asset missing: ${name}`);
  }
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROMIUM_BIN || undefined,
    args: ["--no-sandbox"],
  });
  const report = { cases: [], failures: [] };
  try {
    for (const width of widths) {
      for (const theme of ["light", "dark"]) {
        for (const state of states) {
          const context = await browser.newContext({ viewport: { width, height: 1000 }, colorScheme: theme });
          await context.addInitScript((value) => localStorage.setItem("theme", value), theme);
          const page = await context.newPage();
          const errors = [];
          const external = [];
          page.on("pageerror", (error) => errors.push(error.message));
          await page.route("**/*", (route) => {
            if (route.request().url().startsWith("file:")) return route.continue();
            external.push(route.request().url());
            return route.abort();
          });
          try {
            if (state === "admin") await adminCase(page, width, theme);
            else await settingsCase(page, width, theme, state);
            assert.deepEqual(errors, [], "no script errors are allowed");
            assert.deepEqual(external, [], "all requests must stay local");
            if ([390, 1440].includes(width)) {
              await page.screenshot({
                path: path.join(fixtures, `${state}-${width}-${theme}.png`),
                fullPage: true,
              });
            }
            report.cases.push({ state, width, theme });
          } catch (error) {
            report.failures.push({ state, width, theme, error: error.message, errors });
          } finally {
            await context.close();
          }
        }
      }
    }
  } finally {
    await browser.close();
    fs.writeFileSync(path.join(fixtures, "settings-admin-report.json"), JSON.stringify(report, null, 2));
  }
  console.log(JSON.stringify({ passed: report.cases.length, failures: report.failures }, null, 2));
  if (report.failures.length) process.exitCode = 1;
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
