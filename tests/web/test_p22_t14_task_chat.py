"""P22-T14: task conversations are private, allocated once and access-checked.

The suite runs on an isolated PostgreSQL schema. It covers the server-resolved
binding (eligibility, read-without-rows, first-send allocation, convergence) and
the runtime rule that a task session belongs to exactly one actor.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from app.agent.identity import RuntimeIdentity, session_matches_conversation
from app.core.config import settings
from app.core.database import async_session_maker
from app.models import AgentScenario, AgentSession, TaskChatBinding, TenantUser
from app.services import task_execution_plans
from app.services.execution_plan_contracts import map_social_plan
from app.services.planner.canonical_tasks import CreateTask, task_service
from app.services.task_chat import (
    TaskChatDenied,
    task_chat_actor_id,
    task_chat_service,
)
from tests.planner.test_p22_t01_task_identity_db import actor, task_world  # noqa: F401
from tests.support.schema_guard import require_isolated_schema

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


def _social_plan(scenario_id: int) -> str:
    return map_social_plan(
        {
            "task_plan": {
                "source_scope": "discovery",
                "collection": {"queries": ["synthetic topic"]},
                "scenario_id": scenario_id,
                "trigger": {"type": "schedule", "cron": "0 9 * * *"},
                "vk_access": {"mode": "api"},
            },
            "collect_before_analyze": True,
        },
        job_type="analyze",
    ).model_dump_json()


async def _scenario(tenant_id: int, name: str = "Synthetic scenario") -> AgentScenario:
    async with async_session_maker() as session, session.begin():
        scenario = AgentScenario(tenant_id=tenant_id, name=name, base_prompt="Synthetic method")
        session.add(scenario)
        await session.flush()
        return scenario


async def _bound_task(scenario_id: int, title: str = "Synthetic task") -> int:
    async with async_session_maker() as session:
        plan = _social_plan(scenario_id)
    task = await task_service.create(CreateTask(title=title, visibility="workspace"))
    await task_execution_plans.execution_plan_service.publish(task.id, task.version, plan)
    return task.id


async def _rows(table) -> int:
    async with async_session_maker() as session:
        return int(await session.scalar(select(func.count()).select_from(table)) or 0)


async def _session_chat_ids() -> list[str]:
    async with async_session_maker() as session:
        rows = await session.scalars(select(AgentSession.chat_id).where(AgentSession.channel == "web"))
        return list(rows)


@pytest.fixture(autouse=True)
def allow_dynamic_sources(monkeypatch):
    """No live provider in this suite: discovery access is a local double."""

    async def allow(*args, **kwargs):
        return SimpleNamespace(allowed=True)

    monkeypatch.setattr(task_execution_plans, "check_canonical_read_access", allow)


async def test_task_without_saved_binding_offers_no_conversation(task_world, monkeypatch):  # noqa: F811
    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    world = task_world
    with actor(world, 0):
        task = await task_service.create(CreateTask(title="No scenario yet"))
        action = await task_chat_service.open_task_chat(task.id)
        assert action.available is False
        assert action.reason == "no_scenario_binding"
        with pytest.raises(TaskChatDenied, match="no_scenario_binding"):
            await task_chat_service.conversation(task.id)
        with pytest.raises(TaskChatDenied, match="no_scenario_binding"):
            await task_chat_service.ensure_task_conversation(task.id)

        # A catalog scenario, not bound to this task, never qualifies.
        await _scenario(world.tenants[0].id)
        assert (await task_chat_service.open_task_chat(task.id)).available is False


async def test_binding_is_allocated_once_on_first_send_and_read_creates_no_rows(task_world, monkeypatch):  # noqa: F811
    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    world = task_world
    tenant_id = world.tenants[0].id
    with actor(world, 0):
        scenario = await _scenario(tenant_id)
        task_id = await _bound_task(scenario.id)
        before_sessions = await _rows(AgentSession)
        before_bindings = await _rows(TaskChatBinding)

        action = await task_chat_service.open_task_chat(task_id)
        assert action.available is True
        assert action.scenario_id == scenario.id
        assert action.step == "digest" or action.step == "analyze"

        read = await task_chat_service.conversation(task_id)
        assert read.is_task is True
        assert read.session_id is None
        assert await _rows(AgentSession) == before_sessions
        assert await _rows(TaskChatBinding) == before_bindings

        first = await task_chat_service.ensure_task_conversation(task_id)
        assert first.session_id is not None
        second = await task_chat_service.ensure_task_conversation(task_id)
        assert second.session_id == first.session_id
        assert await _rows(TaskChatBinding) == before_bindings + 1
        assert task_chat_actor_id(world.users[0].id, task_id) in await _session_chat_ids()

        # The main conversation is not the task conversation.
        assert (await task_chat_service.conversation()).session_id != first.session_id


async def _demote_to_member(tenant_id: int, user_id: int, *, ai_chat: bool) -> None:
    """Give a fixture membership a non-owner workspace role (fixtures seed 'owner')."""
    from app.models import WorkspaceRole

    async with async_session_maker() as session, session.begin():
        role_id = await session.scalar(select(WorkspaceRole.id).where(WorkspaceRole.codename == "analyst"))
        membership = await session.scalar(
            select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.user_id == user_id)
        )
        membership.role_id = role_id
        membership.ai_chat_enabled = ai_chat


async def test_two_members_with_access_get_separate_private_conversations(task_world, monkeypatch):  # noqa: F811
    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    world = task_world
    tenant_id = world.tenants[0].id
    async with async_session_maker() as session, session.begin():
        membership = await session.scalar(
            select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.user_id == world.users[1].id)
        )
        membership.ai_chat_enabled = True

    with actor(world, 0):
        scenario = await _scenario(tenant_id)
        task_id = await _bound_task(scenario.id)
        owner_scope = await task_chat_service.ensure_task_conversation(task_id)

    with actor(world, 1):
        member_scope = await task_chat_service.ensure_task_conversation(task_id)
        assert member_scope.session_id is not None
        assert member_scope.session_id != owner_scope.session_id
        assert task_chat_actor_id(world.users[1].id, task_id) not in (task_chat_actor_id(world.users[0].id, task_id),)
        async with async_session_maker() as session:
            owner_chat = await session.get(AgentSession, owner_scope.session_id)
            assert owner_chat.chat_id == task_chat_actor_id(world.users[0].id, task_id)

    async with async_session_maker() as session:
        bindings = await session.scalars(
            select(TaskChatBinding).where(TaskChatBinding.tenant_id == tenant_id, TaskChatBinding.task_id == task_id)
        )
        assert len(list(bindings)) == 2


async def test_member_without_ai_chat_is_denied_even_with_task_access(task_world, monkeypatch):  # noqa: F811
    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    world = task_world
    tenant_id = world.tenants[0].id

    with actor(world, 0):
        scenario = await _scenario(tenant_id)
        task_id = await _bound_task(scenario.id)

    # User 1 is an active member without effective AI chat access.
    await _demote_to_member(tenant_id, world.users[1].id, ai_chat=False)
    with actor(world, 1):
        action = await task_chat_service.open_task_chat(task_id)
        assert action.available is False
        assert action.reason == "ai_chat_denied"
        with pytest.raises(TaskChatDenied, match="ai_chat_denied"):
            await task_chat_service.ensure_task_conversation(task_id)

    # With chat access but without the role rights the binding needs, the task
    # conversation stays closed (missing task or scenario view, one answer).
    await _demote_to_member(tenant_id, world.users[1].id, ai_chat=True)
    with actor(world, 1):
        action = await task_chat_service.open_task_chat(task_id)
        assert action.available is False
        assert action.reason in {"task_unavailable", "scenario_unavailable"}
        with pytest.raises(TaskChatDenied):
            await task_chat_service.ensure_task_conversation(task_id)


async def test_task_surface_offers_the_chat_action_only_with_a_usable_binding(task_world, monkeypatch):  # noqa: F811
    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    from app.services.workspace.task_surface import canonical_task_actions

    world = task_world
    tenant_id = world.tenants[0].id

    with actor(world, 0):
        plain = await task_service.create(CreateTask(title="Задача без сценария", visibility="workspace"))
        plain_id = plain.id

    with actor(world, 0):
        surface = await canonical_task_actions(plain_id)
    assert not [a for a in surface["actions"] if a["reference"] == "open_task_chat"]

    with actor(world, 0):
        scenario = await _scenario(tenant_id)
        task_id = await _bound_task(scenario.id)
        surface = await canonical_task_actions(task_id)
    chat_actions = [a for a in surface["actions"] if a["reference"] == "open_task_chat"]
    assert len(chat_actions) == 1
    action = chat_actions[0]
    assert action["label"] == "Спросить агента"
    assert action["conversation"] == f"task:{task_id}"
    assert action["href"] == f"/chat?conversation=task:{task_id}"
    assert action["scenario"]
    assert action["step"]


async def test_runtime_rules_keep_task_sessions_bound_to_their_actor():
    identity = RuntimeIdentity(
        tenant_id=1,
        user_id=7,
        membership_id=3,
        channel="web",
        chat_id="7",
        external_user_id="7",
        membership_role_id=1,
        user_role_id=1,
        is_owner=True,
        user=SimpleNamespace(),
        ai_chat_enabled=True,
    )
    own = SimpleNamespace(id=11, tenant_id=1, channel="web", chat_id=task_chat_actor_id(7, 42), is_active=True)
    other = SimpleNamespace(id=12, tenant_id=1, channel="web", chat_id=task_chat_actor_id(8, 42), is_active=True)
    main = SimpleNamespace(id=13, tenant_id=1, channel="web", chat_id="7", is_active=True)
    foreign_tenant = SimpleNamespace(
        id=14, tenant_id=2, channel="web", chat_id=task_chat_actor_id(7, 42), is_active=True
    )
    assert session_matches_conversation(own, identity) is True
    assert session_matches_conversation(main, identity) is True
    assert session_matches_conversation(other, identity) is False
    assert session_matches_conversation(foreign_tenant, identity) is False


def test_global_chat_entry_and_compatible_app_entry_are_both_routed():
    """`/chat` is the global entry; `/app/chat` stays a compatible entry."""
    from app.main import app

    paths = set()

    def walk(routes):
        for route in routes:
            path = getattr(route, "path", None)
            if path:
                paths.add(path)
            elif hasattr(route, "original_router"):
                walk(getattr(route.original_router, "routes", []))

    from app.web import web_router
    from app.web.chat import router as chat_router

    walk(app.router.routes)
    assert "/chat" in paths
    assert "/chat/" in paths
    # The compatible entry mounts the very same chat router under `/app`.
    assert web_router.prefix == "/app"
    assert chat_router.prefix == "/chat"


def test_conversation_selector_and_in_place_switch_are_wired():
    """The sidebar names conversations; the server still decides who may read."""
    from pathlib import Path

    workspace = Path("app/web/templates/web/_chat_workspace.html").read_text()
    assert "data-chat-conversations" in workspace
    assert "selectConversation('main')" in workspace
    assert 'x-for="tab in conversationTabs"' in workspace

    page = Path("app/web/templates/web/chat.html").read_text()
    assert "data-chat-conversation-line" in page
    assert "conversationBlocked()" in page, "an unavailable conversation cannot send"
    assert "__chatConversation" in page

    script = Path("app/static/js/chat.js").read_text()
    assert "conversationRequest" in script, "late answers need a request token"
    assert "token !== this.conversationRequest" in script
    assert "'conversation': this.conversationParam()" in script
    assert "sessionStorage.setItem(this.draftKey()" in script, "drafts are per conversation"
    assert "this.messages = [];" in script, "the old transcript leaves the view while switching"
    assert "data-chat-conversation-loading" in page, "a switch has an explicit loading state"


def test_task_chat_drawer_embeds_the_shared_chat_page():
    """The drawer reuses `/chat`; it never renders a second transcript."""
    from app.web.deps import templates

    drawer = templates.env.get_template("web/_task_chat.html")
    html = drawer.render(
        chat_conversation={
            "value": "task:7",
            "title": "Задача",
            "href": "/chat?conversation=task:7",
            "scenario": "Сценарий",
            "step": "analyze",
            "available": True,
        }
    )
    assert 'data-task-chat-conversation="task:7"' in html
    assert "embedded=1" in html
    assert 'src="/chat?conversation=task:7&amp;embedded=1"' in html
    assert "шаг: analyze" in html
    assert "Сценарий" in html

    assert drawer.render(chat_conversation={"available": False}).strip() == ""
    assert drawer.render(chat_conversation=None).strip() == ""
