"""Network boundary tests: never contact a real provider."""

import imaplib
import socket
import ssl

import pytest

from app.integrations import mail_check

__source_only__ = True


def answer(address):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 993))]


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1", "192.168.1.2"]
)
def test_private_dns_answers_never_connect(monkeypatch, address):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: answer(address))
    monkeypatch.setattr(mail_check, "_PinnedIMAP", lambda *_: pytest.fail("must not connect"))
    assert mail_check.check_mail("mailru_mail", "a@example.com", "secret").code == "unsafe_address"


@pytest.mark.parametrize(
    "failure,code,state",
    [
        (imaplib.IMAP4.error("secret in remote text"), "authentication_failed", "reauth"),
        (TimeoutError("secret"), "connection_failed", "error"),
        (ssl.SSLError("secret"), "tls_failed", "error"),
        (imaplib.IMAP4.abort("secret"), "service_unavailable", "error"),
        (None, "imap_authenticated", "verified"),
    ],
)
def test_only_login_and_shutdown_no_mail_operations(monkeypatch, failure, code, state, caplog):
    calls = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: answer("8.8.8.8"))

    class FakeIMAP:
        def __init__(self, host, address):
            calls.append((host, address))

        def login(self, login, password):
            calls.append("login")
            if failure:
                raise failure

        def shutdown(self):
            calls.append("close")

    monkeypatch.setattr(mail_check, "_PinnedIMAP", FakeIMAP)
    result = mail_check.check_mail("yandex_mail", "a@example.com", "secret")
    assert (result.code, result.state) == (code, state)
    assert calls == [("imap.yandex.ru", "8.8.8.8"), "login", "close"]
    assert "secret" not in repr(result) + caplog.text


def test_unknown_provider_and_mixed_dns_fail_closed(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: answer("8.8.8.8") + answer("10.1.1.1"))
    assert mail_check.check_mail("unknown", "a", "s").code == "unsupported_provider"
    assert mail_check.check_mail("mailru_mail", "a", "s").code == "unsafe_address"


def test_tls_uses_fixed_hostname_and_validated_ip(monkeypatch):
    calls = []

    class TLS:
        def wrap_socket(self, sock, *, server_hostname):
            calls.append(server_hostname)
            return "tls-socket"

    instance = object.__new__(mail_check._PinnedIMAP)
    instance.host, instance._address, instance.ssl_context = "imap.mail.ru", "8.8.8.8", TLS()
    monkeypatch.setattr(
        socket, "create_connection", lambda address, timeout: calls.append((address, timeout)) or object()
    )
    assert instance._create_socket(8) == "tls-socket"
    assert calls == [(("8.8.8.8", 993), 8), "imap.mail.ru"]
