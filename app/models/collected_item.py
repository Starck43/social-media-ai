"""Compatibility aliases; canonical declarations live in domain packages."""

from .collection.collected_item import CollectedItem
from .managers.collected_item_manager import CollectedItemManager

__all__ = ["CollectedItem", "CollectedItemManager"]
