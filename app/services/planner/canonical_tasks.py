"""Canonical local tasks. Legacy planner entry points retain their original scope."""

from typing import Literal

from pydantic import Field, StrictInt
from sqlalchemy import and_, delete, or_, select

from app.core.access_catalog import workspace_grants
from app.core.database import with_db_session
from app.core.permissions import get_current_user, has_permission
from app.models import TenantUser, User
from app.models.planner import Task, TaskParticipant
from app.schemas.business import BusinessSchema
from app.services.planner.contracts import TaskCreate, TaskOut, TaskUpdate
from app.services.planner.tasks import MANAGERS, PlannerService, PlannerUnavailable
from app.types import ActionType

Visibility = Literal["private", "shared", "workspace"]


class CreateTask(TaskCreate):
    visibility: Visibility = "private"


class UpdateTask(TaskUpdate):
    visibility: Visibility | None = None


class UpdateParticipation(BusinessSchema):
    expected_version: StrictInt = Field(gt=0)
    user_id: StrictInt = Field(gt=0)
    role: Literal["collaborator", "watcher"]
    enabled: bool


class CanonicalTaskOut(TaskOut):
    visibility: Visibility


class Completion(BusinessSchema):
    task: CanonicalTaskOut
    next_task: CanonicalTaskOut | None = None


def _collaborates(user_id):
    return (
        select(TaskParticipant.user_id)
        .where(
            TaskParticipant.tenant_id == Task.tenant_id,
            TaskParticipant.task_id == Task.id,
            TaskParticipant.user_id == user_id,
            TaskParticipant.role == "collaborator",
        )
        .exists()
    )


def _mine(user_id):
    return or_(
        Task.owner_user_id == user_id,
        Task.assignee_id == user_id,
        and_(Task.visibility != "private", _collaborates(user_id)),
    )


class CanonicalTaskService(PlannerService):
    model = Task
    create_schema = CreateTask
    complete_schema = Completion

    @staticmethod
    def project(row):
        return CanonicalTaskOut(**PlannerService.project(row).model_dump(), visibility=row.visibility)

    @staticmethod
    def visibility(actor_id, role):
        team = [Task.visibility == "workspace"]
        if role in MANAGERS:
            team.append(Task.visibility == "shared")
        return or_(_mine(actor_id), *team)

    def filter_list(self, query, identity, view):
        if view == "mine":
            return query.where(_mine(identity[1]))
        if view == "team":
            return query.where(Task.visibility.in_(("shared", "workspace")))
        if view is not None:
            raise ValueError("invalid_task_view")
        return query

    async def project_for_actor(self, rows, session, identity, *, authoritative=False):
        tenant_id, actor_id, role = identity
        parent_ids = {row.parent_id for row in rows if row.parent_id is not None}
        visible_parents = set()
        if parent_ids:
            visible_parents = set(
                await session.scalars(
                    select(Task.id).where(
                        Task.tenant_id == tenant_id, Task.id.in_(parent_ids), self.visibility(actor_id, role)
                    )
                )
            )
        can_view_contacts = ("contact", ActionType.VIEW) in workspace_grants(role) and has_permission(
            get_current_user(), "contact", ActionType.VIEW
        )
        return [
            self.project(row).model_copy(
                update={
                    "parent_id": row.parent_id if row.parent_id in visible_parents else None,
                    "contact_id": row.contact_id if can_view_contacts else None,
                }
            )
            for row in rows
        ]

    async def check_changed_references(self, session, request, identity, row, changes):
        new_links = {field: None for field in ("assignee_id", "contact_id", "parent_id") if field not in changes}
        await self._references(session, request.model_copy(update=new_links), identity, own_id=row.id)

    async def _task(self, session, task_id, identity, *, lock=False):
        row = await super()._task(session, task_id, identity, lock=lock)
        if lock and not await self._can_edit(session, row, identity):
            raise PermissionError("task_update_denied")
        return row

    @staticmethod
    def _can_share(row, identity):
        return row.owner_user_id == identity[1] or (row.visibility != "private" and identity[2] in MANAGERS)

    async def _can_edit(self, session, row, identity):
        if self._can_share(row, identity) or row.assignee_id == identity[1]:
            return True
        return bool(
            await session.scalar(
                select(_collaborates(identity[1]))
                .select_from(Task)
                .where(Task.id == row.id, Task.tenant_id == row.tenant_id)
            )
        )

    async def check_edit_fields(self, session, row, identity, changes):
        if {"visibility", "assignee_id"} & changes.keys() and not self._can_share(row, identity):
            raise PermissionError("task_share_denied")

    async def apply_edit(self, session, row, changes):
        if changes.get("visibility") == "private":
            await session.execute(
                delete(TaskParticipant).where(
                    TaskParticipant.tenant_id == row.tenant_id,
                    TaskParticipant.task_id == row.id,
                )
            )

    @with_db_session
    async def set_participant(self, task_id, request: UpdateParticipation, session):
        identity = await self._actor(session, ActionType.UPDATE)
        row = await super()._task(session, task_id, identity, lock=True)
        self._editable(row, request.expected_version)
        if request.role == "collaborator" and (not self._can_share(row, identity) or row.visibility == "private"):
            raise PermissionError("task_share_denied")
        if request.role == "watcher" and identity[1] != request.user_id:
            raise PermissionError("task_watch_denied")
        if request.enabled:
            member = await session.scalar(
                select(TenantUser.id)
                .join(User, User.id == TenantUser.user_id)
                .where(
                    TenantUser.tenant_id == identity[0],
                    TenantUser.user_id == request.user_id,
                    TenantUser.is_active.is_(True),
                    User.is_active.is_(True),
                )
                .with_for_update(read=True)
            )
            if member is None:
                raise PlannerUnavailable("task_unavailable")
        key = (identity[0], task_id, request.user_id, request.role)
        existing = await session.get(TaskParticipant, key)
        if request.enabled and existing is None:
            session.add(TaskParticipant(tenant_id=key[0], task_id=key[1], user_id=key[2], role=key[3]))
            row.version += 1
        elif not request.enabled and existing is not None:
            await session.delete(existing)
            row.version += 1
        await session.flush()
        (result,) = await self.project_for_actor([row], session, identity)
        return result

    async def successor_template(self, session, template, identity):
        tenant_id = identity[0]
        changes = {}
        if template.assignee_id is not None:
            if await self._active_assignee(session, tenant_id, template.assignee_id) is None:
                changes["assignee_id"] = None
        if template.contact_id is not None:
            if await self._active_contact(session, tenant_id, template.contact_id) is None:
                changes["contact_id"] = None
        if template.parent_id is not None:
            parent = await session.scalar(
                select(Task.id)
                .where(Task.tenant_id == tenant_id, Task.id == template.parent_id)
                .with_for_update(read=True)
            )
            if parent is None:
                changes["parent_id"] = None
        # The series already admitted these links. The completer does not
        # gain the authority to set new ones or to view their targets.
        return template.model_copy(update=changes) if changes else template

    async def copy_occurrence_participants(self, session, row, successor):
        if successor.visibility == "private":
            return
        participants = await session.execute(
            select(TaskParticipant.user_id, TaskParticipant.role)
            .join(
                TenantUser,
                and_(TenantUser.tenant_id == row.tenant_id, TenantUser.user_id == TaskParticipant.user_id),
            )
            .join(User, User.id == TenantUser.user_id)
            .where(
                TaskParticipant.tenant_id == row.tenant_id,
                TaskParticipant.task_id == row.id,
                TenantUser.is_active.is_(True),
                User.is_active.is_(True),
            )
        )
        for user_id, role in participants:
            session.add(TaskParticipant(tenant_id=row.tenant_id, task_id=successor.id, user_id=user_id, role=role))

    async def visible_successor(self, session, identity, successor):
        try:
            return await self._task(session, successor.id, identity)
        except PlannerUnavailable:
            return None

    async def delete(self, task_id, expected_version, session=None):
        """Canonical deletion is owned by the confirmed command in P22-T05."""
        raise PermissionError("confirmed_task_delete_required")


task_service = CanonicalTaskService()
