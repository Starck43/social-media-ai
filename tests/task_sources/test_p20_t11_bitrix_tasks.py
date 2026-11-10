"""Read-only Bitrix24 task source contracts for P20-T11.

All remote calls are mocked; these tests exercise query scoping, cursor safety,
provider response validation and reference-only persistence without DB fixtures.
"""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from app.integrations.bitrix24 import tasks as bitrix_tasks
from app.integrations.bitrix24.client import Bitrix24Error, Bitrix24RateLimitError
from app.services.task_sources import bitrix24 as source_module
from app.services.task_sources.bitrix24 import Bitrix24TaskSourceService

__source_only__ = True


def task_row(task_id, *, responsible=7, auditors=None, title=None, status=2, deadline=None):
    return {
        "ID": task_id,
        "TITLE": title or f"Task {task_id}",
        "STATUS": status,
        "DEADLINE": deadline,
        "RESPONSIBLE_ID": responsible,
        "AUDITORS": auditors or [],
    }


def cursor_for(*, actor_id=8, connection_id=3, profile_id=7, stream="assigned", offset=0, index=0):
    raw = json.dumps(
        {"a": actor_id, "c": connection_id, "p": profile_id, "s": stream, "o": offset, "i": index},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class FakeSession:
    def __init__(self, scalar_results=()):
        self._scalar_results = iter(scalar_results)
        self.scalar_statements = []
        self.added = []
        self.flush_count = 0

    async def scalar(self, statement):
        self.scalar_statements.append(statement)
        try:
            return next(self._scalar_results)
        except StopIteration:
            return None

    async def scalars(self, statement):
        self.scalar_statements.append(statement)
        return FakeScalarRows([])

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flush_count += 1


class FakeScalarRows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeSessionWithRows(FakeSession):
    def __init__(self, *, scalar_results=(), rows=()):
        super().__init__(scalar_results)
        self.rows = rows
        self.scalars_statements = []

    async def scalars(self, statement):
        self.scalars_statements.append(statement)
        return FakeScalarRows(self.rows)


def connection(
    *,
    owner_id=8,
    tenant_id=4,
    connection_id=3,
    state="verified",
    active=True,
    consent=True,
    consent_actor=8,
    scopes=("user", "task"),
    auth_method="oauth2",
):
    return NS(
        id=connection_id,
        tenant_id=tenant_id,
        owner_user_id=owner_id,
        provider_id="bitrix24",
        state=state,
        is_active=active,
        auth_method=auth_method,
        account_key="team.bitrix24.ru",
        label="Team portal",
        config={
            "scopes": list(scopes),
            "task_source": {
                "read_consent": consent,
                "read_consented_by": consent_actor if consent else None,
                "selected": consent,
                "write_consent": False,
            },
        },
    )


def patch_actor(monkeypatch, result=(4, 8, "owner")):
    monkeypatch.setattr(source_module.planner_service, "_actor", AsyncMock(return_value=result))


def projection_session(monkeypatch, *, connection_row, refs=()):
    session = FakeSession(scalar_results=[connection_row, *([None] * len(refs))])
    monkeypatch.setattr(source_module.planner_service, "_actor", AsyncMock(return_value=(4, 8, "owner")))
    return session


@pytest.mark.parametrize(
    ("response", "offset"),
    [
        (None, 0),
        ({"result": "partial", "next": 50}, 0),
        ({"result": [task_row(1)], "next": 100}, 50),
        ({"result": [task_row(0)]}, 0),
        ({"result": [task_row(1, title="  ")]}, 0),
        ({"result": [task_row(1, status="unknown")]}, 0),
        ({"result": [task_row(1, auditors="8")]}, 0),
        ({"result": [task_row(1, deadline=42)]}, 0),
        ({"error": "ACCESS_DENIED", "result": []}, 0),
    ],
)
def test_provider_list_rejects_malformed_or_partial_pages_without_watermark(response, offset):
    with pytest.raises(bitrix_tasks.BitrixTaskSourceError):
        bitrix_tasks._task_list(response, offset=offset)


def test_provider_list_accepts_full_page_and_exact_next_offset():
    rows = [task_row(task_id) for task_id in range(1, bitrix_tasks.PAGE_SIZE + 1)]

    parsed, next_offset = bitrix_tasks._task_list({"result": rows, "next": 50}, offset=0)

    assert len(parsed) == bitrix_tasks.PAGE_SIZE
    assert parsed[0]["external_id"] == "1"
    assert parsed[-1]["external_id"] == "50"
    assert next_offset == 50


@pytest.mark.parametrize("kind,filter_key", [("assigned", "filter[RESPONSIBLE_ID]"), ("watched", "filter[AUDITOR]")])
@pytest.mark.asyncio
async def test_stream_uses_flattened_rest_keys_and_authenticated_profile(kind, filter_key, monkeypatch):
    call = AsyncMock(return_value={"result": [task_row(12, responsible=7, auditors=[7])], "next": None})
    monkeypatch.setattr(bitrix_tasks.bitrix24_service, "call_method", call)

    rows, next_offset = await bitrix_tasks._stream(
        tenant_id=4, actor_id=8, connection_id=3, profile_id=7, kind=kind, offset=50, session=object()
    )

    kwargs = call.await_args.kwargs
    assert kwargs["method_name"] == "tasks.task.list"
    assert kwargs["params"][filter_key] == "7"
    assert kwargs["params"]["start"] == "50"
    assert kwargs["params"]["order[ID]"] == "desc"
    assert {value for key, value in kwargs["params"].items() if key.startswith("select[")} == set(bitrix_tasks.FIELDS)
    assert rows[0]["external_id"] == "12"
    assert next_offset is None


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_error", [Bitrix24RateLimitError(), Bitrix24Error("access_denied")])
async def test_stream_maps_rate_limit_or_revoked_access_to_unavailable_without_cursor(provider_error, monkeypatch):
    call = AsyncMock(side_effect=provider_error)
    monkeypatch.setattr(bitrix_tasks.bitrix24_service, "call_method", call)

    with pytest.raises(bitrix_tasks.BitrixTaskSourceError):
        await bitrix_tasks._stream(
            tenant_id=4, actor_id=8, connection_id=3, profile_id=7, kind="assigned", offset=0, session=object()
        )


@pytest.mark.asyncio
async def test_profile_id_comes_from_authorized_provider_connection(monkeypatch):
    call = AsyncMock(return_value={"result": {"ID": 73}})
    monkeypatch.setattr(bitrix_tasks.bitrix24_service, "call_method", call)

    profile = await bitrix_tasks.current_user_id(tenant_id=4, actor_id=8, connection_id=3, session=object())

    assert profile == 73
    kwargs = call.await_args.kwargs
    assert kwargs["tenant_id"] == 4
    assert kwargs["actor_id"] == 8
    assert kwargs["connection_id"] == 3
    assert kwargs["method_name"] == "user.current"


@pytest.mark.asyncio
async def test_projection_requires_fresh_actor_before_provider_io(monkeypatch):
    session = FakeSession(scalar_results=[connection()])
    denied = AsyncMock(side_effect=PermissionError("membership_revoked"))
    provider = AsyncMock()
    monkeypatch.setattr(source_module.planner_service, "_actor", denied)
    monkeypatch.setattr(source_module, "_stream", provider)

    with pytest.raises(PermissionError):
        await Bitrix24TaskSourceService().projection(connection_id=3, session=session)

    provider.assert_not_awaited()
    assert session.scalar_statements == []


@pytest.mark.parametrize(
    ("row", "expected_state"),
    [
        (connection(consent=False), "denied"),
        (connection(consent=True, consent_actor=99), "denied"),
        (connection(active=False), "source_unavailable"),
        (connection(state="reauth"), "reauth_required"),
    ],
)
@pytest.mark.asyncio
async def test_unusable_connection_is_not_read_or_projected_as_empty(row, expected_state, monkeypatch):
    session = FakeSession(scalar_results=[row])
    patch_actor(monkeypatch)
    provider = AsyncMock()
    monkeypatch.setattr(source_module, "_stream", provider)
    monkeypatch.setattr(source_module, "current_user_id", AsyncMock(return_value=7))

    result = await Bitrix24TaskSourceService().projection(connection_id=3, session=session)

    assert result["state"] == expected_state
    assert result["items"] == []
    assert result.get("next_cursor") is None
    provider.assert_not_awaited()


@pytest.mark.parametrize("wrong", ["actor", "connection", "malformed"])
@pytest.mark.asyncio
async def test_cursor_is_bound_to_actor_and_connection_before_provider_call(wrong, monkeypatch):
    session = FakeSession(scalar_results=[connection()])
    patch_actor(monkeypatch)
    provider = AsyncMock()
    monkeypatch.setattr(source_module, "_stream", provider)
    monkeypatch.setattr(source_module, "current_user_id", AsyncMock(return_value=7))
    token = (
        "not-base64"
        if wrong == "malformed"
        else cursor_for(
            actor_id=99 if wrong == "actor" else 8,
            connection_id=99 if wrong == "connection" else 3,
        )
    )

    with pytest.raises(ValueError, match="invalid_task_source_cursor"):
        await Bitrix24TaskSourceService().projection(connection_id=3, cursor=token, session=session)

    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_oauth_requested_scope_does_not_replace_explicit_read_probe(monkeypatch):
    """A token's requested `user` scope is not proof tasks are granted; probe live access."""
    row = connection(scopes=("user",), consent=True)
    session = FakeSession(scalar_results=[row])
    patch_actor(monkeypatch)
    profile = AsyncMock(return_value=7)
    monkeypatch.setattr(source_module, "current_user_id", profile)
    provider = AsyncMock(return_value=([], None))
    monkeypatch.setattr(source_module, "_stream", provider)

    result = await Bitrix24TaskSourceService().projection(connection_id=3, session=session)

    assert result["state"] == "available"
    profile.assert_awaited_once()
    assert provider.await_count == 2  # empty assigned stream advances to watched in the same projection


@pytest.mark.asyncio
async def test_provider_failure_returns_no_partial_items_or_advanced_cursor(monkeypatch):
    row = connection()
    session = FakeSession(scalar_results=[row])
    patch_actor(monkeypatch)
    monkeypatch.setattr(source_module, "current_user_id", AsyncMock(return_value=7))
    provider = AsyncMock(side_effect=bitrix_tasks.BitrixTaskSourceError("source_unavailable"))
    monkeypatch.setattr(source_module, "_stream", provider)

    result = await Bitrix24TaskSourceService().projection(connection_id=3, limit=20, session=session)

    assert result["state"] == "source_unavailable"
    assert result["items"] == []
    assert result.get("next_cursor") is None
    provider.assert_awaited_once()
    assert session.added == []


@pytest.mark.asyncio
async def test_projection_cursor_drains_pages_and_streams_without_drop_or_repeat(monkeypatch):
    """Index cursors resume a provider's 50-row page before switching streams."""
    service = Bitrix24TaskSourceService()
    row = connection(scopes=("user",))
    patch_actor(monkeypatch)
    monkeypatch.setattr(source_module, "current_user_id", AsyncMock(return_value=7))

    assigned_first = [
        {"external_id": str(i), "title": f"Task {i}", "status": 2, "due_at": None, "responsible_id": 7, "auditors": []}
        for i in range(1, 51)
    ]
    assigned_second = [
        {"external_id": str(i), "title": f"Task {i}", "status": 2, "due_at": None, "responsible_id": 7, "auditors": []}
        for i in range(51, 61)
    ]
    watched = [
        {"external_id": "50", "title": "Duplicate", "status": 2, "due_at": None, "responsible_id": 7, "auditors": [7]},
        *[
            {
                "external_id": str(i),
                "title": f"Task {i}",
                "status": 2,
                "due_at": None,
                "responsible_id": (7 if i == 65 else 99),
                "auditors": [7],
            }
            for i in range(61, 73)
        ],
    ]
    calls = []
    page_sessions = []

    async def stream(**kwargs):
        calls.append((kwargs["kind"], kwargs["offset"]))
        if kwargs["kind"] == "assigned" and kwargs["offset"] == 0:
            return assigned_first, 50
        if kwargs["kind"] == "assigned" and kwargs["offset"] == 50:
            return assigned_second, None
        if kwargs["kind"] == "watched" and kwargs["offset"] == 0:
            return watched, None
        raise AssertionError(f"unexpected provider page {kwargs['kind']}:{kwargs['offset']}")

    monkeypatch.setattr(source_module, "_stream", stream)
    pages = []
    cursor = None
    for _ in range(8):
        session = FakeSession(scalar_results=[row])
        page_sessions.append(session)
        result = await service.projection(connection_id=3, cursor=cursor, limit=20, session=session)
        assert result["state"] == "available"
        pages.extend(item["source_ref"] for item in result["items"])
        cursor = result["next_cursor"]
        if cursor is None:
            break
    else:
        pytest.fail("cursor did not terminate")

    ids = [int(value.rsplit(":", 1)[1]) for value in pages]
    assert len(ids) == len(set(ids)) == 71
    assert set(ids) == set(range(1, 61)) | (set(range(61, 73)) - {65})
    assert calls.count(("assigned", 0)) == 3
    assert calls.count(("assigned", 50)) == 1
    assert calls.count(("watched", 0)) == 1
    created_references = [obj for page_session in page_sessions for obj in page_session.added]
    assert len(created_references) == 71
    assert {obj.external_id for obj in created_references} == {
        f"bitrix24:task:{task_id}" for task_id in set(range(1, 61)) | (set(range(61, 73)) - {65})
    }
    assert all(not hasattr(obj, "title") and not hasattr(obj, "status") for obj in created_references)


@pytest.mark.asyncio
async def test_cursor_rejects_invalid_stream_index_and_offsets():
    for token in (
        cursor_for(stream="other"),
        cursor_for(index=-1),
        cursor_for(index=50),
        cursor_for(offset=25),
        cursor_for(offset=source_module.MAX_OFFSET + 50),
        cursor_for(actor_id=99),
        cursor_for(connection_id=99),
        cursor_for(profile_id=99),
    ):
        with pytest.raises(ValueError, match="invalid_task_source_cursor"):
            source_module._decode_cursor(token, connection_id=3, actor_id=8, profile_id=7)


def test_provider_task_links_cannot_change_portal_host():
    assert bitrix_tasks.task_url("team.bitrix24.ru", 7, 12) == (
        "https://team.bitrix24.ru/company/personal/user/7/tasks/task/view/12/"
    )
    with pytest.raises(bitrix_tasks.BitrixTaskSourceError):
        bitrix_tasks.task_url("127.0.0.1", 7, 12)


def test_provider_accepts_tasks_object_envelope_and_flattens_only_declared_fields():
    parsed, next_offset = bitrix_tasks._task_list({"result": {"tasks": [task_row(12)]}, "next": None}, offset=0)

    assert parsed[0]["external_id"] == "12"
    assert parsed[0]["title"] == "Task 12"
    assert set(parsed[0]) == {"external_id", "title", "status", "due_at", "responsible_id", "auditors"}
    assert next_offset is None


@pytest.mark.asyncio
async def test_connection_and_consent_queries_are_scoped_to_current_tenant_and_owner(monkeypatch):
    patch_actor(monkeypatch)
    session = FakeSessionWithRows(rows=[])

    result = await Bitrix24TaskSourceService().connections(session=session)

    assert result == {"state": "not_configured", "items": [], "selected_connection_id": None}
    sql = str(session.scalars_statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert "workspace_connections.tenant_id = 4" in sql
    assert "workspace_connections.owner_user_id = 8" in sql
    assert "workspace_connections.provider_id = 'bitrix24'" in sql


@pytest.mark.asyncio
async def test_configure_is_explicit_read_only_actor_consent_and_clears_other_selection(monkeypatch):
    selected = connection(connection_id=3)
    selected.config["task_source"].update(read_consent=False, read_consented_by=None, selected=False)
    other = connection(connection_id=4)
    session = FakeSessionWithRows(rows=[selected, other])
    patch_actor(monkeypatch)
    monkeypatch.setattr(source_module, "workspace_grants", lambda role: {("source", source_module.ActionType.CREATE)})

    result = await Bitrix24TaskSourceService().configure(3, enabled=True, selected=True, session=session)

    assert result == {"state": "available", "connection_id": 3, "enabled": True, "selected": True, "read_only": True}
    assert selected.config["task_source"]["read_consent"] is True
    assert selected.config["task_source"]["read_consented_by"] == 8
    assert selected.config["task_source"]["read_consented_at"]
    assert selected.config["task_source"]["write_consent"] is False
    assert other.config["task_source"]["selected"] is False
    assert session.flush_count == 1


@pytest.mark.asyncio
async def test_configure_rechecks_source_create_grant_before_writing(monkeypatch):
    session = FakeSessionWithRows(rows=[connection()])
    patch_actor(monkeypatch)
    monkeypatch.setattr(source_module, "workspace_grants", lambda _role: set())

    with pytest.raises(PermissionError):
        await Bitrix24TaskSourceService().configure(3, enabled=True, selected=True, session=session)

    assert session.flush_count == 0
    assert connection().config["task_source"]["read_consent"] is True


@pytest.mark.asyncio
async def test_projection_rejects_mismatched_today_actor_before_provider(monkeypatch):
    session = FakeSession(scalar_results=[connection()])
    patch_actor(monkeypatch)
    monkeypatch.setattr(source_module, "workspace_grants", lambda _role: {("source", source_module.ActionType.VIEW)})
    provider = AsyncMock()
    monkeypatch.setattr(source_module, "current_user_id", provider)

    result = await Bitrix24TaskSourceService().projection(
        connection_id=3, expected_tenant_id=4, expected_user_id=99, session=session
    )

    assert result == {"state": "denied", "items": [], "next_cursor": None}
    provider.assert_not_awaited()
    assert session.scalar_statements == []


@pytest.mark.asyncio
async def test_projection_requires_source_view_grant_freshly(monkeypatch):
    session = FakeSession(scalar_results=[connection()])
    patch_actor(monkeypatch)
    monkeypatch.setattr(source_module, "workspace_grants", lambda _role: set())
    provider = AsyncMock()
    monkeypatch.setattr(source_module, "current_user_id", provider)

    with pytest.raises(PermissionError):
        await Bitrix24TaskSourceService().projection(connection_id=3, session=session)

    provider.assert_not_awaited()
    assert session.scalar_statements == []
