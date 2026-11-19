"""S2 tests: typed read-only file-workspace actions on the existing Job queue."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import delete, update

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.tenant_context import tenant_scope
from app.integrations.file_workspace.yandex_content import YandexTextClient
from app.jobs import dispatcher
from app.jobs import progress as job_progress
from app.jobs.claim_outcomes import JobClaim, JobClaimLostError
from app.jobs.job_handlers.file_workspace import file_workspace_action_runtime
from app.jobs.replay_policy import can_replay, replay_block_reason
from app.models import FileCatalogObject, Job, Notification, User, WorkspaceConnection
from app.models.managers.file_catalog_manager import FileCatalogManager
from app.models.managers.file_insight_manager import POLICY_REVISION, FileInsightManager
from app.models.managers.job_manager import JobManager
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.file_workspace import queued_actions as qa
from app.services.file_workspace.queued_actions import (
    FILE_WORKSPACE_JOB_TYPE,
    FileWorkspaceActionError,
    FileWorkspaceMutationDisabledError,
    enqueue_file_workspace_action,
    execute_file_workspace_action,
    validate_file_workspace_action_payload,
)
from tests.shared.file_catalog_fixtures import actor, admit, configure, data  # noqa: F401

pytestmark = pytest.mark.tenancy


@pytest.fixture
async def clean_jobs(data):
    yield
    tenant_ids = [t.id for t in data.tenants]
    async with async_session_maker() as session, session.begin():
        await session.execute(delete(Job).where(Job.tenant_id.in_(tenant_ids)))
        await session.execute(delete(Notification).where(Notification.tenant_id.in_(tenant_ids)))


def _page_client(pages_by_query, *, on_call=None):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"].startswith("OAuth ")
        parsed = urlsplit(str(request.url))
        params = parse_qs(parsed.query)
        path = params["path"][0]
        offset = int(params.get("offset", ["0"])[0])
        calls.append((path, offset))
        if on_call is not None:
            on_call(len(calls), path, offset)
        status, body = pages_by_query[(path, offset)]
        return httpx.Response(status, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


def _mock_yandex_text_client(files_by_path: dict[str, bytes]) -> YandexTextClient:
    class _FakeReader:
        def __init__(self, chunks: list[bytes]):
            self._chunks = chunks

        async def read(self, _size: int) -> bytes:
            return self._chunks.pop(0) if self._chunks else b""

    class _FakeWriter:
        def __init__(self, host: str, chunks_out: list[bytes]):
            self.host = host
            self.buf = bytearray()
            self.chunks_out = chunks_out
            self.closed = False

        def write(self, payload: bytes) -> None:
            self.buf.extend(payload)

        async def drain(self) -> None:
            raw = bytes(self.buf).decode("latin1")
            target = raw.split(" ", 2)[1]
            parsed = urlsplit(target)
            if self.host == "cloud-api.yandex.net" and parsed.path == "/v1/disk/resources":
                path = parse_qs(parsed.query)["path"][0]
                content = files_by_path[path]
                body = json.dumps(
                    {
                        "path": path,
                        "name": path.rsplit("/", 1)[-1],
                        "type": "file",
                        "size": len(content),
                        "md5": hashlib.md5(content, usedforsecurity=False).hexdigest(),
                        "modified": "2026-11-19T12:00:00+00:00",
                    }
                ).encode()
            elif self.host == "cloud-api.yandex.net" and parsed.path == "/v1/disk/resources/download":
                path = parse_qs(parsed.query)["path"][0]
                body = json.dumps(
                    {
                        "href": f"https://downloader.dst.yandex.ru/disk/content?path={path}",
                        "method": "GET",
                        "templated": False,
                    }
                ).encode()
            elif self.host == "downloader.dst.yandex.ru":
                path = parse_qs(parsed.query)["path"][0]
                body = files_by_path[path]
            else:
                raise AssertionError(f"Unexpected request: {self.host} {target}")
            headers = (
                f"HTTP/1.1 200 OK\r\n" f"Content-Length: {len(body)}\r\n" f"Content-Encoding: identity\r\n\r\n"
            ).encode("ascii")
            self.chunks_out[:] = [headers + body]

        def close(self) -> None:
            self.closed = True

        async def wait_closed(self) -> None:
            return None

    async def _opener(host: str):
        chunks: list[bytes] = []
        return _FakeReader(chunks), _FakeWriter(host, chunks)

    return YandexTextClient(_opener=_opener)


async def test_default_flags_off_and_mutations_always_refused(data, clean_jobs):
    assert settings.FILE_CATALOG_UI_ENABLED is False
    assert settings.FILE_WORKSPACE_ACTIONS_ENABLED is False
    assert settings.FILE_PRIVATE_RESULTS_UI_ENABLED is False

    with actor(data, 0) as owner:
        for mutating in (
            "write",
            "upload",
            "update",
            "overwrite",
            "create",
            "mkdir",
            "move",
            "rename",
            "copy",
            "delete",
            "trash",
            "share",
            "export",
            "write_file",
            "mutate_tree",
        ):
            with pytest.raises(FileWorkspaceMutationDisabledError):
                validate_file_workspace_action_payload({"action": mutating, "connection_id": owner["connection_id"]})
            with pytest.raises(FileWorkspaceMutationDisabledError):
                await enqueue_file_workspace_action(
                    initiated_by_user_id=owner["actor_id"],
                    action_payload={"action": mutating, "connection_id": owner["connection_id"]},
                )

        with pytest.raises(FileWorkspaceActionError):
            validate_file_workspace_action_payload({"action": "unknown_op", "connection_id": owner["connection_id"]})

        # Even if a row is inserted manually into jobs bypassing enqueue, dispatcher refuses writes.
        raw_job = await JobManager().enqueue(
            FILE_WORKSPACE_JOB_TYPE,
            payload={"action": "delete", "connection_id": owner["connection_id"]},
            initiated_by_user_id=owner["actor_id"],
            max_attempts=1,
        )
        outcome = await dispatcher.run_job_now(raw_job.id, allow_retry=False)
        assert outcome["status"] == "failed"
        persisted = await JobManager().get(id=raw_job.id)
        assert can_replay(persisted) is False
        assert persisted.status == "failed"
        assert persisted.error == "file_workspace_writes_disabled"


async def test_actor_tenant_owner_spoofing_and_replay_blocked(data, clean_jobs):
    with actor(data, 0) as owner:
        for spoof_key in ("_trusted_initiated_by_user_id", "tenant_id", "actor_id", "token", "url", "allow_writes"):
            with pytest.raises(FileWorkspaceActionError):
                await enqueue_file_workspace_action(
                    initiated_by_user_id=owner["actor_id"],
                    action_payload={
                        "action": "catalog_search",
                        "connection_id": owner["connection_id"],
                        spoof_key: 999,
                    },
                )

        # Forged missing _trusted_initiated_by_user_id in direct execute fails closed.
        with pytest.raises(ConnectionDenied):
            await execute_file_workspace_action({"action": "catalog_search", "connection_id": owner["connection_id"]})

        # Inactive user cannot execute even an already-queued row.
        job = await enqueue_file_workspace_action(
            initiated_by_user_id=owner["actor_id"],
            action_payload={"action": "catalog_search", "connection_id": owner["connection_id"]},
        )
        async with async_session_maker() as session, session.begin():
            await session.execute(update(User).where(User.id == owner["actor_id"]).values(is_active=False))
        try:
            outcome = await dispatcher.run_job_now(job.id, allow_retry=False)
            assert outcome["status"] == "failed"
            row = await JobManager().get(id=job.id)
            assert row.status == "failed"
            assert row.error == "file_workspace_access_denied"
        finally:
            async with async_session_maker() as session, session.begin():
                await session.execute(update(User).where(User.id == owner["actor_id"]).values(is_active=True))

    # Second member in same workspace cannot queue against owner's personal connection.
    with actor(data, 1, 0, 0) as other_member:
        with pytest.raises(ConnectionDenied):
            await enqueue_file_workspace_action(
                initiated_by_user_id=other_member["actor_id"],
                action_payload={"action": "catalog_search", "connection_id": other_member["connection_id"]},
            )

    # Foreign tenant cannot queue against tenant 0's connection.
    with actor(data, 0, 1, 2) as foreign:
        with pytest.raises(ConnectionDenied):
            await enqueue_file_workspace_action(
                initiated_by_user_id=foreign["actor_id"],
                action_payload={"action": "catalog_search", "connection_id": data.connections[0]},
            )


async def test_catalog_search_respects_lifecycle_visibility_budgets_and_privacy(data, clean_jobs, caplog):
    with actor(data, 0) as owner:
        await admit(owner, "alpha.txt", folder="disk:/work")
        await admit(owner, "beta.txt", folder="disk:/work")
        await admit(owner, "gamma.txt", folder="disk:/work")
        await admit(owner, "secret-item.txt", folder="disk:/work/secret")
    await configure(data, restrictions=[{"path": "disk:/work/secret", "deny": ["list"]}])

    with actor(data, 0) as owner:
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(FileCatalogObject)
                .where(FileCatalogObject.tenant_id == owner["tenant_id"], FileCatalogObject.name == "gamma.txt")
                .values(lifecycle_state="missing", missing_since=datetime.now(timezone.utc))
            )

        job = await enqueue_file_workspace_action(
            initiated_by_user_id=owner["actor_id"],
            action_payload={
                "action": "catalog_search",
                "connection_id": owner["connection_id"],
                "lifecycle": "present",
                "page_size": 1,
                "max_pages": 1,
                "max_items": 5,
            },
        )
        with caplog.at_level(logging.INFO):
            outcome = await dispatcher.run_job_now(job.id, allow_retry=False)
        assert outcome["status"] == "done"
        row = await JobManager().get(id=job.id)
        assert row.status == "done"
        assert can_replay(row) is False
        assert "нельзя безопасно повторить" in (replay_block_reason(row) or "")
        assert row.result["action"] == "catalog_search"
        assert row.result["pages_processed"] == 1
        assert row.result["matched_count"] == 1
        assert row.result["has_more"] is True
        assert row.result["budget_exhausted"] is True
        assert row.result["mutations_enabled"] is False
        assert len(row.result["object_ids"]) == 1

        # Zero filenames, paths or secret folder hints leak into result, progress or dispatcher logs.
        serialized = json.dumps(row.result, ensure_ascii=False)
        for sensitive in ("alpha.txt", "beta.txt", "gamma.txt", "secret-item.txt", "disk:/work"):
            assert sensitive not in serialized
            assert sensitive not in caplog.text

        # Now search all present pages cleanly with max_pages=5.
        job_all = await enqueue_file_workspace_action(
            initiated_by_user_id=owner["actor_id"],
            action_payload={
                "action": "catalog_search",
                "connection_id": owner["connection_id"],
                "lifecycle": "present",
                "page_size": 1,
                "max_pages": 5,
                "max_items": 10,
            },
        )
        await dispatcher.run_job_now(job_all.id, allow_retry=False)
        row_all = await JobManager().get(id=job_all.id)
        assert row_all.result["matched_count"] == 2
        assert row_all.result["has_more"] is False
        assert row_all.result["budget_exhausted"] is False


async def test_catalog_scan_step_fail_closed_budgets_and_s1_reconciliation(data, clean_jobs):
    with actor(data, 0) as owner:
        await admit(owner, "gone.txt", folder="disk:/work")

        # Default runtime without reviewed client fails closed cleanly.
        no_client_job = await enqueue_file_workspace_action(
            initiated_by_user_id=owner["actor_id"],
            action_payload={
                "action": "catalog_scan_step",
                "connection_id": owner["connection_id"],
                "start_new": True,
                "folder": "disk:/work",
                "page_size": 1,
                "max_pages": 2,
            },
        )
        await dispatcher.run_job_now(no_client_job.id, allow_retry=False)
        failed_row = await JobManager().get(id=no_client_job.id)
        assert failed_row.status == "failed"
        assert failed_row.error == "file_workspace_dependency_unavailable"

        # Bounded 1-page run leaves scan partial with budget_exhausted=True, then 2nd run exhausts and reconciles.
        client, calls = _page_client(
            {
                (
                    "disk:/work",
                    0,
                ): (
                    200,
                    {
                        "path": "disk:/work",
                        "type": "dir",
                        "_embedded": {
                            "items": [{"path": "disk:/work/a.txt", "name": "a.txt", "type": "file"}],
                            "limit": 1,
                            "offset": 0,
                            "total": 2,
                        },
                    },
                ),
                (
                    "disk:/work",
                    1,
                ): (
                    200,
                    {
                        "path": "disk:/work",
                        "type": "dir",
                        "_embedded": {
                            "items": [{"path": "disk:/work/b.txt", "name": "b.txt", "type": "file"}],
                            "limit": 1,
                            "offset": 1,
                            "total": 2,
                        },
                    },
                ),
                (
                    "disk:/work",
                    2,
                ): (
                    200,
                    {
                        "path": "disk:/work",
                        "type": "dir",
                        "_embedded": {
                            "items": [],
                            "limit": 1,
                            "offset": 2,
                            "total": 2,
                        },
                    },
                ),
            }
        )
        try:
            job_1 = await enqueue_file_workspace_action(
                initiated_by_user_id=owner["actor_id"],
                action_payload={
                    "action": "catalog_scan_step",
                    "connection_id": owner["connection_id"],
                    "start_new": True,
                    "folder": "disk:/work",
                    "page_size": 1,
                    "max_pages": 1,
                },
            )
            with file_workspace_action_runtime(catalog_client=client):
                await dispatcher.run_job_now(job_1.id, allow_retry=False)
            r1 = await JobManager().get(id=job_1.id)
            assert r1.status == "done"
            assert r1.result["pages_processed"] == 1
            assert r1.result["scan_state"] == "partial"
            assert r1.result["exhausted"] is False
            assert r1.result["budget_exhausted"] is True

            job_2 = await enqueue_file_workspace_action(
                initiated_by_user_id=owner["actor_id"],
                action_payload={
                    "action": "catalog_scan_step",
                    "connection_id": owner["connection_id"],
                    "start_new": False,
                    "page_size": 1,
                    "max_pages": 3,
                },
            )
            with file_workspace_action_runtime(catalog_client=client):
                await dispatcher.run_job_now(job_2.id, allow_retry=False)
            r2 = await JobManager().get(id=job_2.id)
            assert r2.status == "done"
            assert r2.result["pages_processed"] == 2
            assert r2.result["scan_state"] == "exhausted"
            assert r2.result["exhausted"] is True
            assert len(calls) == 3
        finally:
            await client.aclose()

        # Verify S1 exhausted reconciliation marked gone.txt missing and new files present.
        catalog = await FileCatalogManager().list_page(
            tenant_id=owner["tenant_id"],
            actor_id=owner["actor_id"],
            connection_id=owner["connection_id"],
        )
        by_name = {item["name"]: item for item in catalog["items"]}
        assert by_name["a.txt"]["lifecycle_state"] == "present"
        assert by_name["b.txt"]["lifecycle_state"] == "present"
        assert by_name["gone.txt"]["lifecycle_state"] == "missing"

        # Rate-limit error returns partial status cleanly without silent success.
        rl_client, _ = _page_client({("disk:/work", 0): (429, {"message": "too many requests"})})
        try:
            job_rl = await enqueue_file_workspace_action(
                initiated_by_user_id=owner["actor_id"],
                action_payload={
                    "action": "catalog_scan_step",
                    "connection_id": owner["connection_id"],
                    "start_new": True,
                    "folder": "disk:/work",
                    "page_size": 1,
                    "max_pages": 2,
                },
            )
            with file_workspace_action_runtime(catalog_client=rl_client):
                await dispatcher.run_job_now(job_rl.id, allow_retry=False)
            r_rl = await JobManager().get(id=job_rl.id)
            assert r_rl.status == "done"
            assert r_rl.result["status"] == "partial"
            assert r_rl.result["scan_state"] == "error"
            assert r_rl.result["error_code"] == "rate_limited"
        finally:
            await rl_client.aclose()


async def test_cooperative_cancellation_stops_before_second_provider_page(data, clean_jobs, monkeypatch):
    with actor(data, 0) as owner:
        job = await enqueue_file_workspace_action(
            initiated_by_user_id=owner["actor_id"],
            action_payload={
                "action": "catalog_scan_step",
                "connection_id": owner["connection_id"],
                "start_new": True,
                "folder": "disk:/work",
                "page_size": 1,
                "max_pages": 3,
            },
        )

    original_report = job_progress.report_action
    page_calls = []

    def on_provider_call(count, _path, _offset):
        page_calls.append(count)

    client, _ = _page_client(
        {
            (
                "disk:/work",
                0,
            ): (
                200,
                {
                    "path": "disk:/work",
                    "type": "dir",
                    "_embedded": {
                        "items": [{"path": "disk:/work/p1.txt", "name": "p1.txt", "type": "file"}],
                        "limit": 1,
                        "offset": 0,
                        "total": 3,
                    },
                },
            ),
            (
                "disk:/work",
                1,
            ): (
                200,
                {
                    "path": "disk:/work",
                    "type": "dir",
                    "_embedded": {
                        "items": [{"path": "disk:/work/p2.txt", "name": "p2.txt", "type": "file"}],
                        "limit": 1,
                        "offset": 1,
                        "total": 3,
                    },
                },
            ),
        },
        on_call=on_provider_call,
    )

    cancelled_once = False

    async def cancelling_report_action(*args, **kwargs):
        nonlocal cancelled_once
        await original_report(*args, **kwargs)
        if kwargs.get("phase") == "running" and kwargs.get("completed") == 0 and not cancelled_once:
            cancelled_once = True
            with tenant_scope(data.tenants[0].id):
                running = await JobManager().get(id=job.id)
                assert running.status == "running"
                claim = JobClaim.capture(running)
                assert await JobManager().cancel_running(claim) is True

    monkeypatch.setattr(job_progress, "report_action", cancelling_report_action)
    try:
        with actor(data, 0):
            with file_workspace_action_runtime(catalog_client=client):
                with pytest.raises(JobClaimLostError):
                    await dispatcher.run_job_now(job.id, allow_retry=False)
            persisted = await JobManager().get(id=job.id)
            assert persisted.status == "failed"
            assert persisted.error == "Cancelled by operator"
            # Page 1 ran before the next boundary, and page 2 was NEVER requested after cancellation.
            assert page_calls == [1]
    finally:
        await client.aclose()


async def test_midflight_connection_revocation_stops_scan_cleanly(data, clean_jobs, monkeypatch):
    client, _ = _page_client(
        {
            (
                "disk:/work",
                0,
            ): (
                200,
                {
                    "path": "disk:/work",
                    "type": "dir",
                    "_embedded": {
                        "items": [{"path": "disk:/work/a.txt", "name": "a.txt", "type": "file"}],
                        "limit": 1,
                        "offset": 0,
                        "total": 2,
                    },
                },
            ),
            (
                "disk:/work",
                1,
            ): (
                200,
                {
                    "path": "disk:/work",
                    "type": "dir",
                    "_embedded": {
                        "items": [{"path": "disk:/work/b.txt", "name": "b.txt", "type": "file"}],
                        "limit": 1,
                        "offset": 1,
                        "total": 2,
                    },
                },
            ),
        }
    )
    original_resume = qa.resume_catalog_scan
    seen_calls = 0

    async def revoking_resume(**kwargs):
        nonlocal seen_calls
        seen_calls += 1
        result = await original_resume(**kwargs)
        if seen_calls == 1:
            async with async_session_maker() as session, session.begin():
                await session.execute(
                    update(WorkspaceConnection)
                    .where(WorkspaceConnection.id == data.connections[0])
                    .values(is_active=False)
                )
        return result

    monkeypatch.setattr(qa, "resume_catalog_scan", revoking_resume)
    try:
        with actor(data, 0) as owner:
            job = await enqueue_file_workspace_action(
                initiated_by_user_id=owner["actor_id"],
                action_payload={
                    "action": "catalog_scan_step",
                    "connection_id": owner["connection_id"],
                    "start_new": True,
                    "folder": "disk:/work",
                    "page_size": 1,
                    "max_pages": 2,
                },
            )
            with file_workspace_action_runtime(catalog_client=client):
                await dispatcher.run_job_now(job.id, allow_retry=False)
            row = await JobManager().get(id=job.id)
            assert row.status == "failed"
            assert row.error == "file_workspace_access_denied"
    finally:
        await client.aclose()
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(WorkspaceConnection).where(WorkspaceConnection.id == data.connections[0]).values(is_active=True)
            )


async def test_content_read_prep_requires_consent_present_lifecycle_and_byte_budget(data, clean_jobs):
    with actor(data, 0) as owner:
        await admit(owner, "notes.txt", folder="disk:/work")
        await admit(owner, "absent.txt", folder="disk:/work")
        listing = await FileCatalogManager().list_page(
            tenant_id=owner["tenant_id"],
            actor_id=owner["actor_id"],
            connection_id=owner["connection_id"],
        )
        ids = {item["name"]: item["id"] for item in listing["items"]}
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(FileCatalogObject)
                .where(FileCatalogObject.id == ids["absent.txt"])
                .values(lifecycle_state="missing", missing_since=datetime.now(timezone.utc))
            )

        # Missing cloud processing consent -> refused at enqueue.
        with pytest.raises(PermissionError):
            await enqueue_file_workspace_action(
                initiated_by_user_id=owner["actor_id"],
                action_payload={
                    "action": "content_read_prep",
                    "connection_id": owner["connection_id"],
                    "object_id": ids["notes.txt"],
                },
            )

        await FileInsightManager(enabled=True).set_consent(
            **owner, processing=True, retention=False, policy_revision=POLICY_REVISION
        )

        # Missing lifecycle object -> refused at enqueue (S1 guard).
        with pytest.raises(ConnectionDenied):
            await enqueue_file_workspace_action(
                initiated_by_user_id=owner["actor_id"],
                action_payload={
                    "action": "content_read_prep",
                    "connection_id": owner["connection_id"],
                    "object_id": ids["absent.txt"],
                },
            )

        body = b"line one private text\nline two sensitive insight\nline three"
        text_client = _mock_yandex_text_client({"disk:/work/notes.txt": body})

        # Byte budget smaller than downloaded file -> fails closed with budget error.
        small_job = await enqueue_file_workspace_action(
            initiated_by_user_id=owner["actor_id"],
            action_payload={
                "action": "content_read_prep",
                "connection_id": owner["connection_id"],
                "object_id": ids["notes.txt"],
                "start_line": 1,
                "end_line": 2,
                "max_bytes": 10,
            },
        )
        with file_workspace_action_runtime(content_client=text_client):
            await dispatcher.run_job_now(small_job.id, allow_retry=False)
        small_row = await JobManager().get(id=small_job.id)
        assert small_row.status == "failed"
        assert small_row.error == "file_workspace_budget_exhausted"

        # Valid bounded content_read_prep extracts in memory and persists only sanitized prep counts.
        ok_job = await enqueue_file_workspace_action(
            initiated_by_user_id=owner["actor_id"],
            action_payload={
                "action": "content_read_prep",
                "connection_id": owner["connection_id"],
                "object_id": ids["notes.txt"],
                "start_line": 1,
                "end_line": 2,
                "max_bytes": 4096,
            },
        )
        # Observe bounded progress snapshot written while job is running; final result replaces display progress.
        observed_snaps = []
        orig_record = JobManager.record_progress

        async def spy_record_progress(self, claim, snapshot):
            ok = await orig_record(self, claim, snapshot)
            if ok:
                row = await JobManager().get(id=ok_job.id)
                observed_snaps.append(job_progress.read_action_snapshot(row.result, row.attempts))
            return ok

        mp = pytest.MonkeyPatch()
        mp.setattr(JobManager, "record_progress", spy_record_progress)
        try:
            with file_workspace_action_runtime(content_client=text_client):
                await dispatcher.run_job_now(ok_job.id, allow_retry=False)
        finally:
            mp.undo()

        assert len(observed_snaps) == 2
        assert observed_snaps[-1] is not None
        assert observed_snaps[-1]["action"] == "content_read_prep"
        assert observed_snaps[-1]["phase"] == "done"
        assert observed_snaps[-1]["processed_bytes"] == len(body)

        ok_row = await JobManager().get(id=ok_job.id)
        assert ok_row.status == "done"
        assert ok_row.result == {
            "status": "ok",
            "action": "content_read_prep",
            "connection_id": owner["connection_id"],
            "object_id": ids["notes.txt"],
            "line_count": 3,
            "selected_lines": 2,
            "byte_count": len(body),
            "parser_version": "utf8-plain-v1",
            "retention_available": False,
            "prepared": True,
            "mutations_enabled": False,
        }
        serialized = json.dumps(ok_row.result, ensure_ascii=False)
        for hidden in ("line one", "sensitive insight", "notes.txt", "disk:/work"):
            assert hidden not in serialized
