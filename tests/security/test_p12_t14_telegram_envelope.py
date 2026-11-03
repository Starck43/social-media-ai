"""Owned synthetic PostgreSQL envelope scope; no provider client or real secrets."""

import asyncio
import json
import secrets
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, select, text, update

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.tenant_context import tenant_scope
from app.models import PersonalConnectorAuthorization as Authorization
from app.models import Platform, Source, TenantUser
from app.models import WorkspaceAuthorizationGrant as Grant
from app.services.social import connector_authorizations as accounts
from app.services.social import telegram_account_lock as locking
from app.services.social import telegram_grant_access as access
from app.services.social.connector_authorizations import AuthorizationUnavailable, VerifiedTelegramResult
from app.types import PlatformType, SourceType
from app.utils.crypto import decrypt_secret, encrypt_secret
from tests.shared.connector_accounts import connector_actor as actor
from tests.shared.connector_accounts import connector_data  # noqa: F401

pytestmark = pytest.mark.tenancy
MATERIAL = {"api_id": 123, "api_hash": "SYNTHETIC-ENVELOPE-HASH", "session": "SYNTHETIC-NOT-A-TELETHON-SESSION"}


@pytest.fixture
async def envelope_data(connector_data):
    with actor(connector_data) as args:
        account, grant = await accounts.record_telegram_result(
            **args, result=VerifiedTelegramResult(subject_id=90113, **MATERIAL)
        )
    async with async_session_maker() as session, session.begin():
        platform = (
            await session.execute(select(Platform).where(Platform.platform_type == PlatformType.TELEGRAM))
        ).scalar_one()
        source = Source(
            tenant_id=args["tenant_id"],
            platform_id=platform.id,
            owner_user_id=args["actor_id"],
            external_id="p12t14-envelope-" + secrets.token_hex(5),
            name="Synthetic envelope scope",
            source_type=SourceType.CHANNEL,
            is_active=True,
            params={
                "mode": "user",
                "telegram_authorization": {"grant_id": grant["grant_id"], "authorization_revision": 1},
            },
        )
        session.add(source)
        await session.flush()
    try:
        yield SimpleNamespace(
            fixture=connector_data,
            args=args,
            account=account,
            grant=grant,
            source=source,
            pin=access.source_grant_selection(source),
        )
    finally:
        async with async_session_maker() as session, session.begin():
            await session.execute(delete(Source).where(Source.id == source.id))


async def change(data, kind):
    async with async_session_maker() as session, session.begin():
        if kind == "detach":
            statement = update(Grant).where(Grant.id == data.grant["grant_id"]).values(is_active=False)
        elif kind == "member":
            statement = (
                update(TenantUser)
                .where(
                    TenantUser.tenant_id == data.pin.tenant_id,
                    TenantUser.user_id == data.pin.owner_user_id,
                )
                .values(is_active=False)
            )
        elif kind == "source":
            statement = update(Source).where(Source.id == data.pin.source_id).values(params={})
        else:
            values = {"state": "revoked", "secret_encrypted": ""} if kind == "revoke" else {"revision": 2}
            statement = (
                update(Authorization).where(Authorization.id == data.account["authorization_id"]).values(**values)
            )
        await session.execute(statement)


async def test_decryption_uses_existing_read_only_lock_and_scope_is_not_retained(envelope_data, monkeypatch, caplog):
    data = envelope_data
    resolve = access._resolve_source_grant_material
    calls = []

    async def checked(session, pin):
        actual = (
            await session.execute(
                text("SELECT current_setting('transaction_isolation'), current_setting('transaction_read_only')")
            )
        ).one()
        assert tuple(actual) == ("read committed", "on")
        async with async_session_maker() as observer, observer.begin():
            assert (
                await observer.scalar(
                    text("SELECT pg_try_advisory_xact_lock(:ns, :id)"),
                    {
                        "ns": 721215,
                        "id": data.account["authorization_id"],
                    },
                )
                is False
            )
        result = await resolve(session, pin)
        calls.append(result[1])
        return result

    monkeypatch.setattr(locking, "_resolve_source_grant_material", checked)
    with tenant_scope(data.pin.tenant_id):
        async with locking.locked_source_grant(data.pin) as guard:
            async with guard._setup_material() as material:
                assert (material.api_id, material.api_hash, material.session) == tuple(MATERIAL.values())
                assert repr(material) == "<TelegramSetupMaterial>"
            assert calls == [{}]
            with pytest.raises(AuthorizationUnavailable):
                _ = material.session
            assert (await guard.inspect()).authorization_id == data.account["authorization_id"]
    assert MATERIAL["session"] not in caplog.text and MATERIAL["api_hash"] not in caplog.text


@pytest.mark.parametrize("kind", ["detach", "member", "source", "revoke", "revision"])
async def test_changes_after_acquisition_deny_material_before_decryption(envelope_data, monkeypatch, kind):
    data = envelope_data

    def forbidden(*args):
        pytest.fail("no decryption after revoked pin")

    with tenant_scope(data.pin.tenant_id):
        async with locking.locked_source_grant(data.pin) as guard:
            await change(data, kind)
            monkeypatch.setattr(accounts, "decrypt_secret", forbidden)
            with pytest.raises(AuthorizationUnavailable):
                async with guard._setup_material():
                    pytest.fail("changed pin must not yield")
            with pytest.raises(AuthorizationUnavailable):
                await guard.inspect()


@pytest.mark.parametrize("kind", ["detach", "member", "source", "revoke", "revision"])
async def test_changes_after_decryption_deny_yield_and_clear_retained_material(envelope_data, monkeypatch, kind):
    data = envelope_data
    resolve = access._resolve_source_grant_material
    retained = []

    async def changed(session, pin):
        result = await resolve(session, pin)
        retained.append(result[1])
        await change(data, kind)
        return result

    monkeypatch.setattr(locking, "_resolve_source_grant_material", changed)
    with tenant_scope(data.pin.tenant_id):
        async with locking.locked_source_grant(data.pin) as guard:
            with pytest.raises(AuthorizationUnavailable):
                async with guard._setup_material():
                    pytest.fail("post-decryption recheck must deny yield")
            assert retained == [{}]
            with pytest.raises(AuthorizationUnavailable):
                await guard.inspect()


@pytest.mark.parametrize("corruption", ["ciphertext", "identity", "wrong_key", "material"])
async def test_unreadable_or_substituted_envelope_invalidates_guard(envelope_data, monkeypatch, corruption, caplog):
    data = envelope_data
    with tenant_scope(data.pin.tenant_id):
        async with locking.locked_source_grant(data.pin) as guard:
            async with async_session_maker() as session, session.begin():
                row = await session.get(Authorization, data.account["authorization_id"])
                if corruption == "ciphertext":
                    row.secret_encrypted = "SYNTHETIC-UNREADABLE-CIPHERTEXT"
                elif corruption == "wrong_key":
                    monkeypatch.setattr(settings, "CREDENTIALS_KEY", Fernet.generate_key().decode())
                else:
                    payload = json.loads(decrypt_secret(row.secret_encrypted))
                    if corruption == "identity":
                        payload["identity"]["owner_user_id"] += 1
                    else:
                        payload["material"]["session"] = ""
                    row.secret_encrypted = encrypt_secret(json.dumps(payload))
            with pytest.raises(AuthorizationUnavailable, match="^connection_unavailable$"):
                async with guard._setup_material():
                    pytest.fail("invalid envelope")
            with pytest.raises(AuthorizationUnavailable):
                await guard.inspect()
    assert "SYNTHETIC" not in caplog.text


async def test_repeated_scope_does_not_reuse_identity_map_ciphertext(envelope_data):
    data = envelope_data
    with tenant_scope(data.pin.tenant_id):
        async with locking.locked_source_grant(data.pin) as guard:
            # Keep an ORM identity alive to prove populate_existing refreshes it.
            cached = await guard._session.get(Authorization, data.account["authorization_id"])
            async with guard._setup_material() as first:
                assert first.session == MATERIAL["session"]
            async with async_session_maker() as session, session.begin():
                await session.execute(
                    update(Authorization)
                    .where(Authorization.id == cached.id)
                    .values(secret_encrypted="SYNTHETIC-CORRUPT-AFTER-FIRST-SCOPE")
                )
            with pytest.raises(AuthorizationUnavailable):
                async with guard._setup_material():
                    pytest.fail("no cached ciphertext reuse")
            assert cached.secret_encrypted == "SYNTHETIC-CORRUPT-AFTER-FIRST-SCOPE"


async def test_cancellation_after_decryption_clears_material_and_releases_account(envelope_data, monkeypatch):
    data = envelope_data
    resolve = access._resolve_source_grant_material
    retained = []
    with tenant_scope(data.pin.tenant_id):
        with pytest.raises(asyncio.CancelledError):
            async with locking.locked_source_grant(data.pin) as guard:

                async def resolved(session, pin):
                    result = await resolve(session, pin)
                    retained.append(result[1])
                    return result

                async def cancelled():
                    raise asyncio.CancelledError()

                monkeypatch.setattr(locking, "_resolve_source_grant_material", resolved)
                monkeypatch.setattr(guard, "inspect", cancelled)
                async with guard._setup_material():
                    pytest.fail("cancelled check cannot yield")
        assert retained == [{}]
        async with locking.locked_source_grant(data.pin) as later, later._setup_material() as material:
            assert material.api_id == 123


async def test_rebound_grant_never_loads_or_decrypts_new_account_under_old_lock(envelope_data, monkeypatch):
    data = envelope_data
    with tenant_scope(data.pin.tenant_id):
        async with locking.locked_source_grant(data.pin) as guard:
            with actor(data.fixture, tenant=1) as args:
                other, _ = await accounts.record_telegram_result(
                    **args, result=VerifiedTelegramResult(subject_id=90114, **MATERIAL)
                )
            async with async_session_maker() as session, session.begin():
                await session.execute(
                    update(Grant)
                    .where(Grant.id == data.grant["grant_id"])
                    .values(authorization_id=other["authorization_id"])
                )

            def forbidden(*args):
                pytest.fail("new account cannot decrypt under old account lock")

            monkeypatch.setattr(accounts, "decrypt_secret", forbidden)
            # Raw ORM absence under the pinned joined query proves ciphertext was
            # not selected, rather than merely rejecting it after decryption.
            with pytest.raises(AuthorizationUnavailable):
                await access._read_source_grant(
                    guard._session,
                    data.pin,
                    include_authorization=True,
                    expected_authorization_id=data.account["authorization_id"],
                )
            with pytest.raises(AuthorizationUnavailable):
                async with guard._setup_material():
                    pytest.fail("rebound account")
