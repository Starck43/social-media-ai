"""Auth & onboarding pages: /app/login, /app/register, /app/invite, /app/logout.

Sessions: the access JWT lives in the server-side session cookie (the same
pattern sqladmin/dashboard use), never in a first-party JS variable. The UI
token is *not* a tenant claim — `TenantUIMiddleware` resolves workspaces from
`tenant_users` web memberships.
"""

from __future__ import annotations

import re
import secrets

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.core.hashing import verify_password
from app.models import Role, User
from app.models.managers.tenant_manager import (
    TenantInviteManager,
    TenantUserManager,
    tenants,
)
from app.services.user.auth import authenticate
from app.types import UserRoleType
from app.utils.token import create_tokens_pair

from .deps import add_flash, ensure_csrf, render, safe_next

router = APIRouter()

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")[:40]
    return slug or "workspace"


async def _unique_slug(base: str) -> str:
    slug = base
    while await tenants.get(slug=slug) is not None:
        slug = f"{base}-{secrets.token_hex(2)}"
    return slug


async def _create_workspace(user: User, name: str) -> int:
    """New tenant owned by `user` (web membership, role=owner). Returns tenant_id."""
    tenant = await tenants.create(
        name=name.strip()[:100] or f"{user.username}'s workspace",
        slug=await _unique_slug(_slugify(name or user.username)),
    )
    await TenantUserManager().add_web_member(tenant_id=tenant.id, user_id=user.id, role="owner")
    return tenant.id


def _user_id_of(request: Request) -> int:
    return request.state.web_user.id


# ---------------------------------------------------------------- login ----


@router.get("/login")
async def login_page(request: Request):
    if getattr(request.state, "web_user", None) is not None:
        return RedirectResponse("/app/", status_code=302)
    return render(request, "web/login.html", next=safe_next(request.query_params.get("next")))


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
    token: str = Form("", alias="_csrf"),
):
    if not ensure_csrf(request, token):
        return render(request, "web/login.html", status_code=403, error="Сессия истекла, попробуйте ещё раз")
    try:
        user = await authenticate(username, password, verify_password)
    except Exception:
        # `authenticate` answers 400 for both wrong credentials and inactive users;
        # the page only needs one generic message.
        return render(request, "web/login.html", status_code=400, error="Неверный логин или пароль")
    tokens = create_tokens_pair(subject=str(user.id))
    request.session["token"] = tokens["access_token"]
    return RedirectResponse(safe_next(next) or "/app/", status_code=302)


@router.post("/logout")
async def logout(request: Request, token: str = Form("", alias="_csrf")):
    if ensure_csrf(request, token):
        request.session.pop("token", None)
        request.session.pop("app_tenant_id", None)
    return RedirectResponse("/app/login", status_code=302)


# ------------------------------------------------------------ register ----


@router.get("/register")
async def register_page(request: Request):
    if getattr(request.state, "web_user", None) is not None:
        return RedirectResponse("/app/", status_code=302)
    return render(request, "web/register.html")


@router.post("/register")
async def register_submit(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    workspace: str = Form(""),
    token: str = Form("", alias="_csrf"),
):
    if not ensure_csrf(request, token):
        return render(request, "web/register.html", status_code=403, error="Сессия истекла, попробуйте ещё раз")
    errors = []
    if len(username) < 3 or len(username) > 50:
        errors.append("Имя пользователя — от 3 до 50 символов")
    if len(password) < 8:
        errors.append("Пароль — не короче 8 символов")
    if await User.objects.get_by_username_or_email(username) is not None:
        errors.append("Такое имя пользователя уже занято")
    if await User.objects.exists(email=email):
        errors.append("Такой e-mail уже зарегистрирован")
    if errors:
        return render(
            request,
            "web/register.html",
            status_code=400,
            error="; ".join(errors),
            form={"username": username, "email": email, "workspace": workspace},
        )

    viewer = await Role.objects.get(codename=UserRoleType.VIEWER.name)
    if viewer is None:
        return render(request, "web/register.html", status_code=500, error="Не настроены роли — обратитесь к оператору")
    user = await User.objects.create_user(
        username=username, email=email, password=password, role_id=viewer.id, is_superuser=False
    )
    tenant_id = await _create_workspace(user, workspace or username)
    tokens = create_tokens_pair(subject=str(user.id))
    request.session["token"] = tokens["access_token"]
    request.session["app_tenant_id"] = tenant_id
    add_flash(request, "success", "Workspace создан. Добавьте первый источник, когда будете готовы.")
    return RedirectResponse("/app/", status_code=302)


# -------------------------------------------------------------- invite ----


@router.get("/invite")
async def invite_page(request: Request):
    needs_workspace = not getattr(request.state, "memberships", [])
    return render(request, "web/invite.html", needs_workspace=needs_workspace)


@router.post("/invite")
async def invite_submit(request: Request, code: str = Form(...), token: str = Form("", alias="_csrf")):
    if not ensure_csrf(request, token):
        return render(request, "web/invite.html", status_code=403, error="Сессия истекла, попробуйте ещё раз")
    result = await TenantInviteManager().redeem_web(code=code.strip().upper(), user_id=_user_id_of(request))
    if result["status"] == "invalid":
        return render(
            request,
            "web/invite.html",
            status_code=400,
            error="Код не найден, истёк или уже использован",
            needs_workspace=not request.state.memberships,
        )
    request.session["app_tenant_id"] = result["tenant_id"]
    add_flash(request, "success", "Вы присоединены к workspace.")
    return RedirectResponse("/app/", status_code=302)


# --------------------------------------------------- extra workspaces ----


@router.post("/workspaces/new")
async def new_workspace(request: Request, name: str = Form(""), token: str = Form("", alias="_csrf")):
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/invite", status_code=302)
    tenant_id = await _create_workspace(request.state.web_user, name)
    request.session["app_tenant_id"] = tenant_id
    add_flash(request, "success", "Workspace создан.")
    return RedirectResponse("/app/", status_code=302)


@router.post("/workspaces/select")
async def select_workspace(request: Request, tenant_id: int = Form(...), token: str = Form("", alias="_csrf")):
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/", status_code=302)
    if any(m.tenant_id == tenant_id for m in request.state.memberships):
        request.session["app_tenant_id"] = tenant_id
    return RedirectResponse("/app/", status_code=302)
