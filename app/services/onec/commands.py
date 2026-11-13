"""1C write commands with preview, per-item approval, DataVersion concurrency, and audit (P17-T04).

Key security and reliability invariants:
1. All 1C write capabilities (`onec.document.create.v1`, `onec.document.post.v1`,
   `onec.catalog.update.v1`) are disabled by default (`enabled_by_default=False`) and
   guarded by both capability enablement (`RUNTIME_ENABLED_CAPABILITIES` + optional
   per-connection `enabled_write_capabilities`) and dedicated kill switches
   (`KILL_ONEC_DOCUMENT_CREATE`, `KILL_ONEC_DOCUMENT_POST`, `KILL_ONEC_CATALOG_UPDATE`,
   `KILL_ONEC_DOCUMENT_WRITE`, `KILL_ALL_WRITES`).
2. Requires a separate 1C write connection method (`WRITE_METHOD = "odata_write_credentials"`).
   Read-only 1C connections (`auth_method == "odata_credentials"`) are strictly rejected
   with `OneCCommandError("write_connection_required")`.
3. Every write command is previewed against `$metadata` (`OneCMetadataCatalog`) and the
   connection allowlist, capturing the remote object's `DataVersion` (for `document.post`
   and `catalog.update`), a human-readable field diff (for `catalog.update`), and a
   deterministic external idempotency reference (`SM1C:<24-hex>`) written to the 1C
   comment/attribute field.
4. Per-item approval via the P13-T04 command ledger (`approval_policy="per_item"`) binds
   `actor_id`, `tenant_id`, `payload_hash` (covering `expected_data_version` and
   `external_ref`), and interactive TTL (`60..3600` seconds).
5. Execution re-verifies capability enablement, kill switch, write connection, RBAC, and
   approval TTL/hash, then re-fetches the target object from 1C OData to verify that
   `DataVersion` has not changed since preview/approval (`data_version_conflict` on mismatch)
   and checks 1C for the external idempotency reference before mutating.
6. Transport timeouts transition the command to `unknown` (`CommandTimeoutUnknown`),
   record `ActionAuditEvent.objects.record_unknown` with `action="onec.command_execute"`,
   and reconcile against 1C OData before any retry.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import ssl
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import quote

import certifi
import h11
from sqlalchemy import select

from app.core.database import async_session_maker
from app.core.outbound import Resolver, default_dns_resolver
from app.core.permissions import get_current_user, set_current_user
from app.core.tenant_context import current_tenant_id, tenant_scope
from app.integrations.onec.connections import METHOD as READ_ONLY_METHOD
from app.integrations.onec.connections import PROVIDER, SCHEMA_VERSION, OneCConnectionError, _stored_endpoint
from app.integrations.onec.credentials import OneCCredentialError, open_basic_credentials
from app.integrations.onec.endpoint import OneCEndpoint, OneCEndpointError, resolve_endpoint
from app.integrations.onec.transport import MAX_BODY_BYTES, REQUEST_TOKEN_COST
from app.integrations.ratelimit import ProviderDeferred, RateLimiter, budget_for
from app.integrations.ratelimit.signals import provider_signal
from app.integrations.ratelimit.store import BudgetConnectionUnavailable
from app.models.actions.business_command import BusinessCommand, BusinessCommandTarget
from app.models.audit.action_audit_event import ActionAuditEvent
from app.models.integrations.connection import WorkspaceConnection, WorkspaceConnectionSecret
from app.models.managers.base_manager import BaseManager
from app.models.managers.workspace_connection_manager import ConnectionDenied, _authorize
from app.models.managers.workspace_outbound_host_manager import WorkspaceOutboundHostManager
from app.services.cases.calculator import CalculationInputError, quantize_money, to_exact_decimal
from app.services.commands import ledger as ledger_mod
from app.services.commands.ledger import (
    BusinessCommandLedger,
    CommandConflict,
    CommandDenied,
    CommandNotFound,
    CommandTimeoutUnknown,
    _verify_member_grant,
)
from app.services.commands.registry import (
    CAPABILITY_REGISTRY,
    DEFAULT_TTL_SECONDS,
    CapabilitySpec,
    canonical_payload_hash,
    get_capability,
)
from app.services.onec.metadata import (
    OneCEntitySetSpec,
    OneCFieldSpec,
    OneCMetadataCatalog,
    OneCMetadataError,
    extract_connection_allowlist,
    is_valid_identifier,
    parse_odata_metadata_xml,
)
from app.services.onec.query import TIMEOUT_SECONDS, MonetaryValue, OneCQueryError

WRITE_METHOD = "odata_write_credentials"
MIN_TTL_SECONDS = 60
MAX_INTERACTIVE_TTL_SECONDS = 60 * 60

CAPABILITY_DOCUMENT_CREATE = "onec.document.create.v1"
CAPABILITY_DOCUMENT_POST = "onec.document.post.v1"
CAPABILITY_CATALOG_UPDATE = "onec.catalog.update.v1"

ONEC_WRITE_CAPABILITIES: tuple[str, ...] = (
    CAPABILITY_DOCUMENT_CREATE,
    CAPABILITY_DOCUMENT_POST,
    CAPABILITY_CATALOG_UPDATE,
)

ONEC_CAPABILITY_SPECS: Mapping[str, CapabilitySpec] = MappingProxyType(
    {code: CAPABILITY_REGISTRY[code] for code in ONEC_WRITE_CAPABILITIES}
)

SYSTEM_READONLY_FIELDS: frozenset[str] = frozenset(
    {
        "Ref_Key",
        "DataVersion",
        "DeletionMark",
        "Posted",
        "Predefined",
        "PredefinedDataName",
    }
)

IDEMPOTENCY_COMMENT_CANDIDATES: tuple[str, ...] = (
    "Комментарий",
    "Comment",
    "ДополнительнаяИнформация",
    "Описание",
    "Description",
)

_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_MAX_STRING_FIELD_LEN = 1000


class OneCCommandError(ValueError):
    """Fixed error code for 1C write command validation, approval, concurrency, and execution."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class OneCFieldDiff:
    """Human-readable before/after diff for a single metadata-validated 1C field."""

    field: str
    edm_type: str
    old_value: Any
    new_value: Any
    formatted_old: str
    formatted_new: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "edm_type": self.edm_type,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "formatted_old": self.formatted_old,
            "formatted_new": self.formatted_new,
        }


@dataclass(frozen=True)
class OneCWritePreviewRequest:
    """Typed input for previewing a 1C write command (`create`, `post`, or `catalog.update`)."""

    capability: str
    idempotency_key: str
    entity_set: str
    target_guid: str | None = None
    fields: Mapping[str, Any] = field(default_factory=dict)
    posting_mode_operational: bool = False
    default_currency: str = "RUB"


@dataclass(frozen=True)
class OneCCommandRecord:
    """Structured projection of a 1C write command across preview, approval, execution, and reconcile."""

    id: int
    tenant_id: int
    actor_id: int
    connection_id: int
    capability: str
    approval_policy: str
    idempotency_key: str
    external_ref: str
    entity_set: str
    target_ref: str
    target_guid: str | None
    expected_data_version: str | None
    payload_hash: str
    approved_payload_hash: str | None
    state: str
    preview_snapshot: Mapping[str, Any]
    provider_receipt: str | None
    error_code: str | None
    reconcile_hint: str | None
    approved_by: int | None
    approved_at: datetime | None
    approval_expires_at: datetime | None
    executed_at: datetime | None
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "actor_id": self.actor_id,
            "connection_id": self.connection_id,
            "capability": self.capability,
            "approval_policy": self.approval_policy,
            "idempotency_key": self.idempotency_key,
            "external_ref": self.external_ref,
            "entity_set": self.entity_set,
            "target_ref": self.target_ref,
            "target_guid": self.target_guid,
            "expected_data_version": self.expected_data_version,
            "payload_hash": self.payload_hash,
            "approved_payload_hash": self.approved_payload_hash,
            "state": self.state,
            "preview_snapshot": dict(self.preview_snapshot),
            "provider_receipt": self.provider_receipt,
            "error_code": self.error_code,
            "reconcile_hint": self.reconcile_hint,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "approval_expires_at": self.approval_expires_at,
            "executed_at": self.executed_at,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class OneCCommandAuditEntry:
    """Tenant-scoped 1C command audit log entry combining `ActionAuditEvent` and `BusinessCommand`."""

    event_id: str
    command_id: int | None
    tenant_id: int
    actor_id: int | None
    action: str
    outcome: str
    capability: str | None
    entity_set: str | None
    target_ref: str | None
    state: str | None
    payload_hash: str | None
    provider_receipt: str | None
    reason_code: str | None
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "command_id": self.command_id,
            "tenant_id": self.tenant_id,
            "actor_id": self.actor_id,
            "action": self.action,
            "outcome": self.outcome,
            "capability": self.capability,
            "entity_set": self.entity_set,
            "target_ref": self.target_ref,
            "state": self.state,
            "payload_hash": self.payload_hash,
            "provider_receipt": self.provider_receipt,
            "reason_code": self.reason_code,
            "created_at": self.created_at,
        }


def get_onec_write_capability(capability: str) -> CapabilitySpec:
    """Resolve one of the three 1C write capabilities from CAPABILITY_REGISTRY or raise `OneCCommandError`."""
    if not isinstance(capability, str) or capability not in ONEC_WRITE_CAPABILITIES:
        raise OneCCommandError("unsupported_capability")
    return get_capability(capability)


def is_onec_write_capability_enabled(
    capability: str,
    *,
    connection_config: Mapping[str, Any] | None = None,
) -> tuple[bool, str | None]:
    """Check runtime kill switches, runtime capability enablement, and optional connection allowlist."""
    spec = get_onec_write_capability(capability)
    kill_switches = ledger_mod.RUNTIME_KILL_SWITCHES
    if (
        kill_switches.get(spec.kill_switch_key, False)
        or kill_switches.get("KILL_ONEC_DOCUMENT_WRITE", False)
        or kill_switches.get("KILL_ALL_WRITES", False)
    ):
        return False, "kill_switch_active"

    enabled_runtime = ledger_mod.RUNTIME_ENABLED_CAPABILITIES
    if spec.code not in enabled_runtime and not spec.enabled_by_default:
        return False, "capability_disabled"

    if connection_config is not None and "enabled_write_capabilities" in connection_config:
        raw_enabled = connection_config.get("enabled_write_capabilities")
        if not isinstance(raw_enabled, (list, tuple, set, frozenset)) or spec.code not in raw_enabled:
            return False, "capability_disabled"

    return True, None


def validate_idempotency_key(idempotency_key: Any) -> str:
    """Validate caller-supplied idempotency key."""
    if not isinstance(idempotency_key, str):
        raise OneCCommandError("invalid_idempotency_key")
    cleaned = idempotency_key.strip()
    if not cleaned or len(cleaned) > 120:
        raise OneCCommandError("invalid_idempotency_key")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned):
        raise OneCCommandError("invalid_idempotency_key")
    return cleaned


def normalize_guid(value: Any) -> str:
    """Validate and normalize a 1C OData GUID (`Ref_Key`)."""
    if not isinstance(value, str) or not _GUID_RE.match(value.strip()):
        raise OneCCommandError("invalid_guid_value")
    return value.strip().lower()


def build_external_idempotency_ref(
    *,
    tenant_id: int,
    connection_id: int,
    capability: str,
    idempotency_key: str,
) -> str:
    """Build a deterministic external reference token stored in 1C to prevent duplicate writes."""
    cleaned_key = validate_idempotency_key(idempotency_key)
    raw = f"{tenant_id}:{connection_id}:{capability}:{cleaned_key}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:24]
    return f"SM1C:{digest}"


def resolve_idempotency_comment_field(
    entity_spec: OneCEntitySetSpec,
    *,
    allowed_fields: frozenset[str] | None = None,
) -> str | None:
    """Find a string comment/description attribute on `entity_spec` for embedding `external_ref`."""
    for candidate in IDEMPOTENCY_COMMENT_CANDIDATES:
        if candidate in entity_spec.fields:
            fspec = entity_spec.fields[candidate]
            if fspec.logical_type == "string" and (allowed_fields is None or candidate in allowed_fields):
                return candidate
    return None


def attach_external_ref_to_comment(existing_comment: str | None, external_ref: str) -> str:
    """Embed `[SM1C:...]` into a 1C comment field idempotently without exceeding limits."""
    marker = f"[{external_ref}]"
    if not existing_comment:
        return marker
    trimmed = existing_comment.strip()
    if marker in trimmed:
        return trimmed
    combined = f"{trimmed} {marker}"
    if len(combined) > _MAX_STRING_FIELD_LEN:
        prefix_len = _MAX_STRING_FIELD_LEN - len(marker) - 1
        combined = f"{trimmed[:prefix_len]} {marker}"
    return combined


def _format_single_value(
    fspec: OneCFieldSpec,
    raw_value: Any,
    *,
    default_currency: str = "RUB",
) -> tuple[Any, str]:
    """Validate and normalize a single field value against `OneCFieldSpec`."""
    if isinstance(raw_value, float):
        raise OneCCommandError("float_forbidden")

    if raw_value is None:
        if not fspec.nullable:
            raise OneCCommandError("invalid_field_value")
        return None, "—"

    ltype = fspec.logical_type
    if fspec.is_monetary:
        if isinstance(raw_value, bool):
            raise OneCCommandError("invalid_field_value")
        try:
            mon = MonetaryValue(amount=raw_value, currency=default_currency)
        except OneCQueryError as exc:
            raise OneCCommandError(exc.code) from None
        return f"{mon.amount:.2f}", mon.formatted

    if ltype in {"decimal", "numeric"}:
        if isinstance(raw_value, bool):
            raise OneCCommandError("invalid_field_value")
        try:
            dec = to_exact_decimal(raw_value)
        except (CalculationInputError, InvalidOperation):
            raise OneCCommandError("invalid_field_value") from None
        if dec is None or not dec.is_finite():
            raise OneCCommandError("invalid_field_value")
        if fspec.scale is not None and fspec.scale >= 0:
            quant = Decimal("1").scaleb(-fspec.scale)
            dec = dec.quantize(quant)
        return str(dec), str(dec)

    if ltype == "integer":
        if type(raw_value) is not int:
            raise OneCCommandError("invalid_field_value")
        return raw_value, str(raw_value)

    if ltype == "boolean":
        if not isinstance(raw_value, bool):
            raise OneCCommandError("invalid_field_value")
        return raw_value, ("Да" if raw_value else "Нет")

    if ltype == "guid":
        guid_val = normalize_guid(raw_value)
        return guid_val, guid_val

    if ltype == "datetime":
        if isinstance(raw_value, datetime):
            dt = raw_value.astimezone(timezone.utc).replace(tzinfo=None) if raw_value.tzinfo else raw_value
            iso_str = dt.strftime("%Y-%m-%dT%H:%M:%S")
            return iso_str, iso_str
        if isinstance(raw_value, date):
            iso_str = f"{raw_value.strftime('%Y-%m-%d')}T00:00:00"
            return iso_str, iso_str
        if isinstance(raw_value, str):
            try:
                parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
                dt = parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
                iso_str = dt.strftime("%Y-%m-%dT%H:%M:%S")
                return iso_str, iso_str
            except ValueError:
                raise OneCCommandError("invalid_field_value") from None
        raise OneCCommandError("invalid_field_value")

    # Edm.String
    if not isinstance(raw_value, str) or len(raw_value) > _MAX_STRING_FIELD_LEN:
        raise OneCCommandError("invalid_field_value")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw_value):
        raise OneCCommandError("invalid_field_value")
    return raw_value, raw_value


def validate_and_normalize_write_fields(
    entity_spec: OneCEntitySetSpec,
    raw_fields: Mapping[str, Any],
    *,
    allowed_fields: frozenset[str] | None = None,
    default_currency: str = "RUB",
) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate a mapping of write fields against `$metadata` and the connection allowlist."""
    if not isinstance(raw_fields, Mapping) or not raw_fields:
        raise OneCCommandError("invalid_fields")
    if not isinstance(default_currency, str) or not _CURRENCY_RE.match(default_currency.strip().upper()):
        raise OneCCommandError("invalid_currency")
    curr = default_currency.strip().upper()

    normalized: dict[str, Any] = {}
    formatted: dict[str, str] = {}

    for fname in sorted(raw_fields.keys()):
        if not is_valid_identifier(fname):
            raise OneCCommandError("invalid_fields")
        if fname in SYSTEM_READONLY_FIELDS or fname in entity_spec.key_fields:
            raise OneCCommandError("readonly_field_forbidden")
        if allowed_fields is not None and fname not in allowed_fields:
            raise OneCCommandError("field_not_allowed")
        if fname not in entity_spec.fields:
            raise OneCCommandError("field_not_found")

        fspec = entity_spec.fields[fname]
        norm_val, fmt_val = _format_single_value(fspec, raw_fields[fname], default_currency=curr)
        normalized[fname] = norm_val
        formatted[fname] = fmt_val

    return normalized, formatted


def extract_data_version(remote_obj: Mapping[str, Any]) -> str:
    """Extract `DataVersion` from a 1C OData object representation."""
    if not isinstance(remote_obj, Mapping):
        raise OneCCommandError("invalid_response")
    raw_version = remote_obj.get("DataVersion") or remote_obj.get("@odata.etag") or remote_obj.get("odata.etag")
    if not isinstance(raw_version, str) or not raw_version.strip():
        raise OneCCommandError("missing_data_version")
    return raw_version.strip()


def verify_data_version_unchanged(
    *,
    expected_data_version: str,
    remote_obj: Mapping[str, Any],
) -> str:
    """Verify that the live 1C object's `DataVersion` matches `expected_data_version`."""
    if not isinstance(expected_data_version, str) or not expected_data_version.strip():
        raise OneCCommandError("missing_data_version")
    live_version = extract_data_version(remote_obj)
    if live_version != expected_data_version.strip():
        raise OneCCommandError("data_version_conflict")
    return live_version


def compute_catalog_diff(
    entity_spec: OneCEntitySetSpec,
    current_object: Mapping[str, Any],
    normalized_changes: Mapping[str, Any],
    formatted_changes: Mapping[str, str],
    *,
    default_currency: str = "RUB",
) -> tuple[OneCFieldDiff, ...]:
    """Compute human-readable field-by-field diff for a `Catalog_*` update."""
    if not isinstance(current_object, Mapping):
        raise OneCCommandError("invalid_response")

    diffs: list[OneCFieldDiff] = []
    for fname in sorted(normalized_changes.keys()):
        fspec = entity_spec.fields[fname]
        new_val = normalized_changes[fname]
        fmt_new = formatted_changes[fname]

        raw_old = current_object.get(fname)
        if raw_old is None:
            old_val = None
            fmt_old = "—"
        else:
            try:
                old_val, fmt_old = _format_single_value(fspec, raw_old, default_currency=default_currency)
            except OneCCommandError:
                old_val = str(raw_old)
                fmt_old = str(raw_old)

        if old_val != new_val:
            diffs.append(
                OneCFieldDiff(
                    field=fname,
                    edm_type=fspec.edm_type,
                    old_value=old_val,
                    new_value=new_val,
                    formatted_old=fmt_old,
                    formatted_new=fmt_new,
                )
            )

    if not diffs:
        raise OneCCommandError("no_effective_changes")
    return tuple(diffs)


def parse_odata_single_object(body: bytes) -> dict[str, Any]:
    """Parse a single 1C OData JSON entity response with `parse_float=Decimal`."""
    if not isinstance(body, (bytes, bytearray)) or not body:
        raise OneCCommandError("invalid_response")
    if len(body) > MAX_BODY_BYTES:
        raise OneCCommandError("invalid_response")
    try:
        payload = json.loads(bytes(body).decode("utf-8-sig"), parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError, InvalidOperation):
        raise OneCCommandError("invalid_response") from None

    if not isinstance(payload, dict):
        raise OneCCommandError("invalid_response")

    if isinstance(payload.get("d"), dict) and "Ref_Key" in payload["d"]:
        return dict(payload["d"])
    if "value" in payload and isinstance(payload["value"], list):
        if not payload["value"]:
            raise OneCCommandError("entity_not_found")
        first = payload["value"][0]
        if not isinstance(first, dict):
            raise OneCCommandError("invalid_response")
        return dict(first)
    return dict(payload)


def parse_odata_object_list(body: bytes) -> list[dict[str, Any]]:
    """Parse a 1C OData JSON list response (`{"value": [...]}`) with `parse_float=Decimal`."""
    if not isinstance(body, (bytes, bytearray)) or not body:
        raise OneCCommandError("invalid_response")
    if len(body) > MAX_BODY_BYTES:
        raise OneCCommandError("invalid_response")
    try:
        payload = json.loads(bytes(body).decode("utf-8-sig"), parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError, InvalidOperation):
        raise OneCCommandError("invalid_response") from None

    if not isinstance(payload, dict):
        raise OneCCommandError("invalid_response")
    if isinstance(payload.get("value"), list):
        return [dict(item) for item in payload["value"] if isinstance(item, Mapping)]
    if isinstance(payload.get("d"), dict) and isinstance(payload["d"].get("results"), list):
        return [dict(item) for item in payload["d"]["results"] if isinstance(item, Mapping)]
    raise OneCCommandError("invalid_response")


async def send_odata_command_request(
    endpoint: OneCEndpoint,
    *,
    method: bytes,
    target_path: str,
    login: str,
    password: str,
    limiter,
    budget,
    json_body: bytes | None = None,
    if_match: str | None = None,
    accept_header: bytes = b"application/json;odata=minimalmetadata",
    resolver: Resolver = default_dns_resolver,
) -> bytes:
    """Perform one IP-pinned HTTPS request (`GET`, `POST`, or `PATCH`) against 1C OData.

    When `method` is a mutation (`POST` or `PATCH`) and a transport timeout occurs,
    propagates `TimeoutError` so the command ledger transitions the command to `unknown`
    and records an unknown audit outcome instead of blindly retrying.
    """
    if not isinstance(login, str) or not isinstance(password, str) or not login or not password or ":" in login:
        raise OneCCommandError("invalid_credentials")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in login + password) or "SSLKEYLOGFILE" in os.environ:
        raise OneCCommandError("invalid_credentials")
    if (
        not isinstance(target_path, str)
        or not target_path.startswith("/")
        or any(ord(c) < 33 or ord(c) == 127 for c in target_path)
    ):
        raise OneCCommandError("invalid_command_target")

    writer = None
    request_sent = False
    definite_refusal = False
    is_mutation = method in (b"POST", b"PATCH")
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
                headers: list[tuple[bytes, bytes]] = [
                    (b"host", host.encode("ascii")),
                    (b"authorization", b"Basic " + auth),
                    (b"accept", accept_header),
                    (b"accept-encoding", b"identity"),
                    (b"connection", b"close"),
                ]
                if if_match:
                    headers.append((b"if-match", if_match.encode("ascii")))
                if json_body is not None:
                    headers.append((b"content-type", b"application/json; charset=utf-8"))
                    headers.append((b"content-length", str(len(json_body)).encode("ascii")))
                elif is_mutation:
                    headers.append((b"content-length", b"0"))

                protocol = h11.Connection(h11.CLIENT, max_incomplete_event_size=16384)
                wire = bytearray(
                    protocol.send(
                        h11.Request(
                            method=method,
                            target=quote(target_path, safe="/%$?:&=@(),'-._~").encode("ascii"),
                            headers=headers,
                        )
                    )
                )
                if json_body is not None:
                    wire.extend(protocol.send(h11.Data(data=json_body)))
                wire.extend(protocol.send(h11.EndOfMessage()))
                # A write may reach 1C even if write/drain or reading the response fails.
                request_sent = True
                writer.write(bytes(wire))
                await writer.drain()

                status, body = None, bytearray()
                while True:
                    event = protocol.next_event()
                    if event is h11.NEED_DATA:
                        chunk = await reader.read(16384)
                        protocol.receive_data(chunk)
                        if not chunk and status is None:
                            raise OneCCommandError("connection_failed")
                    elif isinstance(event, h11.Response):
                        status = event.status_code
                        definite_refusal = 300 <= status < 500
                        if 300 <= status < 400:
                            raise OneCCommandError("redirect_forbidden")
                        if status == 400:
                            raise OneCCommandError("remote_validation_failed")
                        if status in (401, 403):
                            raise OneCCommandError("authentication_failed")
                        if status == 404:
                            raise OneCCommandError("entity_not_found")
                        if status in (409, 412):
                            raise OneCCommandError("data_version_conflict")
                        if status in (429, 503):
                            retry_after = next(
                                (v.decode("ascii", "ignore") for k, v in event.headers if k.lower() == b"retry-after"),
                                None,
                            )
                            signal = provider_signal("onec", status, {"retry-after": retry_after})
                            await limiter.note_signal(budget, signal)
                            raise OneCCommandError("rate_limited" if status == 429 else "service_unavailable")
                        if status not in (200, 201, 204):
                            raise OneCCommandError("service_unavailable")
                        if any(k.lower() == b"content-encoding" and v.lower() != b"identity" for k, v in event.headers):
                            raise OneCCommandError("unsupported_encoding")
                    elif isinstance(event, h11.Data):
                        body.extend(event.data)
                        if len(body) > MAX_BODY_BYTES:
                            raise OneCCommandError("response_too_large")
                    elif isinstance(event, h11.EndOfMessage):
                        return bytes(body)
                    else:
                        raise OneCCommandError("invalid_response")
    except OneCCommandError:
        if is_mutation and request_sent and not definite_refusal:
            raise TimeoutError("1C write confirmation unavailable") from None
        raise
    except OneCEndpointError as exc:
        raise OneCCommandError(exc.code) from None
    except ProviderDeferred:
        raise OneCCommandError("rate_limited") from None
    except BudgetConnectionUnavailable:
        raise ConnectionDenied() from None
    except (TimeoutError, asyncio.TimeoutError):
        if is_mutation:
            raise
        raise OneCCommandError("connection_failed") from None
    except (OSError, ValueError, UnicodeError, h11.ProtocolError):
        if is_mutation and request_sent and not definite_refusal:
            raise TimeoutError("1C write confirmation unavailable") from None
        raise OneCCommandError("connection_failed") from None
    finally:
        if writer is not None:
            writer.close()
            try:
                async with asyncio.timeout(1):
                    await writer.wait_closed()
            except (OSError, TimeoutError):
                pass


def _to_command_record(
    serialized: Mapping[str, Any],
    *,
    cmd: BusinessCommand | None = None,
    fallback_payload: Mapping[str, Any] | None = None,
) -> OneCCommandRecord:
    cmd_payload = dict(getattr(cmd, "payload", None) or {}) if cmd is not None else {}
    payload = dict(serialized.get("payload") or cmd_payload or fallback_payload or {})
    preview_snapshot = dict(serialized.get("preview_snapshot") or {})
    targets = list(serialized.get("targets") or [])
    first_target = targets[0] if targets else {}
    target_refs = list(serialized.get("target_refs") or [])
    target_ref = str(first_target.get("target_ref") or (target_refs[0] if target_refs else ""))
    created_at = (
        serialized.get("created_at")
        or (getattr(cmd, "created_at", None) if cmd is not None else None)
        or datetime.now(timezone.utc)
    )
    return OneCCommandRecord(
        id=int(serialized["id"]),
        tenant_id=int(serialized["tenant_id"]),
        actor_id=int(serialized["actor_user_id"]),
        connection_id=int(serialized["connection_id"] or payload.get("connection_id") or 0),
        capability=str(serialized["capability"]),
        approval_policy=str(serialized["approval_policy"]),
        idempotency_key=str(serialized["idempotency_key"]),
        external_ref=str(payload.get("external_ref") or preview_snapshot.get("external_ref") or ""),
        entity_set=str(payload.get("entity_set") or preview_snapshot.get("entity_set") or ""),
        target_ref=target_ref,
        target_guid=payload.get("target_guid") if "target_guid" in payload else preview_snapshot.get("target_guid"),
        expected_data_version=(
            payload.get("expected_data_version")
            if "expected_data_version" in payload
            else preview_snapshot.get("expected_data_version")
        ),
        payload_hash=str(serialized["payload_hash"]),
        approved_payload_hash=serialized.get("approved_payload_hash"),
        state=str(serialized["state"]),
        preview_snapshot=preview_snapshot,
        provider_receipt=first_target.get("provider_receipt"),
        error_code=serialized.get("error_code") or first_target.get("error_code"),
        reconcile_hint=serialized.get("reconcile_hint"),
        approved_by=serialized.get("approved_user_id"),
        approved_at=serialized.get("approved_at"),
        approval_expires_at=serialized.get("approval_expires_at"),
        executed_at=serialized.get("executed_at"),
        created_at=created_at,
    )


ODataTransportCallable = Callable[..., Awaitable[bytes]]


class OneCCommandService:
    """Authorized 1C write command service with preview, per-item approval, DataVersion check, and audit."""

    def __init__(
        self,
        *,
        ledger: BusinessCommandLedger | None = None,
        transport: ODataTransportCallable | None = None,
    ):
        self._sessions = BaseManager(WorkspaceConnection)
        self._hosts = WorkspaceOutboundHostManager()
        self._limiter = RateLimiter()
        self._ledger = ledger or BusinessCommandLedger()
        self._transport = transport or send_odata_command_request

    async def _load_authorized_write_connection(
        self,
        *,
        session,
        tenant_id: int,
        actor_id: int,
        connection_id: int,
        capability: str,
    ):
        """Load and verify a 1C write connection (`auth_method == 'odata_write_credentials'`).

        Strictly rejects read-only `odata_credentials` connections with
        `OneCCommandError("write_connection_required")`.
        """
        if type(connection_id) is not int or connection_id <= 0:
            raise ConnectionDenied()

        await _authorize(session, tenant_id, actor_id, write=True)
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
        if row is None or not row.is_active or row.provider_id != PROVIDER or row.schema_version != SCHEMA_VERSION:
            raise ConnectionDenied()

        if row.auth_method == READ_ONLY_METHOD or row.auth_method != WRITE_METHOD:
            raise OneCCommandError("write_connection_required")

        if row.state != "verified":
            raise ConnectionDenied()

        config = dict(row.config or {})
        enabled, disable_reason = is_onec_write_capability_enabled(capability, connection_config=config)
        if not enabled:
            raise OneCCommandError(disable_reason or "capability_disabled")

        endpoint = _stored_endpoint(config)
        if endpoint.interface != "odata_standard":
            raise OneCCommandError("unsupported_interface")

        await self._hosts.require_approved(session=session, tenant_id=tenant_id, actor_id=actor_id, host=endpoint.host)
        secret = await session.get(WorkspaceConnectionSecret, connection_id)
        ciphertext = secret.secret_encrypted if secret is not None else ""
        try:
            credentials = open_basic_credentials(ciphertext)
        except OneCCredentialError as exc:
            raise OneCCommandError(exc.code) from None

        try:
            allowed_sets, allowed_fields = extract_connection_allowlist(config)
        except OneCMetadataError as exc:
            raise OneCCommandError(exc.code) from None

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
            config,
        )

    async def _resolve_catalog(
        self,
        *,
        catalog: OneCMetadataCatalog | None,
        endpoint: OneCEndpoint,
        login: str,
        password: str,
        account_key: str,
        connection_id: int,
        expected_sha256: str | None,
        resolver: Resolver = default_dns_resolver,
    ) -> OneCMetadataCatalog:
        if catalog is not None:
            if expected_sha256 is not None and catalog.metadata_sha256 != expected_sha256:
                raise OneCCommandError("metadata_hash_mismatch")
            return catalog

        budget = budget_for(PROVIDER, account_key, connection_id=connection_id)
        xml_bytes = await self._transport(
            endpoint,
            method=b"GET",
            target_path=quote(endpoint.health_path, safe="/%$:@-._~"),
            login=login,
            password=password,
            limiter=self._limiter,
            budget=budget,
            accept_header=b"application/xml",
            resolver=resolver,
        )
        try:
            parsed = parse_odata_metadata_xml(xml_bytes)
        except OneCMetadataError as exc:
            raise OneCCommandError(exc.code) from None
        if expected_sha256 is not None and parsed.metadata_sha256 != expected_sha256:
            raise OneCCommandError("metadata_hash_mismatch")
        return parsed

    async def _fetch_remote_object_by_guid(
        self,
        *,
        endpoint: OneCEndpoint,
        entity_set: str,
        target_guid: str,
        login: str,
        password: str,
        account_key: str,
        connection_id: int,
        resolver: Resolver = default_dns_resolver,
    ) -> dict[str, Any]:
        normalized_base = endpoint.base_path.rstrip("/")
        path = f"{normalized_base}/odata/standard.odata/{quote(entity_set, safe='')}(guid'{target_guid}')?$format=json"
        budget = budget_for(PROVIDER, account_key, connection_id=connection_id)
        raw_bytes = await self._transport(
            endpoint,
            method=b"GET",
            target_path=path,
            login=login,
            password=password,
            limiter=self._limiter,
            budget=budget,
            resolver=resolver,
        )
        return parse_odata_single_object(raw_bytes)

    async def _find_remote_object_by_external_ref(
        self,
        *,
        endpoint: OneCEndpoint,
        entity_set: str,
        comment_field: str,
        comment_value: str,
        login: str,
        password: str,
        account_key: str,
        connection_id: int,
        resolver: Resolver = default_dns_resolver,
    ) -> dict[str, Any] | None:
        normalized_base = endpoint.base_path.rstrip("/")
        escaped = comment_value.replace("'", "''")
        filter_expr = f"{comment_field} eq '{escaped}'"
        path = (
            f"{normalized_base}/odata/standard.odata/{quote(entity_set, safe='')}"
            f"?$format=json&$top=1&$filter={quote(filter_expr, safe='(),=')}"
        )
        budget = budget_for(PROVIDER, account_key, connection_id=connection_id)
        raw_bytes = await self._transport(
            endpoint,
            method=b"GET",
            target_path=path,
            login=login,
            password=password,
            limiter=self._limiter,
            budget=budget,
            resolver=resolver,
        )
        items = parse_odata_object_list(raw_bytes)
        return items[0] if items else None

    async def preview_command(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        connection_id: int,
        request: OneCWritePreviewRequest,
        catalog: OneCMetadataCatalog | None = None,
        session=None,
        resolver: Resolver = default_dns_resolver,
    ) -> OneCCommandRecord:
        """Validate a 1C write request against `$metadata`, capture `DataVersion` and diff, and record preview."""
        spec = get_onec_write_capability(request.capability)
        enabled, disable_reason = is_onec_write_capability_enabled(spec.code)
        if not enabled:
            raise OneCCommandError(disable_reason or "capability_disabled")

        idempotency_key = validate_idempotency_key(request.idempotency_key)
        if not is_valid_identifier(request.entity_set):
            raise OneCCommandError("entity_not_allowed")
        if not isinstance(request.posting_mode_operational, bool):
            raise OneCCommandError("invalid_posting_mode")

        if session is None:
            async with self._sessions._get_owned_session() as owned_session:
                return await self.preview_command(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    connection_id=connection_id,
                    request=request,
                    catalog=catalog,
                    session=owned_session,
                    resolver=resolver,
                )

        (
            endpoint,
            credentials,
            account_key,
            allowed_sets,
            allowed_fields_map,
            expected_sha256,
            _config,
        ) = await self._load_authorized_write_connection(
            session=session,
            tenant_id=tenant_id,
            actor_id=actor_id,
            connection_id=connection_id,
            capability=spec.code,
        )

        if not allowed_sets or request.entity_set not in allowed_sets:
            raise OneCCommandError("entity_not_allowed")

        with tenant_scope(tenant_id):
            resolved_catalog = await self._resolve_catalog(
                catalog=catalog,
                endpoint=endpoint,
                login=credentials.login,
                password=credentials.password,
                account_key=account_key,
                connection_id=connection_id,
                expected_sha256=expected_sha256,
                resolver=resolver,
            )

        try:
            entity_spec = resolved_catalog.get_entity_set(request.entity_set)
        except OneCMetadataError as exc:
            raise OneCCommandError(exc.code) from None

        per_entity_allowed = allowed_fields_map.get(entity_spec.name)
        external_ref = build_external_idempotency_ref(
            tenant_id=tenant_id,
            connection_id=connection_id,
            capability=spec.code,
            idempotency_key=idempotency_key,
        )

        if spec.code == CAPABILITY_DOCUMENT_CREATE:
            if entity_spec.kind != "document":
                raise OneCCommandError("invalid_entity_kind")
            if request.target_guid is not None:
                raise OneCCommandError("invalid_fields")

            normalized_fields, formatted_fields = validate_and_normalize_write_fields(
                entity_spec,
                request.fields,
                allowed_fields=per_entity_allowed,
                default_currency=request.default_currency,
            )
            comment_field = resolve_idempotency_comment_field(entity_spec, allowed_fields=per_entity_allowed)
            if comment_field is None:
                raise OneCCommandError("idempotency_field_unavailable")

            existing_comment = normalized_fields.get(comment_field)
            tagged_comment = attach_external_ref_to_comment(
                str(existing_comment) if existing_comment is not None else None,
                external_ref,
            )
            normalized_fields[comment_field] = tagged_comment
            formatted_fields[comment_field] = tagged_comment

            target_ref = f"onec:{entity_spec.name}:new:{external_ref}"
            payload = {
                "connection_id": connection_id,
                "capability": spec.code,
                "entity_set": entity_spec.name,
                "target_guid": None,
                "expected_data_version": None,
                "external_ref": external_ref,
                "idempotency_field": comment_field,
                "fields": normalized_fields,
                "metadata_sha256": resolved_catalog.metadata_sha256,
            }
            preview_snapshot = {
                "summary": f"Создание документа {entity_spec.name} (черновик без автопроведения)",
                "operation": "document_create",
                "entity_set": entity_spec.name,
                "target_guid": None,
                "expected_data_version": None,
                "external_ref": external_ref,
                "idempotency_field": comment_field,
                "formatted_fields": formatted_fields,
                "diffs": [],
            }

        elif spec.code == CAPABILITY_DOCUMENT_POST:
            if entity_spec.kind != "document":
                raise OneCCommandError("invalid_entity_kind")
            target_guid = normalize_guid(request.target_guid)
            with tenant_scope(tenant_id):
                remote_doc = await self._fetch_remote_object_by_guid(
                    endpoint=endpoint,
                    entity_set=entity_spec.name,
                    target_guid=target_guid,
                    login=credentials.login,
                    password=credentials.password,
                    account_key=account_key,
                    connection_id=connection_id,
                    resolver=resolver,
                )
            if remote_doc.get("DeletionMark") is True:
                raise OneCCommandError("document_deleted")
            if remote_doc.get("Posted") is True:
                raise OneCCommandError("document_already_posted")

            data_version = extract_data_version(remote_doc)
            doc_number = str(remote_doc.get("Number") or remote_doc.get("Номер") or target_guid)
            doc_date = str(remote_doc.get("Date") or remote_doc.get("Дата") or "")
            target_ref = f"onec:{entity_spec.name}:{target_guid}"

            post_diff = OneCFieldDiff(
                field="Posted",
                edm_type="Edm.Boolean",
                old_value=False,
                new_value=True,
                formatted_old="Нет",
                formatted_new="Да",
            )
            payload = {
                "connection_id": connection_id,
                "capability": spec.code,
                "entity_set": entity_spec.name,
                "target_guid": target_guid,
                "expected_data_version": data_version,
                "posting_mode_operational": request.posting_mode_operational,
                "external_ref": external_ref,
                "metadata_sha256": resolved_catalog.metadata_sha256,
            }
            preview_snapshot = {
                "summary": f"Проведение документа {entity_spec.name} № {doc_number}",
                "operation": "document_post",
                "entity_set": entity_spec.name,
                "target_guid": target_guid,
                "document_number": doc_number,
                "document_date": doc_date,
                "expected_data_version": data_version,
                "posting_mode_operational": request.posting_mode_operational,
                "external_ref": external_ref,
                "diffs": [post_diff.to_dict()],
            }

        else:  # CAPABILITY_CATALOG_UPDATE
            if entity_spec.kind != "catalog":
                raise OneCCommandError("invalid_entity_kind")
            target_guid = normalize_guid(request.target_guid)
            normalized_changes, formatted_changes = validate_and_normalize_write_fields(
                entity_spec,
                request.fields,
                allowed_fields=per_entity_allowed,
                default_currency=request.default_currency,
            )
            with tenant_scope(tenant_id):
                remote_item = await self._fetch_remote_object_by_guid(
                    endpoint=endpoint,
                    entity_set=entity_spec.name,
                    target_guid=target_guid,
                    login=credentials.login,
                    password=credentials.password,
                    account_key=account_key,
                    connection_id=connection_id,
                    resolver=resolver,
                )
            if remote_item.get("DeletionMark") is True:
                raise OneCCommandError("document_deleted")

            data_version = extract_data_version(remote_item)
            diffs = compute_catalog_diff(
                entity_spec,
                remote_item,
                normalized_changes,
                formatted_changes,
                default_currency=request.default_currency,
            )
            effective_changes = {d.field: d.new_value for d in diffs}
            comment_field = resolve_idempotency_comment_field(entity_spec, allowed_fields=per_entity_allowed)
            target_ref = f"onec:{entity_spec.name}:{target_guid}"
            item_label = str(
                remote_item.get("Description")
                or remote_item.get("Наименование")
                or remote_item.get("Code")
                or target_guid
            )
            payload = {
                "connection_id": connection_id,
                "capability": spec.code,
                "entity_set": entity_spec.name,
                "target_guid": target_guid,
                "expected_data_version": data_version,
                "external_ref": external_ref,
                "idempotency_field": comment_field,
                "changes": effective_changes,
                "metadata_sha256": resolved_catalog.metadata_sha256,
            }
            preview_snapshot = {
                "summary": f"Обновление элемента справочника {entity_spec.name} ({item_label})",
                "operation": "catalog_update",
                "entity_set": entity_spec.name,
                "target_guid": target_guid,
                "item_label": item_label,
                "expected_data_version": data_version,
                "external_ref": external_ref,
                "diffs": [d.to_dict() for d in diffs],
            }

        prev_user = get_current_user()
        prev_tenant = current_tenant_id()
        try:
            if prev_user is None or getattr(prev_user, "id", None) != actor_id:
                from types import SimpleNamespace

                set_current_user(SimpleNamespace(id=actor_id, is_active=True))
            with tenant_scope(tenant_id):
                try:
                    serialized = await self._ledger.preview_command(
                        capability=spec.code,
                        idempotency_key=idempotency_key,
                        target_refs=[target_ref],
                        payload=payload,
                        preview_snapshot=preview_snapshot,
                        connection_id=connection_id,
                        session=session,
                    )
                except CommandConflict:
                    raise OneCCommandError("command_state_conflict") from None
                except CommandDenied:
                    raise OneCCommandError("capability_disabled") from None
        finally:
            if prev_user is not None or prev_tenant is not None:
                set_current_user(prev_user)

        return _to_command_record(serialized, fallback_payload=payload)

    async def approve_command(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        command_id: int,
        expected_payload_hash: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        session=None,
    ) -> OneCCommandRecord:
        """Record per-item approval bound to actor, tenant, `expected_payload_hash`, and interactive TTL."""
        if type(command_id) is not int or command_id <= 0:
            raise OneCCommandError("command_not_found")
        if type(ttl_seconds) is not int or not (MIN_TTL_SECONDS <= ttl_seconds <= MAX_INTERACTIVE_TTL_SECONDS):
            raise OneCCommandError("invalid_ttl")
        if not isinstance(expected_payload_hash, str) or not expected_payload_hash.startswith("sha256:"):
            raise OneCCommandError("approval_hash_mismatch")

        if session is None:
            async with self._sessions._get_owned_session() as owned_session:
                return await self.approve_command(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    command_id=command_id,
                    expected_payload_hash=expected_payload_hash,
                    ttl_seconds=ttl_seconds,
                    session=owned_session,
                )

        cmd = (
            await session.execute(
                select(BusinessCommand)
                .where(BusinessCommand.tenant_id == tenant_id, BusinessCommand.id == command_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if cmd is None or cmd.capability not in ONEC_WRITE_CAPABILITIES:
            raise OneCCommandError("command_not_found")

        if cmd.connection_id is None:
            raise ConnectionDenied()

        await self._load_authorized_write_connection(
            session=session,
            tenant_id=tenant_id,
            actor_id=actor_id,
            connection_id=cmd.connection_id,
            capability=cmd.capability,
        )

        prev_user = get_current_user()
        try:
            if prev_user is None or getattr(prev_user, "id", None) != actor_id:
                from types import SimpleNamespace

                set_current_user(SimpleNamespace(id=actor_id, is_active=True))
            with tenant_scope(tenant_id):
                try:
                    serialized = await self._ledger.approve_command(
                        command_id,
                        expected_payload_hash=expected_payload_hash,
                        ttl_seconds=ttl_seconds,
                        session=session,
                    )
                except CommandNotFound:
                    raise OneCCommandError("command_not_found") from None
                except CommandConflict:
                    raise OneCCommandError("command_state_conflict") from None
                except CommandDenied as exc:
                    msg = str(exc).lower()
                    if "hash" in msg:
                        raise OneCCommandError("approval_hash_mismatch") from None
                    raise OneCCommandError("capability_disabled") from None

                await ActionAuditEvent.objects.record_success(
                    session=session,
                    actor_kind="user",
                    actor_id=actor_id,
                    action="onec.command_approve",
                    resource_kind="command",
                    resource_id=command_id,
                )
        finally:
            set_current_user(prev_user)

        return _to_command_record(serialized, cmd=cmd)

    async def execute_command(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        command_id: int,
        now: datetime | None = None,
        session=None,
        resolver: Resolver = default_dns_resolver,
    ) -> OneCCommandRecord:
        """Execute an approved 1C write command after rechecking DataVersion, write connection, and kill switch.

        Terminal state transitions (`failed`, `unknown`, `expired`) and `error_code` are
        committed to the database before raising `OneCCommandError` so that `_get_owned_session`
        never rolls back the ledger's terminal state on conflict or timeout.
        """
        if type(command_id) is not int or command_id <= 0:
            raise OneCCommandError("command_not_found")

        record: OneCCommandRecord | None = None
        deferred_error: OneCCommandError | None = None
        record_unknown_audit = False

        if session is None:
            async with self._sessions._get_owned_session() as owned_session:
                record, deferred_error, record_unknown_audit = await self._execute_command_in_session(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    command_id=command_id,
                    now=now,
                    session=owned_session,
                    resolver=resolver,
                )
        else:
            record, deferred_error, record_unknown_audit = await self._execute_command_in_session(
                tenant_id=tenant_id,
                actor_id=actor_id,
                command_id=command_id,
                now=now,
                session=session,
                resolver=resolver,
            )

        if record_unknown_audit:
            prev_user = get_current_user()
            try:
                if prev_user is None or getattr(prev_user, "id", None) != actor_id:
                    from types import SimpleNamespace

                    set_current_user(SimpleNamespace(id=actor_id, is_active=True))
                with tenant_scope(tenant_id):
                    await ActionAuditEvent.objects.record_unknown(
                        actor_kind="user",
                        actor_id=actor_id,
                        action="onec.command_execute",
                        resource_kind="command",
                        resource_id=command_id,
                        reason_code="operation_unconfirmed",
                    )
            finally:
                set_current_user(prev_user)

        if deferred_error is not None:
            raise deferred_error
        assert record is not None
        return record

    async def _execute_command_in_session(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        command_id: int,
        now: datetime | None,
        session,
        resolver: Resolver,
    ) -> tuple[OneCCommandRecord | None, OneCCommandError | None, bool]:
        cmd = (
            await session.execute(
                select(BusinessCommand)
                .where(BusinessCommand.tenant_id == tenant_id, BusinessCommand.id == command_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if cmd is None or cmd.capability not in ONEC_WRITE_CAPABILITIES:
            raise OneCCommandError("command_not_found")
        if cmd.connection_id is None:
            raise ConnectionDenied()

        (
            endpoint,
            credentials,
            account_key,
            allowed_sets,
            allowed_fields,
            expected_sha256,
            _config,
        ) = await self._load_authorized_write_connection(
            session=session,
            tenant_id=tenant_id,
            actor_id=actor_id,
            connection_id=cmd.connection_id,
            capability=cmd.capability,
        )

        payload = dict(cmd.payload or {})
        entity_set = str(payload.get("entity_set") or "")
        conflict_error: OneCCommandError | None = None

        async def _onec_write_adapter(
            locked_cmd: BusinessCommand,
            locked_targets: list[BusinessCommandTarget],
        ) -> dict[str, str]:
            nonlocal conflict_error
            cmd_payload = dict(locked_cmd.payload or {})
            cap = locked_cmd.capability
            ent_set = str(cmd_payload["entity_set"])
            ext_ref = str(cmd_payload["external_ref"])
            target_ref_str = locked_targets[0].target_ref if locked_targets else locked_cmd.target_refs[0]
            normalized_base = endpoint.base_path.rstrip("/")
            budget = budget_for(PROVIDER, account_key, connection_id=locked_cmd.connection_id)

            try:
                if not entity_set or entity_set not in allowed_sets:
                    raise OneCCommandError("entity_not_allowed")
                if expected_sha256 is not None and cmd_payload.get("metadata_sha256") != expected_sha256:
                    raise OneCCommandError("metadata_hash_mismatch")
                outgoing_fields = (
                    cmd_payload.get("fields", {})
                    if cap == CAPABILITY_DOCUMENT_CREATE
                    else cmd_payload.get("changes", {})
                )
                permitted_fields = allowed_fields.get(ent_set)
                if permitted_fields is not None and not set(outgoing_fields).issubset(permitted_fields):
                    raise OneCCommandError("field_not_allowed")
                if cap == CAPABILITY_DOCUMENT_CREATE:
                    comment_field = str(cmd_payload.get("idempotency_field") or "Комментарий")
                    fields_dict = dict(cmd_payload.get("fields") or {})
                    comment_value = str(fields_dict.get(comment_field) or f"[{ext_ref}]")

                    existing_doc = await self._find_remote_object_by_external_ref(
                        endpoint=endpoint,
                        entity_set=ent_set,
                        comment_field=comment_field,
                        comment_value=comment_value,
                        login=credentials.login,
                        password=credentials.password,
                        account_key=account_key,
                        connection_id=locked_cmd.connection_id,
                        resolver=resolver,
                    )
                    if existing_doc is not None and existing_doc.get("Ref_Key"):
                        ref_key = str(existing_doc["Ref_Key"])
                        return {target_ref_str: f"onec:document:{ent_set}:{ref_key}"}

                    post_path = f"{normalized_base}/odata/standard.odata/{quote(ent_set, safe='')}?$format=json"
                    body_bytes = json.dumps(fields_dict, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    resp_bytes = await self._transport(
                        endpoint,
                        method=b"POST",
                        target_path=post_path,
                        login=credentials.login,
                        password=credentials.password,
                        limiter=self._limiter,
                        budget=budget,
                        json_body=body_bytes,
                        resolver=resolver,
                    )
                    try:
                        created = parse_odata_single_object(resp_bytes) if resp_bytes else {}
                    except OneCCommandError:
                        raise TimeoutError("1C write confirmation unavailable") from None
                    ref_key = str(created.get("Ref_Key") or ext_ref)
                    return {target_ref_str: f"onec:document:{ent_set}:{ref_key}"}

                if cap == CAPABILITY_DOCUMENT_POST:
                    t_guid = normalize_guid(cmd_payload.get("target_guid"))
                    expected_ver = str(cmd_payload.get("expected_data_version") or "")
                    live_doc = await self._fetch_remote_object_by_guid(
                        endpoint=endpoint,
                        entity_set=ent_set,
                        target_guid=t_guid,
                        login=credentials.login,
                        password=credentials.password,
                        account_key=account_key,
                        connection_id=locked_cmd.connection_id,
                        resolver=resolver,
                    )
                    verify_data_version_unchanged(expected_data_version=expected_ver, remote_obj=live_doc)
                    if live_doc.get("DeletionMark") is True:
                        raise OneCCommandError("document_deleted")
                    if live_doc.get("Posted") is True:
                        return {target_ref_str: f"onec:posted:{ent_set}:{t_guid}:{expected_ver}"}

                    op_mode = "true" if cmd_payload.get("posting_mode_operational") else "false"
                    post_path = (
                        f"{normalized_base}/odata/standard.odata/{quote(ent_set, safe='')}"
                        f"(guid'{t_guid}')/Post?PostingModeOperational={op_mode}&$format=json"
                    )
                    await self._transport(
                        endpoint,
                        method=b"POST",
                        target_path=post_path,
                        login=credentials.login,
                        password=credentials.password,
                        limiter=self._limiter,
                        budget=budget,
                        if_match=expected_ver,
                        resolver=resolver,
                    )
                    return {target_ref_str: f"onec:posted:{ent_set}:{t_guid}:{expected_ver}"}

                # CAPABILITY_CATALOG_UPDATE
                t_guid = normalize_guid(cmd_payload.get("target_guid"))
                expected_ver = str(cmd_payload.get("expected_data_version") or "")
                live_item = await self._fetch_remote_object_by_guid(
                    endpoint=endpoint,
                    entity_set=ent_set,
                    target_guid=t_guid,
                    login=credentials.login,
                    password=credentials.password,
                    account_key=account_key,
                    connection_id=locked_cmd.connection_id,
                    resolver=resolver,
                )
                verify_data_version_unchanged(expected_data_version=expected_ver, remote_obj=live_item)
                if live_item.get("DeletionMark") is True:
                    raise OneCCommandError("document_deleted")

                changes_dict = dict(cmd_payload.get("changes") or {})
                patch_path = (
                    f"{normalized_base}/odata/standard.odata/{quote(ent_set, safe='')}" f"(guid'{t_guid}')?$format=json"
                )
                body_bytes = json.dumps(changes_dict, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                resp_bytes = await self._transport(
                    endpoint,
                    method=b"PATCH",
                    target_path=patch_path,
                    login=credentials.login,
                    password=credentials.password,
                    limiter=self._limiter,
                    budget=budget,
                    json_body=body_bytes,
                    if_match=expected_ver,
                    resolver=resolver,
                )
                try:
                    updated = parse_odata_single_object(resp_bytes) if resp_bytes else {}
                except OneCCommandError:
                    raise TimeoutError("1C write confirmation unavailable") from None
                new_ver = str(updated.get("DataVersion") or expected_ver)
                return {target_ref_str: f"onec:catalog:{ent_set}:{t_guid}:{new_ver}"}
            except OneCCommandError as exc:
                conflict_error = exc
                raise

        prev_user = get_current_user()
        try:
            if prev_user is None or getattr(prev_user, "id", None) != actor_id:
                from types import SimpleNamespace

                set_current_user(SimpleNamespace(id=actor_id, is_active=True))
            with tenant_scope(tenant_id):
                try:
                    serialized = await self._ledger.execute_command(
                        command_id,
                        adapter=_onec_write_adapter,
                        now=now,
                        session=session,
                    )
                except CommandTimeoutUnknown:
                    await session.flush()
                    return None, OneCCommandError("write_timeout_unknown"), True
                except CommandConflict:
                    raise OneCCommandError("command_state_conflict") from None
                except CommandDenied as exc:
                    msg = str(exc).lower()
                    if "expired" in msg:
                        err = OneCCommandError("approval_expired")
                    elif "kill switch" in msg:
                        err = OneCCommandError("kill_switch_active")
                    else:
                        err = OneCCommandError("capability_disabled")
                    await session.flush()
                    return None, err, False

                if conflict_error is not None:
                    cmd.state = "failed"
                    cmd.error_code = conflict_error.code
                    cmd.outcome_summary = {
                        "targets_succeeded": 0,
                        "targets_total": 1,
                        "error_code": conflict_error.code,
                    }
                    targets = await self._ledger._load_targets(session, tenant_id, cmd.id)
                    for t in targets:
                        t.state = "failed"
                        t.error_code = conflict_error.code
                    await session.flush()
                    return None, conflict_error, False

                if str(serialized.get("state") or "") != "succeeded":
                    # The ledger caught an adapter failure that was not a `OneCCommandError`
                    # (for example the rate-budget store being unavailable) and persisted
                    # `state="failed"` with `error_code="adapter_execution_failed"`. Never report
                    # that as a success. The unknown audit event is written after the commit
                    # (`record_unknown_audit`), so the persisted state and its audit agree.
                    await session.flush()
                    return None, OneCCommandError("adapter_execution_failed"), True

                await ActionAuditEvent.objects.record_success(
                    session=session,
                    actor_kind="user",
                    actor_id=actor_id,
                    action="onec.command_execute",
                    resource_kind="command",
                    resource_id=command_id,
                )
        finally:
            set_current_user(prev_user)

        return _to_command_record(serialized, cmd=cmd), None, False

    async def reconcile_command(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        command_id: int,
        session=None,
        resolver: Resolver = default_dns_resolver,
    ) -> OneCCommandRecord:
        """Reconcile an unknown write; failed observations stay unknown with durable audit evidence."""
        if type(command_id) is not int or command_id <= 0:
            raise OneCCommandError("command_not_found")

        if session is None:
            record: OneCCommandRecord | None = None
            deferred_error: OneCCommandError | None = None
            async with self._sessions._get_owned_session() as owned_session:
                try:
                    record = await self.reconcile_command(
                        tenant_id=tenant_id,
                        actor_id=actor_id,
                        command_id=command_id,
                        session=owned_session,
                        resolver=resolver,
                    )
                except OneCCommandError as exc:
                    deferred_error = exc
            if deferred_error is not None:
                raise deferred_error
            assert record is not None
            return record

        cmd = (
            await session.execute(
                select(BusinessCommand)
                .where(BusinessCommand.tenant_id == tenant_id, BusinessCommand.id == command_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if cmd is None or cmd.capability not in ONEC_WRITE_CAPABILITIES:
            raise OneCCommandError("command_not_found")
        if cmd.state != "unknown":
            raise OneCCommandError("command_state_conflict")
        if cmd.connection_id is None:
            raise ConnectionDenied()

        (
            endpoint,
            credentials,
            account_key,
            allowed_sets,
            allowed_fields,
            expected_sha256,
            _config,
        ) = await self._load_authorized_write_connection(
            session=session,
            tenant_id=tenant_id,
            actor_id=actor_id,
            connection_id=cmd.connection_id,
            capability=cmd.capability,
        )

        payload = dict(cmd.payload or {})
        cap = cmd.capability
        ent_set = str(payload["entity_set"])
        permitted_fields = allowed_fields.get(ent_set)
        ext_ref = str(payload["external_ref"])
        target_ref_str = cmd.target_refs[0]

        resolved_state = "failed"
        provider_receipts: dict[str, str] | None = None

        with tenant_scope(tenant_id):
            try:
                if ent_set not in allowed_sets:
                    raise OneCCommandError("entity_not_allowed")
                if expected_sha256 is not None and payload.get("metadata_sha256") != expected_sha256:
                    raise OneCCommandError("metadata_hash_mismatch")
                read_fields = set(payload.get("changes") or {})
                if cap == CAPABILITY_DOCUMENT_CREATE:
                    read_fields.add(str(payload.get("idempotency_field") or "Комментарий"))
                if permitted_fields is not None and not read_fields.issubset(permitted_fields):
                    raise OneCCommandError("field_not_allowed")

                if cap == CAPABILITY_DOCUMENT_CREATE:
                    comment_field = str(payload.get("idempotency_field") or "Комментарий")
                    fields_dict = dict(payload.get("fields") or {})
                    comment_value = str(fields_dict.get(comment_field) or f"[{ext_ref}]")
                    found = await self._find_remote_object_by_external_ref(
                        endpoint=endpoint,
                        entity_set=ent_set,
                        comment_field=comment_field,
                        comment_value=comment_value,
                        login=credentials.login,
                        password=credentials.password,
                        account_key=account_key,
                        connection_id=cmd.connection_id,
                        resolver=resolver,
                    )
                    if found is not None and found.get("Ref_Key"):
                        resolved_state = "succeeded"
                        provider_receipts = {target_ref_str: f"onec:document:{ent_set}:{found['Ref_Key']}"}

                elif cap == CAPABILITY_DOCUMENT_POST:
                    t_guid = normalize_guid(payload.get("target_guid"))
                    live_doc = await self._fetch_remote_object_by_guid(
                        endpoint=endpoint,
                        entity_set=ent_set,
                        target_guid=t_guid,
                        login=credentials.login,
                        password=credentials.password,
                        account_key=account_key,
                        connection_id=cmd.connection_id,
                        resolver=resolver,
                    )
                    if live_doc.get("Posted") is True:
                        resolved_state = "succeeded"
                        live_ver = str(live_doc.get("DataVersion") or "")
                        provider_receipts = {target_ref_str: f"onec:posted:{ent_set}:{t_guid}:{live_ver}"}

                else:  # CAPABILITY_CATALOG_UPDATE
                    t_guid = normalize_guid(payload.get("target_guid"))
                    changes_dict = dict(payload.get("changes") or {})
                    catalog = await self._resolve_catalog(
                        catalog=None,
                        endpoint=endpoint,
                        login=credentials.login,
                        password=credentials.password,
                        account_key=account_key,
                        connection_id=cmd.connection_id,
                        expected_sha256=expected_sha256,
                        resolver=resolver,
                    )
                    entity_spec = catalog.get_entity_set(ent_set)
                    live_item = await self._fetch_remote_object_by_guid(
                        endpoint=endpoint,
                        entity_set=ent_set,
                        target_guid=t_guid,
                        login=credentials.login,
                        password=credentials.password,
                        account_key=account_key,
                        connection_id=cmd.connection_id,
                        resolver=resolver,
                    )
                    if not changes_dict or not set(changes_dict).issubset(live_item):
                        raise OneCCommandError("invalid_response")
                    expected_values, _ = validate_and_normalize_write_fields(
                        entity_spec, changes_dict, allowed_fields=permitted_fields
                    )
                    live_values, _ = validate_and_normalize_write_fields(
                        entity_spec,
                        {key: live_item[key] for key in changes_dict},
                        allowed_fields=permitted_fields,
                    )
                    if live_values == expected_values:
                        resolved_state = "succeeded"
                        live_ver = str(live_item.get("DataVersion") or "")
                        provider_receipts = {target_ref_str: f"onec:catalog:{ent_set}:{t_guid}:{live_ver}"}
            except OneCCommandError as exc:
                # A failed read cannot establish the write's outcome. Keep the command
                # unknown and commit its last observation error before returning it.
                cmd.error_code = exc.code
                await session.flush()
                await ActionAuditEvent.objects.record_unknown(
                    actor_kind="user",
                    actor_id=actor_id,
                    action="onec.command_execute",
                    resource_kind="command",
                    resource_id=command_id,
                    reason_code="operation_unconfirmed",
                )
                raise

        prev_user = get_current_user()
        try:
            if prev_user is None or getattr(prev_user, "id", None) != actor_id:
                from types import SimpleNamespace

                set_current_user(SimpleNamespace(id=actor_id, is_active=True))
            with tenant_scope(tenant_id):
                serialized = await self._ledger.reconcile_unknown(
                    command_id,
                    resolved_state=resolved_state,
                    provider_receipts=provider_receipts,
                    session=session,
                )
                cmd.error_code = None if resolved_state == "succeeded" else "reconciled_failed"
                serialized["error_code"] = cmd.error_code
                await session.flush()
                if resolved_state == "succeeded":
                    await ActionAuditEvent.objects.record_success(
                        session=session,
                        actor_kind="user",
                        actor_id=actor_id,
                        action="onec.command_execute",
                        resource_kind="command",
                        resource_id=command_id,
                    )
        finally:
            set_current_user(prev_user)

        return _to_command_record(serialized, cmd=cmd)

    async def list_audit_log(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        limit: int = 50,
        session=None,
    ) -> tuple[OneCCommandAuditEntry, ...]:
        """Return actor-scoped 1C write command audit log entries from `ActionAuditEvent` and `BusinessCommand`."""
        if type(limit) is not int or not (1 <= limit <= 200):
            raise OneCCommandError("invalid_limit")

        if session is None:
            async with self._sessions._get_owned_session() as owned_session:
                return await self.list_audit_log(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    limit=limit,
                    session=owned_session,
                )

        await _authorize(session, tenant_id, actor_id, require_source_view=True)

        cmd_rows = list(
            (
                await session.execute(
                    select(BusinessCommand)
                    .where(
                        BusinessCommand.tenant_id == tenant_id,
                        BusinessCommand.actor_user_id == actor_id,
                        BusinessCommand.capability.in_(ONEC_WRITE_CAPABILITIES),
                    )
                    .order_by(BusinessCommand.id.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        commands_by_id: dict[str, BusinessCommand] = {str(c.id): c for c in cmd_rows}
        targets_by_cmd_id: dict[int, BusinessCommandTarget] = {}
        if cmd_rows:
            cmd_ids = [c.id for c in cmd_rows]
            t_rows = list(
                (
                    await session.execute(
                        select(BusinessCommandTarget).where(
                            BusinessCommandTarget.tenant_id == tenant_id,
                            BusinessCommandTarget.command_id.in_(cmd_ids),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for t in t_rows:
                targets_by_cmd_id.setdefault(t.command_id, t)

        audit_rows = list(
            (
                await session.execute(
                    select(ActionAuditEvent)
                    .where(
                        ActionAuditEvent.tenant_id == tenant_id,
                        ActionAuditEvent.resource_kind == "command",
                        ActionAuditEvent.action.in_(
                            ("onec.command_approve", "onec.command_execute", "commands.create")
                        ),
                    )
                    .order_by(ActionAuditEvent.id.desc())
                    .limit(limit * 3)
                )
            )
            .scalars()
            .all()
        )

        entries: list[OneCCommandAuditEntry] = []
        seen_cmd_ids: set[int] = set()
        seen_terminal_exec_cmd_ids: set[int] = set()

        for evt in audit_rows:
            cmd_obj = commands_by_id.get(str(evt.resource_id or ""))
            if cmd_obj is None and evt.action == "commands.create":
                continue
            evt_actor_id = (
                int(evt.actor_id) if evt.actor_id is not None and str(evt.actor_id).isdigit() else evt.actor_id
            )
            evt_time = (
                getattr(evt, "occurred_at", None) or getattr(evt, "created_at", None) or datetime.now(timezone.utc)
            )
            if cmd_obj is not None:
                seen_cmd_ids.add(cmd_obj.id)
                if evt.action == "onec.command_execute":
                    if (cmd_obj.state == "succeeded" and evt.outcome in ("succeeded", "success")) or (
                        cmd_obj.state == "unknown" and evt.outcome == "unknown"
                    ):
                        seen_terminal_exec_cmd_ids.add(cmd_obj.id)
                payload = dict(cmd_obj.payload or {})
                target_obj = targets_by_cmd_id.get(cmd_obj.id)
                reason_code = (
                    evt.reason_code or cmd_obj.error_code if evt.action == "onec.command_execute" else evt.reason_code
                )
                entries.append(
                    OneCCommandAuditEntry(
                        event_id=str(evt.event_id),
                        command_id=cmd_obj.id,
                        tenant_id=tenant_id,
                        actor_id=evt_actor_id,
                        action=evt.action,
                        outcome=evt.outcome,
                        capability=cmd_obj.capability,
                        entity_set=payload.get("entity_set"),
                        target_ref=(
                            target_obj.target_ref
                            if target_obj
                            else (cmd_obj.target_refs[0] if cmd_obj.target_refs else None)
                        ),
                        state=cmd_obj.state,
                        payload_hash=cmd_obj.payload_hash,
                        provider_receipt=(
                            target_obj.provider_receipt if target_obj and evt.action == "onec.command_execute" else None
                        ),
                        reason_code=reason_code,
                        created_at=evt_time,
                    )
                )
            elif evt.action in ("onec.command_approve", "onec.command_execute") and evt_actor_id == actor_id:
                cmd_id_int = int(evt.resource_id) if evt.resource_id and str(evt.resource_id).isdigit() else None
                entries.append(
                    OneCCommandAuditEntry(
                        event_id=str(evt.event_id),
                        command_id=cmd_id_int,
                        tenant_id=tenant_id,
                        actor_id=evt_actor_id,
                        action=evt.action,
                        outcome=evt.outcome,
                        capability=None,
                        entity_set=None,
                        target_ref=None,
                        state=None,
                        payload_hash=None,
                        provider_receipt=None,
                        reason_code=evt.reason_code,
                        created_at=evt_time,
                    )
                )

        # Synthesize terminal execution/denial entries from BusinessCommand when not in ActionAuditEvent
        # (e.g. `failed` with `data_version_conflict`, `expired`, or `cancelled`).
        terminal_entries: list[OneCCommandAuditEntry] = []
        for cmd_obj in cmd_rows:
            needs_terminal_exec = cmd_obj.state in ("failed", "cancelled", "expired") or (
                cmd_obj.state in ("succeeded", "unknown") and cmd_obj.id not in seen_terminal_exec_cmd_ids
            )
            if not needs_terminal_exec and cmd_obj.id in seen_cmd_ids:
                continue
            payload = dict(cmd_obj.payload or {})
            target_obj = targets_by_cmd_id.get(cmd_obj.id)
            action = (
                "onec.command_execute"
                if cmd_obj.state in ("succeeded", "failed", "unknown", "cancelled", "expired")
                else ("onec.command_approve" if cmd_obj.state == "approved" else "commands.create")
            )
            outcome = (
                "unknown"
                if cmd_obj.state == "unknown"
                else ("denied" if cmd_obj.state in ("failed", "cancelled", "expired") else "succeeded")
            )
            ts = cmd_obj.executed_at or cmd_obj.updated_at or cmd_obj.created_at or datetime.now(timezone.utc)
            terminal_entries.append(
                OneCCommandAuditEntry(
                    event_id=f"cmd-{cmd_obj.id}",
                    command_id=cmd_obj.id,
                    tenant_id=tenant_id,
                    actor_id=cmd_obj.actor_user_id,
                    action=action,
                    outcome=outcome,
                    capability=cmd_obj.capability,
                    entity_set=payload.get("entity_set"),
                    target_ref=(
                        target_obj.target_ref
                        if target_obj
                        else (cmd_obj.target_refs[0] if cmd_obj.target_refs else None)
                    ),
                    state=cmd_obj.state,
                    payload_hash=cmd_obj.payload_hash,
                    provider_receipt=target_obj.provider_receipt if target_obj else None,
                    reason_code=cmd_obj.error_code,
                    created_at=ts,
                )
            )

        combined = terminal_entries + entries
        return tuple(combined[:limit])
