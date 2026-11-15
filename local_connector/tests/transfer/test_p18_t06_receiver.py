"""P18-T06 device receiver: `file.fetch` and `index.delete` over the signed envelope."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from local_connector.index import IndexEngine, IndexStore, root_hash
from local_connector.paths import SelectedRoot
from local_connector.search import (
    MAX_RESPONSE_BYTES,
    TIMEOUT_SECONDS,
    LocalSearchService,
    ReplayLedger,
    RootRegistration,
    SearchProtocolError,
    SignedCommandReceiver,
    sign_control_payload,
)
from local_connector.transfer import FetchConsent, LocalFetcher

NOW = 1_700_000_000
KEY_ID = "gw-v1"
ALL_OPS = frozenset({"health", "index.status", "search", "file.fetch", "index.delete"})


class Issuer:
    def __init__(self, token: str | None = "exact-version-1") -> None:
        self.token = token

    def assert_exact_version(self, *, object_id, root_id, relative_path, version):
        return self.token


def envelope(*, operation: str, args: dict, request_id: str | None = None) -> dict:
    return {
        "version": 1,
        "key_id": KEY_ID,
        "request_id": request_id or uuid.uuid4().hex,
        "tenant_id": 7,
        "owner_id": 11,
        "actor_id": 11,
        "device_id": 13,
        "operation": operation,
        "issued_at": NOW,
        "expires_at": NOW + TIMEOUT_SECONDS,
        "device_epoch": 1,
        "consent_epoch": 1,
        "policy_epoch": 1,
        "data_boundary": "company",
        "max_response_bytes": MAX_RESPONSE_BYTES,
        "max_snippet_characters": 512,
        "args": args,
    }


def _world(tmp_path: Path):
    company = tmp_path / "company"
    company.mkdir()
    (company / "report.txt").write_text("company report needle", encoding="utf-8")
    store = IndexStore(tmp_path / "cache" / "index.sqlite")
    engine = IndexEngine(store)
    selected = SelectedRoot(company)
    engine.scan([selected])
    service = LocalSearchService(
        store, [RootRegistration(root_id="corp", selected=selected, boundary="company", cloud_consented=True)]
    )
    ledger = ReplayLedger(tmp_path / "cache" / "replay.sqlite")
    key = Ed25519PrivateKey.generate()
    return store, service, ledger, key, selected


def _receiver(service, ledger, key, **overrides) -> SignedCommandReceiver:
    payload = {
        "search_service": service,
        "replay_ledger": ledger,
        "trusted_keys": {KEY_ID: key.public_key()},
        "tenant_id": 7,
        "owner_id": 11,
        "device_id": 13,
        "enabled_operations": ALL_OPS,
    }
    payload.update(overrides)
    return SignedCommandReceiver(**payload)


def _send(receiver, key, *, operation, args, request_id=None) -> dict:
    payload, _digest = sign_control_payload(envelope(operation=operation, args=args, request_id=request_id), key)
    return json.loads(receiver.handle_payload(payload, now=NOW))


def test_index_delete_removes_only_the_consented_root_and_reports_the_fence(tmp_path: Path) -> None:
    store, service, ledger, key, selected = _world(tmp_path)
    try:
        receiver = _receiver(service, ledger, key)
        before = store.generation
        result = _send(receiver, key, operation="index.delete", args={"root_ids": ["corp"], "scope": "index"})

        assert result["outcome"] == "succeeded"
        assert result["scope"] == "index"
        assert result["removed"] == 1
        assert result["generation"] == before + 1
        assert store.list_files() == []
        assert (selected.path / "report.txt").exists()  # source files are never deleted
    finally:
        ledger.close()
        store.close()


def test_index_delete_replays_the_recorded_outcome_for_one_request_id(tmp_path: Path) -> None:
    store, service, ledger, key, _ = _world(tmp_path)
    try:
        receiver = _receiver(service, ledger, key)
        request_id = uuid.uuid4().hex
        first = _send(
            receiver,
            key,
            operation="index.delete",
            args={"root_ids": ["corp"], "scope": "index"},
            request_id=request_id,
        )
        generation = store.generation
        again = _send(
            receiver,
            key,
            operation="index.delete",
            args={"root_ids": ["corp"], "scope": "index"},
            request_id=request_id,
        )

        assert first == again
        assert store.generation == generation
    finally:
        ledger.close()
        store.close()


def test_index_delete_denies_unconsented_roots_and_scope_all_pauses(tmp_path: Path) -> None:
    store, service, ledger, key, _ = _world(tmp_path)
    try:
        receiver = _receiver(service, ledger, key)
        denied = _send(receiver, key, operation="index.delete", args={"root_ids": ["other"], "scope": "index"})
        assert denied["outcome"] == "denied"
        assert store.list_files()

        wiped = _send(receiver, key, operation="index.delete", args={"root_ids": ["corp"], "scope": "all"})
        assert wiped["outcome"] == "succeeded"
        assert store.list_files() == []
        assert store.indexing_paused is True
        assert store.pause_reason == "deleted"
    finally:
        ledger.close()
        store.close()


def test_index_delete_rejects_malformed_scope_and_traversal(tmp_path: Path) -> None:
    store, service, ledger, key, _ = _world(tmp_path)
    try:
        receiver = _receiver(service, ledger, key)
        with pytest.raises(SearchProtocolError):
            _send(receiver, key, operation="index.delete", args={"root_ids": [], "scope": "index"})
        with pytest.raises(SearchProtocolError):
            _send(receiver, key, operation="index.delete", args={"root_ids": ["../etc"], "scope": "index"})
        with pytest.raises(SearchProtocolError):
            _send(receiver, key, operation="index.delete", args={"root_ids": ["corp"], "scope": "files"})
    finally:
        ledger.close()
        store.close()


def test_file_fetch_without_local_confirmation_is_impossible(tmp_path: Path) -> None:
    store, service, ledger, key, selected = _world(tmp_path)
    try:
        record = store.list_files()[0]
        args = {"object_id": record.path_hash, "root_id": "corp", "relative_path": record.relative_path}

        unavailable = _send(_receiver(service, ledger, key), key, operation="file.fetch", args=args)
        assert unavailable["outcome"] == "unavailable"

        denied = _send(
            _receiver(
                service,
                ledger,
                key,
                fetcher=LocalFetcher(store, {"corp": selected}),
                fetch_confirmation=lambda _c: None,
            ),
            key,
            operation="file.fetch",
            args=args,
        )
        assert denied["outcome"] == "denied"

        def wrong_object(_cmd):
            return FetchConsent(
                request_id=_cmd.request_id,
                object_id="other-object",
                root_id="corp",
                relative_path=record.relative_path,
                confirmed_by_user_id=11,
                confirmed_at=NOW,
            )

        denied = _send(
            _receiver(
                service,
                ledger,
                key,
                fetcher=LocalFetcher(store, {"corp": selected}),
                fetch_confirmation=wrong_object,
            ),
            key,
            operation="file.fetch",
            args=args,
        )
        assert denied["outcome"] == "denied"
    finally:
        ledger.close()
        store.close()


def test_file_fetch_requires_an_exact_version_issuer_and_then_streams_once(tmp_path: Path) -> None:
    store, service, ledger, key, selected = _world(tmp_path)
    try:
        record = store.list_files()[0]
        args = {"object_id": record.path_hash, "root_id": "corp", "relative_path": record.relative_path}
        staged: list[bytes] = []

        def confirm(cmd):
            return FetchConsent(
                request_id=cmd.request_id,
                object_id=record.path_hash,
                root_id="corp",
                relative_path=record.relative_path,
                confirmed_by_user_id=11,
                confirmed_at=NOW,
            )

        fetcher = LocalFetcher(store, {"corp": selected})
        ineligible = _send(
            _receiver(service, ledger, key, fetcher=fetcher, fetch_confirmation=confirm),
            key,
            operation="file.fetch",
            args=args,
        )
        assert ineligible["outcome"] == "denied"
        assert staged == []

        granted = _send(
            _receiver(
                service,
                ledger,
                key,
                fetcher=fetcher,
                fetch_confirmation=confirm,
                fetch_sink=staged.append,
                version_issuer=Issuer(),
            ),
            key,
            operation="file.fetch",
            args=args,
        )
        assert granted["outcome"] == "succeeded"
        assert granted["content_hash"] == record.content_hash
        assert granted["size"] == record.size
        assert granted["version_assertion"] == "exact-version-1"
        assert b"".join(staged) == b"company report needle"
    finally:
        ledger.close()
        store.close()


def test_file_fetch_rejects_absolute_and_traversing_paths(tmp_path: Path) -> None:
    store, service, ledger, key, _ = _world(tmp_path)
    try:
        receiver = _receiver(service, ledger, key)
        for path in ("/etc/passwd", "../secret.txt", "docs/../secret.txt", "docs//a.txt"):
            with pytest.raises(SearchProtocolError):
                _send(
                    receiver,
                    key,
                    operation="file.fetch",
                    args={"object_id": "object-1", "root_id": "corp", "relative_path": path},
                )
        with pytest.raises(SearchProtocolError):
            _send(receiver, key, operation="file.fetch", args={"object_id": "object-1", "root_id": "corp"})
    finally:
        ledger.close()
        store.close()


def test_revocation_stops_both_new_operations(tmp_path: Path) -> None:
    store, service, ledger, key, selected = _world(tmp_path)
    try:
        receiver = _receiver(
            service,
            ledger,
            key,
            fetcher=LocalFetcher(store, {"corp": selected}),
            fetch_confirmation=lambda _cmd: None,
            version_issuer=Issuer(),
        )
        receiver.revoke()
        for operation, args in (
            ("index.delete", {"root_ids": ["corp"], "scope": "index"}),
            ("file.fetch", {"object_id": "object-1", "root_id": "corp", "relative_path": "a.txt"}),
        ):
            assert _send(receiver, key, operation=operation, args=args)["outcome"] == "denied"
        assert store.list_files()
    finally:
        ledger.close()
        store.close()


def test_index_delete_pauses_scope_index_and_denies_replay_after_consent_removal(tmp_path: Path) -> None:
    store, service, ledger, key, _ = _world(tmp_path)
    try:
        receiver = _receiver(service, ledger, key)
        req_id = uuid.uuid4().hex
        res = _send(
            receiver,
            key,
            operation="index.delete",
            args={"root_ids": ["corp"], "scope": "index"},
            request_id=req_id,
        )
        assert res["outcome"] == "succeeded"
        assert store.indexing_paused is True
        assert store.pause_reason == "deleted"
        selected_root = service.root_registrations()[0].selected
        service.register_root(RootRegistration(root_id="corp", selected=selected_root, boundary="local_only"))
        replayed = _send(
            receiver,
            key,
            operation="index.delete",
            args={"root_ids": ["corp"], "scope": "index"},
            request_id=req_id,
        )
        assert replayed["outcome"] == "denied"
        assert {"scope", "generation", "removed"} <= set(replayed.keys())
    finally:
        ledger.close()
        store.close()


def test_file_fetch_denies_unconsented_root_and_emits_schema_complete_error_replies(tmp_path: Path) -> None:
    store, service, ledger, key, selected = _world(tmp_path)
    try:
        record = store.list_files()[0]
        args = {"object_id": record.path_hash, "root_id": "corp", "relative_path": record.relative_path}
        # Unconsented / local_only root must be denied even with confirmation + fetcher
        service.register_root(RootRegistration(root_id="corp", selected=selected, boundary="local_only"))
        fetcher = LocalFetcher(store, {"corp": selected})
        denied = _send(
            _receiver(
                service,
                ledger,
                key,
                fetcher=fetcher,
                fetch_confirmation=lambda cmd: FetchConsent(
                    request_id=cmd.request_id,
                    object_id=record.path_hash,
                    root_id="corp",
                    relative_path=record.relative_path,
                    confirmed_by_user_id=11,
                    confirmed_at=NOW,
                ),
                version_issuer=Issuer(),
            ),
            key,
            operation="file.fetch",
            args=args,
        )
        assert denied["outcome"] == "denied"
        assert denied["object_id"] == record.path_hash
        assert denied["size"] == 0
        # Revoked receiver also emits schema-complete FetchResponse and DeleteResponse fields
        receiver = _receiver(service, ledger, key)
        receiver.revoke()
        revoked_fetch = _send(receiver, key, operation="file.fetch", args=args)
        assert revoked_fetch["outcome"] == "denied"
        assert revoked_fetch["object_id"] == record.path_hash
        assert revoked_fetch["size"] == 0
        revoked_del = _send(receiver, key, operation="index.delete", args={"root_ids": ["corp"], "scope": "index"})
        assert revoked_del["outcome"] == "denied"
        assert revoked_del["scope"] == "index"
        assert revoked_del["removed"] == 0
    finally:
        ledger.close()
        store.close()
