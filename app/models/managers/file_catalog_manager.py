"""Private bounded catalog authority; no provider or browser input adapter.

All entry points recheck the actor, owner, active connection, selected root and
saved restrictions. Connection locks serialize admissions with revoke/policy edits.
No operator bypass, generic CRUD, implicit sharing or local-only persistence.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select

from app.core.database import async_session_maker
from app.integrations.catalog import CATALOG_VERSION
from app.integrations.file_workspace.policy import FileOperation, _components
from app.integrations.file_workspace.yandex_disk import DiskItem, DiskPage
from app.models import FileCatalogObject, FileCatalogObservation, WorkspaceConnection, WorkspaceConnectionSecret
from app.models.managers.workspace_connection_manager import ConnectionDenied, _authorize, _connection
from app.services.file_workspace.yandex_reader import _stored_policy

MAX_PAGE = 100
MAX_OBJECTS = 10_000
MAX_OBSERVATIONS = 32
TICKET_TTL = timedelta(seconds=60)


class CatalogInputError(ValueError):
    """Sanitized internal input error; never echo paths or provider payloads."""


class CatalogCapacityError(ValueError):
    """Explicit bounded storage stop; never evict observations silently."""


def _path(value):
    path = "disk:/" + "/".join(_components(value))
    if len(path.encode("utf-8")) > 2048:
        raise ValueError
    return path


def _key(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _search_key(value):
    """NFKC casefold independent of the PostgreSQL cluster locale.

    Keep the 0125 backfill algorithm in sync. Original names stay unchanged.
    """
    return unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", value).casefold())


def _policy_key(config):
    return _key(json.dumps(config, sort_keys=True, ensure_ascii=True, separators=(",", ":")))


def _bounded_int(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise CatalogInputError("invalid_page")
    return value


@dataclass(frozen=True)
class CatalogPageTicket:
    """Internal admission context, never a bearer permission or browser input.

    Issued before a provider read; a changed policy or expired ticket denies
    admission. Ordering prevents a late earlier read from overwriting newer data.
    Caller must supply the actual page obtained for this exact request.
    """

    tenant_id: int
    actor_id: int
    connection_id: int
    folder: str
    policy_key: str
    issued_at: datetime


def _visible_paths(column, config):
    root = _path(config["file_policy"]["root"])

    def within(folder):
        # Escape LIKE metacharacters: filenames/root segments are not SQL patterns.
        return or_(column == folder, column.startswith(folder.rstrip("/") + "/", autoescape=True))

    conditions = [within(root)]
    for item in config["file_policy"]["restrictions"]:
        if "list" in item["deny"]:
            conditions.append(~within(_path(item["path"])))
    return and_(*conditions)


def _cloud_policy(connection):
    if (
        not connection.is_active
        or connection.state not in {"unverified", "verified"}
        or connection.provider_id != "yandex_disk"
        or connection.platform != "yandex_disk"
        or connection.auth_method != "oauth_token"
        or connection.schema_version != CATALOG_VERSION
        # Future local/company policy cannot silently enter this cloud catalog.
        or not isinstance(connection.config, dict)
        or set(connection.config) != {"file_policy"}
    ):
        raise ConnectionDenied()
    policy = _stored_policy(connection.config)
    try:
        root = _path(connection.config["file_policy"]["root"])
        for rule in connection.config["file_policy"]["restrictions"]:
            _path(rule["path"])
    except ValueError:
        raise ConnectionDenied() from None
    return policy, root


@asynccontextmanager
async def _owned(tenant_id, actor_id, connection_id):
    async with async_session_maker() as session, session.begin():
        await _authorize(session, tenant_id, actor_id, require_source_view=True)
        connection = await _connection(session, tenant_id, actor_id, connection_id)
        policy, root = _cloud_policy(connection)
        secret_present = await session.scalar(
            select(WorkspaceConnectionSecret.connection_id).where(
                WorkspaceConnectionSecret.connection_id == connection.id,
                WorkspaceConnectionSecret.secret_encrypted != "",
            )
        )
        if secret_present is None:
            raise ConnectionDenied()
        yield session, connection, policy, _key(root)


def _object_dict(row):
    return {
        "id": row.id,
        "name": row.name,
        "path": row.path,
        "kind": row.kind,
        "observed_at": row.observed_at,
        "observation_number": row.observation_number,
        "identity_kind": "path",
        "freshness": "metadata_observed",
        "content_version_known": False,
        "content_available": False,
        "original_open_available": False,
    }


class FileCatalogManager:
    async def connections(self, *, tenant_id, actor_id):
        """At most 100 currently available personal cloud mounts, no secret/config.

        This selector does not verify a live provider or certify synchronization.
        Every later list/detail/admission independently rechecks current authority.
        """
        async with async_session_maker() as session, session.begin():
            await _authorize(session, tenant_id, actor_id, require_source_view=True)
            rows = (
                await session.scalars(
                    select(WorkspaceConnection)
                    .join(WorkspaceConnectionSecret, WorkspaceConnectionSecret.connection_id == WorkspaceConnection.id)
                    .where(
                        WorkspaceConnection.tenant_id == tenant_id,
                        WorkspaceConnection.owner_user_id == actor_id,
                        WorkspaceConnection.provider_id == "yandex_disk",
                        WorkspaceConnection.platform == "yandex_disk",
                        WorkspaceConnection.auth_method == "oauth_token",
                        WorkspaceConnection.schema_version == CATALOG_VERSION,
                        WorkspaceConnection.is_active.is_(True),
                        WorkspaceConnection.state.in_(("unverified", "verified")),
                        WorkspaceConnectionSecret.secret_encrypted != "",
                    )
                    .order_by(WorkspaceConnection.id)
                    .limit(100)
                    .with_for_update(of=WorkspaceConnection)
                )
            ).all()
            result = []
            for row in rows:
                try:
                    policy, root = _cloud_policy(row)
                    if not policy.permits(root, FileOperation.LIST):
                        continue
                except ConnectionDenied:
                    continue
                result.append({"id": row.id, "label": row.label, "root": root})
            return result

    async def begin_page(self, *, tenant_id, actor_id, connection_id, folder):
        """Authorize an internal read's context; this does not contact a provider."""
        async with _owned(tenant_id, actor_id, connection_id) as (_, connection, policy, __):
            try:
                folder = _path(folder)
            except ValueError:
                raise ConnectionDenied() from None
            if not policy.permits(folder, FileOperation.LIST):
                raise ConnectionDenied()
            return CatalogPageTicket(
                tenant_id, actor_id, connection_id, folder, _policy_key(connection.config), datetime.now(timezone.utc)
            )

    async def admit_page(self, *, tenant_id, actor_id, connection_id, ticket, page):
        """Atomically admit one trusted metadata page, never infer missing/deleted.

        DiskPage carries no stable ID/version/size. An unchanged replay updates
        no observation count and downloads no bytes. This is an internal adapter
        boundary, not a route accepting user-supplied metadata or page tickets.
        """
        if type(ticket) is not CatalogPageTicket or (ticket.tenant_id, ticket.actor_id, ticket.connection_id) != (
            tenant_id,
            actor_id,
            connection_id,
        ):
            raise ConnectionDenied()
        now = datetime.now(timezone.utc)
        if (
            not isinstance(ticket.issued_at, datetime)
            or ticket.issued_at.tzinfo is None
            or not now - TICKET_TTL <= ticket.issued_at <= now
        ):
            raise ConnectionDenied()
        if type(page) is not DiskPage or type(page.items) is not tuple or len(page.items) > MAX_PAGE:
            raise CatalogInputError("invalid_page")
        if type(page.truncated) is not bool or (
            page.next_offset is not None
            and (type(page.next_offset) is not int or not 0 <= page.next_offset <= 1_000_000)
        ):
            raise CatalogInputError("invalid_page")
        try:
            folder = _components(ticket.folder)
            prepared = {}
            for item in page.items:
                if type(item) is not DiskItem or item.kind not in {"file", "dir"}:
                    raise ValueError
                path = _path(item.path)
                parts = _components(path)
                if len(parts) != len(folder) + 1 or parts[:-1] != folder or item.name != parts[-1]:
                    raise ValueError
                if len(item.name) > 500 or path in prepared:
                    raise ValueError
                prepared[path] = item
        except (ValueError, TypeError, AttributeError):
            raise CatalogInputError("invalid_page") from None
        async with _owned(tenant_id, actor_id, connection_id) as (session, connection, policy, root_key):
            if _policy_key(connection.config) != ticket.policy_key or not policy.permits(
                ticket.folder, FileOperation.LIST
            ):
                raise ConnectionDenied()
            # Filter before loading/storing data; a hidden child's existence is not returned.
            prepared = {p: i for p, i in prepared.items() if policy.permits(p, FileOperation.LIST)}
            keys = [_key(path) for path in prepared]
            rows = (
                await session.scalars(
                    select(FileCatalogObject).where(
                        FileCatalogObject.tenant_id == tenant_id,
                        FileCatalogObject.connection_id == connection_id,
                        FileCatalogObject.root_key == root_key,
                        FileCatalogObject.path_key.in_(keys),
                    )
                )
            ).all()
            existing = {row.path: row for row in rows}
            count = await session.scalar(
                select(func.count())
                .select_from(FileCatalogObject)
                .where(
                    FileCatalogObject.tenant_id == tenant_id,
                    FileCatalogObject.connection_id == connection_id,
                )
            )
            if count + sum(path not in existing for path in prepared) > MAX_OBJECTS:
                raise CatalogCapacityError("catalog_quota")
            created = changed = 0
            for path, item in prepared.items():
                row = existing.get(path)
                if row is not None and row.observed_at >= ticket.issued_at:
                    continue
                different = row is None or (row.name, row.kind) != (item.name, item.kind)
                if row is None:
                    row = FileCatalogObject(
                        tenant_id=tenant_id,
                        connection_id=connection_id,
                        root_key=root_key,
                        path_key=_key(path),
                        path=path,
                        name=item.name,
                        name_search=_search_key(item.name),
                        kind=item.kind,
                        observation_number=1,
                        observed_at=ticket.issued_at,
                    )
                    session.add(row)
                    await session.flush()
                    created += 1
                else:
                    if different:
                        if row.observation_number >= MAX_OBSERVATIONS:
                            raise CatalogCapacityError("observation_quota")
                        row.observation_number += 1
                        changed += 1
                    row.name, row.name_search, row.kind, row.observed_at = (
                        item.name, _search_key(item.name), item.kind, ticket.issued_at
                    )
                if different:
                    session.add(
                        FileCatalogObservation(
                            tenant_id=tenant_id,
                            object_id=row.id,
                            number=row.observation_number,
                            name=item.name,
                            kind=item.kind,
                            observed_at=ticket.issued_at,
                        )
                    )
            return {"created": created, "metadata_changed": changed}

    async def list_page(self, *, tenant_id, actor_id, connection_id, query="", after=0, limit=50):
        _bounded_int(after, 0, 2_147_483_647)
        _bounded_int(limit, 1, MAX_PAGE)
        if not isinstance(query, str) or len(query) > 200 or any(ord(c) < 32 for c in query):
            raise CatalogInputError("invalid_query")
        async with _owned(tenant_id, actor_id, connection_id) as (session, connection, _, root_key):
            stmt = select(FileCatalogObject).where(
                FileCatalogObject.tenant_id == tenant_id,
                FileCatalogObject.connection_id == connection_id,
                FileCatalogObject.root_key == root_key,
                FileCatalogObject.id > after,
                _visible_paths(FileCatalogObject.path, connection.config),
            )
            if query.strip():
                stmt = stmt.where(FileCatalogObject.name_search.contains(_search_key(query.strip()), autoescape=True))
            rows = (await session.scalars(stmt.order_by(FileCatalogObject.id).limit(limit))).all()
            return {
                "items": [_object_dict(row) for row in rows],
                # A hint, not a leaked total/count; an extra empty page is safe.
                "next_after": rows[-1].id if len(rows) == limit else None,
            }

    async def detail(self, *, tenant_id, actor_id, connection_id, object_id):
        if type(object_id) is not int or not 1 <= object_id <= 2_147_483_647:
            raise ConnectionDenied()
        async with _owned(tenant_id, actor_id, connection_id) as (session, connection, _, root_key):
            row = await session.scalar(
                select(FileCatalogObject).where(
                    FileCatalogObject.tenant_id == tenant_id,
                    FileCatalogObject.connection_id == connection_id,
                    FileCatalogObject.root_key == root_key,
                    FileCatalogObject.id == object_id,
                    _visible_paths(FileCatalogObject.path, connection.config),
                )
            )
            if row is None:
                raise ConnectionDenied()
            history = (
                await session.scalars(
                    select(FileCatalogObservation)
                    .where(
                        FileCatalogObservation.tenant_id == tenant_id,
                        FileCatalogObservation.object_id == row.id,
                    )
                    .order_by(FileCatalogObservation.number.desc())
                    .limit(MAX_OBSERVATIONS)
                )
            ).all()
            result = _object_dict(row)
            result["observations"] = [
                {"number": item.number, "name": item.name, "kind": item.kind, "observed_at": item.observed_at}
                for item in history
            ]
            return result
