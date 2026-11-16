"""Inactive trusted admission boundaries; no production authority or version adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.integrations.file_workspace.content_read import ContentReadContext, ContentReadRequest, ReadPlainText
from app.integrations.file_workspace.retention_quota import RetentionQuotaSnapshot

ADMISSION_TIMEOUT = 5.0


class PrivateAdmissionUnavailable(Exception):
    def __init__(self):
        super().__init__("private_admission_unavailable")


@dataclass(frozen=True, slots=True, repr=False)
class PrivateScopeBinding:
    """Capture-time control-plane identity, never a bearer or persisted ACL grant."""

    context: ContentReadContext
    path_key: str
    personal_owner_id: int
    owner_epoch: str
    connection_epoch: str
    object_epoch: str
    consent_revision: str
    policy_revision: str


@dataclass(frozen=True, slots=True, repr=False)
class PrivateReadReceipt:
    """Application-held receipt from the original read; never restamp retained text.

    Trusted orchestration captures time/epochs at the read, and the reviewed
    adapter supplies exact-byte version semantics. Construction alone proves
    neither. No real receipt issuer is registered by this module.
    """

    binding: PrivateScopeBinding
    captured_at: datetime
    raw_sha256: str
    text_sha256: str
    parser_version: str
    provider_version: str
    version_semantics: str
    provider_object_id: str | None


@dataclass(frozen=True, slots=True, repr=False)
class PrivateAdmissionState:
    """One fresh authority result; quota is arithmetic input, NOT a reservation."""

    binding: PrivateScopeBinding
    processing_allowed: bool
    retention_allowed: bool
    now: datetime
    last_observed_at: datetime
    quota: RetentionQuotaSnapshot


@dataclass(frozen=True, slots=True, repr=False)
class ExactVersionAssertion:
    """Reviewed adapter assertion for this read and a freshly checked version.

    Matching strings/hashes cannot establish this claim. A real implementation
    needs reviewed conditional/snapshot semantics for the full representation.
    No Yandex implementation currently meets that contract; missing capability
    must raise, never manufacture a version from hashes/mtime/observations.
    """

    request: ContentReadRequest
    raw_sha256: str
    provider_version: str
    version_semantics: str
    provider_object_id: str | None
    checked_at: datetime


class PrivateAdmissionAuthority(Protocol):
    async def authorize(self, request: ContentReadRequest) -> PrivateAdmissionState:
        """Reload current personal scope and explicit processing/retention consent.

        Use managers/ORM in a future implementation. Check actor, membership,
        source.view, exact owner, live connection/account/secret, LIST+READ, root,
        cloud boundary and durable revocation epochs. Atomically advance trusted
        clock high-water even on denial; release locks before returning/network.
        No model egress, spend, share, writes or production storage is granted.
        """
        ...


class ExactVersionAuthority(Protocol):
    async def verify(self, source: ReadPlainText, receipt: PrivateReadReceipt) -> ExactVersionAssertion:
        """Fresh exact-file revalidation after authority, not a metadata LIST.

        Trusted injected implementation only; no URLs/credentials from callers,
        no default provider, retry, model or external write. Bound full transport
        and close resources under cancellation. Fail if exact-byte read binding
        or fresh matching provider revision cannot be independently established.
        """
        ...
