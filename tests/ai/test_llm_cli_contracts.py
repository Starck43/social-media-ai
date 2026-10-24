"""Whole-source CLI catalog contracts; storage/context doubles, no app/DB/network.

Run directly, without pytest/conftest: python tests/ai/test_llm_cli_contracts.py -v.
The actual handler source is exercised end to end; only storage, permission
context, Typer and Rich are doubles. Nothing under ``app`` is imported (checked
as a per-test module delta), so no engine, ORM session, provider key or HTTP
client can be reached by these tests.
"""

import json
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]


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


class Untouchable:
    """A module double that fails loudly if any attribute is used."""

    def __init__(self, label):
        self._label = label

    def __getattr__(self, attribute):
        raise AssertionError(f"{self._label}.{attribute} must not be touched")


def provider_row(identity, *, owner=7, name=None, active=True):
    return {
        "id": identity,
        "owner_user_id": owner,
        "name": name or f"Provider {identity}",
        "api_format": "openai",
        "base_url": f"https://fixture{identity}.invalid/v1",
        "is_active": active,
    }


def model_row(
    identity,
    *,
    owner=7,
    provider_id=1,
    name=None,
    model_id=None,
    model_type="text",
    active=True,
    default=False,
):
    provider = NS(id=provider_id, name=f"Provider {provider_id}", owner_user_id=owner, is_active=True)
    provider.get_api_key = Mock(side_effect=AssertionError("No key access"))
    return NS(
        id=identity,
        owner_user_id=owner,
        provider_id=provider_id,
        provider=provider,
        name=name or f"Model {identity}",
        model_id=model_id or f"fixture-{identity}",
        model_type=model_type,
        is_active=active,
        is_default=default,
        max_tokens=4096,
        default_temperature=0.3,
        input_cost_per_1k=0.0,
        output_cost_per_1k=0.0,
        use_count=0,
        fail_count=0,
    )


class CatalogContext:
    """Permission-context and catalog-service double with real gate semantics."""

    def __init__(self):
        self.actor = NS(id=7, is_active=True)
        self.bound_owner = None
        self.providers = []
        self.model_rows = []
        self.metadata_calls = []
        self.metadata_error = None

    def current_catalog_owner_id(self):
        if self.bound_owner is not None:
            return self.bound_owner
        actor = self.actor
        if actor is not None and actor.is_active and type(actor.id) is int and actor.id > 0:
            return actor.id
        raise PermissionError("An active personal catalog owner is required")

    def require_actor(self, user_id):
        actor = self.actor
        if type(user_id) is not int or user_id <= 0 or actor is None or not actor.is_active or actor.id != user_id:
            raise PermissionError("Personal catalog access denied")
        return actor

    async def catalog_metadata(self, *, user_id):
        self.require_actor(user_id)
        if self.metadata_error is not None:
            raise self.metadata_error
        self.metadata_calls.append(user_id)
        rows = [row for row in self.model_rows if row.owner_user_id == user_id]
        providers = []
        for provider in self.providers:
            if provider["owner_user_id"] != user_id:
                continue
            providers.append({**provider, "models": [row for row in rows if row.provider_id == provider["id"]]})
        return {"providers": providers, "models": rows, "effective_default_model_id": None}

    def assert_catalog_model(self, model):
        owner = self.current_catalog_owner_id()
        provider = getattr(model, "provider", None)
        if (
            getattr(model, "owner_user_id", None) != owner
            or provider is None
            or getattr(provider, "owner_user_id", None) != owner
        ):
            raise PermissionError("Model is unavailable in this personal catalog")


class ModelStore:
    """Owner-scoped model manager double; filters behave like the real query."""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.forced = None
        self.get_error = None
        self.select_related_calls = []
        self.get_calls = []
        self.filter_calls = []
        self.order_by_calls = []

    def select_related(self, relation):
        self.select_related_calls.append(relation)
        return self

    async def get(self, **filters):
        self.get_calls.append(filters)
        if self.get_error is not None:
            raise self.get_error
        if self.forced is not None:
            return self.forced
        return _single([row for row in self.rows if _matches(row, filters)])

    def filter(self, **filters):
        self.filter_calls.append(filters)
        rows = [row for row in self.rows if _matches(row, filters)]

        async def order_by(*args):
            self.order_by_calls.append(args)
            return rows

        return NS(order_by=order_by)


def _matches(row, filters):
    return all(getattr(row, key) == value for key, value in filters.items())


def _single(rows):
    if len(rows) > 1:
        raise AssertionError("Ambiguous fake lookup")
    return rows[0] if rows else None


class LLMCLIContracts(unittest.TestCase):
    def setUp(self):
        self.output = Mock()
        self.console = NS(print=Mock())
        self.scopes = []
        self.id_sentinel = NS(name="id")
        self.app_modules_before = frozenset(name for name in sys.modules if name == "app" or name.startswith("app."))

        @contextmanager
        def scope(**kwargs):
            self.scopes.append(kwargs)
            yield

        self.catalog = CatalogContext()
        self.model = model_row(7)
        self.foreign_model = model_row(8, owner=9)
        self.catalog.providers = [provider_row(1)]
        self.catalog.model_rows = [self.model]
        self.store = ModelStore([self.model, self.foreign_model])

        self.price = Mock(return_value=0.05)
        self.probe = load_isolated_source(
            "_cli_shared_probe",
            ROOT / "app/services/ai/model_probe.py",
            {"app.services.ai.llm_client": NS(price_usage_usd=self.price)},
            module_imports={"httpx": Untouchable("httpx")},
        )
        self.probe.probe_mock = Mock(wraps=self.probe.probe_mock)
        self.probe.probe_real_capped = AsyncMock(side_effect=AssertionError("No real probe"))

        self.cli = load_isolated_source(
            "_llm_cli",
            ROOT / "cli/commands/llm.py",
            {
                "app.core.tenant_context": NS(tenant_scope=scope),
                "app.core.personal_catalog_context": NS(
                    current_catalog_owner_id=self.catalog.current_catalog_owner_id,
                    assert_catalog_model=self.catalog.assert_catalog_model,
                ),
                "app.services.ai.personal_catalog": NS(
                    require_actor=self.catalog.require_actor,
                    catalog_metadata=self.catalog.catalog_metadata,
                ),
                "app.models": NS(LLMModel=NS(objects=self.store, id=self.id_sentinel)),
                "app.services.ai.model_probe": self.probe,
                # Explicit tripwires: never fall through into real app imports.
                "app.core.database": Untouchable("app.core.database"),
                "app.admin.actions": Untouchable("app.admin.actions"),
                "app.services.ai.llm_client": Untouchable("app.services.ai.llm_client"),
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

    def rejected(self, handler, *, message, **kwargs):
        with self.assertRaises(Exit) as error:
            handler(**kwargs)
        self.assertEqual(error.exception.exit_code, 1)
        self.console.print.assert_not_called()
        self.assertNotIn("PRIVATE_SENTINEL", str(self.output.call_args_list))
        self.assertEqual(self.scopes, [])
        self.assertEqual(self.catalog.metadata_calls, [])
        self.assertEqual(self.store.select_related_calls, [])
        self.assertEqual(self.store.get_calls, [])
        self.assertEqual(self.store.filter_calls, [])
        self.probe.probe_mock.assert_not_called()
        self.probe.probe_real_capped.assert_not_awaited()
        self.assertIn(message, str(self.output.call_args_list))

    def denied(self, handler, **kwargs):
        self.rejected(handler, message="authenticated active user", **kwargs)

    def failed_backend(self):
        with self.assertRaises(Exit) as error:
            self.invoke()
        self.assertEqual(error.exception.exit_code, 1)
        self.console.print.assert_not_called()
        self.assertNotIn("PRIVATE_SENTINEL", str(self.output.call_args_list))
        self.probe.probe_real_capped.assert_not_awaited()
        self.assertEqual(self.scopes, [{"bypass": True}])
        self.assertIn("Local model test failed", str(self.output.call_args_list))

    def report(self):
        args, kwargs = self.console.print.call_args
        self.assertEqual(kwargs, {"markup": False, "highlight": False, "soft_wrap": True})
        self.assertNotIn("\x1b", args[0])
        return json.loads(args[0])

    def test_no_actor_provider_list_is_denied_before_backend(self):
        self.catalog.actor = None
        self.denied(self.cli.provider_list)
        self.probe.probe_mock.assert_not_called()

    def test_no_actor_model_list_is_denied_before_backend(self):
        self.catalog.actor = None
        self.denied(self.cli.model_list)
        self.probe.probe_mock.assert_not_called()

    def test_no_actor_mock_is_denied_before_backend(self):
        self.catalog.actor = None
        self.denied(self.invoke)
        self.model.provider.get_api_key.assert_not_called()

    def test_inactive_actor_is_denied_for_every_command(self):
        self.catalog.actor = NS(id=7, is_active=False)
        for handler in (self.cli.provider_list, self.cli.model_list, self.invoke):
            with self.subTest(handler=handler.__name__):
                self.denied(handler)
        self.probe.probe_mock.assert_not_called()

    def test_worker_owner_scope_without_matching_actor_is_denied(self):
        cases = [(7, None), (7, NS(id=9, is_active=True)), (7, NS(id=7, is_active=False))]
        for bound_owner, actor in cases:
            with self.subTest(bound_owner=bound_owner, actor=actor):
                self.catalog.bound_owner = bound_owner
                self.catalog.actor = actor
                for handler in (self.cli.provider_list, self.cli.model_list, self.invoke):
                    with self.subTest(handler=handler.__name__):
                        self.denied(handler)
        self.probe.probe_mock.assert_not_called()

    def test_bound_owner_with_matching_active_actor_is_trusted(self):
        self.catalog.bound_owner = 7
        self.invoke()
        self.assertEqual(self.store.get_calls, [{"id": 7, "owner_user_id": 7}])
        self.probe.probe_mock.assert_called_once_with(self.model, "hello")
        self.assertTrue(self.report()["ok"])

    def test_provider_list_is_owner_scoped_and_counts_own_models(self):
        self.catalog.providers = [
            provider_row(1, name="one"),
            provider_row(2, name="two", active=False),
            provider_row(3, owner=9, name="foreign"),
        ]
        self.catalog.model_rows = [
            model_row(10, provider_id=1),
            model_row(11, provider_id=1),
            model_row(12, owner=9, provider_id=3),
        ]
        self.cli.provider_list()
        self.assertEqual(self.catalog.metadata_calls, [7])
        self.assertEqual(self.store.select_related_calls, [])
        self.assertEqual(self.store.filter_calls, [])
        table = self.console.print.call_args.args[0]
        self.assertEqual(table.columns, ["id", "name", "api_format", "base_url", "active", "models"])
        self.assertNotIn("default", table.columns)
        self.assertEqual(
            table.rows,
            [
                ("1", "one", "openai", "https://fixture1.invalid/v1", "yes", "2"),
                ("2", "two", "openai", "https://fixture2.invalid/v1", "no", "0"),
            ],
        )

    def test_empty_provider_list(self):
        self.catalog.providers = []
        self.catalog.model_rows = []
        self.cli.provider_list()
        self.assertEqual(self.console.print.call_args.args[0].rows, [])

    def test_model_list_is_owner_scoped_and_filters(self):
        self.store.rows = [
            model_row(7, provider_id=1, name="own text"),
            model_row(8, provider_id=2, name="own other", model_type="image"),
            model_row(9, owner=9, provider_id=1, name="foreign text"),
        ]
        self.cli.model_list(provider=1, active=True, model_type="text")
        self.assertEqual(
            self.store.filter_calls,
            [{"owner_user_id": 7, "provider_id": 1, "is_active": True, "model_type": "text"}],
        )
        self.assertEqual(self.store.order_by_calls, [(self.id_sentinel,)])
        table = self.console.print.call_args.args[0]
        self.assertEqual([row[0] for row in table.rows], ["7"])
        self.assertEqual([row[2] for row in table.rows], ["own text"])
        self.assertIn("default", table.columns)
        self.assertNotIn("foreign text", str(table.rows))

    def test_provider_list_backend_failure_is_redacted(self):
        self.catalog.metadata_error = RuntimeError("PRIVATE_SENTINEL")
        with self.assertRaises(Exit) as error:
            self.cli.provider_list()
        self.assertEqual(error.exception.exit_code, 1)
        self.console.print.assert_not_called()
        self.assertNotIn("PRIVATE_SENTINEL", str(self.output.call_args_list))
        self.assertIn("could not be read", str(self.output.call_args_list))

    def test_mock_uses_actual_shared_probe_once(self):
        self.model.name = "Model [link=https://fixture.invalid]7[/link]"
        self.invoke()
        self.assertEqual(self.store.select_related_calls, ["provider"])
        self.assertEqual(self.store.get_calls, [{"id": 7, "owner_user_id": 7}])
        self.assertEqual(self.catalog.metadata_calls, [])
        self.probe.probe_mock.assert_called_once_with(self.model, "hello")
        self.price.assert_called_once_with(self.model, 8, 12)
        self.model.provider.get_api_key.assert_not_called()
        self.probe.probe_real_capped.assert_not_awaited()
        self.assertEqual(self.scopes, [{"bypass": True}])
        result = self.report()
        self.assertEqual(
            set(result),
            {
                "mode",
                "ok",
                "is_real_response",
                "tokens_spent",
                "content",
                "simulated_usage",
                "estimated_cost_usd",
            },
        )
        self.assertEqual(result["mode"], "mock")
        self.assertTrue(result["ok"])
        self.assertFalse(result["is_real_response"])
        self.assertEqual(result["tokens_spent"], 0)
        self.assertEqual(result["simulated_usage"]["total_tokens"], 20)
        self.assertEqual(result["estimated_cost_usd"], 0.05)
        self.assertIn("[link=", result["content"])
        self.assertNotIn("prompt_echo", result)
        self.assertNotIn("cost_usd", result)
        self.assertNotIn("PRIVATE_SENTINEL", str(self.console.print.call_args_list))

    def test_inactive_model_and_provider_mock_is_not_readiness(self):
        self.model.is_active = False
        self.model.provider.is_active = False
        self.invoke()
        self.assertTrue(self.report()["ok"])
        self.model.provider.get_api_key.assert_not_called()

    def test_real_path_fails_before_catalog_or_billing(self):
        self.rejected(self.invoke, message="authenticated payer", mock=False)
        source = (ROOT / "cli/commands/llm.py").read_text(encoding="utf-8")
        code = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
        self.assertNotIn("billing", code.lower())

    def test_default_handler_argument_is_fail_closed(self):
        with self.assertRaises(Exit) as error:
            self.cli.model_test(7)
        self.assertEqual(error.exception.exit_code, 1)
        self.assertEqual(self.scopes, [])
        self.assertEqual(self.store.select_related_calls, [])
        self.assertEqual(self.catalog.metadata_calls, [])

    def test_invalid_ids_fail_before_catalog(self):
        for model_id in (0, -1, 2_147_483_648, True, "7", None):
            with self.subTest(model_id=model_id):
                self.rejected(self.invoke, message="Model id must be", model_id=model_id)
        self.model.provider.get_api_key.assert_not_called()

    def test_invalid_prompts_fail_before_catalog(self):
        for prompt in ("", " \n\t", "x" * 8001, None):
            with self.subTest(prompt_length=len(prompt) if prompt is not None else None):
                self.rejected(self.invoke, message="Prompt must contain", prompt=prompt)

    def test_maximum_id_and_prompt_are_accepted(self):
        self.store.rows.append(model_row(2_147_483_647, name="Max"))
        self.invoke(model_id=2_147_483_647, prompt="x" * 8000)
        self.assertEqual(self.store.get_calls, [{"id": 2_147_483_647, "owner_user_id": 7}])
        self.assertTrue(self.report()["ok"])

    def test_missing_model_is_generic_failure(self):
        self.store.rows = []
        self.failed_backend()
        self.probe.probe_mock.assert_not_called()

    def test_foreign_model_is_hidden_by_owner_filter(self):
        self.store.rows = [self.foreign_model]
        self.failed_backend()
        self.probe.probe_mock.assert_not_called()

    def test_forced_foreign_model_is_denied_by_catalog_guard(self):
        self.store.forced = self.foreign_model
        self.failed_backend()
        self.probe.probe_mock.assert_not_called()

    def test_missing_provider_is_generic_failure(self):
        self.store.forced = NS(id=7, owner_user_id=7, provider=None)
        self.failed_backend()
        self.probe.probe_mock.assert_not_called()

    def test_lookup_error_is_redacted_and_not_retried(self):
        self.store.get_error = RuntimeError("PRIVATE_SENTINEL")
        self.failed_backend()
        self.assertEqual(len(self.store.get_calls), 1)
        self.probe.probe_mock.assert_not_called()

    def test_mock_exception_is_redacted_and_not_retried(self):
        self.probe.probe_mock.side_effect = RuntimeError("PRIVATE_SENTINEL")
        self.failed_backend()
        self.probe.probe_mock.assert_called_once()
        self.price.assert_not_called()

    def test_failed_result_is_nonzero_and_extra_diagnostics_not_printed(self):
        self.probe.probe_mock.return_value = {"ok": False, "error": "PRIVATE_SENTINEL"}
        self.failed_backend()

    def test_invalid_result_is_not_success(self):
        for result in (None, "PRIVATE_SENTINEL", {}, {"content": " "}, {"content": 9}, {"ok": 1, "content": "x"}):
            with self.subTest(result=result):
                self.probe.probe_mock.return_value = result
                self.scopes.clear()
                self.failed_backend()

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
        self.model.provider.get_api_key.assert_not_called()

    def test_no_app_modules_imported(self):
        loaded = frozenset(name for name in sys.modules if name == "app" or name.startswith("app."))
        self.assertEqual(loaded - self.app_modules_before, frozenset())


if __name__ == "__main__":
    unittest.main()
