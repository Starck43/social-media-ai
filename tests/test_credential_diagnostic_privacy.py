"""Isolated actual-source diagnostics checks; no app/DB/provider imports.

Run from the repository root with:
.venv/bin/python tests/test_credential_diagnostic_privacy.py

Use installed HTTPX exception types with doubled HTTPX/Telethon transports.
The endpoint's dependency gate is source-checked, not exercised through FastAPI;
live authorization is not claimed.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx as real_httpx

ROOT = Path(__file__).resolve().parents[1]
SECRET = "synthetic-private-token"
PRIVATE_URL = f"https://example.invalid/bot{SECRET}/getMe"
PRIVATE_SESSION = "synthetic-private-session"


def module(name, **attributes):
    value = ModuleType(name)
    value.__path__ = []
    value.__dict__.update(attributes)
    return value


def function_source(path, name):
    tree = ast.parse((ROOT / path).read_text())
    return next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == name)


def load_function(path, name, scope=None):
    node = function_source(path, name)
    node.decorator_list = []
    # Admission is supplied explicitly; do not evaluate Depends or import FastAPI.
    node.args.defaults = []
    tree = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node],
        type_ignores=[],
    )
    namespace = dict(scope or {})
    exec(compile(ast.fix_missing_locations(tree), str(ROOT / path), "exec"), namespace)
    return namespace[name]


TransportError = real_httpx.TransportError
TimeoutException = real_httpx.TimeoutException


class HTTPStatusError(real_httpx.HTTPStatusError):
    def __init__(self, status_code):
        request = real_httpx.Request("GET", PRIVATE_URL)
        response = real_httpx.Response(status_code, request=request)
        super().__init__(PRIVATE_URL, request=request, response=response)


class UnformattableError(Exception):
    def __str__(self):
        raise AssertionError("diagnostics must not inspect exception text")


class Response:
    def __init__(self, payload=None, *, status_code=200, json_error=None):
        self.payload, self.status_code, self.json_error = payload, status_code, json_error
        self.json_calls = 0

    def json(self):
        self.json_calls += 1
        if self.json_error is not None:
            raise self.json_error
        return deepcopy(self.payload)


class CredentialDiagnosticPrivacyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.requests = []
        self.response = Response({"response": {"id": 12}})
        self.request_error = None
        self.client_count = 0
        test = self

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url, **kwargs):
                test.requests.append((url, kwargs))
                if test.request_error is not None:
                    raise test.request_error
                return test.response

        def client_factory(**kwargs):
            self.assertEqual(kwargs, {"timeout": 15.0})
            self.client_count += 1
            return Client()

        self.httpx = module(
            "httpx",
            TransportError=TransportError,
            TimeoutException=TimeoutException,
            HTTPStatusError=real_httpx.HTTPStatusError,
            AsyncClient=client_factory,
        )
        spec = importlib.util.spec_from_file_location(
            "_credential_diagnostics_actual",
            ROOT / "app/services/social/credential_diagnostics.py",
        )
        self.diagnostics = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"httpx": self.httpx}):
            spec.loader.exec_module(self.diagnostics)

        self.user = SimpleNamespace(id=12)
        self.status = AsyncMock(return_value={"vk": "user-vault"})
        self.token = AsyncMock(return_value=SECRET)
        self.load_session = AsyncMock(return_value=None)
        self.check_session = AsyncMock(return_value="error: authorization_error")
        self.endpoint = load_function("app/api/v1/endpoints/credentials.py", "test_credentials")
        self.settings = SimpleNamespace(
            VK_API_BASE_URL="https://vk.example.invalid",
            VK_API_VERSION="5.199",
            TELEGRAM_API_BASE_URL="https://tg.example.invalid",
            MAX_API_URL="https://max.example.invalid",
        )
        self.modules = {
            "httpx": self.httpx,
            "app": module("app"),
            "app.services": module("app.services"),
            "app.services.social": module("app.services.social"),
            "app.services.social.credentials": module(
                "app.services.social.credentials",
                credential_status=self.status,
                resolve_token=self.token,
            ),
            "app.services.social.credential_diagnostics": self.diagnostics,
            "app.services.social.tg_session": module(
                "app.services.social.tg_session",
                load_session=self.load_session,
                check_session=self.check_session,
            ),
            "app.core": module("app.core"),
            "app.core.config": module("app.core.config", settings=self.settings),
        }

    async def endpoint_result(self, platform="vk"):
        self.status.return_value = {platform: "user-vault"}
        with patch.dict(sys.modules, self.modules):
            result = await self.endpoint(self.user)
        self.assert_private(result)
        return result[0]["status"]

    def assert_private(self, value):
        public = repr(value)
        for forbidden in (SECRET, PRIVATE_URL, PRIVATE_SESSION, "provider-raw-body"):
            self.assertNotIn(forbidden, public)

    async def test_admission_dependency_and_route_are_preserved_in_actual_source(self):
        node = function_source("app/api/v1/endpoints/credentials.py", "test_credentials")
        default = node.args.defaults[0]
        self.assertEqual(default.func.id, "Depends")
        gate = default.args[0]
        self.assertEqual(gate.func.id, "require_model_perm")
        self.assertEqual(gate.args[0].value, "credential")
        self.assertEqual(gate.args[1].value.id, "ActionType")
        self.assertEqual(gate.args[1].attr, "VIEW")
        self.assertEqual(node.decorator_list[0].args[0].value, "/test")

    async def test_all_exception_categories_ignore_secret_messages(self):
        cases = (
            (TimeoutError(PRIVATE_URL), "timeout"),
            (TimeoutException(PRIVATE_URL), "timeout"),
            (ConnectionError(SECRET), "connection_error"),
            (TransportError(PRIVATE_URL), "connection_error"),
            (ImportError(PRIVATE_SESSION), "integration_unavailable"),
            (PermissionError(SECRET), "authorization_error"),
            (ValueError("provider-raw-body"), "invalid_response"),
            (RuntimeError(PRIVATE_SESSION), "integration_error"),
            (UnformattableError(), "integration_error"),
        )
        for error, category in cases:
            with self.subTest(category=category, error_type=type(error).__name__):
                self.request_error = error
                self.assertEqual(await self.endpoint_result(), f"error: {category}")

    async def test_http_status_errors_use_only_numeric_category(self):
        for code, category in (
            (401, "authorization_error"),
            (403, "authorization_error"),
            (429, "rate_limited"),
            (503, "provider_unavailable"),
            (404, "provider_error"),
        ):
            with self.subTest(code=code):
                self.request_error = HTTPStatusError(code)
                self.assertEqual(await self.endpoint_result(), f"error: {category}")

    async def test_vk_raw_provider_error_and_request_parameters_never_echo(self):
        for code, category in ((5, "authorization_error"), (6, "rate_limited"), (100, "provider_error")):
            with self.subTest(code=code):
                self.response = Response(
                    {
                        "error": {
                            "error_code": code,
                            "error_msg": PRIVATE_URL,
                            "request_params": [{"key": "access_token", "value": SECRET}],
                        },
                        "raw": "provider-raw-body",
                    }
                )
                self.assertEqual(await self.endpoint_result(), f"error: {category}")
        self.assertEqual(self.requests[-1][1]["params"]["access_token"], SECRET)

    async def test_telegram_provider_descriptions_never_echo(self):
        for code, category in ((401, "authorization_error"), (429, "rate_limited"), (400, "provider_error")):
            with self.subTest(code=code):
                self.response = Response({"ok": False, "error_code": code, "description": PRIVATE_URL})
                self.assertEqual(await self.endpoint_result("telegram"), f"error: {category}")
        self.assertIn(SECRET, self.requests[-1][0])  # Secret is needed on the transport only.

    async def test_non_success_http_body_is_not_parsed_or_disclosed(self):
        for platform in ("vk", "telegram", "max"):
            with self.subTest(platform=platform):
                self.response = Response({"response": {"secret": SECRET}}, status_code=503)
                self.assertEqual(await self.endpoint_result(platform), "error: provider_unavailable")
                self.assertEqual(self.response.json_calls, 0)

    async def test_malformed_json_and_error_codes_fail_without_echo(self):
        for platform, payload in (
            ("vk", [SECRET]),
            ("vk", {"error": PRIVATE_URL}),
            ("vk", {"error": {"error_code": SECRET, "error_msg": PRIVATE_URL}}),
            ("vk", {"error": {"error_code": True}}),
            ("telegram", [SECRET]),
            ("telegram", {"ok": False, "description": PRIVATE_URL}),
            ("telegram", {"ok": SECRET, "error_code": PRIVATE_SESSION}),
        ):
            with self.subTest(platform=platform, payload_type=type(payload).__name__):
                self.response = Response(payload)
                self.assertEqual(await self.endpoint_result(platform), "error: invalid_response")
        self.response = Response(json_error=ValueError(PRIVATE_URL))
        self.assertEqual(await self.endpoint_result(), "error: invalid_response")

    async def test_success_missing_no_token_and_unknown_platform_contracts_survive(self):
        for platform, response, expected in (
            ("vk", Response({"response": {"id": 12}}), "ok"),
            ("telegram", Response({"ok": True, "result": {"username": "test_bot"}}), "ok (bot @test_bot)"),
            ("max", Response({"ignored": SECRET}), "ok"),
            ("unknown", Response(), "skipped"),
        ):
            with self.subTest(platform=platform):
                self.response = response
                self.assertEqual(await self.endpoint_result(platform), expected)
        self.status.return_value = {"vk": "missing"}
        before = self.client_count
        with patch.dict(sys.modules, self.modules):
            result = await self.endpoint(self.user)
        self.assertEqual(result[0]["status"], "missing")
        self.assertEqual(self.client_count, before)
        self.token.return_value = None
        self.assertEqual(await self.endpoint_result(), "no_token")
        self.assertEqual(self.client_count, before)

    async def test_current_owner_id_and_l2_diagnostic_shape_are_preserved(self):
        await self.endpoint_result()
        self.status.assert_awaited_once_with(owner_user_id=12)
        self.token.assert_awaited_once_with("vk", owner_user_id=12)
        self.load_session.assert_awaited_once_with(user_id=12)
        self.load_session.return_value = SimpleNamespace(session=PRIVATE_SESSION)
        with patch.dict(sys.modules, self.modules):
            result = await self.endpoint(self.user)
        self.assertEqual(
            result[-1],
            {
                "platform": "telegram",
                "source": "mtproto",
                "status": "error: authorization_error",
            },
        )
        self.assert_private(result)

    async def test_http_cancellation_propagates_without_diagnostic_success(self):
        self.request_error = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.endpoint_result()

    async def test_http_code_validation_never_formats_arbitrary_values(self):
        for value in (True, None, PRIVATE_URL, [], 99, 600):
            self.assertEqual(self.diagnostics.credential_http_error(value), "error: invalid_response")
        error = HTTPStatusError(403)
        error.response = None
        self.assertEqual(self.diagnostics.credential_exception_status(error), "error: invalid_response")

    async def test_optional_telethon_categories_do_not_require_telethon(self):
        for name, category in (
            ("AuthKeyUnregisteredError", "authorization_error"),
            ("SessionRevokedError", "authorization_error"),
            ("FloodWaitError", "rate_limited"),
        ):
            error_type = type(name, (Exception,), {})
            status = self.diagnostics.credential_exception_status(error_type(PRIVATE_SESSION))
            self.assertEqual(status, f"error: {category}")
            self.assert_private(status)

    async def test_mtproto_failure_stages_redact_and_disconnect(self):
        for stage in ("build", "connect", "authorize", "profile", "disconnect"):
            with self.subTest(stage=stage):
                client = SimpleNamespace(
                    connect=AsyncMock(),
                    is_user_authorized=AsyncMock(return_value=True),
                    get_me=AsyncMock(return_value=SimpleNamespace(username="test_user", id=12)),
                    disconnect=AsyncMock(),
                )
                error = UnformattableError(PRIVATE_SESSION)
                if stage != "build":
                    getattr(
                        client,
                        {
                            "connect": "connect",
                            "authorize": "is_user_authorized",
                            "profile": "get_me",
                            "disconnect": "disconnect",
                        }[stage],
                    ).side_effect = error

                def build(credentials):
                    if stage == "build":
                        raise error
                    return client

                check = load_function(
                    "app/services/social/tg_session.py",
                    "check_session",
                    {
                        "build_client": build,
                        "credential_exception_status": self.diagnostics.credential_exception_status,
                    },
                )
                status = await check(SimpleNamespace(session=PRIVATE_SESSION))
                self.assertEqual(status, "error: integration_error")
                self.assert_private(status)
                if stage == "build":
                    client.disconnect.assert_not_awaited()
                else:
                    client.disconnect.assert_awaited_once()

    async def test_mtproto_success_expiry_and_cancellation_keep_cleanup(self):
        client = SimpleNamespace(
            connect=AsyncMock(),
            is_user_authorized=AsyncMock(return_value=True),
            get_me=AsyncMock(return_value=SimpleNamespace(username="test_user", id=12)),
            disconnect=AsyncMock(),
        )
        check = load_function(
            "app/services/social/tg_session.py",
            "check_session",
            {
                "build_client": lambda credentials: client,
                "credential_exception_status": self.diagnostics.credential_exception_status,
            },
        )
        self.assertEqual(await check(SimpleNamespace()), "ok (user @test_user)")
        client.get_me.return_value.username = None
        self.assertEqual(await check(SimpleNamespace()), "ok (user @12)")
        client.is_user_authorized.return_value = False
        self.assertEqual(await check(SimpleNamespace()), "error: session expired or revoked")
        client.is_user_authorized.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await check(SimpleNamespace())
        self.assertEqual(client.disconnect.await_count, 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)