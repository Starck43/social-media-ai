from __future__ import annotations

from pathlib import Path

from local_connector.index import IndexEngine, IndexStore


def test_incremental_scan_reuses_unchanged_files_and_indexes_changes(tmp_path: Path) -> None:
    root = tmp_path / "selected"
    root.mkdir()
    document = root / "notes.txt"
    document.write_text("alpha project", encoding="utf-8")
    store = IndexStore(tmp_path / "index.sqlite")
    try:
        engine = IndexEngine(store)
        first = engine.scan([root])
        assert (first.indexed, first.skipped, first.deleted) == (1, 0, 0)
        assert store.search("alpha")[0]["relative_path"] == "notes.txt"

        second = engine.scan([root])
        assert (second.indexed, second.skipped, second.deleted) == (0, 1, 0)

        document.write_text("beta project", encoding="utf-8")
        changed = engine.scan([root])
        assert changed.indexed == 1
        assert store.search("beta")[0]["relative_path"] == "notes.txt"
        assert store.search("alpha") == []

        document.unlink()
        deleted = engine.scan([root])
        assert deleted.deleted == 1
        assert store.list_files() == []
    finally:
        store.close()


def test_metadata_for_binary_is_kept_without_treating_it_as_text(tmp_path: Path) -> None:
    root = tmp_path / "selected"
    root.mkdir()
    (root / "image.bin").write_bytes(b"\x00\x01\x02")
    store = IndexStore(tmp_path / "index.sqlite")
    try:
        report = IndexEngine(store).scan([root])
        assert report.indexed == 1
        record = store.get_file("image.bin")
        assert record is not None
        assert record.size == 3
        assert store.search("image") == []
    finally:
        store.close()


def test_index_delete_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "selected"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    store = IndexStore(tmp_path / "index.sqlite")
    try:
        IndexEngine(store).scan([root])
        store.delete_all()
        store.delete_all()
        assert store.list_files() == []
    finally:
        store.close()


def test_same_size_and_mtime_content_change_is_detected(tmp_path: Path) -> None:
    root = tmp_path / "selected"
    root.mkdir()
    document = root / "notes.txt"
    document.write_text("alpha", encoding="utf-8")
    store = IndexStore(tmp_path / "index.sqlite")
    try:
        engine = IndexEngine(store)
        engine.scan([root])
        original_stat = document.stat()
        document.write_text("omega", encoding="utf-8")
        document.touch()
        import os

        os.utime(document, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        report = engine.scan([root])
        assert report.indexed == 1
        assert store.search("omega")
        assert store.search("alpha") == []
    finally:
        store.close()


def test_two_selected_roots_do_not_collide(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "same.txt").write_text("first value", encoding="utf-8")
    (second / "same.txt").write_text("second value", encoding="utf-8")
    store = IndexStore(tmp_path / "index.sqlite")
    try:
        report = IndexEngine(store).scan([first, second])
        assert report.indexed == 2
        assert len(store.list_files()) == 2
    finally:
        store.close()
