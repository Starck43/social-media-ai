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


@pytest.mark.parametrize("kind", ["detach", "member", "source", "revoke", "revision"])
async def test_admission_rejects_changes_before_gate_without_write(envelope_data, monkeypatch, kind):
    data = envelope_data

    def forbidden(*args):
        pytest.fail("admission must not decrypt")

    monkeypatch.setattr(accounts, "decrypt_secret", forbidden)
    with tenant_scope(data.pin.tenant_id):
        async with locking.locked_source_grant(data.pin) as guard:
            await change(data, kind)
            with pytest.raises(AuthorizationUnavailable):
                async with guard._admission_transaction():
                    pytest.fail("changed consent")


@pytest.mark.parametrize("target", ["account", "grant", "source", "platform", "tenant", "user", "member", "role"])
async def test_admission_holds_every_mutable_consent_row_until_commit(envelope_data, target):
    from sqlalchemy.exc import DBAPIError

    from app.models import Tenant, User, WorkspaceRole

    data = envelope_data
    statements = {
        "account": select(Authorization.id).where(Authorization.id == data.account["authorization_id"]),
        "grant": select(Grant.id).where(Grant.id == data.grant["grant_id"]),
        "source": select(Source.id).where(Source.id == data.pin.source_id),
        "platform": select(Platform.id).where(Platform.id == data.pin.platform_id),
        "tenant": select(Tenant.id).where(Tenant.id == data.pin.tenant_id),
        "user": select(User.id).where(User.id == data.pin.owner_user_id),
        "member": select(TenantUser.user_id).where(
            TenantUser.tenant_id == data.pin.tenant_id,
            TenantUser.user_id == data.pin.owner_user_id,
        ),
        "role": select(WorkspaceRole.id).where(WorkspaceRole.id == data.fixture.roles["manager"]),
    }
    statement = statements[target].with_for_update(nowait=True)
    with tenant_scope(data.pin.tenant_id):
        async with locking.locked_source_grant(data.pin) as guard:
            async with guard._admission_transaction() as receipt:
                with pytest.raises(DBAPIError) as error:
                    async with async_session_maker() as observer, observer.begin():
                        await observer.execute(statement)
                assert error.value.orig.sqlstate == "55P03"
                await receipt.execute(update(Source).where(Source.id == data.pin.source_id).values(last_item_id="998"))
            # Same-transaction receipt committed, not just a successful check.
            async with async_session_maker() as observer, observer.begin():
                assert (await observer.execute(statement)).scalar_one_or_none() is not None
                assert (
                    await observer.scalar(select(Source.last_item_id).where(Source.id == data.pin.source_id)) == "998"
                )


@pytest.mark.parametrize("operation", ["detach", "revoke"])
async def test_service_withdrawal_waits_for_receipt_commit_then_denies_future_admission(envelope_data, operation):
    data = envelope_data
    pending = None

    async def withdraw():
        with actor(data.fixture) as args:
            if operation == "detach":
                return await accounts.connector_authorizations.detach(**args, grant_id=data.grant["grant_id"])
            return await accounts.connector_authorizations.revoke(
                **args,
                authorization_id=data.account["authorization_id"],
                expected_revision=1,
            )

    try:
        with tenant_scope(data.pin.tenant_id):
            async with locking.locked_source_grant(data.pin) as guard:
                async with guard._admission_transaction() as receipt:
                    pid = await receipt.scalar(text("SELECT pg_backend_pid()"))
                    pending = asyncio.create_task(withdraw())
                    async with asyncio.timeout(3):
                        while True:
                            async with async_session_maker() as observer:
                                blocked = await observer.scalar(
                                    text(
                                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                        "WHERE datname=current_database() AND :pid=ANY(pg_blocking_pids(pid)))"
                                    ),
                                    {"pid": pid},
                                )
                            if blocked:
                                break
                            await asyncio.sleep(0.01)
                    assert not pending.done()
                    await receipt.execute(
                        update(Source).where(Source.id == data.pin.source_id).values(last_item_id="999")
                    )
                result = await asyncio.wait_for(pending, timeout=3)
                assert result["is_active"] is False if operation == "detach" else result["state"] == "revoked"
                with pytest.raises(AuthorizationUnavailable):
                    async with guard._admission_transaction():
                        pytest.fail("withdrawn consent")
        async with async_session_maker() as observer:
            assert await observer.scalar(select(Source.last_item_id).where(Source.id == data.pin.source_id)) == "999"
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("failure", [RuntimeError("synthetic receipt failure"), asyncio.CancelledError()])
async def test_admission_body_failure_rolls_back_synthetic_receipt(envelope_data, failure):
    data = envelope_data
    with tenant_scope(data.pin.tenant_id):
        async with locking.locked_source_grant(data.pin) as guard:
            with pytest.raises(type(failure)):
                async with guard._admission_transaction() as receipt:
                    await receipt.execute(
                        update(Source).where(Source.id == data.pin.source_id).values(last_item_id="997")
                    )
                    raise failure
            with pytest.raises(AuthorizationUnavailable):
                await guard.inspect()
    async with async_session_maker() as observer:
        assert await observer.scalar(select(Source.last_item_id).where(Source.id == data.pin.source_id)) != "997"


async def test_admission_cannot_commit_transaction_local_source_retarget(envelope_data):
    data = envelope_data
    with tenant_scope(data.pin.tenant_id):
        async with locking.locked_source_grant(data.pin) as guard:
            with pytest.raises(AuthorizationUnavailable):
                async with guard._admission_transaction() as receipt:
                    await receipt.execute(
                        update(Source).where(Source.id == data.pin.source_id).values(params={}, last_item_id="996")
                    )
    async with async_session_maker() as observer:
        row = await observer.get(Source, data.pin.source_id)
        assert access.source_grant_selection(row) == data.pin and row.last_item_id != "996"


@pytest.mark.parametrize("kind", ["revoke", "detach", "member", "source"])
async def test_admission_wait_observes_committed_withdrawal_instead_of_old_snapshot(envelope_data, kind):
    data = envelope_data
    ready, proceed = asyncio.Event(), asyncio.Event()
    pending = None

    async def admission_attempt():
        with tenant_scope(data.pin.tenant_id):
            async with locking.locked_source_grant(data.pin) as guard:
                ready.set()
                await proceed.wait()
                with pytest.raises(AuthorizationUnavailable):
                    async with guard._admission_transaction():
                        pytest.fail("withdrawal committed while gate was waiting")

    try:
        pending = asyncio.create_task(admission_attempt())
        await asyncio.wait_for(ready.wait(), timeout=3)
        async with async_session_maker() as blocker, blocker.begin():
            if kind == "revoke":
                stmt = (
                    update(Authorization)
                    .where(Authorization.id == data.account["authorization_id"])
                    .values(state="revoked")
                )
            elif kind == "detach":
                stmt = update(Grant).where(Grant.id == data.grant["grant_id"]).values(is_active=False)
            elif kind == "member":
                stmt = (
                    update(TenantUser)
                    .where(
                        TenantUser.tenant_id == data.pin.tenant_id,
                        TenantUser.user_id == data.pin.owner_user_id,
                    )
                    .values(is_active=False)
                )
            else:
                stmt = update(Source).where(Source.id == data.pin.source_id).values(params={})
            await blocker.execute(stmt)
            pid = await blocker.scalar(text("SELECT pg_backend_pid()"))
            proceed.set()
            async with asyncio.timeout(3):
                while True:
                    async with async_session_maker() as observer:
                        blocked = await observer.scalar(
                            text(
                                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                "WHERE datname=current_database() AND :pid=ANY(pg_blocking_pids(pid)))"
                            ),
                            {"pid": pid},
                        )
                    if blocked:
                        break
                    await asyncio.sleep(0.01)
            assert not pending.done()
        await asyncio.wait_for(pending, timeout=3)
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


async def test_admission_lock_timeout_invalidates_without_replay(envelope_data, monkeypatch):
    data = envelope_data
    gate = access._lock_source_grant_for_admission
    calls = []

    async def short_timeout(session, snapshot):
        calls.append(snapshot)
        await session.execute(text("SET LOCAL lock_timeout = '50ms'"))
        return await gate(session, snapshot)

    monkeypatch.setattr(locking, "_lock_source_grant_for_admission", short_timeout)
    with tenant_scope(data.pin.tenant_id):
        async with locking.locked_source_grant(data.pin) as guard:
            async with async_session_maker() as blocker, blocker.begin():
                await blocker.execute(
                    select(Authorization.id)
                    .where(Authorization.id == data.account["authorization_id"])
                    .with_for_update()
                )
                with pytest.raises(AuthorizationUnavailable):
                    async with guard._admission_transaction():
                        pytest.fail("lock timeout cannot admit")
            with pytest.raises(AuthorizationUnavailable):
                async with guard._admission_transaction():
                    pytest.fail("timed out guard cannot retry")
            assert len(calls) == 1


async def test_lost_account_lock_before_commit_rolls_back_synthetic_receipt(envelope_data):
    data = envelope_data
    with tenant_scope(data.pin.tenant_id), pytest.raises(AuthorizationUnavailable):
        async with locking.locked_source_grant(data.pin) as guard, guard._admission_transaction() as receipt:
            await receipt.execute(update(Source).where(Source.id == data.pin.source_id).values(last_item_id="995"))
            pid = await guard._session.scalar(text("SELECT pg_backend_pid()"))
            async with async_session_maker() as observer:
                # Never terminate a backend outside this fixture's database.
                terminated = await observer.scalar(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE pid=:pid AND datname=current_database()"
                    ),
                    {"pid": pid},
                )
                assert terminated is True
    async with async_session_maker() as observer:
        assert await observer.scalar(select(Source.last_item_id).where(Source.id == data.pin.source_id)) != "995"
    with tenant_scope(data.pin.tenant_id):
        async with locking.locked_source_grant(data.pin) as later:
            assert (await later.inspect()).authorization_id == data.account["authorization_id"]
