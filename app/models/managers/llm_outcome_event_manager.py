"""Owned-commit append/replay for payer-bound outcome evidence."""

from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from app.core.tenant_context import TenantContextError, current_tenant_id

from .base_manager import BaseManager


class OutcomeEvidenceConflict(RuntimeError):
    """An event identity refers to different or inaccessible evidence."""


class OutcomeStorageUnavailable(RuntimeError):
    """Required receipt/event guards are unavailable."""


GUARDS = {
    ("llm_dispatch_receipts", "llm_dispatch_receipts_immutable", 27, "reject_llm_receipt_mutation"),
    ("llm_dispatch_receipts", "llm_dispatch_receipts_no_truncate", 34, "reject_llm_receipt_mutation"),
    ("llm_outcome_events", "llm_outcome_events_immutable", 27, "reject_llm_outcome_mutation"),
    ("llm_outcome_events", "llm_outcome_events_no_truncate", 34, "reject_llm_outcome_mutation"),
    ("llm_outcome_events", "llm_outcome_events_receipt_binding", 7, "check_llm_outcome_receipt"),
}


class LLMOutcomeEventManager(BaseManager):
    def __init__(self):
        from ..llm.llm_outcome_event import LLMOutcomeEvent

        super().__init__(LLMOutcomeEvent)

    async def record_event(self, record: Mapping[str, Any]) -> bool:
        """True after owned insert commit; False after exact scoped replay.

        This is standalone evidence storage, not settlement. Future budget
        settlement must compose event insertion and balance changes atomically;
        this method cannot authorize HTTP, release liability or a reservation.
        """
        tenant_id = current_tenant_id()
        if type(tenant_id) is not int or not 0 < tenant_id <= 2147483647:
            raise TenantContextError("Outcome evidence requires explicit payer scope")
        if type(record.get("tenant_id")) is not int or record["tenant_id"] != tenant_id:
            raise TenantContextError("Outcome payer differs from trusted scope")
        # Avoid registration-time cycles through the eager AI package exports.
        from app.services.ai.dispatch_receipts import RECEIPT_FIELDS
        from app.services.ai.outcome_events import EVENT_FIELDS, validate_outcome_event

        from ..llm.llm_dispatch_receipt import LLMDispatchReceipt

        async with self._get_owned_session() as session:
            guards = (
                await session.execute(
                    text(
                        "SELECT c.relname, t.tgname, t.tgtype, p.proname FROM pg_catalog.pg_trigger t "
                        "JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
                        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                        "JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid "
                        "JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace "
                        "WHERE n.nspname=:schema AND pn.nspname=:schema "
                        "AND c.relname IN ('llm_dispatch_receipts', 'llm_outcome_events') "
                        "AND NOT t.tgisinternal AND t.tgenabled IN ('O', 'A') "
                        "AND t.tgqual IS NULL AND t.tgnargs=0 AND t.tgattr::text='' "
                        "AND current_setting('session_replication_role') IN ('origin', 'local')"
                    ),
                    {"schema": self.model.__table__.schema},
                )
            ).all()
            if not GUARDS.issubset(set(guards)):
                raise OutcomeStorageUnavailable("Immutable outcome/receipt guards are not installed")
            receipt = (
                (
                    await session.execute(
                        select(*(getattr(LLMDispatchReceipt, field) for field in RECEIPT_FIELDS)).where(
                            LLMDispatchReceipt.dispatch_id == record.get("dispatch_id"),
                            LLMDispatchReceipt.tenant_id == tenant_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if receipt is None:
                raise TenantContextError("No receipt in the trusted payer scope")
            value = validate_outcome_event(record, receipt)
            statement = (
                insert(self.model.__table__)
                .values(**value)
                .on_conflict_do_nothing(constraint="uq_llm_outcome_events_identity")
                .returning(self.model.id)
            )
            created = (await session.execute(statement)).scalar_one_or_none() is not None
            if not created:
                existing = (
                    (
                        await session.execute(
                            select(*(getattr(self.model, field) for field in EVENT_FIELDS)).where(
                                self.model.event_id == value["event_id"],
                                self.model.tenant_id == tenant_id,
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing is None or any(existing[field] != value[field] for field in EVENT_FIELDS):
                    raise OutcomeEvidenceConflict("Event identity conflicts with stored evidence")
        return created

    async def unpriced_models(self, tenant_id: int, *, since, limit: int = 10) -> list[dict[str, Any]]:
        """Provider/model pairs whose dispatched attempts have no known price.

        Reads immutable outcome evidence for one tenant only: attempts whose
        `cost_certainty` is `undefined` (and that actually reached dispatch).
        Counts distinct dispatches, not observations, so a reconciliation does
        not inflate exposure. No prompts, payers or provider payloads leave
        this projection.
        """
        from sqlalchemy import func

        from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass

        from ..llm.llm_dispatch_receipt import LLMDispatchReceipt

        if type(tenant_id) is not int or tenant_id <= 0:
            raise ValueError("A workspace ID is required")
        if not is_bypass() and current_tenant_id() != tenant_id:
            raise TenantContextError("Unpriced model evidence requires the active tenant scope")
        if type(limit) is not int or not 0 < limit <= 50:
            raise ValueError("Unpriced model limit must be between 1 and 50")
        if since is None or getattr(since, "tzinfo", None) is None:
            raise ValueError("Unpriced model evidence requires an aware lower bound")

        async with self._get_owned_session() as session:
            attempts = func.count(func.distinct(self.model.dispatch_id))
            rows = (
                await session.execute(
                    select(
                        LLMDispatchReceipt.provider_name,
                        LLMDispatchReceipt.model_id,
                        attempts,
                        func.max(self.model.observed_at),
                    )
                    .select_from(self.model)
                    .join(
                        LLMDispatchReceipt,
                        LLMDispatchReceipt.dispatch_id == self.model.dispatch_id,
                    )
                    .where(
                        self.model.tenant_id == tenant_id,
                        LLMDispatchReceipt.tenant_id == tenant_id,
                        self.model.cost_certainty == "undefined",
                        self.model.dispatch_state != "not_dispatched",
                        self.model.observed_at >= since,
                    )
                    .group_by(LLMDispatchReceipt.provider_name, LLMDispatchReceipt.model_id)
                    .order_by(attempts.desc(), LLMDispatchReceipt.provider_name, LLMDispatchReceipt.model_id)
                    .limit(limit)
                )
            ).all()
        return [
            {
                "provider": str(row[0]),
                "model_id": str(row[1]),
                "attempts": int(row[2] or 0),
                "last_observed_at": row[3],
            }
            for row in rows
        ]
