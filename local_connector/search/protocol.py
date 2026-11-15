"""Device-side signed command receiver matching the accepted P18-T01/P18-T02 wire contract.

Canonical JSON profile matches app/services/local_connector/wire.py:
- ASCII schema keys only
- no floats, duplicate keys, NaN, or Infinity
- UTF-8 strings, sorted object keys, compact separators
- Ed25519 signature verified before interpreting command arguments
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .engine import LocalSearchService
from .models import (
    DEFAULT_ENABLED_OPERATIONS,
    HEX32_PATTERN,
    MAX_CONTROL_BYTES,
    MAX_FETCH_BYTES,
    MAX_HITS,
    MAX_QUERY_BYTES,
    MAX_RESPONSE_BYTES,
    MAX_SNIPPET_CHARS,
    MAX_SNIPPETS,
    PROTOCOL_OPERATIONS,
    TIMEOUT_SECONDS,
    TOKEN_PATTERN,
    SearchFilters,
    SearchProtocolError,
    SearchQuery,
)
from .replay import ReplayLedger

logger = logging.getLogger("local_connector.search.protocol")

_ENVELOPE_KEYS = frozenset(
    {
        "version",
        "key_id",
        "request_id",
        "tenant_id",
        "owner_id",
        "actor_id",
        "device_id",
        "operation",
        "issued_at",
        "expires_at",
        "device_epoch",
        "consent_epoch",
        "policy_epoch",
        "data_boundary",
        "max_response_bytes",
        "max_snippet_characters",
        "args",
    }
)
_SEARCH_ARGS_KEYS = frozenset({"query", "roots", "limit"})
_FETCH_ARGS_KEYS = frozenset(
    {"object_id", "root_id", "relative_path", "destination", "max_bytes", "expected_content_hash"}
)
_DELETE_ARGS_KEYS = frozenset({"scope", "root_ids"})


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result or not key.isascii():
            raise SearchProtocolError("denied")
        result[key] = value
    return result


def _invalid_number(_token: str) -> Any:
    raise SearchProtocolError("denied")


def decode_control_payload(payload: bytes) -> dict[str, Any]:
    """Strictly decode a control payload <= 64 KiB, rejecting duplicates, floats, non-ASCII keys."""

    if type(payload) is not bytes or len(payload) == 0:
        raise SearchProtocolError("denied")
    if len(payload) > MAX_CONTROL_BYTES:
        raise SearchProtocolError("limit_exceeded")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_pairs,
            parse_float=_invalid_number,
            parse_constant=_invalid_number,
        )
    except SearchProtocolError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise SearchProtocolError("denied") from None
    if type(value) is not dict:
        raise SearchProtocolError("denied")
    return value


def canonical_json(value: Mapping[str, Any]) -> bytes:
    """Serialize dictionary to canonical UTF-8 JSON <= 64 KiB without floats or NaN."""

    if not isinstance(value, Mapping):
        raise SearchProtocolError("denied")
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise SearchProtocolError("denied") from None
    decode_control_payload(encoded)
    return encoded


def sign_control_payload(envelope_body: Mapping[str, Any], private_key: object) -> tuple[bytes, str]:
    """Sign an unsigned canonical control envelope using Ed25519."""

    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:
        raise SearchProtocolError("unavailable") from exc
    if not isinstance(private_key, Ed25519PrivateKey):
        raise SearchProtocolError("denied")
    body = dict(envelope_body)
    body.pop("signature", None)
    canonical_bytes = canonical_json(body)
    digest = hashlib.sha256(canonical_bytes).hexdigest()
    signature = base64.b64encode(private_key.sign(canonical_bytes)).decode("ascii")
    return canonical_json({**body, "signature": signature}), digest


def _is_positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _validate_relative_path(value: object) -> str:
    """One relative path beneath a selected root: no absolute form, no traversal, no NUL."""

    if type(value) is not str or not 0 < len(value) <= 2048:
        raise SearchProtocolError("denied")
    if value.startswith("/") or "\x00" in value:
        raise SearchProtocolError("denied")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise SearchProtocolError("denied")
    return value


def _validate_root_list(roots: object) -> tuple[str, ...]:
    if type(roots) is not list or not 1 <= len(roots) <= 16:
        raise SearchProtocolError("denied")
    cleaned: list[str] = []
    for item in roots:
        if type(item) is not str or not TOKEN_PATTERN.fullmatch(item):
            raise SearchProtocolError("denied")
        cleaned.append(item)
    if len(set(cleaned)) != len(cleaned):
        raise SearchProtocolError("denied")
    return tuple(cleaned)


@dataclass(frozen=True)
class VerifiedCommand:
    """Cryptographically verified, closed-schema command envelope."""

    version: int
    key_id: str
    request_id: str
    digest: str
    tenant_id: int
    owner_id: int
    actor_id: int
    device_id: int
    operation: str
    issued_at: int
    expires_at: int
    device_epoch: int
    consent_epoch: int
    policy_epoch: int
    data_boundary: str
    max_response_bytes: int
    max_snippet_characters: int
    args: dict[str, Any] = field(repr=False)


def verify_command_envelope(
    payload: bytes,
    trusted_keys: Mapping[str, object],
    *,
    tenant_id: int,
    owner_id: int,
    device_id: int,
    now: int | None = None,
    clock_skew_seconds: int = 0,
) -> VerifiedCommand:
    """Verify encoding, closed schema, Ed25519 signature, binding and freshness before execution."""

    data = decode_control_payload(payload)
    signature = data.pop("signature", None)
    if type(signature) is not str:
        raise SearchProtocolError("denied")
    if set(data.keys()) != _ENVELOPE_KEYS:
        raise SearchProtocolError("denied")

    if data["version"] != 1 or type(data["version"]) is not int:
        raise SearchProtocolError("denied")
    key_id = data["key_id"]
    request_id = data["request_id"]
    operation = data["operation"]
    if type(key_id) is not str or not TOKEN_PATTERN.fullmatch(key_id):
        raise SearchProtocolError("denied")
    if type(request_id) is not str or not HEX32_PATTERN.fullmatch(request_id):
        raise SearchProtocolError("denied")
    if type(operation) is not str or operation not in PROTOCOL_OPERATIONS:
        raise SearchProtocolError("denied")

    for int_field in (
        "tenant_id",
        "owner_id",
        "actor_id",
        "device_id",
        "issued_at",
        "expires_at",
        "device_epoch",
        "consent_epoch",
        "policy_epoch",
    ):
        if not _is_positive_int(data[int_field]):
            raise SearchProtocolError("denied")

    if data["data_boundary"] != "company":
        raise SearchProtocolError("denied")
    if type(data["max_response_bytes"]) is not int or data["max_response_bytes"] != MAX_RESPONSE_BYTES:
        raise SearchProtocolError("denied")
    if type(data["max_snippet_characters"]) is not int or data["max_snippet_characters"] != MAX_SNIPPET_CHARS:
        raise SearchProtocolError("denied")
    args = data["args"]
    if type(args) is not dict:
        raise SearchProtocolError("denied")

    public_key = trusted_keys.get(key_id)
    if public_key is None:
        raise SearchProtocolError("denied")

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise SearchProtocolError("unavailable") from exc
    if not isinstance(public_key, Ed25519PublicKey):
        raise SearchProtocolError("denied")

    canonical_body = canonical_json(data)
    try:
        sig_bytes = base64.b64decode(signature, validate=True)
        public_key.verify(sig_bytes, canonical_body)
    except (ValueError, InvalidSignature):
        raise SearchProtocolError("denied") from None

    if (
        data["tenant_id"] != tenant_id
        or data["owner_id"] != owner_id
        or data["device_id"] != device_id
        or data["actor_id"] != owner_id
    ):
        raise SearchProtocolError("denied")

    issued_at = int(data["issued_at"])
    expires_at = int(data["expires_at"])
    if not 0 < expires_at - issued_at <= TIMEOUT_SECONDS:
        raise SearchProtocolError("expired")

    clock = int(time.time()) if now is None else now
    if type(clock) is not int or clock <= 0:
        raise SearchProtocolError("unavailable")
    if clock < issued_at - clock_skew_seconds or clock > expires_at + clock_skew_seconds:
        raise SearchProtocolError("expired")

    if operation == "search":
        if not set(args.keys()) <= _SEARCH_ARGS_KEYS or "query" not in args or "roots" not in args:
            raise SearchProtocolError("denied")
        query_text = args["query"]
        if type(query_text) is not str or len(query_text) == 0:
            raise SearchProtocolError("denied")
        if len(query_text.encode("utf-8")) > MAX_QUERY_BYTES:
            raise SearchProtocolError("limit_exceeded")
        _validate_root_list(args["roots"])
        limit = args.get("limit", 20)
        if type(limit) is not int or not 1 <= limit <= MAX_HITS:
            raise SearchProtocolError("limit_exceeded")
    elif operation == "file.fetch":
        if not set(args.keys()) <= _FETCH_ARGS_KEYS or not {"object_id", "root_id", "relative_path"} <= set(
            args.keys()
        ):
            raise SearchProtocolError("denied")
        if type(args["object_id"]) is not str or not TOKEN_PATTERN.fullmatch(args["object_id"]):
            raise SearchProtocolError("denied")
        if type(args["root_id"]) is not str or not TOKEN_PATTERN.fullmatch(args["root_id"]):
            raise SearchProtocolError("denied")
        _validate_relative_path(args["relative_path"])
        if args.get("destination", "private_storage") != "private_storage":
            raise SearchProtocolError("denied")
        max_bytes = args.get("max_bytes", MAX_FETCH_BYTES)
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_FETCH_BYTES:
            raise SearchProtocolError("limit_exceeded")
        expected = args.get("expected_content_hash")
        if expected is not None and (type(expected) is not str or not HEX64_PATTERN.fullmatch(expected)):
            raise SearchProtocolError("denied")
    elif operation == "index.delete":
        if not set(args.keys()) <= _DELETE_ARGS_KEYS or "root_ids" not in args:
            raise SearchProtocolError("denied")
        if args.get("scope", "index") not in {"index", "all"}:
            raise SearchProtocolError("denied")
        _validate_root_list(args["root_ids"])
    elif operation == "health":
        if args:
            raise SearchProtocolError("denied")
    elif operation == "index.status":
        if not set(args.keys()) <= {"roots"}:
            raise SearchProtocolError("denied")
        if "roots" in args:
            _validate_root_list(args["roots"])

    digest = hashlib.sha256(canonical_body).hexdigest()
    return VerifiedCommand(
        version=1,
        key_id=key_id,
        request_id=request_id,
        digest=digest,
        tenant_id=int(data["tenant_id"]),
        owner_id=int(data["owner_id"]),
        actor_id=int(data["actor_id"]),
        device_id=int(data["device_id"]),
        operation=operation,
        issued_at=issued_at,
        expires_at=expires_at,
        device_epoch=int(data["device_epoch"]),
        consent_epoch=int(data["consent_epoch"]),
        policy_epoch=int(data["policy_epoch"]),
        data_boundary=str(data["data_boundary"]),
        max_response_bytes=MAX_RESPONSE_BYTES,
        max_snippet_characters=MAX_SNIPPET_CHARS,
        args=dict(args),
    )


class SignedCommandReceiver:
    """Device-side signed command receiver enforcing epochs, allowlist, boundaries, OS ACLs and replay."""

    def __init__(
        self,
        *,
        search_service: LocalSearchService,
        replay_ledger: ReplayLedger,
        trusted_keys: Mapping[str, object],
        tenant_id: int,
        owner_id: int,
        device_id: int,
        device_epoch: int = 1,
        consent_epoch: int = 1,
        policy_epoch: int = 1,
        enabled_operations: frozenset[str] = DEFAULT_ENABLED_OPERATIONS,
        clock_skew_seconds: int = 0,
        fetcher: object | None = None,
        fetch_confirmation: object | None = None,
        fetch_sink: object | None = None,
        version_issuer: object | None = None,
    ) -> None:
        if not _is_positive_int(tenant_id) or not _is_positive_int(owner_id) or not _is_positive_int(device_id):
            raise ValueError("tenant_id, owner_id and device_id must be positive integers")
        if (
            not _is_positive_int(device_epoch)
            or not _is_positive_int(consent_epoch)
            or not _is_positive_int(policy_epoch)
        ):
            raise ValueError("epochs must be positive integers")
        if not enabled_operations <= PROTOCOL_OPERATIONS:
            raise ValueError("enabled_operations must be a subset of PROTOCOL_OPERATIONS")

        self.search_service = search_service
        self.replay_ledger = replay_ledger
        self.trusted_keys = dict(trusted_keys)
        self.tenant_id = tenant_id
        self.owner_id = owner_id
        self.device_id = device_id
        self.device_epoch = device_epoch
        self.consent_epoch = consent_epoch
        self.policy_epoch = policy_epoch
        self.enabled_operations = frozenset(enabled_operations)
        self.clock_skew_seconds = clock_skew_seconds
        self.fetcher = fetcher
        self.fetch_confirmation = fetch_confirmation
        self.fetch_sink = fetch_sink
        self.version_issuer = version_issuer
        self.revoked = False

    def revoke(self, *, device_epoch: int | None = None) -> None:
        self.revoked = True
        if device_epoch is not None and device_epoch > self.device_epoch:
            self.device_epoch = device_epoch
        else:
            self.device_epoch += 1
        self.replay_ledger.invalidate_device(self.device_id)

    def update_epochs(
        self,
        *,
        device_epoch: int | None = None,
        consent_epoch: int | None = None,
        policy_epoch: int | None = None,
    ) -> None:
        changed = False
        if device_epoch is not None and device_epoch != self.device_epoch:
            if device_epoch < self.device_epoch:
                raise SearchProtocolError("stale")
            self.device_epoch = device_epoch
            changed = True
        if consent_epoch is not None and consent_epoch != self.consent_epoch:
            if consent_epoch < self.consent_epoch:
                raise SearchProtocolError("stale")
            self.consent_epoch = consent_epoch
            changed = True
        if policy_epoch is not None and policy_epoch != self.policy_epoch:
            if policy_epoch < self.policy_epoch:
                raise SearchProtocolError("stale")
            self.policy_epoch = policy_epoch
            changed = True
        if changed:
            self.replay_ledger.invalidate_device(self.device_id)

    def _base_response(self, cmd: VerifiedCommand, *, outcome: str) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "outcome": outcome,
            "request_digest": cmd.digest,
            "request_id": cmd.request_id,
            "version": 1,
        }

    def _search_response(
        self,
        cmd: VerifiedCommand,
        *,
        outcome: str,
        partial: bool = False,
        hits: list[dict[str, Any]] | None = None,
    ) -> bytes:
        base = self._base_response(cmd, outcome=outcome)
        base["partial"] = bool(partial)
        base["hits"] = []
        raw_hits = list(hits or [])
        accepted_hits: list[dict[str, Any]] = []
        snippet_count = 0
        for item in raw_hits:
            if item.get("snippet") is not None:
                if snippet_count >= MAX_SNIPPETS:
                    item = {**item, "snippet": None}
                else:
                    snippet_count += 1
            candidate = {**base, "hits": [*accepted_hits, item]}
            try:
                encoded = canonical_json(candidate)
            except SearchProtocolError:
                base["partial"] = True
                break
            if len(encoded) > MAX_RESPONSE_BYTES:
                base["partial"] = True
                break
            accepted_hits.append(item)

        base["hits"] = accepted_hits
        return canonical_json(base)

    def _epochs_match(self, cmd: VerifiedCommand) -> bool:
        return (cmd.device_epoch, cmd.consent_epoch, cmd.policy_epoch) == (
            self.device_epoch,
            self.consent_epoch,
            self.policy_epoch,
        )

    def _reauthorize_cached_reply(self, cmd: VerifiedCommand, cached_json: str | None) -> bytes:
        if self.revoked or not self._epochs_match(cmd) or cached_json is None:
            outcome = "denied" if self.revoked else ("stale" if not self._epochs_match(cmd) else "unknown")
            if cmd.operation == "search":
                return self._search_response(cmd, outcome=outcome)
            return canonical_json(self._base_response(cmd, outcome=outcome))

        try:
            payload = decode_control_payload(cached_json.encode("utf-8"))
        except SearchProtocolError:
            if cmd.operation == "search":
                return self._search_response(cmd, outcome="unknown")
            return canonical_json(self._base_response(cmd, outcome="unknown"))

        if cmd.operation != "search":
            return canonical_json(payload)

        if payload.get("outcome") != "succeeded":
            return self._search_response(cmd, outcome=str(payload.get("outcome", "unknown")))

        if self.search_service.store.pause_reason == "deleted":
            return self._search_response(cmd, outcome="stale")

        consented_roots = {
            reg.root_id
            for reg in self.search_service.root_registrations()
            if reg.boundary == "company" and reg.cloud_consented
        }
        if any(root_id not in consented_roots for root_id in cmd.args.get("roots", [])):
            return self._search_response(cmd, outcome="denied")

        filtered_hits: list[dict[str, Any]] = []
        partial = bool(payload.get("partial", False))
        for hit in payload.get("hits", []):
            ref = hit.get("reference", {})
            root_id = ref.get("root_id")
            rel_path = ref.get("path")
            if type(root_id) is not str or type(rel_path) is not str:
                partial = True
                continue
            if self.search_service.verify_reference_access(root_id, rel_path, cloud_egress=True):
                filtered_hits.append(hit)
            else:
                partial = True
        return self._search_response(cmd, outcome="succeeded", partial=partial, hits=filtered_hits)

    def _execute_search(self, cmd: VerifiedCommand) -> tuple[bytes, bool]:
        roots = _validate_root_list(cmd.args["roots"])
        limit = int(cmd.args.get("limit", 20))
        search_query = SearchQuery(
            query=str(cmd.args["query"]),
            limit=limit,
            max_snippet_chars=cmd.max_snippet_characters,
            max_snippets=MAX_SNIPPETS,
            filters=SearchFilters(roots=roots),
            allow_absolute_paths=False,
        )
        result = self.search_service.search(search_query, cloud_egress=True)
        wire_hits = [hit.to_wire_dict() for hit in result.hits]
        return (
            self._search_response(cmd, outcome="succeeded", partial=result.partial, hits=wire_hits),
            True,
        )

    def _execute_health(self, cmd: VerifiedCommand) -> bytes:
        resp = self._base_response(cmd, outcome="succeeded")
        resp["protocol_version"] = 1
        resp["paused"] = bool(self.search_service.store.indexing_paused)
        resp["status"] = "paused" if self.search_service.store.indexing_paused else "ok"
        return canonical_json(resp)

    def _execute_index_status(self, cmd: VerifiedCommand) -> bytes:
        consented_by_id = {
            reg.root_id: reg
            for reg in self.search_service.root_registrations()
            if reg.boundary == "company" and reg.cloud_consented
        }
        requested = _validate_root_list(cmd.args["roots"]) if "roots" in cmd.args else tuple(consented_by_id.keys())
        if any(root_id not in consented_by_id for root_id in requested):
            resp = self._base_response(cmd, outcome="denied")
            return canonical_json(resp)

        counts = self.search_service.root_index_counts(requested)
        resp = self._base_response(cmd, outcome="succeeded")
        resp["generation"] = int(self.search_service.store.generation)
        resp["paused"] = bool(self.search_service.store.indexing_paused)
        resp["roots"] = [{"indexed_files": counts.get(root_id, 0), "root_id": root_id} for root_id in sorted(requested)]
        return canonical_json(resp)

    def _execute_index_delete(self, cmd: VerifiedCommand) -> tuple[bytes, bool]:
        from ..index import IndexStore, root_hash

        scope = str(cmd.args.get("scope", "index"))
        requested = _validate_root_list(cmd.args["root_ids"])
        consented = {
            reg.root_id: reg
            for reg in self.search_service.root_registrations()
            if reg.boundary == "company" and reg.cloud_consented
        }
        if any(root_id not in consented for root_id in requested):
            return canonical_json(self._base_response(cmd, outcome="denied")), False
        if scope == "all":
            root_hashes: list[str] | None = None
            pause = True
        else:
            root_hashes = [root_hash(consented[root_id].selected) for root_id in requested]
            pause = False
        store: IndexStore = self.search_service.store
        report = store.delete_scope(
            request_id=cmd.request_id,
            request_digest=cmd.digest,
            root_hashes=root_hashes,
            pause=pause,
        )
        resp = self._base_response(cmd, outcome="succeeded")
        resp["scope"] = scope
        resp["generation"] = int(report.generation)
        resp["removed"] = int(report.removed)
        return canonical_json(resp), bool(report.removed)

    def _execute_file_fetch(self, cmd: VerifiedCommand) -> tuple[bytes, bool]:
        from ..transfer import FetchConsent

        if self.fetcher is None or not callable(self.fetch_confirmation):
            return canonical_json(self._base_response(cmd, outcome="unavailable")), False
        consent = self.fetch_confirmation(cmd)
        if not isinstance(consent, FetchConsent) or consent.request_id != cmd.request_id:
            # A valid signed command is still not authority: a human confirmation must
            # already exist locally for exactly this object, root and path.
            return canonical_json(self._base_response(cmd, outcome="denied")), False
        if not consent.binds(
            object_id=str(cmd.args["object_id"]),
            root_id=str(cmd.args["root_id"]),
            relative_path=str(cmd.args["relative_path"]),
        ):
            return canonical_json(self._base_response(cmd, outcome="denied")), False
        result = self.fetcher.fetch(consent, issuer=self.version_issuer, sink=self.fetch_sink)
        outcome = (
            result.state
            if result.state in {"denied", "stale", "limit_exceeded", "unavailable", "unknown"}
            else ("succeeded" if result.state == "complete" else "denied")
        )
        resp = self._base_response(cmd, outcome=outcome)
        resp["object_id"] = str(cmd.args["object_id"])
        resp["size"] = int(result.bytes_read) if result.complete else 0
        if result.complete:
            resp["content_hash"] = str(result.sha256)
            resp["version_assertion"] = str(result.version_assertion)
        return canonical_json(resp), result.complete

    def handle_payload(self, payload: bytes, *, now: int | None = None) -> bytes:
        """Verify and execute one signed command payload, recording durable replay state."""

        cmd = verify_command_envelope(
            payload,
            self.trusted_keys,
            tenant_id=self.tenant_id,
            owner_id=self.owner_id,
            device_id=self.device_id,
            now=now,
            clock_skew_seconds=self.clock_skew_seconds,
        )
        decision = self.replay_ledger.reserve(
            device_id=self.device_id,
            request_id=cmd.request_id,
            digest=cmd.digest,
            operation=cmd.operation,
            issued_at=cmd.issued_at,
            device_epoch=cmd.device_epoch,
            consent_epoch=cmd.consent_epoch,
            policy_epoch=cmd.policy_epoch,
            now=now,
        )
        if decision.status == "duplicate":
            if decision.state != "completed":
                if cmd.operation == "search":
                    return self._search_response(cmd, outcome="unknown")
                return canonical_json(self._base_response(cmd, outcome="unknown"))
            return self._reauthorize_cached_reply(cmd, decision.response_json)

        try:
            if self.revoked:
                response_bytes = (
                    self._search_response(cmd, outcome="denied")
                    if cmd.operation == "search"
                    else canonical_json(self._base_response(cmd, outcome="denied"))
                )
            elif not self._epochs_match(cmd):
                response_bytes = (
                    self._search_response(cmd, outcome="stale")
                    if cmd.operation == "search"
                    else canonical_json(self._base_response(cmd, outcome="stale"))
                )
            elif cmd.operation not in self.enabled_operations:
                response_bytes = (
                    self._search_response(cmd, outcome="unavailable")
                    if cmd.operation == "search"
                    else canonical_json(self._base_response(cmd, outcome="unavailable"))
                )
            elif cmd.operation == "search":
                try:
                    response_bytes, _ = self._execute_search(cmd)
                except SearchProtocolError as exc:
                    response_bytes = self._search_response(cmd, outcome=exc.code)
            elif cmd.operation == "health":
                response_bytes = self._execute_health(cmd)
            elif cmd.operation == "index.status":
                response_bytes = self._execute_index_status(cmd)
            elif cmd.operation == "index.delete":
                response_bytes, _executed = self._execute_index_delete(cmd)
            elif cmd.operation == "file.fetch":
                response_bytes, _executed = self._execute_file_fetch(cmd)
            else:
                response_bytes = canonical_json(self._base_response(cmd, outcome="unavailable"))

            self.replay_ledger.complete(
                device_id=self.device_id,
                request_id=cmd.request_id,
                digest=cmd.digest,
                response_json=response_bytes.decode("utf-8"),
            )
            logger.info(
                "local_connector_command operation=%s request_id=%s device_id=%s outcome=%s",
                cmd.operation,
                cmd.request_id,
                cmd.device_id,
                decode_control_payload(response_bytes).get("outcome", "unknown"),
            )
            return response_bytes
        except Exception:
            self.replay_ledger.mark_unknown(
                device_id=self.device_id,
                request_id=cmd.request_id,
                digest=cmd.digest,
            )
            raise
