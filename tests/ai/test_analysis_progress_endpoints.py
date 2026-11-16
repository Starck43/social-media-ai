"""Progress endpoint privacy with mocked storage and no database/provider calls."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.models.managers.job_manager import JobManager
from app.web import job_routes as tasks
from app.web import sources, task_presenters

SECRET = "PRIVATE-PROVIDER-DETAIL"


def _request(*, tenant_id=41, memberships=(), platform_admin=False):
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/app/tasks/job/88/status",
            "headers": [],
            "query_string": b"",
        }
    )
    request.state.tenant_id = tenant_id
    request.state.web_user = SimpleNamespace(id=12, is_active=True, is_platform_admin=platform_admin)
    request.state.memberships = list(memberships)
    return request


def _membership(*, tenant_id=41, active=True, role="admin", user_id=12):
    return SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
        channel="web",
        is_active=active,
        role=SimpleNamespace(codename=role),
    )


def _job(*, tenant_id=41):
    progress = {
        "attempt": 3,
        "total": 1,
        "completed": 0,
        "first": 0,
        "current": "Private source",
        "updated_at": "2026-10-17T09:00:00+00:00",
        "items": [
            {
                "source_id": 9,
                "name": "Private source",
                "state": "running",
                "raw_total": 12,
                "raw_processed": 3,
                "raw_selected": 4,
            }
        ],
    }
    return SimpleNamespace(
        id=88,
        tenant_id=tenant_id,
        job_type="analyze",
        status="failed",
        error=SECRET,
        attempts=3,
        locked_at=datetime(2026, 10, 16, tzinfo=timezone.utc),
        agent_task_id=None,
        payload={},
        result={
            "per_source": [
                {
                    "source_id": 9,
                    "name": "Previous attempt",
                    "raw_total": 99,
                    "raw_processed": 99,
                    "raw_selected": 99,
                }
            ],
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
    monkeypatch.setattr(
        tasks.Job.objects,
        "filter",
        lambda **criteria: _JobQuery(
            job if criteria.get("tenant_id") == job.tenant_id and criteria.get("id") == job.id else None
        ),
    )


@pytest.mark.parametrize(
    "memberships,platform_admin",
    [
        ([], True),
        ([_membership(active=False)], False),
        ([_membership(role="owner")], False),
        ([_membership(tenant_id=77)], False),
    ],
)
async def test_job_status_diagnostics_require_active_membership_and_admin_role(
    monkeypatch,
    memberships,
    platform_admin,
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

    body = json.loads(response.body)
    assert SECRET not in response.body.decode()
    diagnostics = body["diagnostics"]
    assert "selected" not in diagnostics and "remaining" not in diagnostics
    assert "raw_coverage" not in body and "progress" not in body
    assert "Private source" not in response.body.decode()


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
    job.result["per_source"] = [
        {
            "source_id": 9,
            "name": "Current",
            "raw_total": 8,
            "raw_processed": 8,
            "raw_selected": 8,
        }
    ]

    coverage = task_presenters._raw_coverage(job)

    assert coverage[0]["total"] == 8
    assert coverage[0]["processed"] == 8
    assert coverage[0]["percent"] == 100


@pytest.mark.parametrize("membership,has_diagnostics", [(_membership(), True), (_membership(active=False), False)])
async def test_source_progress_fragment_obeys_workspace_admin_diagnostics_gate(
    monkeypatch,
    membership,
    has_diagnostics,
):
    job = _job()
    source = SimpleNamespace(id=9, tenant_id=41)

    class _SourceQuery:
        async def first(self):
            return source

    monkeypatch.setattr(sources.Source.objects, "filter", lambda **criteria: _SourceQuery())
    monkeypatch.setattr(JobManager, "source_progress_jobs", lambda self, *args, **kwargs: _async_jobs(job))
    monkeypatch.setattr(sources, "perms_can", lambda request, resource, action: True)

    response = await sources.source_analysis_progress(_request(memberships=[membership]), 9)

    assert response.status_code == 200
    assert SECRET not in response.body.decode()
    assert ("Диагностика" in response.body.decode()) is has_diagnostics


async def test_source_progress_fragment_adds_run_model_and_counters_for_admins(monkeypatch):
    job = _job()
    job.status = "done"
    source = SimpleNamespace(id=9, tenant_id=41)

    class _SourceQuery:
        async def first(self):
            return source

    monkeypatch.setattr(sources.Source.objects, "filter", lambda **criteria: _SourceQuery())
    monkeypatch.setattr(JobManager, "source_progress_jobs", lambda self, *args, **kwargs: _async_jobs(job))
    monkeypatch.setattr(sources, "perms_can", lambda request, resource, action: True)

    async def _summary(source_id, job):
        assert source_id == 9
        return {
            "total": 1,
            "succeeded": 0,
            "failed": 1,
            "model": "zai-org/GLM-5.3-Flash",
            "provider": "river",
            "error_code": "failed_transport",
        }

    monkeypatch.setattr(sources, "_run_llm_summary", _summary)

    response = await sources.source_analysis_progress(_request(memberships=[_membership()]), 9)

    body = response.body.decode()
    assert SECRET not in body
    assert "zai-org/GLM-5.3-Flash" in body and "failed_transport" in body
    assert "0 успешно" in body and "1 с ошибками" in body


async def test_source_progress_fragment_without_run_summary_stays_truthful(monkeypatch):
    job = _job()
    job.status = "done"
    source = SimpleNamespace(id=9, tenant_id=41)

    class _SourceQuery:
        async def first(self):
            return source

    monkeypatch.setattr(sources.Source.objects, "filter", lambda **criteria: _SourceQuery())
    monkeypatch.setattr(JobManager, "source_progress_jobs", lambda self, *args, **kwargs: _async_jobs(job))
    monkeypatch.setattr(sources, "perms_can", lambda request, resource, action: True)

    async def _summary(source_id, job):
        return None

    monkeypatch.setattr(sources, "_run_llm_summary", _summary)

    response = await sources.source_analysis_progress(_request(memberships=[_membership()]), 9)

    body = response.body.decode()
    assert "Диагностика" in body
    # Unknown journal facts stay "—", never invented zeros.
    assert "zai-org" not in body
    assert ">—<" in body.replace("\n", "").replace(" ", "") or "—" in body


def test_run_llm_summary_needs_a_run_window():
    # A job with no timestamps resolves no window and reads no journal —
    # diagnostics must not depend on (=fail with) a database connection here.
    import asyncio

    job = SimpleNamespace(status="done", result={}, error=None, attempts=1)
    assert asyncio.run(sources._run_llm_summary(9, job)) is None


def test_source_run_diagnostics_falls_back_to_the_runs_per_source_error_code(monkeypatch):
    # A run whose dispatch died before any journal row (River transport refusal,
    # job 1757) has no llm_attempts to read; the run's own bounded per-source
    # code must surface instead of a bare "—".
    view = {"selected": 17, "remaining": 17, "error_code": "failed_transport"}
    merged = sources._source_run_diagnostics(view, None)
    assert merged["error_code"] == "failed_transport"
    # A journal outcome wins over the per-source snapshot: the error code must
    # not survive a successful tail attempt.
    journal = {"error_code": "failed_output", "model": "m", "succeeded": 2, "failed": 1}
    merged = sources._source_run_diagnostics(view, journal)
    assert merged["error_code"] == "failed_output" and merged["succeeded"] == 2
    # Non-identifier junk from either side stays unknown, never rendered.
    merged = sources._source_run_diagnostics({"error_code": "not an identifier!"}, None)
    assert merged["error_code"] is None


def test_source_run_diagnostics_keeps_unknowns_unknown():
    view = {"selected": 4, "remaining": 9}
    merged = sources._source_run_diagnostics(view, None)
    assert merged["selected"] == 4 and merged["remaining"] == 9
    assert merged["model"] is None and merged["succeeded"] is None and merged["failed"] is None
    merged = sources._source_run_diagnostics({}, None)
    assert merged["selected"] is None and merged["model"] is None


async def _async_jobs(job):
    return [job]
