from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped

from ...core.config import settings
from ...core.decorators import app_label
from ..base import Base, TimestampMixin


@app_label("billing")
class PersonalBillingAccount(Base, TimestampMixin):
    """A user's personal AI spending account, independent of workspace membership.

    NULL daily_limit_usd means not configured and admission must fail closed;
    zero is an explicit unlimited policy.
    """

    __tablename__ = "personal_billing_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_personal_billing_accounts_user"),
        CheckConstraint(
            "daily_limit_usd IS NULL OR (daily_limit_usd >= 0 AND daily_limit_usd <= 999999999.999999999)",
            name="ck_personal_billing_accounts_daily_limit",
        ),
        CheckConstraint(
            "(configured_at IS NULL AND daily_limit_usd IS NULL AND policy_version IS NULL) OR "
            "(configured_at IS NOT NULL AND daily_limit_usd IS NOT NULL AND "
            "policy_version = 'personal-daily-cap-v1')",
            name="ck_personal_billing_accounts_configuration",
        ),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    user_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="RESTRICT"), nullable=False
    )
    daily_limit_usd: Mapped[Decimal | None] = Column(Numeric(18, 9), nullable=True)
    is_active: Mapped[bool] = Column(Boolean, nullable=False, default=True, server_default="true")
    configured_at: Mapped[datetime | None] = Column(DateTime(timezone=True), nullable=True)
    policy_version: Mapped[str | None] = Column(String(64), nullable=True)

    if TYPE_CHECKING:
        from ..managers.personal_billing_manager import PersonalBillingManager

        objects: ClassVar[PersonalBillingManager]
    else:
        objects: ClassVar = None


@app_label("billing")
class TaskBillingPayer(Base):
    """Current explicit payer consent for a task; no historical inference."""

    __tablename__ = "task_billing_payers"
    __table_args__ = (
        UniqueConstraint("agent_task_id", "consent_version", name="uq_task_billing_payers_consent"),
        CheckConstraint("agent_task_id > 0 AND consent_version > 0", name="ck_task_billing_payers_identity"),
        CheckConstraint("consented_by_user_id = payer_user_id", name="ck_task_billing_payers_self_consent"),
        Index("ix_task_billing_payers_current", "tenant_id", "agent_task_id", "consent_version"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    # Plain task id preserves consent history after the task itself is deleted.
    agent_task_id: Mapped[int] = Column(Integer, nullable=False)
    tenant_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.tenants.id", ondelete="RESTRICT"), nullable=False
    )
    payer_user_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="RESTRICT"), nullable=False
    )
    consented_by_user_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="RESTRICT"), nullable=False
    )
    consented_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    consent_version: Mapped[int] = Column(Integer, nullable=False)

    if TYPE_CHECKING:
        from ..managers.personal_billing_manager import PersonalBillingManager

        objects: ClassVar[PersonalBillingManager]
    else:
        objects: ClassVar = None
