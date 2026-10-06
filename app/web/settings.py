"""Workspace settings `/app/settings` (docs/design/ui.md §4.8).

Four tabs, one page. What belongs here and what stays in `/admin` is not a
preference — it follows `docs/TENANCY.md`:

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

Deliberately absent: `llm_providers` / `llm_models`. The fleet is global and
one-per-deployment, so it stays in the sqladmin console with its connection
tests — a workspace must not be able to repoint the model the runtime uses.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.models.tenant import Tenant
from app.types import ActionType

from .deps import action_tenant_id, add_flash, ensure_csrf, guard_web, render

router = APIRouter(prefix="/settings")

BACK = "/app/settings"

TABS: tuple[str, ...] = ("workspace", "team", "connections", "agent", "channels")

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

ROLE_LABELS = {
    "owner": "Владелец",
    "admin": "Администратор",
    "member": "Участник",
    "viewer": "Наблюдатель",
}

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
    from app.models.tenant import Tenant

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
    return rows


def _plan_labels() -> dict[str, str]:
    from app.models.tenant import Tenant

    return {plan: Tenant.PLAN_LIMITS[plan]["label"] for plan in Tenant.PLANS}


def _tab_of(request: Request) -> str:
    tab = request.query_params.get("tab") or "workspace"
    # `credentials` was this tab's old name. Redirecting to the renamed one is
    # not cosmetic: an old bookmark that silently rendered the workspace tab
    # would look like the credentials tab had been deleted.
    if tab == "credentials":
        tab = "connections"
    return tab if tab in TABS else TABS[0]


@router.get("")
@router.get("/")
async def settings_page(request: Request):
    """One page, four tabs; the query string carries the active tab."""
    from app.models.managers.tenant_manager import TenantChannelManager, TenantUserManager, tenants

    tenant_id = request.state.tenant_id
    user = getattr(request.state, "web_user", None)
    user_id = getattr(user, "id", None)

    tenant = await tenants.get(id=tenant_id)
    memberships = await TenantUserManager().web_memberships_for_tenant(tenant_id)
    member_names = {}
    member_ids = [m.user_id for m in memberships if m.user_id is not None]
    if member_ids:
        from app.models.user import User

        rows = await User.objects.filter(id__in=member_ids)
        member_names = {row.id: row.username for row in rows}

    # Only the caller's own vault rows. `user_credentials` is not tenant-scoped,
    # so the filter is by user id — never "the workspace's credentials".
    credentials = []
    if user_id is not None:
        from app.models.managers.user_credential_manager import user_credentials

        credentials = sorted(
            await user_credentials.filter(user_id=user_id),
            key=lambda row: (row.platform, row.kind),
        )

    # One card per platform, each carrying its own state, its action and — for
    # a manual platform or someone without OAuth — the fallback form.
    from app.services.social.connections import CONNECTIONS, connection_statuses

    statuses = {status.platform: status for status in await connection_statuses(user_id)}
    cards = []
    for spec in CONNECTIONS:
        status = statuses.get(spec.platform)
        card = {
            "platform": spec.platform,
            "title": spec.title,
            "purpose": spec.purpose,
            "is_oauth": spec.is_oauth,
            "manual_hint": spec.manual_hint,
            "status": status,
            "manual_kinds": SELF_SERVICE_KINDS.get(spec.platform, ()),
            "rows": [row for row in credentials if row.platform == spec.platform],
        }
        cards.append(card)

    channels = await TenantChannelManager().filter(tenant_id=tenant_id)

    # Active LLM models for the agent dropdown
    from app.models.llm_model import LLMModel

    active_models = await LLMModel.objects.select_related("provider").filter(is_active=True).order_by(LLMModel.is_default.desc(), LLMModel.id)
    model_options = []
    for m in active_models:
        provider_name = m.provider.name if m.provider else "?"
        cost = f"${m.input_cost_per_1k:.4f}/${m.output_cost_per_1k:.4f}/1K"
        label = f"{provider_name}/{m.model_id} ({cost})"
        if m.is_default:
            label += " ★"
        model_options.append({"id": m.name, "label": label})

    # What this workspace has already consumed, so the plan column can show
    # "3 / 3" instead of a bare ceiling the reader has to count rows to match.
    usage = await _plan_usage(tenant_id, channels, memberships)

    return render(
        request,
        "web/settings.html",
        section="settings",
        tab=_tab_of(request),
        tenant=tenant,
        memberships=memberships,
        member_names=member_names,
        connections=cards,
        credentials=credentials,
        channels=channels,
        self_service_kinds=SELF_SERVICE_KINDS,
        role_labels=ROLE_LABELS,
        action_types=ActionType.choices(),
        plans=list(Tenant.PLANS),
        plan_labels=_plan_labels(),
        plan_rows=_plan_table(),
        plan_usage=usage,
        agent_model_options=model_options,
    )


async def _plan_usage(
    tenant_id: int,
    channels: list[Any],
    memberships: list[Any],
) -> dict[str, str]:
    """Current usage per quota row, as "used/limit" for the plan's column.

    Only rows that are countable get an entry. A row with no counter (a feature
    flag) simply has no usage to show, which is why the template treats a
    missing key as "nothing to annotate".
    """
    from app.models.agent_scenario import AgentScenario
    from app.models.agent_task import AgentTask
    from app.models.source import Source

    tenant = await Tenant.objects.get(id=tenant_id)
    limits = tenant.effective_limits() if tenant else {}

    counts = {
        "max_sources": await Source.objects.filter(tenant_id=tenant_id).count(),
        "max_channels": len(channels),
        "max_scenarios": await AgentScenario.objects.filter(tenant_id=tenant_id).count(),
        "max_team_members": len([m for m in memberships if m.user_id is not None]),
        "max_tasks": await AgentTask.objects.filter(tenant_id=tenant_id).count(),
    }

    usage: dict[str, str] = {}
    for key, used in counts.items():
        limit = limits.get(key)
        if limit is None:
            continue
        over = " ⚠" if used > limit else ""
        usage[key] = f"{used}/{int(limit)}{over}"
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

    denied = guard_web(request, "tenant", "update", back=BACK)
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
    agent_model: str = Form(""),
    agent_max_tokens: str = Form(""),
    agent_temperature: str = Form(""),
    agent_system_prompt: str = Form(""),
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    """Update agent chat settings for this workspace."""
    tenant_id = action_tenant_id(request, tenant_id)

    denied = guard_web(request, "tenant", "update", back=BACK)
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
            if temperature < 0 or temperature > 2:
                add_flash(request, "error", "Temperature должно быть от 0 до 2")
                return RedirectResponse(BACK, status_code=302)
        except ValueError:
            add_flash(request, "error", "Temperature должно быть числом")
            return RedirectResponse(BACK, status_code=302)

    # Clean string fields
    model = agent_model.strip() or None
    prompt = agent_system_prompt.strip() or None

    await tenants.update_by_id(
        tenant_id,
        agent_model=model,
        agent_max_tokens=max_tokens,
        agent_temperature=temperature,
        agent_system_prompt=prompt,
    )
    add_flash(request, "success", "Настройки агента сохранены")
    return RedirectResponse(f"{BACK}?tab=agent", status_code=302)


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
    if user_id is None:
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

    Two invariants the admin console cannot enforce for us: the caller must
    belong to this workspace, and the last owner cannot be demoted — a
    workspace with no owner has nobody who may manage it at all, so it would
    be unreachable rather than merely restricted.
    """
    from app.models.role import Role

    tenant_id = request.state.tenant_id

    denied = guard_web(request, "tenant", "update", back=BACK)
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

    new_role = role.strip().lower()
    if new_role not in ROLE_LABELS:
        add_flash(request, "error", f"Неизвестная роль: {role}")
        return RedirectResponse(f"{BACK}?tab=team", status_code=302)

    # Resolve role string → role_id
    codename_map = {"owner": "SUPERUSER", "admin": "ADMIN", "member": "VIEWER", "viewer": "VIEWER"}
    codename = codename_map.get(new_role)
    new_role_id: int | None = None
    if codename is not None:
        r = await Role.objects.filter(codename=codename).first()
        if r is not None:
            new_role_id = r.id

    if membership.is_owner and new_role != "owner":
        owners = [m for m in await tenant_users.web_memberships_for_tenant(tenant_id) if m.is_owner]
        if len(owners) <= 1:
            add_flash(request, "error", "Владелец должен остаться хотя бы один")
            return RedirectResponse(f"{BACK}?tab=team", status_code=302)

    await tenant_users.update_by_id(membership.id, role_id=new_role_id)
    add_flash(request, "success", f"Роль изменена на «{ROLE_LABELS[new_role]}»")
    return RedirectResponse(f"{BACK}?tab=team", status_code=302)


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
    if user is None or not getattr(user, "is_superuser", False):
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
            f"Тариф изменён на «{label}». Придётся сократить: {'; '.join(over)} — "
            f"до этого добавлять нельзя.",
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

    denied = guard_web(request, "tenant", "update", back=BACK)
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
