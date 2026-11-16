"""History status set: only the states the service writes are valid (P17-T08, F7)."""

from __future__ import annotations

import pytest

from app.services.onec.history import VALID_STATUSES, OneCHistoryError, OneCQueryHistoryRepository


def test_the_repository_refuses_a_status_the_service_never_writes():
    repo = OneCQueryHistoryRepository()
    assert VALID_STATUSES == frozenset({"executed", "exported"})


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["planned", "refused", "unknown", ""])
async def test_an_unwritten_status_is_refused_before_any_write(status):
    repo = OneCQueryHistoryRepository()
    with pytest.raises(OneCHistoryError) as excinfo:
        await repo.record(
            tenant_id=11,
            actor_id=7,
            connection_id=5,
            entity_set="Catalog_Товары",
            plan_json={"entity_set": "Catalog_Товары"},
            plan_fingerprint="b" * 64,
            question="покажи товары",
            status=status,
        )
    assert excinfo.value.code == "invalid_status"
