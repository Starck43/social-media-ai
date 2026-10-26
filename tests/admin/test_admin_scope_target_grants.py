"""SQLAdmin relationship options must honor both platform and membership grants."""

import secrets
from contextlib import contextmanager

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.admin.session import AdminAsyncSession
from app.core.database import async_engine
from app.core.permissions import operator_permission_scope, permission_scope
from app.core.tenant_context import tenant_scope
from app.models import AgentTask, Source, Tenant
from app.types import ActionType

pytestmark = pytest.mark.tenancy
ADMIN_SESSION = async_sessionmaker(async_engine, class_=AdminAsyncSession, expire_on_commit=False)


def _uniq(prefix):
    return f"{prefix}-{secrets.token_hex(5)}"


class _Actor:
    id = 919191
    is_active = True
    is_superuser = False

    def __init__(self, grants):
        self.grants = grants

    def has_admin_access(self):
        return True

    def model_permissions(self, model_name):
        return set(self.grants.get(model_name.lower(), ()))

    def has_perm_for(self, model_name, action):
        return action in self.model_permissions(model_name)


@contextmanager
def _console(actor, tenant_id, member_grants):
    with tenant_scope(tenant_id), permission_scope(actor, member_grants=member_grants):
        yield


def _member(*pairs):
    return frozenset((model.lower(), action) for model, action in pairs)


async def _rows():
    with tenant_scope(bypass=True), operator_permission_scope():
        slug = _uniq("target-grants")
        tenant = await Tenant.objects.create(name=slug, slug=slug)
        source = await Source.objects.create(
            tenant_id=tenant.id,
            name=_uniq("source"),
            platform_id=1,
            source_type="GROUP",
            external_id=_uniq("external"),
        )
        # The task target is useful for testing a same-workspace forged FK.
        task = await AgentTask.objects.create(
            tenant_id=tenant.id,
            name=_uniq("task"),
            cron_expr="0 9 * * *",
            job_type="collect",
        )
        return tenant, source, task.id


@pytest.mark.asyncio
async def test_task_grants_do_not_expose_source_choices_without_source_grants():
    tenant, source, _ = await _rows()
    actor = _Actor({"agenttask": {ActionType.VIEW, ActionType.CREATE}})
    member = _member(("agenttask", ActionType.VIEW), ("agenttask", ActionType.CREATE), ("source", ActionType.VIEW))

    with _console(actor, tenant.id, member):
        async with ADMIN_SESSION() as session:
            visible = list((await session.execute(select(Source))).scalars())
    assert visible == []


@pytest.mark.asyncio
async def test_source_platform_grant_is_capped_by_selected_membership():
    tenant, source, _ = await _rows()
    actor = _Actor({"source": {ActionType.VIEW}})
    member = _member(("agenttask", ActionType.VIEW), ("agenttask", ActionType.CREATE))

    with _console(actor, tenant.id, member):
        async with ADMIN_SESSION() as session:
            visible = list((await session.execute(select(Source))).scalars())
    assert visible == []


@pytest.mark.asyncio
async def test_non_view_source_grant_still_allows_relationship_choice_read():
    tenant, source, _ = await _rows()
    actor = _Actor({"source": {ActionType.CREATE}})
    member = _member(("source", ActionType.CREATE))

    with _console(actor, tenant.id, member):
        async with ADMIN_SESSION() as session:
            visible = list((await session.execute(select(Source))).scalars())
    assert [row.id for row in visible] == [source.id]


@pytest.mark.asyncio
async def test_same_workspace_forged_source_owner_task_fk_requires_target_grant():
    tenant, source, task_id = await _rows()
    actor = _Actor({"source": {ActionType.CREATE}})
    member = _member(("source", ActionType.CREATE))

    with _console(actor, tenant.id, member):
        async with ADMIN_SESSION() as session:
            source.owner_task_id = task_id
            session.add(source)
            with pytest.raises(HTTPException) as refused:
                await session.flush()
            assert refused.value.status_code == 403
            await session.rollback()
