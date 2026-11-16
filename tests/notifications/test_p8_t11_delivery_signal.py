"""Pure projection contracts: no application bootstrap, DB, providers or writes."""

import ast
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest

from app.core.access_catalog import workspace_grants
from app.jobs.attempt_budget import ATTEMPT_BUDGET_STOP_PREFIX
from app.jobs.recovery_policy import CANCELLED_BEFORE_START_ERROR, OPERATOR_CANCELLED_ERROR, OUTCOME_UNCONFIRMED_PREFIX
from app.services.digest.checkpoints import new_checkpoint, transition
from app.services.notifications import delivery_signal as ds
from app.types import ActionType

__source_only__ = True
NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)


def job(**values):
    return Row(
        **(
            dict(
                id=1,
                tenant_id=7,
                job_type="digest",
                status="failed",
                error="private-error",
                attempts=1,
                max_attempts=3,
                created_at=NOW,
                initiated_by_user_id=101,
                agent_task_id=42,
                payload={"chat_id": "private-destination"},
            )
            | values
        )
    )


def run(**values):
    return Row(
        **(
            dict(
                id=1,
                tenant_id=7,
                status="failed",
                error="private-error",
                delivery_state=None,
                created_at=NOW,
                period_start=date(2026, 10, 25),
                period_end=date(2026, 10, 26),
                chat_id="private-chat",
                content="private-content",
            )
            | values
        )
    )


@pytest.mark.parametrize(
    "values, expected",
    [
        ({}, "undelivered"),
        ({"error": None}, "undelivered"),
        ({"status": "pending"}, "retrying"),
        ({"status": "pending", "attempts": 0}, None),
        ({"status": "pending", "error": None}, None),
        ({"status": "pending", "error": ""}, None),
        ({"status": "pending", "attempts": True}, None),
        ({"status": "done"}, None),
        ({"status": "running"}, None),
        ({"job_type": "collect"}, None),
        ({"error": OPERATOR_CANCELLED_ERROR}, None),
        ({"error": CANCELLED_BEFORE_START_ERROR}, None),
        ({"error": OUTCOME_UNCONFIRMED_PREFIX + "private"}, "quarantined"),
        ({"error": ATTEMPT_BUDGET_STOP_PREFIX + "private"}, "unconfirmed"),
    ],
)
def test_job_buckets(values, expected):
    assert ds.job_bucket(job(**values)) == expected


@pytest.mark.parametrize(
    "status, expected",
    [
        ("sent", None),
        ("skipped", None),
        ("pending", None),
        ("failed", "undelivered"),
        ("unrecognized", "unconfirmed"),
    ],
)
def test_legacy_run_buckets(status, expected):
    assert ds.run_bucket(run(status=status)) == expected


@pytest.mark.parametrize(
    "state, expected",
    [
        ("pending", None),
        ("sent", None),
        ("in_flight", "unconfirmed"),
        ("uncertain", "unconfirmed"),
        ("blocked", "undelivered"),
        ("rejected", "undelivered"),
    ],
)
def test_ledger_and_home_classification_parity(state, expected):
    checkpoint = new_checkpoint(
        run_id=1,
        tenant_id=7,
        generation="fixture",
        content="private-content",
        splitter="fixture",
        targets=[dict(binding_id=1, channel="telegram", destination_id="private-chat", parts=["private-content"])],
    )
    if state != "pending":
        checkpoint = transition(checkpoint, 0, 0, "in_flight")
        if state != "in_flight":
            checkpoint = transition(checkpoint, 0, 0, state, **({"message_id": "private"} if state == "sent" else {}))
    row = run(status="pending" if state == "pending" else "failed", error="", delivery_state=checkpoint)
    tree = ast.parse((Path(__file__).resolve().parents[2] / "app/web/dashboard.py").read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_digest_delivery_status")
    fn.decorator_list = []
    module = ast.Module(body=[fn], type_ignores=[])
    scope = {}
    exec(compile(ast.fix_missing_locations(module), "home delivery parity", "exec"), scope)
    for candidate in (
        row,
        run(),
        run(status="sent"),
        run(delivery_state={"broken": True}),
        run(id=2, delivery_state=checkpoint),
        run(tenant_id=8, delivery_state=checkpoint),
        run(error="partial_delivery_requires_review"),
    ):
        assert ds.digest_run_state(candidate) == scope["_digest_delivery_status"](candidate)
    assert ds.run_bucket(row) == expected


@pytest.mark.parametrize(
    "role, expected",
    [
        ("owner", (True, "workspace")),
        ("admin", (True, "workspace")),
        ("manager", (False, "initiated")),
        ("viewer", (False, "initiated")),
        ("analyst", (False, "initiated")),
    ],
)
def test_recipient_policy_uses_fresh_digest_grant(role, expected):
    scope = ds.delivery_scope(7, 101, role, workspace_grants(role))
    assert scope is not None and (scope.runs, scope.jobs) == expected
    assert ds.delivery_scope(7, 101, role, set()) is None
    assert ds.delivery_scope(7, 101, role, {("job", ActionType.VIEW)}) is None


def test_owner_needs_job_view_for_workspace_queue_and_ids_fail_closed():
    assert ds.delivery_scope(7, 101, "owner", {("digestrun", ActionType.VIEW)}).jobs is None
    for ws, uid, role in ((0, 101, "owner"), (True, 101, "owner"), (7, 0, "owner"), (7, True, "owner"), (7, 101, None)):
        assert ds.delivery_scope(ws, uid, role, workspace_grants("owner")) is None


@pytest.mark.asyncio
async def test_projection_redacts_data_checks_tenants_and_queries_only_required_scope():
    repo = Row(
        runs=AsyncMock(return_value=[run(), run(id=2, tenant_id=8)]),
        jobs=AsyncMock(return_value=[job(), job(id=2, initiated_by_user_id=102), job(id=3, tenant_id=8)]),
    )
    scoped = ds.DeliveryScope(7, 101, False, "initiated")
    signal = await ds.workspace_signal(scoped, repo, NOW)
    repo.runs.assert_not_awaited()
    repo.jobs.assert_awaited_once_with(7, NOW - timedelta(days=7), initiated_by=101)
    assert signal.total == 1 and signal.scope == "initiated" and [j["id"] for j in signal.jobs] == [1]
    assert signal.runs == []
    text = json.dumps(ds.bell_item(101, signal)) + json.dumps(signal.jobs)
    for secret in ("private-error", "private-content", "private-chat", "private-destination", "payload", "chat_id"):
        assert secret not in text
    owner = await ds.workspace_signal(ds.DeliveryScope(7, 101, True, "workspace"), repo, NOW)
    assert owner.total == 3 and len(owner.runs) == 1 and len(owner.jobs) == 2
    # Failed run and Job are separate evidence records, not unique deliveries.
    assert owner.counts["undelivered"] == 3


@pytest.mark.asyncio
async def test_bounded_counts_samples_and_deterministic_bell_identity():
    repo = Row(
        runs=AsyncMock(return_value=[run(id=i) for i in range(ds.ROW_LIMIT + 1)]), jobs=AsyncMock(return_value=[])
    )
    signal = await ds.workspace_signal(ds.DeliveryScope(7, 101, True, None), repo, NOW)
    assert signal.total == ds.ROW_LIMIT and len(signal.runs) == ds.SAMPLE_LIMIT and signal.truncated
    assert not signal.jobs_included
    repo.jobs.assert_not_awaited()
    assert "Показано не всё окно" in signal.summary()
    item = ds.bell_item(101, signal)
    assert item["destination"] == "/app/notifications/delivery/7"
    assert item["read_id"] is None and item["action"] == "open" and item["scope"] == "personal"
    assert item["key"] == ds.bell_item(101, signal)["key"]
    assert len(signal.fingerprint()) == 16
    signal.counts["retrying"] += 1
    assert item["key"] != ds.bell_item(101, signal)["key"]


@pytest.mark.asyncio
async def test_personal_signal_and_navigation_fail_closed_without_operator_bypass():
    repo = Row(
        memberships=AsyncMock(return_value=[(7, "owner"), (8, "manager"), (9, "unknown")]),
        runs=AsyncMock(return_value=[run()]),
        jobs=AsyncMock(return_value=[]),
    )
    user = Row(id=101, is_active=True)
    signals = await ds.personal_signals(user, repo, NOW)
    assert [s.workspace_id for s in signals] == [7]
    assert await ds.navigation_allowed(user, 8, repo)
    assert not await ds.navigation_allowed(user, 9, repo)
    assert not await ds.navigation_allowed(user, 10, repo)
    user.is_active = False
    assert not await ds.personal_signals(user, repo, NOW)
    assert not await ds.navigation_allowed(user, 7, repo)
    assert not await ds.personal_signals(None, repo, NOW)
