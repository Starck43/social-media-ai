"""Insert-only manager for the LLM attempt journal."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from .base_manager import BaseManager


class AttemptEvidenceConflict(RuntimeError):
    """An identity already exists with different or inaccessible evidence."""


_RECORD_FIELDS = (
    "attempt_key",
    "purpose",
    "stage",
    "source_id",
    "analysis_date",
    "job_id",
    "agent_task_id",
    "llm_model_row_id",
    "model_id",
    "provider_name",
    "input_cost_per_1k",
    "output_cost_per_1k",
    "cache_read_cost_per_1m",
    "cache_write_5m_cost_per_1m",
    "cache_write_1h_cost_per_1m",
    "pricing_token_unit",
    "prompt_tokens",
    "completion_tokens",
    "cache_usage",
    "pricing_policy_version",
    "cost_usd",
    "cost_certainty",
    "outcome",
)


class LLMAttemptManager(BaseManager):
    """Journal access: duplicate-safe, create-only.

    The journal is evidence, not state, so there is deliberately no update or
    delete flow. `record_attempt` is idempotent through the unique
    `attempt_key`: a retried persistence (worker retry, repeated save, late
    notification) cannot duplicate or silently rewrite a paid attempt.
    """

    def __init__(self):
        from ..llm.llm_attempt import LLMAttempt

        super().__init__(LLMAttempt)

    async def record_attempt(self, record: dict[str, Any]) -> bool:
        """Acknowledge a committed insert or an exact replay, never another error.

        Only the attempt-key constraint suppresses an insert. A collision must
        match the original payer and every evidence field; a changed tariff,
        usage or outcome is not an idempotent replay. Other constraint failures
        and lost commit acknowledgements propagate to the caller.
        """
        if set(record) - set(_RECORD_FIELDS) - {"tenant_id"}:
            raise ValueError("Unsupported attempt evidence fields")
        values = {field: record.get(field) for field in _RECORD_FIELDS}
        from app.services.ai.cache_pricing import validate_cache_usage

        policy = values["pricing_policy_version"]
        if policy in (None, "provider-price-v1"):
            if (
                values["pricing_token_unit"] not in (None, 1000)
                or values["cache_usage"] is not None
                or any(
                    values[field] is not None
                    for field in ("cache_read_cost_per_1m", "cache_write_5m_cost_per_1m", "cache_write_1h_cost_per_1m")
                )
            ):
                raise ValueError("Legacy attempts cannot contain cache tariff evidence")
        elif policy == "cache-tariff-v2":
            if values["pricing_token_unit"] is None:
                if any(
                    values[field] is not None
                    for field in (
                        "input_cost_per_1k",
                        "output_cost_per_1k",
                        "cache_read_cost_per_1m",
                        "cache_write_5m_cost_per_1m",
                        "cache_write_1h_cost_per_1m",
                    )
                ):
                    raise ValueError("A v2 tariff with configured rates requires an explicit unit")
            elif type(values["pricing_token_unit"]) is not int or values["pricing_token_unit"] not in (1000, 1_000_000):
                raise ValueError("Attempt pricing token unit is invalid")
            if (
                values["cache_usage"] is not None
                and validate_cache_usage(values["cache_usage"]) != values["cache_usage"]
            ):
                raise ValueError("Attempt cache usage does not match the normalized evidence contract")
        else:
            raise ValueError("Unsupported attempt pricing policy")
        if "tenant_id" in record:
            values["tenant_id"] = record["tenant_id"]
        async with self._get_owned_session() as session:
            values = await self._apply_tenant(session, values)
            statement = (
                insert(self.model.__table__)
                .values(**values)
                .on_conflict_do_nothing(constraint="uq_llm_attempts_key")
                .returning(self.model.id)
            )
            inserted_id = (await session.execute(statement)).scalar_one_or_none()
            created = inserted_id is not None
            if not created:
                fields = ("tenant_id",) + _RECORD_FIELDS
                existing = (
                    (
                        await session.execute(
                            select(*(getattr(self.model, field) for field in fields)).where(
                                self.model.attempt_key == values["attempt_key"],
                                self.model.tenant_id == values["tenant_id"],
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing is None or any(existing[field] != values[field] for field in fields):
                    # Do not include private record values or another payer's
                    # identity in an exception/log.
                    raise AttemptEvidenceConflict("Attempt identity conflicts with stored evidence")
        # The owned-session context commits before this acknowledgement.
        return created

    async def run_summary(self, source_id: int, *, since: Any, until: Any = None) -> dict[str, Any]:
        """Bounded read-only attempt envelope for one run window (display only).

        Reads the insert-only journal the same way handlers already trust it
        (per-source ``text_analysis`` rows created in ``[since, until]``), so a
        run panel can name the model that actually served the run and count
        survived vs failed calls. Counters come straight from aggregates; the
        newest non-success outcome doubles as the bounded error code — the same
        allowlisted discipline as ``_llm_failure_code`` in jobs handlers. A
        lookup failure must never fail the caller (diagnostic is a hint), and
        nothing here ever writes.
        """
        scope = [
            self.model.source_id == source_id,
            self.model.stage == "text_analysis",
            self.model.created_at >= since,
        ]
        if until is not None:
            scope.append(self.model.created_at <= until)
        return await self._run_summary(scope)

    async def task_run_summary(self, task_id: int, job_id: int) -> dict[str, Any]:
        """Summarize calls explicitly attributed to this task and Job in the current tenant."""
        if any(type(value) is not int or value <= 0 for value in (task_id, job_id)):
            raise ValueError("Task and Job identifiers must be positive integers")
        return await self._run_summary(
            [
                self.model.agent_task_id == task_id,
                self.model.job_id == job_id,
            ]
        )

    async def _run_summary(self, scope: list[Any]) -> dict[str, Any]:
        """Project bounded journal counters and the last model without exposing response text."""
        limits = {"total": 10**9, "succeeded": 10**9}
        async with self._get_owned_session() as session:
            counters = (
                await session.execute(
                    select(
                        func.count().label("total"),
                        func.count().filter(self.model.outcome == "succeeded").label("succeeded"),
                    ).where(*scope, *self._tenant_criterion())
                )
            ).one()
            newest_result = await session.execute(
                select(self.model.model_id, self.model.provider_name, self.model.outcome)
                .where(*scope, *self._tenant_criterion())
                .order_by(self.model.id.desc())
                .limit(1)
            )
            newest = newest_result.first()
        total = int(counters.total or 0)
        succeeded = int(counters.succeeded or 0)
        outcome = getattr(newest, "outcome", None) if newest is not None else None
        return {
            "total": total if 0 <= total <= limits["total"] else None,
            "succeeded": succeeded if 0 <= succeeded <= limits["succeeded"] else None,
            "failed": max(0, total - succeeded) if total >= 0 and succeeded >= 0 else None,
            "model": (str(newest.model_id)[:200] if newest is not None and newest.model_id else None),
            "provider": (str(newest.provider_name)[:200] if newest is not None and newest.provider_name else None),
            # The newest attempt decides the displayed code only when it itself
            # failed: a successful tail run must not show a stale failure code.
            "error_code": (
                outcome
                if isinstance(outcome, str) and outcome and outcome != "succeeded" and outcome.isidentifier()
                else None
            ),
        }

    async def accounting_totals(self, utc_day: date | None = None) -> dict[str, Any]:
        """Tenant-scoped captured-journal statistics, never a complete spend cap.

        Undefined and legacy unknown rows stay visible alongside known USD,
        including when the known subtotal is zero. Date filtering uses the
        existing journal creation time, not a certified provider dispatch time.
        """
        statement = select(
            self.model.cost_certainty,
            func.count().label("attempt_count"),
            func.sum(self.model.cost_usd).label("known_cost_usd"),
            func.count(self.model.cost_usd).label("non_null_cost_count"),
        ).where(*self._tenant_criterion())
        if utc_day is not None:
            first = datetime.combine(utc_day, time.min, tzinfo=timezone.utc)
            statement = statement.where(
                self.model.created_at >= first,
                self.model.created_at < first + timedelta(days=1),
            )
        statement = statement.group_by(self.model.cost_certainty)
        async with self._get_owned_session() as session:
            rows = (await session.execute(statement)).all()
        totals = {
            "scope": "captured_journal_only",
            "attempt_count": 0,
            "priced_count": 0,
            "free_count": 0,
            "undefined_count": 0,
            "not_dispatched_count": 0,
            "known_cost_usd": Decimal(0),
        }
        for certainty, count, cost, non_null in rows:
            totals["attempt_count"] += count
            if certainty in ("priced", "free"):
                amount = Decimal(str(cost)) if cost is not None else Decimal(0)
                if not amount.is_finite() or amount < 0 or (certainty == "free" and amount != 0):
                    totals["undefined_count"] += count
                    continue
                totals[certainty + "_count"] += non_null
                totals["undefined_count"] += count - non_null
                totals["known_cost_usd"] += amount
            elif certainty == "not_dispatched":
                totals["not_dispatched_count"] += count
            else:
                # Includes historical unknown_tariff/unknown_usage, no backfill.
                totals["undefined_count"] += count
        totals["all_costs_known"] = totals["undefined_count"] == 0
        return totals
