"""Installation-level trusted outbound host registry (P14-T09).

Reads feed the egress seam (`app.core.outbound`) as a frozen snapshot taken per
connector operation. Writes are installation-administrator only, re-checked from
the database at execution, and audited in the caller's transaction.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from typing import Any, Iterable

from sqlalchemy import select

from app.core import outbound
from app.types import PlatformRoleType

from .base_manager import BaseManager

logger = logging.getLogger(__name__)

# Mirrors `WebPerms.is_superuser_role`; a test keeps the two in sync.
INSTALLATION_ADMIN_ROLES = frozenset({PlatformRoleType.OPERATOR.value, PlatformRoleType.ADMIN.value})
MAX_ADDRESSES = 16
MAX_HOSTS = 200
AUDIT_ADD = "admin.trusted_host_add"
AUDIT_REMOVE = "admin.trusted_host_remove"
AUDIT_RESOURCE = "trusted_host"


class TrustedHostError(ValueError):
    """Value-safe registry error code for the admin surface."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def validate_host(raw: str) -> str:
    from app.models.integrations.trusted_outbound_host import HOST_PATTERN

    try:
        host = outbound.normalize_host(raw)
    except outbound.OutboundRefusal as exc:
        raise TrustedHostError("invalid_host") from exc
    if outbound.is_ip_literal(host) or re.fullmatch(HOST_PATTERN, host) is None:
        # IP literals and single-label names are refused: TLS needs a hostname.
        raise TrustedHostError("invalid_host")
    return host


def validate_addresses(kind: str, raw: Iterable[str]) -> list[str]:
    """`public` carries no addresses; `private` lists 1..16 private unicast IPs."""
    if kind not in outbound.KINDS:
        raise TrustedHostError("invalid_kind")
    values = [str(item).strip() for item in raw if str(item).strip()]
    if kind == "public":
        if values:
            raise TrustedHostError("public_host_takes_no_addresses")
        return []
    if not values or len(values) > MAX_ADDRESSES:
        raise TrustedHostError("private_host_requires_addresses")
    result: list[str] = []
    for value in values:
        try:
            ip = ipaddress.ip_address(value)
        except ValueError as exc:
            raise TrustedHostError("invalid_address") from exc
        # Never authorize this server's own loopback, cloud metadata or broadcast space.
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            raise TrustedHostError("address_forbidden")
        if str(ip) not in result:
            result.append(str(ip))
    return result


def to_entry(row: Any) -> outbound.TrustedHost:
    return outbound.TrustedHost(
        host=row.host,
        kind=row.kind,
        authorized_addresses=frozenset(str(a) for a in (row.authorized_addresses or [])),
    )


class TrustedOutboundHostManager(BaseManager):
    def __init__(self):
        from ..integrations.trusted_outbound_host import TrustedOutboundHost

        super().__init__(TrustedOutboundHost)

    async def snapshot(self, session=None) -> outbound.RegistryReader:
        async with self._get_owned_session(session) as db:
            rows = (await db.execute(select(self.model).order_by(self.model.id).limit(MAX_HOSTS))).scalars().all()
            return outbound.registry_from_entries(to_entry(row) for row in rows)

    @staticmethod
    async def _require_admin(session, actor_id: int) -> None:
        from app.models.identity.role import PlatformRole
        from app.models.identity.user import User

        if type(actor_id) is not int or actor_id <= 0:
            raise TrustedHostError("admin_required")
        codename = (
            await session.execute(
                select(PlatformRole.codename)
                .join(User, User.role_id == PlatformRole.id)
                .where(User.id == actor_id, User.is_active.is_(True))
            )
        ).scalar_one_or_none()
        if codename not in INSTALLATION_ADMIN_ROLES:
            raise TrustedHostError("admin_required")

    async def list_hosts(self, *, actor_id: int, session=None) -> list[Any]:
        async with self._get_owned_session(session) as db:
            await self._require_admin(db, actor_id)
            return list((await db.execute(select(self.model).order_by(self.model.host))).scalars().all())

    async def add_host(
        self,
        *,
        actor_id: int,
        host: str,
        kind: str,
        addresses: Iterable[str] = (),
        note: str = "",
        session=None,
    ):
        from app.models.audit.action_audit_event import ActionAuditEvent

        normalized = validate_host(host)
        authorized = validate_addresses(kind, addresses)
        clean_note = str(note or "").strip()[:200]
        async with self._get_owned_session(session) as db:
            await self._require_admin(db, actor_id)
            exists = (await db.execute(select(self.model.id).where(self.model.host == normalized))).scalar()
            if exists is not None:
                raise TrustedHostError("host_already_trusted")
            row = self.model(
                host=normalized,
                kind=kind,
                authorized_addresses=authorized,
                note=clean_note,
                created_by_actor_id=actor_id,
            )
            db.add(row)
            await db.flush()
            await ActionAuditEvent.objects.record_success(
                session=db,
                actor_kind="user",
                actor_id=actor_id,
                action=AUDIT_ADD,
                resource_kind=AUDIT_RESOURCE,
                resource_id=row.id,
            )
            return row

    async def remove_host(self, *, actor_id: int, host_id: int, confirm_host: str, session=None) -> str:
        from app.models.audit.action_audit_event import ActionAuditEvent

        async with self._get_owned_session(session) as db:
            await self._require_admin(db, actor_id)
            row = (await db.execute(select(self.model).where(self.model.id == host_id).with_for_update())).scalar()
            if row is None:
                raise TrustedHostError("host_not_found")
            if str(confirm_host or "").strip().lower() != row.host:
                raise TrustedHostError("confirmation_required")
            host = row.host
            await ActionAuditEvent.objects.record_success(
                session=db,
                actor_kind="user",
                actor_id=actor_id,
                action=AUDIT_REMOVE,
                resource_kind=AUDIT_RESOURCE,
                resource_id=row.id,
            )
            await db.delete(row)
            await db.flush()
            return host


async def load_trusted_registry() -> outbound.RegistryReader:
    """Default registry loader for connectors: unreadable or missing → empty (cloud-only)."""
    from app.models.integrations.trusted_outbound_host import TrustedOutboundHost

    try:
        return await TrustedOutboundHost.objects.snapshot()
    except Exception:  # noqa: BLE001 - fail closed to the code-owned cloud policy
        logger.warning("trusted_outbound_registry_unavailable error_code=registry_read_failed")
        return outbound.empty_registry
