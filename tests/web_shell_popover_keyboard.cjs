/* Offline regression: a keyboard-opened model picker must accept a mouse click.

The trigger is focused and Enter opens the panel; the option is then clicked with
the pointer. WebKit does not focus buttons on click, so `focusout` fires with
`relatedTarget=null` while the pointer is over the panel; closing on the next
tick hid the panel before the click landed. Runs in Chromium or WebKit:
  SHELL_ENGINE=webkit node tests/web_shell_popover_keyboard.cjs <fixtures> <vendor>
*/
const { chromium, webkit } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");

const root = path.resolve(__dirname, "..");
const fixtures = path.resolve(process.argv[2]);
const vendor = path.resolve(process.argv[3]);
const engine = process.env.SHELL_ENGINE === "webkit" ? "webkit" : "chromium";

(async () => {
  const browser =
    engine === "webkit"
      ? await webkit.launch({ headless: true })
      : await chromium.launch({
          headless: true,
          executablePath: process.env.CHROMIUM_BIN || "/usr/bin/chromium",
          args: ["--no-sandbox"],
        });
  let passed = 0;
  try {
    for (const theme of ["light", "dark"]) {
      const context = await browser.newContext({
        viewport: { width: 390, height: 900 },
        colorScheme: theme,
      });
      const page = await context.newPage();
      const errors = [];
      const external = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.route("**/*", async (route) => {
        const url = new URL(route.request().url());
        if (url.hostname !== "fixture.test") {
          external.push(url.href);
          return route.abort();
        }
        if (url.pathname.startsWith("/static/")) {
          return route.fulfill({
            path: url.pathname.startsWith("/static/vendor/")
              ? path.join(vendor, path.basename(url.pathname))
              : path.join(root, "app", url.pathname),
          });
        }
        if (url.pathname === "/app/notifications") {
          return route.fulfill({
            json: { items: [], unread_count: 0, attention: {} },
          });
        }
        if (url.pathname === "/app/settings") {
          return route.fulfill({
            path: path.join(fixtures, "settings-agent.html"),
            contentType: "text/html",
          });
        }
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: "{}",
        });
      });

      await page.goto("http://fixture.test/app/settings?tab=agent");
      const group = page.locator("[data-model-controls]");
      const trigger = group.locator("[data-popover-trigger]");
      const hidden = group.locator("[data-popover-value]");
      const before = await hidden.inputValue();

      await trigger.focus();
      await page.keyboard.press("Enter");
      await group.locator('[data-popover-option="model-b"]').click();

      const after = await hidden.inputValue();
      assert.notEqual(before, "model-b", "fixture must start on another model");
      assert.equal(
        after,
        "model-b",
        `${engine}/${theme}: keyboard-opened picker lost the clicked option`,
      );
      assert.deepEqual(errors, [], `${engine}/${theme}: page errors`);
      assert.deepEqual(external, [], `${engine}/${theme}: external requests`);
      passed += 1;
      await context.close();
    }
    console.log(JSON.stringify({ engine, passed, failed: 0 }));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
