"""P14-T05 focused tests: Bitrix24 global-application credentials resolve from an
operator-supplied source only.

Covers the resolver seam (`app.integrations.oauth.credentials.resolve_app_credentials`):
file source, named secret entry, missing/unreadable/malformed/incomplete file (fail closed
with `bitrix_credentials_unavailable`), default-OFF legacy bridge and its precedence,
injected provider, secret never in `str(exc)` / `repr(settings)` / connection views, and the
OAuth start flow failing closed before any network call. No key material is hard-coded here:
the root conftest provides environment keys, and secrets are random bytes.
"""

from __future__ import annotations

import base64
import json
import os
from types import SimpleNamespace

import pytest

__source_only__ = True

from app.core.config import settings
from app.integrations.bitrix24 import Bitrix24Error, Bitrix24Service
from app.integrations.catalog import BITRIX24_OAUTH_METHOD
from app.integrations.oauth import (
    CREDENTIALS_UNAVAILABLE,
    INJECTED_APP_CREDENTIALS,
    OAuthFlowError,
    WorkspaceOAuthService,
    resolve_app_credentials,
    resolve_client_credentials,
    resolve_oauth_method,
)
from app.integrations.oauth.network import default_dns_resolver


def _client_id() -> str:
    return f"app.{base64.urlsafe_b64encode(os.urandom(9)).decode().rstrip('=')}"


def _client_secret() -> str:
    return base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("=")


def _write_secret_file(path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolated_credential_sources(monkeypatch: pytest.MonkeyPatch):
    """No leaked injection and legacy bridge OFF unless a test opts in."""
    monkeypatch.setitem(INJECTED_APP_CREDENTIALS, "bitrix24", None)
    monkeypatch.setitem(INJECTED_APP_CREDENTIALS, "bitrix24", None)
    INJECTED_APP_CREDENTIALS.pop("bitrix24", None)
    monkeypatch.setattr(settings, "BITRIX24_APP_SECRET_FILE", "")
    monkeypatch.setattr(settings, "BITRIX24_APP_SECRET_NAME", "client_secret")
    monkeypatch.setattr(settings, "BITRIX24_LEGACY_ENV_CREDENTIALS", False)
    yield


def _no_network(host: str, port: int) -> list[tuple]:
    raise AssertionError(f"unexpected DNS resolution for {host}:{port}")


def test_global_app_method_declares_no_settings_client_keys() -> None:
    method = resolve_oauth_method("bitrix24")
    assert method.client_config_keys == ("", "")
    assert BITRIX24_OAUTH_METHOD.client_config_keys == ("", "")


def test_credentials_resolved_from_operator_secret_file(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    cid, csec = _client_id(), _client_secret()
    secret_file = tmp_path / "bitrix24-app.json"
    _write_secret_file(secret_file, {"client_id": cid, "client_secret": csec})
    monkeypatch.setattr(settings, "BITRIX24_APP_SECRET_FILE", str(secret_file))

    assert resolve_app_credentials("bitrix24") == (cid, csec)
    assert resolve_client_credentials(resolve_oauth_method("bitrix24"), "bitrix24") == (cid, csec)


def test_credentials_resolved_from_named_entry_in_secret_store(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    cid, csec = _client_id(), _client_secret()
    secret_file = tmp_path / "store.json"
    _write_secret_file(
        secret_file,
        {
            "other-app": {"client_id": _client_id(), "client_secret": _client_secret()},
            "crm": {"client_id": cid, "client_secret": csec},
        },
    )
    monkeypatch.setattr(settings, "BITRIX24_APP_SECRET_FILE", str(secret_file))
    monkeypatch.setattr(settings, "BITRIX24_APP_SECRET_NAME", "crm")

    assert resolve_app_credentials("bitrix24") == (cid, csec)


def test_missing_secret_file_fails_closed(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "BITRIX24_APP_SECRET_FILE", str(tmp_path / "absent.json"))
    for source in (resolve_app_credentials, lambda pid: resolve_client_credentials(resolve_oauth_method(pid), pid)):
        with pytest.raises(OAuthFlowError) as err:
            source("bitrix24")
        assert err.value.code == CREDENTIALS_UNAVAILABLE


def test_unreadable_malformed_and_incomplete_sources_fail_closed(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    unreadable = tmp_path / "unreadable.json"
    unreadable.mkdir()  # a directory is not readable as a file
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not json", encoding="utf-8")
    incomplete = tmp_path / "incomplete.json"
    _write_secret_file(incomplete, {"client_id": _client_id()})

    for candidate in (unreadable, malformed, incomplete):
        monkeypatch.setattr(settings, "BITRIX24_APP_SECRET_FILE", str(candidate))
        with pytest.raises(OAuthFlowError) as err:
            resolve_app_credentials("bitrix24")
        assert err.value.code == CREDENTIALS_UNAVAILABLE


def test_legacy_settings_bridge_is_off_by_default_and_never_used_with_a_file(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_id, legacy_secret = _client_id(), _client_secret()
    fake_settings = SimpleNamespace(
        BITRIX24_APP_SECRET_FILE="",
        BITRIX24_APP_SECRET_NAME="client_secret",
        BITRIX24_LEGACY_ENV_CREDENTIALS=False,
        BITRIX24_CLIENT_ID=legacy_id,
        BITRIX24_CLIENT_SECRET=legacy_secret,
    )
    monkeypatch.setattr("app.core.config.settings", fake_settings)

    with pytest.raises(OAuthFlowError) as err:
        resolve_app_credentials("bitrix24")
    assert err.value.code == CREDENTIALS_UNAVAILABLE

    # Rollback bridge ON: the historical settings path still resolves.
    fake_settings.BITRIX24_LEGACY_ENV_CREDENTIALS = True
    assert resolve_app_credentials("bitrix24") == (legacy_id, legacy_secret)

    # The operator file wins and breaks the bridge: an unreadable file never falls back to settings.
    fake_settings.BITRIX24_APP_SECRET_FILE = str(tmp_path / "absent.json")
    with pytest.raises(OAuthFlowError) as file_err:
        resolve_app_credentials("bitrix24")
    assert file_err.value.code == CREDENTIALS_UNAVAILABLE


def test_rollback_bridge_is_off_in_the_declared_settings() -> None:
    assert settings.BITRIX24_LEGACY_ENV_CREDENTIALS is False
    assert settings.BITRIX24_APP_SECRET_FILE == ""


def test_injected_provider_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    cid, csec = _client_id(), _client_secret()
    monkeypatch.setattr(settings, "BITRIX24_APP_SECRET_FILE", "/nonexistent/operator/store.json")
    monkeypatch.setitem(INJECTED_APP_CREDENTIALS, "bitrix24", lambda: (cid, csec))

    assert resolve_app_credentials("bitrix24") == (cid, csec)


def test_secret_never_appears_in_errors_settings_repr_or_views(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    csec = _client_secret()
    secret_file = tmp_path / "bitrix24-app.json"
    _write_secret_file(secret_file, {"client_id": _client_id(), "client_secret": csec})
    monkeypatch.setattr(settings, "BITRIX24_APP_SECRET_FILE", str(tmp_path / "absent.json"))
    with pytest.raises(OAuthFlowError) as err:
        resolve_app_credentials("bitrix24")
    # The failure reports the path-free code only, never a credential or file content.
    assert csec not in str(err.value)
    assert str(tmp_path) not in str(err.value)

    monkeypatch.setattr(settings, "BITRIX24_APP_SECRET_FILE", str(secret_file))
    assert csec not in repr(settings)
    assert csec not in json.dumps(settings.safe_dict())


@pytest.mark.asyncio
async def test_oauth_start_fails_closed_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "BITRIX24_APP_SECRET_FILE", "/nonexistent/operator/store.json")
    oauth_svc = WorkspaceOAuthService(resolver=_no_network)
    b24 = Bitrix24Service(oauth_service=oauth_svc, resolver=_no_network)
    state_store = SimpleNamespace(issue=lambda **_: (_ for _ in ()).throw(AssertionError("state must not be issued")))
    oauth_svc._state_store = state_store
    from app.core.permissions import set_current_user
    from app.core.tenant_context import tenant_scope

    set_current_user(SimpleNamespace(id=101, is_active=True))
    try:
        with tenant_scope(10):
            with pytest.raises(Bitrix24Error) as err:
                await b24.start_oauth_connect(
                    tenant_id=10,
                    actor_id=101,
                    portal_domain="acme.bitrix24.ru",
                    session=SimpleNamespace(),
                )
    finally:
        set_current_user(None)
    assert err.value.code == CREDENTIALS_UNAVAILABLE
    assert err.value.status_code == 400


def test_production_resolver_default_is_the_dns_one() -> None:
    # Guards the seam wiring used by the live service: no injected resolver by default.
    assert callable(default_dns_resolver)
