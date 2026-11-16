"""Bitrix24 read-only incremental synchronization of contacts, companies, leads, and deals (P14-T02).

Key invariants:
- Strictly read-only against Bitrix24 (`crm.contact.list`, `crm.company.list`,
  `crm.lead.list`, `crm.deal.list` via `batch` or single REST calls).
- Incremental `filter[>DATE_MODIFY]` watermark per `(connection_id, entity_kind)`
  with `order[ID]=ASC` and `start` pagination. Watermark advances ONLY after
  each page is flushed/committed.
- Maps contacts, companies, and leads to `Contact` + `ContactIdentity` (`bitrix24_contact`,
  `bitrix24_company`, `bitrix24_lead`, `email`, `phone`) scoped to `(tenant_id, connection_id)`.
  Never auto-merges contacts across connections or within a portal (R-16).
- Personal data and allowlisted custom fields (`UF_CRM_*`) are stored in Fernet
  `EncryptedText` columns (`title_encrypted`, `personal_fields_encrypted`,
  `custom_fields_encrypted`) and never logged.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select

from app.core.database import async_session_maker
from app.core.pii import PIIError
from app.core.tenant_context import current_tenant_id, is_bypass
from app.integrations.bitrix24.client import (
    Bitrix24Error,
    Bitrix24RateLimitError,
    Bitrix24Service,
    bitrix24_service,
)
from app.models.audit.action_audit_event import ActionAuditEvent
from app.models.contacts.core import Contact, ContactIdentity
from app.models.crm.entity import CRM_ENTITY_KINDS, CrmEntity
from app.models.integrations.connection import WorkspaceConnection
from app.services.contacts.identity import identity_index, identity_values

SYNC_INTERVAL_SECONDS = 15 * 60  # 15 minutes
PAGE_SIZE = 50
DEFAULT_BIRTHDAY_FIELD = "BIRTHDATE"
DEFAULT_ANNIVERSARY_FIELD = "UF_CRM_ANNIVERSARY"

ENTITY_LIST_METHODS: dict[str, str] = {
    "contact": "crm.contact.list",
    "company": "crm.company.list",
    "lead": "crm.lead.list",
    "deal": "crm.deal.list",
}

ENTITY_IDENTITY_KINDS: dict[str, str] = {
    "contact": "bitrix24_contact",
    "company": "bitrix24_company",
    "lead": "bitrix24_lead",
}

_DATE_PREFIX_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_MONTH_DAY_RE = re.compile(r"^--(\d{2})-(\d{2})$")


def parse_crm_date_field(raw: Any) -> tuple[str | None, bool]:
    """Parse Bitrix24 ISO date/datetime or `--MM-DD` into `(normalized_date, has_year)`."""
    if not isinstance(raw, str):
        return None, False
    cleaned = raw.strip()
    if not cleaned:
        return None, False
    m_md = _MONTH_DAY_RE.match(cleaned)
    if m_md:
        month, day = int(m_md.group(1)), int(m_md.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"--{month:02d}-{day:02d}", False
        return None, False
    m_ymd = _DATE_PREFIX_RE.match(cleaned)
    if m_ymd:
        year, month, day = int(m_ymd.group(1)), int(m_ymd.group(2)), int(m_ymd.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            if year <= 1900:
                return f"--{month:02d}-{day:02d}", False
            return f"{year:04d}-{month:02d}-{day:02d}", True
    return None, False


def parse_bitrix_timestamp(raw: Any) -> datetime | None:
    """Parse Bitrix24 `DATE_MODIFY` timestamp into timezone-aware UTC datetime."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def extract_multifield_values(raw: Any) -> list[str]:
    """Extract string values from Bitrix24 `EMAIL` / `PHONE` multifield arrays."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            val = item.get("VALUE") or item.get("value")
            if isinstance(val, str) and val.strip():
                out.append(val.strip())
        elif isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


def extract_allowlisted_custom_fields(
    record: dict[str, Any],
    *,
    allowlist: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Extract allowlisted `UF_CRM_*` fields from a Bitrix24 entity payload."""
    allowed = {str(k).strip().upper() for k in (allowlist or ()) if str(k).strip()}
    custom: dict[str, Any] = {}
    for key, value in record.items():
        key_str = str(key).strip()
        if not key_str.upper().startswith("UF_CRM_"):
            continue
        if allowed and key_str.upper() not in allowed:
            continue
        if value is None or value == "" or value == []:
            continue
        custom[key_str] = value
    return custom


def build_entity_title(entity_kind: str, record: dict[str, Any], external_id: str) -> str:
    """Construct human-readable entity title/display name from Bitrix24 payload."""
    if entity_kind == "contact":
        parts = [
            str(record.get("LAST_NAME") or "").strip(),
            str(record.get("NAME") or "").strip(),
            str(record.get("SECOND_NAME") or "").strip(),
        ]
        full = " ".join(p for p in parts if p).strip()
        if full:
            return full[:500]
    title = str(record.get("TITLE") or record.get("NAME") or "").strip()
    if title:
        return title[:500]
    return f"Bitrix24 {entity_kind.capitalize()} #{external_id}"


def get_field_mapping_config(connection: WorkspaceConnection) -> dict[str, Any]:
    """Return field mapping config for a Bitrix24 connection with safe defaults."""
    cfg = dict(connection.config or {})
    mapping = cfg.get("field_mapping") if isinstance(cfg.get("field_mapping"), dict) else {}
    birthday_field = str(mapping.get("birthday_field") or DEFAULT_BIRTHDAY_FIELD).strip() or DEFAULT_BIRTHDAY_FIELD
    anniversary_field = (
        str(mapping.get("anniversary_field") or DEFAULT_ANNIVERSARY_FIELD).strip() or DEFAULT_ANNIVERSARY_FIELD
    )
    raw_allowlist = mapping.get("custom_fields_allowlist")
    if isinstance(raw_allowlist, list):
        allowlist = [str(x).strip() for x in raw_allowlist if str(x).strip()]
    else:
        allowlist = []
    if anniversary_field.upper().startswith("UF_CRM_") and anniversary_field not in allowlist:
        allowlist.append(anniversary_field)
    if birthday_field.upper().startswith("UF_CRM_") and birthday_field not in allowlist:
        allowlist.append(birthday_field)
    return {
        "birthday_field": birthday_field,
        "anniversary_field": anniversary_field,
        "custom_fields_allowlist": allowlist,
    }


class BitrixSyncService:
    """Incremental read-only synchronization service for Bitrix24 portals."""

    def __init__(self, *, client: Bitrix24Service = bitrix24_service) -> None:
        self._client = client

    async def _upsert_contact_and_identities(
        self,
        session,
        *,
        tenant_id: int,
        connection_id: int,
        entity_kind: str,
        external_id: str,
        title: str,
        birthday: str | None,
        birthday_has_year: bool,
        anniversary: str | None,
        anniversary_has_year: bool,
        notes: str | None,
        emails: list[str],
        phones: list[str],
        existing_contact_id: int | None,
    ) -> tuple[int | None, list[str], list[str]]:
        """Map a CRM contact/company/lead to `Contact` + `ContactIdentity` without auto-merging."""
        identity_kind = ENTITY_IDENTITY_KINDS.get(entity_kind)
        if identity_kind is None:
            return None, [], []

        try:
            native_vals = identity_values(identity_kind, external_id)
            norm_native = native_vals["value_encrypted"]
            raw_native = external_id
            native_bidx = native_vals["blind_index"]
        except (PIIError, ValueError):
            return None, [], []

        contact_row: Contact | None = None
        if existing_contact_id is not None:
            contact_row = (
                await session.execute(
                    select(Contact).where(
                        Contact.id == existing_contact_id,
                        Contact.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()

        native_ident = (
            await session.execute(
                select(ContactIdentity).where(
                    ContactIdentity.tenant_id == tenant_id,
                    ContactIdentity.connection_id == connection_id,
                    ContactIdentity.kind == identity_kind,
                    ContactIdentity.blind_index == native_bidx,
                )
            )
        ).scalar_one_or_none()

        if contact_row is None and native_ident is not None:
            contact_row = (
                await session.execute(
                    select(Contact).where(
                        Contact.id == native_ident.contact_id,
                        Contact.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()

        if contact_row is not None and contact_row.merged_into_id is not None:
            # Never overwrite a contact that was merged into another record; keep reference stable.
            return contact_row.id, [], []

        if contact_row is None:
            contact_row = Contact(
                tenant_id=tenant_id,
                display_name_encrypted=title,
                notes_encrypted=notes if notes else None,
                birthday_encrypted=birthday,
                birthday_has_year=birthday_has_year,
                anniversary_encrypted=anniversary,
                anniversary_has_year=anniversary_has_year,
                preferred_channel="bitrix24",
            )
            session.add(contact_row)
            await session.flush()
        else:
            contact_row.display_name_encrypted = title
            if notes:
                contact_row.notes_encrypted = notes
            if birthday is not None:
                contact_row.birthday_encrypted = birthday
                contact_row.birthday_has_year = birthday_has_year
            if anniversary is not None:
                contact_row.anniversary_encrypted = anniversary
                contact_row.anniversary_has_year = anniversary_has_year

        if native_ident is None:
            native_ident = ContactIdentity(
                tenant_id=tenant_id,
                contact_id=contact_row.id,
                kind=identity_kind,
                connection_id=connection_id,
                value_encrypted=norm_native,
                blind_index=native_bidx,
                verified=True,
                source_native_id_encrypted=raw_native,
            )
            session.add(native_ident)
            await session.flush()

        email_indexes: list[str] = []
        for raw_email in emails:
            try:
                email_vals = identity_values("email", raw_email)
                stored_email = email_vals["value_encrypted"]
                email_bidx = email_vals["blind_index"]
            except (PIIError, ValueError):
                continue
            email_indexes.append(email_bidx)
            existing_email_ident = (
                await session.execute(
                    select(ContactIdentity).where(
                        ContactIdentity.tenant_id == tenant_id,
                        ContactIdentity.connection_id == connection_id,
                        ContactIdentity.kind == "email",
                        ContactIdentity.blind_index == email_bidx,
                    )
                )
            ).scalar_one_or_none()
            if existing_email_ident is None:
                session.add(
                    ContactIdentity(
                        tenant_id=tenant_id,
                        contact_id=contact_row.id,
                        kind="email",
                        connection_id=connection_id,
                        value_encrypted=stored_email,
                        blind_index=email_bidx,
                        verified=False,
                        source_native_id_encrypted=raw_native,
                    )
                )
                await session.flush()

        phone_indexes: list[str] = []
        for raw_phone in phones:
            try:
                phone_vals = identity_values("phone", raw_phone)
                stored_phone = phone_vals["value_encrypted"]
                phone_bidx = phone_vals["blind_index"]
            except (PIIError, ValueError):
                continue
            phone_indexes.append(phone_bidx)
            existing_phone_ident = (
                await session.execute(
                    select(ContactIdentity).where(
                        ContactIdentity.tenant_id == tenant_id,
                        ContactIdentity.connection_id == connection_id,
                        ContactIdentity.kind == "phone",
                        ContactIdentity.blind_index == phone_bidx,
                    )
                )
            ).scalar_one_or_none()
            if existing_phone_ident is None:
                session.add(
                    ContactIdentity(
                        tenant_id=tenant_id,
                        contact_id=contact_row.id,
                        kind="phone",
                        connection_id=connection_id,
                        value_encrypted=stored_phone,
                        blind_index=phone_bidx,
                        verified=False,
                        source_native_id_encrypted=raw_native,
                    )
                )
                await session.flush()

        return contact_row.id, email_indexes, phone_indexes

    async def _resolve_contact_id_for_deal(
        self,
        session,
        *,
        tenant_id: int,
        connection_id: int,
        contact_external_id: str | None,
    ) -> int | None:
        if not contact_external_id:
            return None
        try:
            bidx = identity_index("bitrix24_contact", contact_external_id)
        except (PIIError, ValueError):
            return None
        ident = (
            await session.execute(
                select(ContactIdentity).where(
                    ContactIdentity.tenant_id == tenant_id,
                    ContactIdentity.connection_id == connection_id,
                    ContactIdentity.kind == "bitrix24_contact",
                    ContactIdentity.blind_index == bidx,
                )
            )
        ).scalar_one_or_none()
        return ident.contact_id if ident is not None else None

    async def _upsert_entity_record(
        self,
        session,
        *,
        tenant_id: int,
        connection: WorkspaceConnection,
        entity_kind: str,
        record: dict[str, Any],
        synced_at: datetime,
    ) -> CrmEntity | None:
        raw_id = record.get("ID") or record.get("id")
        if raw_id is None:
            return None
        external_id = str(raw_id).strip()
        if not external_id or not external_id.isdigit() or int(external_id) <= 0:
            return None
        external_id = str(int(external_id))

        mapping_cfg = get_field_mapping_config(connection)
        birthday_field = mapping_cfg["birthday_field"]
        anniversary_field = mapping_cfg["anniversary_field"]
        allowlist = mapping_cfg["custom_fields_allowlist"]

        title = build_entity_title(entity_kind, record, external_id)
        emails = extract_multifield_values(record.get("EMAIL"))
        phones = extract_multifield_values(record.get("PHONE"))

        birthday, birthday_has_year = parse_crm_date_field(record.get(birthday_field))
        anniversary, anniversary_has_year = parse_crm_date_field(record.get(anniversary_field))
        comments = str(record.get("COMMENTS") or "").strip() or None
        post = str(record.get("POST") or "").strip() or None

        existing = (
            await session.execute(
                select(CrmEntity).where(
                    CrmEntity.tenant_id == tenant_id,
                    CrmEntity.connection_id == connection.id,
                    CrmEntity.entity_kind == entity_kind,
                    CrmEntity.external_id == external_id,
                )
            )
        ).scalar_one_or_none()

        contact_id = existing.contact_id if existing is not None else None
        email_indexes: list[str] = []
        phone_indexes: list[str] = []

        if entity_kind in ENTITY_IDENTITY_KINDS:
            contact_id, email_indexes, phone_indexes = await self._upsert_contact_and_identities(
                session,
                tenant_id=tenant_id,
                connection_id=connection.id,
                entity_kind=entity_kind,
                external_id=external_id,
                title=title,
                birthday=birthday if entity_kind == "contact" else None,
                birthday_has_year=birthday_has_year if entity_kind == "contact" else False,
                anniversary=anniversary if entity_kind == "contact" else None,
                anniversary_has_year=anniversary_has_year if entity_kind == "contact" else False,
                notes=comments,
                emails=emails,
                phones=phones,
                existing_contact_id=contact_id,
            )
        elif entity_kind == "deal":
            deal_contact_ext = str(record.get("CONTACT_ID") or "").strip()
            if deal_contact_ext and deal_contact_ext != "0":
                resolved = await self._resolve_contact_id_for_deal(
                    session,
                    tenant_id=tenant_id,
                    connection_id=connection.id,
                    contact_external_id=deal_contact_ext,
                )
                if resolved is not None:
                    contact_id = resolved

        personal_payload: dict[str, Any] = {}
        if emails:
            personal_payload["emails"] = emails
        if phones:
            personal_payload["phones"] = phones
        if email_indexes:
            personal_payload["email_blind_indexes"] = email_indexes
        if phone_indexes:
            personal_payload["phone_blind_indexes"] = phone_indexes
        if birthday:
            personal_payload["birthday"] = birthday
            personal_payload["birthday_has_year"] = birthday_has_year
        if anniversary:
            personal_payload["anniversary"] = anniversary
            personal_payload["anniversary_has_year"] = anniversary_has_year
        if post:
            personal_payload["post"] = post
        if comments:
            personal_payload["comments"] = comments

        custom_payload = extract_allowlisted_custom_fields(record, allowlist=allowlist)

        company_ext = str(record.get("COMPANY_ID") or "").strip()
        if not company_ext or company_ext == "0":
            company_ext = None

        stage_code = str(record.get("STAGE_ID") or "").strip() or None
        status_code = str(record.get("STATUS_ID") or "").strip() or None
        currency_code = str(record.get("CURRENCY_ID") or "").strip()[:12] or None
        assigned_by = str(record.get("ASSIGNED_BY_ID") or "").strip() or None

        opportunity_amount: float | None = None
        raw_amount = record.get("OPPORTUNITY")
        if raw_amount is not None and str(raw_amount).strip() != "":
            try:
                opportunity_amount = float(Decimal(str(raw_amount).strip()))
            except (InvalidOperation, ValueError):
                opportunity_amount = None

        closed_flag = str(record.get("CLOSED") or "").strip().upper() == "Y"
        modified_at = parse_bitrix_timestamp(record.get("DATE_MODIFY"))

        personal_json = json.dumps(personal_payload, ensure_ascii=False, sort_keys=True) if personal_payload else None
        custom_json = json.dumps(custom_payload, ensure_ascii=False, sort_keys=True) if custom_payload else None

        if existing is None:
            row = CrmEntity(
                tenant_id=tenant_id,
                connection_id=connection.id,
                provider="bitrix24",
                entity_kind=entity_kind,
                external_id=external_id,
                contact_id=contact_id,
                company_external_id=company_ext,
                stage_code=stage_code,
                status_code=status_code,
                currency_code=currency_code,
                opportunity_amount=opportunity_amount,
                assigned_by_external_id=assigned_by,
                is_closed=closed_flag,
                is_deleted_remote=False,
                deleted_at=None,
                source_modified_at=modified_at,
                last_synced_at=synced_at,
                title_encrypted=title,
                personal_fields_encrypted=personal_json,
                custom_fields_encrypted=custom_json,
            )
            session.add(row)
            await session.flush()
            return row

        existing.contact_id = contact_id
        existing.company_external_id = company_ext
        existing.stage_code = stage_code
        existing.status_code = status_code
        existing.currency_code = currency_code
        existing.opportunity_amount = opportunity_amount
        existing.assigned_by_external_id = assigned_by
        existing.is_closed = closed_flag
        existing.is_deleted_remote = False
        existing.deleted_at = None
        if modified_at is not None:
            existing.source_modified_at = modified_at
        existing.last_synced_at = synced_at
        existing.title_encrypted = title
        existing.personal_fields_encrypted = personal_json
        existing.custom_fields_encrypted = custom_json
        await session.flush()
        return existing

    async def sync_connection(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        connection_id: int,
        entity_kinds: tuple[str, ...] = CRM_ENTITY_KINDS,
        max_pages_per_kind: int = 20,
        session=None,
    ) -> dict[str, Any]:
        """Run incremental watermark-based synchronization for a Bitrix24 connection."""
        if is_bypass() or current_tenant_id() != tenant_id:
            raise Bitrix24Error("connection_denied", status_code=403)

        if session is None:
            async with async_session_maker() as owned, owned.begin():
                return await self.sync_connection(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    connection_id=connection_id,
                    entity_kinds=entity_kinds,
                    max_pages_per_kind=max_pages_per_kind,
                    session=owned,
                )

        conn = (
            await session.execute(
                select(WorkspaceConnection)
                .where(
                    WorkspaceConnection.id == connection_id,
                    WorkspaceConnection.tenant_id == tenant_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if conn is None or not conn.is_active or conn.provider_id != "bitrix24":
            raise Bitrix24Error("connection_not_found", status_code=404)

        cfg = dict(conn.config or {})
        sync_state = dict(cfg.get("sync_state") or {})
        watermarks = dict(sync_state.get("watermarks") or {})
        cursors = dict(sync_state.get("cursors") or {})
        counts_by_kind: dict[str, int] = {k: 0 for k in entity_kinds if k in ENTITY_LIST_METHODS}
        deferred = False
        deferral_reason: str | None = None
        now = datetime.now(timezone.utc)

        for kind in entity_kinds:
            method_name = ENTITY_LIST_METHODS.get(kind)
            if not method_name:
                continue

            watermark = str(watermarks.get(kind) or "").strip()
            start_offset = int(cursors.get(kind) or 0)
            max_watermark_seen = watermark
            pages_fetched = 0

            while pages_fetched < max_pages_per_kind:
                params: dict[str, Any] = {
                    "order[ID]": "ASC",
                    "start": start_offset,
                    "select[]": "*",
                    "select[0]": "*",
                    "select[1]": "UF_*",
                    "select[2]": "EMAIL",
                    "select[3]": "PHONE",
                }
                if watermark:
                    params["filter[>DATE_MODIFY]"] = watermark

                try:
                    response = await self._client.call_method(
                        tenant_id=tenant_id,
                        actor_id=actor_id,
                        connection_id=connection_id,
                        method_name=method_name,
                        params=params,
                        session=session,
                    )
                except Bitrix24RateLimitError:
                    deferred = True
                    deferral_reason = "rate_limit_deferred"
                    break

                raw_items = response.get("result")
                if not isinstance(raw_items, list):
                    raw_items = []

                page_max_modify = max_watermark_seen
                for item in raw_items:
                    if not isinstance(item, dict):
                        continue
                    await self._upsert_entity_record(
                        session,
                        tenant_id=tenant_id,
                        connection=conn,
                        entity_kind=kind,
                        record=item,
                        synced_at=now,
                    )
                    counts_by_kind[kind] = counts_by_kind.get(kind, 0) + 1
                    item_mod = str(item.get("DATE_MODIFY") or "").strip()
                    if item_mod and (not page_max_modify or item_mod > page_max_modify):
                        page_max_modify = item_mod

                pages_fetched += 1
                next_offset = response.get("next")
                if isinstance(next_offset, int) and next_offset > start_offset:
                    start_offset = next_offset
                    cursors[kind] = start_offset
                    sync_state["cursors"] = dict(cursors)
                    cfg["sync_state"] = dict(sync_state)
                    conn.config = dict(cfg)
                    await session.flush()
                else:
                    # Page series completed for this entity kind: advance committed watermark and reset cursor
                    if page_max_modify:
                        watermarks[kind] = page_max_modify
                    cursors.pop(kind, None)
                    sync_state["watermarks"] = dict(watermarks)
                    sync_state["cursors"] = dict(cursors)
                    cfg["sync_state"] = dict(sync_state)
                    conn.config = dict(cfg)
                    await session.flush()
                    break

            if deferred:
                break

        sync_state["last_synced_at"] = now.isoformat()
        sync_state["last_counts"] = dict(counts_by_kind)
        sync_state["deferred"] = deferred
        sync_state["deferral_reason"] = deferral_reason
        sync_state["watermarks"] = dict(watermarks)
        sync_state["cursors"] = dict(cursors)
        cfg["sync_state"] = sync_state
        conn.config = cfg
        await session.flush()

        if not deferred:
            await ActionAuditEvent.objects.record_success(
                session=session,
                actor_kind="user" if actor_id > 0 else "service",
                actor_id=actor_id if actor_id > 0 else "task_runner",
                action="crm.contact_update",
                resource_kind="contact",
                resource_id=connection_id,
            )

        return {
            "connection_id": connection_id,
            "tenant_id": tenant_id,
            "synced_at": now,
            "counts": counts_by_kind,
            "watermarks": dict(watermarks),
            "cursors": dict(cursors),
            "deferred": deferred,
            "deferral_reason": deferral_reason,
        }

    async def reconcile_deleted_ids(
        self,
        *,
        tenant_id: int,
        connection_id: int,
        entity_kind: str,
        active_external_ids: set[str] | list[str],
        session=None,
    ) -> int:
        """Mark cached CRM entities not present in `active_external_ids` as soft-deleted (`deleted_at`)."""
        if is_bypass() or current_tenant_id() != tenant_id:
            raise Bitrix24Error("connection_denied", status_code=403)
        if entity_kind not in CRM_ENTITY_KINDS:
            raise Bitrix24Error("invalid_entity_kind", status_code=422)

        if session is None:
            async with async_session_maker() as owned, owned.begin():
                return await self.reconcile_deleted_ids(
                    tenant_id=tenant_id,
                    connection_id=connection_id,
                    entity_kind=entity_kind,
                    active_external_ids=active_external_ids,
                    session=owned,
                )

        normalized_active = {str(x).strip() for x in active_external_ids if str(x).strip()}
        rows = list(
            (
                await session.execute(
                    select(CrmEntity).where(
                        CrmEntity.tenant_id == tenant_id,
                        CrmEntity.connection_id == connection_id,
                        CrmEntity.entity_kind == entity_kind,
                        CrmEntity.is_deleted_remote.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )
        now = datetime.now(timezone.utc)
        marked = 0
        for row in rows:
            if row.external_id not in normalized_active:
                row.is_deleted_remote = True
                row.deleted_at = now
                marked += 1
        if marked:
            await session.flush()
        return marked

    async def get_sync_status(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        connection_id: int | None = None,
        session=None,
    ) -> dict[str, Any]:
        """Return sync watermarks, entity totals, and last run status for workspace Bitrix24 connections."""
        if is_bypass() or current_tenant_id() != tenant_id:
            raise Bitrix24Error("connection_denied", status_code=403)

        if session is None:
            async with async_session_maker() as owned:
                return await self.get_sync_status(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    connection_id=connection_id,
                    session=owned,
                )

        stmt = select(WorkspaceConnection).where(
            WorkspaceConnection.tenant_id == tenant_id,
            WorkspaceConnection.provider_id == "bitrix24",
        )
        if connection_id is not None:
            stmt = stmt.where(WorkspaceConnection.id == connection_id)
        stmt = stmt.order_by(WorkspaceConnection.id)
        connections = list((await session.execute(stmt)).scalars().all())

        count_rows = (
            await session.execute(
                select(
                    CrmEntity.connection_id,
                    CrmEntity.entity_kind,
                    func.count(CrmEntity.id),
                )
                .where(
                    CrmEntity.tenant_id == tenant_id,
                    CrmEntity.is_deleted_remote.is_(False),
                )
                .group_by(CrmEntity.connection_id, CrmEntity.entity_kind)
            )
        ).all()

        totals_map: dict[int, dict[str, int]] = {}
        for cid, kind, cnt in count_rows:
            totals_map.setdefault(int(cid), {})[str(kind)] = int(cnt)

        items: list[dict[str, Any]] = []
        for conn in connections:
            cfg = dict(conn.config or {})
            sync_state = dict(cfg.get("sync_state") or {})
            entity_totals = {k: totals_map.get(conn.id, {}).get(k, 0) for k in CRM_ENTITY_KINDS}
            items.append(
                {
                    "connection_id": conn.id,
                    "portal_domain": str(cfg.get("portal_domain") or conn.account_key),
                    "state": conn.state,
                    "last_synced_at": sync_state.get("last_synced_at"),
                    "last_counts": dict(sync_state.get("last_counts") or {}),
                    "entity_totals": entity_totals,
                    "watermarks": dict(sync_state.get("watermarks") or {}),
                    "cursors": dict(sync_state.get("cursors") or {}),
                    "deferred": bool(sync_state.get("deferred", False)),
                    "deferral_reason": sync_state.get("deferral_reason"),
                }
            )

        return {"items": items}


bitrix_sync_service = BitrixSyncService()
