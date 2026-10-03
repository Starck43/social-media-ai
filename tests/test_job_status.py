"""Tests for the run-now notification: job result summary + status endpoint."""

import re
import secrets
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import Job, User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.web.tasks import _job_summary, _source_links

CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')


def _name(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(4)}"


async def _client() -> AsyncClient:
    app = create_application()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True)


async def _csrf(client: AsyncClient, path: str) -> str:
    page = await client.get(path)
    match = CSRF_RE.search(page.text)
    assert match, f"no csrf input on {path}"
    return match.group(1)


async def _register(client: AsyncClient, workspace: str) -> tuple[User, int]:
    username = _name("job")
    token = await _csrf(client, "/app/register")
    resp = await client.post(
        "/app/register",
        data={
            "username": username,
            "email": f"{username}@example.test",
            "password": "secret-password-1",
            "workspace": workspace,
            "_csrf": token,
        },
    )
    assert resp.status_code == 200
    user = await User.objects.get(username=username)
    memberships = await TenantUserManager().web_memberships(user.id)
    return user, memberships[0].tenant_id


# ── _job_summary (pure) ─────────────────────────────────────────────────────


def test_job_summary_collect_done():
    """The modal gets a headline plus named tiles — not one string of `k: v`.

    `источников: 2, собрано: 1, элементов: 12` read like config keys, and
    `собрано` next to `элементов` looked like two names for the same number.
    """
    job = type("J", (), {"job_type": "collect", "status": "done", "error": None})()
    job.result = {
        "sources": 2,
        "collected": 1,
        "empty": 1,
        "error": 0,
        "items": 12,
        "new_items": 3,
    }
    s = _job_summary(job)
    assert s["status"] == "done"
    assert s["title"] == "Сбор данных"
    # The headline is the *new* count: the platform re-serves the same posts, so
    # "12 collected" twice in a row said nothing about the second run.
    assert s["headline"]["value"] == 3
    assert s["headline"]["label"] == "Новых записей"
    tiles = {t["label"]: t["value"] for t in s["stats"]}
    assert tiles == {
        "источников опрошено": 2,
        "ответили данными": 1,
        "получено записей": 12,
        "без содержимого": 1,
        "ошибок": 0,
    }


def test_job_summary_collect_headline_falls_back_when_the_counter_is_absent():
    """Jobs written before `new_items` existed must not claim "0 новых".

    A missing counter means "not measured", so the headline falls back to the
    total that *was* recorded rather than inventing a zero.
    """
    job = type("J", (), {"job_type": "collect", "status": "done", "error": None})()
    job.result = {"sources": 1, "collected": 1, "items": 12}
    s = _job_summary(job)
    assert s["headline"]["value"] == 12


def test_job_summary_counts_failures_the_way_the_handler_writes_them():
    """The handler writes `error`, the modal used to read `failed`.

    Every run with a failed source therefore reported "ошибок: 0" — the counter
    could only ever be zero, which is worse than showing nothing.
    """
    job = type("J", (), {"job_type": "collect", "status": "done", "error": None})()
    job.result = {"sources": 2, "collected": 1, "empty": 0, "items": 12, "error": 1}
    s = _job_summary(job)
    tiles = {t["label"]: t["value"] for t in s["stats"]}
    assert tiles["ошибок"] == 1


def test_job_summary_pluralises_the_headline():
    job = type("J", (), {"job_type": "collect", "status": "done", "error": None})()
    job.result = {"items": 1}
    assert _job_summary(job)["headline"]["noun"] == "запись"
    job.result = {"items": 3}
    assert _job_summary(job)["headline"]["noun"] == "записи"
    job.result = {"items": 7}
    assert _job_summary(job)["headline"]["noun"] == "записей"


def test_job_summary_failed():
    job = type("J", (), {"job_type": "collect", "status": "failed", "error": "boom", "result": None})()
    s = _job_summary(job)
    assert s["status"] == "failed"
    assert s["error"] == "boom"
    assert s["title"] == "Сбор данных"


def test_job_summary_pending():
    job = type("J", (), {"job_type": "collect", "status": "pending", "error": None, "result": None})()
    s = _job_summary(job, task_name="Ежедневный сбор")
    assert s["status"] == "pending"
    assert s["label"] == "Выполнение задачи «Ежедневный сбор»"


def test_job_summary_analyze_done():
    job = type("J", (), {"job_type": "analyze", "status": "done", "error": None})()
    job.result = {"sources": 1, "analyzed": 4, "actions_created": 2, "skipped": 0}
    s = _job_summary(job)
    assert s["status"] == "done"
    assert s["title"] == "Анализ данных"
    assert s["headline"]["value"] == 4
    tiles = {t["label"]: t["value"] for t in s["stats"]}
    assert tiles["действий создано"] == 2


# ── _source_links (run-now modal → source page) ─────────────────────────────


def test_source_links_point_at_each_source_of_the_run():
    """The modal is a summary, not the data — each source must be one click away."""
    job = type("J", (), {"job_type": "collect", "status": "done", "error": None})()
    job.result = {
        "per_source": [
            {"source_id": 7, "name": "Кигель", "items": 32, "outcome": "collected"},
            {"source_id": 9, "name": "Наталья Русских", "items": 0, "outcome": "empty"},
            {"source_id": 10, "name": "Сломанный", "items": 0, "outcome": "error"},
        ]
    }
    links = _source_links(job)
    assert [link["source_id"] for link in links] == [7, 9, 10]
    assert links[0]["note"] == "32 записи"
    assert links[1]["note"] == "без новых данных"
    assert links[2]["note"] == "ошибка"
    assert links[2]["error"] is True


def test_source_links_report_analysis_counts_for_an_analyze_run():
    job = type("J", (), {"job_type": "analyze", "status": "done", "error": None})()
    job.result = {"per_source": [{"source_id": 4, "name": "Канал", "analyzed": 3, "actions": 1}]}
    links = _source_links(job)
    assert links[0]["note"] == "3 анализа"


def test_source_links_are_empty_without_the_breakdown():
    """Jobs written before `per_source` existed get no links, not invented ones."""
    job = type("J", (), {"job_type": "collect", "status": "done", "error": None})()
    job.result = {"sources": 1, "collected": 1, "items": 53, "collected_sources": ["Кигель"]}
    assert _source_links(job) == []


# ── job status endpoint ─────────────────────────────────────────────────────


async def test_job_status_endpoint_returns_summary():
    async with await _client() as c:
        user, tenant_id = await _register(c, "Job Status")
        job_id = None
        try:
            with tenant_scope(bypass=True):
                job = await Job.objects.create(
                    job_type="collect",
                    payload={},
                    status="done",
                    run_at=datetime.now(timezone.utc),
                    result={"sources": 1, "collected": 1, "items": 5, "error": 0},
                    tenant_id=tenant_id,
                )
                job_id = job.id

            resp = await c.get(f"/app/tasks/job/{job_id}/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "done"
            assert data["headline"]["value"] == 5
            assert data["sources"] == []
        finally:
            if job_id is not None:
                with tenant_scope(bypass=True):
                    await Job.objects.delete_by_id(job_id)
            if user is not None:
                await User.objects.delete_user(user.id)
            await tenants.delete_by_id(tenant_id)


async def test_job_status_endpoint_tenant_scoped():
    """A job of another tenant is not visible (returns not_found)."""
    async with await _client() as c:
        user, tenant_id = await _register(c, "Job Scoped A")
        # Create a second, distinct tenant (not via the shared client session,
        # which would switch the session's active workspace).
        with tenant_scope(bypass=True):
            other = await tenants.create(name=f"Job Scoped B {secrets.token_hex(4)}", slug=f"jb{secrets.token_hex(4)}")
            owner_tenant_id = other.id
        job_id = None
        try:
            with tenant_scope(bypass=True):
                job = await Job.objects.create(
                    job_type="collect",
                    payload={},
                    status="done",
                    run_at=datetime.now(timezone.utc),
                    result={"sources": 1, "items": 3},
                    tenant_id=owner_tenant_id,
                )
                job_id = job.id

            # logged-in user belongs to tenant A, job belongs to B → hidden
            resp = await c.get(f"/app/tasks/job/{job_id}/status")
            assert resp.status_code == 200
            assert resp.json()["status"] == "not_found"
        finally:
            if job_id is not None:
                with tenant_scope(bypass=True):
                    await Job.objects.delete_by_id(job_id)
            if user is not None:
                await User.objects.delete_user(user.id)
            await tenants.delete_by_id(tenant_id)
            await tenants.delete_by_id(owner_tenant_id)
