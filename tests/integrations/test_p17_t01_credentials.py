"""Offline Fernet bundle tests; ephemeral synthetic credentials and key only."""

import json

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.integrations.onec.credentials import OneCCredentialError, open_basic_credentials, seal_basic_credentials
from app.utils.crypto import encrypt_secret


@pytest.fixture
def isolated_key(monkeypatch):
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", Fernet.generate_key().decode())


def test_round_trip_encrypts_all_credentials_and_redacts_repr(isolated_key):
    encrypted = seal_basic_credentials("synthetic-user", "synthetic-password")
    assert "synthetic-user" not in encrypted and "synthetic-password" not in encrypted
    credentials = open_basic_credentials(encrypted)
    assert (credentials.login, credentials.password) == ("synthetic-user", "synthetic-password")
    assert "synthetic" not in repr(credentials)


@pytest.mark.parametrize(
    ("login", "password"),
    [("", "p"), ("u", ""), ("u:x", "p"), ("u\n", "p"), ("u", "p\n"), ("u" * 321, "p"), ("u", "p" * 4097)],
)
def test_invalid_inputs_are_rejected_without_leaking_values(isolated_key, login, password):
    with pytest.raises(OneCCredentialError) as exc:
        seal_basic_credentials(login, password)
    assert exc.value.code == "invalid_credentials"


@pytest.mark.parametrize("payload", [{"version": 2, "login": "x", "password": "y"}, {"version": True}, [], {}])
def test_unknown_and_malformed_bundles_fail_closed(isolated_key, payload):
    encrypted = encrypt_secret(json.dumps(payload))
    with pytest.raises(OneCCredentialError, match="secret_unavailable"):
        open_basic_credentials(encrypted)


def test_missing_key_and_unreadable_ciphertext_fail_closed(isolated_key, monkeypatch):
    with pytest.raises(OneCCredentialError, match="secret_unavailable"):
        open_basic_credentials("not-a-token")
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", None)
    with pytest.raises(OneCCredentialError, match="secret_unavailable"):
        seal_basic_credentials("u", "p")
