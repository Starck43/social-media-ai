"""Pinned TLS sockets: P14-T09 authorizes DNS; connect only to its selected IP.

No proxy, redirect, fallback address, retry or second hostname lookup. TLS SNI
and certificate verification continue to use the original provider hostname.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl

from app.core.outbound import OutboundTarget

TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 65536


def open_tls_socket(target: OutboundTarget, timeout: float, context: ssl.SSLContext) -> ssl.SSLSocket:
    address = ipaddress.ip_address(target.addresses[0])
    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect((str(address), target.port))
        return context.wrap_socket(sock, server_hostname=target.host)
    except BaseException:
        sock.close()
        raise


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, target: OutboundTarget):
        self._target = target
        super().__init__(target.host, target.port, timeout=TIMEOUT_SECONDS, context=ssl.create_default_context())

    def connect(self):
        self.sock = open_tls_socket(self._target, self.timeout, self._context)
