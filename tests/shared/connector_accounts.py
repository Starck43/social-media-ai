"""Synthetic personal connector users/workspaces; no real provider or vault data."""

import secrets
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import PlatformRole, Tenant, TenantUser, User, WorkspaceRole


@pytest.fixture
async def connector_data(monkeypatch):
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", Fernet.generate_key().decode())
    suffix = secrets.token_hex(5)
    async with async_session_maker() as session, session.begin():
        platform_role = (
            await session.execute(select(PlatformRole.id).where(PlatformRole.codename == "user"))
        ).scalar_one()
        roles = dict((await session.execute(select(WorkspaceRole.codename, WorkspaceRole.id))).all())
        users = [
            User(
                username=f"p12t14reuse-{suffix}-{i}",
                email=f"p12t14reuse-{suffix}-{i}@example.invalid",
                hashed_password="synthetic-not-a-password",
                role_id=platform_role,
                is_active=True,
            )
            for i in range(2)
        ]
        tenants = [Tenant(name=f"P12-T14 synthetic reuse {i}", slug=f"p12t14reuse-{suffix}-{i}") for i in range(2)]
        session.add_all(users + tenants)
        await session.flush()
        session.add_all(
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
        )
    data = SimpleNamespace(users=users, tenants=tenants, roles=roles)
    try:
        yield data
    finally:
        async with async_session_maker() as session, session.begin():
            await session.execute(delete(Tenant).where(Tenant.id.in_([t.id for t in tenants])))
            await session.execute(delete(User).where(User.id.in_([u.id for u in users])))


@contextmanager
def connector_actor(data, *, user=0, tenant=0):
    with tenant_scope(data.tenants[tenant].id), permission_scope(data.users[user]):
        yield {"tenant_id": data.tenants[tenant].id, "actor_id": data.users[user].id}
