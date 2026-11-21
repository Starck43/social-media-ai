from copy import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.integrations import task_resources as resources
from app.integrations.catalog.task_capabilities import Direction, ResourceKind

__source_only__ = True


@pytest.mark.asyncio
async def test_resolution_requires_explicit_account_and_rechecks_revocation(monkeypatch):
    monkeypatch.setattr(resources, "_authorize", AsyncMock())
    connection = SimpleNamespace(
        id=7,
        provider_id="mailru_mail",
        schema_version=resources.CATALOG_VERSION,
        auth_method="app_password",
        is_active=True,
        state="verified",
        config={},
    )
    second_connection = copy(connection)
    second_connection.id = 8
    accounts = {7: connection, 8: second_connection}

    async def lookup(_session, tenant_id, actor_id, connection_id):
        assert (tenant_id, actor_id) == (1, 2)
        return accounts[connection_id]

    fetch = AsyncMock(side_effect=lookup)
    monkeypatch.setattr(resources, "_connection", fetch)
    session = SimpleNamespace(scalar=AsyncMock(return_value=7))
    request = dict(tenant_id=1, actor_id=2, capability_id="mailru_mail.headers.read", direction=Direction.READ)
    selection = resources.ResourceSelection(ResourceKind.MAIL_FOLDER, 7, "INBOX")
    assert (
        await resources.resolve_task_resource(session, **request, selection=None)
        == resources.ResolutionIssue.SELECT_RESOURCE
    )
    fetch.assert_not_awaited()
    assert await resources.resolve_task_resource(session, **request, selection=selection) == resources.ScopedResource(
        1, 2, "mailru_mail.headers.read", selection
    )
    connection.is_active = False
    assert (
        await resources.resolve_task_resource(session, **request, selection=selection)
        == resources.ResolutionIssue.UNAVAILABLE
    )

    second_selection = resources.ResourceSelection(ResourceKind.MAIL_FOLDER, 8, "INBOX")
    assert await resources.resolve_task_resource(
        session, **request, selection=second_selection
    ) == resources.ScopedResource(1, 2, "mailru_mail.headers.read", second_selection)


@pytest.mark.asyncio
async def test_read_only_and_reserved_operations_never_read_connection(monkeypatch):
    monkeypatch.setattr(resources, "_authorize", AsyncMock())
    connection = AsyncMock()
    monkeypatch.setattr(resources, "_connection", connection)
    common = dict(tenant_id=1, actor_id=2, selection=None)
    assert (
        await resources.resolve_task_resource(
            None, **common, capability_id="bitrix24.tasks.list", direction=Direction.WRITE
        )
        == resources.ResolutionIssue.READ_ONLY
    )
    assert (
        await resources.resolve_task_resource(
            None, **common, capability_id="telegram.bot.read", direction=Direction.READ
        )
        == resources.ResolutionIssue.UNSUPPORTED
    )
    connection.assert_not_awaited()


@pytest.mark.asyncio
async def test_vk_resolution_keeps_the_exact_selected_identity(monkeypatch):
    monkeypatch.setattr(resources, "_authorize", AsyncMock())
    authorize_vk = AsyncMock(return_value=SimpleNamespace(allowed=True))
    monkeypatch.setattr(resources, "check_selected_access", authorize_vk)
    session = SimpleNamespace(scalar=AsyncMock(return_value=SimpleNamespace(owner_user_id=2, params={})))
    request = dict(tenant_id=1, actor_id=2, capability_id="vk.posts.read", direction=Direction.READ)
    choice = resources.VKSelection("user", credential_id=17)
    selected = resources.ResourceSelection(ResourceKind.SOCIAL_SOURCE, 7, vk_access=choice)
    result = await resources.resolve_task_resource(session, **request, selection=selected)
    assert result.selection.vk_access == choice
    authorize_vk.assert_awaited_once_with(choice, tenant_id=1, actor_id=2, required_methods=frozenset({"wall.get"}))
    authorize_vk.return_value = SimpleNamespace(allowed=False)
    assert (
        await resources.resolve_task_resource(session, **request, selection=selected)
        == resources.ResolutionIssue.REAUTH
    )


@pytest.mark.asyncio
async def test_paired_root_reauthorizes_and_does_not_substitute_another_root(monkeypatch):
    monkeypatch.setattr(resources, "_authorize", AsyncMock())
    check = AsyncMock()
    monkeypatch.setattr(resources, "get_gateway", lambda: SimpleNamespace(store=SimpleNamespace(authorize=check)))
    choice = resources.ResourceSelection(ResourceKind.PAIRED_ROOT, 7, "root-1")
    request = dict(tenant_id=1, actor_id=2, capability_id="local_gateway.search", direction=Direction.READ)
    resolved = await resources.resolve_task_resource(None, **request, selection=choice)
    assert resolved.selection == choice
    check.assert_awaited_once_with(owner_id=2, device_id=7, roots=("root-1",))
    check.side_effect = resources.ConnectorUnavailable
    assert (
        await resources.resolve_task_resource(None, **request, selection=choice)
        == resources.ResolutionIssue.UNAVAILABLE
    )


@pytest.mark.asyncio
async def test_vk_installation_is_checked_again_on_each_resolution(monkeypatch):
    monkeypatch.setattr(resources, "_authorize", AsyncMock())
    authorize_vk = AsyncMock(return_value=SimpleNamespace(allowed=True))
    monkeypatch.setattr(resources, "check_selected_access", authorize_vk)
    monkeypatch.setattr(resources.settings, "VK_SERVICE_KEY", None)
    session = SimpleNamespace(scalar=AsyncMock(return_value=SimpleNamespace(owner_user_id=None, params={})))
    choice = resources.VKSelection("api")
    selection = resources.ResourceSelection(ResourceKind.SOCIAL_SOURCE, 7, vk_access=choice)
    request = dict(
        tenant_id=1, actor_id=2, capability_id="vk.posts.read", direction=Direction.READ, selection=selection
    )
    assert await resources.resolve_task_resource(session, **request) == resources.ResolutionIssue.INSTALLATION
    authorize_vk.assert_not_awaited()

    monkeypatch.setattr(resources.settings, "VK_SERVICE_KEY", "fake-installation-key")
    assert await resources.resolve_task_resource(session, **request) == resources.ScopedResource(
        1, 2, "vk.posts.read", selection
    )
    authorize_vk.assert_awaited_once_with(choice, tenant_id=1, actor_id=2, required_methods=frozenset({"wall.get"}))

    monkeypatch.setattr(resources.settings, "VK_SERVICE_KEY", None)
    assert await resources.resolve_task_resource(session, **request) == resources.ResolutionIssue.INSTALLATION


@pytest.mark.asyncio
async def test_telegram_requires_current_runtime_and_exact_source_grant(monkeypatch):
    monkeypatch.setattr(resources, "_authorize", AsyncMock())
    inspect_grant = AsyncMock()
    monkeypatch.setattr(resources, "_inspect_source_grant", inspect_grant)
    monkeypatch.setattr(resources.settings, "TELEGRAM_ACCOUNT_RUNTIME_ENABLED", False)
    source = SimpleNamespace(
        id=7,
        tenant_id=1,
        platform_id=5,
        external_id="test-channel",
        owner_user_id=2,
        params={"mode": "user", "telegram_authorization": {"grant_id": 17, "authorization_revision": 3}},
    )
    session = SimpleNamespace(scalar=AsyncMock(return_value=source))
    selection = resources.ResourceSelection(ResourceKind.SOCIAL_SOURCE, 7)
    request = dict(
        tenant_id=1, actor_id=2, capability_id="telegram.session.read", direction=Direction.READ, selection=selection
    )
    assert await resources.resolve_task_resource(session, **request) == resources.ResolutionIssue.UNAVAILABLE
    inspect_grant.assert_not_awaited()

    monkeypatch.setattr(resources.settings, "TELEGRAM_ACCOUNT_RUNTIME_ENABLED", True)
    assert await resources.resolve_task_resource(session, **request) == resources.ScopedResource(
        1, 2, "telegram.session.read", selection
    )
    inspect_grant.assert_awaited_once_with(session, resources.source_grant_selection(source))
    inspect_grant.side_effect = resources.AuthorizationUnavailable
    assert await resources.resolve_task_resource(session, **request) == resources.ResolutionIssue.REAUTH

    inspect_grant.reset_mock()
    source.owner_user_id = 9
    assert await resources.resolve_task_resource(session, **request) == resources.ResolutionIssue.UNAVAILABLE
    inspect_grant.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_root_value_is_rejected_before_gateway_lookup(monkeypatch):
    monkeypatch.setattr(resources, "_authorize", AsyncMock())
    selection = resources.ResourceSelection(ResourceKind.PAIRED_ROOT, 7, ["root-1"])
    assert (
        await resources.resolve_task_resource(
            None,
            tenant_id=1,
            actor_id=2,
            capability_id="local_gateway.search",
            direction=Direction.READ,
            selection=selection,
        )
        == resources.ResolutionIssue.SELECT_RESOURCE
    )
