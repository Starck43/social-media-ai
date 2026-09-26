"""Shared helpers for `/app` HTML routes: rendering, flash messages, CSRF.

`render()` injects the UI context built by `TenantUIMiddleware` (user,
memberships, active workspace) plus a fresh CSRF token and pending flashes,
so templates never call into session internals.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/web/templates")


def add_flash(request: Request, kind: str, text: str) -> None:
    """Queue a one-shot message (kinds: success | error | info)."""
    flashes = request.session.get("_flashes", [])
    flashes.append({"kind": kind, "text": text})
    request.session["_flashes"] = flashes


def pop_flashes(request: Request) -> list[dict[str, str]]:
    flashes = request.session.get("_flashes", [])
    if flashes:
        request.session["_flashes"] = []
    return flashes


def csrf_token(request: Request) -> str:
    return request.app.state.csrf_manager.generate_token()


def ensure_csrf(request: Request, token: str | None) -> bool:
    manager = getattr(request.app.state, "csrf_manager", None)
    return manager is not None and manager.verify_token(token or "")


def safe_next(raw: str | None) -> str | None:
    """Only same-site /app paths survive, so `?next=` cannot become an open redirect."""
    if raw and raw.startswith("/app") and not raw.startswith("//"):
        return raw
    return None


def render(request: Request, name: str, status_code: int = 200, **extra: Any):
    context: dict[str, Any] = {
        "user": getattr(request.state, "web_user", None),
        "memberships": getattr(request.state, "memberships", []) or [],
        "workspaces": getattr(request.state, "workspaces", []) or [],
        "tenant": getattr(request.state, "tenant", None),
        "csrf": csrf_token(request),
        "flashes": pop_flashes(request),
        "path": request.url.path,
    }
    context.update(extra)
    return templates.TemplateResponse(request=request, name=name, context=context, status_code=status_code)
