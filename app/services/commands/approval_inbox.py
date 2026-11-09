"""Unified approval inbox projection and single-item review router (P20-T03).

Provides one permission-filtered, read-time review projection over the P13-T04
business command ledger (`BusinessCommand` / `command_ledger`) without creating
a second approval authority, persistent `always allow` policy, bulk approval,
or automatic execution on approve.

Boundaries enforced in this increment:
- Only P13-T04 `BusinessCommand` rows are projected (`PROJECTED_SOURCES`).
- Outbox (`P15-T05`), document (`P19-T04`), calendar (`P16-T03`), 1C (`P17-T04`),
  and connected task (`P20-T11`) projections remain disabled until their
  respective owners land and their contracts are wired.
- Social task preview/run remains on its accepted plan path and is never
  represented as a ledger command.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from sqlalchemy import select

from app.core.access_catalog import workspace_grants
from app.core.permissions import get_current_user
from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass
from app.models.actions.business_command import BusinessCommand
from app.models.identity.user import User
from app.models.tenancy.tenant import Tenant
from app.models.tenancy.tenant_user import TenantUser
from app.models.tenancy.workspace_role import WorkspaceRole
from app.services.commands.ledger import (
    AdapterCallable,
    BusinessCommandLedger,
    CommandConflict,
    CommandDenied,
    CommandNotFound,
    command_ledger,
)
from app.services.commands.registry import (
    CAPABILITY_REGISTRY,
    DEFAULT_TTL_SECONDS,
    CapabilitySpec,
    canonical_payload_hash,
    get_capability,
)
from app.types import ActionType

PROJECTED_SOURCES: tuple[str, ...] = ("p13_t04_command_ledger",)
DISABLED_PROJECTION_SOURCES: tuple[str, ...] = (
    "p15_t05_outbox",
    "p19_t04_documents",
    "p16_t03_calendar",
    "p17_t04_onec",
    "p20_t11_connected_tasks",
)
OUTBOX_PROJECTION_ENABLED: bool = False
DOCUMENT_PROJECTION_ENABLED: bool = False

INBOX_STATUSES: tuple[str, ...] = (
    "pending",
    "approved",
    "expired",
    "rejected",
    "executed",
    "failed",
    "unknown",
)

STATUS_LABELS_RU: dict[str, str] = {
    "pending": "Ожидает согласования",
    "approved": "Согласовано (ждёт выполнения)",
    "expired": "Истёк срок согласования",
    "rejected": "Отклонено",
    "executed": "Выполнено",
    "failed": "Ошибка выполнения",
    "unknown": "Статус неизвестен (требует сверки)",
}

CAPABILITY_LABELS_RU: dict[str, str] = {
    "crm.greeting_send.v1": "Отправка CRM-поздравления",
    "comms.batch_campaign.v1": "Пакетная кампания сообщений",
    "calendar.event_write.v1": "Запись события календаря",
    "onec.document_write.v1": "Запись документа 1С",
    "documents.delivery_send.v1": "Отправка документа",
    "comms.email.send.v1": "Отправка Email-сообщения",
    "comms.telegram.bot_send.v1": "Отправка сообщения Telegram-ботом",
    "comms.max.bot_send.v1": "Отправка сообщения MAX-ботом",
}

RISK_LABELS_RU: dict[str, str] = {
    "medium": "средний",
    "high": "высокий",
    "critical": "критический",
}

POLICY_LABELS_RU: dict[str, str] = {
    "per_item": "по каждой записи",
    "per_batch": "на пакет",
    "connection_consent": "по согласию подключения",
}

_SENSITIVE_KEY_SUBSTRINGS: tuple[str, ...] = (
    "secret",
    "token",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "private_key",
    "client_secret",
)

_TARGET_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "phone",
        "email",
        "recipient",
        "recipients",
        "address",
        "body",
        "text",
        "draft_text",
        "message",
        "content",
        "contact_name",
        "full_name",
    }
)

_COMMAND_LOCKS: dict[tuple[int, int], asyncio.Lock] = {}


def _get_command_lock(tenant_id: int, command_id: int) -> asyncio.Lock:
    key = (int(tenant_id), int(command_id))
    lock = _COMMAND_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _COMMAND_LOCKS[key] = lock
    return lock


def _require_inbox_tenant() -> int:
    tenant_id = current_tenant_id()
    if is_bypass() or type(tenant_id) is not int or tenant_id <= 0:
        raise TenantContextError("Approval inbox requires an explicit workspace scope")
    return tenant_id


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_or_none(value: datetime | str | None) -> str | None:
    utc_val = _as_utc(value)
    return utc_val.isoformat() if utc_val is not None else None


def _get_field(cmd: BusinessCommand | Mapping[str, Any], name: str, default: Any = None) -> Any:
    if isinstance(cmd, Mapping):
        return cmd.get(name, default)
    return getattr(cmd, name, default)


def _perms_allows(perms: Any, resource: str, action: ActionType | str) -> bool:
    """Evaluate WebPerms or RBAC-like permission objects; fail closed when provided."""
    if perms is None:
        return True
    if getattr(perms, "is_owner", False) or getattr(perms, "is_superuser_role", False):
        return True
    can_fn = getattr(perms, "can", None)
    if callable(can_fn):
        try:
            return bool(can_fn(resource, action))
        except Exception:
            return False
    return False


def _can_view_inbox(perms: Any) -> bool:
    if perms is None:
        return True
    return (
        _perms_allows(perms, "botaction", ActionType.VIEW)
        or _perms_allows(perms, "botaction", ActionType.UPDATE)
        or _perms_allows(perms, "botaction", ActionType.CREATE)
    )


def _can_view_capability(perms: Any, spec: CapabilitySpec, session_grants: frozenset[tuple[str, ActionType]] | None = None) -> bool:
    if session_grants is not None:
        if (
            (spec.required_resource, spec.required_action) not in session_grants
            and (spec.required_resource, ActionType.VIEW) not in session_grants
            and ("botaction", ActionType.VIEW) not in session_grants
        ):
            return False
    if perms is None:
        return True
    if not _can_view_inbox(perms):
        return False
    return (
        _perms_allows(perms, spec.required_resource, spec.required_action)
        or _perms_allows(perms, spec.required_resource, ActionType.VIEW)
    )


def _can_act_on_capability(perms: Any, spec: CapabilitySpec, session_grants: frozenset[tuple[str, ActionType]] | None = None) -> bool:
    if session_grants is not None:
        if (spec.required_resource, spec.required_action) not in session_grants:
            return False
    if perms is None:
        return True
    if not (
        _perms_allows(perms, "botaction", ActionType.UPDATE)
        or _perms_allows(perms, "botaction", ActionType.CREATE)
    ):
        return False
    return _perms_allows(perms, spec.required_resource, spec.required_action)


async def _load_actor_session_grants(session: Any, tenant_id: int) -> frozenset[tuple[str, ActionType]] | None:
    """Resolve the active actor's workspace role grants from `session` when available."""
    if session is None:
        return None
    actor = get_current_user()
    if actor is None or not getattr(actor, "is_active", False) or type(getattr(actor, "id", None)) is not int:
        return frozenset()
    actor_id = int(actor.id)
    user_ok = (
        await session.execute(select(User.id).where(User.id == actor_id, User.is_active.is_(True)))
    ).scalar_one_or_none()
    tenant_ok = (
        await session.execute(select(Tenant.id).where(Tenant.id == tenant_id, Tenant.is_active.is_(True)))
    ).scalar_one_or_none()
    member = (
        await session.execute(
            select(TenantUser).where(
                TenantUser.tenant_id == tenant_id,
                TenantUser.user_id == actor_id,
                TenantUser.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if user_ok is None or tenant_ok is None or member is None or member.role_id is None:
        return frozenset()
    role_code = (
        await session.execute(select(WorkspaceRole.codename).where(WorkspaceRole.id == member.role_id))
    ).scalar_one_or_none()
    return workspace_grants(role_code) if role_code is not None else frozenset()


def _can_read_target_details(
    perms: Any,
    spec: CapabilitySpec,
    target_refs: list[str],
    accessible_targets: set[str] | None,
    session_grants: frozenset[tuple[str, ActionType]] | None = None,
) -> tuple[bool, bool]:
    """Return `(target_accessible, can_read_details)` for the command's target_refs."""
    if accessible_targets is not None:
        all_accessible = all(ref in accessible_targets for ref in (target_refs or []))
        if not all_accessible:
            return False, False
    if session_grants is not None:
        if (
            (spec.required_resource, spec.required_action) not in session_grants
            and (spec.required_resource, ActionType.VIEW) not in session_grants
        ):
            return False, False
    if perms is None:
        return True, True
    can_read_resource = _perms_allows(perms, spec.required_resource, ActionType.VIEW) or _perms_allows(
        perms, spec.required_resource, spec.required_action
    )
    if not can_read_resource:
        return False, False
    for ref in target_refs or []:
        prefix = ref.split(":", 1)[0].strip().lower() if ":" in ref else ""
        if prefix in {"contact", "crm_contact", "deal"}:
            if not (
                _perms_allows(perms, "contact", ActionType.VIEW)
                or _perms_allows(perms, "contact", ActionType.UPDATE)
            ):
                return False, False
    return True, True


def _redact_mapping(data: Any, *, redact_target_fields: bool) -> Any:
    """Recursively redact secrets and, when required, target-specific PII fields."""
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for key, value in data.items():
            key_str = str(key)
            key_lower = key_str.lower()
            if any(token in key_lower for token in _SENSITIVE_KEY_SUBSTRINGS):
                out[key_str] = "[REDACTED]"
            elif redact_target_fields and key_lower in _TARGET_SENSITIVE_KEYS:
                out[key_str] = "[СКРЫТО ПО ПРАВАМ ДОСТУПА]"
            else:
                out[key_str] = _redact_mapping(value, redact_target_fields=redact_target_fields)
        return out
    if isinstance(data, list):
        if redact_target_fields:
            return ["[СКРЫТО ПО ПРАВАМ ДОСТУПА]" for _ in data]
        return [_redact_mapping(item, redact_target_fields=False) for item in data]
    return data


def _compute_is_stale(cmd: BusinessCommand | Mapping[str, Any]) -> bool:
    capability = str(_get_field(cmd, "capability") or "")
    target_refs = list(_get_field(cmd, "target_refs") or [])
    payload = _get_field(cmd, "payload")
    payload_hash = str(_get_field(cmd, "payload_hash") or "")
    approved_hash = _get_field(cmd, "approved_payload_hash")

    if approved_hash is not None and approved_hash != payload_hash:
        return True
    if isinstance(payload, dict) and capability in CAPABILITY_REGISTRY and target_refs:
        try:
            recomputed = canonical_payload_hash(capability, target_refs, payload)
            if recomputed != payload_hash:
                return True
            if approved_hash is not None and approved_hash != recomputed:
                return True
        except ValueError:
            return True
    return False


def _classify_status(cmd: BusinessCommand | Mapping[str, Any], *, now: datetime) -> tuple[str, bool]:
    """Map ledger state + TTL + payload hash binding to inbox status and stale flag."""
    raw_state = str(_get_field(cmd, "state") or "draft").lower()
    is_stale = _compute_is_stale(cmd)
    expires_at = _as_utc(_get_field(cmd, "approval_expires_at"))

    if raw_state in {"draft", "previewed"}:
        if expires_at is not None and expires_at <= now:
            return "expired", is_stale
        return "pending", is_stale
    if raw_state == "approved":
        if is_stale:
            return "expired", True
        if expires_at is None or expires_at <= now:
            return "expired", is_stale
        return "approved", False
    if raw_state == "expired":
        return "expired", is_stale
    if raw_state == "cancelled":
        return "rejected", is_stale
    if raw_state == "succeeded":
        return "executed", is_stale
    if raw_state == "failed":
        return "failed", is_stale
    if raw_state in {"unknown", "claimed"}:
        return "unknown", is_stale
    return "unknown", is_stale


def _build_preview_summary(
    capability: str,
    capability_version: int,
    payload_hash: str,
    target_refs: list[str],
    safe_preview: dict[str, Any],
    *,
    redacted: bool,
) -> str:
    if redacted:
        return (
            f"Содержимое скрыто: нет прав чтения целевого объекта "
            f"(версия v{capability_version}, хеш {payload_hash[:19]}…)"
        )
    for key in ("summary", "subject", "title", "preview", "text", "draft_text", "message"):
        val = safe_preview.get(key)
        if isinstance(val, str) and val.strip():
            text = val.strip()
            return text if len(text) <= 180 else f"{text[:177]}…"
    payload_preview = safe_preview.get("payload")
    if isinstance(payload_preview, dict):
        for key in ("summary", "subject", "title", "text", "draft_text", "message"):
            val = payload_preview.get(key)
            if isinstance(val, str) and val.strip():
                text = val.strip()
                return text if len(text) <= 180 else f"{text[:177]}…"
    return f"Команда {capability} v{capability_version} · целей: {len(target_refs)}"


def _build_reason_for_review(
    raw_state: str,
    approval_policy: str,
    payload_hash: str,
    spec: CapabilitySpec,
    *,
    is_stale: bool,
) -> str:
    risk_ru = RISK_LABELS_RU.get(spec.risk_class, spec.risk_class)
    policy_ru = POLICY_LABELS_RU.get(approval_policy, approval_policy)
    base = (
        f"Внешняя запись ({spec.domain}, риск: {risk_ru}, режим: {policy_ru}). "
        f"Требуется явное подтверждение хеша нагрузки {payload_hash[:19]}…; "
        f"согласование не запускает отправку автоматически."
    )
    if is_stale:
        return f"{base} Внимание: полезная нагрузка изменилась после согласования — требуется повторное согласование."
    if raw_state == "unknown":
        return (
            f"{base} Статус внешнего провайдера неизвестен — повторная автоматическая "
            f"отправка заблокирована до ручной сверки."
        )
    return base


def format_bot_decision_link(command_id: int, *, capability: str | None = None) -> dict[str, Any]:
    """Return the web decision link structure for bot notifications (no in-bot approve buttons)."""
    cid = int(command_id)
    label = CAPABILITY_LABELS_RU.get(capability or "", "Команда записи")
    url = f"/app/commands/{cid}"
    return {
        "command_id": cid,
        "url": url,
        "label": f"Открыть решение #{cid} в веб-интерфейсе",
        "message": (
            f"{label} #{cid} ожидает решения в веб-интерфейсе: {url} "
            f"(кнопки подтверждения в боте отключены по контракту P20-T03)."
        ),
        "in_bot_buttons_allowed": False,
    }


@dataclass(frozen=True)
class ApprovalInboxItem:
    item_id: str
    command_id: int
    tenant_id: int
    source_ledger: str
    capability: str
    capability_version: int
    type_label: str
    domain: str
    risk_class: str
    approval_policy: str
    requester_user_id: int
    requester_label: str
    approved_user_id: int | None
    connection_id: int | None
    source_label: str
    target_refs: list[str]
    target_count: int
    target_summary: str
    target_accessible: bool
    redacted: bool
    payload_hash: str
    approved_payload_hash: str | None
    is_stale_payload: bool
    safe_preview: dict[str, Any]
    preview_text: str
    reason_for_review: str
    raw_state: str
    status: str
    status_label: str
    actionable: bool
    can_approve: bool
    can_reject: bool
    can_execute: bool
    can_reconcile: bool
    approval_expires_at: str | None
    approved_at: str | None
    executed_at: str | None
    created_at: str | None
    error_code: str | None
    reconcile_hint: str | None
    outcome_summary: dict[str, Any] | None
    targets: list[dict[str, Any]]
    authoritative_url: str
    api_url: str
    bot_decision_link: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "id": self.command_id,
            "command_id": self.command_id,
            "tenant_id": self.tenant_id,
            "source_ledger": self.source_ledger,
            "capability": self.capability,
            "capability_version": self.capability_version,
            "type_label": self.type_label,
            "domain": self.domain,
            "risk_class": self.risk_class,
            "approval_policy": self.approval_policy,
            "requester_user_id": self.requester_user_id,
            "actor_user_id": self.requester_user_id,
            "requester_label": self.requester_label,
            "approved_user_id": self.approved_user_id,
            "connection_id": self.connection_id,
            "source_label": self.source_label,
            "target_refs": list(self.target_refs),
            "target_count": self.target_count,
            "target_summary": self.target_summary,
            "target_accessible": self.target_accessible,
            "redacted": self.redacted,
            "payload_hash": self.payload_hash,
            "approved_payload_hash": self.approved_payload_hash,
            "is_stale_payload": self.is_stale_payload,
            "safe_preview": dict(self.safe_preview),
            "preview_snapshot": dict(self.safe_preview),
            "preview_text": self.preview_text,
            "reason_for_review": self.reason_for_review,
            "raw_state": self.raw_state,
            "state": self.raw_state,
            "status": self.status,
            "status_label": self.status_label,
            "actionable": self.actionable,
            "can_approve": self.can_approve,
            "can_reject": self.can_reject,
            "can_execute": self.can_execute,
            "can_reconcile": self.can_reconcile,
            "approval_expires_at": self.approval_expires_at,
            "approved_at": self.approved_at,
            "executed_at": self.executed_at,
            "created_at": self.created_at,
            "error_code": self.error_code,
            "reconcile_hint": self.reconcile_hint,
            "outcome_summary": self.outcome_summary,
            "targets": list(self.targets),
            "authoritative_url": self.authoritative_url,
            "api_url": self.api_url,
            "bot_decision_link": dict(self.bot_decision_link),
        }


class ApprovalInboxService:
    """Permission-filtered read-time projection and single-item review router over `command_ledger`."""

    def __init__(self, ledger: BusinessCommandLedger | None = None) -> None:
        self._ledger = ledger or command_ledger

    def project_command(
        self,
        cmd: BusinessCommand | Mapping[str, Any],
        *,
        perms: Any = None,
        accessible_targets: set[str] | None = None,
        session_grants: frozenset[tuple[str, ActionType]] | None = None,
        now: datetime | None = None,
    ) -> ApprovalInboxItem | None:
        """Project a single `BusinessCommand` or serialized command dict for the active viewer."""
        tenant_id = _require_inbox_tenant()
        cmd_tenant_id = _get_field(cmd, "tenant_id")
        if cmd_tenant_id != tenant_id:
            return None
        capability = str(_get_field(cmd, "capability") or "")
        if capability not in CAPABILITY_REGISTRY:
            return None
        spec = get_capability(capability)
        if not _can_view_capability(perms, spec, session_grants=session_grants):
            return None

        cmd_id = int(_get_field(cmd, "id"))
        capability_version = int(_get_field(cmd, "capability_version") or 1)
        approval_policy = str(_get_field(cmd, "approval_policy") or spec.approval_policy)
        actor_user_id = int(_get_field(cmd, "actor_user_id"))
        approved_user_id_raw = _get_field(cmd, "approved_user_id")
        approved_user_id = int(approved_user_id_raw) if approved_user_id_raw is not None else None
        conn_id_raw = _get_field(cmd, "connection_id")
        connection_id = int(conn_id_raw) if conn_id_raw is not None else None
        raw_targets = [str(t) for t in (_get_field(cmd, "target_refs") or [])]
        payload_hash = str(_get_field(cmd, "payload_hash") or "")
        approved_payload_hash = _get_field(cmd, "approved_payload_hash")
        raw_state = str(_get_field(cmd, "state") or "draft")

        ref_now = _as_utc(now) or _utcnow()
        target_accessible, can_read_details = _can_read_target_details(
            perms,
            spec,
            raw_targets,
            accessible_targets,
            session_grants=session_grants,
        )
        redacted = not can_read_details
        can_act = _can_act_on_capability(perms, spec, session_grants=session_grants) and target_accessible

        status, is_stale = _classify_status(cmd, now=ref_now)
        status_label = STATUS_LABELS_RU.get(status, status)

        preview_snapshot = _get_field(cmd, "preview_snapshot")
        payload_dict = _get_field(cmd, "payload")
        raw_preview_source = (
            dict(preview_snapshot)
            if isinstance(preview_snapshot, Mapping) and preview_snapshot
            else {
                "capability": capability,
                "version": capability_version,
                "targets": list(raw_targets),
                "payload": dict(payload_dict) if isinstance(payload_dict, Mapping) else {},
            }
        )
        if isinstance(payload_dict, Mapping) and payload_dict and "payload" not in raw_preview_source:
            raw_preview_source["payload"] = dict(payload_dict)

        safe_preview = _redact_mapping(raw_preview_source, redact_target_fields=redacted)
        if not isinstance(safe_preview, dict):
            safe_preview = {}

        if redacted:
            visible_targets = [f"[скрыто:{idx + 1}]" for idx in range(len(raw_targets))]
            target_summary = f"Цели скрыты по правам доступа ({len(raw_targets)})"
        else:
            visible_targets = raw_targets
            target_summary = ", ".join(raw_targets[:3])
            if len(raw_targets) > 3:
                target_summary = f"{target_summary} (+{len(raw_targets) - 3})"

        source_label = (
            f"Журнал команд P13-T04 · подключение #{connection_id}"
            if connection_id is not None
            else "Журнал команд P13-T04 · рабочее пространство"
        )
        type_label = CAPABILITY_LABELS_RU.get(capability, f"Команда {capability}")
        preview_text = _build_preview_summary(
            capability,
            capability_version,
            payload_hash,
            raw_targets,
            safe_preview,
            redacted=redacted,
        )
        reason_for_review = _build_reason_for_review(
            raw_state,
            approval_policy,
            payload_hash,
            spec,
            is_stale=is_stale,
        )

        can_approve = bool(status == "pending" and can_act and not is_stale)
        can_reject = bool(status in {"pending", "approved"} and can_act)
        can_execute = bool(
            status == "approved"
            and can_act
            and not is_stale
            and approved_payload_hash == payload_hash
        )
        can_reconcile = bool(status == "unknown" and can_act)
        actionable = bool(can_approve or can_reject or can_reconcile)

        raw_target_rows = _get_field(cmd, "targets") or []
        projected_targets: list[dict[str, Any]] = []
        for idx, trow in enumerate(raw_target_rows):
            t_dict = dict(trow) if isinstance(trow, Mapping) else {
                "id": getattr(trow, "id", idx + 1),
                "target_ref": getattr(trow, "target_ref", ""),
                "target_idempotency_key": getattr(trow, "target_idempotency_key", ""),
                "state": getattr(trow, "state", "pending"),
                "provider_receipt": getattr(trow, "provider_receipt", None),
                "error_code": getattr(trow, "error_code", None),
                "executed_at": _iso_or_none(getattr(trow, "executed_at", None)),
            }
            if redacted:
                t_dict["target_ref"] = f"[скрыто:{idx + 1}]"
            projected_targets.append(t_dict)

        outcome_raw = _get_field(cmd, "outcome_summary")
        outcome_summary = dict(outcome_raw) if isinstance(outcome_raw, Mapping) else None

        return ApprovalInboxItem(
            item_id=f"command:{cmd_id}",
            command_id=cmd_id,
            tenant_id=int(tenant_id),
            source_ledger="p13_t04_command_ledger",
            capability=capability,
            capability_version=capability_version,
            type_label=type_label,
            domain=spec.domain,
            risk_class=spec.risk_class,
            approval_policy=approval_policy,
            requester_user_id=actor_user_id,
            requester_label=f"Пользователь #{actor_user_id}",
            approved_user_id=approved_user_id,
            connection_id=connection_id,
            source_label=source_label,
            target_refs=visible_targets,
            target_count=len(raw_targets),
            target_summary=target_summary or "—",
            target_accessible=target_accessible,
            redacted=redacted,
            payload_hash=payload_hash,
            approved_payload_hash=str(approved_payload_hash) if approved_payload_hash is not None else None,
            is_stale_payload=is_stale,
            safe_preview=safe_preview,
            preview_text=preview_text,
            reason_for_review=reason_for_review,
            raw_state=raw_state,
            status=status,
            status_label=status_label,
            actionable=actionable,
            can_approve=can_approve,
            can_reject=can_reject,
            can_execute=can_execute,
            can_reconcile=can_reconcile,
            approval_expires_at=_iso_or_none(_get_field(cmd, "approval_expires_at")),
            approved_at=_iso_or_none(_get_field(cmd, "approved_at")),
            executed_at=_iso_or_none(_get_field(cmd, "executed_at")),
            created_at=_iso_or_none(_get_field(cmd, "created_at")),
            error_code=_get_field(cmd, "error_code"),
            reconcile_hint=_get_field(cmd, "reconcile_hint"),
            outcome_summary=outcome_summary,
            targets=projected_targets,
            authoritative_url=f"/app/commands/{cmd_id}",
            api_url=f"/api/v1/commands/{cmd_id}",
            bot_decision_link=format_bot_decision_link(cmd_id, capability=capability),
        )

    async def list_items(
        self,
        *,
        limit: int = 50,
        before_id: int | None = None,
        status_filter: str | None = None,
        actionable_only: bool = False,
        perms: Any = None,
        accessible_targets: set[str] | None = None,
        now: datetime | None = None,
        session: Any = None,
    ) -> dict[str, Any]:
        """List permission-filtered approval inbox items for the active tenant."""
        tenant_id = _require_inbox_tenant()
        bounded_limit = max(1, min(int(limit), 100))
        if not _can_view_inbox(perms):
            return {"items": [], "limit": bounded_limit, "next_before_id": None}

        session_grants = await _load_actor_session_grants(session, tenant_id)
        if session_grants is not None and not session_grants:
            return {"items": [], "limit": bounded_limit, "next_before_id": None}

        page = await self._ledger.list_commands(
            limit=bounded_limit,
            before_id=before_id,
            session=session,
        )
        raw_commands = page.get("items", []) if isinstance(page, Mapping) else list(page or [])
        next_before_id = page.get("next_before_id") if isinstance(page, Mapping) else None

        normalized_filter = (status_filter or "").strip().lower()
        if normalized_filter in {"all", ""}:
            normalized_filter = ""

        # If session has ORM objects in `.commands`, prefer ORM rows so `payload` is available for hash check.
        orm_commands_by_id: dict[int, BusinessCommand] = {}
        if session is not None and hasattr(session, "commands") and isinstance(session.commands, Mapping):
            orm_commands_by_id = {
                int(k): v for k, v in session.commands.items() if getattr(v, "tenant_id", None) == tenant_id
            }

        items: list[dict[str, Any]] = []
        for raw_cmd in raw_commands:
            cid = _get_field(raw_cmd, "id")
            source_obj = orm_commands_by_id.get(int(cid), raw_cmd) if cid is not None else raw_cmd
            projected = self.project_command(
                source_obj,
                perms=perms,
                accessible_targets=accessible_targets,
                session_grants=session_grants,
                now=now,
            )
            if projected is None:
                continue
            if actionable_only and not projected.actionable:
                continue
            if normalized_filter and projected.status != normalized_filter:
                continue
            items.append(projected.to_dict())

        return {
            "items": items,
            "limit": bounded_limit,
            "next_before_id": next_before_id,
        }

    async def get_item(
        self,
        command_id: int,
        *,
        perms: Any = None,
        accessible_targets: set[str] | None = None,
        now: datetime | None = None,
        session: Any = None,
    ) -> dict[str, Any]:
        """Return one projected item by ID or raise CommandNotFound / CommandDenied."""
        tenant_id = _require_inbox_tenant()
        if not _can_view_inbox(perms):
            raise CommandDenied("Viewer does not have permission to view the approval inbox")

        session_grants = await _load_actor_session_grants(session, tenant_id)
        data = await self._ledger.get_command(int(command_id), session=session)
        spec = get_capability(str(data["capability"]))
        if not _can_view_capability(perms, spec, session_grants=session_grants):
            raise CommandDenied(f"Viewer lacks permission for capability '{data['capability']}'")

        source_obj: Any = data
        if session is not None and hasattr(session, "commands") and isinstance(session.commands, Mapping):
            orm_cmd = session.commands.get(int(command_id))
            if orm_cmd is not None and getattr(orm_cmd, "tenant_id", None) == tenant_id:
                merged = dict(data)
                merged["payload"] = dict(getattr(orm_cmd, "payload", {}) or {})
                source_obj = merged

        projected = self.project_command(
            source_obj,
            perms=perms,
            accessible_targets=accessible_targets,
            session_grants=session_grants,
            now=now,
        )
        if projected is None:
            raise CommandNotFound("Business command not found in current tenant")
        return projected.to_dict()

    async def count_pending(
        self,
        *,
        perms: Any = None,
        accessible_targets: set[str] | None = None,
        now: datetime | None = None,
        session: Any = None,
    ) -> int:
        """Count actionable pending items for the header/navigation badge."""
        try:
            page = await self.list_items(
                limit=100,
                status_filter="pending",
                actionable_only=True,
                perms=perms,
                accessible_targets=accessible_targets,
                now=now,
                session=session,
            )
            return len(page.get("items", []))
        except Exception:
            return 0

    @staticmethod
    def _guard_prohibited_options(
        *,
        always_allow: bool = False,
        bulk_command_ids: Iterable[int] | None = None,
        auto_execute: bool = False,
    ) -> None:
        if always_allow:
            raise CommandDenied("Persistent 'always allow' approval policy is not permitted (P20-T03)")
        if bulk_command_ids is not None:
            raise CommandDenied("Bulk approval is not permitted; review one exact item at a time (P20-T03)")
        if auto_execute:
            raise CommandDenied(
                "Approval cannot trigger automatic execution; execution requires a separate explicit call (P20-T03)"
            )

    async def approve_item(
        self,
        command_id: int,
        *,
        expected_payload_hash: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        perms: Any = None,
        accessible_targets: set[str] | None = None,
        always_allow: bool = False,
        bulk_command_ids: Iterable[int] | None = None,
        auto_execute: bool = False,
        now: datetime | None = None,
        session: Any = None,
    ) -> dict[str, Any]:
        """Approve a single exact command item through `command_ledger.approve_command`."""
        tenant_id = _require_inbox_tenant()
        self._guard_prohibited_options(
            always_allow=always_allow,
            bulk_command_ids=bulk_command_ids,
            auto_execute=auto_execute,
        )
        if not isinstance(expected_payload_hash, str) or not expected_payload_hash.strip():
            raise CommandConflict("expected_payload_hash is required to approve a command")

        lock = _get_command_lock(tenant_id, int(command_id))
        async with lock:
            session_grants = await _load_actor_session_grants(session, tenant_id)
            cmd = await self._ledger.get_command(int(command_id), session=session)
            spec = get_capability(str(cmd["capability"]))
            if not _can_act_on_capability(perms, spec, session_grants=session_grants):
                raise CommandDenied(f"Reviewer lacks permission to approve capability '{cmd['capability']}'")
            target_accessible, _ = _can_read_target_details(
                perms,
                spec,
                list(cmd.get("target_refs") or []),
                accessible_targets,
                session_grants=session_grants,
            )
            if not target_accessible:
                raise CommandDenied("Reviewer does not have access to one or more command targets")

            # Single-use review click: a repeated click on an already approved item raises CommandConflict.
            if str(cmd.get("state")) == "approved":
                raise CommandConflict("Command is already approved; repeated approval click rejected")

            approved_cmd = await self._ledger.approve_command(
                int(command_id),
                expected_payload_hash=expected_payload_hash.strip(),
                ttl_seconds=ttl_seconds,
                session=session,
            )
            projected = self.project_command(
                approved_cmd,
                perms=perms,
                accessible_targets=accessible_targets,
                session_grants=session_grants,
                now=now,
            )
            return projected.to_dict() if projected is not None else approved_cmd

    async def reject_item(
        self,
        command_id: int,
        *,
        reason: str = "user_rejected",
        expected_payload_hash: str | None = None,
        perms: Any = None,
        accessible_targets: set[str] | None = None,
        always_allow: bool = False,
        bulk_command_ids: Iterable[int] | None = None,
        now: datetime | None = None,
        session: Any = None,
    ) -> dict[str, Any]:
        """Reject/cancel a single command item through `command_ledger.reject_command`."""
        tenant_id = _require_inbox_tenant()
        self._guard_prohibited_options(
            always_allow=always_allow,
            bulk_command_ids=bulk_command_ids,
            auto_execute=False,
        )
        lock = _get_command_lock(tenant_id, int(command_id))
        async with lock:
            session_grants = await _load_actor_session_grants(session, tenant_id)
            cmd = await self._ledger.get_command(int(command_id), session=session)
            spec = get_capability(str(cmd["capability"]))
            if not _can_act_on_capability(perms, spec, session_grants=session_grants):
                raise CommandDenied(f"Reviewer lacks permission to reject capability '{cmd['capability']}'")
            if expected_payload_hash is not None and expected_payload_hash.strip():
                if str(cmd.get("payload_hash")) != expected_payload_hash.strip():
                    raise CommandConflict("Command payload changed since preview; reload before rejecting")

            rejected_cmd = await self._ledger.reject_command(
                int(command_id),
                session=session,
            )
            projected = self.project_command(
                rejected_cmd,
                perms=perms,
                accessible_targets=accessible_targets,
                session_grants=session_grants,
                now=now,
            )
            if projected is not None:
                out = projected.to_dict()
                out["rejection_reason"] = reason
                return out
            return rejected_cmd

    async def execute_item(
        self,
        command_id: int,
        *,
        expected_payload_hash: str | None = None,
        adapter: AdapterCallable | None = None,
        perms: Any = None,
        accessible_targets: set[str] | None = None,
        now: datetime | None = None,
        session: Any = None,
    ) -> dict[str, Any]:
        """Execute a previously approved command item through `command_ledger.execute_command`."""
        tenant_id = _require_inbox_tenant()
        lock = _get_command_lock(tenant_id, int(command_id))
        async with lock:
            session_grants = await _load_actor_session_grants(session, tenant_id)
            cmd = await self._ledger.get_command(int(command_id), session=session)
            spec = get_capability(str(cmd["capability"]))
            if not _can_act_on_capability(perms, spec, session_grants=session_grants):
                raise CommandDenied(f"Caller lacks permission to execute capability '{cmd['capability']}'")
            target_accessible, _ = _can_read_target_details(
                perms,
                spec,
                list(cmd.get("target_refs") or []),
                accessible_targets,
                session_grants=session_grants,
            )
            if not target_accessible:
                raise CommandDenied("Caller does not have access to one or more command targets")
            if str(cmd.get("state")) == "cancelled":
                raise CommandConflict("Rejected or cancelled command cannot be executed")
            if expected_payload_hash is not None and expected_payload_hash.strip():
                if str(cmd.get("payload_hash")) != expected_payload_hash.strip():
                    raise CommandDenied("Payload hash mismatch; modified commands require a new preview and approval")

            executed_cmd = await self._ledger.execute_command(
                int(command_id),
                adapter=adapter,
                now=now,
                session=session,
            )
            projected = self.project_command(
                executed_cmd,
                perms=perms,
                accessible_targets=accessible_targets,
                session_grants=session_grants,
                now=now,
            )
            return projected.to_dict() if projected is not None else executed_cmd

    async def reconcile_item(
        self,
        command_id: int,
        *,
        resolved_state: str,
        provider_receipts: dict[str, str] | None = None,
        perms: Any = None,
        accessible_targets: set[str] | None = None,
        now: datetime | None = None,
        session: Any = None,
    ) -> dict[str, Any]:
        """Reconcile an `unknown` command outcome through `command_ledger.reconcile_unknown`."""
        tenant_id = _require_inbox_tenant()
        lock = _get_command_lock(tenant_id, int(command_id))
        async with lock:
            session_grants = await _load_actor_session_grants(session, tenant_id)
            cmd = await self._ledger.get_command(int(command_id), session=session)
            spec = get_capability(str(cmd["capability"]))
            if not _can_act_on_capability(perms, spec, session_grants=session_grants):
                raise CommandDenied(f"Caller lacks permission to reconcile capability '{cmd['capability']}'")

            reconciled_cmd = await self._ledger.reconcile_unknown(
                int(command_id),
                resolved_state=resolved_state,
                provider_receipts=provider_receipts,
                session=session,
            )
            projected = self.project_command(
                reconciled_cmd,
                perms=perms,
                accessible_targets=accessible_targets,
                session_grants=session_grants,
                now=now,
            )
            return projected.to_dict() if projected is not None else reconciled_cmd

    async def project_for_today(
        self,
        *,
        perms: Any = None,
        scope: str = "mine",
        actor_id: int | None = None,
        accessible_targets: set[str] | None = None,
        now: datetime | None = None,
        session: Any = None,
    ) -> list[dict[str, Any]]:
        """Project actionable command ledger items into the Today `decisions` section (P20-T07)."""
        page = await self.list_items(
            limit=25,
            perms=perms,
            accessible_targets=accessible_targets,
            now=now,
            session=session,
        )
        decisions: list[dict[str, Any]] = []
        for item in page.get("items", []):
            if item["status"] not in {"pending", "unknown"}:
                continue
            if scope == "mine" and actor_id is not None and item["requester_user_id"] != int(actor_id):
                if not item["can_approve"] and not item["can_reconcile"]:
                    continue
            cid = item["command_id"]
            decisions.append(
                {
                    "key": f"command:{cid}",
                    "id": cid,
                    "title": f"{item['type_label']} #{cid}",
                    "summary": item["preview_text"],
                    "status": item["status"],
                    "status_label": item["status_label"],
                    "href": item["authoritative_url"],
                    "authoritative_url": item["authoritative_url"],
                    "source_kind": "business_command",
                    "source_label": item["source_label"],
                    "why": item["reason_for_review"],
                    "payload_hash": item["payload_hash"],
                    "approval_expires_at": item["approval_expires_at"],
                    "freshness": {
                        "state": "fresh" if item["status"] == "pending" else "degraded",
                        "label": item["status_label"],
                        "updated_at": item["created_at"],
                    },
                }
            )
        return decisions

    async def project_for_chat_decisions(
        self,
        *,
        perms: Any = None,
        accessible_targets: set[str] | None = None,
        now: datetime | None = None,
        session: Any = None,
    ) -> list[dict[str, Any]]:
        """Project actionable command items into the Chat 'Ждут решения' tab and inline decision cards (P20-T08)."""
        page = await self.list_items(
            limit=20,
            perms=perms,
            accessible_targets=accessible_targets,
            now=now,
            session=session,
        )
        decisions: list[dict[str, Any]] = []
        for item in page.get("items", []):
            if item["status"] not in {"pending", "approved", "unknown"}:
                continue
            cid = item["command_id"]
            decisions.append(
                {
                    "id": f"command:{cid}",
                    "command_id": cid,
                    "card_type": "approval_decision",
                    "title": f"{item['type_label']} #{cid}",
                    "summary": f"{item['preview_text']} · Цели: {item['target_summary']}"
                    if item["target_summary"]
                    else item["preview_text"],
                    "status": item["status"],
                    "status_label": item["status_label"],
                    "reason_for_review": item["reason_for_review"],
                    "payload_hash": item["payload_hash"],
                    "approval_expires_at": item["approval_expires_at"],
                    "authoritative_ref": f"command:{cid}",
                    "authoritative_url": item["authoritative_url"],
                    "limitations": (
                        "Решение принимается на странице команды; согласование не запускает отправку автоматически."
                    ),
                    "actions": [
                        {
                            "kind": "link",
                            "label": "Открыть решение",
                            "href": item["authoritative_url"],
                        }
                    ],
                }
            )
        return decisions


approval_inbox = ApprovalInboxService()
