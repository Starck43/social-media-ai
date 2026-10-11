"""Manager for :class:`CollectedItem` — the raw-content staging area.

Everything here is about one property of the table: it is a queue, not an
archive. Rows arrive from a collection, sit unread, and leave once an analysis
has consumed them — so every method is either "put", "what is waiting" or
"discard what has been dealt with".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import JSON, bindparam, text as sa_text

from app.core.config import settings
from app.utils.content_attachments import normalize_attachments

from .base_manager import BaseManager


def _staged_mutation_scope() -> tuple[str, dict[str, int]]:
    """Scope raw staged writes; only explicit platform bypass is unfiltered."""
    from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass

    if is_bypass():
        return "", {}
    tenant_id = current_tenant_id()
    if type(tenant_id) is not int or tenant_id <= 0:
        raise TenantContextError("Staged mutation requires a valid tenant or explicit bypass")
    return " AND tenant_id = :tenant_id", {"tenant_id": tenant_id}


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

        Returns the driver-reported insertion count when positive; otherwise
        a bounded persisted run count (or the legacy no-run estimate). The
        fallback does not prove how many rows were newly inserted on a replay.
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
            "attachments",
            "text",
            "metrics",
            "author",
            "permalink",
        ]
        # JSONB columns must be passed as json, not as a Python dict literal:
        # asyncpg would send the repr as a string.
        json_columns = {"metrics", "author", "attachments"}
        # CAST, not a trailing "::jsonb": next to a bind parameter that cast
        # syntax is parsed as part of the parameter's name and never bound.
        placeholders = ", ".join(f"CAST(:{c} AS jsonb)" if c in json_columns else f":{c}" for c in columns)
        stmt = sa_text(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
            "ON CONFLICT (source_id, external_id) DO NOTHING"
        )
        stmt = stmt.bindparams(*(bindparam(column, type_=JSON(none_as_null=True)) for column in json_columns))
        # Same tenant stamping the ORM path does: refuses to write rather than
        # guessing a workspace for rows collected outside a tenant context.
        params = [
            await self._apply_tenant(session, {**row, "attachments": normalize_attachments(row.get("attachments"))})
            for row in rows
        ]
        result = await session.execute(stmt, params)

        count = int(result.rowcount or 0)
        if count > 0:
            return count
        # An executemany over ON CONFLICT may report no rowcount (-1).
        # Read back persisted rows for the chosen run; existing rows from a
        # replay may be counted, so this is not a measured new-insertion count.
        probe = next((r.get("run_id") for r in params if r.get("run_id") is not None), None)
        if probe is None:
            # Nothing ties these rows to a run, so there is no handle to count by.
            return len(params)
        # A run can contain several sources/workspaces. Count only the stamped
        # tenant/source pairs in this batch for the selected run, not its neighbours.
        scope_pairs = dict.fromkeys(
            (row["tenant_id"], row["source_id"]) for row in params if row.get("run_id") == probe
        )
        count_params: dict[str, Any] = {"run_id": probe}
        scope_clauses = []
        for index, (tenant_id, source_id) in enumerate(scope_pairs):
            scope_clauses.append(f"(tenant_id = :tenant_id_{index} AND source_id = :source_id_{index})")
            count_params[f"tenant_id_{index}"] = tenant_id
            count_params[f"source_id_{index}"] = source_id
        stored = await session.execute(
            sa_text(
                f"SELECT count(*) FROM {table} WHERE run_id = :run_id "
                f"AND ({' OR '.join(scope_clauses)})"
            ),
            count_params,
        )
        return int(stored.scalar() or 0)

    async def admit_items(self, session: Any, rows: Sequence[dict[str, Any]]) -> int:
        """Strict borrowed-transaction receipt, not the legacy rowcount estimate.

        Caller holds the owning Source row lock. Existing staged identities are
        locked against concurrent retirement; covered parent hashes do not stage
        again. No bypass, attempt reset, payload replacement, commit or rollback.
        """
        from sqlalchemy import cast, select
        from sqlalchemy.dialects.postgresql import JSONB, array, insert
        from app.core.tenant_context import current_tenant_id, is_bypass
        from app.models import AIAnalytics

        tenant_id = current_tenant_id()
        if is_bypass() or type(tenant_id) is not int or tenant_id <= 0:
            raise ValueError("content_tenant_invalid")
        if not rows:
            return 0
        source_ids = {row.get("source_id") for row in rows}
        if len(source_ids) != 1 or any(type(value) is not int or value <= 0 for value in source_ids):
            raise ValueError("content_source_invalid")
        source_id = next(iter(source_ids))
        expected = {}
        for row in rows:
            if row.get("tenant_id", tenant_id) != tenant_id:
                raise ValueError("content_tenant_invalid")
            identity, digest = row.get("external_id"), row.get("content_hash")
            if type(identity) is not str or not identity or type(digest) is not str or len(digest) != 64:
                raise ValueError("content_identity_invalid")
            if identity in expected and expected[identity]["content_hash"] != digest:
                raise ValueError("content_identity_conflict")
            expected[identity] = row
        model = self.model
        scope = (model.tenant_id == tenant_id, model.source_id == source_id)
        existing = (await session.execute(select(model.external_id, model.content_hash).where(
            *scope, model.external_id.in_(list(expected)),
        ).with_for_update())).all()
        present = dict(existing)
        missing = []
        for identity, row in expected.items():
            if identity in present:
                if present[identity] != row["content_hash"]:
                    raise ValueError("content_identity_conflict")
            else:
                missing.append(row)
        if not missing:
            return 0
        # One scoped coverage lookup for the whole batch, not one round trip per
        # post. JSONB ?| is only a prefilter; malformed/non-array ledgers cannot
        # become receipt evidence when decoding the returned summaries below.
        summaries = (await session.execute(select(AIAnalytics.summary_data).where(
            AIAnalytics.tenant_id == tenant_id, AIAnalytics.source_id == source_id,
            cast(AIAnalytics.summary_data, JSONB)["content_hashes"].has_any(
                array(list(dict.fromkeys(row["content_hash"] for row in missing)))
            ),
        ))).scalars().all()
        covered = {
            digest for summary in summaries if type(summary) is dict
            and type(summary.get("content_hashes")) is list
            for digest in summary["content_hashes"] if type(digest) is str
        }
        pending = [await self._apply_tenant(session, {
            **row, "attachments": normalize_attachments(row.get("attachments")),
        }) for row in missing if row["content_hash"] not in covered]
        if not pending:
            return 0
        inserted = (await session.execute(insert(model).values(pending).on_conflict_do_nothing(
            index_elements=[model.source_id, model.external_id],
        ).returning(model.external_id))).scalars().all()
        # A concurrent non-admission writer must not turn a conflicting identity
        # into a successful receipt. Read every pending identity back exactly.
        stored = dict((await session.execute(select(model.external_id, model.content_hash).where(
            *scope, model.external_id.in_([row["external_id"] for row in pending]),
        ).with_for_update())).all())
        if any(stored.get(row["external_id"]) != row["content_hash"] for row in pending):
            raise ValueError("content_receipt_missing")
        return len(inserted)

    async def for_source(
        self,
        source_id: int,
        limit: int = 50,
        *,
        include_exhausted: bool = False,
        start_date: Any = None,
        end_date: Any = None,
    ) -> list[CollectedItem]:
        """The raw items waiting for this source, newest first.

        Rows that have burned their `give_up_after_attempts` are skipped by
        default: an item the LLM cannot process (a timeout stores no analysis,
        so the row is never retired) would otherwise be handed out on every
        single run, each time costing a full request timeout and holding the
        batch back. They stay on disk — `include_exhausted` still reads them, and
        `handle_prune` is what reclaims them.

        `start_date`/`end_date` (date or datetime) restrict the drain to items
        whose `published_at` falls inside the content window — the same window
        that gated collection, so an analyze run never touches content outside
        the task's configured period.
        """
        from app.models.collected_item import CollectedItem

        query = self.filter(source_id=source_id)
        if not include_exhausted:
            query = query.filter(CollectedItem.analyze_attempts < CollectedItem.give_up_after_attempts)
        if start_date is not None:
            query = query.filter(CollectedItem.published_at >= start_date)
        if end_date is not None:
            query = query.filter(CollectedItem.published_at <= end_date)
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
        tenant_clause, tenant_params = _staged_mutation_scope()
        result = await session.execute(
            sa_text(
                f"UPDATE {settings.DB_SCHEMA}.collected_items SET analyze_attempts = analyze_attempts + 1 "
                "WHERE source_id = :source_id AND content_hash = ANY(:hashes)" + tenant_clause
            ),
            {"source_id": source_id, "hashes": list(wanted), **tenant_params},
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
        tenant_clause, tenant_params = _staged_mutation_scope()
        result = await session.execute(
            sa_text(
                f"DELETE FROM {settings.DB_SCHEMA}.collected_items "
                "WHERE source_id = :source_id AND content_hash = ANY(:hashes)" + tenant_clause
            ),
            {"source_id": source_id, "hashes": list(wanted), **tenant_params},
        )
        return int(result.rowcount or 0)

    async def delete_older_than(self, session: Any, days: int) -> int:
        """Expire old staged rows in the current tenant or explicit bypass.

        The caller owns the transaction. Missing/malformed tenant context fails
        before SQL; only explicit platform bypass permits a schema-wide sweep.
        Retention age and exhausted-item eligibility are unchanged.
        """
        from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass

        tenant_clause = ""
        params: dict[str, Any] = {}
        if not is_bypass():
            tenant_id = current_tenant_id()
            if type(tenant_id) is not int or tenant_id <= 0:
                raise TenantContextError("Staged retention requires a valid tenant or explicit bypass")
            tenant_clause = " AND tenant_id = :tenant_id"
            params["tenant_id"] = tenant_id
        params["cutoff"] = datetime.now(timezone.utc) - timedelta(days=days)
        result = await session.execute(
            sa_text(
                f"DELETE FROM {settings.DB_SCHEMA}.collected_items "
                "WHERE created_at < :cutoff" + tenant_clause
            ),
            params,
        )
        return int(result.rowcount or 0)

    async def reset_attempts(self, session: Any, source_id: int) -> int:
        """Reset `analyze_attempts` to 0 for all staged rows of a source.

        Used when an operator triggers a manual retry (e.g. after fixing the LLM
        model or raising `give_up_after_attempts`). Returns the number of rows
        affected.
        """
        tenant_clause, tenant_params = _staged_mutation_scope()
        result = await session.execute(
            sa_text(
                f"UPDATE {settings.DB_SCHEMA}.collected_items "
                "SET analyze_attempts = 0 "
                "WHERE source_id = :source_id AND analyze_attempts > 0" + tenant_clause
            ),
            {"source_id": source_id, **tenant_params},
        )
        return int(result.rowcount or 0)
