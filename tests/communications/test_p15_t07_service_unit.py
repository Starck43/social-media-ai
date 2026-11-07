"""DB-exempt recommendation authorization and route checks."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event
from sqlalchemy.engine.interfaces import Dialect

from app.models.managers.contact_manager import ContactUnavailable
from app.services.communications import recommend

NOW = datetime(2026, 11, 7, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_db():
    def forbidden(*args, **kwargs):
        raise AssertionError("DB access forbidden")

    event.listen(Dialect, "do_connect", forbidden)
    yield
    event.remove(Dialect, "do_connect", forbidden)


@pytest.fixture
def actor(monkeypatch):
    from app.core import permissions, tenant_context

    person = SimpleNamespace(id=9, is_active=True)
    monkeypatch.setattr(tenant_context, "current_tenant_id", lambda: 7)
    monkeypatch.setattr(tenant_context, "is_bypass", lambda: False)
    monkeypatch.setattr(permissions, "get_current_user", lambda: person)
    monkeypatch.setattr(permissions, "has_permission", lambda *args: True)
    return person


def session_for(*, role="owner", contact=True, pair=(10, 20), stat=True):
    contact_row = SimpleNamespace(preferred_channel="email") if contact else None
    stats = SimpleNamespace(inbound_count=3, outbound_count=2, last_inbound_at=NOW) if stat else None
    values = [9, 7, SimpleNamespace(role_id=2), role, contact_row, stats]
    return SimpleNamespace(
        scalar=AsyncMock(side_effect=values),
        execute=AsyncMock(return_value=SimpleNamespace(first=lambda: pair)),
    )


@pytest.mark.asyncio
async def test_email_route_requires_real_consent_and_uses_live_role(actor, monkeypatch):
    from app.services.communications import outbox_store

    route = AsyncMock(
        return_value=(
            "not-returned",
            "secret-hash-not-returned",
            SimpleNamespace(config={"send_enabled": True, "account_login": "owner@example.com"}),
            object(),
        )
    )
    monkeypatch.setattr(outbox_store, "resolve_route", route)
    session = session_for()
    result = await recommend.recommend_channel(session, 4, "marketing", now=NOW)
    assert result.channel == "email" and "manual_preference" in result.reasons
    assert "not-returned" not in repr(result) and "secret" not in repr(result)
    message, target = route.await_args.args[1:3]
    assert (message.tenant_id, message.author_id, message.contact_id, message.purpose) == (7, 9, 4, "marketing")
    assert (target.channel, target.identity_id, target.connection_id) == ("email", 10, 20)
    assert session.scalar.await_args_list[2].args[0]._for_update_arg is not None
    assert session.scalar.await_args_list[4].args[0]._for_update_arg is not None
    pair_query = session.execute.await_args.args[0]
    assert "tenant_id" in str(pair_query) and "owner_user_id" in str(pair_query)


@pytest.mark.asyncio
async def test_missing_route_or_withdrawn_consent_is_never_recommended(actor, monkeypatch):
    from app.services.communications import outbox_store

    route = AsyncMock(side_effect=PermissionError("withdrawn"))
    monkeypatch.setattr(outbox_store, "resolve_route", route)
    assert (await recommend.recommend_channel(session_for(), 4, "greeting", now=NOW)).channel is None
    route.reset_mock()
    assert (await recommend.recommend_channel(session_for(pair=None), 4, "greeting", now=NOW)).channel is None
    route.assert_not_awaited()


@pytest.mark.asyncio
async def test_cross_tenant_missing_contact_and_non_owner_role_fail_closed(actor, monkeypatch):
    session = session_for(contact=False)
    with pytest.raises(ContactUnavailable):
        await recommend.recommend_channel(session, 999, "transactional", now=NOW)
    session.execute.assert_not_awaited()
    session = session_for(role="manager")
    with pytest.raises(PermissionError):
        await recommend.recommend_channel(session, 4, "transactional", now=NOW)
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_inactive_or_revoked_membership_denied_even_if_session_scope_grants(actor):
    session = SimpleNamespace(scalar=AsyncMock(side_effect=[9, 7, None]))
    with pytest.raises(PermissionError):
        await recommend.recommend_channel(session, 4, "transactional", now=NOW)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config",
    [
        None,
        {},
        {"send_enabled": False, "account_login": "owner@example.com"},
        {"send_enabled": True, "account_login": "invalid"},
    ],
)
async def test_disabled_or_invalid_sender_never_recommended(actor, monkeypatch, config):
    from app.services.communications import outbox_store

    monkeypatch.setattr(
        outbox_store,
        "resolve_route",
        AsyncMock(return_value=("encrypted", "hash", SimpleNamespace(config=config), object())),
    )
    assert (await recommend.recommend_channel(session_for(), 4, "transactional", now=NOW)).channel is None
