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


def _social_plan(scenario_id: int | None) -> str:
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


def _collect_plan() -> str:
    """A plan with no analysis step: nothing references an agent scenario."""
    return map_social_plan(
        {
            "task_plan": {
                "source_scope": "discovery",
                "collection": {"queries": ["synthetic topic"]},
                "trigger": {"type": "schedule", "cron": "0 9 * * *"},
                "vk_access": {"mode": "api"},
            }
        },
        job_type="collect",
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


async def test_an_open_task_without_scenario_can_be_delegated(task_world, monkeypatch):  # noqa: F811
    """The current contract: delegation needs an open task and no attached scenario."""
    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    world = task_world
    with actor(world, 0):
        task = await task_service.create(CreateTask(title="No scenario yet"))
        action = await task_chat_service.open_task_chat(task.id)
        assert action.available is True
        assert action.delegation is True
        assert action.scenario_name == ""
        assert action.reason == ""

        # Reading the empty conversation allocates nothing.
        before_bindings = await _rows(TaskChatBinding)
        read = await task_chat_service.conversation(task.id)
        assert read.session_id is None
        assert await _rows(TaskChatBinding) == before_bindings

        # A catalog scenario that is not attached to this task changes nothing.
        await _scenario(world.tenants[0].id)
        assert (await task_chat_service.open_task_chat(task.id)).delegation is True

        first = await task_chat_service.ensure_task_conversation(task.id)
        assert first.session_id is not None
        assert await _rows(TaskChatBinding) == before_bindings + 1


async def test_attached_scenario_keeps_history_readable_but_ends_delegation(task_world, monkeypatch):  # noqa: F811
    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    world = task_world
    tenant_id = world.tenants[0].id
    with actor(world, 0):
        task = await task_service.create(CreateTask(title="Delegated before planning"))
        session = await task_chat_service.ensure_task_conversation(task.id)

        scenario = await _scenario(tenant_id)
        await task_execution_plans.execution_plan_service.publish(task.id, task.version, _social_plan(scenario.id))

        action = await task_chat_service.open_task_chat(task.id)
        assert action.available is True, "authorized history stays readable"
        assert action.delegation is False
        assert action.reason == "scenario_attached"
        assert action.scenario_id == scenario.id
        assert action.scenario_name == scenario.name

        # A stale drawer or card cannot spend a turn on the attached scenario.
        with pytest.raises(TaskChatDenied, match="scenario_attached"):
            await task_chat_service.ensure_task_conversation(task.id)

        # The existing private transcript is still the same session.
        read = await task_chat_service.conversation(task.id)
        assert read.session_id == session.session_id
        tabs = await task_chat_service.list_task_conversations()
        assert [(item["task_id"], item["delegation"]) for item in tabs] == [(task.id, False)]

        # Attaching is about the current plan: a later revision without an
        # analysis step restores delegation and resumes the same conversation.
        await task_execution_plans.execution_plan_service.publish(task.id, task.version + 1, _collect_plan())
        restored = await task_chat_service.open_task_chat(task.id)
        assert restored.delegation is True
        assert await _rows(TaskChatBinding) >= 1
        assert (await task_chat_service.ensure_task_conversation(task.id)).session_id == session.session_id


async def test_closing_work_ends_delegation_and_keeps_the_same_private_history(task_world, monkeypatch):  # noqa: F811
    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    world = task_world
    with actor(world, 0):
        task = await task_service.create(CreateTask(title="Delegated then closed"))
        session = await task_chat_service.ensure_task_conversation(task.id)

        from app.services.planner.contracts import TaskUpdate

        await task_service.complete(task.id, task.version)

        action = await task_chat_service.open_task_chat(task.id)
        assert action.available is True
        assert action.delegation is False
        assert action.reason == "task_closed"
        assert action.work_status == "done"
        with pytest.raises(TaskChatDenied, match="task_closed"):
            await task_chat_service.ensure_task_conversation(task.id)
        read = await task_chat_service.conversation(task.id)
        assert read.session_id == session.session_id
        assert read.delegation is False
        assert read.reason == "task_closed"

        # Cancelling is closed work too: the same history, no new delegation.
        # Reopening is P22-T17's contract; this module only reads the status.
        cancelled = await task_service.create(CreateTask(title="Delegated then cancelled"))
        cancelled_session = await task_chat_service.ensure_task_conversation(cancelled.id)
        await task_service.update(
            cancelled.id, TaskUpdate(expected_version=cancelled.version, scope="occurrence", status="cancelled")
        )
        cancelled_action = await task_chat_service.open_task_chat(cancelled.id)
        assert cancelled_action.reason == "task_closed"
        assert (await task_chat_service.conversation(cancelled.id)).session_id == cancelled_session.session_id


async def test_binding_is_allocated_once_on_first_send_and_read_creates_no_rows(task_world, monkeypatch):  # noqa: F811
    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    world = task_world
    with actor(world, 0):
        task = await task_service.create(CreateTask(title="Allocated once"))
        before_sessions = await _rows(AgentSession)
        before_bindings = await _rows(TaskChatBinding)

        action = await task_chat_service.open_task_chat(task.id)
        assert action.available is True
        assert action.delegation is True

        read = await task_chat_service.conversation(task.id)
        assert read.is_task is True
        assert read.session_id is None
        assert await _rows(AgentSession) == before_sessions
        assert await _rows(TaskChatBinding) == before_bindings

        first = await task_chat_service.ensure_task_conversation(task.id)
        assert first.session_id is not None
        second = await task_chat_service.ensure_task_conversation(task.id)
        assert second.session_id == first.session_id
        assert await _rows(TaskChatBinding) == before_bindings + 1
        assert task_chat_actor_id(world.users[0].id, task.id) in await _session_chat_ids()

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
        task = await task_service.create(CreateTask(title="Shared workspace task", visibility="workspace"))
        owner_scope = await task_chat_service.ensure_task_conversation(task.id)
        task_id = task.id

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
        task = await task_service.create(CreateTask(title="Member visibility task", visibility="workspace"))
        task_id = task.id

    # User 1 is an active member without effective AI chat access.
    await _demote_to_member(tenant_id, world.users[1].id, ai_chat=False)
    with actor(world, 1):
        action = await task_chat_service.open_task_chat(task_id)
        assert action.available is False
        assert action.reason == "ai_chat_denied"
        with pytest.raises(TaskChatDenied, match="ai_chat_denied"):
            await task_chat_service.ensure_task_conversation(task_id)

    # With chat access the same workspace task becomes delegable, and the
    # member's private transcript is their own.
    await _demote_to_member(tenant_id, world.users[1].id, ai_chat=True)
    with actor(world, 1):
        action = await task_chat_service.open_task_chat(task_id)
        assert action.available is True
        assert action.delegation is True
        own = await task_chat_service.ensure_task_conversation(task_id)
        assert own.session_id is not None

    # A private task of the owner stays invisible to the member.
    with actor(world, 0):
        private = await task_service.create(CreateTask(title="Owner only", visibility="private"))
    with actor(world, 1):
        assert (await task_chat_service.open_task_chat(private.id)).available is False


async def test_task_surface_offers_delegation_for_an_open_task_without_scenario(task_world, monkeypatch):  # noqa: F811
    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    from app.services.workspace.task_surface import canonical_task_actions

    world = task_world
    tenant_id = world.tenants[0].id

    with actor(world, 0):
        plain = await task_service.create(CreateTask(title="Задача без сценария", visibility="workspace"))
        plain_id = plain.id
        surface = await canonical_task_actions(plain_id)
    chat_actions = [a for a in surface["actions"] if a["reference"] == "open_task_chat"]
    assert len(chat_actions) == 1, "delegation does not require a scenario"
    action = chat_actions[0]
    assert action["label"] == "Поручить агенту"
    assert action["conversation"] == f"task:{plain_id}"
    assert action["href"] == f"/chat?conversation=task:{plain_id}"
    assert not action.get("scenario") and not action.get("step")

    # An attached scenario hides the delegation link, not the history.
    with actor(world, 0):
        scenario = await _scenario(tenant_id)
        await task_execution_plans.execution_plan_service.publish(plain_id, plain.version, _social_plan(scenario.id))
        surface = await canonical_task_actions(plain_id)
    assert not [a for a in surface["actions"] if a["reference"] == "open_task_chat"]

    # Closed work has no delegation link either.
    with actor(world, 0):
        done = await task_service.create(CreateTask(title="Закрытая задача", visibility="workspace"))
        await task_service.complete(done.id, done.version)
        surface = await canonical_task_actions(done.id)
    assert not [a for a in surface["actions"] if a["reference"] == "open_task_chat"]


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
    assert (
        ':disabled="pending"' not in workspace.split("conversationTabs")[1].split("</template>")[0]
    ), "a running turn must not lock the picker"

    page = Path("app/web/templates/web/chat.html").read_text()
    assert "data-chat-conversation-line" in page
    assert "conversationBlocked()" in page, "an unavailable conversation cannot send"
    assert "data-chat-conversation-readonly" in page, "closed work keeps readable history"
    assert "__chatConversation" in page

    script = Path("app/static/js/chat.js").read_text()
    assert "conversationRequest" in script, "late answers need a request token"
    assert "token !== this.conversationRequest" in script
    assert "stillCurrent()" in script, "a turn from another conversation cannot write here"
    assert "this.sendToken += 1" in script, "a switch detaches from a running turn"
    assert "Дождитесь ответа агента, чтобы сменить разговор" not in script, "a switch is never refused"
    assert "'conversation': this.conversationParam()" in script
    assert "conversation?.delegation === false" in script, "delegation needs open, scenario-free work"
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

    plain = drawer.render(
        chat_conversation={
            "value": "task:8",
            "title": "Задача без сценария",
            "href": "/chat?conversation=task:8",
            "available": True,
            "delegation": True,
        }
    )
    assert "Задача без сценария" in plain
    assert "шаг:" not in plain, "delegation carries no scenario step"

    assert drawer.render(chat_conversation={"available": False}).strip() == ""
    assert drawer.render(chat_conversation=None).strip() == ""


async def test_embedded_chat_page_denies_an_unbound_task_without_rows(monkeypatch):
    """The drawer loads the same page; an unreadable selection is refused."""
    import json
    import re
    import secrets

    from httpx import ASGITransport, AsyncClient

    from app.main import create_application
    from app.models import User
    from app.models.managers.tenant_manager import TenantUserManager
    from tests.shared.account_cleanup import purge_account

    application = create_application()
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://testserver", follow_redirects=True
    ) as client:
        username = f"TaskChat{secrets.token_hex(4)}"
        token = re.search(r'name="_csrf" value="([^"]+)"', (await client.get("/app/register")).text).group(1)
        registered = await client.post(
            "/app/register",
            data={
                "username": username,
                "email": f"{username}@example.com",
                "password": "secret-password-1",
                "workspace": "Task chat workspace",
                "_csrf": token,
            },
        )
        assert registered.status_code == 200
        user = await User.objects.get(username=username)
        tenant_id = (await TenantUserManager().web_memberships(user.id))[0].tenant_id
        try:
            before = await _rows(TaskChatBinding)
            embedded = await client.get("/chat?embedded=1")
            assert embedded.status_code == 200
            assert "chat-dock-container" not in embedded.text, "the drawer must not nest another dock"

            denied = await client.get(
                "/chat?conversation=task:999999",
                headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
            )
            assert denied.status_code == 403
            payload = json.loads(denied.text)
            assert payload["code"] in {"task_unavailable", "scenario_unavailable"}
            assert payload["conversation"]["available"] is False
            assert await _rows(TaskChatBinding) == before, "a denied read creates nothing"
        finally:
            await purge_account(user.id)


def test_scope_payload_reports_delegation_and_read_only_history():
    """The browser gets both capabilities; the server decides what they mean."""
    from app.services.task_chat import ChatScope
    from app.web.chat import _scope_public

    open_scope = _scope_public(ChatScope("task", "Задача", None, task_id=3, delegation=True))
    assert open_scope["available"] is True and open_scope["delegation"] is True

    closed = _scope_public(ChatScope("task", "Задача", 5, task_id=3, delegation=False, reason="task_closed"))
    assert closed["available"] is True, "the reader may still read"
    assert closed["delegation"] is False
    assert closed["reason"] == "task_closed"


def test_task_topic_line_is_hidden_from_the_reader_but_not_lost():
    """The server-resolved topic informs the model; bubbles show the question."""
    from app.web.chat import _strip_task_topic

    prefix = "[Разговор по задаче: Задача | сценарий: Сценарий | шаг: analyze]\n"
    assert _strip_task_topic(prefix + "Вопрос") == "Вопрос"
    assert _strip_task_topic("Вопрос") == "Вопрос"
    assert _strip_task_topic(None) is None
    assert _strip_task_topic(prefix.rstrip("\n")) == prefix.rstrip("\n"), "a bare topic line stays"
