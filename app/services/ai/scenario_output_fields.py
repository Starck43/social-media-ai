"""Reviewed explicit output fields for a scenario output contract.

The contract accepted by the persistence guard is the one in
``app/services/ai/scenario_schema.py``; this module only *builds* a candidate
schema for it. The derived contract (``JSONSchemaBuilder.build_json_schema``)
stays intact: extra fields are added on top of it, never in place of it, and
``required`` always stays a subset of ``properties``.

The candidate is validated with the existing validator before it can reach the
database, so an unsupported keyword is rejected while the operator still sees
the offending name instead of a generic failure.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from app.services.ai import scenario_schema
from app.services.ai.json_schema_builder import JSONSchemaBuilder
from app.services.ai.scenario_schema import ScenarioSchemaError

# Types accepted by the contract validator for one property.
FIELD_TYPES = ("array", "boolean", "integer", "null", "number", "object", "string")

# Property names become model aliases, so keep them to plain identifiers.
_FIELD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# The accepted keyword subset lives in the contract module; read it there
# instead of duplicating the rules of that validator.
_ACCEPTED_KEYWORDS = frozenset(getattr(scenario_schema, "_KEYWORDS", ()) or ())


class OutputFieldError(ScenarioSchemaError):
    """Actionable error for an unusable field spec or candidate schema."""

    def __init__(self, message: str, *, code: str = "output_field_invalid"):
        self.code = code
        ValueError.__init__(self, message)


def parse_field_spec(spec: str) -> tuple[str, dict[str, Any]]:
    """Parse the CLI field spec ``name:type`` into a property definition.

    Example: ``"is_relevant:boolean"`` -> ``("is_relevant", {"type": "boolean"})``.
    """
    if not isinstance(spec, str) or spec.count(":") != 1:
        raise OutputFieldError(f"Invalid field spec {spec!r}; expected 'name:type'.")
    name, _, kind = spec.partition(":")
    name = name.strip()
    kind = kind.strip()
    if not _FIELD_NAME.match(name):
        raise OutputFieldError(f"Invalid field name {name!r}; use letters, digits and underscores.")
    if kind not in FIELD_TYPES:
        raise OutputFieldError(
            f"Unknown field type {kind!r} for field {name!r}; " f"supported types: {', '.join(FIELD_TYPES)}."
        )
    return name, {"type": kind}


def base_output_schema(
    stored_schema: dict[str, Any] | None,
    analysis_types: list[str] | None,
    scope: dict[str, Any] | None,
) -> tuple[dict[str, Any], str]:
    """The schema an extension starts from: the stored one, else the derived one.

    Re-deriving would drop fields added earlier, so a stored schema wins.
    """
    if type(stored_schema) is dict and stored_schema:
        return copy.deepcopy(stored_schema), "stored"
    derived = JSONSchemaBuilder.build_json_schema(list(analysis_types or []), scope or {})
    return derived, "derived"


def merge_extra_fields(
    schema: dict[str, Any],
    extra_fields: dict[str, dict[str, Any]],
    required_extra: list[str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Merge extra properties into an existing schema.

    Returns ``(schema, warnings)``. A name that already exists is kept as it is
    (the existing definition wins) and reported as a warning; ``required`` is
    extended only with names that really are properties, so ``required`` stays
    a subset of ``properties``.
    """
    if type(schema) is not dict:
        raise OutputFieldError("A scenario output schema must be a JSON object.")

    merged = copy.deepcopy(schema)
    properties: dict[str, Any] = dict(merged.get("properties") or {})
    required: list[str] = list(merged.get("required") or [])
    warnings: list[str] = []

    for name, spec in (extra_fields or {}).items():
        if name in properties:
            warnings.append(f"Field {name!r} already exists in the schema; the existing definition was kept.")
            continue
        if type(spec) is not dict:
            raise OutputFieldError(f"Field {name!r} must be described by a JSON object.")
        properties[name] = copy.deepcopy(spec)

    for name in list(required_extra or []):
        if name not in properties:
            warnings.append(f"Required field {name!r} is not a property of the schema; it was skipped.")
            continue
        if name not in required:
            required.append(name)

    # `required` must stay a subset of `properties`.
    kept = [name for name in required if name in properties]
    for name in required:
        if name not in properties:
            warnings.append(f"Required field {name!r} is not a property of the schema; it was dropped.")
    merged["properties"] = properties
    merged["required"] = kept
    # `additionalProperties` is never forced here: model fields outside the
    # contract stay allowed.
    return validate_candidate_schema(merged), warnings


def build_extended_output_schema(
    analysis_types: list[str],
    scope: dict[str, Any] | None,
    extra_fields: dict[str, dict[str, Any]],
    required_extra: list[str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Derive the scenario contract and extend it with the extra fields.

    Returns ``(schema, warnings)``; the derived properties and their
    requirements stay untouched.
    """
    derived = JSONSchemaBuilder.build_json_schema(list(analysis_types or []), scope or {})
    return merge_extra_fields(derived, extra_fields, required_extra)


def validate_candidate_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Validate a candidate schema with the existing contract validator.

    The validator decides; this helper only makes the failure actionable by
    naming the offending keyword.
    """
    try:
        return scenario_schema.supported_schema_copy(schema)
    except ScenarioSchemaError as error:
        offending = _find_unsupported_keyword(schema)
        if offending:
            raise OutputFieldError(
                f"Unsupported schema keyword {offending!r} in the output schema.",
                code="unsupported_keyword",
            ) from error
        raise OutputFieldError(
            f"The output schema is not accepted by the scenario contract: {error.code}.",
            code=error.code,
        ) from error


def _find_unsupported_keyword(node: Any, path: tuple[str, ...] = ()) -> str | None:
    """Locate the first keyword the contract validator does not accept."""
    if not _ACCEPTED_KEYWORDS or type(node) is not dict:
        return None
    for key, value in node.items():
        if key not in _ACCEPTED_KEYWORDS:
            return ".".join(path + (str(key),))
        if key == "properties":
            # The children of `properties` are property names, not keywords.
            for name, child in (value or {}).items():
                found = _find_unsupported_keyword(child, path + ("properties", str(name)))
                if found:
                    return found
        elif type(value) is dict:
            found = _find_unsupported_keyword(value, path + (str(key),))
            if found:
                return found
        elif type(value) is list:
            for index, item in enumerate(value):
                if type(item) is dict:
                    found = _find_unsupported_keyword(item, path + (f"{key}[{index}]",))
                    if found:
                        return found
    return None
