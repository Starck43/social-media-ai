"""No-data mTLS diagnostic: prove one handshake with the device identity, nothing else.

The adapter connects to a hostname on the caller's explicit allowlist, verifies
the server against a caller-supplied CA (system trust is never consulted),
presents the caller-supplied client certificate and the private key held in the OS
key store, completes the TLS handshake and reports only version/cipher/digests.
It sends no application request, reads and writes no file or message data,
downloads nothing and activates nothing; there is no code path from here to the
application, the database, the index or the runtime.

Python's SSL engine accepts a PEM client key only as a file, and no public OpenSSL
API in the standard library takes one from memory. The key is therefore written
into a private temporary directory of the *caller's* choosing (owner-only
``0700``, key file ``0600`` created with ``O_EXCL``), loaded into the
``SSLContext``, and unlinked in every exit path, including handshake failure and
signature mismatch. Failed removal denies rather than silently leaving a key on
disk. On Windows the directory must first pass the parent-supplied narrow check
(:func:`local_connector.packaging.key_store.request_private_directory`).
"""

from __future__ import annotations

import hashlib
import os
import re
import socket
import ssl
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

from local_connector.packaging.key_store import (
    KeyStoreError,
    OsKeyStore,
    PrivateDirectoryRequired,
    request_private_directory,
)

__all__ = [
    "ALLOWED_PORTS",
    "HANDSHAKE_TIMEOUT_SECONDS",
    "MAX_ALLOWLIST_ENTRIES",
    "DiagnosticDenied",
    "DiagnosticResult",
    "probe_mtls_handshake",
    "validate_allowlist",
]

MAX_ALLOWLIST_ENTRIES = 32
HANDSHAKE_TIMEOUT_SECONDS = 10.0
ALLOWED_PORTS = frozenset({443})
MINIMUM_TLS = "TLSv1.2"

_HOSTNAME = re.compile(r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}\Z")
_KEY_FILE = "device-private-key.pem"
_CERT_FILE = "client-certificate.pem"

_DENY_ALLOWLIST = "hostname is not on the explicit diagnostic allowlist"
_DENY_INPUT = "diagnostic input is invalid"
_DENY_PKI = "supplied CA or client certificate is not usable"
_DENY_CLIENT_CERT = "server did not present a valid client certificate"
_DENY_TLS = "TLS handshake failed"
_DENY_CLEANUP = "temporary key material could not be removed"
_DENY_PRIVATE_DIR = "temporary key directory is not owner-private"


class DiagnosticDenied(RuntimeError):
    """Fixed, sanitized denial; never carries a certificate, key or socket error text."""


@dataclass(frozen=True)
class DiagnosticResult:
    """Bounded, non-secret evidence of one completed handshake."""

    hostname: str
    port: int
    tls_version: str
    cipher: str
    server_certificate_sha256: str
    client_certificate_sha256: str


@dataclass(frozen=True)
class _TemporaryFile:
    path: Path
    data: bytes = field(repr=False)
    mode: int


def validate_allowlist(allowlist: object) -> tuple[str, ...]:
    """Normalize the explicit host list: canonical DNS names only, no wildcards."""

    if not isinstance(allowlist, (list, tuple, frozenset, set)) or not allowlist:
        raise DiagnosticDenied(_DENY_ALLOWLIST)
    entries: list[str] = []
    for item in allowlist:
        if not isinstance(item, str):
            raise DiagnosticDenied(_DENY_ALLOWLIST)
        name = item.strip().lower().rstrip(".")
        if _HOSTNAME.fullmatch(name) is None:
            raise DiagnosticDenied(_DENY_ALLOWLIST)
        if name not in entries:
            entries.append(name)
    if len(entries) > MAX_ALLOWLIST_ENTRIES:
        raise DiagnosticDenied(_DENY_ALLOWLIST)
    return tuple(entries)


def probe_mtls_handshake(
    *,
    allowlist: object,
    hostname: object,
    ca_pem: object,
    certificate_pem: object,
    key_store: object,
    private_dir: object,
    port: int = 443,
    timeout: float = HANDSHAKE_TIMEOUT_SECONDS,
    open_socket=socket.create_connection,
) -> DiagnosticResult:
    """Perform exactly one verified mutual-TLS handshake and close it.

    ``key_store`` is an :class:`OsKeyStore` whose ``load_private_key_pem`` returns
    the device key; nothing is read from disk except that OS lookup. ``open_socket``
    is the single injectable transport seam so tests can mock the OS boundary.
    """

    entries = validate_allowlist(allowlist)
    if not isinstance(hostname, str) or hostname.strip().lower().rstrip(".") not in entries:
        raise DiagnosticDenied(_DENY_ALLOWLIST)
    host = hostname.strip().lower().rstrip(".")
    if type(port) is not int or port not in ALLOWED_PORTS:
        raise DiagnosticDenied(_DENY_INPUT)
    if type(timeout) not in (int, float) or not 0 < timeout <= 30:
        raise DiagnosticDenied(_DENY_INPUT)
    if not isinstance(ca_pem, bytes) or not isinstance(certificate_pem, bytes) or not ca_pem or not certificate_pem:
        raise DiagnosticDenied(_DENY_INPUT)
    if not isinstance(key_store, OsKeyStore):
        raise DiagnosticDenied(_DENY_INPUT)
    directory = _private_directory(private_dir)

    context = _client_context(ca_pem)
    try:
        stored_certificate, private_key = key_store.load_pair()
        if stored_certificate != certificate_pem:
            raise DiagnosticDenied(_DENY_INPUT)
    except KeyStoreError:
        raise DiagnosticDenied(_DENY_INPUT) from None
    with _temporary_files(directory, certificate_pem, private_key) as (certificate_path, key_path):
        try:
            context.load_cert_chain(certfile=str(certificate_path), keyfile=str(key_path))
        except (ssl.SSLError, OSError):
            raise DiagnosticDenied(_DENY_PKI) from None
        return _handshake(context, host, port, timeout, open_socket, certificate_pem)


def _client_context(ca_pem: bytes) -> ssl.SSLContext:
    """Explicit-CA, hostname-verifying client context; no default roots, no key log."""

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.options |= ssl.OP_NO_COMPRESSION
    try:
        context.load_verify_locations(cadata=ca_pem.decode("ascii"))
    except (ssl.SSLError, ValueError, UnicodeDecodeError):
        raise DiagnosticDenied(_DENY_PKI) from None
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise DiagnosticDenied(_DENY_PKI)
    return context


def _prove_server_identity(peer: object, host: str) -> bool:
    """Second, explicit check that the validated certificate names ``host``.

    ``SSLContext`` already enforces hostname matching when ``check_hostname`` is
    on; this re-derives it from the validated dictionary so a loosened platform
    default cannot pass silently. IP literals never match DNS SANs here.
    """

    if not isinstance(peer, dict) or not isinstance(host, str) or not host:
        return False
    alternative = peer.get("subjectAltName")
    if not isinstance(alternative, tuple):
        return False
    for entry in alternative:
        if isinstance(entry, tuple) and len(entry) == 2 and entry[0] == "DNS" and isinstance(entry[1], str):
            if entry[1].lower().rstrip(".") == host:
                return True
    return False


def _private_directory(private_dir: object) -> Path:
    """Validate the caller's temporary dir; create it owner-only when absent."""

    if not isinstance(private_dir, (str, os.PathLike)):
        raise DiagnosticDenied(_DENY_INPUT)
    directory = Path(private_dir)
    if sys.platform == "win32":
        try:
            resolved = request_private_directory(str(directory))
        except PrivateDirectoryRequired:
            raise DiagnosticDenied(_DENY_PRIVATE_DIR) from None
        return Path(resolved)
    try:
        from local_connector.paths import _open_directory

        fd = _open_directory(directory.absolute())
        os.close(fd)
        info = directory.lstat()
    except (OSError, ValueError):
        raise DiagnosticDenied(_DENY_PRIVATE_DIR) from None
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise DiagnosticDenied(_DENY_PRIVATE_DIR)
    return directory


class _temporary_files:
    """Write PEM files owner-only, and unlink both on every exit path."""

    def __init__(self, directory: Path, certificate_pem: bytes, key_pem: bytes) -> None:
        if not isinstance(key_pem, bytes) or not key_pem:
            raise DiagnosticDenied(_DENY_INPUT)
        self._files = [
            _TemporaryFile(directory / _CERT_FILE, certificate_pem, 0o644),
            _TemporaryFile(directory / _KEY_FILE, key_pem, 0o600),
        ]
        self._created = []

    def __enter__(self) -> tuple[Path, Path]:
        certificate, key = self._files
        try:
            self._create(certificate)
            self._create(key)
        except BaseException:
            self._unlink_all(required=True)
            raise
        return certificate.path, key.path

    def __exit__(self, *exc_info) -> None:
        self._unlink_all(required=True)

    def _create(self, item: _TemporaryFile) -> None:
        if os.name != "posix":
            # A private directory alone cannot make a chmod-created Windows file safe.
            raise DiagnosticDenied(_DENY_PRIVATE_DIR)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(item.path, flags, item.mode)
        except OSError:
            raise DiagnosticDenied(_DENY_PRIVATE_DIR) from None
        try:
            self._created.append(item)
            written = 0
            while written < len(item.data):
                written += os.write(descriptor, item.data[written:])
            os.fsync(descriptor)
        except OSError:
            raise DiagnosticDenied(_DENY_INPUT) from None
        finally:
            os.close(descriptor)

    def _unlink_all(self, *, required: bool) -> None:
        leftovers = False
        for item in self._created:
            try:
                item.path.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                leftovers = True
                continue
            if item.path.exists():
                leftovers = True
        if required and leftovers:
            raise DiagnosticDenied(_DENY_CLEANUP)


def _handshake(context, host: str, port: int, timeout: float, open_socket, certificate_pem: bytes) -> DiagnosticResult:
    raw = None
    tls = None
    try:
        raw = open_socket((host, port), timeout)
        raw.settimeout(timeout)
        tls = context.wrap_socket(raw, server_hostname=host)
        raw = None  # ownership moved to the TLS socket
        version = tls.version()
        cipher = tls.cipher()
        peer = tls.getpeercert(binary_form=True)
        cache = tls.getpeercert()
        try:
            tls.unwrap()
        except (OSError, ValueError):
            pass
    except ssl.SSLCertVerificationError:
        raise DiagnosticDenied(_DENY_PKI) from None
    except (OSError, ValueError):
        raise DiagnosticDenied(_DENY_TLS) from None
    finally:
        for handle in (tls, raw):
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
    if not _strong_enough(version, cipher):
        raise DiagnosticDenied(_DENY_TLS)
    if not isinstance(peer, bytes) or not peer:
        raise DiagnosticDenied(_DENY_CLIENT_CERT)
    if not _prove_server_identity(cache, host):
        raise DiagnosticDenied(_DENY_PKI)
    return DiagnosticResult(
        hostname=host,
        port=port,
        tls_version=version,
        cipher=cipher[0],
        server_certificate_sha256=hashlib.sha256(peer).hexdigest(),
        client_certificate_sha256=hashlib.sha256(certificate_pem).hexdigest(),
    )


def _strong_enough(version: object, cipher: object) -> bool:
    """Accept TLS 1.3, or TLS 1.2 only with a >=256-bit negotiated cipher."""

    if isinstance(version, str) and version.startswith("TLSv1.3"):
        return isinstance(cipher, tuple) and bool(cipher) and isinstance(cipher[0], str)
    return (
        isinstance(version, str)
        and version.startswith("TLSv1.2")
        and isinstance(cipher, tuple)
        and len(cipher) >= 3
        and isinstance(cipher[0], str)
        and isinstance(cipher[2], int)
        and cipher[2] >= 256
    )
