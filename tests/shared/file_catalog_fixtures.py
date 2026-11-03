"""Reusable synthetic file-catalog fixtures; no live accounts or provider calls."""

import asyncio
import secrets
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError

from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.integrations.catalog import CATALOG_VERSION
from app.integrations.file_workspace.yandex_disk import DiskItem, DiskPage
from app.models import (
    FileCatalogObject,
    FileCatalogObservation,
    FileCatalogScan,
    PlatformRole,
    Tenant,
    TenantUser,
    User,
    WorkspaceConnection,
    WorkspaceConnectionSecret,
    WorkspaceRole,
)
from app.models.managers import file_catalog_manager as module
from app.models.managers.file_catalog_manager import CatalogCapacityError, CatalogInputError, FileCatalogManager
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.utils.crypto import encrypt_secret

manager = FileCatalogManager()


@pytest.fixture
async def data():
    suffix = secrets.token_hex(5)
    async with async_session_maker() as session, session.begin():
        role = await session.scalar(select(PlatformRole.id).where(PlatformRole.codename == "user"))
        roles = dict((await session.execute(select(WorkspaceRole.codename, WorkspaceRole.id))).all())
        users = [
            User(
                username=f"p12t12-{suffix}-{i}",
                email=f"p12t12-{suffix}-{i}@example.test",
                hashed_password="synthetic-only",
                role_id=role,
                is_active=True,
            )
            for i in range(2)
        ]
        tenants = [Tenant(name="P12-T12 mock", slug=f"p12t12-{suffix}-{i}") for i in range(2)]
        session.add_all(users + tenants)
        await session.flush()
        session.add_all(
            [
                TenantUser(
                    tenant_id=t.id,
                    user_id=u.id,
                    channel="web",
                    external_user_id=str(u.id),
                    role_id=roles["manager"],
                    is_active=True,
                )
                for t in tenants
                for u in users
            ]
        )
        connections = []
        for i, (tenant, owner) in enumerate(((0, 0), (0, 1), (1, 0))):
            conn = WorkspaceConnection(
                tenant_id=tenants[tenant].id,
                owner_user_id=users[owner].id,
                platform="yandex_disk",
                provider_id="yandex_disk",
                auth_method="oauth_token",
                schema_version=CATALOG_VERSION,
                label="Synthetic drive",
                account_key=f"p12t12-{suffix}-{i}",
                is_active=True,
                state="unverified",
                config={"file_policy": {"version": 1, "root": "disk:/work", "restrictions": []}},
            )
            session.add(conn)
            await session.flush()
            # A synthetic encrypted credential allows MockTransport reader integration.
            session.add(
                WorkspaceConnectionSecret(
                    connection_id=conn.id, secret_encrypted=encrypt_secret("p12t12-synthetic-token")
                )
            )
            connections.append(conn.id)
    result = SimpleNamespace(users=users, tenants=tenants, connections=connections, roles=roles)
    try:
        yield result
    finally:
        async with async_session_maker() as session, session.begin():
            await session.execute(delete(Tenant).where(Tenant.id.in_([t.id for t in tenants])))
            await session.execute(delete(User).where(User.id.in_([u.id for u in users])))
        async with async_session_maker() as session:
            assert not await session.scalar(
                select(func.count()).select_from(WorkspaceConnection).where(WorkspaceConnection.id.in_(connections))
            )
            assert not await session.scalar(
                select(func.count())
                .select_from(FileCatalogObject)
                .where(FileCatalogObject.connection_id.in_(connections))
            )
            assert not await session.scalar(
                select(func.count()).select_from(FileCatalogScan).where(FileCatalogScan.connection_id.in_(connections))
            )
            assert not await session.scalar(
                select(func.count())
                .select_from(FileCatalogObservation)
                .where(FileCatalogObservation.tenant_id.in_([t.id for t in tenants]))
            )


@contextmanager
def actor(data, user=0, tenant=0, connection=0):
    with tenant_scope(data.tenants[tenant].id), permission_scope(data.users[user]):
        yield {
            "tenant_id": data.tenants[tenant].id,
            "actor_id": data.users[user].id,
            "connection_id": data.connections[connection],
        }


def page(*names, folder="disk:/work", kind="file", next_offset=None):
    return DiskPage(tuple(DiskItem(folder + "/" + name, name, kind) for name in names), next_offset)


async def admit(args, *names, folder="disk:/work", kind="file"):
    ticket = await manager.begin_page(**args, folder=folder)
    return await manager.admit_page(**args, ticket=ticket, page=page(*names, folder=folder, kind=kind))


async def configure(data, *, root="disk:/work", restrictions=(), extra=None):
    config = {"file_policy": {"version": 1, "root": root, "restrictions": list(restrictions)}}
    config.update(extra or {})
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(WorkspaceConnection).where(WorkspaceConnection.id == data.connections[0]).values(config=config)
        )
