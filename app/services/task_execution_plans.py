"""Permission-scoped publishing, preview and loading of execution revisions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import or_, select

from app.core.access_catalog import workspace_grants
from app.core.database import with_db_session
from app.core.permissions import get_current_user, has_permission
from app.core.pii import decrypt_pii, encrypt_pii
from app.integrations.catalog.task_capabilities import CAPABILITIES, Direction, ResourceKind
from app.integrations.task_resources import (
    ResolutionIssue,
    ResourceSelection,
    ScopedResource,
    TelegramGrantPin,
    resolve_task_resource,
)
from app.models import AgentScenario, Platform, Source
from app.models.planner.execution_plan import TaskPlanBinding, TaskPlanRevision
from app.services.ai.scenario_schema import compile_scenario_output
from app.services.execution_plan_contracts import (
    MAX_BINDINGS,
    ExecutionPlan,
    ExecutionPlanError,
    parse_execution_plan,
)
from app.services.planner.canonical_tasks import task_service
from app.services.social.connector_authorizations import AuthorizationUnavailable
from app.services.social.telegram_grant_access import source_grant_selection
from app.services.social.vk_connections import VKConnectionDenied, check_selected_access
from app.services.social.vk_task_access import plan_required_methods
from app.services.task_plans import parse_task_plan
from app.types import ActionType, PlatformType

SCENARIO_FIELDS = (
    "id",
    "name",
    "content_types",
    "analysis_types",
    "scope",
    "base_prompt",
    "media_overrides",
    "summary_prompt",
    "max_tokens",
    "text_llm_model_id",
    "image_llm_model_id",
    "video_llm_model_id",
    "llm_strategy",
)
MAX_REVISION_BYTES = 512 * 1024


@dataclass(frozen=True)
class PlanReference:
    task_id: int
    revision: int
    task_version: int


@dataclass(frozen=True)
class AdmissionPlan:
    """Internal only. Callers must not serialize this as a preview or tool result."""

    reference: PlanReference
    plan: ExecutionPlan = field(repr=False)
    methodology_json: str = field(repr=False)
    resources: tuple[ScopedResource, ...] = field(repr=False)


def _permits(identity, model, action):
    return (model, action) in workspace_grants(identity[2]) and has_permission(get_current_user(), model, action)


def _encode(data):
    try:
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise ExecutionPlanError("invalid_execution_plan") from None
    if len(encoded.encode("utf-8")) > MAX_REVISION_BYTES:
        raise ExecutionPlanError("execution_plan_too_large")
    return encoded


async def _active_scenarios(session, identity, plan):
    ids = {step.scenario_id for step in plan.steps if step.scenario_id is not None}
    if not ids:
        return ()
    if not _permits(identity, "agentscenario", ActionType.VIEW):
        raise ExecutionPlanError("scenario_unavailable")
    rows = tuple(
        await session.scalars(
            select(AgentScenario)
            .where(AgentScenario.tenant_id == identity[0], AgentScenario.id.in_(ids), AgentScenario.is_active.is_(True))
            .order_by(AgentScenario.id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
    )
    if len(rows) != len(ids):
        raise ExecutionPlanError("scenario_unavailable")
    return rows


def _pin_methodology(rows):
    snapshots = {}
    for scenario in rows:
        snapshot = {name: getattr(scenario, name) for name in SCENARIO_FIELDS}
        strategy = scenario.llm_strategy
        snapshot["llm_strategy"] = strategy.value if strategy is not None else None
        try:
            snapshot["output_schema"] = compile_scenario_output(scenario).schema
        except ValueError:
            raise ExecutionPlanError("invalid_scenario_contract") from None
        snapshots[str(scenario.id)] = snapshot
    return {"renderer_version": 1, "scenarios": snapshots}


def _revision_definition(plan, scenarios):
    data = plan.model_dump(mode="json")
    parse_execution_plan(_encode(data))
    return _encode({"plan": data, "methodology": _pin_methodology(scenarios)})


async def _resolve(session, identity, capability, selection):
    return await resolve_task_resource(
        session,
        tenant_id=identity[0],
        actor_id=identity[1],
        capability_id=capability,
        direction=Direction.READ,
        selection=selection,
    )


async def _dynamic_sources(session, identity, social):
    """Resolve all-authorized scope before admitting work. Discovery never runs here."""
    if social.source_scope != "all":
        return ()
    platforms = [PlatformType.VK if name == "vk" else PlatformType.TELEGRAM for name in social.platforms]
    candidates = await session.execute(
        select(Source, Platform.platform_type)
        .join(Platform)
        .where(
            Source.tenant_id == identity[0],
            Source.is_active.is_(True),
            Source.owner_task_id.is_(None),
            or_(Source.owner_user_id == identity[1], Source.owner_user_id.is_(None)),
            Platform.platform_type.in_(platforms),
            Platform.is_active.is_(True),
        )
        .order_by(Source.id)
        .limit(MAX_BINDINGS + 1)
        .execution_options(populate_existing=True)
    )
    candidates = candidates.all()
    if len(candidates) > MAX_BINDINGS:
        raise ExecutionPlanError("dynamic_scope_too_large")
    resources = []
    for source, platform in candidates:
        if platform == PlatformType.VK:
            selection = ResourceSelection(ResourceKind.SOCIAL_SOURCE, source.id, vk_access=social.vk_access)
            capability = "vk.posts.read"
        else:
            try:
                grant = source_grant_selection(source)
                if grant.owner_user_id != identity[1]:
                    continue
                pin = TelegramGrantPin(grant.grant_id, grant.authorization_revision)
            except (AuthorizationUnavailable, ValueError):
                continue
            selection = ResourceSelection(ResourceKind.SOCIAL_SOURCE, source.id, telegram_grant=pin)
            capability = "telegram.session.read"
        resource = await _resolve(session, identity, capability, selection)
        if isinstance(resource, ScopedResource):
            resources.append(resource)
    if not resources:
        raise ExecutionPlanError("no_authorized_sources")
    return tuple(resources)


async def _validate_resources(session, identity, plan):
    if any(step.operation == "messages.store_groups" for step in plan.steps) and not _permits(
        identity, "unifiedmessage", ActionType.CREATE
    ):
        raise ExecutionPlanError("sink_unavailable")
    resolved = []
    if plan.social_plan is not None:
        if not all(_permits(identity, "source", action) for action in (ActionType.VIEW, ActionType.ANALYZE)):
            raise ExecutionPlanError("resource_unavailable")
        social = parse_task_plan(plan.social_plan)
        if social.source_scope == "discovery" and social.platforms != ("vk",):
            raise ExecutionPlanError("unsupported_discovery_route")
        if "vk" in social.platforms:
            if social.vk_access is None:
                raise ExecutionPlanError("select_resource")
            try:
                decision = await check_selected_access(
                    social.vk_access,
                    tenant_id=identity[0],
                    actor_id=identity[1],
                    required_methods=plan_required_methods(plan.social_plan),
                )
            except VKConnectionDenied:
                raise ExecutionPlanError("resource_unavailable") from None
            if not decision.allowed:
                raise ExecutionPlanError("resource_unavailable")
        resolved.extend(await _dynamic_sources(session, identity, social))
    for binding in plan.bindings:
        resource = await _resolve(session, identity, binding.capability, binding.selection())
        if isinstance(resource, ResolutionIssue):
            raise ExecutionPlanError(resource.value)
        resolved.append(resource)
    return tuple(resolved)


def _binding_values(binding):
    kind = CAPABILITIES[binding.capability].kind
    return {
        "kind": kind.value,
        "source_id": binding.resource_id if kind == ResourceKind.SOCIAL_SOURCE else None,
        "connection_id": binding.resource_id if kind == ResourceKind.MAIL_FOLDER else None,
    }


class ExecutionPlanService:
    @staticmethod
    async def _revision(session, task, revision=None):
        query = select(TaskPlanRevision).where(
            TaskPlanRevision.tenant_id == task.tenant_id, TaskPlanRevision.task_id == task.id
        )
        if revision is not None:
            query = query.where(TaskPlanRevision.revision == revision)
        return await session.scalar(
            query.order_by(TaskPlanRevision.revision.desc()).limit(1).execution_options(populate_existing=True)
        )

    @with_db_session
    async def preview(self, task_id: int, raw_json: str, session=None) -> dict:
        """Validate a draft without persisting it, scheduling anything or returning secrets."""
        identity = await task_service._actor(session, ActionType.VIEW)
        task = await task_service._task(session, task_id, identity)
        if not (
            _permits(identity, "plannertask", ActionType.UPDATE)
            and await task_service._can_edit(session, task, identity)
        ):
            raise PermissionError("task_update_denied")
        plan = parse_execution_plan(raw_json)
        rows = await _active_scenarios(session, identity, plan)
        await _validate_resources(session, identity, plan)
        _revision_definition(plan, rows)
        return {
            "task_id": task.id,
            "task_version": task.version,
            "operations": [step.operation for step in plan.steps],
            "scheduled": plan.trigger.type == "schedule",
            "dry_run": plan.dry_run,
        }

    @with_db_session
    async def publish(self, task_id: int, expected_version: int, raw_json: str, session=None) -> PlanReference:
        identity = await task_service._actor(session, ActionType.UPDATE)
        if not _permits(identity, "plannertask", ActionType.UPDATE):
            raise PermissionError("task_update_denied")
        task = await task_service._task(session, task_id, identity, lock=True)
        task_service._editable(task, expected_version)
        plan = parse_execution_plan(raw_json)
        rows = await _active_scenarios(session, identity, plan)
        await _validate_resources(session, identity, plan)
        previous = await self._revision(session, task)
        revision = 1 if previous is None else previous.revision + 1
        definition = _revision_definition(plan, rows)
        encrypted = encrypt_pii(definition)
        async with session.begin_nested():
            session.add(
                TaskPlanRevision(
                    tenant_id=task.tenant_id, task_id=task.id, revision=revision, definition_encrypted=encrypted
                )
            )
            await session.flush()
            session.add_all(
                TaskPlanBinding(
                    tenant_id=task.tenant_id,
                    task_id=task.id,
                    revision=revision,
                    name=binding.name,
                    **_binding_values(binding),
                )
                for binding in plan.bindings
            )
            task.version += 1
            await session.flush()
        return PlanReference(task.id, revision, task.version)

    @with_db_session
    async def get_reference(self, task_id: int, *, revision: int | None = None, session=None) -> PlanReference | None:
        """Read current or historical revision identity using task visibility."""
        identity = await task_service._actor(session, ActionType.VIEW)
        task = await task_service._task(session, task_id, identity)
        if revision is not None and (type(revision) is not int or revision <= 0):
            raise ExecutionPlanError("invalid_plan_revision")
        stored = await self._revision(session, task, revision)
        return PlanReference(task.id, stored.revision, task.version) if stored else None

    @with_db_session
    async def load_for_admission(self, task_id: int, expected_revision: int, session=None) -> AdmissionPlan:
        """Load the current pinned plan in the caller's admission transaction.

        T04 still owns run rights, trigger claims, billing and dispatch-time checks.
        An archived revision is never used to admit new work.
        """
        identity = await task_service._actor(session, ActionType.VIEW)
        task = await task_service._task(session, task_id, identity)
        if not (
            _permits(identity, "plannertask", ActionType.UPDATE)
            and await task_service._can_edit(session, task, identity)
        ):
            raise PermissionError("task_update_denied")
        stored = await self._revision(session, task)
        if (
            stored is None
            or type(expected_revision) is not int
            or stored.revision != expected_revision
            or task.status in {"done", "cancelled"}
        ):
            raise ExecutionPlanError("plan_revision_unavailable")
        definition = json.loads(decrypt_pii(stored.definition_encrypted))
        plan = parse_execution_plan(_encode(definition["plan"]))
        rows = await session.scalars(
            select(TaskPlanBinding)
            .where(
                TaskPlanBinding.tenant_id == task.tenant_id,
                TaskPlanBinding.task_id == task.id,
                TaskPlanBinding.revision == stored.revision,
            )
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        actual = {
            row.name: {"kind": row.kind, "source_id": row.source_id, "connection_id": row.connection_id} for row in rows
        }
        if actual != {binding.name: _binding_values(binding) for binding in plan.bindings}:
            raise ExecutionPlanError("resource_unavailable")
        await _active_scenarios(session, identity, plan)
        resources = await _validate_resources(session, identity, plan)
        return AdmissionPlan(
            PlanReference(task.id, stored.revision, task.version), plan, _encode(definition["methodology"]), resources
        )


execution_plan_service = ExecutionPlanService()
