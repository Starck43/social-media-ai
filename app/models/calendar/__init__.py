"""Calendar orchestration records. Event bodies are fetched from the provider."""

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.models.base import Base, TenantScopedMixin, TimestampMixin
from app.models.types.encrypted import EncryptedText

S = settings.DB_SCHEMA


class SourceObject(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "source_objects"
    __table_args__ = (
        UniqueConstraint("tenant_id", "connection_id", "external_id", name="uq_source_object_identity"),
        UniqueConstraint("tenant_id", "id", name="uq_source_object_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            [f"{S}.workspace_connections.tenant_id", f"{S}.workspace_connections.id"],
            ondelete="CASCADE",
        ),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = mapped_column(Integer, nullable=False)
    external_id: Mapped[str] = mapped_column(String(1500), nullable=False)


class CalendarEvent(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "calendar_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_calendar_event_tenant"),
        UniqueConstraint("source_object_id", name="uq_calendar_event_source"),
        UniqueConstraint("tenant_id", "task_id", name="uq_calendar_event_task"),
        ForeignKeyConstraint(
            ["tenant_id", "source_object_id"],
            [f"{S}.source_objects.tenant_id", f"{S}.source_objects.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            [f"{S}.planner_tasks.tenant_id", f"{S}.planner_tasks.id"],
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("state IN ('synced','pending','conflict','deleted','unknown')", name="ck_calendar_event_state"),
        CheckConstraint("origin IN ('local','external')", name="ck_calendar_event_origin"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_object_id: Mapped[int] = mapped_column(Integer, nullable=False)
    task_id: Mapped[int | None] = mapped_column(ForeignKey(f"{S}.planner_tasks.id", ondelete="SET NULL"))
    origin: Mapped[str] = mapped_column(String(10), nullable=False)
    state: Mapped[str] = mapped_column(String(10), nullable=False, default="synced")
    etag: Mapped[str | None] = mapped_column(String(1000))
    task_version: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Only proposed changes and conflict evidence, never a read-time event cache.
    proposal_encrypted: Mapped[str | None] = mapped_column(EncryptedText())
    conflict_encrypted: Mapped[str | None] = mapped_column(EncryptedText())


class CalendarChoice(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "calendar_choices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "owner_user_id", name="uq_calendar_choice_owner"),
        ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            [f"{S}.workspace_connections.tenant_id", f"{S}.workspace_connections.id"],
            deferrable=True,
            initially="DEFERRED",
        ),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey(f"{S}.users.id", ondelete="CASCADE"), nullable=False)
    connection_id: Mapped[int | None] = mapped_column(ForeignKey(f"{S}.workspace_connections.id", ondelete="SET NULL"))
