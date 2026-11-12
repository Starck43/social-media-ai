"""Per-connection `$metadata` catalog cache for the 1C read path (P17-T08, F6).

The catalog describes the 1C schema, never tenant rows, so one question must not cost
two provider requests. No network, no DB: the transport and the session are doubles.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.integrations.onec.credentials import BasicCredentials
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.onec import query as query_mod
from app.services.onec.metadata import parse_odata_metadata_xml
from app.services.onec.query import (
    MAX_METADATA_CACHE_ENTRIES,
    METADATA_CACHE_TTL_SECONDS,
    OneCQueryError,
    OneCMetadataCache,
    OneCReadQueryService,
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


def service_fixture(monkeypatch, *, clock=None, pinned=True, connection_id=5):
    catalog = parse_odata_metadata_xml(METADATA_XML)
    config = {
        "host": "erp.example.com",
        "port": 443,
        "base_path": "/base",
        "interface": "odata_standard",
        "allowed_entity_sets": ["Catalog_Номенклатура"],
    }
    if pinned:
        config["metadata_sha256"] = catalog.metadata_sha256
    row = SimpleNamespace(
        id=connection_id,
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
    metadata_cache = OneCMetadataCache(clock=clock or (lambda: 1000.0))
    service = OneCReadQueryService(metadata_cache=metadata_cache)

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
    return service, catalog, row, metadata_cache


@pytest.mark.asyncio
async def test_two_consecutive_questions_fetch_metadata_once(monkeypatch):
    service, catalog, _, _ = service_fixture(monkeypatch)
    transport = AsyncMock(return_value=METADATA_XML.encode("utf-8"))
    monkeypatch.setattr(query_mod, "fetch_odata_bytes", transport)

    first = await service.fetch_metadata_catalog(tenant_id=11, actor_id=7, connection_id=5)
    second = await service.fetch_metadata_catalog(tenant_id=11, actor_id=7, connection_id=5)

    assert first.metadata_sha256 == second.metadata_sha256 == catalog.metadata_sha256
    # The second question is served from the cache: no second provider request.
    assert transport.await_count == 1


@pytest.mark.asyncio
async def test_a_changed_metadata_pin_invalidates_the_cache(monkeypatch):
    service, catalog, row, _ = service_fixture(monkeypatch)
    transport = AsyncMock(return_value=METADATA_XML.encode("utf-8"))
    monkeypatch.setattr(query_mod, "fetch_odata_bytes", transport)

    await service.fetch_metadata_catalog(tenant_id=11, actor_id=7, connection_id=5)
    assert transport.await_count == 1

    # A re-verified connection changes the pin: the cached catalog must not be served.
    row.config["metadata_sha256"] = "f" * 64
    with pytest.raises(OneCQueryError, match="metadata_hash_mismatch"):
        await service.fetch_metadata_catalog(tenant_id=11, actor_id=7, connection_id=5)
    assert transport.await_count == 2


@pytest.mark.asyncio
async def test_an_expired_entry_is_refetched(monkeypatch):
    now = [1000.0]
    service, _, _, _ = service_fixture(monkeypatch, clock=lambda: now[0])
    transport = AsyncMock(return_value=METADATA_XML.encode("utf-8"))
    monkeypatch.setattr(query_mod, "fetch_odata_bytes", transport)

    await service.fetch_metadata_catalog(tenant_id=11, actor_id=7, connection_id=5)
    now[0] += METADATA_CACHE_TTL_SECONDS + 1
    await service.fetch_metadata_catalog(tenant_id=11, actor_id=7, connection_id=5)

    assert transport.await_count == 2


@pytest.mark.asyncio
async def test_the_cache_is_scoped_per_connection(monkeypatch):
    service, _, _, cache = service_fixture(monkeypatch)
    transport = AsyncMock(return_value=METADATA_XML.encode("utf-8"))
    monkeypatch.setattr(query_mod, "fetch_odata_bytes", transport)

    await service.fetch_metadata_catalog(tenant_id=11, actor_id=7, connection_id=5)
    await service.fetch_metadata_catalog(tenant_id=11, actor_id=7, connection_id=6)

    assert transport.await_count == 2
    assert len(cache._store) == 2


@pytest.mark.asyncio
async def test_authorization_runs_before_the_cache_is_used(monkeypatch):
    service, _, _, _ = service_fixture(monkeypatch)
    transport = AsyncMock(return_value=METADATA_XML.encode("utf-8"))
    monkeypatch.setattr(query_mod, "fetch_odata_bytes", transport)
    monkeypatch.setattr(query_mod, "_authorize", AsyncMock(side_effect=ConnectionDenied()))

    with pytest.raises(ConnectionDenied):
        await service.fetch_metadata_catalog(tenant_id=11, actor_id=7, connection_id=5)
    # The refused caller never reaches the provider, and nothing is cached for it.
    transport.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unpinned_connection_still_caches_but_only_for_the_ttl(monkeypatch):
    service, _, _, _ = service_fixture(monkeypatch, pinned=False)
    transport = AsyncMock(return_value=METADATA_XML.encode("utf-8"))
    monkeypatch.setattr(query_mod, "fetch_odata_bytes", transport)

    await service.fetch_metadata_catalog(tenant_id=11, actor_id=7, connection_id=5)
    await service.fetch_metadata_catalog(tenant_id=11, actor_id=7, connection_id=5)

    assert transport.await_count == 1


def test_the_cache_is_bounded_and_drops_expired_entries():
    now = [1000.0]
    cache = OneCMetadataCache(max_entries=2, clock=lambda: now[0])
    catalog = parse_odata_metadata_xml(METADATA_XML.encode("utf-8"))

    for index in range(3):
        cache.put((11, index, ""), catalog, ttl_seconds=METADATA_CACHE_TTL_SECONDS)
    assert len(cache._store) == 2

    now[0] += METADATA_CACHE_TTL_SECONDS + 1
    assert cache.get((11, 0, "")) is None
    assert MAX_METADATA_CACHE_ENTRIES >= 2


def test_invalidate_connection_drops_only_that_connection():
    cache = OneCMetadataCache()
    catalog = parse_odata_metadata_xml(METADATA_XML.encode("utf-8"))
    cache.put((11, 5, ""), catalog, ttl_seconds=METADATA_CACHE_TTL_SECONDS)
    cache.put((11, 6, ""), catalog, ttl_seconds=METADATA_CACHE_TTL_SECONDS)

    cache.invalidate_connection(11, 5)

    assert cache.get((11, 5, "")) is None
    assert cache.get((11, 6, "")) is not None
