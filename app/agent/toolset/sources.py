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
)
async def sources_list(only_active: bool = True, limit: int = 20) -> list[dict[str, Any]]:
    from app.models import Source

    qs = (
        Source.objects.select_related("platform").filter(is_active=True)
        if only_active
        else Source.objects.select_related("platform")
    )
    rows = await qs.limit(min(int(limit), 100))
    return [
        {
            "id": s.id,
            "name": s.name,
            "platform": s.platform.name if s.platform else "?",
            "type": s.source_type.name if s.source_type else "?",
            "external_id": s.external_id,
            "is_active": s.is_active,
        }
        for s in rows
    ]


@tool(
    name="source_add",
    description="Добавить новый источник контента (группу/канал/пользователя).",
    confirm=True,
    parameters={
        "type": "object",
        "properties": {
            "platform": {"type": "string", "description": "vk | telegram"},
            "source_type": {"type": "string", "description": "user | group | channel | page | public | chat"},
            "external_id": {"type": "string", "description": "ID или username источника в платформе"},
            "name": {"type": "string", "description": "Отображаемое имя"},
        },
        "required": ["platform", "source_type", "external_id"],
    },
)
async def source_add(platform: str, source_type: str, external_id: str, name: str | None = None) -> dict:
    from app.models import Platform
    from app.models.managers.source_manager import SourceManager
    from app.types import SourceType

    plat = await Platform.objects.get(name=platform.lower())
    if plat is None:
        return {"error": f"Unknown platform: {platform}. Available: vk, telegram"}

    member_name = _TYPE_ALIASES.get(source_type.strip().lower(), source_type.strip().upper())
    try:
        st = SourceType[member_name]
    except KeyError:
        return {"error": f"Unknown source_type: {source_type}. Use one of: {', '.join(_TYPE_ALIASES)}"}

    source = await SourceManager().create_source(
        platform_id=plat.id,
        source_type=st,
        external_id=external_id.strip(),
        name=name or external_id.strip(),
    )
    return {"status": "created", "source_id": source.id, "name": source.name}


@tool(
    name="source_disable",
    description="Отключить сбор из источника по id (сбор по нему прекратится).",
    confirm=True,
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
