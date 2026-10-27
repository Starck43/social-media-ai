"""Standalone core-profile template contracts; no root pytest/DB fixtures.

Run: python -I tests/tooling/test_p11_t16_fixture_rendering.py
"""

import importlib.abc
import runpy
import sys
import tempfile
import unittest
from pathlib import Path

__test__ = False  # This script is deliberately not a project-pytest suite.
ROOT = Path(__file__).resolve().parents[2]


class NoApplicationImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "app" or fullname.startswith("app."):
            raise AssertionError(f"Fixture renderer imported application code: {fullname}")
        return None


class FixtureNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.meta_path.insert(0, NoApplicationImports())
        cls.layout = runpy.run_path(str(ROOT / "tests/shared/settings_layout_fixtures.py"))
        cls.llm = runpy.run_path(str(ROOT / "tests/shared/settings_llm_fixtures.py"))

    def test_all_settings_and_invite_states_render_with_strict_context(self):
        with tempfile.TemporaryDirectory() as folder:
            self.layout["render"](Path(folder))
            files = {p.name for p in Path(folder).glob("*.html")}
            assert len(files) == len(self.layout["MODES"]) + 3
            for mode in self.layout["MODES"]:
                html = (Path(folder) / f"settings-{mode}.html").read_text()
                self.assertIn('id="page-content"', html)
                self.assertIn('name="_csrf"', html)
            owner = (Path(folder) / "settings-owner-workspace.html").read_text()
            self.assertIn("synthetic-fixture-policy", owner)
            self.assertFalse(any(name.startswith("app.") for name in sys.modules))

    def test_llm_rendering_preserves_catalog_empty_and_no_workspace_modes(self):
        for mode in ("catalog", "empty", "no-workspace"):
            with self.subTest(mode=mode):
                html = self.llm["render"](mode)
                self.assertIn('id="page-content"', html)
                self.assertIn('name="_csrf"', html)
                self.assertEqual(self.llm["catalog_context"](mode)["tab"], "models")
        self.assertIsNone(self.llm["catalog_context"]("no-workspace")["tenant"])
        self.assertEqual(self.llm["catalog_context"]("empty")["llm_catalog"]["providers"], [])


if __name__ == "__main__":
    unittest.main()
