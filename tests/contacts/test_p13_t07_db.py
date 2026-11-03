"""PostgreSQL integration handoff. Run locally with root conftest and test_schema.

These tests deliberately are NOT part of the remote no-database route.
The shared personal_world fixture owns every synthetic tenant/user and verifies
normal teardown through the existing tenant deletion manager.
"""

from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.access_catalog import workspace_grants
from app.core.config import settings
from app.core.database import async_engine
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import Contact, ContactConsent, ContactIdentity, ContactMergeJournal, WorkspaceConnection
from app.models.managers.contact_manager import ContactUnavailable
from app.services.contacts.workspace import ContactPrivacy, contact_detail
from tests.shared.fixtures.contact_core import contact_world  # noqa: F401
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]
NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def pii_keys(monkeypatch):
    import base64
    import os

    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "PII_INDEX_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())


def scope(world, index):
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(tenant_scope(world.tenants[index].id))
    stack.enter_context(permission_scope(world.users[0 if index == 0 else 2], member_grants=workspace_grants("owner")))
    return stack


async def test_tenant_crud_lookup_and_ciphertext(contact_world):
    world = contact_world
    with scope(world, 0):
        first = await Contact.objects.create(display_name="Synthetic P13 contact", notes="Synthetic private note")
        await Contact.objects.attach_identity(first.id, "email", "P13@Example.invalid")
        await Contact.objects.attach_identity(first.id, "phone", "8 (999) 123-45-67")
        assert [r.id for r in await Contact.objects.lookup("email", "p13@example.invalid")] == [first.id]
        assert [r.id for r in await Contact.objects.lookup("phone", "+79991234567")] == [first.id]
        assert (await contact_detail(first.id))["consents"] == []
    with scope(world, 1):
        second = await Contact.objects.create(display_name="Other synthetic contact")
        await Contact.objects.attach_identity(second.id, "email", "p13@example.invalid")
        assert [r.id for r in await Contact.objects.lookup("email", "P13@Example.invalid")] == [second.id]
        assert await Contact.objects.get(id=first.id) is None
        assert [r.id for r in await Contact.objects.list_contacts()] == [second.id]
        for operation in (contact_detail, ContactPrivacy.export_contact, ContactPrivacy.delete_contact):
            with pytest.raises(ContactUnavailable):
                await operation(first.id)
        with pytest.raises(ContactUnavailable):
            await Contact.objects.update_by_id(first.id, notes="forbidden")
        with pytest.raises(ContactUnavailable):
            await Contact.objects.attach_identity(first.id, "email", "foreign@example.invalid")
    with scope(world, 0):
        schema = async_engine.dialect.identifier_preparer.quote_schema(settings.DB_SCHEMA)
        async with async_engine.connect() as conn:
            stored = (
                await conn.execute(
                    text(f"SELECT display_name_encrypted, notes_encrypted FROM {schema}.contacts WHERE id=:id"),
                    {"id": first.id},
                )
            ).one()
            assert all(value.startswith("pii:v1:") for value in stored)
            assert all("Synthetic" not in value for value in stored)
            raw = (
                await conn.execute(
                    text(f"SELECT value_encrypted, blind_index FROM {schema}.contact_identities WHERE contact_id=:id"),
                    {"id": first.id},
                )
            ).all()
            assert all(value.startswith("pii:v1:") and index.startswith("pii:idx:") for value, index in raw)
        await Contact.objects.update_by_id(first.id, display_name="Renamed synthetic contact")
        assert (await contact_detail(first.id))["display_name"] == "Renamed synthetic contact"


async def test_nullable_connection_uniqueness_and_composite_fks(contact_world):
    world = contact_world
    with scope(world, 0):
        first = await Contact.objects.create(display_name="Unique fixture")
        second = await Contact.objects.create(display_name="Duplicate fixture")
        identifier = await Contact.objects.attach_identity(first.id, "email", "unique@example.invalid")
        with pytest.raises(IntegrityError):
            await Contact.objects.attach_identity(second.id, "email", "UNIQUE@example.invalid")
        assert await Contact.objects.detach_identity(first.id, identifier.id)
        await Contact.objects.attach_identity(second.id, "email", "UNIQUE@example.invalid")
        connection = await WorkspaceConnection.objects.create(
            owner_user_id=world.users[0].id,
            platform="telegram",
            provider_id="telegram",
            auth_method="bot",
            schema_version=1,
            label="Synthetic P13",
            account_key=world.tag,
            config={},
        )
        await Contact.objects.attach_identity(first.id, "email", "unique@example.invalid", connection_id=connection.id)
        assert [r.id for r in await Contact.objects.lookup("email", "unique@example.invalid")] == [second.id]
        assert [
            r.id for r in await Contact.objects.lookup("email", "unique@example.invalid", connection_id=connection.id)
        ] == [first.id]
    with scope(world, 1):
        foreign = await Contact.objects.create(display_name="Foreign fixture")
        with pytest.raises(ContactUnavailable):
            await Contact.objects.attach_identity(
                foreign.id, "email", "third@example.invalid", connection_id=connection.id
            )
        # Direct manager use still cannot evade the database's composite tenant FK.
        with pytest.raises(IntegrityError):
            await ContactIdentity.objects.create(
                contact_id=first.id,
                kind="email",
                value_encrypted="x@example.invalid",
                blind_index="synthetic-invalid-cross-tenant",
            )
        with pytest.raises(IntegrityError):
            await ContactMergeJournal.objects.create(source_contact_id=foreign.id, target_contact_id=first.id)


async def test_consent_privacy_export_and_contact_erasure(contact_world):
    world = contact_world
    with scope(world, 0):
        row = await Contact.objects.create(display_name="Privacy fixture")
        target = await Contact.objects.create(display_name="Journal target fixture")
        await Contact.objects.attach_identity(row.id, "email", "privacy@example.invalid")
        await Contact.objects.record_consent(row.id, "email", "marketing", "consent", "synthetic form", granted_at=NOW)
        await Contact.objects.record_consent(
            row.id, "email", "marketing", "withdrawal", "synthetic form", withdrawn_at=NOW
        )
        await ContactMergeJournal.objects.create(
            source_contact_id=row.id, target_contact_id=target.id, reason_encrypted="synthetic journal"
        )
        exported = await ContactPrivacy.export_contact(row.id)
        assert len(exported["identities"]) == 1 and len(exported["consents"]) == 2
        assert len(exported["merge_journal"]) == 1 and "blind_index" not in repr(exported)
        await ContactPrivacy.delete_contact(row.id)
        assert await Contact.objects.get(id=row.id) is None
        assert await ContactIdentity.objects.filter(contact_id=row.id).count() == 0
        assert await ContactConsent.objects.filter(contact_id=row.id).count() == 0
        assert await ContactMergeJournal.objects.filter(source_contact_id=row.id).count() == 0


async def test_rotation_marker_for_identical_sql_and_workspace_cascade(contact_world):
    world = contact_world
    with scope(world, 0):
        row = await Contact.objects.create(display_name="Marker fixture")
        await Contact.objects.attach_identity(row.id, "email", "marker@example.invalid")
        await Contact.objects.record_consent(
            row.id, "email", "transactional", "contract", "synthetic evidence", granted_at=NOW
        )
        schema = async_engine.dialect.identifier_preparer.quote_schema(settings.DB_SCHEMA)
        target = await Contact.objects.create(display_name="Marker journal target")
        await ContactMergeJournal.objects.create(
            source_contact_id=row.id, target_contact_id=target.id, reason_encrypted="synthetic marker journal"
        )
        async with async_engine.begin() as conn:
            for model, column in (
                (Contact, "display_name_encrypted"),
                (ContactIdentity, "value_encrypted"),
                (ContactConsent, "basis_encrypted"),
                (ContactMergeJournal, "reason_encrypted"),
            ):
                stored = (
                    await conn.execute(
                        select(model.id, model.pii_updated_at).where(model.tenant_id == world.tenants[0].id)
                    )
                ).all()
                for row_id, before in stored:
                    await conn.execute(
                        text(f"UPDATE {schema}.{model.__tablename__} SET {column}={column} WHERE id=:id"),
                        {"id": row_id},
                    )
                    after = (await conn.execute(select(model.pii_updated_at).where(model.id == row_id))).scalar_one()
                    assert after == before + 1
        # The existing workspace deletion path is the offboarding CASCADE path;
        # do not invoke privileged/public offboarding in the test.
        from app.core.permissions import operator_permission_scope
        from app.models import Tenant

        with tenant_scope(bypass=True), operator_permission_scope():
            await Tenant.objects.delete_by_id(world.tenants[0].id)
        for model in (Contact, ContactIdentity, ContactConsent, ContactMergeJournal):
            assert await model.objects.filter(tenant_id=world.tenants[0].id).count() == 0


async def test_http_contact_routes_refuse_foreign_selection(contact_world):
    """Real permission dependency and ORM; only bearer identity is supplied by the fixture."""
    import httpx
    from fastapi import FastAPI

    from app.api.v1.endpoints import contacts
    from app.services.user.auth import get_authenticated_user

    world = contact_world
    with scope(world, 0):
        row = await Contact.objects.create(display_name="HTTP isolation fixture")
    app = FastAPI()
    app.include_router(contacts.router, prefix="/api/v1/contacts")
    app.dependency_overrides[get_authenticated_user] = lambda: world.users[2]
    with scope(world, 1):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/v1/contacts")).json()["items"] == []
            for suffix in ("", "/export"):
                response = await client.get(f"/api/v1/contacts/{row.id}{suffix}")
                assert response.status_code == 404 and response.json() == {"detail": "Contact unavailable"}
    with tenant_scope(world.tenants[1].id), permission_scope(world.users[2], member_grants=workspace_grants("viewer")):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/v1/contacts")).status_code == 403
