"""Shared helpers for `/app` HTML routes: rendering, flash messages, CSRF.

`render()` injects the UI context built by `TenantUIMiddleware` (user,
memberships, active workspace) plus a fresh CSRF token and pending flashes,
so templates never call into session internals.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.models import Tenant
from app.types import ActionType

from .nav import MOBILE_NAV_ITEMS, NAV_ITEMS

templates = Jinja2Templates(directory="app/web/templates")


def plural(n: int, one: str, few: str, many: str) -> str:
    """Russian count form: 1 запись / 2 записи / 5 записей.

    Lives here rather than in one page because every count in the UI needs it,
    and a hand-written conditional in a template gets it wrong the same way
    every time (there is no simple `n == 1` rule in Russian).
    """
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def human_datetime(value: Any, *, empty: str = "—") -> str:
    """Render a timestamp the way a person reads it: `сегодня, 14:30`.

    The pages had five hand-written `strftime` formats between them — a task
    list showing `01.10.2026 09:00` next to a digest list showing `01.10 09:00`
    for the same kind of event. One filter keeps them comparable, and "сегодня"
    answers the only question a reader has about a recent run.

    Accepts `datetime`, `date` and `None`; naive values are read as UTC so the
    output does not depend on the server's local zone.
    """
    if value is None:
        return empty
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        today = datetime.now(timezone.utc).date()
        if moment.date() == today:
            return f"сегодня, {moment:%H:%M}"
        if moment.date() == today - timedelta(days=1):
            return f"вчера, {moment:%H:%M}"
        return moment.strftime("%d.%m.%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    return str(value)


templates.env.filters["human_dt"] = human_datetime


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


async def tenant_filter_context(request: Request, is_superuser: bool) -> tuple[int | None, list]:
    """Resolve the superuser tenant filter (`?tenant_id=`) + tenant list.

    Returns `(filter_tenant_id, tenants)`. A regular user gets `(None, [])` —
    they always see their own tenant. A superuser may narrow to one tenant;
    `tenants` powers the dropdown.
    """
    if not is_superuser:
        return None, []

    raw = request.query_params.get("tenant_id")
    if raw is not None:
        # Explicit ?tenant_id= wins, including "" which clears the filter (all).
        filter_tenant_id = int(raw) if raw.isdigit() else None
    else:
        # No filter given → default to the current workspace, so the superuser
        # sees their own space's data first, not a global mix.
        filter_tenant_id = getattr(request.state, "tenant_id", None)

    # Tenants are global (no `TenantScopedMixin`), so the manager needs no
    # tenant context here — the dropdown must list every workspace.
    tenants = list(await Tenant.objects.order_by(Tenant.name))
    return filter_tenant_id, tenants


def action_tenant_id(request: Request, posted_tenant_id: int | None = None) -> int | None:
    """Resolve the workspace a superuser action should act on.

    A superuser's active workspace (`request.state.tenant_id`) is independent of
    the `?tenant_id=` list filter. POST forms carry the selected tenant as a
    hidden field so that create/toggle/run/delete act on the workspace the user
    is *viewing*, not the one they happen to have active. A regular user always
    acts on their own active workspace.
    """
    user = getattr(request.state, "web_user", None)
    if user is not None and user.is_superuser and posted_tenant_id is not None:
        return posted_tenant_id
    return getattr(request.state, "tenant_id", None)


def guard_web(
    request: Request,
    model_name: str,
    action: ActionType | str,
    *,
    back: str,
    reason: str = "Недостаточно прав для этого действия",
) -> RedirectResponse | None:
    """Gate a mutation: None when the caller may act, else flash + redirect to `back`.

    Hiding a button in the template is not a check — every POST handler calls
    this first. The rule lives in `app/web/perms.py` (workspace owner or the
    platform role's model rights; superusers pass).
    """
    perms = getattr(request.state, "web_perms", None)
    if perms is not None and perms.can(model_name, action):
        return None
    add_flash(request, "error", reason)
    return RedirectResponse(back, status_code=302)


def perms_can(request: Request, model_name: str, action: ActionType | str) -> bool:
    """The same rule as `guard_web`, as a plain boolean.

    For the server side of a page: a delete confirmation that warns about the
    cascade should not be rendered for someone who cannot delete, and a template
    `perms` check alone would not stop the POST from being described. Returns
    False when the request carries no `WebPerms` — fail closed.
    """
    perms = getattr(request.state, "web_perms", None)
    return perms is not None and perms.can(model_name, action)


def render(request: Request, name: str, status_code: int = 200, **extra: Any):
    context: dict[str, Any] = {
        "user": getattr(request.state, "web_user", None),
        "memberships": getattr(request.state, "memberships", []) or [],
        "workspaces": getattr(request.state, "workspaces", []) or [],
        "tenant": getattr(request.state, "tenant", None),
        "unread_notifications": getattr(request.state, "unread_notifications", 0) or 0,
        # Personal connections this person must act on; empty in a healthy setup.
        "connection_alerts": getattr(request.state, "connection_alerts", []) or [],
        "perms": getattr(request.state, "web_perms", None),
        "nav": NAV_ITEMS,
        "mobile_nav": MOBILE_NAV_ITEMS,
        "csrf": csrf_token(request),
        "flashes": pop_flashes(request),
        "path": request.url.path,
        # Tier names, for `plan_badge` on any page. A constant of the deployment,
        # not per-request state, so it belongs here rather than in every handler
        # that might render a badge.
        "plan_labels": {plan: Tenant.PLAN_LIMITS[plan]["label"] for plan in Tenant.PLANS},
    }
    context.update(extra)
    return templates.TemplateResponse(request=request, name=name, context=context, status_code=status_code)
