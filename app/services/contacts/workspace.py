"""Explicit contact projections shared by HTTP and privacy export."""

from app.core.database import with_db_session
from app.models import Contact, ContactConsent, ContactIdentity, ContactMergeJournal
from app.models.managers.contact_manager import ContactUnavailable, require_contact_permission
from app.types import ActionType


def contact_dict(row):
    return {
        "id": row.id,
        "display_name": row.display_name_encrypted,
        "notes": row.notes_encrypted,
        "birthday": row.birthday_encrypted,
        "birthday_has_year": row.birthday_has_year,
        "anniversary": row.anniversary_encrypted,
        "anniversary_has_year": row.anniversary_has_year,
        "preferred_channel": row.preferred_channel,
        "last_active_channel": row.last_active_channel,
        "last_active_at": row.last_active_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def identity_dict(row):
    return {
        "id": row.id,
        "kind": row.kind,
        "value": row.value_encrypted,
        "connection_id": row.connection_id,
        "verified": row.verified,
        "source_native_id": row.source_native_id_encrypted,
    }


def consent_dict(row):
    # Evidence is not authorization. D7 policy will be implemented separately.
    return {
        "id": row.id,
        "channel": row.channel,
        "purpose": row.purpose,
        "basis": row.basis_encrypted,
        "granted_at": row.granted_at,
        "withdrawn_at": row.withdrawn_at,
        "source_evidence": row.source_evidence_encrypted,
    }


async def list_contacts(*, limit=50, before_id=None):
    rows = await Contact.objects.list_contacts(limit=limit, before_id=before_id)
    return {
        "items": [
            {
                "id": row.id,
                "display_name": row.display_name_encrypted,
                "preferred_channel": row.preferred_channel,
                "last_active_at": row.last_active_at,
            }
            for row in rows
        ],
        "next_before_id": rows[-1].id if len(rows) == limit else None,
    }


async def contact_detail(contact_id, *, session=None, action=ActionType.VIEW):
    tenant = require_contact_permission(action)
    row = await Contact.objects.get(id=contact_id, tenant_id=tenant, session=session)
    if row is None:
        raise ContactUnavailable("Contact unavailable")
    identities = (
        await ContactIdentity.objects.filter(tenant_id=tenant, contact_id=contact_id, session=session)
        .order_by(ContactIdentity.id)
        .all()
    )
    consents = (
        await ContactConsent.objects.filter(tenant_id=tenant, contact_id=contact_id, session=session)
        .order_by(ContactConsent.id)
        .all()
    )
    return {
        **contact_dict(row),
        "identities": [identity_dict(item) for item in identities],
        "consents": [consent_dict(item) for item in consents],
    }


class ContactPrivacy:
    """Module privacy adapter. Workspace offboarding uses the tenant CASCADE FKs.

    No generic P6 export registry exists in the current baseline; expose explicit
    aggregate export/erasure rather than falsely claiming a global integration.
    """

    @classmethod
    @with_db_session
    async def export_contact(cls, contact_id, session):
        tenant = require_contact_permission(ActionType.EXPORT)
        await Contact.objects._locked(contact_id, session)
        data = await contact_detail(contact_id, session=session, action=ActionType.EXPORT)
        rows = (
            await ContactMergeJournal.objects.filter(
                (ContactMergeJournal.source_contact_id == contact_id)
                | (ContactMergeJournal.target_contact_id == contact_id),
                tenant_id=tenant,
                session=session,
            )
            .order_by(ContactMergeJournal.id)
            .all()
        )
        data["merge_journal"] = [
            {
                "id": row.id,
                "source_contact_id": row.source_contact_id,
                "target_contact_id": row.target_contact_id,
                "reason": row.reason_encrypted,
                "created_at": row.created_at,
            }
            for row in rows
        ]
        return data

    @staticmethod
    async def delete_contact(contact_id, *, session=None):
        return await Contact.objects.delete_by_id(contact_id, session=session)
