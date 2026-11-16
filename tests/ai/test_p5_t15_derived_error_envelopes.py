"""Lock the current local error boundary, including inherited output models.

Source-only: Pydantic and the isolated output-contract module, with no app
bootstrap, DB fixtures, provider calls or writes. Historical SummaryPayload /
AnalysisPayload input_model types are not present in the current implementation;
these cases exercise the actual public validate_output API and a test-only child.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

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


@pytest.mark.parametrize("cost", [None, 0, 0.125], ids=["unknown-cost", "free", "paid"])
@pytest.mark.parametrize(
    "outcome", ["invalid_structured_output", "private-provider-error", "invalid-payload", "success"]
)
def test_digest_keeps_error_precedence_and_attempt_accounting(cost, outcome):
    """Real digest source, with only catalog/journal/provider/storage doubles."""
    parsed = {"summary": "News"}
    response = {}
    if outcome == "invalid-payload":
        parsed = {"error": "invalid_structured_output", "detail": PRIVATE}
    elif outcome != "success":
        response = {"error": outcome, "detail": PRIVATE}
    attempt = {"parsed": parsed, "response": response, "usage": {"cost": cost}}
    client = NS(analyze=AsyncMock(return_value=attempt))
    journal = AsyncMock()
    sanitizer = load_isolated_source("_p5_t15_sanitizer", ROOT / "app/services/ai/prompt_sanitizer.py")
    validate = Mock(wraps=CONTRACTS.validate_output)
    builder = load_isolated_source(
        "_p5_t15_digest",
        ROOT / "app/services/digest/builder.py",
        {
            "app.core.config": NS(settings=NS()),
            "app.services.ai.llm_client": NS(LLMClientFactory=NS(create=lambda model: client)),
            "app.services.ai.output_contracts": CONTRACTS,
            "app.services.ai.prompt_sanitizer": sanitizer,
            "app.services.ai.reporting": NS(
                ReportAggregator=object, ReportScope=object, normalize_digest_axis=lambda value: value
            ),
            "app.services.digest.coverage": NS(job_coverage_note=AsyncMock()),
            "app.services.digest.render": NS(render_digest=lambda *args, **kwargs: ""),
            "app.services.tenancy.resolver": NS(
                current_daily_cost_limit=AsyncMock(return_value=0), daily_cost_today=AsyncMock(return_value=0)
            ),
            "app.models": NS(
                LLMModel=NS(objects=NS(resolve_default_model=AsyncMock(return_value=NS(name="synthetic-model"))))
            ),
            "app.services.ai.attempt_journal": NS(
                PURPOSE_DIGEST="digest", STAGE_DIGEST_SUMMARY="digest_summary", journal_scoped_attempts=journal
            ),
        },
    )
    builder.validate_output = validate
    summary, info = asyncio.run(builder._summarize({"brief": "Synthetic brief"}))
    assert info["cost"] == cost
    assert PRIVATE not in repr(info)
    journal.assert_awaited_once_with({"digest_summary": attempt}, purpose="digest")
    if outcome == "success":
        assert summary == "News"
        assert "error" not in info
        validate.assert_called_once_with(parsed, CONTRACTS.DigestSummary)
    else:
        assert summary is None
        if outcome == "invalid-payload":
            assert info["error"] == "invalid_structured_output"
            validate.assert_called_once_with(parsed, CONTRACTS.DigestSummary)
        else:
            assert info["error"] == "summary_provider_failed"
            validate.assert_not_called()
