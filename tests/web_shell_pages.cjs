/* Offline strict-fixture smoke and interaction tests. All HTTP intercepted;
 * never launches ASGI, queries DB, sends messages or calls providers.
 * node tests/web_shell_pages.cjs <fixtures> <offline-vendor-dir> */
const { chromium } = require("playwright");
const fs = require("fs"),
  path = require("path"),
  assert = require("node:assert/strict");
const fixtures = path.resolve(process.argv[2]),
  vendor = path.resolve(process.argv[3]);
const root = path.resolve(__dirname, "..");
const contractsOnly = process.argv.includes("--contracts-only");
const report = { cases: [], interactions: [], failures: [] };
(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROMIUM_BIN,
    args: ["--no-sandbox"],
  });
  async function setup(name, width = 390, theme = "light") {
    const context = await browser.newContext({
      viewport: { width, height: 900 },
      colorScheme: theme === "system" ? "dark" : theme,
    });
    await context.addInitScript(
      (mode) => localStorage.setItem("theme", mode),
      theme,
    );
    const page = await context.newPage(),
      errors = [],
      calls = [];
    let inviteStatus = 200;
    page.on("pageerror", (e) => errors.push(e.message));
    await page.route("**/*", async (route) => {
      const request = route.request(),
        u = new URL(request.url());
      if (u.hostname !== "fixture.test") return route.abort();
      if (u.pathname.startsWith("/static/"))
        return route.fulfill({
          path: u.pathname.startsWith("/static/vendor/")
            ? path.join(vendor, path.basename(u.pathname))
            : path.join(root, "app", u.pathname),
        });
      if (u.pathname === "/app/notifications")
        return route.fulfill({
          contentType: "application/json",
          body: JSON.stringify({
            notifications: [
              {
                id: 1,
                title: "Мок",
                message: "Изолированная проверка",
                created_at: "fixture",
                is_read: false,
              },
            ],
          }),
        });
      if (u.pathname === "/app/workspaces/select") {
        const fields = new URLSearchParams(request.postData());
        calls.push({
          url: u.pathname,
          method: request.method(),
          csrf: fields.get("_csrf"),
          tenant: fields.get("tenant_id"),
        });
        return route.fulfill({
          contentType: "text/html",
          body: "<p>Mock workspace selection only</p>",
        });
      }
      if (u.pathname === "/app/settings/invites") {
        calls.push({ url: u.pathname, method: request.method() });
        return route.fulfill({
          status: inviteStatus,
          contentType: "text/html",
          body:
            inviteStatus === 200
              ? fs.readFileSync(
                  path.join(fixtures, "invite-partial.html"),
                  "utf8",
                )
              : "Forbidden",
        });
      }
      if (u.pathname === "/" + name)
        return route.fulfill({
          contentType: "text/html",
          path: path.join(fixtures, name + ".html"),
        });
      calls.push({ url: u.pathname, method: request.method() });
      return route.abort();
    });
    await page.goto("http://fixture.test/" + name);
    await page.waitForFunction(
      () => typeof Alpine !== "undefined" && document.querySelector("style"),
    );
    await page.waitForTimeout(200);
    return {
      page,
      context,
      errors,
      calls,
      deny: () => {
        inviteStatus = 403;
      },
    };
  }
  try {
    const allNames = JSON.parse(
      fs.readFileSync(path.join(fixtures, "pages.json")),
    );
    const names = contractsOnly
      ? allNames.filter((name) => name.endsWith("-viewer") || name === "panel")
      : allNames;
    for (const name of names)
      for (const width of [375, 390, 768, 1440])
        for (const theme of ["light", "dark", "system"]) {
          const f = await setup(name, width, theme);
          try {
            assert.equal(
              await f.page.locator("h1").count(),
              1,
              "one navbar heading",
            );
            assert.equal(
              await f.page
                .locator('[data-dialog-open="context-panel"]')
                .count(),
              name === "panel" ? 1 : 0,
              "no phantom context toggle",
            );
            if (name.endsWith("-viewer")) {
              assert.equal(
                await f.page
                  .locator("#page-actions button, #page-actions form")
                  .count(),
                0,
                "read-only actions gated",
              );
              assert.equal(
                await f.page.locator("[data-invite-open]").count(),
                0,
                "read-only invite entry gated",
              );
            }
            assert.equal(
              await f.page
                .locator(".shell-navbar")
                .evaluate((el) => getComputedStyle(el).backgroundColor),
              "rgba(0, 0, 0, 0)",
              "navbar only has a separator, no distinct fill",
            );
            const metrics = await f.page.evaluate(() => ({
              w: document.documentElement.scrollWidth,
              viewport: innerWidth,
              mainWidth: document.querySelector("main").clientWidth,
              mainScroll: document.querySelector("main").scrollWidth,
              dark: document.documentElement.classList.contains("dark"),
            }));
            assert.ok(
              metrics.w <= width,
              `horizontal overflow ${metrics.w}/${width}`,
            );
            assert.ok(
              metrics.mainScroll <= metrics.mainWidth + 1,
              `main horizontal overflow ${metrics.mainScroll}/${metrics.mainWidth}`,
            );
            assert.equal(metrics.dark, theme !== "light", "theme application");
            const main = await f.page.locator("main").boundingBox(),
              dock = await f.page
                .locator(".shell-notification-dock")
                .boundingBox();
            assert.ok(
              !dock || main.y + main.height <= dock.y + 1,
              "content viewport separated from bell",
            );
            if (name.startsWith("chat")) {
              await f.page.locator(".chat-composer").scrollIntoViewIfNeeded();
              const composer = await f.page
                .locator(".chat-composer")
                .boundingBox();
              assert.ok(
                composer && composer.y + composer.height <= dock.y + 1,
                "composer below fold must remain outside bell band",
              );
            }
            assert.deepEqual(f.errors, [], "no runtime errors");
            assert.deepEqual(
              f.calls,
              [],
              "lazy invites; no unexpected endpoint calls",
            );
            if (
              ["chat", "source_detail", "task_edit", "onboarding"].includes(
                name,
              ) &&
              width === 390 &&
              theme === "light"
            )
              await f.page.screenshot({
                path: path.join(fixtures, name + "-390-light.png"),
              });
            report.cases.push({ name, width, theme });
          } catch (e) {
            report.failures.push({
              name,
              width,
              theme,
              error: e.message,
              errors: f.errors,
            });
            console.log("FAIL", name, width, theme, e.message);
          }
          await f.context.close();
          if (width === 1440 && theme === "system")
            console.log("Checked", name);
        }
    // Workspace utilities are a paired top row, not buried below the switcher.
    for (const width of [375, 390, 768, 1440])
      for (const theme of ["light", "dark", "system"])
        for (const manager of [true, false]) {
          const f = await setup(manager ? "settings" : "settings-viewer", width, theme);
          try {
            if (width < 768)
              await f.page.locator('[data-dialog-open="navigation-drawer"]').click();
            const scope = f.page.locator(width < 768 ? "#navigation-drawer" : ".shell-sidebar");
            await scope.locator(".shell-account-trigger").click();
            const actions = scope.locator(".shell-workspace-actions");
            const settings = actions.locator('a[href="/app/settings"]');
            const invite = actions.locator("[data-invite-open]");
            assert.equal(await settings.count(), 1);
            assert.equal(await invite.count(), manager ? 1 : 0);
            const row = await actions.boundingBox();
            const switcher = await scope.locator(".shell-workspaces").boundingBox();
            assert.ok(row.y + row.height <= switcher.y, "utilities precede workspace switcher");
            for (const button of await actions.locator("a,button").all()) {
              const box = await button.boundingBox();
              assert.ok(box.height >= 44 && box.x >= 0 && box.x + box.width <= width,
                "44px targets contained in viewport");
              assert.ok(await button.evaluate(el => el.scrollWidth <= el.clientWidth + 1),
                "label has no horizontal overflow");
              assert.ok(await button.locator("span").evaluate(el => {
                const canvas = document.createElement("canvas"), ctx = canvas.getContext("2d");
                ctx.font = getComputedStyle(el).font;
                return el.textContent.trim().split(/\s+/).every(word =>
                  ctx.measureText(word).width <= el.clientWidth + 1);
              }), "action labels wrap at word boundaries, not inside words");
            }
            if (manager) {
              const left = await settings.boundingBox(), right = await invite.boundingBox();
              assert.ok(Math.abs(left.y - right.y) < 1 && left.x + left.width <= right.x,
                "settings then invite side by side");
              await settings.focus();
              await f.page.keyboard.press("Tab");
              assert.ok(await invite.evaluate(el => document.activeElement === el),
                "keyboard follows visual action order");
            }
            assert.deepEqual(f.calls, [], "opening menu never preloads invites");
            assert.deepEqual(f.errors, []);
            if (manager && theme === "dark" && [375, 1440].includes(width))
              await f.page.screenshot({ path: path.join(fixtures, `workspace-actions-${width}-dark.png`) });
            report.interactions.push({ name: "workspace-action-pair", width, theme, manager });
          } catch (e) {
            report.failures.push({ name: "workspace-action-pair", width, theme, manager, error: e.message });
          } finally {
            await f.context.close();
          }
        }
    for (const width of [390, 1440]) {
      const f = await setup("dashboard", width);
      try {
        if (width < 768)
          await f.page
            .locator('[data-dialog-open="navigation-drawer"]')
            .click();
        const scope = f.page.locator(
          width < 768 ? "#navigation-drawer" : ".shell-sidebar",
        );
        await scope.locator(".shell-account-trigger").click();
        assert.equal(
          await scope.locator(".shell-workspace-current").count(),
          1,
          "one active workspace, no duplicate context row",
        );
        assert.equal(
          await scope.locator('form[action="/app/workspaces/select"]').count(),
          2,
          "all alternate workspace switches",
        );
        assert.equal(
          await scope.locator('a[href="/app/invite"]').count(),
          0,
          "join is not a daily workspace action",
        );
        await f.page.screenshot({
          path: path.join(fixtures, `workspace-menu-${width}.png`),
        });
        await scope
          .getByRole("button", {
            name: "Переключиться в пространство «Команда аналитики»",
            exact: true,
          })
          .click();
        await f.page.waitForURL("**/app/workspaces/select");
        assert.deepEqual(f.calls, [
          {
            url: "/app/workspaces/select",
            method: "POST",
            csrf: "local-fixture-only",
            tenant: "8",
          },
        ]);
        assert.deepEqual(f.errors, []);
        report.interactions.push({
          name: "grouped-workspace-post-csrf",
          width,
        });
      } catch (e) {
        report.failures.push({
          name: "workspace-switch",
          width,
          error: e.message,
          errors: f.errors,
        });
      } finally {
        await f.context.close();
      }
    }
    for (const width of [390, 1440]) {
      const f = await setup("settings", width);
      try {
        const mobile = width < 768;
        const navOpen = f.page.locator(
          '[data-dialog-open="navigation-drawer"]',
        );
        if (mobile) await navOpen.click();
        const account = f.page.locator(
          mobile
            ? "#navigation-drawer .shell-account-trigger"
            : ".shell-sidebar .shell-account-trigger",
        );
        await account.click();
        await f.page
          .locator(
            mobile
              ? "#navigation-drawer [data-invite-open]"
              : ".shell-sidebar [data-invite-open]",
          )
          .click();
        await f.page.locator("[data-issued-invite]").waitFor();
        assert.equal(f.calls.length, 1, "first open fetches exactly once");
        assert.equal(
          await f.page.locator("#navigation-drawer").evaluate((d) => d.open),
          false,
        );
        const forms = await f.page
          .locator("#invite-body form")
          .evaluateAll((forms) =>
            forms.map((form) => ({
              method: form.method,
              action: form.getAttribute("action"),
              csrf: form.querySelector('[name="_csrf"]')?.value,
            })),
          );
        assert.equal(forms.length, 2, "issue and revoke remain available");
        assert.ok(
          forms.every(
            (form) =>
              form.method === "post" && form.csrf === "local-fixture-only",
          ),
        );
        assert.equal(
          await f.page
            .locator('#invite-body select[name="days"] option')
            .count(),
          3,
        );
        const close = f.page.locator("#invite-dialog [data-dialog-close]");
        await close.focus();
        await f.page.keyboard.press("Shift+Tab");
        assert.ok(
          await f.page
            .locator("#invite-body form")
            .last()
            .locator("button")
            .evaluate((e) => document.activeElement === e),
          "wrap focus backwards",
        );
        await f.page.keyboard.press("Tab");
        assert.ok(
          await close.evaluate((e) => document.activeElement === e),
          "wrap focus forwards",
        );
        await f.page.keyboard.press("Escape");
        await f.page.waitForTimeout(60);
        assert.ok(
          await (mobile ? navOpen : account).evaluate(
            (e) => document.activeElement === e,
          ),
          "restore visible launcher",
        );
        f.deny();
        if (mobile) await navOpen.click();
        await account.click();
        await f.page
          .locator(
            mobile
              ? "#navigation-drawer [data-invite-open]"
              : ".shell-sidebar [data-invite-open]",
          )
          .click();
        await f.page
          .getByRole("button", { name: "Повторить", exact: true })
          .waitFor();
        assert.equal(f.calls.length, 2, "reopen rechecks authorization");
        assert.equal(
          await f.page.locator("[data-issued-invite]").count(),
          0,
          "forbidden reopen removes previous code/list",
        );
        await f.page.keyboard.press("Escape");
        assert.deepEqual(f.errors, []);
        report.interactions.push({
          name: "lazy-invite-focus-forbidden-reopen",
          width,
        });
      } catch (e) {
        report.failures.push({
          name: "invites",
          width,
          error: e.message,
          errors: f.errors,
        });
      } finally {
        await f.context.close();
      }
      const p = await setup("panel", width);
      try {
        const trigger = p.page.locator('[data-dialog-open="context-panel"]');
        await trigger.click();
        assert.equal(await trigger.getAttribute("aria-expanded"), "true");
        const close = p.page.locator("#context-panel [data-dialog-close]");
        await close.focus();
        await p.page.keyboard.press("Shift+Tab");
        assert.equal(
          await p.page
            .locator("#context-panel button")
            .last()
            .evaluate((e) => document.activeElement === e),
          true,
        );
        await p.page.keyboard.press("Tab");
        assert.equal(
          await close.evaluate((e) => document.activeElement === e),
          true,
        );
        await p.page.keyboard.press("Escape");
        await p.page.waitForTimeout(60);
        assert.equal(await trigger.getAttribute("aria-expanded"), "false");
        assert.equal(
          await trigger.evaluate((e) => document.activeElement === e),
          true,
        );
        report.interactions.push({ name: "right-panel-trap-return", width });
      } catch (e) {
        report.failures.push({ name: "panel", width, error: e.message });
      } finally {
        await p.context.close();
      }
    }
    for (const [name, flag] of [
      ["sources", "openAdd"],
      ["tasks", "openAdd"],
      ["source_detail", "openEdit"],
      ["task_detail", "openEdit"],
    ]) {
      const f = await setup(name, 1440);
      try {
        await f.page
          .locator("#page-actions button")
          .filter({
            hasText:
              name === "tasks"
                ? "Создать задачу"
                : flag === "openAdd"
                  ? "Добавить"
                  : "Редактировать",
          })
          .first()
          .click();
        await f.page
          .locator(`[x-show="${flag}"]`)
          .waitFor({ state: "visible" });
        await f.page.keyboard.press("Escape");
        await f.page.locator(`[x-show="${flag}"]`).waitFor({ state: "hidden" });
        assert.deepEqual(f.errors, []);
        report.interactions.push({ name: "teleported-action-" + name });
      } catch (e) {
        report.failures.push({
          name: "actions-" + name,
          error: e.message,
          errors: f.errors,
        });
      } finally {
        await f.context.close();
      }
    }
    const rail = await setup("dashboard", 1440);
    try {
      await rail.page.locator("[data-sidebar-collapse]").click();
      assert.equal(
        await rail.page
          .locator("[data-sidebar-collapse]")
          .getAttribute("aria-expanded"),
        "false",
      );
      assert.equal(
        await rail.page
          .locator("[data-sidebar-collapse]")
          .getAttribute("aria-label"),
        "Развернуть навигацию",
      );
      assert.equal(
        await rail.page.evaluate(() =>
          localStorage.getItem("app.sidebar.collapsed"),
        ),
        "true",
      );
      await rail.page.screenshot({
        path: path.join(fixtures, "compact-sidebar.png"),
      });
      await rail.page
        .locator(".shell-sidebar .shell-sidebar-footer [data-popover-trigger]")
        .click();
      const panel = rail.page.locator("#theme-popover-desktop");
      const box = await panel.boundingBox();
      assert.ok(
        box.x >= 0 && box.y >= 0 && box.x + box.width <= 1440,
        "footer theme stays inside viewport",
      );
      await rail.page.screenshot({
        path: path.join(fixtures, "footer-theme.png"),
      });
      await panel.locator('[data-theme-choice="dark"]').click();
      await rail.page.waitForFunction(() =>
        document.documentElement.classList.contains("dark"),
      );
      assert.ok(
        await rail.page
          .locator(".shell-sidebar .shell-sidebar-footer [data-popover-trigger]")
          .evaluate((e) => document.activeElement === e),
      );
      await rail.page.reload();
      await rail.page.waitForFunction(
        () => document.documentElement.dataset.sidebarCollapsed === "true",
      );
      assert.deepEqual(rail.errors, []);
      report.interactions.push({
        name: "compact-rail-persistence-footer-theme",
      });
    } catch (e) {
      report.failures.push({
        name: "rail-footer",
        error: e.message,
        errors: rail.errors,
      });
    } finally {
      await rail.context.close();
    }
    for (const name of ["scenario_detail", "scenario_wizard"]) {
      const f = await setup(name, 390);
      try {
        const form = await f.page
          .locator('#page-actions button[form="scenario-form"]')
          .evaluate((b) => ({
            id: b.form?.id,
            csrf: b.form?.querySelector('[name="_csrf"]')?.value,
          }));
        assert.equal(form.id, "scenario-form");
        assert.equal(form.csrf, "local-fixture-only");
        assert.deepEqual(f.errors, []);
        report.interactions.push({ name: "save-form-owner-" + name });
      } catch (e) {
        report.failures.push({
          name: "save-form-" + name,
          error: e.message,
          errors: f.errors,
        });
      } finally {
        await f.context.close();
      }
    }
    const edit = await setup("task_edit", 390);
    try {
      await edit.page.locator('input[name="name"]').fill("Новое имя задачи");
      assert.equal(
        await edit.page.locator("h1").innerText(),
        "Новое имя задачи",
      );
      assert.deepEqual(edit.errors, []);
      report.interactions.push({ name: "task-heading-live-update" });
    } catch (e) {
      report.failures.push({
        name: "task-heading",
        error: e.message,
        errors: edit.errors,
      });
    } finally {
      await edit.context.close();
    }
  } finally {
    await browser.close();
    fs.writeFileSync(
      path.join(
        fixtures,
        contractsOnly
          ? "shell-contracts-report.json"
          : "shell-pages-report.json",
      ),
      JSON.stringify(report, null, 2),
    );
  }
  console.log(
    JSON.stringify(
      {
        cases: report.cases.length,
        interactions: report.interactions.length,
        failures: report.failures,
      },
      null,
      2,
    ),
  );
  if (report.failures.length) process.exitCode = 1;
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
