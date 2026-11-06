"""Closed capability registry for business write operations (P13-T04).

Every external write in P14-P19 declares its versioned capability here.
All capabilities default to `enabled=False` and respect a per-capability
kill switch so no external write occurs without explicit activation and
an approved, unexpired, hash-bound command.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from app.types import ActionType

DEFAULT_TTL_SECONDS = 15 * 60
MAX_SCHEDULED_TTL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class CapabilitySpec:
    code: str
    version: int
    domain: str
    risk_class: str  # "medium" | "high" | "critical"
    required_resource: str
    required_action: ActionType
    approval_policy: str  # "per_item" | "per_batch" | "connection_consent"
    kill_switch_key: str
    enabled_by_default: bool = False
    audit_create: str = "commands.create"
    audit_approve: str = "commands.approve"
    audit_execute: str = "commands.execute"


def _build_registry() -> dict[str, CapabilitySpec]:
    specs = (
        CapabilitySpec(
            code="crm.greeting_send.v1",
            version=1,
            domain="crm",
            risk_class="medium",
            required_resource="contact",
            required_action=ActionType.UPDATE,
            approval_policy="per_item",
            kill_switch_key="KILL_CRM_GREETING_SEND",
        ),
        CapabilitySpec(
            code="comms.batch_campaign.v1",
            version=1,
            domain="comms",
            risk_class="high",
            required_resource="botaction",
            required_action=ActionType.CREATE,
            approval_policy="per_batch",
            kill_switch_key="KILL_COMMS_BATCH_CAMPAIGN",
            audit_create="comms.campaign_create",
            audit_approve="comms.campaign_approve",
            audit_execute="comms.campaign_send",
        ),
        CapabilitySpec(
            code="calendar.event_write.v1",
            version=1,
            domain="calendar",
            risk_class="medium",
            required_resource="botaction",
            required_action=ActionType.CREATE,
            approval_policy="connection_consent",
            kill_switch_key="KILL_CALENDAR_EVENT_WRITE",
            audit_create="calendar.event_create",
            audit_approve="commands.approve",
            audit_execute="calendar.event_update",
        ),
        CapabilitySpec(
            code="onec.document_write.v1",
            version=1,
            domain="onec",
            risk_class="critical",
            required_resource="botaction",
            required_action=ActionType.CREATE,
            approval_policy="per_item",
            kill_switch_key="KILL_ONEC_DOCUMENT_WRITE",
            audit_create="commands.create",
            audit_approve="onec.command_approve",
            audit_execute="onec.command_execute",
        ),
        CapabilitySpec(
            code="documents.delivery_send.v1",
            version=1,
            domain="documents",
            risk_class="high",
            required_resource="botaction",
            required_action=ActionType.CREATE,
            approval_policy="per_item",
            kill_switch_key="KILL_DOCUMENTS_DELIVERY_SEND",
            audit_create="documents.create",
            audit_approve="documents.approve",
            audit_execute="documents.export",
        ),
    )
    # Transport commands require explicit D14 approval authority. These remain
    # default-off, independently of the sender's release/kill controls.
    specs += tuple(
        CapabilitySpec(
            code=code,
            version=1,
            domain="comms",
            risk_class="high",
            required_resource="outboxmessage",
            required_action=ActionType.CONFIGURE,
            approval_policy="per_item",
            kill_switch_key=kill,
        )
        # The operator switch is the per-transport pair the outbox already reads
        # (P15_*_SEND_ENABLED / P15_*_SEND_KILL); one name per capability.
        for code, kill in (
            ("comms.email.send.v1", "P15_EMAIL_SEND_KILL"),
            ("comms.telegram.bot_send.v1", "P15_TELEGRAM_BOT_SEND_KILL"),
            ("comms.max.bot_send.v1", "P15_MAX_BOT_SEND_KILL"),
        )
    )
    return {spec.code: spec for spec in specs}


CAPABILITY_REGISTRY = MappingProxyType(_build_registry())


def get_capability(code: str) -> CapabilitySpec:
    if not isinstance(code, str) or code not in CAPABILITY_REGISTRY:
        raise ValueError("Unsupported business command capability")
    return CAPABILITY_REGISTRY[code]


def canonical_payload_hash(capability: str, target_refs: list[str], payload: dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 hash binding capability, sorted targets and payload."""
    spec = get_capability(capability)
    if (
        not isinstance(target_refs, list)
        or not target_refs
        or any(not isinstance(t, str) or not t.strip() for t in target_refs)
    ):
        raise ValueError("Business commands require at least one non-empty target reference")
    if len(set(target_refs)) != len(target_refs):
        raise ValueError("Duplicate target references are not allowed in a single command")
    if not isinstance(payload, dict):
        raise ValueError("Business command payload must be a JSON object")
    envelope = {
        "capability": spec.code,
        "version": spec.version,
        "targets": list(target_refs),
        "payload": payload,
    }
    encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
