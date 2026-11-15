from __future__ import annotations

import json
import logging
import stat
import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from local_connector.index import IndexEngine, IndexStore
from local_connector.paths import SelectedRoot
from local_connector.search import (
    MAX_CONTROL_BYTES,
    MAX_RESPONSE_BYTES,
    TIMEOUT_SECONDS,
    LocalSearchService,
    ReplayLedger,
    RootRegistration,
    SearchProtocolError,
    SignedCommandReceiver,
    canonical_json,
    decode_control_payload,
    sign_control_payload,
    verify_command_envelope,
)
from local_connector.session import DeviceCommandSession, ProtocolError


def _make_envelope(
    *,
    key_id: str = "gw-v1",
    request_id: str | None = None,
    tenant_id: int = 7,
    owner_id: int = 11,
    actor_id: int = 11,
    device_id: int = 13,
    operation: str = "search",
    issued_at: int = 1_700_000_000,
    expires_at: int = 1_700_000_000 + TIMEOUT_SECONDS,
    device_epoch: int = 1,
    consent_epoch: int = 1,
    policy_epoch: int = 1,
    args: dict | None = None,
) -> dict:
    return {
        "version": 1,
        "key_id": key_id,
        "request_id": request_id or uuid.uuid4().hex,
        "tenant_id": tenant_id,
        "owner_id": owner_id,
        "actor_id": actor_id,
        "device_id": device_id,
        "operation": operation,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "device_epoch": device_epoch,
        "consent_epoch": consent_epoch,
        "policy_epoch": policy_epoch,
        "data_boundary": "company",
        "max_response_bytes": MAX_RESPONSE_BYTES,
        "max_snippet_characters": 512,
        "args": args if args is not None else {"query": "alpha", "roots": ["corp"], "limit": 20},
    }


def _setup_receiver(tmp_path: Path, *, with_files: int = 2, boundary: str = "company", consented: bool = True):
    root = tmp_path / "corp"
    root.mkdir(exist_ok=True)
    for i in range(with_files):
        (root / f"report_{i:02d}.txt").write_text(
            f"alpha project metric value {i} " + ("tail " * 40),
            encoding="utf-8",
        )
    store = IndexStore(tmp_path / "cache" / "index.sqlite")
    engine = IndexEngine(store)
    selected = SelectedRoot(root)
    engine.scan([selected])

    service = LocalSearchService(
        store,
        [RootRegistration(root_id="corp", selected=selected, boundary=boundary, cloud_consented=consented)],
    )
    ledger = ReplayLedger(store=store)
    signing_key = Ed25519PrivateKey.generate()
    receiver = SignedCommandReceiver(
        search_service=service,
        replay_ledger=ledger,
        trusted_keys={"gw-v1": signing_key.public_key()},
        tenant_id=7,
        owner_id=11,
        device_id=13,
    )
    return store, engine, root, service, ledger, signing_key, receiver


def test_canonical_json_and_decode_strict_validation() -> None:
    assert canonical_json({"z": 2, "a": "тест"}) == '{"a":"тест","z":2}'.encode("utf-8")
    for invalid in ({"v": 1.5}, {"ключ": 1}, {"v": float("nan")}, {"v": float("inf")}):
        with pytest.raises(SearchProtocolError):
            canonical_json(invalid)

    for bad_bytes in (
        b"",
        b"[]",
        b'{"a":1,"a":2}',
        b'{"\xd0\xba\xd0\xbb\xd1\x8e\xd1\x87":1}',
        b'{"a":1.25}',
        b'{"a":NaN}',
        b"\xff",
        b"x" * (MAX_CONTROL_BYTES + 1),
    ):
        with pytest.raises(SearchProtocolError):
            decode_control_payload(bad_bytes)


def test_envelope_signature_identity_expiry_and_allowlist_checks(tmp_path: Path) -> None:
    store, _e, _r, _s, _l, signing_key, receiver = _setup_receiver(tmp_path)
    try:
        base_now = 1_700_000_005
        env = _make_envelope()
        payload, digest = sign_control_payload(env, signing_key)
        cmd = verify_command_envelope(
            payload,
            {"gw-v1": signing_key.public_key()},
            tenant_id=7,
            owner_id=11,
            device_id=13,
            now=base_now,
        )
        assert cmd.digest == digest
        assert "alpha" not in repr(cmd)

        # Wrong key_id, wrong signature, tampered args, extra field, bool as int, actor swap
        other_key = Ed25519PrivateKey.generate()
        bad_payloads = [
            sign_control_payload({**env, "key_id": "unknown"}, signing_key)[0],
            sign_control_payload(env, other_key)[0],
            sign_control_payload({**env, "actor_id": 99}, signing_key)[0],
            sign_control_payload({**env, "tenant_id": 999}, signing_key)[0],
            sign_control_payload({**env, "device_id": 999}, signing_key)[0],
            sign_control_payload({**env, "version": 2}, signing_key)[0],
            sign_control_payload({**env, "operation": "shell.exec"}, signing_key)[0],
            sign_control_payload({**env, "data_boundary": "local_only"}, signing_key)[0],
            sign_control_payload({**env, "args": {"query": "alpha", "roots": ["corp"], "extra": 1}}, signing_key)[0],
            sign_control_payload({**env, "args": {"query": "ю" * 2049, "roots": ["corp"]}}, signing_key)[0],
            sign_control_payload({**env, "args": {"query": "alpha", "roots": ["../escape"]}}, signing_key)[0],
        ]
        for bad in bad_payloads:
            with pytest.raises(SearchProtocolError):
                receiver.handle_payload(bad, now=base_now)

        # Tampering after signing
        decoded = decode_control_payload(payload)
        decoded["args"]["query"] = "tampered"
        with pytest.raises(SearchProtocolError):
            receiver.handle_payload(canonical_json(decoded), now=base_now)

        # Boolean masquerading as int is rejected
        bool_env = {**env, "device_epoch": True}
        with pytest.raises(SearchProtocolError):
            receiver.handle_payload(sign_control_payload(bool_env, signing_key)[0], now=base_now)

        # Expiry / before issued_at rejected
        with pytest.raises(SearchProtocolError) as exp_err:
            receiver.handle_payload(payload, now=env["expires_at"] + 1)
        assert exp_err.value.code == "expired"
        with pytest.raises(SearchProtocolError):
            receiver.handle_payload(payload, now=env["issued_at"] - 1)
    finally:
        store.close()


def test_receiver_handles_search_health_status_and_unimplemented_allowlist_ops(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store, _e, root, _s, _l, signing_key, receiver = _setup_receiver(tmp_path, with_files=10)
    try:
        now = 1_700_000_005
        with caplog.at_level(logging.INFO, logger="local_connector.search.protocol"):
            # 1. Search
            search_env = _make_envelope(operation="search", args={"query": "alpha", "roots": ["corp"], "limit": 10})
            s_payload, s_digest = sign_control_payload(search_env, signing_key)
            resp_bytes = receiver.handle_payload(s_payload, now=now)
            resp = json.loads(resp_bytes)
            assert resp["outcome"] == "succeeded"
            assert resp["request_id"] == search_env["request_id"]
            assert resp["request_digest"] == s_digest
            assert resp["device_id"] == 13
            assert len(resp["hits"]) == 10
            assert sum(hit["snippet"] is not None for hit in resp["hits"]) == 8
            assert all(not Path(hit["reference"]["path"]).is_absolute() for hit in resp["hits"])
            assert all(hit["data_boundary"] == "company" for hit in resp["hits"])
            assert all(hit["evidence_kind"] == "local_search_observation" for hit in resp["hits"])

            # 2. Health (no file paths, no counts)
            h_payload, _ = sign_control_payload(_make_envelope(operation="health", args={}), signing_key)
            h_resp = json.loads(receiver.handle_payload(h_payload, now=now))
            assert h_resp["outcome"] == "succeeded"
            assert h_resp["status"] == "ok"
            assert "roots" not in h_resp

            # 3. index.status (counts only for consented roots, zero paths)
            st_payload, _ = sign_control_payload(
                _make_envelope(operation="index.status", args={"roots": ["corp"]}), signing_key
            )
            st_resp = json.loads(receiver.handle_payload(st_payload, now=now))
            assert st_resp["outcome"] == "succeeded"
            assert st_resp["roots"] == [{"indexed_files": 10, "root_id": "corp"}]

            # index.status on unconsented root denies
            bad_st, _ = sign_control_payload(
                _make_envelope(operation="index.status", args={"roots": ["other"]}), signing_key
            )
            assert json.loads(receiver.handle_payload(bad_st, now=now))["outcome"] == "denied"

            # 4. P18-T06 ops in protocol allowlist stay fail-closed unavailable in P18-T05
            for op in ("file.metadata", "file.fetch", "index.delete"):
                op_payload, _ = sign_control_payload(_make_envelope(operation=op, args={}), signing_key)
                op_resp = json.loads(receiver.handle_payload(op_payload, now=now))
                assert op_resp["outcome"] == "unavailable"

        # Logs must never leak paths, snippets or queries
        joined_logs = "\n".join(rec.getMessage() for rec in caplog.records)
        assert str(root) not in joined_logs
        assert "alpha" not in joined_logs
        assert "report_00" not in joined_logs
    finally:
        store.close()


def test_response_payload_stays_within_64kib_and_marks_partial(tmp_path: Path) -> None:
    root = tmp_path / "corp"
    nested = root / ("segment_" * 15)  # ~120 chars
    nested.mkdir(parents=True)
    # Create 50 files with long relative paths (~1.4 KiB each -> 50 hits would exceed 64 KiB if untruncated!)
    long_prefix = "/".join(["d" * 120] * 10)
    (nested / long_prefix).mkdir(parents=True)
    for i in range(50):
        (nested / long_prefix / f"f_{i:02d}_" + ("n" * 100) + ".txt") if False else None
        fname = f"file_{i:02d}_" + ("n" * 100) + ".txt"
        (nested / long_prefix / fname).write_text("target_needle content " + ("x" * 400), encoding="utf-8")

    store = IndexStore(tmp_path / "cache" / "index.sqlite")
    try:
        selected = SelectedRoot(root)
        IndexEngine(store).scan([selected])
        service = LocalSearchService(
            store,
            [RootRegistration(root_id="corp", selected=selected, boundary="company", cloud_consented=True)],
        )
        ledger = ReplayLedger(store=store)
        key = Ed25519PrivateKey.generate()
        receiver = SignedCommandReceiver(
            search_service=service,
            replay_ledger=ledger,
            trusted_keys={"gw-v1": key.public_key()},
            tenant_id=7,
            owner_id=11,
            device_id=13,
        )
        payload, _ = sign_control_payload(
            _make_envelope(operation="search", args={"query": "target_needle", "roots": ["corp"], "limit": 50}),
            key,
        )
        resp_bytes = receiver.handle_payload(payload, now=1_700_000_005)
        assert len(resp_bytes) <= MAX_RESPONSE_BYTES
        resp = json.loads(resp_bytes)
        assert resp["outcome"] == "succeeded"
        assert resp["partial"] is True
        assert 1 <= len(resp["hits"]) < 50
    finally:
        store.close()


def test_replay_ledger_digest_binding_restart_acl_recheck_crash_and_limits(tmp_path: Path) -> None:
    root = tmp_path / "corp"
    root.mkdir()
    f1 = root / "first.txt"
    f2 = root / "second.txt"
    f1.write_text("alpha unique first", encoding="utf-8")
    f2.write_text("alpha unique second", encoding="utf-8")

    db_path = tmp_path / "cache" / "index.sqlite"
    replay_path = tmp_path / "cache" / "replay.sqlite"
    store = IndexStore(db_path)
    selected = SelectedRoot(root)
    IndexEngine(store).scan([selected])
    ledger = ReplayLedger(replay_path, capacity=3, ttl_seconds=120)
    assert stat.S_IMODE(replay_path.stat().st_mode) == 0o600
    with pytest.raises(PermissionError, match="already in use"):
        ReplayLedger(replay_path)

    key = Ed25519PrivateKey.generate()
    service = LocalSearchService(
        store,
        [RootRegistration(root_id="corp", selected=selected, boundary="company", cloud_consented=True)],
    )
    receiver = SignedCommandReceiver(
        search_service=service,
        replay_ledger=ledger,
        trusted_keys={"gw-v1": key.public_key()},
        tenant_id=7,
        owner_id=11,
        device_id=13,
    )

    now = 1_700_000_005
    req_id = "a" * 32
    env1 = _make_envelope(request_id=req_id, args={"query": "alpha", "roots": ["corp"], "limit": 10})
    p1, _ = sign_control_payload(env1, key)
    r1 = json.loads(receiver.handle_payload(p1, now=now))
    assert len(r1["hits"]) == 2

    # Same request_id with DIFFERENT digest is rejected fail-closed
    env_tampered = _make_envelope(request_id=req_id, args={"query": "beta", "roots": ["corp"], "limit": 10})
    p_diff, _ = sign_control_payload(env_tampered, key)
    with pytest.raises(SearchProtocolError) as exc_info:
        receiver.handle_payload(p_diff, now=now)
    assert exc_info.value.code == "denied"

    # Restart simulation + f1 made unreadable before retry -> cached reply re-checks ACLs & drops f1!
    ledger.close()
    store.close()
    f1.chmod(0o000)
    try:
        store2 = IndexStore(db_path)
        ledger2 = ReplayLedger(replay_path, capacity=3, ttl_seconds=120)
        sel2 = SelectedRoot(root)
        service2 = LocalSearchService(
            store2,
            [RootRegistration(root_id="corp", selected=sel2, boundary="company", cloud_consented=True)],
        )
        receiver2 = SignedCommandReceiver(
            search_service=service2,
            replay_ledger=ledger2,
            trusted_keys={"gw-v1": key.public_key()},
            tenant_id=7,
            owner_id=11,
            device_id=13,
        )
        r2 = json.loads(receiver2.handle_payload(p1, now=now + 2))
        assert r2["outcome"] == "succeeded"
        assert r2["partial"] is True
        assert [h["reference"]["path"] for h in r2["hits"]] == ["second.txt"]

        # Crash / incomplete in_flight recovery returns truthful unknown
        crashed_id = "b" * 32
        crashed_env = _make_envelope(request_id=crashed_id, operation="health", args={})
        p_crash, d_crash = sign_control_payload(crashed_env, key)
        ledger2.reserve(
            device_id=13,
            request_id=crashed_id,
            digest=d_crash,
            operation="health",
            issued_at=crashed_env["issued_at"],
            device_epoch=1,
            consent_epoch=1,
            policy_epoch=1,
            now=now + 3,
        )
        crash_retry = json.loads(receiver2.handle_payload(p_crash, now=now + 4))
        assert crash_retry["outcome"] == "unknown"

        # Clock rollback denied fail-closed
        rollback_now = now - 100
        rb_env = _make_envelope(
            operation="health",
            args={},
            issued_at=rollback_now - 5,
            expires_at=rollback_now + 20,
        )
        p_rb, _ = sign_control_payload(rb_env, key)
        with pytest.raises(SearchProtocolError) as rb_err:
            receiver2.handle_payload(p_rb, now=rollback_now)
        assert rb_err.value.code == "unavailable"

        # Fill remaining capacity (currently 2 entries: req_id, crashed_id; capacity=3)
        p3, _ = sign_control_payload(_make_envelope(operation="health", args={}), key)
        assert json.loads(receiver2.handle_payload(p3, now=now + 5))["outcome"] == "succeeded"
        p4, _ = sign_control_payload(_make_envelope(operation="health", args={}), key)
        with pytest.raises(SearchProtocolError) as cap_err:
            receiver2.handle_payload(p4, now=now + 6)
        assert cap_err.value.code == "limit_exceeded"

        # Advancing past TTL prunes expired rows and accepts new command
        later = now + 200
        env_later = _make_envelope(
            operation="health",
            args={},
            issued_at=later - 5,
            expires_at=later + 20,
        )
        p_later, _ = sign_control_payload(env_later, key)
        assert json.loads(receiver2.handle_payload(p_later, now=later))["outcome"] == "succeeded"
    finally:
        if f1.exists():
            f1.chmod(0o600)
        ledger2.close()
        store2.close()


def test_device_command_session_epochs_revocation_and_default_off_gate(tmp_path: Path) -> None:
    store, _e, _r, _s, _l, signing_key, receiver = _setup_receiver(tmp_path)
    try:
        now = 1_700_000_005
        # Default session stays disabled until explicit activation
        disabled_session = DeviceCommandSession(receiver)
        payload, _ = sign_control_payload(_make_envelope(operation="health", args={}), signing_key)
        with pytest.raises(ProtocolError, match="disabled"):
            disabled_session.handle_frame(payload, now=now)

        # Enabled session processes frames and emits replies
        enabled_session = DeviceCommandSession(receiver, enabled=True)
        sent_frames: list[bytes] = []
        reply = enabled_session.receive_and_reply(payload, send=sent_frames.append, now=now)
        assert len(sent_frames) == 1
        assert json.loads(reply)["outcome"] == "succeeded"

        # Advancing policy_epoch rejects stale requests
        receiver.update_epochs(policy_epoch=2)
        stale_payload, _ = sign_control_payload(
            _make_envelope(operation="search", policy_epoch=1, args={"query": "alpha", "roots": ["corp"]}),
            signing_key,
        )
        stale_resp = json.loads(enabled_session.handle_frame(stale_payload, now=now))
        assert stale_resp["outcome"] == "stale"
        assert stale_resp["hits"] == []

        # Revocation rejects further operations
        enabled_session.revoke_and_close()
        with pytest.raises(ProtocolError, match="closed or revoked"):
            enabled_session.handle_frame(
                sign_control_payload(_make_envelope(operation="health", args={}), signing_key)[0],
                now=now,
            )
    finally:
        store.close()


def test_replay_invalidated_by_index_delete_and_consent_removal(tmp_path: Path) -> None:
    store, _e, _r, service, _l, signing_key, receiver = _setup_receiver(tmp_path)
    try:
        now = 1_700_000_005
        env = _make_envelope(operation="search", args={"query": "alpha", "roots": ["corp"], "limit": 5})
        payload, _ = sign_control_payload(env, signing_key)
        first = json.loads(receiver.handle_payload(payload, now=now))
        assert first["outcome"] == "succeeded"
        assert len(first["hits"]) == 2

        # Removing consent turns duplicate replay into denied
        reg = service.root_registrations()[0]
        service.register_root(RootRegistration(root_id="corp", selected=reg.selected, boundary="local_only"))
        denied = json.loads(receiver.handle_payload(payload, now=now + 1))
        assert denied["outcome"] == "denied"
        assert denied["hits"] == []

        # Restore consent, then delete index -> duplicate replay returns stale
        service.register_root(
            RootRegistration(root_id="corp", selected=reg.selected, boundary="company", cloud_consented=True)
        )
        store.delete_all(pause=True)
        stale = json.loads(receiver.handle_payload(payload, now=now + 2))
        assert stale["outcome"] == "stale"
        assert stale["hits"] == []

        # Health duplicate replay re-uses completed outcome cleanly
        h_payload, _ = sign_control_payload(_make_envelope(operation="health", args={}), signing_key)
        h1 = json.loads(receiver.handle_payload(h_payload, now=now + 3))
        h2 = json.loads(receiver.handle_payload(h_payload, now=now + 4))
        assert h1 == h2
        assert h2["paused"] is True
    finally:
        store.close()
