"""Real pinned Typer/Rich parser against the personal-catalog CLI handlers.

Run directly: python tests/ai/test_llm_cli_parser.py -v. No cli.main/app imports.
The parser is the installed Typer/Rich pair; the catalog context and storage
doubles are shared with the contracts suite. Every successful command needs an
authenticated active actor — there is no impersonation flag or catalog fallback.
"""

import json
import unittest
from types import SimpleNamespace as NS

import rich
import test_llm_cli_contracts as contracts
import typer
from rich.console import Console
from rich.table import Table
from source_import_isolation import load_isolated_source
from typer.testing import CliRunner


class LLMCLIParser(unittest.TestCase):
    def setUp(self):
        self.fixture = contracts.LLMCLIContracts()
        self.fixture.setUp()
        imports = dict(self.fixture.cli.__isolated_imports__)
        imports.update({"rich": rich, "rich.console": NS(Console=Console), "rich.table": NS(Table=Table)})
        self.cli = load_isolated_source(
            "_real_parser_llm_cli",
            contracts.ROOT / "cli/commands/llm.py",
            imports,
            module_imports={"typer": typer},
        )
        self.runner = CliRunner()

    def run_cli(self, *args, **kwargs):
        return self.runner.invoke(self.cli.app, list(args), catch_exceptions=True, **kwargs)

    def assert_backend_untouched(self):
        self.assertEqual(self.fixture.catalog.metadata_calls, [])
        self.assertEqual(self.fixture.store.select_related_calls, [])
        self.assertEqual(self.fixture.store.get_calls, [])
        self.assertEqual(self.fixture.store.filter_calls, [])
        self.assertEqual(self.fixture.scopes, [])
        self.fixture.probe.probe_mock.assert_not_called()
        self.fixture.model.provider.get_api_key.assert_not_called()

    def test_help_registers_all_existing_commands(self):
        result = self.run_cli("--help")
        self.assertEqual(result.exit_code, 0, result.output)
        for name in ("provider-list", "model-list", "model-add", "model-update", "model-delete", "model-test"):
            self.assertIn(name, result.output)
        self.assertNotIn("--user-id", result.output)
        self.assertNotIn("--owner", result.output)
        self.assert_backend_untouched()

    def test_model_test_help_explains_mock_db_read(self):
        result = self.run_cli("model-test", "--help")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("DB read", result.output)
        self.assertIn("--mock", result.output)
        self.assertNotIn("--user-id", result.output)
        self.assertNotIn("--owner", result.output)
        self.assert_backend_untouched()

    def test_mock_outputs_valid_json_with_literal_markup_and_zero_spend(self):
        self.fixture.model.name = "Model [link=https://fixture.invalid]7[/link]"
        result = self.run_cli("model-test", "7", "--mock", "--prompt", "fixture prompt")
        self.assertEqual(result.exit_code, 0, result.output)
        report = json.loads(result.output)
        self.assertEqual(report["mode"], "mock")
        self.assertEqual(report["tokens_spent"], 0)
        self.assertIn("[link=", report["content"])
        self.assertNotIn("\x1b", result.output)
        self.assertNotIn("fixture prompt", result.output)
        self.fixture.probe.probe_mock.assert_called_once_with(self.fixture.model, "fixture prompt")
        self.fixture.model.provider.get_api_key.assert_not_called()
        self.assertEqual(self.fixture.store.get_calls, [{"id": 7, "owner_user_id": 7}])

    def test_real_default_exit_one_without_db_read(self):
        result = self.run_cli("model-test", "7")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("authenticated payer", result.output)
        self.assert_backend_untouched()

    def test_parser_rejects_noninteger_model_id(self):
        result = self.run_cli("model-test", "invalid", "--mock")
        self.assertEqual(result.exit_code, 2, result.output)
        self.assert_backend_untouched()

    def test_semantic_id_and_prompt_failures_exit_one(self):
        for args in (("0", "--mock"), ("2147483648", "--mock"), ("7", "--mock", "--prompt", " ")):
            with self.subTest(args=args):
                result = self.run_cli("model-test", *args)
                self.assertEqual(result.exit_code, 1, result.output)
        self.assert_backend_untouched()

    def test_probe_failure_is_nonzero_and_redacted(self):
        self.fixture.probe.probe_mock.side_effect = RuntimeError("PRIVATE_SENTINEL")
        result = self.run_cli("model-test", "7", "--mock")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertNotIn("PRIVATE_SENTINEL", result.output)
        self.fixture.probe.probe_mock.assert_called_once()

    def test_provider_list_nonempty_uses_owner_catalog_and_no_default_column(self):
        self.fixture.catalog.providers = [
            contracts.provider_row(1, name="fixture"),
            contracts.provider_row(2, owner=9, name="foreign"),
        ]
        self.fixture.catalog.model_rows = [contracts.model_row(10, provider_id=1)]
        result = self.run_cli("provider-list")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("fixture", result.output)
        self.assertNotIn("foreign", result.output)
        self.assertNotIn("default", result.output)
        self.assertEqual(self.fixture.catalog.metadata_calls, [7])
        self.fixture.model.provider.get_api_key.assert_not_called()

    def test_provider_list_without_actor_is_denied_before_backend(self):
        self.fixture.catalog.actor = None
        result = self.run_cli("provider-list")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("authenticated active user", result.output)
        self.assert_backend_untouched()

    def test_model_list_without_actor_is_denied_before_backend(self):
        self.fixture.catalog.actor = None
        result = self.run_cli("model-list")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("authenticated active user", result.output)
        self.assert_backend_untouched()

    def test_model_test_mock_without_actor_is_denied_before_backend(self):
        self.fixture.catalog.actor = None
        result = self.run_cli("model-test", "7", "--mock")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("authenticated active user", result.output)
        self.assert_backend_untouched()

    def test_model_list_is_owner_scoped_through_the_parser(self):
        self.fixture.store.rows = [
            contracts.model_row(7, provider_id=1, name="own text"),
            contracts.model_row(8, owner=9, provider_id=1, name="foreign text"),
        ]
        result = self.run_cli("model-list", "--provider", "1", "--type", "text", env={"COLUMNS": "200"})
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("own text", result.output)
        self.assertNotIn("foreign text", result.output)
        self.assertEqual(
            self.fixture.store.filter_calls,
            [{"owner_user_id": 7, "provider_id": 1, "model_type": "text"}],
        )

    def test_internal_worker_scope_without_actor_is_denied(self):
        self.fixture.catalog.bound_owner = 7
        self.fixture.catalog.actor = None
        result = self.run_cli("model-test", "7", "--mock")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("authenticated active user", result.output)
        self.assert_backend_untouched()


if __name__ == "__main__":
    unittest.main()
