"""Raw content handed over by a platform, written before it is analysed.

The runtime used to pass the fetched batch straight to the analyser and drop
it on the floor: the texts lived in a local variable for the length of one call
and were gone afterward — including when that call crashed or the LLM was down.
Nothing could show *what* was collected, and the only dedup index was
`ai_analytics`, which stays empty for as long as analysis fails — so every
re-run reported the whole wall as "new" again.

A row here is one fetched item, in the shape the analyser already consumes
(`as_agent_item()`), so a deferred analysis feeds rows back through the very
same code path that a live collection does.

The rows are a write-ahead copy: written first, deleted only once an analysis
has actually stored the matching content (by item hash, so a partial analysis
retires only what it covered). Until then, they are the only copy, so a failed
analysis must never be treated as "processed", and `handle_prune` is the
ceiling on rows nobody ever analysed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped

from ..core.config import settings
from ..core.decorators import app_label
from .base import Base, TenantScopedMixin, TimestampMixin


from .collection.collected_item import CollectedItem


from .managers.collected_item_manager import CollectedItemManager  # noqa: E402

CollectedItem.objects = CollectedItemManager()
