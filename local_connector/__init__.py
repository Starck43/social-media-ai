"""Standalone local-only connector development scaffold.

This package intentionally has no dependency on the server application. It owns
selected-root validation and a private local SQLite index. Device sessions are
disabled until the reviewed protocol is implemented by later P18 work.
"""

from .config import ConnectorConfig
from .extract.limits import DocumentLimits
from .index import IndexEngine, IndexStore, ScanReport
from .paths import PathSafetyError, SelectedRoot, canonicalize_root, iter_files, resolve_relative
from .runtime import ConnectorRuntime

__all__ = [
    "ConnectorConfig",
    "ConnectorRuntime",
    "DocumentLimits",
    "IndexEngine",
    "IndexStore",
    "PathSafetyError",
    "ScanReport",
    "SelectedRoot",
    "canonicalize_root",
    "iter_files",
    "resolve_relative",
]
