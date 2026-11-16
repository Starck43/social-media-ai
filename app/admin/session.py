"""SQLAdmin sessions enforce scope even for SQLAdmin's own ORM statements.

Managers already enforce ownership. SQLAdmin also builds queries for counts,
exports, forms and relationships, and writes directly through an ORM session.
Keep those operations on a dedicated session class, using the same actor and
active workspace as the manager layer. Application/worker sessions are unchanged.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from fastapi import HTTPException
from sqlalchemy import event, false, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, with_loader_criteria

from app.core.access_catalog import resource_for
from app.core.permissions import (
    _member_grants,
    _operator_grant,
    _platform_request,
    _service_grant,
    _workspace_owner,
    get_current_user,
    has_permission,
)
from app.core.tenant_context import current_tenant_id, is_bypass
from app.types import ActionType

_request_row_criteria: ContextVar[dict | None] = ContextVar("admin_request_row_criteria", default=None)
_ambient_row_criteria: ContextVar[tuple[tuple, dict] | None] = ContextVar("admin_ambient_row_criteria", default=None)


@contextmanager
def row_criteria_scope() -> Iterator[dict]:
    """Bind a fresh per-request cache for `row_criterion` and `_scope_reads`."""
    cache: dict = {}
    token = _request_row_criteria.set(cache)
    ambient_token = _ambient_row_criteria.set(None)
    try:
        yield cache
    finally:
        _request_row_criteria.reset(token)
        _ambient_row_criteria.reset(ambient_token)


def _scope_cache_key() -> tuple:
    actor = get_current_user()
    role = getattr(actor, "role", None) if actor is not None else None
    role_perms = getattr(role, "permissions", None) if role is not None else None
    if isinstance(role_perms, (list, tuple, set, frozenset)):
        perm_sig = frozenset(getattr(p, "id", id(p)) for p in role_perms)
    else:
        perm_sig = id(role_perms)
    actor_grants = getattr(actor, "grants", None) if actor is not None else None
    if isinstance(actor_grants, dict):
        grants_sig = tuple(sorted((k, frozenset(v)) for k, v in actor_grants.items()))
    else:
        grants_sig = None
    return (
        id(actor) if actor is not None else None,
        getattr(actor, "id", None) if actor is not None else None,
        bool(getattr(actor, "is_active", False)),
        bool(getattr(actor, "is_superuser", False)),
        id(role) if role is not None else None,
        perm_sig,
        grants_sig,
        current_tenant_id(),
        is_bypass(),
        _member_grants.get(),
        _workspace_owner.get(),
        _service_grant.get(),
        _operator_grant.get(),
        _platform_request.get(),
        id(_may_read_private),
        id(has_permission),
        id(resource_for),
    )


def _active_criteria_cache(scope_key: tuple) -> dict:
    request_cache = _request_row_criteria.get()
    if request_cache is not None:
        return request_cache
    ambient = _ambient_row_criteria.get()
    if ambient is not None and ambient[0] == scope_key:
        return ambient[1]
    fresh: dict = {}
    _ambient_row_criteria.set((scope_key, fresh))
    return fresh


def _may_read_private(model, actor) -> bool:
    """Match console implied read grants, capped by the selected membership."""
    if actor is None or not actor.is_active or not actor.has_admin_access():
        return False
    if actor.is_superuser:
        return True
    resource = resource_for(model)
    if resource is None:
        return False
    granted = actor.model_permissions(resource.model_name)
    if resource.workspace:
        granted = {action for action in granted if has_permission(actor, resource.model_name, action)}
    # Like the authorization backend, any granted console operation lets an
    # actor read the rows needed to reach its form. This does not grant writes.
    return bool(granted)


def _compute_row_criterion(model):
    actor = get_current_user()
    actor_id = getattr(actor, "id", None) if getattr(actor, "is_active", False) else None
    private = (
        getattr(model, "__personal_scoped__", False)
        or getattr(model, "__tenant_scoped__", False)
        or model.__name__ in {"UserCredential", "Tenant"}
    )
    if private and not _may_read_private(model, actor):
        return false()
    if getattr(model, "__personal_scoped__", False):
        return model.owner_user_id == actor_id if actor_id else false()
    if model.__name__ == "UserCredential":
        return model.user_id == actor_id if actor_id else false()
    if getattr(model, "__tenant_scoped__", False) or model.__name__ == "Tenant":
        if actor_id and actor.is_superuser:
            return None
        tenant_id = current_tenant_id()
        column = model.id if model.__name__ == "Tenant" else model.tenant_id
        return column == tenant_id if actor_id and tenant_id is not None else false()
    return None


def row_criterion(model):
    """An ORM predicate for private rows, or None for platform reference data."""
    scope_key = _scope_cache_key()
    cache = _active_criteria_cache(scope_key)
    key = ("model", scope_key, model)
    if key in cache:
        return cache[key]
    criterion = _compute_row_criterion(model)
    cache[key] = criterion
    return criterion


class AdminSession(Session):
    """Synchronous half of the console's dedicated async session."""


class AdminAsyncSession(AsyncSession):
    sync_session_class = AdminSession


@event.listens_for(AdminSession, "do_orm_execute")
def _scope_reads(execute_state):
    if not execute_state.is_select:
        return
    from app.models import Base

    scope_key = (_scope_cache_key(), id(row_criterion), len(Base.registry.mappers))
    cache = _active_criteria_cache(scope_key[0])
    loader_key = ("loader_criteria", scope_key)
    criteria_pairs = cache.get(loader_key)
    if criteria_pairs is None:
        pairs = []
        for mapper in Base.registry.mappers:
            model = mapper.class_
            criterion = row_criterion(model)
            if criterion is not None:
                pairs.append((model, criterion))
        criteria_pairs = tuple(pairs)
        cache[loader_key] = criteria_pairs

    statement = execute_state.statement
    for model, criterion in criteria_pairs:
        statement = statement.options(with_loader_criteria(model, criterion, include_aliases=True))
    execute_state.statement = statement


@event.listens_for(AdminSession, "before_flush")
def _scope_writes(session, flush_context, instances):
    from app.models import Base

    actor = get_current_user()
    if actor is None or not actor.is_active or not actor.has_admin_access():
        raise HTTPException(status_code=403, detail="Authenticated console actor required")
    models_by_table = {mapper.local_table: mapper.class_ for mapper in Base.registry.mappers}
    for obj in set(session.new) | set(session.dirty) | set(session.deleted):
        state = inspect(obj)
        model = type(obj)
        new = obj in session.new
        deleting = obj in session.deleted
        tenant_scoped = getattr(model, "__tenant_scoped__", False)
        private = (
            tenant_scoped
            or getattr(model, "__personal_scoped__", False)
            or model.__name__ in {"Tenant", "UserCredential"}
        )
        action = ActionType.DELETE if deleting else ActionType.CREATE if new else ActionType.UPDATE
        # Backref collection changes do not write the target row. The owning
        # view's route gate authorizes its association operation; scalar writes
        # and nested creates/deletes must carry the target's own platform grant.
        writes_row = new or deleting or session.is_modified(obj, include_collections=False)
        if private and writes_row and not actor.has_perm_for(model.__name__, action):
            raise HTTPException(status_code=403, detail="Console operation denied")
        if tenant_scoped and not new and state.attrs.tenant_id.history.has_changes():
            raise HTTPException(status_code=403, detail="Workspace ownership is immutable")
        if tenant_scoped or model.__name__ == "Tenant":
            if not actor.is_superuser:
                tenant_id = current_tenant_id()
                key = "id" if model.__name__ == "Tenant" else "tenant_id"
                if new and tenant_scoped and getattr(obj, key, None) is None:
                    setattr(obj, key, tenant_id)
                if tenant_id is None or getattr(obj, key, None) != tenant_id:
                    raise HTTPException(status_code=403, detail="Workspace access denied")
                if state.attrs[key].history.deleted:
                    raise HTTPException(status_code=403, detail="Workspace ownership is immutable")
                if writes_row and not has_permission(actor, model.__name__, action):
                    raise HTTPException(status_code=403, detail="Workspace operation denied")
        if getattr(model, "__personal_scoped__", False) or model.__name__ == "UserCredential":
            key = "owner_user_id" if getattr(model, "__personal_scoped__", False) else "user_id"
            # Preserve the existing owner-only provider creation picker. Editing
            # and key use never acquire another user's identity from this value.
            owner_creation = new and actor.is_superuser and model.__name__ == "LLMProvider"
            if not owner_creation and getattr(obj, key, None) != actor.id:
                raise HTTPException(status_code=403, detail="Personal catalog access denied")
            if not new and state.attrs[key].history.has_changes():
                raise HTTPException(status_code=403, detail="Personal ownership is immutable")
        if deleting:
            continue
        # SQLAdmin writes many-to-one relationships as raw FK values. Validate
        # those values in this same transaction, including composite references;
        # filtering the form's options alone does not secure a forged POST.
        for constraint in state.mapper.local_table.foreign_key_constraints:
            elements = list(constraint.elements)
            target = models_by_table.get(elements[0].column.table)
            if target is None:
                continue
            # The workspace ownership stamp is validated above. It is not a
            # user-selected relationship and does not require console access
            # to the workspace settings catalog merely to create a Source.
            if target.__name__ == "Tenant" and all(element.parent.key == "tenant_id" for element in elements):
                continue
            criterion = row_criterion(target)
            same_workspace = tenant_scoped and getattr(target, "__tenant_scoped__", False)
            if criterion is None and not same_workspace:
                continue
            keys = [element.parent.key for element in elements]
            if not new and not any(state.attrs[key].history.has_changes() for key in keys):
                continue
            values = [getattr(obj, key, None) for key in keys]
            if any(value is None for value in values):
                continue
            conditions = [element.column == value for element, value in zip(elements, values)]
            if criterion is not None:
                conditions.append(criterion)
            if same_workspace:
                conditions.append(target.tenant_id == obj.tenant_id)
            stmt = select(target).where(*conditions)
            if session.execute(stmt).first() is None:
                raise HTTPException(status_code=403, detail="Related record access denied")

    # Relationship assignment can leave scalar FKs or association rows unset until SQLAlchemy's
    # dependency processor runs, after before_flush. Inspect changed scalar
    # and collection relationships separately so an attached cached object cannot bypass the
    # same row and workspace checks applied to raw form FK values above.
    for obj in set(session.new) | set(session.dirty):
        state = inspect(obj)
        model = type(obj)
        tenant_scoped = getattr(model, "__tenant_scoped__", False)
        for relationship in state.mapper.relationships:
            related_objects = state.attrs[relationship.key].history.added
            if not related_objects:
                continue
            target = relationship.mapper.class_
            if (
                target.__name__ == "Tenant"
                and relationship.local_columns
                and all(column.key == "tenant_id" for column in relationship.local_columns)
            ):
                continue
            criterion = row_criterion(target)
            same_workspace = tenant_scoped and getattr(target, "__tenant_scoped__", False)
            if criterion is None and not same_workspace:
                continue
            for related in related_objects:
                if related is None:
                    continue
                related_state = inspect(related)
                if related in session.new:
                    # New related rows are independently checked by the first
                    # pass above; require the target's own action grant here too.
                    if getattr(target, "__tenant_scoped__", False):
                        target_allowed = actor.has_perm_for(target.__name__, ActionType.CREATE) and has_permission(
                            actor, target.__name__, ActionType.CREATE
                        )
                    else:
                        target_allowed = actor.has_perm_for(target.__name__, ActionType.CREATE)
                    if not target_allowed:
                        raise HTTPException(status_code=403, detail="Related record access denied")
                    if same_workspace and getattr(related, "tenant_id", None) != getattr(obj, "tenant_id", None):
                        raise HTTPException(status_code=403, detail="Related record access denied")
                    continue
                identity = related_state.identity
                if identity is None:
                    raise HTTPException(status_code=403, detail="Related record access denied")
                conditions = [column == value for column, value in zip(relationship.mapper.primary_key, identity)]
                if criterion is not None:
                    conditions.append(criterion)
                if same_workspace:
                    conditions.append(target.tenant_id == obj.tenant_id)
                if session.execute(select(target).where(*conditions)).first() is None:
                    raise HTTPException(status_code=403, detail="Related record access denied")
