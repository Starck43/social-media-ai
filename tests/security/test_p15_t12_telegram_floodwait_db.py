"""Owner-run PostgreSQL checks for T12; never run against the working schema.

Use root tests/conftest.py, a validated isolated test schema, exclusive DB access,
inventory/cleanup checks, and synthetic provider fakes. No Telegram connection.
The remote executor prepared this file but did not run it against a database.
"""

from datetime import datetime, timezone
from secrets import token_hex
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import delete, func, select, text, update
from telethon.errors import FloodPremiumWaitError, FloodWaitError

from app.core.config import settings
from app.core.database import async_session_maker
from app.jobs.claim_outcomes import JobClaim
from app.models import CollectedItem, Job, PersonalConnectorAuthorization, Platform, Source
from app.models import WorkspaceAuthorizationGrant as Grant
from app.services.social import connector_authorizations as accounts
from app.services.social import telegram_account_lock as locks
from app.services.social import telegram_runtime as runtime
from app.services.social.connector_authorizations import AuthorizationUnavailable, VerifiedTelegramResult
from app.services.social.rate_limit import SourceRateLimited
from app.services.social.telegram_grant_access import source_grant_selection
from app.services.social.telegram_queue_selection import _snapshot_queue_bindings
from app.services.social.tg_client import TelegramClient
from app.types import PlatformType, SourceType
from tests.shared.connector_accounts import connector_actor as actor
from tests.shared.connector_accounts import connector_data  # noqa: F401

pytestmark = pytest.mark.tenancy


@pytest.fixture
async def flood_runtime_data(connector_data):
    # Root conftest redirects the suite before model import; do not use public.
    assert settings.DB_SCHEMA == settings.DB_TEST_SCHEMA and settings.DB_SCHEMA != "public"
    account = grant = source = job = None
    try:
        with actor(connector_data) as args:
            account, grant = await accounts.record_telegram_result(
                **args,
                result=VerifiedTelegramResult(
                    subject_id=90013, api_id=123, api_hash="synthetic-hash", session="synthetic-not-a-session"
                ),
            )
        async with async_session_maker() as session, session.begin():
            platform_id = await session.scalar(
                select(Platform.id).where(Platform.platform_type == PlatformType.TELEGRAM)
            )
            assert platform_id is not None
            source = Source(
                tenant_id=args["tenant_id"],
                platform_id=platform_id,
                external_id="@p15t12_" + token_hex(5),
                name="Synthetic T12",
                source_type=SourceType.CHANNEL,
                owner_user_id=args["actor_id"],
                is_active=True,
                last_item_id="77",
                params={
                    "mode": "user",
                    "telegram_l2_last_item_id": "21",
                    "telegram_authorization": {
                        "grant_id": grant["grant_id"],
                        "authorization_revision": account["revision"],
                    },
                },
            )
            session.add(source)
            await session.flush()
            payload = await _snapshot_queue_bindings(session, {"source_ids": [source.id]}, args["tenant_id"])
            now = datetime.now(timezone.utc)
            job = Job(
                tenant_id=args["tenant_id"],
                initiated_by_user_id=args["actor_id"],
                job_type="collect",
                payload=payload,
                status="running",
                run_at=now,
                locked_at=now,
                started_at=now,
                attempts=1,
            )
            session.add(job)
            await session.flush()
            claim = JobClaim.capture(job)
            selection = source_grant_selection(source)
        yield NS(source=source, claim=claim, selection=selection, account=account, grant=grant, fixture=connector_data)
    finally:
        async with async_session_maker() as session, session.begin():
            if source is not None and source.id is not None:
                await session.execute(delete(CollectedItem).where(CollectedItem.source_id == source.id))
            if job is not None and job.id is not None:
                await session.execute(delete(Job).where(Job.id == job.id))
            if source is not None and source.id is not None:
                await session.execute(delete(Source).where(Source.id == source.id))
            if grant is not None:
                await session.execute(delete(Grant).where(Grant.id == grant["grant_id"]))
            if account is not None:
                await session.execute(
                    delete(PersonalConnectorAuthorization).where(
                        PersonalConnectorAuthorization.id == account["authorization_id"]
                    )
                )


async def _db_snapshot(data):
    async with async_session_maker() as session:
        source = await session.get(Source, data.source.id)
        generation = await session.scalar(
            select(PersonalConnectorAuthorization.execution_generation).where(
                PersonalConnectorAuthorization.id == data.account["authorization_id"]
            )
        )
        count = await session.scalar(
            select(func.count()).select_from(CollectedItem).where(CollectedItem.source_id == data.source.id)
        )
        return source.params, source.last_item_id, source.last_checked, generation, count


async def _assert_account_lock_released(data):
    async with async_session_maker() as session, session.begin():
        acquired = await session.scalar(
            text("SELECT pg_try_advisory_xact_lock(:namespace, :account)"),
            {"namespace": locks._ACCOUNT_LOCK_NAMESPACE, "account": data.account["authorization_id"]},
        )
        assert acquired is True


def install_provider(monkeypatch, data, error_type, scenario):
    state = {"yielded": 0, "sent": 0, "disconnected": False}

    def build(material, fence):
        class Delegate:
            async def send(self, request, ordered=False):
                state["sent"] += 1
                if scenario == "revoked":
                    async with async_session_maker() as session, session.begin():
                        await session.execute(
                            update(Grant).where(Grant.id == data.grant["grant_id"]).values(is_active=False)
                        )
                raise error_type(None, 120)

        class Client:
            _runtime_connecting = False

            async def connect(self):
                pass

            async def get_me(self):
                return NS(id=90013, bot=False)

            async def get_entity(self, reference):
                return "synthetic-target"

            async def iter_messages(self, target, **kwargs):
                state["yielded"] += 1
                yield NS(id=22, date=datetime(2026, 1, 1, tzinfo=timezone.utc))
                await runtime._send_guarded(Delegate(), fence, object(), False, history_request=True)

            async def _disconnect_coro(self):
                state["disconnected"] = True
                if scenario == "disconnect":
                    raise RuntimeError("synthetic disconnect failure")

        return Client()

    monkeypatch.setattr(runtime, "_build_client", build)
    monkeypatch.setattr(TelegramClient, "_normalize_message", lambda self, message, source: {"id": str(message.id)})
    monkeypatch.setattr(settings, "SOCIAL_RATE_LIMIT_MAX_WAIT", 10.0)
    return state


@pytest.mark.parametrize("error_type", [FloodWaitError, FloodPremiumWaitError])
@pytest.mark.parametrize("scenario", ["flood", "revoked", "disconnect"])
async def test_db_guarded_partial_history_is_never_admitted(flood_runtime_data, monkeypatch, error_type, scenario):
    data = flood_runtime_data
    state = install_provider(monkeypatch, data, error_type, scenario)
    before = await _db_snapshot(data)
    with actor(data.fixture):
        expected = SourceRateLimited if scenario == "flood" else AuthorizationUnavailable
        with pytest.raises(expected) as caught:
            async with runtime._telegram_history_batch(data.selection, data.claim, data.source, limit=10):
                pytest.fail("partial messages must never be admitted")
    if scenario == "flood":
        assert caught.value.platform == "telegram" and caught.value.retry_after == 120
    assert state == {"yielded": 1, "sent": 1, "disconnected": True}
    after = await _db_snapshot(data)
    assert before[0:3] == after[0:3]  # params, high-water mark and last_checked
    assert before[4] == after[4] == 0  # no partial raw rows
    assert after[3] == before[3] + 1  # the real DB-backed execution epoch was allocated
    await _assert_account_lock_released(data)
