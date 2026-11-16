/* Synthetic native-form DOM checks for the actual Telegram picker/login templates. */
const { chromium } = require("playwright");
const path = require("node:path");
const assert = require("node:assert/strict");
const root = path.resolve(__dirname, "../..");
const fixtures = path.resolve(process.argv[2]);
const cases = [
  ["source-unselected", "#telegram-source-grant"],
  ["source-selected", "#telegram-source-grant"],
  ["source-empty", null],
  ["login-start", "#telegram-phone"],
  ["login-code", "#telegram-login-secret"],
  ["login-password", "#telegram-login-secret"],
  ["login-error", null],
];

async function main() {
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_BIN || "/usr/local/bin/chromium",
    headless: true,
    args: ["--no-sandbox", "--disable-background-networking"],
  });
  const failures = [];
  try {
    for (const [name, controlSelector] of cases)
      for (const width of [390, 1440])
        for (const theme of ["light", "dark"]) {
          const context = await browser.newContext({
            viewport: { width, height: 1100 },
            colorScheme: theme,
            reducedMotion: "reduce",
            serviceWorkers: "block",
          });
          const page = await context.newPage();
          const unexpected = [];
          page.on("pageerror", (error) => unexpected.push(error.message));
          await page.addInitScript((value) => localStorage.setItem("theme", value), theme);
          await page.route("**/*", async (route) => {
            const request = route.request();
            const url = new URL(request.url());
            if (["cdn.tailwindcss.com", "unpkg.com", "cdn.jsdelivr.net"].includes(url.hostname))
              return route.fulfill({
                contentType: "application/javascript",
                body: url.hostname === "cdn.tailwindcss.com" ? "window.tailwind = {};" : "",
              });
            if (url.hostname !== "fixture.test") {
              unexpected.push("network:" + request.url());
              return route.abort();
            }
            if (url.pathname.startsWith("/static/")) {
              const target = path.resolve(root, "app", "." + url.pathname);
              assert.ok(target.startsWith(path.join(root, "app/static/")));
              return route.fulfill({ path: target });
            }
            return route.fulfill({
              path: path.join(fixtures, name + ".html"),
              contentType: "text/html",
            });
          });
          try {
            await page.goto("http://fixture.test/synthetic/" + name);
            await page.waitForFunction(
              (dark) => document.documentElement.classList.contains("dark") === dark,
              theme === "dark",
            );
            const control = controlSelector ? page.locator(controlSelector) : null;
            if (control) {
              assert.equal(await control.count(), 1, name + " must render its actual form control");
              assert.equal(await control.isVisible(), true);
              await control.focus();
              await page.keyboard.press("Tab");
              await page.keyboard.press("Shift+Tab");
              const metrics = await control.evaluate((element) => {
                const luminance = (color) => {
                  const values = (color.match(/[\d.]+/g) || []).map(Number).slice(0, 3);
                  const channels = values.map((value) => {
                    value /= 255;
                    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
                  });
                  return channels.reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
                };
                const contrast = (first, second) => (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
                const style = getComputedStyle(element);
                return {
                  border: contrast(luminance(style.borderTopColor), luminance(style.backgroundColor)),
                  focus: contrast(luminance(style.outlineColor), luminance(style.backgroundColor)),
                  width: parseFloat(style.outlineWidth),
                  focusVisible: element.matches(":focus-visible"),
                };
              });
              assert.ok(metrics.border >= 3, name + "/" + theme + ": border contrast " + metrics.border);
              assert.ok(metrics.focus >= 3, name + "/" + theme + ": focus contrast " + metrics.focus);
              assert.ok(metrics.width >= 3, name + ": focus outline is at least 3px");
              assert.equal(metrics.focusVisible, true, name + ": keyboard focus is visible");
            } else {
              assert.equal(await page.locator(".telegram-account-select").count(), 0);
              if (name === "login-error")
                assert.ok((await page.locator("[role=alert]").innerText()).includes("Synthetic validation error"));
              else
                assert.ok((await page.locator(".telegram-accounts").innerText()).includes("Нет подходящих разрешений"));
            }
            const rendered = await page.locator(".telegram-accounts").innerText();
            assert.ok(!rendered.includes("synthetic-login-token"));
            assert.ok(!rendered.includes("synthetic-code-token"));
            const secretValues = await page.locator('input[type="tel"], input[type="password"]').evaluateAll((fields) => fields.map((field) => field.value));
            assert.ok(secretValues.every((value) => value === ""), "phone/code/password inputs start empty");
            assert.deepEqual(unexpected, []);
            if (["source-unselected", "login-code"].includes(name))
              await page.screenshot({
                path: path.join(fixtures, name + "-" + width + "-" + theme + ".png"),
                fullPage: true,
              });
          } catch (error) {
            failures.push({ name, width, theme, message: error.message });
          } finally {
            await context.close();
          }
        }
  } finally {
    await browser.close();
  }
  console.log((cases.length * 2 * 2 - failures.length) + " actual-template cases passed; " + failures.length + " failed; network intercepted");
  if (failures.length) {
    console.error(JSON.stringify(failures));
    process.exit(1);
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
