/* Optional local visual regression harness. Requires Node + Playwright, a local
 * Chromium and Python/Jinja fixture environment. No application/DB imports.
 * All page/provider requests are intercepted; unknown URLs abort and fail.
 * Pre-fetch public Tailwind 3.4.17, Alpine 3.14.1, HTMX 1.9.12 into ASSETS_DIR as
 * tailwind.js, alpine.js, htmx.js. Never provide real credentials/accounts.
 */
const { chromium } = require("playwright");
const fs = require("fs"),
  path = require("path");
const { execFileSync } = require("child_process");
const { pathToFileURL } = require("url");
const root = fs.realpathSync(path.resolve(__dirname, "../.."));
const [assetsArg, outArg] = process.argv.slice(2);
if (!assetsArg || !outArg)
  throw Error(
    "Usage: node tests/shared/file_catalog_browser.cjs ASSETS_DIR OUTPUT_DIR",
  );
const assets = path.resolve(assetsArg),
  out = path.resolve(outArg);
execFileSync(
  process.env.PYTHON || "python",
  ["-m", "tests.shared.file_catalog_layout", "--output", out],
  { cwd: root },
);
const staticUri = pathToFileURL(path.join(root, "app/static")).href;
(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM || "chromium",
    headless: true,
    args: ["--no-sandbox"],
  });
  const errors = [],
    blocked = [],
    checks = [];
  async function setup(width, dark = false, dock = true) {
    const context = await browser.newContext({
      viewport: { width, height: 900 },
      colorScheme: dark ? "dark" : "light",
      reducedMotion: "reduce",
    });
    const page = await context.newPage();
    page.on("pageerror", (e) => errors.push(e.message));
    await page.route("**/*", async (route) => {
      const u = new URL(route.request().url());
      if (u.host === "catalog.test") {
        if (u.pathname.startsWith("/static/"))
          return route.fulfill({ path: path.join(root, "app", u.pathname) });
        if (u.pathname === "/app/notifications/preferences")
          return route.fulfill({
            json: {
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
            },
          });
        if (u.pathname === "/app/notifications") {
          const items = dock
            ? [
                {
                  key: "synthetic:connection",
                  kind: "connection",
                  scope: "personal",
                  title: "Проверьте подключение",
                  message:
                    "Синтетический сигнал. Реальный диск не используется.",
                  created_at: "2026-11-03T09:00:00Z",
                  workspace_id: 7,
                  user_id: 101,
                  read_id: null,
                  action: "open",
                  destination: "/app/settings?tab=connections",
                },
              ]
            : [];
          return route.fulfill({
            json: {
              context: { workspace_id: 7, user_id: 101 },
              notifications: items,
              count: items.length,
              complete: true,
              has_more: false,
              task_events_authorized: false,
              unavailable: [],
            },
          });
        }
        const file = path.join(out, u.pathname.slice(1) + ".html");
        if (fs.existsSync(file))
          return route.fulfill({
            contentType: "text/html",
            body: fs
              .readFileSync(file, "utf8")
              .replaceAll(staticUri, "http://catalog.test/static"),
          });
      }
      const map = {
        "cdn.tailwindcss.com": "tailwind.js",
        "cdn.jsdelivr.net": "alpine.js",
        "unpkg.com": "htmx.js",
      };
      if (map[u.host])
        return route.fulfill({
          contentType: "text/javascript",
          path: path.join(assets, map[u.host]),
        });
      blocked.push(u.href);
      return route.abort();
    });
    return { context, page };
  }
  async function freeze(page, name) {
    await page.screenshot({ path: path.join(out, name + ".png") });
    const scroll = await page
      .locator(".shell-content")
      .evaluate((e) => e.scrollTop);
    let html = await page.content();
    html = html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "");
    html = html.replaceAll("http://catalog.test/static", staticUri);
    // Preserve the inspected scroll position in optional standalone captures.
    html = html.replace(
      "</body>",
      `<script>addEventListener('load',()=>document.querySelector('.shell-content').scrollTop=${scroll});</script></body>`,
    );
    fs.writeFileSync(path.join(out, name + "-frozen.html"), html);
  }
  function contrastScript() {
    const rgb = (s) =>
      s
        .match(/[\d.]+/g)
        .slice(0, 3)
        .map(Number);
    const lum = (s) =>
      rgb(s)
        .map((v) => v / 255)
        .map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
        .reduce((a, v, i) => a + v * [0.2126, 0.7152, 0.0722][i], 0);
    const ratio = (a, b) => {
      a = lum(a);
      b = lum(b);
      return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    };
    const input = document.querySelector("#file-query"),
      card = input.closest(".ui-card");
    return {
      border: ratio(
        getComputedStyle(input).borderColor,
        getComputedStyle(card).backgroundColor,
      ),
      placeholder: ratio(
        getComputedStyle(input, "::placeholder").color,
        getComputedStyle(input).backgroundColor,
      ),
    };
  }
  for (const width of [1440, 390]) {
    for (const state of [
      "list",
      "no-mounts",
      "empty",
      "search",
      "detail",
      "long-name",
      "scan-error",
    ]) {
      const { context, page } = await setup(width);
      await page.goto("http://catalog.test/" + state);
      await page.waitForFunction(
        () =>
          window.Alpine &&
          document
            .querySelector(".shell-notification-dock")
            ?.getBoundingClientRect().height > 0,
      );
      await page.waitForTimeout(350);
      const metrics = await page.evaluate(() => {
        const main = document.querySelector(".shell-content"),
          dock = document.querySelector(".shell-notification-dock");
        const a = main.getBoundingClientRect(),
          b = dock.getBoundingClientRect();
        let overlap = false;
        for (const y of [0, main.scrollHeight / 2, main.scrollHeight]) {
          main.scrollTop = y;
          const r = main.getBoundingClientRect();
          overlap ||= r.bottom > b.top;
        }
        main.scrollTop = 0;
        return {
          mainBottom: a.bottom,
          dockTop: b.top,
          overlap,
          overflow:
            main.scrollWidth > main.clientWidth + 1 ||
            document.documentElement.scrollWidth > innerWidth,
          margin: getComputedStyle(main).marginBottom,
        };
      });
      if (metrics.overlap || metrics.overflow)
        throw Error(JSON.stringify({ width, state, ...metrics }));
      if (state === "no-mounts") {
        const gap = await page
          .locator(".file-catalog-empty .ui-control")
          .evaluate(
            (e) =>
              e.getBoundingClientRect().top -
              e.previousElementSibling.getBoundingClientRect().bottom,
          );
        if (gap < 16) throw Error("empty CTA overlaps text: " + gap);
        metrics.ctaGap = gap;
      }
      if (
        width === 390 &&
        ["list", "long-name", "scan-error"].includes(state)
      ) {
        const itemWidth = await page
          .locator(".file-catalog-item")
          .first()
          .evaluate((e) => e.getBoundingClientRect().width);
        if (itemWidth < 200)
          throw Error("mobile filename squeezed to " + itemWidth);
        metrics.itemWidth = itemWidth;
      }
      if (state === "list") {
        metrics.contrast = await page.evaluate(contrastScript);
        if (metrics.contrast.border < 3 || metrics.contrast.placeholder < 4.5)
          throw Error("light contrast " + JSON.stringify(metrics.contrast));
      }
      checks.push({ width, state, ...metrics });
      if (width === 390) {
        const target = ["empty", "search"].includes(state)
          ? ".file-catalog-empty"
          : ["list", "long-name"].includes(state)
            ? ".file-catalog-name"
            : null;
        if (target)
          await page
            .locator(target)
            .first()
            .evaluate((e) => e.scrollIntoView({ block: "center" }));
      }
      await freeze(page, `${state}-${width}`);
      await context.close();
    }
  }
  for (const width of [1440, 390]) {
    const { context, page } = await setup(width, true);
    await page.goto("http://catalog.test/list");
    await page.waitForSelector(".shell-notification-dock:visible");
    await page.waitForTimeout(300);
    const contrasts = await page.evaluate(contrastScript);
    if (contrasts.border < 3 || contrasts.placeholder < 4.5)
      throw Error("contrast " + JSON.stringify(contrasts));
    checks.push({ width, state: "dark-contrast", ...contrasts });
    await freeze(page, `dark-${width}`);
    await context.close();
  }
  {
    const { context, page } = await setup(390);
    await page.goto("http://catalog.test/list");
    await page.waitForSelector(".shell-notification-dock:visible");
    await page
      .getByRole("button", { name: "Открыть навигацию", exact: true })
      .click();
    await page.waitForTimeout(200);
    await freeze(page, "menu-390");
    await page
      .getByRole("button", { name: "Закрыть навигацию", exact: true })
      .click();
    // Navigation and notification modal use real shell JS; responses stay synthetic.
    await page
      .getByRole("button", { name: "Уведомления", exact: true })
      .click();
    await page.waitForTimeout(200);
    if (
      !(await page
        .locator("dialog.ui-notification-overlay")
        .evaluate((e) => e.open))
    )
      throw Error("modal not open");
    await freeze(page, "notifications-390");
    await page.keyboard.press("Escape");
    if (
      await page
        .locator("dialog.ui-notification-overlay")
        .evaluate((e) => e.open)
    )
      throw Error("modal not closed");
    await context.close();
  }
  {
    const { context, page } = await setup(390, false, false);
    await page.goto("http://catalog.test/list");
    await page.waitForTimeout(350);
    const margin = await page
      .locator(".shell-content")
      .evaluate((e) => getComputedStyle(e).marginBottom);
    if (margin !== "0px") throw Error("hidden dock leaves margin " + margin);
    checks.push({ width: 390, state: "no-dock", margin });
    await context.close();
  }
  await browser.close();
  fs.writeFileSync(
    path.join(out, "metrics.json"),
    JSON.stringify({ checks, errors, blocked }, null, 2),
  );
  if (errors.length || blocked.length)
    throw Error(JSON.stringify({ errors, blocked }));
  console.log(
    "UI scenarios:",
    checks.length,
    "page errors:",
    errors.length,
    "unhandled requests:",
    blocked.length,
    "snapshots:18",
  );
})()
  .then(() => process.exit(0))
  .catch((e) => {
    console.error(e);
    process.exit(1);
  });
