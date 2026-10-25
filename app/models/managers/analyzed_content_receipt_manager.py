"""Tenant-scoped persistence for successful analysis coverage receipts."""

from __future__ import annotations

from collections.abc import Sequence
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.tenant_context import TenantContextError, current_tenant_id
from .base_manager import BaseManager


class AnalyzedContentReceiptManager(BaseManager):
    def __init__(self):
        from app.models.analysis.analyzed_content_receipt import AnalyzedContentReceipt

        super().__init__(AnalyzedContentReceipt)

    async def covered_hashes(
        self, source_id: int, analysis_fingerprint: str, hashes: Sequence[str], *, tenant_id: int
    ) -> set[str]:
        """Return matching receipt hashes; lookup errors deliberately propagate."""
        return (await self.covered_by_contexts(
            source_id, [analysis_fingerprint], hashes, tenant_id=tenant_id
        )).get(analysis_fingerprint, set())

    async def covered_by_contexts(
        self,
        source_id: int,
        analysis_fingerprints: Sequence[str],
        hashes: Sequence[str],
        *,
        tenant_id: int,
    ) -> dict[str, set[str]]:
        """Return exact receipts for several contexts in one scoped query."""
        from app.models.analysis.analyzed_content_receipt import AnalyzedContentReceipt

        active_tenant_id = current_tenant_id()
        if type(tenant_id) is not int or tenant_id <= 0:
            raise TenantContextError("Analysis receipt lookup requires an explicit tenant")
        if type(source_id) is not int or source_id <= 0:
            raise ValueError("analysis_receipt_source_invalid")
        fingerprints = list(dict.fromkeys(analysis_fingerprints))
        if not fingerprints:
            return {}
        if any(type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None for value in fingerprints):
            raise ValueError("analysis_receipt_fingerprint_invalid")
        if active_tenant_id is not None and active_tenant_id != tenant_id:
            raise TenantContextError("Analysis receipt tenant differs from the active scope")
        from app.models import Source

        if not await Source.objects.filter(id=source_id, tenant_id=tenant_id).exists():
            raise TenantContextError("Analysis receipt source differs from the explicit tenant")
        wanted = list(dict.fromkeys(h for h in hashes if isinstance(h, str)))
        if not wanted:
            return {fingerprint: set() for fingerprint in fingerprints}
        rows = await self.filter(
            AnalyzedContentReceipt.content_hash.in_(wanted),
            AnalyzedContentReceipt.analysis_fingerprint.in_(fingerprints),
            tenant_id=tenant_id,
            source_id=source_id,
        ).values(AnalyzedContentReceipt.analysis_fingerprint, AnalyzedContentReceipt.content_hash).rows()
        covered = {fingerprint: set() for fingerprint in fingerprints}
        for row in rows:
            if row and row[0] in covered and row[1]:
                covered[row[0]].add(row[1])
        return covered

    async def record_successes(
        self,
        session: Any,
        *,
        tenant_id: int,
        source_id: int,
        analysis_fingerprint: str,
        hashes: Sequence[str],
        scenario_id: int | None,
        analytics_id: int,
    ) -> None:
        """Insert only successful coverage in the caller's analytics transaction."""
        from app.models.analysis.analyzed_content_receipt import AnalyzedContentReceipt
        from app.models import AgentScenario, AIAnalytics, Source

        active_tenant_id = current_tenant_id()
        if type(tenant_id) is not int or tenant_id <= 0:
            raise TenantContextError("Analysis receipt write requires an explicit tenant")
        if active_tenant_id is not None and active_tenant_id != tenant_id:
            raise TenantContextError("Analysis receipt tenant differs from the active scope")
        if type(source_id) is not int or source_id <= 0 or type(analytics_id) is not int or analytics_id <= 0:
            raise ValueError("analysis_receipt_identity_invalid")
        if type(analysis_fingerprint) is not str or re.fullmatch(r"[0-9a-f]{64}", analysis_fingerprint) is None:
            raise ValueError("analysis_receipt_fingerprint_invalid")
        if scenario_id is not None and (type(scenario_id) is not int or scenario_id <= 0):
            raise ValueError("analysis_receipt_scenario_invalid")
        source = (await session.execute(
            select(Source.id).where(Source.id == source_id, Source.tenant_id == tenant_id)
        )).scalar_one_or_none()
        if source is None:
            raise TenantContextError("Analysis receipt source differs from the explicit tenant")
        analytics = (await session.execute(select(AIAnalytics.id).where(
            AIAnalytics.id == analytics_id,
            AIAnalytics.source_id == source_id,
            AIAnalytics.tenant_id == tenant_id,
            AIAnalytics.analysis_fingerprint == analysis_fingerprint,
        ))).scalar_one_or_none()
        if analytics is None:
            raise ValueError("analysis_receipt_analytics_mismatch")
        if scenario_id is not None:
            scenario = (await session.execute(select(AgentScenario.id).where(
                AgentScenario.id == scenario_id, AgentScenario.tenant_id == tenant_id
            ))).scalar_one_or_none()
            if scenario is None:
                raise TenantContextError("Analysis receipt scenario differs from the explicit tenant")
        unique_hashes = list(dict.fromkeys(hashes))
        if any(type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None for value in unique_hashes):
            raise ValueError("analysis_receipt_content_hash_invalid")
        values = [
            {
                "tenant_id": tenant_id,
                "source_id": source_id,
                "content_hash": value,
                "analysis_fingerprint": analysis_fingerprint,
                "scenario_id": scenario_id,
                "analytics_id": analytics_id,
            }
            for value in unique_hashes
        ]
        if not values:
            return
        stmt = insert(AnalyzedContentReceipt).values(values).on_conflict_do_nothing(
            constraint="uq_analyzed_content_receipt_identity"
        )
        await session.execute(stmt)
