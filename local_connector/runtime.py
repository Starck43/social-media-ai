"""Small local-only composition root for the desktop connector core."""

from __future__ import annotations

from pathlib import Path

from .config import ConnectorConfig
from .index import IndexEngine, IndexStore, ScanReport
from .paths import SelectedRoot
from .ui import FolderSelection, LocalStatusServer
from .watcher import ConnectorWatcher


class ConnectorRuntime:
    """Wire selected roots, SQLite, watcher and loopback status UI together."""

    def __init__(self, config: ConnectorConfig) -> None:
        self.config = config
        self.selection = FolderSelection()
        self.store = IndexStore(config.index_path)
        self.engine = IndexEngine(
            self.store,
            text_extensions=config.text_extensions,
            max_text_bytes=config.max_text_bytes,
        )
        self.watcher: ConnectorWatcher | None = None
        self.status = LocalStatusServer(self.selection, self.status_payload)

    def select_roots(self, roots: list[str | Path]) -> tuple[SelectedRoot, ...]:
        selected = self.selection.replace([str(root) for root in roots])
        self.config.selected_roots = [root.path for root in selected]
        return selected

    def scan(self) -> ScanReport:
        return self.engine.scan(
            self.config.selected_roots,
            excluded_paths=self.config.excluded_paths,
            excluded_names=self.config.excluded_names,
        )

    def start(self) -> tuple[str, int]:
        if not self.config.selected_roots:
            raise ValueError("select at least one root before starting")
        self.watcher = ConnectorWatcher(self.engine, self.config.selected_roots, self.config.rescan_seconds)
        self.watcher.start()
        return self.status.start()

    def status_payload(self) -> dict[str, object]:
        report = self.watcher.last_report if self.watcher else ScanReport()
        return {
            "roots": [str(root) for root in self.config.selected_roots],
            "indexed": report.indexed,
            "skipped": report.skipped,
            "deleted": report.deleted,
            "errors": list(report.errors),
        }

    def delete_index(self, *, confirmed: bool) -> None:
        if not confirmed:
            raise PermissionError("index deletion requires explicit confirmation")
        self.store.delete_all()

    def close(self) -> None:
        self.status.close()
        if self.watcher is not None:
            self.watcher.stop()
            self.watcher = None
        self.store.close()
