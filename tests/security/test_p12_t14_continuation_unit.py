"""DB-exempt continuation contracts: real pure logic, fake storage/provider edges."""

import asyncio
import json
import socket
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Dialect

from app.core.tenant_context import tenant_scope
from app.models.managers.source_manager import _protect_telegram_binding
from app.services.social import connector_authorizations as accounts
from app.services.social import telegram_login as login
from app.services.social import telegram_login_maintenance as maintenance
from app.services.social import telegram_runtime as runtime
from app.services.social.connector_authorizations import AuthorizationUnavailable, VerifiedTelegramResult
from app.services.social.telegram_grant_access import source_grant_selection
from app.services.social.telegram_modes import is_bot_push
from app.services.social.telegram_queue_selection import TelegramJobRequeueRequired, _queued_authorization_id
from app.services.social.tg_client import TelegramClient
from app.types import PlatformType


def forbidden(*args, **kwargs):
    raise AssertionError("real DB/network/provider forbidden")


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(Dialect, "connect", forbidden)
    monkeypatch.setattr(login, "telegram_login_step", forbidden)


def source(**params):
    return NS(
        id=3,
        tenant_id=1,
        platform_id=2,
        external_id="@synthetic",
        owner_user_id=7,
        name="synthetic",
        is_active=True,
        last_item_id="900",
        date_from=None,
        platform=NS(platform_type=PlatformType.TELEGRAM),
        params={
            "mode": "user",
            "token_owner": 7,
            "telegram_authorization": {"grant_id": 4, "authorization_revision": 2},
            **params,
        },
    )


@pytest.mark.parametrize("mode", ["push", "api"])
async def test_bot_modes_never_enter_personal_pull_or_preflight(mode):
    from app.services.social.connections import source_connection_spec

    row = source(mode=mode)
    assert is_bot_push(row.params)
    assert source_connection_spec(row) is None
    assert await TelegramClient(NS(params={})).collect_data(row) == []


async def test_current_fenced_history_fetches_oldest_first_without_writing_cursor(monkeypatch):
    seen = {}
    selection = NS(external_id="@synthetic")

    class Client:
        async def get_me(self):
            return NS(id=7, bot=False)

        async def get_entity(self, reference):
            return "synthetic-target"

        async def iter_messages(self, target, **kwargs):
            assert target == "synthetic-target"
            seen.update(kwargs)
            yield NS(id=21, date=datetime.now(timezone.utc))

    fence = NS(
        _pin=NS(selection=selection),
        _subject_key="7",
        _inspect=AsyncMock(),
    )
    row = source(telegram_l2_last_item_id="20")
    row.last_item_id = "999"
    monkeypatch.setattr(runtime, "source_grant_selection", lambda source: selection)
    monkeypatch.setattr(TelegramClient, "_normalize_message", lambda self, message, source: {"id": str(message.id)})

    items = await runtime._history(Client(), fence, row, 25)

    assert items == [{"id": "21"}]
    assert row.last_item_id == "999"
    assert seen["min_id"] == 20
    assert seen["reverse"] is True
    assert seen["wait_time"] == 0
    assert "offset_date" in seen
    assert "min_date" not in seen
    assert fence._inspect.await_count >= 2


@pytest.mark.parametrize("params", [None, [], {}, {"mode": "auto"}, {"mode": "user"}, {"mode": "API"}, {"mode": True}])
def test_bot_allowlist_is_exact(params):
    assert not is_bot_push(params)


def test_telethon_connect_accepts_only_the_pinned_config_envelope():
    from telethon import functions
    from telethon.tl.alltlobjects import LAYER

    request = functions.InvokeWithLayerRequest(
        LAYER,
        functions.InvokeWithoutUpdatesRequest(
            functions.InitConnectionRequest(
                123,
                "synthetic-device",
                "synthetic-system",
                "synthetic-app",
                "en",
                "",
                "en",
                functions.help.GetConfigRequest(),
            )
        ),
    )
    assert runtime._is_connect_config_request(request, functions=functions, layer=LAYER, connecting=True)
    assert not runtime._is_connect_config_request(request, functions=functions, layer=LAYER + 1, connecting=True)
    assert not runtime._is_connect_config_request(request, functions=functions, layer=LAYER, connecting=False)
    assert not runtime._is_connect_config_request(
        functions.InvokeWithLayerRequest(
            LAYER,
            functions.InvokeWithoutUpdatesRequest(
                functions.InitConnectionRequest(
                    123,
                    "synthetic-device",
                    "synthetic-system",
                    "synthetic-app",
                    "en",
                    "",
                    "en",
                    functions.InvokeWithoutUpdatesRequest(functions.help.GetConfigRequest()),
                )
            ),
        ),
        functions=functions,
        layer=LAYER,
        connecting=True,
    )
    assert not runtime._is_connect_config_request(
        functions.InvokeWithoutUpdatesRequest(request), functions=functions, layer=LAYER, connecting=True
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"external_id": "@another"},
        {"tenant_id": 2},
        {"platform_id": 8},
        {"owner_user_id": 8},
        {"params": {}},
        {"params": {"mode": "api"}},
        {
            "params": {
                "mode": "user",
                "token_owner": True,
                "telegram_authorization": {"grant_id": 4, "authorization_revision": 2},
            }
        },
    ],
)
def test_generic_source_update_cannot_retarget_or_remove_consent(changes):
    with pytest.raises(PermissionError, match="telegram_source_picker_required"):
        _protect_telegram_binding(source(), changes)


def test_generic_source_update_preserves_identical_binding_and_allows_unrelated_params():
    row = source()
    _protect_telegram_binding(row, {"name": "new name", "params": {**row.params, "collection": {"limit": 10}}})
    clean = source()
    clean.params = {}
    with pytest.raises(PermissionError):
        _protect_telegram_binding(clean, {"params": source().params})


@pytest.mark.parametrize("malformed", [{}, {"_telegram_source_grants": []}, {"_telegram_source_grants": {"3": {}}}])
def test_unpinned_legacy_jobs_demand_requeue(malformed):
    with pytest.raises(TelegramJobRequeueRequired):
        _queued_authorization_id(malformed, source_grant_selection(source()))


@pytest.mark.parametrize("subject,revision,method", [(99, 2, "mtproto"), (90001, 1, "mtproto"), (90001, 2, "other")])
async def test_recovery_refuses_other_subject_stale_revision_and_method(monkeypatch, subject, revision, method):
    row = NS(id=12, owner_user_id=7, subject_key="90001", provider_id="telegram", auth_method=method, revision=revision)
    monkeypatch.setattr(accounts, "_account", AsyncMock(return_value=row))
    session = NS(execute=AsyncMock(side_effect=forbidden), flush=AsyncMock(side_effect=forbidden))
    with pytest.raises(AuthorizationUnavailable):
        await accounts._record_telegram_recovery(
            session,
            actor_id=7,
            authorization_id=12,
            expected_revision=2,
            result=VerifiedTelegramResult(subject_id=subject, api_id=1, api_hash="synthetic", session="synthetic"),
        )
    session.execute.assert_not_awaited()


async def test_recovery_never_decrypts_or_reattaches_and_fences_all_old_grants(monkeypatch):
    row = NS(
        id=12,
        owner_user_id=7,
        subject_key="90001",
        provider_id="telegram",
        auth_method="mtproto",
        revision=2,
        execution_generation=4,
        state="revoked",
    )
    selection = AsyncMock(return_value=row)
    monkeypatch.setattr(accounts, "_account", selection)
    monkeypatch.setattr(accounts, "decrypt_secret", forbidden)
    monkeypatch.setattr(accounts, "_attach", AsyncMock(side_effect=forbidden))
    monkeypatch.setattr(accounts, "encrypt_secret", lambda value: value)  # synthetic only
    session = NS(execute=AsyncMock(), flush=AsyncMock())
    metadata = await accounts._record_telegram_recovery(
        session,
        actor_id=7,
        authorization_id=12,
        expected_revision=2,
        result=VerifiedTelegramResult(subject_id=90001, api_id=1, api_hash="synthetic", session="synthetic"),
    )
    selection.assert_awaited_once_with(session, 7, 12, include_secret=False)
    assert metadata["revision"] == 3 and metadata["state"] == "active"
    assert row.execution_generation == 5
    assert json.loads(row.secret_encrypted)["identity"]["revision"] == 3
    compiled = session.execute.await_args.args[0].compile(dialect=postgresql.dialect())
    assert "is_active" in str(compiled) and False in compiled.params.values()
    assert "tenant_id" not in str(compiled)  # ALL workspaces, not just initiating tenant.
    accounts._attach.assert_not_awaited()


class Result:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def scalars(self):
        return self


class Session:
    def __init__(self, replies=()):
        self.replies, self.calls, self.outcome = iter(replies), [], None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    @asynccontextmanager
    async def begin(self):
        try:
            yield self
        except BaseException:
            self.outcome = "rollback"
            raise
        else:
            self.outcome = "commit"

    async def execute(self, statement, *args):
        self.calls.append(statement)
        return Result(next(self.replies))


@pytest.mark.parametrize("name", ["purge_attempt_batch", "wipe_terminal_attempt_batch"])
async def test_retention_requires_trusted_bypass_before_any_session(monkeypatch, name):
    monkeypatch.setattr(maintenance, "async_session_maker", forbidden)
    with tenant_scope(1), pytest.raises(RuntimeError, match="trusted bypass"):
        await getattr(maintenance, name)()


async def test_retention_inserts_tombstones_before_deleting_bounded_terminal_metadata(monkeypatch):
    row = NS(id="attempt-uuid", owner_user_id=7, request_key="request-uuid")
    session = Session([[], [], [row], [], [row.id]])
    monkeypatch.setattr(maintenance, "async_session_maker", lambda: session)
    with tenant_scope(bypass=True):
        assert await maintenance.purge_attempt_batch() == 1
    sql = [str(stmt.compile(dialect=postgresql.dialect())) for stmt in session.calls]
    assert session.outcome == "commit"
    assert "FOR UPDATE SKIP LOCKED" in sql[2] and "LIMIT" in sql[2]
    assert "secret_encrypted =" in sql[2] and "expires_at <=" in sql[2]
    assert "INSERT INTO test_schema.telegram_login_request_keys" in sql[3]
    assert "ON CONFLICT DO NOTHING" in sql[3]
    assert "DELETE FROM test_schema.telegram_login_attempts" in sql[4]
    assert "secret_encrypted" not in sql[3] + sql[4]


async def test_retired_start_request_cannot_call_provider_or_create_attempt(monkeypatch):
    class StartSession(Session):
        def __init__(self):
            super().__init__([[], []])

        async def execute(self, statement, *args):
            if "clock_timestamp" in str(statement):
                return NS(scalar_one=lambda: datetime.now(timezone.utc))
            return await super().execute(statement, *args)

        async def scalar(self, stmt):
            return "retired"

        def add(self, row):
            forbidden()

    session = StartSession()
    monkeypatch.setattr(login, "async_session_maker", lambda: session)
    monkeypatch.setattr(login, "_private_logging", lambda: None)
    monkeypatch.setattr(login, "_authorize", AsyncMock())
    with pytest.raises(login.LoginRejected, match="login_request_retired"):
        await login.telegram_logins.start(
            tenant_id=1, actor_id=7, request_id="b1624820-b2a7-4f8a-a4b4-16a316ef5070", phone="+12345678901"
        )
    assert session.outcome == "rollback"


async def test_personal_history_uses_l2_cursor_not_bot_push_highwater(monkeypatch):
    from app.services.social.tg_client import TelegramClient as Normalizer

    row = source()
    selected = source_grant_selection(row)
    fence = NS(_pin=NS(selection=selected), _subject_key="90001", _inspect=AsyncMock())
    params = []

    class Client:
        get_me = AsyncMock(return_value=NS(id=90001, bot=False))
        get_entity = AsyncMock(return_value="target")

        def iter_messages(self, target, **kwargs):
            params.append(kwargs)

            async def messages():
                for value in (1, 2, 3):
                    yield NS(id=value, date=datetime(2030, 1, value, tzinfo=timezone.utc))

            return messages()

    monkeypatch.setattr(Normalizer, "_normalize_message", lambda self, message, source: {"id": message.id})
    values = await runtime._history(Client(), fence, row, 3)
    assert values == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert params[0]["min_id"] == 0  # Push's 900 is not a personal consent cursor.
    assert params[0]["reverse"] is True and params[0]["wait_time"] == 0
    assert fence._inspect.await_count == 5


async def test_runtime_batch_cannot_be_replayed_after_unknown_commit():
    calls = []

    @asynccontextmanager
    async def output():
        calls.append("begin")
        yield "session"
        raise RuntimeError("synthetic unknown commit")

    fence = NS(_require_owner=lambda: None, _output_transaction=output)
    batch = runtime._TelegramReadBatch(fence, [{"id": 1}])
    with pytest.raises(RuntimeError):
        async with batch._admission():
            pass
    with pytest.raises(AuthorizationUnavailable):
        async with batch._admission():
            pytest.fail("replayed")
    batch._close()
    assert calls == ["begin"] and batch._items == []


async def test_old_job_requeue_diagnostic_survives_runtime_boundaries(monkeypatch):
    from app.services.social import telegram_runtime_fence as fences

    @asynccontextmanager
    async def stale(*args):
        raise TelegramJobRequeueRequired()
        yield  # pragma: no cover

    monkeypatch.setattr(fences, "locked_source_grant", stale)
    with pytest.raises(TelegramJobRequeueRequired):
        async with fences._locked_runtime(None, None):
            pytest.fail("old job must not reach provider setup")
    monkeypatch.setattr(runtime, "_locked_runtime", stale)
    with pytest.raises(TelegramJobRequeueRequired):
        async with runtime._telegram_history_batch(None, None, source(), limit=1):
            pytest.fail("old job must not reach a batch")


@pytest.mark.parametrize("boundary", ["inspect", "request", "output"])
async def test_old_job_requeue_diagnostic_survives_mid_epoch_checks(monkeypatch, boundary):
    from app.services.social import telegram_runtime_fence as fences

    guard = NS(
        _require_owner=lambda: None,
        _invalidate=lambda: None,
        inspect=AsyncMock(side_effect=TelegramJobRequeueRequired()),
    )
    fence = fences._RuntimeFence(guard, None, 1, "90001", asyncio.get_running_loop().time() + 60)
    with pytest.raises(TelegramJobRequeueRequired):
        if boundary == "inspect":
            await fence._inspect()
        else:
            scope = fence._request() if boundary == "request" else fence._output_transaction()
            async with scope:
                pytest.fail("stale job cannot enter request/output")
    assert fence._closed
