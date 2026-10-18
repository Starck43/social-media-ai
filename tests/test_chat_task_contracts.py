"""No-DB regressions for the actual task-tool module with explicit import doubles.

Run: python tests/test_chat_task_contracts.py -v in a separate empty venv.
No application, settings, DB, provider, conftest or third-party import is executed.
Manager persistence/permissions are doubles; these tests do not prove DB atomicity
or ASGI/tenant authorization. Registry declarations are checked, not bypassed.
"""

from __future__ import annotations

import ast
import copy
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]


class ChatTaskContracts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenant_id = 7
        self.task = NS(
            id=1, name="fixture", tenant_id=7, job_type="digest", cron_expr="0 9 * * *",
            agent_scenario_id=42, is_active=True, next_run_at=None, last_status=None,
            payload={"brands": ["existing"], "cli_dates": {"start_date": "2026-10-01", "end_date": "2026-10-15"}},
        )
        self.tasks = NS(get=AsyncMock(return_value=self.task), update_by_id=AsyncMock(return_value=self.task),
                        create=AsyncMock(return_value=self.task), order_by=AsyncMock(return_value=[self.task]))
        self.scenarios = NS(get=AsyncMock(return_value=NS(id=42, tenant_id=7)))
        self.sources = NS(filter=AsyncMock(return_value=[NS(id=9, tenant_id=7)]))
        self.tenant = NS(timezone="Europe/Kirov")
        self.tenants = NS(get=AsyncMock(return_value=self.tenant))
        self.set_sources = AsyncMock(return_value=1)
        self.add_sources = AsyncMock(return_value=1)
        self.next_run = Mock(return_value=datetime(2026, 10, 19, 7, tzinfo=timezone.utc))
        self.specs = {}

        def register(**spec):
            def decorate(handler):
                self.specs[spec["name"]] = spec
                return handler
            return decorate

        # Use the real date parser and exact manager date helpers, not a second
        # date implementation. DB methods and cron evaluation remain doubles.
        dates = load_isolated_source("_task_dates_fixture", ROOT / "app/utils/date_parsing.py")
        manager_tree = ast.parse((ROOT / "app/models/managers/agent_task_manager.py").read_text())
        manager_class = next(n for n in manager_tree.body if isinstance(n, ast.ClassDef))
        methods = [n for n in manager_class.body if isinstance(n, ast.FunctionDef) and n.name in {
            "parse_date", "build_dates_payload", "requires_content_dates"
        }]
        manager_ns = {"SOURCE_BASED_JOB_TYPES": {"collect", "analyze"}}
        isolated_import = lambda name, *a, **kw: NS(universal_date_parser=dates.universal_date_parser)
        import builtins
        manager_ns["__builtins__"] = dict(vars(builtins), __import__=isolated_import)
        cls = ast.ClassDef(name="TaskManager", bases=[], keywords=[], body=methods, decorator_list=[])
        code = compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), "date_helpers", "exec")
        exec(code, manager_ns)
        manager = manager_ns["TaskManager"]
        manager.validate_cron = staticmethod(lambda cron: cron in {"0 9 * * *", "0 10 * * *", "@once"})
        manager.set_sources = self.set_sources
        manager.add_sources = self.add_sources
        self.manager = manager
        self.module = load_isolated_source(
            "_chat_task_tools", ROOT / "app/agent/toolset/tasks.py",
            {
                "app.agent.tools": NS(tool=register),
                "app.jobs.handlers": NS(HANDLERS=dict.fromkeys(("digest", "collect", "analyze", "learn"))),
                "app.core.tenant_context": NS(current_tenant_id=lambda: self.tenant_id),
                "app.core.config": NS(settings=NS(SCHEDULER_TIMEZONE="UTC")),
                "app.models": NS(
                    AgentTask=NS(id="id", objects=self.tasks), Tenant=NS(objects=self.tenants),
                    AgentScenario=NS(objects=self.scenarios), Source=NS(objects=self.sources),
                ),
                "app.models.managers.agent_task_manager": NS(AgentTaskManager=manager),
                "app.tasks.cron": NS(next_run_at=self.next_run, resolve_tz=lambda t: t.timezone),
                "app.services.ai.param_registry": NS(missing_target_params=lambda *a: []),
            },
        )

    def assert_no_writes(self):
        self.tasks.update_by_id.assert_not_awaited()
        self.tasks.create.assert_not_awaited()
        self.set_sources.assert_not_awaited()
        self.add_sources.assert_not_awaited()

    async def test_listing_imports_current_model_and_formats_rows(self):
        rows = await self.module.task_list()
        self.assertEqual(rows[0]["name"], "fixture")
        self.assertEqual(rows[0]["cron"], "0 9 * * *")
        self.assert_no_writes()

    async def test_period_edit_preserves_scenario_dates_and_targets(self):
        original = copy.deepcopy(self.task.payload)
        result = await self.module.task_update("fixture", period="week")
        self.assertEqual(result["status"], "updated")
        changes = self.tasks.update_by_id.await_args.kwargs
        self.assertNotIn("agent_scenario_id", changes)
        self.assertNotIn("cron_expr", changes)
        self.assertEqual(changes["payload"], {**original, "period": "week"})
        self.assertEqual(self.task.payload, original)

    async def test_noop_does_not_write_or_reschedule(self):
        self.assertEqual((await self.module.task_update("fixture"))["status"], "unchanged")
        self.assert_no_writes()
        self.next_run.assert_not_called()

    async def test_explicit_null_scenario_does_not_clear_binding(self):
        await self.module.task_update("fixture", scenario_id=None)
        self.assert_no_writes()

    async def test_explicit_clear_changes_only_binding(self):
        await self.module.task_update("fixture", clear_scenario=True)
        self.assertEqual(self.tasks.update_by_id.await_args.kwargs, {"agent_scenario_id": None})
        self.scenarios.get.assert_not_awaited()

    async def test_scenario_change_is_workspace_scoped(self):
        await self.module.task_update("fixture", scenario_id=43)
        self.scenarios.get.assert_awaited_once_with(id=43, tenant_id=7)
        self.assertEqual(self.tasks.update_by_id.await_args.kwargs, {"agent_scenario_id": 43})

    async def test_conflicting_clear_or_wrong_boolean_is_refused(self):
        for kwargs in ({"scenario_id": 42, "clear_scenario": True}, {"clear_scenario": "yes"}):
            with self.subTest(kwargs=kwargs):
                self.assertIn("error", await self.module.task_update("fixture", **kwargs))
                self.assert_no_writes()

    async def test_missing_and_foreign_scenarios_refuse_before_writes(self):
        for scenario in (None, NS(id=99, tenant_id=8)):
            self.scenarios.get.return_value = scenario
            with self.assertRaisesRegex(ValueError, "unavailable"):
                await self.module.task_update("fixture", scenario_id=99, period="week")
            self.assert_no_writes()

    async def test_malformed_scenario_ids_refuse_before_query(self):
        for value in (True, 0, -1, "42", 2_147_483_648):
            with self.assertRaises(ValueError):
                await self.module.task_update("fixture", scenario_id=value)
            self.assert_no_writes()
        self.scenarios.get.assert_not_awaited()

    async def test_cron_and_next_run_are_one_manager_update(self):
        result = await self.module.task_update("fixture", cron="0 10 * * *")
        self.tasks.update_by_id.assert_awaited_once_with(
            1, cron_expr="0 10 * * *", next_run_at=self.next_run.return_value
        )
        self.next_run.assert_called_once_with("0 10 * * *", "Europe/Kirov")
        self.assertEqual(result["old_cron"], "0 9 * * *")
        self.assertEqual(result["new_cron"], "0 10 * * *")

    async def test_once_update_avoids_cron_parser(self):
        before = datetime.now(timezone.utc)
        await self.module.task_update("fixture", cron="@once")
        changes = self.tasks.update_by_id.await_args.kwargs
        self.assertEqual(changes["cron_expr"], "@once")
        self.assertGreaterEqual(changes["next_run_at"], before + timedelta(minutes=1))
        self.assertLess(changes["next_run_at"], datetime.now(timezone.utc) + timedelta(minutes=1, seconds=1))
        self.next_run.assert_not_called()

    async def test_invalid_cron_refuses_before_writes(self):
        for value in ("invalid", 123):
            self.assertIn("error", await self.module.task_update("fixture", cron=value))
            self.assert_no_writes()

    async def test_sources_are_replaced_not_appended(self):
        await self.module.task_update("fixture", source_ids=[9])
        self.sources.filter.assert_awaited_once_with(id__in=[9], tenant_id=7)
        self.set_sources.assert_awaited_once_with(1, [9])
        self.add_sources.assert_not_awaited()
        self.tasks.update_by_id.assert_not_awaited()

    async def test_empty_sources_explicitly_clears_links(self):
        await self.module.task_update("fixture", source_ids=[])
        self.set_sources.assert_awaited_once_with(1, [])
        self.sources.filter.assert_not_awaited()

    async def test_missing_and_foreign_sources_refuse_before_field_update(self):
        for rows in ([], [NS(id=9, tenant_id=8)]):
            self.sources.filter.return_value = rows
            with self.assertRaisesRegex(ValueError, "unavailable"):
                await self.module.task_update("fixture", source_ids=[9], period="week")
            self.assert_no_writes()

    async def test_malformed_sources_refuse_before_query(self):
        for value in ("9", [True], [0], [-1], ["9"], [2_147_483_648]):
            with self.assertRaises(ValueError):
                await self.module.task_update("fixture", source_ids=value)
            self.assert_no_writes()
        self.sources.filter.assert_not_awaited()

    async def test_missing_workspace_or_foreign_task_refuses(self):
        self.tenant_id = None
        self.assertIn("error", await self.module.task_update("fixture", period="week"))
        self.tasks.get.assert_not_awaited()
        self.tenant_id = 7
        self.task.tenant_id = 8
        self.assertIn("error", await self.module.task_update("fixture", period="week"))
        self.assert_no_writes()

    async def test_missing_task_refuses(self):
        self.tasks.get.return_value = None
        self.assertIn("error", await self.module.task_update("fixture", period="week"))
        self.assert_no_writes()

    async def test_disappeared_task_does_not_change_sources(self):
        self.tasks.update_by_id.return_value = None
        result = await self.module.task_update("fixture", period="week", source_ids=[9])
        self.assertIn("error", result)
        self.set_sources.assert_not_awaited()

    async def test_end_only_edit_preserves_start_and_targets(self):
        self.task.job_type = "collect"
        await self.module.task_update("fixture", end_date="2026-10-18")
        payload = self.tasks.update_by_id.await_args.kwargs["payload"]
        self.assertEqual(payload["cli_dates"], {"start_date": "2026-10-01", "end_date": "2026-10-18"})
        self.assertEqual(payload["brands"], ["existing"])

    async def test_start_only_edit_preserves_end(self):
        self.task.job_type = "analyze"
        await self.module.task_update("fixture", start_date="02.10.2026")
        self.assertEqual(self.tasks.update_by_id.await_args.kwargs["payload"]["cli_dates"],
                         {"start_date": "2026-10-02", "end_date": "2026-10-15"})

    async def test_reanalyze_only_edit_keeps_window(self):
        self.task.job_type = "analyze"
        await self.module.task_update("fixture", force_reanalyze=True)
        payload = self.tasks.update_by_id.await_args.kwargs["payload"]
        self.assertEqual(payload["cli_dates"], self.task.payload["cli_dates"])
        self.assertIs(payload["force_reanalyze"], True)

    async def test_switch_to_source_job_requires_start_before_write(self):
        self.task.payload = {"brands": ["existing"]}
        with self.assertRaisesRegex(ValueError, "start_date"):
            await self.module.task_update("fixture", job_type="collect")
        self.assert_no_writes()

    async def test_invalid_or_reversed_dates_refuse_before_write(self):
        self.task.job_type = "collect"
        for kwargs in ({"start_date": "invalid"}, {"end_date": "2026-09-01"}, {"start_date": ""}):
            with self.assertRaises(ValueError):
                await self.module.task_update("fixture", **kwargs)
            self.assert_no_writes()

    async def test_task_create_checks_sources_and_scenario_before_insert(self):
        self.tasks.get.return_value = None
        self.sources.filter.return_value = []
        with self.assertRaises(ValueError):
            await self.module.task_add("new", "0 9 * * *", "digest", source_ids=[99])
        self.assert_no_writes()
        self.scenarios.get.return_value = None
        with self.assertRaises(ValueError):
            await self.module.task_add("new", "0 9 * * *", "digest", scenario_id=99)
        self.assert_no_writes()

    async def test_once_create_avoids_cron_parser(self):
        self.tasks.get.return_value = None
        await self.module.task_add("new", "@once", "digest")
        self.assertEqual(self.tasks.create.await_args.kwargs["cron_expr"], "@once")
        self.assertIsInstance(self.tasks.create.await_args.kwargs["next_run_at"], datetime)
        self.next_run.assert_not_called()

    async def test_confirmation_and_permissions_stay_declared(self):
        for name, action in (("task_add", "create"), ("task_update", "update")):
            self.assertIs(self.specs[name]["confirm"], True)
            self.assertEqual(self.specs[name]["required_permission"], f"scheduling.task.{action}")
        self.assertEqual(self.specs["task_list"]["required_permission"], "scheduling.task.view")
        self.assertEqual(self.specs["task_update"]["parameters"]["properties"]["clear_scenario"]["type"], "boolean")


if __name__ == "__main__":
    unittest.main()
