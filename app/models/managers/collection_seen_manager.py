"""Persistence helpers for collection-time selection evidence."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from app.core.tenant_context import current_tenant_id, is_bypass
from app.models.collection.collection_seen import CollectionSeen

from .base_manager import BaseManager


class CollectionSeenManager(BaseManager[CollectionSeen]):
    """Upsert the dedupe/new-item ledger without ever storing text."""

    def __init__(self) -> None:
        super().__init__(CollectionSeen)

    async def record_outcomes(
        self,
        session: Any,
        source_id: int,
        outcomes: Sequence[Mapping[str, Any]],
        *,
        tenant_id: int | None = None,
    ) -> int:
        if not outcomes:
            return 0
        current = current_tenant_id()
        stamp = tenant_id if is_bypass() and tenant_id is not None else current
        if type(stamp) is not int or stamp <= 0:
            raise ValueError("collection_seen_tenant_invalid")
        if tenant_id is not None and not is_bypass() and tenant_id != stamp:
            raise ValueError("collection_seen_tenant_mismatch")

        rows = []
        for outcome in outcomes:
            external_id = outcome.get("external_id")
            content_hash = outcome.get("content_hash")
            if not isinstance(external_id, str) or not external_id:
                continue
            if not isinstance(content_hash, str) or not content_hash:
                continue
            content_type = str(outcome.get("content_type") or "post")
            status = str(outcome.get("status") or "selected")
            rows.append(
                {
                    "tenant_id": stamp,
                    "source_id": source_id,
                    "content_type": content_type,
                    "external_id": external_id,
                    "content_hash": content_hash,
                    "status": status,
                    "last_seen_at": func.now(),
                    "updated_at": func.now(),
                }
            )
        if not rows:
            return 0
        statement = insert(self.model).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[
                self.model.tenant_id,
                self.model.source_id,
                self.model.content_type,
                self.model.external_id,
            ],
            set_={
                "content_hash": statement.excluded.content_hash,
                "status": statement.excluded.status,
                "last_seen_at": func.now(),
                "updated_at": func.now(),
            },
        )
        await session.execute(statement)
        return len(rows)

    async def hashes_for_source(self, source_id: int) -> set[str]:
        rows = await self.filter(source_id=source_id).values(self.model.content_hash).rows()
        return {row[0] for row in rows if row and isinstance(row[0], str)}
