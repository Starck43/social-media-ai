"""Pure/mocked attention contracts. No application import, conftest, DB or ASGI."""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("isolated_attention", ROOT / "app/services/notifications/attention.py")
attention = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = attention
SPEC.loader.exec_module(attention)  # inspected: only stdlib imports at module level
NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


def event(id=1, *, workspace=7, entity="task", entity_id=3, kind="api_error", read=False, when=None):
    return SimpleNamespace(
        id=id,
        tenant_id=workspace,
        related_entity_type=entity,
        related_entity_id=entity_id,
        notification_type=SimpleNamespace(db_value=kind),
        title="secret-token",
        message="raw exception/password",
        is_read=read,
        created_at=when or NOW + timedelta(seconds=1),
    )


def task(id=3, *, workspace=7, state="failed", when=NOW):
    return SimpleNamespace(
        id=id,
        tenant_id=workspace,
        name="Allowed task <script>",
        last_status=state,
        last_error="raw-provider-token",
        last_run_at=when,
    )


def connection(*, platform="vk", state="reauth", action=True, connect=True):
    return SimpleNamespace(
        platform=platform,
        state=state,
        needs_action=action,
        can_connect=connect,
        label="secret-token",
        detail="credential-owner",
    )


class Perms:
    is_superuser_role = False

    def __init__(self, rights=None):
        self.rights = set(
            rights
            if rights is not None
            else {(model, verb) for model in ["notification", "agenttask", "source"] for verb in ["view", "update"]}
        )

    def can(self, model, action):
        return (model, action) in self.rights


class Repository:
    def __init__(self, events=None, tasks=None, connections=None, entities=None):
        self.rows, self.failures, self.statuses = events or [], tasks or [], connections or []
        self.targets = entities or {"agenttask": [*self.failures]}
        self.calls, self.writes, self.broken = [], [], set()

    async def events(self, context, perms):
        self.calls.append(("messages", context.workspace_id))
        if "messages" in self.broken:
            raise RuntimeError("secret-message-error")
        return self.rows[: attention.EVENT_WINDOW]

    async def task_failures(self, context):
        self.calls.append(("tasks", context.workspace_id))
        if "tasks" in self.broken:
            raise RuntimeError("secret-task-error")
        return self.failures[: attention.TASK_WINDOW]

    async def connections(self, context):
        self.calls.append(("connections", context.user_id))
        if "connections" in self.broken:
            raise RuntimeError("secret-vault-error")
        return self.statuses

    async def entities(self, model, ids, context):
        self.calls.append(("entities", model, context.workspace_id, ids))
        if "entities" in self.broken:
            raise RuntimeError("secret-entity-error")
        return [row for row in self.targets.get(model, []) if row.id in ids]

    async def event(self, context, id):
        self.calls.append(("event", context.workspace_id, id))
        return next((n for n in self.rows if n.id == id and n.tenant_id == context.workspace_id), None)

    async def mark_read(self, context, ids):
        self.writes.append((context.workspace_id, ids))
        marked = 0
        for row in self.rows:
            if row.tenant_id == context.workspace_id and row.id in ids and not row.is_read:
                row.is_read = True
                marked += 1
        return marked


def feed(repo, perms=None, *, user=101, workspace=7):
    return attention.AttentionFeed(attention.AttentionContext(workspace, user), perms or Perms(), repo)


def test_notification_controller_partial_snapshot_contracts():
    result = subprocess.run(["node", "tests/web_shell_state.cjs"], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize("state,historical", [("failed", False), ("ok", False), ("failed", True)])
async def test_partial_event_exposes_only_authorized_current_task_identity(state, historical):
    row = event(when=NOW - timedelta(seconds=1) if historical else None)
    target = task(state=state)
    repo = Repository(events=[row], tasks=[target])
    repo.broken.add("tasks")
    service = feed(repo)
    snapshot = await service.snapshot()
    assert snapshot["complete"] is False and snapshot["unavailable"] == ["tasks"]
    item = snapshot["notifications"][0]
    assert item["kind"] == "notification" and item["task_id"] == target.id
    key = service.task_item(target)["key"] if state == "failed" else None
    assert item["current_task_key"] == key
    assert item["failure_key"] == (key if state == "failed" and not historical else None)
    assert "secret-token" not in json.dumps(snapshot) and repo.writes == []
    denied = await feed(repo, Perms({("notification", "view")})).snapshot()
    assert denied["notifications"] == []


@pytest.mark.asyncio
async def test_same_authorized_snapshot_dedups_current_failure_and_shared_events():
    repo = Repository(events=[event(1), event(2)], tasks=[task()], connections=[connection()])
    service = feed(repo)
    snapshot = await service.snapshot()
    assert snapshot["count"] == len(snapshot["notifications"]) == 2
    assert [n["kind"] for n in snapshot["notifications"]] == ["connection", "task"]
    assert snapshot["context"] == {"workspace_id": 7, "user_id": 101}
    assert snapshot["complete"] is True
    assert repo.writes == []  # GET/poll is a projection, never persistence
    assert snapshot["notifications"][1]["read_id"] == 1
    assert snapshot["notifications"][1]["backing_event"] == {
        "key": "notification:7:1",
        "created_at": attention.timestamp(NOW + timedelta(seconds=1)),
        "failure_key": snapshot["notifications"][1]["key"],
    }
    assert "secret" not in json.dumps(snapshot) and "raw-provider-token" not in json.dumps(snapshot)
    read_only = await feed(
        repo,
        Perms({("notification", "view"), ("agenttask", "view")}),
    ).snapshot()
    assert read_only["notifications"][1]["read_id"] is None
    assert read_only["notifications"][1]["backing_event"]["key"] == "notification:7:1"
    assert await service.read_one(1)
    after = await service.snapshot()
    assert after["count"] == 2  # reading is not resolving
    assert after["notifications"][1]["read_id"] == 2
    assert await service.read_window() == 1
    after = await service.snapshot()
    assert after["count"] == 2 and after["notifications"][1]["read_id"] is None
    repo.failures[0].last_status = "ok"
    repo.statuses.clear()
    assert (await service.snapshot())["count"] == 0  # verified source clears signal


@pytest.mark.asyncio
async def test_historical_or_partial_failure_event_is_not_a_current_problem():
    successful = task(state="ok")
    repo = Repository(events=[event(1)], entities={"agenttask": [successful]})
    result = await feed(repo).snapshot()
    assert result["notifications"][0]["kind"] == "notification"
    current = task(when=NOW + timedelta(days=1))
    repo.failures = [current]
    repo.targets = {"agenttask": [current]}
    assert (await feed(repo).snapshot())["count"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entity,entity_id,workspace", [("source", 5, 7), ("unknown", 3, 7), ("task", None, 7), ("task", 3, 8)]
)
async def test_forbidden_missing_unknown_and_foreign_events_cannot_leak(entity, entity_id, workspace):
    repo = Repository(events=[event(entity=entity, entity_id=entity_id, workspace=workspace)], tasks=[task()])
    service = feed(repo, Perms({("notification", "view"), ("notification", "update")}))
    result = await service.snapshot()
    assert result["count"] == 0 and result["has_more"] is False
    assert not await service.read_one(1)
    assert repo.writes == []
    assert not any(call[0] in {"tasks", "entities"} for call in repo.calls)


@pytest.mark.asyncio
async def test_allowed_entity_names_only_and_no_raw_event_content():
    source = SimpleNamespace(id=5, tenant_id=7, name="Authorized <img>")
    repo = Repository(events=[event(entity="source", entity_id=5)], entities={"source": [source]})
    result = await feed(repo).snapshot()
    item = result["notifications"][0]
    assert "Authorized <img>" in item["title"]
    assert item["destination"] == "/app/sources"
    assert "secret-token" not in json.dumps(result)
    source.tenant_id = 8
    assert (await feed(repo).snapshot())["count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,action,connect,expected",
    [
        ("reauth", True, True, 1),
        ("missing", True, True, 1),
        ("connected", False, True, 0),
        ("renewable", False, True, 0),
        ("unconfigured", False, False, 0),
        ("unknown", True, True, 0),
        ("reauth", True, False, 0),
    ],
)
async def test_connection_states_are_caller_only_and_actionable(state, action, connect, expected):
    repo = Repository(connections=[connection(state=state, action=action, connect=connect)])
    result = await feed(repo, Perms(set()), user=202).snapshot()
    assert result["count"] == expected
    assert repo.calls == [("connections", 202)]
    if expected:
        item = result["notifications"][0]
        assert item["scope"] == "personal" and ":202:" in item["key"] and item["read_id"] is None
        assert item["destination"] == "/app/settings?tab=connections"
        assert "credential-owner" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", ["tasks", "messages", "connections", "entities"])
async def test_source_failure_is_unknown_not_healthy_and_siblings_survive(broken):
    repo = Repository(events=[event()], tasks=[task()], connections=[connection()])
    repo.broken.add(broken)
    result = await feed(repo).snapshot()
    assert result["complete"] is False
    assert result["unavailable"]
    assert result["count"] == len(result["notifications"]) >= 1
    assert "secret" not in json.dumps(result)
    assert repo.writes == []


@pytest.mark.asyncio
async def test_revoked_model_or_read_right_is_rechecked_without_hidden_query_or_write():
    repo = Repository(events=[event()], tasks=[task()])
    perms = Perms()
    service = feed(repo, perms)
    assert (await service.snapshot())["count"] == 1
    perms.rights.remove(("agenttask", "view"))
    repo.calls.clear()
    assert (await service.snapshot())["count"] == 0
    assert not await service.read_one(1)
    assert not any(call[0] == "entities" for call in repo.calls)
    perms.rights.add(("agenttask", "view"))
    perms.rights.remove(("notification", "update"))
    assert (await service.snapshot())["notifications"][0]["read_id"] is None
    assert not await service.read_one(1) and await service.read_window() is None
    assert repo.writes == []


@pytest.mark.asyncio
async def test_limited_count_is_list_count_and_has_more_never_counts_denied_rows():
    tasks = [task(id=i) for i in range(1, 5)]
    repo = Repository(tasks=tasks, events=[event(100, entity="unknown")])
    result = await feed(repo).snapshot(limit=2)
    assert result["count"] == 2 and len(result["notifications"]) == 2 and result["has_more"] is True
    result = await feed(repo, Perms({("notification", "view")})).snapshot(limit=2)
    assert result["count"] == 0 and result["has_more"] is False


@pytest.mark.asyncio
async def test_read_all_only_marks_permitted_workspace_events_not_signals_or_foreign():
    repo = Repository(
        events=[event(1, entity=None), event(2, workspace=8, entity=None), event(3, entity="source", entity_id=99)],
        connections=[connection()],
    )
    assert await feed(repo).read_window() == 1
    assert repo.writes == [(7, [1])]
    assert repo.rows[1].is_read is False and repo.rows[2].is_read is False
    assert (await feed(repo).snapshot())["notifications"][0]["kind"] == "connection"


def route_functions():
    tree = ast.parse((ROOT / "app/web/notifications.py").read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for node in nodes:
        node.decorator_list = []
    scope = dict(
        AttentionContext=attention.AttentionContext,
        AttentionFeed=attention.AttentionFeed,
        positive_id=attention.positive_id,
        JSONResponse=JSONResponse,
        Form=lambda value, **kwargs: value,
        ensure_csrf=lambda request, token: token == "csrf-fixture",
    )
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(
                    body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes],
                    type_ignores=[],
                )
            ),
            "isolated notification route functions",
            "exec",
        ),
        scope,
    )
    return scope


def request(*, workspace=7, user=101, member_user=None, active=True, headers=None):
    return SimpleNamespace(
        headers=headers or {},
        state=SimpleNamespace(
            tenant_id=workspace,
            web_user=SimpleNamespace(id=user, is_active=True),
            memberships=[
                SimpleNamespace(
                    tenant_id=workspace,
                    user_id=user if member_user is None else member_user,
                    channel="web",
                    is_active=active,
                )
            ],
            web_perms=Perms(),
        ),
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(active=False),
        dict(member_user=202),
        dict(headers={"X-App-Workspace": "8"}),
        dict(headers={"X-App-User": "202"}),
        dict(workspace=True),
        dict(user=None),
    ],
)
def test_endpoint_context_rejects_revocation_foreign_membership_and_changed_session(kwargs):
    assert route_functions()["_context"](request(**kwargs)) is None


def test_read_context_requires_csrf_and_originating_workspace():
    scope = route_functions()
    assert scope["_read_context"](request(), "csrf-fixture", "7") is not None
    assert scope["_read_context"](request(), "bad", "7") is None
    assert scope["_read_context"](request(), "csrf-fixture", "8") is None
    assert scope["_read_context"](request(), "csrf-fixture", "") is None


@pytest.mark.asyncio
async def test_route_blocks_stale_read_before_any_repository_access():
    scope = route_functions()
    scope["AttentionFeed"] = lambda *args: pytest.fail("must not create a feed for stale context")
    response = await scope["notification_read_one"](request(workspace=8), 1, "csrf-fixture", "7")
    assert response.status_code == 403 and json.loads(response.body)["reset"] is True


def test_template_unifies_connection_area_and_never_links_raw_remote_destinations():
    base = (ROOT / "app/web/templates/web/base.html").read_text()
    template = (ROOT / "app/web/templates/web/_shell_notifications.html").read_text()
    assert "Подключения требуют внимания" not in base
    assert 'include "web/_shell_notifications.html"' in base
    assert 'x-show="n.read_id"' in template and ':key="n.key"' in template
    assert 'x-text="n.message"' in template and "x-html" not in template


def orm_methods():
    """Compile the actual gateway bodies with manager/model boundaries mocked."""
    tree = ast.parse((ROOT / "app/services/notifications/attention.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AttentionRepository")
    for node in cls.body:
        if isinstance(node, ast.AsyncFunctionDef):
            node.body = [n for n in node.body if not isinstance(n, ast.ImportFrom)]
    calls = []

    class Column:
        def is_(self, value):
            return self

        def in_(self, value):
            calls.append(("entity-types", value))
            return self

        def __or__(self, other):
            return self

        def desc(self):
            return self

    class Query:
        def filter(self, *args, **kwargs):
            calls.append(("filter", kwargs))
            return self

        def order_by(self, *args):
            return self

        def limit(self, value):
            calls.append(("limit", value))
            return self

        def __await__(self):
            async def result():
                return []

            return result().__await__()

        async def first(self):
            return None

        async def update(self, **kwargs):
            calls.append(("update", kwargs))
            return 1

    model = SimpleNamespace(
        objects=Query(), related_entity_type=Column(), created_at=Column(), id=Column(), last_run_at=Column()
    )
    scope = {
        name: model
        for name in ("Notification", "AgentTask", "AIAnalytics", "AgentScenario", "DigestRun", "Platform", "Source")
    }
    scope.update(EVENT_WINDOW=100, TASK_WINDOW=50, ENTITY_MODELS=attention.ENTITY_MODELS, can=attention.can)
    exec(
        compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), "mocked attention gateway", "exec"),
        scope,
    )
    return scope["AttentionRepository"](), calls


@pytest.mark.asyncio
async def test_actual_event_and_task_query_bodies_are_bounded_and_explicitly_workspace_scoped():
    repo, calls = orm_methods()
    context = attention.AttentionContext(7, 101)
    await repo.events(context, Perms({("source", "view")}))
    assert ("entity-types", ["source"]) in calls
    assert ("filter", {"tenant_id": 7, "is_read": False}) in calls
    assert ("limit", 100) in calls
    calls.clear()
    await repo.task_failures(context)
    assert calls == [("filter", {"tenant_id": 7, "last_status": "failed"}), ("limit", 50)]


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["agenttask", "source", "aianalytics", "digestrun", "agentscenario"])
async def test_actual_entity_lookup_bodies_bind_workspace_and_ids(model):
    repo, calls = orm_methods()
    await repo.entities(model, {3}, attention.AttentionContext(7, 101))
    assert calls == [("filter", {"id__in": [3], "tenant_id": 7}), ("limit", 100)]


@pytest.mark.asyncio
async def test_actual_write_body_only_changes_shared_read_flag_for_bound_ids_and_workspace():
    repo, calls = orm_methods()
    assert await repo.mark_read(attention.AttentionContext(7, 101), [1, 2]) == 1
    assert calls == [("filter", {"tenant_id": 7, "id__in": [1, 2], "is_read": False}), ("update", {"is_read": True})]


@pytest.mark.asyncio
@pytest.mark.parametrize("id", [True, -1, 0, None])
async def test_invalid_read_ids_never_query_or_write(id):
    repo = Repository(events=[event()])
    assert not await feed(repo).read_one(id)
    assert repo.calls == [] and repo.writes == []


@pytest.mark.asyncio
async def test_shared_read_is_idempotent_but_already_read_foreign_or_forbidden_ids_still_fail():
    repo = Repository(events=[event(read=True)], tasks=[task()])
    assert await feed(repo).read_one(1)
    assert repo.writes == []
    assert not await feed(repo, Perms({("notification", "view"), ("notification", "update")})).read_one(1)
    repo.rows[0].tenant_id = 8
    assert not await feed(repo).read_one(1)
    assert repo.writes == []


@pytest.mark.asyncio
async def test_shared_read_race_rechecks_committed_read_without_resolving_signal():
    repo = Repository(events=[event()], tasks=[task()])

    async def concurrent_read(context, ids):
        repo.rows[0].is_read = True  # another workspace member's committed acknowledgement
        return 0

    repo.mark_read = concurrent_read
    assert await feed(repo).read_one(1)
    result = await feed(repo).snapshot()
    assert result["count"] == 1 and result["notifications"][0]["kind"] == "task"


@pytest.mark.asyncio
async def test_model_view_is_rechecked_before_emitting_previously_fetched_task_names():
    repo, perms = Repository(tasks=[task()]), Perms()

    async def changed(context):
        perms.rights.remove(("agenttask", "view"))
        return []

    repo.connections = changed
    result = await feed(repo, perms).snapshot()
    assert result["count"] == 0 and "Allowed task" not in json.dumps(result)


def isolated_function(path, name, scope):
    tree = ast.parse((ROOT / path).read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
    node.body = [n for n in node.body if not isinstance(n, ast.ImportFrom)]
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(
                    body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node],
                    type_ignores=[],
                )
            ),
            "isolated metadata lookup",
            "exec",
        ),
        scope,
    )
    return scope[name]


@pytest.mark.asyncio
async def test_personal_status_metadata_query_selects_only_status_columns_and_one_latest_row():
    calls = []

    class Column:
        def __init__(self, name):
            self.name = name

        def desc(self):
            return self

    model = SimpleNamespace(**{n: Column(n) for n in ("updated_at", "created_at", "id", "expires_at", "meta")})

    class Query:
        def filter(self, **kwargs):
            calls.append(("filter", kwargs))
            return self

        def order_by(self, *clauses):
            calls.append(("order", [n.name for n in clauses]))
            return self

        def values(self, *columns):
            calls.append(("columns", [n.name for n in columns]))
            return self

        async def first_row(self):
            calls.append(("one-row",))
            return SimpleNamespace(meta={}, expires_at=None)

    scope = dict(UserCredential=model, func=SimpleNamespace(coalesce=lambda *args: Column("coalesced_time")))
    function = isolated_function("app/models/managers/user_credential_manager.py", "newest_status_metadata", scope)
    await function(Query(), user_id=101, platform="vk", kind="user_token")
    assert calls == [
        ("filter", {"user_id": 101, "platform": "vk", "kind": "user_token", "is_active": True}),
        ("order", ["coalesced_time", "id"]),
        ("columns", ["expires_at", "meta", "created_at", "updated_at"]),
        ("one-row",),
    ]
    # Pin the real manager API's one-row cap, without loading the ORM/app.
    tree = ast.parse((ROOT / "app/models/managers/base_manager.py").read_text())
    first = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "first_row")
    assert any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "limit"
        and isinstance(n.args[0], ast.Constant)
        and n.args[0].value == 1
        for n in ast.walk(first)
    )


@pytest.mark.asyncio
async def test_canonical_connection_status_uses_bounded_caller_metadata_not_secret_or_bulk_vault_reads():
    calls = []

    async def metadata(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(meta={}, expires_at=None)

    scope = {"user_credentials": SimpleNamespace(newest_status_metadata=metadata)}
    function = isolated_function("app/services/social/connections.py", "_vault_row", scope)
    assert await function(None, "vk", "user_token") is None
    assert calls == []
    await function(101, "vk", "user_token")
    assert calls == [{"user_id": 101, "platform": "vk", "kind": "user_token"}]
