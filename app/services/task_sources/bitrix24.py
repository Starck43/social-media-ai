"""Actor-scoped read-time projection of the actor's Bitrix24 tasks."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.access_catalog import workspace_grants
from app.core.database import with_db_session
from app.integrations.bitrix24.tasks import (
    PAGE_SIZE,
    BitrixTaskSourceError,
    _stream,
    current_user_id,
    normalized_deadline,
    task_url,
)
from app.models import SourceObject, WorkspaceConnection
from app.services.planner.tasks import planner_service
from app.types import ActionType

MAX_PAGE_SIZE = 20
MAX_OFFSET = 100_000


def _decode_cursor(cursor: str | None, *, connection_id: int, actor_id: int, profile_id: int) -> dict[str, int | str]:
    if cursor is None:
        return {"stream": "assigned", "offset": 0, "index": 0}
    if not isinstance(cursor, str) or len(cursor) > 1024:
        raise ValueError("invalid_task_source_cursor")
    try:
        payload = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
    except (ValueError, json.JSONDecodeError):
        raise ValueError("invalid_task_source_cursor") from None
    if (
        not isinstance(payload, dict)
        or type(payload.get("c")) is not int
        or type(payload.get("a")) is not int
        or type(payload.get("p")) is not int
        or payload.get("c") != connection_id
        or payload.get("a") != actor_id
        or payload.get("p") != profile_id
    ):
        raise ValueError("invalid_task_source_cursor")
    stream, offset, index = payload.get("s"), payload.get("o"), payload.get("i")
    if stream not in {"assigned", "watched"}:
        raise ValueError("invalid_task_source_cursor")
    if type(offset) is not int or offset < 0 or offset > MAX_OFFSET or offset % PAGE_SIZE:
        raise ValueError("invalid_task_source_cursor")
    if type(index) is not int or index < 0 or index >= PAGE_SIZE:
        raise ValueError("invalid_task_source_cursor")
    return {"stream": stream, "offset": offset, "index": index}


def _encode_cursor(
    connection_id: int,
    actor_id: int,
    profile_id: int,
    stream: str | None,
    offset: int = 0,
    index: int = 0,
) -> str | None:
    if stream is None:
        return None
    value = {"c": connection_id, "a": actor_id, "p": profile_id, "s": stream, "o": offset, "i": index}
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _consent(config: dict) -> dict:
    value = config.get("task_source")
    return value if isinstance(value, dict) else {}


class Bitrix24TaskSourceService:
    """Live-read tasks assigned to or watched by the connection owner."""

    @staticmethod
    def _require_source_grant(identity, action: ActionType) -> None:
        if ("source", action) not in workspace_grants(identity[2]):
            raise PermissionError("task_source_denied")

    async def _unavailable_refs(self, session, identity, row, limit: int):
        refs = list(
            (
                await session.scalars(
                    select(SourceObject)
                    .where(
                        SourceObject.tenant_id == identity[0],
                        SourceObject.connection_id == row.id,
                        SourceObject.external_id.like("bitrix24:task:%"),
                    )
                    .order_by(SourceObject.id.desc())
                    .limit(limit)
                )
            ).all()
        )
        return [
            {
                "id": ref.external_id,
                "source_ref": ref.external_id,
                "connection_id": row.id,
                "provider": "bitrix24",
                "title": "Task unavailable in Bitrix24",
                "status": "unavailable",
                "due_at": None,
                "stream": "unavailable",
                "read_only": True,
                "source_unavailable": True,
            }
            for ref in refs
        ]

    @with_db_session
    async def connections(self, session):
        identity = await planner_service._actor(session, ActionType.VIEW)
        self._require_source_grant(identity, ActionType.VIEW)
        rows = list(
            (
                await session.scalars(
                    select(WorkspaceConnection)
                    .where(
                        WorkspaceConnection.tenant_id == identity[0],
                        WorkspaceConnection.owner_user_id == identity[1],
                        WorkspaceConnection.provider_id == "bitrix24",
                    )
                    .order_by(WorkspaceConnection.id)
                )
            ).all()
        )
        items = []
        selected_id = None
        for row in rows:
            cfg = _consent(row.config or {})
            selected = bool(cfg.get("selected") and cfg.get("read_consent"))
            if selected:
                selected_id = row.id
            items.append(
                {
                    "id": row.id,
                    "label": row.label,
                    "state": row.state if row.is_active else "disabled",
                    "enabled": bool(cfg.get("read_consent")),
                    "selected": selected,
                    "read_only": True,
                    "portal_label": row.label,
                    "status": "available",
                }
            )
        return {
            "state": "available" if items else "not_configured",
            "items": items,
            "selected_connection_id": selected_id,
        }

    @with_db_session
    async def configure(self, connection_id: int, enabled: bool, selected: bool = True, session=None):
        if (
            type(connection_id) is not int
            or connection_id <= 0
            or type(enabled) is not bool
            or type(selected) is not bool
        ):
            raise ValueError("invalid_task_source_configuration")
        identity = await planner_service._actor(session, ActionType.VIEW)
        self._require_source_grant(identity, ActionType.CREATE)
        all_rows = list(
            (
                await session.scalars(
                    select(WorkspaceConnection)
                    .where(
                        WorkspaceConnection.tenant_id == identity[0],
                        WorkspaceConnection.owner_user_id == identity[1],
                        WorkspaceConnection.provider_id == "bitrix24",
                    )
                    .order_by(WorkspaceConnection.id)
                    .with_for_update()
                )
            ).all()
        )
        row = next((candidate for candidate in all_rows if candidate.id == connection_id), None)
        if row is None:
            raise LookupError("task_source_unavailable")
        now = datetime.now(timezone.utc).isoformat()
        for candidate in all_rows:
            config = dict(candidate.config or {})
            consent = dict(_consent(config))
            if candidate.id == connection_id:
                consent.update(
                    {
                        "read_consent": enabled,
                        "read_consented_by": identity[1] if enabled else None,
                        "read_consented_at": now if enabled else None,
                        "selected": bool(selected and enabled),
                        # There is deliberately no write-consent path in this task.
                        "write_consent": False,
                    }
                )
            elif selected and enabled:
                consent["selected"] = False
            config["task_source"] = consent
            candidate.config = config
        await session.flush()
        return {
            "state": "available",
            "connection_id": connection_id,
            "enabled": enabled,
            "selected": bool(selected and enabled),
            "read_only": True,
        }

    @with_db_session
    async def projection(
        self,
        connection_id: int | None = None,
        cursor: str | None = None,
        limit: int = 20,
        expected_tenant_id: int | None = None,
        expected_user_id: int | None = None,
        session=None,
    ):
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError("invalid_task_source_limit")
        identity = await planner_service._actor(session, ActionType.VIEW)
        self._require_source_grant(identity, ActionType.VIEW)
        if expected_tenant_id is not None and expected_tenant_id != identity[0]:
            return {"state": "denied", "items": [], "next_cursor": None}
        if expected_user_id is not None and expected_user_id != identity[1]:
            return {"state": "denied", "items": [], "next_cursor": None}
        if connection_id is None:
            rows = list(
                (
                    await session.scalars(
                        select(WorkspaceConnection)
                        .where(
                            WorkspaceConnection.tenant_id == identity[0],
                            WorkspaceConnection.owner_user_id == identity[1],
                            WorkspaceConnection.provider_id == "bitrix24",
                        )
                        .with_for_update()
                    )
                ).all()
            )
            selected = [r for r in rows if _consent(r.config or {}).get("selected")]
            if not selected:
                return {"state": "not_configured", "items": [], "next_cursor": None}
            if len(selected) != 1:
                return {"state": "source_unavailable", "items": [], "next_cursor": None}
            row = selected[0]
        else:
            row = await session.scalar(
                select(WorkspaceConnection)
                .where(
                    WorkspaceConnection.tenant_id == identity[0],
                    WorkspaceConnection.owner_user_id == identity[1],
                    WorkspaceConnection.id == connection_id,
                    WorkspaceConnection.provider_id == "bitrix24",
                )
                .with_for_update()
            )
            if row is None:
                return {"state": "not_configured", "items": [], "next_cursor": None}
        cfg = dict(row.config or {})
        consent = _consent(cfg)
        if not consent.get("read_consent") or consent.get("read_consented_by") != identity[1]:
            return {"state": "denied", "items": [], "next_cursor": None}
        if not row.is_active or row.state == "disabled":
            return {
                "state": "source_unavailable",
                "items": await self._unavailable_refs(session, identity, row, limit),
                "next_cursor": None,
            }
        if row.state == "reauth":
            return {
                "state": "reauth_required",
                "items": await self._unavailable_refs(session, identity, row, limit),
                "next_cursor": None,
            }
        if row.state != "verified":
            return {
                "state": "source_unavailable",
                "items": await self._unavailable_refs(session, identity, row, limit),
                "next_cursor": None,
            }
        try:
            profile_id = await current_user_id(
                tenant_id=identity[0], actor_id=identity[1], connection_id=row.id, session=session
            )
            cursor_state = _decode_cursor(cursor, connection_id=row.id, actor_id=identity[1], profile_id=profile_id)
        except BitrixTaskSourceError as exc:
            return {
                "state": exc.code,
                "items": await self._unavailable_refs(session, identity, row, limit),
                "next_cursor": None,
            }
        except ValueError:
            raise

        selected_rows = []
        stream = str(cursor_state["stream"])
        offset, start_index = int(cursor_state["offset"]), int(cursor_state["index"])
        next_stream: str | None = stream
        next_page_offset, next_index = offset, start_index
        while next_stream is not None and len(selected_rows) < limit:
            try:
                rows, provider_next = await _stream(
                    tenant_id=identity[0],
                    actor_id=identity[1],
                    connection_id=row.id,
                    profile_id=profile_id,
                    kind=stream,
                    offset=offset,
                    session=session,
                )
            except BitrixTaskSourceError as exc:
                return {
                    "state": exc.code,
                    "items": await self._unavailable_refs(session, identity, row, limit),
                    "next_cursor": None,
                }
            if any(
                (stream == "assigned" and task["responsible_id"] != profile_id)
                or (stream == "watched" and profile_id not in task["auditors"])
                for task in rows
            ):
                return {
                    "state": "source_unavailable",
                    "items": await self._unavailable_refs(session, identity, row, limit),
                    "next_cursor": None,
                }
            if stream == "watched":
                rows = [task for task in rows if task["responsible_id"] != profile_id]
            remaining = limit - len(selected_rows)
            page_items = rows[start_index : start_index + remaining]
            selected_rows.extend({**task, "stream": stream} for task in page_items)
            consumed_index = start_index + len(page_items)
            if consumed_index < len(rows):
                next_page_offset, next_index = offset, consumed_index
                break
            if provider_next is not None:
                offset, start_index = provider_next, 0
                next_page_offset, next_index = offset, 0
                break
            if stream == "assigned":
                stream, offset, start_index = "watched", 0, 0
                next_stream, next_page_offset, next_index = stream, offset, start_index
                # Continue into the watched stream now so watched-only users
                # receive tasks in their first projection.
                continue
            next_stream, next_page_offset, next_index = None, 0, 0
            break
        portal_domain = (row.config or {}).get("portal_domain") or row.account_key
        try:
            views = [
                {
                    "external_id": f"bitrix24:task:{task['external_id']}",
                    "source_ref": f"bitrix24:task:{task['external_id']}",
                    "connection_id": row.id,
                    "provider": "bitrix24",
                    "title": task["title"],
                    "status": task["status"],
                    "due_at": normalized_deadline(task["due_at"]),
                    "url": task_url(portal_domain, profile_id, int(task["external_id"])),
                    "stream": task["stream"],
                    "read_only": True,
                }
                for task in selected_rows
            ]
        except BitrixTaskSourceError as exc:
            return {
                "state": exc.code,
                "items": await self._unavailable_refs(session, identity, row, limit),
                "next_cursor": None,
            }

        # Persist identity only, never task text, status, or deadline. Locking the
        # connection above serializes this connection's registry inserts.
        items = []
        for view in views:
            external_id = view["external_id"]
            source = await session.scalar(
                select(SourceObject).where(
                    SourceObject.tenant_id == identity[0],
                    SourceObject.connection_id == row.id,
                    SourceObject.external_id == external_id,
                )
            )
            if source is None:
                source = SourceObject(tenant_id=identity[0], connection_id=row.id, external_id=external_id)
                session.add(source)
                await session.flush()
            items.append({**view, "id": external_id})
        return {
            "state": "available",
            "items": items[:limit],
            "next_cursor": _encode_cursor(row.id, identity[1], profile_id, next_stream, next_page_offset, next_index),
            "read_only": True,
        }


task_source_service = Bitrix24TaskSourceService()
