"""Read and append successful platform workspace-offboarding evidence."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select, text

from app.core.config import settings
from app.core.permissions import get_current_user
from app.core.tenant_context import TenantContextError, is_bypass

_MAX_ID = 2147483647
_MAX_PAGE = 1000
_EVENT_ID = re.compile(r"^[0-9a-f]{32}$", re.ASCII)
_REASONS = frozenset({"duplicate", "owner_request", "contract_ended", "test_completed"})
_POLICIES = frozenset({"preserve", "expire_elapsed"})
_REQUIRED_GUARDS = {
    ("workspace_offboarding_events_immutable", 27, "guard_workspace_offboarding_mutation"),
    ("workspace_offboarding_events_no_truncate", 34, "guard_workspace_offboarding_mutation"),
}
_EXPECTED_GUARD_BODY = " ".join(
    "BEGIN RAISE EXCEPTION 'Workspace offboarding evidence is immutable' "
    "USING ERRCODE = '55000'; END;".lower().split()
)


class WorkspaceOffboardingManager:
    """Purpose-built manager with no generic create/update/delete surface."""

    def __init__(self):
        from ..audit.workspace_offboarding import WorkspaceOffboardingEvent

        self.model = WorkspaceOffboardingEvent

    @staticmethod
    async def _require_fresh_superuser(session) -> int:
        """Bind to the ambient SU identity and recheck its active role in this transaction."""
        from ..identity.role import PlatformRole
        from ..identity.user import User

        actor = get_current_user()
        if not is_bypass() or actor is None or type(getattr(actor, "id", None)) is not int:
            raise TenantContextError("Workspace offboarding evidence requires an active superuser context")
        row = (
            await session.execute(
                select(User.id)
                .join(PlatformRole, PlatformRole.id == User.role_id)
                .where(
                    User.id == actor.id,
                    User.is_active.is_(True),
                    PlatformRole.codename == "superuser",
                )
                .with_for_update(read=True, of=User)
            )
        ).scalar_one_or_none()
        if row is None:
            raise PermissionError("Workspace offboarding evidence requires a fresh active superuser")
        return int(row)

    async def ensure_available(self, *, session) -> None:
        """Fail closed unless the migrated table has both active immutability guards."""
        await self._require_fresh_superuser(session)
        rows = (
            await session.execute(
                text(
                    "SELECT t.tgname, t.tgtype, t.tgenabled, p.proname, pn.nspname, "
                    "p.prosrc, p.prosecdef, p.proconfig, p.pronargs, l.lanname, rt.typname "
                    "FROM pg_catalog.pg_trigger AS t "
                    "JOIN pg_catalog.pg_class AS c ON c.oid = t.tgrelid "
                    "JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace "
                    "JOIN pg_catalog.pg_proc AS p ON p.oid = t.tgfoid "
                    "JOIN pg_catalog.pg_namespace AS pn ON pn.oid = p.pronamespace "
                    "JOIN pg_catalog.pg_language AS l ON l.oid = p.prolang "
                    "JOIN pg_catalog.pg_type AS rt ON rt.oid = p.prorettype "
                    "WHERE n.nspname = :schema AND c.relname = :table "
                    "AND t.tgname IN ('workspace_offboarding_events_immutable', "
                    "'workspace_offboarding_events_no_truncate') "
                    "AND NOT t.tgisinternal AND t.tgqual IS NULL AND t.tgnargs = 0 "
                    "AND t.tgattr::text = ''"
                ),
                {"schema": settings.DB_SCHEMA, "table": self.model.__tablename__},
            )
        ).all()
        installed = set()
        for row in rows:
            body = " ".join(str(row[5]).lower().split())
            if (
                row[2] in ("O", "A")
                and str(row[4]) == settings.DB_SCHEMA
                and body == _EXPECTED_GUARD_BODY
                and row[6] is False
                and row[7] is None
                and int(row[8]) == 0
                and str(row[9]) == "plpgsql"
                and str(row[10]) == "trigger"
            ):
                installed.add((str(row[0]), int(row[1]), str(row[3])))
        if not _REQUIRED_GUARDS.issubset(installed):
            raise RuntimeError("Workspace offboarding journal is unavailable; apply its database migration")

    async def record_success(
        self,
        *,
        session,
        event_id: str,
        workspace_id: int,
        actor_id: int,
        reason_code: str,
        retention_policy: str,
        expired_audit_count: int,
    ):
        """Append success evidence to the caller's workspace-deletion transaction."""
        await self.ensure_available(session=session)
        # ensure_available rechecked the actor inside this same transaction.
        actor = get_current_user()
        if actor is None or type(getattr(actor, "id", None)) is not int:
            raise TenantContextError("Workspace offboarding evidence requires an active superuser context")
        fresh_actor_id = actor.id
        if type(event_id) is not str or _EVENT_ID.fullmatch(event_id) is None:
            raise ValueError("Invalid workspace-offboarding event identity")
        if type(workspace_id) is not int or not 0 < workspace_id <= _MAX_ID:
            raise ValueError("Invalid workspace identity")
        if type(actor_id) is not int or actor_id != fresh_actor_id:
            raise PermissionError("Workspace-offboarding actor differs from the active superuser")
        if reason_code not in _REASONS or retention_policy not in _POLICIES:
            raise ValueError("Invalid workspace-offboarding reason or retention policy")
        if type(expired_audit_count) is not int or expired_audit_count < 0:
            raise ValueError("Invalid expired audit count")
        event = self.model(
            event_id=event_id,
            workspace_id=workspace_id,
            actor_id=fresh_actor_id,
            reason_code=reason_code,
            retention_policy=retention_policy,
            expired_audit_count=expired_audit_count,
            outcome="succeeded",
        )
        session.add(event)
        await session.flush()
        return event

    async def list_events(self, *, session, limit: int = 100, before_id: int | None = None) -> list[Any]:
        """List newest first using a bounded page and fresh SU authorization."""
        await self.ensure_available(session=session)
        if type(limit) is not int or not 1 <= limit <= _MAX_PAGE:
            raise ValueError("Workspace-offboarding page size must be between 1 and 1000")
        if before_id is not None and (type(before_id) is not int or before_id <= 0):
            raise ValueError("Invalid workspace-offboarding cursor")
        statement = select(self.model).order_by(self.model.id.desc()).limit(limit)
        if before_id is not None:
            statement = statement.where(self.model.id < before_id)
        return list((await session.execute(statement)).scalars().all())

    async def export_events(self, *, session, limit: int = _MAX_PAGE) -> list[dict[str, Any]]:
        """Export bounded minimal journal fields, never free text or related PII."""
        events = await self.list_events(session=session, limit=limit)
        return [
            {
                "event_id": event.event_id,
                "workspace_id": event.workspace_id,
                "actor_id": event.actor_id,
                "occurred_at": event.occurred_at,
                "reason_code": event.reason_code,
                "retention_policy": event.retention_policy,
                "expired_audit_count": event.expired_audit_count,
                "outcome": event.outcome,
            }
            for event in events
        ]
