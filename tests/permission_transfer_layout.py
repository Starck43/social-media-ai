"""Offline actual-template tests for the permission transfer control (no app/DB)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SELECT = "select[data-permission-transfer]"
AVAILABLE = "#permissions-available-list"
CHOSEN = "#permissions-chosen-list"


def values(page):
    return page.locator(SELECT).evaluate("s => Array.from(new FormData(s.form).getAll(s.name))")


def offline(route):
    if route.request.url.startswith("file:"):
        route.continue_()
    else:
        route.abort()


def run():
    with tempfile.TemporaryDirectory(prefix="permission-transfer-") as directory:
        subprocess.run([sys.executable, str(ROOT / "tests/permission_transfer_fixture.py"), directory], check=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("CHROMIUM_BIN") or shutil.which("chromium"),
                headless=True,
                args=["--no-sandbox"],
            )
            cases = 0
            heights = {}
            try:
                for width in [390, 768, 1440]:
                    for theme in ["light", "dark"]:
                        for state in ["initial", "full", "small"]:
                            page = browser.new_page(viewport={"width": width, "height": 1000})
                            page.route("**/*", offline)
                            page.add_init_script(f"localStorage.setItem('theme', '{theme}')")
                            errors = []
                            page.on("pageerror", lambda error: errors.append(str(error)))
                            page.goto(Path(directory, state + ".html").as_uri())
                            page.wait_for_selector("[data-permission-transfer-ui]")
                            assert not errors, errors
                            assert page.locator(SELECT).is_hidden()
                            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (
                                width,
                                state,
                            )
                            assert len(values(page)) == {"initial": 10, "full": 30, "small": 0}[state]
                            assert page.locator('[role="option"]').count() == (3 if state == "small" else 30)
                            for selector in [AVAILABLE, CHOSEN]:
                                dimensions = page.locator(selector).evaluate("""n => ({h:n.clientHeight,
                                    scroll:n.scrollHeight, overflow:getComputedStyle(n).overflowY,
                                    contain:getComputedStyle(n).overscrollBehaviorY})""")
                                assert 100 <= dimensions["h"] <= 450, dimensions
                                heights[(width, theme, state)] = dimensions["h"]
                                assert dimensions["overflow"] == "auto" and dimensions["contain"] == "contain"
                            assert page.locator(".permission-transfer-panel").evaluate_all(
                                "nodes => nodes.every(n => !['auto','scroll'].includes(getComputedStyle(n).overflowY))"
                            )
                            if state == "initial":
                                assert page.locator(AVAILABLE).evaluate("n => n.scrollHeight > n.clientHeight")
                            if width in [390, 1440] and theme == "dark" and state == "initial":
                                page.screenshot(path=f"/data/permission-transfer-{width}.png", full_page=True)
                            page.close()
                            cases += 1

                assert heights[(1440, "dark", "small")] < heights[(1440, "dark", "initial")]
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.route("**/*", offline)
                page.goto(Path(directory, "initial.html").as_uri())
                page.wait_for_selector("[data-permission-transfer-ui]")
                initial = values(page)
                # Real doubleclick includes two click events; option nodes must survive them.
                page.locator(AVAILABLE + ' [data-value="1"]').dblclick()
                assert set(values(page)) == set(initial + ["1"])
                page.locator(CHOSEN + ' [data-value="1"]').dblclick()
                assert values(page) == initial
                page.locator(AVAILABLE + ' [data-value="2"]').click()
                page.locator('[data-transfer-from="available"]').click()
                assert "2" in values(page)
                # Filtering never removes selected values from native form submission.
                page.locator("#permissions-chosen-search").fill("permission_02")
                assert "2" in values(page) and len(values(page)) == 11
                assert page.locator(CHOSEN + ' [role="option"]').count() == 1
                page.locator("#permissions-chosen-search").fill("")
                page.locator(CHOSEN + ' [data-value="2"]').click()
                page.locator('[data-transfer-from="chosen"]').click()
                assert values(page) == initial
                available = page.locator(AVAILABLE)
                available.focus()
                available.press("Home")
                available.press(" ")
                assert page.locator(AVAILABLE + ' [data-value="1"]').get_attribute("aria-selected") == "true"
                available.press("Escape")
                assert page.locator(AVAILABLE + ' [aria-selected="true"]').count() == 0
                available.press("ArrowDown")
                available.press("Enter")
                assert "2" in values(page)
                # Keyboard navigation scrolls the list, never its page or parent.
                available.scroll_into_view_if_needed()
                available.focus()
                before = page.evaluate("scrollY")
                available.press("End")
                assert available.evaluate("n => n.scrollTop > 0")
                assert abs(page.evaluate("scrollY") - before) < 1
                active = available.get_attribute("aria-activedescendant")
                assert page.locator("#" + active).count() == 1
                available.hover()
                page.mouse.wheel(0, 1600)
                page.wait_for_timeout(150)
                assert abs(page.evaluate("scrollY") - before) < 1
                # Select all visible, transfer, then remove all; no duplicate submitted values.
                available.press("Control+a")
                available.press("Enter")
                assert len(values(page)) == len(set(values(page))) == 30
                chosen = page.locator(CHOSEN)
                chosen.focus()
                chosen.press("Control+a")
                chosen.press("Enter")
                assert values(page) == []
                page.locator(SELECT).evaluate("s => s.form.reset()")
                page.wait_for_timeout(30)
                assert values(page) == initial
                assert page.locator(CHOSEN + ' [role="option"]').count() == 10
                page.close()

                touch = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
                page = touch.new_page()
                page.route("**/*", offline)
                page.goto(Path(directory, "initial.html").as_uri())
                page.locator(AVAILABLE + ' [data-value="3"]').tap()
                add = page.locator('[data-transfer-from="available"]')
                assert add.bounding_box()["height"] >= 44
                add.tap()
                assert "3" in values(page)
                page.locator(CHOSEN + ' [data-value="3"]').tap()
                page.locator('[data-transfer-from="chosen"]').tap()
                assert "3" not in values(page)
                touch.close()

                page = browser.new_page(java_script_enabled=False, viewport={"width": 390, "height": 844})
                page.route("**/*", offline)
                page.goto(Path(directory, "initial.html").as_uri())
                assert page.locator(SELECT).is_visible()
                assert page.locator(SELECT + " option:checked").count() == 10
                assert page.locator(SELECT).bounding_box()["height"] <= 380
                page.close()
                print(
                    json.dumps(
                        {
                            "layout_cases_passed": cases,
                            "interaction_suites_passed": 3,
                            "mock_permissions": 30,
                            "browser": browser.version,
                        }
                    )
                )
            finally:
                browser.close()


if __name__ == "__main__":
    run()
