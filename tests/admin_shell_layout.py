"""Offline Chromium layout checks. Run with the isolated environment Python."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def run():
    with tempfile.TemporaryDirectory(prefix="admin-shell-") as directory:
        subprocess.run([sys.executable, str(ROOT / "tests/admin_shell_fixture.py"), directory], check=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=os.environ.get("CHROMIUM_BIN") or shutil.which("chromium"),
                headless=True,
                args=["--no-sandbox"],
            )
            cases = 0
            try:
                for width in [390, 768, 1440]:
                    for theme in ["light", "dark"]:
                        for state in ["initial", "result", "long"]:
                            page = browser.new_page(viewport={"width": width, "height": 1000})
                            page.route(
                                "**/*",
                                lambda route: (
                                    route.continue_() if route.request.url.startswith("file:") else route.abort()
                                ),
                            )
                            page.add_init_script("localStorage.setItem('theme', '" + theme + "')")
                            errors = []
                            page.on("pageerror", lambda error: errors.append(str(error)))
                            page.goto(Path(directory, state + ".html").as_uri())
                            page.wait_for_timeout(80)
                            assert not errors, errors
                            assert page.locator("[data-admin-username]").count() == 1
                            assert page.locator("[data-admin-role]").inner_text() == "Администратор платформы"
                            assert page.locator(".admin-menu .nav-link").count() == 16
                            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (
                                width,
                                theme,
                                state,
                                "overflow",
                            )
                            links = page.locator(".admin-menu .nav-link")
                            assert all(
                                30 <= box["height"] <= 65 for box in [item.bounding_box() for item in links.all()]
                            ), "stretched nav items"
                            if width >= 768:
                                sidebar = page.locator(".admin-sidebar").bounding_box()
                                content = page.locator(".page-wrapper").bounding_box()
                                assert 240 <= sidebar["width"] <= 280
                                assert content["x"] >= sidebar["width"]
                                assert page.locator(".admin-sidebar-footer").bounding_box()["y"] < 1000
                            else:
                                page.locator(".admin-navigation > summary").click()
                                assert not page.locator(".admin-menu").is_visible()
                                page.locator(".admin-navigation > summary").click()
                                assert page.locator(".admin-menu").is_visible()
                            if width == 1440 and state == "initial":
                                page.screenshot(path=f"/data/admin-shell-{theme}.png", full_page=True)
                            page.select_option("#admin-theme", "dark" if theme == "light" else "light")
                            assert page.locator("html").get_attribute("class") == ("dark" if theme == "light" else "")
                            page.close()
                            cases += 1
                # Native navigation remains usable without JavaScript.
                page = browser.new_page(viewport={"width": 390, "height": 844}, java_script_enabled=False)
                page.route(
                    "**/*", lambda route: route.continue_() if route.request.url.startswith("file:") else route.abort()
                )
                page.goto(Path(directory, "initial.html").as_uri())
                assert page.locator(".admin-menu").is_visible()
                page.locator("summary").first.click()
                assert not page.locator(".admin-menu").is_visible()
                page.close()
                print(json.dumps({"layout_cases_passed": cases, "no_js_cases_passed": 1, "browser": browser.version}))
            finally:
                browser.close()


if __name__ == "__main__":
    run()
