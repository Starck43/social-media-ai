"""Actual-module mocked refresh checks; no app bootstrap, DB or live providers.

Run with stdlib unittest in an environment with HTTPX. Application imports are
explicit doubles while complete OAuth and diagnostic source modules are loaded
by file path. This is not a project-conftest bypass or a DB integration suite.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx

ROOT = Path(__file__).resolve().parents[2]
SECRETS = ("PRIVATE_ACCESS_SENTINEL", "PRIVATE_REFRESH_SENTINEL", "PRIVATE_DEVICE_SENTINEL", "PRIVATE_PKCE_SENTINEL")
PRIVATE_URL = "https://example.invalid/oauth?access_token=" + SECRETS[0]


def module(name, **attrs):
    result = ModuleType(name)
    result.__path__ = []
    result.__dict__.update(attrs)
    return result


def load_source(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class HostileError(Exception):
    def __str__(self):
        raise AssertionError("Exception text must not be inspected")

    def __repr__(self):
        raise AssertionError("Exception repr must not be inspected")


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []
        self.messages = []

    def emit(self, record):
        # Force actual formatter behavior, including exception/traceback rendering.
        self.messages.append(self.format(record))
        self.records.append(record)


class VKOAuthLogPrivacyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.row = SimpleNamespace(
            id=19,
            user_id=7,
            meta={"refresh_token": SECRETS[1], "device_id": SECRETS[2], "refresh_failed_at": "old"},
        )
        self.manager = SimpleNamespace(newest=AsyncMock(return_value=self.row), update_by_id=AsyncMock())
        self.encrypt = Mock(side_effect=lambda value: "cipher:" + value)
        self.resolve = AsyncMock(return_value="SERVICE_SECRET_SENTINEL")
        self.settings = SimpleNamespace(VK_APP_ID="12345")
        diagnostics = load_source("_p6_t02_actual_diagnostics", "app/services/social/credential_diagnostics.py")
        doubles = {
            name: module(name)
            for name in (
                "app",
                "app.core",
                "app.services",
                "app.services.social",
                "app.models",
                "app.models.managers",
                "app.utils",
            )
        }
        doubles.update(
            {
                "app.core.config": module("app.core.config", settings=self.settings),
                "app.services.social.platform_config": module(
                    "app.services.social.platform_config",
                    load_platform_config=AsyncMock(return_value={"redirect_uri": "https://example.invalid/callback"}),
                    require_config_param=lambda params, key: params[key],
                ),
                "app.services.social.credentials": module(
                    "app.services.social.credentials", resolve_token=self.resolve
                ),
                "app.services.social.credential_diagnostics": diagnostics,
                "app.models.managers.user_credential_manager": module(
                    "app.models.managers.user_credential_manager",
                    user_credentials=self.manager,
                ),
                "app.utils.crypto": module("app.utils.crypto", encrypt_secret=self.encrypt),
            }
        )
        self.import_patch = patch.dict(sys.modules, doubles)
        self.import_patch.start()
        self.addCleanup(self.import_patch.stop)
        self.oauth = load_source("_p6_t02_actual_vk_oauth", "app/services/social/vk_oauth.py")
        self.post = AsyncMock(
            return_value={
                "access_token": SECRETS[0],
                "refresh_token": "ROTATED_SECRET_SENTINEL",
                "expires_in": 3600,
            }
        )
        self.oauth._post_form = self.post
        transport = patch.object(httpx, "AsyncClient", side_effect=AssertionError("No live transport allowed"))
        transport.start()
        self.addCleanup(transport.stop)
        self.capture = Capture()
        logger = self.oauth.logger
        old_level, old_propagate = logger.level, logger.propagate
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(self.capture)
        self.addCleanup(logger.removeHandler, self.capture)
        self.addCleanup(logger.setLevel, old_level)
        self.addCleanup(setattr, logger, "propagate", old_propagate)

    def assert_private_logs(self):
        rendered = "\n".join(self.capture.messages)
        for secret in (*SECRETS, "SERVICE_SECRET_SENTINEL", "ROTATED_SECRET_SENTINEL", PRIVATE_URL):
            self.assertNotIn(secret, rendered)
        for record in self.capture.records:
            self.assertIsNone(record.exc_info)
            self.assertIsNone(record.exc_text)
            self.assertIsNone(record.stack_info)
            self.assertTrue(all(type(arg) in (int, str, type(None)) for arg in record.args))
        self.assertNotIn("Traceback", rendered)

    async def test_refresh_exception_categories_are_bounded_and_secret_free(self):
        request = httpx.Request("POST", PRIVATE_URL)
        response = httpx.Response(401, request=request)
        for error, category in (
            (httpx.ReadTimeout(PRIVATE_URL, request=request), "timeout"),
            (httpx.ConnectError(PRIVATE_URL, request=request), "connection_error"),
            (httpx.HTTPStatusError(PRIVATE_URL, request=request, response=response), "authorization_error"),
            (ValueError(" ".join(SECRETS)), "invalid_response"),
            (RuntimeError(" ".join(SECRETS)), "integration_error"),
        ):
            with self.subTest(category=category):
                self.capture.records.clear()
                self.capture.messages.clear()
                self.post.side_effect = error
                self.manager.update_by_id.reset_mock()
                self.assertIsNone(await self.oauth.refresh_user_token(7))
                self.assertEqual(self.capture.messages, [f"VK ID refresh failed user_id=7 code=error: {category}"])
                self.manager.update_by_id.assert_awaited_once()
                args = self.manager.update_by_id.await_args
                self.assertEqual(args.args, (19,))
                self.assertIn("refresh_failed_at", args.kwargs["meta"])
                datetime.fromisoformat(args.kwargs["meta"]["refresh_failed_at"])
                self.encrypt.assert_not_called()
                self.assert_private_logs()

    async def test_hostile_refresh_exception_is_never_formatted(self):
        self.post.side_effect = HostileError()
        self.assertIsNone(await self.oauth.refresh_user_token(7))
        self.assertIn("code=error: integration_error", self.capture.messages[0])
        self.assert_private_logs()

    async def test_bookkeeping_exception_has_no_traceback_or_message(self):
        self.manager.update_by_id.side_effect = RuntimeError(" ".join(SECRETS))
        self.assertIsNone(await self.oauth._mark_refresh_failed(self.manager, self.row, dict(self.row.meta)))
        self.assertEqual(
            self.capture.messages, ["VK ID refresh failure bookkeeping failed user_id=7 code=error: integration_error"]
        )
        self.assert_private_logs()

    async def test_hostile_bookkeeping_exception_is_never_formatted(self):
        self.manager.update_by_id.side_effect = HostileError()
        self.assertIsNone(await self.oauth._mark_refresh_failed(self.manager, self.row, dict(self.row.meta)))
        self.assertEqual(len(self.capture.records), 1)
        self.assert_private_logs()

    async def test_double_failure_remains_best_effort(self):
        self.post.side_effect = httpx.ConnectError(PRIVATE_URL)
        self.manager.update_by_id.side_effect = HostileError()
        self.assertIsNone(await self.oauth.refresh_user_token(7))
        self.assertEqual(len(self.capture.records), 2)
        self.assert_private_logs()

    async def test_success_preserves_rotation_and_expiry_without_secret_logs(self):
        before = datetime.now(timezone.utc)
        result = await self.oauth.refresh_user_token(7)
        self.assertEqual(result, SECRETS[0])
        self.post.assert_awaited_once()
        request = self.post.await_args.args[0]
        self.assertEqual(request["refresh_token"], SECRETS[1])
        self.assertEqual(request["device_id"], SECRETS[2])
        self.assertEqual(request["service_token"], "SERVICE_SECRET_SENTINEL")
        self.manager.newest.assert_awaited_once_with(user_id=7, platform="vk", kind="user_token")
        self.manager.update_by_id.assert_awaited_once()
        stored = self.manager.update_by_id.await_args.kwargs
        self.assertEqual(stored["secret_encrypted"], "cipher:" + SECRETS[0])
        self.assertEqual(stored["meta"]["refresh_token"], "ROTATED_SECRET_SENTINEL")
        self.assertEqual(stored["meta"]["device_id"], SECRETS[2])
        self.assertNotIn("refresh_failed_at", stored["meta"])
        self.assertGreaterEqual((stored["expires_at"] - before).total_seconds(), 3600)
        self.assert_private_logs()

    async def test_absent_rotated_refresh_never_reuses_spent_value(self):
        self.post.return_value = {"access_token": SECRETS[0]}
        await self.oauth.refresh_user_token(7)
        self.assertEqual(self.manager.update_by_id.await_args.kwargs["meta"]["refresh_token"], "")
        self.assert_private_logs()

    async def test_missing_application_does_not_read_vault(self):
        self.settings.VK_APP_ID = ""
        self.assertIsNone(await self.oauth.refresh_user_token(7))
        self.manager.newest.assert_not_awaited()
        self.post.assert_not_awaited()

    async def test_missing_row_does_not_contact_provider(self):
        self.manager.newest.return_value = None
        self.assertIsNone(await self.oauth.refresh_user_token(7))
        self.post.assert_not_awaited()
        self.manager.update_by_id.assert_not_awaited()

    async def test_missing_refresh_does_not_contact_provider(self):
        self.row.meta = {}
        self.assertIsNone(await self.oauth.refresh_user_token(7))
        self.post.assert_not_awaited()
        self.manager.update_by_id.assert_not_awaited()

    async def test_refresh_cancellation_propagates_without_bookkeeping(self):
        self.post.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.oauth.refresh_user_token(7)
        self.manager.update_by_id.assert_not_awaited()
        self.assertEqual(self.capture.records, [])

    async def test_bookkeeping_cancellation_propagates(self):
        self.manager.update_by_id.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.oauth._mark_refresh_failed(self.manager, self.row, {})
        self.assertEqual(self.capture.records, [])

    async def test_invalid_identifier_is_not_formatted_in_error_logs(self):
        self.post.side_effect = HostileError()
        for value in (True, -1, 0, "PRIVATE_PKCE_SENTINEL", HostileError()):
            self.capture.records.clear()
            self.capture.messages.clear()
            self.assertIsNone(await self.oauth.refresh_user_token(value))
            self.assertIn("user_id=None", self.capture.messages[0])
            self.assert_private_logs()

    async def test_invalid_bookkeeping_identifier_is_not_formatted(self):
        self.row.user_id = HostileError()
        self.manager.update_by_id.side_effect = HostileError()
        await self.oauth._mark_refresh_failed(self.manager, self.row, {})
        self.assertIn("user_id=None", self.capture.messages[0])
        self.assert_private_logs()


if __name__ == "__main__":
    unittest.main()
