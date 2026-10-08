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


@app_label("social")
class CollectedItem(Base, TenantScopedMixin, TimestampMixin):
    """One fetched item, written raw before analysis and removed once it saved."""

    __tablename__ = "collected_items"
    __table_args__ = (
        Index("ix_collected_items_tenant_id", "tenant_id"),
        # The dedup lookup is "hashes this source has already seen".
        Index("ix_collected_items_source_hash", "source_id", "content_hash"),
        # "Что собрано" lists one run's items newest-first.
        Index("ix_collected_items_run_id", "run_id"),
        Index("ix_collected_items_source_published", "source_id", "published_at"),
        # A platform never hands over the same item twice within a live window;
        # the unique key makes a double insert (retry, overlapping run) a no-op
        # instead of a duplicate. A regular (non-partial) unique index is used
        # because PostgreSQL treats NULLs as distinct in UNIQUE indexes, so
        # multiple rows with a NULL external_id stay legal — and only a non-partial
        # index can serve as an ON CONFLICT DO NOTHING target.
        Index(
            "uq_collected_items_source_external",
            "source_id",
            "external_id",
            unique=True,
        ),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    # Which collect run produced the row. Not a foreign key: a pruned job row
    # must not take the raw content with it, and `prune` already drops old jobs.
    run_id: Mapped[int | None] = Column(Integer, nullable=True)
    source_id: Mapped[int] = Column(Integer, nullable=False)

    # Identity. `external_id` is the platform's own id (VK post id, Telegram
    # chat+message) and is the strongest key; `content_hash` covers items that
    # arrive without one. Both are built the same way `dedup.item_hash` does,
    # deliberately without metrics — engagement moves between fetches and must
    # not make an unchanged post look new.
    external_id: Mapped[str | None] = Column(String(255), nullable=True)
    content_hash: Mapped[str] = Column(String(64), nullable=False)
    platform: Mapped[str | None] = Column(String(50), nullable=True)

    published_at: Mapped[Any] = Column(DateTime(timezone=True), nullable=True)
    media_type: Mapped[str | None] = Column(String(20), nullable=True)
    # The body, verbatim. Long texts are truncated on render, not on store.
    text: Mapped[str | None] = Column(Text, nullable=True)
    metrics: Mapped[dict[str, Any] | None] = Column(JSON, nullable=True)
    author: Mapped[dict[str, Any] | None] = Column(JSON, nullable=True)
    # Link back to the original, so a truncated preview can be read in full.
    permalink: Mapped[str | None] = Column(String(500), nullable=True)

    # How many times analysis of this row has been attempted and failed, and the
    # ceiling after which it is no longer handed out (`CollectedItemManager
    # .for_source`). A timeout stores no analysis, so without a counter the row
    # is retried on every run and keeps costing a full request timeout — the
    # batch never drains. The row is never deleted for failing: the raw copy is
    # the only copy, and `handle_prune` is what eventually reclaims it.
    analyze_attempts: Mapped[int] = Column(Integer, nullable=False, default=0, server_default="0")
    give_up_after_attempts: Mapped[int] = Column(Integer, nullable=False, default=3, server_default="3")

    if TYPE_CHECKING:
        from .managers.base_manager import BaseManager
        from .managers.collected_item_manager import CollectedItemManager

        objects: ClassVar[CollectedItemManager | BaseManager]
    else:
        objects: ClassVar = None

    def as_agent_item(self) -> dict[str, Any]:
        """This row as the dict `analyze_content` expects for one item.

        The keys mirror what the platform clients emit, so analysis of stored
        rows and analysis of a live batch take the same path.
        """
        metrics = dict(self.metrics or {})
        return {
            **{
                key: metrics[key] for key in ("reactions", "comments", "views", "metric_availability") if key in metrics
            },
            "platform": self.platform,
            "external_id": self.external_id,
            "text": self.text or "",
            "published_at": self.published_at,
            "date": self.published_at,
            "media_type": self.media_type,
            "metrics": dict(self.metrics or {}),
            "author": dict(self.author or {}),
            "permalink": self.permalink,
            "content_hash": self.content_hash,
        }


from .managers.collected_item_manager import CollectedItemManager  # noqa: E402

CollectedItem.objects = CollectedItemManager()
