"""Tools: list / add / disable content sources."""

from __future__ import annotations

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
            cities = await client.get_cities(city, owner_user_id=owner, count=5)
            if cities:
                resolved_city = cities[0]
                city_id = cities[0]["id"]
        count = max(1, min(int(limit), 50))
        if kind == "user":
            items = await client.search_users(query, owner_user_id=owner, city_id=city_id, count=count)
        else:
            group_type = kind if kind in ("page", "event") else None
            items = await client.search_groups(
                query, owner_user_id=owner, group_type=group_type, city_id=city_id, count=count
            )
    except AuthorizationRequired as error:
        return {"error": str(error), "hint": error.hint, "needs_action": "connect_vk"}
    except RuntimeError as error:
        # VK answers 1051 for search methods when the app profile type is not
        # allowed to call them; say so instead of a generic collection failure.
        return {
            "error": str(error),
            "hint": "Поисковые методы VK недоступны для текущего типа приложения (ошибка 1051).",
            "needs_action": "vk_app_search_scope",
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

    existing = await SourceManager().get_source_by_platform(plat.id, external_id.strip())
    if existing is not None:
        # Re-adding is not an error: the caller asked for an outcome ("this
        # source is in the workspace"), and a duplicate is that outcome.
        return {"status": "exists", **_compact(existing, platform_key)}
    params: dict = {}
    if st == SourceType.SEARCH:
        params["query"] = (query or external_id).strip()
        if owner_ids:
            params["owner_ids"] = [int(value) for value in owner_ids]
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
