"""Whole-source CLI/shared-mock contracts; stdlib doubles, no app/DB/provider.

Run directly, without pytest/conftest: python tests/test_llm_cli_contracts.py -v.
Typer/Rich doubles test handlers, not their parser or terminal implementation.
"""

import json
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]


class Exit(Exception):
    def __init__(self, code=0):
        self.exit_code = code
        super().__init__(code)


class Table:
    def __init__(self, **kwargs):
        self.columns = []
        self.rows = []

    def add_column(self, name):
        self.columns.append(name)

    def add_row(self, *values):
        self.rows.append(values)


class LLMCLIContracts(unittest.TestCase):
    def setUp(self):
        self.output = Mock()
        self.console = NS(print=Mock())
        self.scopes = []

        @contextmanager
        def scope(**kwargs):
            self.scopes.append(kwargs)
            yield

        self.model = NS(
            name="Fixture [link=https://fixture.invalid]name[/link]\x1b",
            model_id="fixture/model",
            provider=NS(name="Fixture", is_active=False),
            is_active=False,
        )
        self.model.provider.get_api_key = Mock(side_effect=AssertionError("No key access"))
        self.query = NS(get=AsyncMock(return_value=self.model))
        self.models = NS(select_related=Mock(return_value=self.query))
        self.price = Mock(return_value=0.05)
        self.probe = load_isolated_source(
            "_cli_shared_probe",
            ROOT / "app/services/ai/model_probe.py",
            {"app.services.ai.llm_client": NS(price_usage_usd=self.price)},
            module_imports={"httpx": NS()},
        )
        self.probe.probe_mock = Mock(wraps=self.probe.probe_mock)
        self.probe.probe_real_capped = AsyncMock(side_effect=AssertionError("No real probe"))
        self.providers = NS(order_by=AsyncMock(return_value=[]))
        self.models.filter = Mock(return_value=NS(all=AsyncMock(return_value=[])))
        self.cli = load_isolated_source(
            "_llm_cli",
            ROOT / "cli/commands/llm.py",
            {
                "app.core.tenant_context": NS(tenant_scope=scope),
                "app.models": NS(LLMModel=NS(objects=self.models), LLMProvider=NS(id="id", objects=self.providers)),
                "app.services.ai.model_probe": self.probe,
                # Explicit baseline tripwires: never fall through into real app imports.
                "app.admin.actions": NS(),
                "app.services.ai.llm_client": NS(),
                "rich": NS(print=self.output),
                "rich.table": NS(Table=Table),
                "rich.console": NS(Console=lambda: self.console),
            },
            module_imports={
                "typer": NS(
                    Typer=lambda **kw: NS(command=lambda *a, **kw: lambda fn: fn),
                    Option=lambda default, *a, **kw: default,
                    Argument=lambda default, *a, **kw: default,
                    Exit=Exit,
                )
            },
        )

    def invoke(self, model_id=7, prompt="hello", mock=True):
        return self.cli.model_test(model_id=model_id, prompt=prompt, mock=mock)

    def failed(self, **kwargs):
        with self.assertRaises(Exit) as error:
            self.invoke(**kwargs)
        self.assertEqual(error.exception.exit_code, 1)
        self.console.print.assert_not_called()
        self.assertNotIn("PRIVATE_SENTINEL", str(self.output.call_args_list))
        self.probe.probe_real_capped.assert_not_awaited()

    def report(self):
        args, kwargs = self.console.print.call_args
        self.assertEqual(kwargs, {"markup": False, "highlight": False, "soft_wrap": True})
        self.assertNotIn("\x1b", args[0])
        return json.loads(args[0])

    def test_provider_list_without_removed_default_and_model_counts(self):
        self.providers.order_by.return_value = [
            NS(id=1, name="one", api_format="openai", base_url="https://fixture.invalid", is_active=True),
            NS(id=2, name="two", api_format="openai", base_url="https://fixture.invalid", is_active=False),
        ]
        self.models.filter.return_value.all.return_value = [NS(provider_id=1), NS(provider_id=1)]
        self.cli.provider_list()
        table = self.console.print.call_args.args[0]
        self.assertEqual(table.columns, ["id", "name", "api_format", "base_url", "active", "models"])
        self.assertEqual([row[-1] for row in table.rows], ["2", "0"])
        self.assertEqual([row[-2] for row in table.rows], ["yes", "no"])
        self.models.filter.assert_called_once_with(provider_id__in=[1, 2])

    def test_empty_provider_list(self):
        self.cli.provider_list()
        self.assertEqual(self.console.print.call_args.args[0].rows, [])

    def test_mock_uses_actual_shared_probe_once(self):
        self.invoke()
        self.models.select_related.assert_called_once_with("provider")
        self.query.get.assert_awaited_once_with(id=7)
        self.probe.probe_mock.assert_called_once_with(self.model, "hello")
        self.price.assert_called_once_with(self.model, 8, 12)
        self.model.provider.get_api_key.assert_not_called()
        self.probe.probe_real_capped.assert_not_awaited()
        self.assertEqual(self.scopes, [{"bypass": True}])
        result = self.report()
        self.assertTrue(result["ok"])
        self.assertFalse(result["is_real_response"])
        self.assertEqual(result["tokens_spent"], 0)
        self.assertEqual(result["simulated_usage"]["total_tokens"], 20)
        self.assertEqual(result["estimated_cost_usd"], 0.05)
        self.assertNotIn("prompt_echo", result)
        self.assertNotIn("cost_usd", result)

    def test_inactive_model_and_provider_mock_is_not_readiness(self):
        self.invoke()
        self.assertTrue(self.report()["ok"])
        self.model.provider.get_api_key.assert_not_called()

    def test_default_real_path_fails_before_query_or_scope(self):
        self.failed(mock=False)
        self.models.select_related.assert_not_called()
        self.probe.probe_mock.assert_not_called()
        self.assertEqual(self.scopes, [])
        self.assertIn("authenticated payer", str(self.output.call_args_list))

    def test_default_handler_argument_is_fail_closed(self):
        with self.assertRaises(Exit) as error:
            self.cli.model_test(7)
        self.assertEqual(error.exception.exit_code, 1)
        self.models.select_related.assert_not_called()

    def test_invalid_ids_fail_before_query(self):
        for model_id in (0, -1, 2_147_483_648, True, "7", None):
            with self.subTest(model_id=model_id):
                self.failed(model_id=model_id)
        self.models.select_related.assert_not_called()
        self.assertEqual(self.scopes, [])

    def test_invalid_prompts_fail_before_query(self):
        for prompt in ("", " \n\t", "x" * 8001, None):
            with self.subTest(prompt_length=len(prompt) if prompt is not None else None):
                self.failed(prompt=prompt)
        self.models.select_related.assert_not_called()
        self.assertEqual(self.scopes, [])

    def test_maximum_id_and_prompt_are_accepted(self):
        self.invoke(model_id=2_147_483_647, prompt="x" * 8000)
        self.query.get.assert_awaited_once_with(id=2_147_483_647)
        self.assertTrue(self.report()["ok"])

    def test_missing_model_is_nonzero(self):
        self.query.get.return_value = None
        self.failed()
        self.probe.probe_mock.assert_not_called()

    def test_missing_provider_is_nonzero(self):
        self.model.provider = None
        self.failed()
        self.probe.probe_mock.assert_not_called()

    def test_lookup_error_is_redacted_and_not_retried(self):
        self.query.get.side_effect = RuntimeError("PRIVATE_SENTINEL")
        self.failed()
        self.query.get.assert_awaited_once()

    def test_mock_exception_is_redacted_and_not_retried(self):
        self.probe.probe_mock.side_effect = RuntimeError("PRIVATE_SENTINEL")
        self.failed()
        self.probe.probe_mock.assert_called_once()

    def test_failed_result_is_nonzero_and_extra_diagnostics_not_printed(self):
        self.probe.probe_mock.return_value = {"ok": False, "error": "PRIVATE_SENTINEL"}
        self.failed()

    def test_invalid_result_is_not_success(self):
        for result in (None, "PRIVATE_SENTINEL", {}, {"content": " "}, {"content": 9}, {"ok": 1, "content": "x"}):
            with self.subTest(result=result):
                self.probe.probe_mock.return_value = result
                self.failed()

    def test_success_output_is_allowlisted(self):
        self.probe.probe_mock.return_value = {
            "content": "local",
            "usage": {},
            "cost": None,
            "error": "PRIVATE_SENTINEL",
            "response_body": "PRIVATE_SENTINEL",
            "prompt_echo": "PRIVATE_SENTINEL",
        }
        self.invoke()
        self.assertNotIn("PRIVATE_SENTINEL", str(self.console.print.call_args_list))
        self.assertIsNone(self.report()["estimated_cost_usd"])

    def test_no_app_modules_imported(self):
        self.assertFalse(any(name == "app" or name.startswith("app.") for name in sys.modules))


if __name__ == "__main__":
    unittest.main()
