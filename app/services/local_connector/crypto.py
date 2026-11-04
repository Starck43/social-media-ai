"""Explicit operator CA, short-lived certificates, no implicit deployment keys."""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .wire import ConnectorUnavailable


class ConnectorAuthority:
    def __init__(self, *, ca_pem: bytes, ca_key_pem: bytes, signing_key_pem: bytes, pairing_key: bytes, key_id: str):
        self.ca = x509.load_pem_x509_certificate(ca_pem)
        self.ca_key = serialization.load_pem_private_key(ca_key_pem, password=None)
        self.signer = serialization.load_pem_private_key(signing_key_pem, password=None)
        self.pairing_key = pairing_key
        self.key_id = key_id
        if not isinstance(self.ca_key, Ed25519PrivateKey) or not isinstance(self.signer, Ed25519PrivateKey):
            raise ConnectorUnavailable()
        if (
            len(pairing_key) < 32
            or self.ca.public_key().public_bytes_raw() != self.ca_key.public_key().public_bytes_raw()
        ):
            raise ConnectorUnavailable()
        if not self.ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            raise ConnectorUnavailable()
        self._current()

    def _current(self):
        now = datetime.now(timezone.utc)
        if not self.ca.not_valid_before_utc <= now < self.ca.not_valid_after_utc:
            raise ConnectorUnavailable()

    def verifier(self, pairing_id: str, secret: str) -> str:
        return hmac.new(self.pairing_key, (pairing_id + "\0" + secret).encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def csr(pem: str):
        try:
            if type(pem) is not str or len(pem.encode()) > 8192:
                raise ValueError
            csr = x509.load_pem_x509_csr(pem.encode("ascii"))
            if len(csr.subject) or len(csr.extensions):
                raise ValueError
            key = csr.public_key()
            if not csr.is_signature_valid or not isinstance(key, Ed25519PublicKey):
                raise ValueError
            fingerprint = hashlib.sha256(key.public_bytes_raw()).hexdigest()
            public_pem = key.public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            ).decode()
            return fingerprint, public_pem
        except Exception:
            raise ConnectorUnavailable() from None

    def issue(self, public_pem: str, *, tenant_id: int, owner_id: int, device_id: int) -> str:
        self._current()
        now = datetime.now(timezone.utc)
        key = serialization.load_pem_public_key(public_pem.encode())
        identity = f"urn:local-connector:{tenant_id}:{owner_id}:{device_id}"
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, identity)]))
            .issuer_name(self.ca.subject)
            .public_key(key)
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(seconds=5))
            .not_valid_after(min(now + timedelta(hours=1), self.ca.not_valid_after_utc))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=True)
            .sign(self.ca_key, algorithm=None)
        )
        return cert.public_bytes(serialization.Encoding.PEM).decode()

    def peer(self, scope: dict):
        """Only authenticated ASGI TLS identity; public headers are never inspected.

        The deployment adapter must provide the ASGI TLS extension after mTLS
        verification. Missing extension or TLS verification error denies admission.
        """
        try:
            self._current()
            tls = scope["extensions"]["tls"]
            if scope.get("scheme") != "wss" or tls.get("client_cert_error"):
                raise ValueError
            chain = tls["client_cert_chain"]
            if not isinstance(chain, (list, tuple)) or not 1 <= len(chain) <= 4:
                raise ValueError
            cert = x509.load_pem_x509_certificate(chain[0].encode())
            cert.verify_directly_issued_by(self.ca)
            now = datetime.now(timezone.utc)
            if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
                raise ValueError
            if (
                ExtendedKeyUsageOID.CLIENT_AUTH
                not in cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
            ):
                raise ValueError
            return hashlib.sha256(cert.public_key().public_bytes_raw()).hexdigest(), str(cert.serial_number)
        except Exception:
            raise ConnectorUnavailable() from None
