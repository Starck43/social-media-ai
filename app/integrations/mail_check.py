"""Bounded IMAP authentication check; no message reads, writes, SMTP or redirects."""

from __future__ import annotations

import imaplib
import ipaddress
import socket
import ssl
from dataclasses import dataclass

from app.integrations.catalog import PROVIDERS, WORKSPACE_PROVIDER_IDS

SOCKET_TIMEOUT = 8


@dataclass(frozen=True)
class CheckResult:
    state: str
    code: str


class _PinnedIMAP(imaplib.IMAP4_SSL):
    def __init__(self, host: str, address: str):
        self._address = address
        super().__init__(host, 993, ssl_context=ssl.create_default_context(), timeout=SOCKET_TIMEOUT)
        self.debug = 0

    def _create_socket(self, timeout):
        # Resolve once, validate all answers, and connect to the validated IP.
        # SNI and certificate verification still use the fixed provider hostname.
        sock = socket.create_connection((self._address, 993), timeout)
        try:
            return self.ssl_context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def check_mail(provider_id: str, login: str, password: str) -> CheckResult:
    """Only fixed catalog hosts are reachable; exception text never escapes."""
    if provider_id not in WORKSPACE_PROVIDER_IDS:
        return CheckResult("error", "unsupported_provider")
    host = dict(PROVIDERS[provider_id].defaults)["imap_host"]
    client = None
    try:
        answers = socket.getaddrinfo(host, 993, type=socket.SOCK_STREAM)
        addresses = [answer[4][0] for answer in answers]
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            return CheckResult("error", "unsafe_address")
        client = _PinnedIMAP(host, addresses[0])
        # Yandex documents the local part as the login for @yandex.ru.
        auth_login = (
            login.split("@", 1)[0] if provider_id == "yandex_mail" and login.lower().endswith("@yandex.ru") else login
        )
        client.login(auth_login, password)
        return CheckResult("verified", "imap_authenticated")
    except ssl.SSLError:
        return CheckResult("error", "tls_failed")
    except imaplib.IMAP4.abort:
        return CheckResult("error", "service_unavailable")
    except imaplib.IMAP4.error:
        return CheckResult("reauth", "authentication_failed")
    except (OSError, ValueError, UnicodeError):
        return CheckResult("error", "connection_failed")
    finally:
        if client is not None:
            # Close the socket locally: no mailbox commands, no unbounded logout.
            try:
                client.shutdown()
            except OSError:
                pass
