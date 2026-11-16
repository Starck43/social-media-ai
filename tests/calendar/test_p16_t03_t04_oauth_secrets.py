"""Google OAuth client credentials stay redacted until the token exchange boundary."""

import json

from pydantic import SecretStr

from app.core.config import settings
from app.integrations.oauth.service import OAuthFlowError, resolve_client_credentials, resolve_oauth_method


def test_google_calendar_client_secret_is_absent_from_settings_diagnostics(monkeypatch):
    secret = "calendar-client-secret-regression-marker"
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_CLIENT_ID", "calendar-client-id")
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_CLIENT_SECRET", SecretStr(secret))

    diagnostic = json.dumps(settings.safe_dict(), sort_keys=True)
    assert secret not in diagnostic
    assert secret not in repr(settings)
    assert "GOOGLE_CALENDAR_CLIENT_SECRET" not in diagnostic


def test_google_calendar_resolver_unwraps_secret_only_for_oauth_exchange(monkeypatch):
    secret = "calendar-client-secret-exchange-marker"
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_CLIENT_ID", "calendar-client-id")
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_CLIENT_SECRET", SecretStr(secret))

    method = resolve_oauth_method("google_calendar", "oauth2")

    assert resolve_client_credentials(method, "google_calendar") == ("calendar-client-id", secret)
    assert secret not in json.dumps(settings.safe_dict(), sort_keys=True)


def test_google_calendar_resolver_fails_closed_without_client_credentials(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_CLIENT_ID", "")
    monkeypatch.setattr(settings, "GOOGLE_CALENDAR_CLIENT_SECRET", SecretStr(""))

    try:
        resolve_client_credentials(resolve_oauth_method("google_calendar", "oauth2"), "google_calendar")
    except OAuthFlowError as exc:
        assert exc.code == "oauth_client_not_configured"
    else:
        raise AssertionError("missing Google OAuth credentials must fail closed")
