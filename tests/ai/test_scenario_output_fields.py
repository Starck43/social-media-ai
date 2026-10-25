"""Explicit scenario output fields: building, merging and validating a contract.

The unit cases below touch no database. The guarded persistence behaviour lives
in the tenancy-marked cases at the end of this module.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import delete, update

from app.services.ai.json_schema_builder import JSONSchemaBuilder
from app.services.ai.scenario_output_fields import (
    OutputFieldError,
    base_output_schema,
    build_extended_output_schema,
    merge_extra_fields,
    parse_field_spec,
    validate_candidate_schema,
)
from app.services.ai.scenario_schema import (
    ScenarioSchemaError,
    supported_schema_copy,
    validate_output_instance,
)


# ---------------------------------------------------------------------------
# Building the candidate schema
# ---------------------------------------------------------------------------
def test_derived_properties_are_kept_and_extra_fields_are_added():
    schema, warnings = build_extended_output_schema(
        ["sentiment"],
        {},
        {"is_relevant": {"type": "boolean"}, "participants": {"type": "array"}},
    )

    derived = JSONSchemaBuilder.build_json_schema(["sentiment"], {})
    assert set(derived["properties"]) <= set(schema["properties"])
    assert schema["properties"]["is_relevant"] == {"type": "boolean"}
    assert schema["properties"]["participants"] == {"type": "array"}
    assert warnings == []


def test_required_is_extended_and_stays_a_subset_of_properties():
    schema, _warnings = build_extended_output_schema(
        ["sentiment"], {}, {"is_relevant": {"type": "boolean"}}, ["is_relevant"]
    )

    assert "is_relevant" in schema["required"]
    assert set(schema["required"]) <= set(schema["properties"])
    # The derived requirements are untouched.
    assert "summary" in schema["required"]
    assert "sentiment_score" in schema["required"]


def test_a_colliding_name_keeps_the_derived_property_and_is_reported():
    schema, warnings = build_extended_output_schema(["sentiment"], {}, {"summary": {"type": "integer"}})

    assert schema["properties"]["summary"]["type"] == "string"
    assert any("summary" in warning for warning in warnings)


def test_a_required_name_without_a_property_is_reported_and_skipped():
    schema, warnings = merge_extra_fields(
        {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]},
        {},
        ["ghost"],
    )

    assert "ghost" not in schema["required"]
    assert set(schema["required"]) <= set(schema["properties"])
    assert any("ghost" in warning for warning in warnings)


def test_extra_model_fields_stay_allowed():
    schema, _warnings = build_extended_output_schema(["sentiment"], {}, {"is_relevant": {"type": "boolean"}})

    assert schema.get("additionalProperties") is not False


def test_a_stored_schema_is_the_base_of_a_new_extension():
    stored = {
        "type": "object",
        "properties": {"summary": {"type": "string"}, "is_relevant": {"type": "boolean"}},
        "required": ["summary"],
    }

    base, origin = base_output_schema(stored, ["sentiment"], {})
    assert origin == "stored"
    assert base == stored

    derived, origin = base_output_schema(None, ["sentiment"], {})
    assert origin == "derived"
    assert "sentiment_score" in derived["properties"]


# ---------------------------------------------------------------------------
# Field specs
# ---------------------------------------------------------------------------
def test_field_specs_are_parsed_into_property_definitions():
    assert parse_field_spec("is_relevant:boolean") == ("is_relevant", {"type": "boolean"})
    assert parse_field_spec("participants:array") == ("participants", {"type": "array"})
    assert parse_field_spec("confidence:number") == ("confidence", {"type": "number"})
    assert parse_field_spec("author_role:string") == ("author_role", {"type": "string"})


def test_an_unknown_field_type_is_rejected_with_a_clear_message():
    with pytest.raises(OutputFieldError) as caught:
        parse_field_spec("score:float")

    message = str(caught.value)
    assert "float" in message
    assert "string" in message


def test_a_malformed_field_spec_is_rejected():
    with pytest.raises(OutputFieldError):
        parse_field_spec("is_relevant")
    with pytest.raises(OutputFieldError):
        parse_field_spec("is relevant:boolean")


# ---------------------------------------------------------------------------
# The candidate schema goes through the existing contract validator
# ---------------------------------------------------------------------------
def test_a_candidate_schema_with_an_unsupported_keyword_is_rejected():
    candidate = {
        "type": "object",
        "properties": {"score": {"type": "number", "unevaluatedProperties": False}},
        "required": ["score"],
    }

    with pytest.raises(ScenarioSchemaError):
        supported_schema_copy(candidate)

    with pytest.raises(OutputFieldError) as caught:
        validate_candidate_schema(candidate)
    assert "unevaluatedProperties" in str(caught.value)


def test_the_merged_schema_is_accepted_by_the_contract_validator():
    schema, _warnings = build_extended_output_schema(
        ["sentiment"], {}, {"is_relevant": {"type": "boolean"}}, ["is_relevant"]
    )

    assert supported_schema_copy(schema) == schema


# ---------------------------------------------------------------------------
# The extra fields are really validated
# ---------------------------------------------------------------------------
def test_validate_output_instance_accepts_an_instance_with_the_extra_field():
    schema, _warnings = build_extended_output_schema([], {}, {"is_relevant": {"type": "boolean"}}, ["is_relevant"])

    instance = {"summary": "ок", "is_relevant": True}
    assert validate_output_instance(instance, schema) == instance


def test_validate_output_instance_rejects_a_missing_required_extra_field():
    schema, _warnings = build_extended_output_schema([], {}, {"is_relevant": {"type": "boolean"}}, ["is_relevant"])

    with pytest.raises(ScenarioSchemaError) as caught:
        validate_output_instance({"summary": "ок"}, schema)
    assert caught.value.code == "output_required_field_missing"


def test_validate_output_instance_rejects_a_wrong_type_of_the_extra_field():
    schema, _warnings = build_extended_output_schema([], {}, {"is_relevant": {"type": "boolean"}}, ["is_relevant"])

    with pytest.raises(ScenarioSchemaError) as caught:
        validate_output_instance({"summary": "ок", "is_relevant": "yes"}, schema)
    assert caught.value.code == "output_type_mismatch"


# ---------------------------------------------------------------------------
# Guarded persistence (own database, run by the DB test lane)
# ---------------------------------------------------------------------------
@pytest.fixture
async def scenario_row():
    """A fresh scenario row in a fresh workspace."""
    from app.core.database import async_session_maker
    from app.models import AgentScenario, Tenant

    tenant = await Tenant.objects.create(
        slug=f"schema-fields-{uuid4().hex}", name="Scenario schema fields", plan="business"
    )
    async with async_session_maker() as db:
        row = AgentScenario(
            name=f"sc_{uuid4().hex[:6]}",
            tenant_id=tenant.id,
            analysis_types=["sentiment"],
            content_types=["posts"],
            scope={},
            is_active=True,
        )
        db.add(row)
        await db.commit()
        scenario_id = row.id

    try:
        yield tenant.id, scenario_id
    finally:
        async with async_session_maker() as db:
            await db.execute(delete(AgentScenario).where(AgentScenario.id == scenario_id))
            await db.commit()
        await Tenant.objects.delete_by_id(tenant.id)


@pytest.mark.tenancy
async def test_the_guarded_per_row_write_accepts_a_custom_output_schema(scenario_row):
    """The API path: one row, one flush, the guard validates the contract."""
    from app.core.database import async_session_maker
    from app.models import AgentScenario

    _tenant_id, scenario_id = scenario_row
    schema, _warnings = build_extended_output_schema(
        ["sentiment"], {}, {"is_relevant": {"type": "boolean"}}, ["is_relevant"]
    )

    async with async_session_maker() as db:
        row = await db.get(AgentScenario, scenario_id)
        row.output_schema = schema
        await db.commit()

    async with async_session_maker() as db:
        saved = await db.get(AgentScenario, scenario_id)
        assert saved.output_schema["properties"]["is_relevant"] == {"type": "boolean"}
        assert "is_relevant" in saved.output_schema["required"]
        # The derived contract survived the write.
        assert saved.output_schema["properties"]["sentiment_score"]["type"] == "number"


@pytest.mark.tenancy
async def test_a_bulk_write_of_the_same_column_is_rejected_by_the_guard(scenario_row):
    """A bulk (executemany) write cannot be checked row by row, so it fails."""
    from app.core.database import async_session_maker
    from app.models import AgentScenario

    _tenant_id, scenario_id = scenario_row
    schema, _warnings = build_extended_output_schema(["sentiment"], {}, {"is_relevant": {"type": "boolean"}})

    async with async_session_maker() as db:
        try:
            with pytest.raises(ScenarioSchemaError) as caught:
                await db.execute(
                    update(AgentScenario).where(AgentScenario.id == scenario_id).values(output_schema=schema),
                    [{"output_schema": schema}, {"output_schema": schema}],
                )
            assert caught.value.code == "per_row_schema_update_required"
        finally:
            await db.rollback()
