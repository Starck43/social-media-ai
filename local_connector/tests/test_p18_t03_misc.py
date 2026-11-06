from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

import pytest

from local_connector.config import ConnectorConfig
from local_connector.index import IndexEngine, IndexStore
from local_connector.paths import PathSafetyError
from local_connector.runtime import ConnectorRuntime
from local_connector.watcher import ConnectorWatcher


def _token(html: str) -> str:
    match = re.search(r'const csrf="([A-Za-z0-9_-]+)"', html)
    assert match
    return match.group(1)


def _post(url: str, token: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-CSRF-Token": token},
        method="POST",
    )
    return json.loads(urllib.request.urlopen(request, timeout=2).read())


def test_config_rejects_invalid_limits(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ConnectorConfig(tmp_path / "index.sqlite", rescan_seconds=0)
    with pytest.raises(ValueError):
        ConnectorConfig(tmp_path / "index.sqlite", max_text_bytes=0)


def test_watcher_applies_exclusions_and_stops_cleanly(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "visible.txt").write_text("visible", encoding="utf-8")
    (root / "secret.txt").write_text("secret", encoding="utf-8")
    store = IndexStore(tmp_path / "index.sqlite")
    watcher = ConnectorWatcher(IndexEngine(store), [root], interval=60, excluded_paths={"secret.txt"})
    try:
        report = watcher.rescan()
        assert report.indexed == 1
        assert [row.relative_path for row in store.list_files()] == ["visible.txt"]
        watcher.start()
        watcher.stop()
        assert watcher.rescan() == watcher.last_report
    finally:
        watcher.stop()
        store.close()


def test_runtime_deletion_persists_pause_until_explicit_resume(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "a.txt"
    source.write_text("first", encoding="utf-8")
    database = tmp_path / "private" / "index.sqlite"
    config = ConnectorConfig(database)
    runtime = ConnectorRuntime(config)
    runtime.select_roots([root])
    try:
        assert runtime.scan().indexed == 1
        runtime.delete_index(confirmed=True)
        assert runtime.store.list_files() == []
        assert runtime.store.indexing_paused
        source.write_text("second", encoding="utf-8")
        assert "paused" in runtime.scan().errors[0]
        runtime.close()

        resumed = ConnectorRuntime(ConnectorConfig(database))
        assert resumed.store.indexing_paused
        assert resumed.selection.paths() == (root.resolve(),)
        assert "paused" in resumed.scan().errors[0]
        resumed.resume_indexing()
        assert not resumed.store.indexing_paused
        assert resumed.scan().indexed == 1
        resumed.close()
    finally:
        runtime.close()


def test_runtime_first_configured_roots_are_approved_and_restored(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("alpha", encoding="utf-8")
    database = tmp_path / "private" / "index.sqlite"
    config = ConnectorConfig(database, selected_roots=[root])
    runtime = ConnectorRuntime(config)
    try:
        assert runtime.selection.paths() == (root.resolve(),)
        assert runtime.scan().indexed == 1
    finally:
        runtime.close()

    restored = ConnectorRuntime(ConnectorConfig(database))
    try:
        assert restored.selection.paths() == (root.resolve(),)
        assert restored.store.list_approved_roots()
        assert restored.scan().skipped == 1
    finally:
        restored.close()


def test_runtime_rejects_cache_inside_selected_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    runtime = ConnectorRuntime(ConnectorConfig(root / "connector" / "index.sqlite"))
    try:
        with pytest.raises(PathSafetyError, match="outside selected folders"):
            runtime.select_roots([root])
    finally:
        runtime.close()


def test_runtime_ui_starts_http_and_selection_replaces_index_scope(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "a.txt").write_text("alpha", encoding="utf-8")
    (second / "b.txt").write_text("bravo", encoding="utf-8")
    runtime = ConnectorRuntime(ConnectorConfig(tmp_path / "private" / "index.sqlite", rescan_seconds=60))
    runtime.select_roots([first])
    try:
        host, port = runtime.start()
        base = f"http://{host}:{port}"
        page = urllib.request.urlopen(base + "/", timeout=2).read().decode()
        token = _token(page)
        status = json.loads(urllib.request.urlopen(base + "/status", timeout=2).read())
        assert status["roots"] == [str(first.resolve())]
        assert "alpha" in [row["text"] for row in runtime.store.search("alpha")]

        _post(base + "/folders", token, {"roots": [str(second)]})
        assert [str(root.path) for root in runtime.selection.roots()] == [str(second.resolve())]
        assert runtime.config.selected_roots == [second.resolve()]
        assert runtime.store.search("alpha") == []
        assert "bravo" in [row["text"] for row in runtime.store.search("bravo")]

        _post(base + "/delete", token, {"confirmed": True})
        assert runtime.store.indexing_paused
        assert runtime.store.list_files() == []
        from urllib.error import HTTPError

        with pytest.raises(HTTPError) as error:
            _post(base + "/resume", "wrong-token", {})
        assert error.value.code == 403
        _post(base + "/resume", token, {})
        assert not runtime.store.indexing_paused
    finally:
        runtime.close()


def test_missing_or_replaced_root_pauses_until_reselection(tmp_path: Path) -> None:
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "safe.txt").write_text("safe", encoding="utf-8")
    (outside / "private.txt").write_text("private", encoding="utf-8")
    database = tmp_path / "private" / "index.sqlite"
    runtime = ConnectorRuntime(ConnectorConfig(database))
    runtime.select_roots([root])
    try:
        assert runtime.scan().indexed == 1
        moved = tmp_path / "root-moved"
        root.rename(moved)
        root.symlink_to(outside, target_is_directory=True)
        report = runtime.watcher.rescan() if runtime.watcher else runtime.scan()
        assert report.errors
        assert runtime.store.indexing_paused
        with pytest.raises(PathSafetyError, match="Select it again"):
            runtime.resume_indexing()
        assert runtime.store.search("private") == []
        # Choosing the new folder is an explicit new selection and cannot reuse
        # the replaced lexical path's stored root identity.
        runtime.select_roots([outside])
        assert not runtime.store.indexing_paused
        assert runtime.scan().indexed == 1
    finally:
        runtime.close()


def test_restart_does_not_reapprove_a_replaced_configured_root(tmp_path: Path) -> None:
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "safe.txt").write_text("safe", encoding="utf-8")
    (outside / "private.txt").write_text("private", encoding="utf-8")
    database = tmp_path / "private-cache" / "index.sqlite"
    config = ConnectorConfig(database, selected_roots=[root])
    runtime = ConnectorRuntime(config)
    try:
        assert runtime.scan().indexed == 1
    finally:
        runtime.close()

    moved = tmp_path / "root-moved"
    root.rename(moved)
    root.symlink_to(outside, target_is_directory=True)
    reopened = ConnectorRuntime(ConnectorConfig(database, selected_roots=[root]))
    try:
        assert reopened.store.indexing_paused
        assert reopened.status_payload()["errors"] == ["Folder changed or is unavailable. Select it again."]
        with pytest.raises(PathSafetyError, match="Select it again"):
            reopened.resume_indexing()
        assert reopened.scan().errors
        assert reopened.store.search("private") == []
        reopened.select_roots([outside])
        assert reopened.scan().indexed == 1
    finally:
        reopened.close()


def test_restart_does_not_approve_a_replaced_configured_root(tmp_path: Path) -> None:
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "safe.txt").write_text("safe", encoding="utf-8")
    (outside / "private.txt").write_text("private", encoding="utf-8")
    database = tmp_path / "private-cache" / "index.sqlite"
    config = ConnectorConfig(database, selected_roots=[root])
    runtime = ConnectorRuntime(config)
    try:
        assert runtime.scan().indexed == 1
    finally:
        runtime.close()

    moved = tmp_path / "root-moved"
    root.rename(moved)
    root.symlink_to(outside, target_is_directory=True)
    reopened = ConnectorRuntime(config)
    try:
        assert reopened.store.indexing_paused
        assert reopened.status_payload()["errors"] == ["Folder changed or is unavailable. Select it again."]
        with pytest.raises(PathSafetyError, match="Select it again"):
            reopened.resume_indexing()
        assert reopened.scan().errors
        assert reopened.store.search("private") == []
        reopened.select_roots([outside])
        assert reopened.scan().indexed == 1
    finally:
        reopened.close()
