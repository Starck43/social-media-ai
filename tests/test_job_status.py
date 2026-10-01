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
from app.web.tasks import _job_summary

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
    job = type("J", (), {"job_type": "collect", "status": "done", "error": None})()
    job.result = {"sources": 2, "collected": 1, "items": 12, "failed": 1}
    s = _job_summary(job)
    assert s["status"] == "done"
    assert "источников: 2" in s["detail"]
    assert "элементов: 12" in s["detail"]


def test_job_summary_failed():
    job = type("J", (), {"job_type": "collect", "status": "failed", "error": "boom", "result": None})()
    s = _job_summary(job)
    assert s["status"] == "failed"
    assert s["error"] == "boom"


def test_job_summary_pending():
    job = type("J", (), {"job_type": "collect", "status": "pending", "error": None, "result": None})()
    s = _job_summary(job, task_name="Ежедневный сбор")
    assert s["status"] == "pending"
    assert s["label"] == "Выполнение задачи «Ежедневный сбор»"


def test_job_summary_analyze_done():
    job = type("J", (), {"job_type": "analyze", "status": "done", "error": None})()
    job.result = {"sources": 1, "analyzed": 4, "actions_created": 2}
    s = _job_summary(job)
    assert s["status"] == "done"
    assert "действий: 2" in s["detail"]


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
                    result={"sources": 1, "collected": 1, "items": 5, "failed": 0},
                    tenant_id=tenant_id,
                )
                job_id = job.id

            resp = await c.get(f"/app/tasks/job/{job_id}/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "done"
            assert "элементов: 5" in data["detail"]
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
