"""Prepared loader/fixture isolation regressions; no app, DB or live calls.

Run: python tests/test_source_import_isolation.py.
Pytest still loads the repository's normal isolated-schema conftest.
"""

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from source_import_isolation import load_isolated_source


def _load_dep(name, path):
    # Cross-group helper module loader (absolute path; bare sibling imports do not
    # resolve across domain dirs).
    import pathlib

    filepath = pathlib.Path(__file__).resolve().parents[2] / path
    spec = importlib.util.spec_from_file_location(name, filepath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    # Same insertion pytest itself does for collected test modules so the loaded
    # helper's own bare sibling imports keep resolving.
    if str(filepath.parent) not in sys.path:
        sys.path.insert(0, str(filepath.parent))
    spec.loader.exec_module(module)
    return module


class SourceImportIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "source.py"

    def source(self, text, *, name="_isolated_loader_probe", imports=None):
        self.path.write_text(text, encoding="utf-8")
        return load_isolated_source(name, self.path, imports)

    @staticmethod
    def dependency(value):
        module = types.ModuleType("loader_dependency")
        module.value = value
        return module

    def test_declared_dependency_is_local_and_global_binding_is_unchanged(self):
        actual = self.dependency("actual")
        fake = self.dependency("fake")
        with patch.dict(sys.modules, {"_loader_dependency": actual}):
            module = self.source("from _loader_dependency import value", imports={"_loader_dependency": fake})
            self.assertEqual(module.value, "fake")
            self.assertIs(sys.modules["_loader_dependency"], actual)

    def test_late_import_uses_private_doubles_without_poisoning_following_import(self):
        actual = self.dependency("actual")
        fake = self.dependency("fake")
        with patch.dict(sys.modules, {"_loader_dependency": actual}):
            module = self.source(
                "def read():\n    from _loader_dependency import value\n    return value\n",
                imports={"_loader_dependency": fake},
            )
            self.assertEqual(module.read(), "fake")
            from _loader_dependency import value

            self.assertEqual(value, "actual")

    def test_relative_dependency_is_resolved_against_private_source_package(self):
        module = self.source(
            "from .dependency import value",
            name="_loader_package.probe",
            imports={"_loader_package.dependency": self.dependency(17)},
        )
        self.assertEqual(module.value, 17)

    def test_existing_private_alias_is_restored_after_execution(self):
        previous = types.ModuleType("previous_private_alias")
        with patch.dict(sys.modules, {"_isolated_loader_probe": previous}):
            module = self.source("value = 9")
            self.assertEqual(module.value, 9)
            self.assertIs(sys.modules["_isolated_loader_probe"], previous)

    def test_failed_source_removes_its_temporary_alias(self):
        with patch.dict(sys.modules):
            sys.modules.pop("_isolated_loader_probe", None)
            with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                self.source("raise RuntimeError('fixture failure')")
            self.assertNotIn("_isolated_loader_probe", sys.modules)

    def test_dataclass_is_created_while_alias_is_registered(self):
        module = self.source(
            "from dataclasses import dataclass\n@dataclass\nclass Record:\n    value: int\n",
        )
        self.assertEqual(module.Record(7).value, 7)

    def test_undeclared_standard_library_import_keeps_normal_semantics(self):
        module = self.source("from pathlib import PurePosixPath\nvalue = str(PurePosixPath('a') / 'b')")
        self.assertEqual(module.value, "a/b")

    def test_dispatcher_fixture_does_not_replace_live_infrastructure_modules(self):

        fixtures = _load_dep("_dep_returned_job_failures", "tests/jobs/test_returned_job_failures.py")
        names = (
            "app.core.tenant_context",
            "app.jobs.handlers",
            "app.models.managers.job_manager",
            "app.services.digest.job_delivery",
        )
        sentinels = {name: self.dependency(name) for name in names}
        with patch.dict(sys.modules, sentinels):
            fixture = fixtures.DispatcherTests("test_success_remains_success")
            try:
                fixture.setUp()
                for name, sentinel in sentinels.items():
                    self.assertIs(sys.modules[name], sentinel)
                self.assertIs(fixture.dispatcher.jobs, fixture.jobs)
            finally:
                fixture.doCleanups()
            for name, sentinel in sentinels.items():
                self.assertIs(sys.modules[name], sentinel)


if __name__ == "__main__":
    unittest.main()
