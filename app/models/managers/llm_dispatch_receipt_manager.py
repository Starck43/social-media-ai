"""Commit-acknowledged, explicit-payer storage for preparation receipts."""

from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from app.core.tenant_context import TenantContextError, current_tenant_id

from .base_manager import BaseManager


class ReceiptEvidenceConflict(RuntimeError):
    """A receipt identity conflicts with stored or inaccessible evidence."""


class ReceiptStorageUnavailable(RuntimeError):
    """Schema immutability guards are missing or disabled."""


class LLMDispatchReceiptManager(BaseManager):
    def __init__(self):
        from ..analysis.llm_dispatch_receipt import LLMDispatchReceipt

        super().__init__(LLMDispatchReceipt)

    async def record_receipt(self, record: Mapping[str, Any]) -> bool:
        """Store or compare a receipt; return only after owned commit.

        No externally supplied session and no implicit bootstrap payer.
        Budget admission must later join receipt insertion to its own atomic
        reservation transaction; this storage API cannot authorize HTTP.
        """
        async with self._get_owned_session() as session:
            created = await self._record_in_session(record, session)
        return created

    async def _record_in_session(self, record: Mapping[str, Any], session: Any) -> bool:
        """Internal composition only; caller owns commit, never grants dispatch."""
        tenant_id = current_tenant_id()
        if type(tenant_id) is not int or not 0 < tenant_id <= 2147483647:
            raise TenantContextError("Dispatch receipt requires an explicit payer scope")
        # The AI package eagerly imports its analyzer. Defer this dependency
        # until root model registration/binding is complete.
        from app.services.ai.dispatch_receipts import RECEIPT_FIELDS, validate_receipt

        value = validate_receipt(record)
        if value["tenant_id"] != tenant_id:
            raise TenantContextError("Dispatch receipt payer differs from the trusted scope")
        guard_result = await session.execute(
            text(
                "SELECT t.tgname, t.tgtype FROM pg_catalog.pg_trigger t "
                "JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_catalog.pg_proc p ON p.oid = t.tgfoid "
                "JOIN pg_catalog.pg_namespace pn ON pn.oid = p.pronamespace "
                "WHERE n.nspname = :schema AND c.relname = :table "
                "AND pn.nspname = :schema AND p.proname = 'reject_llm_receipt_mutation' "
                "AND NOT t.tgisinternal AND t.tgenabled IN ('O', 'A') "
                "AND current_setting('session_replication_role') IN ('origin', 'local')"
            ),
            {"schema": self.model.__table__.schema, "table": self.model.__tablename__},
        )
        guards = guard_result.all()
        if not {
            ("llm_dispatch_receipts_immutable", 27),
            ("llm_dispatch_receipts_no_truncate", 34),
        }.issubset(set(guards)):
            # pg_trigger bits: ROW=1, BEFORE=2, DELETE=8, UPDATE=16,
            # TRUNCATE=32. Metadata-only bootstrap must not masquerade as
            # the reviewed schema or silently accept mutable evidence.
            raise ReceiptStorageUnavailable("Receipt immutability guards are not installed")
        statement = (
            insert(self.model.__table__)
            .values(**value)
            .on_conflict_do_nothing(constraint="uq_llm_dispatch_receipts_identity")
            .returning(self.model.id)
        )
        created = (await session.execute(statement)).scalar_one_or_none() is not None
        if not created:
            existing = (
                (
                    await session.execute(
                        select(*(getattr(self.model, field) for field in RECEIPT_FIELDS)).where(
                            self.model.dispatch_id == value["dispatch_id"],
                            self.model.tenant_id == tenant_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is None or any(existing[field] != value[field] for field in RECEIPT_FIELDS):
                raise ReceiptEvidenceConflict("Receipt identity conflicts with stored evidence")
        return created
