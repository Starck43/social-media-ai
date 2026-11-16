"""CRM contact cards, deal summaries, portal deep-links, and deterministic duplicate candidates (P14-T03).

Invariants:
- Cards show only the current workspace's (`tenant_id`) data.
- Birthday and anniversary fields from CRM are strictly read-only (`read_only_dates=True`);
  the external CRM remains the source of truth.
- Duplicate candidates are surfaced deterministically by matching `email` and `phone`
  HMAC blind indexes across `ContactIdentity` and `CrmEntity` records within the same
  workspace, with explainable `matched_kinds` (`["email"]`, `["phone"]`, `["email", "phone"]`).
  No automatic or manual merge is performed here (manual merge belongs to P15-T03, R-16).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.core.database import async_session_maker
from app.core.tenant_context import current_tenant_id, is_bypass
from app.integrations.bitrix24.client import Bitrix24Error
from app.models.contacts.core import Contact, ContactIdentity
from app.models.crm.entity import CRM_ENTITY_KINDS, CrmEntity
from app.models.integrations.connection import WorkspaceConnection

PORTAL_ENTITY_PATHS: dict[str, str] = {
    "contact": "contact",
    "company": "company",
    "lead": "lead",
    "deal": "deal",
}


def build_portal_entity_url(portal_domain: str | None, entity_kind: str, external_id: str) -> str | None:
    """Build a safe HTTPS deep-link to the entity card on the Bitrix24 portal."""
    domain = (portal_domain or "").strip().lower()
    if not domain:
        return None
    segment = PORTAL_ENTITY_PATHS.get(entity_kind, "contact")
    ext = str(external_id).strip()
    if not ext:
        return None
    return f"https://{domain}/crm/{segment}/details/{ext}/"


class CrmContactCardService:
    """Read-only CRM contact cards and deterministic duplicate candidate detection."""

    async def list_crm_contacts(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        entity_kind: str | None = None,
        updated_since: datetime | None = None,
        has_birthday: bool | None = None,
        connection_id: int | None = None,
        limit: int = 50,
        session=None,
    ) -> dict[str, Any]:
        """List synchronized CRM entities for the workspace with optional filters."""
        if is_bypass() or current_tenant_id() != tenant_id:
            raise Bitrix24Error("connection_denied", status_code=403)
        if entity_kind is not None and entity_kind not in CRM_ENTITY_KINDS:
            raise Bitrix24Error("invalid_entity_kind", status_code=422)

        if session is None:
            async with async_session_maker() as owned:
                return await self.list_crm_contacts(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    entity_kind=entity_kind,
                    updated_since=updated_since,
                    has_birthday=has_birthday,
                    connection_id=connection_id,
                    limit=limit,
                    session=owned,
                )

        conn_rows = list(
            (
                await session.execute(
                    select(WorkspaceConnection).where(
                        WorkspaceConnection.tenant_id == tenant_id,
                        WorkspaceConnection.provider_id == "bitrix24",
                    )
                )
            )
            .scalars()
            .all()
        )
        portal_by_conn: dict[int, str] = {
            c.id: str((c.config or {}).get("portal_domain") or c.account_key) for c in conn_rows
        }

        stmt = select(CrmEntity).where(
            CrmEntity.tenant_id == tenant_id,
            CrmEntity.is_deleted_remote.is_(False),
        )
        if entity_kind is not None:
            stmt = stmt.where(CrmEntity.entity_kind == entity_kind)
        if connection_id is not None:
            stmt = stmt.where(CrmEntity.connection_id == connection_id)
        stmt = stmt.order_by(CrmEntity.id)

        entities = list((await session.execute(stmt)).scalars().all())

        contacts_by_id: dict[int, Contact] = {}
        contact_ids = {e.contact_id for e in entities if e.contact_id is not None}
        if contact_ids:
            c_rows = list(
                (
                    await session.execute(
                        select(Contact).where(
                            Contact.tenant_id == tenant_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            contacts_by_id = {c.id: c for c in c_rows if c.id in contact_ids}

        filtered: list[dict[str, Any]] = []
        for ent in entities:
            if updated_since is not None:
                effective_ts = ent.source_modified_at or ent.last_synced_at
                if effective_ts is None or effective_ts < updated_since:
                    continue

            p_fields = ent.personal_fields
            linked_contact = contacts_by_id.get(ent.contact_id) if ent.contact_id is not None else None
            bday = (linked_contact.birthday_encrypted if linked_contact else None) or p_fields.get("birthday")
            bday_has_year = (
                bool(linked_contact.birthday_has_year)
                if linked_contact and linked_contact.birthday_encrypted
                else bool(p_fields.get("birthday_has_year", False))
            )
            anniv = (linked_contact.anniversary_encrypted if linked_contact else None) or p_fields.get("anniversary")
            anniv_has_year = (
                bool(linked_contact.anniversary_has_year)
                if linked_contact and linked_contact.anniversary_encrypted
                else bool(p_fields.get("anniversary_has_year", False))
            )

            if has_birthday is True and not bday:
                continue
            if has_birthday is False and bday:
                continue

            portal_domain = portal_by_conn.get(ent.connection_id)
            filtered.append(
                {
                    "id": ent.id,
                    "tenant_id": ent.tenant_id,
                    "connection_id": ent.connection_id,
                    "entity_kind": ent.entity_kind,
                    "external_id": ent.external_id,
                    "contact_id": ent.contact_id,
                    "title": ent.title_encrypted,
                    "birthday": bday,
                    "birthday_has_year": bday_has_year,
                    "anniversary": anniv,
                    "anniversary_has_year": anniv_has_year,
                    "read_only_dates": True,
                    "portal_url": build_portal_entity_url(portal_domain, ent.entity_kind, ent.external_id),
                    "source_modified_at": ent.source_modified_at,
                    "last_synced_at": ent.last_synced_at,
                }
            )
            if len(filtered) >= max(1, min(limit, 200)):
                break

        return {"items": filtered, "total": len(filtered)}

    async def _find_duplicate_candidates(
        self,
        session,
        *,
        tenant_id: int,
        target_entity: CrmEntity,
        all_entities: list[CrmEntity],
        all_identities: list[ContactIdentity],
        contacts_by_id: dict[int, Contact],
    ) -> list[dict[str, Any]]:
        """Deterministically identify duplicate candidates in the same workspace by blind index."""
        p_fields = target_entity.personal_fields
        target_email_bidx: set[str] = set(p_fields.get("email_blind_indexes") or [])
        target_phone_bidx: set[str] = set(p_fields.get("phone_blind_indexes") or [])

        for ident in all_identities:
            if target_entity.contact_id is not None and ident.contact_id == target_entity.contact_id:
                if ident.kind == "email":
                    target_email_bidx.add(ident.blind_index)
                elif ident.kind == "phone":
                    target_phone_bidx.add(ident.blind_index)

        if not target_email_bidx and not target_phone_bidx:
            return []

        candidates_by_key: dict[tuple[int | None, int | None], dict[str, Any]] = {}

        # 1. Check other CRM entities in the same workspace
        for other in all_entities:
            if other.id == target_entity.id or other.is_deleted_remote:
                continue
            if (
                target_entity.contact_id is not None
                and other.contact_id is not None
                and other.contact_id == target_entity.contact_id
            ):
                continue

            other_pf = other.personal_fields
            other_emails = set(other_pf.get("email_blind_indexes") or [])
            other_phones = set(other_pf.get("phone_blind_indexes") or [])
            matched: set[str] = set()
            if target_email_bidx & other_emails:
                matched.add("email")
            if target_phone_bidx & other_phones:
                matched.add("phone")
            if not matched:
                continue

            key = (other.contact_id, other.id)
            candidates_by_key[key] = {
                "candidate_contact_id": other.contact_id,
                "candidate_crm_entity_id": other.id,
                "connection_id": other.connection_id,
                "entity_kind": other.entity_kind,
                "external_id": other.external_id,
                "display_name": other.title_encrypted,
                "matched_kinds": sorted(matched),
                "explanation": f"Совпадение по {', '.join(sorted(matched))}",
            }

        # 2. Check other ContactIdentity rows in the same workspace not already covered
        covered_contacts = {
            c["candidate_contact_id"] for c in candidates_by_key.values() if c["candidate_contact_id"] is not None
        }
        matched_by_contact: dict[int, set[str]] = {}
        conn_by_contact: dict[int, int | None] = {}
        for ident in all_identities:
            if target_entity.contact_id is not None and ident.contact_id == target_entity.contact_id:
                continue
            if ident.contact_id in covered_contacts:
                continue
            if ident.kind == "email" and ident.blind_index in target_email_bidx:
                matched_by_contact.setdefault(ident.contact_id, set()).add("email")
                conn_by_contact.setdefault(ident.contact_id, ident.connection_id)
            elif ident.kind == "phone" and ident.blind_index in target_phone_bidx:
                matched_by_contact.setdefault(ident.contact_id, set()).add("phone")
                conn_by_contact.setdefault(ident.contact_id, ident.connection_id)

        for cid, kinds in sorted(matched_by_contact.items()):
            contact_obj = contacts_by_id.get(cid)
            if contact_obj is None or contact_obj.merged_into_id is not None:
                continue
            sorted_kinds = sorted(kinds)
            candidates_by_key[(cid, None)] = {
                "candidate_contact_id": cid,
                "candidate_crm_entity_id": None,
                "connection_id": conn_by_contact.get(cid),
                "entity_kind": "contact",
                "external_id": None,
                "display_name": contact_obj.display_name_encrypted,
                "matched_kinds": sorted_kinds,
                "explanation": f"Совпадение по {', '.join(sorted_kinds)}",
            }

        return sorted(
            candidates_by_key.values(),
            key=lambda item: (
                item["candidate_crm_entity_id"] or 0,
                item["candidate_contact_id"] or 0,
            ),
        )

    async def get_crm_contact_card(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        crm_entity_id: int,
        session=None,
    ) -> dict[str, Any]:
        """Return detailed CRM contact card, linked identities, deal summary, and duplicate candidates."""
        if is_bypass() or current_tenant_id() != tenant_id:
            raise Bitrix24Error("connection_denied", status_code=403)

        if session is None:
            async with async_session_maker() as owned:
                return await self.get_crm_contact_card(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    crm_entity_id=crm_entity_id,
                    session=owned,
                )

        all_entities = list(
            (
                await session.execute(
                    select(CrmEntity).where(
                        CrmEntity.tenant_id == tenant_id,
                        CrmEntity.is_deleted_remote.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )
        target = next((e for e in all_entities if e.id == crm_entity_id), None)
        if target is None:
            raise Bitrix24Error("crm_entity_not_found", status_code=404)

        conn_rows = list(
            (
                await session.execute(
                    select(WorkspaceConnection).where(
                        WorkspaceConnection.tenant_id == tenant_id,
                        WorkspaceConnection.provider_id == "bitrix24",
                    )
                )
            )
            .scalars()
            .all()
        )
        portal_by_conn = {
            c.id: str((c.config or {}).get("portal_domain") or c.account_key) for c in conn_rows
        }
        portal_domain = portal_by_conn.get(target.connection_id)

        contact_rows = list(
            (await session.execute(select(Contact).where(Contact.tenant_id == tenant_id))).scalars().all()
        )
        contacts_by_id = {c.id: c for c in contact_rows}

        identity_rows = list(
            (
                await session.execute(
                    select(ContactIdentity).where(ContactIdentity.tenant_id == tenant_id)
                )
            )
            .scalars()
            .all()
        )

        linked_contact = contacts_by_id.get(target.contact_id) if target.contact_id is not None else None
        linked_identities = [
            {
                "id": ident.id,
                "kind": ident.kind,
                "value": ident.value_encrypted,
                "connection_id": ident.connection_id,
                "verified": ident.verified,
            }
            for ident in sorted(identity_rows, key=lambda r: r.id)
            if target.contact_id is not None and ident.contact_id == target.contact_id
        ]

        # Summarize deals linked to this contact or company within the same workspace
        related_deals: list[dict[str, Any]] = []
        open_count = 0
        closed_count = 0
        total_amount = 0.0

        for ent in sorted(all_entities, key=lambda r: r.id):
            if ent.entity_kind != "deal":
                continue
            matched_deal = False
            if target.contact_id is not None and ent.contact_id == target.contact_id:
                matched_deal = True
            elif (
                target.entity_kind == "company"
                and ent.connection_id == target.connection_id
                and ent.company_external_id == target.external_id
            ):
                matched_deal = True
            elif (
                target.company_external_id
                and ent.connection_id == target.connection_id
                and ent.company_external_id == target.company_external_id
            ):
                matched_deal = True

            if not matched_deal:
                continue

            if ent.is_closed:
                closed_count += 1
            else:
                open_count += 1
            amt = float(ent.opportunity_amount) if ent.opportunity_amount is not None else 0.0
            total_amount += amt
            deal_portal = portal_by_conn.get(ent.connection_id)
            related_deals.append(
                {
                    "id": ent.id,
                    "external_id": ent.external_id,
                    "title": ent.title_encrypted,
                    "stage_code": ent.stage_code,
                    "currency_code": ent.currency_code,
                    "opportunity_amount": amt if ent.opportunity_amount is not None else None,
                    "is_closed": ent.is_closed,
                    "portal_url": build_portal_entity_url(deal_portal, "deal", ent.external_id),
                }
            )

        p_fields = target.personal_fields
        bday = (linked_contact.birthday_encrypted if linked_contact else None) or p_fields.get("birthday")
        bday_has_year = (
            bool(linked_contact.birthday_has_year)
            if linked_contact and linked_contact.birthday_encrypted
            else bool(p_fields.get("birthday_has_year", False))
        )
        anniv = (linked_contact.anniversary_encrypted if linked_contact else None) or p_fields.get("anniversary")
        anniv_has_year = (
            bool(linked_contact.anniversary_has_year)
            if linked_contact and linked_contact.anniversary_encrypted
            else bool(p_fields.get("anniversary_has_year", False))
        )

        duplicate_candidates = await self._find_duplicate_candidates(
            session,
            tenant_id=tenant_id,
            target_entity=target,
            all_entities=all_entities,
            all_identities=identity_rows,
            contacts_by_id=contacts_by_id,
        )

        return {
            "id": target.id,
            "tenant_id": target.tenant_id,
            "connection_id": target.connection_id,
            "portal_domain": portal_domain,
            "entity_kind": target.entity_kind,
            "external_id": target.external_id,
            "contact_id": target.contact_id,
            "title": target.title_encrypted,
            "stage_code": target.stage_code,
            "status_code": target.status_code,
            "company_external_id": target.company_external_id,
            "assigned_by_external_id": target.assigned_by_external_id,
            "birthday": bday,
            "birthday_has_year": bday_has_year,
            "anniversary": anniv,
            "anniversary_has_year": anniv_has_year,
            "birthday_read_only": True,
            "anniversary_read_only": True,
            "read_only_dates": True,
            "personal_fields": {
                k: v
                for k, v in p_fields.items()
                if k not in {"email_blind_indexes", "phone_blind_indexes"}
            },
            "custom_fields": target.custom_fields,
            "portal_url": build_portal_entity_url(portal_domain, target.entity_kind, target.external_id),
            "source_modified_at": target.source_modified_at,
            "last_synced_at": target.last_synced_at,
            "identities": linked_identities,
            "deals_summary": {
                "total_count": len(related_deals),
                "open_count": open_count,
                "closed_count": closed_count,
                "total_opportunity_amount": round(total_amount, 2),
                "deals": related_deals,
            },
            "duplicate_candidates": duplicate_candidates,
        }


crm_contact_card_service = CrmContactCardService()
