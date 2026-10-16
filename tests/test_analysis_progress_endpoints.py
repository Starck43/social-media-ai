"""Progress endpoint privacy with mocked storage and no database/provider calls."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.models.managers.job_manager import JobManager
from app.web import sources, tasks

SECRET = "PRIVATE-PROVIDER-DETAIL"


def _request(*, tenant_id=41, memberships=(), platform_admin=False):
    request = Request({
        "type": "http", "method": "GET", "path": "/app/tasks/job/88/status",
        "headers": [], "query_string": b"",
    })
    request.state.tenant_id = tenant_id
    request.state.web_user = SimpleNamespace(id=12, is_active=True, is_platform_admin=platform_admin)
    request.state.memberships = list(memberships)
    return request


def _membership(*, tenant_id=41, active=True, role="admin", user_id=12):
    return SimpleNamespace(
        tenant_id=tenant_id, user_id=user_id, channel="web", is_active=active,
        role=SimpleNamespace(codename=role),
    )


def _job(*, tenant_id=41):
    progress = {
        "attempt": 3, "total": 1, "completed": 0, "first": 0,
        "current": "Private source", "updated_at": "2026-10-17T09:00:00+00:00",
        "items": [{
            "source_id": 9, "name": "Private source", "state": "running",
            "raw_total": 12, "raw_processed": 3, "raw_selected": 4,
        }],
    }
    return SimpleNamespace(
        id=88, tenant_id=tenant_id, job_type="analyze", status="failed", error=SECRET,
        attempts=3, locked_at=datetime(2026, 10, 16, tzinfo=timezone.utc),
        agent_task_id=None, payload={}, result={
            "per_source": [{
                "source_id": 9, "name": "Previous attempt", "raw_total": 99,
                "raw_processed": 99, "raw_selected": 99,
            }],
            "progress": progress,
            "provider_payload": SECRET,
        },
    )


class _JobQuery:
    def __init__(self, job):
        self.job = job

    async def first(self):
        return self.job


def _mock_job_store(monkeypatch, job):
    monkeypatch.setattr(tasks.Job.objects, "filter", lambda **criteria: _JobQuery(
        job if criteria.get("tenant_id") == job.tenant_id and criteria.get("id") == job.id else None
    ))


@pytest.mark.parametrize(
    "memberships,platform_admin",
    [([], True), ([_membership(active=False)], False), ([_membership(role="owner")], False),
     ([_membership(tenant_id=77)], False)],
)
async def test_job_status_diagnostics_require_active_membership_and_admin_role(
    monkeypatch, memberships, platform_admin,
):
    job = _job()
    _mock_job_store(monkeypatch, job)
    monkeypatch.setattr(tasks, "perms_can", lambda request, resource, action: resource == "agenttask")

    response = await tasks.job_status(_request(memberships=memberships, platform_admin=platform_admin), 88)

    data = response.body.decode()
    assert SECRET not in data
    assert "diagnostics" not in data
    assert "raw_coverage" not in data


async def test_workspace_admin_gets_allowlisted_job_diagnostics(monkeypatch):
    job = _job()
    _mock_job_store(monkeypatch, job)
    monkeypatch.setattr(tasks, "perms_can", lambda request, resource, action: True)

    response = await tasks.job_status(_request(memberships=[_membership()]), 88)

    data = response.body.decode()
    assert SECRET not in data
    assert '"job_id":88' in data
    assert '"attempt":3' in data
    assert '"selected":4' in data
    assert '"remaining":9' in data
    assert '"error_code":"unknown"' in data
    assert '"raw_coverage"' in data
    assert '"total":12' in data and '"total":99' not in data


async def test_job_without_source_permission_hides_source_coverage_and_progress(monkeypatch):
    job = _job()
    _mock_job_store(monkeypatch, job)
    monkeypatch.setattr(tasks, "perms_can", lambda request, resource, action: resource == "agenttask")

    response = await tasks.job_status(_request(memberships=[_membership()]), 88)

    data = response.body.decode()
    assert SECRET not in data
    assert '"diagnostics"' in data
    assert '"selected":null' in data and '"remaining":null' in data
    assert '"raw_coverage"' not in data and '"progress"' not in data
    assert "Private source" not in data


def test_pending_retry_diagnostics_do_not_reuse_prior_snapshot_counters_or_time():
    job = _job()
    job.status = "pending"

    diagnostics = tasks._diagnostics_view(job)

    assert diagnostics["selected"] is None
    assert diagnostics["remaining"] is None
    assert diagnostics["progress_updated_at"] is None


def test_cancelled_before_start_diagnostics_do_not_reuse_prior_snapshot():
    job = _job()
    job.error = "Cancelled before start"

    diagnostics = tasks._diagnostics_view(job)

    assert diagnostics["selected"] is None
    assert diagnostics["remaining"] is None
    assert diagnostics["progress_updated_at"] is None


def test_done_coverage_prefers_final_per_source_counters():
    job = _job()
    job.status = "done"
    job.result["per_source"] = [{
        "source_id": 9, "name": "Current", "raw_total": 8, "raw_processed": 8, "raw_selected": 8,
    }]

    coverage = tasks._raw_coverage(job)

    assert coverage[0]["total"] == 8
    assert coverage[0]["processed"] == 8
    assert coverage[0]["percent"] == 100


@pytest.mark.parametrize("membership,has_diagnostics", [(_membership(), True), (_membership(active=False), False)])
async def test_source_progress_fragment_obeys_workspace_admin_diagnostics_gate(
    monkeypatch, membership, has_diagnostics,
):
    job = _job()
    source = SimpleNamespace(id=9, tenant_id=41)

    class _SourceQuery:
        async def first(self):
            return source

    monkeypatch.setattr(sources.Source.objects, "filter", lambda **criteria: _SourceQuery())
    monkeypatch.setattr(JobManager, "source_progress_jobs", lambda self, *args, **kwargs: _async_jobs(job))
    monkeypatch.setattr(sources, "perms_can", lambda request, resource, action: True)

    response = await sources.source_analysis_progress(
        _request(memberships=[membership]), 9
    )

    assert response.status_code == 200
    assert SECRET not in response.body.decode()
    assert ("Диагностика" in response.body.decode()) is has_diagnostics


async def _async_jobs(job):
    return [job]
