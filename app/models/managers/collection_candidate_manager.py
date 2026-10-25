"""Persistence for discovered collection candidates.

A candidate is an identity awaiting an operator decision, so this manager has
one rule that a plain upsert gets wrong: a re-collection must never reset an
existing decision. Rows are inserted with ON CONFLICT DO NOTHING on the
workspace identity key, which keeps the first stored evidence and leaves
`status` (including `confirmed`/`rejected`) untouched.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from sqlalchemy.dialects.postgresql import insert

from app.models.collection.collection_candidate import CollectionCandidate

from .base_manager import BaseManager


class CollectionCandidateManager(BaseManager[CollectionCandidate]):
    """Insert discovered identities once per workspace and platform."""

    def __init__(self) -> None:
        super().__init__(CollectionCandidate)

    async def store_candidates(
        self,
        session: Any,
        rows: Sequence[Mapping[str, Any]],
    ) -> int:
        """Insert candidates, ignoring identities this workspace already knows.

        Returns the number of rows actually inserted. A duplicate is not an
        error: the same person can react to two posts, and both lanes report
        it. Malformed rows are skipped rather than stored as anonymous
        identities.
        """
        prepared: list[dict[str, Any]] = []
        for row in rows:
            platform = str(row.get("platform") or "").strip().lower()
            kind = str(row.get("kind") or "person").strip().lower()
            external_id = str(row.get("external_id") or "").strip()
            if not platform or not kind or not external_id:
                continue
            prepared.append(
                await self._apply_tenant(
                    session,
                    {
                        "platform": platform,
                        "kind": kind,
                        "external_id": external_id,
                        "name": row.get("name"),
                        "url": row.get("url"),
                        "city": row.get("city"),
                        "city_source": row.get("city_source"),
                        "city_confidence": row.get("city_confidence"),
                        "metrics": row.get("metrics"),
                        "evidence": row.get("evidence"),
                        "status": row.get("status") or "new",
                        "source_id": row.get("source_id"),
                        "discovered_by": row.get("discovered_by"),
                    },
                )
            )
        if not prepared:
            return 0

        model = self.model
        statement = (
            insert(model)
            .values(prepared)
            .on_conflict_do_nothing(
                index_elements=[model.tenant_id, model.platform, model.kind, model.external_id],
            )
            .returning(model.id)
        )
        inserted = (await session.execute(statement)).scalars().all()
        return len(inserted)
