"""Exporting a stored 1C result marks its history row instead of duplicating it (P17-T07 F3)."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.services.onec.metadata import parse_odata_metadata_xml
from app.services.onec.nl_query import OneCNaturalLanguageError, OneCQueryAnswerService
from app.services.onec.query import (
    MonetaryValue,
    OneCConnectionContext,
    OneCQueryHistoryMetadata,
    OneCQueryResult,
)

METADATA_XML = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx">
  <edmx:DataServices>
    <Schema Namespace="StandardODATA">
      <EntityType Name="Catalog_Товары">
        <Key><PropertyRef Name="Ref_Key"/></Key>
        <Property Name="Ref_Key" Type="Edm.Guid" Nullable="false"/>
        <Property Name="Description" Type="Edm.String"/>
        <Property Name="Цена" Type="Edm.Decimal" Precision="15" Scale="2"/>
      </EntityType>
      <EntityContainer Name="Container">
        <EntitySet Name="Catalog_Товары" EntityType="StandardODATA.Catalog_Товары"/>
      </EntityContainer>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
"""

STORED_PLAN = {
    "entity_set": "Catalog_Товары",
    "select_fields": ["Description", "Цена"],
    "filters": [],
    "order_by": [],
    "register_function": None,
    "period": None,
    "date_from": None,
    "date_to": None,
    "top": 10,
    "default_currency": "RUB",
}


class FakeEndpoint:
    base_path = "/odata/standard.odata"


@pytest.fixture()
def catalog():
    return parse_odata_metadata_xml(METADATA_XML.encode("utf-8"))


class FakeHistory:
    def __init__(self, *, connection_id=7):
        self.connection_id = connection_id
        self.marked = []
        self.recorded = []

    async def load_plan(self, *, tenant_id, actor_id, history_id):
        return type(
            "Stored", (), {"id": history_id, "connection_id": self.connection_id, "plan_json": dict(STORED_PLAN)}
        )()

    async def mark_exported(self, *, tenant_id, actor_id, history_id, exported_format):
        self.marked.append((history_id, exported_format))

    async def record(self, **kwargs):
        self.recorded.append(kwargs)
        return 999


class FakeReader:
    def __init__(self, catalog):
        self._catalog = catalog

    async def load_connection_context(self, *, tenant_id, actor_id, connection_id):
        return OneCConnectionContext(
            endpoint=FakeEndpoint(),
            credentials=object(),
            account_key="budget-key",
            allowed_entity_sets=frozenset({"Catalog_Товары"}),
            allowed_fields={"Catalog_Товары": frozenset({"Ref_Key", "Description", "Цена"})},
            metadata_sha256=self._catalog.metadata_sha256,
        )

    async def fetch_metadata_catalog(self, **kwargs):
        return self._catalog

    async def execute_query(self, *, tenant_id, actor_id, connection_id, plan, catalog, cache_ttl_seconds=0, **kw):
        rows = ({"Description": "Товар", "Цена": MonetaryValue(amount=Decimal("10.00"))},)
        return OneCQueryResult(
            entity_set=plan.entity_set,
            entity_kind="Catalog",
            register_function=None,
            selected_fields=plan.select_fields,
            rows=rows,
            row_count=1,
            top=plan.top,
            skip=0,
            next_page_token=None,
            plan_fingerprint="f" * 64,
            history_metadata=OneCQueryHistoryMetadata(
                query_id="q-1",
                tenant_id=tenant_id,
                actor_id=actor_id,
                connection_id=connection_id,
                entity_set=plan.entity_set,
                entity_kind="Catalog",
                register_function=None,
                plan_fingerprint="f" * 64,
                row_count=1,
                top=plan.top,
                skip=0,
                duration_ms=2,
                from_cache=False,
                executed_at=datetime.now(timezone.utc),
            ),
        )


@pytest.mark.asyncio
async def test_export_of_a_stored_result_marks_the_same_row(catalog):
    history = FakeHistory()
    file, history_id = await OneCQueryAnswerService().export(
        tenant_id=1,
        actor_id=2,
        connection_id=7,
        format="xlsx",
        history=history,
        history_id=42,
        reader=FakeReader(catalog),
    )

    assert history_id == 42
    assert history.marked == [(42, "xlsx")]
    assert history.recorded == []
    assert file.extension == "xlsx" and file.row_count == 1


@pytest.mark.asyncio
async def test_export_of_a_stored_result_of_another_connection_is_refused(catalog):
    history = FakeHistory(connection_id=8)

    with pytest.raises(OneCNaturalLanguageError) as excinfo:
        await OneCQueryAnswerService().export(
            tenant_id=1,
            actor_id=2,
            connection_id=7,
            format="xlsx",
            history=history,
            history_id=42,
            reader=FakeReader(catalog),
        )

    assert excinfo.value.code == "stored_plan_connection_mismatch"
    assert history.marked == [] and history.recorded == []
