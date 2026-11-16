"""No-I/O validation of the bounded capability contract."""

import pytest

from app.services.actions.telegram_message import _outcome, released, valid_destination, valid_payload, valid_text

__source_only__ = True


@pytest.mark.parametrize("flag", [None, "0", "true", "True", "yes", " 1", "1 "])
def test_master_switch_is_exact_and_default_off(monkeypatch, flag):
    monkeypatch.setenv("P9_TELEGRAM_MESSAGE_BINDINGS", "12:34")
    if flag is None:
        monkeypatch.delenv("P9_TELEGRAM_MESSAGE_ENABLED", raising=False)
    else:
        monkeypatch.setenv("P9_TELEGRAM_MESSAGE_ENABLED", flag)
    assert not released(12, 34)


@pytest.mark.parametrize("value", ["", "*", "12:*", "12:34,", "12:34,bad", "12:34, 15:1", "012:34", "12:034", "-1:34"])
def test_allowlist_malformed_values_fail_closed(monkeypatch, value):
    monkeypatch.setenv("P9_TELEGRAM_MESSAGE_ENABLED", "1")
    monkeypatch.setenv("P9_TELEGRAM_MESSAGE_BINDINGS", value)
    assert not released(12, 34)


def test_release_does_not_leak_between_workspaces_or_bindings(monkeypatch):
    monkeypatch.setenv("P9_TELEGRAM_MESSAGE_ENABLED", "1")
    monkeypatch.setenv("P9_TELEGRAM_MESSAGE_BINDINGS", "12:34,15:2")
    assert released(12, 34) and released(15, 2)
    assert not released(12, 2) and not released(15, 34)


@pytest.mark.parametrize(
    "value", [None, True, 123, "@channel", "123", "-123", " -100123", "-1000123", "-100123/1", "-10099999999999999"]
)
def test_only_canonical_numeric_channel_destinations(value):
    assert not valid_destination(value)


def test_text_bound_is_utf16_not_python_codepoints():
    assert valid_text("a" * 4096) and valid_text("😀" * 2048)
    assert not valid_text("a" * 4097) and not valid_text("😀" * 2049)
    assert not valid_text("\ud800") and not valid_text("\x00") and not valid_text("  ")


def test_payload_cannot_override_credentials_destination_or_transport():
    payload = {"binding_id": 1, "destination_id": "-100123", "text": "reviewed", "transport_fingerprint": "a" * 64}
    assert valid_payload(payload)
    for key, value in [("token", "forbidden"), ("parse_mode", "HTML"), ("user_id", 7), ("dry_run", False)]:
        assert not valid_payload({**payload, key: value})
    assert not valid_payload({**payload, "binding_id": True})
    assert not valid_payload({**payload, "transport_fingerprint": ""})


@pytest.mark.parametrize(
    "value", [None, [], {}, {"outcome": []}, {"outcome": "sent"}, {"outcome": "sent", "message_id": " 1"}]
)
def test_unconfirmed_provider_results_are_always_unknown(value):
    assert _outcome(value) == ("uncertain", None)
