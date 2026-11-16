"""Mock-only payload preparation: fresh authority/version/time/quota composition.

This is NOT admission to storage, an atomic reservation or an authorization grant.
No default authority, provider, persistence, task, route or runtime registration.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.integrations.file_workspace.content_read import ContentReadRequest, ReadPlainText
from app.integrations.file_workspace.insight_candidates import InsightCandidateBatch
from app.integrations.file_workspace.line_evidence import verify_line_evidence
from app.integrations.file_workspace.private_admission import (
    ADMISSION_TIMEOUT,
    ExactVersionAssertion,
    ExactVersionAuthority,
    PrivateAdmissionAuthority,
    PrivateAdmissionState,
    PrivateAdmissionUnavailable,
    PrivateReadReceipt,
    PrivateScopeBinding,
)
from app.integrations.file_workspace.private_projection import serialize_same_read_payload
from app.integrations.file_workspace.retention_quota import retention_quota_allows
from app.integrations.file_workspace.retention_time import make_retention_deadline

_BINDING_FIELDS = ("path_key", "owner_epoch", "connection_epoch", "object_epoch", "consent_revision", "policy_revision")


def _live(deadline):
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError()
    if asyncio.get_running_loop().time() >= deadline:
        raise ValueError


def _utc(value):
    if type(value) is not datetime or value.tzinfo is not timezone.utc:
        raise ValueError
    return value


def _stamp(value):
    return _utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _binding(binding, source, batch):
    if (
        type(binding) is not PrivateScopeBinding
        or type(binding.personal_owner_id) is not int
        or binding.personal_owner_id != source.context.request.actor_id
        or any(type(getattr(binding, field)) is not str for field in _BINDING_FIELDS)
        or not verify_line_evidence(
            batch.evidence, ReadPlainText(binding.context, source.extraction, source.provider_version)
        )
    ):
        raise ValueError


def _receipt(source, batch, receipt):
    if (
        type(source) is not ReadPlainText
        or type(batch) is not InsightCandidateBatch
        or type(receipt) is not PrivateReadReceipt
        or not verify_line_evidence(batch.evidence, source)
    ):
        raise ValueError
    _binding(receipt.binding, source, batch)
    _utc(receipt.captured_at)
    for actual, expected in (
        (receipt.raw_sha256, source.extraction.raw_sha256),
        (receipt.text_sha256, source.extraction.text_sha256),
        (receipt.parser_version, source.extraction.parser_version),
        (receipt.provider_version, source.provider_version),
    ):
        if type(actual) is not str or actual != expected:
            raise ValueError
    if type(receipt.version_semantics) is not str:
        raise ValueError


def _state(state, source, batch, receipt):
    if (
        type(state) is not PrivateAdmissionState
        or state.processing_allowed is not True
        or state.retention_allowed is not True
    ):
        raise ValueError
    _binding(state.binding, source, batch)
    if state.binding != receipt.binding:
        raise ValueError
    # Checks UTC, rollback against the authority's durable mark, age and expiry.
    return make_retention_deadline(
        captured_at=receipt.captured_at, now=state.now, last_observed_at=state.last_observed_at
    )


def _metadata(receipt, now, expires_at):
    binding = receipt.binding
    return {
        "path_key": binding.path_key,
        "version_semantics": receipt.version_semantics,
        "provider_object_id": receipt.provider_object_id,
        "owner_epoch": binding.owner_epoch,
        "connection_epoch": binding.connection_epoch,
        "object_epoch": binding.object_epoch,
        "consent_revision": binding.consent_revision,
        "policy_revision": binding.policy_revision,
        "lifecycle": {
            "revision": "private-retention-v1",
            "captured_at": _stamp(receipt.captured_at),
            "created_at": _stamp(now),
            "expires_at": _stamp(expires_at),
        },
    }


def _version(assertion, source, receipt, first, final):
    if type(assertion) is not ExactVersionAssertion or type(assertion.request) is not ContentReadRequest:
        raise ValueError
    request = assertion.request
    if (
        any(
            type(value) is not int
            for value in (request.tenant_id, request.actor_id, request.connection_id, request.object_id)
        )
        or type(request.nonce) is not str
        or request != source.context.request
    ):
        raise ValueError
    for actual, expected in (
        (assertion.raw_sha256, receipt.raw_sha256),
        (assertion.provider_version, receipt.provider_version),
        (assertion.version_semantics, receipt.version_semantics),
    ):
        if type(actual) is not str or actual != expected:
            raise ValueError
    if (
        type(assertion.provider_object_id) is not type(receipt.provider_object_id)
        or assertion.provider_object_id != receipt.provider_object_id
        or not first.now <= _utc(assertion.checked_at) <= final.now
    ):
        raise ValueError


async def _prepare(source, batch, receipt, authority, versions, deadline):
    _receipt(source, batch, receipt)
    _live(deadline)
    first = await authority.authorize(source.context.request)
    _live(deadline)
    expiry = _state(first, source, batch, receipt)
    # Reject malformed capture metadata/batch BEFORE the version boundary.
    serialize_same_read_payload(source, batch, metadata=_metadata(receipt, first.now, expiry.expires_at))
    _live(deadline)
    assertion = await versions.verify(source, receipt)
    _live(deadline)
    final = await authority.authorize(source.context.request)
    _live(deadline)
    expiry = _state(final, source, batch, receipt)
    if final.now < first.now or final.last_observed_at < first.now:
        raise ValueError
    _version(assertion, source, receipt, first, final)
    payload = serialize_same_read_payload(source, batch, metadata=_metadata(receipt, final.now, expiry.expires_at))
    if not retention_quota_allows(payload, final.quota):
        raise ValueError
    _live(deadline)
    return payload


async def prepare_private_payload(
    source: ReadPlainText,
    batch: InsightCandidateBatch,
    receipt: PrivateReadReceipt,
    *,
    authority: PrivateAdmissionAuthority | None = None,
    versions: ExactVersionAuthority | None = None,
) -> bytes:
    """Prepare canonical bytes using only trusted injected synthetic boundaries.

    Exclusive application-owned inputs, no concurrent mutation. Receipt issuance,
    durable clock/epochs/consent, real exact-version semantics and transaction-time
    rechecks/reservations are NOT implemented or proven here. Returned bytes are
    an unregistered preview, never authority for a save or a promise that capacity
    is reserved. Full graph fabrication remains outside the consistency boundary.
    One cooperative 5s budget; sanitized ordinary failures; cancellation propagates.
    """
    try:
        if authority is None or versions is None:
            raise ValueError
        deadline = asyncio.get_running_loop().time() + ADMISSION_TIMEOUT
        async with asyncio.timeout(ADMISSION_TIMEOUT):
            return await _prepare(source, batch, receipt, authority, versions, deadline)
    except Exception:
        pass
    raise PrivateAdmissionUnavailable()
