"""LOCAL ONLY: the 1C query history status constraint matches the written states (P17-T08).

Synthetic tenants, synthetic connection rows and synthetic plans only. Every question is
encrypted at rest; rows never carry 1C payloads.
"""

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models.integrations.connection import WorkspaceConnection
from app.models.onec import OneCQueryHistory
from app.services.onec.history import VALID_STATUSES, OneCQueryHistoryRepository
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401

pytestmark = [pytest.mark.asyncio, pytest.mark.tenancy]


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


async def test_the_database_refuses_a_status_the_service_never_writes(world):
    async with async_session_maker() as session, session.begin():
        row = OneCQueryHistory(
            tenant_id=world.tenants[0].id,
            user_id=world.users[0].id,
            connection_id=world.connection_id,
            entity_set="Catalog_Товары",
            plan_json={"entity_set": "Catalog_Товары"},
            plan_fingerprint="b" * 64,
            question_encrypted=None,
            row_count=0,
            duration_ms=0,
            status="planned",
        )
        session.add(row)
        with pytest.raises(IntegrityError):
            await session.flush()
    await session.rollback()


async def test_the_database_accepts_the_statuses_the_service_writes(world):
    repo = OneCQueryHistoryRepository()
    with tenant_scope(world.tenants[0].id), permission_scope(world.users[0]):
        history_id = await repo.record(
            tenant_id=world.tenants[0].id,
            actor_id=world.users[0].id,
            connection_id=world.connection_id,
            entity_set="Catalog_Товары",
            plan_json={"entity_set": "Catalog_Товары"},
            plan_fingerprint="b" * 64,
            question="покажи товары",
            row_count=1,
            duration_ms=2,
            status="executed",
        )
    async with async_session_maker() as session:
        row = await session.get(OneCQueryHistory, history_id)
        assert row is not None and row.status == "executed"


async def test_the_constraint_and_the_repository_agree(world):
    async with async_session_maker() as session:
        definition = await session.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_onec_query_history_status' "
                "AND connamespace = to_regnamespace(:schema)"
            ),
            {"schema": settings.DB_SCHEMA},
        )
    assert definition is not None
    for status in VALID_STATUSES:
        assert f"'{status}'" in definition
    for status in {"planned", "refused"}:
        assert f"'{status}'" not in definition
