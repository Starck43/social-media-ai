"""Local PostgreSQL digest approval acceptance; never run against production.

Uses current conftest (read-only schema validation), real managers and runtime.
Only publisher and model continuation are replaced. Run sequentially via OpenCode.
This module also provides owned-fixture helpers for the sibling ASGI module.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager, contextmanager
from copy import copy, deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

# isort: split
# Refuse --noconftest/direct execution BEFORE importing application engines/models.
_expected_conftest = Path(__file__).resolve().with_name("conftest.py")
_conftest = next(
    (
        module
        for module in tuple(sys.modules.values())
        if getattr(module, "__file__", None) and Path(module.__file__).resolve() == _expected_conftest
    ),
    None,
)
if _conftest is None or getattr(_conftest, "TEST_SCHEMA", None) != "test_schema":
    raise RuntimeError("STOP: current root conftest and test_schema redirect are required; do not use --noconftest")
_target = urlsplit(getattr(_conftest, "TEST_DATABASE_URL", ""))
if (
    _target.hostname not in ("localhost", "127.0.0.1")
    or (_target.port or 5432) != 5432
    or _target.path != "/social_manager"
):
    raise RuntimeError("STOP: only the pre-approved local social_manager/test_schema target is allowed")
# isort: split

from sqlalchemy import text

from app.agent import runtime
from app.agent.confirmation import make_pending_intent
from app.agent.identity import resolve_runtime_identity
from app.agent.tools import get_tool
from app.channels.base import Inbound
from app.core.config import settings
from app.core.database import async_engine, async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import current_tenant_id, tenant_scope
from app.models import AgentSession, PlatformRole, User, WorkspaceRole
from app.models.managers.agent_session_manager import agent_sessions
from app.models.managers.tenant_manager import tenant_channels, tenant_users, tenants

# A prior app import must not silently retain the working engine/model schema.
if settings.DB_SCHEMA != "test_schema" or AgentSession.__table__.schema != "test_schema":
    raise RuntimeError("STOP: application settings/model schema differ from the reviewed test target")
if (
    async_engine.url.host not in ("localhost", "127.0.0.1")
    or (async_engine.url.port or 5432) != 5432
    or async_engine.url.database != "social_manager"
):
    raise RuntimeError("STOP: application engine differs from the reviewed local test target")

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]
ARGS = {"period": "week", "group_by": "sources", "time_breakdown": True}


async def role_id(model, code):
    row = await model.objects.filter(codename=code).first()
    assert row is not None, f"Existing test catalog lacks {model.__name__}:{code}; do not seed"
    return row.id


@dataclass
class DigestWorkspace:
    """Own ONLY newly generated rows; never edit seeded roles or grant links."""

    tenant_id: int
    users: list = field(default_factory=list)
    owner: object = None
    password: str = field(default_factory=lambda: f"Synthetic-{uuid4().hex}")

    async def user(self, platform="user"):
        code_id = await role_id(PlatformRole, platform)
        name = f"digest-accept-{uuid4().hex}"
        user = await User.objects.create_user(
            username=name,
            email=f"{name}@example.invalid",
            password=self.password,
            role_id=code_id,
            is_active=True,
        )
        self.users.append(user.id)
        return user

    async def member(self, user, role="analyst", *, ai=True, channel="web"):
        code_id = await role_id(WorkspaceRole, role)
        with tenant_scope(self.tenant_id):
            return await tenant_users.create(
                tenant_id=self.tenant_id,
                channel=channel,
                external_user_id=str(user.id),
                user_id=user.id,
                role_id=code_id,
                ai_chat_enabled=ai,
                is_active=True,
            )

    @contextmanager
    def as_owner(self):
        with tenant_scope(self.tenant_id), permission_scope(self.owner, is_owner=True):
            yield

    async def change(self, membership, change):
        """Normal team services where available; owned deletion is fault injection."""
        if change in ("admin", "viewer"):
            code_id = await role_id(WorkspaceRole, change)
            with self.as_owner():
                assert await tenant_users.change_member_role(
                    tenant_id=self.tenant_id,
                    membership_id=membership.id,
                    role_id=code_id,
                )
        elif change == "ai":
            with self.as_owner():
                assert await tenant_users.set_ai_access(
                    tenant_id=self.tenant_id,
                    membership_id=membership.id,
                    enabled=False,
                )
        elif change == "inactive":
            with self.as_owner():
                assert await tenant_users.disable_web_member(
                    tenant_id=self.tenant_id,
                    membership_id=membership.id,
                )
        elif change == "missing":
            # Deliberate deletion of this fixture's membership, not historical cleanup.
            with tenant_scope(bypass=True):
                assert await tenant_users.delete_by_id(membership.id)
        else:
            raise AssertionError(f"Unknown mutation: {change}")

    async def cleanup(self):
        with tenant_scope(bypass=True):
            await tenants.delete_by_id(self.tenant_id)
            for user_id in reversed(self.users):
                await User.objects.delete_by_id(user_id)
            assert await tenants.get(id=self.tenant_id) is None
            for user_id in self.users:
                assert await User.objects.get(id=user_id) is None
            assert not list(await AgentSession.objects.filter(tenant_id=self.tenant_id))


@asynccontextmanager
async def owned_workspace():
    # Catalog reads are intentionally not a fresh-schema bootstrap.
    await role_id(PlatformRole, "user")
    await role_id(WorkspaceRole, "owner")
    tenant = await tenants.create(
        slug=f"digest-accept-{uuid4().hex}",
        name="Synthetic digest acceptance",
        plan="business",
        is_active=True,
        daily_cost_limit=0,
    )
    workspace = DigestWorkspace(tenant.id)
    try:
        workspace.owner = await workspace.user()
        await workspace.member(workspace.owner, "owner", ai=False)
        yield workspace
    finally:
        await workspace.cleanup()


@pytest.fixture
 def_placeholder():
    pass
