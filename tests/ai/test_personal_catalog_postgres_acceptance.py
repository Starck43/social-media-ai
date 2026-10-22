"""Sequential real-PostgreSQL catalog acceptance; never prepares/migrates a schema.

Run only through the approved root conftest and primary R10 runner. Synthetic
rows are owned by this fixture; existing users/roles/catalogs are never changed.
No provider/network calls. Migration/backfill acceptance is a separate gate.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

# STOP before importing application models/engines if the normal redirect is absent.
_expected = Path(__file__).resolve().parents[1] / "conftest.py"
_conftest = next(
    (module for module in tuple(sys.modules.values()) if getattr(module, "__file__", None) == str(_expected)),
    None,
)
if _conftest is None or getattr(_conftest, "TEST_SCHEMA", None) != "test_schema":
    raise RuntimeError("STOP: root conftest/test_schema redirect required; never use --noconftest")
_target = urlsplit(getattr(_conftest, "TEST_DATABASE_URL", ""))
if (
    _target.hostname not in {"localhost", "127.0.0.1"}
    or (_target.port or 5432) != 5432
    or _target.path != "/social_manager"
):
    raise RuntimeError("STOP: only pre-approved local social_manager/test_schema is allowed")

# isort: split
from sqlalchemy import inspect, select, text, update
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import operator_permission_scope, permission_scope, service_permission_scope
from app.core.tenant_context import tenant_scope
from app.models import (
    AgentScenario,
    AgentTask,
    LLMModel,
    LLMProvider,
    Platform,
    PlatformRole,
    Source,
    User,
    WorkspaceRole,
)
from app.models.agent_task import agent_task_sources
from app.models.managers.tenant_manager import tenant_users, tenants
from app.services.ai.personal_catalog import (
    catalog_metadata,
    chat_model_options,
    save_provider_bundle,
    set_default_model,
)
from app.types import SourceType

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


@dataclass
class OwnedCatalog:
    users: list = field(default_factory=list)
    workspace_ids: list[int] = field(default_factory=list)

    @contextmanager
    def as_user(self, user, tenant_id=None):
        with tenant_scope(tenant_id), permission_scope(user, is_owner=tenant_id is not None):
            yield

    async def bundle(self, user, *, uid=None, models=None):
        with self.as_user(user):
            return await save_provider_bundle(
                user_id=user.id,
                provider={
                    "name": "Synthetic catalog",
                    "uid": uid or f"accept-{uuid4().hex}",
                    "base_url": "https://catalog.example.invalid/v1",
                    "api_key": "synthetic-not-a-live-key",
                },
                models=models if models is not None else [{"name": "Synthetic text", "model_id": "synthetic-text"}],
            )

    async def metadata(self, user):
        with self.as_user(user):
            return await catalog_metadata(user_id=user.id)

    async def workspace(self, user):
        role = await WorkspaceRole.objects.filter(codename="owner").first()
        assert role is not None, "STOP: missing seeded workspace owner role; do not seed"
        with tenant_scope(bypass=True), operator_permission_scope():
            workspace = await tenants.create(
                name="Synthetic catalog acceptance", slug=f"catalog-{uuid4().hex}", plan="business", is_active=True
            )
        self.workspace_ids.append(workspace.id)
        with self.as_user(user, workspace.id):
            await tenant_users.create(
                tenant_id=workspace.id,
                channel="web",
                external_user_id=str(user.id),
                user_id=user.id,
                role_id=role.id,
                is_active=True,
            )
        return workspace

    async def task(self, user, workspace):
        with self.as_user(user, workspace.id):
            return await AgentTask.objects.create(
                name=f"catalog-task-{uuid4().hex}", job_type="collect", cron_expr="0 9 * * *", is_active=True
            )

    async def source(self, user, workspace, *, owner_task_id=None):
        platform = await Platform.objects.filter(is_active=True).first()
        assert platform is not None, "STOP: missing seeded active platform; do not seed"
        with self.as_user(user, workspace.id), service_permission_scope("source", "create"):
            return await Source.objects.create(
                name="Synthetic catalog source",
                platform_id=platform.id,
                external_id=f"catalog-{uuid4().hex}",
                source_type=SourceType.GROUP,
                owner_task_id=owner_task_id,
            )

    async def cleanup(self):
        # Remove only fixture-generated workspaces before catalog/user RESTRICT FKs.
        with tenant_scope(bypass=True), operator_permission_scope():
            for tenant_id in reversed(self.workspace_ids):
                await tenants.delete_by_id(tenant_id)
        for user in reversed(self.users):
            with self.as_user(user):
                await LLMProvider.objects.filter(owner_user_id=user.id).delete()
        with tenant_scope(bypass=True), operator_permission_scope():
            for user in reversed(self.users):
                await User.objects.delete_by_id(user.id)


@pytest.fixture
async def owned_catalog():
    data = OwnedCatalog()
    try:
        role = await PlatformRole.objects.filter(codename="user").first()
        assert role is not None, "STOP: missing seeded platform user role; do not seed"
        for _ in range(2):
            name = f"catalog-{uuid4().hex}"
            with tenant_scope(bypass=True), operator_permission_scope():
                user = await User.objects.create_user(
                    username=name,
                    password=f"Synthetic-{uuid4().hex}",
                    email=f"{name}@example.invalid",
                    role_id=role.id,
                    is_active=True,
                )
            data.users.append(user)
        yield data
    finally:
        await data.cleanup()


def model_ids(provider):
    return [row["id"] for row in provider["models"]]


async def test_owner_catalog_works_without_membership_and_uid_is_per_user(owned_catalog):
    data = owned_catalog
    first, second = data.users
    a = await data.bundle(first, uid="synthetic-shared-slug")
    b = await data.bundle(second, uid="synthetic-shared-slug")
    assert a["id"] != b["id"]
    for user, own, foreign in ((first, a, b), (second, b, a)):
        metadata = await data.metadata(user)
        assert [row["id"] for row in metadata["providers"]] == [own["id"]]
        serialized = repr(metadata)
        assert "synthetic-not-a-live-key" not in serialized and "encrypted_api_key" not in serialized
        with data.as_user(user), pytest.raises((ValueError, PermissionError)):
            await save_provider_bundle(user_id=user.id, provider={"id": foreign["id"], "name": "Forbidden"}, models=[])


async def test_bundle_foreign_nth_model_rolls_back_all_earlier_edits(owned_catalog):
    data = owned_catalog
    first, second = data.users
    own = await data.bundle(first)
    foreign = await data.bundle(second)
    original = await data.metadata(first)
    with data.as_user(first), pytest.raises((ValueError, PermissionError)):
        await save_provider_bundle(
            user_id=first.id,
            provider={"id": own["id"], "name": "Must roll back"},
            models=[
                {"id": model_ids(own)[0], "name": "Must also roll back"},
                {"id": model_ids(foreign)[0], "name": "Forbidden"},
            ],
        )
    assert await data.metadata(first) == original


async def test_existing_model_bundle_default_eager_loads_provider_and_preserves_sparse_fields(owned_catalog):
    data = owned_catalog
    user = data.users[0]
    own = await data.bundle(user)
    with data.as_user(user):
        saved = await save_provider_bundle(
            user_id=user.id,
            provider={"id": own["id"], "uid": "synthetic-renamed"},
            models=[{"id": model_ids(own)[0], "is_default": True}],
        )
    assert saved["uid"] == "synthetic-renamed"
    assert saved["models"][0]["is_default"] is True
    assert saved["models"][0]["model_id"] == "synthetic-text"
    assert saved["has_api_key"] is True


async def test_concurrent_defaults_serialize_inside_one_owner(owned_catalog):
    data = owned_catalog
    first, second = data.users
    own = await data.bundle(
        first, models=[{"name": "A", "model_id": "synthetic-a"}, {"name": "B", "model_id": "synthetic-b"}]
    )
    foreign = await data.bundle(
        second, models=[{"name": "Foreign", "model_id": "synthetic-foreign", "is_default": True}]
    )

    async def choose(identity):
        with data.as_user(first):
            await set_default_model(user_id=first.id, model_id=identity)

    await asyncio.wait_for(asyncio.gather(*(choose(identity) for identity in model_ids(own))), timeout=20)
    metadata = await data.metadata(first)
    assert sum(row["is_default"] for provider in metadata["providers"] for row in provider["models"]) == 1
    assert (await data.metadata(second))["providers"][0]["models"][0]["is_default"] is True
    with data.as_user(first), pytest.raises((ValueError, PermissionError)):
        await set_default_model(user_id=first.id, model_id=model_ids(foreign)[0])


async def test_concurrent_duplicate_uid_rolls_back_loser(owned_catalog):
    data = owned_catalog
    user = data.users[0]

    async def create():
        try:
            return await data.bundle(user, uid="synthetic-race")
        except ValueError:
            return None

    results = await asyncio.wait_for(asyncio.gather(create(), create()), timeout=20)
    assert sum(result is not None for result in results) == 1
    assert len((await data.metadata(user))["providers"]) == 1


async def test_database_rejects_cross_owner_provider_fk_and_owner_reassignment(owned_catalog):
    data = owned_catalog
    first, second = data.users
    provider = await data.bundle(first)
    with pytest.raises(IntegrityError) as foreign_fk:
        async with async_session_maker() as session, session.begin():
            session.add(
                LLMModel(
                    provider_id=provider["id"],
                    owner_user_id=second.id,
                    name="Forbidden FK",
                    model_id="synthetic-forbidden",
                )
            )
            await session.flush()
    assert getattr(foreign_fk.value.orig, "sqlstate", None) == "23503"
    with pytest.raises(IntegrityError) as owner_change:
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(LLMProvider).where(LLMProvider.id == provider["id"]).values(owner_user_id=second.id)
            )
    assert getattr(owner_change.value.orig, "sqlstate", None) == "23514"


async def test_same_personal_model_options_in_two_owned_workspaces(owned_catalog):
    data = owned_catalog
    user = data.users[0]
    own = await data.bundle(user)
    workspaces = [await data.workspace(user), await data.workspace(user)]
    expected = set(model_ids(own))
    for workspace in workspaces:
        with data.as_user(user, workspace.id):
            options = await chat_model_options(user_id=user.id, tenant_id=workspace.id)
            assert {row["id"] for row in options} == expected


async def test_source_replacement_rejects_foreign_and_other_task_owned_rows(owned_catalog):
    data = owned_catalog
    user = data.users[0]
    workspace, other = await data.workspace(user), await data.workspace(user)
    task, other_task = await data.task(user, workspace), await data.task(user, workspace)
    source = await data.source(user, workspace)
    foreign = await data.source(user, other)
    lane = await data.source(user, workspace, owner_task_id=other_task.id)
    with data.as_user(user, workspace.id):
        await AgentTask.objects.set_sources(task.id, [source.id])
        for bad_id in (foreign.id, lane.id):
            with pytest.raises(ValueError):
                await AgentTask.objects.set_sources(task.id, [source.id, bad_id])
            async with async_session_maker() as session:
                linked = (
                    (
                        await session.execute(
                            select(agent_task_sources.c.source_id).where(agent_task_sources.c.agent_task_id == task.id)
                        )
                    )
                    .scalars()
                    .all()
                )
            assert set(linked) == {source.id}


async def test_database_catalog_has_composite_fk_and_ownership_guards():
    assert settings.DB_SCHEMA == "test_schema", "STOP: schema redirect changed"
    async with async_session_maker() as session:
        foreign_keys = await session.run_sync(
            lambda sync: inspect(sync.bind).get_foreign_keys("llm_models", schema=settings.DB_SCHEMA)
        )
        assert any(
            fk["constrained_columns"] == ["provider_id", "owner_user_id"]
            and fk["referred_columns"] == ["id", "owner_user_id"]
            for fk in foreign_keys
        )
        guards = set(
            (
                await session.execute(
                    text("""
            SELECT c.relname, t.tgname FROM pg_trigger t
            JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=:schema AND NOT t.tgisinternal AND t.tgenabled <> 'D'
        """),
                    {"schema": settings.DB_SCHEMA},
                )
            ).all()
        )
        assert {
            ("llm_providers", "guard_personal_catalog_owner"),
            ("llm_models", "guard_personal_catalog_owner"),
            ("agent_task_sources", "guard_task_source_binding"),
            ("sources", "guard_workspace_ownership"),
        } <= guards
