"""Tenant-fenced contact aggregate writers using the shared transaction owner."""

from datetime import date, datetime

from sqlalchemy import select

from app.core.database import with_db_session
from app.core.permissions import get_current_user, has_permission
from app.core.tenant_context import TenantContextError, current_tenant_id
from app.models.contacts.core import CHANNELS, PURPOSES, Contact
from app.models.managers.base_manager import BaseManager
from app.services.contacts.identity import identity_index, identity_values
from app.types import ActionType


class ContactUnavailable(LookupError):
    """Opaque selection failure; never disclose a foreign workspace row."""


def require_contact_permission(action):
    tenant = current_tenant_id()
    if tenant is None:
        raise TenantContextError("Contact access requires a selected workspace")
    if not has_permission(get_current_user(), "contact", action):
        raise PermissionError("Contact access denied")
    return tenant


def contact_values(values, *, creating=False):
    """Allowlist input; dates and all free text are encrypted, never indexed."""
    allowed = {
        "display_name",
        "notes",
        "birthday",
        "anniversary",
        "birthday_has_year",
        "anniversary_has_year",
        "preferred_channel",
        "last_active_channel",
        "last_active_at",
    }
    if set(values) - allowed or (creating and "display_name" not in values):
        raise ValueError("Invalid contact fields")
    result = {}
    for field, value in values.items():
        if field in {"display_name", "notes"}:
            if value is not None and (not isinstance(value, str) or len(value) > 10000):
                raise ValueError("Invalid contact text")
            if field == "display_name" and (not value or not value.strip()):
                raise ValueError("Contact display name required")
            result[field + "_encrypted"] = value
        elif field in {"birthday", "anniversary"}:
            has_year = values.get(field + "_has_year", False)
            if not isinstance(has_year, bool):
                raise ValueError("Invalid contact date flag")
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError("Invalid contact date")
                # Leap-day month/day is valid when the year is intentionally absent.
                parsed = date.fromisoformat(value if has_year else "2000-" + value)
                if value != (parsed.isoformat() if has_year else parsed.strftime("%m-%d")):
                    raise ValueError("Invalid contact date format")
            result[field + "_encrypted"] = value
            result[field + "_has_year"] = has_year if value is not None else False
        elif field.endswith("_has_year"):
            if field.removesuffix("_has_year") not in values:
                raise ValueError("Date flag requires its date")
        elif field.endswith("channel"):
            if value is not None and value not in CHANNELS:
                raise ValueError("Invalid contact channel")
            result[field] = value
        else:
            if value is not None and (not isinstance(value, datetime) or value.utcoffset() is None):
                raise ValueError("Contact activity time must include a timezone")
            result[field] = value
    return result


class ContactManager(BaseManager):
    def __init__(self):
        super().__init__(Contact)

    def get_queryset(self, session=None):
        tenant = current_tenant_id()
        if tenant is None:
            raise TenantContextError("Contact access requires a selected workspace")
        # Unlike a global operator list, contacts always retain an explicit tenant fence.
        return super().get_queryset(session).filter(tenant_id=tenant)

    async def _locked(self, contact_id, session):
        result = await session.execute(
            select(Contact).where(Contact.id == contact_id, Contact.tenant_id == current_tenant_id()).with_for_update()
        )
        contact = result.scalar_one_or_none()
        if contact is None or contact.merged_into_id is not None:
            raise ContactUnavailable("Contact unavailable")
        return contact

    @with_db_session
    async def create(self, session, **values):
        tenant = require_contact_permission(ActionType.CREATE)
        return await super().create(session=session, tenant_id=tenant, **contact_values(values, creating=True))

    async def bulk_create(self, *args, **kwargs):
        raise ValueError("Use contact aggregate writers")

    async def delete(self, *args, **kwargs):
        raise ValueError("Use delete_by_id for contact privacy cleanup")

    @with_db_session
    async def update_by_id(self, instance_id, session, **values):
        require_contact_permission(ActionType.UPDATE)
        fields = contact_values(values)
        row = await self._locked(instance_id, session)
        for key, value in fields.items():
            setattr(row, key, value)
        await session.flush()
        await session.refresh(row)
        return row

    @with_db_session
    async def delete_by_id(self, instance_id, session):
        require_contact_permission(ActionType.DELETE)
        row = await self._locked(instance_id, session)
        await session.delete(row)
        await session.flush()
        return True

    async def list_contacts(self, *, limit=50, before_id=None):
        require_contact_permission(ActionType.VIEW)
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("Invalid contact page size")
        if before_id is not None and (not isinstance(before_id, int) or before_id <= 0):
            raise ValueError("Invalid contact cursor")
        query = self.filter(merged_into_id=None)
        if before_id is not None:
            query = query.filter(Contact.id < before_id)
        return await query.order_by(Contact.id.desc()).limit(limit).all()

    async def lookup(self, kind, value, *, connection_id=None):
        require_contact_permission(ActionType.VIEW)
        from app.models import ContactIdentity

        # SQL predicate contains only a keyed exact-match index, never plaintext.
        ids = select(ContactIdentity.contact_id).where(
            ContactIdentity.tenant_id == current_tenant_id(),
            ContactIdentity.kind == kind,
            ContactIdentity.blind_index == identity_index(kind, value),
            ContactIdentity.connection_id == connection_id,
        )
        return await self.filter(Contact.id.in_(ids), merged_into_id=None).order_by(Contact.id).all()

    @with_db_session
    async def attach_identity(
        self, contact_id, kind, value, session, *, connection_id=None, verified=False, source_native_id=None
    ):
        tenant = require_contact_permission(ActionType.UPDATE)
        from app.models import ContactIdentity, WorkspaceConnection

        pair = identity_values(kind, value)
        if not isinstance(verified, bool):
            raise ValueError("Invalid verification flag")
        if source_native_id is not None and (not isinstance(source_native_id, str) or len(source_native_id) > 512):
            raise ValueError("Invalid source identifier")
        await self._locked(contact_id, session)
        if connection_id is not None:
            connection = (
                await session.execute(
                    select(WorkspaceConnection.id).where(
                        WorkspaceConnection.tenant_id == tenant,
                        WorkspaceConnection.id == connection_id,
                    )
                )
            ).scalar_one_or_none()
            if connection is None:
                raise ContactUnavailable("Connection unavailable")
        row = ContactIdentity(
            tenant_id=tenant,
            contact_id=contact_id,
            kind=kind,
            connection_id=connection_id,
            verified=verified,
            source_native_id_encrypted=source_native_id,
            **pair,
        )
        session.add(row)
        # Uniqueness (including NULL connection) is enforced by PostgreSQL, not a pre-check.
        await session.flush()
        await session.refresh(row)
        return row

    @with_db_session
    async def detach_identity(self, contact_id, identity_id, session):
        tenant = require_contact_permission(ActionType.UPDATE)
        from app.models import ContactIdentity

        await self._locked(contact_id, session)
        row = (
            await session.execute(
                select(ContactIdentity).where(
                    ContactIdentity.tenant_id == tenant,
                    ContactIdentity.contact_id == contact_id,
                    ContactIdentity.id == identity_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise ContactUnavailable("Identity unavailable")
        await session.delete(row)
        await session.flush()
        return True

    @with_db_session
    async def record_consent(
        self, contact_id, channel, purpose, basis, source_evidence, session, *, granted_at=None, withdrawn_at=None
    ):
        tenant = require_contact_permission(ActionType.UPDATE)
        from app.models import ContactConsent

        if channel not in CHANNELS or purpose not in PURPOSES:
            raise ValueError("Invalid consent scope")
        for value in (basis, source_evidence):
            if not isinstance(value, str) or not value.strip() or len(value) > 10000:
                raise ValueError("Consent basis and evidence required")
        if granted_at is None and withdrawn_at is None:
            raise ValueError("Consent is never granted implicitly")
        for value in (granted_at, withdrawn_at):
            if value is not None and (not isinstance(value, datetime) or value.utcoffset() is None):
                raise ValueError("Consent time must include a timezone")
        if granted_at is not None and withdrawn_at is not None and withdrawn_at < granted_at:
            raise ValueError("Consent withdrawal precedes grant")
        await self._locked(contact_id, session)
        row = ContactConsent(
            tenant_id=tenant,
            contact_id=contact_id,
            channel=channel,
            purpose=purpose,
            basis_encrypted=basis,
            source_evidence_encrypted=source_evidence,
            granted_at=granted_at,
            withdrawn_at=withdrawn_at,
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
        return row
