"""Real adapter, fake Telethon modules: no optional install or network required."""

import asyncio
import logging
import sys
from types import ModuleType, SimpleNamespace

import pytest

from app.services.social.telegram_login_provider import TelegramProviderFailure, telegram_login_step
from tests.shared.telegram_login import HASH, PHONE, SESSION


@pytest.fixture
def fake_telethon(monkeypatch):
    root, errors, sessions = (ModuleType(n) for n in ("telethon", "telethon.errors", "telethon.sessions"))
    for name in ("FloodWaitError", "PasswordHashInvalidError", "PhoneCodeInvalidError", "SessionPasswordNeededError"):
        setattr(errors, name, type(name, (Exception,), {}))
    state = SimpleNamespace(
        error=None, me=SimpleNamespace(id=90001, bot=False), authorized=True, disconnected=0, calls=[]
    )

    class FakeSession:
        def __init__(self, value):
            assert value in ("", SESSION)

        def save(self):
            return SESSION

    class Client:
        def __init__(self, session, api_id, api_hash, **kw):
            self.session = session
            assert api_id == 12345 and api_hash == HASH
            assert kw["request_retries"] == 0 and kw["connection_retries"] == 0
            assert kw["receive_updates"] is False
            assert kw["base_logger"].propagate is False
            assert kw["base_logger"].level > logging.CRITICAL

        async def connect(self):
            state.calls.append("connect")

        async def send_code_request(self, phone):
            assert phone == PHONE
            state.calls.append("send_code")
            return SimpleNamespace(phone_code_hash="SYNTHETIC-CODE-HASH")

        async def sign_in(self, **kwargs):
            state.calls.append("sign_in")
            if state.error:
                raise state.error("SYNTHETIC-PRIVATE-EXCEPTION")

        async def is_user_authorized(self):
            return state.authorized

        async def get_me(self):
            return state.me

        async def disconnect(self):
            state.disconnected += 1

    root.TelegramClient, sessions.StringSession = Client, FakeSession
    for module in (root, errors, sessions):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    state.errors = errors
    return state


def material():
    return dict(api_id=12345, api_hash=HASH, phone=PHONE, session=SESSION, phone_code_hash="SYNTHETIC-CODE-HASH")


async def test_begin_and_verified_identity_without_input(fake_telethon, monkeypatch):
    import builtins

    def forbidden(*args):
        raise AssertionError("no terminal input")

    monkeypatch.setattr(builtins, "input", forbidden)
    begin = await telegram_login_step("begin", material())
    assert begin.phase == "code" and begin.session == SESSION
    complete = await telegram_login_step("code", material(), "12345")
    assert complete.phase == "authorized" and complete.result.subject_id == 90001
    assert fake_telethon.disconnected == 2


@pytest.mark.parametrize(
    "error,operation,phase",
    [
        ("PhoneCodeInvalidError", "code", "invalid_code"),
        ("SessionPasswordNeededError", "code", "password"),
        ("PasswordHashInvalidError", "password", "invalid_password"),
    ],
)
async def test_bounded_retry_states(fake_telethon, error, operation, phase):
    fake_telethon.error = getattr(fake_telethon.errors, error)
    assert (await telegram_login_step(operation, material(), "12345")).phase == phase
    assert fake_telethon.disconnected == 1


@pytest.mark.parametrize(
    "error,code",
    [
        (RuntimeError, "provider_outcome_unknown"),
        (TimeoutError, "provider_outcome_unknown"),
        ("FloodWaitError", "provider_rate_limited"),
    ],
)
async def test_unknown_and_rate_limited_errors_are_redacted(fake_telethon, error, code, caplog):
    fake_telethon.error = getattr(fake_telethon.errors, error) if isinstance(error, str) else error
    with pytest.raises(TelegramProviderFailure) as caught:
        await telegram_login_step("code", material(), "12345")
    assert caught.value.code == code
    assert "SYNTHETIC-PRIVATE" not in str(caught.value) + caplog.text
    assert fake_telethon.disconnected == 1


@pytest.mark.parametrize(
    "me", [None, SimpleNamespace(id=0), SimpleNamespace(id=True), SimpleNamespace(id=12, bot=True)]
)
async def test_invalid_or_bot_identity_never_completes(fake_telethon, me):
    fake_telethon.me = me
    with pytest.raises(TelegramProviderFailure, match="authentication_failed"):
        await telegram_login_step("password", material(), "synthetic-password")


async def test_cancellation_disconnects_and_is_not_converted_to_retry(fake_telethon):
    fake_telethon.error = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await telegram_login_step("code", material(), "12345")
    assert fake_telethon.disconnected == 1
