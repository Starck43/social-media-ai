"""Persisted 1C query history: metadata, encrypted question and reusable typed plans (P17-T03).

Rows never hold returned 1C data. Reads re-check tenant membership and the source
view permission; question text is stored only as Fernet ciphertext.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import select

from app.models.managers.base_manager import BaseManager
from app.models.managers.workspace_connection_manager import ConnectionDenied, _authorize
from app.models.onec import OneCQueryHistory
from app.utils.crypto import encrypt_secret

MAX_HISTORY_ROWS = 50
MAX_STORED_PLAN_BYTES = 20000
VALID_STATUSES = frozenset({"executed", "exported"})
VALID_EXPORT_FORMATS = frozenset({"xlsx", "pdf"})


class OneCHistoryError(ValueError):
    """Fixed error code; never echoes stored question text or plan payload."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class OneCHistoryEntry:
    """PII-free history row returned to the API and chat tool."""

    id: int
    connection_id: int
    entity_set: str
    plan_fingerprint: str
    row_count: int
    duration_ms: int
    status: str
    exported_format: str | None
    created_at: datetime


@dataclass(frozen=True)
class OneCStoredPlan:
    """A stored plan available for reuse; the caller re-validates it before execution."""

    id: int
    connection_id: int
    entity_set: str
    plan_json: Mapping[str, Any]


class OneCQueryHistoryRepository:
    """Tenant- and actor-scoped persistence for read-only 1C query metadata."""

    def __init__(self) -> None:
        self._sessions = BaseManager(OneCQueryHistory)

    async def record(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        connection_id: int,
        entity_set: str,
        plan_json: Mapping[str, Any],
        plan_fingerprint: str,
        question: str | None = None,
        row_count: int = 0,
        duration_ms: int = 0,
        status: str = "executed",
        exported_format: str | None = None,
    ) -> int:
        if status not in VALID_STATUSES:
            raise OneCHistoryError("invalid_status")
        if exported_format is not None and exported_format not in VALID_EXPORT_FORMATS:
            raise OneCHistoryError("invalid_export_format")
        if type(row_count) is not int or row_count < 0 or type(duration_ms) is not int or duration_ms < 0:
            raise OneCHistoryError("invalid_metrics")
        if not isinstance(plan_fingerprint, str) or len(plan_fingerprint) != 64:
            raise OneCHistoryError("invalid_plan_fingerprint")
        if not isinstance(entity_set, str) or not entity_set or len(entity_set) > 128:
            raise OneCHistoryError("invalid_entity_set")
        try:
            serialized = dict(plan_json)
            encoded = str(serialized)
        except (TypeError, ValueError):
            raise OneCHistoryError("invalid_plan") from None
        if len(encoded) > MAX_STORED_PLAN_BYTES:
            raise OneCHistoryError("invalid_plan")

        question_encrypted = None
        if question:
            if not isinstance(question, str) or len(question) > 1000:
                raise OneCHistoryError("invalid_question")
            question_encrypted = encrypt_secret(question)

        async with self._sessions._get_owned_session() as session:
            await _authorize(session, tenant_id, actor_id, require_source_view=True)
            row = OneCQueryHistory(
                tenant_id=tenant_id,
                user_id=actor_id,
                connection_id=connection_id,
                entity_set=entity_set,
                plan_json=serialized,
                plan_fingerprint=plan_fingerprint,
                question_encrypted=question_encrypted,
                row_count=row_count,
                duration_ms=duration_ms,
                status=status,
                exported_format=exported_format,
                exported_at=datetime.now(timezone.utc) if exported_format is not None else None,
            )
            session.add(row)
            await session.flush()
            return int(row.id)

    async def mark_exported(self, *, tenant_id: int, actor_id: int, history_id: int, exported_format: str) -> None:
        if exported_format not in VALID_EXPORT_FORMATS:
            raise OneCHistoryError("invalid_export_format")
        async with self._sessions._get_owned_session() as session:
            await _authorize(session, tenant_id, actor_id, require_source_view=True)
            row = (
                await session.execute(
                    select(OneCQueryHistory).where(
                        OneCQueryHistory.id == history_id,
                        OneCQueryHistory.tenant_id == tenant_id,
                        OneCQueryHistory.user_id == actor_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                raise OneCHistoryError("history_not_found")
            row.exported_format = exported_format
            row.exported_at = datetime.now(timezone.utc)
            row.status = "exported"

    async def list_for_actor(
        self, *, tenant_id: int, actor_id: int, limit: int = MAX_HISTORY_ROWS
    ) -> tuple[OneCHistoryEntry, ...]:
        if type(limit) is not int or not 1 <= limit <= MAX_HISTORY_ROWS:
            raise OneCHistoryError("invalid_limit")
        async with self._sessions._get_owned_session() as session:
            await _authorize(session, tenant_id, actor_id, require_source_view=True)
            rows = (
                (
                    await session.execute(
                        select(OneCQueryHistory)
                        .where(
                            OneCQueryHistory.tenant_id == tenant_id,
                            OneCQueryHistory.user_id == actor_id,
                        )
                        .order_by(OneCQueryHistory.created_at.desc(), OneCQueryHistory.id.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
        return tuple(
            OneCHistoryEntry(
                id=int(row.id),
                connection_id=int(row.connection_id),
                entity_set=str(row.entity_set),
                plan_fingerprint=str(row.plan_fingerprint),
                row_count=int(row.row_count),
                duration_ms=int(row.duration_ms),
                status=str(row.status),
                exported_format=row.exported_format,
                created_at=row.created_at,
            )
            for row in rows
        )

    async def load_plan(self, *, tenant_id: int, actor_id: int, history_id: int) -> OneCStoredPlan:
        if type(history_id) is not int or history_id <= 0:
            raise OneCHistoryError("history_not_found")
        async with self._sessions._get_owned_session() as session:
            await _authorize(session, tenant_id, actor_id, require_source_view=True)
            row = (
                await session.execute(
                    select(OneCQueryHistory).where(
                        OneCQueryHistory.id == history_id,
                        OneCQueryHistory.tenant_id == tenant_id,
                        OneCQueryHistory.user_id == actor_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None or not isinstance(row.plan_json, Mapping):
                raise OneCHistoryError("history_not_found")
            return OneCStoredPlan(
                id=int(row.id),
                connection_id=int(row.connection_id),
                entity_set=str(row.entity_set),
                plan_json=dict(row.plan_json),
            )


__all__ = [
    "ConnectionDenied",
    "OneCHistoryEntry",
    "OneCHistoryError",
    "OneCQueryHistoryRepository",
    "OneCStoredPlan",
]
