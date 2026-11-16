"""OS key-store adapter for connector device credentials; never stores a key in cleartext.

``keyring`` is the only OS key-store boundary, and this module never lets it choose
its own backend. ``PYTHON_KEYRING_BACKENDS``, ``keyringrc.cfg`` and priority
autodiscovery can all select the plaintext file backend, so the composition root
calls :func:`open_os_key_store`, which loads exactly one class from
:func:`approved_backends` for the running platform (macOS Keychain, Windows
Credential Manager, Linux Secret Service) and never asks ``keyring`` to pick.
Every operation fails closed with a fixed, sanitized reason if that backend is
missing, locked, misconfigured or raises: there is no plaintext fallback.

Credential material is validated with ``cryptography`` before it is accepted:
the certificate must parse as exactly one X.509 certificate, the PIN-less PEM key
must parse as one private key, and the two must be the same key pair. A malformed
or mismatched pair is refused at :class:`DeviceCredentials` construction, so it
can never reach the vault.

Storage layout
--------------
The bundle is stored as one or more vault items under the caller's
``service``/``username`` pair and a ``{service}:{salt}:{device_id}`` key:

* ``.probe`` - read-only canary that is never written;
* ``.meta``  - field sizes, digest and chunk count as short ASCII text;
* ``.0`` ... - vault-safe UTF-16 encoded chunks of the payload.

The single-Blob design does not fit Windows: ``CredRead``/``CredWrite`` keep
``CredentialBlob`` as a ``DWORD`` and the Credential Manager rejects blobs above
``CRED_MAX_CREDENTIAL_BLOB_SIZE`` (2560 bytes, 5*512). A realistic client
certificate plus PIN-less key exceeds that, so :func:`vault_chunk_bytes` returns
the platform ceiling minus margin and the payload is split into pieces that each
fit. A half-written set can never be read back as credentials: the ``.meta``
setter is the single commit point, it is written last, and it records sizes and a
SHA-256 digest that every read verifies. :meth:`OsKeyStore.remove` deletes the
chunks, then ``.meta``, and re-reads every item, so a failed delete leaves a
bundle that is provably inert (legacy ``.legacy`` items are deleted too). Encryption
is the backend's own (Keychain/DPAPI/Secret Service); the UTF-16 step only keeps
every unit inside the 2-byte alphabet these vaults accept.

What this module does not do: it never generates certificates, private keys or
CSRs (deployment identity is operator-supplied per Q-19 R-2), never writes a
secret into a log line, an exception message, a representation or a command
line, and never imports application, database or network code.

Known limitation: :meth:`OsKeyStore.probe` reads a canary that is never written.
A locked backend raises and denies, but a backend that reports "not found"
without asking the user (some Linux Secret Service configurations) can answer
the canary healthy while the real entry stays locked. ``probe`` is therefore an
availability pre-check, not proof that the store is unlocked; the real proof is a
successful :meth:`OsKeyStore.load` of an existing bundle.

Integration (coordinated separately, not part of this unit)
----------------------------------------------------------
* Enrollment (P18-T05/T06 scope) receives caller-supplied material and calls
  :meth:`OsKeyStore.store`.
* Startup calls :func:`open_os_key_store` and :meth:`OsKeyStore.probe` before
  offering pairing, so a missing or locked store denies early;
  :mod:`local_connector.packaging.tls_probe` reads the device entry back.
* On Windows the composition root installs :func:`request_private_directory`'s
  handler with :func:`local_connector.packaging.windows_fs.ensure_private_directory`.
"""

from __future__ import annotations

import hashlib
import importlib
import re
import sys
from dataclasses import dataclass, field
from typing import Callable

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed448, ed25519, rsa
from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes, PublicKeyTypes

__all__ = [
    "APPROVED_KEYRING_BACKENDS",
    "DEFAULT_SERVICE",
    "DEFAULT_USERNAME",
    "DeviceCredentials",
    "KeyStoreError",
    "OsKeyStore",
    "PrivateDirectoryRequired",
    "approved_backends",
    "open_os_key_store",
    "payload_limit",
    "request_private_directory",
    "vault_chunk_bytes",
]

MAX_SERVICE_CHARS = 128
MAX_DEVICE_ID_CHARS = 160
MAX_CERTIFICATE_PEM_BYTES = 64 * 1024
MAX_PRIVATE_KEY_PEM_BYTES = 16 * 1024
MAX_SALT_CHARS = 64
# Items per bundle: payload ceiling / smallest allowed chunk, plus '.meta'.
MAX_CREDENTIAL_ITEMS = 512

DEFAULT_SERVICE = "social-media-ai-connector"
DEFAULT_USERNAME = "device-credentials"

# Windows Credential Manager: CRED_MAX_CREDENTIAL_BLOB_SIZE is 5*512 bytes and
# ``CredentialBlobSize`` is a DWORD, so 2560 is the hard ceiling for one item.
WINDOWS_VAULT_LIMIT_BYTES = 2560
DEFAULT_VAULT_LIMIT_BYTES = 2048
_VAULT_MARGIN_BYTES = 64

SUFFIX_PROBE = ".probe"
SUFFIX_META = ".meta"
SUFFIX_LEGACY = ".legacy"

# Fully qualified keyring backend classes that keep material in the OS vault.
# ``keyring.backends.file`` (plaintext) and third-party plugins are deliberately
# absent: this mapping is the entire allowlist.
APPROVED_KEYRING_BACKENDS: dict[str, str] = {
    "darwin": "keyring.backends.macOS.Keyring",
    "win32": "keyring.backends.Windows.WinVaultKeyring",
    "linux": "keyring.backends.SecretService.Keyring",
}
_APPROVED_BACKEND_NAMES = frozenset(APPROVED_KEYRING_BACKENDS.values())
_APPROVED_MODULES = frozenset(name.rpartition(".")[0] for name in APPROVED_KEYRING_BACKENDS.values())
_APPROVED_CLASSES = frozenset(name.rpartition(".")[2] for name in APPROVED_KEYRING_BACKENDS.values())

# Fixed reasons double as public: callers match on them, so no backend text leaks.
REASON_MISSING = "operating-system key store is not available"
REASON_LOCKED = "operating-system key store is locked or its approval was refused"
REASON_REFUSED = "operating-system key store refused the request"
REASON_BACKEND = "an approved operating-system key store backend is required"
REASON_INPUT = "device credential input is invalid"
REASON_MISMATCH = "stored device credential does not match the expected value"

_SERVICE = re.compile(r"[a-z0-9][a-z0-9.-]{0,127}")
_DEVICE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}")
_SALT = re.compile(r"[A-Za-z0-9._-]{1,64}")
_FREE_TEXT = re.compile(r"[\x20-\x7e]{0,160}")
_NAME = re.compile(rb"[A-Z][A-Za-z]{1,20}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
# PKCS#8/PKCS#1/SEC1 PEM lines are base64 bounded by 64 or 76 columns. The
# backreference is deliberately not followed by a space: in a bytes pattern
# ``\1 `` parses as an octal escape.
_PEM_PRIVATE_KEY = re.compile(
    rb"-----BEGIN ([A-Z0-9]+ |)PRIVATE KEY-----\r?\n"
    rb"(?:[A-Za-z0-9+/=]{1,76}\r?\n)*"
    rb"-----END \1PRIVATE KEY-----\r?\n?"
)
_PEM_CERTIFICATE = re.compile(
    rb"-----BEGIN CERTIFICATE-----\r?\n(?:[A-Za-z0-9+/=]{1,76}\r?\n)*-----END CERTIFICATE-----\r?\n?"
)

_HEADER = b"connector-device-credentials/1"
_FIELDS = (b"DeviceId", b"Tenant", b"Owner", b"CertificatePem", b"PrivateKeyPem")


class KeyStoreError(RuntimeError):
    """Fixed, sanitized denial; never carries a secret, path or backend text."""


class PrivateDirectoryRequired(KeyStoreError):
    """The narrow Windows private-directory check was not installed."""


def approved_backends(platform: str | None = None) -> tuple[str, ...]:
    """Return the approved OS backend class for ``platform``; empty if unsupported."""

    name = sys.platform if platform is None else platform
    if name == "darwin":
        key = "darwin"
    elif name in ("win32", "cygwin"):
        key = "win32"
    elif name.startswith("linux"):
        key = "linux"
    else:
        key = ""
    backend = APPROVED_KEYRING_BACKENDS.get(key)
    return (backend,) if backend else ()


def _approved_class(name: object):
    """Import exactly one allowlisted class; any other name or shape denies."""

    if not isinstance(name, str) or name not in _APPROVED_BACKEND_NAMES:
        raise KeyStoreError(REASON_BACKEND)
    module_name, _, class_name = name.rpartition(".")
    try:
        module = importlib.import_module(module_name)
        candidate = getattr(module, class_name)
    except Exception:
        raise KeyStoreError(REASON_MISSING) from None
    if not isinstance(candidate, type) or candidate.__module__ != module_name or candidate.__name__ != class_name:
        raise KeyStoreError(REASON_BACKEND)
    return candidate


def open_os_key_store(
    device_id: str,
    salt: str,
    *,
    service: str = DEFAULT_SERVICE,
    username: str = DEFAULT_USERNAME,
    platform: str | None = None,
) -> "OsKeyStore":
    """Build the key store on this platform's approved backend; never auto-discovers.

    The backend class is taken from :func:`approved_backends` only, imported by
    its exact allowlisted name, instantiated with no arguments and accepted only
    if its own ``__module__``/``__name__`` still match. ``keyring.get_keyring``
    is never called, so ``PYTHON_KEYRING_BACKENDS``, ``keyringrc.cfg`` and
    plugin priority cannot redirect material into the plaintext file backend.
    """

    name = sys.platform if platform is None else platform
    names = approved_backends(name)
    if not names:
        raise KeyStoreError(REASON_BACKEND)
    backend = _approved_class(names[0])()
    return OsKeyStore(backend, device_id=device_id, salt=salt, service=service, username=username)


def request_private_directory(path: str) -> str:
    """Return ``path`` only after an installed owner-private Windows check passes.

    Python's SSL engine accepts a PEM client key only as a file, so the TLS
    adapter writes that file itself. POSIX pins the directory and creates the
    file with ``O_EXCL`` mode ``0600``. Windows has no equivalent mode and this
    module will not guess: the parent installs a narrow callback at
    ``request_private_directory.handler`` backed by
    :func:`local_connector.packaging.windows_fs.ensure_private_directory`.
    Without it the request is refused instead of writing key material.
    """

    handler = getattr(request_private_directory, "handler", None)
    if not callable(handler):
        raise PrivateDirectoryRequired(REASON_MISSING)
    accepted = handler(path)
    if not isinstance(accepted, str) or accepted != path:
        raise PrivateDirectoryRequired(REASON_INPUT)
    return accepted


def _check(pattern: re.Pattern | None, value: object, limit: int) -> None:
    if not isinstance(value, str) or len(value) > limit:
        raise KeyStoreError(REASON_INPUT)
    if pattern is not None and pattern.fullmatch(value) is None:
        raise KeyStoreError(REASON_INPUT)


def _public_key(key: object) -> PublicKeyTypes:
    if isinstance(key, (rsa.RSAPrivateKey, dsa.DSAPrivateKey, ec.EllipticCurvePrivateKey)):
        return key.public_key()
    if isinstance(key, (ed25519.Ed25519PrivateKey, ed448.Ed448PrivateKey)):
        return key.public_key()
    raise KeyStoreError(REASON_INPUT)


def _same_key_pair(certificate: x509.Certificate, private_key: PrivateKeyTypes) -> bool:
    """True only when the certificate's public key is this private key's public key."""

    try:
        return certificate.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        ) == _public_key(private_key).public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    except (ValueError, TypeError, KeyStoreError):
        return False


def _check_certificate_pem(value: object) -> x509.Certificate:
    if not isinstance(value, bytes) or not 1 <= len(value) <= MAX_CERTIFICATE_PEM_BYTES:
        raise KeyStoreError(REASON_INPUT)
    if _PEM_CERTIFICATE.fullmatch(value) is None:
        raise KeyStoreError(REASON_INPUT)
    try:
        return x509.load_pem_x509_certificate(value)
    except Exception:
        raise KeyStoreError(REASON_INPUT) from None


def _check_private_key_pem(value: object) -> PrivateKeyTypes:
    if not isinstance(value, bytes) or not 1 <= len(value) <= MAX_PRIVATE_KEY_PEM_BYTES:
        raise KeyStoreError(REASON_INPUT)
    if _PEM_PRIVATE_KEY.fullmatch(value) is None:
        raise KeyStoreError(REASON_INPUT)
    try:
        # No password argument: a PIN-less key is required, encrypted PEM stays refused.
        return serialization.load_pem_private_key(value, password=None)
    except Exception:
        raise KeyStoreError(REASON_INPUT) from None


def payload_limit() -> int:
    return MAX_CERTIFICATE_PEM_BYTES + MAX_PRIVATE_KEY_PEM_BYTES + 4096


def vault_chunk_bytes(platform: str | None = None) -> int:
    """Usable bytes of one vault item on ``platform`` after a safety margin."""

    name = sys.platform if platform is None else platform
    limit = WINDOWS_VAULT_LIMIT_BYTES if name in ("win32", "cygwin") else DEFAULT_VAULT_LIMIT_BYTES
    # The Windows backend stores strings as UTF-16, not UTF-8 bytes.
    return (limit - _VAULT_MARGIN_BYTES) // 2


@dataclass(frozen=True)
class DeviceCredentials:
    """Caller-supplied enrollment material for exactly one device identity.

    Stored verbatim: no CA, key or certificate is derived, renewed or generated
    here. ``private_key_pem`` must be an unencrypted PEM private key supplied by
    the operator's enrollment flow, and it must be the key of ``certificate_pem``.
    Both byte fields are redacted from ``repr`` so a traceback or a log line that
    formats the object cannot copy the key.
    """

    device_id: str
    certificate_pem: bytes = field(repr=False)
    private_key_pem: bytes = field(repr=False)
    tenant: str = ""
    owner: str = ""

    def __post_init__(self) -> None:
        _check(_DEVICE_ID, self.device_id, MAX_DEVICE_ID_CHARS)
        _check(_FREE_TEXT, self.tenant, MAX_DEVICE_ID_CHARS)
        _check(_FREE_TEXT, self.owner, MAX_DEVICE_ID_CHARS)
        certificate = _check_certificate_pem(self.certificate_pem)
        private_key = _check_private_key_pem(self.private_key_pem)
        if not _same_key_pair(certificate, private_key):
            raise KeyStoreError(REASON_INPUT)

    def __repr__(self) -> str:
        return (
            f"DeviceCredentials(device_id={self.device_id!r}, tenant={self.tenant!r}, "
            f"owner={self.owner!r}, certificate_pem=<redacted>, private_key_pem=<redacted>)"
        )

    def to_payload(self) -> bytes:
        """Deterministic bytes for the vault: named, length-prefixed fields."""

        values = {
            b"DeviceId": self.device_id.encode("ascii"),
            b"Tenant": self.tenant.encode("utf-8"),
            b"Owner": self.owner.encode("utf-8"),
            b"CertificatePem": self.certificate_pem,
            b"PrivateKeyPem": self.private_key_pem,
        }
        lines = [_HEADER]
        for name in _FIELDS:
            value = values[name]
            lines.append(name + b" " + str(len(value)).encode("ascii"))
            lines.append(value)
        payload = b"\n".join(lines)
        if len(payload) > payload_limit():
            raise KeyStoreError(REASON_INPUT)
        return payload

    @classmethod
    def from_payload(cls, payload: object) -> "DeviceCredentials":
        raw = payload.encode("utf-8") if isinstance(payload, str) else payload
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= payload_limit():
            raise KeyStoreError(REASON_REFUSED)
        head, sep, rest = raw.partition(b"\n")
        if not sep or head != _HEADER:
            raise KeyStoreError(REASON_REFUSED)
        values: dict[bytes, bytes] = {}
        for _ in _FIELDS:
            line, sep, rest = rest.partition(b"\n")
            name, space, length = line.partition(b" ")
            if not sep or not space or _NAME.fullmatch(name) is None or name in values:
                raise KeyStoreError(REASON_REFUSED)
            size = _size(length)
            if size > len(rest):
                raise KeyStoreError(REASON_REFUSED)
            # PEM fields end in their own newline, so the separator after a field
            # is already part of it and only a following empty line may exist.
            values[name] = rest[:size]
            rest = rest[size:]
            if rest.startswith(b"\n"):
                rest = rest[1:]
        if rest or tuple(values) != _FIELDS:
            raise KeyStoreError(REASON_REFUSED)
        try:
            return cls(
                device_id=values[b"DeviceId"].decode("ascii"),
                certificate_pem=values[b"CertificatePem"],
                private_key_pem=values[b"PrivateKeyPem"],
                tenant=values[b"Tenant"].decode("utf-8"),
                owner=values[b"Owner"].decode("utf-8"),
            )
        except (UnicodeDecodeError, KeyStoreError):
            raise KeyStoreError(REASON_REFUSED) from None


def _size(length: bytes) -> int:
    if not length.isdigit() or not 1 <= len(length) <= 6:
        raise KeyStoreError(REASON_REFUSED)
    return int(length)


def _split(text: str, size: int) -> list[str]:
    """Split a UTF-16 string on code-unit boundaries; no surrogate is halved."""

    if size <= 0:
        raise KeyStoreError(REASON_BACKEND)
    pieces: list[str] = []
    offset = 0
    while offset < len(text):
        end = min(offset + size, len(text))
        if end < len(text) and "\ud800" <= text[end - 1] <= "\udbff":
            end -= 1
        pieces.append(text[offset:end])
        offset = end
    return pieces


class OsKeyStore:
    """Device credentials in one explicitly approved OS key-store backend.

    The item key is ``{service}:{salt}:{device_id}`` under the caller's
    ``service``/``username`` pair, so distinct devices and disposable test runs
    never collide; the salt is caller-owned and short-lived. A bundle occupies
    ``.meta`` plus ``.0..N`` items and a never-written ``.probe`` canary.
    """

    def __init__(
        self,
        backend,
        *,
        device_id: str,
        salt: str,
        service: str = DEFAULT_SERVICE,
        username: str = DEFAULT_USERNAME,
    ) -> None:
        backend_type = type(backend)
        if f"{backend_type.__module__}.{backend_type.__name__}" not in _APPROVED_BACKEND_NAMES:
            raise KeyStoreError(REASON_BACKEND)
        _check(_SERVICE, service, MAX_SERVICE_CHARS)
        _check(_SERVICE, username, MAX_SERVICE_CHARS)
        _check(_DEVICE_ID, device_id, MAX_DEVICE_ID_CHARS)
        _check(_SALT, salt, MAX_SALT_CHARS)
        self._backend = backend
        self._service = service
        self._username = username
        self._item = f"{service}:{salt}:{device_id}"
        self._device_id = device_id

    @property
    def backend_name(self) -> str:
        """Approved class name for evidence lines; never key material."""

        return type(self._backend).__name__

    @property
    def vault_chunk_bytes(self) -> int:
        """Largest accepted text of one item; small enough for the Windows Blob limit."""

        return vault_chunk_bytes()

    def probe(self) -> bool:
        """Read a canary that was never written: only an absent item is healthy.

        A locked, misconfigured or plaintext backend raises, and any value means
        something else already owns this namespace; both deny. A backend that
        answers "not found" without unlocking can still pass this pre-check, so a
        successful ``probe`` is not proof that the store is unlocked.
        """

        value = self._call(self._backend.get_password, self._service, self._item + SUFFIX_PROBE)
        if value is not None:
            raise KeyStoreError(REASON_REFUSED)
        return True

    def store(self, credentials: DeviceCredentials) -> None:
        """Write one credential bundle; an existing bundle is never overwritten.

        Chunks are written first and ``.meta`` last, so an interrupted write
        leaves data that no read accepts. A failed write removes what it created.
        """

        if type(credentials) is not DeviceCredentials or credentials.device_id != self._device_id:
            raise KeyStoreError(REASON_INPUT)
        payload = credentials.to_payload().decode("utf-8")
        chunk_size = self.vault_chunk_bytes
        pieces = _split(payload, chunk_size)
        if not pieces or len(pieces) + 2 > MAX_CREDENTIAL_ITEMS:
            raise KeyStoreError(REASON_INPUT)
        if self._read_optional(self._item + SUFFIX_LEGACY) is not None or self._meta() is not None:
            raise KeyStoreError(REASON_REFUSED)
        if any(self._read_optional(f"{self._item}.{i}") is not None for i in range(MAX_CREDENTIAL_ITEMS - 2)):
            raise KeyStoreError(REASON_REFUSED)  # Never overwrite an interrupted enrollment.
        written: list[str] = []
        committed = False
        try:
            for index, piece in enumerate(pieces):
                name = f"{self._item}.{index}"
                written.append(name)
                self._call(self._backend.set_password, self._service, name, piece)
            meta = self._meta_text(pieces)
            committed = True
            self._call(self._backend.set_password, self._service, self._item + SUFFIX_META, meta)
        except KeyStoreError:
            self._discard(written, remove_meta=committed)
            raise
        except BaseException:
            self._discard(written, remove_meta=committed)
            raise
        if not self.is_present(expected=credentials):
            # Never leave a bundle that cannot be read back exactly.
            self._discard(written, remove_meta=True)
            raise KeyStoreError(REASON_REFUSED)

    def load(self) -> DeviceCredentials:
        """Read back the bundle; a missing or malformed entry fails closed."""

        try:
            payload = self._bundle_payload(self._meta())
            credentials = DeviceCredentials.from_payload(payload)
            if credentials.device_id != self._device_id:
                raise KeyStoreError(REASON_MISMATCH)
            return credentials
        except KeyStoreError:
            raise KeyStoreError(REASON_REFUSED) from None

    def load_pair(self) -> tuple[bytes, bytes]:
        """The certificate and the private key from the same stored bundle.

        Both come from one entry, so a caller can never attach a certificate and a
        key that were enrolled for different devices.
        """

        credentials = self.load()
        return credentials.certificate_pem, credentials.private_key_pem

    def load_private_key_pem(self) -> bytes:
        return self.load_pair()[1]

    def is_present(self, *, expected: DeviceCredentials | None = None) -> bool:
        """Existence check; ``expected`` additionally proves exact content."""

        meta = self._meta()
        if meta is None:
            return False
        if expected is None:
            return True
        if type(expected) is not DeviceCredentials:
            raise KeyStoreError(REASON_INPUT)
        actual = self._bundle_payload(meta)
        if actual != expected.to_payload().decode("utf-8"):
            raise KeyStoreError(REASON_MISMATCH)
        return True

    def remove(self) -> bool:
        """Delete the bundle, then re-read every item to prove removal.

        ``.meta`` is the commit point and goes last. If a delete fails, the
        remaining bundle still lacks ``.meta`` and is therefore unreadable; a
        locked store denies instead of reporting success.
        """

        meta = self._meta()
        legacy = self._read_optional(self._item + SUFFIX_LEGACY) is not None
        chunks = [
            f"{self._item}.{i}"
            for i in range(MAX_CREDENTIAL_ITEMS - 2)
            if self._read_optional(f"{self._item}.{i}") is not None
        ]
        if meta is None and not legacy and not chunks:
            return False
        self._delete(self._item + SUFFIX_META, ignore_missing=True)
        for name in chunks:
            self._delete(name)
        if legacy:
            self._delete(self._item + SUFFIX_LEGACY)
        if self._read_optional(self._item + SUFFIX_META) is not None:
            raise KeyStoreError(REASON_REFUSED)
        for name in chunks:
            if self._read_optional(name) is not None:
                raise KeyStoreError(REASON_REFUSED)
        return True

    # ------------------------------------------------------------------ internals

    def _delete(self, name: str, *, ignore_missing: bool = False) -> None:
        try:
            self._call(self._backend.delete_password, self._service, name)
        except KeyStoreError as exc:
            if ignore_missing and str(exc) == REASON_LOCKED and self._read_optional(name) is None:
                return
            raise

    def _discard(self, written: list[str], *, remove_meta: bool) -> None:
        """Remove only owned writes and verify removal, including partial OS errors."""
        names = ([self._item + SUFFIX_META] if remove_meta else []) + written
        for name in names:
            if self._read_optional(name) is not None:
                self._call(self._backend.delete_password, self._service, name)
        if any(self._read_optional(name) is not None for name in names):
            raise KeyStoreError(REASON_REFUSED)

    def _meta_text(self, pieces: list[str]) -> str:
        digest = hashlib.sha256("".join(pieces).encode("utf-8")).hexdigest()
        sizes = ",".join(str(len(piece)) for piece in pieces)
        return f"v1;{len(pieces)};{digest};{sizes}"

    def _meta(self) -> tuple[int, str, list[int]] | None:
        raw = self._read_optional(self._item + SUFFIX_META)
        if raw is None:
            return None
        version, _, rest = raw.partition(";")
        count, _, rest = rest.partition(";")
        digest, _, rest = rest.partition(";")
        if version != "v1" or not count.isdigit() or not 1 <= len(count) <= 4 or _DIGEST.fullmatch(digest) is None:
            raise KeyStoreError(REASON_REFUSED)
        total = int(count)
        if not 1 <= total <= MAX_CREDENTIAL_ITEMS - 2:
            raise KeyStoreError(REASON_REFUSED)
        sizes = rest.split(",") if rest else []
        if len(sizes) != total:
            raise KeyStoreError(REASON_REFUSED)
        try:
            numbers = [int(size) for size in sizes]
        except ValueError:
            raise KeyStoreError(REASON_REFUSED) from None
        if any(not 0 <= size <= self.vault_chunk_bytes for size in numbers) or any(size == 0 for size in numbers):
            raise KeyStoreError(REASON_REFUSED)
        return total, digest, numbers

    def _chunk_names(self, meta: tuple[int, str, list[int]]) -> list[str]:
        return [f"{self._item}.{index}" for index in range(meta[0])]

    def _bundle_payload(self, meta: tuple[int, str, list[int]] | None) -> str:
        if meta is None:
            raise KeyStoreError(REASON_MISSING)
        total, digest, sizes = meta
        pieces: list[str] = []
        for index in range(total):
            value = self._read_optional(f"{self._item}.{index}")
            if value is None:
                raise KeyStoreError(REASON_REFUSED)
            if len(value) != sizes[index]:
                raise KeyStoreError(REASON_REFUSED)
            pieces.append(value)
        payload = "".join(pieces)
        if len(payload) > payload_limit() or hashlib.sha256(payload.encode("utf-8")).hexdigest() != digest:
            raise KeyStoreError(REASON_REFUSED)
        return payload

    def _read_optional(self, name: str) -> str | None:
        value = self._call(self._backend.get_password, self._service, name)
        if value is None:
            return None
        if not isinstance(value, str) or not value or len(value) > self.vault_chunk_bytes:
            raise KeyStoreError(REASON_REFUSED)
        return value

    @staticmethod
    def _call(method: Callable, *args):
        try:
            return method(*args)
        except Exception:
            # Backend exception text can embed service names or keychain paths.
            raise KeyStoreError(REASON_LOCKED) from None
