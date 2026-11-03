"""Inactive closed-schema JSON format; not provenance, rights or persistence.

No source/context dumping, clock reads or I/O. Even canonical data can be forged.
Future admission must independently bind trusted source/version/epochs and rights.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime

from app.integrations.file_workspace.insight_candidates import (
    MAX_CANDIDATE_CHARACTERS,
    MAX_CANDIDATE_REFERENCES,
    MAX_INSIGHT_CANDIDATES,
    MAX_TOTAL_CANDIDATE_CHARACTERS,
)
from app.integrations.file_workspace.line_evidence import (
    MAX_EVIDENCE_CHARACTERS,
    MAX_EVIDENCE_LINES,
    MAX_EVIDENCE_RANGES,
)
from app.integrations.file_workspace.plain_text import MAX_LINES, PARSER_VERSION
from app.integrations.file_workspace.retention_quota import MAX_RECORD_BYTES
from app.integrations.file_workspace.retention_time import MAX_CAPTURE_AGE, RETENTION_TTL

SCHEMA_VERSION = 1
SERIALIZER_REVISION = "private-insight-json-v1"
LIFECYCLE_REVISION = "private-retention-v1"
MAX_ID = 2_147_483_647
MAX_OPAQUE_BYTES = 512
MAX_REVISION_BYTES = 64

_ROOT_KEYS = frozenset(
    (
        "schema_version",
        "serializer_revision",
        "scope",
        "source",
        "binding",
        "lifecycle",
        "line_count",
        "excerpts",
        "candidates",
        "review_status",
    )
)


class PrivatePayloadUnavailable(Exception):
    """No input values or chained validation/parser diagnostics."""

    def __init__(self):
        super().__init__("private_payload_unavailable")


def _mapping(value: object, keys: frozenset[str]) -> dict:
    # Check count before inspecting keys; never invoke arbitrary key callbacks.
    if type(value) is not dict or len(value) != len(keys):
        raise ValueError
    if any(type(key) is not str for key in value) or value.keys() != keys:
        raise ValueError
    return value


def _integer(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError
    return value


def _literal(value: object, expected: str) -> None:
    if type(value) is not str or value != expected:
        raise ValueError


def _hex(value: object, length: int) -> None:
    if type(value) is not str or len(value) != length or re.fullmatch(r"[0-9a-f]+", value) is None:
        raise ValueError


def _revision(value: object) -> None:
    if (
        type(value) is not str
        or not 1 <= len(value) <= MAX_REVISION_BYTES
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) is None
    ):
        raise ValueError


def _opaque(value: object) -> None:
    if type(value) is not str or not 1 <= len(value) <= MAX_OPAQUE_BYTES:
        raise ValueError
    if (
        not value.strip()
        or any(unicodedata.category(char).startswith("C") for char in value)
        or len(value.encode("utf-8")) > MAX_OPAQUE_BYTES
    ):
        raise ValueError


def _text(value: object, maximum: int, *, nonblank: bool = False) -> str:
    if type(value) is not str or len(value) > maximum:
        raise ValueError
    if (nonblank and not value.strip()) or any(
        unicodedata.category(char).startswith("C") and char not in "\n\t" for char in value
    ):
        raise ValueError
    return value


def _timestamp(value: object) -> datetime:
    if (
        type(value) is not str
        or len(value) != 27
        or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z", value) is None
    ):
        raise ValueError
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _validate(data: object) -> dict:
    data = _mapping(data, _ROOT_KEYS)
    if _integer(data["schema_version"], SCHEMA_VERSION, SCHEMA_VERSION) != SCHEMA_VERSION:
        raise ValueError
    _literal(data["serializer_revision"], SERIALIZER_REVISION)
    _literal(data["review_status"], "unverified")

    scope = _mapping(
        data["scope"], frozenset(("tenant_id", "owner_id", "connection_id", "object_id", "root_key", "path_key"))
    )
    for name in ("tenant_id", "owner_id", "connection_id", "object_id"):
        _integer(scope[name], 1, MAX_ID)
    for name in ("root_key", "path_key"):
        _hex(scope[name], 64)

    source = _mapping(
        data["source"],
        frozenset(
            (
                "provider_version",
                "version_semantics",
                "provider_object_id",
                "raw_sha256",
                "text_sha256",
                "parser_version",
            )
        ),
    )
    _opaque(source["provider_version"])
    _revision(source["version_semantics"])
    if source["provider_object_id"] is not None:
        _opaque(source["provider_object_id"])
    for name in ("raw_sha256", "text_sha256"):
        _hex(source[name], 64)
    _literal(source["parser_version"], PARSER_VERSION)

    binding = _mapping(
        data["binding"],
        frozenset(
            (
                "read_attempt_id",
                "generation_attempt_id",
                "owner_epoch",
                "connection_epoch",
                "object_epoch",
                "consent_revision",
                "policy_revision",
            )
        ),
    )
    for name in ("read_attempt_id", "generation_attempt_id", "owner_epoch", "connection_epoch", "object_epoch"):
        _hex(binding[name], 32)
    for name in ("consent_revision", "policy_revision"):
        _revision(binding[name])

    lifecycle = _mapping(data["lifecycle"], frozenset(("revision", "captured_at", "created_at", "expires_at")))
    _literal(lifecycle["revision"], LIFECYCLE_REVISION)
    captured, created, expires = (_timestamp(lifecycle[name]) for name in ("captured_at", "created_at", "expires_at"))
    if not captured <= created <= captured + MAX_CAPTURE_AGE or expires != captured + RETENTION_TTL:
        raise ValueError

    line_count = _integer(data["line_count"], 1, MAX_LINES)
    excerpts = data["excerpts"]
    if type(excerpts) is not list or not 1 <= len(excerpts) <= MAX_EVIDENCE_RANGES:
        raise ValueError
    previous_end = total_lines = total_characters = 0
    for item in excerpts:
        item = _mapping(item, frozenset(("start", "end", "text")))
        start = _integer(item["start"], previous_end + 1, line_count)
        end = _integer(item["end"], start, line_count)
        text = _text(item["text"], MAX_EVIDENCE_CHARACTERS)
        total_lines += end - start + 1
        total_characters += len(text)
        if (
            text.count("\n") != end - start
            or total_lines > MAX_EVIDENCE_LINES
            or total_characters > MAX_EVIDENCE_CHARACTERS
        ):
            raise ValueError
        previous_end = end

    candidates = data["candidates"]
    if type(candidates) is not list or len(candidates) > MAX_INSIGHT_CANDIDATES:
        raise ValueError
    total_characters = 0
    for item in candidates:
        item = _mapping(item, frozenset(("text", "evidence_indices")))
        total_characters += len(_text(item["text"], MAX_CANDIDATE_CHARACTERS, nonblank=True))
        if total_characters > MAX_TOTAL_CANDIDATE_CHARACTERS:
            raise ValueError
        indices = item["evidence_indices"]
        if type(indices) is not list or not 1 <= len(indices) <= MAX_CANDIDATE_REFERENCES:
            raise ValueError
        previous = 0
        for index in indices:
            previous = _integer(index, previous + 1, len(excerpts))
    return data


def serialize_private_payload(data: object) -> bytes:
    """Validate explicit JSON-shaped allowlist; never convert a source context.

    All nested field/count bounds precede encoding. Returned UTF-8 bytes include
    the whole envelope; charge len(result) outside it. Success is FORMAT ONLY.
    """
    try:
        checked = _validate(data)
        payload = json.dumps(
            checked, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
        if len(payload) <= MAX_RECORD_BYTES:
            return payload
    except Exception:
        pass
    # Outside the handler: ordinary tracebacks cannot chain private diagnostics.
    raise PrivatePayloadUnavailable()


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError


def private_payload_is_canonical(payload: object) -> bool:
    """Bound before decoding; no parsed-data release or historical read forgery.

    No schema fallback/repair, whitespace normalization or duplicate-key collapse.
    This is not authentication, currentness, source consistency or authorization.
    """
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_RECORD_BYTES:
        return False
    try:
        data = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        return serialize_private_payload(data) == payload
    except Exception:
        return False
