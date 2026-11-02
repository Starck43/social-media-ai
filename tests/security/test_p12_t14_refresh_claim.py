"""Actual-source refresh orchestration with in-memory transaction doubles.

Run via stdlib unittest; no application bootstrap, DB or live network is used.
The shared-lock double exercises interleavings, NOT PostgreSQL lock semantics.
PostgreSQL multi-process validation remains a separate required gate.
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import logging
import sys
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[2]


def module(name, **attrs):
    result = ModuleType(name)
    result.__path__ = []
    result.__dict__.update(attrs)
    return result


def source(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class Vault:
    """Shared persisted state plus separate short transaction contexts."""

    def __init__(self):
        self.lock = asyncio.Lock()
        self.row = SimpleNamespace(
            id=19,
            user_id=7,
            meta={"refresh_token": "old-refresh", "device_id": "device", "custom": "keep"},
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            secret_encrypted="encrypted:old-access",
        )
        self.row.reveal = lambda: self.row.secret_encrypted.removeprefix("encrypted:")
        self.commits = []
        self.writes = []
        self.in_transaction = False
        self.fail_claim = False
        self.fail_final = False

    @asynccontextmanager
    async def locked_vk_token(self, *, user_id):
        async with self.lock:
            before = copy.deepcopy(self.row)
            self.in_transaction = True
            try:
                yield self, self.row
                if self.fail_claim and (self.row.meta or {}).get("refresh_attempt"):
                    raise RuntimeError("claim commit failure")
                self.commits.append(copy.deepcopy(self.row))
            except BaseException:
                self.row = before
                raise
            finally:
                self.in_transaction = False

    async def update_by_id(self, row_id, *, session, **values):
        assert session is self and self.in_transaction
        assert self.row.id == row_id
        if self.fail_final and "secret_encrypted" in values:
            raise RuntimeError("final write failure")
        self.writes.append(copy.deepcopy(values))
        for key, value in values.items():
            setattr(self.row, key, value)
        return self.row

    async def create(self, *, session, **values):
        assert session is self and self.in_transaction
        self.row = SimpleNamespace(id=20, **values)
        self.row.reveal = lambda: self.row.secret_encrypted.removeprefix("encrypted:")
        return self.row


class RefreshClaimTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.vault = Vault()
        self.settings = SimpleNamespace(VK_APP_ID="app")
        self.resolve = AsyncMock(return_value="service")
        self.encrypt = Mock(side_effect=lambda x: "encrypted:" + x)
        names = [
            "app",
            "app.core",
            "app.models",
            "app.models.managers",
            "app.services",
            "app.services.social",
            "app.utils",
        ]
        doubles = {name: module(name) for name in names}
        doubles.update(
            {
                "app.core.config": module("app.core.config", settings=self.settings),
                "app.models.managers.user_credential_manager": module(
                    "app.models.managers.user_credential_manager", user_credentials=self.vault
                ),
                "app.services.social.credentials": module(
                    "app.services.social.credentials", resolve_token=self.resolve
                ),
                "app.services.social.connections": module(
                    "app.services.social.connections", REFRESH_FAILURE_COOLDOWN=timedelta(hours=6)
                ),
                "app.services.social.platform_config": module(
                    "app.services.social.platform_config", load_platform_config=AsyncMock(), require_config_param=Mock()
                ),
                "app.services.social.credential_diagnostics": source(
                    "_p12_actual_diagnostics", "app/services/social/credential_diagnostics.py"
                ),
                "app.utils.crypto": module("app.utils.crypto", encrypt_secret=self.encrypt),
            }
        )
        self.imports = patch.dict(sys.modules, doubles)
        self.imports.start()
        self.addCleanup(self.imports.stop)
        self.oauth = source("_p12_actual_oauth", "app/services/social/vk_oauth.py")
        self.post = AsyncMock(
            return_value={"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}
        )
        self.oauth._post_form = self.post
        transport = patch("httpx.AsyncClient", side_effect=AssertionError("Live transport forbidden"))
        transport.start()
        self.addCleanup(transport.stop)

    async def test_claim_is_committed_before_http(self):
        async def post(payload):
            self.assertFalse(self.vault.in_transaction)
            self.assertEqual(self.vault.commits[-1].meta["refresh_attempt"]["state"], "pending")
            self.assertEqual(payload["refresh_token"], "old-refresh")
            return {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}

        self.post.side_effect = post
        self.assertEqual(await self.oauth.refresh_user_token(7), "new-access")
        self.assertNotIn("refresh_attempt", self.vault.row.meta)

    async def test_three_entrypoints_share_one_refresh_generation(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def post(payload):
            entered.set()
            await release.wait()
            return {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}

        self.post.side_effect = post
        first = asyncio.create_task(self.oauth.renew_if_expiring(7))
        await entered.wait()
        self.assertIsNone(await self.oauth.refresh_user_token(7))
        self.assertIsNone(await self.oauth.renew_if_expiring(7))
        release.set()
        self.assertEqual(await first, "new-access")
        self.assertEqual(await self.oauth.refresh_user_token(7), "new-access")
        self.post.assert_awaited_once()

    async def test_failure_is_fenced_against_new_login(self):
        async def post(payload):
            await self.oauth._store_vault_token(
                user_id=7,
                access_token="login-access",
                refresh_token="login-refresh",
                expires_in=3600,
                meta={"scope": "new"},
            )
            raise RuntimeError("old refresh failed")

        self.post.side_effect = post
        self.assertIsNone(await self.oauth.refresh_user_token(7))
        self.assertEqual(self.vault.row.secret_encrypted, "encrypted:login-access")
        self.assertEqual(self.vault.row.meta["refresh_token"], "login-refresh")
        self.assertNotIn("refresh_failed_at", self.vault.row.meta)

    async def test_success_is_fenced_against_new_login(self):
        async def post(payload):
            await self.oauth._store_vault_token(
                user_id=7, access_token="login-access", refresh_token="login-refresh", expires_in=3600, meta={}
            )
            return {"access_token": "stale-access", "refresh_token": "stale-refresh"}

        self.post.side_effect = post
        self.assertIsNone(await self.oauth.refresh_user_token(7))
        self.assertEqual(self.vault.row.meta["refresh_token"], "login-refresh")
        self.assertEqual(self.vault.row.secret_encrypted, "encrypted:login-access")

    async def test_no_finalization_after_row_replacement(self):
        async def post(payload):
            self.vault.row.id += 1
            return {"access_token": "stale-access"}

        self.post.side_effect = post
        self.assertIsNone(await self.oauth.refresh_user_token(7))
        self.assertEqual(self.vault.row.secret_encrypted, "encrypted:old-access")

    async def test_cancellation_never_replays_spent_refresh(self):
        self.post.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.oauth.refresh_user_token(7)
        self.assertEqual(self.vault.row.meta["refresh_attempt"]["state"], "pending")
        self.assertIsNone(await self.oauth.refresh_user_token(7))
        self.post.assert_awaited_once()

    async def test_ambiguous_network_failure_never_retries_consumed_generation(self):
        self.post.side_effect = TimeoutError("secret provider response")
        self.assertIsNone(await self.oauth.refresh_user_token(7))
        self.assertEqual(self.vault.row.meta["refresh_attempt"]["state"], "failed")
        self.assertIn("refresh_failed_at", self.vault.row.meta)
        self.vault.row.meta["refresh_failed_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.assertIsNone(await self.oauth.refresh_user_token(7))
        self.post.assert_awaited_once()

    async def test_cooldown_blocks_legacy_failure(self):
        self.vault.row.meta["refresh_failed_at"] = datetime.now(timezone.utc).isoformat()
        self.assertIsNone(await self.oauth.refresh_user_token(7))
        self.post.assert_not_awaited()

    async def test_old_legacy_failure_does_not_block_after_cooldown(self):
        self.vault.row.meta["refresh_failed_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.assertEqual(await self.oauth.refresh_user_token(7), "new-access")
        self.assertNotIn("refresh_failed_at", self.vault.row.meta)

    async def test_malformed_legacy_failure_fails_closed(self):
        self.vault.row.meta["refresh_failed_at"] = "not-a-date"
        self.assertIsNone(await self.oauth.refresh_user_token(7))
        self.post.assert_not_awaited()

    async def test_failed_claim_commit_prevents_http(self):
        self.vault.fail_claim = True
        with self.assertRaisesRegex(RuntimeError, "claim commit failure"):
            await self.oauth.refresh_user_token(7)
        self.post.assert_not_awaited()
        self.assertNotIn("refresh_attempt", self.vault.row.meta)

    async def test_failed_final_write_retains_claim_and_prevents_replay(self):
        self.vault.fail_final = True
        with self.assertRaisesRegex(RuntimeError, "final write failure"):
            await self.oauth.refresh_user_token(7)
        self.assertEqual(self.vault.row.meta["refresh_attempt"]["state"], "pending")
        self.assertIsNone(await self.oauth.refresh_user_token(7))
        self.post.assert_awaited_once()

    async def test_missing_rotated_refresh_never_falls_back(self):
        self.post.return_value = {"access_token": "new-access", "expires_in": 3600}
        await self.oauth.refresh_user_token(7)
        self.assertEqual(self.vault.row.meta["refresh_token"], "")

    async def test_success_preserves_unrelated_metadata(self):
        await self.oauth.refresh_user_token(7)
        self.assertEqual(self.vault.row.meta["custom"], "keep")
        self.assertEqual(self.vault.row.meta["device_id"], "device")

    async def test_existing_login_updates_all_metadata_and_supersedes_claim(self):
        self.vault.row.meta["refresh_failed_at"] = "old-failure"
        self.vault.row.meta["refresh_attempt"] = {"state": "pending", "id": "old"}
        await self.oauth._store_vault_token(
            user_id=7, access_token="login", refresh_token="login-refresh", expires_in=3600, meta={"scope": "scope"}
        )
        self.assertEqual(self.vault.row.meta["refresh_token"], "login-refresh")
        self.assertEqual(self.vault.row.meta["custom"], "keep")
        self.assertNotIn("refresh_failed_at", self.vault.row.meta)
        self.assertNotIn("refresh_attempt", self.vault.row.meta)

    async def test_new_login_stores_metadata_even_without_expiry_or_refresh(self):
        self.vault.row = None
        await self.oauth._store_vault_token(
            user_id=7, access_token="login", refresh_token=None, expires_in=None, meta={"user_id": 9}
        )
        self.assertEqual(self.vault.row.meta["user_id"], 9)
        self.assertIn("refresh_token", self.vault.row.meta)
        self.assertIsNone(self.vault.row.meta["refresh_token"])

    async def test_invalid_identifiers_do_not_enter_vault(self):
        with patch.object(self.vault, "locked_vk_token", side_effect=AssertionError("must not enter")):
            for value in (True, False, 0, -1, "secret"):
                self.assertIsNone(await self.oauth.refresh_user_token(value))
        self.post.assert_not_awaited()

    async def test_no_app_no_http(self):
        self.settings.VK_APP_ID = None
        self.assertIsNone(await self.oauth.refresh_user_token(7))
        self.post.assert_not_awaited()

    async def test_missing_row_no_http(self):
        self.vault.row = None
        self.assertIsNone(await self.oauth.refresh_user_token(7))
        self.post.assert_not_awaited()

    async def test_missing_refresh_no_http(self):
        self.vault.row.meta.pop("refresh_token")
        self.assertIsNone(await self.oauth.refresh_user_token(7))
        self.post.assert_not_awaited()

    async def test_custom_margin_is_checked_under_claim_lock(self):
        self.vault.row.expires_at = datetime.now(timezone.utc) + timedelta(minutes=3)
        self.assertEqual(await self.oauth.renew_if_expiring(7, margin=timedelta(minutes=1)), "old-access")
        self.post.assert_not_awaited()
        self.assertEqual(await self.oauth.renew_if_expiring(7, margin=timedelta(minutes=4)), "new-access")

    async def test_missing_expiry_proactive_does_not_refresh_but_explicit_does(self):
        self.vault.row.expires_at = None
        self.assertIsNone(await self.oauth.renew_if_expiring(7))
        self.post.assert_not_awaited()
        self.assertEqual(await self.oauth.refresh_user_token(7), "new-access")

    async def test_invalid_response_leaves_failed_claim(self):
        self.post.return_value = {"access_token": ""}
        self.assertIsNone(await self.oauth.refresh_user_token(7))
        self.assertEqual(self.vault.row.meta["refresh_attempt"]["state"], "failed")

    async def test_secret_failure_text_is_not_logged(self):
        self.post.side_effect = RuntimeError("PRIVATE_REFRESH_SENTINEL")
        with self.assertLogs(self.oauth.logger, logging.WARNING) as captured:
            self.assertIsNone(await self.oauth.refresh_user_token(7))
        self.assertNotIn("PRIVATE_REFRESH_SENTINEL", "\n".join(captured.output))


class ManagerLockTests(unittest.IsolatedAsyncioTestCase):
    """Execute the actual manager context, compile SQL; no PostgreSQL access."""

    def setUp(self):
        from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table

        metadata = MetaData()
        users = Table("users", metadata, Column("id", Integer), Column("is_active", Boolean), schema="test_schema")
        credentials = Table(
            "user_credentials",
            metadata,
            Column("id", Integer),
            Column("user_id", Integer),
            Column("platform", String),
            Column("kind", String),
            Column("is_active", Boolean),
            Column("created_at", DateTime),
            Column("updated_at", DateTime),
            schema="test_schema",
        )
        self.session = SimpleNamespace(execute=AsyncMock(), scalar=AsyncMock(side_effect=[7, "locked-row"]))
        session = self.session
        self.events = []
        events = self.events

        class Base:
            def __class_getitem__(cls, _):
                return cls

            def __init__(self, model):
                self.model = model

            @asynccontextmanager
            async def _get_owned_session(self):
                events.append("begin")
                try:
                    yield session
                    events.append("commit")
                except BaseException:
                    events.append("rollback")
                    raise

        name = "app.models.managers.user_credential_manager"
        doubles = {
            "app": module("app"),
            "app.models": module("app.models"),
            "app.models.managers": module("app.models.managers"),
            "app.models.identity": module("app.models.identity"),
            "app.models.managers.base_manager": module("app.models.managers.base_manager", BaseManager=Base),
            "app.models.identity.user": module("app.models.identity.user", User=users.c),
            "app.models.identity.user_credential": module(
                "app.models.identity.user_credential", UserCredential=credentials
            ),
        }
        # select(mapped_model) is represented by a real SQLAlchemy table in this
        # compiler-only double. Expose mapped column attributes on that table.
        for col in credentials.c:
            setattr(credentials, col.name, col)
        self.imports = patch.dict(sys.modules, doubles)
        self.imports.start()
        self.addCleanup(self.imports.stop)
        self.manager = source(name, "app/models/managers/user_credential_manager.py").UserCredentialManager()

    async def test_locks_active_user_then_newest_active_vk_row(self):
        from sqlalchemy.dialects import postgresql

        async with self.manager.locked_vk_token(user_id=7) as (session, row):
            self.assertIs(session, self.session)
            self.assertEqual(row, "locked-row")
            self.assertEqual(self.events, ["begin"])
        self.assertEqual(self.events, ["begin", "commit"])
        self.assertEqual(str(self.session.execute.await_args.args[0]), "SET LOCAL lock_timeout = '5s'")
        owner, credential = [x.args[0] for x in self.session.scalar.await_args_list]
        owner_sql = str(owner.compile(dialect=postgresql.dialect()))
        token_sql = str(credential.compile(dialect=postgresql.dialect()))
        self.assertIn("users.is_active IS true", owner_sql)
        self.assertIn("FOR UPDATE", owner_sql)
        self.assertIn("FOR UPDATE", token_sql)
        self.assertIn("user_credentials.is_active IS true", token_sql)
        self.assertIn("ORDER BY coalesce(", token_sql)
        self.assertIn("user_credentials.id DESC", token_sql)
        self.assertTrue(credential.get_execution_options()["populate_existing"])
        self.assertEqual(set(credential.compile().params.values()), {7, "vk", "user_token", 1})

    async def test_absent_owner_never_creates_or_locks_token(self):
        self.session.scalar.side_effect = [None]
        with self.assertRaises(PermissionError):
            async with self.manager.locked_vk_token(user_id=7):
                self.fail("must not yield a missing owner")
        self.session.scalar.assert_awaited_once()
        self.assertEqual(self.events, ["begin", "rollback"])

    async def test_absent_token_yields_owned_session_for_oauth_creation(self):
        self.session.scalar.side_effect = [7, None]
        async with self.manager.locked_vk_token(user_id=7) as (session, row):
            self.assertIsNone(row)
            self.assertIs(session, self.session)
        self.assertEqual(self.events, ["begin", "commit"])

    async def test_exception_or_cancellation_rolls_back(self):
        for error in (RuntimeError("failure"), asyncio.CancelledError()):
            self.events.clear()
            self.session.scalar.side_effect = [7, "locked-row"]
            with self.assertRaises(type(error)):
                async with self.manager.locked_vk_token(user_id=7):
                    raise error
            self.assertEqual(self.events, ["begin", "rollback"])

    async def test_invalid_owner_never_opens_transaction(self):
        for user in (True, False, 0, -1, "secret"):
            with self.assertRaises(ValueError):
                async with self.manager.locked_vk_token(user_id=user):
                    self.fail("must not yield")
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main()
