"""TLS connection checks using fake sockets and streams only."""

import asyncio
import socket
import ssl

import pytest

from app.integrations.file_workspace import yandex_tls
from app.integrations.file_workspace.yandex_network import NetworkUnavailable

__source_only__ = True


class FakeSocket:
    def __init__(self, family, kind):
        self.family, self.kind = family, kind
        self.blocking = None
        self.closed = False

    def setblocking(self, blocking):
        self.blocking = blocking

    def close(self):
        self.closed = True


class FakeLoop:
    def __init__(self):
        self.calls = []

    async def sock_connect(self, sock, address):
        self.calls.append((sock, address))


@pytest.mark.asyncio
@pytest.mark.parametrize("ip,family", [("8.8.8.8", socket.AF_INET), ("2606:4700:4700::1111", socket.AF_INET6)])
async def test_pinned_ip_and_verified_original_hostname(ip, family):
    sockets, calls = [], []
    loop = FakeLoop()
    reader, writer = object(), object()

    def make_socket(family, kind):
        sock = FakeSocket(family, kind)
        sockets.append(sock)
        return sock

    async def open_connection(**kwargs):
        calls.append(kwargs)
        return reader, writer

    result = await yandex_tls._open_tls_to_ip(
        ip, 443, _socket_factory=make_socket, _loop=loop, _open_connection=open_connection
    )
    assert result == (reader, writer)
    sock = sockets[0]
    assert (sock.family, sock.kind, sock.blocking, sock.closed) == (family, socket.SOCK_STREAM, False, False)
    assert loop.calls == [(sock, (ip, 443))]
    assert calls[0]["sock"] is sock
    assert calls[0]["server_hostname"] == "cloud-api.yandex.net"
    assert calls[0]["ssl_handshake_timeout"] == 8.0
    assert calls[0]["ssl"].check_hostname is True
    assert calls[0]["ssl"].verify_mode == ssl.CERT_REQUIRED
    assert calls[0]["ssl"].minimum_version == ssl.TLSVersion.TLSv1_2
    assert calls[0]["ssl"].get_ca_certs()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ip,port",
    [
        ("127.0.0.1", 443),
        ("169.254.169.254", 443),
        ("8.8.8.8", 80),
        ("cloud-api.yandex.net", 443),
        ("::ffff:8.8.8.8", 443),
        ("ff02::1", 443),
    ],
)
async def test_unsafe_destination_never_creates_socket(ip, port):
    def unexpected(*args):
        raise AssertionError("socket must not be created")

    with pytest.raises(NetworkUnavailable, match="^network_unavailable$"):
        await yandex_tls._open_tls_to_ip(ip, port, _socket_factory=unexpected)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["connect", "handshake", "cancel"])
async def test_socket_closed_on_connection_handshake_failure_or_cancellation(stage):
    sockets = []

    def make_socket(family, kind):
        sock = FakeSocket(family, kind)
        sockets.append(sock)
        return sock

    class Loop(FakeLoop):
        async def sock_connect(self, sock, address):
            if stage == "connect":
                raise OSError("network failure")

    async def open_connection(**kwargs):
        if stage == "cancel":
            raise asyncio.CancelledError()
        raise ssl.SSLError("handshake failure")

    expected = asyncio.CancelledError if stage == "cancel" else OSError
    with pytest.raises(expected):
        await yandex_tls._open_tls_to_ip(
            "8.8.8.8", 443, _socket_factory=make_socket, _loop=Loop(), _open_connection=open_connection
        )
    assert sockets[0].closed


@pytest.mark.asyncio
async def test_unverified_tls_context_is_rejected_before_socket_creation():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    def unexpected(*args):
        raise AssertionError("socket must not be created")

    with pytest.raises(NetworkUnavailable, match="^network_unavailable$"):
        await yandex_tls._open_tls_to_ip("8.8.8.8", 443, _socket_factory=unexpected, _context_factory=lambda: context)


@pytest.mark.asyncio
async def test_entry_point_uses_pinning_and_propagates_sanitized_failure(monkeypatch):
    async def pin(connector):
        assert connector is yandex_tls._open_tls_to_ip
        raise NetworkUnavailable("network_unavailable")

    monkeypatch.setattr(yandex_tls, "_connect_pinned", pin)
    with pytest.raises(NetworkUnavailable, match="^network_unavailable$"):
        await yandex_tls.open_yandex_tls()


def test_tls_context_ignores_ca_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("SSLKEYLOGFILE", raising=False)
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "not-a-ca.pem"))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path / "not-a-directory"))
    context = yandex_tls._tls_context()
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.keylog_filename is None
    assert context.get_ca_certs()


def test_tls_context_rejects_keylog_environment_before_context_creation(monkeypatch, tmp_path):
    path = tmp_path / "must-not-be-written"
    monkeypatch.setenv("SSLKEYLOGFILE", str(path))
    with pytest.raises(NetworkUnavailable, match="^network_unavailable$"):
        yandex_tls._tls_context()
    assert not path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "certificate_name,trusted,success",
    [("cloud-api.yandex.net", True, True), ("wrong.invalid", True, False), ("cloud-api.yandex.net", False, False)],
)
async def test_in_memory_tls_verifies_hostname(tmp_path, monkeypatch, certificate_name, trusted, success):
    """Use actual OpenSSL handshake and an ephemeral test CA, no sockets."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    monkeypatch.delenv("SSLKEYLOGFILE", raising=False)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, certificate_name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(certificate_name)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if trusted:
        client_context.load_verify_locations(cafile=str(cert_path))
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(str(cert_path), str(key_path))
    sockets = []

    def make_socket(family, kind):
        sock = FakeSocket(family, kind)
        sockets.append(sock)
        return sock

    async def handshake(**kwargs):
        ci, co, si, so = (ssl.MemoryBIO() for _ in range(4))
        client = kwargs["ssl"].wrap_bio(ci, co, server_side=False, server_hostname=kwargs["server_hostname"])
        server = server_context.wrap_bio(si, so, server_side=True)
        complete = [False, False]
        for _ in range(20):
            for i, endpoint in enumerate((client, server)):
                if not complete[i]:
                    try:
                        endpoint.do_handshake()
                        complete[i] = True
                    except ssl.SSLWantReadError:
                        pass
            if co.pending:
                si.write(co.read())
            if so.pending:
                ci.write(so.read())
            if all(complete):
                return "reader", "writer"
        raise AssertionError("handshake did not finish")

    kwargs = dict(
        _socket_factory=make_socket,
        _loop=FakeLoop(),
        _open_connection=handshake,
        _context_factory=lambda: client_context,
    )
    if success:
        assert await yandex_tls._open_tls_to_ip("8.8.8.8", 443, **kwargs) == ("reader", "writer")
        assert not sockets[0].closed
    else:
        with pytest.raises(ssl.SSLCertVerificationError):
            await yandex_tls._open_tls_to_ip("8.8.8.8", 443, **kwargs)
        assert sockets[0].closed


@pytest.mark.asyncio
async def test_keylog_setting_added_during_connect_closes_socket(monkeypatch, tmp_path):
    monkeypatch.delenv("SSLKEYLOGFILE", raising=False)
    sockets = []

    def make_socket(family, kind):
        sock = FakeSocket(family, kind)
        sockets.append(sock)
        return sock

    class Loop(FakeLoop):
        async def sock_connect(self, sock, address):
            monkeypatch.setenv("SSLKEYLOGFILE", str(tmp_path / "keylog"))

    async def unexpected(**kwargs):
        raise AssertionError("handshake must not start")

    with pytest.raises(NetworkUnavailable, match="^network_unavailable$"):
        await yandex_tls._open_tls_to_ip(
            "8.8.8.8", 443, _socket_factory=make_socket, _loop=Loop(), _open_connection=unexpected
        )
    assert sockets[0].closed
    assert not (tmp_path / "keylog").exists()
