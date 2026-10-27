"""Source-local doubles for task-plan policy/adapter checks; no app/DB import."""

import ast
import asyncio
import builtins
from contextlib import asynccontextmanager, nullcontext
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from tests.source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]


def load_plans():
    intent = load_isolated_source(
        "_plan_intent",
        ROOT / "app/services/collection/intent.py",
        {"app.core.config": NS(settings=NS(SOCIAL_COLLECT_LIMIT=100, SOCIAL_COLLECT_HARD_LIMIT=1000))},
    )
    plans = load_isolated_source(
        "_task_plans", ROOT / "app/services/task_plans.py", {"app.services.collection.intent": intent}
    )
    return plans, intent


def plan(**overrides):
    result = {
        "platforms": ["vk"],
        "source_scope": "selected",
        "source_ids": [11],
        "scenario_id": 3,
        "collection": {"queries": ["renovation"]},
        "window": {"type": "rolling", "days": 1},
        "trigger": {"type": "once"},
    }
    result.update(overrides)
    return result


def source_functions(path, names, imports, namespace=None, package=""):
    """Execute unchanged function ASTs with explicit local imports/decorator doubles.

    No module startup/application wiring is executed; this covers adapter logic,
    not authentication, routing, settings, database transactions or CLI rendering.
    """
    tree = ast.parse((ROOT / path).read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    normal = builtins.__import__

    def local_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level:
            import importlib.util

            name = importlib.util.resolve_name("." * level + name, package)
        if name in imports:
            return imports[name]
        return normal(name, globals, locals, fromlist, 0)

    scope = {
        "__name__": package + ".isolated",
        "__package__": package,
        "__builtins__": dict(vars(builtins), __import__=local_import),
    }
    scope.update(namespace or {})
    tree = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(tree), str(ROOT / path), "exec"), scope)
    return NS(**scope)


class Query:
    def __init__(self, rows):
        self.rows = rows

    def select_related(self, *_args):
        return self

    def __await__(self):
        async def rows():
            return self.rows

        return rows().__await__()


class Session:
    @asynccontextmanager
    async def begin(self):
        yield

    async def close(self):
        pass


def adapter_update(surface, payload, changes):
    """Call actual adapter functions with identical synthetic current task/input."""
    plans, intent = load_plans()
    import fastapi
    import typer

    row = NS(
        id=7,
        name="Research",
        payload=dict(payload),
        tenant_id=2,
        job_type="analyze",
        cron_expr="@once",
        agent_scenario_id=3,
        next_run_at=None,
    )
    writes = []

    async def update(_id, **values):
        values.pop("session", None)
        writes.append(values)
        for key, value in values.items():
            setattr(row, key, value)
        return row

    objects = NS(
        get=AsyncMock(return_value=row),
        filter=AsyncMock(return_value=[row]),
        update_by_id=update,
        validate_cron=lambda value: True,
    )
    model = NS(objects=objects)
    manager = type(
        "Manager",
        (),
        {
            "_lock_task_for_mutation": AsyncMock(return_value=row),
            "set_sources": AsyncMock(),
            "validate_cron": staticmethod(lambda value: True),
            "requires_content_dates": staticmethod(lambda value: value in {"collect", "analyze"}),
            "parse_date": staticmethod(lambda value: date.fromisoformat(value) if value else None),
            "build_selection_payload": staticmethod(lambda **kw: dict(kw.get("existing") or {})),
            "build_dates_payload": staticmethod(lambda *a, **kw: {}),
        },
    )
    imports = {
        "app.services.task_plans": plans,
        "app.services.collection.intent": intent,
        "app.core.database": NS(new_session=Session),
        "app.core.permissions": NS(service_permission_scope=lambda *a: nullcontext()),
        "app.core.tenant_context": NS(tenant_scope=lambda *a: nullcontext(), current_tenant_id=lambda: 2),
        "app.models": NS(AgentTask=model, Tenant=NS(objects=NS(get=AsyncMock(return_value=NS(timezone="UTC"))))),
        "app.models.managers.agent_task_manager": NS(AgentTaskManager=manager),
        "app.models.managers.tenant_manager": NS(tenants=NS(get=AsyncMock(return_value=NS(timezone="UTC")))),
        "app.services.monitoring.selection": NS(SelectionError=ValueError),
        "app.tasks.cron": NS(next_run_at=lambda cron, zone: NS(cron=cron, zone=zone), resolve_tz=lambda *a: "UTC"),
    }
    if surface == "api":
        namespace = {
            "router": NS(patch=lambda *a, **kw: lambda f: f),
            "Depends": lambda *a: None,
            "require_model_perm": lambda *a: None,
            "ActionType": NS(UPDATE="update"),
            "HTTPException": fastapi.HTTPException,
            "AgentTask": model,
            "TaskResponse": object,
            "tenant_scope": lambda *a: nullcontext(),
            "service_permission_scope": lambda *a: nullcontext(),
            "SimpleNamespace": NS,
            "datetime": datetime,
            "timezone": timezone,
            "timedelta": timedelta,
            "_selection_payload": lambda request, existing: dict(existing or {}),
            "_collection_payload": lambda payload, raw, **kw: intent.apply_collection(payload, raw, **kw),
            "_task_response": lambda task: task,
        }
        module = source_functions(
            "app/api/v1/endpoints/tasks.py", ["update_task", "_web_api_payload"], imports, namespace
        )
        request = NS(model_fields_set=set(changes), model_dump=lambda **kw: dict(changes))
        asyncio.run(module.update_task(7, request, _user=NS(id=5)))
    elif surface == "chat":
        namespace = {
            "tool": lambda **kw: lambda f: f,
            "_MAX_PG_INTEGER": 2_147_483_647,
            "_get_job_types": lambda: ("collect", "analyze", "digest", "prune", "learn", "reflect"),
        }
        module = source_functions("app/agent/toolset/tasks.py", ["task_update"], imports, namespace)
        aliases = {"cron_expr": "cron", "agent_scenario_id": "scenario_id"}
        response = asyncio.run(module.task_update("Research", **{aliases.get(k, k): v for k, v in changes.items()}))
        if "error" in response:
            raise ValueError(response["error"])
    else:
        imports["cli._tenant"] = NS(
            resolve_tenant_id=AsyncMock(return_value=2), tenant_label=AsyncMock(return_value="test")
        )
        imports["app.jobs.handlers"] = NS(
            HANDLERS=dict.fromkeys(("collect", "analyze", "digest", "prune", "learn", "reflect"))
        )
        namespace = {
            "task_app": NS(command=lambda *a, **kw: lambda f: f),
            "typer": typer,
            "_run_platform": asyncio.run,
            "nullcontext": nullcontext,
            "_split_ints": lambda text: [int(x) for x in text.split(",") if x],
            "_check_task_sources": AsyncMock(),
        }
        module = source_functions("cli/commands/tasks.py", ["task_update"], imports, namespace, package="cli.commands")
        import inspect
        import json

        values = dict.fromkeys(inspect.signature(module.task_update).parameters)
        values["name"] = "Research"
        aliases = {"cron_expr": "cron", "agent_scenario_id": "scenario_id"}
        for key, value in changes.items():
            values[aliases.get(key, key)] = (
                json.dumps(value)
                if key in {"task_plan", "payload"}
                else ",".join(map(str, value)) if key == "source_ids" else value
            )
        module.task_update(**values)
    return writes
