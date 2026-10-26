"""Offline real-template browser checks; synthetic source choices, no app/DB runtime.

Run with PYTHONPATH=tests:tests/admin:. and FIXTURE_PYTHON pointing to a pyproject
venv if the browser runner uses another Python. All network requests are blocked.
"""

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


async def render(directory):
    from importlib.metadata import distribution

    import httpx
    from shared.filtered_select_fixture import task_console

    package = distribution("sqladmin").locate_file("sqladmin")
    for state, count in [("sources", 60), ("empty", 0), ("large", 206)]:
        ctx = task_console(count=count)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=ctx.app), base_url="https://fixture.invalid"
        ) as client:
            response = await client.get("/admin/agent-task/edit/2027")
        assert response.status_code == 200, response.text
        html = response.text.replace('"/static/', '"' + (ROOT / "app/static").as_uri() + "/")
        html = html.replace("https://fixture.invalid/static/", (ROOT / "app/static").as_uri() + "/")
        html = html.replace("https://fixture.invalid/admin/statics/", (package / "statics").as_uri() + "/")
        html = re.sub(r'<script\b[^>]*src="https?://[^"]+"[^>]*>\s*</script>', "", html)
        (directory / f"{state}.html").write_text(html)


def run():
    from playwright.sync_api import sync_playwright

    def offline(route):
        route.continue_() if route.request.url.startswith("file:") else route.abort()

    def values(page, field="sources"):
        return page.locator("#" + field).evaluate("s => new FormData(s.form).getAll(s.name)")

    with tempfile.TemporaryDirectory(prefix="p8-t36-select-") as directory:
        subprocess.run([os.environ.get("FIXTURE_PYTHON", sys.executable), __file__, "--render", directory], check=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=shutil.which("chromium"), headless=True, args=["--no-sandbox"]
            )
            try:
                cases = 0
                for width in [390, 768, 1440]:
                    for theme in ["light", "dark"]:
                        for state, count in [("sources", 60), ("empty", 0), ("large", 206)]:
                            page = browser.new_page(viewport={"width": width, "height": 844})
                            errors = []
                            page.on("pageerror", lambda error: errors.append(str(error)))
                            page.route("**/*", offline)
                            page.add_init_script(f"localStorage.setItem('theme', '{theme}')")
                            page.goto(Path(directory, state + ".html").as_uri())
                            page.wait_for_selector("[data-permission-transfer-ui]")
                            assert not errors, errors
                            assert page.locator("#sources").is_hidden()
                            assert page.locator(".admin-related-actions button").count() == 2
                            assert page.locator(".admin-related-input > .permission-transfer").count() == 1
                            assert page.locator(".permission-transfer-filter").count() == 0
                            assert page.locator('[role="option"]').count() == count
                            assert values(page) == ([str(count)] if count else [])
                            # The stock edit footer already overflows on mobile; assert the
                            # component's own bounds, without hiding that unrelated issue.
                            bounds = page.locator(".permission-transfer").bounding_box()
                            assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width + 1, bounds
                            for side in ["available", "chosen"]:
                                box = page.locator(f"#sources-{side}-list").bounding_box()
                                assert 80 <= box["height"] <= 416, box
                            if width == 390 and theme == "dark" and state == "sources":
                                page.locator(".permission-transfer").screenshot(path="/data/p8-t36-sources-mobile.png")
                            if width == 1440 and theme == "light" and state == "sources":
                                page.locator(".permission-transfer").screenshot(path="/data/p8-t36-sources-desktop.png")
                            page.close()
                            cases += 1

                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.route("**/*", offline)
                page.goto(Path(directory, "sources.html").as_uri())
                page.wait_for_selector("#sources-available-list")
                assert values(page) == ["60"]
                # Plain labels retain punctuation, unlike permission code grouping.
                assert "VK.news /" in page.locator('#sources-available-list [data-value="1"]').inner_text()
                page.locator('#sources-available-list [data-value="1"]').dblclick()
                assert set(values(page)) == {"1", "60"}
                page.locator("#sources-chosen-search").fill("источник 060")
                assert page.locator('#sources-chosen-list [role="option"]').count() == 1
                assert set(values(page)) == {"1", "60"}  # search is never selection
                page.locator("#sources-chosen-search").fill("")
                page.locator('#sources-chosen-list [data-value="1"]').dblclick()
                assert values(page) == ["60"]
                available = page.locator("#sources-available-list")
                available.focus()
                available.press("Home")
                available.press(" ")
                page.locator('[data-transfer-from="available"]').click()
                assert set(values(page)) == {"1", "60"}
                page.locator("#sources").evaluate("s => s.form.reset()")
                page.wait_for_timeout(30)
                assert values(page) == ["60"]
                # Popup-style option addition/rename and external change synchronize.
                page.locator("#sources").evaluate("""s => {
                    s.add(new Option('<img src=x onerror=alert(1)> — safe', 'new', false, true));
                    s.dispatchEvent(new Event('change', {bubbles: true}));
                }""")
                page.wait_for_selector('#sources-chosen-list [data-value="new"]')
                assert page.locator(".permission-transfer img").count() == 0
                assert set(values(page)) == {"60", "new"}
                page.locator("#sources").evaluate("""s => {
                    const option = new Option('Locked', 'locked'); option.disabled = true; s.add(option);
                }""")
                locked = page.locator('#sources-available-list [data-value="locked"]')
                locked.wait_for()
                assert locked.get_attribute("aria-disabled") == "true"
                locked.dblclick(force=True)
                assert "locked" not in values(page)
                available.focus()
                available.press("Control+a")
                available.press("Enter")
                assert "locked" not in values(page) and len(values(page)) == 61
                # Multiple instances, duplicate init, and independent native names.
                page.evaluate("""() => {
                    const original = document.querySelector('#sources');
                    const clone = original.cloneNode(true);
                    clone.id = 'backup'; clone.name = 'backup'; clone.hidden = false;
                    delete clone.dataset.transferReady;
                    for (const option of clone.options) option.selected = false;
                    original.form.append(clone);
                    FilteredSelect.init(); FilteredSelect.init();
                }""")
                assert page.locator("[data-permission-transfer-ui]").count() == 2
                page.locator('#backup-available-list [data-value="2"]').dblclick()
                assert values(page, "backup") == ["2"] and "2" in values(page)
                page.locator('#backup-chosen-list [data-value="2"]').dblclick()
                assert values(page, "backup") == [] and "2" in values(page)
                page.locator("#backup").evaluate("s => {s.disabled = true}")
                page.wait_for_function("document.querySelector('#backup').hidden === false")
                assert page.locator("#backup").is_disabled()
                page.locator("#backup").evaluate("s => {s.disabled = false}")
                page.wait_for_function("document.querySelector('#backup').hidden === true")
                assert page.locator("[data-permission-transfer-ui]").count() == 2
                # The other initialization order must preserve the enhanced UI too.
                page.evaluate("""() => {
                    const config = document.querySelector('#admin-related-controls');
                    const controls = JSON.parse(config.textContent);
                    controls[0].field = 'backup';
                    config.textContent = JSON.stringify(controls);
                    const locked = document.querySelector('#backup').cloneNode(true);
                    locked.id = 'locked-select'; locked.name = 'locked-select';
                    locked.hidden = false; locked.disabled = true;
                    delete locked.dataset.transferReady;
                    document.querySelector('#sources').form.append(locked);
                    FilteredSelect.init();
                }""")
                page.add_script_tag(path=str(ROOT / "app/static/js/admin-related.js"))
                assert page.locator(".admin-related-actions button").count() == 4
                assert page.locator(".admin-related-input > [data-filtered-select-ui]").count() == 2
                assert page.locator("#locked-select").is_visible() and page.locator("#locked-select").is_disabled()
                assert page.locator("[data-permission-transfer-ui]").count() == 2
                page.close()

                page = browser.new_page(java_script_enabled=False, viewport={"width": 390, "height": 844})
                page.route("**/*", offline)
                page.goto(Path(directory, "sources.html").as_uri())
                assert page.locator("#sources").is_visible()
                page.select_option("#sources", ["1", "2"])
                assert values(page) == ["1", "2"]
                page.close()
                print(
                    json.dumps(
                        {
                            "layout_cases_passed": cases,
                            "interaction_suites_passed": 2,
                            "mock_sources": 206,
                            "browser": browser.version,
                        }
                    )
                )
            finally:
                browser.close()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--render":
        asyncio.run(render(Path(sys.argv[2])))
    else:
        run()
