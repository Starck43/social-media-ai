"""Contact PII and tenant-bound references (P13-T07)."""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.models.base import Base, TenantScopedMixin, TimestampMixin
from app.models.types.encrypted import INDEX_COLUMN_LENGTH, EncryptedText

S = settings.DB_SCHEMA
IDENTITY_KINDS = (
    "email",
    "phone",
    "telegram_id",
    "telegram_username",
    "max_id",
    "bitrix24_contact",
    "bitrix24_company",
    "bitrix24_lead",
)
CHANNELS = ("email", "phone", "telegram", "max", "bitrix24")
PURPOSES = ("transactional", "greeting", "marketing")


def choices(column, values, name):
    return CheckConstraint(f"{column} IN ({', '.join(repr(v) for v in values)})", name=name)


def contact_fk(column="contact_id", *, ondelete="CASCADE"):
    return ForeignKeyConstraint(
        ["tenant_id", column],
        [f"{S}.contacts.tenant_id", f"{S}.contacts.id"],
        ondelete=ondelete,
    )


class Contact(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_contacts_tenant_id"),
        contact_fk("merged_into_id", ondelete="NO ACTION"),
        CheckConstraint("merged_into_id IS NULL OR merged_into_id != id", name="ck_contact_not_self_merged"),
        choices("preferred_channel", CHANNELS, "ck_contact_preferred_channel"),
        choices("last_active_channel", CHANNELS, "ck_contact_last_channel"),
        Index("ix_contacts_tenant_id_id", "tenant_id", "id"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name_encrypted: Mapped[str] = mapped_column(EncryptedText(), nullable=False)
    notes_encrypted: Mapped[str | None] = mapped_column(EncryptedText())
    birthday_encrypted: Mapped[str | None] = mapped_column(EncryptedText())
    birthday_has_year: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    anniversary_encrypted: Mapped[str | None] = mapped_column(EncryptedText())
    anniversary_has_year: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    preferred_channel: Mapped[str | None] = mapped_column(String(20))
    last_active_channel: Mapped[str | None] = mapped_column(String(20))
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    merged_into_id: Mapped[int | None] = mapped_column(Integer)
    pii_updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default="1")
    objects: ClassVar = None


class ContactIdentity(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "contact_identities"
    __table_args__ = (
        contact_fk(),
        ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            [f"{S}.workspace_connections.tenant_id", f"{S}.workspace_connections.id"],
            ondelete="CASCADE",
        ),
        choices("kind", IDENTITY_KINDS, "ck_contact_identity_kind"),
        UniqueConstraint("tenant_id", "kind", "blind_index", "connection_id", name="uq_contact_identity_connection"),
        Index(
            "uq_contact_identity_unbound",
            "tenant_id",
            "kind",
            "blind_index",
            unique=True,
            postgresql_where=text("connection_id IS NULL"),
        ),
        Index("ix_contact_identity_contact", "tenant_id", "contact_id"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    connection_id: Mapped[int | None] = mapped_column(Integer)
    value_encrypted: Mapped[str] = mapped_column(EncryptedText(), nullable=False)
    blind_index: Mapped[str] = mapped_column(String(INDEX_COLUMN_LENGTH), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    source_native_id_encrypted: Mapped[str | None] = mapped_column(EncryptedText())
    pii_updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default="1")
    objects: ClassVar = None


class ContactConsent(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "contact_consents"
    __table_args__ = (
        contact_fk(),
        choices("channel", CHANNELS, "ck_contact_consent_channel"),
        choices("purpose", PURPOSES, "ck_contact_consent_purpose"),
        CheckConstraint("granted_at IS NOT NULL OR withdrawn_at IS NOT NULL", name="ck_contact_consent_recorded"),
        CheckConstraint(
            "withdrawn_at IS NULL OR granted_at IS NULL OR withdrawn_at >= granted_at", name="ck_contact_consent_order"
        ),
        Index("ix_contact_consent_contact", "tenant_id", "contact_id"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(Integer, nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    basis_encrypted: Mapped[str] = mapped_column(EncryptedText(), nullable=False)
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_evidence_encrypted: Mapped[str] = mapped_column(EncryptedText(), nullable=False)
    pii_updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default="1")
    objects: ClassVar = None


class ContactMergeJournal(Base, TenantScopedMixin, TimestampMixin):
    """Reserved journal schema for P15-T03; no merge writer is exposed yet."""

    __tablename__ = "contact_merge_journal"
    __table_args__ = (
        contact_fk("source_contact_id"),
        contact_fk("target_contact_id"),
        CheckConstraint("source_contact_id != target_contact_id", name="ck_contact_merge_distinct"),
        Index("ix_contact_merge_source", "tenant_id", "source_contact_id"),
        Index("ix_contact_merge_target", "tenant_id", "target_contact_id"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_contact_id: Mapped[int] = mapped_column(Integer, nullable=False)
    target_contact_id: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_encrypted: Mapped[str | None] = mapped_column(EncryptedText())
    pii_updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default="1")
    objects: ClassVar = None
