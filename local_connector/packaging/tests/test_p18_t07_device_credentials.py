"""Focused, no-DB tests for the OS key store and the no-data mTLS diagnostic.

Only the OS and TLS boundaries are doubled: the keyring backend is a dict-shaped
fake carrying an approved class identity, the socket is an injected factory, and
the ``SSLContext`` is a stub that records the PEM files it is handed and asserts
the key's file mode. No real key store, no network, no application import. The
native roundtrip test is opt-in and, unless its environment variable is set, runs
no key-store operation at all.
"""

from __future__ import annotations

import importlib
import os
import re
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from local_connector.packaging import key_store as store_module  # noqa: E402
from local_connector.packaging import tls_probe as probe_module  # noqa: E402
from local_connector.packaging.key_store import (  # noqa: E402
    APPROVED_KEYRING_BACKENDS,
    DeviceCredentials,
    KeyStoreError,
    OsKeyStore,
    PrivateDirectoryRequired,
    approved_backends,
    request_private_directory,
)
from local_connector.packaging.tls_probe import (  # noqa: E402
    DiagnosticDenied,
    probe_mtls_handshake,
    validate_allowlist,
)

_SYNTHETIC_KEY = ed25519.Ed25519PrivateKey.generate()
_NAME = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "connector.example.test")])
_NOW = datetime.now(timezone.utc)
CERTIFICATE_PEM = (
    x509.CertificateBuilder()
    .subject_name(_NAME)
    .issuer_name(_NAME)
    .public_key(_SYNTHETIC_KEY.public_key())
    .serial_number(1)
    .not_valid_before(_NOW - timedelta(days=1))
    .not_valid_after(_NOW + timedelta(days=1))
    .sign(_SYNTHETIC_KEY, None)
    .public_bytes(serialization.Encoding.PEM)
)
CA_PEM = CERTIFICATE_PEM
PRIVATE_KEY_PEM = _SYNTHETIC_KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
)
SALT = "t07-0123456789abcdef"
DEVICE = "device-0001"


def _device(**overrides) -> DeviceCredentials:
    values = {"device_id": DEVICE, "certificate_pem": CERTIFICATE_PEM, "private_key_pem": PRIVATE_KEY_PEM}
    values.update(overrides)
    return DeviceCredentials(**values)


class _FakeKeyring:
    """Approved-identity backend double: a dict plus failure switches."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], str] = {}
        self.locked = False
        self.fail_delete = False
        self.ignore_delete = False
        self.writes: list[tuple[str, str, str]] = []

    def get_password(self, service: str, username: str):
        if self.locked:
            raise RuntimeError("keychain is locked at /Users/someone/Library/Keychains/login")
        return self._items.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        if self.locked:
            raise RuntimeError("approval was refused by the user")
        self._items[(service, username)] = password
        self.writes.append((service, username, password))

    def delete_password(self, service: str, username: str) -> None:
        if self.fail_delete:
            raise RuntimeError("delete denied")
        if self.ignore_delete:
            return
        self._items.pop((service, username), None)


def _approved_fake(module: str, name: str):
    fake_type = type(name, (_FakeKeyring,), {"__module__": module})
    return fake_type()


@pytest.fixture
def fake():
    return _approved_fake("keyring.backends.macOS", "Keyring")


def _store(fake, **kwargs) -> OsKeyStore:
    options = {"device_id": DEVICE, "salt": SALT}
    options.update(kwargs)
    return OsKeyStore(fake, **options)


def _private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True)
    os.chmod(path, 0o700)
    return path


class _FakeTlsSocket:
    def __init__(self, *, version="TLSv1.3", cipher=("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256), peer=b"server-der"):
        self.version_value = version
        self.cipher_value = cipher
        self.peer = peer
        self.cache = {"subjectAltName": (("DNS", "connector.example.com"),)}
        self.closed = False
        self.unwrapped = False

    def settimeout(self, _timeout):
        pass

    def version(self):
        return self.version_value

    def cipher(self):
        return self.cipher_value

    def getpeercert(self, binary_form=False):
        return self.peer if binary_form else self.cache

    def unwrap(self):
        self.unwrapped = True

    def close(self):
        self.closed = True


class _FakeRawSocket:
    def __init__(self) -> None:
        self.closed = False

    def settimeout(self, _timeout):
        pass

    def close(self):
        self.closed = True


class _FakeContext:
    """Records the PEM files handed to ``load_cert_chain`` and fakes one handshake."""

    def __init__(self):
        self.loaded = []
        self.tls = _FakeTlsSocket()
        self.raw = _FakeRawSocket()
        self.handshake_error = None

    def wrap_socket(self, raw, server_hostname=None):
        assert server_hostname == "connector.example.com"
        if self.handshake_error is not None:
            raise self.handshake_error
        return self.tls

    def load_cert_chain(self, certfile, keyfile):
        certificate, key = Path(certfile), Path(keyfile)
        self.loaded.append(
            {
                "certificate": certificate.read_bytes(),
                "key": key.read_bytes(),
                "key_mode": key.stat().st_mode & 0o777,
                "directory_mode": key.parent.stat().st_mode & 0o777,
            }
        )


def _probe(monkeypatch, context, *, writable_dir, key_store, allowlist=("connector.example.com",), **kwargs):
    opened = []

    def open_socket(address, timeout):
        opened.append((address, timeout))
        return context.raw

    monkeypatch.setattr(probe_module, "_client_context", lambda _ca: context)
    result = probe_mtls_handshake(
        allowlist=allowlist,
        hostname="connector.example.com",
        ca_pem=CA_PEM,
        certificate_pem=CERTIFICATE_PEM,
        key_store=key_store,
        private_dir=writable_dir,
        open_socket=open_socket,
        **kwargs,
    )
    return result, opened


# --------------------------------------------------------------------- backend selection


def test_approved_backends_cover_three_os_families_and_nothing_else():
    assert approved_backends("darwin") == ("keyring.backends.macOS.Keyring",)
    assert approved_backends("win32") == ("keyring.backends.Windows.WinVaultKeyring",)
    assert approved_backends("linux") == ("keyring.backends.SecretService.Keyring",)
    assert approved_backends("linux2") == ("keyring.backends.SecretService.Keyring",)
    assert approved_backends("freebsd14") == ()
    assert all("file" not in name for name in APPROVED_KEYRING_BACKENDS.values())


def test_plaintext_and_unknown_backends_are_refused():
    with pytest.raises(KeyStoreError, match=store_module.REASON_BACKEND):
        _store(_approved_fake("keyring.backends.file", "PlaintextKeyring"))
    with pytest.raises(KeyStoreError, match=store_module.REASON_BACKEND):
        _store(_approved_fake("some_plugin", "Keyring"))
    with pytest.raises(KeyStoreError, match=store_module.REASON_BACKEND):
        OsKeyStore(None, device_id=DEVICE, salt=SALT)


def test_namespace_and_input_bounds(fake):
    assert _store(fake).backend_name == "Keyring"
    for bad in [
        {"device_id": ""},
        {"device_id": "bad id"},
        {"device_id": "d" * 161},
        {"salt": "x" * 65},
        {"salt": "bad salt"},
        {"service": "Bad Service"},
        {"username": "a/b"},
    ]:
        with pytest.raises(KeyStoreError):
            _store(fake, **bad)


BROKEN_KEY_PEM = b"-----BEGIN ENCRYPTED PRIVATE KEY-----\nAAAA\n-----END ENCRYPTED PRIVATE KEY-----\n"


def test_credentials_reject_encrypted_trailing_and_oversized_material():
    with pytest.raises(KeyStoreError):
        _device(private_key_pem=BROKEN_KEY_PEM)
    with pytest.raises(KeyStoreError):
        _device(private_key_pem=PRIVATE_KEY_PEM + b"trailing")
    with pytest.raises(KeyStoreError):
        _device(private_key_pem=PRIVATE_KEY_PEM.replace(b"PRIVATE KEY", b"YOUR KEY"))
    with pytest.raises(KeyStoreError):
        _device(private_key_pem=b"x" * (store_module.MAX_PRIVATE_KEY_PEM_BYTES + 1))
    with pytest.raises(KeyStoreError):
        _device(certificate_pem=b"not a certificate")
    with pytest.raises(KeyStoreError):
        _device(certificate_pem=b"x" * (store_module.MAX_CERTIFICATE_PEM_BYTES + 1))
    with pytest.raises(KeyStoreError):
        _device(tenant="tab\there")
    with pytest.raises(KeyStoreError):
        _device(owner="o" * 161)


def test_payload_roundtrip_is_deterministic_and_rejects_tampering():
    original = _device(tenant="tenant-a", owner="owner-a")
    payload = original.to_payload()
    assert payload == _device(tenant="tenant-a", owner="owner-a").to_payload()
    assert DeviceCredentials.from_payload(payload) == original
    assert DeviceCredentials.from_payload(payload.decode("utf-8")) == original
    assert PRIVATE_KEY_PEM not in _device(tenant="x").to_payload().split(b"PrivateKeyPem")[0]
    for tampered in (b"", b"junk", payload.replace(b"connector-device-credentials/1", b"v0"), payload[:-5]):
        with pytest.raises(KeyStoreError):
            DeviceCredentials.from_payload(tampered)
    # A size line that overstates its field must not shift the remaining parse.
    with pytest.raises(KeyStoreError):
        DeviceCredentials.from_payload(payload.replace(f"DeviceId {len(DEVICE)}".encode(), b"DeviceId 99"))
    with pytest.raises(KeyStoreError):
        DeviceCredentials.from_payload(payload + b"extra")


def test_store_load_and_probe_use_the_approved_backend(fake):
    store = _store(fake)
    assert store.probe() is True
    credentials = _device(tenant="tenant-a")
    store.store(credentials)
    assert len(fake.writes) >= 2  # Payload chunk(s), then the commit metadata.
    service, username, written = fake.writes[0]
    assert service == store_module.DEFAULT_SERVICE
    assert username == f"{store_module.DEFAULT_SERVICE}:{SALT}:{DEVICE}.0"
    assert all(len(value.encode("utf-16-le")) <= 2560 for _, _, value in fake.writes)
    assert PRIVATE_KEY_PEM.decode() in "".join(value for _, _, value in fake.writes[:-1])
    assert store.load() == credentials
    assert store.load_private_key_pem() == PRIVATE_KEY_PEM
    assert store.is_present(expected=credentials) is True


def test_store_never_overwrites_different_material(fake):
    store = _store(fake)
    store.store(_device())
    with pytest.raises(KeyStoreError, match=store_module.REASON_REFUSED):
        store.store(_device(tenant="tenant-b"))
    assert store.load() == _device()


def test_probe_denies_locked_or_occupied_namespace(fake):
    store = _store(fake)
    fake.locked = True
    with pytest.raises(KeyStoreError) as denied:
        store.probe()
    assert str(denied.value) == store_module.REASON_LOCKED
    fake.locked = False
    username = f"{store_module.DEFAULT_SERVICE}:{SALT}:{DEVICE}{store_module.SUFFIX_PROBE}"
    fake.set_password(store_module.DEFAULT_SERVICE, username, "occupied")
    with pytest.raises(KeyStoreError, match=store_module.REASON_REFUSED):
        store.probe()


def test_errors_are_sanitized_and_never_leak_backend_text(fake):
    store = _store(fake)
    fake.locked = True
    with pytest.raises(KeyStoreError) as denied:
        store.load()
    assert "Keychains" not in str(denied.value)
    assert denied.value.__cause__ is None
    assert denied.value.__suppress_context__ is True


def test_missing_entry_and_expected_mismatch(fake):
    store = _store(fake)
    assert store.is_present() is False
    assert store.remove() is False
    with pytest.raises(KeyStoreError, match=store_module.REASON_REFUSED):
        store.load()
    store.store(_device())
    with pytest.raises(KeyStoreError, match=store_module.REASON_MISMATCH):
        store.is_present(expected=_device(tenant="someone-else"))


def test_remove_verifies_deletion_and_refuses_silent_failures(fake):
    store = _store(fake)
    store.store(_device())
    assert store.remove() is True
    assert store.is_present() is False
    store.store(_device())
    fake.ignore_delete = True
    with pytest.raises(KeyStoreError, match=store_module.REASON_REFUSED):
        store.remove()
    fake.ignore_delete = False
    fake.fail_delete = True
    with pytest.raises(KeyStoreError, match=store_module.REASON_LOCKED):
        store.remove()


def test_windows_private_directory_check_requires_a_supplied_handler():
    assert not hasattr(request_private_directory, "handler")
    with pytest.raises(PrivateDirectoryRequired):
        request_private_directory("/tmp/keys")
    request_private_directory.handler = lambda path: path + "-changed"
    try:
        with pytest.raises(PrivateDirectoryRequired):
            request_private_directory("/tmp/keys")
        request_private_directory.handler = lambda path: path
        assert request_private_directory("/tmp/keys") == "/tmp/keys"
    finally:
        del request_private_directory.handler


def test_windows_target_uses_the_supplied_private_directory_check(monkeypatch, tmp_path, fake):
    checked = []
    monkeypatch.setattr(probe_module.sys, "platform", "win32")

    def handler(path):
        checked.append(path)
        return path

    request_private_directory.handler = handler
    store = _store(fake)
    store.store(_device())
    try:
        result, _ = _probe(monkeypatch, _FakeContext(), writable_dir=tmp_path, key_store=store)
    finally:
        del request_private_directory.handler
    assert checked == [str(tmp_path)]
    assert result.tls_version == "TLSv1.3"


def test_windows_target_without_handler_refuses_to_write_key(monkeypatch, tmp_path, fake):
    monkeypatch.setattr(probe_module.sys, "platform", "win32")
    store = _store(fake)
    store.store(_device())
    with pytest.raises(DiagnosticDenied, match="owner-private"):
        _probe(monkeypatch, _FakeContext(), writable_dir=tmp_path, key_store=store)
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------- TLS diagnostic


def test_allowlist_is_explicit_and_canonical():
    assert validate_allowlist(["Connector.Example.COM."]) == ("connector.example.com",)
    assert validate_allowlist(["a.example.com", "a.example.com"]) == ("a.example.com",)
    for bad in [
        [],
        "connector.example.com",
        ["*.example.com"],
        ["localhost"],
        ["127.0.0.1"],
        [42],
        ["bad host.example.com"],
        [f"h{n}.example.com" for n in range(33)],
    ]:
        with pytest.raises(DiagnosticDenied):
            validate_allowlist(bad)


def test_handshake_uses_temporary_pem_with_owner_only_key_and_cleans_up(monkeypatch, tmp_path, fake):
    store = _store(fake)
    store.store(_device())
    context = _FakeContext()
    directory = _private_dir(tmp_path / "keys")
    result, opened = _probe(monkeypatch, context, writable_dir=directory, key_store=store)
    assert opened == [(("connector.example.com", 443), 10.0)]
    assert len(context.loaded) == 1
    loaded = context.loaded[0]
    assert loaded["key"] == PRIVATE_KEY_PEM
    assert loaded["key_mode"] == 0o600
    assert loaded["directory_mode"] == 0o700
    assert loaded["certificate"] == CERTIFICATE_PEM
    assert result.hostname == "connector.example.com"
    assert result.port == 443
    assert result.cipher == "TLS_AES_256_GCM_SHA384"
    assert re.fullmatch(r"[0-9a-f]{64}", result.server_certificate_sha256)
    assert re.fullmatch(r"[0-9a-f]{64}", result.client_certificate_sha256)
    assert context.tls.closed and context.tls.unwrapped
    assert list(directory.iterdir()) == []


def test_handshake_failure_still_removes_the_key(monkeypatch, tmp_path, fake):
    store = _store(fake)
    store.store(_device())
    context = _FakeContext()
    context.handshake_error = OSError("connection reset by peer at 10.0.0.1")
    directory = _private_dir(tmp_path / "keys")
    with pytest.raises(DiagnosticDenied) as denied:
        _probe(monkeypatch, context, writable_dir=directory, key_store=store)
    assert "10.0.0.1" not in str(denied.value)
    assert context.raw.closed
    assert list(directory.iterdir()) == []


def test_certificate_key_mismatch_removes_the_key(monkeypatch, tmp_path, fake):
    store = _store(fake)
    store.store(_device())
    context = _FakeContext()

    def load_cert_chain(certfile, keyfile):
        raise probe_module.ssl.SSLError("key values mismatch at /private/var/tmp/connector")

    context.load_cert_chain = load_cert_chain
    directory = _private_dir(tmp_path / "keys")
    with pytest.raises(DiagnosticDenied) as denied:
        _probe(monkeypatch, context, writable_dir=directory, key_store=store)
    assert "/private/var/tmp" not in str(denied.value)
    assert list(directory.iterdir()) == []


def test_hostname_outside_allowlist_never_opens_a_socket(monkeypatch, tmp_path, fake):
    store = _store(fake)
    store.store(_device())
    opened = []
    with pytest.raises(DiagnosticDenied, match="allowlist"):
        probe_mtls_handshake(
            allowlist=("connector.example.com",),
            hostname="evil.example.net",
            ca_pem=CA_PEM,
            certificate_pem=CERTIFICATE_PEM,
            key_store=store,
            private_dir=_private_dir(tmp_path / "keys"),
            open_socket=lambda *args, **kwargs: opened.append(args),
        )
    assert opened == []


@pytest.mark.parametrize(
    ("version", "cipher", "allowed"),
    [
        ("TLSv1.1", ("AES256", "TLSv1.1", 256), False),
        ("TLSv1.2", ("AES128-SHA", "TLSv1.2", 128), False),
        ("TLSv1.2", ("ECDHE-RSA-AES256-GCM-SHA384", "TLSv1.2", 256), True),
        ("TLSv1.3", ("TLS_CHACHA20_POLY1305_SHA256", "TLSv1.3", 256), True),
    ],
)
def test_protocol_and_cipher_floor(monkeypatch, tmp_path, fake, version, cipher, allowed):
    store = _store(fake)
    store.store(_device())
    context = _FakeContext()
    context.tls = _FakeTlsSocket(version=version, cipher=cipher)
    directory = _private_dir(tmp_path / "keys")
    if allowed:
        result, _ = _probe(monkeypatch, context, writable_dir=directory, key_store=store)
        assert result.tls_version == version
    else:
        with pytest.raises(DiagnosticDenied, match="handshake"):
            _probe(monkeypatch, context, writable_dir=directory, key_store=store)
    assert list(directory.iterdir()) == []


def test_missing_server_certificate_is_refused(monkeypatch, tmp_path, fake):
    store = _store(fake)
    store.store(_device())
    context = _FakeContext()
    context.tls = _FakeTlsSocket(peer=b"")
    directory = _private_dir(tmp_path / "keys")
    with pytest.raises(DiagnosticDenied, match="client certificate"):
        _probe(monkeypatch, context, writable_dir=directory, key_store=store)
    assert list(directory.iterdir()) == []


def test_handshake_rejects_a_certificate_for_another_host(monkeypatch, tmp_path, fake):
    store = _store(fake)
    store.store(_device())
    context = _FakeContext()
    context.tls = _FakeTlsSocket()
    context.tls.cache = {"subjectAltName": (("DNS", "somebody-else.example.net"),)}
    directory = _private_dir(tmp_path / "keys")
    with pytest.raises(DiagnosticDenied, match="not usable"):
        _probe(monkeypatch, context, writable_dir=directory, key_store=store)
    assert list(directory.iterdir()) == []


def test_server_identity_evidence_is_checked_explicitly():
    assert probe_module._prove_server_identity(
        {"subjectAltName": (("DNS", "Connector.Example.com."),)}, "connector.example.com"
    )
    assert not probe_module._prove_server_identity(
        {"subjectAltName": (("DNS", "a.example.net"),)}, "connector.example.com"
    )
    assert not probe_module._prove_server_identity(
        {"subjectCommonName": "connector.example.com"}, "connector.example.com"
    )
    assert not probe_module._prove_server_identity("junk", "connector.example.com")


def test_non_private_foreign_or_symlinked_directory_denies_before_any_write(monkeypatch, tmp_path, fake):
    store = _store(fake)
    store.store(_device())
    shared = tmp_path / "shared"
    shared.mkdir()
    os.chmod(shared, 0o755)
    with pytest.raises(DiagnosticDenied, match="owner-private"):
        _probe(monkeypatch, _FakeContext(), writable_dir=shared, key_store=store)
    assert list(shared.iterdir()) == []
    target = _private_dir(tmp_path / "target")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(DiagnosticDenied, match="owner-private"):
        _probe(monkeypatch, _FakeContext(), writable_dir=link, key_store=store)
    assert target.exists() and list(target.iterdir()) == []


def test_leftover_key_file_is_refused_and_never_relinked(monkeypatch, tmp_path, fake):
    store = _store(fake)
    store.store(_device())
    directory = _private_dir(tmp_path / "keys")
    (directory / probe_module._KEY_FILE).write_bytes(b"leftover")
    os.chmod(directory / probe_module._KEY_FILE, 0o600)
    with pytest.raises(DiagnosticDenied):
        _probe(monkeypatch, _FakeContext(), writable_dir=directory, key_store=store)
    assert (directory / probe_module._KEY_FILE).read_bytes() == b"leftover"
    assert not (directory / probe_module._CERT_FILE).exists()


def test_real_client_context_refuses_unusable_ca_and_disables_default_trust(monkeypatch):
    with pytest.raises(DiagnosticDenied, match="not usable"):
        probe_module._client_context(b"-----BEGIN CERTIFICATE-----\nMIIBnotacert\n-----END CERTIFICATE-----\n")
    with pytest.raises(DiagnosticDenied, match="not usable"):
        probe_module._client_context(b"\xff\xfe")
    with pytest.raises(DiagnosticDenied, match="not usable"):
        probe_module._client_context(b"MIIBnotacert")
    assert not hasattr(probe_module, "ssl_create_default_context")
    assert "create_default_context" not in Path(probe_module.__file__).read_text(encoding="utf-8")


def test_bad_port_and_timeout_are_refused_before_the_key_is_read(monkeypatch, tmp_path, fake):
    store = _store(fake)
    store.store(_device())
    directory = _private_dir(tmp_path / "keys")

    def forbidden():
        raise AssertionError("key store must not be read for invalid input")

    monkeypatch.setattr(store, "load_pair", forbidden)
    for kwargs in ({"port": 8443}, {"port": "443"}, {"timeout": 0}, {"timeout": 31}):
        with pytest.raises(DiagnosticDenied, match="invalid"):
            _probe(monkeypatch, _FakeContext(), writable_dir=directory, key_store=store, **kwargs)
    assert list(directory.iterdir()) == []


def test_foreign_key_store_objects_are_refused(tmp_path):
    class Truthy:
        def load_private_key_pem(self):
            return PRIVATE_KEY_PEM

    with pytest.raises(DiagnosticDenied):
        probe_mtls_handshake(
            allowlist=("connector.example.com",),
            hostname="connector.example.com",
            ca_pem=CA_PEM,
            certificate_pem=CERTIFICATE_PEM,
            key_store=Truthy(),
            private_dir=tmp_path,
        )


# --------------------------------------------------------------------- opt-in native roundtrip

NATIVE_ENV = "CONNECTOR_T07_NATIVE_KEYSTORE"


@pytest.mark.skipif(not os.environ.get(NATIVE_ENV), reason=f"opt-in native OS key store: set {NATIVE_ENV}=1")
def test_native_keystore_roundtrip_in_disposable_namespace():
    """Real Keychain/Credential Manager/Secret Service roundtrip in a unique namespace.

    Requires the ``keyring`` runtime and an unlocked store; runs nothing unless the
    environment variable is set. Cleanup is verified, so a crashing store cannot
    leave credentials behind silently.
    """

    import keyring.core

    backend = keyring.core.load_keyring(approved_backends()[0])
    store = OsKeyStore(backend, device_id=DEVICE, salt=f"t07-{secrets.token_hex(8)}")
    credentials = _device(tenant="native-test", owner="native-test")
    try:
        assert store.probe() is True
        store.store(credentials)
        assert store.load() == credentials
        assert store.is_present(expected=credentials) is True
    finally:
        store.remove()
    assert store.is_present() is False


def test_modules_import_no_application_database_or_logging_code():
    for name in ("sqlalchemy", "alembic", "app", "local_connector.runtime", "local_connector.index"):
        assert name not in sys.modules
    source = Path(store_module.__file__).read_text(encoding="utf-8") + Path(probe_module.__file__).read_text(
        encoding="utf-8"
    )
    for banned in ("import app", "from app", "sqlalchemy", "subprocess", "os.system", "logging."):
        assert banned not in source
    assert importlib.import_module("local_connector.packaging.key_store") is store_module
