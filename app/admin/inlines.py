"""Read-only reverse-FK projections for authenticated console object pages.

Never traverse an ORM relationship or call a row's __str__: both can lazy-load
unscoped rows/secrets. Query only registered, authorized views, retaining their
list predicates and adding explicit personal/workspace visibility before LIMIT.
"""

from contextlib import nullcontext
from dataclasses import replace
from datetime import date, datetime
from enum import Enum
from typing import Any

import anyio
from sqladmin.pagination import Pagination
from sqlalchemy import JSON, LargeBinary, and_, false, func, inspect, select, true

from app.core.access_catalog import WORKSPACE_LABELS, resource_for, workspace_grants
from app.types import ActionType

INLINE_LIMIT = 25
CELL_LIMIT = 180
# List projections are intentionally stricter than general console formatters.
PRIVATE_NAMES = ("password", "secret", "token", "key", "hash", "payload", "error", "meta", "params", "prompt")


def scope_predicate(model: Any, actor: Any):
    """Additional inline scope; console grants alone never widen membership.

    The platform owner's existing workspace inspection right is retained, but
    personal catalogs/vaults remain private for everyone, including the owner.
    Unknown resources/ownership layouts fail closed rather than becoming global.
    """
    from app.models import Tenant, TenantUser, WorkspaceRole

    resource = resource_for(model.__table__.name)
    if resource is None:
        return false()
    if getattr(model, "__personal_scoped__", False):
        owner = getattr(model, "owner_user_id", None)
        return owner == actor.id if owner is not None else false()
    personal = true()
    if resource.personal:
        owner = getattr(model, "user_id", None)
        personal = owner == actor.id if owner is not None else false()
    if not resource.workspace:
        return personal
    if actor.is_superuser:
        return personal
    roles = [role for role in WORKSPACE_LABELS if (resource.model_name, ActionType.VIEW) in workspace_grants(role)]
    column = model.id if model is Tenant else getattr(model, "tenant_id", None)
    if column is None or not roles:
        return false()
    allowed = (
        select(TenantUser.tenant_id)
        .join(Tenant, Tenant.id == TenantUser.tenant_id)
        .join(WorkspaceRole, WorkspaceRole.id == TenantUser.role_id)
        .where(
            TenantUser.user_id == actor.id,
            TenantUser.channel == "web",
            TenantUser.is_active.is_(True),
            Tenant.is_active.is_(True),
            WorkspaceRole.codename.in_(roles),
        )
    )
    return and_(personal, column.in_(allowed))


def readable(view: Any, request: Any) -> bool:
    return bool(
        view.is_accessible(request)
        and view.can_view_details
        and view.has_permission(request, "details")
        and view.has_permission(request, "list")
    )


def projection_columns(view: Any) -> list[str]:
    """Only configured scalar list AND detail columns; never custom formatters."""
    mapper = inspect(view.model)
    details = set(view._details_prop_names)
    names = []
    configured = getattr(view, "inline_columns", None)
    for name in dict.fromkeys(configured if configured is not None else view._list_prop_names):
        if name not in view._list_prop_names:
            continue
        if name not in mapper.column_attrs or name not in details:
            continue
        prop = mapper.column_attrs[name]
        if len(prop.columns) != 1 or any(word in name.lower() for word in PRIVATE_NAMES):
            continue
        if isinstance(prop.columns[0].type, (JSON, LargeBinary)):
            continue
        names.append(name)
    return names


def reverse_foreign_keys(parent: Any, target: Any):
    """Discover inbound constraints, including FK columns without backrefs.

    Keep compound constraints together and distinguish multiple FKs to the same
    parent (e.g. author vs recipient). No association-table or unregistered read.
    """
    parent_table = inspect(type(parent)).local_table
    for constraint in sorted(target.__table__.foreign_key_constraints, key=lambda c: tuple(c.column_keys)):
        if not all(element.column.table is parent_table for element in constraint.elements):
            continue
        predicates = []
        for element in constraint.elements:
            name = inspect(type(parent)).get_property_by_column(element.column).key
            value = getattr(parent, name)
            if value is None:
                break
            predicates.append(element.parent == value)
        else:
            yield ", ".join(constraint.column_keys), and_(*predicates)


def scoped_projection(
    view: Any, request: Any, actor: Any, names: list[str], predicate: Any, *, limit: int | None = INLINE_LIMIT
):
    # Reuse the view's predicates (including catalog filtering) without invoking
    # its ORM formatters/eager loaders. An outer scalar SELECT strips ORM options.
    context = view._catalog_scope(request) if hasattr(view, "_catalog_scope") else nullcontext()
    with context:
        rows = view.list_query(request).where(scope_predicate(view.model, actor), predicate).order_by(None).subquery()
    keys = [inspect(view.model).get_property_by_column(col).key for col in inspect(view.model).primary_key]
    # Include keys to keep DISTINCT and deterministic ordering compatible even
    # when a compact inline omits ID. Counts and pages use this exact projection.
    selected = dict.fromkeys([*keys, *names])
    return select(*(rows.c[name] for name in selected)).distinct().order_by(*(rows.c[key] for key in keys)).limit(limit)


async def read_rows(view: Any, statement: Any) -> list[dict]:
    """Use SQLAdmin's existing session factory, not a separate engine/target."""
    if view.is_async:
        async with view.session_maker(expire_on_commit=False) as session:
            return [dict(row) for row in (await session.execute(statement)).mappings()]

    def read_sync():
        with view.session_maker(expire_on_commit=False) as session:
            return [dict(row) for row in session.execute(statement).mappings()]

    return await anyio.to_thread.run_sync(read_sync)


def cell_text(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, Enum):
        value = getattr(value, "label", value.value)
    if not isinstance(value, (str, int, float)):
        return "—"
    text = str(value)
    return text[:CELL_LIMIT] + "…" if len(text) > CELL_LIMIT else text


def positive_int(value: Any, default: int) -> int:
    """Bound parsing before int(): malformed/huge query values never reach SQL."""
    text = str(value)
    return int(text) if text.isascii() and text.isdigit() and len(text) <= 9 and int(text) > 0 else default


def inline_pagination(view: Any, request: Any, section_id: str, count: int):
    """Reuse SQLAdmin's page window/clamping with isolated per-section URLs."""
    options = tuple(
        dict.fromkeys(
            size
            for size in getattr(view, "inline_page_size_options", (10, 25, 50, 100))
            if type(size) is int and 0 < size <= 200
        )
    ) or (INLINE_LIMIT,)
    default = getattr(view, "inline_page_size", INLINE_LIMIT)
    default = default if type(default) is int and default in options else options[0]
    page_key, size_key = f"{section_id}-page", f"{section_id}-size"
    size = positive_int(request.query_params.get(size_key), default)
    size = size if size in options else default
    page = positive_int(request.query_params.get(page_key), 1)
    pagination = Pagination(rows=[], page=page, page_size=size, count=count)
    pagination.add_pagination_urls(request.url)

    def url(**params):
        return str(request.url.include_query_params(**params).replace(fragment=section_id))

    for control in pagination.page_controls:
        control.url = url(**{page_key: control.number})
    sizes = [
        {
            "size": option,
            "url": url(
                **{
                    size_key: option,
                    page_key: replace(pagination, page_controls=[]).resize(option).page,
                }
            ),
        }
        for option in options
    ]
    return pagination, sizes


async def related_inlines(view: Any, obj: Any, request: Any) -> list[dict]:
    """Called only on persisted details/edit pages, with no new HTTP endpoint."""
    actor = getattr(request.state, "admin_user", None)
    if (
        actor is None
        or not actor.is_active
        or type(actor.id) is not int
        or actor.id <= 0
        or not actor.has_admin_access()
        or obj is None
        or not readable(view, request)
        or not view.has_permission(request, "details", obj)
    ):
        return []
    mapper = inspect(view.model)
    keys = [mapper.get_property_by_column(column).key for column in mapper.primary_key]
    if any(getattr(obj, key, None) is None for key in keys):
        return []
    parent_filter = and_(*(getattr(view.model, key) == getattr(obj, key) for key in keys))
    if not await read_rows(view, scoped_projection(view, request, actor, keys, parent_filter, limit=1)):
        return []
    sections = []
    for target in view._admin_ref._views:
        if not getattr(target, "model", None) or not readable(target, request):
            continue
        if getattr(target, "inline_exclude", False):
            continue
        if resource_for(target.model.__table__.name) is None:
            continue
        columns = projection_columns(target)
        if not columns:
            continue
        for foreign_key, predicate in reverse_foreign_keys(obj, target.model):
            # Stable names isolate every FK's navigation, including two FKs to
            # one parent. Only metadata, never request-supplied SQL identifiers.
            section_id = f"inline-{target.identity}-{foreign_key.replace(', ', '-')}"
            statement = scoped_projection(target, request, actor, columns, predicate, limit=None)
            count_rows = await read_rows(
                target, select(func.count().label("total")).select_from(statement.order_by(None).subquery())
            )
            pagination, sizes = inline_pagination(target, request, section_id, count_rows[0]["total"])
            rows = await read_rows(
                target, statement.limit(pagination.page_size).offset((pagination.page - 1) * pagination.page_size)
            )
            pagination.rows = [[cell_text(row[name]) for name in columns] for row in rows]
            sections.append(
                {
                    "id": section_id,
                    "title": target.name_plural,
                    "foreign_key": foreign_key,
                    "relation_label": " · ".join(
                        target._column_labels.get(key, key) for key in foreign_key.split(", ")
                    ),
                    "column_names": columns,
                    "columns": [target._column_labels.get(name, name) for name in columns],
                    "rows": pagination.rows,
                    "pagination": pagination,
                    "sizes": sizes,
                }
            )
    return sections
