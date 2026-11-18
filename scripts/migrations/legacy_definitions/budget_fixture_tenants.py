"""Frozen tenants DDL for the five-table budget fixture; never a runtime model.

Derived from Tenant and TimestampMixin at eb6cad6c56e78768b6482eba312082b849d24712.
The owner approved keeping this historical layout separate from current tenancy.
No users/model FKs, manager, app registration or current settings are loaded here.
"""

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)

SOURCE_REVISION = "eb6cad6c56e78768b6482eba312082b849d24712"
DEFAULT_PLAN = "pro"


def tenant_table(schema: str) -> Table:
    """Build only the historical table and indexes in the caller's explicit schema."""
    return Table(
        "tenants",
        MetaData(schema=schema),
        Column("id", Integer, primary_key=True),
        Column("name", String(100), nullable=False),
        Column("slug", String(50), nullable=False),
        Column("plan", String(20), nullable=False, default=DEFAULT_PLAN, server_default=DEFAULT_PLAN),
        Column("timezone", String(50), nullable=False, default="Europe/Moscow", server_default="Europe/Moscow"),
        Column("is_active", Boolean, nullable=False, default=True, server_default="true"),
        Column("daily_cost_limit", Float, nullable=False, default=5.0, server_default="5.0"),
        Column("max_sources", Integer, nullable=False, default=20, server_default="20"),
        Column(
            "agent_style",
            JSON,
            nullable=True,
            comment="Owner's reply style contract: {tone, length, language, quiet_hours}; rendered into the system prompt",
        ),
        Column(
            "agent_model",
            String(100),
            nullable=True,
            comment="LLM model name override for agent chat (e.g. 'gpt-4o', 'claude-3-sonnet')",
        ),
        Column(
            "agent_max_tokens",
            Integer,
            nullable=True,
            default=1024,
            comment="Max tokens for agent replies (overrides AGENT_MAX_TOKENS)",
        ),
        Column(
            "agent_temperature",
            Float,
            nullable=True,
            default=0.3,
            comment="Temperature for agent replies (overrides AGENT_TEMPERATURE)",
        ),
        Column(
            "agent_system_prompt",
            Text,
            nullable=True,
            comment="Custom system prompt override (appended to built-in DEFAULT_SYSTEM_PROMPT)",
        ),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()),
        UniqueConstraint("slug", name="uq_tenant_slug"),
        Index("ix_tenants_slug", "slug"),
        CheckConstraint("plan IN ('starter', 'pro', 'business')", name="ck_tenants_plan"),
    )
