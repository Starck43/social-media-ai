"""Personal authorization material and independently revocable workspace consent.

No generic manager or HTTP secret-import surface is registered for these tables.
Use the permission-scoped connector_authorizations service, never the legacy vault.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped

from app.core.config import settings
from app.models.base import Base, TenantScopedMixin, TimestampMixin


class PersonalConnectorAuthorization(Base, TimestampMixin):
    __tablename__ = "personal_connector_authorizations"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id", "provider_id", "auth_method", "subject_key", name="uq_personal_connector_subject"
        ),
        UniqueConstraint("id", "owner_user_id", name="uq_personal_connector_owner"),
        CheckConstraint("state IN ('active','reauth','revoked')", name="ck_personal_connector_state"),
        CheckConstraint("revision > 0", name="ck_personal_connector_revision"),
        CheckConstraint("execution_generation >= 0", name="ck_personal_connector_execution_generation"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="CASCADE"), nullable=False
    )
    provider_id: Mapped[str] = Column(String(50), nullable=False)
    auth_method: Mapped[str] = Column(String(30), nullable=False)
    # Provider-verified stable identity, NOT a supplied email/phone/username.
    subject_key: Mapped[str] = Column(String(320), nullable=False)
    state: Mapped[str] = Column(String(20), nullable=False, default="active", server_default="active")
    revision: Mapped[int] = Column(Integer, nullable=False, default=1, server_default="1")
    # Runtime fencing epoch, NOT consent/envelope revision. Never reset/reuse.
    execution_generation: Mapped[int] = Column(BigInteger, nullable=False, default=0, server_default="0")
    secret_encrypted: Mapped[str] = Column(Text, nullable=False)


class WorkspaceAuthorizationGrant(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "workspace_authorization_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["authorization_id", "owner_user_id"],
            [
                f"{settings.DB_SCHEMA}.personal_connector_authorizations.id",
                f"{settings.DB_SCHEMA}.personal_connector_authorizations.owner_user_id",
            ],
            ondelete="CASCADE",
            name="fk_workspace_authorization_owner",
        ),
        UniqueConstraint("tenant_id", "authorization_id", name="uq_workspace_authorization_grant"),
        CheckConstraint("authorization_revision > 0", name="ck_workspace_authorization_revision"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = Column(Integer, nullable=False)
    authorization_id: Mapped[int] = Column(Integer, nullable=False)
    authorization_revision: Mapped[int] = Column(Integer, nullable=False)
    is_active: Mapped[bool] = Column(Boolean, nullable=False, default=True, server_default="true")
