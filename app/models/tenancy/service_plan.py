"""Dormant account tariff storage; never exposed through generic workspace CRUD."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped

from app.core.config import settings

from ..base import Base, TimestampMixin


class ServicePlanPolicy(Base):
    __tablename__ = "service_plan_policies"
    __table_args__ = (
        CheckConstraint("plan_code IN ('starter', 'pro', 'business')", name="ck_service_plan_policy_code"),
        CheckConstraint("policy_version > 0", name="ck_service_plan_policy_version"),
        CheckConstraint("json_typeof(module_grants) = 'array'", name="ck_service_plan_policy_grants"),
        CheckConstraint("json_typeof(daily_quotas) = 'object'", name="ck_service_plan_policy_quotas"),
        CheckConstraint("json_typeof(resource_limits) = 'object'", name="ck_service_plan_policy_resources"),
        CheckConstraint(
            "(owned_workspace_limit_configured OR max_owned_workspaces IS NULL) "
            "AND (max_owned_workspaces IS NULL OR max_owned_workspaces >= 0)",
            name="ck_service_plan_policy_workspaces",
        ),
        CheckConstraint(
            "(NOT token_policy_configured AND token_unit IS NULL AND token_period IS NULL AND token_limit IS NULL) "
            "OR (token_policy_configured AND token_unit IS NOT NULL AND length(trim(token_unit)) > 0 "
            "AND token_period IS NOT NULL AND length(trim(token_period)) > 0 "
            "AND (token_limit IS NULL OR token_limit >= 0))",
            name="ck_service_plan_policy_tokens",
        ),
        {"schema": settings.DB_SCHEMA},
    )

    plan_code: Mapped[str] = Column(String(20), primary_key=True)
    policy_version: Mapped[int] = Column(Integer, primary_key=True)
    module_grants: Mapped[list[str]] = Column(JSON, nullable=False)
    daily_quotas: Mapped[dict[str, int]] = Column(JSON, nullable=False)
    resource_limits: Mapped[dict] = Column(JSON, nullable=False)
    owned_workspace_limit_configured: Mapped[bool] = Column(Boolean, nullable=False, server_default="false")
    max_owned_workspaces: Mapped[int | None] = Column(BigInteger, nullable=True)
    token_policy_configured: Mapped[bool] = Column(Boolean, nullable=False, server_default="false")
    token_unit: Mapped[str | None] = Column(String(80), nullable=True)
    token_period: Mapped[str | None] = Column(String(80), nullable=True)
    token_limit: Mapped[int | None] = Column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class UserServicePlan(Base, TimestampMixin):
    __tablename__ = "user_service_plans"
    __table_args__ = (
        ForeignKeyConstraint(
            ["plan_code", "policy_version"],
            [
                f"{settings.DB_SCHEMA}.service_plan_policies.plan_code",
                f"{settings.DB_SCHEMA}.service_plan_policies.policy_version",
            ],
            name="fk_user_service_plan_policy",
            ondelete="RESTRICT",
        ),
        {"schema": settings.DB_SCHEMA},
    )

    user_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="RESTRICT"), primary_key=True
    )
    plan_code: Mapped[str] = Column(String(20), nullable=False)
    policy_version: Mapped[int] = Column(Integer, nullable=False)
    is_active: Mapped[bool] = Column(Boolean, nullable=False, server_default="true")
    plan_expires_at: Mapped[datetime | None] = Column(DateTime(timezone=True), nullable=True)


class ServicePlanBackfillResolution(Base):
    __tablename__ = "service_plan_backfill_resolutions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["plan_code", "policy_version"],
            [
                f"{settings.DB_SCHEMA}.service_plan_policies.plan_code",
                f"{settings.DB_SCHEMA}.service_plan_policies.policy_version",
            ],
            name="fk_service_plan_backfill_policy",
            ondelete="RESTRICT",
        ),
        CheckConstraint("operator_user_id > 0", name="ck_service_plan_backfill_operator"),
        CheckConstraint("length(trim(decision_ref)) > 0", name="ck_service_plan_backfill_decision"),
        CheckConstraint("json_typeof(owner_user_ids) = 'array'", name="ck_service_plan_backfill_owners"),
        {"schema": settings.DB_SCHEMA},
    )

    tenant_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.tenants.id", ondelete="RESTRICT"), primary_key=True
    )
    holder_user_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="RESTRICT"), nullable=False
    )
    owner_user_ids: Mapped[list[int]] = Column(JSON, nullable=False)
    legacy_plan: Mapped[str] = Column(String(20), nullable=False)
    legacy_plan_expires_at: Mapped[datetime | None] = Column(DateTime(timezone=True), nullable=True)
    plan_code: Mapped[str] = Column(String(20), nullable=False)
    policy_version: Mapped[int] = Column(Integer, nullable=False)
    plan_is_active: Mapped[bool] = Column(Boolean, nullable=False)
    plan_expires_at: Mapped[datetime | None] = Column(DateTime(timezone=True), nullable=True)
    plan_decision_ref: Mapped[str | None] = Column(String(250), nullable=True)
    operator_user_id: Mapped[int] = Column(Integer, nullable=False)
    decision_ref: Mapped[str] = Column(String(250), nullable=False)
    resolved_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False)
