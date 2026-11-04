"""Closed business audit registry; adding a code also requires a DB migration.

This module is dependency-free so model, manager and tests share one contract.
Never register request data or allow arbitrary namespace suffixes at runtime.
"""

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class AuditCode:
    resource_kind: str
    unknown_reason: str = "operation_unconfirmed"


def _registry() -> dict[str, AuditCode]:
    codes = {"scheduling.task_admit": AuditCode("agent_task", "launch_unconfirmed")}

    def register(namespace: str, resource: str, operations: tuple[str, ...]) -> None:
        for operation in operations:
            code = f"{namespace}.{operation}"
            if code in codes:
                raise ValueError("Duplicate audit code")
            codes[code] = AuditCode(resource)

    register("crm", "contact", ("contact_create", "contact_update", "contact_delete"))
    register("comms", "campaign", ("campaign_create", "campaign_approve", "campaign_send"))
    register("planner", "task", ("task_create", "task_update", "task_complete"))
    register("calendar", "event", ("event_create", "event_update", "event_delete"))
    register("onec", "command", ("command_approve", "command_execute"))
    register("files.connector", "local_connector", ("register", "revoke", "search", "fetch", "index_delete"))
    register("documents", "document", ("create", "approve", "export"))
    register("commands", "command", ("create", "approve", "execute"))
    # P14-T09: installation-level trusted outbound hosts; migration 0138.
    register("admin", "trusted_host", ("trusted_host_add", "trusted_host_remove"))
    return codes


AUDIT_CODES = MappingProxyType(_registry())
AUDIT_ACTIONS = frozenset(AUDIT_CODES)
AUDIT_UNKNOWN_REASONS = frozenset(spec.unknown_reason for spec in AUDIT_CODES.values())


def audit_check_sql() -> dict[str, str]:
    """Render SQL only from source-owned literals, never user input."""
    actions = ", ".join(f"'{code}'" for code in sorted(AUDIT_CODES))
    resources = " OR ".join(
        f"(action = '{code}' AND resource_kind = '{spec.resource_kind}')" for code, spec in sorted(AUDIT_CODES.items())
    )
    reasons = " OR ".join(
        f"(action = '{code}' AND reason_code = '{spec.unknown_reason}')" for code, spec in sorted(AUDIT_CODES.items())
    )
    return {
        "action": f"action IN ({actions})",
        # Explicit NOT NULL matters: PostgreSQL CHECK accepts SQL UNKNOWN.
        "resource_kind": "(action = 'scheduling.task_admit' AND resource_kind IS NULL) OR "
        f"(resource_kind IS NOT NULL AND ({resources}))",
        # Preserve all legacy CHECK-valid rows; new actions reject SQL NULL reasons.
        "reason_code": "(action = 'scheduling.task_admit' AND ((outcome = 'succeeded' AND reason_code IS NULL) OR "
        "(outcome = 'unknown' AND reason_code = 'launch_unconfirmed'))) OR "
        "(action <> 'scheduling.task_admit' AND ((outcome = 'succeeded' AND reason_code IS NULL) OR "
        f"(outcome = 'unknown' AND reason_code IS NOT NULL AND ({reasons}))))",
    }
