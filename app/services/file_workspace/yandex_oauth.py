"""Start and complete a personal Yandex Disk connection after explicit OAuth consent."""

from __future__ import annotations

from urllib.parse import urlencode, urlsplit

from app.core.config import settings
from app.core.database import async_session_maker
from app.integrations.file_workspace.yandex_http import exchange_yandex_code
from app.integrations.file_workspace.yandex_tls import OAUTH_HOST
from app.integrations.oauth.state import OAuthStateError, oauth_state_store

CALLBACK_PATH = "/app/settings/yandex-connections/oauth/callback"
_DISK_SCOPE = "cloud_api:disk.read"
_PROVIDER = "yandex_disk"


class YandexOAuthError(Exception):
    """Sanitized result for a setup failure."""


def _redirect_uri() -> str | None:
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    try:
        url = urlsplit(base)
        port = url.port
    except ValueError:
        return None
    if (
        not url.hostname
        or url.username is not None
        or url.password is not None
        or url.path
        or "?" in base
        or "#" in base
        or any(char.isspace() or ord(char) < 32 or char == "\\" for char in base)
        or port == 0
    ):
        return None
    # For local development, Yandex redirect registration can use loopback HTTP.
    if url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}):
        return None
    return base + CALLBACK_PATH


def oauth_configured() -> bool:
    return bool(
        settings.YANDEX_DISK_CLIENT_ID and settings.YANDEX_DISK_CLIENT_SECRET.get_secret_value() and _redirect_uri()
    )


async def _authorize_setup(tenant_id: int, actor_id: int) -> None:
    from app.models.managers.workspace_connection_manager import _authorize

    async with async_session_maker() as session, session.begin():
        await _authorize(session, tenant_id, actor_id, write=True, require_source_view=True)


async def start_yandex_oauth(*, tenant_id: int, actor_id: int, session_id: str) -> str:
    redirect_uri = _redirect_uri()
    if not oauth_configured() or redirect_uri is None:
        raise YandexOAuthError("not_configured")
    await _authorize_setup(tenant_id, actor_id)
    pending = oauth_state_store.issue(
        tenant_id=tenant_id,
        actor_user_id=actor_id,
        provider_id=_PROVIDER,
        connection_id=None,
        redirect_uri=redirect_uri,
        browser_session_id=session_id,
        setup=("", "/"),
    )
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.YANDEX_DISK_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "scope": _DISK_SCOPE,
            "force_confirm": "yes",
            "state": pending.state_token,
            "code_challenge": pending.code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"https://{OAUTH_HOST}/authorize?{query}"


async def complete_yandex_oauth(
    *, tenant_id: int, actor_id: int, session_id: str, state: str, code: str, cancelled: bool
) -> str:
    from .connections import validate_yandex_token
    from .folder_picker import PendingFolder, issue

    try:
        pending = oauth_state_store.consume(
            state, tenant_id=tenant_id, actor_user_id=actor_id, provider_id=_PROVIDER, browser_session_id=session_id
        )
    except OAuthStateError:
        raise YandexOAuthError("invalid_state") from None
    if cancelled:
        raise YandexOAuthError("cancelled")
    if (
        not oauth_configured()
        or pending.redirect_uri != _redirect_uri()
        or not pending.code_verifier
        or not pending.setup
    ):
        raise YandexOAuthError("invalid_state")
    if not 1 <= len(code) <= 2048 or any(not 33 <= ord(char) <= 126 for char in code):
        raise YandexOAuthError("invalid_code")
    await _authorize_setup(tenant_id, actor_id)

    response = await exchange_yandex_code(
        code=code,
        verifier=pending.code_verifier,
        client_id=settings.YANDEX_DISK_CLIENT_ID,
        client_secret=settings.YANDEX_DISK_CLIENT_SECRET.get_secret_value(),
    )
    # Yandex omits scope when the full requested set was granted.
    scope = response.get("scope", _DISK_SCOPE)
    if not isinstance(scope, str) or set(scope.split()) != {_DISK_SCOPE}:
        raise YandexOAuthError("unexpected_scope")
    token = validate_yandex_token(response.get("access_token"))
    return issue(
        PendingFolder(f"yandex-browse:{session_id}:{actor_id}:{tenant_id}", _PROVIDER, token, pending.expires_at)
    )
