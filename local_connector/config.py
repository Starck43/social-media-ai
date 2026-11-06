"""Configuration for the local-only connector scaffold."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .paths import DEFAULT_EXCLUDED_NAMES


@dataclass
class ConnectorConfig:
    """Local-only configuration; no cloud credential or server object is stored."""

    index_path: Path
    selected_roots: list[Path] = field(default_factory=list)
    excluded_paths: set[str] = field(default_factory=set)
    excluded_names: set[str] = field(default_factory=lambda: set(DEFAULT_EXCLUDED_NAMES))
    rescan_seconds: float = 300.0
    text_extensions: frozenset[str] = frozenset({".txt", ".md", ".markdown", ".csv", ".json", ".log"})
    max_text_bytes: int = 4 * 1024 * 1024

    def __post_init__(self) -> None:
        self.index_path = Path(self.index_path).expanduser()
        self.selected_roots = [Path(root).expanduser() for root in self.selected_roots]
        self.excluded_paths = {str(Path(path).as_posix()) for path in self.excluded_paths}
        if self.rescan_seconds <= 0:
            raise ValueError("rescan_seconds must be positive")
        if self.max_text_bytes <= 0:
            raise ValueError("max_text_bytes must be positive")
