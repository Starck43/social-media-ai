"""Explicit, version-bound file transfer for the local connector (P18-T06).

Nothing here is reachable without a user-confirmed consent record for one object and
one destination, and no byte leaves the device unless a reviewed exact-version issuer
can assert the object's version. The module performs no network I/O; it produces a
bounded, verified stream that the cloud side stages privately.
"""

from __future__ import annotations

from .consent import FetchConsent, FetchConsentError
from .fetch import ExactVersionIssuer, FetchError, FetchResult, LocalFetcher
from .version import (
    FETCH_TIMEOUT_SECONDS,
    MAX_FETCH_BYTES,
    FileVersion,
    VersionEvidence,
    compare_versions,
)

__all__ = [
    "ExactVersionIssuer",
    "FETCH_TIMEOUT_SECONDS",
    "FetchConsent",
    "FetchConsentError",
    "FetchError",
    "FetchResult",
    "FileVersion",
    "LocalFetcher",
    "MAX_FETCH_BYTES",
    "VersionEvidence",
    "compare_versions",
]
