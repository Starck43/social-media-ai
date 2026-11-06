"""Serialized filesystem watcher with periodic polling fallback."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Iterable

from .index import IndexEngine, ScanReport

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:  # pragma: no cover - exercised on minimal installations
    FileSystemEventHandler = object  # type: ignore[assignment,misc]
    Observer = None  # type: ignore[assignment]


class ConnectorWatcher:
    """Rescan selected roots without reacting to file-open or index-write events."""

    def __init__(
        self,
        engine: IndexEngine,
        roots: Iterable[Path | str],
        interval: float = 300.0,
        *,
        excluded_paths: Iterable[str] = (),
        excluded_names: Iterable[str] = (),
        on_root_error: Callable[[ScanReport], None] | None = None,
    ) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        self.engine = engine
        self.roots = tuple(roots)
        self.interval = interval
        self.excluded_paths = tuple(excluded_paths)
        self.excluded_names = tuple(excluded_names)
        self.on_root_error = on_root_error
        self._stop = threading.Event()
        self._scan_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._observer = None
        self._debounce: threading.Timer | None = None
        self._closed = False
        self.last_report = ScanReport()

    def rescan(self) -> ScanReport:
        with self._scan_lock:
            with self._state_lock:
                if self._closed:
                    return self.last_report
            self.last_report = self.engine.scan(
                self.roots,
                excluded_paths=self.excluded_paths,
                excluded_names=self.excluded_names,
            )
            if any(error.startswith("root:") for error in self.last_report.errors) and self.on_root_error is not None:
                self.on_root_error(self.last_report)
            return self.last_report

    def start(self) -> None:
        with self._state_lock:
            if self._thread is not None:
                raise RuntimeError("watcher already started")
            self._closed = False
            self._stop.clear()
        self.rescan()
        if Observer is not None:
            observer = Observer()
            handler = _RescanHandler(self)
            try:
                for root in self.roots:
                    observer.schedule(handler, str(getattr(root, "path", root)), recursive=True)
                observer.start()
            except (OSError, RuntimeError):
                observer.stop()
                # Polling remains available if the native event backend fails.
                observer = None
            self._observer = observer
        self._thread = threading.Thread(target=self._periodic, name="local-connector-rescan", daemon=True)
        self._thread.start()

    def _periodic(self) -> None:
        while not self._stop.wait(self.interval):
            self.rescan()

    def _schedule_event_rescan(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            if self._debounce is not None:
                self._debounce.cancel()
            self._debounce = threading.Timer(0.2, self.rescan)
            self._debounce.daemon = True
            self._debounce.start()

    def stop(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._stop.set()
            timer, self._debounce = self._debounce, None
            observer, self._observer = self._observer, None
            thread, self._thread = self._thread, None
        if timer is not None:
            timer.cancel()
        if observer is not None:
            observer.stop()
            observer.join(timeout=5)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
        # Wait for a debounced scan or periodic scan to finish before the runtime
        # replaces roots or closes the shared SQLite connection.
        with self._scan_lock:
            pass


class _RescanHandler(FileSystemEventHandler):
    _MUTATING_EVENTS = frozenset({"created", "modified", "deleted", "moved"})

    def __init__(self, watcher: ConnectorWatcher) -> None:
        self.watcher = watcher

    def on_any_event(self, event) -> None:  # noqa: ANN001 - watchdog's callback type
        if event.event_type in self._MUTATING_EVENTS and not event.is_directory:
            self.watcher._schedule_event_rescan()
