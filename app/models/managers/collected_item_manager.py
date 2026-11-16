"""Manager for :class:`CollectedItem` — the raw-content staging area.

Everything here is about one property of the table: it is a queue, not an
archive. Rows arrive from a collection, sit unread, and leave once an analysis
has consumed them — so every method is either "put", "what is waiting" or
"discard what has been dealt with".
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import JSON, and_, bindparam, case, func, or_, select
from sqlalchemy import text as sa_text

from app.core.config import settings
from app.utils.content_attachments import normalize_attachments

from .base_manager import BaseManager


def _bounded_content_hashes(values: Sequence[str] | None) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    return list(
        dict.fromkeys(value for value in values if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value))
    )[:2000]


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
            "content_type",
            "parent_external_id",
            "lane_id",
            "selection_status",
            "selection_reason",
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
            "ON CONFLICT (source_id, content_type, external_id) DO NOTHING"
        )
        stmt = stmt.bindparams(*(bindparam(column, type_=JSON(none_as_null=True)) for column in json_columns))
        # Same tenant stamping the ORM path does: refuses to write rather than
        # guessing a workspace for rows collected outside a tenant context.
        params = [
            await self._apply_tenant(
                session,
                {
                    **row,
                    "content_type": row.get("content_type") or "post",
                    "parent_external_id": row.get("parent_external_id"),
                    "lane_id": row.get("lane_id"),
                    "selection_status": row.get("selection_status") or "selected",
                    "selection_reason": row.get("selection_reason"),
                    "attachments": normalize_attachments(row.get("attachments")),
                },
            )
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
            sa_text(f"SELECT count(*) FROM {table} WHERE run_id = :run_id " f"AND ({' OR '.join(scope_clauses)})"),
            count_params,
        )
        return int(stored.scalar() or 0)

    async def confirm_stored_items(self, session: Any, rows: Sequence[dict[str, Any]]) -> None:
        """Confirm a strict staging receipt inside the caller's transaction.

        Unlike ``store_items``' compatibility count, this proves every identity
        is durable before a collection run claims it. Existing rows are only
        enriched with a missing reserved parent context; their payload and
        processing state remain untouched.
        """
        if not rows:
            return

        from app.core.tenant_context import current_tenant_id, is_bypass

        tenant_id = current_tenant_id()
        if is_bypass() or type(tenant_id) is not int or tenant_id <= 0:
            raise ValueError("content_tenant_invalid")
        expected: dict[tuple[str, str | None, str | None], dict[str, Any]] = {}
        source_ids = {row.get("source_id") for row in rows}
        if len(source_ids) != 1 or any(type(value) is not int or value <= 0 for value in source_ids):
            raise ValueError("content_source_invalid")
        for row in rows:
            if row.get("tenant_id", tenant_id) != tenant_id:
                raise ValueError("content_tenant_invalid")
            content_type = str(row.get("content_type") or "post")
            external_id = row.get("external_id")
            external_id = str(external_id) if external_id is not None else None
            digest = row.get("content_hash")
            if type(digest) is not str or len(digest) != 64:
                raise ValueError("content_identity_invalid")
            key = (content_type, external_id, None if external_id is not None else digest)
            if key in expected and expected[key].get("content_hash") != digest:
                raise ValueError("content_identity_conflict")
            expected[key] = row
        if not expected:
            return

        model = self.model
        source_id = next(iter(source_ids))
        identity_clauses = []
        for content_type, external_id, anonymous_hash in expected:
            clause = [model.content_type == content_type]
            if external_id is not None:
                clause.append(model.external_id == external_id)
            else:
                clause.extend((model.external_id.is_(None), model.content_hash == anonymous_hash))
            identity_clauses.append(and_(*clause))
        persisted = (
            (
                await session.execute(
                    select(model)
                    .where(
                        model.tenant_id == tenant_id,
                        model.source_id == source_id,
                        or_(*identity_clauses),
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        by_identity: dict[tuple[str, str | None, str | None], Any] = {}
        for stored in persisted:
            key = (
                stored.content_type or "post",
                stored.external_id,
                None if stored.external_id is not None else stored.content_hash,
            )
            by_identity.setdefault(key, stored)

        matched: list[tuple[Any, dict[str, Any]]] = []
        for key, row in expected.items():
            stored = by_identity.get(key)
            if stored is None:
                raise ValueError("content_receipt_missing")
            if stored.content_hash != row["content_hash"]:
                raise ValueError("content_identity_conflict")
            stored_status = stored.selection_status or "selected"
            incoming_status = row.get("selection_status") or "selected"
            if stored_status != incoming_status:
                raise ValueError("content_selection_conflict")
            matched.append((stored, row))

        for stored, row in matched:
            incoming_metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            parent_context = incoming_metrics.get("parent_context")
            if not isinstance(parent_context, dict):
                continue
            metrics = dict(stored.metrics) if isinstance(stored.metrics, dict) else {}
            if "parent_context" not in metrics:
                metrics["parent_context"] = dict(parent_context)
                stored.metrics = metrics
        await session.flush()

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

        rows = [
            {
                **row,
                "content_type": row.get("content_type") or "post",
                "parent_external_id": row.get("parent_external_id"),
                "lane_id": row.get("lane_id"),
                "selection_status": row.get("selection_status") or "selected",
                "selection_reason": row.get("selection_reason"),
            }
            for row in rows
        ]
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
        existing = (
            await session.execute(
                select(
                    model.external_id,
                    model.content_hash,
                    model.selection_status,
                )
                .where(
                    *scope,
                    model.external_id.in_(list(expected)),
                )
                .with_for_update()
            )
        ).all()
        present = {external_id: (digest, status) for external_id, digest, status in existing}
        missing = []
        for identity, row in expected.items():
            if identity in present:
                stored_hash, stored_status = present[identity]
                if stored_hash != row["content_hash"]:
                    raise ValueError("content_identity_conflict")
                # A locked row whose durable selection differs from this receipt
                # is a conflicting admission, not a duplicate to swallow.
                if (stored_status or "selected") != row["selection_status"]:
                    raise ValueError("content_selection_conflict")
            else:
                missing.append(row)
        if not missing:
            return 0
        # One scoped coverage lookup for the whole batch, not one round trip per
        # post. JSONB ?| is only a prefilter; malformed/non-array ledgers cannot
        # become receipt evidence when decoding the returned summaries below.
        summaries = (
            (
                await session.execute(
                    select(AIAnalytics.summary_data).where(
                        AIAnalytics.tenant_id == tenant_id,
                        AIAnalytics.source_id == source_id,
                        cast(AIAnalytics.summary_data, JSONB)["content_hashes"].has_any(
                            array(list(dict.fromkeys(row["content_hash"] for row in missing)))
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        covered = {
            digest
            for summary in summaries
            if type(summary) is dict and type(summary.get("content_hashes")) is list
            for digest in summary["content_hashes"]
            if type(digest) is str
        }
        pending = [
            await self._apply_tenant(
                session,
                {
                    **row,
                    "attachments": normalize_attachments(row.get("attachments")),
                },
            )
            for row in missing
            if row["content_hash"] not in covered
        ]
        if not pending:
            return 0
        inserted = (
            (
                await session.execute(
                    insert(model)
                    .values(pending)
                    .on_conflict_do_nothing(
                        index_elements=[model.source_id, model.content_type, model.external_id],
                    )
                    .returning(model.external_id)
                )
            )
            .scalars()
            .all()
        )
        # A concurrent non-admission writer must not turn a conflicting identity
        # into a successful receipt. Read every pending identity back exactly.
        stored = {
            external_id: (digest, status)
            for external_id, digest, status in (
                await session.execute(
                    select(
                        model.external_id,
                        model.content_hash,
                        model.selection_status,
                    )
                    .where(
                        *scope,
                        model.external_id.in_([row["external_id"] for row in pending]),
                    )
                    .with_for_update()
                )
            ).all()
        }
        for row in pending:
            record = stored.get(row["external_id"])
            if record is None or record[0] != row["content_hash"]:
                raise ValueError("content_receipt_missing")
            if (record[1] or "selected") != row["selection_status"]:
                raise ValueError("content_selection_conflict")
        return len(inserted)

    async def for_source(
        self,
        source_id: int,
        limit: int = 50,
        *,
        include_exhausted: bool = False,
        include_review: bool = False,
        start_date: Any = None,
        end_date: Any = None,
        content_hashes: Sequence[str] | None = None,
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
        from app.models.collection.collected_item import CollectedItem

        hashes = _bounded_content_hashes(content_hashes) if content_hashes is not None else None
        if hashes == []:
            return []
        query = self.filter(source_id=source_id)
        if hashes is not None:
            query = query.filter(CollectedItem.content_hash.in_(hashes))
        if not include_exhausted:
            query = query.filter(CollectedItem.analyze_attempts < CollectedItem.give_up_after_attempts)
        if not include_review:
            # Legacy rows (NULL) remain analysable; collection-time selected rows
            # pass; needs_review waits for an operator and rejected text is absent.
            query = query.filter(
                or_(CollectedItem.selection_status.is_(None), CollectedItem.selection_status == "selected")
            )
        if start_date is not None:
            query = query.filter(CollectedItem.published_at >= start_date)
        if end_date is not None:
            query = query.filter(CollectedItem.published_at <= end_date)
        return list(await query.order_by(CollectedItem.published_at.desc().nullslast()).limit(limit))

    async def exhausted_count(self, source_id: int, *, start_date: Any = None, end_date: Any = None) -> int:
        """How many staged rows for a source have burned their ceiling.

        `start_date`/`end_date` scope the count to a content window — the same
        bounds `count_for_source` applies, so a warning about stuck rows reads
        against the window a run would actually process, not the whole history.
        """
        from app.models.collection.collected_item import CollectedItem

        # Both columns are read because the ceiling is per row: one workspace may
        # have widened it while another kept the default.
        query = self.filter(source_id=source_id)
        if start_date is not None:
            query = query.filter(CollectedItem.published_at >= start_date)
        if end_date is not None:
            query = query.filter(CollectedItem.published_at <= end_date)
        rows = await query.values(CollectedItem.analyze_attempts, CollectedItem.give_up_after_attempts).rows()
        return sum(1 for r in rows if r and r[0] is not None and r[0] >= (r[1] or 0))

    async def count_for_source(
        self,
        source_id: int,
        *,
        include_review: bool = False,
        start_date: Any = None,
        end_date: Any = None,
        content_hashes: Sequence[str] | None = None,
    ) -> int:
        """Count the fixed staged window, including exhausted rows."""
        from app.models.collection.collected_item import CollectedItem

        hashes = _bounded_content_hashes(content_hashes) if content_hashes is not None else None
        if hashes == []:
            return 0
        query = self.filter(source_id=source_id)
        if hashes is not None:
            query = query.filter(CollectedItem.content_hash.in_(hashes))
        if not include_review:
            query = query.filter(
                or_(CollectedItem.selection_status.is_(None), CollectedItem.selection_status == "selected")
            )
        if start_date is not None:
            query = query.filter(CollectedItem.published_at >= start_date)
        if end_date is not None:
            query = query.filter(CollectedItem.published_at <= end_date)
        return int(await query.values(func.count(CollectedItem.id)).scalar(0) or 0)

    async def backlog_counts_for_sources(self, source_windows: Sequence[dict[str, Any]]) -> dict[str, int]:
        """Aggregate staged backlog for many sources and their individual windows.

        The result is a task-wide current-queue snapshot, not a run receipt. A
        single aggregate query avoids one count round-trip per source on the
        task progress poll. Callers must omit sources whose configured date
        window is malformed; silently removing a bad bound would widen scope.
        """
        from app.models.collection.collected_item import CollectedItem

        predicates = []
        for window in source_windows:
            source_id = window.get("source_id")
            if type(source_id) is not int or source_id <= 0:
                continue
            conditions = [CollectedItem.source_id == source_id]
            if window.get("start_date") is not None:
                conditions.append(CollectedItem.published_at >= window["start_date"])
            if window.get("end_date") is not None:
                conditions.append(CollectedItem.published_at <= window["end_date"])
            predicates.append(and_(*conditions))
        if not predicates:
            return {"pending": 0, "exhausted": 0, "eligible": 0}

        exhausted = CollectedItem.analyze_attempts >= CollectedItem.give_up_after_attempts
        rows = await (
            self.filter(or_(*predicates))
            .values(
                func.count(CollectedItem.id),
                func.coalesce(func.sum(case((exhausted, 1), else_=0)), 0),
                func.coalesce(func.sum(case((~exhausted, 1), else_=0)), 0),
            )
            .rows()
        )
        if not rows:
            return {"pending": 0, "exhausted": 0, "eligible": 0}
        pending, exhausted_count, eligible = rows[0]
        return {
            "pending": int(pending or 0),
            "exhausted": int(exhausted_count or 0),
            "eligible": int(eligible or 0),
        }

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
        from app.models.collection.collected_item import CollectedItem

        return list(
            await self.filter(run_id=run_id).order_by(CollectedItem.published_at.desc().nullslast()).limit(limit)
        )

    async def hashes_for_source(self, source_id: int) -> set[str]:
        """Content hashes currently staged for a source (an unread ledger)."""
        from app.models.collection.collected_item import CollectedItem

        # The column object, not its name: a bare string is taken as a text()
        # expression, which this query rejects outright. `rows()` is the
        # documented read path for a `values()` query.
        # One column, read positionally: `rows()` yields Row objects, which
        # answer to indexing but not to `.get`.
        rows = await self.filter(source_id=source_id).values(CollectedItem.content_hash).rows()
        return {r[0] for r in rows if r and r[0]}

    async def delete_hashes(
        self,
        session: Any,
        source_id: int,
        hashes: Sequence[str],
        *,
        tenant_id: int | None = None,
        analysis_fingerprint: str | None = None,
    ) -> int:
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
        from app.models import Source

        source_tenant_id = (
            await session.execute(select(Source.tenant_id).where(Source.id == source_id))
        ).scalar_one_or_none()
        if type(source_tenant_id) is not int or source_tenant_id <= 0:
            raise ValueError("content_source_invalid")
        if tenant_id is None:
            tenant_id = source_tenant_id
        if type(tenant_id) is not int or tenant_id != source_tenant_id:
            raise ValueError("content_tenant_invalid")
        scoped_tenant = tenant_params.get("tenant_id")
        if scoped_tenant is not None and scoped_tenant != source_tenant_id:
            from app.core.tenant_context import TenantContextError

            raise TenantContextError("Staged mutation source differs from the active tenant")
        if analysis_fingerprint:
            protected = await self._pending_other_context_hashes(
                session, source_id, tenant_id, wanted, analysis_fingerprint
            )
            wanted = [value for value in wanted if value not in protected]
            if not wanted:
                return 0
        result = await session.execute(
            sa_text(
                f"DELETE FROM {settings.DB_SCHEMA}.collected_items "
                "WHERE tenant_id = :tenant_id AND source_id = :source_id "
                "AND content_hash = ANY(:hashes)" + tenant_clause
            ),
            {"tenant_id": tenant_id, "source_id": source_id, "hashes": list(wanted), **tenant_params},
        )
        return int(result.rowcount or 0)

    async def delete_unprocessed_for_source(self, session: Any, source_id: int, tenant_id: int) -> int:
        """Delete only raw staged rows belonging to one source and tenant.

        This is an explicit operator cleanup, not analysis retirement: analytics
        and analysis receipts are intentionally outside this table operation.
        The caller owns the transaction and must reject active source jobs.
        """
        if type(source_id) is not int or source_id <= 0 or type(tenant_id) is not int or tenant_id <= 0:
            raise ValueError("content_scope_invalid")

        tenant_clause, tenant_params = _staged_mutation_scope()
        if tenant_params.get("tenant_id", tenant_id) != tenant_id:
            from app.core.tenant_context import TenantContextError

            raise TenantContextError("Staged mutation tenant differs from the active tenant")

        from app.models import Source

        source_tenant_id = (
            await session.execute(select(Source.tenant_id).where(Source.id == source_id).with_for_update())
        ).scalar_one_or_none()
        if source_tenant_id != tenant_id:
            raise ValueError("content_source_scope_invalid")

        result = await session.execute(
            sa_text(
                f"DELETE FROM {settings.DB_SCHEMA}.collected_items "
                "WHERE tenant_id = :tenant_id AND source_id = :source_id" + tenant_clause
            ),
            {"tenant_id": tenant_id, "source_id": source_id, **tenant_params},
        )
        return int(result.rowcount or 0)

    async def _pending_other_context_hashes(
        self, session: Any, source_id: int, tenant_id: int, hashes: Sequence[str], current_fingerprint: str
    ) -> set[str]:
        """Keep staged rows needed by another active or queued context in its date window."""
        from app.models import AgentScenario, CollectedItem, Job, Source
        from app.models.managers.agent_task_manager import AgentTaskManager
        from app.services.ai.analysis_context import analysis_fingerprint
        from app.services.ai.scenario_schema import compile_scenario_output
        from app.utils.date_parsing import universal_date_parser

        rows = (
            await session.execute(
                select(CollectedItem.content_hash, CollectedItem.published_at).where(
                    CollectedItem.tenant_id == tenant_id,
                    CollectedItem.source_id == source_id,
                    CollectedItem.content_hash.in_(list(hashes)),
                )
            )
        ).all()
        dates = {digest: (published.date() if published is not None else None) for digest, published in rows}
        contexts: list[tuple[Any, dict[str, Any], dict[str, Any]]] = []
        task_manager = AgentTaskManager()
        scheduled = await task_manager.filter(
            tenant_id=tenant_id, is_active=True, job_type__in=["analyze", "collect"]
        ).prefetch_related("sources", "agent_scenario")
        jobs = await Job.objects.filter(
            tenant_id=tenant_id, status__in=["pending", "running"], job_type__in=["analyze", "collect"]
        ).limit(200)
        queued_task_ids = {job.agent_task_id for job in jobs if job.agent_task_id}
        queued_payloads = {job.agent_task_id: job.payload for job in jobs if job.agent_task_id}
        queued = (
            await task_manager.filter(
                tenant_id=tenant_id, id__in=list(queued_task_ids), job_type__in=["analyze", "collect"]
            ).prefetch_related("sources", "agent_scenario")
            if queued_task_ids
            else []
        )
        known_ids = set()
        for task in scheduled:
            if task.id in known_ids:
                continue
            known_ids.add(task.id)
            contexts.append((task, task.payload if isinstance(task.payload, dict) else {}, {}))
        for task in queued:
            job_payload = queued_payloads.get(task.id)
            contexts.append(
                (
                    task,
                    task.payload if isinstance(task.payload, dict) else {},
                    job_payload if isinstance(job_payload, dict) else {},
                )
            )
        for job in jobs:
            if job.agent_task_id is not None or job.job_type != "analyze":
                continue
            job_payload = job.payload if isinstance(job.payload, dict) else {}
            source_ids = job_payload.get("source_ids")
            if isinstance(source_ids, list) and source_ids and source_id not in source_ids:
                continue
            contexts.append((None, {}, job_payload))

        default_scenario = await AgentScenario.objects.get_default_scenario(tenant_id=tenant_id)
        source = await Source.objects.filter(id=source_id, tenant_id=tenant_id).first()
        pending: set[str] = set()
        context_hashes: dict[str, set[str]] = {}
        for task, task_payload, job_payload in contexts:
            if task is not None and task.job_type == "collect" and not task_payload.get("analyze_inline"):
                continue
            if task is not None and task.sources and all(item.id != source_id for item in task.sources):
                continue
            if task is None:
                scenario = None
                if job_payload.get("scenario_id"):
                    scenario = await AgentScenario.objects.filter(
                        id=job_payload["scenario_id"], tenant_id=tenant_id
                    ).first()
                if scenario is None:
                    scenario = await AgentTaskManager().get_source_scenario(source_id)
                task_payload = {}
            else:
                scenario = task.agent_scenario
            if scenario is None and task_payload.get("scenario_id"):
                scenario = await AgentScenario.objects.filter(
                    id=task_payload["scenario_id"], tenant_id=tenant_id
                ).first()
            if scenario is None or not scenario.is_active:
                scenario = default_scenario
            if scenario is None or not scenario.is_active:
                # A broken active context is still a possible future consumer.
                pending.update(hashes)
                continue
            try:
                output_contract = compile_scenario_output(scenario)
                fingerprint = analysis_fingerprint(
                    scenario,
                    trigger_config=task.trigger_config if task is not None else None,
                    task_payload=task_payload,
                    output_schema=output_contract.schema,
                )
            except Exception:
                pending.update(hashes)
                continue
            if fingerprint == current_fingerprint:
                continue
            effective_dates = job_payload.get("cli_dates") or task_payload.get("cli_dates")
            if not effective_dates and source is not None and isinstance(source.params, dict):
                effective_dates = source.params.get("cli_dates")
            start = end = None
            if isinstance(effective_dates, dict):
                start_value = universal_date_parser(effective_dates.get("start_date"))
                end_value = universal_date_parser(effective_dates.get("end_date"))
                start = start_value.date() if start_value else None
                end = end_value.date() if end_value else None
            eligible = {
                digest
                for digest in hashes
                if dates.get(digest) is None
                or ((start is None or dates[digest] >= start) and (end is None or dates[digest] <= end))
            }
            if eligible:
                context_hashes.setdefault(fingerprint, set()).update(eligible)
        context_hashes.pop(current_fingerprint, None)
        if context_hashes:
            from app.models import AnalyzedContentReceipt

            fingerprints = list(context_hashes)
            all_hashes = set().union(*(context_hashes[value] for value in fingerprints))
            completed = await AnalyzedContentReceipt.objects.covered_by_contexts(
                source_id, fingerprints, list(all_hashes), tenant_id=tenant_id
            )
            for fingerprint in fingerprints:
                pending.update(context_hashes[fingerprint] - completed.get(fingerprint, set()))
        return pending

    @staticmethod
    async def delete_run(session: Any, run_id: int) -> int:
        """Discard rows first staged by one job run (operator cancellation choice).

        Only rows still waiting for analysis exist here: analysed rows are already
        retired. Rows staged earlier by another run keep that run's id (ON CONFLICT
        DO NOTHING) and are untouched. The caller owns the transaction.
        """
        if type(run_id) is not int or run_id <= 0:
            raise ValueError("A persisted run id is required")
        tenant_clause, tenant_params = _staged_mutation_scope()
        result = await session.execute(
            sa_text(f"DELETE FROM {settings.DB_SCHEMA}.collected_items WHERE run_id = :run_id" + tenant_clause),
            {"run_id": run_id, **tenant_params},
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
            sa_text(f"DELETE FROM {settings.DB_SCHEMA}.collected_items " "WHERE created_at < :cutoff" + tenant_clause),
            params,
        )
        return int(result.rowcount or 0)

    async def reset_attempts(
        self, session: Any, source_id: int, *, start_date: Any = None, end_date: Any = None
    ) -> int:
        """Reset attempts for staged rows of a source, optionally within a window.

        Used when an operator triggers a manual retry (e.g. after fixing the LLM
        model or raising `give_up_after_attempts`). Returns the number of rows
        affected.
        """
        tenant_clause, tenant_params = _staged_mutation_scope()
        params = {"source_id": source_id, **tenant_params}
        window_clause = ""
        if start_date is not None:
            window_clause += " AND published_at >= :start_date"
            params["start_date"] = start_date
        if end_date is not None:
            window_clause += " AND published_at <= :end_date"
            params["end_date"] = end_date
        result = await session.execute(
            sa_text(
                f"UPDATE {settings.DB_SCHEMA}.collected_items "
                "SET analyze_attempts = 0 "
                "WHERE source_id = :source_id AND analyze_attempts > 0" + tenant_clause + window_clause
            ),
            params,
        )
        return int(result.rowcount or 0)
