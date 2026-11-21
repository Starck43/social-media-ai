"""Execution revisions and resource fencing on isolated PostgreSQL."""

import json
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError

from app.core.access_catalog import workspace_grants
from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import get_current_user, permission_scope
from app.core.tenant_context import tenant_scope
from app.integrations.catalog import CATALOG_VERSION
from app.integrations.task_resources import WorkspaceAccountPin
from app.models import AgentScenario, Platform, PlatformRole, Source, Tenant, TenantUser, User, WorkspaceRole
from app.models.integrations.connection import WorkspaceConnection, WorkspaceConnectionSecret
from app.models.planner.execution_plan import TaskPlanBinding
from app.services import task_execution_plans
from app.services.execution_plan_contracts import Binding, ExecutionPlanError, map_social_plan
from app.services.planner.canonical_tasks import CreateTask, task_service
from app.services.planner.tasks import PlannerConflict, PlannerUnavailable
from app.services.task_execution_plans import execution_plan_service
from app.types import ActionType, PlatformType, SourceType
from tests.shared.execution_plan_payloads import mail_plan
from tests.support.schema_guard import require_isolated_schema

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


@pytest.fixture
async def workspace(monkeypatch):
    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    tag = f"plan-{uuid4().hex}"
    async with async_session_maker() as session:
        role = await session.scalar(select(PlatformRole.id).where(PlatformRole.codename == "user"))
        owner_role = await session.scalar(select(WorkspaceRole.id).where(WorkspaceRole.codename == "owner"))
        viewer_role = await session.scalar(select(WorkspaceRole.id).where(WorkspaceRole.codename == "viewer"))
        user = User(
            username=tag, email=f"{tag}@example.invalid", hashed_password="synthetic", role_id=role, is_active=True
        )
        other_user = User(
            username=f"{tag}-other",
            email=f"{tag}-other@example.invalid",
            hashed_password="synthetic",
            role_id=role,
            is_active=True,
        )
        session.add_all([user, other_user])
        await session.flush()
        tenants = [Tenant(name=f"{tag}-{i}", slug=f"{tag}-{i}", plan_holder_user_id=user.id) for i in range(2)]
        session.add_all(tenants)
        await session.flush()
        session.add_all(
            TenantUser(
                tenant_id=tenant.id,
                user_id=user.id,
                channel="web",
                external_user_id=str(user.id),
                role_id=owner_role,
                is_active=True,
            )
            for tenant in tenants
        )
        session.add(
            TenantUser(
                tenant_id=tenants[0].id,
                user_id=other_user.id,
                channel="web",
                external_user_id=str(other_user.id),
                role_id=viewer_role,
                is_active=True,
            )
        )
        await session.commit()

    @contextmanager
    def scope(index=0, *, grants=None):
        effective = workspace_grants("owner") if grants is None else grants
        with tenant_scope(tenants[index].id), permission_scope(user, member_grants=effective):
            yield

    try:
        yield SimpleNamespace(tenants=tenants, user=user, other_user=other_user, viewer_role=viewer_role, scope=scope)
    finally:
        async with async_session_maker() as session, session.begin():
            for tenant in tenants:
                await session.delete(tenant)
            await session.flush()
            await session.delete(user)
            await session.delete(other_user)


async def _connection(workspace):
    async with async_session_maker() as session, session.begin():
        connection = WorkspaceConnection(
            tenant_id=workspace.tenants[0].id,
            owner_user_id=workspace.user.id,
            platform="mailru_mail",
            provider_id="mailru_mail",
            auth_method="app_password",
            schema_version=CATALOG_VERSION,
            label="Synthetic inbox",
            account_key="synthetic@example.invalid",
            config={"imap_folders": ["INBOX"]},
            is_active=True,
            state="verified",
        )
        session.add(connection)
        await session.flush()
        session.add(WorkspaceConnectionSecret(connection_id=connection.id, secret_encrypted="synthetic-fixture"))
        return connection.id, WorkspaceAccountPin.from_connection(connection).digest


async def test_attaching_mail_keeps_identity_and_rechecks_folder_and_connection(workspace):
    connection_id, pin = await _connection(workspace)
    raw = json.dumps(mail_plan(connection_id, pin))
    with workspace.scope():
        note = await task_service.create(CreateTask(title="Review correspondence"))
        assert await execution_plan_service.get_reference(note.id) is None
        preview = await execution_plan_service.preview(note.id, raw)
        assert preview["operations"] == ["mail.read_headers", "mail.group_by_sender", "messages.store_groups"]
        assert pin not in repr(preview)
        assert await execution_plan_service.get_reference(note.id) is None
        first = await execution_plan_service.publish(note.id, note.version, raw)
        assert first.task_id == note.id and first.task_version == note.version + 1
        admitted = await execution_plan_service.load_for_admission(note.id, first.revision)
        assert admitted.resources[0].selection.path == "INBOX"
        assert json.loads(admitted.methodology_json)["scenarios"] == {}
        with pytest.raises(PlannerConflict):
            await execution_plan_service.publish(note.id, note.version, raw)

        async with async_session_maker() as session, session.begin():
            bad = mail_plan(connection_id, pin)
            bad["bindings"][0]["path"] = "Private"
            with pytest.raises(ExecutionPlanError, match="select_resource"):
                await execution_plan_service.publish(note.id, first.task_version, json.dumps(bad), session=session)
            assert await execution_plan_service.get_reference(note.id, session=session) == first

        async with async_session_maker() as session, session.begin():
            stored = await session.scalar(
                text(f'SELECT definition_encrypted FROM "{settings.DB_SCHEMA}".task_plan_revisions')
            )
            assert stored.startswith("pii:v1:") and pin not in stored

        async with async_session_maker() as session, session.begin():
            connection = await session.get(WorkspaceConnection, connection_id)
            connection.state = "reauth"
        with pytest.raises(ExecutionPlanError, match="reauth_required"):
            await execution_plan_service.load_for_admission(note.id, first.revision)

        async with async_session_maker() as session, session.begin():
            await session.delete(await session.get(WorkspaceConnection, connection_id))
        with pytest.raises(ExecutionPlanError, match="resource_unavailable"):
            await execution_plan_service.load_for_admission(note.id, first.revision)
    with workspace.scope(1), pytest.raises(PlannerUnavailable):
        await execution_plan_service.get_reference(note.id)
    with tenant_scope(workspace.tenants[0].id):
        async with async_session_maker() as session, session.begin():
            with pytest.raises(ExecutionPlanError, match="schedule_unavailable"):
                await execution_plan_service.load_for_scheduled_admission(
                    note.id, first.revision, principal_id=workspace.user.id, session=session
                )


async def test_shared_scenario_pins_methodology_until_republish_and_blocks_revocation(workspace, monkeypatch):
    # Use a local authorization double. These tests never query a live VK application.
    async def allow_vk(*args, **kwargs):
        return SimpleNamespace(allowed=True)

    monkeypatch.setattr(task_execution_plans, "check_canonical_read_access", allow_vk)
    async with async_session_maker() as session, session.begin():
        scenario = AgentScenario(tenant_id=workspace.tenants[0].id, name="Synthetic", base_prompt="Method one")
        session.add(scenario)
        await session.flush()
        scenario_id = scenario.id

    def draft(query, hour):
        return map_social_plan(
            {
                "task_plan": {
                    "source_scope": "discovery",
                    "collection": {"queries": [query]},
                    "scenario_id": scenario_id,
                    "trigger": {"type": "schedule", "cron": f"0 {hour} * * *"},
                    "vk_access": {"mode": "api"},
                },
                "collect_before_analyze": True,
            },
            job_type="analyze",
        ).model_dump_json()

    with workspace.scope():
        a = await task_service.create(CreateTask(title="Synthetic task A"))
        b = await task_service.create(CreateTask(title="Synthetic task B"))
        first = await execution_plan_service.publish(a.id, a.version, draft("widgets", 9))
        other = await execution_plan_service.publish(b.id, b.version, draft("gadgets", 10))
        before = await execution_plan_service.load_for_admission(a.id, first.revision)
        second_task = await execution_plan_service.load_for_admission(b.id, other.revision)
        assert before.methodology_json == second_task.methodology_json
        assert before.plan.trigger != second_task.plan.trigger
        assert before.plan.social_plan["collection"] != second_task.plan.social_plan["collection"]
        async with async_session_maker() as session, session.begin():
            (await session.get(AgentScenario, scenario_id)).base_prompt = "Method two"
        assert (
            await execution_plan_service.load_for_admission(a.id, first.revision)
        ).methodology_json == before.methodology_json
        updated = await execution_plan_service.publish(a.id, first.task_version, draft("widgets", 9))
        after = await execution_plan_service.load_for_admission(a.id, updated.revision)
        assert "Method two" in after.methodology_json and "Method one" in before.methodology_json
        assert (await execution_plan_service.get_reference(a.id, revision=1)).revision == 1
        with pytest.raises(ExecutionPlanError, match="plan_revision_unavailable"):
            await execution_plan_service.load_for_admission(a.id, first.revision)

    read_only = workspace_grants("owner") - {("source", ActionType.ANALYZE)}
    with workspace.scope(grants=read_only), pytest.raises(ExecutionPlanError, match="source_analysis_denied"):
        await execution_plan_service.load_for_admission(a.id, updated.revision)

    with workspace.scope(1):
        foreign = await task_service.create(CreateTask(title="Other workspace"))
        with pytest.raises(ExecutionPlanError, match="scenario_unavailable"):
            await execution_plan_service.preview(foreign.id, draft("foreign", 12))

    async with async_session_maker() as session, session.begin():
        await session.delete(await session.get(AgentScenario, scenario_id))
    with workspace.scope(), pytest.raises(ExecutionPlanError, match="scenario_unavailable"):
        await execution_plan_service.load_for_admission(b.id, other.revision)


@pytest.fixture
async def vk_sources(workspace, monkeypatch):
    # Configure synthetic, local VK capability evidence. No requests reach VK.
    monkeypatch.setattr(settings, "VK_SERVICE_KEY", "synthetic-service-key")
    methods = ["wall.get", "groups.getById", "users.get"]
    monkeypatch.setattr(settings, "VK_SERVICE_METHOD_GRANTS", methods)
    monkeypatch.setattr(settings, "VK_SERVICE_METHOD_SCOPES", {method: [] for method in methods})
    async with async_session_maker() as session, session.begin():
        platform = await session.scalar(select(Platform.id).where(Platform.platform_type == PlatformType.VK))
        sources = [
            Source(
                tenant_id=tenant.id,
                platform_id=platform,
                name="Synthetic VK source",
                external_id="101",
                source_type=SourceType.GROUP,
                params={},
                is_active=True,
            )
            for tenant in workspace.tenants
        ]
        session.add_all(sources)
        await session.flush()
        return tuple(source.id for source in sources)


def _vk_collect(source_id):
    vk = {"mode": "api"}
    return map_social_plan(
        {
            "task_plan": {
                "source_scope": "selected",
                "source_ids": [source_id],
                "vk_access": vk,
                "trigger": {"type": "schedule", "cron": "0 9 * * *"},
            }
        },
        job_type="collect",
        bindings=(Binding(name="vk", capability="vk.posts.read", resource_id=source_id, vk_access=vk),),
    ).model_dump_json()


async def test_social_source_binding_enforces_tenant_at_service_and_database(workspace, vk_sources):
    own, foreign = vk_sources
    with workspace.scope():
        note = await task_service.create(CreateTask(title="Synthetic VK collection"))
        with pytest.raises(ExecutionPlanError, match="resource_unavailable"):
            await execution_plan_service.preview(note.id, _vk_collect(foreign))
        saved = await execution_plan_service.publish(note.id, note.version, _vk_collect(own))
        admitted = await execution_plan_service.load_for_admission(note.id, saved.revision)
        assert admitted.resources[0].selection.resource_id == own

        async with async_session_maker() as session, session.begin():
            # Exercise the DB constraint directly, bypassing service authorization.
            with pytest.raises(IntegrityError, match="fk_task_binding_source_scope"):
                async with session.begin_nested():
                    await session.execute(
                        update(TaskPlanBinding)
                        .where(TaskPlanBinding.tenant_id == workspace.tenants[0].id, TaskPlanBinding.task_id == note.id)
                        .values(source_id=foreign)
                    )
                    await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            assert await session.scalar(select(TaskPlanBinding.source_id)) == own

        async with async_session_maker() as session, session.begin():
            await session.delete(await session.get(Source, own))
        assert await execution_plan_service.get_reference(note.id) == saved
        async with async_session_maker() as session:
            binding = await session.scalar(select(TaskPlanBinding))
            assert binding.source_id is None
        with pytest.raises(ExecutionPlanError, match="resource_unavailable"):
            await execution_plan_service.load_for_admission(note.id, saved.revision)


async def test_view_only_scheduled_vk_read_is_pinned_to_verified_principal(workspace, vk_sources, monkeypatch):
    own, _ = vk_sources
    tenant_id = workspace.tenants[0].id
    with workspace.scope():
        note = await task_service.create(CreateTask(title="Scheduled collection", visibility="workspace"))
        saved = await execution_plan_service.publish(note.id, note.version, _vk_collect(own))

    async def scheduled_admission(index=0, principal=None):
        with tenant_scope(workspace.tenants[index].id):
            async with async_session_maker() as session, session.begin():
                result = await execution_plan_service.load_for_scheduled_admission(
                    note.id,
                    saved.revision,
                    principal_id=workspace.user.id if principal is None else principal,
                    session=session,
                )
                assert get_current_user() is None
                return result

    async with async_session_maker() as session, session.begin():
        member = await session.scalar(
            select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.user_id == workspace.user.id)
        )
        member.role_id = workspace.viewer_role

    view_grants = workspace_grants("viewer")
    assert ("source", ActionType.ANALYZE) not in view_grants
    with workspace.scope(grants=view_grants):
        manual = await execution_plan_service.load_for_admission(note.id, saved.revision)
        assert manual.resources[0].selection.resource_id == own
        with pytest.raises(PermissionError, match="scheduled_principal_unavailable"):
            await scheduled_admission()

    admitted = await scheduled_admission()
    assert admitted.resources[0].actor_id == workspace.user.id
    assert admitted.resources[0].selection.resource_id == own

    # Another active viewer of this same shared task can load it manually.
    # It cannot substitute for the principal pinned to the automated revision.
    with tenant_scope(tenant_id), permission_scope(workspace.other_user, member_grants=view_grants):
        assert (await execution_plan_service.load_for_admission(note.id, saved.revision)).resources
    with pytest.raises(PermissionError, match="scheduled_principal_unavailable"):
        await scheduled_admission(principal=workspace.other_user.id)
    with pytest.raises(PlannerUnavailable):
        await scheduled_admission(index=1)

    with monkeypatch.context() as limited:
        limited.setattr(settings, "VK_SERVICE_METHOD_GRANTS", ["wall.get", "users.get"])
        with pytest.raises(ExecutionPlanError, match="resource_unavailable"):
            await scheduled_admission()

    async with async_session_maker() as session, session.begin():
        member = await session.scalar(
            select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.user_id == workspace.user.id)
        )
        member.is_active = False
    with pytest.raises(PermissionError, match="scheduled_principal_unavailable"):
        await scheduled_admission()
