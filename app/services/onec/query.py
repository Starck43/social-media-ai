"""1C read-only OData query builder, row normalizer, and authorized extraction service (P17-T02).

Key invariants:
1. Read-only only: only HTTP GET requests against standard 1C OData endpoints are emitted.
   Never accepts raw OData query strings or free-form filter expressions from callers.
2. Hard limits: at most 1000 rows per request (`MAX_ROWS_PER_REQUEST = 1000`), 30 s network
   timeout (`TIMEOUT_SECONDS = 30`), 2 MiB response cap, and shared P13-T03 rate-limit admission
   (`REQUEST_TOKEN_COST = 2`) preserving the recorded P17-T01 single-account-per-connection policy.
3. Deterministic Decimal arithmetic (P12-T05 baseline): JSON responses are parsed with
   `parse_float=Decimal` so floating-point binary drift never occurs; monetary fields are
   normalized into `MonetaryValue(amount=Decimal("0.00"), currency=...)` using `ROUND_HALF_EVEN`.
4. Store nothing by default except query history metadata (`OneCQueryHistoryMetadata`);
   row payloads are returned to the caller and optionally cached in-memory only when the caller
   explicitly requests a positive `cache_ttl_seconds`.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import ssl
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping
from urllib.parse import quote

import certifi
import h11

from app.core.outbound import Resolver, default_dns_resolver
from app.integrations.onec.connections import (
    METHOD,
    PROVIDER,
    SCHEMA_VERSION,
    OneCConnectionError,
    _stored_endpoint,
)
from app.integrations.onec.credentials import OneCCredentialError, open_basic_credentials
from app.integrations.onec.endpoint import OneCEndpoint, OneCEndpointError, resolve_endpoint
from app.integrations.onec.transport import MAX_BODY_BYTES, REQUEST_TOKEN_COST
from app.integrations.ratelimit import ProviderDeferred, RateLimiter, budget_for
from app.integrations.ratelimit.signals import provider_signal
from app.integrations.ratelimit.store import BudgetConnectionUnavailable
from app.models.integrations.connection import WorkspaceConnection, WorkspaceConnectionSecret
from app.models.managers.base_manager import BaseManager
from app.models.managers.workspace_connection_manager import ConnectionDenied, _authorize
from app.models.managers.workspace_outbound_host_manager import WorkspaceOutboundHostManager
from app.services.cases.calculator import CalculationInputError, format_rub_human, quantize_money, to_exact_decimal
from app.services.onec.metadata import (
    OneCEntitySetSpec,
    OneCFieldSpec,
    OneCMetadataCatalog,
    OneCMetadataError,
    extract_connection_allowlist,
    is_valid_identifier,
    parse_odata_metadata_xml,
    validate_entity_access,
)

MAX_ROWS_PER_REQUEST = 1000
DEFAULT_ROWS_PER_REQUEST = 100
MAX_SKIP_OFFSET = 100_000
TIMEOUT_SECONDS = 30
MAX_CACHE_TTL_SECONDS = 300
MAX_HISTORY_ENTRIES = 200

_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")

ALLOWED_FILTER_OPERATORS: frozenset[str] = frozenset({"eq", "ne", "gt", "ge", "lt", "le", "startswith"})
ALLOWED_REGISTER_FUNCTIONS: frozenset[str] = frozenset({"Balance"})


class OneCQueryError(ValueError):
    """Fixed error code; never echoes raw provider responses, filter values, or secrets."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class MonetaryValue:
    """Deterministic Decimal monetary amount paired with an ISO 4217 currency code."""

    amount: Decimal
    currency: str = "RUB"

    def __post_init__(self) -> None:
        try:
            exact = to_exact_decimal(self.amount)
        except CalculationInputError:
            raise OneCQueryError("invalid_monetary_value") from None
        if exact is None:
            raise OneCQueryError("invalid_monetary_value")
        curr = self.currency.strip().upper() if isinstance(self.currency, str) else ""
        if not _CURRENCY_RE.match(curr):
            raise OneCQueryError("invalid_currency")
        object.__setattr__(self, "amount", quantize_money(exact))
        object.__setattr__(self, "currency", curr)

    @property
    def formatted(self) -> str:
        if self.currency == "RUB":
            return format_rub_human(self.amount)
        return f"{self.amount:.2f} {self.currency}"

    def to_dict(self) -> dict[str, str]:
        return {
            "amount": f"{self.amount:.2f}",
            "currency": self.currency,
            "formatted": self.formatted,
        }


@dataclass(frozen=True)
class OneCFilterPredicate:
    """Single typed filter predicate bound to a metadata-validated property."""

    field: str
    operator: str
    value: str | int | bool | Decimal | date | datetime

    def __post_init__(self) -> None:
        if not is_valid_identifier(self.field):
            raise OneCQueryError("invalid_filter_field")
        if self.operator not in ALLOWED_FILTER_OPERATORS:
            raise OneCQueryError("invalid_filter_operator")
        if isinstance(self.value, float):
            raise OneCQueryError("float_forbidden")


@dataclass(frozen=True)
class OneCSortSpec:
    """Typed ordering clause (`asc` or `desc`) on a metadata-validated property."""

    field: str
    direction: str = "asc"

    def __post_init__(self) -> None:
        if not is_valid_identifier(self.field):
            raise OneCQueryError("invalid_sort_field")
        if self.direction not in {"asc", "desc"}:
            raise OneCQueryError("invalid_sort_direction")


@dataclass(frozen=True)
class OneCQueryPlan:
    """Typed read-only 1C query plan for catalogs, documents, and registers."""

    entity_set: str
    select_fields: tuple[str, ...]
    filters: tuple[OneCFilterPredicate, ...] = ()
    register_function: str | None = None
    period: datetime | date | None = None
    date_from: datetime | date | None = None
    date_to: datetime | date | None = None
    counterparty_key: str | None = None
    item_key: str | None = None
    order_by: tuple[OneCSortSpec, ...] = ()
    top: int = DEFAULT_ROWS_PER_REQUEST
    skip: int = 0
    page_token: str | None = None
    default_currency: str = "RUB"

    def __post_init__(self) -> None:
        if not is_valid_identifier(self.entity_set):
            raise OneCQueryError("entity_not_allowed")
        if not isinstance(self.select_fields, (tuple, list)) or not self.select_fields:
            raise OneCQueryError("invalid_fields")
        object.__setattr__(self, "select_fields", tuple(self.select_fields))
        object.__setattr__(self, "filters", tuple(self.filters))
        object.__setattr__(self, "order_by", tuple(self.order_by))

        if type(self.top) is not int or not (1 <= self.top <= MAX_ROWS_PER_REQUEST):
            raise OneCQueryError("invalid_limit")
        if type(self.skip) is not int or not (0 <= self.skip <= MAX_SKIP_OFFSET):
            raise OneCQueryError("invalid_offset")
        if self.register_function is not None and self.register_function not in ALLOWED_REGISTER_FUNCTIONS:
            raise OneCQueryError("invalid_register_function")
        if not isinstance(self.default_currency, str) or not _CURRENCY_RE.match(self.default_currency.strip().upper()):
            raise OneCQueryError("invalid_currency")
        object.__setattr__(self, "default_currency", self.default_currency.strip().upper())


@dataclass(frozen=True)
class ValidatedOneCQuery:
    """Validated query target ready for IP-pinned OData GET execution."""

    entity_spec: OneCEntitySetSpec
    selected_fields: tuple[OneCFieldSpec, ...]
    odata_relative_path: str
    plan_fingerprint: str
    effective_top: int
    effective_skip: int
    default_currency: str


@dataclass(frozen=True)
class OneCQueryHistoryMetadata:
    """PII-free execution metadata; row payloads and filter literals are never stored here."""

    query_id: str
    tenant_id: int
    actor_id: int
    connection_id: int
    entity_set: str
    entity_kind: str
    register_function: str | None
    plan_fingerprint: str
    row_count: int
    top: int
    skip: int
    duration_ms: int
    from_cache: bool
    executed_at: datetime


@dataclass(frozen=True)
class OneCQueryResult:
    """Read-only extraction response returned to the caller."""

    entity_set: str
    entity_kind: str
    register_function: str | None
    selected_fields: tuple[str, ...]
    rows: tuple[Mapping[str, Any], ...]
    row_count: int
    top: int
    skip: int
    next_page_token: str | None
    plan_fingerprint: str
    history_metadata: OneCQueryHistoryMetadata
    from_cache: bool = False


def _format_datetime_literal(value: datetime | date) -> str:
    if isinstance(value, datetime):
        dt = value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
        return f"datetime'{dt.strftime('%Y-%m-%dT%H:%M:%S')}'"
    if isinstance(value, date):
        return f"datetime'{value.strftime('%Y-%m-%d')}T00:00:00'"
    raise OneCQueryError("invalid_filter_value")


def _format_guid_literal(value: str) -> str:
    if not isinstance(value, str) or not _GUID_RE.match(value.strip()):
        raise OneCQueryError("invalid_guid_value")
    return f"guid'{value.strip().lower()}'"


def _format_string_literal(value: str) -> str:
    if not isinstance(value, str) or len(value) > 512:
        raise OneCQueryError("invalid_filter_value")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise OneCQueryError("invalid_filter_value")
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def _format_filter_literal(field_spec: OneCFieldSpec, value: Any) -> str:
    if isinstance(value, float):
        raise OneCQueryError("float_forbidden")

    ltype = field_spec.logical_type
    if ltype == "guid":
        if not isinstance(value, str):
            raise OneCQueryError("invalid_guid_value")
        return _format_guid_literal(value)

    if ltype == "datetime":
        if isinstance(value, (datetime, date)):
            return _format_datetime_literal(value)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return _format_datetime_literal(parsed)
            except ValueError:
                raise OneCQueryError("invalid_filter_value") from None
        raise OneCQueryError("invalid_filter_value")

    if ltype == "boolean":
        if not isinstance(value, bool):
            raise OneCQueryError("invalid_filter_value")
        return "true" if value else "false"

    if ltype == "integer":
        if type(value) is not int:
            raise OneCQueryError("invalid_filter_value")
        return str(value)

    if ltype in {"decimal", "numeric"}:
        if isinstance(value, bool):
            raise OneCQueryError("invalid_filter_value")
        try:
            dec = to_exact_decimal(value)
        except (CalculationInputError, InvalidOperation):
            raise OneCQueryError("invalid_filter_value") from None
        if dec is None or not dec.is_finite():
            raise OneCQueryError("invalid_filter_value")
        return f"{dec}m" if ltype == "decimal" else str(dec)

    # Default Edm.String
    if not isinstance(value, str):
        raise OneCQueryError("invalid_filter_value")
    return _format_string_literal(value)


def _render_predicate(entity_spec: OneCEntitySetSpec, pred: OneCFilterPredicate) -> str:
    try:
        fspec = entity_spec.get_field(pred.field)
    except OneCMetadataError as exc:
        raise OneCQueryError(exc.code) from None

    if pred.operator == "startswith":
        if fspec.logical_type != "string" or not isinstance(pred.value, str):
            raise OneCQueryError("invalid_filter_operator")
        lit = _format_string_literal(pred.value)
        return f"startswith({fspec.name},{lit})"

    if pred.operator in {"gt", "ge", "lt", "le"} and fspec.logical_type in {"boolean", "guid"}:
        raise OneCQueryError("invalid_filter_operator")

    lit = _format_filter_literal(fspec, pred.value)
    return f"{fspec.name} {pred.operator} {lit}"


def _compute_plan_fingerprint(
    *,
    entity_set: str,
    register_function: str | None,
    period_literal: str | None,
    select_names: tuple[str, ...],
    filter_expr: str,
    orderby_expr: str,
    default_currency: str,
) -> str:
    canonical = json.dumps(
        {
            "entity_set": entity_set,
            "register_function": register_function,
            "period": period_literal,
            "select": list(select_names),
            "filter": filter_expr,
            "orderby": orderby_expr,
            "currency": default_currency,
        },
        ensure_ascii=True,
         sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def encode_page_token(*, plan_fingerprint: str, skip: int) -> str:
    """Encode a deterministic pagination token bound to `plan_fingerprint` and `skip`."""
    if not isinstance(plan_fingerprint, str) or len(plan_fingerprint) != 64:
        raise OneCQueryError("invalid_page_token")
    if type(skip) is not int or not (0 <= skip <= MAX_SKIP_OFFSET):
        raise OneCQueryError("invalid_offset")
    payload = json.dumps(
        {"v": 1, "fp": plan_fingerprint, "skip": skip},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_page_token(token: str, *, expected_fingerprint: str) -> int:
    """Decode and verify a pagination token against `expected_fingerprint`."""
    if not isinstance(token, str) or not token or len(token) > 256:
        raise OneCQueryError("invalid_page_token")
    padding = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode((token + padding).encode("ascii"))
        data = json.loads(raw.decode("ascii"))
    except (ValueError, UnicodeDecodeError):
        raise OneCQueryError("invalid_page_token") from None

    if (
        not isinstance(data, dict)
        or set(data) != {"v", "fp", "skip"}
        or data.get("v") != 1
        or data.get("fp") != expected_fingerprint
        or type(data.get("skip")) is not int
        or not (0 <= data["skip"] <= MAX_SKIP_OFFSET)
    ):
        raise OneCQueryError("invalid_page_token")
    return data["skip"]


def build_odata_query_target(
    plan: OneCQueryPlan,
    catalog: OneCMetadataCatalog,
    *,
    allowed_entity_sets: frozenset[str],
    allowed_fields_by_entity: Mapping[str, frozenset[str]] | None = None,
    expected_metadata_sha256: str | None = None,
    base_path: str = "",
) -> ValidatedOneCQuery:
    """Validate `plan` against `$metadata` and connection allowlist, returning the OData GET target."""
    if not isinstance(plan, OneCQueryPlan):
        raise OneCQueryError("invalid_plan")

    try:
        entity_spec, selected_specs = validate_entity_access(
            catalog,
            plan.entity_set,
            plan.select_fields,
            allowed_entity_sets=allowed_entity_sets,
            allowed_fields_by_entity=allowed_fields_by_entity,
            expected_metadata_sha256=expected_metadata_sha256,
        )
    except OneCMetadataError as exc:
        raise OneCQueryError(exc.code) from None

    per_entity_allowed = (allowed_fields_by_entity or {}).get(plan.entity_set)

    def _require_field_allowed(fname: str) -> None:
        if per_entity_allowed is not None and fname not in per_entity_allowed:
            raise OneCQueryError("field_not_allowed")

    # Validate register function (`Balance(Period=...)`)
    period_literal: str | None = None
    if plan.register_function is not None:
        if not entity_spec.supports_balance or entity_spec.kind not in {
            "accumulation_register",
            "information_register",
        }:
            raise OneCQueryError("balance_not_supported")
        if plan.period is not None:
            period_literal = _format_datetime_literal(plan.period)
        entity_segment = (
            f"{entity_spec.name}/{plan.register_function}(Period={period_literal})"
            if period_literal is not None
            else f"{entity_spec.name}/{plan.register_function}()"
        )
    else:
        if plan.period is not None:
            pfield = entity_spec.period_field or entity_spec.date_field
            if not pfield:
                raise OneCQueryError("period_filter_not_supported")
            _require_field_allowed(pfield)
        entity_segment = entity_spec.name

    # Collect all filter predicates (explicit predicates + typed date/counterparty/item/period filters)
    combined_predicates: list[OneCFilterPredicate] = list(plan.filters)

    if plan.register_function is None and plan.period is not None:
        pfield = entity_spec.period_field or entity_spec.date_field
        assert pfield is not None
        combined_predicates.append(OneCFilterPredicate(field=pfield, operator="le", value=plan.period))

    if plan.date_from is not None:
        dfield = entity_spec.date_field
        if not dfield:
            raise OneCQueryError("date_filter_not_supported")
        _require_field_allowed(dfield)
        combined_predicates.append(OneCFilterPredicate(field=dfield, operator="ge", value=plan.date_from))

    if plan.date_to is not None:
        dfield = entity_spec.date_field
        if not dfield:
            raise OneCQueryError("date_filter_not_supported")
        _require_field_allowed(dfield)
        combined_predicates.append(OneCFilterPredicate(field=dfield, operator="le", value=plan.date_to))

    if plan.counterparty_key is not None:
        cfield = entity_spec.counterparty_field
        if not cfield:
            raise OneCQueryError("counterparty_filter_not_supported")
        _require_field_allowed(cfield)
        combined_predicates.append(OneCFilterPredicate(field=cfield, operator="eq", value=plan.counterparty_key))

    if plan.item_key is not None:
        ifield = entity_spec.item_field
        if not ifield:
            raise OneCQueryError("item_filter_not_supported")
        _require_field_allowed(ifield)
        combined_predicates.append(OneCFilterPredicate(field=ifield, operator="eq", value=plan.item_key))

    rendered_filters: list[str] = []
    for pred in combined_predicates:
        _require_field_allowed(pred.field)
        rendered_filters.append(_render_predicate(entity_spec, pred))
    filter_expr = " and ".join(rendered_filters)

    # Validate or synthesize deterministic $orderby
    sort_specs = list(plan.order_by)
    if not sort_specs:
        default_sort_field = entity_spec.key_fields[0] if entity_spec.key_fields else selected_specs[0].name
        if per_entity_allowed is None or default_sort_field in per_entity_allowed:
            sort_specs = [OneCSortSpec(field=default_sort_field, direction="asc")]
        else:
            sort_specs = [OneCSortSpec(field=selected_specs[0].name, direction="asc")]

    rendered_sorts: list[str] = []
    for sort_item in sort_specs:
        _require_field_allowed(sort_item.field)
        try:
            entity_spec.get_field(sort_item.field)
        except OneCMetadataError as exc:
            raise OneCQueryError(exc.code) from None
        rendered_sorts.append(f"{sort_item.field} {sort_item.direction}")
    orderby_expr = ",".join(rendered_sorts)

    select_names = tuple(spec.name for spec in selected_specs)
    plan_fingerprint = _compute_plan_fingerprint(
        entity_set=entity_spec.name,
        register_function=plan.register_function,
        period_literal=period_literal,
        select_names=select_names,
        filter_expr=filter_expr,
        orderby_expr=orderby_expr,
        default_currency=plan.default_currency,
    )

    effective_skip = (
        decode_page_token(plan.page_token, expected_fingerprint=plan_fingerprint)
        if plan.page_token is not None
        else plan.skip
    )
    effective_top = plan.top

    # Include currency reference fields in OData $select if a monetary field is selected
    odata_select_list = list(select_names)
    for spec in selected_specs:
        if (
            spec.is_monetary
            and spec.currency_field
            and spec.currency_field not in odata_select_list
            and (per_entity_allowed is None or spec.currency_field in per_entity_allowed)
        ):
            odata_select_list.append(spec.currency_field)

    query_parts = [
        "$format=json",
        f"$select={quote(','.join(odata_select_list), safe=',')}",
    ]
    if filter_expr:
        query_parts.append(f"$filter={quote(filter_expr, safe='(),=')}")
    if orderby_expr:
        query_parts.append(f"$orderby={quote(orderby_expr, safe=',')}")
    query_parts.append(f"$top={effective_top}")
    query_parts.append(f"$skip={effective_skip}")

    normalized_base = base_path.rstrip("/")
    encoded_entity_segment = quote(entity_segment, safe="/(),'=:")
    odata_relative_path = f"{normalized_base}/odata/standard.odata/{encoded_entity_segment}?{'&'.join(query_parts)}"

    return ValidatedOneCQuery(
        entity_spec=entity_spec,
        selected_fields=selected_specs,
        odata_relative_path=odata_relative_path,
        plan_fingerprint=plan_fingerprint,
        effective_top=effective_top,
        effective_skip=effective_skip,
        default_currency=plan.default_currency,
    )


def _normalize_row(
    raw_row: Any,
    selected_fields: tuple[OneCFieldSpec, ...],
    default_currency: str,
) -> dict[str, Any]:
    if not isinstance(raw_row, Mapping):
        raise OneCQueryError("invalid_response")

    normalized: dict[str, Any] = {}
    for fspec in selected_fields:
        raw_val = raw_row.get(fspec.name)
        if raw_val is None:
            normalized[fspec.name] = None
            continue

        if fspec.is_monetary:
            row_currency = default_currency
            if fspec.currency_field:
                candidate = raw_row.get(fspec.currency_field)
                if isinstance(candidate, str) and _CURRENCY_RE.match(candidate.strip().upper()):
                    row_currency = candidate.strip().upper()
            normalized[fspec.name] = MonetaryValue(amount=raw_val, currency=row_currency)
        elif fspec.logical_type == "decimal":
            try:
                exact_dec = to_exact_decimal(raw_val)
            except (CalculationInputError, InvalidOperation):
                raise OneCQueryError("invalid_response") from None
            normalized[fspec.name] = exact_dec
        elif fspec.logical_type == "integer":
            if isinstance(raw_val, bool) or not isinstance(raw_val, int):
                try:
                    normalized[fspec.name] = int(str(raw_val))
                except (ValueError, TypeError):
                    raise OneCQueryError("invalid_response") from None
            else:
                normalized[fspec.name] = raw_val
        elif fspec.logical_type == "boolean":
            if not isinstance(raw_val, bool):
                raise OneCQueryError("invalid_response")
            normalized[fspec.name] = raw_val
        else:
            normalized[fspec.name] = str(raw_val)

    return normalized


def parse_odata_json_rows(
    body: bytes,
    validated: ValidatedOneCQuery,
) -> tuple[tuple[Mapping[str, Any], ...], str | None]:
    """Parse 1C OData JSON payload using exact Decimal parsing and project allowlisted fields."""
    if not isinstance(body, (bytes, bytearray)) or not body:
        raise OneCQueryError("empty_response")
    if len(body) > MAX_BODY_BYTES:
        raise OneCQueryError("response_too_large")
    try:
        payload = json.loads(
            bytes(body).decode("utf-8-sig"),
            parse_float=Decimal,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, InvalidOperation):
        raise OneCQueryError("invalid_response") from None

    if not isinstance(payload, dict):
        raise OneCQueryError("invalid_response")

    # Standard 1C OData v3/v4 JSON formats: {"value": [...]} or {"d": {"results": [...]}}
    if "value" in payload and isinstance(payload["value"], list):
        raw_items = payload["value"]
        has_server_next = bool(payload.get("odata.nextLink") or payload.get("@odata.nextLink"))
    elif isinstance(payload.get("d"), dict) and isinstance(payload["d"].get("results"), list):
        raw_items = payload["d"]["results"]
        has_server_next = bool(payload["d"].get("__next"))
    else:
        raise OneCQueryError("invalid_response")

    if len(raw_items) > validated.effective_top or len(raw_items) > MAX_ROWS_PER_REQUEST:
        raise OneCQueryError("response_too_large")

    rows = tuple(
        _normalize_row(item, validated.selected_fields, validated.default_currency)
        for item in raw_items
    )

    next_token: str | None = None
    if len(rows) == validated.effective_top or has_server_next:
        next_skip = validated.effective_skip + len(rows)
        if next_skip <= MAX_SKIP_OFFSET and len(rows) > 0:
            next_token = encode_page_token(plan_fingerprint=validated.plan_fingerprint, skip=next_skip)

    return rows, next_token


class OneCQueryCache:
    """Bounded opt-in TTL cache for read-only 1C query results, scoped to tenant/actor/connection/hash."""

    def __init__(self, *, max_entries: int = 128, clock: Callable[[], float] = time.monotonic):
        self._max_entries = max(1, max_entries)
        self._clock = clock
        self._store: dict[tuple[Any, ...], tuple[float, tuple[Mapping[str, Any], ...], str | None]] = {}

    def get(
        self, key: tuple[Any, ...]
    ) -> tuple[tuple[Mapping[str, Any], ...], str | None] | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, rows, next_token = entry
        if self._clock() >= expires_at:
            self._store.pop(key, None)
            return None
        return rows, next_token

    def put(
        self,
        key: tuple[Any, ...],
        rows: tuple[Mapping[str, Any], ...],
        next_token: str | None,
        *,
        ttl_seconds: int,
    ) -> None:
        if ttl_seconds <= 0:
            return
        capped_ttl = min(ttl_seconds, MAX_CACHE_TTL_SECONDS)
        now = self._clock()
        expired_keys = [k for k, (exp, _, _) in self._store.items() if now >= exp]
        for k in expired_keys:
            self._store.pop(k, None)
        if len(self._store) >= self._max_entries and key not in self._store:
            oldest_key = next(iter(self._store))
            self._store.pop(oldest_key, None)
        self._store[key] = (now + capped_ttl, rows, next_token)

    def invalidate_connection(self, tenant_id: int, connection_id: int) -> None:
        to_remove = [k for k in self._store if len(k) >= 3 and k[0] == tenant_id and k[2] == connection_id]
        for k in to_remove:
            self._store.pop(k, None)


async def fetch_odata_bytes(
    endpoint: OneCEndpoint,
    target_path: str,
    login: str,
    password: str,
    *,
    limiter,
    budget,
    resolver: Resolver = default_dns_resolver,
    accept_header: bytes = b"application/json;odata=minimalmetadata",
) -> bytes:
    """Perform one IP-pinned HTTPS GET request under the shared 1C rate-limit budget."""
    if not isinstance(login, str) or not isinstance(password, str) or not login or not password or ":" in login:
        raise OneCQueryError("invalid_credentials")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in login + password) or "SSLKEYLOGFILE" in os.environ:
        raise OneCQueryError("invalid_credentials")
    if not isinstance(target_path, str) or not target_path.startswith("/") or any(ord(c) < 33 or ord(c) == 127 for c in target_path):
        raise OneCQueryError("invalid_query_target")

    writer = None
    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            async with limiter.acquire(budget, cost=REQUEST_TOKEN_COST, max_wait=0):
                target = await asyncio.to_thread(resolve_endpoint, endpoint, resolver=resolver)
                tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                tls.load_verify_locations(certifi.where())
                tls.minimum_version = ssl.TLSVersion.TLSv1_2
                tls.set_alpn_protocols(["http/1.1"])
                reader, writer = await asyncio.open_connection(
                    target.addresses[0], target.port, ssl=tls, server_hostname=target.host, ssl_handshake_timeout=8
                )
                auth = base64.b64encode(f"{login}:{password}".encode("utf-8"))
                host = target.host if target.port == 443 else f"{target.host}:{target.port}"
                protocol = h11.Connection(h11.CLIENT, max_incomplete_event_size=16384)
                wire = protocol.send(
                    h11.Request(
                        method=b"GET",
                        target=quote(target_path, safe="/%$?:&=@(),'-._~").encode("ascii"),
                        headers=[
                            (b"host", host.encode("ascii")),
                            (b"authorization", b"Basic " + auth),
                            (b"accept", accept_header),
                            (b"accept-encoding", b"identity"),
                            (b"connection", b"close"),
                        ],
                    )
                ) + protocol.send(h11.EndOfMessage())
                writer.write(wire)
                await writer.drain()
                status, body = None, bytearray()
                while True:
                    event = protocol.next_event()
                    if event is h11.NEED_DATA:
                        chunk = await reader.read(16384)
                        protocol.receive_data(chunk)
                        if not chunk and status is None:
                            raise OneCQueryError("connection_failed")
                    elif isinstance(event, h11.Response):
                        status = event.status_code
                        if 300 <= status < 400:
                            raise OneCQueryError("redirect_forbidden")
                        if status in (401, 403):
                            raise OneCQueryError("authentication_failed")
                        if status == 404:
                            raise OneCQueryError("entity_not_found")
                        if status in (429, 503):
                            retry_after = next(
                                (v.decode("ascii", "ignore") for k, v in event.headers if k.lower() == b"retry-after"),
                                None,
                            )
                            signal = provider_signal("onec", status, {"retry-after": retry_after})
                            await limiter.note_signal(budget, signal)
                            raise OneCQueryError("rate_limited" if status == 429 else "service_unavailable")
                        if status != 200:
                            raise OneCQueryError("service_unavailable")
                        if any(k.lower() == b"content-encoding" and v.lower() != b"identity" for k, v in event.headers):
                            raise OneCQueryError("unsupported_encoding")
                    elif isinstance(event, h11.Data):
                        body.extend(event.data)
                        if len(body) > MAX_BODY_BYTES:
                            raise OneCQueryError("response_too_large")
                    elif isinstance(event, h11.EndOfMessage):
                        if not body:
                            raise OneCQueryError("empty_response")
                        return bytes(body)
                    else:
                        raise OneCQueryError("invalid_response")
    except OneCQueryError:
        raise
    except OneCEndpointError as exc:
        raise OneCQueryError(exc.code) from None
    except (OSError, TimeoutError, ValueError, UnicodeError, h11.ProtocolError):
        raise OneCQueryError("connection_failed") from None
    finally:
        if writer is not None:
            writer.close()
            try:
                async with asyncio.timeout(1):
                    await writer.wait_closed()
            except (OSError, TimeoutError):
                pass


class OneCReadQueryService:
    """Authorized read-only 1C metadata and data extraction service (P17-T02).

    Enforces workspace RBAC (`require_source_view=True`), active/verified connection state,
    approved workspace host (`WorkspaceOutboundHostManager.require_approved`),
    per-connection administrator entity/field allowlists, shared provider rate budget,
    and IP-pinned HTTPS GET execution.
    """

    def __init__(self, *, cache: OneCQueryCache | None = None):
        self._sessions = BaseManager(WorkspaceConnection)
        self._hosts = WorkspaceOutboundHostManager()
        self._limiter = RateLimiter()
        self._cache = cache if cache is not None else OneCQueryCache()
        self._history: deque[OneCQueryHistoryMetadata] = deque(maxlen=MAX_HISTORY_ENTRIES)

    def list_history_metadata(self, *, tenant_id: int, actor_id: int | None = None) -> tuple[OneCQueryHistoryMetadata, ...]:
        """Return PII-free query history metadata recorded for `tenant_id` (and optional `actor_id`)."""
        return tuple(
            entry
            for entry in self._history
            if entry.tenant_id == tenant_id and (actor_id is None or entry.actor_id == actor_id)
        )

    async def _load_authorized_connection(self, *, tenant_id: int, actor_id: int, connection_id: int):
        if type(connection_id) is not int or connection_id <= 0:
            raise ConnectionDenied()
        async with self._sessions._get_owned_session() as session:
            await _authorize(session, tenant_id, actor_id, require_source_view=True)
            from sqlalchemy import select

            row = (
                await session.execute(
                    select(WorkspaceConnection)
                    .where(
                        WorkspaceConnection.id == connection_id,
                        WorkspaceConnection.tenant_id == tenant_id,
                        WorkspaceConnection.owner_user_id == actor_id,
                    )
                    .with_for_update(read=True)
                )
            ).scalar_one_or_none()
            if (
                row is None
                or not row.is_active
                or row.provider_id != PROVIDER
                or row.auth_method != METHOD
                or row.schema_version != SCHEMA_VERSION
                or row.state != "verified"
            ):
                raise ConnectionDenied()

            config = dict(row.config or {})
            endpoint = _stored_endpoint(config)
            if endpoint.interface != "odata_standard":
                raise OneCQueryError("unsupported_interface")

            await self._hosts.require_approved(session=session, tenant_id=tenant_id, actor_id=actor_id, host=endpoint.host)
            secret = await session.get(WorkspaceConnectionSecret, connection_id)
            ciphertext = secret.secret_encrypted if secret is not None else ""
            try:
                credentials = open_basic_credentials(ciphertext)
            except OneCCredentialError as exc:
                raise OneCQueryError(exc.code) from None

            try:
                allowed_sets, allowed_fields = extract_connection_allowlist(config)
            except OneCMetadataError as exc:
                raise OneCQueryError(exc.code) from None

            metadata_sha256 = config.get("metadata_sha256")
            if metadata_sha256 is not None and (not isinstance(metadata_sha256, str) or len(metadata_sha256) != 64):
                raise OneCConnectionError("invalid_configuration")

            return (
                endpoint,
                credentials,
                row.account_key,
                allowed_sets,
                allowed_fields,
                metadata_sha256,
            )

    async def fetch_metadata_catalog(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        connection_id: int,
        resolver: Resolver = default_dns_resolver,
    ) -> OneCMetadataCatalog:
        """Fetch and parse `$metadata` for an authorized, verified 1C OData connection."""
        (
            endpoint,
            credentials,
            account_key,
            _allowed_sets,
            _allowed_fields,
            expected_sha256,
        ) = await self._load_authorized_connection(
            tenant_id=tenant_id, actor_id=actor_id, connection_id=connection_id
        )
        budget = budget_for(PROVIDER, account_key, connection_id=connection_id)
        try:
            xml_bytes = await fetch_odata_bytes(
                endpoint,
                quote(endpoint.health_path, safe="/%$:@-._~"),
                credentials.login,
                credentials.password,
                limiter=self._limiter,
                budget=budget,
                resolver=resolver,
                accept_header=b"application/xml",
            )
        except ProviderDeferred:
            raise OneCQueryError("rate_limited") from None
        except BudgetConnectionUnavailable:
            raise ConnectionDenied() from None

        try:
            catalog = parse_odata_metadata_xml(xml_bytes)
        except OneCMetadataError as exc:
            raise OneCQueryError(exc.code) from None

        if expected_sha256 is not None and catalog.metadata_sha256 != expected_sha256:
            raise OneCQueryError("metadata_hash_mismatch")
        return catalog

    async def execute_query(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        connection_id: int,
        plan: OneCQueryPlan,
        catalog: OneCMetadataCatalog,
        cache_ttl_seconds: int = 0,
        resolver: Resolver = default_dns_resolver,
    ) -> OneCQueryResult:
        """Validate `plan` against `catalog` and connection allowlist, then execute a bounded OData GET."""
        if type(cache_ttl_seconds) is not int or not (0 <= cache_ttl_seconds <= MAX_CACHE_TTL_SECONDS):
            raise OneCQueryError("invalid_cache_ttl")

        started = time.monotonic()
        (
            endpoint,
            credentials,
            account_key,
            allowed_sets,
            allowed_fields,
            expected_sha256,
        ) = await self._load_authorized_connection(
            tenant_id=tenant_id, actor_id=actor_id, connection_id=connection_id
        )

        # Validate the plan and allowlist BEFORE checking cache or consuming provider rate budget.
        validated = build_odata_query_target(
            plan,
            catalog,
            allowed_entity_sets=allowed_sets,
            allowed_fields_by_entity=allowed_fields,
            expected_metadata_sha256=expected_sha256,
            base_path=endpoint.base_path,
        )

        cache_key = (
            tenant_id,
            actor_id,
            connection_id,
            account_key,
            catalog.metadata_sha256,
            validated.plan_fingerprint,
            validated.effective_skip,
            validated.effective_top,
        )

        cached = self._cache.get(cache_key) if cache_ttl_seconds > 0 else None
        if cached is not None:
            rows, next_token = cached
            from_cache = True
        else:
            budget = budget_for(PROVIDER, account_key, connection_id=connection_id)
            try:
                raw_body = await fetch_odata_bytes(
                    endpoint,
                    validated.odata_relative_path,
                    credentials.login,
                    credentials.password,
                    limiter=self._limiter,
                    budget=budget,
                    resolver=resolver,
                )
            except ProviderDeferred:
                raise OneCQueryError("rate_limited") from None
            except BudgetConnectionUnavailable:
                raise ConnectionDenied() from None

            rows, next_token = parse_odata_json_rows(raw_body, validated)
            from_cache = False
            if cache_ttl_seconds > 0:
                self._cache.put(cache_key, rows, next_token, ttl_seconds=cache_ttl_seconds)

        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        history_entry = OneCQueryHistoryMetadata(
            query_id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            actor_id=actor_id,
            connection_id=connection_id,
            entity_set=validated.entity_spec.name,
            entity_kind=validated.entity_spec.kind,
            register_function=plan.register_function,
            plan_fingerprint=validated.plan_fingerprint,
            row_count=len(rows),
            top=validated.effective_top,
            skip=validated.effective_skip,
            duration_ms=duration_ms,
            from_cache=from_cache,
            executed_at=datetime.now(timezone.utc),
        )
        self._history.append(history_entry)

        return OneCQueryResult(
            entity_set=validated.entity_spec.name,
            entity_kind=validated.entity_spec.kind,
            register_function=plan.register_function,
            selected_fields=tuple(spec.name for spec in validated.selected_fields),
            rows=rows,
            row_count=len(rows),
            top=validated.effective_top,
            skip=validated.effective_skip,
            next_page_token=next_token,
            plan_fingerprint=validated.plan_fingerprint,
            history_metadata=history_entry,
            from_cache=from_cache,
        )
