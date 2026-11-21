"""Transactional planner aggregate. Shared connection/actor locks, no ad-hoc sessions."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select

from app.core.access_catalog import workspace_grants
from app.core.database import with_db_session
from app.core.permissions import get_current_user, has_permission
from app.core.tenant_context import current_tenant_id, is_bypass
from app.models import Contact, Tenant, TenantUser, User, WorkspaceRole
from app.models.planner import PlannerTask
from app.services.planner.contracts import CompleteOut, TaskCreate, TaskOut, TaskReschedule, TaskUpdate
from app.services.planner.recurrence import next_occurrence, occurrence_key
from app.types import ActionType

MANAGERS = frozenset({"owner", "admin", "manager"})


class PlannerUnavailable(LookupError):
    """Opaque missing/invisible/foreign item or reference."""


class PlannerConflict(ValueError):
    """Stale version, invalid transition, cycle or external recurrence authority."""


def _projection(row) -> TaskOut:
    values = {key: getattr(row, key) for key in TaskOut.model_fields if key not in {"title", "description"}}
    # An empty description is stored as NULL; the response contract keeps "" so a task
    # created without one can still be listed (round-1 audit).
    return TaskOut(title=row.title_encrypted, description=row.description_encrypted or "", **values)


def _values(request: TaskCreate):
    values = request.model_dump()
    values["title_encrypted"] = values.pop("title")
    # "" is not secret; store NULL so an empty field is not a ciphertext of nothing and
    # the column's NOT NULL contract stays meaningful for real content.
    values["description_encrypted"] = values.pop("description") or None
    return values


def _visibility(actor_id, role):
    return True if role in MANAGERS else or_(PlannerTask.owner_user_id == actor_id, PlannerTask.assignee_id == actor_id)


class PlannerService:
    model = PlannerTask
    create_schema = TaskCreate
    complete_schema = CompleteOut
    visibility = staticmethod(_visibility)
    project = staticmethod(_projection)
    creation_fields = staticmethod(_values)

    async def authoritative(self, row, session, identity):
        from app.services.calendar.service import authoritative_task_projection

        return await authoritative_task_projection(row, self.project(row), session, identity)

    def filter_list(self, query, identity, view):
        if view is not None:
            raise ValueError("invalid_task_view")
        return query

    async def project_for_actor(self, rows, session, identity, *, authoritative=False):
        if authoritative:
            return [await self.authoritative(row, session, identity) for row in rows]
        return [self.project(row) for row in rows]

    async def check_changed_references(self, session, request, identity, row, changes):
        await self._references(session, request, identity, own_id=row.id)

    async def check_edit_fields(self, session, row, identity, changes):
        """Apply additional aggregate-specific authorization."""

    async def apply_edit(self, session, row, changes):
        """Apply aggregate-specific side effects after validating the edit."""

    async def visible_successor(self, session, identity, successor):
        return (
            successor
            if identity[2] in MANAGERS or identity[1] in {successor.owner_user_id, successor.assignee_id}
            else None
        )

    async def copy_occurrence_participants(self, session, row, successor):
        """Legacy planner occurrences have no participant rows."""

    async def _actor(self, session, action, *, member_channel=None):
        tenant, actor = current_tenant_id(), get_current_user()
        if is_bypass() or type(tenant) is not int or tenant <= 0 or actor is None or not actor.is_active:
            raise PermissionError("planner_denied")
        # Existing lock order: user -> tenant -> membership -> role. Exclusive
        # workspace lock on writes also serializes hierarchy cycle checks.
        user = await session.scalar(
            select(User.id).where(User.id == actor.id, User.is_active.is_(True)).with_for_update(read=True)
        )
        workspace = await session.scalar(
            select(Tenant.id)
            .where(Tenant.id == tenant, Tenant.is_active.is_(True))
            .with_for_update(read=action == ActionType.VIEW)
        )
        member_query = select(TenantUser).where(
            TenantUser.tenant_id == tenant, TenantUser.user_id == actor.id, TenantUser.is_active.is_(True)
        )
        if member_channel is not None:
            member_query = member_query.where(TenantUser.channel == member_channel)
        member = await session.scalar(member_query.with_for_update(read=True).execution_options(populate_existing=True))
        role = (
            None
            if member is None
            else await session.scalar(
                select(WorkspaceRole.codename).where(WorkspaceRole.id == member.role_id).with_for_update(read=True)
            )
        )
        if user is None or workspace is None or ("plannertask", action) not in workspace_grants(role):
            raise PermissionError("planner_denied")
        return tenant, actor.id, role

    async def _task(self, session, task_id, identity, *, lock=False):
        tenant, actor, role = identity
        if type(task_id) is not int or task_id <= 0:
            raise PlannerUnavailable("planner_unavailable")
        query = (
            select(self.model)
            .where(self.model.tenant_id == tenant, self.model.id == task_id, self.visibility(actor, role))
            .execution_options(populate_existing=True)
        )
        row = await session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise PlannerUnavailable("planner_unavailable")
        return row

    async def _active_assignee(self, session, tenant_id, user_id):
        return await session.scalar(
            select(TenantUser.id)
            .join(User, User.id == TenantUser.user_id)
            .where(
                TenantUser.tenant_id == tenant_id,
                TenantUser.user_id == user_id,
                TenantUser.is_active.is_(True),
                User.is_active.is_(True),
            )
            .with_for_update(read=True)
        )

    async def _active_contact(self, session, tenant_id, contact_id):
        return await session.scalar(
            select(Contact.id)
            .where(Contact.tenant_id == tenant_id, Contact.id == contact_id, Contact.merged_into_id.is_(None))
            .with_for_update(read=True)
        )

    async def _references(self, session, request, identity, *, own_id=None):
        tenant, _, role = identity
        if (
            request.assignee_id is not None
            and await self._active_assignee(session, tenant, request.assignee_id) is None
        ):
            raise PlannerUnavailable("planner_unavailable")
        if request.contact_id is not None:
            if ("contact", ActionType.VIEW) not in workspace_grants(role) or not has_permission(
                get_current_user(), "contact", ActionType.VIEW
            ):
                raise PermissionError("planner_contact_denied")
            if await self._active_contact(session, tenant, request.contact_id) is None:
                raise PlannerUnavailable("planner_unavailable")
        ancestor, seen = request.parent_id, {own_id}
        for _ in range(100):
            if ancestor is None:
                return
            if ancestor in seen:
                raise PlannerConflict("planner_parent_cycle")
            seen.add(ancestor)
            row = await self._task(session, ancestor, identity)
            ancestor = row.parent_id
        raise PlannerConflict("planner_parent_depth")

    @staticmethod
    def _editable(row, expected_version):
        if (
            type(expected_version) is not int
            or expected_version <= 0
            or row.version != expected_version
            or row.status in {"done", "cancelled"}
        ):
            raise PlannerConflict("planner_not_editable")
        if row.recurrence_source != "planner":
            raise PlannerConflict("planner_source_unavailable")

    @with_db_session
    async def create(self, request: TaskCreate, session):
        identity = await self._actor(session, ActionType.CREATE)
        await self._references(session, request, identity)
        anchor = request.due_at or request.due_date
        # Non-recurring, undated records get an opaque one-off key; no recurrence
        # identity is derived from the completion/retry wall clock.
        key = occurrence_key(request.recurrence, anchor, anchor) if anchor is not None else "once"
        row = self.model(
            tenant_id=identity[0],
            owner_user_id=identity[1],
            **self.creation_fields(request),
            status="todo",
            version=1,
            occurrence_index=0,
            occurrence_key=key,
            recurrence_source="planner",
            series_template_encrypted=request.model_dump_json(),
        )
        session.add(row)
        await session.flush()
        row.series_id = row.id
        await session.flush()
        if self.model is PlannerTask:
            from app.services.planner.legacy_migration import compatibility_writer

            await compatibility_writer.planner(session, row)
        (result,) = await self.project_for_actor([row], session, identity)
        return result

    @with_db_session
    async def get(self, task_id, session):
        identity = await self._actor(session, ActionType.VIEW)
        row = await self._task(session, task_id, identity)
        (result,) = await self.project_for_actor([row], session, identity, authoritative=True)
        return result

    @with_db_session
    async def list(self, session, *, limit=50, before_id=None, status=None, view=None):
        identity = await self._actor(session, ActionType.VIEW)
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or (before_id is not None and (type(before_id) is not int or before_id <= 0))
        ):
            raise ValueError("invalid_page")
        if status is not None and status not in {"todo", "in_progress", "done", "cancelled"}:
            raise ValueError("invalid_status")
        query = select(self.model).where(self.model.tenant_id == identity[0], self.visibility(identity[1], identity[2]))
        query = self.filter_list(query, identity, view)
        if before_id is not None:
            query = query.where(self.model.id < before_id)
        if status is not None:
            query = query.where(self.model.status == status)
        rows = list(
            (
                await session.scalars(
                    query.order_by(self.model.id.desc()).limit(limit + 1).execution_options(populate_existing=True)
                )
            ).all()
        )
        items = await self.project_for_actor(rows[:limit], session, identity, authoritative=True)
        return {
            "items": items,
            "limit": limit,
            "next_before_id": rows[limit - 1].id if len(rows) > limit else None,
        }

    @with_db_session
    async def calendar_range(self, session, *, first_day, next_day, start_utc, end_utc, limit=50, before_id=None):
        """Actor-scoped month, with date-only and timestamp deadlines kept distinct."""
        identity = await self._actor(session, ActionType.VIEW)
        if (
            type(first_day) is not date
            or type(next_day) is not date
            or first_day.day != 1
            or next_day != date(first_day.year + (first_day.month == 12), first_day.month % 12 + 1, 1)
            or not isinstance(start_utc, datetime)
            or not isinstance(end_utc, datetime)
            or start_utc.utcoffset() is None
            or end_utc.utcoffset() is None
            or start_utc >= end_utc
            or type(limit) is not int
            or not 1 <= limit <= 100
            or (before_id is not None and (type(before_id) is not int or before_id <= 0))
        ):
            raise ValueError("invalid_calendar_range")
        query = select(self.model).where(
            self.model.tenant_id == identity[0],
            self.visibility(identity[1], identity[2]),
            self.model.status.in_(("todo", "in_progress")),
            self.model.recurrence_source != "calendar",
            or_(
                (self.model.due_at >= start_utc) & (self.model.due_at < end_utc),
                (self.model.due_at.is_(None)) & (self.model.due_date >= first_day) & (self.model.due_date < next_day),
            ),
        )
        if before_id is not None:
            query = query.where(self.model.id < before_id)
        rows = list(
            (
                await session.scalars(
                    query.order_by(self.model.id.desc()).limit(limit + 1).execution_options(populate_existing=True)
                )
            ).all()
        )
        return {
            "items": await self.project_for_actor(rows[:limit], session, identity),
            "next_before_id": rows[limit - 1].id if len(rows) > limit else None,
        }

    async def _change(self, session, task_id, request, *, reschedule=False):
        identity = await self._actor(session, ActionType.UPDATE)
        row = await self._task(session, task_id, identity, lock=True)
        self._editable(row, request.expected_version)
        template = self.create_schema.model_validate_json(row.series_template_encrypted)
        current = self.create_schema.model_validate(
            self.project(row).model_dump(include=set(self.create_schema.model_fields))
        )
        changes = request.model_dump(exclude_unset=True, exclude={"expected_version", "scope", "status"})
        await self.check_edit_fields(session, row, identity, changes)
        if reschedule:
            if request.scope == "occurrence" and "recurrence" in changes:
                raise PlannerConflict("series_rule_requires_series_scope")
            changes.update(due_at=request.due_at, due_date=request.due_date)
        values = current.model_dump()
        values.update(changes)
        updated = self.create_schema.model_validate(values)
        if (
            reschedule
            and request.scope == "occurrence"
            and template.recurrence is not None
            and (updated.due_at is not None) != (template.due_at is not None)
        ):
            raise PlannerConflict("deadline_kind_requires_series_scope")
        await self.check_changed_references(session, updated, identity, row, changes)
        if request.scope == "series":
            # Apply to this open occurrence and future generated occurrences,
            # never rewrite historical completed rows or precreate a series.
            defaults = template.model_dump()
            defaults.update(changes)
            template = self.create_schema.model_validate(defaults)
        await self.apply_edit(session, row, changes)
        if request.scope == "series":
            row.series_template_encrypted = template.model_dump_json()
        for name, value in self.creation_fields(updated).items():
            setattr(row, name, value)
        if not reschedule and request.status is not None:
            row.status = request.status
        row.version += 1
        await session.flush()
        if self.model is PlannerTask:
            from app.services.planner.legacy_migration import compatibility_writer

            await compatibility_writer.planner(session, row)
        (result,) = await self.project_for_actor([row], session, identity)
        return result

    @with_db_session
    async def update(self, task_id, request: TaskUpdate, session):
        return await self._change(session, task_id, request)

    @with_db_session
    async def reschedule(self, task_id, request: TaskReschedule, session):
        return await self._change(session, task_id, request, reschedule=True)

    async def successor_template(self, session, template, identity):
        await self._references(session, template, identity)
        return template

    async def completion_response(self, session, identity, row, following):
        rows = [row] if following is None else [row, following]
        results = await self.project_for_actor(rows, session, identity)
        return self.complete_schema(task=results[0], next_task=results[1] if following is not None else None)

    def following_occurrence(self, row, now):
        template = self.create_schema.model_validate_json(row.series_template_encrypted)
        if template.recurrence is None:
            return template, None
        anchor = template.due_at or template.due_date
        threshold = (
            max(row.due_at, now)
            if template.due_at is not None
            else max(row.due_date, now.astimezone(ZoneInfo(template.timezone)).date())
        )
        return template, next_occurrence(anchor, template.recurrence, after=threshold, tz=template.timezone)

    async def advance_occurrence(self, session, row, identity, now):
        template, following = self.following_occurrence(row, now)
        if following is None:
            return None
        due, index = following
        key = occurrence_key(template.recurrence, template.due_at or template.due_date, due)
        existing = await session.scalar(
            select(self.model.id).where(
                self.model.tenant_id == identity[0],
                self.model.series_id == row.series_id,
                self.model.occurrence_key == key,
            )
        )
        if existing is not None:
            raise PlannerConflict("planner_occurrence_already_exists")
        successor_template = await self.successor_template(session, template, identity)
        values = self.creation_fields(successor_template)
        values["due_at" if template.due_at is not None else "due_date"] = due
        successor = self.model(
            tenant_id=identity[0],
            owner_user_id=row.owner_user_id,
            **values,
            status="todo",
            version=1,
            recurrence_source="planner",
            series_id=row.series_id,
            previous_occurrence_id=row.id,
            occurrence_index=index,
            occurrence_key=key,
            series_template_encrypted=(
                row.series_template_encrypted
                if successor_template is template
                else successor_template.model_dump_json()
            ),
        )
        session.add(successor)
        await session.flush()
        if self.model is PlannerTask:
            from app.services.planner.legacy_migration import compatibility_writer

            await compatibility_writer.planner(session, successor)
        await self.copy_occurrence_participants(session, row, successor)
        await session.flush()
        return successor

    @with_db_session
    async def complete(self, task_id, expected_version, session):
        identity = await self._actor(session, ActionType.UPDATE)
        row = await self._task(session, task_id, identity, lock=True)
        if row.status == "done":
            following = await session.scalar(
                select(self.model)
                .where(
                    self.model.tenant_id == identity[0],
                    self.model.previous_occurrence_id == row.id,
                    self.visibility(identity[1], identity[2]),
                )
                .execution_options(populate_existing=True)
            )
            # Retrying completion reads the existing successor; never inserts it.
            return await self.completion_response(session, identity, row, following)
        self._editable(row, expected_version)
        now = datetime.now(timezone.utc)
        next_row = await self.advance_occurrence(session, row, identity, now)
        row.status, row.completed_at, row.version = "done", now, row.version + 1
        await session.flush()
        if self.model is PlannerTask:
            from app.services.planner.legacy_migration import compatibility_writer

            await compatibility_writer.planner(session, row)
        if next_row is not None:
            next_row = await self.visible_successor(session, identity, next_row)
        return await self.completion_response(session, identity, row, next_row)

    @with_db_session
    async def delete(self, task_id, expected_version, session):
        identity = await self._actor(session, ActionType.DELETE)
        row = await self._task(session, task_id, identity, lock=True)
        if type(expected_version) is not int or expected_version <= 0 or row.version != expected_version:
            raise PlannerConflict("stale_version")
        if identity[1] != row.owner_user_id and identity[2] not in MANAGERS:
            raise PermissionError("planner_denied")
        if self.model is PlannerTask:
            from app.services.planner.legacy_migration import resolve_legacy_task

            canonical = await resolve_legacy_task(
                session,
                tenant_id=row.tenant_id,
                namespace="planner",
                legacy_id=row.id,
                lock=True,
            )
            if canonical is not None:
                canonical.deleted_at = datetime.now(timezone.utc)
        await session.delete(row)
        await session.flush()
        return {"deleted": True}


planner_service = PlannerService()
