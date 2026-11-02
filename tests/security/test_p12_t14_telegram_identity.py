"""Personal MTProto sessions cannot borrow a deployment or another user's identity."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.models.managers.tenant_manager import TenantUserManager
from app.models.managers.user_credential_manager import user_credentials
from app.services.social.credentials import AuthorizationRequired
from app.services.social.tg_client import TelegramClient
from app.services.social.tg_session import load_session
from tests.collection.test_source_owner import _deactivate_web_membership, make_user, workspace  # noqa: F401


@pytest.fixture
def deployment_session(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_API_ID", "100")
    monkeypatch.setattr(settings, "TELEGRAM_API_HASH", "FAKE_DEPLOYMENT_HASH")
    monkeypatch.setattr(settings, "TELEGRAM_SESSION", "FAKE_DEPLOYMENT_SESSION")


def source(workspace, owner=None, params=None):
    return SimpleNamespace(id=7, name="synthetic", tenant_id=workspace.id, owner_user_id=owner, params=params or {})


async def put(user_id, kind, value):
    await user_credentials.store(user_id=user_id, platform="telegram", kind=kind, secret=value)


async def test_partial_vault_cannot_borrow_env_or_another_user(workspace, make_user, deployment_session):
    first, second = await make_user(), await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=first.id, role="viewer")
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=second.id, role="viewer")
    await put(first.id, "api_id", "101")
    await put(first.id, "api_hash", "FAKE_FIRST_HASH")
    for kind, value in (("api_id", "102"), ("api_hash", "FAKE_SECOND_HASH"), ("session", "FAKE_SECOND_SESSION")):
        await put(second.id, kind, value)
    assert await load_session(user_id=first.id) is None
    other = await load_session(user_id=second.id)
    assert other.api_id == 102 and other.api_hash == "FAKE_SECOND_HASH" and other.session == "FAKE_SECOND_SESSION"
    legacy = await load_session()
    assert legacy.api_id == 100 and legacy.session == "FAKE_DEPLOYMENT_SESSION"


async def test_owned_auto_checks_owner_session_not_global(workspace, make_user, deployment_session, monkeypatch):
    user = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=user.id, role="viewer")
    client = TelegramClient(SimpleNamespace(params={}))
    pull = AsyncMock(side_effect=AssertionError("no personal session, no MTProto pull"))
    monkeypatch.setattr(client, "_collect_mtproto", pull)
    assert await client.collect_data(source(workspace, user.id, {"mode": "auto"})) == []
    pull.assert_not_awaited()
    with pytest.raises(AuthorizationRequired):
        await TelegramClient(SimpleNamespace(params={})).collect_data(source(workspace, user.id, {"mode": "user"}))


@pytest.mark.parametrize("mode", ["auto", "user"])
async def test_revoked_or_forged_owner_cannot_use_global_session(
    workspace, make_user, deployment_session, monkeypatch, mode
):
    owner, outsider = await make_user(), await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=owner.id, role="viewer")
    await _deactivate_web_membership(workspace.id, owner.id)
    import app.services.social.tg_session as sessions

    loader = AsyncMock(side_effect=AssertionError("must reject before loading any session"))
    monkeypatch.setattr(sessions, "load_session", loader)
    client = TelegramClient(SimpleNamespace(params={}))
    for item in (
        source(workspace, owner.id, {"mode": mode}),
        source(workspace, None, {"mode": mode, "token_owner": outsider.id}),
        source(workspace, owner.id, {"mode": mode, "token_owner": 0}),
    ):
        with pytest.raises(AuthorizationRequired):
            await client.collect_data(item)
    loader.assert_not_awaited()


async def test_ownerless_legacy_session_remains_visible_and_logged(workspace, deployment_session, caplog):
    client = TelegramClient(SimpleNamespace(params={}))
    with caplog.at_level("WARNING"):
        resolved = await client._source_session(source(workspace))
    assert resolved.session == "FAKE_DEPLOYMENT_SESSION"
    assert "legacy deployment-wide session" in caplog.text
    assert "FAKE_DEPLOYMENT_SESSION" not in caplog.text
    assert "FAKE_DEPLOYMENT_HASH" not in caplog.text


@pytest.mark.parametrize("mode", ["auto", "user"])
async def test_owned_source_pulls_only_its_complete_personal_session(
    workspace, make_user, deployment_session, monkeypatch, mode
):
    import app.services.social.tg_session as sessions

    user = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=user.id, role="viewer")
    for kind, value in (("api_id", "102"), ("api_hash", "FAKE_PERSONAL_HASH"), ("session", "FAKE_PERSONAL_SESSION")):
        await put(user.id, kind, value)
    used = []

    class FakeTransport:
        async def connect(self):
            pass

        async def is_user_authorized(self):
            return True

        async def disconnect(self):
            pass

        def iter_messages(self, entity, **kwargs):
            async def entries():
                yield SimpleNamespace(id=99)

            return entries()

    def build(session):
        used.append(session.session)
        return FakeTransport()

    monkeypatch.setattr(sessions, "build_client", build)
    client = TelegramClient(SimpleNamespace(params={}))
    monkeypatch.setattr(client, "_resolve_entity", AsyncMock(return_value="chat"))
    monkeypatch.setattr(client, "_normalize_message", lambda message, row: {"id": message.id})
    assert await client.collect_data(source(workspace, user.id, {"mode": mode})) == [{"id": 99}]
    assert used == ["FAKE_PERSONAL_SESSION"]


async def test_auto_rechecks_owner_before_mtproto_after_revocation(
    workspace, make_user, deployment_session, monkeypatch
):
    user = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=user.id, role="viewer")
    for kind, value in (("api_id", "102"), ("api_hash", "FAKE_PERSONAL_HASH"), ("session", "FAKE_PERSONAL_SESSION")):
        await put(user.id, kind, value)
    client = TelegramClient(SimpleNamespace(params={}))
    original = client._collect_mtproto

    async def revoke_then_pull(row):
        await _deactivate_web_membership(workspace.id, user.id)
        return await original(row)

    monkeypatch.setattr(client, "_collect_mtproto", revoke_then_pull)
    with pytest.raises(AuthorizationRequired):
        await client.collect_data(source(workspace, user.id, {"mode": "auto"}))
