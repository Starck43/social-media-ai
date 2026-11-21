"""The v2 canonical task handlers against an isolated PostgreSQL schema.

The handlers are called directly inside the workspace scope: FastAPI's
authentication and per-route permission dependencies are covered by
`test_p22_t07_work_tasks_unit.py`, while the service rights, versions, admission
and side-effect rules are exercised here for real.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import func, select

from app.api.v2 import tasks as api
from app.core.config import settings
from app.core.database import async_session_maker
from app.models.planner.task_run import TaskRun
from app.schemas.work_tasks import (
    DecisionConfirmation,
    DecisionRequest,
    DeleteConfirmation,
    DeleteScope,
    PlanDraft,
    WorkTaskCreate,
    WorkTaskUpdate,
    WorkTaskVersion,
)
from app.services.commands.task_decisions import task_decision_service as decisions
from app.services.planner.canonical_tasks import task_service
from app.services.planner.tasks import PlannerConflict
from app.services.task_execution_plans import execution_plan_service
from tests.jobs.test_p22_t03_execution_plan_db import _connection
from tests.shared.execution_plan_payloads import mail_plan
from tests.support.schema_guard import require_isolated_schema

pytest_plugins = ("tests.jobs.test_p22_t03_execution_plan_db",)
pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


def value(payload, key):
    """Handlers return service models, dataclasses or dicts; read all three."""
    return payload[key] if isinstance(payload, dict) else getattr(payload, key)


async def _run_count(task_id: int) -> int:
    async with async_session_maker() as session:
        return await session.scalar(select(func.count(TaskRun.id)).where(TaskRun.task_id == task_id))


async def test_preview_has_no_effects_and_run_replays_one_key(workspace):
    require_isolated_schema()
    connection_id, pin = await _connection(workspace)
    plan = mail_plan(connection_id, pin)
    plan["steps"] = plan["steps"][:2]
    with workspace.scope():
        task = await api.create_task(WorkTaskCreate(title="Synthetic API task"))
        assert value(task, "title") == "Synthetic API task" and value(task, "version") == 1

        preview = await api.preview_plan(PlanDraft(plan=plan), value(task, "id"))
        assert value(preview, "task_id") == value(task, "id") and value(preview, "scheduled") is False
        assert value(preview, "operations") == [step["operation"] for step in plan["steps"]]
        assert await _run_count(value(task, "id")) == 0

        published = await execution_plan_service.publish(value(task, "id"), value(task, "version"), json.dumps(plan))
        listed = await api.list_tasks(limit=50, before_id=None, status=None, view=None)
        assert [item.id for item in value(listed, "items")] == [value(task, "id")]

        actions = await api.task_actions(value(task, "id"))
        references = {value(action, "reference") for action in value(actions, "actions")}
        assert {"edit", "complete", "run", "delete"} <= references
        assert value(actions, "latest_run") is None and value(actions, "work_status") == "todo"

        receipt = await api.run_task(value(task, "id"), "api-key-1")
        replay = await api.run_task(value(task, "id"), "api-key-1")
        assert value(replay, "run_id") == value(receipt, "run_id") and value(replay, "replayed") is True
        assert value(receipt, "revision") == published.revision and value(receipt, "status") in {"pending", "skipped"}

        # The replay did not admit a second run: the plan is not editable while it is active.
        with pytest.raises(PlannerConflict):
            await execution_plan_service.publish(value(task, "id"), value(task, "version"), json.dumps(plan))

        receipts = await api.list_runs(value(task, "id"), 50, None)
        assert [value(item, "run_id") for item in value(receipts, "items")] == [value(receipt, "run_id")]
        assert value(receipts, "next_before_id") is None


async def test_edit_complete_and_confirmed_delete_stay_version_bound(workspace):
    require_isolated_schema()
    with workspace.scope():
        task = await api.create_task(WorkTaskCreate(title="Synthetic closing task"))
        with pytest.raises(HTTPException) as stale:
            await api.update_task(
                WorkTaskUpdate(expected_version=9, scope="occurrence", title="Renamed"), value(task, "id")
            )
        assert stale.value.status_code == 409

        edited = await api.update_task(
            WorkTaskUpdate(expected_version=value(task, "version"), scope="occurrence", title="Renamed"),
            value(task, "id"),
        )
        assert value(edited, "title") == "Renamed" and value(edited, "version") == value(task, "version") + 1

        with pytest.raises(HTTPException) as unknown:
            await api.get_task(10**9)
        assert unknown.value.status_code == 404

        completed = await api.complete_task(
            WorkTaskVersion(expected_version=value(edited, "version")), value(task, "id")
        )
        assert value(value(completed, "task"), "status") == "done" and value(completed, "next_task") is None

        preview = await api.preview_delete(DeleteScope(scope="occurrence"), value(task, "id"))
        assert value(value(preview, "effects"), "occurrences") == 1 and value(preview, "expires_in") > 0
        outcome = await api.confirm_delete(
            DeleteConfirmation(scope="occurrence", confirmation=value(preview, "confirmation")), value(task, "id")
        )
        assert value(value(outcome, "effects"), "occurrences") == 1
        with pytest.raises(HTTPException) as gone:
            await api.get_task(value(task, "id"))
        assert gone.value.status_code == 404


async def test_pending_decision_reaches_actions_and_closes_by_confirmation(workspace, monkeypatch):
    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    with workspace.scope():
        task = await api.create_task(WorkTaskCreate(title="Synthetic review task", visibility="workspace"))
        proposal = await decisions.propose(
            value(task, "id"),
            expected_version=value(task, "version"),
            reviewer_id=workspace.user.id,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            reason="Synthetic review",
        )
        actions = await api.task_actions(value(task, "id"))
        assert proposal in [value(item, "reference") for item in value(actions, "pending_decisions")]

        preview = await api.preview_decision(DecisionRequest(reference=proposal, outcome="accepted"), value(task, "id"))
        assert value(preview, "confirmation")
        outcome = await api.decide(
            DecisionConfirmation(confirmation=value(preview, "confirmation"), reason="Reviewed"), value(task, "id")
        )
        assert value(outcome, "outcome") == "accepted"
        assert value(await api.task_actions(value(task, "id")), "pending_decisions") == []
