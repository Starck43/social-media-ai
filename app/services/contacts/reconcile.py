"""Contact identity reconciliation: suggestions, manual merge, and lossless unmerge (`P15-T03`).

Key invariants:
- Suggestions are computed strictly within the active workspace (`current_tenant_id()`):
  - Strong (`score = 0.95`): exact blind-index match on `email` or `phone` across
    distinct unmerged contacts.
  - Weak (`score = 0.60`): identical normalized display name + shared `MessageThread`
    participation.
  - Never auto-merges without human confirmation.
- Merge locks both contacts in ascending ID order (`FOR UPDATE`) to serialize
  concurrent merges, moves identities/consents/messages/threads/CRM links, merges
  `ContactChannelStat`, sets `source.merged_into_id = target.id`, and stores the
  complete before-state snapshot in `ContactMergeJournal.reason_encrypted`.
- Unmerge restores the exact previous state of both contacts, their identities,
  messages, threads, consents, and channel stats from the journal snapshot.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select

from app.core.permissions import get_current_user, has_permission
from app.core.tenant_context import TenantContextError, current_tenant_id
from app.models.audit.action_audit_event import ActionAuditEvent
from app.models.communications.message import ContactChannelStat, MessageThread, UnifiedMessage
from app.models.contacts.core import Contact, ContactConsent, ContactIdentity, ContactMergeJournal
from app.models.crm.entity import CrmEntity
from app.models.managers.contact_manager import ContactUnavailable
from app.types import ActionType

logger = logging.getLogger("app.audit.comms")

AUDIT_ACTION_MERGE = "comms.contact.merge"
AUDIT_ACTION_UNMERGE = "comms.contact.unmerge"
STRONG_MATCH_KINDS = ("email", "phone")


def _require_reconcile_permission(action: ActionType) -> int:
    """Require active workspace and either `comms.message` or `contact` permission."""
    tenant = current_tenant_id()
    if tenant is None:
        raise TenantContextError("Contact reconciliation requires a selected workspace")
    user = get_current_user()
    if not (has_permission(user, "comms.message", action) or has_permission(user, "contact", action)):
        raise PermissionError("Contact reconciliation access denied")
    return tenant


class ContactMergeError(ValueError):
    """Raised when a merge or unmerge request is invalid."""


class ContactMergeConflictError(ContactMergeError):
    """Raised when a contact is already merged or field conflicts are unresolved."""


@dataclass(frozen=True)
class MergeSuggestion:
    source_contact_id: int
    target_contact_id: int
    confidence: str  # "strong" | "weak"
    score: float
    reasons: tuple[str, ...]
    conflicting_fields: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_contact_id": self.source_contact_id,
            "target_contact_id": self.target_contact_id,
            "confidence": self.confidence,
            "score": self.score,
            "reasons": list(self.reasons),
            "conflicting_fields": list(self.conflicting_fields),
            "auto_merge": False,
        }


def _normalize_name(name: str | None) -> str:
    if not name:
        return ""
    return " ".join(name.strip().lower().split())


def _dt_to_iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if isinstance(dt, datetime) else None


def _iso_to_dt(raw: str | None) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    return datetime.fromisoformat(raw)


def _snapshot_contact(contact: Contact) -> dict[str, Any]:
    return {
        "display_name": contact.display_name_encrypted,
        "notes": contact.notes_encrypted,
        "birthday": contact.birthday_encrypted,
        "birthday_has_year": bool(contact.birthday_has_year),
        "anniversary": contact.anniversary_encrypted,
        "anniversary_has_year": bool(contact.anniversary_has_year),
        "preferred_channel": contact.preferred_channel,
        "last_active_channel": contact.last_active_channel,
        "last_active_at": _dt_to_iso(contact.last_active_at),
        "merged_into_id": contact.merged_into_id,
    }


def _restore_contact_fields(contact: Contact, snap: dict[str, Any]) -> None:
    contact.display_name_encrypted = snap["display_name"]
    contact.notes_encrypted = snap.get("notes")
    contact.birthday_encrypted = snap.get("birthday")
    contact.birthday_has_year = bool(snap.get("birthday_has_year", False))
    contact.anniversary_encrypted = snap.get("anniversary")
    contact.anniversary_has_year = bool(snap.get("anniversary_has_year", False))
    contact.preferred_channel = snap.get("preferred_channel")
    contact.last_active_channel = snap.get("last_active_channel")
    contact.last_active_at = _iso_to_dt(snap.get("last_active_at"))
    contact.merged_into_id = snap.get("merged_into_id")


def _snapshot_stats(rows: list[ContactChannelStat]) -> list[dict[str, Any]]:
    return [
        {
            "channel": r.channel,
            "inbound_count": int(r.inbound_count or 0),
            "outbound_count": int(r.outbound_count or 0),
            "last_inbound_at": _dt_to_iso(r.last_inbound_at),
            "last_outbound_at": _dt_to_iso(r.last_outbound_at),
            "last_message_at": _dt_to_iso(r.last_message_at),
        }
        for r in sorted(rows, key=lambda x: x.channel)
    ]


def _detect_conflicting_fields(source: Contact, target: Contact) -> tuple[str, ...]:
    conflicts: list[str] = []
    if (
        source.display_name_encrypted
        and target.display_name_encrypted
        and _normalize_name(source.display_name_encrypted) != _normalize_name(target.display_name_encrypted)
    ):
        conflicts.append("display_name")
    if (
        source.birthday_encrypted
        and target.birthday_encrypted
        and source.birthday_encrypted != target.birthday_encrypted
    ):
        conflicts.append("birthday")
    if (
        source.anniversary_encrypted
        and target.anniversary_encrypted
        and source.anniversary_encrypted != target.anniversary_encrypted
    ):
        conflicts.append("anniversary")
    return tuple(conflicts)


def encode_merge_journal_payload(
    *,
    reason: str,
    snapshot: dict[str, Any],
    unmerged: bool = False,
) -> str:
    payload = {
        "v": 1,
        "reason": reason.strip(),
        "unmerged": unmerged,
        "snapshot": snapshot,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def decode_merge_journal_payload(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {"v": 1, "reason": "", "unmerged": False, "snapshot": {}}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and "snapshot" in parsed:
            return parsed
    except (ValueError, TypeError):
        pass
    return {"v": 1, "reason": str(raw), "unmerged": False, "snapshot": {}}


async def _record_audit(
    session,
    *,
    actor_id: int | None,
    audit_code: str,
    target_contact_id: int,
    source_contact_id: int,
    journal_id: int,
) -> None:
    user = get_current_user()
    resolved_actor = actor_id or (getattr(user, "id", None) if user is not None else None)
    if type(resolved_actor) is int and resolved_actor > 0 and ActionAuditEvent.objects is not None:
        try:
            await ActionAuditEvent.objects.record_success(
                session=session,
                actor_kind="user",
                actor_id=resolved_actor,
                action="crm.contact_update",
                resource_kind="contact",
                resource_id=target_contact_id,
            )
        except Exception:  # noqa: BLE001
            pass
    logger.info(
        "audit_event action=%s target_contact_id=%d source_contact_id=%d journal_id=%d actor_id=%s",
        audit_code,
        target_contact_id,
        source_contact_id,
        journal_id,
        resolved_actor,
    )


async def suggest_contact_merges(
    session,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Compute exact blind-index (strong) and shared-thread display-name (weak) merge candidates."""
    tenant_id = _require_reconcile_permission(ActionType.VIEW)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")

    contacts = list(
        (
            await session.execute(
                select(Contact)
                .where(Contact.tenant_id == tenant_id, Contact.merged_into_id.is_(None))
                .order_by(Contact.id.asc())
            )
        )
        .scalars()
        .all()
    )
    by_id = {c.id: c for c in contacts}
    if len(by_id) < 2:
        return []

    identities = list(
        (
            await session.execute(
                select(ContactIdentity)
                .where(ContactIdentity.tenant_id == tenant_id)
                .order_by(ContactIdentity.id.asc())
            )
        )
        .scalars()
        .all()
    )

    pair_reasons: dict[tuple[int, int], list[str]] = {}
    pair_strong: set[tuple[int, int]] = set()

    # 1. Strong matches: exact blind_index on email or phone across distinct active contacts
    by_kind_index: dict[tuple[str, str], list[int]] = {}
    for ident in identities:
        if ident.kind in STRONG_MATCH_KINDS and ident.contact_id in by_id:
            bucket = by_kind_index.setdefault((ident.kind, ident.blind_index), [])
            if ident.contact_id not in bucket:
                bucket.append(ident.contact_id)

    for (kind, _b_idx), contact_ids in by_kind_index.items():
        if len(contact_ids) < 2:
            continue
        sorted_ids = sorted(contact_ids)
        target_id = sorted_ids[0]
        for source_id in sorted_ids[1:]:
            pair = (source_id, target_id)
            pair_strong.add(pair)
            reasons = pair_reasons.setdefault(pair, [])
            reason_tag = f"exact_{kind}_blind_index"
            if reason_tag not in reasons:
                reasons.append(reason_tag)

    # 2. Weak matches: same normalized display name + shared thread participation
    messages = list(
        (
            await session.execute(
                select(UnifiedMessage).where(
                    UnifiedMessage.tenant_id == tenant_id,
                    UnifiedMessage.contact_id.is_not(None),
                    UnifiedMessage.thread_id.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    thread_contacts: dict[int, set[int]] = {}
    for msg in messages:
        if msg.thread_id is not None and msg.contact_id in by_id:
            thread_contacts.setdefault(msg.thread_id, set()).add(msg.contact_id)

    for _th_id, c_ids in thread_contacts.items():
        if len(c_ids) < 2:
            continue
        sorted_c = sorted(c_ids)
        for i, cid_a in enumerate(sorted_c):
            for cid_b in sorted_c[i + 1 :]:
                ca, cb = by_id[cid_a], by_id[cid_b]
                if _normalize_name(ca.display_name_encrypted) and _normalize_name(
                    ca.display_name_encrypted
                ) == _normalize_name(cb.display_name_encrypted):
                    pair = (cid_b, cid_a)
                    reasons = pair_reasons.setdefault(pair, [])
                    if "same_display_name_shared_thread" not in reasons:
                        reasons.append("same_display_name_shared_thread")

    suggestions: list[MergeSuggestion] = []
    for (source_id, target_id), reasons in sorted(pair_reasons.items()):
        source_c = by_id[source_id]
        target_c = by_id[target_id]
        is_strong = (source_id, target_id) in pair_strong
        suggestions.append(
            MergeSuggestion(
                source_contact_id=source_id,
                target_contact_id=target_id,
                confidence="strong" if is_strong else "weak",
                score=0.95 if is_strong else 0.60,
                reasons=tuple(reasons),
                conflicting_fields=_detect_conflicting_fields(source_c, target_c),
            )
        )
        if len(suggestions) >= limit:
            break

    suggestions.sort(key=lambda s: (-s.score, s.target_contact_id, s.source_contact_id))
    return [s.to_dict() for s in suggestions]


def _snapshot_thread(th: MessageThread) -> dict[str, Any]:
    return {
        "id": th.id,
        "contact_id": th.contact_id,
        "connection_id": th.connection_id,
        "channel": th.channel,
        "thread_key": th.thread_key,
        "message_count": int(th.message_count or 0),
        "unread_count": int(th.unread_count or 0),
        "last_message_at": _dt_to_iso(th.last_message_at),
    }


async def merge_contacts(
    session,
    *,
    source_contact_id: int,
    target_contact_id: int,
    reason: str = "manual_reconciliation",
    field_choices: dict[str, str] | None = None,
    actor_id: int | None = None,
) -> dict[str, Any]:
    """Merge `source_contact_id` into survivor `target_contact_id` with full before-state journal."""
    tenant_id = _require_reconcile_permission(ActionType.UPDATE)
    if (
        type(source_contact_id) is not int
        or type(target_contact_id) is not int
        or source_contact_id <= 0
        or target_contact_id <= 0
    ):
        raise ContactMergeError("source_contact_id and target_contact_id must be positive integers")
    if source_contact_id == target_contact_id:
        raise ContactMergeError("Cannot merge a contact into itself")
    if not isinstance(reason, str) or not reason.strip():
        raise ContactMergeError("Merge reason is required")

    # Lock in ascending ID order to serialize concurrent merges on the same pair
    locked_by_id: dict[int, Contact] = {}
    for cid in sorted((source_contact_id, target_contact_id)):
        row = (
            await session.execute(
                select(Contact)
                .where(Contact.tenant_id == tenant_id, Contact.id == cid)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise ContactUnavailable("Contact unavailable")
        if row.merged_into_id is not None:
            raise ContactMergeConflictError(f"Contact {cid} is already merged")
        locked_by_id[cid] = row

    source = locked_by_id[source_contact_id]
    target = locked_by_id[target_contact_id]

    choices = field_choices or {}
    for k, v in choices.items():
        if k not in ("display_name", "notes", "birthday", "anniversary", "preferred_channel"):
            raise ContactMergeError(f"Unsupported field_choices key: {k!r}")
        if v not in ("source", "target"):
            raise ContactMergeError(f"Invalid choice for {k!r}: must be 'source' or 'target'")

    # Capture full before-state
    source_before = _snapshot_contact(source)
    target_before = _snapshot_contact(target)

    source_identities = list(
        (
            await session.execute(
                select(ContactIdentity)
                .where(
                    ContactIdentity.tenant_id == tenant_id,
                    ContactIdentity.contact_id == source_contact_id,
                )
                .order_by(ContactIdentity.id.asc())
            )
        )
        .scalars()
        .all()
    )
    target_identities = list(
        (
            await session.execute(
                select(ContactIdentity)
                .where(
                    ContactIdentity.tenant_id == tenant_id,
                    ContactIdentity.contact_id == target_contact_id,
                )
                .order_by(ContactIdentity.id.asc())
            )
        )
        .scalars()
        .all()
    )

    source_consents = list(
        (
            await session.execute(
                select(ContactConsent)
                .where(
                    ContactConsent.tenant_id == tenant_id,
                    ContactConsent.contact_id == source_contact_id,
                )
                .order_by(ContactConsent.id.asc())
            )
        )
        .scalars()
        .all()
    )
    source_messages = list(
        (
            await session.execute(
                select(UnifiedMessage)
                .where(
                    UnifiedMessage.tenant_id == tenant_id,
                    UnifiedMessage.contact_id == source_contact_id,
                )
                .order_by(UnifiedMessage.id.asc())
            )
        )
        .scalars()
        .all()
    )
    target_messages = list(
        (
            await session.execute(
                select(UnifiedMessage)
                .where(
                    UnifiedMessage.tenant_id == tenant_id,
                    UnifiedMessage.contact_id == target_contact_id,
                )
                .order_by(UnifiedMessage.id.asc())
            )
        )
        .scalars()
        .all()
    )
    source_threads = list(
        (
            await session.execute(
                select(MessageThread)
                .where(
                    MessageThread.tenant_id == tenant_id,
                    MessageThread.contact_id == source_contact_id,
                )
                .order_by(MessageThread.id.asc())
            )
        )
        .scalars()
        .all()
    )
    target_threads = list(
        (
            await session.execute(
                select(MessageThread)
                .where(
                    MessageThread.tenant_id == tenant_id,
                    MessageThread.contact_id == target_contact_id,
                )
                .order_by(MessageThread.id.asc())
            )
        )
        .scalars()
        .all()
    )
    known_thread_ids = {t.id for t in source_threads} | {t.id for t in target_threads}
    extra_thread_ids = {
        m.thread_id
        for m in [*source_messages, *target_messages]
        if m.thread_id is not None and m.thread_id not in known_thread_ids
    }
    extra_threads: list[MessageThread] = []
    if extra_thread_ids:
        extra_threads = list(
            (
                await session.execute(
                    select(MessageThread)
                    .where(
                        MessageThread.tenant_id == tenant_id,
                        MessageThread.id.in_(extra_thread_ids),
                    )
                    .order_by(MessageThread.id.asc())
                )
            )
            .scalars()
            .all()
        )

    source_crm_entities = list(
        (
            await session.execute(
                select(CrmEntity)
                .where(
                    CrmEntity.tenant_id == tenant_id,
                    CrmEntity.contact_id == source_contact_id,
                )
                .order_by(CrmEntity.id.asc())
            )
        )
        .scalars()
        .all()
    )
    source_stats = list(
        (
            await session.execute(
                select(ContactChannelStat)
                .where(
                    ContactChannelStat.tenant_id == tenant_id,
                    ContactChannelStat.contact_id == source_contact_id,
                )
                .order_by(ContactChannelStat.id.asc())
            )
        )
        .scalars()
        .all()
    )
    target_stats = list(
        (
            await session.execute(
                select(ContactChannelStat)
                .where(
                    ContactChannelStat.tenant_id == tenant_id,
                    ContactChannelStat.contact_id == target_contact_id,
                )
                .order_by(ContactChannelStat.id.asc())
            )
        )
        .scalars()
        .all()
    )

    # Snapshot messages and threads before mutation (B-9)
    all_tracked_threads: dict[int, MessageThread] = {
        th.id: th for th in [*source_threads, *target_threads, *extra_threads]
    }
    threads_before = [_snapshot_thread(th) for th in sorted(all_tracked_threads.values(), key=lambda x: x.id)]
    messages_before = [
        {
            "id": m.id,
            "contact_id": m.contact_id,
            "thread_id": m.thread_id,
        }
        for m in source_messages
    ]

    # Move identities that do not collide with an identical (kind, blind_index, connection_id) on target
    target_ident_keys = {(i.kind, i.blind_index, i.connection_id) for i in target_identities}
    moved_identity_ids: list[int] = []
    for ident in source_identities:
        key = (ident.kind, ident.blind_index, ident.connection_id)
        if key not in target_ident_keys:
            ident.contact_id = target_contact_id
            target_ident_keys.add(key)
            moved_identity_ids.append(ident.id)

    moved_consent_ids = [c.id for c in source_consents]
    for consent in source_consents:
        consent.contact_id = target_contact_id

    # Enforce per-connection thread invariant (B-9):
    # Threads are uniquely scoped by (tenant_id, connection_id, channel, thread_key).
    # Merging across different connection_ids keeps each connection's thread separate
    # and re-points contact_id to target_contact_id.
    threads_by_conn_key: dict[tuple[int | None, str, str], MessageThread] = {
        (th.connection_id, th.channel, th.thread_key): th for th in target_threads
    }
    moved_thread_ids: list[int] = []
    created_thread_ids: list[int] = []
    reassigned_threads: set[int] = set()

    for th in source_threads:
        th.contact_id = target_contact_id
        moved_thread_ids.append(th.id)
        threads_by_conn_key.setdefault((th.connection_id, th.channel, th.thread_key), th)

    for th in extra_threads:
        if th.contact_id is None:
            th.contact_id = target_contact_id
            moved_thread_ids.append(th.id)
        threads_by_conn_key.setdefault((th.connection_id, th.channel, th.thread_key), th)

    moved_message_ids = [m.id for m in source_messages]
    for msg in source_messages:
        msg.contact_id = target_contact_id
        if msg.thread_id is not None:
            cur_th = all_tracked_threads.get(msg.thread_id)
            if cur_th is not None and cur_th.connection_id != msg.connection_id:
                # Re-bind message to a thread matching its own connection_id (B-9)
                conn_key = (msg.connection_id, msg.channel, cur_th.thread_key)
                dest_th = threads_by_conn_key.get(conn_key)
                if dest_th is None:
                    dest_th = MessageThread(
                        tenant_id=tenant_id,
                        connection_id=msg.connection_id,
                        contact_id=target_contact_id,
                        channel=msg.channel,
                        thread_key=cur_th.thread_key,
                        subject_encrypted=cur_th.subject_encrypted,
                        participants_encrypted=cur_th.participants_encrypted,
                        last_message_at=msg.sent_at,
                        message_count=0,
                        unread_count=0,
                        is_archived=bool(cur_th.is_archived),
                    )
                    session.add(dest_th)
                    await session.flush()
                    threads_by_conn_key[conn_key] = dest_th
                    all_tracked_threads[dest_th.id] = dest_th
                    created_thread_ids.append(dest_th.id)
                    moved_thread_ids.append(dest_th.id)
                reassigned_threads.add(cur_th.id)
                reassigned_threads.add(dest_th.id)
                msg.thread_id = dest_th.id

    if reassigned_threads:
        all_known_msgs = [*source_messages, *target_messages]
        for th_id in reassigned_threads:
            th_obj = all_tracked_threads.get(th_id)
            if th_obj is None:
                continue
            th_msgs = [m for m in all_known_msgs if m.thread_id == th_id and not m.is_hidden]
            th_obj.message_count = len(th_msgs)
            th_obj.unread_count = sum(1 for m in th_msgs if not m.is_read and m.direction == "inbound")
            th_obj.last_message_at = max((m.sent_at for m in th_msgs), default=th_obj.last_message_at)

    moved_crm_entity_ids = [e.id for e in source_crm_entities]
    for entity in source_crm_entities:
        entity.contact_id = target_contact_id

    # Snapshot stats before modifying
    source_stats_snap = _snapshot_stats(source_stats)
    target_stats_snap = _snapshot_stats(target_stats)

    # Merge ContactChannelStat onto target
    target_stat_by_channel = {s.channel: s for s in target_stats}
    for s_stat in source_stats:
        t_stat = target_stat_by_channel.get(s_stat.channel)
        if t_stat is None:
            s_stat.contact_id = target_contact_id
            target_stat_by_channel[s_stat.channel] = s_stat
        else:
            t_stat.inbound_count = int(t_stat.inbound_count or 0) + int(s_stat.inbound_count or 0)
            t_stat.outbound_count = int(t_stat.outbound_count or 0) + int(s_stat.outbound_count or 0)
            if s_stat.last_inbound_at and (
                t_stat.last_inbound_at is None or s_stat.last_inbound_at > t_stat.last_inbound_at
            ):
                t_stat.last_inbound_at = s_stat.last_inbound_at
            if s_stat.last_outbound_at and (
                t_stat.last_outbound_at is None or s_stat.last_outbound_at > t_stat.last_outbound_at
            ):
                t_stat.last_outbound_at = s_stat.last_outbound_at
            if s_stat.last_message_at and (
                t_stat.last_message_at is None or s_stat.last_message_at > t_stat.last_message_at
            ):
                t_stat.last_message_at = s_stat.last_message_at
            await session.execute(delete(ContactChannelStat).where(ContactChannelStat.id == s_stat.id))

    # Apply field resolution choices on survivor target contact
    if choices.get("display_name") == "source" and source.display_name_encrypted:
        target.display_name_encrypted = source.display_name_encrypted
    if choices.get("notes") == "source" or (not target.notes_encrypted and source.notes_encrypted):
        target.notes_encrypted = source.notes_encrypted
    if choices.get("birthday") == "source" or (not target.birthday_encrypted and source.birthday_encrypted):
        target.birthday_encrypted = source.birthday_encrypted
        target.birthday_has_year = source.birthday_has_year
    if choices.get("anniversary") == "source" or (not target.anniversary_encrypted and source.anniversary_encrypted):
        target.anniversary_encrypted = source.anniversary_encrypted
        target.anniversary_has_year = source.anniversary_has_year
    if choices.get("preferred_channel") == "source" or (
        not target.preferred_channel and source.preferred_channel
    ):
        target.preferred_channel = source.preferred_channel
    if source.last_active_at and (target.last_active_at is None or source.last_active_at > target.last_active_at):
        target.last_active_at = source.last_active_at
        target.last_active_channel = source.last_active_channel

    source.merged_into_id = target_contact_id

    snapshot = {
        "source_before": source_before,
        "target_before": target_before,
        "moved_identity_ids": moved_identity_ids,
        "moved_consent_ids": moved_consent_ids,
        "moved_message_ids": moved_message_ids,
        "moved_thread_ids": moved_thread_ids,
        "created_thread_ids": created_thread_ids,
        "messages_before": messages_before,
        "threads_before": threads_before,
        "moved_crm_entity_ids": moved_crm_entity_ids,
        "source_stats_before": source_stats_snap,
        "target_stats_before": target_stats_snap,
    }

    journal = ContactMergeJournal(
        tenant_id=tenant_id,
        source_contact_id=source_contact_id,
        target_contact_id=target_contact_id,
        reason_encrypted=encode_merge_journal_payload(reason=reason, snapshot=snapshot, unmerged=False),
    )
    session.add(journal)
    await session.flush()

    await _record_audit(
        session,
        actor_id=actor_id,
        audit_code=AUDIT_ACTION_MERGE,
        target_contact_id=target_contact_id,
        source_contact_id=source_contact_id,
        journal_id=journal.id,
    )

    return {
        "journal_id": journal.id,
        "source_contact_id": source_contact_id,
        "target_contact_id": target_contact_id,
        "moved_identities": len(moved_identity_ids),
        "moved_messages": len(moved_message_ids),
        "moved_threads": len(moved_thread_ids),
        "audit_action": AUDIT_ACTION_MERGE,
    }


async def unmerge_contacts(
    session,
    *,
    journal_id: int,
    actor_id: int | None = None,
) -> dict[str, Any]:
    """Reverse a prior merge by `journal_id`, restoring the exact pre-merge state."""
    tenant_id = _require_reconcile_permission(ActionType.UPDATE)
    if type(journal_id) is not int or journal_id <= 0:
        raise ContactMergeError("journal_id must be a positive integer")

    journal = (
        await session.execute(
            select(ContactMergeJournal).where(
                ContactMergeJournal.tenant_id == tenant_id,
                ContactMergeJournal.id == journal_id,
            )
        )
    ).scalar_one_or_none()
    if journal is None:
        raise ContactUnavailable("Merge journal entry unavailable")

    payload = decode_merge_journal_payload(journal.reason_encrypted)
    if payload.get("unmerged"):
        raise ContactMergeConflictError("Merge journal entry has already been unmerged")

    snapshot = payload.get("snapshot") or {}
    if not snapshot or "source_before" not in snapshot or "target_before" not in snapshot:
        raise ContactMergeError("Merge journal entry does not contain a reversible snapshot")

    source_id = journal.source_contact_id
    target_id = journal.target_contact_id

    locked_by_id: dict[int, Contact] = {}
    for cid in sorted((source_id, target_id)):
        row = (
            await session.execute(
                select(Contact)
                .where(Contact.tenant_id == tenant_id, Contact.id == cid)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise ContactUnavailable("Contact unavailable")
        locked_by_id[cid] = row

    source = locked_by_id[source_id]
    target = locked_by_id[target_id]
    if source.merged_into_id != target_id:
        raise ContactMergeConflictError("Source contact is not currently merged into target contact")

    _restore_contact_fields(source, snapshot["source_before"])
    _restore_contact_fields(target, snapshot["target_before"])

    moved_identity_ids = set(snapshot.get("moved_identity_ids") or [])
    if moved_identity_ids:
        ident_rows = (
            (
                await session.execute(
                    select(ContactIdentity).where(
                        ContactIdentity.tenant_id == tenant_id,
                        ContactIdentity.id.in_(moved_identity_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        for ident in ident_rows:
            ident.contact_id = source_id

    moved_consent_ids = set(snapshot.get("moved_consent_ids") or [])
    if moved_consent_ids:
        consent_rows = (
            (
                await session.execute(
                    select(ContactConsent).where(
                        ContactConsent.tenant_id == tenant_id,
                        ContactConsent.id.in_(moved_consent_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        for consent in consent_rows:
            consent.contact_id = source_id

    moved_message_ids = set(snapshot.get("moved_message_ids") or [])
    messages_before_by_id = {
        int(entry["id"]): entry
        for entry in (snapshot.get("messages_before") or [])
        if isinstance(entry, dict) and "id" in entry
    }
    if moved_message_ids:
        msg_rows = (
            (
                await session.execute(
                    select(UnifiedMessage).where(
                        UnifiedMessage.tenant_id == tenant_id,
                        UnifiedMessage.id.in_(moved_message_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        for msg in msg_rows:
            before_entry = messages_before_by_id.get(msg.id)
            if before_entry is not None:
                msg.contact_id = before_entry.get("contact_id", source_id)
                if "thread_id" in before_entry:
                    msg.thread_id = before_entry["thread_id"]
            else:
                msg.contact_id = source_id

    moved_thread_ids = set(snapshot.get("moved_thread_ids") or [])
    threads_before_by_id = {
        int(entry["id"]): entry
        for entry in (snapshot.get("threads_before") or [])
        if isinstance(entry, dict) and "id" in entry
    }
    thread_ids_to_restore = moved_thread_ids | set(threads_before_by_id.keys())
    if thread_ids_to_restore:
        th_rows = (
            (
                await session.execute(
                    select(MessageThread).where(
                        MessageThread.tenant_id == tenant_id,
                        MessageThread.id.in_(thread_ids_to_restore),
                    )
                )
            )
            .scalars()
            .all()
        )
        for th in th_rows:
            before_th = threads_before_by_id.get(th.id)
            if before_th is not None:
                th.contact_id = before_th.get("contact_id")
                th.message_count = int(before_th.get("message_count", th.message_count or 0))
                th.unread_count = int(before_th.get("unread_count", th.unread_count or 0))
                if "last_message_at" in before_th:
                    th.last_message_at = _iso_to_dt(before_th.get("last_message_at"))
            elif th.id in moved_thread_ids:
                th.contact_id = source_id

    created_thread_ids = set(snapshot.get("created_thread_ids") or [])
    if created_thread_ids:
        await session.execute(
            delete(MessageThread).where(
                MessageThread.tenant_id == tenant_id,
                MessageThread.id.in_(created_thread_ids),
            )
        )

    moved_crm_ids = set(snapshot.get("moved_crm_entity_ids") or [])
    if moved_crm_ids:
        crm_rows = (
            (
                await session.execute(
                    select(CrmEntity).where(
                        CrmEntity.tenant_id == tenant_id,
                        CrmEntity.id.in_(moved_crm_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        for entity in crm_rows:
            entity.contact_id = source_id

    # Restore exact ContactChannelStat rows for both contacts
    await session.execute(
        delete(ContactChannelStat).where(
            ContactChannelStat.tenant_id == tenant_id,
            ContactChannelStat.contact_id.in_((source_id, target_id)),
        )
    )
    for owner_cid, stat_snaps in (
        (source_id, snapshot.get("source_stats_before") or []),
        (target_id, snapshot.get("target_stats_before") or []),
    ):
        for st in stat_snaps:
            session.add(
                ContactChannelStat(
                    tenant_id=tenant_id,
                    contact_id=owner_cid,
                    channel=st["channel"],
                    inbound_count=int(st.get("inbound_count") or 0),
                    outbound_count=int(st.get("outbound_count") or 0),
                    last_inbound_at=_iso_to_dt(st.get("last_inbound_at")),
                    last_outbound_at=_iso_to_dt(st.get("last_outbound_at")),
                    last_message_at=_iso_to_dt(st.get("last_message_at")),
                )
            )

    journal.reason_encrypted = encode_merge_journal_payload(
        reason=payload.get("reason") or "manual_reconciliation",
        snapshot=snapshot,
        unmerged=True,
    )
    await session.flush()

    await _record_audit(
        session,
        actor_id=actor_id,
        audit_code=AUDIT_ACTION_UNMERGE,
        target_contact_id=target_id,
        source_contact_id=source_id,
        journal_id=journal.id,
    )

    return {
        "journal_id": journal.id,
        "source_contact_id": source_id,
        "target_contact_id": target_id,
        "restored_identities": len(moved_identity_ids),
        "restored_messages": len(moved_message_ids),
        "restored_threads": len(moved_thread_ids),
        "audit_action": AUDIT_ACTION_UNMERGE,
    }
