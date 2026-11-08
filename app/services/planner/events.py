"""Event templates and tenant-serialized, opt-in planner generation."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class TemplateStep:
    key: str
    title: str
    lead_days: int
    action: str | None = None


@dataclass(frozen=True, slots=True)
class PlannedStep:
    key: str
    title: str
    due_date: date
    action: str | None


# These are local defaults, not a persisted or user-configurable template store.
DEFAULT_TEMPLATES = MappingProxyType(
    {
        "birthday": (
            TemplateStep("gift", "Купить подарок", 7),
            TemplateStep("greeting", "Написать поздравление", 1, "greeting_draft"),
        ),
        "anniversary": (TemplateStep("greeting", "Написать поздравление", 1, "greeting_draft"),),
        "meeting": (TemplateStep("prepare", "Подготовиться к встрече", 1),),
        "deadline": (TemplateStep("prepare", "Подготовиться к сроку", 1),),
    }
)


def expand_template(event_type: str, event_date: date) -> tuple[PlannedStep, ...]:
    """Return date-only steps for a local event date without writing or sending."""
    if not isinstance(event_date, date) or isinstance(event_date, datetime):
        raise ValueError("event_date_must_be_date")
    try:
        steps = DEFAULT_TEMPLATES[event_type]
    except (KeyError, TypeError):
        raise ValueError("unknown_event_template") from None
    try:
        return tuple(
            PlannedStep(step.key, step.title, event_date - timedelta(days=step.lead_days), step.action)
            for step in steps
        )
    except OverflowError:
        raise ValueError("event_date_out_of_range") from None


class EventPlanningUnavailable(LookupError):
    """A task or event is missing, invisible, or belongs to another workspace."""


async def merge_event_lineage(session, *, tenant_id: int, source_id: int, target_id: int):
    """Coalesce keys under the caller's tenant and ordered contact locks."""
    from sqlalchemy import or_, select, update

    from app.models.planner.events import PlannerEventClaim, PlannerEventGroup, PlannerEventRun
    from app.models.planner.task import PlannerTask

    runs = list(
        (
            await session.execute(
                select(PlannerEventRun)
                .where(PlannerEventRun.tenant_id == tenant_id, PlannerEventRun.contact_id.in_([source_id, target_id]))
                .order_by(PlannerEventRun.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    claims = list(
        (
            await session.execute(
                select(PlannerEventClaim)
                .where(
                    PlannerEventClaim.tenant_id == tenant_id, PlannerEventClaim.contact_id.in_([source_id, target_id])
                )
                .order_by(PlannerEventClaim.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    source_claims = {(c.event_type, c.year): c for c in claims if c.contact_id == source_id}
    target_claims = {(c.event_type, c.year): c for c in claims if c.contact_id == target_id}
    target_runs = {(r.event_type, r.year): r for r in runs if r.contact_id == target_id}
    for run in (r for r in runs if r.contact_id == source_id):
        key = (run.event_type, run.year)
        if key not in source_claims:
            claim = PlannerEventClaim(
                tenant_id=tenant_id, contact_id=source_id, run_id=run.id, event_type=run.event_type, year=run.year
            )
            session.add(claim)
            source_claims[key] = claim
        groups = list(
            (
                await session.execute(
                    select(PlannerEventGroup)
                    .where(PlannerEventGroup.tenant_id == tenant_id, PlannerEventGroup.run_id == run.id)
                    .order_by(PlannerEventGroup.id)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        roots = [v for v in [run.root_task_id, *(g.root_task_id for g in groups)] if v is not None]
        greetings = [v for v in [run.greeting_task_id, *(g.greeting_task_id for g in groups)] if v is not None]
        if roots or greetings:
            await session.execute(
                update(PlannerTask)
                .where(
                    PlannerTask.tenant_id == tenant_id,
                    PlannerTask.contact_id == source_id,
                    or_(PlannerTask.id.in_(roots), PlannerTask.parent_id.in_(roots), PlannerTask.id.in_(greetings)),
                )
                .values(contact_id=target_id, version=PlannerTask.version + 1)
            )
        target = target_runs.get(key)
        if target is None and key in target_claims:
            target = (
                await session.execute(
                    select(PlannerEventRun)
                    .where(PlannerEventRun.tenant_id == tenant_id, PlannerEventRun.id == target_claims[key].run_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
        if target is None:
            run.contact_id = target_id
            target = run
            target_runs[key] = run
        elif target.id != run.id:
            session.add(
                PlannerEventGroup(
                    tenant_id=tenant_id,
                    run_id=target.id,
                    root_task_id=run.root_task_id,
                    greeting_task_id=run.greeting_task_id,
                )
            )
            for group in groups:
                group.run_id = target.id
            # Claims, including those belonging to earlier unmerged contacts,
            # must survive the coalescence of their backing run.
            await session.execute(
                update(PlannerEventClaim)
                .where(PlannerEventClaim.tenant_id == tenant_id, PlannerEventClaim.run_id == run.id)
                .values(run_id=target.id)
            )
            await session.flush()
            await session.delete(run)
            for claim in source_claims.values():
                if claim.run_id == run.id:
                    claim.run_id = target.id
        if key not in target_claims:
            claim = PlannerEventClaim(
                tenant_id=tenant_id, contact_id=target_id, run_id=target.id, event_type=key[0], year=key[1]
            )
            session.add(claim)
            target_claims[key] = claim
    for key, claim in source_claims.items():
        if key not in target_claims:
            session.add(
                PlannerEventClaim(
                    tenant_id=tenant_id, contact_id=target_id, run_id=claim.run_id, event_type=key[0], year=key[1]
                )
            )
    await session.flush()


async def unmerge_event_lineage(session, *, tenant_id: int, source_id: int, target_id: int):
    """A restored source inherits closed keys; tasks are never regenerated."""
    from sqlalchemy import select

    from app.models.planner.events import PlannerEventClaim

    claims = list(
        (
            await session.execute(
                select(PlannerEventClaim)
                .where(
                    PlannerEventClaim.tenant_id == tenant_id, PlannerEventClaim.contact_id.in_([source_id, target_id])
                )
                .order_by(PlannerEventClaim.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    source_keys = {(c.event_type, c.year) for c in claims if c.contact_id == source_id}
    for claim in claims:
        key = (claim.event_type, claim.year)
        if claim.contact_id == target_id and key not in source_keys:
            session.add(
                PlannerEventClaim(
                    tenant_id=tenant_id,
                    contact_id=source_id,
                    run_id=claim.run_id,
                    event_type=claim.event_type,
                    year=claim.year,
                )
            )
            source_keys.add(key)
    await session.flush()


class EventPlanningService:
    """Tenant-serialized event generation; never sends or generates a greeting itself."""

    @staticmethod
    def _tenant():
        from app.core.tenant_context import current_tenant_id, is_bypass

        tenant = current_tenant_id()
        if is_bypass() or type(tenant) is not int or tenant <= 0:
            raise PermissionError("event_planning_denied")
        return tenant

    async def set_enabled(self, enabled, *, session):
        """Change opt-in in the caller's transaction; enabling binds the actor."""
        from sqlalchemy import select

        from app.core.access_catalog import workspace_grants
        from app.models.planner.events import PlannerEventSettings
        from app.services.planner.tasks import planner_service
        from app.types import ActionType

        if type(enabled) is not bool:
            raise ValueError("invalid_event_setting")
        tenant = self._tenant()
        identity = await planner_service._actor(session, ActionType.UPDATE)
        # Workspace-wide configuration is limited to management roles; the bound
        # actor must also be able to create tasks and view contacts on each run.
        if identity[2] not in {"owner", "admin", "manager"}:
            raise PermissionError("event_planning_denied")
        grants = workspace_grants(identity[2])
        if enabled and not {("plannertask", ActionType.CREATE), ("contact", ActionType.VIEW)} <= grants:
            raise PermissionError("event_planning_denied")
        config = await session.scalar(
            select(PlannerEventSettings).where(PlannerEventSettings.tenant_id == tenant).with_for_update()
        )
        if config is None:
            if not enabled:
                return False
            config = PlannerEventSettings(tenant_id=tenant)
            session.add(config)
        config.owner_user_id = identity[1] if enabled else None
        await session.flush()
        return enabled

    async def run_daily(self, *, session, reference_date=None):
        """Generate one atomic event group per year; caller owns commit/rollback.

        The initial configuration lookup does not grant authority. Fresh actor,
        workspace, membership, role and config are locked and rechecked in the
        established planner lock order before reading contacts or writing tasks.
        """
        from sqlalchemy import select

        from app.core.access_catalog import workspace_grants
        from app.core.permissions import permission_scope
        from app.models import Contact, Tenant, TenantUser, User, WorkspaceRole
        from app.models.planner.events import PlannerEventClaim, PlannerEventRun, PlannerEventSettings
        from app.services.crm.greetings import crm_greetings_service
        from app.services.planner.contracts import TaskCreate
        from app.services.planner.tasks import planner_service
        from app.types import ActionType

        tenant = self._tenant()
        owner_id = await session.scalar(
            select(PlannerEventSettings.owner_user_id).where(PlannerEventSettings.tenant_id == tenant)
        )
        if owner_id is None:
            return {"status": "disabled", "created": 0}
        user = await session.scalar(
            select(User).where(User.id == owner_id, User.is_active.is_(True)).with_for_update(read=True)
        )
        workspace = await session.scalar(
            select(Tenant).where(Tenant.id == tenant, Tenant.is_active.is_(True)).with_for_update()
        )
        member = await session.scalar(
            select(TenantUser)
            .where(TenantUser.tenant_id == tenant, TenantUser.user_id == owner_id, TenantUser.is_active.is_(True))
            .with_for_update(read=True)
        )
        role = (
            None
            if member is None
            else await session.scalar(
                select(WorkspaceRole.codename).where(WorkspaceRole.id == member.role_id).with_for_update(read=True)
            )
        )
        config = await session.scalar(
            select(PlannerEventSettings).where(PlannerEventSettings.tenant_id == tenant).with_for_update()
        )
        if config is None or config.owner_user_id != owner_id:
            return {"status": "disabled", "created": 0}
        grants = workspace_grants(role)
        if (
            user is None
            or workspace is None
            or not {("plannertask", ActionType.CREATE), ("contact", ActionType.VIEW)} <= grants
        ):
            return {"status": "owner_unavailable", "created": 0}

        # Merge and unmerge take the tenant lock before sorted contact locks.
        # Lock before listing, so the event snapshot cannot race a merge or
        # erasure. This locks the workspace contacts until the caller commits.
        await session.execute(
            select(Contact.id).where(Contact.tenant_id == tenant).order_by(Contact.id.asc()).with_for_update()
        )
        timezone = workspace.timezone
        with permission_scope(user, is_owner=(role == "owner"), member_grants=grants):
            events = await crm_greetings_service.list_upcoming_events(
                tenant_id=tenant,
                actor_id=owner_id,
                horizon_days=7,
                reference_date=reference_date,
                tz_name=timezone,
                emit_notifications=False,
                session=session,
            )
            created = 0
            for event in events["items"]:
                if event["event_type"] not in {"birthday", "anniversary"}:
                    continue
                year = event["event_date"].year
                if not 1970 <= year <= 2100:
                    raise ValueError("event_year_out_of_range")
                previous = await session.scalar(
                    select(PlannerEventClaim.id).where(
                        PlannerEventClaim.tenant_id == tenant,
                        PlannerEventClaim.contact_id == event["contact_id"],
                        PlannerEventClaim.event_type == event["event_type"],
                        PlannerEventClaim.year == year,
                    )
                )
                if previous is not None:
                    continue
                ledger = PlannerEventRun(
                    tenant_id=tenant, contact_id=event["contact_id"], event_type=event["event_type"], year=year
                )
                session.add(ledger)
                await session.flush()  # database unique key is the final concurrent guard
                session.add(
                    PlannerEventClaim(
                        tenant_id=tenant,
                        contact_id=event["contact_id"],
                        event_type=event["event_type"],
                        year=year,
                        run_id=ledger.id,
                    )
                )
                kind_label = "День рождения" if event["event_type"] == "birthday" else "Памятная дата"
                name = str(event["display_name"] or "")[:200]
                root = await planner_service.create(
                    TaskCreate(
                        title=f"{kind_label}: {name}" if name else kind_label,
                        due_date=event["event_date"],
                        timezone=timezone,
                        contact_id=event["contact_id"],
                    ),
                    session=session,
                )
                ledger.root_task_id = root.id
                template = "birthday" if event["event_type"] == "birthday" else "anniversary"
                for step in expand_template(template, event["event_date"]):
                    child = await planner_service.create(
                        TaskCreate(
                            title=step.title,
                            due_date=step.due_date,
                            timezone=timezone,
                            contact_id=event["contact_id"],
                            parent_id=root.id,
                        ),
                        session=session,
                    )
                    if step.action == "greeting_draft":
                        ledger.greeting_task_id = child.id
                created += 1
            await session.flush()
            return {"status": "ok", "created": created}

    async def is_enabled(self, *, session):
        from sqlalchemy import select

        from app.models.planner.events import PlannerEventSettings
        from app.services.planner.tasks import planner_service
        from app.types import ActionType

        tenant = self._tenant()
        identity = await planner_service._actor(session, ActionType.VIEW)
        owner = await session.scalar(
            select(PlannerEventSettings.owner_user_id).where(PlannerEventSettings.tenant_id == tenant)
        )
        return {"enabled": owner is not None, "can_configure": identity[2] in {"owner", "admin", "manager"}}

    async def greeting_task_ids(self, task_ids, *, session):
        from sqlalchemy import select

        from app.models.planner.events import PlannerEventGroup, PlannerEventRun
        from app.services.planner.tasks import planner_service
        from app.types import ActionType

        tenant = self._tenant()
        await planner_service._actor(session, ActionType.VIEW)
        if not task_ids:
            return set()
        result = await session.scalars(
            select(PlannerEventRun.greeting_task_id).where(
                PlannerEventRun.tenant_id == tenant, PlannerEventRun.greeting_task_id.in_(task_ids)
            )
        )
        ids = set(result.all())
        extra = await session.scalars(
            select(PlannerEventGroup.greeting_task_id).where(
                PlannerEventGroup.tenant_id == tenant, PlannerEventGroup.greeting_task_id.in_(task_ids)
            )
        )
        return ids | set(extra.all())

    async def draft_for_task(self, task_id, *, session):
        """Create a draft only after a user action and fresh task/source checks."""
        from sqlalchemy import select

        from app.core.access_catalog import workspace_grants
        from app.core.permissions import get_current_user, has_permission
        from app.models import Tenant
        from app.models.planner.events import PlannerEventClaim, PlannerEventGroup, PlannerEventRun
        from app.services.crm.greetings import crm_greetings_service
        from app.services.planner.tasks import planner_service
        from app.types import ActionType

        tenant = self._tenant()
        identity = await planner_service._actor(session, ActionType.VIEW)
        task = await planner_service._task(session, task_id, identity, lock=True)
        if ("source", ActionType.CREATE) not in workspace_grants(identity[2]) or not has_permission(
            get_current_user(), "source", ActionType.CREATE
        ):
            raise PermissionError("greeting_draft_denied")
        event = await session.scalar(
            select(PlannerEventRun)
            .where(PlannerEventRun.tenant_id == tenant, PlannerEventRun.greeting_task_id == task_id)
            .with_for_update()
        )
        if event is None:
            event = await session.scalar(
                select(PlannerEventRun)
                .join(
                    PlannerEventGroup,
                    (PlannerEventGroup.run_id == PlannerEventRun.id)
                    & (PlannerEventGroup.tenant_id == PlannerEventRun.tenant_id),
                )
                .where(PlannerEventGroup.tenant_id == tenant, PlannerEventGroup.greeting_task_id == task_id)
                .with_for_update(of=PlannerEventRun)
            )
        if event is None or task.contact_id is None:
            raise EventPlanningUnavailable("event_unavailable")
        if task.contact_id != event.contact_id:
            claim = await session.scalar(
                select(PlannerEventClaim.id).where(
                    PlannerEventClaim.tenant_id == tenant,
                    PlannerEventClaim.contact_id == task.contact_id,
                    PlannerEventClaim.run_id == event.id,
                    PlannerEventClaim.event_type == event.event_type,
                    PlannerEventClaim.year == event.year,
                )
            )
            if claim is None:
                raise EventPlanningUnavailable("event_unavailable")
        workspace = await session.scalar(select(Tenant).where(Tenant.id == tenant))
        if workspace is None:
            raise EventPlanningUnavailable("event_unavailable")
        return await crm_greetings_service.generate_greeting_draft(
            tenant_id=tenant,
            actor_id=identity[1],
            contact_id=task.contact_id,
            event_type=event.event_type,
            year=event.year,
            tz_name=workspace.timezone,
            session=session,
        )


event_planning_service = EventPlanningService()
