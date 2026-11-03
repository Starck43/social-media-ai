"""Synthetic PostgreSQL tests for narrow operator recovery, without provider access."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.models.managers.user_credential_manager import user_credentials
from app.services.social import vk_oauth
from app.services.social.connections import connection_status
from app.services.social.credential_recovery import RecoveryRefused, recover_unreadable_vk
from app.utils.crypto import encrypt_secret
from scripts.ops.recover_vk_unreadable import main
from tests.collection.test_web_sources import _client, _drop, _register


@pytest.mark.asyncio
async def test_wrong_key_probe_and_approved_recovery(monkeypatch):
    monkeypatch.setattr(settings, "VK_APP_ID", "synthetic-app")
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", key)
    client = await _client()
    user = tenant_id = None
    try:
        user, tenant_id = await _register(client, "t14recover")
        row = await user_credentials.store(user_id=user.id, platform="vk", kind="user_token", secret="FAKE_ACCESS")
        await user_credentials.update_by_id(
            row.id,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            refresh_secret_encrypted=encrypt_secret("FAKE_REFRESH"),
            meta={"refresh_available": True, "other": "preserve"},
        )
        post = AsyncMock(side_effect=AssertionError("no provider requests"))
        monkeypatch.setattr(vk_oauth, "_post_form", post)
        monkeypatch.setattr(settings, "CREDENTIALS_KEY", Fernet.generate_key().decode())
        assert await vk_oauth.refresh_user_token(user.id) is None
        with pytest.raises(RecoveryRefused):
            await recover_unreadable_vk(user_id=user.id, credential_id=row.id, apply=True)
        persisted = await user_credentials.newest(user_id=user.id, platform="vk", kind="user_token")
        assert persisted.meta["refresh_attempt"] == {"state": "unreadable"}
        assert (await connection_status("vk", user.id)).operator_attention
        monkeypatch.setattr(settings, "CREDENTIALS_KEY", key)
        assert await recover_unreadable_vk(user_id=user.id, credential_id=row.id, apply=False) == "eligible"
        persisted = await user_credentials.newest(user_id=user.id, platform="vk", kind="user_token")
        before_access, before_refresh = persisted.secret_encrypted, persisted.refresh_secret_encrypted
        assert persisted.meta["refresh_attempt"] == {"state": "unreadable"}
        assert await recover_unreadable_vk(user_id=user.id, credential_id=row.id, apply=True) == "cleared"
        persisted = await user_credentials.newest(user_id=user.id, platform="vk", kind="user_token")
        assert persisted.meta == {"refresh_available": True, "other": "preserve"}
        assert (persisted.secret_encrypted, persisted.refresh_secret_encrypted) == (before_access, before_refresh)
        assert (await connection_status("vk", user.id)).state == "renewal_unverified"
        post.assert_not_awaited()
        with pytest.raises(RecoveryRefused):
            await recover_unreadable_vk(user_id=user.id, credential_id=row.id, apply=True)
    finally:
        await client.aclose()
        await _drop(user, tenant_id)


@pytest.mark.asyncio
async def test_unrecognized_claims_stale_id_and_corrupt_access_fail_closed(monkeypatch):
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", Fernet.generate_key().decode())
    client = await _client()
    user = tenant_id = None
    try:
        user, tenant_id = await _register(client, "t14recoverclosed")
        row = await user_credentials.store(user_id=user.id, platform="vk", kind="user_token", secret="FAKE_ACCESS")
        encrypted = encrypt_secret("FAKE_REFRESH")
        for attempt in (
            {"state": "pending", "id": "x"},
            {"state": "failed"},
            {"state": "unreadable", "id": "unexpected"},
            "unreadable",
            None,
        ):
            meta = {"refresh_available": True, "refresh_attempt": attempt, "refresh_failed_at": "bad"}
            await user_credentials.update_by_id(row.id, refresh_secret_encrypted=encrypted, meta=meta)
            with pytest.raises(RecoveryRefused):
                await recover_unreadable_vk(user_id=user.id, credential_id=row.id, apply=True)
            assert (await user_credentials.newest(user_id=user.id, platform="vk", kind="user_token")).meta == meta
        meta = {"refresh_available": True, "refresh_attempt": {"state": "unreadable"}, "refresh_failed_at": "bad"}
        await user_credentials.update_by_id(row.id, meta=meta)
        with pytest.raises(RecoveryRefused):
            await recover_unreadable_vk(user_id=user.id, credential_id=row.id + 100000, apply=True)
        # A mismatched access ciphertext must block recovery even with a readable refresh.
        await user_credentials.update_by_id(row.id, secret_encrypted="not-fernet")
        with pytest.raises(RecoveryRefused):
            await recover_unreadable_vk(user_id=user.id, credential_id=row.id, apply=True)
        assert (await user_credentials.newest(user_id=user.id, platform="vk", kind="user_token")).meta == meta
    finally:
        await client.aclose()
        await _drop(user, tenant_id)


def test_cli_requires_explicit_target_maintenance_and_backup(capsys):
    for args in (
        ["--user-id", "1", "--credential-id", "2"],
        ["--user-id", "1", "--credential-id", "2", "--target-approved", "--writers-stopped", "--apply"],
    ):
        with pytest.raises(SystemExit) as exc:
            main(args)
        assert exc.value.code == 2
    assert "--backup-confirmed" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_actual_cli_probe_and_apply_on_synthetic_target(monkeypatch):
    import asyncio
    import os
    import sys

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", key)
    client = await _client()
    user = tenant_id = None
    try:
        user, tenant_id = await _register(client, "t14recovercli")
        row = await user_credentials.store(user_id=user.id, platform="vk", kind="user_token", secret="FAKE_ACCESS")
        meta = {"refresh_available": True, "refresh_attempt": {"state": "unreadable"}, "refresh_failed_at": "bad"}
        await user_credentials.update_by_id(row.id, refresh_secret_encrypted=encrypt_secret("FAKE_REFRESH"), meta=meta)
        env = os.environ.copy()
        env.update(
            POSTGRES_URL=env["TEST_POSTGRES_URL"], DB_SCHEMA=env["DB_TEST_SCHEMA"], CREDENTIALS_KEY=key, DEBUG="false"
        )
        argv = [
            sys.executable,
            "-m",
            "scripts.ops.recover_vk_unreadable",
            "--user-id",
            str(user.id),
            "--credential-id",
            str(row.id),
            "--target-approved",
            "--writers-stopped",
        ]

        async def run(*args, current_env=env):
            proc = await asyncio.create_subprocess_exec(
                *argv, *args, env=current_env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            out, err = await proc.communicate()
            assert b"FAKE_ACCESS" not in out + err and b"FAKE_REFRESH" not in out + err
            return proc.returncode, out, err

        wrong_env = {**env, "CREDENTIALS_KEY": Fernet.generate_key().decode()}
        code, _, _ = await run("--apply", "--backup-confirmed", current_env=wrong_env)
        assert code == 1
        assert (await user_credentials.newest(user_id=user.id, platform="vk", kind="user_token")).meta == meta
        code, out, _ = await run()
        assert code == 0 and b"marker unchanged" in out
        assert (await user_credentials.newest(user_id=user.id, platform="vk", kind="user_token")).meta == meta
        code, out, _ = await run("--apply", "--backup-confirmed")
        assert code == 0 and b"marker cleared" in out
        assert (await user_credentials.newest(user_id=user.id, platform="vk", kind="user_token")).meta == {
            "refresh_available": True,
            "refresh_failed_at": "bad",
        }
        assert (await connection_status("vk", user.id)).state == "reauth"
    finally:
        await client.aclose()
        await _drop(user, tenant_id)
