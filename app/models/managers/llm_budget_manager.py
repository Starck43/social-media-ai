"""Atomic, owned-commit receipt plus reservation; no HTTP or settlement."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from app.core.tenant_context import TenantContextError, current_tenant_id

from .base_manager import BaseManager
from .llm_dispatch_receipt_manager import LLMDispatchReceiptManager

GUARDS = {
    ("llm_budget_reservations_immutable", 27, "reject_llm_budget_reservation_mutation"),
    ("llm_budget_reservations_no_truncate", 34, "reject_llm_budget_reservation_mutation"),
    ("llm_budget_reservations_receipt_binding", 7, "check_llm_budget_reservation_receipt"),
}


class LLMBudgetManager(BaseManager):
    def __init__(self):
        from ..analysis.llm_budget import LLMBudgetReservation

        super().__init__(LLMBudgetReservation)

    async def reserve_receipt(
        self, record: Mapping[str, Any], *, ceiling_usd: Decimal | None, bound_version: str | None = None
    ) -> bool:
        """True only after a fresh receipt/reservation/balance commit is acknowledged.

        False is exact historical replay, NEVER permission to resend. Exceptions,
        including ambiguous commit acknowledgement, cannot authorize dispatch.
        Missing/frozen baseline fails closed. No external session is accepted.
        """
        from app.services.ai.budget_admission import (
            PLAN_FIELDS, BudgetAdmissionConflict, BudgetAdmissionDenied, BudgetAdmissionUnavailable,
            exact_usd, require_capacity, reservation_plan,
        )
        from app.services.ai.dispatch_receipts import validate_receipt
        from app.services.tenancy.resolver import resolved_daily_cost_limit

        from ..analysis.llm_budget import LLMBudgetDay
        from ..tenancy.tenant import Tenant

        payer = current_tenant_id()
        if type(payer) is not int or not 0 < payer <= 2147483647:
            raise TenantContextError("Budget admission requires an explicit payer scope")
        value = validate_receipt(record)
        if value["tenant_id"] != payer:
            raise TenantContextError("Reservation payer differs from trusted scope")
        plan = reservation_plan(value, ceiling_usd, bound_version)
        created = False
        async with self._get_owned_session() as session:
            guards = (await session.execute(text(
                "SELECT t.tgname, t.tgtype, p.proname FROM pg_catalog.pg_trigger t "
                "JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid "
                "JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace "
                "WHERE n.nspname=:schema AND pn.nspname=:schema AND c.relname='llm_budget_reservations' "
                "AND NOT t.tgisinternal AND t.tgenabled IN ('O', 'A') "
                "AND t.tgqual IS NULL AND t.tgnargs=0 AND t.tgattr::text='' "
                "AND current_setting('session_replication_role') IN ('origin', 'local')"
            ), {"schema": self.model.__table__.schema})).all()
            if not GUARDS.issubset(set(guards)):
                raise BudgetAdmissionUnavailable("Reservation guards are not installed")
            # Consistent payer-then-day lock order also fences concurrent plan/cap
            # updates and older-day admissions. No lock survives this method.
            tenant = (
                await session.execute(select(Tenant).where(Tenant.id == payer).with_for_update())
            ).scalar_one_or_none()
            if tenant is None or not tenant.is_active:
                raise TenantContextError("Admission payer is absent or inactive")
            raw_cap = resolved_daily_cost_limit(tenant)
            cap = None if raw_cap == 0 else exact_usd(Decimal(str(raw_cap)))
            budget = (await session.execute(select(LLMBudgetDay).where(
                LLMBudgetDay.tenant_id == payer, LLMBudgetDay.utc_day == value["utc_day"]
            ).with_for_update())).scalar_one_or_none()
            existing = (await session.execute(select(self.model).where(
                self.model.dispatch_id == value["dispatch_id"], self.model.tenant_id == payer
            ))).scalar_one_or_none()
            receipts = LLMDispatchReceiptManager()
            if existing is not None:
                if existing.utc_day != value["utc_day"] or any(
                    getattr(existing, field) != plan[field] for field in PLAN_FIELDS
                ):
                    raise BudgetAdmissionConflict("Reservation identity conflicts with stored evidence")
                if await receipts._record_in_session(value, session):
                    raise BudgetAdmissionConflict("Reservation receipt was not already durable")
                # Replay is comparison only: preserve original cap and counters,
                # even if the current cap has changed or a baseline is frozen.
            else:
                if budget is None or budget.frozen or not budget.baseline_version.strip():
                    raise BudgetAdmissionUnavailable("Reviewed payer/day baseline is unavailable or frozen")
                if value["utc_day"] != datetime.now(timezone.utc).date():
                    raise BudgetAdmissionDenied("Fresh admission requires the current UTC day")
                if cap is not None and plan["ceiling_state"] != "undefined":
                    older = (await session.execute(select(self.model.id).where(
                        self.model.tenant_id == payer, self.model.utc_day != value["utc_day"],
                        self.model.ceiling_state != "undefined"
                    ).limit(1))).scalar_one_or_none()
                    if older is not None:
                        # This slice has no settlement/reconciler. Never assume
                        # TTL/day rollover releases an earlier liability.
                        raise BudgetAdmissionDenied("Earlier-day liability requires reconciliation")
                require_capacity(cap, budget.settled_usd, budget.reserved_usd, budget.unbounded_count, plan)
                if not await receipts._record_in_session(value, session):
                    raise BudgetAdmissionConflict("Standalone receipt cannot be upgraded to fresh admission")
                reservation = dict(
                    dispatch_id=value["dispatch_id"], tenant_id=payer, utc_day=value["utc_day"], cap_usd=cap, **plan
                )
                row_id = (await session.execute(insert(self.model.__table__).values(**reservation)
                    .on_conflict_do_nothing(constraint="uq_llm_budget_reservations_identity")
                    .returning(self.model.id))).scalar_one_or_none()
                if row_id is None:
                    raise BudgetAdmissionConflict("Reservation identity conflicts with stored evidence")
                budget.reserved_usd += plan["ceiling_usd"] or Decimal(0)
                budget.undefined_count += int(plan["ceiling_state"] == "undefined")
                budget.unbounded_count += int(plan["ceiling_state"] == "unbounded")
                created = True
        return created
