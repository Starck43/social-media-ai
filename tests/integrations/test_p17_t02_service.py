"""Service, transport, rate-limit budget, and opt-in TTL cache tests for 1C read-only extraction (P17-T02)."""

from __future__ import annotations

import asyncio
import json
import socket
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.integrations.onec.credentials import BasicCredentials, OneCCredentialError
from app.integrations.onec.endpoint import validate_endpoint
from app.integrations.onec.transport import MAX_BODY_BYTES
from app.integrations.ratelimit import ProviderDeferred
from app.integrations.ratelimit.store import BudgetConnectionUnavailable
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.onec import query as query_mod
from app.services.onec.metadata import parse_odata_metadata_xml
from app.services.onec.query import (
    OneCFilterPredicate,
    OneCQueryCache,
    OneCQueryError,
    OneCQueryPlan,
    OneCReadQueryService,
    decode_page_token,
    encode_page_token,
    fetch_odata_bytes,
)
from tests.integrations.test_p17_t02_metadata import SYNTHETIC_METADATA_XML


class Limiter:
    def __init__(self):
        self.calls = []
        self.signals = []

    @asynccontextmanager
    async def acquire(self, budget, *, cost, max_wait):
        self.calls.append((budget, cost, max_wait))
        yield

    async def note_signal(self, budget, signal):
        self.signals.append((budget, signal.retry_after))


class Reader:
    def __init__(self, payload: bytes):
        self.payload = payload

    async def read(self, n: int) -> bytes:
        chunk, self.payload = self.payload[:n], self.payload[n:]
        return chunk


class Writer:
    def __init__(self):
        self.wire = b""
        self.closed = False

    def write(self, data: bytes) -> None:
        self.wire += data

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class Session:
    def __init__(self, row, secret):
        self.row = row
        self.secret = secret
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return Result(self.row)

    async def get(self, model, key):
        return self.secret


def dns(host, port):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]


def http_response(status: int = 200, body: bytes = b"{}", extra: bytes = b"") -> bytes:
    return b"HTTP/1.1 %d OK\r\nContent-Length: %d\r\n" % (status, len(body)) + extra + b"\r\n" + body


def endpoint(interface: str = "odata_standard"):
    return validate_endpoint("https://erp.example.com/base", interface, frozenset({"erp.example.com"}))


def fake_network(monkeypatch, payload: bytes):
    writer = Writer()
    calls = []

    async def open_connection(host, port, **kw):
        calls.append((host, port, kw))
        return Reader(payload), writer

    monkeypatch.setattr(asyncio, "open_connection", open_connection)
    return calls, writer


def service_fixture(monkeypatch, *, allowed_sets=None, allowed_fields=None, state="verified", interface="odata_standard"):
    catalog = parse_odata_metadata_xml(SYNTHETIC_METADATA_XML)
    config = {
        "host": "erp.example.com",
        "port": 443,
        "base_path": "/base",
        "interface": interface,
        "metadata_sha256": catalog.metadata_sha256,
        "allowed_entity_sets": allowed_sets
        if allowed_sets is not None
        else [
            "Catalog_Номенклатура",
            "Document_РеализацияТоваровУслуг",
            "AccumulationRegister_ТоварыНаСкладах",
            "InformationRegister_ЦеныНоменклатуры",
        ],
    }
    if allowed_fields is not None:
        config["allowed_fields"] = allowed_fields

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
        state=state,
    )
    secret = SimpleNamespace(secret_encrypted="ciphertext-v1")
    session = Session(row, secret)
    clock_now = [100.0]
    cache = OneCQueryCache(clock=lambda: clock_now[0])
    service = OneCReadQueryService(cache=cache)

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
    return service, catalog, row, secret, session, clock_now


@pytest.mark.asyncio
async def test_fetch_odata_bytes_pins_dns_sends_get_and_handles_errors(monkeypatch):
    payload = b'{"value":[{"Ref_Key":"11111111-2222-3333-4444-555555555555"}]}'
    calls, writer = fake_network(monkeypatch, http_response(200, payload))
    limiter = Limiter()
    body = await fetch_odata_bytes(
        endpoint(),
        "/base/odata/standard.odata/Catalog_Номенклатура?$format=json&$top=1",
        "reader",
        "secret",
        limiter=limiter,
        budget="shared-budget",
        resolver=dns,
    )
    assert body == payload
    assert limiter.calls == [("shared-budget", 2, 0)]
    assert calls[0][0:2] == ("8.8.8.8", 443)
    assert calls[0][2]["server_hostname"] == "erp.example.com"
    assert b"GET /base/odata/standard.odata/" in writer.wire
    assert writer.closed


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (302, "redirect_forbidden"),
        (401, "authentication_failed"),
        (403, "authentication_failed"),
        (404, "entity_not_found"),
        (429, "rate_limited"),
        (500, "service_unavailable"),
        (503, "service_unavailable"),
    ],
)
@pytest.mark.asyncio
async def test_fetch_odata_bytes_status_mapping_and_redaction(monkeypatch, status, code):
    fake_network(monkeypatch, http_response(status, b"sensitive-body", extra=b"Retry-After: 9\r\n"))
    limiter = Limiter()
    with pytest.raises(OneCQueryError) as exc:
        await fetch_odata_bytes(
            endpoint(),
            "/base/odata/standard.odata/Catalog_Номенклатура",
            "reader",
            "secret",
            limiter=limiter,
            budget="b1",
            resolver=dns,
        )
    assert exc.value.code == code
    assert "sensitive-body" not in str(exc.value)
    if status in (429, 503):
        assert limiter.signals == [("b1", 9.0)]


@pytest.mark.asyncio
async def test_fetch_odata_bytes_rejects_gzip_oversize_and_invalid_inputs(monkeypatch):
    fake_network(monkeypatch, http_response(200, b"{}", extra=b"Content-Encoding: gzip\r\n"))
    with pytest.raises(OneCQueryError, match="unsupported_encoding"):
        await fetch_odata_bytes(endpoint(), "/base/odata", "reader", "secret", limiter=Limiter(), budget=1, resolver=dns)

    fake_network(monkeypatch, http_response(200, b"x" * (MAX_BODY_BYTES + 1)))
    with pytest.raises(OneCQueryError, match="response_too_large"):
        await fetch_odata_bytes(endpoint(), "/base/odata", "reader", "secret", limiter=Limiter(), budget=1, resolver=dns)

    fake_network(monkeypatch, http_response(200, b""))
    with pytest.raises(OneCQueryError, match="empty_response"):
        await fetch_odata_bytes(endpoint(), "/base/odata", "reader", "secret", limiter=Limiter(), budget=1, resolver=dns)

    with pytest.raises(OneCQueryError, match="invalid_credentials"):
        await fetch_odata_bytes(endpoint(), "/base/odata", "bad:login", "secret", limiter=Limiter(), budget=1, resolver=dns)

    with pytest.raises(OneCQueryError, match="invalid_query_target"):
        await fetch_odata_bytes(endpoint(), "relative/path", "reader", "secret", limiter=Limiter(), budget=1, resolver=dns)


@pytest.mark.asyncio
async def test_service_executes_query_and_records_only_history_metadata_by_default(monkeypatch):
    service, catalog, _, _, session, clock_now = service_fixture(monkeypatch)
    odata_payload = json.dumps(
        {
            "value": [
                {
                    "Ref_Key": "11111111-2222-3333-4444-555555555555",
                    "Code": "НМ-001",
                    "Description": "Коммутатор ядра 48G",
                    "ЦенаБазовая": "184500.50",
                    "Валюта_Key": "RUB",
                }
            ]
        }
    ).encode("utf-8")
    transport = AsyncMock(return_value=odata_payload)
    monkeypatch.setattr(query_mod, "fetch_odata_bytes", transport)

    plan = OneCQueryPlan(
        entity_set="Catalog_Номенклатура",
        select_fields=("Ref_Key", "Code", "Description", "ЦенаБазовая"),
        filters=(OneCFilterPredicate("Code", "eq", "НМ-001"),),
        top=50,
    )

    # Default cache_ttl_seconds=0: nothing stored in cache, history stores only metadata
    res1 = await service.execute_query(
        tenant_id=11,
        actor_id=7,
        connection_id=5,
        plan=plan,
        catalog=catalog,
    )
    assert session.statements[0]._for_update_arg.read is True
    assert res1.from_cache is False
    assert res1.row_count == 1
    assert res1.rows[0]["ЦенаБазовая"].amount == Decimal("184500.50")
    assert res1.rows[0]["ЦенаБазовая"].currency == "RUB"

    res2 = await service.execute_query(
        tenant_id=11,
        actor_id=7,
        connection_id=5,
        plan=plan,
        catalog=catalog,
    )
    assert res2.from_cache is False
    assert transport.await_count == 2

    # Opt-in request TTL cache
    res3 = await service.execute_query(
        tenant_id=11,
        actor_id=7,
        connection_id=5,
        plan=plan,
        catalog=catalog,
        cache_ttl_seconds=60,
    )
    assert res3.from_cache is False
    assert transport.await_count == 3

    res4 = await service.execute_query(
        tenant_id=11,
        actor_id=7,
        connection_id=5,
        plan=plan,
        catalog=catalog,
        cache_ttl_seconds=60,
    )
    assert res4.from_cache is True
    assert transport.await_count == 3  # Served from cache without hitting transport

    # Advance clock past TTL -> fetches afresh
    clock_now[0] += 61.0
    res5 = await service.execute_query(
        tenant_id=11,
        actor_id=7,
        connection_id=5,
        plan=plan,
        catalog=catalog,
        cache_ttl_seconds=60,
    )
    assert res5.from_cache is False
    assert transport.await_count == 4

    history = service.list_history_metadata(tenant_id=11, actor_id=7)
    assert len(history) == 5
    assert "Коммутатор" not in str(history[0])
    assert service.list_history_metadata(tenant_id=99) == ()


@pytest.mark.asyncio
async def test_non_allowlisted_entity_is_rejected_before_network_or_budget(monkeypatch):
    service, catalog, _, _, _, _ = service_fixture(monkeypatch, allowed_sets=["Catalog_Номенклатура"])
    transport = AsyncMock()
    monkeypatch.setattr(query_mod, "fetch_odata_bytes", transport)

    plan = OneCQueryPlan(
        entity_set="Catalog_Контрагенты",
        select_fields=("Ref_Key", "Description"),
    )
    with pytest.raises(OneCQueryError, match="entity_not_allowed"):
        await service.execute_query(
            tenant_id=11,
            actor_id=7,
            connection_id=5,
            plan=plan,
            catalog=catalog,
        )
    transport.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_enforces_connection_state_budget_policy_and_metadata_fetch(monkeypatch):
    service, catalog, row, _, _, _ = service_fixture(monkeypatch)
    monkeypatch.setattr(query_mod, "fetch_odata_bytes", AsyncMock(return_value=SYNTHETIC_METADATA_XML.encode("utf-8")))

    fetched_catalog = await service.fetch_metadata_catalog(tenant_id=11, actor_id=7, connection_id=5)
    assert fetched_catalog.metadata_sha256 == catalog.metadata_sha256

    # Metadata hash mismatch in fetch_metadata_catalog
    row.config["metadata_sha256"] = "f" * 64
    with pytest.raises(OneCQueryError, match="metadata_hash_mismatch"):
        await service.fetch_metadata_catalog(tenant_id=11, actor_id=7, connection_id=5)
    row.config["metadata_sha256"] = catalog.metadata_sha256

    # Metadata rate_limited and connection_unavailable
    monkeypatch.setattr(query_mod, "fetch_odata_bytes", AsyncMock(side_effect=ProviderDeferred(10)))
    with pytest.raises(OneCQueryError, match="rate_limited"):
        await service.fetch_metadata_catalog(tenant_id=11, actor_id=7, connection_id=5)

    monkeypatch.setattr(query_mod, "fetch_odata_bytes", AsyncMock(side_effect=BudgetConnectionUnavailable()))
    with pytest.raises(ConnectionDenied):
        await service.fetch_metadata_catalog(tenant_id=11, actor_id=7, connection_id=5)

    # Recorded P17-T01 budget policy: BudgetConnectionUnavailable -> ConnectionDenied
    plan = OneCQueryPlan(entity_set="Catalog_Номенклатура", select_fields=("Ref_Key",))
    with pytest.raises(ConnectionDenied):
        await service.execute_query(tenant_id=11, actor_id=7, connection_id=5, plan=plan, catalog=catalog)

    # ProviderDeferred -> rate_limited
    monkeypatch.setattr(query_mod, "fetch_odata_bytes", AsyncMock(side_effect=ProviderDeferred(15)))
    with pytest.raises(OneCQueryError, match="rate_limited"):
        await service.execute_query(tenant_id=11, actor_id=7, connection_id=5, plan=plan, catalog=catalog)

    # Unverified connection is denied
    row.state = "unverified"
    with pytest.raises(ConnectionDenied):
        await service.execute_query(tenant_id=11, actor_id=7, connection_id=5, plan=plan, catalog=catalog)


@pytest.mark.asyncio
async def test_service_rejects_http_service_interface_and_bad_secrets(monkeypatch):
    service, catalog, _, _, _, _ = service_fixture(monkeypatch, interface="http_service")
    plan = OneCQueryPlan(entity_set="Catalog_Номенклатура", select_fields=("Ref_Key",))
    with pytest.raises(OneCQueryError, match="unsupported_interface"):
        await service.execute_query(tenant_id=11, actor_id=7, connection_id=5, plan=plan, catalog=catalog)

    service2, catalog2, _, _, _, _ = service_fixture(monkeypatch)
    monkeypatch.setattr(
        query_mod,
        "open_basic_credentials",
        lambda _: (_ for _ in ()).throw(OneCCredentialError("secret_unavailable")),
    )
    with pytest.raises(OneCQueryError, match="secret_unavailable"):
        await service2.execute_query(tenant_id=11, actor_id=7, connection_id=5, plan=plan, catalog=catalog2)

    with pytest.raises(ConnectionDenied):
        await service2.execute_query(tenant_id=11, actor_id=7, connection_id=0, plan=plan, catalog=catalog2)

    with pytest.raises(OneCQueryError, match="invalid_cache_ttl"):
        await service2.execute_query(
            tenant_id=11, actor_id=7, connection_id=5, plan=plan, catalog=catalog2, cache_ttl_seconds=999
        )


def test_cache_eviction_and_page_token_edge_cases():
    cache = OneCQueryCache(max_entries=2, clock=lambda: 10.0)
    cache.put((11, 7, 5, "k1"), ({"a": 1},), None, ttl_seconds=30)
    cache.put((11, 7, 5, "k2"), ({"a": 2},), None, ttl_seconds=30)
    cache.put((11, 7, 5, "k3"), ({"a": 3},), None, ttl_seconds=30)
    assert cache.get((11, 7, 5, "k1")) is None
    assert cache.get((11, 7, 5, "k2")) is not None
    cache.invalidate_connection(11, 5)
    assert cache.get((11, 7, 5, "k2")) is None

    with pytest.raises(OneCQueryError, match="invalid_page_token"):
        encode_page_token(plan_fingerprint="short", skip=0)
    with pytest.raises(OneCQueryError, match="invalid_offset"):
        encode_page_token(plan_fingerprint="a" * 64, skip=-1)
    with pytest.raises(OneCQueryError, match="invalid_page_token"):
        decode_page_token("not-base64!!!", expected_fingerprint="a" * 64)
