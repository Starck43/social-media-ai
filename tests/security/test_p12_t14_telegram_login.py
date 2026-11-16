"""Durable login fences and permissions with a fake provider, never Telegram I/O."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select, update

from app.core.config import settings
from app.core.database import async_session_maker
from app.models import PersonalConnectorAuthorization as Authorization
from app.models import TelegramLoginAttempt as Attempt
from app.models import TenantUser
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.social import telegram_login as service
from app.services.social.connector_authorizations import VerifiedTelegramResult, connector_authorizations
from app.services.social.telegram_login_provider import LoginProgress, TelegramProviderFailure
from tests.shared.connector_accounts import connector_actor as actor
from tests.shared.connector_accounts import connector_data  # noqa: F401
from tests.shared.telegram_login import HASH, PHONE, SESSION, login_provider, start_login  # noqa: F401

pytestmark = pytest.mark.tenancy


async def persisted(attempt_id):
    async with async_session_maker() as session:
        return await session.get(Attempt, attempt_id)


async def submit(args, row, operation="code", secret="12345"):
    return await service.telegram_logins.submit(
        **args, attempt_id=row["attempt_id"], expected_revision=row["revision"], operation=operation, secret=secret
    )


async def test_encrypted_code_2fa_completion_and_explicit_other_workspace_reuse(
    connector_data, login_provider, monkeypatch, caplog
):
    with actor(connector_data) as args:
        started = await start_login(args)
        saved = await persisted(started["attempt_id"])
        assert saved.secret_encrypted and all(v not in saved.secret_encrypted for v in (PHONE, SESSION, HASH))

        async def twofa(operation, material, secret=None):
            assert operation == "code" and secret == "12345"
            return LoginProgress("password", SESSION)

        monkeypatch.setattr(service, "telegram_login_step", twofa)
        password = await submit(args, started)
        assert password["state"] == "password"
        assert password["revision"] > started["revision"]

        async def verified(operation, material, secret=None):
            assert operation == "password" and secret == "  SYNTH-PASSWORD  "
            return LoginProgress("authorized", result=VerifiedTelegramResult(90001, 12345, HASH, SESSION))

        monkeypatch.setattr(service, "telegram_login_step", verified)
        done = await submit(args, password, "password", "  SYNTH-PASSWORD  ")
        assert done["state"] == "completed" and done["authorization_id"] and done["grant_id"]
        assert (await persisted(done["attempt_id"])).secret_encrypted == ""
    with actor(connector_data, tenant=1) as target:
        assert await connector_authorizations.list_grants(**target) == []
        account = (await connector_authorizations.list_reusable(**target))[0][0]
        grant = await connector_authorizations.attach(
            **target, authorization_id=account["authorization_id"], expected_revision=account["revision"]
        )
        assert grant["authorization_id"] == done["authorization_id"]
    assert all(v not in caplog.text for v in (PHONE, SESSION, HASH, "SYNTH-PASSWORD"))


async def test_idempotent_start_same_key_never_sends_code_again(connector_data, login_provider):
    key = uuid4()
    with actor(connector_data) as args:
        first = await service.telegram_logins.start(**args, request_id=key, phone=PHONE)
        repeated = await service.telegram_logins.start(**args, request_id=key, phone="+15555559999")
        assert repeated == first
        with pytest.raises(service.LoginRejected, match="already_pending"):
            await start_login(args)
    with actor(connector_data, tenant=1) as args:
        with pytest.raises(service.LoginRejected):
            await service.telegram_logins.start(**args, request_id=key, phone=PHONE)
    assert login_provider == ["begin"]


@pytest.mark.parametrize("foreign", [{"user": 1}, {"tenant": 1}])
async def test_foreign_attempt_ids_never_reveal_or_use_state(connector_data, login_provider, foreign):
    with actor(connector_data) as args:
        started = await start_login(args)
    with actor(connector_data, **foreign) as args:
        for action in ("status", "cancel"):
            with pytest.raises(ConnectionDenied):
                await getattr(service.telegram_logins, action)(**args, attempt_id=started["attempt_id"])
        with pytest.raises(ConnectionDenied):
            await submit(args, started)
    assert login_provider == ["begin"]


async def test_concurrent_submit_claimed_once_cancel_fences_inflight_completion(
    connector_data, login_provider, monkeypatch
):
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(operation, material, secret=None):
        entered.set()
        await release.wait()
        return LoginProgress("authorized", result=VerifiedTelegramResult(90001, 12345, HASH, SESSION))

    with actor(connector_data) as args:
        started = await start_login(args)
        monkeypatch.setattr(service, "telegram_login_step", blocked)
        task = asyncio.create_task(submit(args, started))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert (await persisted(started["attempt_id"])).state == "processing"
            with pytest.raises(service.LoginRejected):
                await submit(args, started)
            cancelled = await service.telegram_logins.cancel(**args, attempt_id=started["attempt_id"])
            assert cancelled["state"] == "cancelled"
        finally:
            release.set()
        assert (await task)["state"] == "cancelled"
        assert (await connector_authorizations.list_reusable(**args))[0] == []
    assert (await persisted(started["attempt_id"])).secret_encrypted == ""


async def test_membership_revoked_during_provider_cannot_install_session(connector_data, login_provider, monkeypatch):
    async def revoked(operation, material, secret=None):
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(TenantUser)
                .where(
                    TenantUser.user_id == connector_data.users[0].id,
                    TenantUser.tenant_id == connector_data.tenants[0].id,
                )
                .values(is_active=False)
            )
        return LoginProgress("authorized", result=VerifiedTelegramResult(90001, 12345, HASH, SESSION))

    with actor(connector_data) as args:
        started = await start_login(args)
        monkeypatch.setattr(service, "telegram_login_step", revoked)
        result = await submit(args, started)
        assert result["state"] == "failed" and result["failure_code"] == "access_revoked"
    async with async_session_maker() as session:
        assert (
            not (
                await session.execute(
                    select(Authorization.id).where(Authorization.owner_user_id == connector_data.users[0].id)
                )
            )
            .scalars()
            .all()
        )
    assert (await persisted(started["attempt_id"])).secret_encrypted == ""


@pytest.mark.parametrize("damage", ["key", "ciphertext", "identity"])
async def test_unreadable_or_swapped_state_fails_before_provider(connector_data, login_provider, monkeypatch, damage):
    with actor(connector_data) as args:
        started = await start_login(args)
        if damage == "key":
            monkeypatch.setattr(settings, "CREDENTIALS_KEY", Fernet.generate_key().decode())
        else:
            async with async_session_maker() as session, session.begin():
                row = await session.get(Attempt, started["attempt_id"])
                if damage == "ciphertext":
                    row.secret_encrypted = "corrupt"
                else:
                    row.revision += 1
                    started["revision"] += 1
        failed = await submit(args, started)
        assert failed["failure_code"] == "secret_unavailable"
    assert login_provider == ["begin"]
    assert (await persisted(started["attempt_id"])).secret_encrypted == ""


async def test_ttl_expires_and_wipes_without_provider_or_restarting_ttl(connector_data, login_provider):
    with actor(connector_data) as args:
        started = await start_login(args)
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(Attempt)
                .where(Attempt.id == started["attempt_id"])
                .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
            )
        result = await submit(args, started)
        assert result["state"] == "expired"
        assert (await persisted(started["attempt_id"])).secret_encrypted == ""
        newer = await start_login(args)
        assert newer["attempt_id"] != started["attempt_id"]
    assert login_provider == ["begin", "begin"]


async def test_unknown_outcome_is_terminal_not_replayed(connector_data, login_provider, monkeypatch, caplog):
    async def broken(*args):
        raise RuntimeError("SYNTHETIC-PRIVATE-CODE-SECRET")

    with actor(connector_data) as args:
        started = await start_login(args)
        monkeypatch.setattr(service, "telegram_login_step", broken)
        result = await submit(args, started)
        assert result["state"] == "failed" and result["failure_code"] == "provider_outcome_unknown"
        with pytest.raises(service.LoginRejected):
            await submit(args, result)
    assert "SYNTHETIC-PRIVATE-CODE-SECRET" not in caplog.text


@pytest.mark.parametrize("operation,limit", [("code", 5), ("password", 3)])
async def test_invalid_submission_limits_and_no_ttl_extension(
    connector_data, login_provider, monkeypatch, operation, limit
):
    with actor(connector_data) as args:
        row = await start_login(args)
        if operation == "password":

            async def password(*args):
                return LoginProgress("password", SESSION)

            monkeypatch.setattr(service, "telegram_login_step", password)
            row = await submit(args, row)
        expires = row["expires_at"]

        async def invalid(*args):
            return LoginProgress("invalid_" + operation, SESSION)

        monkeypatch.setattr(service, "telegram_login_step", invalid)
        for _ in range(limit):
            row = await submit(args, row, operation)
            assert row["expires_at"] == expires
        assert row["state"] == "failed" and row["failure_code"] == "attempt_limit_reached"
        assert (await persisted(row["attempt_id"])).secret_encrypted == ""


async def test_duplicate_verified_subject_does_not_replace_account_or_grants(connector_data, login_provider):
    with actor(connector_data) as args:
        done = await submit(args, await start_login(args))
        async with async_session_maker() as session:
            before = (await session.get(Authorization, done["authorization_id"])).secret_encrypted
        duplicate = await submit(args, await start_login(args))
        assert duplicate["state"] == "failed" and duplicate["failure_code"] == "account_already_exists"
        async with async_session_maker() as session:
            assert (await session.get(Authorization, done["authorization_id"])).secret_encrypted == before


async def test_start_limit_and_missing_configuration_never_call_provider(connector_data, login_provider, monkeypatch):
    with actor(connector_data) as args:
        monkeypatch.setattr(settings, "TELEGRAM_API_HASH", None)
        with pytest.raises(service.LoginRejected, match="configuration_unavailable"):
            await start_login(args)
        assert login_provider == []
        monkeypatch.setattr(settings, "TELEGRAM_API_HASH", HASH)
        for _ in range(3):
            row = await start_login(args)
            await service.telegram_logins.cancel(**args, attempt_id=row["attempt_id"])
        with pytest.raises(service.LoginRejected, match="rate_limited"):
            await start_login(args)
        assert login_provider == ["begin"] * 3


async def test_cancelled_task_seals_claim_without_provider_retry(connector_data, login_provider, monkeypatch):
    entered = asyncio.Event()

    async def blocked(*args):
        entered.set()
        await asyncio.Event().wait()

    with actor(connector_data) as args:
        row = await start_login(args)
        monkeypatch.setattr(service, "telegram_login_step", blocked)
        task = asyncio.create_task(submit(args, row))
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        saved = await persisted(row["attempt_id"])
        assert saved.state == "failed" and saved.secret_encrypted == ""


async def test_restart_with_unknown_processing_outcome_cannot_replay(connector_data, login_provider, monkeypatch):
    from sqlalchemy.exc import OperationalError

    original = service._record_telegram_result

    async def database_loss(session, **kwargs):
        await original(session, **kwargs)
        raise OperationalError("synthetic DB unavailable", {}, None)

    with actor(connector_data) as args:
        row = await start_login(args)
        monkeypatch.setattr(service, "_record_telegram_result", database_loss)
        with pytest.raises(OperationalError):
            await submit(args, row)
        saved = await persisted(row["attempt_id"])
        assert saved.state == "processing"
        # A new service object simulates a new worker: only durable DB state
        # governs resubmission. No in-memory idempotency or lock is required.
        worker = service.TelegramLoginManager()
        current = await worker.status(**args, attempt_id=row["attempt_id"])
        with pytest.raises(service.LoginRejected):
            await worker.submit(
                **args,
                attempt_id=row["attempt_id"],
                expected_revision=current["revision"],
                operation="code",
                secret="12345",
            )
        assert (await connector_authorizations.list_reusable(**args))[0] == []
        assert await connector_authorizations.list_grants(**args) == []
        cancelled = await worker.cancel(**args, attempt_id=row["attempt_id"])
        assert cancelled["state"] == "cancelled" and (await persisted(row["attempt_id"])).secret_encrypted == ""
    assert login_provider == ["begin", "code"]


async def test_concurrent_start_same_request_id_sends_only_once(connector_data, login_provider, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(operation, material, secret=None):
        assert operation == "begin"
        entered.set()
        await release.wait()
        return LoginProgress("code", SESSION, "SYNTHETIC-CODE-HASH")

    monkeypatch.setattr(service, "telegram_login_step", blocked)
    with actor(connector_data) as args:
        key = uuid4()
        task = asyncio.create_task(service.telegram_logins.start(**args, request_id=key, phone=PHONE))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            same = await service.TelegramLoginManager().start(**args, request_id=key, phone=PHONE)
            assert same["state"] == "processing"
            with pytest.raises(service.LoginRejected, match="already_pending"):
                await start_login(args)
        finally:
            release.set()
        result = await task
        assert result["attempt_id"] == same["attempt_id"] and result["state"] == "code"


async def test_two_users_can_have_independent_attempts_in_same_workspace(connector_data, login_provider):
    with actor(connector_data) as args:
        first = await start_login(args)
    with actor(connector_data, user=1) as args:
        second = await start_login(args)
    assert first["attempt_id"] != second["attempt_id"]
    assert login_provider == ["begin", "begin"]


async def test_debug_logging_refused_before_secret_storage_or_provider(connector_data, login_provider, monkeypatch):
    monkeypatch.setattr(settings, "DEBUG", True)
    with actor(connector_data) as args:
        with pytest.raises(service.LoginRejected, match="private_login_logging_required"):
            await start_login(args)
    assert login_provider == []


@pytest.mark.parametrize("change", ["expiry", "key"])
async def test_late_verified_result_rechecks_expiry_and_key(connector_data, login_provider, monkeypatch, change):
    with actor(connector_data) as args:
        row = await start_login(args)

        async def late(*unused):
            if change == "expiry":
                async with async_session_maker() as session, session.begin():
                    await session.execute(
                        update(Attempt)
                        .where(Attempt.id == row["attempt_id"])
                        .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
                    )
            else:
                monkeypatch.setattr(settings, "CREDENTIALS_KEY", Fernet.generate_key().decode())
            return LoginProgress("authorized", result=VerifiedTelegramResult(90001, 12345, HASH, SESSION))

        monkeypatch.setattr(service, "telegram_login_step", late)
        result = await submit(args, row)
        assert result["state"] == ("expired" if change == "expiry" else "failed")
        assert (await connector_authorizations.list_reusable(**args))[0] == []
        assert await connector_authorizations.list_grants(**args) == []
        assert (await persisted(row["attempt_id"])).secret_encrypted == ""


async def test_cancellation_with_failed_cleanup_retains_durable_fence(connector_data, login_provider, monkeypatch):
    from sqlalchemy.exc import OperationalError

    entered = asyncio.Event()

    async def blocked(*args):
        entered.set()
        await asyncio.Event().wait()

    async def unavailable(*args):
        raise OperationalError("synthetic DB unavailable", {}, None)

    worker = service.TelegramLoginManager()
    with actor(connector_data) as args:
        row = await start_login(args)
        monkeypatch.setattr(service, "telegram_login_step", blocked)
        monkeypatch.setattr(worker, "_settle", unavailable)
        task = asyncio.create_task(
            worker.submit(
                **args,
                attempt_id=row["attempt_id"],
                expected_revision=row["revision"],
                operation="code",
                secret="12345",
            )
        )
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        saved = await persisted(row["attempt_id"])
        assert saved.state == "processing"
        with pytest.raises(service.LoginRejected):
            await service.TelegramLoginManager().submit(
                **args, attempt_id=row["attempt_id"], expected_revision=saved.revision, operation="code", secret="12345"
            )
