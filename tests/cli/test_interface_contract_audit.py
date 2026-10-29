"""Offline interface-contract audit probes refreshed from the historical 14 probes.

Every probe reads the current working-tree source or executes an isolated source
slice with declared doubles. The suite imports no application package: no manager,
model, settings, database, provider, worker or network path is touched.
Strict xfails mark verified unresolved contracts and their reasons state the
limitation itself, never a suspected vulnerability.
"""

from __future__ import annotations

import ast
import asyncio
import builtins
import importlib.util
import itertools
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import ANY, AsyncMock, Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
_HELPER_SPEC = importlib.util.spec_from_file_location(
    "_interface_audit_source_import_isolation", ROOT / "tests" / "source_import_isolation.py"
)
if _HELPER_SPEC is None or _HELPER_SPEC.loader is None:
    raise ImportError("Cannot load source isolation helper")
_HELPER = importlib.util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_HELPER)
load_isolated_source = _HELPER.load_isolated_source
CLI_LLM = ROOT / "cli" / "commands" / "llm.py"
RUNTIME = ROOT / "app" / "agent" / "runtime.py"
TASKS = ROOT / "app" / "agent" / "toolset" / "tasks.py"
TOOLS = ROOT / "app" / "agent" / "tools.py"
TOOLSET = ROOT / "app" / "agent" / "toolset"
ENTRY = ROOT / "app" / "api" / "v1" / "entry.py"
CREDENTIALS = ROOT / "app" / "api" / "v1" / "endpoints" / "credentials.py"
PROVIDER = ROOT / "app" / "models" / "llm" / "llm_provider.py"
MODEL_SCHEMA = ROOT / "app" / "schemas" / "llm_model.py"

_LOAD_COUNTER = itertools.count()


def _tree(path):
    return ast.parse(Path(path).read_text(encoding="utf-8"))


def _function(tree, name):
    node = next(
        (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name),
        None,
    )
    assert node is not None, f"missing function {name}"
    return node


def _app_modules():
    return {name for name in sys.modules if name == "app" or name.startswith("app.")}


@contextmanager
def _no_app_imports():
    """Fail if the block loads any application module beyond what was already present."""
    before = _app_modules()
    yield
    added = _app_modules() - before
    assert not added, f"probe loaded application modules: {sorted(added)}"


def _load(path, *, imports=None, module_imports=None):
    name = f"_audit_isolated_{next(_LOAD_COUNTER)}"
    return load_isolated_source(name, path, imports, module_imports=module_imports)


def _tool_declarations():
    found = []
    for path in sorted(TOOLSET.glob("*.py")):
        for node in _tree(path).body:
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Name)
                    and decorator.func.id == "tool"
                ):
                    found.append((path, node, {kw.arg: kw.value for kw in decorator.keywords}))
    return found


def _tool_contract(path, name):
    for _, node, keywords in _tool_declarations():
        if node.name == name:
            return node, keywords
    raise AssertionError(f"missing tool handler {name} in {path}")


def _properties(decorator_keywords):
    parameters = decorator_keywords["parameters"]
    assert isinstance(parameters, ast.Dict)
    props = next(
        value
        for key, value in zip(parameters.keys, parameters.values)
        if key is not None and ast.literal_eval(key) == "properties"
    )
    assert isinstance(props, ast.Dict)
    return props


# ──────────────────────────────────────────────────────────────────────────────
# Isolated source loading
# ──────────────────────────────────────────────────────────────────────────────


class _CliHarness:
    """Load `cli/commands/llm.py` with declared stdlib-free doubles only."""

    def __init__(self, *, models, probe=None, tenant_scope=None):
        self.exit = type("Exit", (Exception,), {})
        self.console_print = Mock()
        self.table = NS(add_column=Mock(), add_row=Mock())
        self.table_factory = Mock(return_value=self.table)
        self.rich_print = Mock()
        self.probe = probe or NS(probe_mock=Mock(return_value={"ok": True, "content": "fixture"}))
        self.actor = NS(id=7, is_active=True)

        def current_catalog_owner_id():
            if self.actor is None or not self.actor.is_active:
                raise PermissionError("An active personal catalog owner is required")
            return self.actor.id

        def require_actor(owner_id):
            if self.actor is None or not self.actor.is_active or self.actor.id != owner_id:
                raise PermissionError("Personal catalog access denied")
            return self.actor

        def assert_catalog_model(model):
            if (
                getattr(model, "owner_user_id", None) != current_catalog_owner_id()
                or model.provider is None
                or getattr(model.provider, "owner_user_id", None) != self.actor.id
            ):
                raise PermissionError("Model is unavailable in this personal catalog")

        async def catalog_metadata(*, user_id):
            require_actor(user_id)
            providers = []
            for provider in await models.LLMProvider.objects.order_by(models.LLMProvider.id):
                if getattr(provider, "owner_user_id", user_id) != user_id:
                    continue
                own_models = [
                    row
                    for row in models.LLMModel.objects.rows
                    if getattr(row, "owner_user_id", user_id) == user_id and row.provider_id == provider.id
                ]
                providers.append(
                    {
                        "id": provider.id,
                        "name": provider.name,
                        "api_format": provider.api_format,
                        "base_url": provider.base_url,
                        "is_active": provider.is_active,
                        "models": own_models,
                    }
                )
            return {"providers": providers}

        self.catalog = NS(
            current_catalog_owner_id=current_catalog_owner_id,
            require_actor=require_actor,
            assert_catalog_model=assert_catalog_model,
            catalog_metadata=catalog_metadata,
        )
        typer = NS(
            Typer=lambda **kwargs: NS(command=lambda *args, **kwargs: (lambda fn: fn)),
            Option=lambda *args, **kwargs: None,
            Argument=lambda *args, **kwargs: None,
            Exit=self.exit,
        )
        self.module = _load(
            CLI_LLM,
            imports={
                "rich": NS(print=self.rich_print),
                "rich.table": NS(Table=self.table_factory),
                "rich.console": NS(Console=lambda: NS(print=self.console_print)),
                "app.models": models,
                "app.services.ai.model_probe": self.probe,
                "app.core.tenant_context": NS(tenant_scope=tenant_scope or _tenant_scope),
                "app.core.personal_catalog_context": NS(
                    current_catalog_owner_id=self.catalog.current_catalog_owner_id,
                    assert_catalog_model=self.catalog.assert_catalog_model,
                ),
                "app.services.ai.personal_catalog": NS(
                    require_actor=self.catalog.require_actor,
                    catalog_metadata=self.catalog.catalog_metadata,
                ),
            },
            module_imports={"typer": typer},
        )


@contextmanager
def _tenant_scope(**kwargs):
    yield


@contextmanager
def _forbidden_scope(**kwargs):
    raise AssertionError("tenant scope was entered")
    yield


def _models(*, providers=None, models=None, model=None):
    providers = providers or []
    rows = list(models or [])
    if model is not None:
        if not hasattr(model, "id"):
            model.id = 1
        if not hasattr(model, "owner_user_id"):
            model.owner_user_id = 7
        if model.provider is not None and not hasattr(model.provider, "owner_user_id"):
            model.provider.owner_user_id = 7
        rows.append(model)
    for provider in providers:
        if not hasattr(provider, "owner_user_id"):
            provider.owner_user_id = 7
    for row in rows:
        if not hasattr(row, "owner_user_id"):
            row.owner_user_id = 7
        if getattr(row, "provider", None) is not None and not hasattr(row.provider, "owner_user_id"):
            row.provider.owner_user_id = 7

    class Query:
        def __init__(self, selected=None):
            self.selected = selected

        async def get(self, **filters):
            return next(
                (row for row in rows if all(getattr(row, key) == value for key, value in filters.items())),
                None,
            )

        async def order_by(self, *_):
            return [
                row for row in rows if all(getattr(row, key) == value for key, value in (self.selected or {}).items())
            ]

        def filter(self, **filters):
            return Query(filters)

    objects = NS(
        rows=rows,
        select_related=Mock(return_value=Query()),
        filter=Mock(side_effect=lambda **filters: Query(filters)),
    )
    return NS(
        LLMProvider=NS(id=1, objects=NS(order_by=AsyncMock(return_value=providers))),
        LLMModel=NS(id=2, objects=objects),
    )


def _task_module():
    """Load tasks.py with its module-level and function-local imports declared."""
    from contextlib import asynccontextmanager

    task = NS(
        id=1,
        name="fixture",
        tenant_id=7,
        job_type="digest",
        cron_expr="0 9 * * *",
        agent_scenario_id=42,
        payload={"brands": ["fixture"]},
        is_active=True,
        next_run_at=None,
        last_status=None,
    )
    task.id = 1
    manager = NS(
        get=AsyncMock(return_value=task),
        update_by_id=AsyncMock(),
        add_sources=AsyncMock(return_value=0),
        set_sources=AsyncMock(return_value=0),
        _lock_task_for_mutation=AsyncMock(return_value=task),
        order_by=AsyncMock(return_value=[task]),
    )

    class Session:
        @asynccontextmanager
        async def begin(self):
            yield self

        async def close(self):
            return None

    session = Session()

    class TaskManager:
        validate_cron = staticmethod(lambda cron: True)
        requires_content_dates = staticmethod(lambda job_type: False)
        parse_date = staticmethod(lambda value: value)
        build_dates_payload = staticmethod(
            lambda start, end, force_refresh=False: {"cli_dates": {"start_date": start, "end_date": end}}
        )

        @staticmethod
        def build_selection_payload(*, tracked_users, keywords, keywords_match, existing):
            return dict(existing)

        async def add_sources(self, task_id, source_ids, session=None):
            await manager.add_sources(task_id, source_ids, session=session)

        async def set_sources(self, task_id, source_ids, session=None):
            await manager.set_sources(task_id, source_ids, session=session)

        async def _lock_task_for_mutation(self, session, task_id):
            return await manager._lock_task_for_mutation(session, task_id)

    class SelectionError(Exception):
        pass

    class CollectionIntentError(Exception):
        pass

    module = _load(
        TASKS,
        imports={
            "app.agent.tools": NS(tool=lambda **kwargs: (lambda fn: fn)),
            "app.jobs.handlers": NS(HANDLERS={"digest": object()}),
            "app.core.tenant_context": NS(current_tenant_id=lambda: 7),
            "app.core.database": NS(new_session=lambda: session),
            "app.core.config": NS(settings=NS(SCHEDULER_TIMEZONE="UTC")),
            "app.models": NS(
                AgentTask=NS(id=1, objects=manager),
                Tenant=NS(objects=NS(get=AsyncMock(return_value=NS()))),
                Source=NS(objects=NS(filter=AsyncMock(return_value=[]))),
            ),
            "app.models.managers.agent_task_manager": NS(AgentTaskManager=TaskManager),
            "app.models.managers.job_manager": NS(JobManager=NS(lock_source_admission=AsyncMock())),
            "app.tasks.cron": NS(
                next_run_at=lambda cron, tz: "next-fixture",
                resolve_tz=lambda tenant: "UTC",
            ),
            "app.services.monitoring.selection": NS(SelectionError=SelectionError),
            "app.services.collection.intent": NS(
                CollectionIntentError=CollectionIntentError,
                apply_collection=lambda payload, collection, job_type=None: payload,
                collection_error_message=str,
            ),
        },
    )
    return module, manager, task


def _tools_module():
    class PermissionDeniedError(Exception):
        pass

    return _load(
        TOOLS,
        imports={
            "app.agent": NS(toolset=NS()),
            "app.agent.confirmation": NS(consume_tool_confirmation=lambda spec, arguments: True),
            "app.core.permissions": NS(
                PermissionDeniedError=PermissionDeniedError,
                get_current_user=lambda: None,
                has_permission_by_codename=lambda user, permission: True,
            ),
        },
    )


def _slice(path, names, namespace, imports):
    """Compile exact top-level definitions; redirect only declared imports locally."""
    selected = []
    for node in _tree(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in names:
            selected.append(node)
    assert {node.name for node in selected} == set(names), f"missing source definitions in {path}"

    normal_import = builtins.__import__

    def isolated_import(import_name, globals=None, locals=None, fromlist=(), level=0):
        assert not level, f"unexpected relative import {import_name}"
        if import_name in imports:
            if not fromlist:
                raise ImportError("declared doubles require an explicit from-import")
            return imports[import_name]
        if import_name == "app" or import_name.startswith("app."):
            raise AssertionError(f"undeclared application import: {import_name}")
        return normal_import(import_name, globals, locals, fromlist, level)

    module = ast.Module(
        body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            *selected,
        ],
        type_ignores=[],
    )
    state = dict(namespace)
    state["__builtins__"] = dict(vars(builtins), __import__=isolated_import)
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), state)
    return state


def _runtime_scope():
    chosen = NS(id=2, name="chosen-fixture")
    query = NS(get=AsyncMock(return_value=chosen))
    selected = Mock(return_value=query)
    resolver = AsyncMock()
    llm_models = NS(objects=NS(select_related=selected, resolve_default_model=resolver))
    tenant = NS(
        agent_model_id=2,
        agent_max_tokens=None,
        agent_temperature=None,
        plan_limits=lambda: {"model_types": ["text"]},
    )
    fallback = AsyncMock(return_value={"content": "fixture"})
    eligible = Mock(return_value=True)
    scope = _slice(
        RUNTIME,
        {"_chat", "_preferred_agent_model"},
        {
            "settings": NS(AGENT_MAX_TOKENS=100, AGENT_TEMPERATURE=0.0),
            "logger": NS(info=Mock(), warning=Mock()),
        },
        {
            "app.core.tenant_context": NS(current_tenant_id=lambda: 7),
            "app.models.managers.tenant_manager": NS(tenants=NS(get=AsyncMock(return_value=tenant))),
            "app.models": NS(LLMModel=llm_models),
            "app.core.personal_catalog_context": NS(current_catalog_owner_id=lambda: 7),
            "app.services.ai.personal_catalog": NS(
                eligible_chat_model=eligible,
            ),
            "app.services.ai.llm_client": NS(chat_with_fallback=fallback),
        },
    )
    return scope, chosen, query, resolver, fallback, eligible


# ──────────────────────────────────────────────────────────────────────────────
# Passing contracts
# ──────────────────────────────────────────────────────────────────────────────


def test_isolated_sources_never_import_application_modules():
    with _no_app_imports():
        _CliHarness(models=_models())
        _task_module()
        _tools_module()
        _runtime_scope()


def test_tool_names_and_schema_fields_match_handlers():
    names = []
    for path, node, keywords in _tool_declarations():
        name = ast.literal_eval(keywords["name"])
        names.append(name)
        assert "required_permission" in keywords, (path.name, node.name)
        explicit = {ast.literal_eval(key) for key in _properties(keywords).keys if key is not None}
        arguments = {arg.arg for arg in node.args.args + node.args.kwonlyargs}
        assert explicit <= arguments, (node.name, explicit - arguments)
    assert len(names) > 20
    assert len(names) == len(set(names)), "duplicate registered tool names"


def test_llm_provider_orm_drops_removed_default_flag():
    provider = next(
        node for node in _tree(PROVIDER).body if isinstance(node, ast.ClassDef) and node.name == "LLMProvider"
    )
    fields = {
        node.target.id
        for node in provider.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert "is_default" not in fields

    migration = (
        ROOT / "scripts" / "migrations" / "legacy_definitions" / "0091_drop_llm_provider_default_flag.py"
    ).read_text()
    assert 'COLUMN = "is_default"' in migration

    provider_list = ast.unparse(_function(_tree(CLI_LLM), "provider_list"))
    assert "is_default" not in provider_list


def test_provider_list_renders_current_provider_without_default_field():
    provider = NS(
        id=1,
        name="fixture",
        api_format="openai",
        base_url="https://fixture.invalid",
        is_active=True,
    )
    harness = _CliHarness(models=_models(providers=[provider], models=[NS(provider_id=1)]))
    with _no_app_imports():
        harness.module.provider_list()
    assert harness.console_print.call_count == 1
    assert harness.table.add_row.call_count == 1
    row = harness.table.add_row.call_args.args
    assert row[0] == "1"
    assert row[3] == "https://fixture.invalid"
    assert row[4] == "yes"
    assert row[5] == "1"


def test_cli_model_test_mock_uses_shared_probe_without_llm_client():
    model = NS(name="fixture-model", provider=NS(name="fixture-provider"))
    harness = _CliHarness(
        models=_models(model=model),
        probe=NS(probe_mock=Mock(return_value={"ok": True, "content": "fixture content", "cost": 0})),
    )
    body = ast.unparse(_function(_tree(CLI_LLM), "model_test"))
    assert "probe_mock" in body
    assert "LLMClient" not in body

    with _no_app_imports():
        harness.module.model_test(1, prompt="fixture", mock=True)

    report = harness.console_print.call_args.args[0]
    assert isinstance(report, str)
    assert '"mode": "mock"' in report
    assert '"tokens_spent": 0' in report
    assert "fixture content" in report
    harness.probe.probe_mock.assert_called_once_with(model, "fixture")


def test_cli_real_model_probe_fails_closed_before_query_and_tenant_scope():
    class UntouchableModel:
        @property
        def objects(self):
            raise AssertionError("real CLI probe reached the model query")

    probe = NS(probe_mock=Mock(side_effect=AssertionError("probe executed")))
    harness = _CliHarness(models=NS(LLMModel=UntouchableModel()), probe=probe, tenant_scope=_forbidden_scope)
    with _no_app_imports(), pytest.raises(harness.exit):
        harness.module.model_test(1, prompt="fixture", mock=False)
    probe.probe_mock.assert_not_called()

    messages = " ".join(str(call.args[0]) for call in harness.rich_print.call_args_list)
    assert "authenticated payer" in messages
    assert "unavailable" in messages


def test_cli_catalog_commands_require_actor_and_mock_hides_foreign_models():
    provider = NS(
        id=1,
        name="fixture",
        api_format="openai",
        base_url="https://fixture.invalid",
        is_active=True,
    )
    models = _models(providers=[provider], model=NS(id=9, owner_user_id=9, provider=NS(owner_user_id=9)))
    harness = _CliHarness(models=models)
    harness.actor = None
    with _no_app_imports(), pytest.raises(harness.exit):
        harness.module.provider_list()
    models.LLMProvider.objects.order_by.assert_not_awaited()

    harness.actor = NS(id=7, is_active=True)
    with _no_app_imports(), pytest.raises(harness.exit):
        harness.module.model_test(9, prompt="fixture", mock=True)
    harness.probe.probe_mock.assert_not_called()


def test_workspace_model_override_reaches_chat_selection():
    scope, chosen, query, resolver, fallback, eligible = _runtime_scope()
    with _no_app_imports():
        asyncio.run(scope["_chat"]([], []))
    assert resolver.await_count == 0
    assert query.get.await_args.kwargs == {"id": 2, "is_active": True}
    assert eligible.call_args.kwargs == {"user_id": 7, "allowed_types": ["text"]}
    assert fallback.await_args.kwargs["preferred_model"] is chosen


def test_llm_model_test_view_scope_reaches_paid_probe_cost_consent_debt():
    """Cost/consent debt: `llm.model.view` can spend provider tokens.

    The tool is declared with a read permission but executes a real chat request
    (billed, provider traffic) without a confirmation or consent gate. This is a
    recorded debt, not a demonstrated permission bypass; the probe never calls it.
    """
    node, keywords = _tool_contract(ROOT / "app" / "agent" / "toolset" / "llm.py", "llm_model_test")
    assert ast.literal_eval(keywords["required_permission"]) == "llm.model.view"
    assert not keywords.get("confirm")
    body = ast.unparse(node)
    assert "LLMClientFactory.create" in body
    assert "client.chat" in body


# ──────────────────────────────────────────────────────────────────────────────
# Verified unresolved contracts (strict xfails)
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.xfail(
    strict=True, reason="LLM HTTP model_type pattern rejects capability sets such as video or text,image"
)
def test_llm_model_schema_accepts_capability_sets():
    base = next(
        node for node in _tree(MODEL_SCHEMA).body if isinstance(node, ast.ClassDef) and node.name == "LLMModelBase"
    )
    field = next(node for node in base.body if isinstance(node, ast.AnnAssign) and node.target.id == "model_type")
    pattern = next(ast.literal_eval(keyword.value) for keyword in field.value.keywords if keyword.arg == "pattern")
    for value in ("video", "text,image", "text,video"):
        assert re.fullmatch(pattern, value) is not None, f"model_type pattern rejects {value!r}"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "LLM model add/list enums declare single types, and llm_models_list writes "
        "filters['model_type'] = model_type for exact equality, so a stored comma-separated "
        "capability set is never matched by a capability filter"
    ),
)
def test_llm_model_tool_enums_accept_capability_sets():
    llm_tool = ROOT / "app" / "agent" / "toolset" / "llm.py"
    problems = []
    for handler in ("llm_model_add", "llm_models_list"):
        _, keywords = _tool_contract(llm_tool, handler)
        properties = _properties(keywords)
        model_type = next(
            value
            for key, value in zip(properties.keys, properties.values)
            if key is not None and ast.literal_eval(key) == "model_type"
        )
        options = ast.literal_eval(model_type)["enum"]
        if "video" not in options:
            problems.append(f"{handler} enum lacks video: {options}")
        if not any("," in str(option) for option in options):
            problems.append(f"{handler} enum lacks capability sets: {options}")

    class _StoredModel:
        def __init__(self, id, name, model_type):
            self.id = id
            self.name = name
            self.model_type = model_type
            self.provider = None

        def __getattr__(self, name):
            return None

    stored = [
        _StoredModel(1, "video-only", "video"),
        _StoredModel(2, "text-set", "text,image"),
    ]

    class _ExactMatchQuery:
        """Emulate exact string equality on the stored model_type column."""

        def filter(self, **filters):
            wanted = filters.get("model_type")
            matched = [model for model in stored if wanted is None or model.model_type == wanted]
            return NS(order_by=AsyncMock(return_value=matched))

    llm_models = NS(objects=NS(select_related=Mock(return_value=_ExactMatchQuery())))
    scope = _slice(
        llm_tool,
        {"llm_models_list"},
        {"_public_model_prices": lambda model: {}},
        {"app.models": NS(LLMModel=llm_models)},
    )
    with _no_app_imports():
        listed = asyncio.run(scope["llm_models_list"](model_type="text"))
    if not any("text" in model.model_type.split(",") for model in listed):
        problems.append("llm_models_list model_type='text' exact filter misses stored capability set 'text,image'")

    assert not problems, "; ".join(problems)


def test_task_list_resolves_agent_task_symbol():
    module = _tree(TASKS)
    task_list = _function(module, "task_list")
    imported = set()
    for node in [*module.body, *ast.walk(task_list)]:
        if isinstance(node, ast.ImportFrom):
            imported.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
    assert "AgentTask" in ast.unparse(task_list)
    assert "AgentTask" in imported
    module, _, task = _task_module()
    with _no_app_imports():
        rows = asyncio.run(module.task_list())
    assert rows == [
        {
            "name": task.name,
            "cron": task.cron_expr,
            "job_type": task.job_type,
            "active": task.is_active,
            "next_run_at": None,
            "last_status": None,
        }
    ]


def _credential_disable_mount():
    entry = _tree(ENTRY)
    mount = next(
        node.value
        for node in entry.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "include_router"
        and ast.unparse(node.value.args[0]) == "credentials.router"
    )
    prefix = next(ast.literal_eval(keyword.value) for keyword in mount.keywords if keyword.arg == "prefix")
    endpoint = next(
        node
        for node in _tree(CREDENTIALS).body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "disable_credential"
    )
    route = ast.literal_eval(endpoint.decorator_list[0].args[0])
    return "/api/v1" + prefix + route


def test_api_docs_records_actual_credential_disable_route():
    mounted = _credential_disable_mount()
    assert mounted == "/api/v1/credentials/credentials/{credential_id}/disable"
    assert mounted in (ROOT / "docs" / "API.md").read_text(encoding="utf-8")


@pytest.mark.xfail(
    strict=True,
    reason="credential endpoint repeats the mounted /credentials prefix in its runtime route",
)
def test_credential_disable_route_uses_canonical_single_segment():
    assert _credential_disable_mount() == "/api/v1/credentials/{credential_id}/disable"


@pytest.mark.parametrize(
    "source_ids",
    [pytest.param([17], id="replace-existing"), pytest.param([], id="explicit-clear")],
)
def test_task_update_source_edit_replaces_links(source_ids):
    module, manager, task = _task_module()
    with _no_app_imports():
        asyncio.run(module.task_update("fixture", source_ids=source_ids))
    manager.set_sources.assert_awaited_once_with(task.id, source_ids, session=ANY)
    manager.add_sources.assert_not_awaited()


@pytest.mark.xfail(
    strict=True,
    reason="call_tool does not validate arguments against the declared JSON schema before dispatch",
)
def test_generic_dispatcher_validates_arguments_against_declared_schema():
    module = _tools_module()

    async def handler(name: str = "default") -> str:
        return "ran"

    module.tool(
        name="probe_required",
        description="offline probe",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    )(handler)

    with _no_app_imports(), pytest.raises((TypeError, ValueError, module.ToolError)):
        asyncio.run(module.call_tool("probe_required", {}))


# ──────────────────────────────────────────────────────────────────────────────
# Task update contracts
# ──────────────────────────────────────────────────────────────────────────────


def test_task_update_partial_payload_preserves_existing_targets():
    module, manager, _ = _task_module()
    with _no_app_imports():
        asyncio.run(module.task_update("fixture", period="week"))
    updates = manager.update_by_id.await_args.kwargs
    assert updates["payload"] == {"brands": ["fixture"], "period": "week"}


def test_task_update_omitted_scenario_binding_is_preserved():
    module, manager, task = _task_module()
    with _no_app_imports():
        asyncio.run(module.task_update("fixture", period="week"))
    updates = manager.update_by_id.await_args.kwargs
    assert "agent_scenario_id" not in updates
    assert updates["job_type"] == "digest"
    assert updates["payload"] == {"brands": ["fixture"], "period": "week"}
    assert task.agent_scenario_id == 42


def test_task_update_persists_requested_cron_expression():
    module, manager, _ = _task_module()
    with _no_app_imports():
        asyncio.run(module.task_update("fixture", cron="0 10 * * *"))
    updates = manager.update_by_id.await_args.kwargs
    assert updates.get("cron_expr") == "0 10 * * *"
    assert updates.get("next_run_at") == "next-fixture"


def test_task_update_explicitly_clears_scenario():
    module, manager, task = _task_module()
    with _no_app_imports():
        asyncio.run(module.task_update("fixture", clear_scenario=True))
    updates = manager.update_by_id.await_args.kwargs
    assert updates.get("agent_scenario_id", ANY) is None
    assert task.agent_scenario_id == 42
