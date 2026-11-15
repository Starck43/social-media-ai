"""P18-T06 cloud staging: consent, exact-version eligibility, ceilings and discard."""

from __future__ import annotations

import hashlib

import pytest

from app.services.local_connector.fetch import (
    CHUNK_BYTES,
    FetchConsent,
    FetchError,
    FetchTransfer,
    LocalFetchService,
)
from app.services.local_connector.wire import MAX_FETCH_BYTES, TIMEOUT

REQUEST_ID = "a" * 32
PAYLOAD = b"company report body " * 64
CONTENT_HASH = hashlib.sha256(PAYLOAD).hexdigest()


class Staging:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.committed: tuple[str, int] | None = None
        self.discarded = False

    def stage(self, chunk: bytes) -> None:
        if self.discarded or self.committed is not None:
            raise AssertionError("staging used after it was closed")
        self.chunks.append(chunk)

    def commit(self, *, sha256: str, size: int) -> None:
        self.committed = (sha256, size)

    def discard(self) -> None:
        self.discarded = True
        self.chunks.clear()


class Issuer:
    def __init__(self, token: str | None = "exact-version-1") -> None:
        self.token = token
        self.calls: list[dict] = []

    async def assert_exact_version(self, *, consent, content_hash, size):
        self.calls.append({"object_id": consent.object_id, "hash": content_hash, "size": size})
        return self.token


def consent(**overrides) -> FetchConsent:
    payload = {
        "request_id": REQUEST_ID,
        "object_id": "object-1",
        "root_id": "root-a",
        "relative_path": "docs/report.txt",
        "confirmed_by_user_id": 7,
        "confirmed_at": 1_700_000_000,
    }
    payload.update(overrides)
    return FetchConsent(**payload)


def service(staging: Staging, *, issuer=None, max_bytes: int = MAX_FETCH_BYTES) -> LocalFetchService:
    return LocalFetchService(staging_factory=lambda _consent: staging, issuer=issuer, max_bytes=max_bytes)


@pytest.mark.parametrize(
    "overrides",
    [
        {"confirmed_by_user_id": 0},
        {"confirmed_at": 0},
        {"object_id": ""},
        {"relative_path": ""},
        {"destination": "cloud_catalog"},
        {"request_id": "not-a-request-id"},
        {"expected_content_hash": "short"},
    ],
)
def test_consent_rejects_unconfirmed_or_malformed_records(overrides) -> None:
    with pytest.raises(FetchError):
        consent(**overrides)


@pytest.mark.asyncio
async def test_begin_refuses_without_a_reviewed_exact_version_issuer() -> None:
    staging = Staging()
    with pytest.raises(FetchError) as refused:
        await service(staging).begin(consent(), content_hash=CONTENT_HASH, size=len(PAYLOAD))
    assert refused.value.code == "ineligible"
    assert staging.chunks == [] and staging.committed is None

    with pytest.raises(FetchError):
        await service(staging, issuer=Issuer(token=None)).begin(consent(), content_hash=CONTENT_HASH, size=len(PAYLOAD))


@pytest.mark.asyncio
async def test_begin_enforces_hash_size_and_ceiling_before_staging() -> None:
    staging = Staging()
    issuer = Issuer()
    with pytest.raises(FetchError) as stale:
        await service(staging, issuer=issuer).begin(
            consent(expected_content_hash="b" * 64), content_hash=CONTENT_HASH, size=len(PAYLOAD)
        )
    assert stale.value.code == "stale"
    with pytest.raises(FetchError) as oversized:
        await service(staging, issuer=issuer, max_bytes=16).begin(
            consent(), content_hash=CONTENT_HASH, size=len(PAYLOAD)
        )
    assert oversized.value.code == "limit_exceeded"
    assert issuer.calls == []
    assert staging.chunks == []


@pytest.mark.asyncio
async def test_complete_transfer_commits_only_after_full_verification() -> None:
    staging = Staging()
    transfer = await service(staging, issuer=Issuer()).begin(consent(), content_hash=CONTENT_HASH, size=len(PAYLOAD))
    transfer.feed(PAYLOAD)
    assert staging.committed is None

    receipt = transfer.finish()

    assert receipt.state == "complete"
    assert receipt.sha256 == CONTENT_HASH
    assert receipt.size == len(PAYLOAD)
    assert receipt.version_assertion == "exact-version-1"
    assert staging.committed == (CONTENT_HASH, len(PAYLOAD))


@pytest.mark.asyncio
async def test_hash_mismatch_discards_staged_bytes_and_reports_stale() -> None:
    staging = Staging()
    transfer = await service(staging, issuer=Issuer()).begin(consent(), content_hash=CONTENT_HASH, size=len(PAYLOAD))
    transfer.feed(b"company report body x" * 64)

    receipt = transfer.finish()

    assert receipt.state == "stale"
    assert receipt.sha256 is None and receipt.size == 0
    assert staging.discarded is True and staging.committed is None


@pytest.mark.asyncio
async def test_stream_ceiling_and_chunk_bounds_discard_and_fail_closed() -> None:
    staging = Staging()
    transfer = await service(staging, issuer=Issuer(), max_bytes=16).begin(
        consent(), content_hash=CONTENT_HASH, size=16
    )
    with pytest.raises(FetchError):
        transfer.feed(b"x" * 32)
    assert staging.discarded is True

    other = Staging()
    transfer = await service(other, issuer=Issuer()).begin(consent(), content_hash=CONTENT_HASH, size=len(PAYLOAD))
    with pytest.raises(FetchError):
        transfer.feed(b"x" * (CHUNK_BYTES + 1))
    assert other.discarded is True


@pytest.mark.asyncio
async def test_deadline_and_abort_discard_staging() -> None:
    staging = Staging()
    ticks = iter([0.0, TIMEOUT + 1.0])
    transfer = FetchTransfer(
        consent=consent(),
        staging=staging,
        content_hash=CONTENT_HASH,
        version_assertion="exact-version-1",
        clock=lambda: next(ticks, TIMEOUT + 1.0),
    )
    with pytest.raises(FetchError):
        transfer.feed(PAYLOAD)
    assert staging.discarded is True

    aborted = Staging()
    with pytest.raises(RuntimeError):
        with await service(aborted, issuer=Issuer()).begin(consent(), content_hash=CONTENT_HASH, size=len(PAYLOAD)):
            raise RuntimeError("transport died")
    assert aborted.discarded is True
    assert aborted.committed is None


def test_service_limits_are_bounded_by_the_protocol_ceiling() -> None:
    staging = Staging()
    with pytest.raises(FetchError):
        LocalFetchService(staging_factory=lambda _c: staging, max_bytes=MAX_FETCH_BYTES + 1)
    with pytest.raises(FetchError):
        LocalFetchService(staging_factory=lambda _c: staging, timeout_seconds=0)
