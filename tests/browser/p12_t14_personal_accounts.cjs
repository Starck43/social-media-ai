/* Offline native-form DOM contracts. Synthetic HTML only; all requests intercepted.
 * Optional CDN libraries are no-ops: this deliberately tests local-CSS/no-CDN
 * rendering, not CDN/Tailwind integration. No app, DB or real provider runs.
 */
const { chromium } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const root = path.resolve(__dirname, "../..");
const fixtures = path.resolve(process.argv[2]);
const modes = ["active", "reauth", "revoked", "empty", "error", "unsafe"];
const accountPath = "/app/settings/telegram-accounts";
const cdn = new Set(["cdn.tailwindcss.com", "unpkg.com", "cdn.jsdelivr.net"]);

async function main() {
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_BIN || "/usr/local/bin/chromium",
    headless: true,
    args: ["--no-sandbox", "--disable-background-networking"],
  });
  const report = { cases: [], failures: [] };
  try {
    for (const mode of modes)
      for (const width of [390, 1440])
        for (const theme of ["light", "dark"]) {
          const context = await browser.newContext({
            viewport: { width, height: 1100 },
            colorScheme: theme,
            serviceWorkers: "block",
            reducedMotion: "reduce",
          });
          const page = await context.newPage();
          const violations = [],
            errors = [],
            submissions = [];
          page.on("pageerror", (e) => errors.push(e.message));
          await context.addInitScript(
            (t) => localStorage.setItem("theme", t),
            theme,
          );
          await context.route("**/*", async (route) => {
            const req = route.request(),
              url = new URL(req.url());
            if (cdn.has(url.hostname))
              return route.fulfill({
                contentType: "application/javascript",
                body:
                  url.hostname === "cdn.tailwindcss.com"
                    ? "window.tailwind = {};"
                    : "",
              });
            if (url.hostname !== "fixture.test") {
              violations.push(req.url());
              return route.abort();
            }
            if (url.pathname.startsWith("/static/")) {
              const target = path.resolve(root, "app", "." + url.pathname);
              assert.ok(target.startsWith(path.join(root, "app/static/")));
              return route.fulfill({ path: target });
            }
            if (url.pathname === accountPath && req.method() === "GET")
              return route.fulfill({
                path: path.join(fixtures, `accounts-${mode}.html`),
                contentType: "text/html",
              });
            if (
              url.pathname === accountPath + "/12/revoke" &&
              req.method() === "POST"
            ) {
              submissions.push(
                Object.fromEntries(new URLSearchParams(req.postData())),
              );
              return route.fulfill({
                status: 200,
                contentType: "text/html",
                body: "<!doctype html><title>Fake local result</title><p>Fake local revoke received</p>",
              });
            }
            violations.push(req.url());
            return route.abort();
          });
          try {
            await page.goto("http://fixture.test" + accountPath);
            await page.waitForFunction(
              (t) =>
                document.documentElement.classList.contains("dark") ===
                (t === "dark"),
              theme,
            );
            assert.equal(await page.locator("h1").count(), 1);
            assert.equal(await page.locator("#page-content img").count(), 0);
            assert.ok(
              await page
                .locator("#page-content")
                .innerText()
                .then((t) =>
                  t.includes("не завершает сессию на стороне Telegram"),
                ),
            );
            assert.equal(
              await page.locator("[data-telegram-empty]").count(),
              Number(mode === "empty"),
            );
            assert.equal(
              await page.locator('#page-content [role="alert"]').count(),
              Number(mode === "error"),
            );
            assert.ok(
              await page.evaluate(
                () => document.documentElement.scrollWidth <= innerWidth,
              ),
              "no horizontal page overflow",
            );
            for (const element of await page
              .locator(
                "#page-content form, #page-content article, #page-content section",
              )
              .all()) {
              const b = await element.boundingBox();
              assert.ok(
                b.x >= 0 && b.x + b.width <= width + 1,
                "card/form must fit viewport",
              );
            }
            const contrast = await page
              .locator(".telegram-accounts")
              .evaluate((root) => {
                const rgb = (value) =>
                  (value.match(/[\d.]+/g) || []).map(Number);
                const luminance = (values) =>
                  values
                    .slice(0, 3)
                    .map((v) => {
                      v /= 255;
                      return v <= 0.04045
                        ? v / 12.92
                        : ((v + 0.055) / 1.055) ** 2.4;
                    })
                    .reduce(
                      (sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i],
                      0,
                    );
                return [
                  ...root.querySelectorAll(
                    "h2, p, a, button, label, .ui-badge",
                  ),
                ].map((element) => {
                  let ancestor = element,
                    background;
                  while (ancestor) {
                    background = rgb(
                      getComputedStyle(ancestor).backgroundColor,
                    );
                    if (background.length === 3 || background[3] === 1) break;
                    ancestor = ancestor.parentElement;
                  }
                  const fg = luminance(rgb(getComputedStyle(element).color));
                  const bg = luminance(background || [255, 255, 255]);
                  return (Math.max(fg, bg) + 0.05) / (Math.min(fg, bg) + 0.05);
                });
              });
            assert.ok(
              contrast.every((value) => value >= 4.5),
              "declared text colors need AA contrast",
            );
            if (mode === "empty") {
              if (width === 390)
                await page
                  .getByRole("button", {
                    name: "Открыть навигацию",
                    exact: true,
                  })
                  .click();
              const scope =
                width === 390
                  ? page.getByRole("dialog", { name: "Навигация", exact: true })
                  : page.locator(".shell-sidebar");
              await scope
                .getByRole("button", {
                  name: "Меню пользователя: synthetic-user",
                  exact: true,
                })
                .click();
              assert.equal(
                await scope
                  .getByRole("link", {
                    name: "Telegram-авторизации",
                    exact: true,
                  })
                  .isVisible(),
                true,
              );
              await page.screenshot({
                path: path.join(fixtures, `menu-${width}-${theme}.png`),
                fullPage: true,
              });
              await page.keyboard.press("Escape");
              if (width === 390)
                await scope
                  .getByRole("button", {
                    name: "Закрыть навигацию",
                    exact: true,
                  })
                  .click();
            }
            if (mode === "active")
              await page.screenshot({
                path: path.join(fixtures, `unchecked-${width}-${theme}.png`),
                fullPage: true,
              });
            const form = page.locator("form.telegram-account-revoke");
            const hasForm = ["active", "reauth", "unsafe"].includes(mode);
            assert.equal(await form.count(), Number(hasForm));
            if (hasForm) {
              const button = form.getByRole("button", {
                name: "Отозвать локально во всех пространствах",
              });
              const b = await button.boundingBox();
              assert.ok(b.height >= 44);
              await button.click();
              assert.equal(
                submissions.length,
                0,
                "native confirmation is required",
              );
              await form.getByRole("checkbox").focus();
              await page.keyboard.press("Space");
              assert.equal(await form.getByRole("checkbox").isChecked(), true);
            }
            await page.screenshot({
              path: path.join(fixtures, `${mode}-${width}-${theme}.png`),
              fullPage: true,
            });
            if (hasForm) {
              await form.getByRole("button").click();
              await page.waitForURL("**/12/revoke");
              assert.deepEqual(submissions, [
                {
                  _csrf: "synthetic-account-token",
                  expected_revision: "2",
                  confirm: "revoke-local",
                },
              ]);
            }
            assert.deepEqual(errors, []);
            assert.deepEqual(violations, []);
            report.cases.push({
              mode,
              width,
              theme,
              localCssNoCdn: true,
              pass: true,
            });
          } catch (error) {
            report.failures.push({
              mode,
              width,
              theme,
              message: error.message,
            });
          } finally {
            await context.close();
          }
        }
  } finally {
    await browser.close();
  }
  fs.writeFileSync(
    path.join(fixtures, "dom-report.json"),
    JSON.stringify(report, null, 2),
  );
  console.log(
    `${report.cases.length} DOM cases passed; ${report.failures.length} failed; local CSS/no CDN; no live app/DB/provider`,
  );
  if (report.failures.length) {
    console.error(JSON.stringify(report.failures));
    process.exit(1);
  }
}
main().catch((error) => {
  console.error(error);
  process.exit(1);
});
