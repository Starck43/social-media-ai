"""Guarded Telegram FloodWait: real orchestration, synthetic provider/storage edges."""

import asyncio
import socket
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.engine import Dialect
from telethon.errors import FloodPremiumWaitError, FloodWaitError

from app.core.config import settings
from app.services.social import telegram_runtime as runtime
from app.services.social import telegram_runtime_fence as fences
from app.services.social.connector_authorizations import AuthorizationUnavailable
from app.services.social.rate_limit import SourceRateLimited, telegram_flood_wait_seconds
from app.services.social.telegram_runtime_fence import _RuntimeFence
from app.services.social.tg_client import TelegramClient
from app.types import PlatformType, SourceType


def forbidden(*args, **kwargs):
    raise AssertionError("DB/network/staging forbidden")


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(Dialect, "connect", forbidden)
    monkeypatch.setattr(settings, "SOCIAL_RATE_LIMIT_MAX_WAIT", 10.0)


def source():
    return NS(
        id=3,
        tenant_id=1,
        platform_id=2,
        external_id="@synthetic",
        owner_user_id=7,
        name="synthetic",
        is_active=True,
        source_type=SourceType.CHANNEL,
        last_item_id="77",
        last_checked=None,
        date_from=None,
        date_to=None,
        platform=NS(platform_type=PlatformType.TELEGRAM, params={}),
        params={
            "mode": "user",
            "token_owner": 7,
            "telegram_l2_last_item_id": "21",
            "telegram_authorization": {"grant_id": 4, "authorization_revision": 2},
        },
    )


def provider_error(error_type=FloodWaitError, seconds=120):
    error = error_type(None, seconds)
    error.args = ("private-provider-token",)
    return error


class FakeGuard:
    def __init__(self, state):
        self.state = state

    def _require_owner(self):
        pass

    def _invalidate(self):
        self.state["closed"] = True

    @asynccontextmanager
    async def _admission_transaction(self):
        session = NS(execute=AsyncMock(return_value=NS(one_or_none=lambda: (1, "7"))))
        yield session

    @asynccontextmanager
    async def _setup_material(self):
        try:
            yield object()
        finally:
            self.state["material_closed"] = True


class TestFence(_RuntimeFence):
    __test__ = False

    async def _inspect(self):
        self._require_owner()
        self._guard.state["inspections"] += 1
        if self._guard.state.get("deny_after_send") and self._guard.state.get("sent"):
            self._close()
            raise AuthorizationUnavailable()


@pytest.fixture
def provider(monkeypatch):
    def install(error, *, deny_after_send=False, disconnect_error=False, where="history"):
        row = source()
        selection = runtime.source_grant_selection(row)
        state = {
            "inspections": 0,
            "sent": 0,
            "disconnected": False,
            "closed": False,
            "material_closed": False,
            "locked_exited": False,
            "yielded": 0,
            "deny_after_send": deny_after_send,
        }
        guard = FakeGuard(state)
        guard._snapshot = NS(authorization_id=9)
        guard._session = object()
        active = {}

        class Delegate:
            async def send(self, request, ordered=False):
                state["sent"] += 1
                raise error

        class Client:
            _runtime_connecting = False

            async def connect(self):
                if where == "connect":
                    await runtime._send_guarded(Delegate(), active["fence"], object(), False)

            async def _disconnect_coro(self):
                state["disconnected"] = True
                if disconnect_error:
                    raise RuntimeError("private disconnect")

            async def get_me(self):
                if where == "identity":
                    await runtime._send_guarded(Delegate(), active["fence"], object(), False)
                return NS(id=7, bot=False)

            async def get_entity(self, reference):
                if where == "resolution":
                    await runtime._send_guarded(Delegate(), active["fence"], object(), False)
                return "target"

            async def iter_messages(self, target, **kwargs):
                state["yielded"] += 1
                yield NS(id=22, date=datetime(2026, 1, 1, tzinfo=timezone.utc))
                await runtime._send_guarded(Delegate(), active["fence"], object(), False, history_request=True)

        @asynccontextmanager
        async def locked_source_grant(selected):
            assert selected == selection
            try:
                yield guard
            finally:
                state["locked_exited"] = True

        def build(material, fence):
            active["fence"] = fence
            return Client()

        monkeypatch.setattr(fences, "locked_source_grant", locked_source_grant)
        monkeypatch.setattr(
            fences, "_capture_execution", AsyncMock(return_value=NS(authorization_id=9, selection=selection))
        )
        monkeypatch.setattr(fences, "_check_execution", AsyncMock())
        monkeypatch.setattr(fences, "_RuntimeFence", TestFence)
        monkeypatch.setattr(runtime, "_build_client", build)
        monkeypatch.setattr(TelegramClient, "_normalize_message", lambda self, message, source: {"id": message.id})
        return row, selection, state

    return install


@pytest.mark.parametrize("error_type", [FloodWaitError, FloodPremiumWaitError])
async def test_long_real_telethon_flood_errors_defer_after_fenced_cleanup(provider, error_type, caplog):
    row, selected, state = provider(provider_error(error_type), where="history")
    params = dict(row.params)
    with pytest.raises(SourceRateLimited) as caught:
        async with runtime._telegram_history_batch(selected, object(), row, limit=10):
            pytest.fail("partial batch must never be admitted")
    assert caught.value.platform == "telegram" and caught.value.retry_after == 120
    assert state["sent"] == 1 and state["yielded"] == 1
    assert state["disconnected"] and state["material_closed"] and state["locked_exited"] and state["closed"]
    assert state["inspections"] >= 3
    assert row.params == params and row.last_item_id == "77"
    assert "private-provider-token" not in str(caught.value) + caplog.text
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.parametrize("seconds", [float("inf"), 2**31, 10**500])
async def test_unbounded_wait_hint_is_omitted(provider, seconds):
    class FloodWaitError(Exception):
        def __init__(self):
            self.seconds = seconds

    row, selected, state = provider(FloodWaitError())
    with pytest.raises(SourceRateLimited) as caught:
        async with runtime._telegram_history_batch(selected, object(), row, limit=10):
            pytest.fail("cannot admit")
    assert caught.value.retry_after is None
    assert state["sent"] == 1 and state["disconnected"] and state["closed"]


@pytest.mark.parametrize("error", [ConnectionError("private-provider-token"), provider_error(seconds=5)])
async def test_ordinary_and_short_wait_errors_remain_fail_closed(provider, error, caplog):
    row, selected, state = provider(error)
    with pytest.raises(AuthorizationUnavailable) as caught:
        async with runtime._telegram_history_batch(selected, object(), row, limit=10):
            pytest.fail("cannot admit")
    assert state["sent"] == 1 and state["disconnected"] and state["closed"]
    assert "private-provider-token" not in str(caught.value) + caplog.text


@pytest.mark.parametrize("failure", ["revoked", "disconnect"])
async def test_authority_or_cleanup_failure_wins_over_provider_limit(provider, failure):
    row, selected, state = provider(
        provider_error(), deny_after_send=failure == "revoked", disconnect_error=failure == "disconnect"
    )
    with pytest.raises(AuthorizationUnavailable):
        async with runtime._telegram_history_batch(selected, object(), row, limit=10):
            pytest.fail("cannot admit")
    assert state["sent"] == 1 and state["disconnected"] and state["closed"]


def test_pinned_telethon_error_classes_are_independently_recognized():
    assert not issubclass(FloodPremiumWaitError, FloodWaitError)
    assert telegram_flood_wait_seconds(provider_error(FloodWaitError)) == 120
    assert telegram_flood_wait_seconds(provider_error(FloodPremiumWaitError)) == 120
    # The independent diagnostic already recognizes the shared FloodError base.
    from app.services.social.credential_diagnostics import credential_exception_status

    assert credential_exception_status(provider_error(FloodPremiumWaitError)) == "error: rate_limited"


async def test_default_off_gate_still_prevents_provider_entry(monkeypatch):
    from app.services.monitoring import telegram_collection as collection
    from app.services.social.credentials import AuthorizationRequired

    monkeypatch.setattr(settings, "TELEGRAM_ACCOUNT_RUNTIME_ENABLED", False)
    monkeypatch.setattr(collection, "_telegram_history_batch", forbidden)
    with pytest.raises(AuthorizationRequired):
        await collection.collect_personal_telegram(None, source())


async def test_real_job_and_collector_report_deferral_without_staging(provider, monkeypatch, caplog):
    from app.jobs import handlers
    from app.models.managers.entitlement_manager import EntitlementManager
    from app.services.monitoring import collector, telegram_collection

    row, _, state = provider(provider_error())
    initial_params = dict(row.params)
    monkeypatch.setattr(settings, "TELEGRAM_ACCOUNT_RUNTIME_ENABLED", True)
    monkeypatch.setattr(
        telegram_collection, "current_claim", lambda: NS(job_type="collect", job_id=5, agent_task_id=None)
    )
    monkeypatch.setattr(telegram_collection, "_admit_runtime_telegram_batch", AsyncMock(side_effect=forbidden))
    monkeypatch.setattr(handlers, "_load_task", AsyncMock(return_value=None))
    monkeypatch.setattr(handlers, "_resolve_sources", AsyncMock(return_value=[row]))
    monkeypatch.setattr(handlers, "_record_lane_evidence", AsyncMock())
    monkeypatch.setattr(EntitlementManager, "admit_execution", AsyncMock())
    monkeypatch.setattr(collector.ContentCollector, "__init__", lambda self: None)
    monkeypatch.setattr(collector, "NOTIFICATIONS_AVAILABLE", False)
    result = await handlers.handle_collect({"job_id": 5})
    assert result["rate_limited"] == 1 and result["error"] == 0 and result["empty"] == 0
    assert result["items"] == 0 and result["per_source"][0]["outcome"] == "rate_limited"
    assert result["per_source"][0]["retry_after"] == 120
    assert row.params == initial_params and row.last_item_id == "77"
    telegram_collection._admit_runtime_telegram_batch.assert_not_awaited()
    assert state["yielded"] == 1 and state["sent"] == 1 and state["disconnected"] and state["closed"]
    assert "private-provider-token" not in str(result) + caplog.text


@pytest.mark.parametrize("error_type", [FloodWaitError, FloodPremiumWaitError])
@pytest.mark.parametrize("where", ["connect", "identity", "resolution"])
async def test_long_wait_before_history_fails_closed(provider, error_type, where, caplog):
    row, selected, state = provider(provider_error(error_type), where=where)
    with pytest.raises(AuthorizationUnavailable) as caught:
        async with runtime._telegram_history_batch(selected, object(), row, limit=10):
            pytest.fail("pre-history failures cannot defer or admit")
    assert state["sent"] == 1 and state["yielded"] == 0
    assert state["disconnected"] and state["material_closed"] and state["locked_exited"] and state["closed"]
    assert "private-provider-token" not in str(caught.value) + caplog.text


@pytest.mark.parametrize("error_type", [FloodWaitError, FloodPremiumWaitError])
@pytest.mark.parametrize("stage", ["connect", "identity", "resolution", "history"])
async def test_real_built_sender_limits_deferral_to_history_rpc(error_type, stage):
    from telethon.crypto import AuthKey
    from telethon.sessions import StringSession
    from telethon.tl import functions
    from telethon.tl.alltlobjects import LAYER

    session = StringSession()
    session.set_dc(2, "192.0.2.1", 443)  # reserved documentation address; never connected
    session.auth_key = AuthKey(data=bytes(range(256)))
    state = {"inspections": 0, "sent": 0, "closed": False}
    guard = FakeGuard(state)
    fence = TestFence(guard, None, 1, "7", asyncio.get_running_loop().time() + 60)
    client = runtime._build_client(NS(session=session.save(), api_id=1, api_hash="synthetic"), fence)

    class Delegate:
        async def send(self, request, ordered=False):
            state["sent"] += 1
            raise provider_error(error_type)

    client._sender._delegate = Delegate()
    with pytest.raises(AuthorizationUnavailable):
        await client._sender.send(functions.messages.SendMessageRequest(peer=None, message="forbidden"))
    assert state["sent"] == 0
    if stage == "connect":
        client._runtime_connecting = True
        request = functions.InvokeWithLayerRequest(
            LAYER,
            functions.InvokeWithoutUpdatesRequest(
                functions.InitConnectionRequest(
                    123, "synthetic", "synthetic", "synthetic", "en", "", "en", functions.help.GetConfigRequest()
                )
            ),
        )
    elif stage == "identity":
        request = functions.InvokeWithoutUpdatesRequest(functions.users.GetUsersRequest(id=[]))
    elif stage == "resolution":
        request = functions.InvokeWithoutUpdatesRequest(functions.contacts.ResolveUsernameRequest(username="synthetic"))
    else:
        request = functions.InvokeWithoutUpdatesRequest(
            functions.messages.GetHistoryRequest(
                peer=None, offset_id=0, offset_date=None, add_offset=0, limit=1, max_id=0, min_id=0, hash=0
            )
        )
    if stage == "history":
        with pytest.raises(runtime._ProviderFloodWait) as caught:
            await client._sender.send(request)
        assert caught.value.retry_after == 120 and state["inspections"] == 2 and not state["closed"]
    else:
        with pytest.raises(AuthorizationUnavailable):
            await client._sender.send(request)
        assert state["inspections"] == 1 and state["closed"]
    assert state["sent"] == 1


async def test_cancelled_provider_send_does_not_become_a_deferral(provider):
    row, selected, state = provider(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        async with runtime._telegram_history_batch(selected, object(), row, limit=10):
            pytest.fail("cannot admit")
    assert state["sent"] == 1 and state["disconnected"] and state["closed"]


async def test_successful_provider_send_returns_result_after_both_guard_checks():
    state = {"inspections": 0, "sent": 0, "closed": False}
    guard = FakeGuard(state)
    fence = TestFence(guard, None, 1, "7", asyncio.get_running_loop().time() + 60)

    class Delegate:
        async def send(self, request, ordered=False):
            state["sent"] += 1
            return "synthetic-response"

    assert await runtime._send_guarded(Delegate(), fence, object(), False) == "synthetic-response"
    assert state["sent"] == 1 and state["inspections"] == 2 and not state["closed"]
