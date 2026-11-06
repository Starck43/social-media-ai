"""Standalone local file connector primitives.

This package intentionally has no dependency on the server application. It owns
selected-root validation, the local SQLite index, and outbound-only boundaries.
"""

from .config import ConnectorConfig
from .index import IndexEngine, IndexStore, ScanReport
from .paths import PathSafetyError, SelectedRoot, canonicalize_root, iter_files, resolve_relative

__all__ = [
    "ConnectorConfig",
    "IndexEngine",
    "IndexStore",
    "PathSafetyError",
    "ScanReport",
    "SelectedRoot",
    "canonicalize_root",
    "iter_files",
    "resolve_relative",
]
