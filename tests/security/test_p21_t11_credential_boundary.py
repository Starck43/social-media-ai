"""Source-isolated synthetic custody and pinned egress checks; no DB or network."""

from __future__ import annotations

import asyncio
import socket
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import httpx

from tests.shared.credential_sources import ROOT, boundary, egress, outbound
from tests.source_import_isolation import load_isolated_source

__source_only__ = True


def answer(ip, port):
    return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


class CredentialBoundaryTests(unittest.TestCase):
    def test_managed_ref_never_reads_unlisted_env_name(self):
        allowlist = {"managed-a": "LLM_SYNTHETIC_MANAGED_KEY"}
        module = load_isolated_source(
            "_managed_ref",
            ROOT / "app/services/ai/credential_boundary.py",
            {"app.core.config": NS(settings=NS(LLM_MANAGED_CREDENTIAL_REFS=allowlist))},
        )
        with patch.dict(
            module.os.environ, {"LLM_SYNTHETIC_MANAGED_KEY": "synthetic-key", "OTHER_SECRET": "not-readable"}
        ):
            self.assertEqual(module.resolve_managed_credential("managed-a"), "synthetic-key")
            for ref in ("OTHER_SECRET", "env:OTHER_SECRET", "missing", "managed-a\r\n", None):
                with self.subTest(ref=ref), patch.object(module.os.environ, "get") as read:
                    with self.assertRaises(PermissionError):
                        module.resolve_managed_credential(ref)
                    read.assert_not_called()

    def test_missing_or_stale_versions_deny(self):
        boundary.require_current_credential_version(3, 3)
        for accepted, current in ((1, 2), (None, 1), (True, 1), (0, 0), (-1, -1), (2**63, 2**63)):
            with self.subTest(accepted=accepted, current=current), self.assertRaises(PermissionError):
                boundary.require_current_credential_version(accepted, current)

    def test_header_names_templates_and_crlf(self):
        self.assertEqual(egress.credential_headers("x-api-key", "synthetic")["x-api-key"], "synthetic")
        self.assertEqual(
            egress.credential_headers("Authorization: Bearer {key}", "synthetic")["Authorization"], "Bearer synthetic"
        )
        for value in (
            "Host",
            "Cookie",
            "Authorization: literal-token",
            "Authorization\r\nHost",
            "https://example.org",
            "X-Api-Key: {key} injected",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                egress.validate_auth_header(value)
        with self.assertRaises(ValueError):
            egress.credential_headers(None, "synthetic\r\nHost: metadata")

    def test_paths_and_joined_destinations(self):
        self.assertEqual(
            egress.provider_destination("https://example.org/v1/", "/chat/completions"),
            "https://example.org/v1/chat/completions",
        )
        for path in (
            "//evil.example",
            "https://evil.example",
            "/\\evil",
            "/%2fmetadata",
            "/%252fmetadata",
            "/../models",
            "/chat?key=synthetic",
            "/#fragment",
            "/chat\r\n",
            "/%5cevil",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                egress.provider_destination("https://example.org/v1", path)
        for url in (
            "http://example.org",
            "https://user:password@example.org",
            "https://example.org/?key=synthetic",
            "https://example.org/#secret",
            "https://example.org\\@evil.org",
            "https://example.org:99999",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                egress.validate_base_url(url)

    def test_public_api_provider_projection_omits_secret_and_ref(self):
        catalog = load_isolated_source(
            "_credential_catalog",
            ROOT / "app/services/ai/personal_catalog.py",
            {
                "app.core.permissions": NS(get_current_user=lambda: None),
                "app.models": NS(LLMModel=None, LLMProvider=None, Tenant=None, User=None),
                "app.services.ai.provider_egress": egress,
            },
        )
        provider = NS(
            id=1,
            owner_user_id=2,
            name="Synthetic",
            uid="synthetic",
            api_format="openai",
            base_url="https://example.org",
            auth_header="SYNTH_HEADER_SECRET",
            description=None,
            is_active=True,
            encrypted_api_key="SYNTH_CIPHERTEXT",
            managed_credential_ref="SYNTH_REF",
        )
        projection = catalog.provider_metadata(provider, [])
        self.assertTrue(projection["has_api_key"])
        self.assertIsNone(projection["auth_header"])
        self.assertNotIn("SYNTH_", repr(projection))
        self.assertNotIn("encrypted_api_key", projection)
        self.assertNotIn("managed_credential_ref", projection)


class PinnedEgressTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.real_acquire = outbound.acquire_outbound_target

    def policy(self, addresses, registry=None):
        def acquire(host, port):
            answers = [entry for ip in addresses for entry in answer(ip, port)]
            return self.real_acquire(host, port, resolver=lambda *_: answers, registry=registry)

        return patch.object(egress, "acquire_outbound_target", acquire)

    async def test_resolves_once_and_connects_numeric_ip(self):
        raw = NS(connect_tcp=AsyncMock(return_value="stream"))
        backend = egress._PinnedBackend(raw)
        with self.policy(["8.8.8.8"]):
            result = await backend.connect_tcp("example.org", 443, timeout=1)
        self.assertEqual(result, "stream")
        raw.connect_tcp.assert_awaited_once_with("8.8.8.8", 443, timeout=1, local_address=None, socket_options=None)

    async def test_private_mixed_special_and_metadata_dns_never_connect(self):
        for ip in (
            "127.0.0.1",
            "10.0.0.1",
            "169.254.169.254",
            "100.100.100.200",
            "::1",
            "fc00::1",
            "fe80::1",
            "ff02::1",
            "::ffff:8.8.8.8",
            "2002:0808:0808::1",
        ):
            raw = NS(connect_tcp=AsyncMock())
            with self.subTest(ip=ip), self.policy(["8.8.8.8", ip]), self.assertRaises(ValueError):
                await egress._PinnedBackend(raw).connect_tcp("example.org", 443, timeout=1)
            raw.connect_tcp.assert_not_awaited()

    async def test_dns_rebinding_rechecks_each_attempt(self):
        raw = NS(connect_tcp=AsyncMock(return_value="stream"))
        backend = egress._PinnedBackend(raw)
        with self.policy(["8.8.8.8"]):
            await backend.connect_tcp("example.org", 443, timeout=1)
        with self.policy(["127.0.0.1"]), self.assertRaises(ValueError):
            await backend.connect_tcp("example.org", 443, timeout=1)
        self.assertEqual(raw.connect_tcp.await_count, 1)

    async def test_explicit_installation_private_mode_only_exact_addresses(self):
        registry = outbound.registry_from_entries(
            [outbound.TrustedHost("local.example", "private", frozenset({"10.1.2.3"}))]
        )
        raw = NS(connect_tcp=AsyncMock(return_value="stream"))
        with self.policy(["10.1.2.3"], registry):
            await egress._PinnedBackend(raw).connect_tcp("local.example", 8443, timeout=1)
        with self.policy(["10.1.2.4"], registry), self.assertRaises(ValueError):
            await egress._PinnedBackend(raw).connect_tcp("local.example", 8443, timeout=1)
        self.assertEqual(raw.connect_tcp.await_count, 1)

    async def test_transport_retains_host_sni_disables_proxy_and_redirect(self):
        transport = egress.ProviderTransport()
        self.assertIsInstance(transport._pool._network_backend, egress._PinnedBackend)
        request = httpx.Request("POST", "https://example.org/chat/completions")
        response = httpx.Response(307, headers={"Location": "https://127.0.0.1"}, request=request)
        with patch.object(httpx.AsyncHTTPTransport, "handle_async_request", AsyncMock(return_value=response)) as send:
            with self.assertRaisesRegex(ValueError, "redirects"):
                await transport.handle_async_request(request)
        self.assertEqual(send.call_args.args[0].url.host, "example.org")
        self.assertTrue(response.is_closed)
        await transport.aclose()


if __name__ == "__main__":
    unittest.main()
