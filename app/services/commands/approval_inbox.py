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

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Iterable, Mapping

from sqlalchemy import select

from app.core.access_catalog import workspace_grants
from app.core.database import async_session_maker
from app.core.permissions import get_current_user
from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass
from app.models.actions.business_command import BusinessCommand, BusinessCommandTarget
from app.models.contacts.core import Contact
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
    serialize_command,
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

# F2: the generic P13-T04 review workflow is only wired for the capability whose
# owning workflow is the ledger itself. Every other registered capability belongs
# to a workflow that is still disabled (outbox P15-T05, documents P19-T04,
# calendar P16-T03, 1C P17-T04) or whose generic cancel would desynchronize the
# owning service's child records (`comms.batch_campaign.v1` owns per-contact
# outbox sends, so a generic reject must not touch it). Unsupported capabilities
# are omitted from the list and refused on get/approve/reject/execute/reconcile
# without reaching the ledger; their owning services keep their own authority.
INBOX_SUPPORTED_CAPABILITIES: frozenset[str] = frozenset({"crm.greeting_send.v1"})
_PENDING_COUNT_PAGE_SIZE = 100
UNSUPPORTED_CAPABILITY_REASONS: dict[str, str] = {
    "comms.batch_campaign.v1": (
        "the campaign workflow owns its per-contact outbox sends; a generic cancel would desynchronize them"
    ),
    "calendar.event_write.v1": "the calendar owns events and recurrence (D24); its projection is disabled",
    "onec.document_write.v1": "1C writes are not wired into this inbox (P17-T04)",
    "documents.delivery_send.v1": "document delivery belongs to the P19-T04 approval workflow",
    "comms.email.send.v1": "transport executes only through the persistent outbox (P15-T05)",
    "comms.telegram.bot_send.v1": "transport executes only through the persistent outbox (P15-T05)",
    "comms.max.bot_send.v1": "transport executes only through the persistent outbox (P15-T05)",
}

# F3: the only target reference shape this inbox can verify against an owned row.
_CONTACT_TARGET_PREFIX = "contact"

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

# F4: single-use review clicks are enforced by the ledger's database row lock,
# not by a process-local asyncio lock, which cannot span processes.


def _require_inbox_tenant() -> int:
    tenant_id = current_tenant_id()
    if is_bypass() or type(tenant_id) is not int or tenant_id <= 0:
        raise TenantContextError("Approval inbox requires an explicit workspace scope")
    return tenant_id


@asynccontextmanager
async def _inbox_session(session: Any = None) -> AsyncIterator[Any]:
    """Yield the caller's session, or an internally owned one when none was supplied.

    F1: an omitted session used to skip the actor, membership and role-grant
    checks entirely. The inbox now always has a session to resolve them from, so
    `session=None` can never widen what the caller may see or act on.
    """
    if session is not None:
        yield session
        return
    async with async_session_maker() as owned:
        yield owned


@asynccontextmanager
async def _inbox_mutation_session(session: Any = None) -> AsyncIterator[Any]:
    """Own a transaction only when the inbox also owns the session.

    A caller-provided AsyncSession may already be in a transaction. The caller
    retains transaction ownership; our row lock and ledger mutation still use
    that same session/transaction.
    """
    if session is not None:
        yield session
        return
    async with async_session_maker() as owned:
        async with owned.begin():
            yield owned


def _is_inbox_supported(capability: str) -> bool:
    """F2: whether the generic P13-T04 review workflow may serve this capability."""
    return capability in INBOX_SUPPORTED_CAPABILITIES


def _unsupported_message(capability: str) -> str:
    reason = UNSUPPORTED_CAPABILITY_REASONS.get(capability, "its owning workflow is not wired into this inbox")
    return f"Capability '{capability}' is not supported by the approval inbox: {reason}"


def _contact_target_id(target_ref: str) -> int | None:
    """Parse `contact:<positive-id>`; anything else is not a verifiable target."""
    if not isinstance(target_ref, str):
        return None
    prefix, _, raw_id = target_ref.strip().partition(":")
    if prefix.strip().lower() != _CONTACT_TARGET_PREFIX:
        return None
    token = raw_id.strip()
    if not token.isdigit():
        return None
    contact_id = int(token)
    return contact_id if contact_id > 0 else None


def _is_canonical_payload_hash(value: Any) -> bool:
    """F4: a review click must bind the exact canonical hash, not a prefix or a guess."""
    if not isinstance(value, str):
        return False
    token = value.strip()
    if not token.startswith("sha256:") or len(token) != 71:
        return False
    return all(char in "0123456789abcdef" for char in token[7:])


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


def _can_view_capability(
    perms: Any, spec: CapabilitySpec, session_grants: frozenset[tuple[str, ActionType]] | None = None
) -> bool:
    """May the viewer see this capability's items.

    F1: the capability's own resource grant is mandatory. `botaction` VIEW and a
    platform superuser role are not substitutes for it, so a caller who may open
    the inbox still sees nothing of a capability the active role does not grant.
    """
    if session_grants is not None:
        if (spec.required_resource, spec.required_action) not in session_grants and (
            spec.required_resource,
            ActionType.VIEW,
        ) not in session_grants:
            return False
    if perms is None:
        return True
    if not _can_view_inbox(perms):
        return False
    return _perms_allows(perms, spec.required_resource, spec.required_action) or _perms_allows(
        perms, spec.required_resource, ActionType.VIEW
    )


def _can_act_on_capability(
    perms: Any, spec: CapabilitySpec, session_grants: frozenset[tuple[str, ActionType]] | None = None
) -> bool:
    if session_grants is not None:
        if (spec.required_resource, spec.required_action) not in session_grants:
            return False
    if perms is None:
        return True
    if not (
        _perms_allows(perms, "botaction", ActionType.UPDATE) or _perms_allows(perms, "botaction", ActionType.CREATE)
    ):
        return False
    return _perms_allows(perms, spec.required_resource, spec.required_action)


async def _load_actor_session_grants(
    session: Any, tenant_id: int, *, for_update: bool = False
) -> frozenset[tuple[str, ActionType]]:
    """Resolve the active actor's workspace role grants from real ORM queries.

    F1: fail closed. A missing or inactive actor, an inactive workspace, an
    inactive membership, a membership without a role, or a role with no grants
    all resolve to an empty grant set, which denies every capability. `session`
    is always a real session because `_inbox_session()` supplies an owned one.
    """
    if session is None:
        return frozenset()
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
    member_stmt = select(TenantUser).where(
        TenantUser.tenant_id == tenant_id,
        TenantUser.user_id == actor_id,
        TenantUser.channel == "web",
        TenantUser.is_active.is_(True),
    )
    if for_update:
        member_stmt = member_stmt.with_for_update(read=True).execution_options(populate_existing=True)
    member = (await session.execute(member_stmt)).scalar_one_or_none()
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
    """Return `(target_accessible, can_read_details)` for the command's target_refs.

    F3: this is a strict gate, not a redaction hint. When the caller cannot read
    the command's own resource or any one of its targets, the item is omitted
    from the list and refused on detail/mutation. Field-name redaction is never
    the fallback, because summaries, outcomes and receipts can carry the same
    secrets under keys nobody enumerated.

    `accessible_targets` can only narrow this decision: it is a caller-supplied
    hint and never proves access on its own. Row-level proof comes from
    `_verify_contact_targets()` against tenant-owned `Contact` rows.
    """
    if accessible_targets is not None:
        all_accessible = all(ref in accessible_targets for ref in (target_refs or []))
        if not all_accessible:
            return False, False
    if session_grants is not None:
        if (spec.required_resource, spec.required_action) not in session_grants and (
            spec.required_resource,
            ActionType.VIEW,
        ) not in session_grants:
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
                _perms_allows(perms, "contact", ActionType.VIEW) or _perms_allows(perms, "contact", ActionType.UPDATE)
            ):
                return False, False
    return True, True


def _redact_secrets(data: Any) -> Any:
    """Recursively replace credential-shaped keys for a viewer who may read the item.

    F3: target-scoped PII is no longer masked here. An inaccessible target means
    the whole item is withheld, so this pass only removes credentials that no
    reviewer should ever see in a preview or outcome payload.
    """
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for key, value in data.items():
            key_str = str(key)
            if any(token in key_str.lower() for token in _SENSITIVE_KEY_SUBSTRINGS):
                out[key_str] = "[REDACTED]"
            else:
                out[key_str] = _redact_secrets(value)
        return out
    if isinstance(data, list):
        return [_redact_secrets(item) for item in data]
    return data


async def _verify_contact_targets(session: Any, tenant_id: int, target_refs: list[str], *, lock: bool = False) -> bool:
    """F3: prove every target is a live contact owned by the active tenant.

    Accepted shape is `contact:<positive-id>`; the row must exist in `contacts`
    for this tenant and must not be merged into another contact. An unknown,
    malformed, missing or cross-tenant target makes the whole command
    inaccessible, so nothing about it is projected.
    """
    refs = list(target_refs or [])
    if not refs:
        return False
    contact_ids: list[int] = []
    for ref in refs:
        contact_id = _contact_target_id(ref)
        if contact_id is None:
            return False
        contact_ids.append(contact_id)
    stmt = (
        select(Contact.id)
        .where(
            Contact.tenant_id == tenant_id,
            Contact.id.in_(contact_ids),
            Contact.merged_into_id.is_(None),
        )
        .order_by(Contact.id)
    )
    if lock:
        stmt = stmt.with_for_update(read=True)
    rows = (await session.execute(stmt)).scalars()
    found = {int(row) for row in rows.all()}
    return found == set(contact_ids)


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
) -> str:
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

    async def project_command(
        self,
        cmd: BusinessCommand | Mapping[str, Any],
        *,
        perms: Any = None,
        accessible_targets: set[str] | None = None,
        session_grants: frozenset[tuple[str, ActionType]] | None = None,
        now: datetime | None = None,
        session: Any = None,
    ) -> ApprovalInboxItem | None:
        """Project a single command for the active viewer, or None when withheld.

        Returns None when the command is outside the active tenant, its capability
        is not served by this generic review workflow (F2), the viewer lacks the
        capability's own resource grant (F1), or any target is not a live
        tenant-owned contact (F3). A withheld item leaves no preview, outcome or
        receipt field behind to be redacted later.
        """
        tenant_id = _require_inbox_tenant()
        cmd_tenant_id = _get_field(cmd, "tenant_id")
        if cmd_tenant_id != tenant_id:
            return None
        capability = str(_get_field(cmd, "capability") or "")
        if capability not in CAPABILITY_REGISTRY:
            return None
        # F2: only capabilities whose owning workflow is this ledger's own.
        if not _is_inbox_supported(capability):
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
        # F3: withhold the item instead of masking its fields.
        if not (target_accessible and can_read_details):
            return None
        if not await _verify_contact_targets(session, tenant_id, raw_targets):
            return None
        can_act = _can_act_on_capability(perms, spec, session_grants=session_grants)

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

        safe_preview = _redact_secrets(raw_preview_source)
        if not isinstance(safe_preview, dict):
            safe_preview = {}

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
        )
        reason_for_review = _build_reason_for_review(
            raw_state,
            approval_policy,
            payload_hash,
            spec,
            is_stale=is_stale,
        )

        # The normalized status collapses `draft`/`previewed` into `pending` and
        # `claimed`/`unknown` into `unknown`. Only a `previewed` row may be
        # approved and only an `unknown` row may be reconciled, so a draft or a
        # claimed row never becomes actionable through the normalized status.
        can_approve = bool(status == "pending" and can_act and not is_stale and raw_state == "previewed")
        can_reject = bool(status in {"pending", "approved"} and can_act)
        can_execute = bool(status == "approved" and can_act and not is_stale and approved_payload_hash == payload_hash)
        can_reconcile = bool(status == "unknown" and can_act and raw_state == "unknown")
        actionable = bool(can_approve or can_reject or can_reconcile)

        raw_target_rows = _get_field(cmd, "targets") or []
        projected_targets: list[dict[str, Any]] = []
        for idx, trow in enumerate(raw_target_rows):
            t_dict = (
                dict(trow)
                if isinstance(trow, Mapping)
                else {
                    "id": getattr(trow, "id", idx + 1),
                    "target_ref": getattr(trow, "target_ref", ""),
                    "target_idempotency_key": getattr(trow, "target_idempotency_key", ""),
                    "state": getattr(trow, "state", "pending"),
                    "provider_receipt": getattr(trow, "provider_receipt", None),
                    "error_code": getattr(trow, "error_code", None),
                    "executed_at": _iso_or_none(getattr(trow, "executed_at", None)),
                }
            )
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
            redacted=False,
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
        raw_states: set[str] | frozenset[str] | None = None,
        actionable_only: bool = False,
        perms: Any = None,
        accessible_targets: set[str] | None = None,
        now: datetime | None = None,
        session: Any = None,
    ) -> dict[str, Any]:
        """List permission-filtered approval inbox items for the active tenant.

        The active actor's membership and role grants are always resolved from the
        database (F1); an omitted session is replaced by an owned one, never by a
        skip. Items whose capability this workflow does not serve (F2) or whose
        targets are not live tenant-owned contacts (F3) are omitted entirely.
        """
        tenant_id = _require_inbox_tenant()
        bounded_limit = max(1, min(int(limit), 100))
        if not _can_view_inbox(perms):
            return {"items": [], "limit": bounded_limit, "next_before_id": None}

        async with _inbox_session(session) as active_session:
            session_grants = await _load_actor_session_grants(active_session, tenant_id)
            if not session_grants:
                return {"items": [], "limit": bounded_limit, "next_before_id": None}

            stmt = select(BusinessCommand).where(
                BusinessCommand.tenant_id == tenant_id,
                BusinessCommand.capability.in_(INBOX_SUPPORTED_CAPABILITIES),
            )
            if raw_states:
                stmt = stmt.where(BusinessCommand.state.in_(tuple(raw_states)))
            if before_id is not None:
                stmt = stmt.where(BusinessCommand.id < before_id)
            rows = list(
                (await active_session.execute(stmt.order_by(BusinessCommand.id.desc()).limit(bounded_limit + 1)))
                .scalars()
                .all()
            )
            has_more = len(rows) > bounded_limit
            commands = rows[:bounded_limit]
            next_before_id = int(commands[-1].id) if has_more and commands else None

            command_ids = [int(cmd.id) for cmd in commands]
            targets_by_command: dict[int, list[BusinessCommandTarget]] = {}
            if command_ids:
                target_rows = (
                    (
                        await active_session.execute(
                            select(BusinessCommandTarget)
                            .where(
                                BusinessCommandTarget.tenant_id == tenant_id,
                                BusinessCommandTarget.command_id.in_(command_ids),
                            )
                            .order_by(BusinessCommandTarget.command_id, BusinessCommandTarget.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                for target in target_rows:
                    targets_by_command.setdefault(int(target.command_id), []).append(target)
            raw_commands = [serialize_command(cmd, targets_by_command.get(int(cmd.id), [])) for cmd in commands]

            # The ledger serializes commands without `payload`; load the stored ORM
            # payload so the canonical stale/hash check runs on persisted data.
            payloads = await self._load_orm_payloads(
                active_session,
                tenant_id,
                [int(_get_field(cmd, "id")) for cmd in raw_commands if _get_field(cmd, "id") is not None],
            )

            normalized_filter = (status_filter or "").strip().lower()
            if normalized_filter in {"all", ""}:
                normalized_filter = ""

            items: list[dict[str, Any]] = []
            for raw_cmd in raw_commands:
                cid = _get_field(raw_cmd, "id")
                if cid is None:
                    continue
                stored_payload = payloads.get(int(cid))
                # F1: a command whose stored payload cannot be read is not projected.
                if stored_payload is None:
                    continue
                source_obj = dict(raw_cmd) if isinstance(raw_cmd, Mapping) else raw_cmd
                if isinstance(source_obj, dict):
                    source_obj["payload"] = stored_payload
                projected = await self.project_command(
                    source_obj,
                    perms=perms,
                    accessible_targets=accessible_targets,
                    session_grants=session_grants,
                    now=now,
                    session=active_session,
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

    @staticmethod
    async def _load_orm_payloads(session: Any, tenant_id: int, command_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Load stored payloads for the given commands, tenant-fenced."""
        if not command_ids:
            return {}
        rows = (
            await session.execute(
                select(BusinessCommand.id, BusinessCommand.payload).where(
                    BusinessCommand.tenant_id == tenant_id,
                    BusinessCommand.id.in_(command_ids),
                )
            )
        ).all()
        payloads: dict[int, dict[str, Any]] = {}
        for row in rows:
            row_id, row_payload = row[0], row[1]
            payloads[int(row_id)] = dict(row_payload) if isinstance(row_payload, Mapping) else {}
        return payloads

    async def get_item(
        self,
        command_id: int,
        *,
        perms: Any = None,
        accessible_targets: set[str] | None = None,
        now: datetime | None = None,
        session: Any = None,
    ) -> dict[str, Any]:
        """Return one projected item by ID or raise CommandNotFound / CommandDenied.

        A capability this workflow does not serve is refused explicitly (F2). A
        command whose target is not a live tenant-owned contact is reported as
        not-found (F3), so the caller learns nothing about an item it may not read.
        """
        tenant_id = _require_inbox_tenant()
        if not _can_view_inbox(perms):
            raise CommandDenied("Viewer does not have permission to view the approval inbox")

        async with _inbox_session(session) as active_session:
            session_grants = await _load_actor_session_grants(active_session, tenant_id)
            if not session_grants:
                raise CommandDenied("Active workspace membership with a role grant is required")

            data = await self._ledger.get_command(int(command_id), session=active_session)
            capability = str(data["capability"])
            if not _is_inbox_supported(capability):
                raise CommandDenied(_unsupported_message(capability))
            spec = get_capability(capability)
            if not _can_view_capability(perms, spec, session_grants=session_grants):
                raise CommandDenied(f"Viewer lacks permission for capability '{capability}'")

            payloads = await self._load_orm_payloads(active_session, tenant_id, [int(command_id)])
            stored_payload = payloads.get(int(command_id))
            if stored_payload is None:
                raise CommandNotFound("Business command not found in current tenant")
            source_obj: Any = dict(data)
            source_obj["payload"] = stored_payload

            projected = await self.project_command(
                source_obj,
                perms=perms,
                accessible_targets=accessible_targets,
                session_grants=session_grants,
                now=now,
                session=active_session,
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
        """Count authorized actionable pending items without scanning history.

        Candidates are restricted in SQL to this inbox's one supported capability
        and the ledger's pending states. Each candidate still passes through the
        same permission, canonical-payload and tenant-owned-target checks as the
        list projection.
        """
        total = 0
        before_id: int | None = None
        try:
            async with _inbox_session(session) as active_session:
                while True:
                    tenant_id = _require_inbox_tenant()
                    if not _can_view_inbox(perms):
                        break
                    session_grants = await _load_actor_session_grants(active_session, tenant_id)
                    if not session_grants:
                        break
                    stmt = (
                        select(BusinessCommand)
                        .where(
                            BusinessCommand.tenant_id == tenant_id,
                            BusinessCommand.capability.in_(INBOX_SUPPORTED_CAPABILITIES),
                            BusinessCommand.state.in_(("draft", "previewed")),
                        )
                        .order_by(BusinessCommand.id.desc())
                        .limit(_PENDING_COUNT_PAGE_SIZE)
                    )
                    if before_id is not None:
                        stmt = stmt.where(BusinessCommand.id < before_id)
                    candidates = list((await active_session.execute(stmt)).scalars().all())
                    if not candidates:
                        break
                    command_ids = [int(cmd.id) for cmd in candidates]
                    payloads = await self._load_orm_payloads(active_session, tenant_id, command_ids)
                    for cmd in candidates:
                        payload = payloads.get(int(cmd.id))
                        if payload is None:
                            continue
                        cmd.payload = payload
                        projection = await self.project_command(
                            cmd,
                            perms=perms,
                            accessible_targets=accessible_targets,
                            session_grants=session_grants,
                            now=now,
                            session=active_session,
                        )
                        if projection is not None and projection.status == "pending" and projection.actionable:
                            total += 1
                    before_id = int(candidates[-1].id)
            return total
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

    async def _lock_command_row(self, active_session: Any, tenant_id: int, command_id: int) -> Any:
        """F4: take the ledger's own row lock before any authorization decision.

        The lock is the database row lock the ledger already uses, so a repeated
        concurrent click in another process waits here and then loses with
        `CommandConflict` instead of double-approving. No process-local lock is
        used for correctness.
        """
        return (
            await active_session.execute(
                select(BusinessCommand)
                .where(BusinessCommand.tenant_id == tenant_id, BusinessCommand.id == int(command_id))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def _authorize_mutation(
        self,
        active_session: Any,
        command_id: int,
        *,
        perms: Any,
        accessible_targets: set[str] | None,
        action_label: str,
    ) -> tuple[dict[str, Any], CapabilitySpec, frozenset[tuple[str, ActionType]]]:
        """Authorize a single-item mutation while the row lock is held.

        F2 and F3 live here so no mutation path can bypass them: a capability this
        workflow does not serve is refused, and a target that is not a live
        tenant-owned contact is refused before `approve_command`/
        `reject_command`/`execute_command`/`reconcile_unknown` is called. F1's
        actor, membership and role-grant checks run against the same session and
        transaction the mutation will use.
        """
        tenant_id = _require_inbox_tenant()
        session_grants = await _load_actor_session_grants(active_session, tenant_id, for_update=True)
        if not session_grants:
            raise CommandDenied("Active workspace membership with a role grant is required")
        locked = await self._lock_command_row(active_session, tenant_id, command_id)
        if locked is None:
            raise CommandNotFound("Command not found")
        cmd = await self._ledger.get_command(int(command_id), session=active_session)
        # The ORM object was loaded under FOR UPDATE above. Bind all later
        # authorization/projection checks to its stored payload, never a
        # serialized preview or a caller-supplied hash alone.
        locked_payload = dict(getattr(locked, "payload", {}) or {})
        cmd["payload"] = locked_payload
        actual_hash = canonical_payload_hash(str(cmd["capability"]), list(cmd.get("target_refs") or []), locked_payload)
        if actual_hash != str(cmd.get("payload_hash") or ""):
            raise CommandConflict("Command payload changed since preview; reload before review")
        capability = str(cmd["capability"])
        if not _is_inbox_supported(capability):
            raise CommandDenied(_unsupported_message(capability))
        spec = get_capability(capability)
        if not _can_act_on_capability(perms, spec, session_grants=session_grants):
            raise CommandDenied(f"Reviewer lacks permission to {action_label} capability '{capability}'")
        target_refs = list(cmd.get("target_refs") or [])
        target_accessible, can_read_details = _can_read_target_details(
            perms,
            spec,
            target_refs,
            accessible_targets,
            session_grants=session_grants,
        )
        if not (target_accessible and can_read_details):
            raise CommandDenied("Reviewer does not have access to one or more command targets")
        if not await _verify_contact_targets(active_session, tenant_id, target_refs, lock=True):
            raise CommandDenied("Reviewer does not have access to one or more command targets")
        return cmd, spec, session_grants

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
        """Approve a single exact command item through `command_ledger.approve_command`.

        F4: authorization, target verification, capability support and the exact
        canonical hash are all checked while the ledger row lock is held, in the
        same transaction the ledger then mutates. A repeated concurrent click
        waits on that lock and loses with `CommandConflict`.
        """
        tenant_id = _require_inbox_tenant()
        self._guard_prohibited_options(
            always_allow=always_allow,
            bulk_command_ids=bulk_command_ids,
            auto_execute=auto_execute,
        )
        if not _is_canonical_payload_hash(expected_payload_hash):
            raise CommandConflict("A canonical sha256 payload hash is required to approve a command")

        async with _inbox_mutation_session(session) as active_session:
            cmd, _spec, session_grants = await self._authorize_mutation(
                active_session,
                command_id,
                perms=perms,
                accessible_targets=accessible_targets,
                action_label="approve",
            )
            if str(cmd.get("payload_hash")) != expected_payload_hash.strip():
                raise CommandConflict("Command payload changed since preview; reload before approving")
            if str(cmd.get("state")) == "approved":
                raise CommandConflict("Command is already approved; repeated approval click rejected")

            approved_cmd = await self._ledger.approve_command(
                int(command_id),
                expected_payload_hash=expected_payload_hash.strip(),
                ttl_seconds=ttl_seconds,
                session=active_session,
            )
            projected = await self.project_command(
                {**approved_cmd, "payload": cmd["payload"]},
                perms=perms,
                accessible_targets=accessible_targets,
                session_grants=session_grants,
                now=now,
                session=active_session,
            )
            if projected is None:
                raise CommandNotFound("Command is no longer available")
            return projected.to_dict()

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
        """Reject/cancel a single command item through `command_ledger.reject_command`.

        F4: a reject click must bind the exact canonical hash too, so a reviewer
        cannot cancel a payload they never saw. Authorization and target
        verification run under the same row lock as the mutation.
        """
        tenant_id = _require_inbox_tenant()
        self._guard_prohibited_options(
            always_allow=always_allow,
            bulk_command_ids=bulk_command_ids,
            auto_execute=False,
        )
        if not _is_canonical_payload_hash(expected_payload_hash):
            raise CommandConflict("A canonical sha256 payload hash is required to reject a command")

        async with _inbox_mutation_session(session) as active_session:
            cmd, _spec, session_grants = await self._authorize_mutation(
                active_session,
                command_id,
                perms=perms,
                accessible_targets=accessible_targets,
                action_label="reject",
            )
            if str(cmd.get("payload_hash")) != expected_payload_hash.strip():
                raise CommandConflict("Command payload changed since preview; reload before rejecting")

            rejected_cmd = await self._ledger.reject_command(
                int(command_id),
                session=active_session,
            )
            projected = await self.project_command(
                {**rejected_cmd, "payload": cmd["payload"]},
                perms=perms,
                accessible_targets=accessible_targets,
                session_grants=session_grants,
                now=now,
                session=active_session,
            )
            if projected is not None:
                out = projected.to_dict()
                out["rejection_reason"] = reason
                return out
            raise CommandNotFound("Command is no longer available")

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
        async with _inbox_mutation_session(session) as active_session:
            cmd, _spec, session_grants = await self._authorize_mutation(
                active_session,
                command_id,
                perms=perms,
                accessible_targets=accessible_targets,
                action_label="execute",
            )
            if str(cmd.get("state")) == "cancelled":
                raise CommandConflict("Rejected or cancelled command cannot be executed")
            if expected_payload_hash is not None and expected_payload_hash.strip():
                if cmd["payload_hash"] != expected_payload_hash.strip():
                    raise CommandDenied("Payload hash mismatch; modified commands require a new preview and approval")

            executed_cmd = await self._ledger.execute_command(
                int(command_id),
                adapter=adapter,
                now=now,
                session=active_session,
            )
            projected = await self.project_command(
                {**executed_cmd, "payload": cmd["payload"]},
                perms=perms,
                accessible_targets=accessible_targets,
                session_grants=session_grants,
                now=now,
                session=active_session,
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
        async with _inbox_mutation_session(session) as active_session:
            cmd, _spec, session_grants = await self._authorize_mutation(
                active_session,
                command_id,
                perms=perms,
                accessible_targets=accessible_targets,
                action_label="reconcile",
            )
            reconciled_cmd = await self._ledger.reconcile_unknown(
                int(command_id),
                resolved_state=resolved_state,
                provider_receipts=provider_receipts,
                session=active_session,
            )
            projected = await self.project_command(
                {**reconciled_cmd, "payload": cmd["payload"]},
                perms=perms,
                accessible_targets=accessible_targets,
                session_grants=session_grants,
                now=now,
                session=active_session,
            )
            if projected is None:
                raise CommandNotFound("Command is no longer available")
            return projected.to_dict()

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
        """Project actionable command ledger items into the Today `decisions` section (P20-T07).

        F6: Today reads the same authorized projection as the inbox, so an
        unsupported capability or an unverifiable target never reaches the page.
        """
        page = await self.list_items(
            limit=25,
            raw_states={"draft", "previewed", "unknown", "claimed"},
            actionable_only=True,
            perms=perms,
            accessible_targets=accessible_targets,
            now=now,
            session=session,
        )
        decisions: list[dict[str, Any]] = []
        for item in page.get("items", []):
            if item["status"] not in {"pending", "unknown"}:
                continue
            if scope == "mine":
                if type(actor_id) is not int or actor_id <= 0:
                    continue
                if item["requester_user_id"] != actor_id:
                    continue
            cid = item["command_id"]
            decisions.append(
                {
                    "key": f"command:{cid}",
                    "id": cid,
                    "tenant_id": item["tenant_id"],
                    "requester_user_id": item["requester_user_id"],
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
            raw_states={"draft", "previewed", "approved", "unknown", "claimed"},
            actionable_only=True,
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
                    "summary": (
                        f"{item['preview_text']} · Цели: {item['target_summary']}"
                        if item["target_summary"]
                        else item["preview_text"]
                    ),
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
