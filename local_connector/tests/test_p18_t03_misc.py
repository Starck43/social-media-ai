from __future__ import annotations

from pathlib import Path

import pytest

from local_connector.config import ConnectorConfig
from local_connector.index import IndexEngine, IndexStore
from local_connector.watcher import ConnectorWatcher


def test_config_rejects_invalid_limits(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ConnectorConfig(tmp_path / "index.sqlite", rescan_seconds=0)
    with pytest.raises(ValueError):
        ConnectorConfig(tmp_path / "index.sqlite", max_text_bytes=0)


def test_watcher_rescan_and_lifecycle(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    store = IndexStore(tmp_path / "index.sqlite")
    watcher = ConnectorWatcher(IndexEngine(store), [root], interval=60)
    try:
        assert watcher.rescan().indexed == 1
        watcher.start()
        watcher.stop()
    finally:
        store.close()
