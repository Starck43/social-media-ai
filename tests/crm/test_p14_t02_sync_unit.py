"""P14-T02 unit and contract tests for Bitrix24 read-only incremental synchronization.

Covers:
- Helper functions: `parse_crm_date_field`, `parse_bitrix_timestamp`, `extract_multifield_values`,
  `extract_allowlisted_custom_fields`, `build_entity_title`, `get_field_mapping_config`.
- Incremental sync of `contact`, `company`, `lead`, and `deal` entities into `CrmEntity`
  with Fernet-encrypted `title_encrypted`, `personal_fields_encrypted`, `custom_fields_encrypted`.
- Mapping contacts, companies, and leads to `Contact` + `ContactIdentity` (`bitrix24_contact`,
  `bitrix24_company`, `bitrix24_lead`, `email`, `phone`) without auto-merging duplicates.
-Interrupted sync resume via `cursors` and `watermarks` without creating duplicate `CrmEntity`
  or `Contact` rows, plus rate-limit deferral (`Bitrix24RateLimitError`).
- Deletion reconciliation (`reconcile_deleted_ids`) setting `is_deleted_remote=True` and `deleted_at`.
- Workspace isolation between two Bitrix24 portals in two tenants (`tenant_id` 10 vs 20).
- `handle_crm_sync` background job handler and PII rotation `REGISTRY` entries.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from cryptography.fernet import Fernet

os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
os.environ.setdefault("POSTGRES_URL", "postgresql+asyncpg://user:pass@localhost:5432/test_db")
os.environ.setdefault("CREDENTIALS_KEY", Fernet.generate_key().decode("ascii"))
os.environ.setdefault("PII_ENCRYPTION_KEYS", Fernet.generate_key().decode("ascii"))
os.environ.setdefault("PII_INDEX_KEY", "unit-test-pii-index-key-0123456789abcdef0123456789abcdef")

import pytest

__source_only__ = True

from app.core.config import settings
from app.core.permissions import set_current_user
from app.core.tenant_context import TenantContextError, tenant_scope
from app.integrations.bitrix24.client import Bitrix24Error, Bitrix24RateLimitError
from app.jobs.job_handlers.crm_sync import JOB_NAME, handle_crm_sync
from app.models.audit.action_audit_event import ActionAuditEvent
from app.models.contacts.core import Contact, ContactIdentity
from app.models.crm.entity import CrmEntity
from app.models.integrations.connection import WorkspaceConnection
from app.models.managers.crm_entity_manager import CrmEntityManager
from app.services.crm.sync import (
    BitrixSyncService,
    build_entity_title,
    extract_allowlisted_custom_fields,
    extract_multifield_values,
    get_field_mapping_config,
    parse_bitrix_timestamp,
    parse_crm_date_field,
)
from cli.commands.pii import REGISTRY


@pytest.fixture(autouse=True)
def _pii_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode("ascii"))
    monkeypatch.setattr(settings, "PII_INDEX_KEY", "unit-test-pii-index-key-0123456789abcdef0123456789abcdef")


class _ScalarResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalars(self) -> "_ScalarResult":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class FakeCrmSyncSession:
    def __init__(self) -> None:
        self.connections: dict[int, WorkspaceConnection] = {}
        self.contacts: dict[int, Contact] = {}
        self.identities: dict[int, ContactIdentity] = {}
        self.crm_entities: dict[int, CrmEntity] = {}
        self._next_id = 1

    def add(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = self._next_id
            self._next_id += 1
        if isinstance(obj, WorkspaceConnection):
            self.connections[obj.id] = obj
        elif isinstance(obj, Contact):
            self.contacts[obj.id] = obj
        elif isinstance(obj, ContactIdentity):
            self.identities[obj.id] = obj
        elif isinstance(obj, CrmEntity):
            self.crm_entities[obj.id] = obj

    async def flush(self) -> None:
        return None

    async def execute(self, stmt: Any) -> _ScalarResult:
        sql = str(stmt)
        params = stmt.compile().params
        tables = {getattr(f, "name", "") for f in stmt.get_final_froms()}

        if "workspace_connections" in tables:
            tenant_id = params.get("tenant_id_1")
            conn_id = params.get("id_1")
            rows = [
                c
                for c in self.connections.values()
                if (tenant_id is None or c.tenant_id == tenant_id)
                and (conn_id is None or c.id == conn_id)
                and c.provider_id == "bitrix24"
            ]
            if "is_active" in sql:
                rows = [c for c in rows if c.is_active]
            return _ScalarResult(sorted(rows, key=lambda r: r.id))

        if "contacts" in tables and "contact_identities" not in tables:
            cid = params.get("id_1")
            tid = params.get("tenant_id_1")
            c = self.contacts.get(cid)
            if c and c.tenant_id == tid:
                return _ScalarResult([c])
            return _ScalarResult([])

        if "contact_identities" in tables:
            tid = params.get("tenant_id_1")
            conn_id = params.get("connection_id_1")
            kind = params.get("kind_1")
            bidx = params.get("blind_index_1")
            rows = [
                ident
                for ident in self.identities.values()
                if ident.tenant_id == tid
                and ident.connection_id == conn_id
                and ident.kind == kind
                and ident.blind_index == bidx
            ]
            return _ScalarResult(rows)

        if "crm_entities" in tables:
            if "count(" in sql.lower():
                tid = params.get("tenant_id_1")
                grouped: dict[tuple[int, str], int] = {}
                for ent in self.crm_entities.values():
                    if ent.tenant_id == tid and not ent.is_deleted_remote:
                        key = (ent.connection_id, ent.entity_kind)
                        grouped[key] = grouped.get(key, 0) + 1
                return _ScalarResult([(cid, kind, cnt) for (cid, kind), cnt in grouped.items()])

            tid = params.get("tenant_id_1")
            conn_id = params.get("connection_id_1")
            kind = params.get("entity_kind_1")
            ext_id = params.get("external_id_1")
            rows = [
                ent
                for ent in self.crm_entities.values()
                if (tid is None or ent.tenant_id == tid)
                and (conn_id is None or ent.connection_id == conn_id)
                and (kind is None or ent.entity_kind == kind)
                and (ext_id is None or ent.external_id == ext_id)
            ]
            if "is_deleted_remote" in sql and ext_id is None:
                rows = [ent for ent in rows if not ent.is_deleted_remote]
            return _ScalarResult(rows)

        return _ScalarResult([])


class FakeBitrixClient:
    def __init__(self) -> None:
        self.responses: dict[tuple[int, str, int], dict[str, Any] | Exception] = {}
        self.calls: list[tuple[int, str, dict[str, Any]]] = []

    async def call_method(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        connection_id: int,
        method_name: str,
        params: dict[str, Any] | None = None,
        session=None,
    ) -> dict[str, Any]:
        p = dict(params or {})
        self.calls.append((connection_id, method_name, p))
        start = int(p.get("start") or 0)
        entry = self.responses.get((connection_id, method_name, start))
        if isinstance(entry, Exception):
            raise entry
        if isinstance(entry, dict):
            return entry
        return {"result": []}


def test_p14_t02_date_and_field_helpers() -> None:
    assert parse_crm_date_field("1991-05-14T00:00:00+03:00") == ("1991-05-14", True)
    assert parse_crm_date_field("1900-08-22") == ("--08-22", False)
    assert parse_crm_date_field("--11-04") == ("--11-04", False)
    assert parse_crm_date_field("--13-40") == (None, False)
    assert parse_crm_date_field("") == (None, False)
    assert parse_crm_date_field(None) == (None, False)

    dt = parse_bitrix_timestamp("2026-11-04T12:30:00+03:00")
    assert dt is not None and dt.tzinfo == timezone.utc and dt.hour == 9
    assert parse_bitrix_timestamp("2026-11-04T09:30:00") is not None
    assert parse_bitrix_timestamp("not-a-date") is None
    assert parse_bitrix_timestamp("") is None

    assert extract_multifield_values([{"VALUE": " alice@example.com "}, "bob@example.com", {}]) == [
        "alice@example.com",
        "bob@example.com",
    ]
    assert extract_multifield_values(None) == []

    custom = extract_allowlisted_custom_fields(
        {
            "ID": "10",
            "UF_CRM_ANNIVERSARY": "2020-06-15",
            "UF_CRM_SECRET_UNLISTED": "do-not-include",
            "UF_CRM_VIP": "Y",
            "UF_CRM_EMPTY": "",
        },
        allowlist=["UF_CRM_ANNIVERSARY", "UF_CRM_VIP"],
    )
    assert custom == {"UF_CRM_ANNIVERSARY": "2020-06-15", "UF_CRM_VIP": "Y"}

    assert (
        build_entity_title("contact", {"LAST_NAME": "Иванов", "NAME": "Иван", "SECOND_NAME": "Иванович"}, "5")
        == "Иванов Иван Иванович"
    )
    assert build_entity_title("deal", {"TITLE": "Годовой контракт"}, "9") == "Годовой контракт"
    assert build_entity_title("lead", {}, "12") == "Bitrix24 Lead #12"

    conn = WorkspaceConnection(
        id=1,
        tenant_id=10,
        owner_user_id=101,
        platform="bitrix24",
        provider_id="bitrix24",
        auth_method="oauth2",
        label="Portal",
        account_key="acme.bitrix24.ru",
        config={
            "portal_domain": "acme.bitrix24.ru",
            "field_mapping": {
                "birthday_field": "UF_CRM_BIRTHDAY_CUSTOM",
                "anniversary_field": "UF_CRM_ANNIVERSARY",
                "custom_fields_allowlist": ["UF_CRM_SEGMENT"],
            },
        },
        is_active=True,
        state="verified",
    )
    mapping = get_field_mapping_config(conn)
    assert mapping["birthday_field"] == "UF_CRM_BIRTHDAY_CUSTOM"
    assert "UF_CRM_BIRTHDAY_CUSTOM" in mapping["custom_fields_allowlist"]
    assert "UF_CRM_ANNIVERSARY" in mapping["custom_fields_allowlist"]
    assert "UF_CRM_SEGMENT" in mapping["custom_fields_allowlist"]


@pytest.mark.asyncio
async def test_p14_t02_incremental_sync_and_no_auto_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeCrmSyncSession()
    conn = WorkspaceConnection(
        id=1,
        tenant_id=10,
        owner_user_id=101,
        platform="bitrix24",
        provider_id="bitrix24",
        auth_method="oauth2",
        label="Acme Portal",
        account_key="acme.bitrix24.ru",
        config={
            "portal_domain": "acme.bitrix24.ru",
            "field_mapping": {
                "birthday_field": "BIRTHDATE",
                "anniversary_field": "UF_CRM_ANNIVERSARY",
                "custom_fields_allowlist": ["UF_CRM_VIP_TIER"],
            },
        },
        is_active=True,
        state="verified",
    )
    session.add(conn)

    client = FakeBitrixClient()
    # Two contacts sharing the same email in Bitrix24: must NOT auto-merge into one Contact!
    client.responses[(1, "crm.contact.list", 0)] = {
        "result": [
            {
                "ID": "101",
                "NAME": "Анна",
                "LAST_NAME": "Смирнова",
                "BIRTHDATE": "1992-03-15T00:00:00+03:00",
                "UF_CRM_ANNIVERSARY": "2021-09-01",
                "UF_CRM_VIP_TIER": "Gold",
                "UF_CRM_NOT_ALLOWLISTED": "IgnoreMe",
                "EMAIL": [{"VALUE": "Anna@Example.com"}],
                "PHONE": [{"VALUE": "+7 (999) 111-22-33"}],
                "POST": "Директор",
                "COMMENTS": "Ключевой клиент",
                "COMPANY_ID": "301",
                "ASSIGNED_BY_ID": "7",
                "DATE_MODIFY": "2026-11-04T10:00:00+00:00",
            },
            {
                "ID": "102",
                "NAME": "Анна (дубль)",
                "LAST_NAME": "Смирнова",
                "BIRTHDATE": "--03-15",
                "EMAIL": [{"VALUE": "anna@example.com"}],
                "PHONE": [{"VALUE": "+7 (999) 111-22-33"}],
                "DATE_MODIFY": "2026-11-04T10:05:00+00:00",
            },
        ]
    }
    client.responses[(1, "crm.company.list", 0)] = {
        "result": [
            {
                "ID": "301",
                "TITLE": "ООО Ромашка",
                "EMAIL": [{"VALUE": "info@romashka.ru"}],
                "DATE_MODIFY": "2026-11-04T10:10:00+00:00",
            }
        ]
    }
    client.responses[(1, "crm.lead.list", 0)] = {
        "result": [
            {
                "ID": "401",
                "TITLE": "Входящая заявка с сайта",
                "STATUS_ID": "NEW",
                "EMAIL": [{"VALUE": "lead@example.com"}],
                "DATE_MODIFY": "2026-11-04T10:15:00+00:00",
            }
        ]
    }
    client.responses[(1, "crm.deal.list", 0)] = {
        "result": [
            {
                "ID": "501",
                "TITLE": "Поставка оборудования",
                "CONTACT_ID": "101",
                "COMPANY_ID": "301",
                "STAGE_ID": "C1:WON",
                "CURRENCY_ID": "RUB",
                "OPPORTUNITY": "250000.50",
                "CLOSED": "Y",
                "ASSIGNED_BY_ID": "7",
                "DATE_MODIFY": "2026-11-04T10:20:00+00:00",
            }
        ]
    }

    audit_mock = AsyncMock(return_value=SimpleNamespace(id=1))
    monkeypatch.setattr(ActionAuditEvent.objects, "record_success", audit_mock)

    svc = BitrixSyncService(client=client)  # type: ignore[arg-type]
    actor = SimpleNamespace(id=101, is_active=True, is_superuser=False)
    set_current_user(actor)
    try:
        with tenant_scope(10):
            result = await svc.sync_connection(
                tenant_id=10,
                actor_id=101,
                connection_id=1,
                session=session,
            )
    finally:
        set_current_user(None)

    assert result["deferred"] is False
    assert result["counts"] == {"contact": 2, "company": 1, "lead": 1, "deal": 1}
    assert result["watermarks"]["contact"] == "2026-11-04T10:05:00+00:00"
    assert result["watermarks"]["deal"] == "2026-11-04T10:20:00+00:00"

    # 2 contacts + 1 company + 1 lead = 4 distinct Contact rows (never auto-merged!)
    assert len(session.contacts) == 4
    assert len(session.crm_entities) == 5

    # Deal 501 is linked to Contact for Bitrix contact 101
    deal_ent = next(e for e in session.crm_entities.values() if e.entity_kind == "deal")
    contact_101_ent = next(
        e for e in session.crm_entities.values() if e.entity_kind == "contact" and e.external_id == "101"
    )
    assert deal_ent.contact_id == contact_101_ent.contact_id
    assert deal_ent.stage_code == "C1:WON"
    assert deal_ent.opportunity_amount == 250000.50
    assert deal_ent.is_closed is True
    assert deal_ent.entity_type == "deal"
    assert deal_ent.native_id == "501"
    assert deal_ent.date_modify is not None

    # Personal fields & allowlisted custom fields are accessible via decrypted properties
    assert contact_101_ent.personal_fields["birthday"] == "1992-03-15"
    assert contact_101_ent.personal_fields["anniversary"] == "2021-09-01"
    assert contact_101_ent.custom_fields == {
        "UF_CRM_ANNIVERSARY": "2021-09-01",
        "UF_CRM_VIP_TIER": "Gold",
    }


@pytest.mark.asyncio
async def test_p14_t02_interrupted_sync_resumes_from_cursor_and_reconciles_deletions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeCrmSyncSession()
    conn = WorkspaceConnection(
        id=2,
        tenant_id=10,
        owner_user_id=101,
        platform="bitrix24",
        provider_id="bitrix24",
        auth_method="oauth2",
        label="Portal 2",
        account_key="p2.bitrix24.ru",
        config={"portal_domain": "p2.bitrix24.ru"},
        is_active=True,
        state="verified",
    )
    session.add(conn)

    client = FakeBitrixClient()
    # Page 1 returns next=50; Page 2 raises rate limit!
    client.responses[(2, "crm.contact.list", 0)] = {
        "result": [
            {"ID": "10", "NAME": "Петр", "LAST_NAME": "Петров", "DATE_MODIFY": "2026-11-04T11:00:00+00:00"}
        ],
        "next": 50,
    }
    client.responses[(2, "crm.contact.list", 50)] = Bitrix24RateLimitError()

    monkeypatch.setattr(ActionAuditEvent.objects, "record_success", AsyncMock(return_value=SimpleNamespace(id=1)))
    svc = BitrixSyncService(client=client)  # type: ignore[arg-type]

    actor = SimpleNamespace(id=101, is_active=True, is_superuser=False)
    set_current_user(actor)
    try:
        with tenant_scope(10):
            first_run = await svc.sync_connection(
                tenant_id=10,
                actor_id=101,
                connection_id=2,
                entity_kinds=("contact",),
                session=session,
            )
            assert first_run["deferred"] is True
            assert first_run["deferral_reason"] == "rate_limit_deferred"
            assert first_run["cursors"] == {"contact": 50}
            assert "contact" not in first_run["watermarks"]

            # Now page 2 succeeds on resume from start=50
            client.responses[(2, "crm.contact.list", 50)] = {
                "result": [
                    {"ID": "11", "NAME": "Мария", "LAST_NAME": "Сидорова", "DATE_MODIFY": "2026-11-04T11:05:00+00:00"}
                ]
            }
            second_run = await svc.sync_connection(
                tenant_id=10,
                actor_id=101,
                connection_id=2,
                entity_kinds=("contact",),
                session=session,
            )
            assert second_run["deferred"] is False
            assert second_run["cursors"] == {}
            assert second_run["watermarks"]["contact"] == "2026-11-04T11:05:00+00:00"
            assert len(session.crm_entities) == 2

            # Deletion reconciliation marks ID 10 deleted while keeping ID 11 active
            marked = await svc.reconcile_deleted_ids(
                tenant_id=10,
                connection_id=2,
                entity_kind="contact",
                active_external_ids={"11"},
                session=session,
            )
            assert marked == 1
            ent_10 = next(e for e in session.crm_entities.values() if e.external_id == "10")
            assert ent_10.is_deleted_remote is True
            assert ent_10.deleted_at is not None

            status = await svc.get_sync_status(tenant_id=10, actor_id=101, connection_id=2, session=session)
            assert len(status["items"]) == 1
            assert status["items"][0]["entity_totals"]["contact"] == 1
    finally:
        set_current_user(None)


@pytest.mark.asyncio
async def test_p14_t02_workspace_isolation_and_job_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeCrmSyncSession()
    conn_t10 = WorkspaceConnection(
        id=10,
        tenant_id=10,
        owner_user_id=101,
        platform="bitrix24",
        provider_id="bitrix24",
        auth_method="oauth2",
        label="T10 Portal",
        account_key="t10.bitrix24.ru",
        config={"portal_domain": "t10.bitrix24.ru"},
        is_active=True,
        state="verified",
    )
    conn_t20 = WorkspaceConnection(
        id=20,
        tenant_id=20,
        owner_user_id=202,
        platform="bitrix24",
        provider_id="bitrix24",
        auth_method="oauth2",
        label="T20 Portal",
        account_key="t20.bitrix24.ru",
        config={"portal_domain": "t20.bitrix24.ru"},
        is_active=True,
        state="verified",
    )
    session.add(conn_t10)
    session.add(conn_t20)

    client = FakeBitrixClient()
    client.responses[(10, "crm.contact.list", 0)] = {
        "result": [{"ID": "1", "NAME": "TenantTen", "DATE_MODIFY": "2026-11-04T12:00:00+00:00"}]
    }
    monkeypatch.setattr(ActionAuditEvent.objects, "record_success", AsyncMock(return_value=SimpleNamespace(id=1)))
    svc = BitrixSyncService(client=client)  # type: ignore[arg-type]

    # Tenant 10 cannot sync Tenant 20's connection
    with tenant_scope(10):
        with pytest.raises(Bitrix24Error) as exc_info:
            await svc.sync_connection(tenant_id=10, actor_id=101, connection_id=20, session=session)
        assert exc_info.value.code == "connection_not_found"

        # Job handler runs only for Tenant 10's connections
        job_res = await handle_crm_sync(
            {"entity_kinds": ["contact"]},
            sync_service=svc,
            session=session,
        )
        assert job_res["job"] == JOB_NAME
        assert job_res["connections_synced"] == 1
        assert all(e.tenant_id == 10 for e in session.crm_entities.values())

    # Without tenant context, CrmEntityManager and handle_crm_sync refuse access
    with pytest.raises(TenantContextError):
        CrmEntityManager().get_queryset(session=session)
    with pytest.raises(TenantContextError):
        await handle_crm_sync({}, sync_service=svc, session=session)


def test_p14_t02_pii_registry_includes_crm_entities() -> None:
    registered = {(t, c) for t, c, _ in REGISTRY}
    assert ("crm_entities", "title_encrypted") in registered
    assert ("crm_entities", "personal_fields_encrypted") in registered
    assert ("crm_entities", "custom_fields_encrypted") in registered
