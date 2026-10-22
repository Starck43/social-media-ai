/* Offline UI only: no server, database, secrets, live provider or external requests. */
const { chromium } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const root = path.resolve(__dirname, "..");
const fixtures = path.resolve(process.argv[2]);
const vendor = path.resolve(process.argv[3]);
(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROMIUM_BIN || "/usr/bin/chromium",
    args: ["--no-sandbox"],
  });
  let passed = 0;
  try {
    for (const mode of ["catalog", "empty", "no-workspace"])
      for (const width of [320, 390, 768, 1440])
        for (const theme of ["light", "dark"]) {
          const context = await browser.newContext({
            viewport: { width, height: 1000 },
            colorScheme: theme,
          });
          const page = await context.newPage();
          const errors = [],
            external = [];
          page.on("pageerror", (error) => errors.push(error.message));
          await context.addInitScript(
            (t) => localStorage.setItem("theme", t),
            theme,
          );
          await page.route("**/*", async (route) => {
            const url = new URL(route.request().url());
            if (url.hostname !== "fixture.test") {
              external.push(url.href);
              return route.abort();
            }
            if (url.pathname.startsWith("/static/"))
              return route.fulfill({
                path: url.pathname.startsWith("/static/vendor/")
                  ? path.join(vendor, path.basename(url.pathname))
                  : path.join(root, "app", url.pathname),
              });
            if (url.pathname === "/app/notifications")
              return route.fulfill({
                json: { items: [], unread_count: 0, attention: {} },
              });
            if (url.pathname === "/app/settings")
              return route.fulfill({
                path: path.join(fixtures, mode + ".html"),
                contentType: "text/html",
              });
            return route.fulfill({
              status: 200,
              contentType: "application/json",
              body: "{}",
            });
          });
          await page.goto("http://fixture.test/app/settings?tab=models");
          await page.waitForTimeout(300);
          const overflow = () =>
            page.evaluate(
              () => document.documentElement.scrollWidth > innerWidth + 1,
            );
          assert.equal(
            await overflow(),
            false,
            `${mode}/${width}/${theme} initial overflow`,
          );
          if (mode === "catalog") {
            assert.equal(
              await page.locator(".llm-default").textContent(),
              "По умолчанию",
            );
            assert.equal(
              await page.getByText("Выбрана для чата", { exact: true }).count(),
              1,
            );
            const editor = page
              .locator(".llm-provider")
              .first()
              .locator("details.llm-editor");
            await editor.locator(":scope > summary").click();
            await editor.locator("[name=provider_uid]").fill("qwen-renamed");
            await editor.locator("[name=provider_name]").fill("Qwen New.AI");
            assert.equal(
              await editor.locator("[name=provider_uid]").inputValue(),
              "qwen-renamed",
            );
            assert.equal(
              await editor.locator("[name=provider_id]").inputValue(),
              "21",
            );
            await editor.locator(":scope > summary").click();
          }
          await page.locator("[data-llm-open]").click();
          const form = page.locator("[data-llm-new]");
          assert.equal(
            (await page.locator("#llm-add").getAttribute("open")) !== null,
            true,
          );
          await form.locator("[name=provider_name]").fill("Qwen.AI");
          assert.equal(
            await form.locator("[name=provider_uid]").inputValue(),
            "qwen",
          );
          await form.locator("[name=provider_uid]").fill("manual");
          await form.locator("[name=provider_name]").fill("Changed.AI");
          assert.equal(
            await form.locator("[name=provider_uid]").inputValue(),
            "manual",
          );
          await form
            .locator("[name=base_url]")
            .fill("http://fixture.invalid/v1");
          assert.equal(
            await form
              .locator("[name=base_url]")
              .evaluate((el) => el.checkValidity()),
            false,
          );
          await form
            .locator("[name=base_url]")
            .fill("https://provider.fixture.invalid/v1");
          const firstRow = form.locator("[data-llm-row]").first();
          await firstRow.locator("[data-model-api]").fill("partial");
          assert.equal(
            await firstRow
              .locator("[data-model-name]")
              .evaluate((el) => el.required),
            true,
          );
          assert.equal(await form.evaluate((el) => el.checkValidity()), false);
          await firstRow.locator("[data-model-api]").fill("");
          assert.equal(
            await firstRow
              .locator("[data-model-name]")
              .evaluate((el) => el.required),
            false,
          );
          assert.equal(await form.evaluate((el) => el.checkValidity()), true);
          await form.locator("[data-llm-add]").click();
          assert.equal(await form.locator("[data-llm-row]").count(), 2);
          await form
            .locator("[data-llm-row]")
            .last()
            .locator("[data-llm-remove]")
            .click();
          assert.equal(await form.locator("[data-llm-row]").count(), 1);
          await form.locator("[data-llm-add]").click();
          await form
            .locator("[data-llm-row]")
            .nth(0)
            .locator("[data-model-api]")
            .fill("qwen-plus");
          await form
            .locator("[data-llm-row]")
            .nth(0)
            .locator("[data-model-name]")
            .fill("Qwen Plus");
          await form
            .locator("[data-llm-row]")
            .nth(1)
            .locator("[data-model-api]")
            .fill("qwen-max");
          await form
            .locator("[data-llm-row]")
            .nth(1)
            .locator("[data-model-name]")
            .fill("Qwen Max");
          if (mode !== "empty") {
            await form.locator("[data-popover-trigger]").press("ArrowDown");
            const panel = form.locator(".shell-popover-panel");
            const box = await panel.boundingBox();
            assert.ok(
              box.x >= 0 &&
                box.x + box.width <= width + 1 &&
                box.y >= 0 &&
                box.y + box.height <= 1001,
            );
            await form.locator('[data-popover-option="21"]').press("Enter");
            assert.equal(
              await form.locator("[name=provider_id]").inputValue(),
              "21",
            );
            assert.equal(
              await form.locator("[name=provider_name]").inputValue(),
              "Qwen.AI",
            );
            assert.equal(
              await form.locator("[name=provider_uid]").inputValue(),
              "qwen",
            );
            assert.equal(
              await form.locator("[name=base_url]").inputValue(),
              "https://provider.fixture.invalid/v1",
            );
          }
          await form.evaluate((el) => {
            window.syntheticCatalogPosts = [];
            el.addEventListener("submit", (event) => {
              if (!event.defaultPrevented)
                window.syntheticCatalogPosts.push([
                  ...new FormData(el).entries(),
                ]);
              event.preventDefault();
            });
          });
          await form
            .locator('button[type="submit"], .llm-actions button')
            .click();
          const posts = await page.evaluate(() => window.syntheticCatalogPosts);
          assert.equal(
            posts.length,
            1,
            "valid bundle must submit exactly once",
          );
          const payload = posts[0];
          assert.equal(
            payload.filter(([key]) => key === "model_name").length,
            2,
          );
          assert.equal(
            payload.some(([key]) => key === "owner_user_id"),
            false,
          );
          const ids = await page
            .locator("[id]")
            .evaluateAll((els) => els.map((el) => el.id));
          assert.equal(new Set(ids).size, ids.length, "duplicate IDs");
          assert.equal(
            await overflow(),
            false,
            `${mode}/${width}/${theme} expanded overflow`,
          );
          if (width === 390 || width === 1440) {
            await page.screenshot({
              path: path.join(
                fixtures,
                `${mode}-${width}-${theme}-expanded.png`,
              ),
              fullPage: true,
            });
            await form
              .locator("[data-llm-row]")
              .last()
              .scrollIntoViewIfNeeded();
            await page.screenshot({
              path: path.join(fixtures, `${mode}-${width}-${theme}-rows.png`),
              fullPage: true,
            });
            await page.locator("#llm-add > summary").click();
            await page.evaluate(() => {
              scrollTo(0, 0);
              document.querySelector(".shell-content").scrollTop = 0;
            });
            await page.screenshot({
              path: path.join(fixtures, `${mode}-${width}-${theme}.png`),
              fullPage: true,
            });
          }
          assert.deepEqual(external, []);
          assert.deepEqual(errors, []);
          passed++;
          await context.close();
        }
    console.log(JSON.stringify({ passed, failed: 0 }));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
