"""Selected VK request-boundary checks using actual modules and local doubles."""

from __future__ import annotations

import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tests.source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]


def _module(name: str, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    return module


def _load_modules():
    policy = load_isolated_source("_p8_t38_execution_vk_access", ROOT / "app/services/social/vk_access.py")
    task_access = load_isolated_source(
        "_p8_t38_execution_task_access",
        ROOT / "app/services/social/vk_task_access.py",
        {"app.services.social.vk_access": policy},
    )
    sent: list[tuple[str, dict]] = []

    class BaseClient:
        def __init__(self, platform=None):
            self.platform = platform

        async def _make_request(self, method, params, *, form_data=False):
            if params.get("access_token") != "legacy":
                assert form_data is True, "Selected tokens must travel in POST body, never in URL logs"
            sent.append((method, dict(params)))
            return {"response": {"ok": 1}}

    source_type = types.SimpleNamespace(SEARCH=object())
    credentials = _module(
        "app.services.social.credentials",
        AuthorizationRequired=type("AuthorizationRequired", (RuntimeError,), {}),
        resolve_token=AsyncMock(),
    )
    imports = {
        "app.core.config": _module("app.core.config", settings=types.SimpleNamespace()),
        "app.models": _module("app.models", Source=object),
        "app.services.social.base": _module("app.services.social.base", BaseClient=BaseClient),
        "app.services.social.credentials": credentials,
        "app.services.social.owner": _module("app.services.social.owner", resolve_source_owner=AsyncMock()),
        "app.types": _module("app.types", SourceType=source_type),
        "app.utils.date_parsing": _module(
            "app.utils.date_parsing", to_unix_timestamp=lambda value: value, universal_date_parser=lambda value: value
        ),
        "app.utils.enum_helpers": _module("app.utils.enum_helpers", get_enum_value=lambda value: value),
    }
    client_module = load_isolated_source(
        "_p8_t38_execution_vk_client", ROOT / "app/services/social/vk_client.py", imports
    )
    return policy, task_access, client_module.VKClient, credentials, sent


class VKSelectedRequestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.policy, self.access, self.VKClient, self.credentials, self.sent = _load_modules()
        self.client = self.VKClient(platform=types.SimpleNamespace(params={}))
        self.connection_module = _module("app.services.social.vk_connections")
        self.resolve_selected = AsyncMock(side_effect=["fresh-one", "fresh-two"])
        self.connection_module.resolve_selected_token = self.resolve_selected
        self.rate_limit = load_isolated_source(
            "_p8_t38_execution_rate_limit",
            ROOT / "app/services/social/rate_limit.py",
            {"app.core.config": _module("app.core.config", settings=types.SimpleNamespace())},
        )

    async def _request_with_selected_access(self, method="wall.get", params=None):
        with patch.dict(
            sys.modules,
            {
                "app.services.social.vk_task_access": self.access,
                "app.services.social.vk_connections": self.connection_module,
                "app.services.social.rate_limit": self.rate_limit,
            },
        ):
            with self.access.vk_task_access_scope(self.policy.VKSelection("user", 7), tenant_id=10, actor_id=3):
                return await self.client._make_request(method, params or {})

    async def test_each_outbound_request_gets_a_fresh_exact_selected_token(self):
        await self._request_with_selected_access(params={"access_token": "stale-cache", "q": "first"})
        await self._request_with_selected_access(params={"access_token": "stale-cache", "q": "second"})

        self.assertEqual([params["access_token"] for _, params in self.sent], ["fresh-one", "fresh-two"])
        self.assertEqual([params["q"] for _, params in self.sent], ["first", "second"])
        self.assertEqual(
            [call.args[0] for call in self.resolve_selected.call_args_list],
            [self.policy.VKSelection("user", 7), self.policy.VKSelection("user", 7)],
        )
        self.credentials.resolve_token.assert_not_awaited()

    async def test_expired_or_revoked_selection_denial_prevents_http(self):
        for reason in ("connection_expired", "connection_revoked"):
            with self.subTest(reason=reason):
                self.resolve_selected.side_effect = PermissionError(reason)
                with self.assertRaisesRegex(PermissionError, reason):
                    await self._request_with_selected_access()
        self.assertEqual(self.sent, [])
        self.credentials.resolve_token.assert_not_awaited()

    async def test_provider_error_text_and_request_url_are_not_exposed(self):
        sentinel = "DO_NOT_LEAK_TOKEN https://vk.example/request?access_token=hidden"

        async def provider_error(_client, method, params, *, form_data=False):
            self.sent.append((method, dict(params)))
            return {"error": {"error_code": 5, "error_msg": sentinel}}

        self.VKClient.__mro__[1]._make_request = provider_error
        with self.assertRaises(RuntimeError) as caught:
            await self._request_with_selected_access()
        self.assertEqual(str(caught.exception), "vk_selected_authorization_error")
        self.assertNotIn("DO_NOT_LEAK_TOKEN", str(caught.exception))
        self.assertNotIn("vk.example", str(caught.exception))

    async def test_transport_exception_details_are_replaced_with_fixed_error(self):
        async def transport_error(_client, method, params, *, form_data=False):
            raise RuntimeError("https://vk.example/?access_token=hidden")

        self.VKClient.__mro__[1]._make_request = transport_error
        with self.assertRaisesRegex(RuntimeError, "^vk_selected_request_failed$") as caught:
            await self._request_with_selected_access()
        self.assertNotIn("vk.example", str(caught.exception))
        self.assertNotIn("hidden", str(caught.exception))

    async def test_selected_rate_limit_response_preserves_bounded_retry_signal(self):
        async def provider_error(_client, method, params, *, form_data=False):
            return {"error": {"error_code": 6, "error_msg": "PRIVATE_SENTINEL"}}

        self.VKClient.__mro__[1]._make_request = provider_error
        result = await self._request_with_selected_access()
        self.assertEqual(self.rate_limit.response_rate_limit(result), (True, None))
        self.assertNotIn("PRIVATE_SENTINEL", repr(result))

    async def test_selected_http_429_keeps_retry_hint_without_original_request(self):
        import httpx

        original = httpx.Request("GET", "https://vk.example/?access_token=PRIVATE_SENTINEL")
        response = httpx.Response(429, headers={"Retry-After": "2"}, request=original)

        async def provider_error(_client, method, params, *, form_data=False):
            raise httpx.HTTPStatusError("PRIVATE_SENTINEL", request=original, response=response)

        self.VKClient.__mro__[1]._make_request = provider_error
        with self.assertRaises(httpx.HTTPStatusError) as caught:
            await self._request_with_selected_access()
        self.assertEqual(self.rate_limit.http_rate_limit(caught.exception), (True, 2.0))
        self.assertNotIn("PRIVATE_SENTINEL", str(caught.exception))
        self.assertNotIn("PRIVATE_SENTINEL", str(caught.exception.request.url))

    async def test_not_found_is_empty_only_for_identity_methods(self):
        async def provider_error(_client, method, params, *, form_data=False):
            return {"error": {"error_code": 100, "error_msg": "PRIVATE_SENTINEL"}}

        self.VKClient.__mro__[1]._make_request = provider_error
        self.assertEqual(await self._request_with_selected_access("users.get"), {"response": []})
        with self.assertRaisesRegex(RuntimeError, "vk_selected_provider_error"):
            await self._request_with_selected_access("wall.get")

    async def test_legacy_request_keeps_existing_behavior_without_selected_fallback(self):
        with patch.dict(sys.modules, {"app.services.social.vk_task_access": self.access}):
            result = await self.client._make_request("wall.get", {"access_token": "legacy", "q": "x"})
        self.assertEqual(result, {"response": {"ok": 1}})
        self.assertEqual(self.sent, [("wall.get", {"access_token": "legacy", "q": "x"})])
        self.resolve_selected.assert_not_awaited()
        self.credentials.resolve_token.assert_not_awaited()

    async def test_contextvar_resets_after_error_and_isolated_between_coroutines(self):
        self.assertIsNone(self.access.selected_task_access())
        with self.assertRaisesRegex(RuntimeError, "sentinel"):
            with self.access.vk_task_access_scope(self.policy.VKSelection("user", 7), tenant_id=10, actor_id=3):
                raise RuntimeError("sentinel")
        self.assertIsNone(self.access.selected_task_access())

        barrier = asyncio.Event()
        both_started = 0

        async def observe(credential_id):
            nonlocal both_started
            selection = self.policy.VKSelection("user", credential_id)
            with self.access.vk_task_access_scope(selection, tenant_id=10, actor_id=credential_id):
                both_started += 1
                if both_started == 2:
                    barrier.set()
                await barrier.wait()
                bound = self.access.selected_task_access()
                return bound.selection.credential_id, bound.actor_id

        self.assertEqual(await asyncio.gather(observe(7), observe(8)), [(7, 7), (8, 8)])
        self.assertIsNone(self.access.selected_task_access())


class SelectedTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_transport_keeps_selected_token_out_of_url(self):
        import ast

        source = ast.parse((ROOT / "app/services/social/base.py").read_text())
        client_class = next(
            node for node in source.body if isinstance(node, ast.ClassDef) and node.name == "BaseClient"
        )
        method = next(
            node
            for node in client_class.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_make_request"
        )
        response = types.SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"response": []})
        transport = types.SimpleNamespace(post=AsyncMock(return_value=response), get=AsyncMock(return_value=response))

        class Session:
            async def __aenter__(self):
                return transport

            async def __aexit__(self, *_args):
                return False

        namespace = {
            "httpx": types.SimpleNamespace(AsyncClient=Session),
            "settings": types.SimpleNamespace(VK_REQUEST_TIMEOUT=5, SOCIAL_REQUEST_TIMEOUT=5),
        }
        exec(
            compile(ast.Module(body=[method], type_ignores=[]), "<actual BaseClient._make_request>", "exec"), namespace
        )
        client = type("ActualTransport", (), {"_make_request": namespace["_make_request"]})()
        client.platform = types.SimpleNamespace(params={"api_base_url": "https://vk.example/method"})
        await client._make_request("wall.get", {"access_token": "PRIVATE_SENTINEL"}, form_data=True)
        transport.get.assert_not_awaited()
        args, kwargs = transport.post.await_args
        self.assertEqual(args, ("https://vk.example/method/wall.get",))
        self.assertNotIn("PRIVATE_SENTINEL", args[0])
        self.assertNotIn("params", kwargs)
        self.assertEqual(kwargs["data"], {"access_token": "PRIVATE_SENTINEL"})
