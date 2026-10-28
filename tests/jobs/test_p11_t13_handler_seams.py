"""Actual per-job digest implementation and facade routing, no sends or DB."""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from tests.source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]


class HandlerSeamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.publish = AsyncMock(return_value={"status": "ok"})
        self.module = load_isolated_source("_p11_digest", ROOT / "app/jobs/job_handlers/digest.py", {
            "app.services.digest.builder": NS(build_and_publish=self.publish),
        })

    async def test_task_scope_and_options_win_over_payload(self):
        task = NS(sources=[NS(id=4)], agent_scenario_id=8)
        load = AsyncMock(return_value=task)
        result = await self.module.handle_digest(
            {"agent_task_id": 2, "period": "day", "scenario_id": 10},
            load_task=load,
            task_payload=lambda row: {"period": "week", "group_by": "sources", "time_breakdown": True},
        )
        self.assertEqual(result, {"status": "ok"})
        load.assert_awaited_once_with(2)
        self.publish.assert_awaited_once_with(period="week", agent_task_id=2, force=False, source_ids=[4],
                                            group_by="sources", time_breakdown=True, scenario_id=10)

    async def test_invalid_period_never_publishes(self):
        result = await self.module.handle_digest({"period": "invalid"}, load_task=AsyncMock(return_value=None),
                                                 task_payload=lambda task: {})
        self.assertEqual(result, {"status": "failed", "error": "Invalid period: invalid"})
        self.publish.assert_not_awaited()

    async def test_public_dispatch_stays_on_the_facade(self):
        tree = ast.parse((ROOT / "app/jobs/handlers.py").read_text())
        for name in ("handle_digest", "handle_prune"):
            function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
            self.assertEqual([a.arg for a in function.args.args], ["payload"])
            self.assertTrue(any(isinstance(n, ast.ImportFrom) and n.module.startswith("app.jobs.job_handlers.")
                                for n in ast.walk(function)))
        registry = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == "HANDLERS" for t in n.targets))
        self.assertEqual({k.value: v.id for k, v in zip(registry.keys, registry.values)}, {
            "collect": "handle_collect", "analyze": "handle_analyze", "digest": "handle_digest",
            "prune": "handle_prune", "learn": "handle_learn", "reflect": "handle_reflect",
        })


if __name__ == "__main__":
    unittest.main()
