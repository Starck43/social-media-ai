"""Standalone updater checks: tmp_path, in-memory bytes, mocked health and network.

No app fixtures, DB, provider write, activation or real feed: signatures use a
synthetic in-process Ed25519 key and every download is an in-memory table.
"""

import hashlib
import io
import json
import os
import stat
import zipfile

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from local_connector.packaging import updater, updates

PLATFORM = "linux-x86_64"
FAKE_BINARY = b"#!/bin/sh\nexit 0\n"


class Feed:
    """Synthetic trusted publisher: signs manifests and builds signed ZIPs."""

    def __init__(self) -> None:
        self.key = Ed25519PrivateKey.generate()
        self.trusted = self.key.public_key().public_bytes_raw()

    def release(self, version, *, now=1000, lifetime=10_000, members=None, launcher=FAKE_BINARY, issued=None):
        members = (
            members
            if members is not None
            else {
                "ai-assistant-connector": launcher,
                "lib/runtime.bin": b"payload",
            }
        )
        artifact = archive(members)
        manifest = json.dumps(
            {
                "schema": 1,
                "product": "ai-assistant-connector",
                "version": version,
                "platform": PLATFORM,
                "sha256": hashlib.sha256(artifact).hexdigest(),
                "size": len(artifact),
                "issued_at": issued if issued is not None else now,
                "expires_at": (issued if issued is not None else now) + lifetime,
            }
        ).encode()
        return manifest, self.key.sign(manifest), artifact


def archive(members) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, data in members.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (0o100755 if data == FAKE_BINARY else 0o100644) << 16
            bundle.writestr(info, data)
    return buffer.getvalue()


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return path


@pytest.fixture(autouse=True)
def synthetic_execution(monkeypatch):
    # The signed lifetime is checked against a pinned clock; the real wall clock
    # is never patched, so ZIP timestamps stay valid.
    monkeypatch.setattr(updater, "_now", lambda: 1000)
    monkeypatch.setattr(updater.UpdateManager, "_probe_health", lambda *_: True)


@pytest.fixture
def publisher(tmp_path):
    (tmp_path / "root").mkdir(mode=0o700)
    return Feed()


@pytest.fixture
def wire(tmp_path):
    def deliver(manifest, signature, artifact):
        return (
            write(tmp_path, "manifest.json", manifest),
            write(tmp_path, "manifest.json.sig", signature),
            write(tmp_path, "artifact.zip", artifact),
        )

    return deliver


@pytest.fixture
def manager(tmp_path, publisher):
    with updater.UpdateManager(tmp_path / "root", publisher.trusted, PLATFORM, "1.0.0") as created:
        yield created


def install(manager, wire, release):
    return manager.install(*wire(*release))


def failing_health(manager):
    def health(binary, slot):
        return False

    manager._health = health


def state(manager):
    return json.loads((manager._tree.path("state.json")).read_text())


def index_bytes(tmp_path):
    index = tmp_path / "root" / "index.sqlite3"
    index.write_bytes(b"index-data")
    return index


def test_install_two_releases_then_rollback(manager, wire, publisher, tmp_path):
    index = index_bytes(tmp_path)
    one = install(manager, wire, publisher.release("1.1.0"))
    two = install(manager, wire, publisher.release("1.2.0"))
    assert one != two
    record = state(manager)
    assert (record["active"], record["previous"], record["high_water"]) == (two, one, "1.2.0")
    binary = manager.active_binary()
    assert binary is not None and binary.parent.name == two
    assert binary.stat().st_mode & 0o111 and not binary.stat().st_mode & 0o077
    assert index.read_bytes() == b"index-data"
    assert manager.rollback(confirmed=True) == one
    record = state(manager)
    assert (record["active"], record["previous"], record["high_water"]) == (one, two, "1.2.0")
    assert index.read_bytes() == b"index-data"
    # A rollback survives a restart and the floor still blocks the old release.
    manager.close()
    with updater.UpdateManager(manager._tree.root, publisher.trusted, PLATFORM, "1.0.0") as reopened:
        assert reopened.rollback(confirmed=True) == two
        with pytest.raises(updates.UpdateRejected):
            reopened.install(*wire(*publisher.release("1.1.0")))


def test_rollback_requires_confirmation_and_previous(manager, wire, publisher):
    with pytest.raises(updater.UpdaterError):
        manager.rollback(confirmed="yes")
    with pytest.raises(updater.UpdaterError):
        manager.rollback(confirmed=None)
    install(manager, wire, publisher.release("1.1.0"))
    with pytest.raises(updater.UpdaterError):
        manager.rollback(confirmed=True)


def test_tampered_expired_and_downgrade_rejected(manager, wire, publisher):
    install(manager, wire, publisher.release("1.1.0"))
    manifest, signature, artifact = publisher.release("1.3.0")
    with pytest.raises(updates.UpdateRejected):
        manager.install(*wire(manifest.replace(b"1.3.0", b"1.4.0"), signature, artifact))
    with pytest.raises(updates.UpdateRejected):
        manager.install(*wire(manifest, signature, artifact + b"tamper"))
    manager._clock = lambda: 200_000
    with pytest.raises(updates.UpdateRejected):
        manager.install(*wire(*publisher.release("1.4.0")))
    manager._clock = lambda: 1_000
    with pytest.raises(updates.UpdateRejected):
        manager.install(*wire(*publisher.release("1.0.5")))
    with pytest.raises(updates.UpdateRejected):
        manager.install(*wire(*publisher.release("1.1.0")))
    assert state(manager)["high_water"] == "1.1.0"


def test_untrusted_key_and_initial_flag(manager, wire, publisher):
    other = Ed25519PrivateKey.generate()
    manifest, _, artifact = publisher.release("1.1.0")
    with pytest.raises(updates.UpdateRejected):
        manager.install(*wire(manifest, other.sign(manifest), artifact))
    with pytest.raises(updater.UpdaterError):
        manager.install(*wire(*publisher.release("1.1.0")), initial=True)


@pytest.mark.parametrize(
    "members",
    [
        {"ai-assistant-connector": FAKE_BINARY, "../escape": b"x"},
        {"ai-assistant-connector": FAKE_BINARY, "/etc/passwd": b"x"},
        {"ai-assistant-connector": FAKE_BINARY, "..\\escape": b"x"},
        {"ai-assistant-connector": FAKE_BINARY, "C:/evil": b"x"},
        {"ai-assistant-connector": FAKE_BINARY, "lib/a:stream": b"x"},
        {"ai-assistant-connector": FAKE_BINARY, "lib/A": b"1", "lib/a": b"2"},
    ],
)
def test_unsafe_archive_members_rejected(manager, wire, publisher, tmp_path, members):
    with pytest.raises(updater.UpdaterError):
        install(manager, wire, publisher.release("1.1.0", members=members))
    assert not (tmp_path / "escape").exists()
    assert manager.active_binary() is None
    assert [name for name, mode in manager._tree.listdir("slots") if stat.S_ISDIR(mode)] == []
    assert [name for name, mode in manager._tree.listdir("staging") if stat.S_ISDIR(mode)] == []


def test_file_marked_as_directory_rejected(manager, wire, publisher):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("ai-assistant-connector", FAKE_BINARY)
        entry = zipfile.ZipInfo("lib/odd")
        entry.external_attr = 0x10  # MS-DOS directory attribute on a file name
        bundle.writestr(entry, b"x")
    artifact = buffer.getvalue()
    manifest, _, _ = publisher.release("1.1.0")
    payload = json.loads(manifest)
    payload["sha256"], payload["size"] = hashlib.sha256(artifact).hexdigest(), len(artifact)
    signed = json.dumps(payload).encode()
    with pytest.raises(updater.UpdaterError):
        manager.install(*wire(signed, publisher.key.sign(signed), artifact))


def test_symlink_member_rejected(manager, wire, publisher):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("ai-assistant-connector", FAKE_BINARY)
        link = zipfile.ZipInfo("lib/link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(link, "/etc/passwd")
    artifact = buffer.getvalue()
    manifest, signature, _ = publisher.release("1.1.0")
    payload = json.loads(manifest)
    payload["sha256"], payload["size"] = hashlib.sha256(artifact).hexdigest(), len(artifact)
    signed = json.dumps(payload).encode()
    with pytest.raises(updater.UpdaterError):
        manager.install(*wire(signed, publisher.key.sign(signed), artifact))
    assert manager.active_binary() is None


def test_archive_bounds_rejected(manager, wire, publisher, monkeypatch):
    monkeypatch.setattr(updater, "MAX_ENTRIES", 1)
    with pytest.raises(updater.UpdaterError):
        install(manager, wire, publisher.release("1.1.0"))


def test_bad_health_keeps_current_slot(manager, wire, publisher, tmp_path):
    index = index_bytes(tmp_path)
    first = install(manager, wire, publisher.release("1.1.0"))
    failing_health(manager)
    with pytest.raises(updater.UpdaterError):
        install(manager, wire, publisher.release("1.2.0"))
    record = state(manager)
    assert record["active"] == first and record["high_water"] == "1.1.0"
    assert manager.active_binary().parent.name == first
    assert index.read_bytes() == b"index-data"


def test_missing_launcher_and_self_test_timeout(manager, wire, publisher):
    with pytest.raises(updater.UpdaterError):
        install(manager, wire, publisher.release("1.1.0", members={"lib/runtime.bin": b"x"}))

    def timeout(binary, slot):
        raise updater.subprocess.TimeoutExpired("binary", 1)

    manager._health = timeout
    with pytest.raises(updater.subprocess.TimeoutExpired):
        install(manager, wire, publisher.release("1.2.0"))
    assert manager.active_binary() is None


def test_crash_after_stage_leaves_pointer_unchanged(manager, wire, publisher):
    first = install(manager, wire, publisher.release("1.1.0"))

    def crash(*_args, **_kwargs):
        raise OSError("simulated crash before the pointer flip")

    manager._write_state = crash
    with pytest.raises(OSError):
        install(manager, wire, publisher.release("1.2.0"))
    manager.close()
    publisher_trusted = publisher.trusted
    with updater.UpdateManager(manager._tree.root, publisher_trusted, PLATFORM, "1.0.0") as reopened:
        record = state(reopened)
        assert record["active"] == first and record["high_water"] == "1.1.0"
        assert reopened.active_binary().parent.name == first


@pytest.mark.parametrize(
    "mutation",
    [
        lambda record: record.update(schema=2),
        lambda record: record.update(active="../evil"),
        lambda record: record.update(high_water="0.9.0"),
        lambda record: record.update(extra=1),
    ],
)
def test_state_corruption_denied(tmp_path, publisher, wire, mutation):
    root = tmp_path / "root"
    with updater.UpdateManager(root, publisher.trusted, PLATFORM, "1.0.0") as manager:
        install(manager, wire, publisher.release("1.1.0"))
        record = state(manager)
    mutation(record)
    (root / "state.json").write_text(json.dumps(record))
    with pytest.raises(updater.UpdaterError):
        updater.UpdateManager(root, publisher.trusted, PLATFORM, "1.0.0")


def test_missing_state_with_slots_never_bootstraps(tmp_path, publisher, wire):
    root = tmp_path / "root"
    with updater.UpdateManager(root, publisher.trusted, PLATFORM, "1.0.0") as manager:
        install(manager, wire, publisher.release("1.1.0"))
    (root / "state.json").unlink()
    with pytest.raises(updater.UpdaterError):
        updater.UpdateManager(root, publisher.trusted, PLATFORM, "1.0.0")


def test_missing_state_without_slots_bootstraps(tmp_path, publisher):
    root = tmp_path / "root"
    with updater.UpdateManager(root, publisher.trusted, PLATFORM, "1.0.0") as manager:
        assert state(manager)["high_water"] == "1.0.0"
        assert manager.active_binary() is None


def test_root_and_component_link_denied(tmp_path, publisher):
    public = tmp_path / "root"
    public.chmod(0o755)
    with pytest.raises(updater.UpdaterError):
        updater.UpdateManager(public, publisher.trusted, PLATFORM, "1.0.0")
    target = tmp_path / "elsewhere"
    target.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(updater.UpdaterError):
        updater.UpdateManager(link, publisher.trusted, PLATFORM, "1.0.0")
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    with updater.UpdateManager(private, publisher.trusted, PLATFORM, "1.0.0") as manager:
        manager.close()
        (private / "slots").rmdir()
        os.symlink(target, private / "slots", target_is_directory=True)
        with pytest.raises((OSError, updater.UpdaterError)):
            updater.UpdateManager(private, publisher.trusted, PLATFORM, "1.0.0")


def test_windows_seam_fails_closed(tmp_path, publisher, monkeypatch):
    monkeypatch.setattr(updater.os, "name", "nt")
    with pytest.raises(updater.UpdaterError):
        updater.UpdateManager(tmp_path / "root", publisher.trusted, PLATFORM, "1.0.0")


@pytest.mark.parametrize(
    "trusted_key, platform",
    [(b"short", PLATFORM), (bytes(32), PLATFORM), (b"k" * 32, "plan9")],
)
def test_constructor_requires_bundled_key_and_platform(tmp_path, trusted_key, platform):
    with pytest.raises(updater.UpdaterError):
        updater.UpdateManager(tmp_path / "root", trusted_key, platform, "1.0.0")


def test_previous_slot_tampering_blocks_rollback(manager, wire, publisher):
    install(manager, wire, publisher.release("1.1.0"))
    install(manager, wire, publisher.release("1.2.0"))
    previous = state(manager)["previous"]
    for entry in (manager._tree.path("objects")).rglob("*"):
        if entry.is_file():
            entry.chmod(0o600)
            entry.write_bytes(b"corrupt")
    with pytest.raises(updater.UpdaterError):
        manager.rollback(confirmed=True)
    assert state(manager)["active"] != previous


def test_rollback_honours_expired_archived_release(manager, wire, publisher):
    one = install(manager, wire, publisher.release("1.1.0", now=1000, lifetime=500))
    install(manager, wire, publisher.release("1.2.0", now=1000, lifetime=500))
    manager._clock = lambda: 10_000  # both feeds have expired long ago
    assert manager.rollback(confirmed=True) == one


def test_prune_keeps_owned_names_only(manager, wire, publisher):
    for version in ("1.1.0", "1.2.0", "1.3.0", "1.4.0"):
        install(manager, wire, publisher.release(version))
    slots = [name for name, mode in manager._tree.listdir("slots") if stat.S_ISDIR(mode)]
    assert len(slots) == updater.MAX_SLOTS
    foreign = manager._tree.path("slots", "not-a-slot")
    foreign.mkdir()
    (manager._tree.path("objects", "zz")).mkdir()
    install(manager, wire, publisher.release("1.5.0"))
    assert foreign.exists()
    with pytest.raises(updater.UpdaterError):
        manager._tree.remove_tree("slots/not-a-slot")


def test_install_is_idempotent_and_capped(manager, wire, publisher):
    first = install(manager, wire, publisher.release("1.1.0"))
    with pytest.raises(updates.UpdateRejected):
        install(manager, wire, publisher.release("1.1.0"))
    assert state(manager)["active"] == first
    assert [name for name, mode in manager._tree.listdir("staging") if stat.S_ISDIR(mode)] == []


# --------------------------------------------------------------------------- feed


class FakeResponse:
    def __init__(self, url, body, *, status=200, redirect=None, chunk=64 * 1024):
        self._url, self._body, self._status = url, body, status
        self._redirect, self._chunk = redirect, chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    @property
    def status(self):
        return self._status

    def geturl(self):
        return self._redirect or self._url

    def read(self, size):
        chunk, self._body = self._body[:size], self._body[size:]
        return chunk


FEED_BASE = "https://updates.example.test/connector"


def feed_table(publisher, manifest, signature, artifact):
    return {
        f"{FEED_BASE}/{PLATFORM}/manifest.json": manifest,
        f"{FEED_BASE}/{PLATFORM}/manifest.json.sig": signature,
        f"{FEED_BASE}/artifacts/{hashlib.sha256(artifact).hexdigest()}.zip": artifact,
    }


@pytest.fixture
def network(manager):
    table = {}
    seen = []

    def opener(url, timeout):
        seen.append(url)
        body = table.get(url)
        if body is None:
            raise updater.urllib.error.URLError("no route")
        return FakeResponse(url, body)

    manager._opener = opener
    return table, seen


def test_automatic_update_downloads_and_installs(manager, network, publisher):
    table, seen = network
    table.update(feed_table(publisher, *publisher.release("1.1.0")))
    digest = manager.automatic_update(FEED_BASE)
    assert digest == state(manager)["active"]
    assert seen == [
        f"{FEED_BASE}/{PLATFORM}/manifest.json",
        f"{FEED_BASE}/{PLATFORM}/manifest.json.sig",
        f"{FEED_BASE}/artifacts/{hashlib.sha256(publisher.release('1.1.0')[2]).hexdigest()}.zip",
    ]
    assert manager.active_binary().parent.name == digest


def test_automatic_update_rejects_unsigned_artifact_url_override(manager, network, publisher):
    table, _ = network
    manifest, signature, artifact = publisher.release("1.1.0")
    payload = json.loads(manifest)
    payload["url"] = "https://evil.example/artifact.zip"
    signed = json.dumps(payload).encode()
    table.update(feed_table(publisher, signed, publisher.key.sign(signed), artifact))
    # The extra field is not part of the closed schema, so the manifest itself
    # is refused before any artifact is fetched.
    with pytest.raises(updates.UpdateRejected):
        manager.automatic_update(FEED_BASE)
    assert manager.active_binary() is None


def test_automatic_update_digest_mismatch_never_installs(manager, network, publisher):
    table, _ = network
    manifest, signature, artifact = publisher.release("1.1.0")
    table.update(feed_table(publisher, manifest, signature, artifact + b"x"))
    table[f"{FEED_BASE}/artifacts/{hashlib.sha256(artifact).hexdigest()}.zip"] = artifact + b"x"
    with pytest.raises(updates.UpdateRejected):
        manager.automatic_update(FEED_BASE)
    assert manager.active_binary() is None


@pytest.mark.parametrize(
    "feed",
    [
        "http://updates.example.test/connector",
        "https://user:pass@updates.example.test/connector",
        "https://updates.example.test/connector?token=1",
        "https://updates.example.test/connector#fragment",
        "https://updates.example.test:8443/connector",
        "file:///tmp/feed",
        "https:///connector",
        "https://updates.example.test\\connector",
        "",
        None,
    ],
)
def test_feed_policy_denies_unsafe_bases(manager, feed):
    with pytest.raises(updater.UpdaterError):
        manager.automatic_update(feed)


def test_feed_redirect_and_oversize_denied(manager):
    manager._opener = lambda url, timeout: FakeResponse(url, b"{}", redirect="https://evil.example/x")
    with pytest.raises(updater.UpdaterError):
        manager.automatic_update(FEED_BASE)
    manager._opener = lambda url, timeout: FakeResponse(url, b"x" * (65 * 1024))
    # An oversized manifest is refused as an authentication failure.
    with pytest.raises(updates.UpdateRejected):
        manager.automatic_update(FEED_BASE)


def test_default_opener_never_redirects_to_another_origin():
    """The real policy handler rejects redirects instead of following them."""

    handler = updater._NoRedirect()
    with pytest.raises(updater.UpdaterError):
        handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/x")
    with pytest.raises(updater.UpdaterError):
        handler.redirect_request(None, None, 301, "Moved", {}, FEED_BASE)


def test_network_unavailable_keeps_state(manager, network, publisher, wire):
    install(manager, wire, publisher.release("1.1.0"))
    before = state(manager)
    with pytest.raises(updater.UpdaterError):
        manager.automatic_update(FEED_BASE)
    assert state(manager) == before
