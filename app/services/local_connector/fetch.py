"""Cloud-side staging for one explicitly confirmed ``file.fetch`` (P18-T06).

Nothing here reads a device file, and no byte reaches durable storage until the whole
object has arrived, matched its confirmed content hash and carried a reviewed exact
version assertion. Staged bytes are discarded on every failure path — refusal, timeout,
hash mismatch, size mismatch, abort — so a partial transfer can never be mistaken for a
complete file. Bytes already released to the caller are never claimed back.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from .wire import MAX_FETCH_BYTES, TIMEOUT

CHUNK_BYTES = 256 * 1024
_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
STATES = frozenset({"complete", "denied", "stale", "ineligible", "limit_exceeded", "unavailable", "unknown"})


class FetchError(RuntimeError):
    """Sanitized fetch failure carrying a bounded code, never content or paths."""

    def __init__(self, code: str = "unavailable") -> None:
        self.code = code if code in STATES else "unavailable"
        super().__init__(self.code)


@dataclass(frozen=True)
class FetchConsent:
    """Server-side record of one user confirmation; one object, one destination."""

    request_id: str
    object_id: str
    root_id: str
    relative_path: str
    confirmed_by_user_id: int
    confirmed_at: int
    destination: Literal["private_storage"] = "private_storage"
    expected_content_hash: str | None = None

    def __post_init__(self) -> None:
        if type(self.request_id) is not str or not _HEX32.fullmatch(self.request_id):
            raise FetchError("denied")
        if type(self.object_id) is not str or not _TOKEN.fullmatch(self.object_id):
            raise FetchError("denied")
        if type(self.root_id) is not str or not _TOKEN.fullmatch(self.root_id):
            raise FetchError("denied")
        if type(self.relative_path) is not str or not 0 < len(self.relative_path) <= 2048:
            raise FetchError("denied")
        if self.destination != "private_storage":
            raise FetchError("denied")
        if type(self.confirmed_by_user_id) is not int or self.confirmed_by_user_id <= 0:
            raise FetchError("denied")
        if type(self.confirmed_at) is not int or self.confirmed_at <= 0:
            raise FetchError("denied")
        if self.expected_content_hash is not None and (
            type(self.expected_content_hash) is not str or not _HEX64.fullmatch(self.expected_content_hash)
        ):
            raise FetchError("denied")


class ExactVersionIssuer(Protocol):
    """Reviewed authority that may assert an exact version for one object, or refuse."""

    async def assert_exact_version(self, *, consent: FetchConsent, content_hash: str, size: int) -> str | None: ...


class FetchStaging(Protocol):
    """Private staging area; implements discard/commit or nothing is retained."""

    def stage(self, chunk: bytes) -> None: ...

    def commit(self, *, sha256: str, size: int) -> None: ...

    def discard(self) -> None: ...


@dataclass(frozen=True)
class FetchReceipt:
    request_id: str
    object_id: str
    state: Literal["complete", "denied", "stale", "ineligible", "limit_exceeded", "unavailable", "unknown"]
    sha256: str | None = None
    size: int = 0
    version_assertion: str | None = None
    evidence_kind: Literal["cloud_fetch_receipt"] = "cloud_fetch_receipt"

    @property
    def complete(self) -> bool:
        return self.state == "complete"


class FetchTransfer:
    """Accumulates one object's bytes and commits them only when fully verified."""

    def __init__(
        self,
        *,
        consent: FetchConsent,
        staging: FetchStaging,
        content_hash: str,
        version_assertion: str,
        max_bytes: int = MAX_FETCH_BYTES,
        timeout_seconds: int = TIMEOUT,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._consent = consent
        self._staging = staging
        self._content_hash = content_hash
        self._assertion = version_assertion
        self._max_bytes = max_bytes
        self._deadline = clock() + timeout_seconds
        self._clock = clock
        self._digest = hashlib.sha256()
        self._size = 0
        self._closed = False

    def feed(self, chunk: bytes) -> None:
        if self._closed:
            raise FetchError("unavailable")
        if type(chunk) is not bytes or len(chunk) > CHUNK_BYTES:
            self.abort()
            raise FetchError("limit_exceeded")
        if self._clock() > self._deadline:
            self.abort()
            raise FetchError("limit_exceeded")
        if self._size + len(chunk) > self._max_bytes:
            self.abort()
            raise FetchError("limit_exceeded")
        self._size += len(chunk)
        self._digest.update(chunk)
        self._staging.stage(chunk)

    def finish(self) -> FetchReceipt:
        if self._closed:
            raise FetchError("unavailable")
        observed = self._digest.hexdigest()
        if observed != self._content_hash or self._size <= 0:
            self.abort()
            return self._receipt("stale")
        self._closed = True
        self._staging.commit(sha256=observed, size=self._size)
        return self._receipt("complete", sha256=observed)

    def abort(self) -> None:
        if not self._closed:
            self._closed = True
            self._staging.discard()

    def __enter__(self) -> FetchTransfer:
        return self

    def __exit__(self, *_exc) -> None:
        self.abort()

    def _receipt(self, state: str, *, sha256: str | None = None) -> FetchReceipt:
        return FetchReceipt(
            request_id=self._consent.request_id,
            object_id=self._consent.object_id,
            state=state,  # type: ignore[arg-type]
            sha256=sha256,
            size=self._size if state == "complete" else 0,
            version_assertion=self._assertion if state == "complete" else None,
        )


class LocalFetchService:
    """Opens exactly one staging transfer per confirmed object, or refuses."""

    def __init__(
        self,
        *,
        staging_factory: Callable[[FetchConsent], FetchStaging],
        issuer: ExactVersionIssuer | None = None,
        max_bytes: int = MAX_FETCH_BYTES,
        timeout_seconds: int = TIMEOUT,
    ) -> None:
        if not 0 < max_bytes <= MAX_FETCH_BYTES or not 0 < timeout_seconds <= TIMEOUT:
            raise FetchError("limit_exceeded")
        self._factory = staging_factory
        self._issuer = issuer
        self._max_bytes = max_bytes
        self._timeout_seconds = timeout_seconds

    async def begin(self, consent: FetchConsent, *, content_hash: str, size: int) -> FetchTransfer:
        """Refuse before staging anything unless the object is eligible and versioned."""

        if not isinstance(consent, FetchConsent):
            raise FetchError("denied")
        if type(content_hash) is not str or not _HEX64.fullmatch(content_hash):
            raise FetchError("denied")
        if type(size) is not int or size < 0:
            raise FetchError("denied")
        if size > self._max_bytes:
            raise FetchError("limit_exceeded")
        if consent.expected_content_hash is not None and consent.expected_content_hash != content_hash:
            raise FetchError("stale")
        if self._issuer is None:
            raise FetchError("ineligible")
        assertion = await self._issuer.assert_exact_version(consent=consent, content_hash=content_hash, size=size)
        if type(assertion) is not str or not 0 < len(assertion) <= 128:
            raise FetchError("ineligible")
        return FetchTransfer(
            consent=consent,
            staging=self._factory(consent),
            content_hash=content_hash,
            version_assertion=assertion,
            max_bytes=self._max_bytes,
            timeout_seconds=self._timeout_seconds,
        )
