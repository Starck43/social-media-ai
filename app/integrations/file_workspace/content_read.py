"""Inactive, provider-neutral contracts for a bounded authorized plain-text read.

Only trusted application adapters may implement these protocols. No production
adapter, authorization policy, transport, route or persistence is registered.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol

from app.integrations.file_workspace.plain_text import PlainTextResult

READ_TIMEOUT = 5.0
MAX_READ_CHUNKS = 1024


class PlainTextReadUnavailable(Exception):
    """One sanitized failure, without provider, source or authorization details."""

    def __init__(self):
        super().__init__("plain_text_unavailable")


@dataclass(frozen=True, slots=True, repr=False)
class ContentReadRequest:
    """Catalog IDs plus a fresh orchestration nonce; not a bearer grant."""

    tenant_id: int
    actor_id: int
    connection_id: int
    object_id: int
    nonce: str


@dataclass(frozen=True, slots=True, repr=False)
class ContentReadContext:
    """Authority assertion for this request, NOT proof a policy check occurred.

    Keys are opaque change fingerprints, never raw credentials. Observation is
    metadata history, not a content revision. A reviewed authority must verify
    current actor/membership/source.view, personal ownership, LIST+READ, root,
    credentials and explicit cloud processing permission independently.
    """

    request: ContentReadRequest
    path: str
    name: str
    root_key: str
    policy_key: str
    credential_key: str
    observation_number: int
    processing_boundary: str
    processing_policy_key: str


@dataclass(frozen=True, slots=True, repr=False)
class ContentReadStream:
    """Trusted reader assertion: complete exact-file representation, no ranges.

    expected_bytes is the full decoded representation size, not compressed wire
    length. The adapter must independently bound transport/decompression/chunk
    allocations and ensure provider_version, if supplied, identifies THESE bytes.
    Syntax/equality cannot establish a dishonest adapter's assertion.
    """

    context: ContentReadContext
    chunks: AsyncIterator[bytes]
    expected_bytes: int
    complete: bool
    provider_version: str | None = None


@dataclass(frozen=True, slots=True, repr=False)
class ReadPlainText:
    """In-memory untrusted text with request lineage, not retained knowledge.

    An optional version is only the reviewed reader's assertion, never derived
    from hashes/name/metadata. No latest-version, snapshot, backup or sharing
    guarantee; revocation cannot recall data already handed to trusted code.
    """

    context: ContentReadContext
    extraction: PlainTextResult
    provider_version: str | None


class ContentReadAuthority(Protocol):
    async def authorize(self, request: ContentReadRequest) -> ContentReadContext:
        """Fresh checks on EVERY call; release transactions/locks before return.

        Deny local_only/company/unknown boundaries and absent processing consent.
        Reuse existing managers/ORM in a future reviewed implementation; none is
        supplied here. Recheck the same request before releasing extracted text.
        """
        ...


class ContentReader(Protocol):
    def open(self, context: ContentReadContext, *, max_bytes: int) -> AbstractAsyncContextManager[ContentReadStream]:
        """Close resources on success/error/cancellation; never return a URL.

        A future P8-T34 adapter owns safe network/credentials, exact-file live
        revalidation, full-response/version integrity and stream allocation caps.
        No DB lock may be held across this operation. No adapter exists here.
        """
        ...
