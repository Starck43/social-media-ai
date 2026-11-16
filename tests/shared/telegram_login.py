"""Fake login provider, usable only with synthetic connector fixtures."""

from uuid import uuid4

import pytest

from app.core.config import settings
from app.services.social import telegram_login as service
from app.services.social.connector_authorizations import VerifiedTelegramResult
from app.services.social.telegram_login_provider import LoginProgress

PHONE = "+15555550123"
SESSION = "SYNTHETIC-SESSION-NEVER-REAL"
HASH = "f" * 32


@pytest.fixture
def login_provider(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_API_ID", "12345")
    monkeypatch.setattr(settings, "TELEGRAM_API_HASH", HASH)
    calls = []

    async def fake(operation, material, secret=None):
        calls.append(operation)  # Never retain plaintext inputs in diagnostics.
        if operation == "begin":
            return LoginProgress("code", SESSION, "SYNTHETIC-CODE-HASH")
        return LoginProgress("authorized", result=VerifiedTelegramResult(90001, 12345, HASH, SESSION))

    monkeypatch.setattr(service, "telegram_login_step", fake)
    return calls


async def start_login(args):
    return await service.telegram_logins.start(**args, request_id=uuid4(), phone=PHONE)
