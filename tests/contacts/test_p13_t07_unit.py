"""DB-exempt contracts: DB/provider boundaries are explicit fail-fast doubles."""

import base64
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from sqlalchemy.dialects import postgresql

from app.core.config import settings
from app.core.pii import PIIError, decrypt_pii, encrypt_pii, normalize_opaque_id
from app.core.tenant_context import TenantContextError, tenant_scope
from app.models import Contact, ContactConsent, ContactIdentity, ContactMergeJournal
from app.models.managers import contact_manager as managers
from app.models.managers.contact_manager import ContactManager, ContactUnavailable, contact_values
from app.models.types.encrypted import EncryptedText
from app.services.contacts import identity, workspace
from app.types import ActionType

pytestmark = pytest.mark.tenancy
NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    from sqlalchemy import event
    from sqlalchemy.engine.interfaces import Dialect

    import app.core.database as database
    import app.models.managers.base_manager as base

    def forbidden(*args, **kwargs):
        raise AssertionError("DB access forbidden in contact unit tests")

    event.listen(Dialect, "do_connect", forbidden)
    monkeypatch.setattr(database, "async_session_maker", forbidden)
    monkeypatch.setattr(base, "async_session_maker", forbidden)
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "PII_INDEX_KEY", base64.urlsafe_b64encode(b"u" * 32).decode())
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", None)
    yield
    event.remove(Dialect, "do_connect", forbidden)


@pytest.fixture
def allowed(monkeypatch):
    monkeypatch.setattr(managers, "has_permission", lambda *a: True)
    with tenant_scope(201):
        yield


def contact(**overrides):
    fields = dict(
        id=1,
        tenant_id=201,
        display_name_encrypted="Synthetic Contact",
        notes_encrypted="Private note",
        birthday_encrypted="02-29",
        birthday_has_year=False,
        anniversary_encrypted=None,
        anniversary_has_year=False,
        preferred_channel="email",
        last_active_channel=None,
        last_active_at=None,
        merged_into_id=None,
        created_at=NOW,
        updated_at=NOW,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def session_for(row):
    session = SimpleNamespace(add=Mock(), flush=AsyncMock(), refresh=AsyncMock(), delete=AsyncMock())
    session.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: row))
    return session


def query(rows):
    q = Mock()
    q.filter.return_value = q
    q.order_by.return_value = q
    q.limit.return_value = q
    q.all = AsyncMock(return_value=rows)
    return q


@pytest.mark.parametrize(
    "kind,raw,expected",
    [
        ("email", " Anna@Example.com ", "anna@example.com"),
        ("phone", "8 (999) 123-45-67", "+79991234567"),
        ("telegram_username", "@Name", "name"),
        ("telegram_id", "00123", "123"),
        ("max_id", "42", "42"),
        ("bitrix24_contact", "8", "8"),
        ("bitrix24_company", "9", "9"),
        ("bitrix24_lead", "10", "10"),
    ],
)
def test_identity_pair(kind, raw, expected):
    pair = identity.identity_values(kind, raw)
    assert pair["value_encrypted"] == expected
    assert pair["blind_index"] == identity.identity_index(kind, expected)
    digest = pair["blind_index"].rsplit(":", 1)[1]
    assert len(digest) == 64 and set(digest) <= set("0123456789abcdef")
    encrypted = encrypt_pii(expected)
    assert encrypted.startswith("pii:v1:") and decrypt_pii(encrypted) == expected


@pytest.mark.parametrize(
    "kind,value",
    [
        ("other", "x"),
        ("email", ""),
        ("phone", None),
        ("email", "x" * 513),
        ("max_id", "x"),
        ("telegram_id", "0"),
        ("bitrix24_lead", "-1"),
        ("max_id", "１２a"),
    ],
)
def test_invalid_identifiers(kind, value):
    with pytest.raises((ValueError, PIIError)):
        identity.identity_values(kind, value)


@pytest.mark.parametrize("value", ["", "a\x00b", "x" * 513])
def test_opaque_id_invalid(value):
    with pytest.raises(PIIError):
        normalize_opaque_id(value)


def test_opaque_id_preserves_case():
    assert normalize_opaque_id(" AbC ") == "AbC"


def test_encrypted_columns_and_offboarding_contract():
    expected = {
        Contact: {"display_name_encrypted", "notes_encrypted", "birthday_encrypted", "anniversary_encrypted"},
        ContactIdentity: {"value_encrypted", "source_native_id_encrypted"},
        ContactConsent: {"basis_encrypted", "source_evidence_encrypted"},
        ContactMergeJournal: {"reason_encrypted"},
    }
    from cli.commands.pii import REGISTRY

    for model, columns in expected.items():
        table = model.__table__
        assert {c.name for c in table.c if isinstance(c.type, EncryptedText)} == columns
        assert "pii_updated_at" in table.c
        assert any(fk.column.table.name == "tenants" and fk.ondelete == "CASCADE" for fk in table.foreign_keys)
        assert all((table.name, c, None) in REGISTRY for c in columns)
        for fk in table.foreign_key_constraints:
            if len(fk.elements) > 1:
                assert "tenant_id" in fk.column_keys
    index = next(i for i in ContactIdentity.__table__.indexes if i.name == "uq_contact_identity_unbound")
    assert index.unique and str(index.dialect_options["postgresql"]["where"]) == "connection_id IS NULL"
    assert ContactConsent.__table__.c.granted_at.default is None


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"display_name": ""},
        {"display_name": " "},
        {"tenant_id": 99},
        {"display_name": 4},
        {"notes": "x" * 10001},
        {"birthday": "02-30"},
        {"birthday": 12},
        {"birthday_has_year": True},
        {"birthday": "11-03", "birthday_has_year": "yes"},
        {"birthday": "11-3"},
        {"preferred_channel": "sms"},
        {"last_active_at": NOW.replace(tzinfo=None)},
        {"last_active_at": "today"},
        {"merged_into_id": 3},
        {"pii_updated_at": 0},
    ],
)
def test_invalid_contact_values(values):
    with pytest.raises(ValueError):
        contact_values(values, creating="display_name" in values or not values)


def test_contact_value_projection():
    assert contact_values(
        {
            "display_name": "Synthetic",
            "notes": None,
            "birthday": "02-29",
            "anniversary": "2020-01-01",
            "anniversary_has_year": True,
            "preferred_channel": None,
            "last_active_channel": "email",
            "last_active_at": NOW,
        }
    ) == {
        "display_name_encrypted": "Synthetic",
        "notes_encrypted": None,
        "birthday_encrypted": "02-29",
        "birthday_has_year": False,
        "anniversary_encrypted": "2020-01-01",
        "anniversary_has_year": True,
        "preferred_channel": None,
        "last_active_channel": "email",
        "last_active_at": NOW,
    }
    assert contact_values({"birthday": None}) == {"birthday_encrypted": None, "birthday_has_year": False}


def test_manager_no_context_and_permission_denied(monkeypatch):
    with tenant_scope(None):
        with pytest.raises(TenantContextError):
            ContactManager().get_queryset()
        with pytest.raises(TenantContextError):
            managers.require_contact_permission(ActionType.VIEW)
    monkeypatch.setattr(managers, "has_permission", lambda *a: False)
    with tenant_scope(201):
        with pytest.raises(PermissionError):
            managers.require_contact_permission(ActionType.VIEW)


def test_queryset_stays_tenant_fenced_in_bypass():
    with tenant_scope(201, bypass=True):
        statement = ContactManager().get_queryset().to_select()
        compiled = statement.compile(dialect=postgresql.dialect())
        assert 201 in compiled.params.values()
        assert "tenant_id" in str(compiled)


@pytest.mark.asyncio
async def test_crud_and_locked_selection(allowed):
    manager = ContactManager()
    row = contact()
    session = session_for(row)
    created = await manager.create(session=session, display_name="Synthetic")
    assert created.tenant_id == 201 and created.display_name_encrypted == "Synthetic"
    updated = await manager.update_by_id(1, session=session, notes="New private note")
    assert updated.notes_encrypted == "New private note"
    assert "FOR UPDATE" in str(session.execute.call_args.args[0])
    assert await manager.delete_by_id(1, session=session)
    session.delete.assert_awaited_once_with(row)
    for method in (manager.bulk_create, manager.delete):
        with pytest.raises(ValueError):
            await method()
    for missing in (None, contact(merged_into_id=2)):
        with pytest.raises(ContactUnavailable):
            await manager._locked(1, session_for(missing))


@pytest.mark.asyncio
async def test_list_and_blind_index_lookup(allowed, monkeypatch):
    manager = ContactManager()
    q = query([contact()])
    monkeypatch.setattr(manager, "filter", Mock(return_value=q))
    assert len(await manager.list_contacts(limit=1, before_id=4)) == 1
    assert len(await manager.lookup("email", "Anna@Example.com")) == 1
    subquery = manager.filter.call_args.args[0].right.element
    compiled = subquery.compile(dialect=postgresql.dialect())
    assert "anna@example.com" not in repr(compiled.params).lower()
    assert any(isinstance(v, str) and v.startswith("pii:idx:") for v in compiled.params.values())
    assert 201 in compiled.params.values()
    for params in ({"limit": 0}, {"limit": 101}, {"before_id": 0}, {"before_id": "x"}):
        with pytest.raises(ValueError):
            await manager.list_contacts(**params)


@pytest.mark.asyncio
async def test_identity_attach_detach(allowed, monkeypatch):
    manager = ContactManager()
    monkeypatch.setattr(manager, "_locked", AsyncMock(return_value=contact()))
    session = session_for(1)
    row = await manager.attach_identity(1, "email", "Anna@Example.com", session=session, connection_id=8)
    assert row.tenant_id == 201 and row.contact_id == 1 and row.verified is False
    assert "tenant_id" in str(session.execute.call_args.args[0])
    detached = SimpleNamespace(id=4)
    assert await manager.detach_identity(1, 4, session=session_for(detached))
    with pytest.raises(ContactUnavailable):
        await manager.attach_identity(1, "email", "a@b.ru", connection_id=9, session=session_for(None))
    with pytest.raises(ContactUnavailable):
        await manager.detach_identity(1, 99, session=session_for(None))
    for params in ({"verified": "yes"}, {"source_native_id": 1}, {"source_native_id": "x" * 513}):
        with pytest.raises(ValueError):
            await manager.attach_identity(1, "email", "a@b.ru", session=session, **params)


@pytest.mark.asyncio
async def test_consent_default_none_and_evidence(allowed, monkeypatch):
    manager = ContactManager()
    monkeypatch.setattr(manager, "_locked", AsyncMock(return_value=contact()))
    session = session_for(None)
    with pytest.raises(ValueError, match="implicitly"):
        await manager.record_consent(1, "email", "marketing", "consent", "form", session=session)
    row = await manager.record_consent(1, "email", "marketing", "consent", "form", granted_at=NOW, session=session)
    assert row.granted_at == NOW and row.withdrawn_at is None
    row = await manager.record_consent(1, "email", "marketing", "withdrawal", "form", withdrawn_at=NOW, session=session)
    assert row.granted_at is None
    for change in (
        {"channel": "sms"},
        {"purpose": "other"},
        {"basis": ""},
        {"source_evidence": None},
        {"granted_at": NOW.replace(tzinfo=None)},
        {"granted_at": "today"},
        {"granted_at": NOW, "withdrawn_at": NOW.replace(year=2025)},
    ):
        arguments = dict(
            contact_id=1,
            channel="email",
            purpose="marketing",
            basis="consent",
            source_evidence="form",
            granted_at=NOW,
            session=session,
        )
        arguments.update(change)
        with pytest.raises(ValueError):
            await manager.record_consent(**arguments)


@pytest.mark.asyncio
async def test_projections_and_privacy(allowed, monkeypatch):
    row = contact()
    monkeypatch.setattr(Contact.objects, "list_contacts", AsyncMock(return_value=[row]))
    page = await workspace.list_contacts(limit=1)
    assert page["next_before_id"] == 1
    assert set(page["items"][0]) == {"id", "display_name", "preferred_channel", "last_active_at"}
    monkeypatch.setattr(Contact.objects, "get", AsyncMock(return_value=row))
    identifier = SimpleNamespace(
        id=2,
        kind="email",
        value_encrypted="a@b.ru",
        connection_id=None,
        verified=False,
        source_native_id_encrypted="source",
    )
    consent = SimpleNamespace(
        id=3,
        channel="email",
        purpose="marketing",
        basis_encrypted="consent",
        granted_at=NOW,
        withdrawn_at=None,
        source_evidence_encrypted="form",
    )
    journal = SimpleNamespace(
        id=4, source_contact_id=1, target_contact_id=5, reason_encrypted="duplicate", created_at=NOW
    )
    monkeypatch.setattr(ContactIdentity.objects, "filter", Mock(return_value=query([identifier])))
    monkeypatch.setattr(ContactConsent.objects, "filter", Mock(return_value=query([consent])))
    monkeypatch.setattr(ContactMergeJournal.objects, "filter", Mock(return_value=query([journal])))
    detail = await workspace.contact_detail(1)
    assert detail["identities"][0]["value"] == "a@b.ru"
    assert detail["consents"][0]["source_evidence"] == "form"
    assert "blind_index" not in repr(detail) and "encrypted" not in repr(detail)
    monkeypatch.setattr(Contact.objects, "_locked", AsyncMock(return_value=row))
    exported = await workspace.ContactPrivacy.export_contact(1, session=session_for(row))
    assert exported["merge_journal"][0]["reason"] == "duplicate"
    assert ContactMergeJournal.objects.filter.call_args.kwargs["tenant_id"] == 201
    monkeypatch.setattr(Contact.objects, "delete_by_id", AsyncMock(return_value=True))
    assert await workspace.ContactPrivacy.delete_contact(1)
    monkeypatch.setattr(Contact.objects, "get", AsyncMock(return_value=None))
    with pytest.raises(ContactUnavailable):
        await workspace.contact_detail(99)


@pytest.mark.asyncio
async def test_api_read_only_validation_and_opaque_404(monkeypatch):
    from app.api.v1.endpoints import contacts

    app = FastAPI()
    app.include_router(contacts.router, prefix="/api/v1/contacts")
    app.dependency_overrides[contacts.router.dependencies[0].dependency] = lambda: None
    monkeypatch.setattr(contacts, "list_contacts", AsyncMock(return_value={"items": [], "next_before_id": None}))
    monkeypatch.setattr(contacts, "contact_detail", AsyncMock(side_effect=ContactUnavailable()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/v1/contacts")).status_code == 200
        assert (await client.get("/api/v1/contacts?limit=101")).status_code == 422
        assert (await client.get("/api/v1/contacts/99")).json() == {"detail": "Contact unavailable"}
        assert (await client.get("/api/v1/contacts/0")).status_code == 422
        assert (await client.post("/api/v1/contacts")).status_code == 405
    assert all("GET" in route.methods for route in contacts.router.routes)


@pytest.mark.asyncio
async def test_api_authentication_not_waived():
    from app.api.v1.endpoints import contacts

    app = FastAPI()
    app.include_router(contacts.router, prefix="/contacts")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/contacts")).status_code in {401, 403}


@pytest.mark.asyncio
async def test_web_denial_and_render(monkeypatch):
    from app.web import contacts

    monkeypatch.setattr(contacts, "list_contacts", AsyncMock(side_effect=PermissionError()))
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        await contacts.contacts_page(SimpleNamespace(), before_id=None)
    assert error.value.status_code == 403
    monkeypatch.setattr(contacts, "list_contacts", AsyncMock(return_value={"items": [], "next_before_id": None}))
    monkeypatch.setattr(contacts, "render", Mock(return_value="rendered"))
    assert await contacts.contacts_page(SimpleNamespace(), before_id=None) == "rendered"


def test_real_membership_presets_fail_closed():
    from app.core.access_catalog import workspace_grants
    from app.core.permissions import permission_scope

    actor = SimpleNamespace(id=101, is_active=True)
    for role, expected in (("owner", True), ("admin", True), ("manager", False), ("viewer", False)):
        with tenant_scope(201), permission_scope(actor, member_grants=workspace_grants(role)):
            if expected:
                assert managers.require_contact_permission(ActionType.VIEW) == 201
            else:
                with pytest.raises(PermissionError):
                    managers.require_contact_permission(ActionType.VIEW)
    with tenant_scope(201), permission_scope(None):
        with pytest.raises(PermissionError):
            managers.require_contact_permission(ActionType.VIEW)


@pytest.mark.asyncio
async def test_api_export_requires_its_extra_permission(monkeypatch):
    from app.api.v1.endpoints import contacts

    app = FastAPI()
    app.include_router(contacts.router, prefix="/contacts")
    app.dependency_overrides[contacts.router.dependencies[0].dependency] = lambda: None
    export_route = next(route for route in contacts.router.routes if route.path.endswith("/export"))
    extra_dependency = export_route.dependencies[-1].dependency
    # No override of EXPORT: even a permitted reader cannot export implicitly.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/contacts/1/export")).status_code in {401, 403}
    app.dependency_overrides[extra_dependency] = lambda: None
    monkeypatch.setattr(contacts.ContactPrivacy, "export_contact", AsyncMock(return_value={"identities": []}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/contacts/1/export")).json() == {"identities": []}
        monkeypatch.setattr(contacts.ContactPrivacy, "export_contact", AsyncMock(side_effect=ContactUnavailable()))
        assert (await client.get("/contacts/99/export")).status_code == 404


def test_migration_shape_matches_models_without_executing_sql(monkeypatch):
    """Capture Alembic operations as metadata. This is NOT PostgreSQL validation."""
    import importlib.util
    from pathlib import Path

    from sqlalchemy import Column, Integer, MetaData, Table

    path = Path(__file__).resolve().parents[2] / "migrations/versions/0130_contact_core.py"
    spec = importlib.util.spec_from_file_location("contact_core_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    metadata = MetaData(schema=settings.DB_SCHEMA)
    Table("tenants", metadata, Column("id", Integer, primary_key=True))
    Table("workspace_connections", metadata, Column("id", Integer, primary_key=True), Column("tenant_id", Integer))
    created, dropped, operations = {}, [], []
    bind = SimpleNamespace(dialect=postgresql.dialect(), execute=Mock())

    def create_table(name, *args, **kwargs):
        created[name] = Table(name, metadata, *args, **kwargs)

    fake_op = SimpleNamespace(
        create_table=create_table,
        create_index=Mock(),
        get_bind=lambda: bind,
        execute=lambda statement: operations.append(statement),
        drop_table=lambda name, **kw: dropped.append(name),
    )
    monkeypatch.setattr(migration, "op", fake_op)
    migration.upgrade()
    assert set(created) == set(migration.TABLES)
    for model in (Contact, ContactIdentity, ContactConsent, ContactMergeJournal):
        actual, expected = created[model.__tablename__], model.__table__
        assert set(actual.c.keys()) == set(expected.c.keys())
        for column in actual.c:
            wanted = expected.c[column.name]
            assert column.nullable == wanted.nullable
            assert str(column.type.compile(dialect=bind.dialect)) == str(wanted.type.compile(dialect=bind.dialect))
        signature = lambda fk: (tuple(fk.column_keys), tuple(e.target_fullname for e in fk.elements), fk.ondelete)
        assert {signature(fk) for fk in actual.foreign_key_constraints} == {
            signature(fk) for fk in expected.foreign_key_constraints
        }
    assert sum("CREATE TRIGGER" in sql for sql in operations) == 4
    assert "OLD.pii_updated_at + 1" in operations[0]
    assert all("public." not in sql for sql in operations) if settings.DB_SCHEMA != "public" else True
    migration.downgrade()
    assert dropped == list(reversed(migration.TABLES))
    assert "DROP FUNCTION" in operations[-1]


@pytest.mark.parametrize(
    "items,next_id", [([], None), ([{"display_name": "<script>bad</script>", "preferred_channel": None}], 5)]
)
def test_template_escapes_personal_names_and_has_empty_state(items, next_id):
    from pathlib import Path

    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(Path(__file__).resolve().parents[2] / "app/web/templates"),
        autoescape=select_autoescape(),
        extensions=["jinja2.ext.do"],
    )
    env.filters["ui_label"] = str
    env.globals["static_v"] = lambda path: "/static/" + path
    env.globals["url_for"] = lambda name, **kw: "/static/" + kw.get("path", "")
    rendered = env.get_template("web/contacts.html").render(
        request=SimpleNamespace(query_params={}),
        user=None,
        tenant=None,
        section="contacts",
        items=items,
        next_before_id=next_id,
    )
    assert "<script>bad</script>" not in rendered
    assert "Контактов пока нет" in rendered if not items else "&lt;script&gt;bad&lt;/script&gt;" in rendered
    assert "/app/contacts?before_id=5" in rendered if next_id else "Следующие контакты" not in rendered


def test_contact_navigation_matches_registered_route_and_permission():
    from app.web import web_router
    from app.web.nav import NAV_ITEMS

    item = next(item for item in NAV_ITEMS if item.key == "contacts")
    assert item.href == "/app/contacts" and item.ready
    assert item.group == "communications" and item.required_permission == "contacts.contact.view"
    app = FastAPI()
    app.include_router(web_router)
    assert "/app/contacts" in app.openapi()["paths"]
