"""Actual pipeline with explicit in-memory ORM/provider/reporting boundaries.

This harness does not certify SQL locking, collector normalization, model quality,
HTTP authorization or real entitlement/payer persistence. No app package imports.
"""

from contextlib import nullcontext
from copy import deepcopy
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests.shared.vk_research_journey import ROOT, journey_plan, load_plan_modules, module
from tests.source_import_isolation import load_isolated_source


class _Scope:
    def __init__(self, **values):
        self.__dict__.update(values)
        self.days = (self.end - self.start).days + 1


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class ResearchHarness:
    def __init__(self):
        self._init_state()
        self._init_fixtures()
        self._init_doubles()
        imports = self._bindings()
        self.pipeline = load_isolated_source("_p8_t41_pipeline", ROOT / "app/services/task_pipeline.py", imports)
        self.results = load_isolated_source("_p8_t41_results", ROOT / "app/services/task_results.py", imports)

    def _init_state(self):
        """Mutable access/payer toggles that individual tests flip."""
        self.tenant_id = 3
        self.member_active = True
        self.permission_allowed = True
        self.catalog_owner_id = 5
        self.payer_available = True
        self.entitlement_allowed = True
        self.checkpoints = []
        self.order = []
        self.authorization_queries = []
        self.entitlement_calls = []

    def _init_fixtures(self):
        """Synthetic task/source/scenario rows, admitted snapshot and stage outputs."""
        self.scenario = SimpleNamespace(id=42, tenant_id=3, is_active=True)
        self.source = SimpleNamespace(id=11)
        self.task = SimpleNamespace(
            id=7,
            tenant_id=3,
            is_active=True,
            cron_expr="@once",
            launch_job_id=99,
            payload={"source_ids": [88]},
            sources=[self.source],
        )
        self.policy, self.plans, self.preparation = load_plan_modules()
        prepared = self.preparation.prepare_task_plan_create({}, journey_plan(), job_type="analyze")
        prepared.payload["source_ids"] = list(prepared.source_ids)
        self.snapshot = {"version": 1, "task_id": 7, "task_payload": prepared.payload, "once": True}
        self.collection = {
            "sources": 1,
            "items": 2,
            "new_items": 2,
            "error": 0,
            "rate_limited": 0,
            "skipped": 0,
            "per_source": [
                {
                    "source_id": 11,
                    "items": 2,
                    "selected": 2,
                    "outcome": "collected",
                    "content_hashes": ["a" * 64, "b" * 64, "a" * 64],
                }
            ],
        }
        self.analysis = {
            "sources": 1,
            "analyzed": 2,
            "skipped": 0,
            "error": 0,
            "staged_errors": 0,
            "per_source": [{"source_id": 11, "analyzed": 2, "analytics_ids": [123, 124]}],
        }

    def _init_doubles(self):
        """Provider, model, delivery and job-handler doubles."""
        self.aggregate = AsyncMock(side_effect=self._aggregate)
        self.summarize = AsyncMock(return_value=("Synthetic summary", {"model": "mock", "cost": 0}))
        self.send = AsyncMock(side_effect=AssertionError("Preview must never send"))
        self.handlers = SimpleNamespace(
            _load_task=AsyncMock(side_effect=lambda _: self.task),
            _resolve_sources=AsyncMock(side_effect=self._sources),
            handle_collect=AsyncMock(side_effect=self._collect),
            handle_analyze=AsyncMock(side_effect=self._analyze),
        )

    def _bindings(self):
        """Module-local import doubles for the pipeline and result reader source."""
        return {
            "app.core.tenant_context": module(
                "tenant", current_tenant_id=lambda: self.tenant_id, is_bypass=lambda: False
            ),
            "app.core.permissions": module(
                "permissions",
                has_permission=lambda *_: self.permission_allowed,
                permission_scope=lambda *_args, **_kw: nullcontext(),
                get_current_user=lambda: SimpleNamespace(id=5),
                require_permission=lambda *_: lambda fn: fn,
            ),
            "app.core.personal_catalog_context": module(
                "catalog", current_catalog_owner_id=lambda: self.catalog_owner_id
            ),
            "app.services.billing.payer": module("payer", resolve_scheduled_task_payer=self._payer),
            "app.models": module(
                "models",
                AgentTask=self._model("task", lambda: self.task),
                AgentScenario=self._model("scenario", lambda: self.scenario),
                User=SimpleNamespace(objects=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(id=5)))),
            ),
            "app.models.managers.tenant_manager": module(
                "membership",
                tenant_users=SimpleNamespace(
                    web_memberships=self._memberships, member_grants=AsyncMock(return_value={})
                ),
            ),
            "app.types": module(
                "types",
                ActionType=SimpleNamespace(VIEW="view", UPDATE="update", ANALYZE="analyze", CREATE="create"),
                AgentActionType=SimpleNamespace(),
                BotTriggerType=SimpleNamespace(),
            ),
            "app.jobs": module(
                "jobs", handlers=self.handlers, progress=SimpleNamespace(pipeline_checkpoint=self._checkpoint)
            ),
            "app.core.database": module("database", async_session_maker=_Session),
            "app.models.managers.entitlement_manager": module(
                "entitlements",
                EntitlementManager=lambda: SimpleNamespace(admit_execution=self._admit),
            ),
            "app.services.collection.entitlement_policy": module(
                "capabilities", required_capabilities=self._capabilities
            ),
            "app.services.ai.reporting": module(
                "reporting", ReportScope=_Scope, normalize_digest_axis=lambda value: value
            ),
            "app.services.digest.builder": module(
                "builder",
                aggregate=self.aggregate,
                _summarize=self.summarize,
                render_digest=lambda data, **_: "Synthetic evidence https://vk.com/wall-100_1\n"
                + data.get("coverage_note", ""),
                broadcast_digest=self.send,
            ),
        }

    async def _capabilities(self, session, payload, *, sources):
        assert payload == self.snapshot["task_payload"]
        assert [s.id for s in sources] == [11]
        return frozenset({"social.collect", "ai.analyze"})

    async def _admit(self, required, *, job_id):
        self.entitlement_calls.append((required, job_id))
        if not self.entitlement_allowed:
            raise PermissionError("synthetic_entitlement_denied")

    async def _payer(self, **values):
        assert values == {"task_id": 7, "tenant_id": 3}
        if not self.payer_available:
            raise RuntimeError("private billing detail")
        return SimpleNamespace(user_id=5)

    async def _memberships(self, actor):
        assert actor == 5
        return [SimpleNamespace(tenant_id=3, is_active=self.member_active)]

    def _model(self, kind, record):
        def query(**criteria):
            self.authorization_queries.append((kind, criteria))
            row = record()
            found = row if row and all(getattr(row, k, None) == v for k, v in criteria.items()) else None
            return SimpleNamespace(first=AsyncMock(return_value=found))

        return SimpleNamespace(objects=SimpleNamespace(filter=query))

    def payload(self, *, attempt=1, prior=None):
        return {
            "agent_task_id": 7,
            "job_id": 99,
            "attempts": attempt,
            "_trusted_initiated_by_user_id": 5,
            "_effective_snapshot": deepcopy(self.snapshot),
            "_prior_result": deepcopy(prior or {}),
        }

    async def _checkpoint(self, value):
        self.checkpoints.append(deepcopy(value))

    async def _sources(self, task, payload, discovered_source_ids=None):
        assert task.payload == self.snapshot["task_payload"]
        assert payload["source_ids"] == [11]
        if discovered_source_ids is not None:
            assert discovered_source_ids == [11]
        return [self.source]

    async def _collect(self, payload, *, stage_only, _resolved_context):
        assert stage_only is True
        assert _resolved_context[2] == self.snapshot["task_payload"]
        self.order.append("collect")
        return deepcopy(self.collection)

    async def _analyze(self, payload, *, _resolved_context, collection_stats_override):
        assert _resolved_context[2] == self.snapshot["task_payload"]
        hashes = collection_stats_override["per_source"][0]["content_hashes"]
        assert hashes == list(dict.fromkeys(self.collection["per_source"][0]["content_hashes"]))
        self.order.append("analyze")
        return deepcopy(self.analysis)

    async def _aggregate(self, period, source_ids, group_by, time_breakdown, scenario_id, *, report_scope):
        assert (period, source_ids, group_by, time_breakdown, scenario_id) == ("custom", [11], "sources", True, 42)
        assert report_scope.tenant_id == 3
        assert report_scope.source_ids == frozenset({11})
        assert (report_scope.start, report_scope.end) == (date(2026, 10, 19), date(2026, 10, 21))
        self.order.append("digest")
        return {"stats": {"analyses": self.analysis["analyzed"]}}, None, None
