"""Data models, bounds and sanitized errors for local connector search."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from ..paths import SelectedRoot

MAX_CONTROL_BYTES = 65536
MAX_QUERY_BYTES = 4096
MAX_HITS = 50
MAX_SNIPPETS = 8
MAX_SNIPPET_CHARS = 512
MAX_RESPONSE_BYTES = 65536
TIMEOUT_SECONDS = 30
REPLAY_SECONDS = 86400
REPLAY_CAPACITY = 4096

TOKEN_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
HEX32_PATTERN = re.compile(r"^[0-9a-f]{32}$")
HEX64_PATTERN = re.compile(r"^[0-9a-f]{64}$")

PROTOCOL_OPERATIONS = frozenset(
    {
        "health",
        "index.status",
        "search",
        "file.metadata",
        "file.fetch",
        "index.delete",
    }
)
DEFAULT_ENABLED_OPERATIONS = frozenset({"health", "index.status", "search"})
ERROR_CODES = frozenset({"denied", "unavailable", "expired", "limit_exceeded", "stale", "unknown"})
VALID_BOUNDARIES = frozenset({"local_only", "company"})


class SearchError(ValueError):
    """Raised when local search inputs or boundaries are invalid."""


class SearchProtocolError(RuntimeError):
    """Sanitized protocol error with a bounded domain code and no secret/path leakage."""

    def __init__(self, code: str = "unavailable") -> None:
        if code not in ERROR_CODES:
            code = "unavailable"
        self.code = code
        super().__init__(code)


def validate_token(value: object, *, name: str = "token") -> str:
    if type(value) is not str or not TOKEN_PATTERN.fullmatch(value):
        raise SearchError(f"invalid {name}")
    return value


@dataclass(frozen=True)
class RootRegistration:
    """Explicitly registered local root with boundary and consent metadata."""

    root_id: str
    selected: SelectedRoot
    boundary: Literal["local_only", "company"] = "local_only"
    cloud_consented: bool = False
    allow_absolute_paths: bool = False

    def __post_init__(self) -> None:
        validate_token(self.root_id, name="root_id")
        if not isinstance(self.selected, SelectedRoot):
            raise SearchError("selected must be a SelectedRoot")
        if self.boundary not in VALID_BOUNDARIES:
            raise SearchError("invalid data_boundary")
        if type(self.cloud_consented) is not bool or type(self.allow_absolute_paths) is not bool:
            raise SearchError("boolean flags required")
        if self.cloud_consented and self.boundary != "company":
            raise SearchError("local_only roots cannot carry cloud consent")


@dataclass(frozen=True)
class SearchFilters:
    """Bounded structured filters applied to SQL metadata and live file stats."""

    file_types: tuple[str, ...] = ()
    modified_after: int | None = None
    modified_before: int | None = None
    modified_after_ns: int | None = None
    modified_before_ns: int | None = None
    min_size: int | None = None
    max_size: int | None = None
    folders: tuple[str, ...] = field(default_factory=tuple, repr=False)
    roots: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.file_types, list):
            object.__setattr__(self, "file_types", tuple(self.file_types))
        if isinstance(self.folders, list):
            object.__setattr__(self, "folders", tuple(self.folders))
        if isinstance(self.roots, list):
            object.__setattr__(self, "roots", tuple(self.roots))
        for item in self.roots:
            validate_token(item, name="root_id")
        if len(set(self.roots)) != len(self.roots):
            raise SearchError("duplicate root_id filter")
        for size_val in (self.min_size, self.max_size):
            if size_val is not None and (type(size_val) is not int or size_val < 0):
                raise SearchError("size filter must be a non-negative int")
        if self.min_size is not None and self.max_size is not None and self.min_size > self.max_size:
            raise SearchError("min_size cannot exceed max_size")
        for ts in (self.modified_after, self.modified_before, self.modified_after_ns, self.modified_before_ns):
            if ts is not None and (type(ts) is not int or ts < 0):
                raise SearchError("timestamp filter must be a non-negative int")
        effective_after_ns = self.effective_modified_after_ns()
        effective_before_ns = self.effective_modified_before_ns()
        if (
            effective_after_ns is not None
            and effective_before_ns is not None
            and effective_after_ns > effective_before_ns
        ):
            raise SearchError("modified_after cannot exceed modified_before")

    def effective_modified_after_ns(self) -> int | None:
        candidates = []
        if self.modified_after is not None:
            candidates.append(self.modified_after * 1_000_000_000)
        if self.modified_after_ns is not None:
            candidates.append(self.modified_after_ns)
        return max(candidates) if candidates else None

    def effective_modified_before_ns(self) -> int | None:
        candidates = []
        if self.modified_before is not None:
            candidates.append(self.modified_before * 1_000_000_000)
        if self.modified_before_ns is not None:
            candidates.append(self.modified_before_ns)
        return min(candidates) if candidates else None


@dataclass(frozen=True)
class SearchQuery:
    """Validated local search query with strict byte and result bounds."""

    query: str = field(repr=False)
    limit: int = 20
    max_snippet_chars: int = MAX_SNIPPET_CHARS
    max_snippets: int = MAX_SNIPPETS
    filters: SearchFilters = field(default_factory=SearchFilters)
    allow_absolute_paths: bool = False

    def __post_init__(self) -> None:
        if type(self.query) is not str or not self.query.strip():
            raise SearchError("query must be non-empty text")
        if len(self.query.encode("utf-8")) > MAX_QUERY_BYTES:
            raise SearchError("query exceeds 4096 UTF-8 bytes")
        if type(self.limit) is not int or not 1 <= self.limit <= MAX_HITS:
            raise SearchError("limit must be between 1 and 50")
        if type(self.max_snippet_chars) is not int or not 0 <= self.max_snippet_chars <= MAX_SNIPPET_CHARS:
            raise SearchError("max_snippet_chars must be between 0 and 512")
        if type(self.max_snippets) is not int or not 0 <= self.max_snippets <= MAX_SNIPPETS:
            raise SearchError("max_snippets must be between 0 and 8")
        if type(self.allow_absolute_paths) is not bool:
            raise SearchError("allow_absolute_paths must be a bool")

    def __repr__(self) -> str:
        return f"SearchQuery(limit={self.limit}, max_snippet_chars={self.max_snippet_chars}, max_snippets={self.max_snippets})"


@dataclass(frozen=True)
class LocalFileReference:
    """Minimal reference to an indexed local file without leaking forbidden paths."""

    object_id: str
    root_id: str
    relative_path: str = field(repr=False)
    absolute_path: str | None = field(default=None, repr=False)
    page_anchor: str | None = None
    sheet_anchor: str | None = field(default=None, repr=False)
    cell_anchor: str | None = field(default=None, repr=False)
    content_hash: str = ""
    mtime_ns: int = 0
    document_modified: int | None = None
    file_type: str = ""
    size: int = 0

    def wire_page(self) -> int | None:
        if self.page_anchor and self.page_anchor.isdigit():
            value = int(self.page_anchor)
            return value if value > 0 else None
        return None

    def to_wire_dict(self) -> dict[str, object]:
        return {
            "object_id": self.object_id,
            "page": self.wire_page(),
            "path": self.relative_path[:2048],
            "root_id": self.root_id,
            "sheet": self.sheet_anchor[:128] if self.sheet_anchor else None,
        }


@dataclass(frozen=True)
class LocalSearchHit:
    """Ranked search match with bounded snippet and data-boundary label."""

    reference: LocalFileReference
    rank: int
    score: float
    bm25: float
    snippet: str | None = field(default=None, repr=False)
    data_boundary: Literal["local_only", "company"] = "local_only"
    evidence_kind: Literal["local_search_observation"] = "local_search_observation"

    def to_wire_dict(self) -> dict[str, object]:
        if self.data_boundary != "company":
            raise SearchProtocolError("denied")
        return {
            "data_boundary": "company",
            "evidence_kind": self.evidence_kind,
            "rank": self.rank,
            "reference": self.reference.to_wire_dict(),
            "snippet": self.snippet,
        }


@dataclass(frozen=True)
class LocalSearchResult:
    """Result set returned by local search."""

    hits: tuple[LocalSearchHit, ...]
    partial: bool = False
    denied_roots: tuple[str, ...] = ()
