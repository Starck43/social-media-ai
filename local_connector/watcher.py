"""Filesystem watcher with a bounded periodic rescan fallback."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Iterable

from .index import IndexEngine, ScanReport

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:  # pragma: no cover - exercised on minimal installations
    FileSystemEventHandler = object  # type: ignore[assignment,misc]
    Observer = None  # type: ignore[assignment]


class ConnectorWatcher:
    """Keep an index current without exposing an inbound HTTP file server."""

    def __init__(self, engine: IndexEngine, roots: Iterable[Path | str], interval: float = 300.0) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        self.engine = engine
        self.roots = tuple(roots)
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._observer = None
        self.last_report = ScanReport()

    def rescan(self) -> ScanReport:
        self.last_report = self.engine.scan(self.roots)
        return self.last_report

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("watcher already started")
        self.rescan()
        if Observer is not None:
            self._observer = Observer()
            handler = _RescanHandler(self)
            for root in self.roots:
                self._observer.schedule(handler, str(root), recursive=True)
            self._observer.start()
        self._thread = threading.Thread(target=self._periodic, name="local-connector-rescan", daemon=True)
        self._thread.start()

    def _periodic(self) -> None:
        while not self._stop.wait(self.interval):
            self.rescan()

    def stop(self) -> None:
        self._stop.set()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


class _RescanHandler(FileSystemEventHandler):
    def __init__(self, watcher: ConnectorWatcher) -> None:
        self.watcher = watcher

    def on_any_event(self, event) -> None:  # noqa: ANN001 - watchdog's callback type
        if not event.is_directory:
            self.watcher.rescan()
