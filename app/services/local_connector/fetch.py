"""Cloud-side staging, version drift tracking, and device snippet purge for P18-T06.

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
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal, Protocol

from .wire import MAX_FETCH_BYTES, TIMEOUT

CHUNK_BYTES = 256 * 1024
FETCH_RETENTION_SECONDS = 86400
MAX_FILE_NAME = 255
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
    file_name: str | None = None
    expected_size: int | None = None
    device_id: int | None = None

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
        if self.file_name is not None:
            if (
                type(self.file_name) is not str
                or not 0 < len(self.file_name) <= MAX_FILE_NAME
                or "/" in self.file_name
                or "\x00" in self.file_name
                or self.file_name in {".", ".."}
                or self.file_name != PurePosixPath(self.relative_path).name
            ):
                raise FetchError("denied")
        if self.expected_size is not None and (
            type(self.expected_size) is not int or not 0 <= self.expected_size <= MAX_FETCH_BYTES
        ):
            raise FetchError("denied")
        if self.device_id is not None and (type(self.device_id) is not int or self.device_id <= 0):
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


@dataclass(frozen=True)
class FetchedObjectRecord:
    """Cloud evidence retained for one verified private-storage fetch."""

    request_id: str
    object_id: str
    root_id: str
    destination: Literal["private_storage"]
    content_hash: str
    size: int
    version_assertion: str
    fetched_at: int
    expires_at: int
    device_id: int | None = None


@dataclass(frozen=True)
class CloudVersionStatus:
    """Post-fetch comparison between retained cloud evidence and a live device observation."""

    state: Literal["current", "local_changed_after_fetch", "stale", "unknown"]
    fetched_content_hash: str
    fetched_size: int
    live_content_hash: str | None = None
    live_size: int | None = None
    evidence_kind: Literal["cloud_version_status"] = "cloud_version_status"


def compare_cloud_version(
    fetched: FetchedObjectRecord,
    *,
    live_content_hash: str | None = None,
    live_size: int | None = None,
    index_deleted: bool = False,
) -> CloudVersionStatus:
    """Flag local post-fetch drift without altering or discarding retained cloud bytes."""

    if index_deleted:
        return CloudVersionStatus(
            state="stale",
            fetched_content_hash=fetched.content_hash,
            fetched_size=fetched.size,
            live_content_hash=live_content_hash,
            live_size=live_size,
        )
    if live_content_hash is None or live_size is None:
        return CloudVersionStatus(
            state="unknown",
            fetched_content_hash=fetched.content_hash,
            fetched_size=fetched.size,
        )
    if live_content_hash != fetched.content_hash or live_size != fetched.size:
        return CloudVersionStatus(
            state="local_changed_after_fetch",
            fetched_content_hash=fetched.content_hash,
            fetched_size=fetched.size,
            live_content_hash=live_content_hash,
            live_size=live_size,
        )
    return CloudVersionStatus(
        state="current",
        fetched_content_hash=fetched.content_hash,
        fetched_size=fetched.size,
        live_content_hash=live_content_hash,
        live_size=live_size,
    )


@dataclass
class DeviceSnippetCache:
    """Bounded server-side cache of device search snippets, purgeable on revoke/delete."""

    max_per_device: int = 128
    _entries: dict[int, list[dict[str, object]]] = field(default_factory=dict)

    def store(self, device_id: int, items: list[dict[str, object]]) -> None:
        if type(device_id) is not int or device_id <= 0:
            raise FetchError("denied")
        bounded = [dict(item) for item in items[: self.max_per_device]]
        self._entries[device_id] = bounded

    def list_for_device(self, device_id: int) -> list[dict[str, object]]:
        return [dict(item) for item in self._entries.get(device_id, [])]

    def purge_device(self, device_id: int) -> int:
        removed = self._entries.pop(device_id, [])
        return len(removed)


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
        wall_clock: Callable[[], int] | None = None,
        on_commit: Callable[[FetchedObjectRecord], None] | None = None,
    ) -> None:
        self._consent = consent
        self._staging = staging
        self._content_hash = content_hash
        self._assertion = version_assertion
        self._max_bytes = max_bytes
        self._deadline = clock() + timeout_seconds
        self._clock = clock
        self._wall_clock = wall_clock or (lambda: int(time.time()))
        self._on_commit = on_commit
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
        if self._consent.expected_size is not None and self._size != self._consent.expected_size:
            self.abort()
            return self._receipt("stale")
        self._closed = True
        self._staging.commit(sha256=observed, size=self._size)
        if self._on_commit is not None:
            now_ts = int(self._wall_clock())
            self._on_commit(
                FetchedObjectRecord(
                    request_id=self._consent.request_id,
                    object_id=self._consent.object_id,
                    root_id=self._consent.root_id,
                    destination=self._consent.destination,
                    content_hash=observed,
                    size=self._size,
                    version_assertion=self._assertion,
                    fetched_at=now_ts,
                    expires_at=now_ts + FETCH_RETENTION_SECONDS,
                    device_id=self._consent.device_id,
                )
            )
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
        snippet_cache: DeviceSnippetCache | None = None,
        retention_seconds: int = FETCH_RETENTION_SECONDS,
    ) -> None:
        if not 0 < max_bytes <= MAX_FETCH_BYTES or not 0 < timeout_seconds <= TIMEOUT:
            raise FetchError("limit_exceeded")
        self._factory = staging_factory
        self._issuer = issuer
        self._max_bytes = max_bytes
        self._timeout_seconds = timeout_seconds
        self.snippet_cache = snippet_cache if snippet_cache is not None else DeviceSnippetCache()
        self._retention_seconds = retention_seconds
        self._records: dict[str, FetchedObjectRecord] = {}

    def record_for(self, request_id: str) -> FetchedObjectRecord | None:
        return self._records.get(request_id)

    def purge_device(self, device_id: int) -> dict[str, int]:
        """Purge server-side cached snippets and device-scoped fetch records on revoke."""

        snippets_removed = self.snippet_cache.purge_device(device_id)
        records_removed = 0
        for req_id in [key for key, rec in self._records.items() if rec.device_id == device_id]:
            self._records.pop(req_id, None)
            records_removed += 1
        return {"snippets_removed": snippets_removed, "records_removed": records_removed}

    def _store_record(self, record: FetchedObjectRecord) -> None:
        self._records[record.request_id] = FetchedObjectRecord(
            request_id=record.request_id,
            object_id=record.object_id,
            root_id=record.root_id,
            destination=record.destination,
            content_hash=record.content_hash,
            size=record.size,
            version_assertion=record.version_assertion,
            fetched_at=record.fetched_at,
            expires_at=record.fetched_at + self._retention_seconds,
            device_id=record.device_id,
        )

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
        if consent.expected_size is not None and consent.expected_size != size:
            raise FetchError("stale")
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
            on_commit=self._store_record,
        )
