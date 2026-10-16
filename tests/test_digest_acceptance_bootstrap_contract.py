"""No-DB checks of the acceptance modules' fail-closed bootstrap contract.

Only selected guard AST statements are executed with synthetic metadata. Never
imports the integration modules, app, conftest, settings, engines or DB drivers.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PG = ROOT / "tests/test_digest_send_postgres_acceptance.py"
ASGI = ROOT / "tests/test_digest_send_asgi_acceptance.py"


def guard_nodes():
    selected = []
    for node in ast.parse(PG.read_text()).body:
        if isinstance(node, ast.ImportFrom) and node.module == "sqlalchemy":
            break
        if isinstance(node, (ast.Assign, ast.If)):
            selected.append(node)
    return ast.Module(body=selected, type_ignores=[])


class DigestAcceptanceBootstrapContractTests(unittest.TestCase):
    def bootstrap(self, *, present=True, schema="test_schema", url="postgresql://localhost:5432/social_manager"):
        conftest = SimpleNamespace(__file__=str(PG.with_name("conftest.py")), TEST_SCHEMA=schema, TEST_DATABASE_URL=url)
        namespace = {
            "__file__": str(PG),
            "Path": Path,
            "urlsplit": urlsplit,
            "sys": SimpleNamespace(modules={"arbitrary.pytest.import.name": conftest} if present else {}),
        }
        exec(compile(guard_nodes(), "acceptance-guard-only", "exec"), namespace)
        return namespace

    def test_missing_root_conftest_stops_before_app_imports(self):
        with self.assertRaisesRegex(RuntimeError, "root conftest"):
            self.bootstrap(present=False)

    def test_wrong_schema_stops(self):
        for schema in (None, "public", "test_schema_other"):
            with self.subTest(schema=schema), self.assertRaisesRegex(RuntimeError, "root conftest"):
                self.bootstrap(schema=schema)

    def test_wrong_endpoint_stops(self):
        for url in (
            "postgresql://remote.invalid:5432/social_manager",
            "postgresql://localhost:6543/social_manager",
            "postgresql://localhost:5432/business",
            "",
        ):
            with self.subTest(url=url), self.assertRaisesRegex(RuntimeError, "pre-approved local"):
                self.bootstrap(url=url)

    def test_approved_endpoints_need_no_actual_connection(self):
        for host in ("localhost", "127.0.0.1"):
            with self.subTest(host=host):
                scope = self.bootstrap(url=f"postgresql://{host}:5432/social_manager")
                self.assertEqual(scope["_target"].hostname, host)

    def test_guard_is_before_all_application_imports(self):
        nodes = ast.parse(PG.read_text()).body
        first_app = next(
            i for i, n in enumerate(nodes) if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("app")
        )
        guarded = [i for i, n in enumerate(nodes) if isinstance(n, ast.If) and i < first_app]
        self.assertEqual(len(guarded), 2)
        self.assertFalse(any(isinstance(n, (ast.Import, ast.ImportFrom)) for n in guard_nodes().body))

    def test_prior_working_engine_or_model_cannot_pass_marker_only(self):
        nodes = ast.parse(PG.read_text()).body
        # The two application-target checks, after imports but before pytestmark.
        checks = []
        for node in nodes:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
            ):
                break
            if isinstance(node, ast.If) and any(
                isinstance(n, ast.Name) and n.id in ("settings", "async_engine") for n in ast.walk(node)
            ):
                checks.append(node)
        self.assertEqual(len(checks), 2)
        code = compile(ast.Module(body=checks, type_ignores=[]), "application-target-guard-only", "exec")
        for setting, model, host, database, rejected in (
            ("test_schema", "test_schema", "localhost", "social_manager", False),
            ("public", "test_schema", "localhost", "social_manager", True),
            ("test_schema", "public", "localhost", "social_manager", True),
            ("test_schema", "test_schema", "remote.invalid", "social_manager", True),
            ("test_schema", "test_schema", "localhost", "business", True),
        ):
            with self.subTest(setting=setting, model=model, host=host, database=database):
                scope = {
                    "settings": SimpleNamespace(DB_SCHEMA=setting),
                    "AgentSession": SimpleNamespace(__table__=SimpleNamespace(schema=model)),
                    "async_engine": SimpleNamespace(url=SimpleNamespace(host=host, port=5432, database=database)),
                }
                if rejected:
                    with self.assertRaisesRegex(RuntimeError, "STOP:"):
                        exec(code, scope)
                else:
                    exec(code, scope)

    def test_asgi_uses_guarded_sibling_before_app_import(self):
        imports = [n.module for n in ast.parse(ASGI.read_text()).body if isinstance(n, ast.ImportFrom)]
        self.assertLess(imports.index("test_digest_send_postgres_acceptance"), imports.index("app.main"))

    def test_runner_contract_distinguishes_integration_from_no_db(self):
        for name in ("docs/RULES.md", ".agents/skills/test-runner/SKILL.md", ".codex/agents/test-runner.toml"):
            with self.subTest(path=name):
                content = (ROOT / name).read_text()
                self.assertIn("real db/asgi", content.lower())
                self.assertIn("DB_TEST_SCHEMA=test_schema", content)
                self.assertIn("--noconftest", content)
                self.assertIn("read-only", content.lower())


if __name__ == "__main__":
    unittest.main()
