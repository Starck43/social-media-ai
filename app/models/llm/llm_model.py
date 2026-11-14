from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, relationship

from ...core.config import settings
from ...core.decorators import app_label
from ..base import Base, TimestampMixin

#: The closed capability vocabulary stored in the JSONB column. It is the
#: `PLAN_LIMITS["model_types"]` set (`tenant.py`): `decision` belongs to the
#: retired role/permission `model_type` registry and is never a routing
#: capability, so a legacy CSV that names it keeps it in `model_type` only.
LEGACY_CAPABILITIES: frozenset[str] = frozenset({"text", "image", "video", "embedding", "audio"})

#: Input modalities a model may consume; a vision model is not an image
#: generator, so its input side is tracked apart from its output capability.
INPUT_CAPABILITIES: frozenset[str] = frozenset({"text", "image", "audio", "file"})

#: Operations a model may serve. Image generation is its own operation.
OPERATION_CAPABILITIES: frozenset[str] = frozenset(
    {"chat", "completion", "embedding", "image_generation", "transcript"}
)

PRICING_STATES: tuple[str, ...] = ("free", "priced", "undefined")


def _model_type_to_capabilities(model_type: str | None) -> list[str]:
    """Parse the legacy CSV exactly as `capabilities`/`can_handle` always did."""
    if not model_type:
        return []
    return [c.strip() for c in model_type.split(",") if c.strip()]


def normalize_capabilities(value) -> list[str] | None:
    """Validated, sorted, deduplicated capability list; `None` stays `None`.

    Raises `ValueError` for an unknown key, a non-list value or a duplicate
    after normalisation, so the stored set can never disagree with itself.
    """
    if value is None:
        return None
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise ValueError("Capabilities must be a list")
    items = list(value)
    if any(not isinstance(item, str) or item not in LEGACY_CAPABILITIES for item in items):
        raise ValueError("Unknown model capability")
    if len(set(items)) != len(items):
        raise ValueError("Duplicate model capability")
    return sorted(items)


#: The frozen legacy CSV order; the migration and the DB guard restate it.
_CAPABILITY_ORDER: tuple[str, ...] = ("text", "image", "video", "embedding", "audio")


def legacy_model_type(capabilities) -> str | None:
    """The legacy CSV mirror of a capability set, in the frozen parser order."""
    if capabilities is None:
        return None
    return ",".join(name for name in _CAPABILITY_ORDER if name in set(capabilities))


@app_label("llm")
class LLMModel(Base, TimestampMixin):
    """One model row inside a provider of the same catalog.

    Capabilities, ceilings and prices are stored here; generation defaults
    (`max_tokens`, `default_temperature`) stay separate — a model output
    ceiling is not a total allowance. `is_default` keeps its personal-catalog
    meaning; managed plan defaults live in `plan_model_grants`.
    """

    __tablename__ = "llm_models"
    __personal_scoped__ = True
    __table_args__ = (
        ForeignKeyConstraint(
            ["provider_id", "catalog_id"],
            [f"{settings.DB_SCHEMA}.llm_providers.id", f"{settings.DB_SCHEMA}.llm_providers.catalog_id"],
            name="fk_llm_model_provider_catalog",
            ondelete="CASCADE",
        ),
        UniqueConstraint("provider_id", "model_id", name="uq_llm_model_provider_model"),
        CheckConstraint(
            "(cache_read_cost_per_1m IS NULL OR cache_read_cost_per_1m >= 0) "
            "AND (cache_write_5m_cost_per_1m IS NULL OR cache_write_5m_cost_per_1m >= 0) "
            "AND (cache_write_1h_cost_per_1m IS NULL OR cache_write_1h_cost_per_1m >= 0)",
            name="ck_llm_models_cache_rates",
        ),
        CheckConstraint("pricing_token_unit IN (1000, 1000000)", name="ck_llm_models_pricing_token_unit"),
        CheckConstraint(
            "(input_price IS NULL OR input_price >= 0) AND (output_price IS NULL OR output_price >= 0)",
            name="ck_llm_models_prices",
        ),
        CheckConstraint(
            "pricing_state IS NULL OR pricing_state IN ('free', 'priced', 'undefined')",
            name="ck_llm_models_pricing_state",
        ),
        CheckConstraint(
            "(pricing_state IS DISTINCT FROM 'free') OR ((input_price IS NOT NULL AND output_price IS NOT NULL) "
            "AND (input_price = 0 AND output_price = 0))",
            name="ck_llm_models_free_price_zero",
        ),
        CheckConstraint(
            "(pricing_state IS DISTINCT FROM 'priced') OR ((input_price IS NOT NULL AND output_price IS NOT NULL) "
            "AND (input_price > 0 OR output_price > 0))",
            name="ck_llm_models_priced_positive",
        ),
        CheckConstraint(
            "context_window_tokens IS NULL OR context_window_tokens > 0", name="ck_llm_models_context_window"
        ),
        CheckConstraint("max_output_tokens IS NULL OR max_output_tokens > 0", name="ck_llm_models_max_output_tokens"),
        CheckConstraint(
            "capabilities IS NULL OR jsonb_typeof(capabilities) = 'array'", name="ck_llm_models_capability_shape"
        ),
        CheckConstraint(
            "input_capabilities IS NULL OR jsonb_typeof(input_capabilities) = 'array'",
            name="ck_llm_models_input_capability_shape",
        ),
        CheckConstraint(
            "operation_capabilities IS NULL OR jsonb_typeof(operation_capabilities) = 'array'",
            name="ck_llm_models_operation_capability_shape",
        ),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    provider_id: Mapped[int] = Column(
        Integer,
        nullable=False,
        index=True,
    )
    catalog_id: Mapped[int] = Column(Integer, nullable=False)

    # Retained compatibility owner: personal catalog owner, or NULL for platform.
    owner_user_id: Mapped[int | None] = Column(
        ForeignKey(
            f"{settings.DB_SCHEMA}.users.id",
            ondelete="RESTRICT",
            name="fk_llm_model_owner_user",
        ),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = Column(String(100), nullable=False)
    model_id: Mapped[str] = Column(String(100), nullable=False, comment="Provider API model key, kept as a string")
    description: Mapped[str | None] = Column(Text, nullable=True)

    model_type: Mapped[str] = Column(
        String(20), nullable=False, default="text", comment="Legacy CSV mirror; increment B retires it"
    )
    input_capabilities: Mapped[list | None] = Column(JSONB, nullable=True, comment="Accepted input modalities")
    operation_capabilities: Mapped[list | None] = Column(JSONB, nullable=True, comment="Supported API operations")

    custom_endpoint_path: Mapped[str | None] = Column(
        String(200), nullable=True, comment="Custom endpoint path for api_format=custom, e.g. /v1/systemone"
    )

    input_cost_per_1k: Mapped[float] = Column(Float, nullable=False, default=0.0)
    output_cost_per_1k: Mapped[float] = Column(Float, nullable=False, default=0.0)
    cache_read_cost_per_1m: Mapped[Decimal | None] = Column(Numeric(18, 8), nullable=True)
    cache_write_5m_cost_per_1m: Mapped[Decimal | None] = Column(Numeric(18, 8), nullable=True)
    cache_write_1h_cost_per_1m: Mapped[Decimal | None] = Column(Numeric(18, 8), nullable=True)
    pricing_token_unit: Mapped[int] = Column(Integer, nullable=False, default=1_000_000, server_default="1000000")
    input_price: Mapped[Decimal | None] = Column(
        Numeric(18, 8), nullable=True, comment="Exact USD per pricing_token_unit input tokens"
    )
    output_price: Mapped[Decimal | None] = Column(
        Numeric(18, 8), nullable=True, comment="Exact USD per pricing_token_unit output tokens"
    )
    pricing_state: Mapped[str | None] = Column(
        String(16), nullable=True, comment="FREE/PRICED/UNDEFINED; NULL = legacy row not yet classified"
    )

    context_window_tokens: Mapped[int | None] = Column(Integer, nullable=True)
    max_output_tokens: Mapped[int | None] = Column(Integer, nullable=True)

    last_request_cost: Mapped[float | None] = Column(
        Float, nullable=True, comment="Estimated cost of the last test request (USD)"
    )
    last_request_cost_at: Mapped[datetime | None] = Column(
        DateTime, nullable=True, comment="Timestamp of the last computed estimated cost"
    )

    # Phase 2 — usage/health counters (backfilled from ai_analytics history)
    last_used_at: Mapped[datetime | None] = Column(DateTime, nullable=True, comment="Last time this model was invoked")
    last_success_at: Mapped[datetime | None] = Column(
        DateTime, nullable=True, comment="Last successful invocation (no 5xx/timeout)"
    )
    last_error_at: Mapped[datetime | None] = Column(
        DateTime, nullable=True, comment="Last failed invocation (5xx/timeout/network)"
    )
    use_count: Mapped[int] = Column(Integer, nullable=False, default=0, server_default="0")
    fail_count: Mapped[int] = Column(Integer, nullable=False, default=0, server_default="0")

    max_tokens: Mapped[int] = Column(Integer, nullable=False, default=4096)
    default_temperature: Mapped[float] = Column(Float, nullable=False, default=0.3)

    is_active: Mapped[bool] = Column(Boolean, default=True)
    is_default: Mapped[bool] = Column(Boolean, default=False)

    provider = relationship(
        "LLMProvider",
        back_populates="models",
        foreign_keys="[LLMModel.provider_id, LLMModel.catalog_id]",
        primaryjoin="and_(LLMProvider.id == foreign(LLMModel.provider_id), "
        "LLMProvider.catalog_id == foreign(LLMModel.catalog_id))",
    )

    text_scenarios = relationship(
        "AgentScenario",
        back_populates="text_llm_model",
        foreign_keys="AgentScenario.text_llm_model_id",
    )
    image_scenarios = relationship(
        "AgentScenario",
        back_populates="image_llm_model",
        foreign_keys="AgentScenario.image_llm_model_id",
    )
    video_scenarios = relationship(
        "AgentScenario",
        back_populates="video_llm_model",
        foreign_keys="AgentScenario.video_llm_model_id",
    )

    #: The persisted JSONB column. Its attribute is ``_capabilities_column`` so
    #: it can never collide with the hybrid ``capabilities`` property below; the
    #: table column itself is still named ``capabilities``.
    _capabilities_column: Mapped[list | None] = Column(
        "capabilities", JSONB, nullable=True, comment="Validated capability set"
    )

    if TYPE_CHECKING:
        from ..managers.base_manager import BaseManager
        from ..managers.llm_model_manager import LLMModelManager

        objects: ClassVar[LLMModelManager | BaseManager]
    else:
        objects: ClassVar[Any] = None

    @hybrid_property
    def capabilities(self) -> list[str]:
        """Capability set: the stored column, else the legacy CSV until B retires it."""
        stored = self.__dict__.get("_capabilities_column")
        return list(stored) if stored is not None else _model_type_to_capabilities(self.model_type)

    @capabilities.setter
    def capabilities(self, value) -> None:
        """Normalise the capability set and mirror it into the legacy CSV.

        The two stores must never disagree, because `model_type` is still read
        by `PLAN_LIMITS["model_types"]`, the admin formatter and
        `app/services/ai/**` until increment B. So the mirror is written from
        the new set, and a stored CSV that names anything the new set does not
        is refused rather than silently widened or dropped — classification is
        explicit, never lossy.
        """
        normalized = normalize_capabilities(value)
        if normalized is None:
            self._capabilities_column = None
            return
        current = set(_model_type_to_capabilities(self.model_type))
        if current - set(normalized):
            raise ValueError("Legacy model_type would lose capabilities")
        self._capabilities_column = normalized
        self.model_type = legacy_model_type(normalized)

    @property
    def effective_input_price(self) -> Decimal | None:
        """Exact input price, or the compatibility Float until B retires it."""
        value = self.__dict__.get("input_price")
        if value is not None:
            return Decimal(value)
        return Decimal(str(self.input_cost_per_1k)) if self.input_cost_per_1k is not None else None

    @property
    def effective_output_price(self) -> Decimal | None:
        value = self.__dict__.get("output_price")
        if value is not None:
            return Decimal(value)
        return Decimal(str(self.output_cost_per_1k)) if self.output_cost_per_1k is not None else None

    @property
    def is_platform_catalog(self) -> bool:
        return self.owner_user_id is None

    def __str__(self) -> str:
        # Avoid triggering lazy load of self.provider in sync contexts
        # (e.g. sqladmin form scaffolding). Check if the relationship
        # is already loaded before accessing it.
        try:
            from sqlalchemy.inspection import inspect

            state = inspect(self)
            if state.session_id is None or "provider" not in state.attrs:
                # Detached or not loaded — don't touch the relationship
                return f"{self.name} ({self.model_id})"
            provider = state.attrs.provider.value
            if provider is None:
                return f"{self.name} ({self.model_id})"
            unit = "1K" if getattr(self, "pricing_token_unit", 1000) == 1000 else "1M"
            return f"{provider.name}/{self.model_id} (${self.input_cost_per_1k}/${self.output_cost_per_1k}/{unit})"
        except Exception:
            return f"{self.name} ({self.model_id})"

    def get_cost_per_million(self) -> tuple[float, float]:
        divisor = getattr(self, "pricing_token_unit", 1000) or 1000
        return (
            float(Decimal(str(self.input_cost_per_1k)) * Decimal(1_000_000) / Decimal(divisor)),
            float(Decimal(str(self.output_cost_per_1k)) * Decimal(1_000_000) / Decimal(divisor)),
        )

    def can_handle(self, capability: str) -> bool:
        return capability in self.capabilities
