"""Telegram MTProto (L2) session: vault-backed credentials and client building.

Three kinds in the per-tenant vault make an L2 session work:

| kind       | meaning                                  |
|------------|------------------------------------------|
| `api_id`   | Telegram app id from my.telegram.org      |
| `api_hash` | Telegram app hash from my.telegram.org    |
| `session`  | Telethon `StringSession` after login      |

The session string is a full-access credential (equivalent to being logged in
as the user): store it only in the vault, never log or echo it. telethon is
imported lazily so the app boots and tests run without it installed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from app.services.social.credentials import resolve_token

logger = logging.getLogger(__name__)

# Vault kinds that make up one L2 login (order matches `credentials login`).
SESSION_KINDS: tuple[str, ...] = ("api_id", "api_hash", "session")


class SessionMissing(RuntimeError):
    """L2 collection was requested but no MTProto session exists."""


@dataclass(frozen=True)
class TelegramSession:
    """Resolved MTProto credentials, plaintext only as a return value."""

    api_id: int
    api_hash: str
    session: str  # Telethon StringSession serialised


async def load_session(tenant_id: Optional[int] = None) -> Optional[TelegramSession]:
    """Resolve api_id/api_hash/session from the vault (env as legacy fallback).

    Returns None when any part is missing — partial configuration never
    produces a half-working client. Never logs the secrets.
    """
    api_id = await resolve_token("telegram", tenant_id=tenant_id, kinds=("api_id",))
    api_hash = await resolve_token("telegram", tenant_id=tenant_id, kinds=("api_hash",))
    session = await resolve_token("telegram", tenant_id=tenant_id, kinds=("session",))

    if not (api_id and api_hash and session):
        return None

    try:
        return TelegramSession(api_id=int(api_id), api_hash=api_hash, session=session)
    except ValueError:
        logger.error("telegram/api_id in the vault is not an integer - L2 session unusable")
        return None


async def require_session(tenant_id: Optional[int] = None) -> TelegramSession:
    """Like `load_session` but raises `SessionMissing` instead of returning None."""
    session = await load_session(tenant_id)
    if session is None:
        raise SessionMissing(
            "No Telegram MTProto session configured (need api_id + api_hash + session in the vault). "
            "Run: python -m cli.main credentials login telegram"
        )
    return session


def build_client(session: TelegramSession):
    """Create an unconnected Telethon client from a session string (lazy import)."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    return TelegramClient(StringSession(session.session), session.api_id, session.api_hash)


async def check_session(session: TelegramSession) -> str:
    """Cheap `getMe` equivalent for `credentials test`; never raises."""
    try:
        client = build_client(session)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                return "error: session expired or revoked"
            me = await client.get_me()
            return f"ok (user @{me.username or me.id})"
        finally:
            await client.disconnect()
    except Exception as e:  # noqa: BLE001 - diagnostics must never crash
        return f"error: {e}"


async def save_session(tenant_id: int, *, api_id: int, api_hash: str, session: str) -> None:
    """Persist the three L2 parts into the vault, replacing the active ones.

    Old rows are deactivated, not deleted: resolution already prefers the
    newest row, but a clean single-active row keeps admin/CLI output honest.
    Secrets go through `store()` (Fernet) and are never logged.
    """
    from app.models.managers.tenant_manager import tenant_credentials

    for kind, value in (("api_id", str(api_id)), ("api_hash", api_hash), ("session", session)):
        stale = await tenant_credentials.filter(tenant_id=tenant_id, platform="telegram", kind=kind, is_active=True)
        for row in stale:
            await tenant_credentials.update_by_id(row.id, is_active=False)
        await tenant_credentials.store(tenant_id=tenant_id, platform="telegram", kind=kind, secret=value)
    logger.info(f"Stored Telegram MTProto session parts for workspace {tenant_id}")


async def interactive_login(api_id: int, api_hash: str) -> str:
    """Login by phone code and return a new StringSession (interactive, CLI only).

    Handles the 2FA password prompt. The caller runs this in a terminal —
    input() is intentional and never used outside the CLI.
    """
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            phone = input("Phone number (with country code): ").strip()
            sent = await client.send_code_request(phone)
            code = input("Code from Telegram: ").strip()
            try:
                await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
            except SessionPasswordNeededError:
                await client.sign_in(password=input("2FA password: ").strip())
        return client.session.save()
    finally:
        await client.disconnect()
