"""Lock the current local error boundary, including inherited output models.

Source-only: Pydantic and the isolated output-contract module, with no app
bootstrap, DB fixtures, provider calls or writes. Historical SummaryPayload /
AnalysisPayload input_model types are not present in the current implementation;
these cases exercise the actual public validate_output API and a test-only child.
"""

import json
from pathlib import Path

import pytest
from pydantic import StrictStr, ValidationError
from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = load_isolated_source("_p5_t15_contracts", ROOT / "app/services/ai/output_contracts.py")
PRIVATE = "P5-T15-PRIVATE-PROVIDER-CONTENT"


class DerivedSummary(CONTRACTS.DigestSummary):
    """Test-only inheritance: future consumers keep the same boundary."""

    topic: StrictStr


CASES = [
    pytest.param(CONTRACTS.DigestSummary, {"summary": "News"}, id="digest"),
    pytest.param(CONTRACTS.LearnedFacts, {"facts": []}, id="facts"),
    pytest.param(CONTRACTS.ReflectionResult, {"ops": []}, id="reflection"),
    pytest.param(DerivedSummary, {"summary": "News", "topic": "Local"}, id="derived-summary"),
]


def encode(payload, representation):
    if representation == "dict":
        return payload
    text = json.dumps(payload)
    return text if representation == "json" else f"```json\n{text}\n```"


def assert_boundary_error(payload, model):
    with pytest.raises(CONTRACTS.OutputContractError, match="^invalid_structured_output$") as caught:
        CONTRACTS.validate_output(payload, model)
    assert type(caught.value) is CONTRACTS.OutputContractError
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__
    assert PRIVATE not in str(caught.value)
    assert PRIVATE not in repr(caught.value)


@pytest.mark.parametrize("model,valid", CASES)
@pytest.mark.parametrize("representation", ["dict", "json", "fenced-json"])
def test_failed_output_envelope_is_normalized_before_return(model, valid, representation):
    payload = {"error": "invalid_structured_output", "detail": PRIVATE}
    assert_boundary_error(encode(payload, representation), model)


@pytest.mark.parametrize("model,valid", CASES)
@pytest.mark.parametrize("error", ["invalid_structured_output", {"message": PRIVATE}, False])
def test_error_field_cannot_be_smuggled_beside_valid_content(model, valid, error):
    assert_boundary_error({**valid, "error": error}, model)


@pytest.mark.parametrize("model,valid", CASES)
def test_complete_client_envelope_is_not_a_success_payload(model, valid):
    assert_boundary_error(
        {"parsed": valid, "response": {"error": "invalid_structured_output", "detail": PRIVATE}}, model
    )


@pytest.mark.parametrize("model,valid", CASES)
def test_direct_pydantic_errors_are_normalized_at_the_public_boundary(model, valid):
    payload = {"error": "invalid_structured_output"}
    # Raw model validation is not the public boundary and need not return our
    # content-free exception; callers must use validate_output for that contract.
    with pytest.raises(ValidationError):
        model.model_validate(payload, strict=True)
    assert_boundary_error(payload, model)


@pytest.mark.parametrize("model,valid", CASES)
@pytest.mark.parametrize("representation", ["dict", "json", "fenced-json"])
def test_success_payloads_remain_strict_typed_results(model, valid, representation):
    result = CONTRACTS.validate_output(encode(valid, representation), model)
    assert isinstance(result, model)
    assert result.model_dump() == {**valid, **({"prompt_advice": ""} if model is CONTRACTS.ReflectionResult else {})}
