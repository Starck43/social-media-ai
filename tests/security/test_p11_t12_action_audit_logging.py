"""Source-only checks for the separate safe action-audit log paths."""

__source_only__ = True

import logging

import pytest

from app.services.action_audit import log_denied, log_rolled_back, log_unknown
from app.models.managers.action_audit_event_manager import ActionAuditEventManager


def test_denied_and_rollback_use_safe_distinct_outcomes(caplog):
    caplog.set_level(logging.WARNING, logger="app.action_audit.denied_rolled_back")
    log_denied(
        tenant_id=12,
        actor_kind="user",
        actor_id=34,
        task_id=56,
        reason_code="permission_denied",
    )
    log_rolled_back(
        tenant_id=12,
        actor_kind="user",
        actor_id=34,
        task_id=56,
    )

    messages = [record.getMessage() for record in caplog.records]
    assert any("action_audit_path=denied outcome=denied" in message for message in messages)
    assert any("action_audit_path=denied_rolled_back outcome=rolled_back" in message for message in messages)
    assert all("raw" not in message and "secret" not in message for message in messages)


def test_unknown_has_its_own_logger_category(caplog):
    caplog.set_level(logging.ERROR, logger="app.action_audit.unknown")
    log_unknown(
        tenant_id=12,
        actor_kind="scheduler",
        actor_id="scheduler",
        task_id=56,
        job_id=78,
        reason_code="commit_ack_unknown",
    )

    record = next(record for record in caplog.records if record.name == "app.action_audit.unknown")
    assert "action_audit_path=unknown outcome=unknown" in record.getMessage()
    assert "job_id=78" in record.getMessage()
    assert "payment" not in record.getMessage()


def test_unknown_persistence_gap_is_explicit_and_safe(caplog):
    from app.services.action_audit import log_persistence_gap

    caplog.set_level(logging.CRITICAL, logger="app.action_audit.unknown")
    log_persistence_gap(
        tenant_id=12,
        actor_kind="user",
        actor_id=34,
        task_id=56,
    )
    message = caplog.records[-1].getMessage()
    assert "action_audit_path=unknown_persistence_gap outcome=unknown persistence=failed" in message
    assert "audit_write_failed" in message
    assert "secret" not in message and "raw" not in message


def test_raw_string_resource_identity_is_never_logged(caplog):
    log_denied(
        tenant_id=12,
        actor_kind="user",
        actor_id=34,
        task_id="secret-token-value",
        reason_code="permission_denied",
    )
    assert not caplog.records


def test_action_identifier_matches_documented_domain_operation_contract():
    fields = ActionAuditEventManager._validate(
        actor_kind="user",
        actor_id=12,
        action="scheduling.task_admit",
        outcome="succeeded",
        resource_kind="agent_task",
        resource_id=12,
        reason_code=None,
        request_id=None,
        job_id=13,
        receipt_ref=None,
    )
    assert fields["action"] == "scheduling.task_admit"


def test_persisted_event_manager_rejects_untrusted_resource_text():
    with pytest.raises(ValueError, match="resource identity"):
        ActionAuditEventManager._validate(
            actor_kind="user",
            actor_id=12,
            action="scheduling.task_admit",
            outcome="succeeded",
            resource_kind="agent_task",
            resource_id="secret-token",
            reason_code=None,
            request_id=None,
            job_id=None,
            receipt_ref=None,
        )
