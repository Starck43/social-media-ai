"""Strict local contracts at digest and learned-memory write boundaries.

These checks are independent of provider-native structured-output support.
Validation is not authorization, factual verification or an injection guarantee.
"""

from __future__ import annotations

import json
import math
from typing import Annotated, Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError, field_validator

MAX_OUTPUT_CHARS = 16000
MAX_LEARNED_FACTS = 8
MAX_REFLECTION_OPS = 16


class OutputContractError(ValueError):
    """A content-free error safe to return or log without the invalid payload."""


class StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class DigestSummary(StrictOutput):
    summary: Annotated[StrictStr, Field(min_length=1, max_length=2000)]

    @field_validator("summary")
    @classmethod
    def nonempty_summary(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("empty summary")
        return value


class ExtractedFact(StrictOutput):
    key: Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9_]{0,99}$")]
    value: Annotated[StrictStr, Field(min_length=1, max_length=500)]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    evidence_id: Annotated[StrictInt, Field(gt=0)]

    @field_validator("value")
    @classmethod
    def nonempty_value(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("empty value")
        return value


class LearnedFacts(StrictOutput):
    facts: Annotated[list[ExtractedFact], Field(max_length=MAX_LEARNED_FACTS)]

    @field_validator("facts")
    @classmethod
    def unique_keys(cls, facts: list[ExtractedFact]) -> list[ExtractedFact]:
        if len({fact.key for fact in facts}) != len(facts):
            raise ValueError("duplicate keys")
        return facts


class DeleteMemory(StrictOutput):
    op: Literal["delete"]
    id: Annotated[StrictInt, Field(gt=0)]
    reason: Annotated[StrictStr, Field(max_length=500)] = ""


class UpdateMemory(StrictOutput):
    op: Literal["update"]
    id: Annotated[StrictInt, Field(gt=0)]
    value: Annotated[StrictStr, Field(min_length=1, max_length=500)]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    reason: Annotated[StrictStr, Field(max_length=500)] = ""

    @field_validator("value")
    @classmethod
    def nonempty_value(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("empty value")
        return value


MemoryOperation = Annotated[DeleteMemory | UpdateMemory, Field(discriminator="op")]


class ReflectionResult(StrictOutput):
    ops: Annotated[list[MemoryOperation], Field(max_length=MAX_REFLECTION_OPS)]
    prompt_advice: Annotated[StrictStr, Field(max_length=500)] = ""

    @field_validator("ops")
    @classmethod
    def unique_ids(cls, ops: list[MemoryOperation]) -> list[MemoryOperation]:
        if len({op.id for op in ops}) != len(ops):
            raise ValueError("duplicate operation ids")
        return ops


T = TypeVar("T", bound=BaseModel)


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OutputContractError("invalid_structured_output")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise OutputContractError("invalid_structured_output")


def validate_output(payload: Any, model: type[T]) -> T:
    """Validate a dict or a complete JSON response, never extract arbitrary prose.

    One complete JSON fence is tolerated for providers that wrap their answer.
    Unknown fields, duplicate JSON keys, coercion, nonfinite values and excess
    operations fail before callers perform the first write.
    """
    try:
        if isinstance(payload, str):
            if len(payload) > MAX_OUTPUT_CHARS:
                raise OutputContractError("invalid_structured_output")
            text = payload.strip()
            lines = text.splitlines()
            if lines and lines[0].lower() in ("```", "```json"):
                if len(lines) < 3 or lines[-1] != "```":
                    raise OutputContractError("invalid_structured_output")
                text = "\n".join(lines[1:-1])
            payload = json.loads(text, object_pairs_hook=_object_pairs, parse_constant=_reject_constant)
        if not isinstance(payload, dict):
            raise OutputContractError("invalid_structured_output")
        return model.model_validate(payload, strict=True)
    except (ValidationError, json.JSONDecodeError, TypeError, ValueError, RecursionError):
        # Validation errors can contain customer values: do not expose them.
        raise OutputContractError("invalid_structured_output") from None


def learned_facts(payload: Any, allowed_evidence_ids: set[int]) -> LearnedFacts:
    result = validate_output(payload, LearnedFacts)
    if any(fact.evidence_id not in allowed_evidence_ids for fact in result.facts):
        raise OutputContractError("invalid_evidence_reference")
    return result


def reflection_result(payload: Any, allowed_fact_ids: set[int]) -> ReflectionResult:
    result = validate_output(payload, ReflectionResult)
    if any(op.id not in allowed_fact_ids for op in result.ops):
        raise OutputContractError("invalid_memory_reference")
    return result


def known_cost_usd(response: Any) -> float | None:
    """Retain reported finite nonnegative cost; missing usage is not free usage."""
    if not isinstance(response, dict) or not isinstance(response.get("usage"), dict):
        return None
    value = response["usage"].get("cost")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) and value >= 0 else None


def response_is_incomplete(response: Any) -> bool:
    """Reject known truncation/filter/tool-call endings before accepting data."""
    if not isinstance(response, dict):
        return True
    reasons = [response.get("finish_reason")]
    raw = response.get("response") or response.get("raw")
    if isinstance(raw, dict):
        reasons.append(raw.get("stop_reason"))
        choices = raw.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            reasons.append(choices[0].get("finish_reason"))
    return bool(response.get("tool_calls")) or any(
        reason in ("length", "max_tokens", "content_filter", "tool_calls", "tool_use") for reason in reasons
    )
