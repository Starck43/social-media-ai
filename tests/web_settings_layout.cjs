/* Synthetic settings UI only. All network endpoints intercepted; no app/DB/provider. */
const { chromium } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const root = path.resolve(__dirname, "..");
const fixtures = path.resolve(process.argv[2]);
const vendor = path.resolve(process.argv[3]);
const modes = [
  "account",
  "no-workspace",
  "member-workspace",
  "member-team",
  "owner-workspace",
  "owner-team",
  "team-empty",
  "agent",
  "agent-error",
  "channels",
  "channels-empty",
  "connections",
  "operator",
];

async function choose(root, name, value) {
  const picker = root.locator(
    `[data-popover-select]:has(input[name="${name}"])`,
  );
  await picker.locator("[data-popover-trigger]").press("ArrowDown");
  assert.equal(await picker.locator('[data-popover-option=""]').count(), 0);
  const bounds = await picker.locator(".shell-popover-panel").boundingBox();
  assert.ok(
    bounds.y >= 0 &&
      bounds.y + bounds.height <= root.page().viewportSize().height,
    "open selector must fit vertically",
  );
  assert.ok(
    bounds.x >= 0 &&
      bounds.x + bounds.width <= root.page().viewportSize().width,
    "open selector must fit horizontally",
  );
  if ([390, 1440].includes(root.page().viewportSize().width)) {
    const theme = await root
      .page()
      .evaluate(() =>
        document.documentElement.classList.contains("dark") ? "dark" : "light",
      );
    await root.page().screenshot({
      path: path.join(
        fixtures,
        `select-${name}-${value}-${root.page().viewportSize().width}-${theme}.png`,
      ),
    });
  }
  await picker.locator(`[data-popover-option="${value}"]`).press("Enter");
}

async function main() {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROMIUM_BIN,
    args: ["--no-sandbox"],
  });
  const report = { cases: [], failures: [] };
  try {
    for (const mode of modes)
      for (const width of [375, 390, 768, 1440])
        for (const theme of ["light", "dark"]) {
          const context = await browser.newContext({
            viewport: { width, height: 1000 },
            colorScheme: theme,
          });
          const page = await context.newPage();
          const errors = [],
            external = [];
          let inviteState = "open";
          page.on("pageerror", (e) => errors.push(e.message));
          await context.addInitScript(
            (t) => localStorage.setItem("theme", t),
            theme,
          );
          await page.route("**/*", async (route) => {
            const req = route.request(),
              url = new URL(req.url());
            if (url.hostname !== "fixture.test") {
              external.push(req.url());
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
                contentType: "application/json",
                body: JSON.stringify({
                  context: { workspace_id: 7, user_id: 101 },
                  notifications: [],
                  count: 0,
                  complete: true,
                  has_more: false,
                }),
              });
            if (url.pathname === "/app/notifications/preferences")
              return route.fulfill({
                contentType: "application/json",
                body: JSON.stringify({
                  user_id: 101,
                  preferences: {
                    version: 1,
                    connections: true,
                    tasks: true,
                    popup: false,
                    quiet: false,
                    start: "22:00",
                    end: "08:00",
                  },
                }),
              });
            if (url.pathname === "/app/settings/invites")
              return route.fulfill({
                path: path.join(fixtures, `invites-${inviteState}.html`),
              });
            if (url.pathname === "/settings")
              return route.fulfill({
                path: path.join(fixtures, `settings-${mode}.html`),
              });
            return route.abort();
          });
          try {
            await page.goto("http://fixture.test/settings");
            await page.waitForFunction(
              () =>
                typeof Alpine !== "undefined" &&
                typeof tailwind !== "undefined",
            );
            await page.waitForTimeout(80);
            const content = page.locator("#page-content");
            const nav = content.getByRole("navigation", {
              name: "Разделы настроек",
              exact: true,
              includeHidden: true,
            });
            assert.equal(await nav.count(), 1);
            assert.equal(await nav.locator('[aria-current="page"]').count(), 1);
            assert.equal(await content.locator(".ui-segments").count(), 0);
            assert.equal(await page.locator("h1").count(), 1);
            assert.equal(
              await content.locator('a[href="/admin/"]').count(),
              mode === "operator" ? 1 : 0,
            );
            assert.equal(await content.locator("select").count(), 0);
            const menu = content.locator(".settings-menu");
            if (width < 1024) {
              assert.equal(await menu.getAttribute("open"), null);
              const toggle = menu.locator("summary.settings-menu-trigger");
              await toggle.focus();
              await page.keyboard.press("Enter");
              assert.equal(await nav.isVisible(), true);
              await page.screenshot({
                path: path.join(
                  fixtures,
                  `navigation-${mode}-${width}-${theme}.png`,
                ),
              });
              await page.keyboard.press("Enter");
              assert.equal(await nav.isVisible(), false);
            } else {
              assert.equal(await nav.isVisible(), true);
            }
            await page.evaluate(() => {
              window.syntheticSubmissions = [];
              document.addEventListener("submit", (event) => {
                if (event.defaultPrevented) return;
                event.preventDefault();
                const button = event.submitter;
                window.syntheticSubmissions.push({
                  action: new URL(
                    button?.getAttribute("formaction") || event.target.action,
                    location.href,
                  ).pathname,
                  fields: Object.fromEntries(
                    new FormData(event.target, button),
                  ),
                });
              });
            });
            if (mode === "owner-team") {
              assert.equal(
                await content.locator('[data-member-id="1"] summary').count(),
                0,
              );
              assert.equal(
                await content
                  .locator('form[action="/app/settings/members/1/role"]')
                  .count(),
                0,
              );
              const member = content.locator('[data-member-id="2"]');
              assert.equal(
                await member
                  .getByRole("button", {
                    name: "Отключить доступ",
                    exact: true,
                  })
                  .isVisible(),
                false,
              );
              const summary = member.locator("summary");
              await summary.focus();
              await page.keyboard.press("Enter");
              await choose(member, "role", "viewer");
              await member
                .getByRole("button", { name: "Сохранить роль", exact: true })
                .click();
              await member
                .getByRole("button", { name: "Отключить AI", exact: true })
                .click();
              await member
                .getByRole("button", { name: "Отключить доступ", exact: true })
                .click();
              const confirm = page.locator("#ui-confirm");
              await confirm.waitFor({ state: "visible" });
              assert.ok((await confirm.innerText()).includes("Анна Смирнова"));
              await page.keyboard.press("Escape");
              assert.equal(
                await page.evaluate(() => syntheticSubmissions.length),
                2,
              );
              await member
                .getByRole("button", { name: "Отключить доступ", exact: true })
                .click();
              await confirm.locator("[data-confirm-accept]").click();
              const posts = await page.evaluate(() => syntheticSubmissions);
              assert.deepEqual(
                posts.map((p) => p.action),
                [
                  "/app/settings/members/2/role",
                  "/app/settings/members/2/ai",
                  "/app/settings/members/2/disable",
                ],
              );
              assert.equal(posts[0].fields.role, "viewer");
              assert.equal(posts[1].fields.enabled, "0");
              assert.ok(posts.every((p) => p.fields._csrf === "fixture-csrf"));
              assert.equal(
                await content
                  .locator('form[action="/app/settings/members/1/disable"]')
                  .count(),
                0,
              );
              // Lazy dialog opens without issuing an invite, then uses existing independent forms.
              await content.locator("[data-invite-open]").click();
              const dialog = page.locator("#invite-dialog");
              await dialog
                .locator('form[action="/app/settings/invites"]')
                .waitFor();
              await choose(dialog, "role", "editor");
              await choose(dialog, "days", "30");
              await dialog
                .getByRole("button", {
                  name: "Создать приглашение",
                  exact: true,
                })
                .click();
              assert.equal(
                (await page.evaluate(() => syntheticSubmissions)).at(-1).fields
                  .role,
                "editor",
              );
              assert.equal(
                (await page.evaluate(() => syntheticSubmissions)).at(-1).fields
                  .days,
                "30",
              );
              await dialog
                .getByRole("button", { name: "Отозвать", exact: true })
                .click();
              await confirm.locator("[data-confirm-cancel]").click();
              assert.equal(
                (await page.evaluate(() => syntheticSubmissions)).at(-1).action,
                "/app/settings/invites",
              );
              await dialog
                .getByRole("button", { name: "Отозвать", exact: true })
                .click();
              await confirm.locator("[data-confirm-accept]").click();
              assert.equal(
                (await page.evaluate(() => syntheticSubmissions)).at(-1).action,
                "/app/settings/invites/42/revoke",
              );
              await page.screenshot({
                path: path.join(fixtures, `invites-${width}-${theme}.png`),
              });
              await dialog.locator("[data-dialog-close]").click();
              inviteState = "issued";
              await content.locator("[data-invite-open]").click();
              await dialog.locator("[data-issued-invite]").waitFor();
              const previousPosts = await page.evaluate(
                () => syntheticSubmissions.length,
              );
              await choose(dialog, "role", "editor");
              await choose(dialog, "days", "1");
              assert.equal(
                await dialog.locator('input[name="days"]').inputValue(),
                "1",
              );
              assert.equal(
                await page.evaluate(() => syntheticSubmissions.length),
                previousPosts,
              );
              assert.equal(
                await dialog.locator("#invite-link").inputValue(),
                "http://fixture.test/app/invite/fake",
              );
              await page.screenshot({
                path: path.join(
                  fixtures,
                  `invite-issued-${width}-${theme}.png`,
                ),
              });
              await dialog.locator("[data-dialog-close]").click();
              await content.locator('[data-member-id="3"] summary').click();
            }
            if (mode === "agent" || mode === "agent-error") {
              const group = content.locator("[data-model-controls]");
              const trigger = group.locator("[data-popover-trigger]");
              const probe = group.getByRole("button", {
                name: "Тестировать модель",
                exact: true,
              });
              const left = await trigger.boundingBox(),
                right = await probe.boundingBox();
              assert.ok(
                right.x >= left.x + left.width,
                "probe must be to the right of selector",
              );
              assert.ok(Math.abs(left.y - right.y) < 2, "model row must align");
              await trigger.focus();
              await page.keyboard.press("Enter");
              assert.equal(
                await page.evaluate(() => syntheticSubmissions.length),
                0,
                "opening selector must not submit",
              );
              await group.locator('[data-popover-option="32"]').click();
              assert.equal(
                await content.locator('input[name="agent_model_id"]').inputValue(),
                "32",
              );
              assert.ok(
                (await trigger.boundingBox()).height <= 64,
                "long selected model must remain compact",
              );
              await probe.click();
              let posts = await page.evaluate(() => syntheticSubmissions);
              assert.equal(posts[0].action, "/app/settings/agent/model-probe");
              assert.equal(posts[0].fields.agent_model_id, "32");
              await content.locator("#ag-max-tokens").fill("2048");
              await content.locator("#ag-max-tokens").press("Enter");
              posts = await page.evaluate(() => syntheticSubmissions);
              assert.equal(posts[1].action, "/app/settings/agent");
              assert.equal(posts[1].fields.agent_max_tokens, "2048");
              await content.locator("#ag-max-tokens").fill("1");
              await content.locator("[data-agent-save]").click();
              assert.equal(
                await page.evaluate(() => syntheticSubmissions.length),
                2,
                "native validation must be retained",
              );
              await content.locator("#ag-max-tokens").fill("2048");
              await content.locator("#ag-prompt").press("Enter");
              assert.equal(
                await page.evaluate(() => syntheticSubmissions.length),
                2,
                "textarea Enter must not submit",
              );
            }
            if (mode === "channels") {
              const form = content.locator(
                'form[action="/app/settings/channels/12"]',
              );
              await form
                .getByRole("button", { name: "Сохранить", exact: true })
                .click();
              assert.equal(
                (await page.evaluate(() => syntheticSubmissions))[0].action,
                "/app/settings/channels/12",
              );
            }
            if (mode === "owner-workspace") {
              if (width === 1440) {
                const account = page.locator(
                  ".shell-sidebar .shell-account-trigger",
                );
                await account.click();
                assert.equal(
                  await account.getAttribute("aria-expanded"),
                  "true",
                );
                await page.locator("#page-heading").click();
                assert.equal(
                  await account.getAttribute("aria-expanded"),
                  "false",
                );
                const themeMenu = page.locator(".shell-sidebar .shell-theme");
                await themeMenu.locator("[data-popover-trigger]").click();
                await themeMenu
                  .locator(`[data-theme-choice="${theme}"]`)
                  .click();
                assert.equal(
                  await themeMenu
                    .locator("[data-popover-trigger]")
                    .getAttribute("aria-expanded"),
                  "false",
                );
              }
              assert.equal(
                await content.locator(".settings-plans summary").count(),
                0,
              );
              assert.equal(
                await content.locator(".settings-plans table").isVisible(),
                true,
              );
            }
            assert.equal(
              await page.evaluate(
                () => document.documentElement.scrollWidth <= innerWidth,
              ),
              true,
              "page overflow",
            );
            const outliers = await content
              .locator(
                'button:visible, select:visible, input:not([type="hidden"]):visible, summary:visible',
              )
              .evaluateAll((els) =>
                els
                  .filter((el) => {
                    const r = el.getBoundingClientRect();
                    return r.width && (r.right > innerWidth + 1 || r.left < -1);
                  })
                  .map((el) => el.outerHTML.slice(0, 120)),
              );
            assert.deepEqual(
              outliers,
              [],
              "controls must stay inside viewport",
            );
            assert.deepEqual(errors, []);
            assert.deepEqual(
              external,
              [],
              "all network requests must remain synthetic",
            );
            if ([390, 1440].includes(width)) {
              await page.locator("#page-content").evaluate((el) => {
                el.scrollTop = 0;
              });
              await page.screenshot({
                path: path.join(fixtures, `${mode}-${width}-${theme}.png`),
                fullPage: true,
              });
              await page.locator("#page-content").evaluate((el) => {
                el.scrollTop = el.scrollHeight;
              });
              await page.screenshot({
                path: path.join(
                  fixtures,
                  `${mode}-bottom-${width}-${theme}.png`,
                ),
              });
            }
            report.cases.push({ mode, width, theme });
          } catch (e) {
            report.failures.push({
              mode,
              width,
              theme,
              error: e.message,
              errors,
            });
          } finally {
            await context.close();
          }
        }
  } finally {
    await browser.close();
    fs.writeFileSync(
      path.join(fixtures, "settings-layout-report.json"),
      JSON.stringify(report, null, 2),
    );
  }
  console.log(
    JSON.stringify(
      { passed: report.cases.length, failures: report.failures },
      null,
      2,
    ),
  );
  if (report.failures.length) process.exitCode = 1;
}
main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
