"""Closed signed search protocol. No shell, URL, fetch or destructive operation.

Canonical form is this restricted JSON profile, not a claim of general RFC8785:
ASCII schema keys, no floats, duplicate keys or non-finite numbers, UTF-8 strings,
sorted object keys, compact separators. Signing covers all fields except signature.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from typing import Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field

MAX_CONTROL_BYTES = 65536
MAX_QUERY_BYTES = 4096
MAX_HITS = 50
MAX_SNIPPETS = 8
TIMEOUT = 30
REPLAY_SECONDS = 86400
REPLAY_CAPACITY = 4096
TOKEN = r"^[a-zA-Z0-9_-]{1,64}$"
HEX32 = r"^[0-9a-f]{32}$"
HEX64 = r"^[0-9a-f]{64}$"


class ConnectorUnavailable(Exception):
    def __init__(self):
        super().__init__("connector_unavailable")


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class SearchArgs(WireModel):
    query: str = Field(min_length=1, max_length=4096, repr=False)
    roots: list[str] = Field(min_length=1, max_length=16)
    limit: int = Field(default=20, ge=1, le=50)

    def checked(self):
        if len(self.query.encode("utf-8")) > MAX_QUERY_BYTES or len(set(self.roots)) != len(self.roots):
            raise ConnectorUnavailable()
        if any(not __import__("re").fullmatch(TOKEN, root) for root in self.roots):
            raise ConnectorUnavailable()
        return self


class SearchRequest(WireModel):
    version: Literal[1] = 1
    key_id: str = Field(pattern=TOKEN)
    request_id: str = Field(pattern=HEX32)
    tenant_id: int = Field(gt=0)
    owner_id: int = Field(gt=0)
    actor_id: int = Field(gt=0)
    device_id: int = Field(gt=0)
    operation: Literal["search"] = "search"
    issued_at: int = Field(gt=0)
    expires_at: int = Field(gt=0)
    device_epoch: int = Field(gt=0)
    consent_epoch: int = Field(gt=0)
    policy_epoch: int = Field(gt=0)
    data_boundary: Literal["company"] = "company"
    max_response_bytes: Literal[65536] = 65536
    max_snippet_characters: Literal[512] = 512
    args: SearchArgs


class FileReference(WireModel):
    object_id: str = Field(pattern=TOKEN)
    root_id: str = Field(pattern=TOKEN)
    path: str = Field(min_length=1, max_length=2048, repr=False)
    page: int | None = Field(default=None, gt=0)
    sheet: str | None = Field(default=None, max_length=128, repr=False)


class SearchHit(WireModel):
    reference: FileReference
    rank: int = Field(ge=0, le=2147483647)
    snippet: str | None = Field(default=None, max_length=512, repr=False)
    data_boundary: Literal["company"] = "company"
    # Catalog observation only: never impersonates exact-version/line evidence.
    evidence_kind: Literal["local_search_observation"] = "local_search_observation"


class SearchResponse(WireModel):
    version: Literal[1] = 1
    request_id: str = Field(pattern=HEX32)
    request_digest: str = Field(pattern=HEX64)
    device_id: int = Field(gt=0)
    outcome: Literal["succeeded", "denied", "unavailable", "expired", "limit_exceeded", "stale", "unknown"]
    partial: bool = False
    hits: list[SearchHit] = Field(default_factory=list, max_length=50)

    def checked(self, request: SearchRequest, digest: str):
        if self.request_id != request.request_id or self.device_id != request.device_id or self.request_digest != digest:
            raise ConnectorUnavailable()
        if len(self.hits) > request.args.limit or sum(hit.snippet is not None for hit in self.hits) > MAX_SNIPPETS:
            raise ConnectorUnavailable()
        if self.outcome != "succeeded" and self.hits:
            raise ConnectorUnavailable()
        if any(hit.reference.root_id not in request.args.roots for hit in self.hits):
            raise ConnectorUnavailable()
        return self


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result or not key.isascii():
            raise ConnectorUnavailable()
        result[key] = value
    return result


def _invalid_number(_):
    raise ConnectorUnavailable()


def decode(payload: bytes) -> dict:
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_CONTROL_BYTES:
        raise ConnectorUnavailable()
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_float=_invalid_number, parse_constant=_invalid_number)
        if type(value) is not dict:
            raise ValueError
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise ConnectorUnavailable() from None


def canonical(value: dict) -> bytes:
    try:
        payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        decode(payload)
        return payload
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ConnectorUnavailable() from None


def sign(request: SearchRequest, key: Ed25519PrivateKey) -> tuple[bytes, str]:
    request.args.checked()
    data = request.model_dump()
    content = canonical(data)
    digest = hashlib.sha256(content).hexdigest()
    signature = base64.b64encode(key.sign(content)).decode("ascii")
    return canonical({**data, "signature": signature}), digest


def verify(payload: bytes, key: Ed25519PublicKey, *, key_id: str, now: int | None = None) -> SearchRequest:
    try:
        data = decode(payload)
        signature = data.pop("signature")
        if type(signature) is not str:
            raise ValueError
        request = SearchRequest.model_validate(data)
        key.verify(base64.b64decode(signature, validate=True), canonical(data))
        clock = int(time.time()) if now is None else now
        if request.key_id != key_id or request.owner_id != request.actor_id:
            raise ValueError
        if not request.issued_at <= clock <= request.expires_at or not 0 < request.expires_at-request.issued_at <= TIMEOUT:
            raise ValueError
        request.args.checked()
        return request
    except Exception:
        raise ConnectorUnavailable() from None


def new_request(*, key_id, tenant_id, owner_id, device_id, device_epoch, consent_epoch, policy_epoch, args):
    now = int(time.time())
    return SearchRequest(key_id=key_id, request_id=uuid.uuid4().hex, tenant_id=tenant_id, owner_id=owner_id,
                         actor_id=owner_id, device_id=device_id, issued_at=now, expires_at=now+TIMEOUT,
                         device_epoch=device_epoch, consent_epoch=consent_epoch, policy_epoch=policy_epoch,
                         args=args)
