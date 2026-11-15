from __future__ import annotations

from pathlib import Path

import pytest

from local_connector.index import DeleteConflict, IndexEngine, IndexStore
from local_connector.paths import SelectedRoot

REQUEST_ID = "b" * 32
DIGEST = "c" * 64


def _populated(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    for root in (first, second):
        root.mkdir()
        (root / "note.txt").write_text(f"{root.name} budget ledger", encoding="utf-8")
    store = IndexStore(tmp_path / "cache" / "index.sqlite")
    engine = IndexEngine(store)
    first_root, second_root = SelectedRoot(first), SelectedRoot(second)
    engine.scan([first_root, second_root])
    return store, engine, first_root, second_root


def test_delete_scope_removes_only_the_named_root_and_never_source_files(tmp_path: Path) -> None:
    store, _, first_root, second_root = _populated(tmp_path)
    root_hash = IndexEngine._root_hash(first_root)
    before = store.generation

    report = store.delete_scope(request_id=REQUEST_ID, request_digest=DIGEST, root_hashes=[root_hash])

    assert report.removed == 1
    assert report.scope == root_hash
    assert report.replayed is False
    assert store.generation == before + 1
    assert (first_root.path / "note.txt").exists() and (second_root.path / "note.txt").exists()
    remaining = {record.relative_path for record in store.list_files()}
    assert remaining == {"note.txt"}
    assert store.search("ledger") and len(store.search("ledger")) == 1


def test_delete_scope_is_durably_idempotent_for_the_same_request(tmp_path: Path) -> None:
    store, _, first_root, _ = _populated(tmp_path)
    root_hash = IndexEngine._root_hash(first_root)

    first = store.delete_scope(request_id=REQUEST_ID, request_digest=DIGEST, root_hashes=[root_hash])
    generation = store.generation
    again = store.delete_scope(request_id=REQUEST_ID, request_digest=DIGEST, root_hashes=[root_hash])

    assert again.replayed is True
    assert again.generation == first.generation == generation
    assert again.removed == first.removed
    assert store.generation == generation


def test_delete_scope_refuses_a_reused_request_id_with_different_content(tmp_path: Path) -> None:
    store, _, first_root, _ = _populated(tmp_path)
    root_hash = IndexEngine._root_hash(first_root)
    store.delete_scope(request_id=REQUEST_ID, request_digest=DIGEST, root_hashes=[root_hash])

    with pytest.raises(DeleteConflict):
        store.delete_scope(request_id=REQUEST_ID, request_digest="d" * 64, root_hashes=[root_hash])
    with pytest.raises(DeleteConflict):
        store.delete_scope(request_id=REQUEST_ID, request_digest=DIGEST)


def test_delete_all_purges_every_indexed_object_and_pauses_indexing(tmp_path: Path) -> None:
    store, _, _, _ = _populated(tmp_path)

    report = store.delete_scope(request_id=REQUEST_ID, request_digest=DIGEST, root_hashes=None, pause=True)

    assert report.removed == 2
    assert report.scope == "all"
    assert store.list_files() == []
    assert store.search("ledger") == []
    assert store.indexing_paused is True
    assert store.pause_reason == "deleted"


def test_delete_scope_validates_its_inputs(tmp_path: Path) -> None:
    store, _, _, _ = _populated(tmp_path)
    with pytest.raises(ValueError):
        store.delete_scope(request_id="", request_digest=DIGEST, root_hashes=None)
    with pytest.raises(ValueError):
        store.delete_scope(request_id=REQUEST_ID, request_digest="", root_hashes=None)
    with pytest.raises(ValueError):
        store.delete_scope(request_id=REQUEST_ID, request_digest=DIGEST, root_hashes=[""])


def test_revocation_purges_cached_replay_snippets_for_the_device(tmp_path: Path) -> None:
    from local_connector.search import ReplayLedger

    ledger = ReplayLedger(tmp_path / "cache" / "replay.sqlite")
    try:
        ledger.reserve(
            device_id=42,
            request_id="e" * 32,
            digest="f" * 64,
            operation="search",
            issued_at=1_700_000_000,
            device_epoch=1,
            consent_epoch=1,
            policy_epoch=1,
            now=1_700_000_000,
        )
        ledger.complete(device_id=42, request_id="e" * 32, digest="f" * 64, response_json='{"hits":[]}')
        with ledger.lock:
            cached = ledger.connection.execute(
                "SELECT response_json FROM command_replay_ledger WHERE device_id = 42"
            ).fetchone()[0]
        assert cached == '{"hits":[]}'

        ledger.invalidate_device(42)

        with ledger.lock:
            row = ledger.connection.execute(
                "SELECT state, response_json FROM command_replay_ledger WHERE device_id = 42"
            ).fetchone()
        assert row[0] == "unknown"
        assert row[1] is None
    finally:
        ledger.close()


def test_replay_ledger_purge_device_keeps_monotonic_clock_state(tmp_path: Path) -> None:
    from local_connector.search import ReplayLedger, SearchProtocolError

    ledger = ReplayLedger(tmp_path / "cache" / "replay.sqlite")
    try:
        ledger.reserve(
            device_id=42,
            request_id="e" * 32,
            digest="f" * 64,
            operation="search",
            issued_at=1_700_000_000,
            device_epoch=1,
            consent_epoch=1,
            policy_epoch=1,
            now=1_700_000_000,
        )
        assert ledger.purge_device(42) == 1
        with ledger.lock:
            count = ledger.connection.execute(
                "SELECT COUNT(*) FROM command_replay_ledger WHERE device_id = 42"
            ).fetchone()[0]
        assert count == 0
        with pytest.raises(SearchProtocolError) as rollback:
            ledger.reserve(
                device_id=42,
                request_id="a" * 32,
                digest="b" * 64,
                operation="health",
                issued_at=1_699_999_900,
                device_epoch=1,
                consent_epoch=1,
                policy_epoch=1,
                now=1_699_999_900,
            )
        assert rollback.value.code == "unavailable"
    finally:
        ledger.close()


def test_runtime_and_ui_remove_all_data_keep_source_files_intact(tmp_path: Path) -> None:
    import json
    import re
    import urllib.request

    from local_connector.config import ConnectorConfig
    from local_connector.runtime import ConnectorRuntime
    from local_connector.search import ReplayLedger

    docs = tmp_path / "docs"
    docs.mkdir()
    source_file = docs / "contract.txt"
    source_file.write_text("source file untouched", encoding="utf-8")
    ledger = ReplayLedger(tmp_path / "cache" / "replay.sqlite")
    ledger.reserve(
        device_id=7,
        request_id="1" * 32,
        digest="2" * 64,
        operation="search",
        issued_at=1_700_000_000,
        device_epoch=1,
        consent_epoch=1,
        policy_epoch=1,
        now=1_700_000_000,
    )
    removed_creds: list[bool] = []
    cfg = ConnectorConfig(index_path=tmp_path / "cache" / "index.sqlite", selected_roots=[docs])
    runtime = ConnectorRuntime(
        cfg,
        replay_ledger=ledger,
        device_id=7,
        credential_remover=lambda: removed_creds.append(True),
    )
    try:
        runtime.scan()
        assert len(runtime.store.list_files()) == 1
        host, port = runtime.start()
        page = urllib.request.urlopen(f"http://{host}:{port}/", timeout=2).read().decode("utf-8")
        assert "Remove all data" in page
        csrf = re.search(r'const csrf="([A-Za-z0-9_-]+)"', page).group(1)
        req = urllib.request.Request(
            f"http://{host}:{port}/remove-all-data",
            data=json.dumps({"confirmed": True}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
        )
        res = json.loads(urllib.request.urlopen(req, timeout=2).read())
        assert res == {"ok": True}
        assert runtime.store.list_files() == []
        assert runtime.store.list_approved_roots() == []
        assert runtime.selection.roots() == ()
        assert removed_creds == [True]
        assert source_file.exists() and source_file.read_text(encoding="utf-8") == "source file untouched"
    finally:
        runtime.close()
        ledger.close()
