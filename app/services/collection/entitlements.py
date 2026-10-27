"""Connector-owned product capability declarations and requirement derivation.

These are subscription rights, not provider API scopes or credential grants.
"""

from collections.abc import Iterable, Mapping
from typing import Any

CAPABILITIES = frozenset({"connector.vk.read", "connector.vk.search", "connector.telegram.read"})


def _value(value: Any) -> str:
    return str(getattr(value, "db_value", getattr(value, "value", value)) or "").lower()


def source_capabilities(sources: Iterable[Any]) -> set[str]:
    required = set()
    for source in sources:
        platform_row = getattr(source, "platform", None)
        platform = _value(getattr(platform_row, "platform_type", platform_row))
        operation = "search" if platform == "vk" and _value(source.source_type) == "search" else "read"
        # Unknown adapters intentionally produce an unregistered key (fail closed).
        required.add(f"connector.{platform or 'unknown'}.{operation}")
    return required


def intent_capabilities(payload: Mapping[str, Any], *, fallback_platform: str = "vk") -> set[str]:
    plan = payload.get("task_plan")
    plan = plan if isinstance(plan, dict) else {}
    from app.services.collection.intent import parse_collection_intent

    collection = parse_collection_intent({"collection": payload.get("collection") or plan.get("collection") or {}})
    required = set()
    platforms = set(plan.get("platforms") or [fallback_platform])
    discovery = collection.discovery
    if "vk" in platforms and (
        collection.queries or (discovery.queries and (discovery.people or discovery.communities))
    ):
        required.add("connector.vk.search")
    # A discovery can create walls; deny missing read rights before doing searches.
    if discovery.queries and (discovery.people or discovery.communities):
        if "vk" in platforms:
            required.add("connector.vk.read")
    return required
