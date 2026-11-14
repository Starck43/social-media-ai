"""P21-T13 increment A: the catalog storage mapping, without a database.

The regression this file pins is `LLMModel.capabilities`: it is a hybrid
property over a JSONB column, so the mapped table must really carry the column
(an earlier revision defined a `Column` and replaced it with a same-named
`property`, leaving `capabilities` absent from `LLMModel.__table__.c` while the
migration created it). Nothing here connects to PostgreSQL — the mapping,
defaults and hybrid behaviour are pure source artefacts.
"""

from __future__ import annotations

import pytest

from app.models import LLMCatalog, LLMModel, LLMProvider, PlanModelGrant
from app.models.llm.llm_model import (
    LEGACY_CAPABILITIES,
    PRICING_STATES,
    legacy_model_type,
    normalize_capabilities,
)

__source_only__ = True


def _columns(model) -> set[str]:
    return set(model.__table__.columns.keys())


def test_model_table_carries_every_increment_a_column():
    """The JSONB/hybrid column must exist as a column, not only as an attribute."""
    columns = _columns(LLMModel)
    for name in (
        "catalog_id",
        "capabilities",
        "input_capabilities",
        "operation_capabilities",
        "context_window_tokens",
        "max_output_tokens",
        "input_price",
        "output_price",
        "pricing_state",
    ):
        assert name in columns, f"LLMModel is missing the {name!r} column"
    assert LLMModel.__table__.c.capabilities.nullable is True
    assert str(LLMModel.__table__.c.capabilities.type).upper().startswith("JSONB")


def test_provider_and_catalog_tables_carry_custody_columns():
    provider_columns = _columns(LLMProvider)
    assert {"catalog_id", "managed_credential_ref", "owner_user_id"} <= provider_columns
    assert LLMProvider.__table__.c.catalog_id.nullable is False
    assert LLMProvider.__table__.c.owner_user_id.nullable is True

    catalog_columns = _columns(LLMCatalog)
    assert {"id", "kind", "owner_user_id", "is_active"} <= catalog_columns
    assert LLMCatalog.__table__.c.kind.nullable is False
    assert LLMCatalog.__table__.c.owner_user_id.nullable is True


def test_composite_custody_fk_replaces_the_owner_composite():
    """Models are joined to their provider by custody, not by the retained owner."""
    constraints = {fk.name: fk for fk in LLMModel.__table__.foreign_key_constraints}
    assert "fk_llm_model_provider_owner" not in constraints
    custody = constraints["fk_llm_model_provider_catalog"]
    assert list(custody.column_keys) == ["provider_id", "catalog_id"]
    assert custody.ondelete == "CASCADE"
    # The provider side must expose the unique the composite FK points at.
    provider_uniques = {
        tuple(sorted(c.columns.keys()))
        for c in LLMProvider.__table__.constraints
        if c.__class__.__name__ == "UniqueConstraint"
    }
    assert ("catalog_id", "id") in provider_uniques


def test_provider_relationship_uses_both_custody_columns():
    relationship = LLMModel.__mapper__.relationships["provider"]
    assert len(relationship.local_columns) == 2


def test_plan_grant_stores_a_numeric_model_id_and_the_plan_set():
    columns = _columns(PlanModelGrant)
    assert {"plan", "capability", "llm_model_id", "is_active", "priority", "is_default"} <= columns
    assert PlanModelGrant.__table__.c.llm_model_id.nullable is False
    grant_fks = list(PlanModelGrant.__table__.c.llm_model_id.foreign_keys)
    assert len(grant_fks) == 1
    assert grant_fks[0].target_fullname.endswith(".llm_models.id")


def test_capability_vocabulary_matches_the_plan_limits_set():
    """`decision` is a retired permission registry key, never a routing capability."""
    assert LEGACY_CAPABILITIES == {"text", "image", "video", "embedding", "audio"}
    assert "decision" not in LEGACY_CAPABILITIES


def test_legacy_csv_read_falls_back_when_nothing_is_stored():
    model = LLMModel(name="legacy", model_id="legacy", model_type="text,image")
    assert model.capabilities == ["text", "image"]
    assert model.can_handle("image") is True
    assert model.can_handle("audio") is False


def test_setting_capabilities_mirrors_the_legacy_csv_mirror():
    model = LLMModel(name="fresh", model_id="fresh")
    model.capabilities = ["image", "text"]
    assert model.capabilities == ["image", "text"]
    assert model.model_type == "text,image"
    assert legacy_model_type(model.capabilities) == model.model_type
    # A duplicate would make the CSV and the JSONB set disagree, so it is refused.
    with pytest.raises(ValueError):
        model.capabilities = ["text", "text"]


def test_setting_capabilities_refuses_to_lose_a_legacy_capability():
    model = LLMModel(name="legacy", model_id="legacy", model_type="text,image")
    with pytest.raises(ValueError):
        model.capabilities = ["text"]
    model.capabilities = ["text", "image", "video"]
    assert model.capabilities == ["image", "text", "video"]
    assert model.model_type == "text,image,video"


def test_unknown_or_duplicate_capabilities_are_refused():
    with pytest.raises(ValueError):
        normalize_capabilities(["decision"])
    with pytest.raises(ValueError):
        normalize_capabilities(["text", "text"])
    with pytest.raises(ValueError):
        normalize_capabilities("text")
    assert normalize_capabilities(None) is None


def test_pricing_state_keeps_the_storage_vocabulary():
    assert PRICING_STATES == ("free", "priced", "undefined")
    assert LLMModel.__table__.c.pricing_state.nullable is True
    assert LLMModel.__table__.c.input_price.nullable is True
    assert LLMModel.__table__.c.output_price.nullable is True
