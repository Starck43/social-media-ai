"""Prepared owner-local DB authorization checks; NOT executed remotely.

Requires the reviewed bootstrap-free plugin, an explicitly approved exclusive
window, an existing verified test target and mocked external boundaries.
Uses fixture-owned records only; no conftest, bootstrap, seed or schema writes.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sys
from contextlib import asynccontextmanager
from copy import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

PLUGIN_SHA256 = "809d7880306d1b0dbb76a35afab94e5e446c6b9de6adeb7e52e1703829373be6"
plugin = sys.modules.get("tests.plugins.bootstrap_free")
if plugin is None or hashlib.sha256(Path(plugin.__file__).read_bytes()).hexdigest() != PLUGIN_SHA256:
    raise RuntimeError("STOP: load the exact reviewed bootstrap-free plugin before collection")

from app.agent import runtime  # noqa: E402
from app.agent.confirmation import make_pending_intent, pending_rejection  # noqa: E402
from app.agent.identity import resolve_runtime_identity  # noqa: E402
from app.agent.tools import ToolConfirmationRequiredError, call_tool, get_tool  # noqa: E402
from app.agent.toolset import reports  # noqa: E402,F401
from app.channels.base import Inbound  # noqa: E402
from app.core.permissions import get_current_user, has_permission_by_codename, permission_scope  # noqa: E402
from app.core.tenant_context import is_bypass, tenant_scope  # noqa: E402
from app.main import create_application  # noqa: E402
from app.models import AgentSession, Permission, Role, User  # noqa: E402
from app.models.managers.agent_session_manager import agent_sessions  # noqa: E402
from app.models.managers.tenant_manager import tenant_channels, tenant_users, tenants  # noqa: E402
from app.services.digest import builder  # noqa: E402
from app.services.tenancy.resolver import Resolution  # noqa: E402
from app.types import ActionType, UserRoleType  # noqa: E402
from app.web import digests as web_digests  # noqa: E402

pytestmark = [pytest.mark.tenancy, pytest.mark.bootstrap_free]
ARGS = {"period": "week", "group_by": "sources", "time_breakdown": True}
PASSWORD = "synthetic-matrix-password-1"
CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')


@asynccontextmanager
async def owned_matrix(channel, right):
    """One channel per User/tenant, matching the real partial UNIQUE index."""
    assert not is_bypass(), "tenancy tests must not inherit platform bypass"
    assert get_current_user() is None, "no inherited operator permission scope"
    assert get_tool("digest_send_now").required_permission == "digestrun.create"
    suffix = secrets.token_hex(6)
    owned_tenants, users, roles = [], [], []
    sentinel = None
    own = None
    try:
        permissions = list(await Permission.objects.prefetch_related("model_type").filter())
        ids = {
            p.action_type: p.id
            for p in permissions
            if p.model_type is not None and p.model_type.model_name.lower() == "digestrun"
        }
        needed = (ActionType.VIEW, ActionType.UPDATE, ActionType.CREATE)
        assert all(action in ids for action in needed), "existing digest reference rights required; do not seed"
        actions = {"view": ActionType.VIEW, "update": ActionType.UPDATE, "create": ActionType.CREATE}
        for label, grants in (("platform", [ids[a] for a in needed]), ("workspace", [ids[actions[right]]])):
            role = await Role.objects.create_role(
                name=f"digest-matrix-{label}-{suffix}", codename=UserRoleType.VIEWER.name
            )
            assert role is not None
            roles.append(role)
            await Role.objects.set_permissions(role.id, grants)
        own = await tenants.create(name="Digest matrix", slug=f"digest-matrix-{suffix}", plan="business")
        assert own is not None
        owned_tenants.append(own)
        other = await tenants.create(name="Digest sentinel", slug=f"digest-sentinel-{suffix}", plan="business")
        assert other is not None
        owned_tenants.append(other)
        with tenant_scope(other.id):
            sentinel = await AgentSession.objects.create(
                channel="telegram",
                chat_id=f"digest-sentinel-{suffix}",
                kind="private",
                is_active=True,
                state={"sentinel": suffix},
            )
        assert sentinel is not None
        members = []
        for index in range(2):
            username = f"digest-matrix-{index}-{suffix}"
            user = await User.objects.create_user(
                username=username,
                email=f"{username}@example.com",
                password=PASSWORD,
                role_id=roles[0].id,
                is_superuser=False,
            )
            assert user is not None
            users.append(user)
            external_id = str(user.id) if channel == "web" else f"digest-actor-{index}-{suffix}"
            with tenant_scope(own.id):
                if channel == "web":
                    member = await tenant_users.add_web_member(tenant_id=own.id, user_id=user.id, role_id=roles[1].id)
                else:
                    member = await tenant_users.add_member(
                        tenant_id=own.id,
                        channel=channel,
                        external_user_id=external_id,
                        role_id=roles[1].id,
                    )
                    assert member is not None
                    member = await tenant_users.update_by_id(member.id, user_id=user.id)
            assert member is not None and member.channel == channel
            members.append(member)
            loaded = await User.objects.prefetch_related("role.permissions.model_type").get(id=user.id)
            assert loaded is not None and not loaded.is_superuser and not loaded._is_superuser_role()
            assert loaded.has_perm_for("digestrun", ActionType.CREATE), "platform grant is deliberately stronger"
        chat = f"digest-group-{suffix}"
        binding = None
        if channel != "web":
            with tenant_scope(own.id):
                binding = await tenant_channels.bind(tenant_id=own.id, channel=channel, chat_id=chat, kind="group")
            assert binding is not None
        with tenant_scope(own.id):
            assert await AgentSession.objects.get(id=sentinel.id) is None, "foreign sentinel must be hidden"
        yield SimpleNamespace(
            tenant=own,
            sentinel=sentinel,
            sentinel_tenant=other,
            sentinel_state={"sentinel": suffix},
            users=users,
            members=members,
            channel=channel,
            chat=chat,
            binding=binding,
            workspace_role=roles[1],
            permission_ids=ids,
            initial_right=right,
        )
    finally:
        errors = []
        # Preserve the foreign sentinel across primary-tenant cleanup, including failed setup.
        if own is not None:
            try:
                with tenant_scope(own.id):
                    await tenants.delete_by_id(own.id)
                    assert await tenants.get(id=own.id) is None
            except Exception as exc:
                errors.append(f"primary tenant cleanup: {type(exc).__name__}")
        if sentinel is not None:
            try:
                with tenant_scope(sentinel.tenant_id):
                    stored = await AgentSession.objects.get(id=sentinel.id)
                    assert stored is not None and stored.state == {"sentinel": suffix}
            except Exception as exc:
                errors.append(f"sentinel preservation: {type(exc).__name__}")
        for tenant in reversed(owned_tenants):
            if own is not None and tenant.id == own.id:
                continue
            try:
                with tenant_scope(tenant.id):
                    await tenants.delete_by_id(tenant.id)
                    assert await tenants.get(id=tenant.id) is None
            except Exception as exc:
                errors.append(f"sentinel tenant cleanup: {type(exc).__name__}")
        for user in reversed(users):
            try:
                await User.objects.delete_user(user.id)
                assert await User.objects.get(id=user.id) is None
            except Exception as exc:
                errors.append(f"user cleanup: {type(exc).__name__}")
        for role in reversed(roles):
            try:
                await Role.objects.set_permissions(role.id, [])
                await Role.objects.delete_by_id(role.id)
                assert await Role.objects.get(id=role.id) is None
            except Exception as exc:
                errors.append(f"role cleanup: {type(exc).__name__}")
        assert not errors, "; ".join(errors)


@pytest.fixture
async def matrix(request, monkeypatch):
    channel, right = getattr(request, "param", ("telegram", "create"))
    async with owned_matrix(channel, right) as case:
        case.publisher = AsyncMock(return_value={"status": "sent"})
        monkeypatch.setattr(builder, "build_and_publish", case.publisher)
        monkeypatch.setattr(web_digests, "build_and_publish", case.publisher)
        asked = False

        async def chat(messages, specs):
            nonlocal asked
            if not asked:
                asked = True
                return {
                    "content": "",
                    "usage": {},
                    "tool_calls": [
                        {"id": "matrix-call", "name": "digest_send_now", "arguments": dict(ARGS)},
                    ],
                }
            return {"content": "Done.", "usage": {}, "tool_calls": []}

        case.chat_mock = AsyncMock(side_effect=chat)
        monkeypatch.setattr(runtime, "_chat", case.chat_mock)
        yield case


def resolution(case, actor=0, *, external_id=None):
    member = case.members[actor]
    external = member.external_user_id if external_id is None else external_id
    chat = str(case.users[actor].id) if case.channel == "web" else case.chat
    return Resolution(tenant_id=case.tenant.id, channel=case.channel, chat_id=chat, user_id=external)


async def turn(case, text, actor=0, *, external_id=None):
    resolved = resolution(case, actor, external_id=external_id)
    inbound = Inbound(channel=resolved.channel, chat_id=resolved.chat_id, user_id=resolved.user_id, text=text)
    # Exercise real interactive admission/identity, storage, rights and dispatch.
    # Routing is already resolved, as in the web entry point; not a router test.
    with tenant_scope(case.tenant.id):
        return await runtime._handle_in_tenant(inbound, resolved)


async def session(case, actor=0):
    resolved = resolution(case, actor)
    with tenant_scope(case.tenant.id):
        return await agent_sessions.get(channel=resolved.channel, chat_id=resolved.chat_id)


async def stage(case):
    result = await turn(case, "prepare digest")
    assert "Требуется подтверждение" in result
    row = await session(case)
    assert row is not None
    pending = (row.state or {}).get("pending_confirmation")
    assert pending is not None and pending["required_permission"] == "digestrun.create"
    with tenant_scope(case.tenant.id):
        identity = await resolve_runtime_identity(resolution(case))
        assert identity is not None and not identity.is_owner
        assert pending_rejection(pending, identity, row, get_tool("digest_send_now")) is None
    case.publisher.assert_not_awaited()
    return row, pending


@pytest.mark.parametrize(
    "matrix",
    [
        pytest.param(("telegram", "view"), id="view-only"),
        pytest.param(("telegram", "update"), id="update-only"),
    ],
    indirect=True,
)
async def test_agent_workspace_right_denies_staging(matrix):
    result = await turn(matrix, "prepare digest")
    assert "Требуется подтверждение" not in result
    row = await session(matrix)
    assert row is not None and (row.state or {}).get("pending_confirmation") is None
    with tenant_scope(matrix.tenant.id):
        identity = await resolve_runtime_identity(resolution(matrix))
        assert identity is not None and not identity.is_owner
        with permission_scope(identity.user, member_grants=identity.member_grants):
            assert not has_permission_by_codename(identity.user, "digestrun.create")
        history = await row.history()
    assert any(m.get("role") == "tool" and "нет прав" in m.get("content", "") for m in history)
    matrix.publisher.assert_not_awaited()


async def test_agent_create_requires_confirmation_with_real_identity(matrix):
    with tenant_scope(matrix.tenant.id):
        identity = await resolve_runtime_identity(resolution(matrix))
        assert identity is not None and not identity.is_owner
        with permission_scope(identity.user, member_grants=identity.member_grants):
            assert has_permission_by_codename(identity.user, "digestrun.create")
            with pytest.raises(ToolConfirmationRequiredError):
                await call_tool("digest_send_now", dict(ARGS))
    matrix.publisher.assert_not_awaited()


async def test_agent_confirmation_dispatches_once_and_persists_consumption(matrix):
    await stage(matrix)
    reply = await turn(matrix, "да")
    assert "Выполнено: digest_send_now" in reply
    matrix.publisher.assert_awaited_once_with(**ARGS)
    assert (await session(matrix)).state.get("pending_confirmation") is None
    await turn(matrix, "да")
    matrix.publisher.assert_awaited_once_with(**ARGS)
    assert (await session(matrix)).state.get("pending_confirmation") is None


async def test_agent_create_revoked_after_staging(matrix):
    await stage(matrix)
    await Role.objects.set_permissions(matrix.workspace_role.id, [matrix.permission_ids[ActionType.VIEW]])
    reply = await turn(matrix, "да")
    assert "Подтверждение отклонено" in reply
    matrix.publisher.assert_not_awaited()
    assert (await session(matrix)).state.get("pending_confirmation") is None


@pytest.mark.parametrize("revocation", ["inactive-user", "inactive-member", "removed-member", "inactive-binding"])
async def test_agent_identity_revoked_after_staging(matrix, revocation):
    await stage(matrix)
    matrix.chat_mock.reset_mock()
    with tenant_scope(matrix.tenant.id):
        if revocation == "inactive-user":
            await User.objects.filter(id=matrix.users[0].id).update(is_active=False)
        elif revocation == "inactive-member":
            await tenant_users.update_by_id(matrix.members[0].id, is_active=False)
        elif revocation == "removed-member":
            await tenant_users.delete_by_id(matrix.members[0].id)
        else:
            await tenant_channels.update_by_id(matrix.binding.id, is_active=False)
    reply = await turn(matrix, "да")
    assert "Чат не связан с активным пользователем" in reply
    matrix.publisher.assert_not_awaited()
    matrix.chat_mock.assert_not_awaited()


async def test_agent_unknown_actor_cannot_consume_pending(matrix):
    _row, pending = await stage(matrix)
    reply = await turn(matrix, "да", external_id="unbound-synthetic-actor")
    assert "Чат не связан с активным пользователем" in reply
    matrix.publisher.assert_not_awaited()
    assert (await session(matrix)).state["pending_confirmation"] == pending


async def test_agent_foreign_active_actor_cannot_consume_pending(matrix):
    _row, pending = await stage(matrix)
    reply = await turn(matrix, "да", actor=1)
    assert "Подтверждение отклонено" in reply
    matrix.publisher.assert_not_awaited()
    assert (await session(matrix)).state["pending_confirmation"] == pending


async def test_agent_well_formed_old_update_contract_is_refused(matrix):
    await Role.objects.set_permissions(matrix.workspace_role.id, [matrix.permission_ids[ActionType.UPDATE]])
    with tenant_scope(matrix.tenant.id):
        identity = await resolve_runtime_identity(resolution(matrix))
        assert identity is not None
        row = await agent_sessions.get_or_create(channel=matrix.channel, chat_id=matrix.chat, kind="group")
        assert row is not None
        old_spec = copy(get_tool("digest_send_now"))
        old_spec.required_permission = "digestrun.update"
        with permission_scope(identity.user, member_grants=identity.member_grants):
            assert has_permission_by_codename(identity.user, old_spec.required_permission)
        pending = make_pending_intent(identity, row, old_spec, dict(ARGS), "old-contract-call")
        assert pending is not None and pending_rejection(pending, identity, row, old_spec) is None
        await runtime._set_pending(row, pending)
    await Role.objects.set_permissions(matrix.workspace_role.id, [matrix.permission_ids[ActionType.CREATE]])
    with tenant_scope(matrix.tenant.id):
        fresh = await resolve_runtime_identity(resolution(matrix))
        assert fresh is not None
        assert pending_rejection(pending, fresh, row, get_tool("digest_send_now")) == "tool_changed"
    reply = await turn(matrix, "да")
    assert "Подтверждение отклонено" in reply
    matrix.publisher.assert_not_awaited()
    assert (await session(matrix)).state.get("pending_confirmation") is None


@asynccontextmanager
async def authenticated_client(case):
    assert case.channel == "web"
    app = create_application()
    # ASGITransport does not enter lifespan: no startup/migrator/runtime owner.
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=False
    ) as client:
        page = await client.get("/app/login")
        assert page.status_code == 200
        match = CSRF_RE.search(page.text)
        assert match is not None
        logged_in = await client.post(
            "/app/login",
            data={
                "username": case.users[0].username,
                "password": PASSWORD,
                "_csrf": match.group(1),
            },
        )
        assert logged_in.status_code == 302 and logged_in.headers["location"] == "/app/"
        token = app.state.csrf_manager.generate_token()
        assert app.state.csrf_manager.verify_token(token)
        yield client, token


async def send_web(client, token):
    data = {"period": "week", "group_by": "sources", "time_breakdown": "true"}
    if token is not None:
        data["_csrf"] = token
    return await client.post("/app/digests/send-now", data=data)


@pytest.mark.parametrize(
    "matrix",
    [
        pytest.param(("web", "view"), id="view-only"),
        pytest.param(("web", "update"), id="update-only"),
        pytest.param(("web", "create"), id="create-only"),
    ],
    indirect=True,
)
async def test_web_workspace_right_with_valid_csrf(matrix):
    async with authenticated_client(matrix) as (client, token):
        response = await send_web(client, token)
        assert response.status_code == 302 and response.headers["location"] == "/app/digests"
    if matrix.initial_right == "create":
        matrix.publisher.assert_awaited_once_with(**ARGS, force=True)
    else:
        matrix.publisher.assert_not_awaited()


@pytest.mark.parametrize("matrix", [pytest.param(("web", "create"), id="create-only")], indirect=True)
@pytest.mark.parametrize(
    "csrf", [pytest.param(None, id="missing"), pytest.param("invalid-synthetic-token", id="invalid")]
)
async def test_web_create_without_valid_csrf_is_refused(matrix, csrf):
    async with authenticated_client(matrix) as (client, _token):
        response = await send_web(client, csrf)
        assert response.status_code == 302 and response.headers["location"] == "/app/digests"
    matrix.publisher.assert_not_awaited()


@pytest.mark.parametrize("matrix", [pytest.param(("web", "create"), id="create-only")], indirect=True)
@pytest.mark.parametrize("revocation", ["create-right", "inactive-user", "inactive-member", "removed-member"])
async def test_web_reloads_authority_after_login(matrix, revocation):
    async with authenticated_client(matrix) as (client, token):
        control = await send_web(client, token)
        assert control.status_code == 302
        matrix.publisher.assert_awaited_once_with(**ARGS, force=True)
        matrix.publisher.reset_mock()
        with tenant_scope(matrix.tenant.id):
            if revocation == "create-right":
                await Role.objects.set_permissions(matrix.workspace_role.id, [matrix.permission_ids[ActionType.VIEW]])
            elif revocation == "inactive-user":
                await User.objects.filter(id=matrix.users[0].id).update(is_active=False)
            elif revocation == "inactive-member":
                await tenant_users.update_by_id(matrix.members[0].id, is_active=False)
            else:
                await tenant_users.delete_by_id(matrix.members[0].id)
        denied = await send_web(client, token)
        assert denied.status_code == 302
        target = denied.headers["location"]
        if revocation == "create-right":
            assert target == "/app/digests"
            page = await client.get(target)
            assert page.status_code == 200 and "Недостаточно прав" in page.text
        elif revocation == "inactive-user":
            assert target.startswith("/app/login?next=")
        else:
            assert target == "/app/invite"
    matrix.publisher.assert_not_awaited()
