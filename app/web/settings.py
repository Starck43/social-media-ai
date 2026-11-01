"""Workspace settings `/app/settings` (docs/API.md).

Scoped sections on one page. What belongs here and what stays in `/admin` is not
a preference — it follows `docs/TENANCY.md`:

* **workspace** — the profile of the current workspace, and of nothing else.
* **team** — memberships in `tenant_users`; a personal secret is only usable by
  a member, so this tab and the credentials tab describe the same trust circle.
* **connections** — one card per platform: authorize, renew, disconnect. This
  is where a person connects their own accounts once, and where the state of
  that connection is explained. It replaced a raw list of `user_credentials`
  rows, because a row is an implementation detail while «ВКонтакте подключено»
  is a fact a person acts on. Manual key entry survives inside each card as the
  fallback for platforms without an OAuth flow (and for anyone who prefers it).
  A user only ever sees and writes their own secrets, never another member's.
* **channels** — where digests and the agent are delivered.
* The global platform catalog is managed only in SQLAdmin, not user settings.

Personal providers/models are managed on the models tab. Workspace chat
configuration references a numeric model ID in the current user's catalog.
"""

from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.models.tenancy.tenant import Tenant
from app.services.tenancy import member_roles
from app.types import ActionType

from ..models import LLMModel
from .deps import action_tenant_id, add_flash, ensure_csrf, render

router = APIRouter(prefix="/settings")

BACK = "/app/settings"

TABS: tuple[str, ...] = (
    "account",
    "connections",
    "models",
    "workspace",
    "team",
    "agent",
    "channels",
)
#: Tabs that only configure the workspace; a member who cannot manage it does
#: not get them (their forms are refused anyway by `_guard_workspace_settings`).
MANAGER_TABS: frozenset[str] = frozenset({"agent", "channels"})

# Personal kinds a user may store themselves, per platform. `bot_token`,
# `app_id` and `client_secret` are per-deployment config, so they are not here:
# they live in the environment (see
# `app/services/social/credentials.py::ENV_FALLBACK`). The values mirror the
# registry in `app/services/social/connections.py` — a platform is a card, and
# this is the manual fallback *inside* that card for anyone who cannot use OAuth.
SELF_SERVICE_KINDS: dict[str, tuple[tuple[str, str], ...]] = {
    "vk": (("user_token", "VK: личный токен вручную"),),
    "telegram": (("session", "Telegram: файл сессии MTProto"),),
}

#: Access levels an owner gives a member (see `app/services/tenancy/member_roles.py`).
ROLE_LABELS = {role.key: role.label for role in member_roles.MEMBER_ROLES}
#: Invite validity choices, days.
INVITE_DAYS: tuple[int, ...] = (1, 7, 30)

#: The comparison table's rows: what each tier is compared on.
#:
#: The wording lives here, next to the numbers it describes, rather than in the
#: template — so the pricing page and `Tenant.PLAN_LIMITS` cannot drift into
#: showing a limit the runtime does not enforce. `key` is the `PLAN_LIMITS` key,
#: which is also how the template looks up this row's usage counter.
#:
#: `None` renders as ∞; a boolean renders as a tick or a dash.
PLAN_ROWS: tuple[dict[str, Any], ...] = (
    {"key": "max_sources", "label": "Источники", "unit": "шт"},
    {"key": "daily_cost_limit", "label": "Бюджет LLM в сутки", "unit": "$"},
    {"key": "max_channels", "label": "Каналы доставки", "unit": "шт"},
    {"key": "max_scenarios", "label": "Сценарии агента", "unit": "шт"},
    {"key": "max_team_members", "label": "Участники команды", "unit": "чел"},
    {"key": "max_tasks", "label": "Задачи по расписанию", "unit": "шт"},
    {"key": "retention_days", "label": "Хранение данных", "unit": "дн."},
    {"key": "allow_auto_actions", "label": "Авто-комментарии", "unit": ""},
    {"key": "allow_learning", "label": "Обучение агента", "unit": ""},
    {"key": "allow_reflection", "label": "Рефлексия", "unit": ""},
)


#: Plan -> the value shown in its column for each row. Built from
#: `PLAN_LIMITS` so a new tier appears without touching the template, and a
#: changed number appears everywhere it is shown.
def _plan_table() -> list[dict[str, Any]]:
    from app.models.tenancy.tenant import Tenant

    rows = []
    for row in PLAN_ROWS:
        entry = {"key": row["key"], "label": row["label"]}
        for plan in Tenant.PLANS:
            value = Tenant.PLAN_LIMITS[plan].get(row["key"])
            if row["unit"] == "$" and isinstance(value, (int, float)):
                value = f"${value:g}"
            elif isinstance(value, (int, float)) and row["unit"] == "дн.":
                value = f"{value} дн."
            entry[plan] = value
        rows.append(entry)
    from app.services.tenancy.plan_catalog import CATALOG_VERSION, PLAN_CATALOG

    rows.append(
        {
            "key": "product_capabilities",
            "label": f"Product capabilities (catalog v{CATALOG_VERSION})",
            **{plan: ", ".join(PLAN_CATALOG[plan].capabilities) for plan in Tenant.PLANS},
        }
    )
    return rows


def _plan_labels() -> dict[str, str]:
    from app.models.tenancy.tenant import Tenant

    return {plan: Tenant.PLAN_LIMITS[plan]["label"] for plan in Tenant.PLANS}


def _guard_workspace_settings(request: Request, tenant_id: int | None) -> RedirectResponse | None:
    """Authorize the resolved target without granting global tenant.update."""
    perms = getattr(request.state, "web_perms", None)
    if perms is not None and perms.can_manage_workspace(tenant_id):
        return None
    add_flash(request, "error", "Недостаточно прав для этого действия")
    return RedirectResponse(BACK, status_code=302)


def _tab_of(request: Request) -> str:
    tab = request.query_params.get("tab") or "workspace"
    # `credentials` was this tab's old name. Redirecting to the renamed one is
    # not cosmetic: an old bookmark that silently rendered the workspace tab
    # would look like the credentials tab had been deleted.
    if tab == "credentials":
        tab = "connections"
    return tab if tab in TABS else "workspace"


def _personal_access(request: Request) -> bool:
    user = getattr(request.state, "web_user", None)
    user_id = getattr(user, "id", None)
    return bool(user is not None and getattr(user, "is_active", False) and type(user_id) is int and user_id > 0)


def _workspace_access(request: Request, tenant_id: int | None) -> bool:
    """Read name/timezone only for this authenticated active workspace context."""
    if not _personal_access(request) or type(tenant_id) is not int or tenant_id <= 0:
        return False
    tenant = getattr(request.state, "tenant", None)
    if tenant is None or tenant.id != tenant_id or not getattr(tenant, "is_active", False):
        return False
    user = request.state.web_user
    return bool(
        False
        or any(
            m.tenant_id == tenant_id
            and m.user_id == user.id
            and getattr(m, "channel", None) == "web"
            and getattr(m, "is_active", False)
            for m in getattr(request.state, "memberships", ())
        )
    )


def _visible_tabs(request: Request, tenant_id: int | None) -> tuple[str, ...]:
    """Personal data is caller-owned; concrete workspace capabilities gate shared data."""
    if not _personal_access(request):
        return ()
    workspace_tabs = TABS
    if not _workspace_access(request, tenant_id):
        # Existing personal connection write/OAuth routes still require onboarding.
        return ("account", "models")
    perms = getattr(request.state, "web_perms", None)
    if perms is not None and perms.can_manage_workspace(tenant_id):
        return workspace_tabs
    return tuple(tab for tab in workspace_tabs if tab not in MANAGER_TABS)


def _visible_tab(request: Request, tenant_id: int | None, visible: tuple[str, ...]) -> str:
    tab = _tab_of(request)
    return tab if tab in visible else visible[0]


@router.get("")
@router.get("/")
async def settings_page(request: Request):
    """Resolve access/tab first; load only the selected scope, never hidden-tab data."""
    tenant_id = request.state.tenant_id
    visible_tabs = _visible_tabs(request, tenant_id)
    if not visible_tabs:
        return HTMLResponse("Недостаточно прав", status_code=403)
    tab = _visible_tab(request, tenant_id, visible_tabs)
    user = request.state.web_user
    perms = getattr(request.state, "web_perms", None)
    manage = bool(_workspace_access(request, tenant_id) and perms and perms.can_manage_workspace(tenant_id))
    context: dict[str, Any] = {
        "tab": tab,
        "visible_tabs": visible_tabs,
        "settings_manage": manage,
        "settings_team_visible": manage,
        "is_superuser": False,
        "settings_scope": "personal" if tab in ("account", "connections", "models") else "workspace",
    }

    if tab == "workspace":
        tenant = request.state.tenant
        if manage:
            # Full configuration and quota projections require concrete-target capability.
            tenant_id = request.state.tenant_id
            context.update(
                tenant=tenant,
                plans=list(Tenant.PLANS),
                plan_labels=_plan_labels(),
                plan_rows=_plan_table(),
                plan_usage=await _plan_usage(tenant, perms),
                entitlement_policy=tenant.entitlement_policy,
            )
        else:
            # Explicit member read contract: no budget/prompt/channel/plan configuration.
            context["workspace_summary"] = {"name": tenant.name, "timezone": tenant.timezone}
    elif tab == "agent" and manage:
        from app.services.ai.personal_catalog import chat_model_options

        context["agent_model_options"] = await chat_model_options(user_id=user.id, tenant_id=tenant_id)

        # The Models tab and Agent form share the same compact probe outcome.
        probe_result = request.session.get("settings_model_probe_result")
        if isinstance(probe_result, dict) and probe_result.get("owner_user_id") == user.id:
            context["probe_result"] = probe_result
        else:
            context["probe_result"] = None

        tenant = request.state.tenant
        if manage:
            # Full configuration and quota projections require concrete-target capability.
            context.update(
                tenant=tenant,
                plans=list(Tenant.PLANS),
                plan_labels=_plan_labels(),
                plan_rows=_plan_table(),
                plan_usage=await _plan_usage(tenant, perms),
            )
        else:
            # Explicit member read contract: no budget/prompt/channel/plan configuration.
            context["workspace_summary"] = {"name": tenant.name, "timezone": tenant.timezone}
    elif tab == "team" and manage:
        from app.models.identity.user import User
        from app.models.managers.tenant_manager import TenantUserManager

        memberships = await TenantUserManager().web_memberships_for_tenant(tenant_id)
        member_ids = [m.user_id for m in memberships if m.user_id is not None]
        rows = await User.objects.filter(id__in=member_ids) if member_ids else []
        context.update(
            memberships=memberships,
            member_names={row.id: row.username for row in rows},
            role_labels=ROLE_LABELS,
            member_role_keys={m.id: member_roles.key_of(m) for m in memberships},
        )
    elif tab == "models":
        from .settings_llm import settings_llm_context

        context.update(await settings_llm_context(request))
    elif tab == "connections":
        from app.models.managers.user_credential_manager import user_credentials
        from app.services.social.connections import CONNECTIONS, connection_statuses

        credentials = await user_credentials.web_metadata_for_user(user_id=user.id)
        statuses = {status.platform: status for status in await connection_statuses(user.id)}
        context["connections"] = [
            {
                "platform": spec.platform,
                "title": spec.title,
                "purpose": spec.purpose,
                "is_oauth": spec.is_oauth,
                "manual_hint": spec.manual_hint,
                "status": statuses.get(spec.platform),
                "manual_kinds": SELF_SERVICE_KINDS.get(spec.platform, ()),
                "rows": [row for row in credentials if row.platform == spec.platform],
            }
            for spec in CONNECTIONS
        ]
    elif tab == "channels" and manage:
        from app.models.managers.tenant_manager import TenantChannelManager

        context.update(
            channels=await TenantChannelManager().filter(tenant_id=tenant_id), action_types=ActionType.choices()
        )

    response = render(request, "web/settings.html", section="settings", **context)
    response.headers["Cache-Control"] = "no-store"
    return response


async def _plan_usage(tenant: Any, perms: Any) -> dict[str, str]:
    """Manager-only quota projections; resource counts additionally require VIEW."""
    from app.models.analysis.agent_scenario import AgentScenario
    from app.models.collection.source import Source
    from app.models.managers.tenant_manager import TenantChannelManager, TenantUserManager
    from app.models.scheduling.agent_task import AgentTask

    counts = {}
    for key, model, model_name in (
        ("max_sources", Source, "source"),
        ("max_scenarios", AgentScenario, "agentscenario"),
        ("max_tasks", AgentTask, "agenttask"),
    ):
        if perms.can(model_name, "view"):
            counts[key] = await model.objects.filter(tenant_id=tenant.id).count()
    # Workspace capability includes team and delivery administration, not global rights.
    counts["max_channels"] = await TenantChannelManager().filter(tenant_id=tenant.id).count()
    counts["max_team_members"] = len(
        [m for m in await TenantUserManager().web_memberships_for_tenant(tenant.id) if m.user_id is not None]
    )
    usage = {}
    for key, used in counts.items():
        limit = tenant.effective_limits().get(key)
        if limit is not None:
            usage[key] = f"{used}/{int(limit)}" + (" ⚠" if used > limit else "")
    return usage


@router.post("/workspace")
async def workspace_update(
    request: Request,
    name: str = Form(...),
    timezone: str = Form(...),
    daily_cost_limit: str = Form(...),
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    """Rename the current workspace and set its schedule timezone and cost cap."""
    tenant_id = action_tenant_id(request, tenant_id)

    denied = _guard_workspace_settings(request, tenant_id)
    if denied is not None:
        return denied

    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse(BACK, status_code=302)

    from app.models.managers.tenant_manager import tenants

    # `action_tenant_id` is what makes this safe for a superuser: it resolves the
    # workspace the form was rendered for, not the one in the session.
    if await tenants.get(id=tenant_id) is None:
        add_flash(request, "error", "Пространство не найдено")
        return RedirectResponse(BACK, status_code=302)

    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        add_flash(request, "error", f"Неизвестная таймзона: {timezone}")
        return RedirectResponse(BACK, status_code=302)

    try:
        limit = float(daily_cost_limit)
    except ValueError:
        add_flash(request, "error", "Лимит должен быть числом")
        return RedirectResponse(BACK, status_code=302)
    if limit < 0:
        add_flash(request, "error", "Лимит не может быть отрицательным")
        return RedirectResponse(BACK, status_code=302)

    await tenants.update_by_id(
        tenant_id,
        name=name.strip()[:100],
        timezone=timezone,
        daily_cost_limit=limit,
    )
    add_flash(request, "success", "Настройки воркспейса сохранены")
    return RedirectResponse(BACK, status_code=302)


@router.post("/agent")
async def agent_settings_update(
    request: Request,
    agent_model_id: str = Form(""),
    agent_max_tokens: str = Form(""),
    agent_temperature: str = Form(""),
    agent_system_prompt: str = Form(""),
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    """Update agent chat settings for this workspace."""
    tenant_id = action_tenant_id(request, tenant_id)

    denied = _guard_workspace_settings(request, tenant_id)
    if denied is not None:
        return denied

    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse(BACK, status_code=302)

    from app.models.managers.tenant_manager import tenants

    if await tenants.get(id=tenant_id) is None:
        add_flash(request, "error", "Пространство не найдено")
        return RedirectResponse(BACK, status_code=302)

    # Parse and validate numeric fields
    max_tokens = None
    if agent_max_tokens.strip():
        try:
            max_tokens = int(agent_max_tokens)
            if max_tokens < 64 or max_tokens > 8192:
                add_flash(request, "error", "Max tokens должно быть от 64 до 8192")
                return RedirectResponse(BACK, status_code=302)
        except ValueError:
            add_flash(request, "error", "Max tokens должно быть числом")
            return RedirectResponse(BACK, status_code=302)

    temperature = None
    if agent_temperature.strip():
        try:
            temperature = float(agent_temperature)
            if not math.isfinite(temperature) or temperature < 0 or temperature > 2:
                add_flash(request, "error", "Temperature должно быть от 0 до 2")
                return RedirectResponse(BACK, status_code=302)
        except ValueError:
            add_flash(request, "error", "Temperature должно быть числом")
            return RedirectResponse(BACK, status_code=302)

    # Clean string fields
    try:
        selected_id = int(agent_model_id) if agent_model_id.strip() else None
        from app.services.ai.personal_catalog import validate_chat_model

        selected = await validate_chat_model(
            user_id=request.state.web_user.id, tenant_id=tenant_id, model_id=selected_id
        )
    except (ValueError, PermissionError):
        add_flash(request, "error", "Модель недоступна для пользователя или пространства")
        return RedirectResponse(BACK + "?tab=agent", status_code=302)
    prompt = agent_system_prompt.strip() or None

    await tenants.update_by_id(
        tenant_id,
        agent_model_id=selected.id if selected is not None else None,
        agent_max_tokens=max_tokens,
        agent_temperature=temperature,
        agent_system_prompt=prompt,
    )
    add_flash(request, "success", "Настройки агента сохранены")
    # A save without a response body ends the request with an empty document
    # (HTTP `null`): the operator sees no confirmation and cannot tell whether
    # the click landed. Every other settings write redirects back to its tab,
    # where the flash is rendered; this one used to just stop.
    return RedirectResponse(BACK, status_code=302)


@router.post("/agent/model-probe")
async def agent_model_probe(
    request: Request,
    agent_model_id: str = Form(""),
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
    probe_mode: str = Form("local"),
    consent: str = Form(""),
):
    """Run the shared local check or explicitly consented bounded provider test."""
    tenant_id = action_tenant_id(request, tenant_id)
    denied = _guard_workspace_settings(request, tenant_id)
    if denied is not None:
        return denied
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse(BACK, status_code=302)

    from app.models.managers.tenant_manager import tenants
    from app.services.ai.personal_catalog import chat_model_options, validate_chat_model

    if await tenants.get(id=tenant_id) is None:
        add_flash(request, "error", "Пространство не найдено")
        return RedirectResponse(BACK, status_code=302)

    tenant = await tenants.get(id=tenant_id)
    model_id = None
    if agent_model_id.strip():
        try:
            model_id = int(agent_model_id)
            await validate_chat_model(
                user_id=request.state.web_user.id, tenant_id=tenant_id, model_id=int(agent_model_id)
            )
        except (ValueError, PermissionError):
            add_flash(request, "error", "Выбранная модель недоступна")
            return RedirectResponse(BACK + "?tab=agent", status_code=302)

    else:
        if tenant.agent_model_id is not None:
            try:
                model_id = int(tenant.agent_model_id)
                await validate_chat_model(user_id=request.state.web_user.id, tenant_id=tenant_id, model_id=model_id)
            except (ValueError, PermissionError):
                model_id = None
        else:
            options = await chat_model_options(user_id=request.state.web_user.id, tenant_id=tenant_id)
            effective = next((option for option in options if option["is_effective_default"]), None)
            model_id = effective["id"] if effective else None
    if model_id is None:
        request.session["settings_model_probe_result"] = {
            "model_id": None,
            "model_name": None,
            "owner_user_id": request.state.web_user.id,
            "mode": probe_mode,
            "ok": False,
            "text": "Для теста выберите модель, доступную этому пространству.",
            "limit_hint": None,
        }
        return RedirectResponse(f"{BACK}?tab=agent", status_code=303)
    from .settings_llm import _run_probe

    response = await _run_probe(
        request,
        model_id=str(model_id),
        mode=probe_mode,
        tenant_id=tenant_id,
        consent=consent,
    )
    if isinstance(response, RedirectResponse):
        response.headers["Location"] = f"{BACK}?tab=agent"
    return response


@router.post("/credentials")
async def credential_store(
    request: Request,
    kind: str = Form(...),
    secret: str = Form(...),
    label: str = Form(""),
    token: str = Form("", alias="_csrf"),
):
    """Store one of the caller's own personal secrets.

    No permission gate on purpose: a row here is keyed by `users.id`, so a user
    writes only their own vault and needs no right to do it. What *does* need a
    right is using someone else's — that is `owner.py::resolve_source_owner`.

    `kind` arrives as `platform::kind` from a single `<select>`: a platform with
    two secret kinds (telegram today) cannot be expressed as a flat list, and
    two coupled selects would have to be kept in sync in the browser.
    """
    user = getattr(request.state, "web_user", None)
    user_id = getattr(user, "id", None)
    if not _personal_access(request):
        add_flash(request, "error", "Только для вошедшего пользователя")
        return RedirectResponse(BACK, status_code=302)

    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse(BACK, status_code=302)

    platform, _, kind = kind.partition("::")
    kinds = dict(SELF_SERVICE_KINDS.get(platform.strip().lower(), ()))
    if not kind or kind not in kinds:
        # An unknown kind is refused rather than stored: the vault is also the
        # fallback for `bot_token` / `app_id`, and this form must not become a
        # second way to write deployment config.
        add_flash(request, "error", f"Ключ '{kind}' нельзя задать из интерфейса")
        return RedirectResponse(BACK, status_code=302)

    if not secret.strip():
        add_flash(request, "error", "Значение не может быть пустым")
        return RedirectResponse(BACK, status_code=302)

    from app.models.managers.user_credential_manager import user_credentials

    await user_credentials.store(
        user_id=user_id,
        platform=platform.strip().lower(),
        kind=kind,
        secret=secret.strip(),
        label=label.strip()[:100] or kinds[kind],
    )
    add_flash(request, "success", f"Сохранено: {kinds[kind]}")
    return RedirectResponse(f"{BACK}?tab=connections", status_code=302)


@router.post("/credentials/{credential_id}/disable")
async def credential_disable(
    request: Request,
    credential_id: int,
    token: str = Form("", alias="_csrf"),
):
    """Deactivate one of the caller's own rows; the row is kept for audit."""
    user = getattr(request.state, "web_user", None)
    user_id = getattr(user, "id", None)

    if not _personal_access(request):
        add_flash(request, "error", "Только для вошедшего пользователя")
        return RedirectResponse(BACK, status_code=302)

    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse(BACK, status_code=302)

    from app.models.managers.user_credential_manager import user_credentials

    # `id` and `user_id` in one predicate: possession of the row id must not be
    # enough to edit a row that belongs to somebody else.
    row = await user_credentials.get(id=credential_id, user_id=user_id)
    if row is None:
        add_flash(request, "error", "Ключ не найден")
        return RedirectResponse(BACK, status_code=302)

    await user_credentials.update_by_id(row.id, is_active=False)
    add_flash(request, "success", f"Ключ «{row.label or row.kind}» отключён")
    return RedirectResponse(f"{BACK}?tab=connections", status_code=302)


@router.post("/members/{membership_id}/role")
async def membership_role(
    request: Request,
    membership_id: int,
    role: str = Form(...),
    token: str = Form("", alias="_csrf"),
):
    """Change a member's workspace role.

    The caller must belong to this workspace and cannot edit their own role.
    When editing another member, the last owner cannot be demoted — a
    workspace with no owner has nobody who may manage it at all, so it would
    be unreachable rather than merely restricted.
    """
    from app.models import WorkspaceRole as Role

    tenant_id = request.state.tenant_id

    denied = _guard_workspace_settings(request, tenant_id)
    if denied is not None:
        return denied

    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse(BACK, status_code=302)

    from app.models.managers.tenant_manager import tenant_users

    # Scoped by `tenant_id`: knowing a row id from another workspace changes nothing.
    membership = await tenant_users.get(id=membership_id, tenant_id=tenant_id)
    if membership is None:
        add_flash(request, "error", "Участник не найден")
        return RedirectResponse(f"{BACK}?tab=team", status_code=302)

    if membership.user_id == request.state.web_user.id:
        add_flash(request, "error", "Нельзя менять собственную роль в пространстве")
        return RedirectResponse(f"{BACK}?tab=team", status_code=302)

    level = member_roles.resolve(role)
    if level is None:
        add_flash(request, "error", f"Неизвестная роль: {role}")
        return RedirectResponse(f"{BACK}?tab=team", status_code=302)
    new_role = level.key

    r = await Role.objects.filter(codename=level.codename).first()
    if r is None:
        add_flash(request, "error", "Роль не настроена — обратитесь к оператору")
        return RedirectResponse(f"{BACK}?tab=team", status_code=302)
    new_role_id = r.id

    changed = await tenant_users.change_member_role(
        tenant_id=tenant_id, membership_id=membership.id, role_id=new_role_id
    )
    if not changed:
        add_flash(request, "error", "Недостаточно прав или нельзя снять последнего владельца")
        return RedirectResponse(f"{BACK}?tab=team", status_code=302)
    add_flash(request, "success", f"Роль изменена на «{ROLE_LABELS[new_role]}»")
    return RedirectResponse(f"{BACK}?tab=team", status_code=302)


@router.post("/members/{membership_id}/ai")
async def membership_ai_access(
    request: Request, membership_id: int, enabled: str = Form(""), token: str = Form("", alias="_csrf")
):
    tenant_id = request.state.tenant_id
    denied = _guard_workspace_settings(request, tenant_id)
    if denied is not None:
        return denied
    if not ensure_csrf(request, token):
        return HTMLResponse("Сессия истекла", status_code=403)
    from app.models.managers.tenant_manager import tenant_users

    changed = await tenant_users.set_ai_access(tenant_id=tenant_id, membership_id=membership_id, enabled=enabled == "1")
    add_flash(
        request, "success" if changed else "error", "Доступ к AI изменён" if changed else "Изменение доступа запрещено"
    )
    return RedirectResponse(f"{BACK}?tab=team", status_code=302)


@router.post("/members/{membership_id}/disable")
async def membership_disable(request: Request, membership_id: int, token: str = Form("", alias="_csrf")):
    """Disable web access, retaining the membership and its audit identity."""
    tenant_id = request.state.tenant_id
    denied = _guard_workspace_settings(request, tenant_id)
    if denied is not None:
        return denied
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse(f"{BACK}?tab=team", status_code=302)
    from app.models.managers.tenant_manager import tenant_users

    changed = await tenant_users.disable_web_member(tenant_id=tenant_id, membership_id=membership_id)
    add_flash(
        request,
        "success" if changed else "error",
        "Доступ участника отключён" if changed else "Участник не найден или защищён как владелец",
    )
    return RedirectResponse(f"{BACK}?tab=team", status_code=302)


_ISSUED_INVITE_KEY = "_issued_invite"


def _pop_issued_invite(request: Request) -> dict[str, Any] | None:
    """The invite issued by the previous POST, shown exactly once."""
    issued = request.session.pop(_ISSUED_INVITE_KEY, None)
    if not isinstance(issued, dict) or not isinstance(issued.get("code"), str):
        return None
    if issued.get("tenant_id") != request.state.tenant_id:
        return None
    link = f"{str(request.base_url).rstrip('/')}/app/invite?code={issued['code']}"
    return {**issued, "link": link, "qr_svg": invite_qr_svg(link)}


def invite_qr_svg(link: str) -> str | None:
    """Inline SVG QR code for the invite link, or None when `segno` is missing."""
    try:
        import segno
    except ImportError:  # optional: the link and the code still work without it
        return None
    return segno.make(link, error="m").svg_inline(scale=4, border=2, dark="#0f172a", light="#ffffff")


async def _open_invites(request: Request, tenant_id: int | None, visible_tabs: tuple[str, ...]) -> list[dict]:
    """Redeemable invites for the team tab — only for someone who manages the workspace."""
    from app.models import WorkspaceRole as Role
    from app.models.managers.tenant_manager import TenantInviteManager

    perms = getattr(request.state, "web_perms", None)
    if tenant_id is None or perms is None or not perms.can_manage_workspace(tenant_id):
        return []
    rows = await TenantInviteManager().open_for_tenant(tenant_id)
    if not rows:
        return []
    by_codename = {level.codename: level for level in member_roles.MEMBER_ROLES}
    labels: dict[int, str] = {}
    for role in await Role.objects.filter(id__in=list({r.role_id for r in rows if r.role_id is not None})):
        codename = getattr(role.codename, "name", role.codename)
        if not isinstance(codename, str):
            codename = str(codename) if codename is not None else ""
        level = by_codename.get(codename)
        labels[role.id] = level.label if level is not None else str(codename)
    return [
        {
            "id": row.id,
            "role": labels.get(row.role_id, "Владелец" if row.role_id is None else "—"),
            "created": row.created_at.strftime("%d.%m.%Y %H:%M") if row.created_at else "—",
            "expires": row.expires_at.strftime("%d.%m.%Y %H:%M UTC") if row.expires_at else "бессрочно",
            "uses": f"{row.used_count} из {row.max_uses}",
        }
        for row in rows
    ]


@router.get("/invites")
async def invite_dialog(request: Request):
    """Load only the permitted workspace's invitation UI, on explicit demand."""
    tenant_id = request.state.tenant_id
    perms = getattr(request.state, "web_perms", None)
    if perms is None or not perms.can_manage_workspace(tenant_id):
        return HTMLResponse("Недостаточно прав", status_code=403)
    response = render(
        request,
        "web/_invite_dialog.html",
        invite_roles=tuple(level for level in member_roles.INVITABLE if perms.can_manage_member("viewer", level.key)),
        invite_default=member_roles.DEFAULT_INVITE_KEY,
        invite_days=INVITE_DAYS,
        issued_invite=_pop_issued_invite(request),
        open_invites=await _open_invites(request, tenant_id, ("team",)),
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/invites/{invite_id}/revoke")
async def invite_revoke(request: Request, invite_id: int, token: str = Form("", alias="_csrf")):
    """Revoke one open invite of this workspace; its code stops working at once."""
    from app.models.managers.tenant_manager import TenantInviteManager

    tenant_id = request.state.tenant_id
    denied = _guard_workspace_settings(request, tenant_id)
    if denied is not None:
        return denied
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse(f"{BACK}?tab=team&invite_dialog=1", status_code=302)
    if await TenantInviteManager().revoke(tenant_id=tenant_id, invite_id=invite_id):
        add_flash(request, "success", "Приглашение отозвано")
    else:
        add_flash(request, "error", "Приглашение не найдено или уже недействительно")
    return RedirectResponse(f"{BACK}?tab=team&invite_dialog=1", status_code=302)


@router.post("/invites")
async def invite_issue(
    request: Request,
    role: str = Form(member_roles.DEFAULT_INVITE_KEY),
    days: int = Form(7),
    token: str = Form("", alias="_csrf"),
):
    """Issue a single-use invite link for this workspace with a chosen access level.

    Only someone who manages this workspace may invite; the owner level is not
    invitable (promote an existing member on this tab instead). The plaintext
    code exists only in this response cycle — the database keeps its hash.
    """
    from datetime import datetime, timedelta, timezone

    from app.models.managers.tenant_manager import TenantInviteManager

    tenant_id = request.state.tenant_id
    denied = _guard_workspace_settings(request, tenant_id)
    if denied is not None:
        return denied
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse(f"{BACK}?tab=team&invite_dialog=1", status_code=302)
    level = member_roles.resolve(role, invitable_only=True)
    if level is None or days not in INVITE_DAYS:
        add_flash(request, "error", "Выберите уровень доступа и срок действия из списка")
        return RedirectResponse(f"{BACK}?tab=team&invite_dialog=1", status_code=302)

    perms = request.state.web_perms
    if not perms.is_owner and level.key == "admin":
        return HTMLResponse("Назначать администратора может только владелец", status_code=403)
    expires_at = datetime.now(timezone.utc) + timedelta(days=days)
    invite, code = await TenantInviteManager().issue(
        tenant_id=tenant_id, role=level.codename, max_uses=1, expires_at=expires_at
    )
    if invite is None or invite.role_id is None:
        add_flash(request, "error", "Роль не настроена — обратитесь к оператору")
        return RedirectResponse(f"{BACK}?tab=team&invite_dialog=1", status_code=302)
    request.session[_ISSUED_INVITE_KEY] = {
        "code": code,
        "tenant_id": tenant_id,
        "role": level.label,
        "expires": expires_at.strftime("%d.%m.%Y %H:%M UTC"),
    }
    return RedirectResponse(f"{BACK}?tab=team&invite_dialog=1", status_code=302)


@router.post("/plan")
async def plan_update(
    request: Request,
    plan: str = Form(...),
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    """Move a workspace to another billing tier.

    Superuser-only, and deliberately so: this is a billing act, not a workspace
    preference, so a workspace owner cannot grant themselves limits they did not
    buy. A downgrade that leaves the workspace over its new ceilings is allowed
    and reported — refusing would trap a customer who has to downgrade precisely
    because they are over budget — but it is spelled out so the operator knows
    what to delete before the next create attempt fails.
    """
    back = f"{BACK}?tab=workspace"

    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse(back, status_code=302)

    user = getattr(request.state, "web_user", None)
    if user is None or not False:
        add_flash(request, "error", "Менять тариф может только оператор платформы")
        return RedirectResponse(back, status_code=302)

    from app.models.managers.tenant_manager import tenants
    from app.services.tenancy.limits import plan_overage

    target_id = action_tenant_id(request, tenant_id) or request.state.tenant_id
    tenant = await tenants.get(id=target_id)
    if tenant is None:
        add_flash(request, "error", "Пространство не найдено")
        return RedirectResponse(BACK, status_code=302)

    requested = (plan or "").strip().lower()
    if requested not in Tenant.PLAN_LIMITS:
        # Normalise rather than 422: the form posts a fixed set, but a
        # hand-written request must not be able to write a value the CHECK
        # constraint would reject at commit time.
        add_flash(request, "error", f"Неизвестный тариф: {plan}")
        return RedirectResponse(back, status_code=302)

    if requested == tenant.plan:
        add_flash(request, "info", f"Тариф уже «{tenant.plan_label}»")
        return RedirectResponse(back, status_code=302)

    over = await plan_overage(tenant.id, requested)
    await tenants.update_by_id(tenant.id, plan=requested)

    label = Tenant.PLAN_LIMITS[requested]["label"]
    if over:
        add_flash(
            request,
            "warning",
            f"Тариф изменён на «{label}». Придётся сократить: {'; '.join(over)} — " f"до этого добавлять нельзя.",
        )
    else:
        add_flash(request, "success", f"Тариф изменён на «{label}»")
    return RedirectResponse(back, status_code=302)


@router.post("/channels/{channel_id}")
async def channel_update(
    request: Request,
    channel_id: int,
    is_digest_target: str = Form(""),
    token: str = Form("", alias="_csrf"),
):
    """Toggle whether a bound channel receives the daily digest."""
    tenant_id = request.state.tenant_id

    denied = _guard_workspace_settings(request, tenant_id)
    if denied is not None:
        return denied

    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse(BACK, status_code=302)

    from app.models.managers.tenant_manager import TenantChannelManager

    channel = await TenantChannelManager().get(id=channel_id, tenant_id=tenant_id)
    if channel is None:
        add_flash(request, "error", "Канал не найден")
        return RedirectResponse(f"{BACK}?tab=channels", status_code=302)

    await TenantChannelManager().update_by_id(channel.id, is_digest_target=is_digest_target == "on")
    add_flash(request, "success", "Настройка канала сохранена")
    return RedirectResponse(f"{BACK}?tab=channels", status_code=302)
