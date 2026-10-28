"""Strict, provider-neutral tool contracts. No application settings or DB imports."""

from __future__ import annotations

import inspect
import json
import math
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, get_type_hints

from pydantic import ConfigDict, TypeAdapter, ValidationError, create_model


class ToolInputError(ValueError):
    """Arguments violate the registered contract (never includes payloads)."""


class ToolResultError(ValueError):
    """A handler returned a value outside its registered result type/budget."""


@dataclass(frozen=True)
class ToolBudget:
    max_argument_bytes: int = 65_536
    max_result_bytes: int = 1_048_576
    timeout_seconds: float = 120.0

    def __post_init__(self):
        if (
            type(self.max_argument_bytes) is not int
            or self.max_argument_bytes <= 0
            or type(self.max_result_bytes) is not int
            or self.max_result_bytes <= 0
            or type(self.timeout_seconds) not in (int, float)
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("Invalid tool budget")


def json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))


_TYPES = {
    "string": lambda v: type(v) is str,
    "integer": lambda v: type(v) is int,
    "number": lambda v: type(v) in (int, float) and math.isfinite(v),
    "boolean": lambda v: type(v) is bool,
    "null": lambda v: v is None,
    "object": lambda v: type(v) is dict and all(type(k) is str for k in v),
    "array": lambda v: type(v) is list,
}
_SCHEMA_KEYS = {
    "title",
    "pattern",
    "additionalProperties",
    "type",
    "description",
    "properties",
    "required",
    "items",
    "enum",
    "default",
    "minimum",
    "maximum",
}


def validate_schema(schema: Any) -> None:
    """Fail closed on unsupported schema keywords, rather than silently ignoring them.

    This deliberately supports the built-in tool vocabulary, not arbitrary JSON Schema.
    Unstructured nested objects remain JSON objects; use-case contracts validate their fields.
    """
    if not isinstance(schema, dict) or set(schema) - _SCHEMA_KEYS:
        raise ValueError("Unsupported tool schema")
    types = schema.get("type")
    types = types if isinstance(types, list) else [types]
    if not types or any(not isinstance(t, str) or t not in _TYPES for t in types) or len(set(types)) != len(types):
        raise ValueError("Invalid tool schema type")
    if "title" in schema and not isinstance(schema["title"], str):
        raise ValueError("Invalid schema title")
    if "description" in schema and not isinstance(schema["description"], str):
        raise ValueError("Invalid schema description")
    if "pattern" in schema:
        if types != ["string"] or not isinstance(schema["pattern"], str):
            raise ValueError("Invalid schema pattern")
        try:
            re.compile(schema["pattern"])
        except re.error:
            raise ValueError("Invalid schema pattern") from None
    if "additionalProperties" in schema and (types != ["object"] or type(schema["additionalProperties"]) is not bool):
        raise ValueError("Invalid additionalProperties")
    if "properties" in schema or "required" in schema:
        if types != ["object"]:
            raise ValueError("Object schema required")
        props = schema.get("properties", {})
        required = schema.get("required", [])
        if (
            not isinstance(props, dict)
            or any(not isinstance(k, str) or not k for k in props)
            or not isinstance(required, list)
            or any(not isinstance(k, str) or k not in props for k in required)
            or len(set(required)) != len(required)
        ):
            raise ValueError("Invalid tool schema properties")
        for child in props.values():
            validate_schema(child)
    if "array" in types:
        if "items" not in schema:
            raise ValueError("Array items schema required")
        validate_schema(schema["items"])
    elif "items" in schema:
        raise ValueError("Unexpected items schema")
    for key in ("minimum", "maximum"):
        if key in schema and (
            not set(types) <= {"number", "integer", "null"}
            or type(schema[key]) not in (int, float)
            or not math.isfinite(schema[key])
        ):
            raise ValueError("Invalid numeric constraint")
    if "minimum" in schema and "maximum" in schema and schema["minimum"] > schema["maximum"]:
        raise ValueError("Invalid numeric range")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise ValueError("Invalid tool enum")
    try:
        json_size(schema)
        for value in schema.get("enum", []):
            validate_value(value, {k: v for k, v in schema.items() if k != "enum"})
        if "default" in schema:
            validate_value(schema["default"], schema)
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ValueError("Invalid tool schema value") from None


def validate_value(value: Any, schema: dict[str, Any]) -> None:
    types = schema["type"]
    types = types if isinstance(types, list) else [types]
    if not any(_TYPES[t](value) for t in types):
        raise ToolInputError("Invalid tool arguments")
    if "enum" in schema and not any(type(value) is type(v) and value == v for v in schema["enum"]):
        raise ToolInputError("Invalid tool arguments")
    if "pattern" in schema and re.search(schema["pattern"], value) is None:
        raise ToolInputError("Invalid tool arguments")
    if type(value) in (int, float):
        if ("minimum" in schema and value < schema["minimum"]) or ("maximum" in schema and value > schema["maximum"]):
            raise ToolInputError("Invalid tool arguments")
    if isinstance(value, dict) and ("properties" in schema or schema.get("additionalProperties") is False):
        properties = schema.get("properties", {})
        if (set(value) - properties.keys() and schema.get("additionalProperties") is not True) or set(
            schema.get("required", [])
        ) - value.keys():
            raise ToolInputError("Invalid tool arguments")
        for key, child in value.items():
            if key in properties:
                validate_value(child, properties[key])
    if isinstance(value, list):
        for child in value:
            validate_value(child, schema["items"])


def _compatible_schema(declared: dict, typed: dict) -> bool:
    """The advertised non-null JSON types must fit the handler annotation."""
    alternatives = typed.get("anyOf", [typed])
    kinds = declared["type"]
    kinds = kinds if isinstance(kinds, list) else [kinds]
    for kind in kinds:
        matches = [
            t for t in alternatives if t.get("type") == kind or (kind == "integer" and t.get("type") == "number")
        ]
        if not matches:
            return False
        if kind == "array" and not any(_compatible_schema(declared["items"], t.get("items", {})) for t in matches):
            return False
    return True


class ToolContract:
    """Derive strict input/result adapters from the actual async handler's annotations."""

    def __init__(self, handler, parameters):
        validate_schema(parameters)
        if parameters.get("type") != "object" or "properties" not in parameters:
            raise ValueError("Tool parameters must declare object properties")
        if not inspect.iscoroutinefunction(handler):
            raise ValueError("Tool handler must be async")
        self._parameters = deepcopy(parameters)
        signature = inspect.signature(handler)
        hints = get_type_hints(handler)
        named = {k: p for k, p in signature.parameters.items() if p.kind != p.VAR_KEYWORD}
        if set(named) != set(parameters["properties"]):
            raise ValueError("Tool schema and handler parameters differ")
        fields = {}
        for name, param in named.items():
            if param.kind not in (param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY) or name not in hints:
                raise ValueError("Tool parameters must be typed keyword arguments")
            if param.default is inspect.Parameter.empty and name not in parameters.get("required", []):
                raise ValueError("Required handler parameter missing from schema")
            if not _compatible_schema(parameters["properties"][name], TypeAdapter(hints[name]).json_schema()):
                raise ValueError("Tool schema and handler annotation differ")
            fields[name] = (hints[name], ... if param.default is inspect.Parameter.empty else param.default)
        if "return" not in hints or hints["return"] is Any:
            raise ValueError("Tool result annotation required")
        self.input_model = create_model(
            f"{handler.__name__}Input", __config__=ConfigDict(extra="forbid", strict=True), **fields
        )
        self.result_adapter = TypeAdapter(hints["return"])

    @property
    def parameters(self):
        return deepcopy(self._parameters)

    def arguments(self, value: Any, budget: ToolBudget) -> dict[str, Any]:
        try:
            if json_size(value) > budget.max_argument_bytes:
                raise ToolInputError("Tool argument budget exceeded")
            validate_value(value, self._parameters)
            self.input_model.model_validate(value, strict=True)
            # No coercion, default insertion or normalization before confirmation hashing.
            return deepcopy(value)
        except (ValidationError, TypeError, ValueError, OverflowError, RecursionError):
            raise ToolInputError("Invalid tool arguments") from None

    def result(self, value: Any, budget: ToolBudget) -> Any:
        try:
            self.result_adapter.validate_python(value, strict=True)
            if json_size(value) > budget.max_result_bytes:
                raise ToolResultError("Tool result budget exceeded")
            return value
        except (ValidationError, TypeError, ValueError, OverflowError, RecursionError):
            raise ToolResultError("Invalid tool result") from None
