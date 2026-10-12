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
    "prompt_tokens",
    "completion_tokens",
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
        from ..analysis.llm_attempt import LLMAttempt

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
