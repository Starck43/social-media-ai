"""Standalone direct-command wiring regressions with infrastructure doubles.

Owner: python tests/test_cli_direct_awaitables.py
Loads actual command/helper source, but never real app, tenant resolver, source
query, handler, Typer or Rich modules. This is not CLI/DB/provider acceptance.
"""

import asyncio
import builtins
import contextlib
import contextvars
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

_ROOT = Path(__file__).resolve().parents[1]


def load_source(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def package(name):
    module = types.ModuleType(name)
    module.__path__ = []
    return module


class CliExit(Exception):
    def __init__(self, code):
        self.exit_code = code
        super().__init__(code)


class DirectAwaitableTests(unittest.TestCase):
    def setUp(self):
        self.active = contextvars.ContextVar("test_owner_scope", default=False)
        self.scope_calls = []

        @contextlib.contextmanager
        def tenant_scope(*, bypass):
            self.scope_calls.append(bypass)
            token = self.active.set(bypass)
            try:
                yield
            finally:
                self.active.reset(token)

        self.resolve_tenant = AsyncMock(return_value=7)
        self.sources = [
            types.SimpleNamespace(
                id=11, name="synthetic", platform=types.SimpleNamespace(name="stub")
            )
        ]
        self.resolve_sources = AsyncMock(return_value=self.sources)
        self.stats = {"status": "ok", "synthetic_count": 1}
        self.handler = AsyncMock(return_value=self.stats)
        self.output = Mock()
        tenant = types.ModuleType("cli._tenant")
        tenant.resolve_tenant_id = self.resolve_tenant
        run = types.ModuleType("cli.run")
        run.resolve_sources, run.run_handler = self.resolve_sources, self.handler
        typer = types.ModuleType("typer")
        typer.Option = lambda default, *args, **kwargs: default
        typer.Exit = CliExit
        rich = package("rich")
        rich.print = self.output
        panel, table = types.ModuleType("rich.panel"), types.ModuleType("rich.table")
        panel.Panel, table.Table = Mock(), Mock()
        tenant_context = types.ModuleType("app.core.tenant_context")
        tenant_context.tenant_scope = tenant_scope
        helper = load_source("_isolated_cli_inputs", _ROOT / "cli" / "_inputs.py")
        modules = {
            "app": package("app"),
            "app.core": package("app.core"),
            "app.core.tenant_context": tenant_context,
            "cli": package("cli"),
            "cli._tenant": tenant,
            "cli.run": run,
            "cli._inputs": helper,
            "typer": typer,
            "rich": rich,
            "rich.panel": panel,
            "rich.table": table,
        }
        replacements = patch.dict(sys.modules, modules)
        replacements.start()
        self.addCleanup(replacements.stop)
        real_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if (
                name == "app" or name.startswith("app.")
            ) and name != "app.core.tenant_context":
                raise AssertionError(f"Real application imports are forbidden: {name}")
            return real_import(name, *args, **kwargs)

        guard = patch("builtins.__import__", side_effect=guarded_import)
        guard.start()
        self.addCleanup(guard.stop)
        self.direct = load_source(
            "_isolated_direct_commands", _ROOT / "cli" / "commands" / "direct.py"
        )
        self.direct._report = Mock()

    def assert_dispatch(self, job_type, extra):
        self.resolve_tenant.assert_awaited_once_with("workspace")
        self.resolve_sources.assert_awaited_once_with("11", 7)
        self.handler.assert_awaited_once_with(
            job_type, {"source_ids": [11], **extra}, 7
        )
        self.direct._report.assert_called_once()
        self.assertIs(self.direct._report.call_args.args[1], self.stats)
        self.assertEqual(self.scope_calls, [True])
        self.assertFalse(self.active.get())

    def test_sync_accepts_created_coroutine_inside_existing_scope(self):
        async def operation():
            self.assertTrue(self.active.get())
            return self.stats

        self.assertIs(self.direct._sync(operation()), self.stats)
        self.assertEqual(self.scope_calls, [True])
        self.assertFalse(self.active.get())

    def test_sync_keeps_factory_compatibility_and_calls_it_once_in_scope(self):
        async def operation():
            self.assertTrue(self.active.get())
            return self.stats

        def create():
            self.assertTrue(self.active.get())
            return operation()

        factory = Mock(side_effect=create)
        self.assertIs(self.direct._sync(factory), self.stats)
        factory.assert_called_once_with()
        self.assertFalse(self.active.get())

    def test_sync_restores_scope_after_error(self):
        async def operation():
            raise ValueError("synthetic failure")

        with self.assertRaisesRegex(ValueError, "synthetic failure"):
            self.direct._sync(operation())
        self.assertFalse(self.active.get())
        self.handler.assert_not_awaited()

    def test_sync_preserves_cancellation(self):
        async def operation():
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            self.direct._sync(operation())
        self.assertFalse(self.active.get())

    def test_resolver_is_awaitable_and_keeps_falsey_extras_except_none(self):
        self.sources.append(self.sources[0])
        tenant_id, payload, sources = self.direct._sync(
            self.direct._resolve(
                "workspace",
                "11",
                missing=None,
                flag=False,
                amount=0,
                text="",
                values=[],
            )
        )
        self.assertEqual(tenant_id, 7)
        self.assertIs(sources, self.sources)
        self.assertEqual(
            payload,
            {
                "source_ids": [11, 11],
                "flag": False,
                "amount": 0,
                "text": "",
                "values": [],
            },
        )
        self.handler.assert_not_awaited()

    def test_resolver_preserves_none_tenant(self):
        self.resolve_tenant.return_value = None
        tenant_id, payload, _ = self.direct._sync(self.direct._resolve(None, "11"))
        self.assertIsNone(tenant_id)
        self.assertEqual(payload, {"source_ids": [11]})
        self.resolve_sources.assert_awaited_once_with("11", None)

    def test_analyze_dispatches_once_with_shared_username_parsing(self):
        result = self.direct.cmd_analyze(
            src="11", tenant="workspace", scenario=3, excluded="@A,@A", verbose=True
        )
        self.assertIs(result, self.stats)
        self.assert_dispatch(
            "analyze", {"scenario_id": 3, "excluded_users": ["A", "A"]}
        )

    def test_digest_dispatches_once_with_false_time_breakdown(self):
        result = self.direct.cmd_digest(
            src="11",
            tenant="workspace",
            period="week",
            group_by="sources",
            time_breakdown=False,
            verbose=True,
        )
        self.assertIs(result, self.stats)
        self.assert_dispatch(
            "digest", {"period": "week", "group_by": "sources", "time_breakdown": False}
        )

    def test_prune_dispatches_once_preserving_zero_days(self):
        self.assertIs(
            self.direct.cmd_prune(src="11", tenant="workspace", days=0), self.stats
        )
        self.assert_dispatch("prune", {"days": 0})

    def test_learn_dispatches_once_preserving_zero_options(self):
        self.assertIs(
            self.direct.cmd_learn(
                src="11", tenant="workspace", min_messages=0, window=0
            ),
            self.stats,
        )
        self.assert_dispatch("learn", {"min_messages": 0, "window": 0})

    def test_reflect_dispatches_once_preserving_false_dedup(self):
        self.assertIs(
            self.direct.cmd_reflect(src="11", tenant="workspace", dedup=False),
            self.stats,
        )
        self.assert_dispatch("reflect", {"dedup": False})

    def test_empty_sources_stop_all_commands_before_handler_or_success_report(self):
        self.resolve_sources.return_value = []
        for command in (
            self.direct.cmd_analyze,
            self.direct.cmd_digest,
            self.direct.cmd_prune,
            self.direct.cmd_learn,
            self.direct.cmd_reflect,
        ):
            with self.subTest(command=command.__name__):
                with self.assertRaises(CliExit) as raised:
                    command(src="11", tenant="workspace")
                self.assertEqual(raised.exception.exit_code, 1)
                self.assertFalse(self.active.get())
        self.handler.assert_not_awaited()
        self.direct._report.assert_not_called()
        self.assertEqual(self.resolve_tenant.await_count, 5)
        self.assertEqual(self.resolve_sources.await_count, 5)

    def test_tenant_resolution_error_stops_before_source_query(self):
        self.resolve_tenant.side_effect = ValueError("synthetic tenant failure")
        with self.assertRaisesRegex(ValueError, "synthetic tenant failure"):
            self.direct.cmd_prune(src="11", tenant="workspace")
        self.resolve_sources.assert_not_awaited()
        self.handler.assert_not_awaited()
        self.direct._report.assert_not_called()
        self.assertFalse(self.active.get())

    def test_source_resolution_error_stops_before_handler(self):
        self.resolve_sources.side_effect = ValueError("synthetic source failure")
        with self.assertRaisesRegex(ValueError, "synthetic source failure"):
            self.direct.cmd_prune(src="11", tenant="workspace")
        self.handler.assert_not_awaited()
        self.direct._report.assert_not_called()
        self.assertFalse(self.active.get())

    def test_handler_error_propagates_without_retry_or_success_report(self):
        async def fail(*args):
            self.assertTrue(self.active.get())
            raise ValueError("synthetic handler failure")

        self.handler.side_effect = fail
        with self.assertRaisesRegex(ValueError, "synthetic handler failure"):
            self.direct.cmd_prune(src="11", tenant="workspace")
        self.handler.assert_awaited_once_with(
            "prune", {"source_ids": [11], "days": 7}, 7
        )
        self.direct._report.assert_not_called()
        self.assertFalse(self.active.get())


if __name__ == "__main__":
    unittest.main()
