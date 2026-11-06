from __future__ import annotations

import contextlib
import errno
import os
import stat
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from local_connector.index import IndexEngine, IndexStore
from local_connector.paths import PathSafetyError, SelectedRoot, iter_files


def test_incomplete_walk_does_not_prune_existing_rows(tmp_path: Path) -> None:
    root = tmp_path / "selected"
    root.mkdir()
    (root / "good.txt").write_text("retained", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("not selected", encoding="utf-8")
    store = IndexStore(tmp_path / "cache" / "index.sqlite")
    engine = IndexEngine(store)
    try:
        assert engine.scan([root]).indexed == 1
        (root / "00-link.txt").symlink_to(outside)
        report = engine.scan([root])
        assert report.deleted == 0
        assert report.errors == ("root: PathSafetyError",)
        assert [record.relative_path for record in store.list_files()] == ["good.txt"]
    finally:
        store.close()


def test_repeated_directory_enumeration_does_not_duplicate_directory_fds(
    tmp_path: Path,
) -> None:
    import local_connector.paths as paths_module

    root = tmp_path / "selected"
    root.mkdir()
    (root / "note.txt").write_text("safe", encoding="utf-8")

    real_dup = os.dup
    duplicated_fds: list[int] = []

    def track_dup(fd: int) -> int:
        duplicate = real_dup(fd)
        duplicated_fds.append(duplicate)
        return duplicate

    with patch.object(paths_module.os, "dup", track_dup):
        for _ in range(100):
            assert [relative for _, relative in iter_files(root)] == ["note.txt"]
            for duplicate in duplicated_fds:
                with pytest.raises(OSError) as exc_info:
                    os.fstat(duplicate)
                assert exc_info.value.errno == errno.EBADF
            duplicated_fds.clear()


def test_root_and_ancestor_replacement_fail_closed(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    root = parent / "selected"
    root.mkdir(parents=True)
    selected = SelectedRoot(root)
    root.rename(parent / "moved")
    (parent / "selected").symlink_to(parent / "moved", target_is_directory=True)
    with pytest.raises(PathSafetyError):
        selected.verify()

    (parent / "selected").unlink()
    (parent / "moved").rename(parent / "selected")
    selected = SelectedRoot(root)
    parent.rename(tmp_path / "old-parent")
    (parent / "selected").mkdir(parents=True)
    with pytest.raises(PathSafetyError):
        selected.verify()


def test_engine_path_pin_survives_root_swap(tmp_path: Path) -> None:
    root = tmp_path / "selected"
    root.mkdir()
    (root / "safe.txt").write_text("safe", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leaked.txt").write_text("secret", encoding="utf-8")
    store = IndexStore(tmp_path / "cache" / "index.sqlite")
    engine = IndexEngine(store)
    try:
        assert engine.scan([root]).indexed == 1
        root.rename(tmp_path / "original")
        root.symlink_to(outside, target_is_directory=True)
        report = engine.scan([root])
        assert report.errors == ("root: PathSafetyError",)
        assert [record.relative_path for record in store.list_files()] == ["safe.txt"]
    finally:
        store.close()


def test_approved_root_identity_survives_restart(tmp_path: Path) -> None:
    root = tmp_path / "selected"
    root.mkdir()
    selected = SelectedRoot(root)
    database = tmp_path / "cache" / "index.sqlite"
    store = IndexStore(database)
    store.set_approved_roots([selected])
    store.close()

    root.rename(tmp_path / "moved")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leaked.txt").write_text("secret", encoding="utf-8")
    root.symlink_to(outside, target_is_directory=True)
    restored = IndexStore(database)
    try:
        with pytest.raises(PathSafetyError):
            restored.restore_root(root)
    finally:
        restored.close()


def test_file_swap_during_single_handle_read_never_commits(tmp_path: Path) -> None:
    import local_connector.index as index_module
    from local_connector.paths import open_selected_file as safe_open

    root = tmp_path / "selected"
    root.mkdir()
    source = root / "document.txt"
    source.write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside secret", encoding="utf-8")
    store = IndexStore(tmp_path / "cache" / "index.sqlite")
    engine = IndexEngine(store)

    @contextlib.contextmanager
    def replace_before_validation(selected: SelectedRoot, relative: str):
        with safe_open(selected, relative) as stream:
            yield stream
            source.unlink()
            source.symlink_to(outside)

    try:
        with patch.object(index_module, "open_selected_file", replace_before_validation):
            with pytest.raises(PathSafetyError):
                engine.index_file(SelectedRoot(root), "document.txt")
        assert store.list_files() == []
    finally:
        store.close()


def test_hash_and_text_chunks_come_from_one_verified_open_file(tmp_path: Path) -> None:
    import hashlib

    import local_connector.index as index_module
    from local_connector.paths import open_selected_file as safe_open

    root = tmp_path / "selected"
    root.mkdir()
    (root / "notes.txt").write_text("same verified bytes", encoding="utf-8")
    store = IndexStore(tmp_path / "cache" / "index.sqlite")
    engine = IndexEngine(store)
    opened: list[str] = []

    @contextlib.contextmanager
    def count_opens(selected: SelectedRoot, relative: str):
        opened.append(relative)
        with safe_open(selected, relative) as stream:
            yield stream

    try:
        with patch.object(index_module, "open_selected_file", count_opens):
            assert engine.index_file(SelectedRoot(root), "notes.txt")
        record = store.get_file("notes.txt")
        assert record is not None
        assert record.content_hash == hashlib.sha256(b"same verified bytes").hexdigest()
        assert store.search("verified")[0]["text"] == "same verified bytes"
        assert opened == ["notes.txt"]
    finally:
        store.close()


def test_selected_cache_permissions_and_exclusive_process_lock(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    database = cache / "index.sqlite"
    store = IndexStore(database)
    try:
        assert stat.S_IMODE(cache.stat().st_mode) == 0o700
        assert stat.S_IMODE(database.stat().st_mode) == 0o600
        assert stat.S_IMODE(Path(f"{database}.lock").stat().st_mode) == 0o600
        with pytest.raises(PermissionError, match="already in use"):
            IndexStore(database)
    finally:
        store.close()


def test_existing_shared_cache_directory_is_not_chmodded(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o755)
    with pytest.raises(PermissionError, match="must be owned and private"):
        IndexStore(shared / "index.sqlite")
    assert stat.S_IMODE(shared.stat().st_mode) == 0o755


def test_delete_pauses_durably_until_explicit_resume(tmp_path: Path) -> None:
    root = tmp_path / "selected"
    root.mkdir()
    (root / "private.txt").write_text("derived secret", encoding="utf-8")
    database = tmp_path / "cache" / "index.sqlite"
    store = IndexStore(database)
    engine = IndexEngine(store)
    try:
        assert engine.scan([SelectedRoot(root)]).indexed == 1
        previous_generation = store.generation
        store.delete_all()
        assert store.generation == previous_generation + 1
        assert store.pause_reason == "deleted"
        assert store.search("derived") == []
        assert b"derived secret" not in database.read_bytes()
    finally:
        store.close()

    reopened = IndexStore(database)
    engine = IndexEngine(reopened)
    try:
        assert engine.scan([SelectedRoot(root)]).errors == ("indexing paused",)
        assert reopened.list_files() == []
        assert reopened.pause_reason == "deleted"
        reopened.resume_indexing()
        assert engine.scan([SelectedRoot(root)]).indexed == 1
    finally:
        reopened.close()


def test_in_flight_scan_finishes_before_delete_and_cannot_refill(tmp_path: Path) -> None:
    root = tmp_path / "selected"
    root.mkdir()
    source = root / "private.txt"
    source.write_text("derived secret", encoding="utf-8")
    store = IndexStore(tmp_path / "cache" / "index.sqlite")
    engine = IndexEngine(store)
    read_started = threading.Event()
    resume_read = threading.Event()
    delete_done = threading.Event()
    original_read = engine._read_selected

    def delayed_read(selected: SelectedRoot, relative_path: str):
        read_started.set()
        assert resume_read.wait(timeout=3)
        return original_read(selected, relative_path)

    engine._read_selected = delayed_read  # type: ignore[method-assign]
    scan_errors: list[Exception] = []

    def scan() -> None:
        try:
            engine.scan([SelectedRoot(root)])
        except Exception as exc:  # pragma: no cover - thread failure reporting
            scan_errors.append(exc)

    def delete() -> None:
        store.delete_all()
        delete_done.set()

    scan_thread = threading.Thread(target=scan)
    delete_thread = threading.Thread(target=delete)
    try:
        scan_thread.start()
        assert read_started.wait(timeout=3)
        delete_thread.start()
        resume_read.set()
        scan_thread.join(timeout=3)
        delete_thread.join(timeout=3)
        assert not scan_thread.is_alive()
        assert not delete_thread.is_alive()
        assert scan_errors == []
        assert delete_done.is_set()
        assert store.indexing_paused
        assert store.list_files() == []
        assert source.read_text(encoding="utf-8") == "derived secret"
    finally:
        resume_read.set()
        scan_thread.join(timeout=3)
        if delete_thread.ident is not None:
            delete_thread.join(timeout=3)
        store.close()


def test_exclusions_apply_to_scans_and_direct_indexing(tmp_path: Path) -> None:
    root = tmp_path / "selected"
    (root / "visible").mkdir(parents=True)
    (root / "visible" / "notes.txt").write_text("safe", encoding="utf-8")
    (root / "Chrome").mkdir()
    (root / "Chrome" / "Login Data").write_text("credential db", encoding="utf-8")
    (root / "private").mkdir()
    (root / "private" / "secret.txt").write_text("secret", encoding="utf-8")
    store = IndexStore(tmp_path / "cache" / "index.sqlite")
    engine = IndexEngine(store)
    try:
        report = engine.scan([SelectedRoot(root)], excluded_paths={"private"})
        assert report.errors == ()
        assert [record.relative_path for record in store.list_files()] == ["visible/notes.txt"]
        with pytest.raises(PathSafetyError, match="excluded"):
            engine.index_file(root, "Chrome/Login Data")
    finally:
        store.close()


def test_sqlite_cache_files_inside_selected_root_are_excluded(tmp_path: Path) -> None:
    root = tmp_path / "selected"
    root.mkdir()
    (root / "note.txt").write_text("hello", encoding="utf-8")
    store = IndexStore(root / "connector-cache" / "index.sqlite")
    try:
        report = IndexEngine(store).scan([SelectedRoot(root)])
        assert report.errors == ()
        assert [record.relative_path for record in store.list_files()] == ["note.txt"]
    finally:
        store.close()


def test_private_storage_denies_unsupported_platforms(tmp_path: Path, monkeypatch) -> None:
    import local_connector.index as index_module

    database = tmp_path / "cache" / "index.sqlite"
    monkeypatch.setattr(index_module, "os", SimpleNamespace(name="nt"))
    with pytest.raises(PermissionError, match="unavailable on this platform"):
        IndexStore(database)
