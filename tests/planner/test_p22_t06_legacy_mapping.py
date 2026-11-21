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
    _proven_agent_owner,
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
        "rate_limit_per_hour": None,
        "cooldown_seconds": None,
        "launch_fingerprint": None,
        "launch_job_id": None,
        "last_error": None,
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


@pytest.mark.asyncio
async def test_delete_attached_agent_retires_only_the_agent_alias(monkeypatch):
    mapping = SimpleNamespace(
        task_id=91,
        details={"attached": True},
        resolution="mapped",
        resolution_reason=None,
    )

    class Session:
        async def scalar(self, *_args, **_kwargs):
            return mapping

        async def flush(self):
            return None

    monkeypatch.setattr(compatibility_writer, "_database_session", lambda _session: True)
    await compatibility_writer.delete_agent(Session(), _agent())

    assert mapping.task_id is None
    assert mapping.resolution == "unresolved"
    assert mapping.resolution_reason == "legacy_deleted"
    assert mapping.details["deleted"] is True


def test_conflict_error_is_distinct_from_invalid_reference():
    assert issubclass(LegacyMappingConflict, LegacyIdentityError)


@pytest.mark.asyncio
async def test_first_attachment_preserves_the_planner_title(monkeypatch):
    """An attached schedule must not rename the human work it joins."""
    task = SimpleNamespace(id=91, owner_user_id=23, title_encrypted="Human work", version=4)

    class Session:
        def __init__(self):
            self.calls = 0

        async def scalar(self, *_args, **_kwargs):
            self.calls += 1
            return task if self.calls == 1 else None

        async def flush(self):
            return None

        def add(self, _row):
            return None

    async def no_mapping(*_args, **_kwargs):
        return None

    async def no_alias(*_args, **_kwargs):
        return None

    async def source_ids(*_args, **_kwargs):
        return []

    async def updated_at(*_args, **_kwargs):
        return datetime(2026, 1, 1, tzinfo=timezone.utc)

    monkeypatch.setattr(compatibility_writer, "_database_session", lambda _session: True)
    monkeypatch.setattr(compatibility_writer, "_mapping", no_mapping)
    monkeypatch.setattr(compatibility_writer, "_alias", no_alias)
    monkeypatch.setattr(compatibility_writer, "_source_updated_at", updated_at)
    monkeypatch.setattr("app.services.planner.legacy_migration._agent_source_ids", source_ids)

    result = await compatibility_writer.agent(
        Session(), _agent(name="Schedule name"), owner_user_id=23, attach_to_task_id=91
    )

    assert result is task
    assert task.title_encrypted == "Human work"
    assert task.version == 4


@pytest.mark.asyncio
async def test_standalone_schedule_still_mirrors_its_title(monkeypatch):
    """The attachment guard must not freeze an independent schedule's title."""
    task = SimpleNamespace(id=92, owner_user_id=23, title_encrypted="Old schedule", version=2)
    mapping = SimpleNamespace(
        task_id=92,
        details={"attached": False},
        resolution="mapped",
        resolution_reason=None,
    )

    class Session:
        async def scalar(self, *_args, **_kwargs):
            return task

        async def flush(self):
            return None

        def add(self, _row):
            return None

    async def mapped(*_args, **_kwargs):
        return mapping

    async def no_alias(*_args, **_kwargs):
        return None

    async def source_ids(*_args, **_kwargs):
        return []

    async def updated_at(*_args, **_kwargs):
        return datetime(2026, 1, 1, tzinfo=timezone.utc)

    monkeypatch.setattr(compatibility_writer, "_database_session", lambda _session: True)
    monkeypatch.setattr(compatibility_writer, "_mapping", mapped)
    monkeypatch.setattr(compatibility_writer, "_alias", no_alias)
    monkeypatch.setattr(compatibility_writer, "_source_updated_at", updated_at)
    monkeypatch.setattr("app.services.planner.legacy_migration._agent_source_ids", source_ids)

    result = await compatibility_writer.agent(Session(), _agent(name="New schedule"), owner_user_id=23)

    assert result is task
    assert task.title_encrypted == "New schedule"
    assert task.version == 3


@pytest.mark.asyncio
async def test_backfill_does_not_promote_payer_or_job_initiator_to_owner():
    """Billing and job history are not ownership evidence for a schedule."""

    class Session:
        async def scalar(self, *_args, **_kwargs):
            raise AssertionError("ownership must not be inferred from billing or job history")

    assert await _proven_agent_owner(Session(), _agent(job_type="collect")) is None


@pytest.mark.asyncio
async def test_backfill_accepts_only_the_signed_brief_recipient(monkeypatch):
    from app.services.social import brief_schedule

    monkeypatch.setattr(brief_schedule, "schedule_for_task", lambda _row: SimpleNamespace(user_id=23))

    async def workspace_owner(_session, tenant_id, user_id):
        assert (tenant_id, user_id) == (11, 23)
        return 23

    monkeypatch.setattr("app.services.planner.legacy_migration._workspace_owner", workspace_owner)

    assert await _proven_agent_owner(object(), _agent(job_type="social.brief")) == 23
