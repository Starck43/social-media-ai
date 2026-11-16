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
    def test_admin_fernet_prefix_is_not_proof_of_encryption(self):
        encrypt = lambda value: "sealed:" + value
        admin = load_isolated_source(
            "_credential_admin",
            ROOT / "app/admin/secrets.py",
            {
                "app.utils.crypto": NS(encrypt_secret=encrypt),
            },
        )
        data = {"encrypted_api_key": "gAAAAAsynthetic-plaintext-prefix"}  # pragma: allowlist secret (synthetic)
        admin.seal_form_secret(data, "encrypted_api_key")
        self.assertEqual(data["encrypted_api_key"], "sealed:gAAAAAsynthetic-plaintext-prefix")
        admin.hide_form_secret(data, "encrypted_api_key")
        self.assertEqual(data["encrypted_api_key"], "")

    def test_nested_connector_configuration_omits_secret_keys_and_ciphertext(self):
        module = load_isolated_source("_credential_metadata", ROOT / "app/utils/secret_metadata.py")
        value = {
            "host": "example.org",
            "api_key": "synthetic-key",  # pragma: allowlist secret (synthetic)
            "nested": {
                "refresh_token": "synthetic-refresh",  # pragma: allowlist secret (synthetic)
                "items": [
                    {"private_key": "synthetic-private", "label": "Safe"}  # pragma: allowlist secret (synthetic)
                ],  # pragma: allowlist secret (synthetic)
                "note": "Bearer synthetic-echo",
                "cipher": "gAAAAAsynthetic",
                "iv": "synthetic-iv",
                "tag": "synthetic-tag",
            },
        }
        projection = module.safe_secret_metadata(value)
        self.assertNotIn("synthetic", repr(projection))
        self.assertEqual(projection["host"], "example.org")
        self.assertEqual(projection["nested"]["items"], [{"label": "Safe"}])

    def test_managed_ref_never_reads_unlisted_env_name(self):
        allowlist = {"managed-a": "LLM_SYNTHETIC_MANAGED_KEY"}
        module = load_isolated_source(
            "_managed_ref",
            ROOT / "app/services/ai/credential_boundary.py",
            {"app.core.config": NS(settings=NS(LLM_MANAGED_CREDENTIAL_REFS=allowlist))},
        )
        with patch.dict(
            module.os.environ,
            {
                "LLM_SYNTHETIC_MANAGED_KEY": "synthetic-key",
                "OTHER_SECRET": "not-readable",  # pragma: allowlist secret (synthetic)
            },  # pragma: allowlist secret (synthetic)
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
            "https://user:password@example.org",  # pragma: allowlist secret (synthetic)
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
            encrypted_api_key="SYNTH_CIPHERTEXT",  # pragma: allowlist secret (synthetic)
            managed_credential_ref="SYNTH_REF",
        )
        projection = catalog.provider_metadata(provider, [])
        self.assertTrue(projection["has_api_key"])
        self.assertIsNone(projection["auth_header"])
        self.assertNotIn("SYNTH_", repr(projection))
        self.assertNotIn("encrypted_api_key", projection)
        self.assertNotIn("managed_credential_ref", projection)


class PinnedEgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_cross_owner_and_stale_dispatch_deny_before_decrypt(self):
        owner_id = 2
        decrypt = unittest.mock.Mock(return_value="synthetic")
        provider = NS(owner_user_id=2, is_active=True, credential_version=2, get_api_key=decrypt)
        fresh = NS(provider=provider)
        module = load_isolated_source(
            "_credential_dispatch",
            ROOT / "app/services/ai/personal_dispatch.py",
            {
                "app.core.personal_catalog_context": NS(
                    assert_catalog_model=lambda _: None, current_catalog_owner_id=lambda: owner_id
                ),
                "app.core.tenant_context": NS(current_tenant_id=lambda: None, is_bypass=lambda: False),
                "app.models": NS(
                    LLMModel=NS(objects=NS(select_related=lambda _: NS(get=AsyncMock(return_value=fresh)))),
                    User=NS(objects=NS(get=AsyncMock(return_value=NS(is_active=True)))),
                ),
                "app.services.ai.credential_boundary": boundary,
            },
        )
        client = NS(model=NS(id=1), credential_version=1)
        with self.assertRaises(PermissionError):
            await module.validate_personal_dispatch(client)
        decrypt.assert_not_called()
        provider.owner_user_id = 3
        with self.assertRaises(PermissionError):
            await module.validate_personal_dispatch(client)
        decrypt.assert_not_called()
        provider.owner_user_id = 2
        provider.credential_version = 1
        provider.id = 2
        client.provider = NS(id=1)
        with self.assertRaises(PermissionError):
            await module.validate_personal_dispatch(client)
        decrypt.assert_not_called()

    async def test_social_callback_does_not_echo_error_or_exception_secret(self):
        class PlatformConfigurationError(RuntimeError):
            pass

        oauth = NS(
            build_authorize_url=AsyncMock(), complete_authorization=AsyncMock(side_effect=ValueError("SYNTHETIC_TOKEN"))
        )
        module = load_isolated_source(
            "_credential_social",
            ROOT / "app/api/v1/endpoints/social.py",
            {
                "app.services.social.platform_config": NS(PlatformConfigurationError=PlatformConfigurationError),
                "app.services.social.vk_oauth": oauth,
            },
        )
        response = await module.vk_callback(code="synthetic", state="synthetic", error="SYNTHETIC_INPUT_TOKEN")
        self.assertNotIn(b"SYNTHETIC_INPUT_TOKEN", response.body)
        oauth.complete_authorization.assert_not_awaited()
        response = await module.vk_callback(code="synthetic", state="synthetic")
        self.assertNotIn(b"SYNTHETIC_TOKEN", response.body)

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

    async def test_installation_private_mode_cannot_allow_loopback_or_metadata(self):
        for ip in ("127.0.0.1", "::1", "169.254.169.254", "100.100.100.200"):
            registry = outbound.registry_from_entries(
                [outbound.TrustedHost("local.example", "private", frozenset({ip}))]
            )
            raw = NS(connect_tcp=AsyncMock())
            with self.subTest(ip=ip), self.policy([ip], registry), self.assertRaises(ValueError):
                await egress._PinnedBackend(raw).connect_tcp("local.example", 443, timeout=1)
            raw.connect_tcp.assert_not_awaited()

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
