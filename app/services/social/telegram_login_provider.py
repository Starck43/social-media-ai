"""Bounded Telethon adapter. No prompts, logging, retry, file session or secret import."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from app.services.social.connector_authorizations import VerifiedTelegramResult


class TelegramProviderFailure(RuntimeError):
    def __init__(self, code="provider_outcome_unknown"):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class LoginProgress:
    phase: str
    session: str = field(default="", repr=False)
    phone_code_hash: str = field(default="", repr=False)
    result: VerifiedTelegramResult | None = field(default=None, repr=False)


async def telegram_login_step(operation, material, secret=None):
    """Use only internal encrypted-attempt material; never reuse ambiguous requests.

    Telegram may have acted before a timeout/cancellation. The caller durably
    claims a step before invoking this adapter and never retries a processing step.
    """
    try:
        from telethon import TelegramClient
        from telethon.errors import (
            FloodWaitError,
            PasswordHashInvalidError,
            PhoneCodeInvalidError,
            SessionPasswordNeededError,
        )
        from telethon.sessions import StringSession
    except ImportError:
        raise TelegramProviderFailure("adapter_unavailable") from None

    # Disable library diagnostics entirely for this secret-bearing client. No
    # phone/code/password/request payload is allowed into application loggers.
    silent = logging.Logger("telegram.login.private", level=logging.CRITICAL + 1)
    silent.addHandler(logging.NullHandler())
    silent.propagate = False
    client = TelegramClient(
        StringSession(material["session"]),
        material["api_id"],
        material["api_hash"],
        request_retries=0,
        connection_retries=0,
        timeout=10,
        flood_sleep_threshold=0,
        receive_updates=False,
        base_logger=silent,
    )
    try:
        async with asyncio.timeout(20):
            await client.connect()
            if operation == "begin":
                sent = await client.send_code_request(material["phone"])
                return LoginProgress("code", client.session.save(), sent.phone_code_hash)
            if operation == "code":
                try:
                    await client.sign_in(
                        phone=material["phone"], code=secret, phone_code_hash=material["phone_code_hash"]
                    )
                except PhoneCodeInvalidError:
                    return LoginProgress("invalid_code", client.session.save())
                except SessionPasswordNeededError:
                    return LoginProgress("password", client.session.save())
            elif operation == "password":
                try:
                    await client.sign_in(password=secret)
                except PasswordHashInvalidError:
                    return LoginProgress("invalid_password", client.session.save())
            else:
                raise TelegramProviderFailure()
            if not await client.is_user_authorized():
                raise TelegramProviderFailure("authentication_failed")
            me = await client.get_me()
            if me is None or type(me.id) is not int or me.id <= 0 or getattr(me, "bot", False):
                raise TelegramProviderFailure("authentication_failed")
            result = VerifiedTelegramResult(
                subject_id=me.id,
                api_id=material["api_id"],
                api_hash=material["api_hash"],
                session=client.session.save(),
            )
            return LoginProgress("authorized", result=result)
    except asyncio.CancelledError:
        raise
    except TelegramProviderFailure:
        raise
    except FloodWaitError:
        raise TelegramProviderFailure("provider_rate_limited") from None
    except Exception:
        # Includes expired codes and unknown network outcomes. No exception text.
        raise TelegramProviderFailure() from None
    finally:
        try:
            async with asyncio.timeout(5):
                await client.disconnect()
        except Exception:
            raise TelegramProviderFailure() from None
