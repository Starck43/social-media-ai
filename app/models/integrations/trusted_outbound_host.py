"""Installation-wide registry of outbound hosts approved by the administrator (P14-T09)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import CheckConstraint, DateTime, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings

from ..base import Base

HOST_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"


class TrustedOutboundHost(Base):
    """One destination the server may reach on behalf of any connector.

    No `tenant_id`, by the owner-authorized exception of 2026-11-04
    (docs/BUSINESS_MODULES.md, "Tenant isolation"): the row is a platform-wide
    technical fact that holds no customer data and is global by nature — it
    authorizes our egress, not a workspace's content. Only the installation
    administrator (`is_superuser_role`) can see or change it; a tenant actor can
    *use* an approved host through a connector and can never add one.

    `kind = 'public'` keeps the global-address rule and carries no addresses;
    `kind = 'private'` authorizes exactly `authorized_addresses`, per address.
    Changes are recorded in the installation-level `trusted_outbound_host_events` ledger.
    """

    __tablename__ = "trusted_outbound_hosts"
    __table_args__ = (
        UniqueConstraint("host", name="uq_trusted_outbound_hosts_host"),
        CheckConstraint(f"host ~ '{HOST_PATTERN}'", name="ck_trusted_outbound_hosts_host"),
        CheckConstraint("kind IN ('public', 'private')", name="ck_trusted_outbound_hosts_kind"),
        CheckConstraint(
            "jsonb_typeof(authorized_addresses) = 'array' AND ("
            "(kind = 'public' AND jsonb_array_length(authorized_addresses) = 0) OR "
            "(kind = 'private' AND jsonb_array_length(authorized_addresses) BETWEEN 1 AND 16))",
            name="ck_trusted_outbound_hosts_addresses",
        ),
        CheckConstraint("length(note) <= 200", name="ck_trusted_outbound_hosts_note"),
        CheckConstraint("created_by_actor_id > 0", name="ck_trusted_outbound_hosts_creator"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    host: Mapped[str] = mapped_column(String(253), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    authorized_addresses: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    note: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    created_by_actor_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    if TYPE_CHECKING:
        from ..managers.trusted_outbound_host_manager import TrustedOutboundHostManager

        objects: ClassVar[TrustedOutboundHostManager]
    else:
        objects: ClassVar = None
