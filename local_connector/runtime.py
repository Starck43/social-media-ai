"""Small local-only composition root for the desktop connector scaffold."""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

from .config import ConnectorConfig
from .index import IndexEngine, IndexStore, ScanReport
from .paths import PathSafetyError, SelectedRoot
from .ui import FolderSelection, LocalStatusServer
from .watcher import ConnectorWatcher


class ConnectorRuntime:
    """Wire pinned roots, private SQLite, watcher and loopback development UI."""

    def __init__(self, config: ConnectorConfig) -> None:
        initial = tuple(config.selected_roots)
        self.config = replace(config, selected_roots=list(initial))
        self._control_lock = threading.RLock()
        self.store = IndexStore(config.index_path)
        self.engine = IndexEngine(
            self.store,
            text_extensions=config.text_extensions,
            max_text_bytes=config.max_text_bytes,
        )
        self.selection = FolderSelection()
        self.config.selected_roots = []
        self._root_unavailable = False
        self._selection_error: str | None = None
        self._started = False
        self.watcher: ConnectorWatcher | None = None
        self.status = LocalStatusServer(
            self.selection,
            self.status_payload,
            select_roots=self.select_roots,
            delete_index=self._delete_from_ui,
            resume_indexing=self.resume_indexing,
        )

        # First-run configured paths are explicit initial selections. Once a root
        # identity has been persisted, restart may only restore that identity;
        # a changed or unknown path needs a fresh selection through the UI.
        try:
            approved = self.store.list_approved_roots()
            if initial:
                if approved:
                    selected = tuple(self.store.restore_root(root) for root in initial)
                else:
                    selected = tuple(SelectedRoot(root) for root in initial)
                    for root in selected:
                        self.store.approve_root(root)
                self._check_cache_separation(selected)
                self._set_selection(selected)
            elif approved:
                selected = tuple(self.store.restore_root(root) for root in approved)
                self._check_cache_separation(selected)
                self._set_selection(selected)
        except (OSError, PathSafetyError, ValueError) as exc:
            message = str(exc)
            lowered = message.casefold()
            self._root_unavailable = (
                "selected root" in lowered or "folder changed" in lowered or "folder must be selected again" in lowered
            )
            self._selection_error = (
                "Folder changed or is unavailable. Select it again." if self._root_unavailable else message
            )
            self.store.pause_indexing(reason="root_changed")

    def _check_cache_separation(self, roots: tuple[SelectedRoot, ...]) -> None:
        database = self.store.database_path
        for root in roots:
            try:
                database.relative_to(root.path)
            except ValueError:
                continue
            raise PathSafetyError("The local index cache must be outside selected folders.")

    def _cache_exclusions(self, roots: tuple[SelectedRoot, ...]) -> tuple[str, ...]:
        database = self.store.database_path
        names = {
            database.name,
            database.name + "-wal",
            database.name + "-shm",
            database.name + "-journal",
            database.name + ".lock",
            database.name + "-lock",
        }
        exclusions = {str(item) for item in self.config.excluded_paths}
        for root in roots:
            try:
                relative = database.relative_to(root.path)
            except ValueError:
                continue
            if relative.parts:
                parent = Path(*relative.parts[:-1]).as_posix()
                for name in names:
                    exclusions.add((Path(parent) / name).as_posix() if parent != "." else name)
        return tuple(exclusions)

    def _set_selection(self, roots: tuple[SelectedRoot, ...]) -> None:
        self.selection.replace(roots)
        self.config.selected_roots = [Path(root.selection_path) for root in roots]

    def select_roots(self, roots: list[str | Path]) -> tuple[SelectedRoot, ...]:
        selected = tuple(SelectedRoot(root) for root in roots)
        if len({str(root.path) for root in selected}) != len(selected):
            raise PathSafetyError("duplicate selected folder")
        self._check_cache_separation(selected)
        with self._control_lock:
            was_paused = self.store.indexing_paused
            pause_reason = self.store.pause_reason
            watcher, self.watcher = self.watcher, None
            if watcher is not None:
                watcher.stop()
            # A new explicit selection clears entries that were scoped to old
            # roots. Deletion remains paused; root-change recovery is explicit
            # re-selection and can resume only the newly pinned roots.
            self.store.delete_all(pause=was_paused and pause_reason == "deleted")
            self.store.set_approved_roots(selected)
            self._set_selection(selected)
            self._root_unavailable = False
            self._selection_error = None
            if was_paused and pause_reason == "deleted":
                self.store.pause_indexing(reason="deleted")
            elif was_paused:
                self.store.resume_indexing()
            if self._started and selected and not self.store.indexing_paused:
                self._start_watcher(selected)
        return selected

    def _start_watcher(self, roots: tuple[SelectedRoot, ...]) -> None:
        watcher = ConnectorWatcher(
            self.engine,
            roots,
            self.config.rescan_seconds,
            excluded_paths=self._cache_exclusions(roots),
            excluded_names=self.config.excluded_names,
            on_root_error=self._handle_root_error,
        )
        self.watcher = watcher
        watcher.start()

    def _handle_root_error(self, report: ScanReport) -> None:
        # Do not acquire _control_lock here: an event/periodic callback could be
        # running while select_roots is stopping and joining that watcher.
        self.store.pause_indexing(reason="root_changed")
        self._root_unavailable = True

    def scan(self) -> ScanReport:
        with self._control_lock:
            roots = self.selection.roots()
            if self.store.indexing_paused:
                return ScanReport(errors=(f"indexing paused: {self.store.pause_reason or 'paused'}",))
            report = self.engine.scan(
                roots,
                excluded_paths=self._cache_exclusions(roots),
                excluded_names=self.config.excluded_names,
            )
            if any(error.startswith("root:") for error in report.errors):
                self._handle_root_error(report)
            return report

    def start(self) -> tuple[str, int]:
        with self._control_lock:
            if self._started:
                raise RuntimeError("connector runtime already started")
            self._started = True
            roots = self.selection.roots()
            if roots and not self.store.indexing_paused and not self._root_unavailable:
                self._start_watcher(roots)
            try:
                return self.status.start()
            except BaseException:
                watcher, self.watcher = self.watcher, None
                if watcher is not None:
                    watcher.stop()
                self._started = False
                raise

    def resume_indexing(self) -> None:
        with self._control_lock:
            if self.store.pause_reason == "root_changed" or self._root_unavailable:
                raise PathSafetyError("Folder changed or is unavailable. Select it again.")
            roots = self.selection.roots()
            for root in roots:
                root.verify()
            self.store.resume_indexing()
            if self._started and roots:
                if self.watcher is not None:
                    watcher, self.watcher = self.watcher, None
                    watcher.stop()
                self._start_watcher(roots)

    def delete_index(self, *, confirmed: bool) -> None:
        if not confirmed:
            raise PermissionError("index deletion requires explicit confirmation")
        with self._control_lock:
            watcher, self.watcher = self.watcher, None
            if watcher is not None:
                watcher.stop()
            self.store.delete_all(pause=True)

    def _delete_from_ui(self) -> None:
        self.delete_index(confirmed=True)

    def status_payload(self) -> dict[str, object]:
        with self._control_lock:
            report = self.watcher.last_report if self.watcher else ScanReport()
            roots = [str(root.path) for root in self.selection.roots()]
            errors = list(report.errors)
            if self._selection_error:
                errors = [self._selection_error]
            elif self._root_unavailable:
                errors = ["Folder changed or is unavailable. Select it again."]
            elif self.store.pause_reason == "deleted":
                errors = ["Index deleted. Choose Resume indexing when ready."]
            return {
                "roots": roots,
                "indexed": report.indexed,
                "skipped": report.skipped,
                "deleted": report.deleted,
                "errors": errors,
                "indexing_paused": self.store.indexing_paused,
                "pause_reason": self.store.pause_reason,
            }

    def close(self) -> None:
        with self._control_lock:
            self._started = False
            watcher, self.watcher = self.watcher, None
        self.status.close()
        if watcher is not None:
            watcher.stop()
        self.store.close()
