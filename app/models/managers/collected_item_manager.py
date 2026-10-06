"""Manager for :class:`CollectedItem` — the raw-content staging area.

Everything here is about one property of the table: it is a queue, not an
archive. Rows arrive from a collection, sit unread, and leave once an analysis
has consumed them — so every method is either "put", "what is waiting" or
"discard what has been dealt with".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import text as sa_text

from app.core.config import settings

from .base_manager import BaseManager


class CollectedItemManager(BaseManager["CollectedItem"]):
    """Store, list and retire raw collected items."""

    async def store_items(
        self,
        session: Any,
        rows: Sequence[dict[str, Any]],
    ) -> int:
        """Insert raw items, ignoring ones already staged for the source.

        Uses ON CONFLICT DO NOTHING rather than a plain insert: a retried run
        or two overlapping collections must not blow up on the unique key, and
        a duplicate is never worth failing a collection over.

        Returns the number of rows actually inserted.
        """
        if not rows:
            return 0

        table = f"{settings.DB_SCHEMA}.collected_items"
        columns = [
            "tenant_id",
            "run_id",
            "source_id",
            "external_id",
            "content_hash",
            "platform",
            "published_at",
            "media_type",
            "text",
            "metrics",
            "author",
            "permalink",
        ]
        # JSONB columns must be passed as json, not as a Python dict literal:
        # asyncpg would send the repr as a string.
        json_columns = {"metrics", "author"}
        # CAST, not a trailing "::jsonb": next to a bind parameter that cast
        # syntax is parsed as part of the parameter's name and never bound.
        placeholders = ", ".join(
            f"CAST(:{c} AS jsonb)" if c in json_columns else f":{c}" for c in columns
        )
        stmt = sa_text(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
            "ON CONFLICT DO NOTHING"
        )
        # Same tenant stamping the ORM path does: refuses to write rather than
        # guessing a workspace for rows collected outside a tenant context.
        params = [await self._apply_tenant(session, dict(row)) for row in rows]
        result = await session.execute(stmt, params)

        count = int(result.rowcount or 0)
        if count >= 0:
            return count
        # An executemany over ON CONFLICT reports no rowcount (-1), so the real
        # number of staged rows is read back rather than assumed from the input
        # size: a re-run that collides on the unique key staged nothing new.
        probe = next((r.get("run_id") for r in params if r.get("run_id") is not None), None)
        if probe is None:
            # Nothing ties these rows to a run, so there is no handle to count by.
            return len(params)
        stored = await session.execute(
            sa_text(
                f"SELECT count(*) FROM {settings.DB_SCHEMA}.collected_items WHERE run_id = :run_id"
            ),
            {"run_id": probe},
        )
        return int(stored.scalar() or 0)

    async def for_source(
        self,
        source_id: int,
        limit: int = 50,
        *,
        include_exhausted: bool = False,
    ) -> list[CollectedItem]:
        """The raw items waiting for this source, newest first.

        Rows that have burned their `give_up_after_attempts` are skipped by
        default: an item the LLM cannot process (a timeout stores no analysis,
        so the row is never retired) would otherwise be handed out on every
        single run, each time costing a full request timeout and holding the
        batch back. They stay on disk — `include_exhausted` still reads them, and
        `handle_prune` is what reclaims them.
        """
        from app.models.collected_item import CollectedItem

        query = self.filter(source_id=source_id)
        if not include_exhausted:
            query = query.filter(CollectedItem.analyze_attempts < CollectedItem.give_up_after_attempts)
        return list(await query.order_by(CollectedItem.published_at.desc().nullslast()).limit(limit))

    async def exhausted_count(self, source_id: int) -> int:
        """How many staged rows for a source have burned their ceiling."""
        from app.models.collected_item import CollectedItem

        # Both columns are read because the ceiling is per row: one workspace may
        # have widened it while another kept the default.
        rows = (
            await self.filter(source_id=source_id)
            .values(CollectedItem.analyze_attempts, CollectedItem.give_up_after_attempts)
            .rows()
        )
        return sum(1 for r in rows if r and r[0] is not None and r[0] >= (r[1] or 0))

    async def record_attempts(self, session: Any, source_id: int, hashes: Sequence[str]) -> int:
        """Count one failed analysis attempt against the given staged rows.

        Called when an analysis of `hashes` produced nothing to store, so the
        next runs stop offering the same rows once the ceiling is reached.
        Never deletes anything: a failing row still holds the only copy of its
        content, and dropping it here would lose data the collect step fetched.
        """
        wanted = [h for h in dict.fromkeys(hashes) if h]
        if not wanted:
            return 0
        result = await session.execute(
            sa_text(
                f"UPDATE {settings.DB_SCHEMA}.collected_items SET analyze_attempts = analyze_attempts + 1 "
                "WHERE source_id = :source_id AND content_hash = ANY(:hashes)"
            ),
            {"source_id": source_id, "hashes": list(wanted)},
        )
        return int(result.rowcount or 0)

    async def for_run(self, run_id: int, limit: int = 50) -> list[CollectedItem]:
        """Raw items of one collect run, newest first."""
        from app.models.collected_item import CollectedItem

        return list(
            await self.filter(run_id=run_id)
            .order_by(CollectedItem.published_at.desc().nullslast())
            .limit(limit)
        )

    async def hashes_for_source(self, source_id: int) -> set[str]:
        """Content hashes currently staged for a source (an unread ledger)."""
        from app.models.collected_item import CollectedItem

        # The column object, not its name: a bare string is taken as a text()
        # expression, which this query rejects outright. `rows()` is the
        # documented read path for a `values()` query.
        # One column, read positionally: `rows()` yields Row objects, which
        # answer to indexing but not to `.get`.
        rows = await self.filter(source_id=source_id).values(CollectedItem.content_hash).rows()
        return {r[0] for r in rows if r and r[0]}

    async def delete_hashes(self, session: Any, source_id: int, hashes: Sequence[str]) -> int:
        """Retire the raw items whose content an analysis has actually saved.

        The precise counterpart to "analyse first, store the raw copy": the
        caller passes the item hashes that ended up in
        `ai_analytics.summary_data["content_hashes"]`, so a partial analysis
        (one day of three failed) leaves exactly the unanalysed rows behind
        instead of declaring the whole batch consumed.

        Keyed on the hash rather than the run so it works for collections that
        carry no job at all (API endpoints, `--src` on the CLI, the agent tool):
        `run_id` is NULL there, and a NULL-keyed delete would either miss or
        sweep unrelated rows.
        """
        wanted = [h for h in dict.fromkeys(hashes) if h]
        if not wanted:
            return 0
        result = await session.execute(
            sa_text(
                f"DELETE FROM {settings.DB_SCHEMA}.collected_items "
                "WHERE source_id = :source_id AND content_hash = ANY(:hashes)"
            ),
            {"source_id": source_id, "hashes": list(wanted)},
        )
        return int(result.rowcount or 0)

    async def delete_older_than(self, session: Any, days: int) -> int:
        """Backstop for rows whose run never produced an analysis.

        A collection whose analysis failed keeps its raw items on purpose; this
        is the ceiling on that, so a permanently failing source cannot grow the
        table without bound.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        result = await session.execute(
            sa_text(
                f"DELETE FROM {settings.DB_SCHEMA}.collected_items "
                "WHERE created_at < :cutoff"
            ),
            {"cutoff": cutoff},
        )
        return int(result.rowcount or 0)

    async def reset_attempts(self, session: Any, source_id: int) -> int:
        """Reset `analyze_attempts` to 0 for all staged rows of a source.

        Used when an operator triggers a manual retry (e.g. after fixing the LLM
        model or raising `give_up_after_attempts`). Returns the number of rows
        affected.
        """
        result = await session.execute(
            sa_text(
                f"UPDATE {settings.DB_SCHEMA}.collected_items "
                "SET analyze_attempts = 0 "
                "WHERE source_id = :source_id AND analyze_attempts > 0"
            ),
            {"source_id": source_id},
        )
        return int(result.rowcount or 0)
