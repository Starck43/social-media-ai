"""LOCAL ONLY: P17-T03 query history against an isolated, migrated test schema.

Synthetic tenants, synthetic connection rows and synthetic plans only. Every
question is encrypted at rest; rows never carry 1C payloads.
"""

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, func, select

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models.integrations.connection import WorkspaceConnection
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.models.onec import OneCQueryHistory
from app.services.onec.history import OneCHistoryError, OneCQueryHistoryRepository
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401

pytestmark = [pytest.mark.asyncio, pytest.mark.tenancy]

PLAN = {
    "entity_set": "Catalog_Товары",
    "select_fields": ["Description", "Цена"],
    "filters": [{"field": "Description", "operator": "eq", "value": "Болт"}],
    "order_by": [{"field": "Цена", "direction": "desc"}],
    "register_function": None,
    "period": None,
    "date_from": None,
    "date_to": None,
    "top": 100,
    "default_currency": "RUB",
}
FINGERPRINT = "b" * 64


@pytest.fixture
async def world(personal_world, monkeypatch):
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", Fernet.generate_key().decode())
    tenant_id = personal_world.tenants[0].id
    async with async_session_maker() as session, session.begin():
        connection = WorkspaceConnection(
            tenant_id=tenant_id,
            owner_user_id=personal_world.users[0].id,
            platform="erp",
            provider_id="onec",
            auth_method="basic",
            account_key="c" * 64,
            label="Synthetic ERP",
            config={"base_url": "https://erp.example.invalid/base", "interface": "odata_standard"},
            state="verified",
            is_active=True,
            schema_version=1,
        )
        session.add(connection)
        await session.flush()
        connection_id = int(connection.id)
    personal_world.connection_id = connection_id  # type: ignore[attr-defined]
    tenant_ids = [tenant.id for tenant in personal_world.tenants]
    try:
        yield personal_world
    finally:
        async with async_session_maker() as session, session.begin():
            await session.execute(delete(OneCQueryHistory).where(OneCQueryHistory.tenant_id.in_(tenant_ids)))
            await session.execute(delete(WorkspaceConnection).where(WorkspaceConnection.tenant_id.in_(tenant_ids)))
        async with async_session_maker() as session:
            for model in (OneCQueryHistory, WorkspaceConnection):
                assert (
                    await session.scalar(select(func.count()).select_from(model).where(model.tenant_id.in_(tenant_ids)))
                    == 0
                )


def as_actor(world, tenant=0, user=0):
    context = tenant_scope(world.tenants[tenant].id)
    grants = permission_scope(world.users[user])
    return context, grants


async def record_entry(repo, world, *, tenant=0, user=0, status="executed", exported_format=None):
    context, grants = as_actor(world, tenant, user)
    with context, grants:
        return await repo.record(
            tenant_id=world.tenants[tenant].id,
            actor_id=world.users[user].id,
            connection_id=world.connection_id,
            entity_set="Catalog_Товары",
            plan_json=PLAN,
            plan_fingerprint=FINGERPRINT,
            question="покажи остатки товара Болт",
            row_count=2,
            duration_ms=15,
            status=status,
            exported_format=exported_format,
        )


async def test_record_encrypts_question_and_returns_metadata(world):
    repo = OneCQueryHistoryRepository()
    history_id = await record_entry(repo, world)
    async with async_session_maker() as session:
        row = await session.get(OneCQueryHistory, history_id)
        assert row is not None
        assert row.question_encrypted and "Болт" not in row.question_encrypted
        assert row.plan_json["entity_set"] == "Catalog_Товары"
        assert row.status == "executed" and row.row_count == 2
        from app.utils.crypto import decrypt_secret

        assert decrypt_secret(row.question_encrypted) == "покажи остатки товара Болт"


async def test_history_is_scoped_to_actor_and_tenant(world):
    repo = OneCQueryHistoryRepository()
    history_id = await record_entry(repo, world)

    context, grants = as_actor(world, 0, 0)
    with context, grants:
        entries = await repo.list_for_actor(tenant_id=world.tenants[0].id, actor_id=world.users[0].id)
    assert [entry.id for entry in entries] == [history_id]
    assert entries[0].entity_set == "Catalog_Товары" and entries[0].exported_format is None

    context, grants = as_actor(world, 0, 1)
    with context, grants:
        other_entries = await repo.list_for_actor(tenant_id=world.tenants[0].id, actor_id=world.users[1].id)
    assert other_entries == ()
    context, grants = as_actor(world, 0, 1)
    with context, grants, pytest.raises(OneCHistoryError) as excinfo:
        # Another actor in the same workspace cannot reuse this plan.
        await repo.load_plan(tenant_id=world.tenants[0].id, actor_id=world.users[1].id, history_id=history_id)
    assert excinfo.value.code == "history_not_found"


async def test_stored_plan_can_be_reused_only_by_its_owner(world):
    repo = OneCQueryHistoryRepository()
    history_id = await record_entry(repo, world)

    context, grants = as_actor(world, 0, 0)
    with context, grants:
        stored = await repo.load_plan(tenant_id=world.tenants[0].id, actor_id=world.users[0].id, history_id=history_id)
    assert stored.entity_set == "Catalog_Товары"
    assert stored.plan_json["filters"][0]["value"] == "Болт"

    context, grants = as_actor(world, 0, 0)
    with context, grants, pytest.raises(OneCHistoryError) as excinfo:
        await repo.load_plan(tenant_id=world.tenants[0].id, actor_id=world.users[0].id, history_id=history_id + 999)
    assert excinfo.value.code == "history_not_found"


async def test_export_marks_history_row(world):
    repo = OneCQueryHistoryRepository()
    history_id = await record_entry(repo, world)
    context, grants = as_actor(world, 0, 0)
    with context, grants:
        await repo.mark_exported(
            tenant_id=world.tenants[0].id, actor_id=world.users[0].id, history_id=history_id, exported_format="xlsx"
        )
        entries = await repo.list_for_actor(tenant_id=world.tenants[0].id, actor_id=world.users[0].id)
    assert entries[0].status == "exported" and entries[0].exported_format == "xlsx"

    context, grants = as_actor(world, 0, 0)
    with context, grants, pytest.raises(OneCHistoryError) as excinfo:
        await repo.mark_exported(
            tenant_id=world.tenants[0].id, actor_id=world.users[0].id, history_id=history_id, exported_format="docx"
        )
    assert excinfo.value.code == "invalid_export_format"


async def test_cross_tenant_access_is_denied(world):
    repo = OneCQueryHistoryRepository()
    history_id = await record_entry(repo, world)
    # Tenant 1 is synthetic and has no access to tenant 0's history: the row is invisible, never shared.
    context, grants = as_actor(world, 1, 0)
    with context, grants:
        entries = await repo.list_for_actor(tenant_id=world.tenants[1].id, actor_id=world.users[0].id)
    assert entries == ()
    context, grants = as_actor(world, 1, 0)
    with context, grants, pytest.raises(OneCHistoryError) as excinfo:
        await repo.load_plan(tenant_id=world.tenants[1].id, actor_id=world.users[0].id, history_id=history_id)
    assert excinfo.value.code == "history_not_found"
