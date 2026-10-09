"""Workspace model and existing plan-limit behavior."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import JSON, Boolean, CheckConstraint, Column, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped

from ...core.config import settings
from ...core.decorators import app_label
from ..base import Base, TimestampMixin

if TYPE_CHECKING:
    from ..managers.base_manager import BaseManager
    from ..managers.tenant_manager import TenantManager


@app_label("account")
class Tenant(Base, TimestampMixin):
    """A client workspace (or your own "owner" workspace).

    The billing tier is `plan`. It is not decoration: `PLAN_LIMITS` is the
    single place that says what a tier may do, and `app/services/tenancy/limits.py`
    is what enforces it at every write path. Changing a tier therefore changes
    behaviour immediately — there is no second copy of the numbers to keep in
    sync.

    `daily_cost_limit` and `max_sources` remain columns because they are
    *per-workspace overrides* a superuser may tune below the tier's ceiling
    (a team that knows it spends little can tighten its own budget). The tier
    is the ceiling: `effective_limits()` takes the tighter of the two.
    """

    __tablename__ = "tenants"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_tenant_slug"),
        Index("ix_tenants_slug", "slug"),
        CheckConstraint(
            "plan IN ('starter', 'pro', 'business')",
            name="ck_tenants_plan",
        ),
        {"schema": settings.DB_SCHEMA},
    )

    #: Tier ceilings. `None` means unlimited (business).
    #:
    #: Two numbers here overlap with columns on this table (`daily_cost_limit`,
    #: `max_sources`). That is deliberate, not a duplicate source of truth: the
    #: column is the workspace's own setting, this is the plan's ceiling, and
    #: `effective_limits()` honours whichever is tighter. `docs/TENANCY.md` is the
    #: human-readable copy of this table — keep the two in step.
    PLAN_LIMITS: ClassVar[dict[str, dict[str, Any]]] = {
        "starter": {
            "max_sources": 3,
            "daily_cost_limit": 2.0,
            "max_channels": 1,
            "max_scenarios": 1,
            "max_team_members": 1,
            "max_tasks": 3,
            # Which analysis facets the tier may switch on (see
            # `app/services/ai/analyzer.py`). `None` = everything.
            "analytics_depth": ["sentiment"],
            # Starter may *decide* to act but never act unattended: the job
            # handler forces `dry_run` when this is False.
            "allow_auto_actions": False,
            "allow_learning": False,
            "allow_reflection": False,
            "retention_days": 7,
            "model_types": ["text"],
            "label": "Starter",
        },
        "pro": {
            "max_sources": 20,
            "daily_cost_limit": 20.0,
            "max_channels": 5,
            "max_scenarios": 10,
            "max_team_members": 5,
            "max_tasks": 20,
            "analytics_depth": ["sentiment", "topics", "content_mix"],
            "allow_auto_actions": True,
            "allow_learning": True,
            "allow_reflection": True,
            "retention_days": 30,
            "model_types": ["text", "image", "embedding"],
            "label": "Pro",
        },
        "business": {
            "max_sources": None,
            "daily_cost_limit": None,
            "max_channels": None,
            "max_scenarios": None,
            "max_team_members": None,
            "max_tasks": None,
            "analytics_depth": None,
            "allow_auto_actions": True,
            "allow_learning": True,
            "allow_reflection": True,
            "retention_days": 90,
            "model_types": ["text", "image", "embedding"],
            "label": "Business",
        },
    }

    #: Order the tiers are shown in; also the order of the CHECK constraint's
    #: `IN` list, so a downgrade reads as "fewer features", never as "invalid".
    PLANS: ClassVar[tuple[str, ...]] = ("starter", "pro", "business")

    #: A workspace created without an explicit tier gets `pro`. `personal` was the
    #: old free-flavoured label and is migrated to `pro` by migration 0071.
    DEFAULT_PLAN: ClassVar[str] = "pro"

    id: Mapped[int] = Column(Integer, primary_key=True)
    name: Mapped[str] = Column(String(100), nullable=False)
    slug: Mapped[str] = Column(String(50), nullable=False)
    plan: Mapped[str] = Column(
        String(20),
        nullable=False,
        default=DEFAULT_PLAN,
        server_default=DEFAULT_PLAN,
    )
    timezone: Mapped[str] = Column(String(50), nullable=False, default="Europe/Moscow", server_default="Europe/Moscow")
    is_active: Mapped[bool] = Column(Boolean, nullable=False, default=True, server_default="true")
    daily_cost_limit: Mapped[float] = Column(Float, nullable=False, default=5.0, server_default="5.0")
    max_sources: Mapped[int] = Column(Integer, nullable=False, default=20, server_default="20")
    agent_style: Mapped[dict[str, Any] | None] = Column(
        JSON,
        nullable=True,
        comment="Owner's reply style contract: {tone, length, language, quiet_hours}; rendered into the system prompt",
    )
    agent_model: Mapped[str | None] = Column(
        String(100),
        nullable=True,
        comment="LLM model name override for agent chat (e.g. 'gpt-4o', 'claude-3-sonnet')",
    )
    agent_max_tokens: Mapped[int | None] = Column(
        Integer,
        nullable=True,
        default=1024,
        comment="Max tokens for agent replies (overrides AGENT_MAX_TOKENS)",
    )
    agent_temperature: Mapped[float | None] = Column(
        Float,
        nullable=True,
        default=0.3,
        comment="Temperature for agent replies (overrides AGENT_TEMPERATURE)",
    )
    agent_system_prompt: Mapped[str | None] = Column(
        Text,
        nullable=True,
        comment="Custom system prompt override (appended to built-in DEFAULT_SYSTEM_PROMPT)",
    )

    if TYPE_CHECKING:
        objects: ClassVar[TenantManager | BaseManager]
    else:
        objects: ClassVar = None

    def __str__(self) -> str:
        return f"{self.name} ({self.slug})"

    # ── plan ──────────────────────────────────────────────────────────────

    @classmethod
    def normalize_plan(cls, plan: str | None) -> str:
        """A known tier name, or the default for anything else.

        Callers use this *before* writing, so a legacy `personal` row or a typo
        in the admin form cannot reach the CHECK constraint and abort the write.
        """
        value = (plan or "").strip().lower()
        return value if value in cls.PLAN_LIMITS else cls.DEFAULT_PLAN

    def plan_limits(self) -> dict[str, Any]:
        """The tier's ceilings (never the per-workspace override)."""
        return self.PLAN_LIMITS[self.normalize_plan(self.plan)]

    def effective_limits(self) -> dict[str, Any]:
        """Ceilings after applying this workspace's own overrides.

        `max_sources` and `daily_cost_limit` exist as columns so a superuser can
        tighten a workspace below its tier (a team on `pro` that only wants 5
        sources). Two rules follow, and they are worth stating because the
        alternative reading is wrong:

        * The tier caps upward. A column above the tier's ceiling is ignored, so
          a workspace cannot grant itself what it did not buy.
        * An unlimited tier stays unlimited. On `business` the columns are not
          consulted, because the tier *is* the contract there — honouring a
          `NOT NULL` column that defaults to 20 would silently cap a plan the
          pricing page advertises as unlimited.

        The practical consequence for `business`: to restrict such a workspace,
        move it down a tier rather than lowering the column.
        """
        limits = dict(self.plan_limits())
        for key in ("max_sources", "daily_cost_limit"):
            ceiling = limits[key]
            override = getattr(self, key, None)
            if ceiling is None or override is None:
                continue
            limits[key] = min(float(ceiling), float(override))
        return limits

    def has_feature(self, feature: str) -> bool:
        """A plan boolean: `allow_learning`, `allow_reflection`, ..."""
        return bool(self.plan_limits().get(feature))

    @property
    def plan_label(self) -> str:
        return self.plan_limits()["label"]

    def allows_model_type(self, model_type: str) -> bool:
        """Whether this tier may route calls to a model of that type.

        `starter` is text-only: it pays for tokens, not for an image pipeline.
        """
        allowed = self.plan_limits().get("model_types")
        return True if allowed is None else model_type in allowed

