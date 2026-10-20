"""Collection models with lazy exports. Managers bind in app.models.bootstrap."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .collected_item import CollectedItem
    from .collection_candidate import CollectionCandidate
    from .collection_lane_run import CollectionLaneRun
    from .collection_seen import CollectionSeen
    from .platform import Platform
    from .source import Source

__all__ = [
    "Platform",
    "Source",
    "CollectedItem",
    "CollectionSeen",
    "CollectionCandidate",
    "CollectionLaneRun",
]
_MODULES = {
    "Platform": "platform",
    "Source": "source",
    "CollectedItem": "collected_item",
    "CollectionSeen": "collection_seen",
    "CollectionCandidate": "collection_candidate",
    "CollectionLaneRun": "collection_lane_run",
}


def __getattr__(name: str):
    if name not in _MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{_MODULES[name]}", __name__), name)
    globals()[name] = value
    return value
