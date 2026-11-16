"""Read-only tenant-scoped CRM entity cache (`crm_entities`, P14-T02).

Bitrix24 contacts, companies, leads, and deals are synchronized into `crm_entities`
while contacts/companies/leads are mapped into `Contact` + `ContactIdentity`
without auto-merging across workspaces or connections. Personal fields and
allowlisted custom fields are stored in `EncryptedText` columns (`title_encrypted`,
`personal_fields_encrypted`, `custom_fields_encrypted`) and registered in
`cli/commands/pii.py`.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.models.base import Base, TenantScopedMixin, TimestampMixin
from app.models.types.encrypted import EncryptedText

CRM_PROVIDERS = ("bitrix24",)
CRM_ENTITY_KINDS = ("contact", "company", "lead", "deal")


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class CrmEntity(Base, TimestampMixin, TenantScopedMixin):
    """Synchronized CRM entity snapshot (contact, company, lead, or deal)."""

    __tablename__ = "crm_entities"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_crm_entities_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "connection_id",
            "entity_kind",
            "external_id",
            name="uq_crm_entities_connection_external",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            [f"{settings.DB_SCHEMA}.workspace_connections.tenant_id", f"{settings.DB_SCHEMA}.workspace_connections.id"],
            ondelete="CASCADE",
            name="fk_crm_entities_connection",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            [f"{settings.DB_SCHEMA}.contacts.tenant_id", f"{settings.DB_SCHEMA}.contacts.id"],
            ondelete="SET NULL",
            name="fk_crm_entities_contact",
        ),
        CheckConstraint(f"provider IN ({_sql_list(CRM_PROVIDERS)})", name="ck_crm_entities_provider"),
        CheckConstraint(f"entity_kind IN ({_sql_list(CRM_ENTITY_KINDS)})", name="ck_crm_entities_kind"),
        Index("ix_crm_entities_tenant_kind", "tenant_id", "entity_kind"),
        Index("ix_crm_entities_tenant_contact", "tenant_id", "contact_id"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pii_updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default="1")
    connection_id: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False, default="bitrix24", server_default="bitrix24")
    entity_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    company_external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stage_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    currency_code: Mapped[str | None] = mapped_column(String(12), nullable=True)
    opportunity_amount: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    assigned_by_external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_deleted_remote: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    title_encrypted: Mapped[str] = mapped_column(EncryptedText(length=512), nullable=False)
    personal_fields_encrypted: Mapped[str | None] = mapped_column(EncryptedText(length=16000), nullable=True)
    custom_fields_encrypted: Mapped[str | None] = mapped_column(EncryptedText(length=32000), nullable=True)
    objects: ClassVar = None

    @property
    def entity_type(self) -> str:
        return self.entity_kind

    @entity_type.setter
    def entity_type(self, value: str) -> None:
        self.entity_kind = value

    @property
    def native_id(self) -> str:
        return self.external_id

    @native_id.setter
    def native_id(self, value: str) -> None:
        self.external_id = value

    @property
    def date_modify(self) -> datetime | None:
        return self.source_modified_at

    @date_modify.setter
    def date_modify(self, value: datetime | None) -> None:
        self.source_modified_at = value

    @property
    def personal_fields(self) -> dict[str, Any]:
        if not self.personal_fields_encrypted:
            return {}
        try:
            data = json.loads(self.personal_fields_encrypted)
            return data if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}

    @property
    def custom_fields(self) -> dict[str, Any]:
        if not self.custom_fields_encrypted:
            return {}
        try:
            data = json.loads(self.custom_fields_encrypted)
            return data if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}
