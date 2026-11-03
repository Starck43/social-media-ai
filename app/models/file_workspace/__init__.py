"""Cloud metadata only; observations are not content snapshots or backups.

Connection ownership/policy remain the authority. These models intentionally have
no generic BaseManager: use FileCatalogManager, which rechecks that authority.
"""

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.decorators import app_label
from app.models.base import Base, TenantScopedMixin, TimestampMixin


def _connection_fk():
    return ForeignKeyConstraint(
        ["tenant_id", "connection_id"],
        [f"{settings.DB_SCHEMA}.workspace_connections.tenant_id", f"{settings.DB_SCHEMA}.workspace_connections.id"],
        ondelete="CASCADE",
    )


@app_label("knowledge")
class FileCatalogObject(TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "file_catalog_objects"
    __table_args__ = (
        _connection_fk(),
        UniqueConstraint("tenant_id", "id", name="uq_file_catalog_object_tenant"),
        UniqueConstraint("tenant_id", "connection_id", "root_key", "path_key", name="uq_file_catalog_path"),
        CheckConstraint("kind IN ('file','dir')", name="ck_file_catalog_kind"),
        CheckConstraint("observation_number > 0", name="ck_file_catalog_number"),
        CheckConstraint("octet_length(path) <= 2048", name="ck_file_catalog_path_size"),
        CheckConstraint("path_key = encode(sha256(convert_to(path, 'UTF8')), 'hex')", name="ck_file_catalog_path_key"),
        Index("ix_file_catalog_page", "tenant_id", "connection_id", "root_key", "id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = mapped_column(Integer)
    # Path identity is explicit until the adapter supplies reviewed stable IDs.
    root_key: Mapped[str] = mapped_column(String(64))
    path_key: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(String(2048))
    name: Mapped[str] = mapped_column(String(500))
    # Derived search data, not identity or display text.
    name_search: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(10))
    observation_number: Mapped[int] = mapped_column(Integer, default=1)
    observed_at = mapped_column(DateTime(timezone=True), nullable=False)


@app_label("knowledge")
class FileCatalogObservation(TenantScopedMixin, TimestampMixin, Base):
    """Bounded metadata observations; never claim detection of content changes."""

    __tablename__ = "file_catalog_observations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "object_id"],
            [f"{settings.DB_SCHEMA}.file_catalog_objects.tenant_id", f"{settings.DB_SCHEMA}.file_catalog_objects.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "object_id", "number", name="uq_file_catalog_observation"),
        CheckConstraint("kind IN ('file','dir')", name="ck_file_observation_kind"),
        CheckConstraint("number > 0", name="ck_file_observation_number"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    object_id: Mapped[int] = mapped_column(Integer)
    number: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(10))
    observed_at = mapped_column(DateTime(timezone=True), nullable=False)
