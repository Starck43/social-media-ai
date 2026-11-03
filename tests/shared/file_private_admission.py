"""Synthetic trusted-boundary assertions only; no DB, settings or providers."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.integrations.file_workspace.insight_candidates import InsightCandidate, InsightCandidateBatch
from app.integrations.file_workspace.line_evidence import LineRange, build_line_evidence
from app.integrations.file_workspace.private_admission import (
    ExactVersionAssertion,
    PrivateAdmissionState,
    PrivateReadReceipt,
    PrivateScopeBinding,
)
from app.integrations.file_workspace.retention_quota import RetentionQuotaSnapshot, RetentionQuotaUsage
from tests.shared.file_content_read import synthetic_plain_text_read


def synthetic_private_admission():
    source = synthetic_plain_text_read(b"first\r\nsecond\nthird\n", provider_version="synthetic-exact-version")
    source = replace(source, context=replace(source.context, root_key="a" * 64))
    evidence = build_line_evidence(source, ranges=(LineRange(1, 1), LineRange(3, 3)))
    batch = InsightCandidateBatch("b" * 32, evidence, (InsightCandidate("Unverified", (1, 2)),))
    binding = PrivateScopeBinding(source.context, "c" * 64, 2, "d" * 32, "e" * 32, "f" * 32, "consent-v1", "policy-v1")
    captured = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
    receipt = PrivateReadReceipt(
        binding,
        captured,
        source.extraction.raw_sha256,
        source.extraction.text_sha256,
        source.extraction.parser_version,
        source.provider_version,
        "synthetic-bytes-v1",
        None,
    )
    quota = RetentionQuotaSnapshot(0, 0, *(RetentionQuotaUsage(0, 0) for _ in range(3)))
    first = PrivateAdmissionState(binding, True, True, captured + timedelta(minutes=1), captured, quota)
    final = replace(first, now=first.now + timedelta(seconds=1), last_observed_at=first.now)
    assertion = ExactVersionAssertion(
        source.context.request,
        receipt.raw_sha256,
        receipt.provider_version,
        receipt.version_semantics,
        receipt.provider_object_id,
        first.now + timedelta(microseconds=1),
    )
    return source, batch, receipt, first, final, assertion


class SyntheticPrivateAdmissionAuthority:
    def __init__(self, first, final, events):
        self.states = (first, final)
        self.events = events
        self.requests = []
        self.error_at = None

    async def authorize(self, request):
        self.requests.append(request)
        self.events.append("authorize")
        if len(self.requests) == self.error_at:
            raise PermissionError("private-account-secret")
        return self.states[len(self.requests) - 1]


class SyntheticExactVersionAuthority:
    def __init__(self, assertion, events):
        self.assertion = assertion
        self.events = events
        self.inputs = []
        self.hook = None
        self.error = None

    async def verify(self, source, receipt):
        self.inputs.append((source, receipt))
        self.events.append("version")
        if self.hook:
            await self.hook()
        if self.error:
            raise self.error
        return self.assertion
