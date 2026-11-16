"""Continuation of a stored 1C plan: second page never re-runs the planner (P17-T06)."""

from datetime import datetime, timezone

import pytest

from app.services.onec.metadata import parse_odata_metadata_xml
from app.services.onec.nl_query import (
    OneCAnswer,
    OneCNaturalLanguageError,
    OneCQueryAnswerService,
    plan_from_json,
)
from app.services.onec.query import (
    OneCConnectionContext,
    OneCQueryHistoryMetadata,
    OneCQueryResult,
    build_odata_query_target,
    encode_page_token,
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

ALLOWED_SETS = frozenset({"Catalog_Товары"})
ALLOWED_FIELDS = {"Catalog_Товары": frozenset({"Ref_Key", "Description", "Цена"})}

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


def stored_plan(**overrides):
    payload = dict(STORED_PLAN)
    payload.update(overrides)
    return payload


class FakeEndpoint:
    base_path = "/odata/standard.odata"


def connection_context(metadata_sha256):
    return OneCConnectionContext(
        endpoint=FakeEndpoint(),
        credentials=object(),
        account_key="budget-key",
        allowed_entity_sets=ALLOWED_SETS,
        allowed_fields=ALLOWED_FIELDS,
        metadata_sha256=metadata_sha256,
    )


@pytest.fixture()
def catalog():
    return parse_odata_metadata_xml(METADATA_XML.encode("utf-8"))


class FakeHistory:
    """In-memory stand-in for the actor/tenant-scoped repository."""

    def __init__(self, plan_json, *, connection_id=7, error=None):
        self.plan_json = plan_json
        self.connection_id = connection_id
        self.error = error
        self.recorded = []
        self.loaded = []

    async def load_plan(self, *, tenant_id, actor_id, history_id):
        if self.error is not None:
            raise self.error
        self.loaded.append((tenant_id, actor_id, history_id))
        return type(
            "Stored", (), {"id": history_id, "connection_id": self.connection_id, "plan_json": dict(self.plan_json)}
        )()

    async def record(self, **kwargs):
        self.recorded.append(kwargs)
        return 999


def plan_fingerprint(plan, catalog):
    validated = build_odata_query_target(
        plan,
        catalog,
        allowed_entity_sets=ALLOWED_SETS,
        allowed_fields_by_entity=ALLOWED_FIELDS,
        expected_metadata_sha256=catalog.metadata_sha256,
        base_path="/odata/standard.odata",
    )
    return validated.plan_fingerprint


def result_for(plan, *, next_page_token=None, skip=0):
    return OneCQueryResult(
        entity_set=plan.entity_set,
        entity_kind="Catalog",
        register_function=None,
        selected_fields=plan.select_fields,
        rows=({"Description": "Товар", "Цена": "10.00"},),
        row_count=1,
        top=plan.top,
        skip=skip,
        next_page_token=next_page_token,
        plan_fingerprint="fingerprint",
        history_metadata=OneCQueryHistoryMetadata(
            query_id="q-1",
            tenant_id=1,
            actor_id=2,
            connection_id=7,
            entity_set=plan.entity_set,
            entity_kind="Catalog",
            register_function=None,
            plan_fingerprint="fingerprint",
            row_count=1,
            top=plan.top,
            skip=skip,
            duration_ms=3,
            from_cache=False,
            executed_at=datetime.now(timezone.utc),
        ),
    )


class FakeReader:
    def __init__(self, catalog, *, context_connection_id=7):
        self._catalog = catalog
        self._context_connection_id = context_connection_id
        self.executed = []
        self.metadata_calls = 0

    async def load_connection_context(self, *, tenant_id, actor_id, connection_id):
        assert connection_id == self._context_connection_id
        return connection_context(self._catalog.metadata_sha256)

    async def fetch_metadata_catalog(self, **kwargs):
        self.metadata_calls += 1
        return self._catalog

    async def execute_query(self, *, tenant_id, actor_id, connection_id, plan, catalog, cache_ttl_seconds=0, **kwargs):
        self.executed.append(plan)
        skip = 10 if plan.page_token else 0
        return result_for(plan, next_page_token="next" if not plan.page_token else None, skip=skip)


class Plannercounter:
    """Planner stand-in that fails the test if a continuation ever reaches it."""

    def __init__(self):
        self.calls = 0

    async def interpret(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("planner must not run for a stored-plan continuation")


def service(catalog, *, stored=None, connection_id=7, error=None):
    planner = Plannercounter()
    answer_service = OneCQueryAnswerService(planner=planner)
    reader = FakeReader(catalog)
    history = FakeHistory(stored if stored is not None else stored_plan(), connection_id=connection_id, error=error)
    return answer_service, planner, reader, history


@pytest.mark.asyncio
async def test_continuation_skips_the_planner_and_advances_the_page(catalog):
    answer_service, planner, reader, history = service(catalog)
    token = encode_page_token(plan_fingerprint=plan_fingerprint(plan_from_json(stored_plan()), catalog), skip=10)

    answer = await answer_service.answer(
        tenant_id=1,
        actor_id=2,
        connection_id=7,
        question="покажи товары",
        history=history,
        reader=reader,
        page_token=token,
        history_id=44,
    )

    assert planner.calls == 0
    assert history.loaded == [(1, 2, 44)]
    assert reader.executed[0].page_token == token
    assert answer.result.skip == 10
    assert answer.history_id == 44
    # A continuation reuses the stored row instead of duplicating the history entry.
    assert history.recorded == []


@pytest.mark.asyncio
async def test_stored_plan_without_token_reexecutes_the_first_page(catalog):
    answer_service, planner, reader, history = service(catalog)

    answer = await answer_service.answer(
        tenant_id=1, actor_id=2, connection_id=7, history=history, reader=reader, history_id=44
    )

    assert planner.calls == 0
    assert answer.result.skip == 0
    assert answer.history_id == 44
    assert history.recorded == []


@pytest.mark.asyncio
async def test_stored_plan_of_another_connection_is_refused(catalog):
    answer_service, planner, reader, history = service(catalog, connection_id=8)

    with pytest.raises(OneCNaturalLanguageError) as excinfo:
        await answer_service.answer(
            tenant_id=1, actor_id=2, connection_id=7, history=history, reader=reader, history_id=44
        )

    assert excinfo.value.code == "history_not_found"
    assert planner.calls == 0 and reader.executed == []


@pytest.mark.asyncio
async def test_token_of_another_plan_is_refused(catalog):
    answer_service, _planner, reader, history = service(catalog)

    with pytest.raises(OneCNaturalLanguageError) as excinfo:
        await answer_service.answer(
            tenant_id=1,
            actor_id=2,
            connection_id=7,
            history=history,
            reader=reader,
            page_token="token-from-another-plan",
            history_id=44,
        )

    assert excinfo.value.code == "invalid_page_token"
    assert reader.executed == []


@pytest.mark.asyncio
async def test_continuation_refuses_a_stored_plan_outside_the_allowlist(catalog):
    answer_service, _planner, reader, history = service(catalog, stored=stored_plan(entity_set="Catalog_Секреты"))

    with pytest.raises(OneCNaturalLanguageError) as excinfo:
        await answer_service.answer(
            tenant_id=1, actor_id=2, connection_id=7, history=history, reader=reader, history_id=44
        )

    assert excinfo.value.code == "entity_not_allowed"
    assert reader.executed == []


@pytest.mark.asyncio
async def test_question_is_required_when_there_is_no_stored_plan(catalog):
    answer_service, planner, reader, history = service(catalog)

    with pytest.raises(OneCNaturalLanguageError) as excinfo:
        await answer_service.answer(tenant_id=1, actor_id=2, connection_id=7, history=history, reader=reader)

    assert excinfo.value.code == "question_required"
    assert planner.calls == 0


@pytest.mark.asyncio
async def test_missing_history_row_is_reported_once(catalog):
    answer_service, _planner, reader, history = service(catalog, error=OneCNaturalLanguageError("history_not_found"))

    with pytest.raises(OneCNaturalLanguageError) as excinfo:
        await answer_service.answer(
            tenant_id=1, actor_id=2, connection_id=7, history=history, reader=reader, history_id=44
        )

    assert excinfo.value.code == "history_not_found"


def test_stored_plan_roundtrip_keeps_page_token_out_of_the_row():
    from app.services.onec.nl_query import plan_to_json

    plan = plan_from_json(stored_plan())
    assert plan.page_token is None
    assert plan_to_json(plan)["top"] == 10
    assert "page_token" not in plan_to_json(plan)


def test_answer_dataclass_keeps_question_optional():
    assert OneCAnswer(question="").history_id is None
