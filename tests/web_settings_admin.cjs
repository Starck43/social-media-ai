/* Real templates/styles/SQLAdmin JS. All non-file requests aborted. No app/DB. */
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const folder = path.resolve(process.argv[2]);
async function contrast(locator) {
  return locator.evaluate((el) => {
    const css = getComputedStyle(el);
    let bg = css.backgroundColor,
      node = el;
    while (bg === "rgba(0, 0, 0, 0)" && node.parentElement) {
      node = node.parentElement;
      bg = getComputedStyle(node).backgroundColor;
    }
    const lum = (c) =>
      c
        .match(/[\d.]+/g)
        .slice(0, 3)
        .map(Number)
        .map((v) => {
          v /= 255;
          return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
        })
        .reduce((a, v, i) => a + v * [0.2126, 0.7152, 0.0722][i], 0);
    const a = lum(css.color),
      b = lum(bg);
    return {
      ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05),
      fg: css.color,
      bg,
    };
  });
}
(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROMIUM_BIN || undefined,
    args: ["--no-sandbox"],
  });
  let cases = 0;
  try {
    for (const width of [320, 390, 768, 1440])
      for (const theme of ["light", "dark"])
        for (const state of ["platforms", "empty", "no-workspace", "admin"]) {
          const context = await browser.newContext({
            viewport: { width, height: 1000 },
            colorScheme: theme,
          });
          await context.addInitScript(
            (t) => localStorage.setItem("theme", t),
            theme,
          );
          const page = await context.newPage(),
            errors = [];
          page.on("pageerror", (e) => errors.push(e.message));
          await page.route("**/*", (r) =>
            r.request().url().startsWith("file:") ? r.continue() : r.abort(),
          );
          await page.goto(
            "file://" +
              path.join(
                folder,
                state === "admin" ? "admin.html" : `settings-${state}.html`,
              ),
          );
          await page.waitForTimeout(150);
          assert(
            await page.evaluate(
              () => document.documentElement.scrollWidth <= innerWidth + 1,
            ),
            `${width}/${theme}/${state} page overflow`,
          );
          if ([390, 1440].includes(width))
            await page.screenshot({
              path: path.join(folder, `${state}-${width}-${theme}-initial.png`),
              fullPage: true,
            });
          if (state === "admin") {
            for (const button of await page
              .locator("[data-test-kind], #dropdownMenuButton")
              .all()) {
              const kind =
                (await button.getAttribute("data-test-kind")) || "actions";
              const normal = await contrast(button);
              assert(
                normal.ratio >= 4.5,
                `${width}/${theme}/${kind} contrast ${JSON.stringify(normal)}`,
              );
              await button.hover();
              const hover = await contrast(button);
              assert(
                hover.ratio >= 4.5,
                `${width}/${theme}/${kind} hover ${JSON.stringify(hover)}`,
              );
            }
            await page.locator("#dropdownMenuButton").click();
            const action = page.locator("#action-delete");
            await action.waitFor({ state: "visible" });
            const dropdownContrast = await contrast(action);
            assert(
              dropdownContrast.ratio >= 4.5,
              `dropdown ${JSON.stringify(dropdownContrast)}`,
            );
            await action.click();
            await page.locator("#modal-delete.show").waitFor();
            await page.waitForTimeout(300);
            const detailContrast = await contrast(
              page.locator("#modal-delete-text"),
            );
            assert(
              detailContrast.ratio >= 4.5,
              `modal details ${JSON.stringify(detailContrast)}`,
            );
            const text = await page.locator("#modal-delete-text").innerText();
            assert(
              text.includes("1276") && text.includes("1077"),
              `bulk selection preserved: ${text}`,
            );
            const bounds = await page
              .locator("#modal-delete-text")
              .evaluate((el) => ({
                width: el.clientWidth,
                scroll: el.scrollWidth,
                top: el.getBoundingClientRect().top,
                bottom: el.getBoundingClientRect().bottom,
                dialog: el
                  .closest(".modal-content")
                  .getBoundingClientRect()
                  .toJSON(),
                count: el.textContent.split(",").length,
              }));
            assert.equal(bounds.count, 200);
            assert(
              bounds.scroll <= bounds.width + 1,
              "IDs must wrap within parent",
            );
            assert(
              bounds.dialog.x >= 0 && bounds.dialog.right <= width + 1,
              "dialog inside viewport",
            );
            for (const button of await page
              .locator(".modal-footer .btn")
              .all()) {
              const pair = await contrast(button);
              assert(pair.ratio >= 4.5, `modal button ${JSON.stringify(pair)}`);
              const box = await button.boundingBox();
              assert(box.y + box.height <= 1000, "actions visible");
            }
          } else {
            assert.equal(
              await page.locator(".settings-heading h2").innerText(),
              "Платформы",
            );
            assert.equal(
              await page.locator('a[href="/admin/platform/list"]').count(),
              0,
            );
            await page.locator("details.ui-card > summary").click();
            const picker = page.locator("#platform-new-type-trigger");
            await picker.click();
            const bounds = await page
              .locator("#platform-new-type-popover")
              .boundingBox();
            assert(
              bounds.x >= 0 && bounds.x + bounds.width <= width + 1,
              "picker within viewport",
            );
            if ([390, 1440].includes(width))
              await page.screenshot({
                path: path.join(
                  folder,
                  `${state}-${width}-${theme}-picker.png`,
                ),
                fullPage: true,
              });
            await page.locator('[data-popover-option="telegram"]').click();
            assert.equal(
              await page.locator('[name="platform_type"]').inputValue(),
              "telegram",
            );
            await page.evaluate(() =>
              document
                .querySelectorAll('form[action="/app/settings/platforms"]')
                .forEach((form) =>
                  form.addEventListener("submit", (e) => {
                    e.preventDefault();
                    window.testForm = [...new FormData(form)];
                  }),
                ),
            );
            const form = page.locator("details.ui-card form");
            await form.locator('[name="name"]').fill("Новая платформа");
            await form
              .locator('[name="base_url"]')
              .fill("https://api.example.invalid");
            await form.locator('button[type="submit"]').click();
            const submitted = await page.evaluate(() => window.testForm);
            assert(
              submitted.some(
                ([k, v]) => k === "platform_type" && v === "telegram",
              ),
            );
            assert(
              submitted.some(([k, v]) => k === "_csrf" && v === "fixture-csrf"),
            );
            assert(
              await page.evaluate(
                () => document.documentElement.scrollWidth <= innerWidth + 1,
              ),
              "expanded forms overflow",
            );
          }
          assert.deepEqual(errors, [], `${state} script errors`);
          if ([390, 1440].includes(width))
            await page.screenshot({
              path: path.join(folder, `${state}-${width}-${theme}.png`),
              fullPage: true,
            });
          cases++;
          await context.close();
        }
    console.log(JSON.stringify({ cases, failures: 0 }));
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
