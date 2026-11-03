"""Unit tests for P13-T02 personal-data helpers: encryption, blind index, redaction.

No database and no paid provider: keys are generated per test and injected with
monkeypatch, so a developer's real `.env` keys never participate.
"""

from __future__ import annotations

import base64
import logging

import pytest
from cryptography.fernet import Fernet

from app.core import pii
from app.core.config import settings
from app.core.settings_views import SENSITIVE_FIELDS, SecuritySettingsView


def _keys(*, count: int = 1) -> list[str]:
    return [Fernet.generate_key().decode() for _ in range(count)]


def _configure(
    monkeypatch, keys: list[str] | None = None, index_key: str | None = None, credentials: str | None = None
):
    keys = keys if keys is not None else _keys()
    index_key = index_key if index_key is not None else base64.urlsafe_b64encode(b"k" * 32).decode()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", ",".join(keys), raising=False)
    monkeypatch.setattr(settings, "PII_INDEX_KEY", index_key, raising=False)
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", credentials, raising=False)
    return keys


def test_round_trip_and_ciphertext_shape(monkeypatch):
    keys = _configure(monkeypatch)
    value = pii.encrypt_pii("anna@example.com")
    assert value.startswith(pii.ENCRYPTION_PREFIX)
    assert "anna" not in value and "example.com" not in value
    assert pii.decrypt_pii(value) == "anna@example.com"
    assert keys[0].encode() not in value.encode()


def test_empty_and_none_are_not_encrypted(monkeypatch):
    _configure(monkeypatch)
    assert pii.encrypt_pii("") == ""
    assert pii.decrypt_pii(None) is None
    assert pii.decrypt_pii("") is None


def test_rotation_old_key_still_decrypts_and_new_key_writes(monkeypatch):
    old, new = _keys(count=2)
    _configure(monkeypatch, [old])
    legacy = pii.encrypt_pii("+79991234567")
    assert pii.is_active_ciphertext(legacy)

    _configure(monkeypatch, [new, old])
    assert pii.decrypt_pii(legacy) == "+79991234567"
    assert not pii.is_active_ciphertext(legacy)
    fresh = pii.encrypt_pii("+79991234567")
    assert pii.is_active_ciphertext(fresh)
    assert pii.decrypt_pii(fresh) == "+79991234567"


def test_wrong_or_missing_key_fails_closed(monkeypatch):
    _configure(monkeypatch)
    token = pii.encrypt_pii("secret")
    _configure(monkeypatch, _keys())
    with pytest.raises(pii.PIIError, match="no configured PII key"):
        pii.decrypt_pii(token)

    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", "", raising=False)
    with pytest.raises(pii.PIIError, match="PII_ENCRYPTION_KEYS is not set"):
        pii.encrypt_pii("secret")


def test_invalid_key_and_credential_reuse_are_refused(monkeypatch):
    _configure(monkeypatch, ["not-a-fernet-key"])
    with pytest.raises(pii.PIIError, match="not a valid Fernet key"):
        pii.encrypt_pii("x")

    shared = _keys()[0]
    _configure(monkeypatch, [shared], credentials=shared)
    with pytest.raises(pii.PIIError, match="must not reuse CREDENTIALS_KEY"):
        pii.encrypt_pii("x")


def test_plaintext_value_is_not_interpreted_as_ciphertext(monkeypatch):
    _configure(monkeypatch)
    with pytest.raises(pii.PIIError, match="not a PII ciphertext"):
        pii.decrypt_pii("anna@example.com")


def test_index_key_validation(monkeypatch):
    _configure(monkeypatch, index_key="")
    with pytest.raises(pii.PIIError, match="PII_INDEX_KEY is not set"):
        pii.blind_index("email", "a@b.c")
    _configure(monkeypatch, index_key=base64.urlsafe_b64encode(b"short").decode())
    with pytest.raises(pii.PIIError, match="at least 32 bytes"):
        pii.blind_index("email", "a@b.c")


def test_blind_index_is_stable_normalized_and_kind_separated(monkeypatch):
    _configure(monkeypatch)
    assert pii.blind_index("email", " Anna@Example.COM ") == pii.blind_index("email", "anna@example.com")
    assert pii.blind_index("phone", "+7 (999) 123-45-67") == pii.blind_index("phone", "89991234567")
    assert pii.blind_index("phone", "8 999 123 45 67") == pii.blind_index("phone", "+79991234567")
    assert pii.blind_index("username", "@Anna") == pii.blind_index("username", "anna")
    assert pii.blind_index("email", "anna@example.com") != pii.blind_index("username", "anna@example.com")
    assert pii.is_blind_index(pii.blind_index("email", "anna@example.com"))


def test_index_changes_with_key_and_never_contains_value(monkeypatch):
    _configure(monkeypatch, index_key=base64.urlsafe_b64encode(b"a" * 32).decode())
    first = pii.blind_index("email", "anna@example.com")
    _configure(monkeypatch, index_key=base64.urlsafe_b64encode(b"b" * 32).decode())
    assert pii.blind_index("email", "anna@example.com") != first
    assert "anna" not in first and "example" not in first


def test_normalizers_reject_foreign_input(monkeypatch):
    _configure(monkeypatch)
    with pytest.raises(pii.PIIError):
        pii.normalize_email("anna")
    with pytest.raises(pii.PIIError, match="not supported"):
        pii.normalize_phone("+15551234567", region="US")
    with pytest.raises(pii.PIIError):
        pii.blind_index("card", "1234")
    with pytest.raises(pii.PIIError, match="empty"):
        pii.blind_index("email", "   ")


def test_redaction_removes_personal_values():
    text = "contact anna@example.com or +7 (999) 123-45-67, task 2027 and 12:30 stay"
    redacted = pii.redact_pii(text)
    assert "anna@example.com" not in redacted and "123-45-67" not in redacted
    assert "2027" in redacted and "12:30" in redacted


def test_redaction_filter_keeps_logs_clean(caplog):
    class _Filter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            record.msg = pii.redact_pii(str(record.msg))
            if isinstance(record.args, tuple):
                record.args = tuple(pii.redact_pii(str(a)) for a in record.args)
            return True

    logger = logging.getLogger("p13_t02_probe")
    logger.addFilter(_Filter())
    caplog.set_level(logging.INFO, logger="p13_t02_probe")
    try:
        logger.info("sending to %s", "anna@example.com")
    finally:
        logger.filters.clear()
    assert "anna@example.com" not in caplog.text
    assert "sending to" in caplog.text


def test_keys_are_sensitive_in_settings_views(monkeypatch):
    keys = _configure(monkeypatch)
    for name in ("PII_ENCRYPTION_KEYS", "PII_INDEX_KEY"):
        assert name in SENSITIVE_FIELDS
        assert name in SecuritySettingsView.model_fields
    monkeypatch.setattr(settings, "SECRET_KEY", "x" * 32, raising=False)
    monkeypatch.setattr(settings, "ALGORITHM", "HS256", raising=False)
    monkeypatch.setattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 30, raising=False)
    monkeypatch.setattr(settings, "REFRESH_TOKEN_EXPIRE_DAYS", 7, raising=False)
    monkeypatch.setattr(settings, "PASSWORD_MIN_LENGTH", 8, raising=False)
    for flag in (
        "PASSWORD_REQUIRE_UPPERCASE",
        "PASSWORD_REQUIRE_LOWERCASE",
        "PASSWORD_REQUIRE_NUMBERS",
        "PASSWORD_REQUIRE_SPECIAL",
    ):
        monkeypatch.setattr(settings, flag, True, raising=False)
    view = SecuritySettingsView.from_settings(settings)
    assert keys[0] not in repr(view)
