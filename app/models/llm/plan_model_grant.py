"""Per-plan, per-capability grants of platform catalog models (P21-T13).

A grant names a numeric `llm_models.id` in a **platform** catalog, the capability
it serves and its deterministic order. Managed defaults live here, not in
`llm_models.is_default`, which keeps its personal-catalog meaning.

The plan key is the existing validated tier set (`Tenant.PLANS`); plan policy
itself remains the P2-T17 authority, so this table stores grants, not packaging.
Nothing resolves managed models through these rows yet: increment A is storage.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, relationship

from ...core.config import settings
from ...core.decorators import app_label
from ..base import Base, TimestampMixin

#: The validated plan set already enforced by `tenants.plan` and the accepted
#: P2-T17 policy storage. This module does not introduce another registry.
PLAN_CODES: tuple[str, ...] = ("starter", "pro", "business")

#: Capability keys a grant may name. The closed catalog vocabulary; grants are
#: never free-form strings, so a typo cannot silently grant nothing (or worse).
GRANT_CAPABILITIES: tuple[str, ...] = ("text", "image", "video", "embedding", "audio")


@app_label("llm")
class PlanModelGrant(Base, TimestampMixin):
    """One capability of one platform model granted to one plan."""

    __tablename__ = "plan_model_grants"
    __table_args__ = (
        CheckConstraint("plan IN ('starter', 'pro', 'business')", name="ck_plan_model_grant_plan"),
        CheckConstraint(
            "capability IN ('text', 'image', 'video', 'embedding', 'audio')", name="ck_plan_model_grant_capability"
        ),
        CheckConstraint("priority >= 0", name="ck_plan_model_grant_priority"),
        Index(
            "uq_plan_model_grant_membership",
            "plan",
            "capability",
            "llm_model_id",
            unique=True,
        ),
        Index(
            "uq_plan_model_grant_active_default",
            "plan",
            "capability",
            unique=True,
            postgresql_where=(text("is_active AND is_default")),
        ),
        Index(
            "ix_plan_model_grant_order",
            "plan",
            "capability",
            "is_active",
            "priority",
            "llm_model_id",
        ),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    plan: Mapped[str] = Column(String(20), nullable=False)
    capability: Mapped[str] = Column(String(64), nullable=False)
    llm_model_id: Mapped[int] = Column(
        Integer,
        ForeignKey(f"{settings.DB_SCHEMA}.llm_models.id", ondelete="RESTRICT", name="fk_plan_model_grant_model"),
        nullable=False,
        index=True,
    )
    is_active: Mapped[bool] = Column(Boolean, nullable=False, default=True, server_default="true")
    priority: Mapped[int] = Column(Integer, nullable=False, default=100, server_default="100")
    is_default: Mapped[bool] = Column(Boolean, nullable=False, default=False, server_default="false")

    model = relationship("LLMModel", foreign_keys=[llm_model_id])

    if TYPE_CHECKING:
        from ..managers.base_manager import BaseManager
        from ..managers.llm_catalog_manager import PlanModelGrantManager

        objects: ClassVar[PlanModelGrantManager | BaseManager]
    else:
        objects: ClassVar[Any] = None

    def __str__(self) -> str:
        return f"{self.plan}/{self.capability} -> model #{self.llm_model_id}"
