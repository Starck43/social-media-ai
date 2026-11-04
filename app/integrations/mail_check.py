"""Bounded IMAP authentication check; no message reads, writes, SMTP or redirects."""

from __future__ import annotations

import imaplib
import ipaddress
import socket
import ssl
from dataclasses import dataclass

from app.core.outbound import OutboundRefusal, StaticPolicy, acquire_outbound_target
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
        # The seam resolved and validated all answers; connect to the validated IP.
        # SNI and certificate verification still use the fixed provider hostname.
        ip = ipaddress.ip_address(self._address)
        family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout)
            sock.connect((str(ip), 993))
        except BaseException:
            sock.close()
            raise
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
        # P14-T09: the shared egress seam owns DNS and address policy; the fixed
        # catalog host is this provider's whole static policy.
        try:
            target = acquire_outbound_target(host, 993, policy=StaticPolicy((host,)))
        except OutboundRefusal as refusal:
            if isinstance(refusal.__cause__, OSError):
                return CheckResult("error", "connection_failed")
            return CheckResult("error", "unsafe_address")
        client = _PinnedIMAP(target.host, target.addresses[0])
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
