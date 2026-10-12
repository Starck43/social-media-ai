"""Insert-only manager for the LLM attempt journal."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .base_manager import BaseManager


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
        """Record one attempt; False when the ledger already holds it."""
        existing = await self.get(attempt_key=record["attempt_key"])
        if existing is not None:
            return False
        try:
            await self.create(**record)
        except IntegrityError:
            # A concurrent writer stored the same attempt first; the ledger is
            # already correct, so the duplicate is not a failure.
            return False
        return True

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
