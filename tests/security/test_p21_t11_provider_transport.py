"""Exercise the actual HTTPcore/HTTPX pinning bridge without sockets or DNS."""

import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import httpx

from tests.shared.credential_sources import egress, outbound


class Stream:
    def __init__(self):
        self.hostname = None
        self.writes = []
        self.data = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}"

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.hostname = server_hostname
        return self

    async def read(self, max_bytes, timeout=None):
        result, self.data = self.data, b""
        return result

    async def write(self, buffer, timeout=None):
        self.writes.append(buffer)

    async def aclose(self):
        pass

    def get_extra_info(self, name):
        return False if name == "is_readable" else None


class ProviderTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_numeric_tcp_preserves_original_host_and_verified_tls_sni(self):
        stream = Stream()
        backend = NS(connect_tcp=AsyncMock(return_value=stream))
        transport = egress.ProviderTransport()
        transport._pool._network_backend = egress._PinnedBackend(backend)
        with (
            patch.object(
                egress,
                "acquire_outbound_target",
                return_value=outbound.OutboundTarget("example.org", 443, ("8.8.8.8",)),
            ),
            patch.dict(egress.os.environ, {"HTTPS_PROXY": "http://127.0.0.1:3128"}),
        ):
            async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
                response = await client.post(
                    "https://example.org/v1/chat/completions", headers={"Authorization": "Bearer synthetic"}, json={}
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(backend.connect_tcp.call_args.args[:2], ("8.8.8.8", 443))
        self.assertEqual(stream.hostname, "example.org")
        sent = b"".join(stream.writes).lower()
        self.assertIn(b"host: example.org", sent)
        self.assertIn(b"authorization: bearer synthetic", sent)
        self.assertNotIn(b"127.0.0.1", sent)

    def test_tls_key_logging_fails_closed(self):
        with (
            patch.dict(egress.os.environ, {"SSLKEYLOGFILE": "synthetic-disallowed-path"}),
            self.assertRaises(ValueError),
        ):
            egress.ProviderTransport()


if __name__ == "__main__":
    unittest.main()
