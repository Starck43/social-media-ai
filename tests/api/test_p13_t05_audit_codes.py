"""DB-exempt validation/DDL contracts, no sessions or DB connections."""

import runpy
from pathlib import Path

import pytest

from app.models.audit.action_audit_event import ActionAuditEvent
from app.models.managers.action_audit_event_manager import ActionAuditEventManager
from app.services.audit_codes import AUDIT_CODES, audit_check_sql

ROOT = Path(__file__).resolve().parents[2]


def fields(action="scheduling.task_admit", **updates):
    values = dict(
        actor_kind="user",
        actor_id=1,
        action=action,
        outcome="succeeded",
        resource_kind=AUDIT_CODES.get(action, AUDIT_CODES["scheduling.task_admit"]).resource_kind,
        resource_id=2,
        reason_code=None,
        request_id=None,
        job_id=None,
        receipt_ref=None,
    )
    return {**values, **updates}


@pytest.mark.parametrize("action", AUDIT_CODES)
def test_every_registered_action_accepts_only_safe_typed_envelope(action):
    assert ActionAuditEventManager._validate(**fields(action))["action"] == action
    assert (
        ActionAuditEventManager._validate(
            **fields(action, outcome="unknown", reason_code=AUDIT_CODES[action].unknown_reason)
        )["outcome"]
        == "unknown"
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"action": "files.connector.unregistered"},
        {"action": "crm.payload:secret"},
        {"resource_kind": "private-path"},
        {"resource_id": "/private/path"},
        {"actor_id": True},
        {"actor_kind": "user", "actor_id": 0},
        {"actor_kind": "service", "actor_id": "untrusted"},
        {"outcome": "denied"},
        {"reason_code": "private-provider-content"},
        {"outcome": "unknown", "reason_code": None},
        {"request_id": "private"},
        {"receipt_ref": "private"},
        {"job_id": False},
    ],
)
def test_untrusted_fields_are_rejected(updates):
    with pytest.raises(ValueError):
        ActionAuditEventManager._validate(**fields(**updates))


def test_model_matches_frozen_migration_and_never_widens_to_wildcards():
    migration = runpy.run_path(str(ROOT / "migrations/versions/0132_business_audit_codes.py"))
    assert migration["NEW_CHECKS"] == audit_check_sql()
    constraints = {c.name: str(c.sqltext) for c in ActionAuditEvent.__table__.constraints if hasattr(c, "sqltext")}
    for suffix, expression in audit_check_sql().items():
        assert constraints[f"ck_action_audit_events_{suffix}"] == expression
    assert "LIKE" not in migration["NEW_CHECKS"]["action"]
    assert migration["down_revision"] == "0131"


def test_registry_is_immutable():
    with pytest.raises(TypeError):
        AUDIT_CODES["untrusted.action"] = AUDIT_CODES["scheduling.task_admit"]


def test_migration_preserves_existing_rows_and_refuses_lossy_downgrade(monkeypatch):
    migration = runpy.run_path(str(ROOT / "migrations/versions/0132_business_audit_codes.py"))
    calls = []
    op = migration["op"]
    monkeypatch.setattr(op, "drop_constraint", lambda *a, **kw: calls.append(("drop", a, kw)))
    monkeypatch.setattr(op, "create_check_constraint", lambda *a, **kw: calls.append(("create", a, kw)))
    migration["upgrade"]()
    assert len(calls) == 6
    assert all(call[2]["schema"] for call in calls)
    from types import SimpleNamespace

    monkeypatch.setattr(
        op, "get_bind", lambda: SimpleNamespace(execute=lambda query: SimpleNamespace(first=lambda: (1,)))
    )
    calls.clear()
    with pytest.raises(ValueError, match="expired_events"):
        migration["downgrade"]()
    assert not calls
    monkeypatch.setattr(
        op, "get_bind", lambda: SimpleNamespace(execute=lambda query: SimpleNamespace(first=lambda: None))
    )
    migration["downgrade"]()
    assert len(calls) == 6


def test_migration_keeps_legacy_check_valid_nullable_rows():
    checks = audit_check_sql()
    assert "(action = 'scheduling.task_admit' AND resource_kind IS NULL)" in checks["resource_kind"]
    assert (
        "action = 'scheduling.task_admit' AND ((outcome = 'succeeded' AND reason_code IS NULL) OR "
        in checks["reason_code"]
    )
    assert "(outcome = 'unknown' AND reason_code = 'launch_unconfirmed')" in checks["reason_code"]
