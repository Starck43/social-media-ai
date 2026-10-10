"""Collection-domain models with lazy exports preserving root import order.

The original root imports CollectedItem, Platform and Source at different
points. Eagerly importing all three here would change mapper registration order.
Legacy modules retain manager binding; canonical leaves only declare models.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .collected_item import CollectedItem
    from .platform import Platform
    from .source import Source

__all__ = ["Platform", "Source", "CollectedItem"]
_MODULES = {"Platform": "platform", "Source": "source", "CollectedItem": "collected_item"}


def __getattr__(name: str):
    if name not in _MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{_MODULES[name]}", __name__), name)
    globals()[name] = value
    return value
