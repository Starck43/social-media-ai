"""Version evidence for one indexed object.

The protocol forbids inventing an immutable "exact version" from mtime, a path or a
hash alone, so this module only compares an indexed observation with the live file and
reports ``current``/``changed``/``unknown``. Turning that observation into an exact
version assertion is the job of a reviewed issuer (see ``fetch.ExactVersionIssuer``);
without one the object is ineligible and is never served.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

MAX_FETCH_BYTES = 10 * 1024 * 1024
FETCH_TIMEOUT_SECONDS = 30
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class FetchError(RuntimeError):
    """Sanitized fetch failure with a bounded domain code and no path or content leakage."""

    CODES = frozenset({"denied", "stale", "ineligible", "limit_exceeded", "unavailable", "unknown"})

    def __init__(self, code: str = "unavailable") -> None:
        if code not in self.CODES:
            code = "unavailable"
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class FileVersion:
    """Observation of one file: content digest plus the cheap stat identity."""

    content_hash: str
    size: int
    mtime_ns: int

    def __post_init__(self) -> None:
        if type(self.content_hash) is not str or not _HEX64.fullmatch(self.content_hash):
            raise FetchError("denied")
        if type(self.size) is not int or self.size < 0:
            raise FetchError("denied")
        if type(self.mtime_ns) is not int or self.mtime_ns < 0:
            raise FetchError("denied")


@dataclass(frozen=True)
class VersionEvidence:
    """Comparison of the indexed observation with what the device sees now."""

    state: Literal["current", "changed", "unknown"]
    indexed: FileVersion | None
    observed: FileVersion | None
    evidence_kind: Literal["local_version_evidence"] = "local_version_evidence"

    def __post_init__(self) -> None:
        if self.state not in {"current", "changed", "unknown"}:
            raise FetchError("denied")
        if self.state == "current" and (self.indexed is None or self.observed is None):
            raise FetchError("denied")
        if self.state == "changed" and (self.indexed is None or self.observed is None):
            raise FetchError("denied")


def compare_versions(indexed: FileVersion | None, observed: FileVersion | None) -> VersionEvidence:
    """Classify an object without ever claiming a stronger version than observed."""

    if indexed is None or observed is None:
        return VersionEvidence(state="unknown", indexed=indexed, observed=observed)
    if (
        indexed.content_hash == observed.content_hash
        and indexed.size == observed.size
        and indexed.mtime_ns == observed.mtime_ns
    ):
        return VersionEvidence(state="current", indexed=indexed, observed=observed)
    return VersionEvidence(state="changed", indexed=indexed, observed=observed)
