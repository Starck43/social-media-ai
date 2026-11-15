"""Single-object, consent-gated, version-bound fetch of one indexed file.

The fetcher never enumerates, never batches and never touches the network: it verifies
confirmation, scope, current OS access and version eligibility, then streams at most one
object within the P18 ceiling so the cloud side can stage it privately. Partial bytes are
never reported as complete, and an object without a reviewed exact version assertion is
ineligible rather than served under a weaker claim.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from ..index import FileRecord, IndexStore
from ..paths import PathSafetyError, SelectedRoot, open_selected_file
from ..search.acl import verify_root_and_file_access
from ..search.models import RootRegistration
from .consent import FetchConsent, FetchConsentError
from .version import FETCH_TIMEOUT_SECONDS, MAX_FETCH_BYTES, FetchError, FileVersion, compare_versions

CHUNK_BYTES = 256 * 1024


class ExactVersionIssuer(Protocol):
    """Reviewed authority that can assert an exact version, or refuse to."""

    def assert_exact_version(
        self, *, object_id: str, root_id: str, relative_path: str, version: FileVersion
    ) -> str | None: ...


@dataclass(frozen=True)
class FetchResult:
    """Outcome of one fetch attempt; only ``complete`` carries deliverable bytes."""

    request_id: str
    object_id: str
    root_id: str
    state: Literal["complete", "denied", "stale", "ineligible", "limit_exceeded", "unavailable", "unknown"]
    version: FileVersion | None = None
    version_assertion: str | None = None
    sha256: str | None = None
    bytes_read: int = 0
    evidence_kind: Literal["local_fetch"] = "local_fetch"

    @property
    def complete(self) -> bool:
        return self.state == "complete"


def _root_hash(root: SelectedRoot) -> str:
    identity = f"{root.path}\x00{root.device}\x00{root.inode}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


class LocalFetcher:
    """Streams at most one confirmed object per call."""

    def __init__(
        self,
        store: IndexStore,
        roots: Mapping[str, SelectedRoot],
        *,
        max_bytes: int = MAX_FETCH_BYTES,
        timeout_seconds: int = FETCH_TIMEOUT_SECONDS,
        folder_policy: object | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0 < max_bytes <= MAX_FETCH_BYTES:
            raise FetchError("limit_exceeded")
        if not 0 < timeout_seconds <= FETCH_TIMEOUT_SECONDS:
            raise FetchError("limit_exceeded")
        self._store = store
        self._roots = dict(roots)
        self._max_bytes = max_bytes
        self._timeout = timeout_seconds
        self._policy = folder_policy
        self._clock = clock

    def fetch(
        self,
        consent: FetchConsent,
        *,
        issuer: ExactVersionIssuer | None = None,
        sink: Callable[[bytes], None] | None = None,
    ) -> FetchResult:
        """Fetch one object named by ``consent``; never a second object, never a retry."""

        if not isinstance(consent, FetchConsent):
            raise FetchConsentError("fetch requires a user confirmation record")
        record = self._store.get_by_hash(consent.object_id)
        if record is None:
            return self._result(consent, "denied")
        if not consent.binds(object_id=record.path_hash, root_id=consent.root_id, relative_path=record.relative_path):
            return self._result(consent, "denied")
        if consent.expected_content_hash is not None and consent.expected_content_hash != record.content_hash:
            return self._result(consent, "stale")
        root = self._roots.get(consent.root_id)
        if root is None or not isinstance(root, SelectedRoot):
            return self._result(consent, "denied")
        if _root_hash(root) != record.root_hash:
            return self._result(consent, "stale")

        try:
            root.verify()
        except (OSError, PathSafetyError):
            return self._result(
                consent, "stale", version=FileVersion(record.content_hash, record.size, record.mtime_ns)
            )
        registration = RootRegistration(consent.root_id, root, boundary="company", cloud_consented=True)
        live = verify_root_and_file_access(registration, record.relative_path, folder_policy=self._policy)
        if live is None:
            return self._result(consent, "denied")
        indexed = FileVersion(record.content_hash, record.size, record.mtime_ns)
        stat_version = FileVersion(record.content_hash, live.st_size, live.st_mtime_ns)
        if compare_versions(indexed, stat_version).state != "current":
            return self._result(consent, "stale", version=indexed)
        if issuer is None:
            return self._result(consent, "ineligible", version=indexed)
        assertion = issuer.assert_exact_version(
            object_id=record.path_hash,
            root_id=consent.root_id,
            relative_path=record.relative_path,
            version=indexed,
        )
        if type(assertion) is not str or not assertion:
            return self._result(consent, "ineligible", version=indexed)

        return self._stream(consent, record, root, indexed, assertion, sink)

    def _stream(
        self,
        consent: FetchConsent,
        record: FileRecord,
        root: SelectedRoot,
        indexed: FileVersion,
        assertion: str,
        sink: Callable[[bytes], None] | None,
    ) -> FetchResult:
        digest = hashlib.sha256()
        total = 0
        deadline = self._clock() + self._timeout
        try:
            with open_selected_file(root, record.relative_path) as stream:
                while True:
                    if self._clock() > deadline:
                        return self._result(consent, "limit_exceeded", version=indexed, bytes_read=total)
                    remaining = self._max_bytes - total
                    block = stream.read(min(CHUNK_BYTES, remaining))
                    if not block:
                        break
                    total += len(block)
                    if total >= self._max_bytes and stream.read(1):
                        return self._result(consent, "limit_exceeded", version=indexed, bytes_read=self._max_bytes)
                    digest.update(block)
                    if sink is not None:
                        sink(block)
        except (OSError, PathSafetyError):
            return self._result(consent, "stale", version=indexed, bytes_read=total)

        observed = FileVersion(digest.hexdigest(), total, indexed.mtime_ns)
        if compare_versions(indexed, observed).state != "current":
            return self._result(consent, "stale", version=indexed, bytes_read=total)
        return FetchResult(
            request_id=consent.request_id,
            object_id=record.path_hash,
            root_id=consent.root_id,
            state="complete",
            version=indexed,
            version_assertion=assertion,
            sha256=observed.content_hash,
            bytes_read=total,
        )

    @staticmethod
    def _result(
        consent: FetchConsent,
        state: str,
        *,
        version: FileVersion | None = None,
        bytes_read: int = 0,
    ) -> FetchResult:
        return FetchResult(
            request_id=consent.request_id,
            object_id=consent.object_id,
            root_id=consent.root_id,
            state=state,  # type: ignore[arg-type]
            version=version,
            bytes_read=bytes_read,
        )
