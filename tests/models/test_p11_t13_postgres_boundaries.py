"""P11-T13: real query/loader I/O and session ownership, without providers or DDL.

The root conftest validates the existing schema. Every synthetic write belongs
to one outer transaction; owned manager sessions release real PostgreSQL
savepoints, never that outer transaction. Teardown verifies rollback from a
fresh connection. Cross-connection claim/fencing checks live in tests/jobs.
"""

from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import database
from app.core.config import settings
from app.core.tenant_context import tenant_scope
from app.models import AgentTask, Platform, Source, Tenant
from app.models.managers import base_manager as manager_module
from app.models.managers.base_manager import BaseManager, Prefetch
from app.types import SourceType

pytestmark = pytest.mark.tenancy


@pytest.fixture
async def pg_boundaries(monkeypatch):
    target = urlsplit(settings.POSTGRES_URL)
    if (
        target.hostname not in {"localhost", "127.0.0.1", "::1"}
        or (target.port or 5432) != 5432
        or target.path != "/social_manager"
        or settings.DB_SCHEMA != "test_schema"
    ):
        raise RuntimeError("P11-T13 requires the approved local social_manager/test_schema target")
    assert all(model.__table__.schema == "test_schema" for model in (Tenant, AgentTask, Source))
    prefix = "p11-t13-" + uuid4().hex
    engine = database.async_engine
    connection = await engine.connect()
    outer = await connection.begin()
    commits = []

    def reject_commit(_connection):
        commits.append(True)
        raise RuntimeError("P11-T13 rollback fixture forbids physical COMMIT")

    event.listen(engine.sync_engine, "commit", reject_commit)
    try:
        await connection.exec_driver_sql("SET LOCAL statement_timeout = '10s'")
        await connection.exec_driver_sql("SET LOCAL lock_timeout = '3s'")
        factory = async_sessionmaker(
            bind=connection, class_=AsyncSession, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        # CRUD's decorator and QuerySet's execution facade own separate imports.
        monkeypatch.setattr(database, "async_session_maker", factory)
        monkeypatch.setattr(manager_module, "async_session_maker", factory)
        async with factory() as session:
            async with session.begin():
                tenants = [
                    Tenant(name="P11-T13 synthetic", slug=prefix + suffix, plan="business")
                    for suffix in ("-own", "-other")
                ]
                session.add_all(tenants)
                await session.flush()
                platform_id = (await session.execute(select(Platform.id).order_by(Platform.id).limit(1))).scalar_one()
                sources = [
                    Source(
                        tenant_id=tenants[0].id,
                        platform_id=platform_id,
                        name=prefix + suffix,
                        external_id=prefix + suffix,
                        source_type=SourceType.GROUP,
                        is_active=active,
                    )
                    for suffix, active in (("-active", True), ("-inactive", False))
                ]
                session.add_all(sources)
                await session.flush()
                tasks = [
                    AgentTask(
                        tenant_id=tenant.id,
                        name=prefix + suffix,
                        cron_expr="0 0 * * *",
                        job_type="learn",
                        payload={},
                        is_active=False,
                        sources=sources if index == 0 else [],
                    )
                    for index, (tenant, suffix) in enumerate(zip(tenants, ("-own", "-other")))
                ]
                session.add_all(tasks)
                await session.flush()
        yield SimpleNamespace(
            prefix=prefix,
            own=tenants[0].id,
            other=tenants[1].id,
            task=tasks[0].id,
            source_ids={source.id for source in sources},
            active_source=sources[0].id,
            platform=platform_id,
            factory=factory,
            tasks=BaseManager(AgentTask),
            sources=BaseManager(Source),
        )
        assert outer.is_active and not commits
    finally:
        try:
            if outer.is_active:
                await outer.rollback()
            await connection.close()
            async with engine.connect() as verify:
                for model, field in ((Tenant, Tenant.slug), (AgentTask, AgentTask.name), (Source, Source.name)):
                    assert not (await verify.execute(select(model.id).where(field.startswith(prefix)))).all()
                await verify.rollback()
        finally:
            event.remove(engine.sync_engine, "commit", reject_commit)


@pytest.mark.parametrize("path", ["sources.platform", "sources__platform"])
async def test_nested_relations_are_loaded_after_owned_session_closes(pg_boundaries, path):
    fixture = pg_boundaries
    with tenant_scope(fixture.own):
        rows = await (
            fixture.tasks.filter(name__startswith=fixture.prefix)
            .select_related("tenant")
            .prefetch_related(path)
            .order_by(AgentTask.id)
            .all()
        )
    assert [row.id for row in rows] == [fixture.task]
    assert rows[0].tenant.id == fixture.own
    assert {source.id for source in rows[0].sources} == fixture.source_ids
    assert {source.platform.id for source in rows[0].sources} == {fixture.platform}


async def test_filtered_prefetch_preserves_lookup_and_tenant_scope(pg_boundaries):
    fixture = pg_boundaries
    with tenant_scope(fixture.own):
        descriptor = Prefetch(
            "sources",
            queryset=fixture.sources.filter(name__startswith=fixture.prefix),
            filters={"is_active": True},
        )
        rows = await fixture.tasks.filter(id__in=[fixture.task]).prefetch_related(descriptor).all()
    assert [row.id for row in rows] == [fixture.task]
    assert [source.id for source in rows[0].sources] == [fixture.active_source]


async def test_caller_session_keeps_rollback_ownership(pg_boundaries):
    fixture = pg_boundaries
    name = fixture.prefix + "-caller"
    with tenant_scope(fixture.own):
        async with fixture.factory() as session:
            await session.begin()
            await fixture.tasks.create(
                session=session, name=name, cron_expr="0 0 * * *", job_type="learn", payload={}, is_active=False
            )
            assert session.in_transaction()
            await session.rollback()
        assert await fixture.tasks.count(name=name) == 0


async def test_owned_success_and_duplicate_failure_have_separate_transactions(pg_boundaries):
    fixture = pg_boundaries
    name = fixture.prefix + "-owned"
    values = dict(name=name, cron_expr="0 0 * * *", job_type="learn", payload={}, is_active=False)
    with tenant_scope(fixture.own):
        row = await fixture.tasks.create(**values)
        assert (await fixture.tasks.get(id=row.id)).name == name
        with pytest.raises(IntegrityError):
            await fixture.tasks.create(**values)
        assert await fixture.tasks.count(name=name) == 1
        assert (await fixture.tasks.get(id=row.id)).name == name
