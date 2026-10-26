"""Tools: list / add / disable content sources."""

from __future__ import annotations

import re
from typing import Any

from app.agent.tools import tool

# Friendly names → SourceType member names (store_as_name=True in DB).
_TYPE_ALIASES = {
    "user": "USER",
    "channel": "CHANNEL",
    "group": "GROUP",
    "page": "PAGE",
    "public": "PUBLIC",
    "chat": "CHAT",
    "search": "SEARCH",
}

# Friendly names → Platform.platform_type db value. Platform rows are named per
# deployment ("ВКонтакте", "Телеграм"), so the type column is the stable key.
_PLATFORM_ALIASES = {
    "vk": "vk",
    "vkontakte": "vk",
    "вк": "vk",
    "вконтакте": "vk",
    "telegram": "telegram",
    "tg": "telegram",
    "телеграм": "telegram",
    "max": "max",
    "макс": "max",
}


@tool(
    name="sources_list",
    description="Показать список источников контента (id, платформа, тип, активность).",
    parameters={
        "type": "object",
        "properties": {
            "only_active": {"type": "boolean", "description": "Только активные (по умолчанию true)"},
            "limit": {"type": "integer", "description": "Максимум строк (по умолчанию 20)"},
        },
        "required": [],
    },
    required_permission="collection.source.view",
)
async def sources_list(only_active: bool = True, limit: int = 20) -> list[dict[str, Any]]:
    from app.models import Source

    qs = (
        Source.objects.select_related("platform").filter(is_active=True)
        if only_active
        else Source.objects.select_related("platform")
    )
    rows = await qs.limit(min(int(limit), 100))
    return [_compact(s) for s in rows]


# Rows the model reads. A verbose row (display name, platform label, enum name,
# active flag) costs tokens on every turn the chat loads this list into context;
# `docs/AGENT.md` "Aggregate-first analytics tools" sets the same rule for the
# analytics tools. `source_get` / `source_add` can always reach the full row.
def _compact(source: Any, platform_key: str | None = None) -> dict[str, Any]:
    """The row the model needs, never a lazy relation of a detached object.

    `platform_key` is the caller's already-resolved platform (`vk`,
    `telegram`); without it the relation is read only when it is loaded.
    """
    if platform_key is None:
        platform = getattr(getattr(source, "platform", None), "platform_type", None)
        platform_key = getattr(platform, "db_value", platform)
    source_type = getattr(source, "source_type", None)
    return {
        "id": source.id,
        "name": source.name,
        "platform": platform_key or "?",
        "type": (getattr(source_type, "name", None) or "?").lower(),
        "external_id": source.external_id,
    }


@tool(
    name="sources_search",
    description=(
        "Найти сообщества или людей на платформе по строке запроса (как поиск на сайте). "
        "Возвращает кандидатов с external_id, который можно передать в source_add."
    ),
    parameters={
        "type": "object",
        "properties": {
            "platform": {"type": "string", "description": "vk (поиск людей/сообществ сейчас только ВКонтакте)"},
            "query": {"type": "string", "description": "Поисковая строка, например «дизайн интерьера Киров»"},
            "kind": {
                "type": "string",
                "enum": ["group", "page", "event", "user"],
                "description": "group/page/event — сообщества, user — люди (по умолчанию group)",
            },
            "city": {"type": "string", "description": "Город для сужения (например, Киров)"},
            "limit": {"type": "integer", "description": "Максимум кандидатов (по умолчанию 10, максимум 50)"},
        },
        "required": ["platform", "query"],
    },
    required_permission="collection.source.view",
)
async def sources_search(
    platform: str, query: str, kind: str = "group", city: str | None = None, limit: int = 10
) -> dict:
    """Platform search that feeds `source_add` instead of guessing a wall id.

    VK `groups.search`/`users.search` need a user token, which is why the
    result can be an actionable authorization error rather than an empty list.
    """
    from types import SimpleNamespace

    from app.core.tenant_context import current_tenant_id
    from app.models import Platform
    from app.services.social.credentials import AuthorizationRequired
    from app.services.social.factory import get_social_client
    from app.services.social.owner import resolve_source_owner

    if not isinstance(query, str) or not query.strip() or len(query.strip()) > 200:
        return {"error": "Укажите поисковую фразу длиной от 1 до 200 символов", "code": "query_invalid"}
    if kind not in {"group", "page", "event", "user"}:
        return {"error": "Тип поиска должен быть group, page, event или user", "code": "kind_invalid"}
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        return {"error": "Лимит кандидатов должен быть от 1 до 50", "code": "limit_invalid"}
    query = query.strip()
    platform_key = _PLATFORM_ALIASES.get(platform.strip().lower())
    if platform_key is None:
        return {"error": f"Unknown platform: {platform}. Available: vk, telegram, max"}
    if platform_key != "vk":
        return {"error": "Поиск источников сейчас поддержан только для ВКонтакте"}

    plat = await Platform.objects.filter(platform_type="vk").first()
    if plat is None:
        return {"error": "Платформа ВКонтакте не настроена в этом окружении"}

    # The search runs as the workspace owner, exactly like source collection —
    # a personal token may only be used inside a workspace the person belongs to.
    owner = await resolve_source_owner(SimpleNamespace(tenant_id=current_tenant_id(), params={}))
    client = get_social_client(plat)

    items: list[dict] = []
    resolved_city = None
    try:
        city_id = None
        if city:
            if not isinstance(city, str) or not city.strip() or len(city.strip()) > 120:
                return {"error": "Укажите корректное название города", "code": "city_invalid"}
            cities = await client.get_cities(city.strip(), owner_user_id=owner, count=20)
            exact = [item for item in cities if str(item.get("title", "")).casefold() == city.strip().casefold()]
            if len(exact) == 1:
                resolved_city = exact[0]
            elif len(cities) == 1:
                resolved_city = cities[0]
            elif cities:
                return {
                    "error": "Найдено несколько одноимённых городов. Уточните регион или выберите вариант.",
                    "code": "city_ambiguous",
                    "candidates": cities[:10],
                }
            else:
                return {
                    "error": "Город не найден; поиск без ограничения по городу не выполнялся.",
                    "code": "city_not_found",
                }
            city_id = resolved_city["id"]
        count = limit
        if kind == "user":
            items = await client.search_users(query, owner_user_id=owner, city_id=city_id, count=count)
        else:
            group_type = kind if kind in ("page", "event") else None
            items = await client.search_groups(
                query, owner_user_id=owner, group_type=group_type, city_id=city_id, count=count
            )
    except AuthorizationRequired as error:
        return {
            "error": str(error),
            "hint": error.hint,
            "needs_action": "connect_vk",
            "code": "authorization_required",
        }
    except Exception as error:
        from app.services.social.rate_limit import SourceRateLimited

        if isinstance(error, SourceRateLimited):
            return {
                "error": "VK ограничил частоту запросов; повторите поиск позже.",
                "code": "rate_limited",
            }
        if not isinstance(error, RuntimeError):
            raise
        match = re.search(r"Ошибка API \((\d+)\)", str(error))
        code = int(match.group(1)) if match else None
        if code == 1051:
            return {
                "error": "Поисковый метод VK недоступен для типа приложения этого окружения.",
                "code": "api_1051",
                "hint": "Смена личного токена сама по себе не меняет доступность метода для профиля приложения.",
                "needs_action": "vk_app_search_scope",
            }
        if code == 5:
            return {
                "error": "VK отклонил авторизацию. Переподключите VK и повторите поиск.",
                "code": "authorization_failed",
            }
        if code == 15:
            return {"error": "VK запретил доступ к этому поиску.", "code": "access_denied"}
        if code in {6, 9}:
            return {"error": "VK ограничил частоту запросов; повторите поиск позже.", "code": "rate_limited"}
        return {
            "error": "VK не выполнил поиск.",
            "code": f"vk_api_error:{code}" if code else "vk_api_error",
        }

    return {
        "query": query,
        "kind": kind,
        "city": resolved_city,
        "count": len(items),
        "items": items,
        "next": "source_add(platform='vk', source_type=..., external_id=...) для выбранных кандидатов",
    }


@tool(
    name="source_add",
    description="Добавить новый источник контента (группу/канал/пользователя).",
    confirm=True,
    required_permission="collection.source.create",
    parameters={
        "type": "object",
        "properties": {
            "platform": {"type": "string", "description": "vk | telegram"},
            "source_type": {
                "type": "string",
                "description": "user | group | channel | page | public | chat | search",
            },
            "external_id": {"type": "string", "description": "ID или username источника в платформе"},
            "name": {"type": "string", "description": "Отображаемое имя"},
            "query": {"type": "string", "description": "Для source_type=search: поисковая фраза"},
            "owner_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Для source_type=search: оставить только эти стены (id сообществ отрицательные)",
            },
            "mode": {
                "type": "string",
                "enum": ["auto", "api", "user"],
                "description": "Способ доступа платформы; auto сохраняет существующее поведение",
            },
            "token_owner": {
                "type": "integer",
                "description": "ID активного участника текущего workspace, чей личный токен разрешено использовать",
            },
        },
        "required": ["platform", "source_type", "external_id"],
    },
)
async def source_add(
    platform: str,
    source_type: str,
    external_id: str,
    name: str | None = None,
    query: str | None = None,
    owner_ids: list[int] | None = None,
    mode: str = "auto",
    token_owner: int | None = None,
) -> dict:
    from app.models import Platform
    from app.models.managers.source_manager import SourceManager
    from app.types import SourceType

    platform_key = _PLATFORM_ALIASES.get(platform.strip().lower())
    if platform_key is None:
        return {"error": f"Unknown platform: {platform}. Available: vk, telegram, max"}

    plat = await Platform.objects.filter(platform_type=platform_key).first()
    if plat is None:
        return {"error": f"Platform {platform_key} is not configured in this deployment"}

    member_name = _TYPE_ALIASES.get(source_type.strip().lower(), source_type.strip().upper())
    try:
        st = SourceType[member_name]
    except KeyError:
        return {"error": f"Unknown source_type: {source_type}. Use one of: {', '.join(_TYPE_ALIASES)}"}

    if st == SourceType.SEARCH and not (query or external_id).strip():
        return {"error": "Для поискового источника нужна фраза (query)"}
    if mode not in {"auto", "api", "user"}:
        return {"error": "Способ доступа должен быть auto, api или user", "code": "mode_invalid"}
    params: dict[str, Any] = {"mode": mode} if mode != "auto" else {}
    if token_owner is not None:
        if isinstance(token_owner, bool) or not isinstance(token_owner, int) or token_owner <= 0:
            return {
                "error": "token_owner должен быть ID активного участника workspace",
                "code": "token_owner_invalid",
            }
        from app.core.tenant_context import current_tenant_id
        from app.models.managers.tenant_manager import tenant_users

        members = await tenant_users.web_memberships_for_tenant(current_tenant_id())
        if token_owner not in {member.user_id for member in members}:
            return {
                "error": "Владелец личного подключения не состоит в этом workspace",
                "code": "token_owner_forbidden",
            }
        params["token_owner"] = token_owner
    if st == SourceType.SEARCH:
        params["query"] = (query or external_id).strip()
        if owner_ids is not None:
            if len(owner_ids) > 1_000 or any(
                isinstance(value, bool) or not isinstance(value, int) or value == 0 for value in owner_ids
            ):
                return {
                    "error": "owner_ids должны содержать не более 1000 ненулевых целых ID VK",
                    "code": "owner_ids_invalid",
                }
            params["owner_ids"] = owner_ids
    elif owner_ids is not None:
        return {"error": "owner_ids допустимы только для поискового источника", "code": "owner_ids_invalid"}

    existing = await SourceManager().get_source_by_platform(plat.id, external_id.strip())
    if existing is not None:
        # Re-adding is not an error: the caller asked for an outcome ("this
        # source is in the workspace"), and a duplicate is that outcome.
        return {"status": "exists", **_compact(existing, platform_key)}
    source = await SourceManager().create_source(
        platform_id=plat.id,
        source_type=st,
        external_id=external_id.strip(),
        name=name or external_id.strip(),
        params=params or None,
    )
    # The created row is detached from its session: reading `source.platform`
    # here would raise a detached lazy-load error instead of confirming the
    # source. The platform key is already known from the lookup above.
    return {"status": "created", **_compact(source, platform_key)}


@tool(
    name="source_disable",
    description="Отключить сбор из источника по id (сбор по нему прекратится).",
    confirm=True,
    required_permission="collection.source.update",
    parameters={
        "type": "object",
        "properties": {"source_id": {"type": "integer", "description": "ID источника"}},
        "required": ["source_id"],
    },
)
async def source_disable(source_id: int) -> dict:
    from app.models import Source

    source = await Source.objects.get(id=int(source_id))
    if source is None:
        return {"error": f"Source {source_id} not found"}
    await Source.objects.update_by_id(source.id, is_active=False)
    return {"status": "disabled", "source_id": source.id, "name": source.name}


@tool(
    name="source_enable",
    description="Снова включить сбор из ранее отключённого источника по id.",
    confirm=True,
    required_permission="collection.source.update",
    parameters={
        "type": "object",
        "properties": {"source_id": {"type": "integer", "description": "ID источника"}},
        "required": ["source_id"],
    },
)
async def source_enable(source_id: int) -> dict:
    from app.models import Source

    source = await Source.objects.get(id=int(source_id))
    if source is None:
        return {"error": f"Source {source_id} not found"}
    await Source.objects.update_by_id(source.id, is_active=True)
    return {"status": "enabled", "source_id": source.id, "name": source.name}
