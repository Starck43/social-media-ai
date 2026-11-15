"""User confirmation records that gate ``file.fetch``.

One consent record covers exactly one object, one selected root and one destination,
and only exists after a human confirmed the name, size and destination in the UI. A
consent record is immutable; re-fetching the same object later needs a new one. Batch,
background, scheduled and model-initiated fetches therefore cannot be expressed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

_TOKEN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
MAX_RELATIVE_PATH = 2048


class FetchConsentError(ValueError):
    """Raised when a consent record is malformed, so the fetch must not start."""


@dataclass(frozen=True)
class FetchConsent:
    """Human confirmation for one object and one destination."""

    request_id: str
    object_id: str
    root_id: str
    relative_path: str
    destination: Literal["private_storage"] = "private_storage"
    confirmed_by_user_id: int = 0
    confirmed_at: int = 0
    expected_content_hash: str | None = None

    def __post_init__(self) -> None:
        if type(self.request_id) is not str or not _HEX32.fullmatch(self.request_id):
            raise FetchConsentError("invalid request_id")
        if type(self.object_id) is not str or not _TOKEN.fullmatch(self.object_id):
            raise FetchConsentError("invalid object_id")
        if type(self.root_id) is not str or not _TOKEN.fullmatch(self.root_id):
            raise FetchConsentError("invalid root_id")
        if type(self.relative_path) is not str or not 0 < len(self.relative_path) <= MAX_RELATIVE_PATH:
            raise FetchConsentError("invalid relative_path")
        if self.destination != "private_storage":
            raise FetchConsentError("unsupported destination")
        if type(self.confirmed_by_user_id) is not int or self.confirmed_by_user_id <= 0:
            raise FetchConsentError("fetch requires an explicit user confirmation")
        if type(self.confirmed_at) is not int or self.confirmed_at <= 0:
            raise FetchConsentError("fetch requires a confirmation timestamp")
        if self.expected_content_hash is not None:
            if type(self.expected_content_hash) is not str or not _HEX64.fullmatch(self.expected_content_hash):
                raise FetchConsentError("invalid expected_content_hash")

    def binds(self, *, object_id: str, root_id: str, relative_path: str) -> bool:
        """True only when this confirmation names exactly the object being transferred."""

        return self.object_id == object_id and self.root_id == root_id and self.relative_path == relative_path
