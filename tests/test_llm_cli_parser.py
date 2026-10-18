"""Real pinned Typer/Rich parser with isolated source and DB/provider doubles.

Run directly: python tests/test_llm_cli_parser.py -v. No cli.main/app imports.
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

    def run_cli(self, *args):
        return self.runner.invoke(self.cli.app, list(args), catch_exceptions=True)

    def test_help_registers_all_existing_commands(self):
        result = self.run_cli("--help")
        self.assertEqual(result.exit_code, 0, result.output)
        for name in ("provider-list", "model-list", "model-add", "model-update", "model-delete", "model-test"):
            self.assertIn(name, result.output)
        self.fixture.models.select_related.assert_not_called()

    def test_model_test_help_explains_mock_db_read(self):
        result = self.run_cli("model-test", "--help")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("DB read", result.output)
        self.assertIn("--mock", result.output)

    def test_mock_outputs_valid_json_with_literal_markup_and_zero_spend(self):
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

    def test_real_default_exit_one_without_db_read(self):
        result = self.run_cli("model-test", "7")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("authenticated payer", result.output)
        self.fixture.models.select_related.assert_not_called()

    def test_parser_rejects_noninteger_model_id(self):
        result = self.run_cli("model-test", "invalid", "--mock")
        self.assertEqual(result.exit_code, 2, result.output)
        self.fixture.models.select_related.assert_not_called()

    def test_semantic_id_and_prompt_failures_exit_one(self):
        for args in (("0", "--mock"), ("2147483648", "--mock"), ("7", "--mock", "--prompt", " ")):
            with self.subTest(args=args):
                result = self.run_cli("model-test", *args)
                self.assertEqual(result.exit_code, 1, result.output)
        self.fixture.models.select_related.assert_not_called()

    def test_probe_failure_is_nonzero_and_redacted(self):
        self.fixture.probe.probe_mock.side_effect = RuntimeError("PRIVATE_SENTINEL")
        result = self.run_cli("model-test", "7", "--mock")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertNotIn("PRIVATE_SENTINEL", result.output)
        self.fixture.probe.probe_mock.assert_called_once()

    def test_provider_list_nonempty_uses_current_schema(self):
        self.fixture.providers.order_by.return_value = [
            NS(id=1, name="fixture", api_format="openai", base_url="https://fixture.invalid", is_active=True),
        ]
        result = self.run_cli("provider-list")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("fixture", result.output)
        self.assertNotIn("default", result.output)


if __name__ == "__main__":
    unittest.main()
