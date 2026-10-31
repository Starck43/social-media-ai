"""Signed, expiring, session-bound CSRF tokens for the console and the web UI.

One `CSRFTokenManager` lives on `app.state.csrf_manager` (created in
`app/main.py`, reused by `app/admin/setup.py`). Tokens are signed payloads with
a bounded lifetime; verification is constant-time via itsdangerous.

Console tokens are bound to the issuing session: `issue_token` stamps the
session's random CSRF id into the payload and `verify_request_token` refuses a
token minted for another session. `AdminCSRFMiddleware` is the single
console-wide verification point for every mutating `/admin/*` request; the web
UI keeps its own per-request verification in `app/web/deps.py`.
"""

import secrets
from datetime import datetime, timezone

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.responses import PlainTextResponse

#: Session key carrying the random id every console CSRF token is bound to.
SESSION_ID_KEY = "csrf_session_id"

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_FORM_CONTENT_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")


class CSRFTokenManager:
    """CSRF token manager with expiration and optional session binding."""

    def __init__(self, secret_key: str, expire_minutes: int = 30):
        self.serializer = URLSafeTimedSerializer(secret_key, salt="csrf-token")
        self.expire_seconds = expire_minutes * 60

    def generate_token(self, session_id: str | None = None) -> str:
        payload = {"csrf": True, "ts": datetime.now(timezone.utc).isoformat(), "sid": session_id}
        return self.serializer.dumps(payload, salt="csrf-token")

    def verify_token(self, token: str, session_id: str | None = None) -> bool:
        if not token:
            return False
        try:
            payload = self.serializer.loads(
                token,
                salt="csrf-token",
                max_age=self.expire_seconds,
            )
        except (BadSignature, SignatureExpired):
            return False
        if not isinstance(payload, dict) or payload.get("csrf") is not True:
            return False
        # A session-bound token only verifies for the session it was issued to.
        if session_id is not None and payload.get("sid") != session_id:
            return False
        return True


def get_csrf_manager(request: Request) -> CSRFTokenManager:
    csrf_manager = getattr(request.app.state, "csrf_manager", None)
    if not csrf_manager:
        raise HTTPException(500, "CSRF protection not configured")
    return csrf_manager


def ensure_session_id(request: Request) -> str:
    """Return the session's CSRF id, minting one on first use."""
    session_id = request.session.get(SESSION_ID_KEY)
    if not session_id:
        session_id = secrets.token_urlsafe(16)
        request.session[SESSION_ID_KEY] = session_id
    return session_id


def rotate_session_id(request: Request) -> str:
    """Mint a fresh CSRF id at login so pre-login tokens stop verifying."""
    session_id = secrets.token_urlsafe(16)
    request.session[SESSION_ID_KEY] = session_id
    return session_id


def issue_token(request: Request) -> str:
    """Mint a token bound to the requesting session."""
    return get_csrf_manager(request).generate_token(session_id=ensure_session_id(request))


def verify_request_token(request: Request, token: str | None) -> bool:
    """Verify a token against the session it claims to come from."""
    session_id = request.session.get(SESSION_ID_KEY)
    if not session_id or not token:
        return False
    return get_csrf_manager(request).verify_token(token, session_id=session_id)


class AdminCSRFMiddleware:
    """Single console-wide CSRF verification point.

    Every mutating `/admin/*` request must carry a valid session-bound token
    in the `csrf_token` form field or the `X-CSRF-Token` header; anything else
    is refused with 403 before it reaches a route. Read-only methods and
    non-console paths pass through untouched. Registered inside
    `SessionMiddleware` so the signed session is available.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in MUTATING_METHODS:
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        if not request.url.path.startswith("/admin"):
            await self.app(scope, receive, send)
            return
        # The login POST is verified by `AdminAuthBackend.login` itself, which
        # answers a bad token with a form message instead of a bare 403.
        if request.url.path == "/admin/login":
            await self.app(scope, receive, send)
            return

        token = request.headers.get("X-CSRF-Token")
        if token is None and request.headers.get("content-type", "").split(";")[0].strip() in _FORM_CONTENT_TYPES:
            # Cache the body *before* parsing: `form()` drains the stream and
            # Starlette refuses a second read ("Stream consumed"), which would
            # 500 every console form POST.
            body = await request.body()
            form = await request.form()
            value = form.get("csrf_token")
            token = value if isinstance(value, str) else None
            # This middleware consumed the receive channel; replay the cached
            # body so the route handler parses the same payload.
            body_replayed = False

            async def replay_receive():
                nonlocal body_replayed
                if not body_replayed:
                    body_replayed = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            receive = replay_receive

        if not verify_request_token(request, token):
            response = PlainTextResponse("Invalid or missing CSRF token", status_code=403)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
