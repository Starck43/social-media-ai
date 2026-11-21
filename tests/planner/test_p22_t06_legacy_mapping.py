"""Focused, database-free checks for the P22-T06 identity bridge."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.planner.legacy_migration import (
    AGENT_NAMESPACE,
    PLANNER_NAMESPACE,
    LegacyIdentityError,
    LegacyMappingConflict,
    _canonical_values_from_agent,
    _validate_reference,
    compatibility_writer,
    fingerprint,
)

__source_only__ = True


def _agent(**overrides):
    values = {
        "id": 7,
        "tenant_id": 11,
        "name": "Daily review",
        "launch_key": "launch-7",
        "cron_expr": "@once",
        "job_type": "analyze",
        "payload": {"source_ids": [4]},
        "is_active": False,
        "next_run_at": None,
        "last_run_at": None,
        "last_status": "queued",
        "agent_scenario_id": 3,
        "trigger_type": None,
        "trigger_config": None,
        "action_type": None,
        "dry_run": False,
        "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_legacy_namespaces_keep_colliding_numeric_ids_distinct():
    assert _validate_reference(11, PLANNER_NAMESPACE, 7).legacy_id == 7
    assert _validate_reference(11, AGENT_NAMESPACE, 7).legacy_id == 7
    assert fingerprint({"namespace": PLANNER_NAMESPACE, "id": 7}) != fingerprint(
        {"namespace": AGENT_NAMESPACE, "id": 7}
    )


@pytest.mark.parametrize(
    ("tenant_id", "namespace", "legacy_id"),
    [(0, "planner", 1), (1, "", 1), (1, "planner", 0), (1, " planner", 1)],
)
def test_legacy_reference_rejects_unsafe_keys(tenant_id, namespace, legacy_id):
    with pytest.raises(LegacyIdentityError):
        _validate_reference(tenant_id, namespace, legacy_id)


def test_standalone_agent_canonical_shape_preserves_legacy_identity():
    values = _canonical_values_from_agent(_agent(), owner_user_id=23)
    assert values["tenant_id"] == 11
    assert values["owner_user_id"] == 23
    assert values["series_template_encrypted"] == '{"legacy_namespace": "agent", "legacy_id": 7}'
    assert values["visibility"] == "shared"


@pytest.mark.asyncio
async def test_link_execution_rejects_self_reference_before_database_access():
    class Session:
        async def scalar(self, *_args, **_kwargs):
            raise AssertionError("self-links must be rejected before querying")

    with pytest.raises(LegacyIdentityError, match="task_link_self_reference"):
        await compatibility_writer.link_execution(
            Session(),
            tenant_id=11,
            from_task_id=4,
            to_task_id=4,
            legacy_id=7,
            details=None,
        )


def test_conflict_error_is_distinct_from_invalid_reference():
    assert issubclass(LegacyMappingConflict, LegacyIdentityError)
