"""DB-exempt coverage for canonical admission and scheduler wiring."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import task_run_admission, task_run_triggers

__source_only__ = True


def test_scheduler_uses_the_shared_admission_service():
    assert task_run_triggers.task_run_service is task_run_admission.task_run_service


@pytest.mark.asyncio
@pytest.mark.parametrize("same_intent", [True, False])
async def test_manual_replay_or_conflict_without_creating_another_run(monkeypatch, same_intent):
    task_id, actor_id, revision, key = 41, 19, 2, "mock-request"
    task = SimpleNamespace(id=task_id, tenant_id=7)
    existing = SimpleNamespace(
        id=51,
        task_id=task_id,
        revision=revision,
        job_id=81,
        status="pending",
        result=None,
        fingerprint=task_run_admission._digest(task_id, revision if same_intent else revision + 1),
    )
    session = SimpleNamespace(scalar=AsyncMock(return_value=existing))
    monkeypatch.setattr(task_run_admission, "authorize_task_run", AsyncMock(return_value=task))
    monkeypatch.setattr(task_run_admission, "get_current_user", lambda: SimpleNamespace(id=actor_id))
    insert = AsyncMock()
    monkeypatch.setattr(task_run_admission, "_insert_run", insert)

    if same_intent:
        receipt = await task_run_admission.task_run_service.admit_manual(
            task_id, expected_revision=revision, request_key=key, session=session
        )
        assert receipt.replayed and receipt.run_id == existing.id and receipt.job_id == existing.job_id
    else:
        with pytest.raises(task_run_admission.TaskRunAdmissionError, match="request_key_conflict"):
            await task_run_admission.task_run_service.admit_manual(
                task_id, expected_revision=revision, request_key=key, session=session
            )
    insert.assert_not_awaited()
    session.scalar.assert_awaited_once()


def test_scheduler_next_occurrence_is_aware_and_later():
    now = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
    due = task_run_triggers._next_occurrence("0 9 * * *", "Europe/Kirov", now)
    assert due.utcoffset() is not None and due > now


def test_deactivate_clears_cursor():
    trigger = SimpleNamespace(enabled=True, next_due_at=datetime.now(timezone.utc))
    task_run_triggers._deactivate(trigger)
    assert trigger.enabled is False and trigger.next_due_at is None


@pytest.mark.asyncio
async def test_active_run_creates_skip_receipt_without_enqueuing(monkeypatch):
    from unittest.mock import MagicMock

    from app.services import task_run_worker

    task = SimpleNamespace(id=41, tenant_id=7)
    plan = SimpleNamespace(steps=[], model_dump=lambda **_: {"steps": []})
    admitted = SimpleNamespace(plan=plan, methodology_json="{}", resources=[], reference=SimpleNamespace(revision=2))
    session = SimpleNamespace(scalar=AsyncMock(return_value=51), add=MagicMock(), flush=AsyncMock())
    monkeypatch.setattr(task_run_worker, "validate_runtime_plan", lambda plan: None)
    monkeypatch.setattr(task_run_admission, "encrypt_pii", lambda plaintext: "encrypted-mock")

    receipt = await task_run_admission._insert_run(
        session,
        task=task,
        admitted=admitted,
        principal_id=19,
        effective_at=datetime(2026, 11, 21, tzinfo=timezone.utc),
        fingerprint="a" * 64,
        manual_actor_id=19,
        request_key="b" * 64,
    )
    assert receipt.status == "skipped" and receipt.job_id is None
    assert receipt.result == {"reason": "active_run"}
    session.add.assert_called_once()
    assert session.add.call_args.args[0].status == "skipped"


@pytest.mark.asyncio
async def test_revoked_binding_stops_before_step_execution(monkeypatch):
    from app.integrations.task_resources import ResolutionIssue
    from app.services import task_run_worker

    resolver = AsyncMock(return_value=ResolutionIssue.REAUTH)
    monkeypatch.setattr(task_run_worker, "resolve_task_resource", resolver)
    binding = SimpleNamespace(name="inbox", capability="mailru_mail.headers.read", selection=lambda: object())
    plan = SimpleNamespace(bindings=[binding])
    run = SimpleNamespace(tenant_id=7, principal_id=19)
    step = SimpleNamespace(bindings=["inbox"])
    with pytest.raises(task_run_worker.TaskRunStopped, match="reauth_required"):
        await task_run_worker._resolve_bindings(object(), run, plan, step)
    resolver.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_worker_claim_does_not_lock_or_modify_run(monkeypatch):
    from app.jobs.claim_outcomes import JobClaimLostError
    from app.services import task_run_worker

    claim = SimpleNamespace(
        job_type="task.execute",
        tenant_id=7,
        job_id=1,
        agent_task_id=None,
        attempts=1,
        started_at=datetime(2026, 11, 21, tzinfo=timezone.utc),
    )
    session = SimpleNamespace(scalar=AsyncMock(return_value=None))
    lock_run = AsyncMock()
    monkeypatch.setattr(task_run_worker, "current_tenant_id", lambda: 7)
    monkeypatch.setattr(task_run_worker, "is_bypass", lambda: False)
    monkeypatch.setattr(task_run_worker, "lock_run", lock_run)
    with pytest.raises(JobClaimLostError):
        await task_run_worker._locked_run(session, claim)
    lock_run.assert_not_awaited()


def test_result_projection_excludes_provider_bodies_and_unbounded_fields():
    raw = {
        "coverage": "partial",
        "code": "provider secret",
        "provider_body": "private message",
        "steps": {
            "read": {"status": "done", "count": 7, "headers": "private"},
            "group": {"status": "unavailable", "count": -1},
            "invalid name": {"status": "done", "count": 1},
        },
    }
    assert task_run_admission._permitted_result(raw) == {
        "coverage": "partial",
        "code": "task_execution_refused",
        "steps": {"read": {"status": "done", "count": 7}, "group": {"status": "unavailable"}},
    }


def test_result_projection_rejects_malformed_fields():
    assert task_run_admission._permitted_result({"coverage": [], "steps": {"read": {"status": {}}}}) is None


@pytest.mark.asyncio
async def test_paid_operation_is_rejected_before_run_or_job_creation():
    from unittest.mock import MagicMock

    session = SimpleNamespace(add=MagicMock(), flush=AsyncMock())
    admitted = SimpleNamespace(plan=SimpleNamespace(steps=[SimpleNamespace(operation="social.analyze")]))
    with pytest.raises(task_run_admission.TaskRunAdmissionError, match="payer_unavailable"):
        await task_run_admission._insert_run(
            session,
            task=SimpleNamespace(id=1, tenant_id=1),
            admitted=admitted,
            principal_id=1,
            effective_at=datetime.now(timezone.utc),
            fingerprint="0" * 64,
        )
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.parametrize("operation", ["messages.store_groups", "social.collect", "social.cached"])
def test_unsupported_provider_effects_fail_closed(operation):
    from app.services.execution_plan_contracts import ExecutionPlanError
    from app.services.task_run_worker import validate_runtime_plan

    plan = SimpleNamespace(steps=[SimpleNamespace(operation=operation)], legacy_effects=None)
    with pytest.raises(ExecutionPlanError, match="unsupported_execution_operation"):
        validate_runtime_plan(plan)
