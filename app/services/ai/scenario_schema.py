"""Bounded scenario output contracts shared by persistence and analysis.

This is an explicit JSON Schema subset, not a general-purpose schema engine.
Unsupported assertions fail before a provider request or scenario persistence.
Compiled contracts belong to one invocation; no global cache or ORM mutation.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel

SCHEMA_FIELDS = frozenset({'output_schema', 'analysis_types', 'scope'})
_TYPES = frozenset({'string', 'integer', 'number', 'boolean', 'null', 'object', 'array'})
_ANNOTATIONS = frozenset({'title', 'description', 'default', 'examples', '$schema'})
_NUMERIC = frozenset({'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum'})
_STRING = frozenset({'minLength', 'maxLength', 'pattern'})
_ARRAY = frozenset({'minItems', 'maxItems', 'items'})
_OBJECT = frozenset({'properties', 'required', 'additionalProperties'})
_KEYWORDS = _ANNOTATIONS | _NUMERIC | _STRING | _ARRAY | _OBJECT | {'type', 'enum'}
MAX_SCHEMA_BYTES = 65536
MAX_SCHEMA_DEPTH = 16
MAX_SCHEMA_NODES = 2048


class ScenarioSchemaError(ValueError):
    """Static, non-secret errors suitable for API/form boundaries and logs."""

    def __init__(self, code: str):
        self.code = code
        super().__init__('Invalid or unsupported scenario output schema: ' + code)


@dataclass(frozen=True)
class ScenarioOutputContract:
    schema_json: str
    model: type[BaseModel]

    @property
    def schema(self) -> dict[str, Any]:
        """A detached copy; changing it cannot change the compiled contract."""
        return json.loads(self.schema_json)


def _plain_json(value: Any, *, depth: int = 0) -> None:
    if depth > 64:
        raise ScenarioSchemaError('json_depth_exceeded')
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _plain_json(item, depth=depth + 1)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _plain_json(item, depth=depth + 1)
        return
    raise ScenarioSchemaError('non_json_value')


def _types(node: dict) -> set[str]:
    declared = node.get('type')
    if declared is None:
        return set()
    values = declared if type(declared) is list else [declared]
    if not values or any(type(value) is not str or value not in _TYPES for value in values):
        raise ScenarioSchemaError('unsupported_type')
    if len(values) != len(set(values)):
        raise ScenarioSchemaError('duplicate_type')
    return set(values)


def _matches_type(value: Any, kind: str) -> bool:
    return {
        'null': value is None,
        'string': type(value) is str,
        'boolean': type(value) is bool,
        'integer': type(value) is int,
        'number': type(value) in (int, float) and (type(value) is not float or math.isfinite(value)),
        'object': type(value) is dict,
        'array': type(value) is list,
    }[kind]


def _enum_equal(left: Any, right: Any) -> bool:
    # Python True == 1 is not JSON enum equality.
    if type(left) is bool or type(right) is bool:
        return type(left) is type(right) and left == right
    return left == right


def supported_schema_copy(raw: Any) -> dict[str, Any]:
    """Reject unsupported semantics, malformed bounds and unbounded schemas."""
    if type(raw) is not dict:
        raise ScenarioSchemaError('root_object_schema_required')
    _plain_json(raw)
    try:
        encoded = json.dumps(raw, ensure_ascii=False, allow_nan=False)
    except (ValueError, OverflowError, RecursionError) as error:
        raise ScenarioSchemaError('non_json_value') from error
    try:
        encoded_bytes = encoded.encode("utf-8")
    except UnicodeError as error:
        raise ScenarioSchemaError("non_json_value") from error
    if len(encoded_bytes) > MAX_SCHEMA_BYTES:
        raise ScenarioSchemaError('schema_size_exceeded')
    snapshot = json.loads(encoded)  # Never retain caller-owned nested dictionaries.
    remaining = MAX_SCHEMA_NODES

    def check(node: Any, depth: int, *, root: bool = False) -> None:
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > MAX_SCHEMA_DEPTH:
            raise ScenarioSchemaError('schema_complexity_exceeded')
        if type(node) is not dict or set(node) - _KEYWORDS:
            raise ScenarioSchemaError('unsupported_keyword_or_schema_node')
        kinds = _types(node)
        if root and kinds != {'object'}:
            raise ScenarioSchemaError('root_object_type_required')
        for name in ('title', 'description', '$schema'):
            if name in node and type(node[name]) is not str:
                raise ScenarioSchemaError('invalid_annotation')
        if '$schema' in node and node['$schema'] not in {
            'http://json-schema.org/draft-07/schema#', 'https://json-schema.org/draft-07/schema',
            'https://json-schema.org/draft/2020-12/schema',
        }:
            raise ScenarioSchemaError('unsupported_schema_dialect')
        if 'examples' in node and type(node['examples']) is not list:
            raise ScenarioSchemaError('invalid_annotation')
        enum = node.get('enum')
        if 'enum' in node:
            if type(enum) is not list or not enum or len(enum) > 256:
                raise ScenarioSchemaError('invalid_enum')
            if any(type(value) not in (str, int, float, bool, type(None)) for value in enum):
                raise ScenarioSchemaError('scalar_enum_required')
            if any(any(_enum_equal(value, prior) for prior in enum[:index]) for index, value in enumerate(enum)):
                raise ScenarioSchemaError('duplicate_enum')
            if kinds and any(not any(_matches_type(value, kind) for kind in kinds) for value in enum):
                raise ScenarioSchemaError('enum_type_mismatch')
        if set(node) & _NUMERIC:
            if not kinds & {'integer', 'number'}:
                raise ScenarioSchemaError('numeric_type_required')
            for name in set(node) & _NUMERIC:
                if type(node[name]) not in (int, float) or (type(node[name]) is float and not math.isfinite(node[name])):
                    raise ScenarioSchemaError('invalid_numeric_bound')
        for names, allowed in ((_STRING, {'string'}), (_ARRAY, {'array'}), (_OBJECT, {'object'})):
            if set(node) & names and not kinds & allowed:
                raise ScenarioSchemaError('assertion_type_mismatch')
        for low, high in (('minLength', 'maxLength'), ('minItems', 'maxItems')):
            for name in (low, high):
                if name in node and (type(node[name]) is not int or node[name] < 0):
                    raise ScenarioSchemaError('invalid_length_bound')
            if low in node and high in node and node[low] > node[high]:
                raise ScenarioSchemaError('inconsistent_length_bounds')
        if 'pattern' in node and type(node['pattern']) is not str:
            raise ScenarioSchemaError('invalid_pattern')
        lower = [node[name] for name in ('minimum', 'exclusiveMinimum') if name in node]
        upper = [node[name] for name in ('maximum', 'exclusiveMaximum') if name in node]
        if lower and upper:
            if max(lower) > min(upper) or (max(lower) == min(upper) and (
                    node.get('exclusiveMinimum') == max(lower) or node.get('exclusiveMaximum') == min(upper))):
                raise ScenarioSchemaError('inconsistent_numeric_bounds')
        if 'additionalProperties' in node and type(node['additionalProperties']) is not bool:
            raise ScenarioSchemaError('boolean_additional_properties_required')
        properties = node.get('properties', {})
        if type(properties) is not dict or len(properties) > 256:
            raise ScenarioSchemaError('invalid_properties')
        required = node.get('required', [])
        if (type(required) is not list or any(type(name) is not str for name in required)
                or len(required) != len(set(required)) or set(required) - set(properties)):
            raise ScenarioSchemaError('required_declared_properties_only')
        for name, child in properties.items():
            if not name or len(name) > 256:
                raise ScenarioSchemaError('invalid_property_name')
            check(child, depth + 1)
        if 'items' in node:
            check(node['items'], depth + 1)

    check(snapshot, 0, root=True)
    return snapshot


def validate_output_instance(value: Any, schema: dict[str, Any]) -> Any:
    """Pre-validation prevents Literal/union coercion from weakening JSON types."""
    _plain_json(value)

    def check(item: Any, node: dict) -> None:
        kinds = _types(node)
        if kinds and not any(_matches_type(item, kind) for kind in kinds):
            raise ScenarioSchemaError('output_type_mismatch')
        if 'enum' in node and not any(_enum_equal(item, choice) for choice in node['enum']):
            raise ScenarioSchemaError('output_enum_mismatch')
        if type(item) is dict:
            properties = node.get('properties', {})
            if any(name not in item for name in node.get('required', [])):
                raise ScenarioSchemaError('output_required_field_missing')
            if node.get('additionalProperties') is False and set(item) - set(properties):
                raise ScenarioSchemaError('output_extra_field')
            for name, child in properties.items():
                if name in item:
                    check(item[name], child)
        elif type(item) is list:
            for child in item:
                check(child, node.get('items', {}))

    check(value, schema)
    return value


def compile_scenario_output(scenario: Any) -> ScenarioOutputContract | None:
    """Snapshot and compile once; absent scenario preserves generic analysis."""
    if scenario is None:
        return None
    from app.services.ai.json_schema_builder import JSONSchemaBuilder, _model_from_schema

    raw = getattr(scenario, 'output_schema', None)
    if raw is None or (type(raw) is dict and not raw):
        analysis_types = getattr(scenario, 'analysis_types', None) or []
        scope = getattr(scenario, 'scope', None) or {}
        if type(analysis_types) is not list or any(type(value) is not str for value in analysis_types) or type(scope) is not dict:
            raise ScenarioSchemaError('invalid_derived_schema_configuration')
        try:
            raw = JSONSchemaBuilder.build_json_schema(analysis_types, scope)
        except Exception as error:
            raise ScenarioSchemaError('invalid_derived_schema_configuration') from error
    snapshot = supported_schema_copy(raw)
    try:
        model = _model_from_schema(snapshot, 'ScenarioOutput')
    except Exception as error:
        # Compiler/regex errors must not become an untyped provider request.
        raise ScenarioSchemaError('schema_compilation_failed') from error
    return ScenarioOutputContract(json.dumps(snapshot, ensure_ascii=False), model)


def validate_scenario_values(values: dict[str, Any]) -> ScenarioOutputContract:
    """Prospective schema fields used by ORM/bulk writes and form preflights."""
    contract = compile_scenario_output(SimpleNamespace(**{
        field: values.get(field) for field in SCHEMA_FIELDS
    }))
    assert contract is not None
    return contract


def schema_write_requires_validation(changes: dict[str, Any], *, creating: bool = False) -> bool:
    """Legacy invalid scenarios may still be disabled or renamed, not activated."""
    if "is_active" in changes and changes["is_active"] is not None and type(changes["is_active"]) is not bool:
        raise ScenarioSchemaError("boolean_activation_required")
    return creating or bool(set(changes) & SCHEMA_FIELDS) or changes.get('is_active') is True
