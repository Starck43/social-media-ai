"""Cache lifecycle and request cost for the 1C read path (P17-T09).

No network and no DB: the transport, the session and the history repository are doubles.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.integrations.onec.credentials import BasicCredentials
from app.services.onec import nl_query as nl_query_mod
from app.services.onec import query as query_mod
from app.services.onec.metadata import parse_odata_metadata_xml
from app.services.onec.nl_query import OneCNaturalLanguageError, OneCQueryAnswerService
from app.services.onec.query import (
    MAX_METADATA_CACHE_ENTRIES,
    OneCQueryCache,
    OneCQueryPlan,
    OneCReadQueryService,
    invalidate_metadata_catalog,
)

METADATA_XML = """<?xml version="1.0" encoding="UTF-8"?>
<edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx">
  <edmx:DataServices>
    <Schema Namespace="StandardODATA">
      <EntityType Name="Catalog_Номенклатура">
        <Key><PropertyRef Name="Ref_Key"/></Key>
        <Property Name="Ref_Key" Type="Edm.Guid" Nullable="false"/>
        <Property Name="Description" Type="Edm.String"/>
      </EntityType>
      <EntityContainer Name="Container">
        <EntitySet Name="Catalog_Номенклатура" EntityType="StandardODATA.Catalog_Номенклатура"/>
      </EntityContainer>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>"""


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class Session:
    def __init__(self, row, secret):
        self.row = row
        self.secret = secret

    async def execute(self, statement):
        return Result(self.row)

    async def get(self, model, key):
        return self.secret


def service_fixture(monkeypatch, *, clock=None, metadata_cache=None, query_cache=None):
    catalog = parse_odata_metadata_xml(METADATA_XML)
    config = {
        "host": "erp.example.com",
        "port": 443,
        "base_path": "/base",
        "interface": "odata_standard",
        "metadata_sha256": catalog.metadata_sha256,
        "allowed_entity_sets": ["Catalog_Номенклатура"],
    }
    row = SimpleNamespace(
        id=5,
        tenant_id=11,
        owner_user_id=7,
        is_active=True,
        provider_id="onec",
        auth_method="odata_credentials",
        schema_version=1,
        config=config,
        account_key="account-digest-01",
        label="1C ERP Prod",
        state="verified",
    )
    session = Session(row, SimpleNamespace(secret_encrypted="ciphertext-v1"))
    service = OneCReadQueryService(
        cache=query_cache if query_cache is not None else OneCQueryCache(clock=clock or (lambda: 1000.0)),
        metadata_cache=(
            metadata_cache
            if metadata_cache is not None
            else query_mod.OneCMetadataCache(clock=clock or (lambda: 1000.0))
        ),
    )

    @asynccontextmanager
    async def owned_session():
        yield session

    service._sessions._get_owned_session = owned_session
    service._hosts.require_approved = AsyncMock(return_value="erp.example.com")
    monkeypatch.setattr(query_mod, "_authorize", AsyncMock())
    monkeypatch.setattr(
        query_mod,
        "open_basic_credentials",
        lambda ciphertext: BasicCredentials("odata_reader", "synthetic-secret"),
    )
    monkeypatch.setattr(
        query_mod,
        "budget_for",
        lambda provider, account, *, connection_id: f"budget:{provider}:{account}:{connection_id}",
    )
    return service, catalog, row


def plan_for(entity_set="Catalog_Номенклатура"):
    return OneCQueryPlan(entity_set=entity_set, select_fields=("Ref_Key",), top=100)


# --- eviction order -------------------------------------------------------


def test_the_catalog_cache_evicts_the_least_recently_used_entry():
    now = [1000.0]
    cache = query_mod.OneCMetadataCache(max_entries=2, clock=lambda: now[0])
    catalog = parse_odata_metadata_xml(METADATA_XML.encode("utf-8"))
    cache.put((11, 5, "a"), catalog, ttl_seconds=60)
    cache.put((11, 6, "b"), catalog, ttl_seconds=60)

    # A hit makes the first entry the most recently used one.
    assert cache.get((11, 5, "a")) is not None
    cache.put((11, 7, "c"), catalog, ttl_seconds=60)

    assert cache.get((11, 6, "b")) is None
    assert cache.get((11, 5, "a")) is not None
    assert cache.get((11, 7, "c")) is not None


def test_the_result_cache_evicts_the_least_recently_used_entry():
    now = [1000.0]
    cache = OneCQueryCache(max_entries=2, clock=lambda: now[0])
    cache.put((11, 7, 5, "k", "s", "p", 100, 0), ({"Ref_Key": "a"},), None, ttl_seconds=60)
    cache.put((11, 7, 6, "k", "s", "p", 100, 0), ({"Ref_Key": "b"},), None, ttl_seconds=60)

    assert cache.get((11, 7, 5, "k", "s", "p", 100, 0)) is not None
    cache.put((11, 7, 7, "k", "s", "p", 100, 0), ({"Ref_Key": "c"},), None, ttl_seconds=60)

    assert cache.get((11, 7, 6, "k", "s", "p", 100, 0)) is None
    assert cache.get((11, 7, 5, "k", "s", "p", 100, 0)) is not None


def test_the_catalog_cache_stays_bounded():
    cache = query_mod.OneCMetadataCache()
    catalog = parse_odata_metadata_xml(METADATA_XML.encode("utf-8"))
    for index in range(MAX_METADATA_CACHE_ENTRIES + 5):
        cache.put((11, index, ""), catalog, ttl_seconds=60)
    assert len(cache._store) == MAX_METADATA_CACHE_ENTRIES


# --- invalidation ---------------------------------------------------------


def test_invalidate_metadata_catalog_drops_only_that_connection():
    cache = query_mod.OneCMetadataCache()
    catalog = parse_odata_metadata_xml(METADATA_XML.encode("utf-8"))
    cache.put((11, 5, "a"), catalog, ttl_seconds=60)
    cache.put((11, 6, "b"), catalog, ttl_seconds=60)
    monkey_cached = query_mod._METADATA_CACHE
    query_mod._METADATA_CACHE = cache
    try:
        invalidate_metadata_catalog(11, 5)
    finally:
        query_mod._METADATA_CACHE = monkey_cached
    assert cache.get((11, 5, "a")) is None
    assert cache.get((11, 6, "b")) is not None


def test_invalidate_metadata_catalog_ignores_broken_arguments():
    invalidate_metadata_catalog(0, 5)
    invalidate_metadata_catalog(11, 0)
    invalidate_metadata_catalog("11", 5)


# --- ordering -------------------------------------------------------------


class ExplodingReader:
    """A reader that must not be asked for a catalog once the request is refused."""

    def __init__(self):
        self.catalog_calls = 0
        self.executed = []

    async def load_connection_context(self, **kwargs):
        from app.services.onec.query import OneCConnectionContext

        class Endpoint:
            base_path = "/base"

        return OneCConnectionContext(
            endpoint=Endpoint(),
            credentials=object(),
            account_key="k" * 64,
            allowed_entity_sets=frozenset({"Catalog_Номенклатура"}),
            allowed_fields={},
            metadata_sha256=None,
        )

    async def fetch_metadata_catalog(self, **kwargs):
        self.catalog_calls += 1
        raise AssertionError("the catalog must not be fetched for a refused continuation")

    async def execute_query(self, **kwargs):
        self.executed.append(kwargs)
        raise AssertionError("a refused continuation must not execute")


class ForeignHistory:
    """A history row of another connection."""

    def __init__(self, connection_id=99):
        self.connection_id = connection_id

    async def load_plan(self, *, tenant_id, actor_id, history_id):
        return SimpleNamespace(id=history_id, connection_id=self.connection_id, plan_json={})


@pytest.mark.asyncio
async def test_a_foreign_stored_row_is_refused_before_the_catalog_is_fetched():
    reader = ExplodingReader()
    service = OneCQueryAnswerService()
    with pytest.raises(OneCNaturalLanguageError) as excinfo:
        await service.answer(
            tenant_id=11,
            actor_id=7,
            connection_id=5,
            history=ForeignHistory(),
            reader=reader,
            history_id=42,
        )
    assert excinfo.value.code == "history_not_found"
    assert reader.catalog_calls == 0
    assert reader.executed == []


# --- the shared reader ----------------------------------------------------


def test_the_answer_service_uses_one_shared_reader():
    assert isinstance(nl_query_mod._SHARED_READER, OneCReadQueryService)


@pytest.mark.asyncio
async def test_the_opt_in_result_cache_serves_a_repeated_query(monkeypatch):
    service, catalog, _ = service_fixture(monkeypatch)
    transport = AsyncMock(return_value=b'{"value": [{"Ref_Key": "a"}]}')
    monkeypatch.setattr(query_mod, "fetch_odata_bytes", transport)
    plan = plan_for()

    first = await service.execute_query(
        tenant_id=11, actor_id=7, connection_id=5, plan=plan, catalog=catalog, cache_ttl_seconds=300
    )
    second = await service.execute_query(
        tenant_id=11, actor_id=7, connection_id=5, plan=plan, catalog=catalog, cache_ttl_seconds=300
    )

    assert first.row_count == second.row_count == 1
    # The second call was served from the cache of the same reader instance.
    assert transport.await_count == 1


# --- the connection mutation paths invalidate the catalog ------------------


def api_app():
    import httpx
    from fastapi import FastAPI

    from app.api.v1.endpoints import onec
    from app.services.user.auth import get_authenticated_user

    app = FastAPI()
    app.include_router(onec.router, prefix="/api/v1/integrations/1c")
    app.dependency_overrides[get_authenticated_user] = lambda: SimpleNamespace(id=7)

    @app.middleware("http")
    async def scope(request, call_next):
        request.state.tenant_id = 11
        return await call_next(request)

    return app, onec


def api_request(method, path, body=None):
    import asyncio

    import httpx

    app, _ = api_app()

    async def send():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, "/api/v1/integrations/1c" + path, json=body)

    return asyncio.run(send())


@pytest.fixture()
def fake_connections(monkeypatch):
    _, onec = api_app()
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        onec, "invalidate_metadata_catalog", lambda tenant_id, connection_id: calls.append((tenant_id, connection_id))
    )
    monkeypatch.setattr(
        onec,
        "OneCConnectionService",
        lambda: SimpleNamespace(
            disable=AsyncMock(return_value=None),
            replace_credentials=AsyncMock(
                return_value={
                    "id": 5,
                    "label": "l",
                    "host": "h",
                    "port": 443,
                    "base_path": "/b",
                    "interface": "odata_standard",
                    "state": "verified",
                    "check_code": None,
                    "checked_at": None,
                    "is_active": True,
                }
            ),
            list_for_actor=AsyncMock(return_value=[]),
        ),
    )
    monkeypatch.setattr(
        onec,
        "OneCHealthService",
        lambda: SimpleNamespace(
            check=AsyncMock(
                return_value={
                    "id": 5,
                    "label": "l",
                    "host": "h",
                    "port": 443,
                    "base_path": "/b",
                    "interface": "odata_standard",
                    "state": "verified",
                    "check_code": None,
                    "checked_at": None,
                    "is_active": True,
                }
            )
        ),
    )
    return calls


def test_replacing_the_credentials_invalidates_the_catalog(fake_connections):
    response = api_request("PATCH", "/connections/5/credentials", {"login": "u", "password": "p"})
    assert response.status_code == 200
    assert fake_connections == [(11, 5)]


def test_disabling_a_connection_invalidates_the_catalog(fake_connections):
    response = api_request("DELETE", "/connections/5")
    assert response.status_code == 204
    assert fake_connections == [(11, 5)]


def test_a_health_check_invalidates_the_catalog(fake_connections):
    response = api_request("POST", "/connections/5/health-check")
    assert response.status_code == 200
    assert fake_connections == [(11, 5)]


def test_reading_the_connections_does_not_invalidate_the_catalog(fake_connections):
    response = api_request("GET", "/connections")
    assert response.status_code == 200
    assert fake_connections == []
