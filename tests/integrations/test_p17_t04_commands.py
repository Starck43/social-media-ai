"""Unit and service tests for 1C write commands with preview, approval, DataVersion check, and audit (P17-T04)."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import h11
import pytest

from app.core.outbound import OutboundTarget
from app.integrations.onec.connections import METHOD as READ_ONLY_METHOD
from app.integrations.onec.connections import PROVIDER, SCHEMA_VERSION
from app.integrations.onec.credentials import seal_basic_credentials
from app.integrations.onec.endpoint import OneCEndpoint
from app.models.actions.business_command import BusinessCommand, BusinessCommandTarget
from app.models.audit.action_audit_event import ActionAuditEvent
from app.models.integrations.connection import WorkspaceConnection, WorkspaceConnectionSecret
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.commands import ledger as ledger_mod
from app.services.commands.ledger import BusinessCommandLedger
from app.services.onec import commands as commands_mod
from app.services.onec.commands import (
    CAPABILITY_CATALOG_UPDATE,
    CAPABILITY_DOCUMENT_CREATE,
    CAPABILITY_DOCUMENT_POST,
    ONEC_CAPABILITY_SPECS,
    ONEC_WRITE_CAPABILITIES,
    WRITE_METHOD,
    OneCCommandAuditEntry,
    OneCCommandError,
    OneCCommandRecord,
    OneCCommandService,
    OneCFieldDiff,
    OneCWritePreviewRequest,
    attach_external_ref_to_comment,
    build_external_idempotency_ref,
    compute_catalog_diff,
    extract_data_version,
    get_onec_write_capability,
    is_onec_write_capability_enabled,
    normalize_guid,
    parse_odata_object_list,
    parse_odata_single_object,
    resolve_idempotency_comment_field,
    send_odata_command_request,
    validate_and_normalize_write_fields,
    validate_idempotency_key,
    verify_data_version_unchanged,
)
from app.services.onec.metadata import parse_odata_metadata_xml

SAMPLE_METADATA_XML = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx Version="1.0" xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx">
  <edmx:DataServices m:DataServiceVersion="3.0" xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">
    <Schema Namespace="StandardODATA" xmlns="http://schemas.microsoft.com/ado/2009/11/edm">
      <EntityType Name="Document_РеализацияТоваровУслуг">
        <Key><PropertyRef Name="Ref_Key"/></Key>
        <Property Name="Ref_Key" Type="Edm.Guid" Nullable="false"/>
        <Property Name="DataVersion" Type="Edm.String"/>
        <Property Name="DeletionMark" Type="Edm.Boolean" Nullable="false"/>
        <Property Name="Posted" Type="Edm.Boolean" Nullable="false"/>
        <Property Name="Number" Type="Edm.String"/>
        <Property Name="Date" Type="Edm.DateTime" Nullable="false"/>
        <Property Name="Контрагент_Key" Type="Edm.Guid"/>
        <Property Name="СуммаДокумента" Type="Edm.Decimal" Precision="15" Scale="2"/>
        <Property Name="Комментарий" Type="Edm.String" Nullable="true"/>
      </EntityType>
      <EntityType Name="Catalog_Контрагенты">
        <Key><PropertyRef Name="Ref_Key"/></Key>
        <Property Name="Ref_Key" Type="Edm.Guid" Nullable="false"/>
        <Property Name="DataVersion" Type="Edm.String"/>
        <Property Name="DeletionMark" Type="Edm.Boolean" Nullable="false"/>
        <Property Name="Code" Type="Edm.String"/>
        <Property Name="Description" Type="Edm.String" Nullable="false"/>
        <Property Name="ИНН" Type="Edm.String"/>
        <Property Name="КредитныйЛимитСумма" Type="Edm.Decimal" Precision="15" Scale="2"/>
        <Property Name="Активен" Type="Edm.Boolean"/>
        <Property Name="Рейтинг" Type="Edm.Int32"/>
        <Property Name="ДатаРегистрации" Type="Edm.DateTime"/>
        <Property Name="ОсновнойДоговор_Key" Type="Edm.Guid" Nullable="true"/>
        <Property Name="Комментарий" Type="Edm.String" Nullable="true"/>
      </EntityType>
      <EntityType Name="Document_БезКомментария">
        <Key><PropertyRef Name="Ref_Key"/></Key>
        <Property Name="Ref_Key" Type="Edm.Guid" Nullable="false"/>
        <Property Name="DataVersion" Type="Edm.String"/>
        <Property Name="СуммаДокумента" Type="Edm.Decimal" Precision="15" Scale="2"/>
      </EntityType>
      <EntityContainer Name="StandardODATA" m:IsDefaultEntityContainer="true">
        <EntitySet Name="Document_РеализацияТоваровУслуг" EntityType="StandardODATA.Document_РеализацияТоваровУслуг"/>
        <EntitySet Name="Catalog_Контрагенты" EntityType="StandardODATA.Catalog_Контрагенты"/>
        <EntitySet Name="Document_БезКомментария" EntityType="StandardODATA.Document_БезКомментария"/>
      </EntityContainer>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
"""

CATALOG = parse_odata_metadata_xml(SAMPLE_METADATA_XML)
DOC_GUID = "11111111-2222-3333-4444-555555555555"
CAT_GUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
COUNTERPARTY_GUID = "99999999-8888-7777-6666-555555555555"


class FakeScalars:
    def __init__(self, rows: list[Any]):
        self._rows = list(rows)

    def all(self) -> list[Any]:
        return list(self._rows)


class FakeExecuteResult:
    def __init__(self, rows: list[Any]):
        self._rows = list(rows)

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalars(self) -> FakeScalars:
        return FakeScalars(self._rows)


class FakeOneCCommandSession:
    """In-memory SQLAlchemy session stub supporting `WorkspaceConnection`, `BusinessCommand`, and `ActionAuditEvent`."""

    def __init__(
        self,
        *,
        connection: WorkspaceConnection,
        secret: WorkspaceConnectionSecret,
    ):
        self.connections: dict[int, WorkspaceConnection] = {connection.id: connection}
        self.secrets: dict[int, WorkspaceConnectionSecret] = {secret.connection_id: secret}
        self.commands: dict[int, BusinessCommand] = {}
        self.targets: dict[int, BusinessCommandTarget] = {}
        self.audit_events: list[ActionAuditEvent] = []
        self._next_cmd_id = 1
        self._next_target_id = 1

    def add(self, obj: Any) -> None:
        if isinstance(obj, BusinessCommand):
            if getattr(obj, "id", None) is None:
                obj.id = self._next_cmd_id
                self._next_cmd_id += 1
            self.commands[obj.id] = obj
        elif isinstance(obj, BusinessCommandTarget):
            if getattr(obj, "id", None) is None:
                obj.id = self._next_target_id
                self._next_target_id += 1
            self.targets[obj.id] = obj
        elif isinstance(obj, ActionAuditEvent):
            self.audit_events.append(obj)

    async def flush(self) -> None:
        return None

    async def get(self, model: Any, pk: int) -> Any:
        if model is WorkspaceConnectionSecret:
            return self.secrets.get(pk)
        if model is WorkspaceConnection:
            return self.connections.get(pk)
        if model is BusinessCommand:
            return self.commands.get(pk)
        return None

    async def execute(self, stmt: Any) -> FakeExecuteResult:
        sql = str(stmt)
        params = stmt.compile().params

        if "workspace_connections" in sql:
            rows = []
            for conn in self.connections.values():
                if "id_1" in params and conn.id != params["id_1"]:
                    continue
                if "tenant_id_1" in params and conn.tenant_id != params["tenant_id_1"]:
                    continue
                if "owner_user_id_1" in params and conn.owner_user_id != params["owner_user_id_1"]:
                    continue
                rows.append(conn)
            return FakeExecuteResult(rows)

        if "business_commands" in sql and "business_command_targets" not in sql:
            rows = list(self.commands.values())
            if "tenant_id_1" in params:
                rows = [r for r in rows if r.tenant_id == params["tenant_id_1"]]
            if "actor_user_id_1" in params:
                rows = [r for r in rows if r.actor_user_id == params["actor_user_id_1"]]
            if "capability_1" in params:
                cap_param = params["capability_1"]
                if isinstance(cap_param, (list, tuple, set, frozenset)):
                    rows = [r for r in rows if r.capability in cap_param]
                else:
                    rows = [r for r in rows if r.capability == cap_param]
            if "idempotency_key_1" in params:
                rows = [r for r in rows if r.idempotency_key == params["idempotency_key_1"]]
            if "id_1" in params and "idempotency_key_1" not in params:
                rows = [r for r in rows if r.id == params["id_1"]]
            rows.sort(key=lambda r: r.id, reverse=("DESC" in sql.upper()))
            return FakeExecuteResult(rows)

        if "business_command_targets" in sql:
            rows = list(self.targets.values())
            if "tenant_id_1" in params:
                rows = [r for r in rows if r.tenant_id == params["tenant_id_1"]]
            if "command_id_1" in params:
                cmd_param = params["command_id_1"]
                if isinstance(cmd_param, (list, tuple, set, frozenset)):
                    rows = [r for r in rows if r.command_id in cmd_param]
                else:
                    rows = [r for r in rows if r.command_id == cmd_param]
            rows.sort(key=lambda r: r.id)
            return FakeExecuteResult(rows)

        if "action_audit_events" in sql:
            rows = list(self.audit_events)
            if "tenant_id_1" in params:
                rows = [r for r in rows if r.tenant_id == params["tenant_id_1"]]
            rows.sort(key=lambda r: getattr(r, "id", 0), reverse=True)
            return FakeExecuteResult(rows)

        return FakeExecuteResult([])


def _make_write_connection(
    *,
    conn_id: int = 7,
    tenant_id: int = 10,
    actor_id: int = 101,
    auth_method: str = WRITE_METHOD,
    interface: str = "odata_standard",
    state: str = "verified",
    is_active: bool = True,
    extra_config: dict[str, Any] | None = None,
) -> tuple[WorkspaceConnection, WorkspaceConnectionSecret]:
    cfg: dict[str, Any] = {
        "host": "1c.company.ru",
        "port": 443,
        "base_path": "/erp",
        "interface": interface,
        "metadata_sha256": CATALOG.metadata_sha256,
        "allowed_entity_sets": [
            "Document_РеализацияТоваровУслуг",
            "Catalog_Контрагенты",
            "Document_БезКомментария",
        ],
    }
    if extra_config:
        cfg.update(extra_config)
    conn = WorkspaceConnection(
        id=conn_id,
        tenant_id=tenant_id,
        owner_user_id=actor_id,
        platform="erp",
        provider_id=PROVIDER,
        auth_method=auth_method,
        schema_version=SCHEMA_VERSION,
        label="1C ERP Write",
        account_key="onec-write-acc-1",
        config=cfg,
        state=state,
        is_active=is_active,
    )
    secret = WorkspaceConnectionSecret(
        connection_id=conn_id,
        secret_encrypted=seal_basic_credentials("writer_bot", "secret-pass-123"),
    )
    return conn, secret


@pytest.fixture()
def write_env(monkeypatch: pytest.MonkeyPatch):
    """Configure runtime capabilities, kill switches, RBAC stubs, and audit event capture."""
    from app.core.config import settings

    if not getattr(settings, "CREDENTIALS_KEY", None):
        monkeypatch.setattr(settings, "CREDENTIALS_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
    monkeypatch.setattr(
        ledger_mod,
        "RUNTIME_ENABLED_CAPABILITIES",
        set(ONEC_WRITE_CAPABILITIES),
    )
    monkeypatch.setattr(ledger_mod, "RUNTIME_KILL_SWITCHES", {})

    async def _noop_authorize(_session, _tenant_id, _actor_id, **_kwargs):
        return None

    async def _noop_member_grant(_session, _tenant_id, _user_id, _spec):
        return None

    async def _noop_verify_conn(_session, _tenant_id, _conn_id):
        return None

    async def _noop_require_approved(_self, **_kwargs):
        return None

    monkeypatch.setattr(commands_mod, "_authorize", _noop_authorize)
    monkeypatch.setattr(commands_mod.WorkspaceOutboundHostManager, "require_approved", _noop_require_approved)
    monkeypatch.setattr(commands_mod, "budget_for", lambda *_a, **_kw: SimpleNamespace(key="onec:test"))
    monkeypatch.setattr(ledger_mod, "_verify_member_grant", _noop_member_grant)
    monkeypatch.setattr(ledger_mod, "_verify_connection", _noop_verify_conn)

    recorded_events: list[ActionAuditEvent] = []
    next_evt_idx = 1

    async def _record_success(
        *,
        actor_kind: str,
        action: str,
        resource_kind: str,
        actor_id: int | None = None,
        resource_id: Any = None,
        reason_code: str | None = None,
        session: Any = None,
        **_kwargs: Any,
    ):
        nonlocal next_evt_idx
        evt = ActionAuditEvent(
            id=next_evt_idx,
            event_id=f"evt-ok-{next_evt_idx}",
            tenant_id=10,
            actor_kind=actor_kind,
            actor_id=str(actor_id) if actor_id is not None else None,
            action=action,
            resource_kind=resource_kind,
            resource_id=str(resource_id) if resource_id is not None else None,
            outcome="success",
            reason_code=reason_code,
            occurred_at=datetime.now(timezone.utc),
        )
        next_evt_idx += 1
        recorded_events.append(evt)
        if session is not None and hasattr(session, "add"):
            session.add(evt)
        return evt

    async def _record_unknown(
        *,
        actor_kind: str,
        action: str,
        resource_kind: str,
        actor_id: int | None = None,
        resource_id: Any = None,
        reason_code: str | None = "operation_unconfirmed",
        **_kwargs: Any,
    ):
        nonlocal next_evt_idx
        evt = ActionAuditEvent(
            id=next_evt_idx,
            event_id=f"evt-unk-{next_evt_idx}",
            tenant_id=10,
            actor_kind=actor_kind,
            actor_id=str(actor_id) if actor_id is not None else None,
            action=action,
            resource_kind=resource_kind,
            resource_id=str(resource_id) if resource_id is not None else None,
            outcome="unknown",
            reason_code=reason_code,
            occurred_at=datetime.now(timezone.utc),
        )
        next_evt_idx += 1
        recorded_events.append(evt)
        return evt

    monkeypatch.setattr(
        ActionAuditEvent,
        "objects",
        SimpleNamespace(record_success=_record_success, record_unknown=_record_unknown),
    )
    return SimpleNamespace(events=recorded_events)


def test_capabilities_off_by_default_and_kill_switch_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    for cap_code in ONEC_WRITE_CAPABILITIES:
        spec = get_onec_write_capability(cap_code)
        assert spec.enabled_by_default is False
        assert spec.approval_policy == "per_item"
        assert spec.audit_approve == "onec.command_approve"
        assert spec.audit_execute == "onec.command_execute"

    with pytest.raises(OneCCommandError) as exc_info:
        get_onec_write_capability("onec.unknown.v1")
    assert exc_info.value.code == "unsupported_capability"

    # By default (empty RUNTIME_ENABLED_CAPABILITIES), all three capabilities are disabled
    monkeypatch.setattr(ledger_mod, "RUNTIME_ENABLED_CAPABILITIES", set())
    monkeypatch.setattr(ledger_mod, "RUNTIME_KILL_SWITCHES", {})
    for cap_code in ONEC_WRITE_CAPABILITIES:
        enabled, reason = is_onec_write_capability_enabled(cap_code)
        assert enabled is False
        assert reason == "capability_disabled"

    # Enable globally, then check per-connection enabled_write_capabilities filter
    monkeypatch.setattr(ledger_mod, "RUNTIME_ENABLED_CAPABILITIES", set(ONEC_WRITE_CAPABILITIES))
    enabled_ok, reason_ok = is_onec_write_capability_enabled(
        CAPABILITY_DOCUMENT_CREATE,
        connection_config={"enabled_write_capabilities": [CAPABILITY_DOCUMENT_CREATE]},
    )
    assert enabled_ok is True
    assert reason_ok is None

    enabled_blocked, reason_blocked = is_onec_write_capability_enabled(
        CAPABILITY_DOCUMENT_POST,
        connection_config={"enabled_write_capabilities": [CAPABILITY_DOCUMENT_CREATE]},
    )
    assert enabled_blocked is False
    assert reason_blocked == "capability_disabled"

    # Activate kill switch -> immediately blocked with kill_switch_active
    monkeypatch.setattr(ledger_mod, "RUNTIME_KILL_SWITCHES", {"KILL_ONEC_DOCUMENT_CREATE": True})
    enabled_ks, reason_ks = is_onec_write_capability_enabled(CAPABILITY_DOCUMENT_CREATE)
    assert enabled_ks is False
    assert reason_ks == "kill_switch_active"


def test_pure_field_validation_diff_and_data_version_helpers() -> None:
    doc_spec = CATALOG.get_entity_set("Document_РеализацияТоваровУслуг")
    cat_spec = CATALOG.get_entity_set("Catalog_Контрагенты")

    # External idempotency ref is deterministic and bounded
    ref1 = build_external_idempotency_ref(
        tenant_id=10,
        connection_id=7,
        capability=CAPABILITY_DOCUMENT_CREATE,
        idempotency_key="invoice-2026-001",
    )
    ref2 = build_external_idempotency_ref(
        tenant_id=10,
        connection_id=7,
        capability=CAPABILITY_DOCUMENT_CREATE,
        idempotency_key="invoice-2026-001",
    )
    assert ref1 == ref2
    assert ref1.startswith("SM1C:")

    with pytest.raises(OneCCommandError) as exc_idem:
        validate_idempotency_key("   ")
    assert exc_idem.value.code == "invalid_idempotency_key"

    with pytest.raises(OneCCommandError) as exc_ctrl:
        validate_idempotency_key("bad\x00key")
    assert exc_ctrl.value.code == "invalid_idempotency_key"

    # Attach external ref idempotently
    tagged = attach_external_ref_to_comment("Оплата по счету №15", ref1)
    assert tagged == f"Оплата по счету №15 [{ref1}]"
    assert attach_external_ref_to_comment(tagged, ref1) == tagged
    long_comment = "А" * 995
    assert len(attach_external_ref_to_comment(long_comment, ref1)) <= 1000

    # Float values are strictly rejected (P12-T05 deterministic Decimal rule)
    with pytest.raises(OneCCommandError) as exc_float:
        validate_and_normalize_write_fields(doc_spec, {"СуммаДокумента": 1250.50})
    assert exc_float.value.code == "float_forbidden"

    # System readonly fields are rejected
    with pytest.raises(OneCCommandError) as exc_ro:
        validate_and_normalize_write_fields(doc_spec, {"Posted": True})
    assert exc_ro.value.code == "readonly_field_forbidden"

    with pytest.raises(OneCCommandError) as exc_key:
        validate_and_normalize_write_fields(doc_spec, {"Ref_Key": DOC_GUID})
    assert exc_key.value.code == "readonly_field_forbidden"

    # Valid catalog fields normalize Decimal, boolean, int, date, GUID, nullable
    norm, fmt = validate_and_normalize_write_fields(
        cat_spec,
        {
            "Description": "ООО Ромашка Плюс",
            "КредитныйЛимитСумма": Decimal("250000.5"),
            "Активен": True,
            "Рейтинг": 5,
            "ДатаРегистрации": date(2026, 5, 10),
            "ОсновнойДоговор_Key": None,
            "Комментарий": "Проверенный партнер",
        },
    )
    assert norm["КредитныйЛимитСумма"] == "250000.50"
    assert "250 000,50" in fmt["КредитныйЛимитСумма"]
    assert norm["Активен"] is True
    assert fmt["Активен"] == "Да"
    assert norm["Рейтинг"] == 5
    assert norm["ДатаРегистрации"] == "2026-05-10T00:00:00"
    assert norm["ОсновнойДоговор_Key"] is None
    assert fmt["ОсновнойДоговор_Key"] == "—"

    # Compute catalog diff
    current_obj = {
        "Ref_Key": CAT_GUID,
        "DataVersion": "AAAAAA1=",
        "Description": "ООО Ромашка",
        "КредитныйЛимитСумма": Decimal("100000.00"),
        "Активен": True,
        "Рейтинг": 5,
        "ДатаРегистрации": "2026-05-10T00:00:00",
        "ОсновнойДоговор_Key": None,
        "Комментарий": "Проверенный партнер",
    }
    diffs = compute_catalog_diff(cat_spec, current_obj, norm, fmt)
    assert len(diffs) == 2
    diff_by_field = {d.field: d for d in diffs}
    assert diff_by_field["Description"].old_value == "ООО Ромашка"
    assert diff_by_field["Description"].new_value == "ООО Ромашка Плюс"
    assert diff_by_field["КредитныйЛимитСумма"].old_value == "100000.00"
    assert diff_by_field["КредитныйЛимитСумма"].new_value == "250000.50"

    # No effective changes raises no_effective_changes
    with pytest.raises(OneCCommandError) as exc_noop:
        compute_catalog_diff(
            cat_spec,
            current_obj,
            {"Description": "ООО Ромашка"},
            {"Description": "ООО Ромашка"},
        )
    assert exc_noop.value.code == "no_effective_changes"

    # DataVersion checks
    assert extract_data_version(current_obj) == "AAAAAA1="
    assert verify_data_version_unchanged(expected_data_version="AAAAAA1=", remote_obj=current_obj) == "AAAAAA1="
    with pytest.raises(OneCCommandError) as exc_dv:
        verify_data_version_unchanged(expected_data_version="AAAAAA1=", remote_obj={"DataVersion": "BBBBBB2="})
    assert exc_dv.value.code == "data_version_conflict"


@pytest.mark.asyncio
async def test_readonly_connection_cannot_execute_or_preview_write_commands(write_env) -> None:
    """Acceptance check: read-only connection (`odata_credentials`) cannot execute write commands."""
    ro_conn, ro_secret = _make_write_connection(auth_method=READ_ONLY_METHOD)
    session = FakeOneCCommandSession(connection=ro_conn, secret=ro_secret)
    service = OneCCommandService(ledger=BusinessCommandLedger())

    with pytest.raises(OneCCommandError) as exc_info:
        await service.preview_command(
            tenant_id=10,
            actor_id=101,
            connection_id=ro_conn.id,
            request=OneCWritePreviewRequest(
                capability=CAPABILITY_DOCUMENT_CREATE,
                idempotency_key="ro-attempt-1",
                entity_set="Document_РеализацияТоваровУслуг",
                fields={"СуммаДокумента": Decimal("1500.00")},
            ),
            catalog=CATALOG,
            session=session,
        )
    assert exc_info.value.code == "write_connection_required"


@pytest.mark.asyncio
async def test_document_create_preview_approve_execute_and_audit(write_env) -> None:
    """Full lifecycle for `onec.document.create.v1`: preview -> per-item approve -> execute -> audit log."""
    conn, secret = _make_write_connection()
    session = FakeOneCCommandSession(connection=conn, secret=secret)
    transport_calls: list[tuple[bytes, str, bytes | None]] = []

    async def fake_transport(endpoint, *, method: bytes, target_path: str, json_body: bytes | None = None, **_kw):
        transport_calls.append((method, target_path, json_body))
        if method == b"GET" and "$filter=" in target_path:
            return json.dumps({"value": []}).encode("utf-8")
        if method == b"POST":
            return json.dumps(
                {
                    "Ref_Key": DOC_GUID,
                    "DataVersion": "DOCVER1=",
                    "Number": "РТУ-000042",
                }
            ).encode("utf-8")
        raise AssertionError(f"Unexpected transport call: {method} {target_path}")

    service = OneCCommandService(ledger=BusinessCommandLedger(), transport=fake_transport)

    preview = await service.preview_command(
        tenant_id=10,
        actor_id=101,
        connection_id=conn.id,
        request=OneCWritePreviewRequest(
            capability=CAPABILITY_DOCUMENT_CREATE,
            idempotency_key="create-inv-001",
            entity_set="Document_РеализацияТоваровУслуг",
            fields={
                "Date": datetime(2026, 11, 12, 10, 30, tzinfo=timezone.utc),
                "Контрагент_Key": COUNTERPARTY_GUID,
                "СуммаДокумента": Decimal("48500.00"),
                "Комментарий": "Заказ клиента №99",
            },
        ),
        catalog=CATALOG,
        session=session,
    )
    assert preview.state == "previewed"
    assert preview.approval_policy == "per_item"
    assert preview.expected_data_version is None
    assert preview.external_ref.startswith("SM1C:")
    assert preview.external_ref in preview.preview_snapshot["formatted_fields"]["Комментарий"]

    # Approving with wrong payload hash is rejected
    with pytest.raises(OneCCommandError) as exc_hash:
        await service.approve_command(
            tenant_id=10,
            actor_id=101,
            command_id=preview.id,
            expected_payload_hash="sha256:" + "0" * 64,
            ttl_seconds=600,
            session=session,
        )
    assert exc_hash.value.code == "approval_hash_mismatch"

    # Valid per-item approval succeeds
    approved = await service.approve_command(
        tenant_id=10,
        actor_id=101,
        command_id=preview.id,
        expected_payload_hash=preview.payload_hash,
        ttl_seconds=600,
        session=session,
    )
    assert approved.state == "approved"
    assert approved.approved_payload_hash == preview.payload_hash

    # Execute checks 1C for external_ref first, then sends POST
    executed = await service.execute_command(
        tenant_id=10,
        actor_id=101,
        command_id=preview.id,
        session=session,
    )
    assert executed.state == "succeeded"
    assert executed.provider_receipt == f"onec:document:Document_РеализацияТоваровУслуг:{DOC_GUID}"
    assert len(transport_calls) == 2
    assert transport_calls[0][0] == b"GET"
    assert transport_calls[1][0] == b"POST"
    sent_json = json.loads(transport_calls[1][2].decode("utf-8"))
    assert preview.external_ref in sent_json["Комментарий"]

    # Audit log contains create, approve, and execute events
    audit_entries = await service.list_audit_log(tenant_id=10, actor_id=101, session=session)
    actions = [e.action for e in audit_entries]
    assert "onec.command_approve" in actions
    assert "onec.command_execute" in actions


@pytest.mark.asyncio
async def test_document_post_and_catalog_update_data_version_mismatch_refused(write_env) -> None:
    """Acceptance check: if remote `DataVersion` changed on 1C before execute, command is refused without mutating."""
    conn, secret = _make_write_connection()
    session = FakeOneCCommandSession(connection=conn, secret=secret)

    remote_version = "VER_INITIAL_1="
    mutation_sent = False

    async def fake_transport(endpoint, *, method: bytes, target_path: str, **_kw):
        nonlocal mutation_sent
        if method == b"GET":
            return json.dumps(
                {
                    "Ref_Key": CAT_GUID,
                    "DataVersion": remote_version,
                    "DeletionMark": False,
                    "Description": "ООО Старое Имя",
                    "КредитныйЛимитСумма": "100000.00",
                }
            ).encode("utf-8")
        mutation_sent = True
        return b"{}"

    service = OneCCommandService(ledger=BusinessCommandLedger(), transport=fake_transport)

    preview = await service.preview_command(
        tenant_id=10,
        actor_id=101,
        connection_id=conn.id,
        request=OneCWritePreviewRequest(
            capability=CAPABILITY_CATALOG_UPDATE,
            idempotency_key="cat-upd-conflict",
            entity_set="Catalog_Контрагенты",
            target_guid=CAT_GUID,
            fields={"Description": "ООО Новое Имя"},
        ),
        catalog=CATALOG,
        session=session,
    )
    assert preview.expected_data_version == "VER_INITIAL_1="
    assert preview.preview_snapshot["diffs"][0]["old_value"] == "ООО Старое Имя"
    assert preview.preview_snapshot["diffs"][0]["new_value"] == "ООО Новое Имя"

    await service.approve_command(
        tenant_id=10,
        actor_id=101,
        command_id=preview.id,
        expected_payload_hash=preview.payload_hash,
        session=session,
    )

    # Simulate concurrent modification in 1C before execute
    remote_version = "VER_CHANGED_BY_OTHER_USER_2="

    with pytest.raises(OneCCommandError) as exc_conflict:
        await service.execute_command(
            tenant_id=10,
            actor_id=101,
            command_id=preview.id,
            session=session,
        )
    assert exc_conflict.value.code == "data_version_conflict"
    assert mutation_sent is False
    assert session.commands[preview.id].state == "failed"
    assert session.commands[preview.id].error_code == "data_version_conflict"


@pytest.mark.asyncio
async def test_document_post_success_and_catalog_update_success(write_env) -> None:
    """Execute `onec.document.post.v1` and `onec.catalog.update.v1` when `DataVersion` matches."""
    conn, secret = _make_write_connection()
    session = FakeOneCCommandSession(connection=conn, secret=secret)
    calls: list[tuple[bytes, str, str | None]] = []

    async def fake_transport(endpoint, *, method: bytes, target_path: str, if_match: str | None = None, **_kw):
        from urllib.parse import unquote

        decoded_path = unquote(target_path)
        calls.append((method, decoded_path, if_match))
        if "Document_РеализацияТоваровУслуг" in decoded_path and method == b"GET":
            return json.dumps(
                {
                    "Ref_Key": DOC_GUID,
                    "DataVersion": "DOC_V1=",
                    "DeletionMark": False,
                    "Posted": False,
                    "Number": "РТУ-105",
                    "Date": "2026-11-12T09:00:00",
                }
            ).encode("utf-8")
        if "Document_РеализацияТоваровУслуг" in decoded_path and method == b"POST":
            return b""
        if "Catalog_Контрагенты" in decoded_path and method == b"GET":
            return json.dumps(
                {
                    "Ref_Key": CAT_GUID,
                    "DataVersion": "CAT_V1=",
                    "DeletionMark": False,
                    "Description": "ЗАО Альфа",
                    "КредитныйЛимитСумма": "50000.00",
                }
            ).encode("utf-8")
        if "Catalog_Контрагенты" in decoded_path and method == b"PATCH":
            return json.dumps(
                {
                    "Ref_Key": CAT_GUID,
                    "DataVersion": "CAT_V2=",
                    "Description": "АО Альфа Групп",
                }
            ).encode("utf-8")
        raise AssertionError(f"Unexpected call: {method} {target_path}")

    service = OneCCommandService(ledger=BusinessCommandLedger(), transport=fake_transport)

    # 1. Document post
    post_preview = await service.preview_command(
        tenant_id=10,
        actor_id=101,
        connection_id=conn.id,
        request=OneCWritePreviewRequest(
            capability=CAPABILITY_DOCUMENT_POST,
            idempotency_key="post-doc-105",
            entity_set="Document_РеализацияТоваровУслуг",
            target_guid=DOC_GUID,
            posting_mode_operational=False,
        ),
        catalog=CATALOG,
        session=session,
    )
    assert post_preview.expected_data_version == "DOC_V1="
    await service.approve_command(
        tenant_id=10,
        actor_id=101,
        command_id=post_preview.id,
        expected_payload_hash=post_preview.payload_hash,
        session=session,
    )
    post_exec = await service.execute_command(
        tenant_id=10,
        actor_id=101,
        command_id=post_preview.id,
        session=session,
    )
    assert post_exec.state == "succeeded"
    assert post_exec.provider_receipt == f"onec:posted:Document_РеализацияТоваровУслуг:{DOC_GUID}:DOC_V1="

    # 2. Catalog update
    cat_preview = await service.preview_command(
        tenant_id=10,
        actor_id=101,
        connection_id=conn.id,
        request=OneCWritePreviewRequest(
            capability=CAPABILITY_CATALOG_UPDATE,
            idempotency_key="upd-cat-alpha",
            entity_set="Catalog_Контрагенты",
            target_guid=CAT_GUID,
            fields={"Description": "АО Альфа Групп"},
        ),
        catalog=CATALOG,
        session=session,
    )
    await service.approve_command(
        tenant_id=10,
        actor_id=101,
        command_id=cat_preview.id,
        expected_payload_hash=cat_preview.payload_hash,
        session=session,
    )
    cat_exec = await service.execute_command(
        tenant_id=10,
        actor_id=101,
        command_id=cat_preview.id,
        session=session,
    )
    assert cat_exec.state == "succeeded"
    assert cat_exec.provider_receipt == f"onec:catalog:Catalog_Контрагенты:{CAT_GUID}:CAT_V2="


@pytest.mark.asyncio
async def test_timeout_transitions_to_unknown_and_reconciles_by_external_ref(write_env) -> None:
    """Acceptance check: transport timeout -> `unknown` + unknown audit -> reconcile checks 1C before retry."""
    conn, secret = _make_write_connection()
    session = FakeOneCCommandSession(connection=conn, secret=secret)

    created_on_server = False
    post_attempts = 0

    async def timeout_then_reconcile_transport(endpoint, *, method: bytes, target_path: str, **_kw):
        nonlocal created_on_server, post_attempts
        if method == b"GET" and "$filter=" in target_path:
            if created_on_server:
                return json.dumps(
                    {
                        "value": [
                            {
                                "Ref_Key": DOC_GUID,
                                "DataVersion": "AFTER_TIMEOUT_V1=",
                            }
                        ]
                    }
                ).encode("utf-8")
            return json.dumps({"value": []}).encode("utf-8")
        if method == b"POST":
            post_attempts += 1
            created_on_server = True
            raise TimeoutError("1C OData gateway timed out after commit")
        raise AssertionError(f"Unexpected call: {method} {target_path}")

    service = OneCCommandService(
        ledger=BusinessCommandLedger(),
        transport=timeout_then_reconcile_transport,
    )

    preview = await service.preview_command(
        tenant_id=10,
        actor_id=101,
        connection_id=conn.id,
        request=OneCWritePreviewRequest(
            capability=CAPABILITY_DOCUMENT_CREATE,
            idempotency_key="timeout-doc-1",
            entity_set="Document_РеализацияТоваровУслуг",
            fields={"СуммаДокумента": Decimal("19900.00")},
        ),
        catalog=CATALOG,
        session=session,
    )
    await service.approve_command(
        tenant_id=10,
        actor_id=101,
        command_id=preview.id,
        expected_payload_hash=preview.payload_hash,
        session=session,
    )

    with pytest.raises(OneCCommandError) as exc_timeout:
        await service.execute_command(
            tenant_id=10,
            actor_id=101,
            command_id=preview.id,
            session=session,
        )
    assert exc_timeout.value.code == "write_timeout_unknown"
    assert session.commands[preview.id].state == "unknown"
    assert post_attempts == 1

    # Verify unknown audit event was recorded with action="onec.command_execute"
    unk_events = [e for e in write_env.events if e.action == "onec.command_execute" and e.outcome == "unknown"]
    assert len(unk_events) == 1
    assert unk_events[0].reason_code == "operation_unconfirmed"

    # Re-calling execute_command on an `unknown` command is refused without sending another POST
    with pytest.raises(OneCCommandError) as exc_conflict:
        await service.execute_command(
            tenant_id=10,
            actor_id=101,
            command_id=preview.id,
            session=session,
        )
    assert exc_conflict.value.code == "command_state_conflict"
    assert post_attempts == 1

    # Reconcile queries 1C OData by external_ref and settles command to `succeeded` without a second POST
    reconciled = await service.reconcile_command(
        tenant_id=10,
        actor_id=101,
        command_id=preview.id,
        session=session,
    )
    assert reconciled.state == "succeeded"
    assert reconciled.provider_receipt == f"onec:document:Document_РеализацияТоваровУслуг:{DOC_GUID}"
    assert post_attempts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", [CAPABILITY_DOCUMENT_CREATE, CAPABILITY_CATALOG_UPDATE])
@pytest.mark.parametrize("revocation", ["field", "metadata", "entity"])
async def test_execute_rechecks_approved_connection_contract(write_env, capability, revocation) -> None:
    conn, secret = _make_write_connection()
    session = FakeOneCCommandSession(connection=conn, secret=secret)
    mutations = []

    async def transport(_endpoint, *, method, **_kwargs):
        if method == b"GET":
            return json.dumps({"Ref_Key": CAT_GUID, "DataVersion": "V1", "Description": "Before"}).encode()
        mutations.append(method)
        return b"{}"

    service = OneCCommandService(transport=transport)
    is_create = capability == CAPABILITY_DOCUMENT_CREATE
    entity_set = "Document_РеализацияТоваровУслуг" if is_create else "Catalog_Контрагенты"
    fields = {"СуммаДокумента": "100.00"} if is_create else {"Description": "After"}
    preview = await service.preview_command(
        tenant_id=10,
        actor_id=101,
        connection_id=conn.id,
        request=OneCWritePreviewRequest(
            capability=capability,
            idempotency_key=f"revoked-{revocation}",
            entity_set=entity_set,
            target_guid=None if is_create else CAT_GUID,
            fields=fields,
        ),
        catalog=CATALOG,
        session=session,
    )
    await service.approve_command(
        tenant_id=10,
        actor_id=101,
        command_id=preview.id,
        expected_payload_hash=preview.payload_hash,
        session=session,
    )
    config = dict(conn.config)
    if revocation == "field":
        # In the create case the user's field remains allowed, but the injected marker is revoked.
        config["allowed_fields"] = {entity_set: ["СуммаДокумента"] if is_create else ["Code"]}
        expected_error = "field_not_allowed"
    elif revocation == "metadata":
        config["metadata_sha256"] = "0" * 64
        expected_error = "metadata_hash_mismatch"
    else:
        config["allowed_entity_sets"] = ["Document_БезКомментария"]
        expected_error = "entity_not_allowed"
    conn.config = config
    with pytest.raises(OneCCommandError, match=expected_error):
        await service.execute_command(tenant_id=10, actor_id=101, command_id=preview.id, session=session)
    assert mutations == []
    assert session.commands[preview.id].state == "failed"
    assert session.commands[preview.id].error_code == expected_error
    entries = await service.list_audit_log(tenant_id=10, actor_id=101, session=session)
    assert any(
        e.command_id == preview.id and e.outcome == "denied" and e.reason_code == expected_error for e in entries
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", [CAPABILITY_DOCUMENT_CREATE, CAPABILITY_CATALOG_UPDATE])
@pytest.mark.parametrize("failure", ["reset", "truncated", "invalid_json", "oversized", "pre_send"])
async def test_post_send_failure_stays_unknown_until_reconcile(write_env, monkeypatch, capability, failure) -> None:
    from urllib.parse import unquote

    conn, secret = _make_write_connection()
    session = FakeOneCCommandSession(connection=conn, secret=secret)
    is_create = capability == CAPABILITY_DOCUMENT_CREATE
    applied = False
    mutations = 0

    class Limiter:
        @asynccontextmanager
        async def acquire(self, *_args, **_kwargs):
            yield

    class Reader:
        async def read(self, _size):
            if failure == "reset":
                raise ConnectionResetError("lost response after commit")
            data = self.data[:_size]
            self.data = self.data[_size:]
            return data

        def __init__(self):
            if failure == "oversized":
                body = b"x" * (commands_mod.MAX_BODY_BYTES + 1)
                self.data = f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body
            else:
                self.data = (
                    b"HTTP/1.1 200 OK\r\nContent-Length: 40\r\n\r\n{"
                    if failure == "truncated"
                    else b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\n\r\n{"
                )

    class Writer:
        def write(self, _data):
            nonlocal applied, mutations
            applied = True
            mutations += 1

        async def drain(self):
            pass

        def close(self):
            pass

        async def wait_closed(self):
            pass

    async def open_connection(*_args, **_kwargs):
        if failure == "pre_send":
            raise ConnectionResetError("connection failed before request write")
        return Reader(), Writer()

    monkeypatch.setattr(asyncio, "open_connection", open_connection)
    monkeypatch.setattr(
        commands_mod, "resolve_endpoint", lambda *_a, **_kw: OutboundTarget("1c.company.ru", 443, ("198.51.100.25",))
    )

    async def transport(endpoint, *, method, target_path, **kwargs):
        decoded = unquote(target_path)
        if method != b"GET":
            return await send_odata_command_request(endpoint, method=method, target_path=target_path, **kwargs)
        if "$metadata" in decoded:
            return SAMPLE_METADATA_XML.encode()
        if "$filter=" in decoded:
            return json.dumps({"value": [{"Ref_Key": DOC_GUID}] if applied else []}).encode()
        return json.dumps(
            {
                "Ref_Key": CAT_GUID,
                "DataVersion": "V2" if applied else "V1",
                "Description": "After" if applied else "Before",
            }
        ).encode()

    service = OneCCommandService(transport=transport)
    service._limiter = Limiter()
    preview = await service.preview_command(
        tenant_id=10,
        actor_id=101,
        connection_id=conn.id,
        request=OneCWritePreviewRequest(
            capability=capability,
            idempotency_key=f"post-send-{failure}",
            entity_set="Document_РеализацияТоваровУслуг" if is_create else "Catalog_Контрагенты",
            target_guid=None if is_create else CAT_GUID,
            fields={"СуммаДокумента": "100.00"} if is_create else {"Description": "After"},
        ),
        catalog=CATALOG,
        session=session,
    )
    await service.approve_command(
        tenant_id=10,
        actor_id=101,
        command_id=preview.id,
        expected_payload_hash=preview.payload_hash,
        session=session,
    )
    expected_error = "connection_failed" if failure == "pre_send" else "write_timeout_unknown"
    with pytest.raises(OneCCommandError, match=expected_error):
        await service.execute_command(tenant_id=10, actor_id=101, command_id=preview.id, session=session)
    if failure == "pre_send":
        assert session.commands[preview.id].state == "failed"
        assert session.commands[preview.id].error_code == "connection_failed"
        assert not applied
        assert mutations == 0
        assert not any(e.action == "onec.command_execute" and e.outcome == "unknown" for e in write_env.events)
        entries = await service.list_audit_log(tenant_id=10, actor_id=101, session=session)
        assert any(
            e.command_id == preview.id and e.outcome == "denied" and e.reason_code == "connection_failed"
            for e in entries
        )
        return
    assert session.commands[preview.id].state == "unknown"
    assert any(e.action == "onec.command_execute" and e.outcome == "unknown" for e in write_env.events)
    reconciled = await service.reconcile_command(tenant_id=10, actor_id=101, command_id=preview.id, session=session)
    assert reconciled.state == "succeeded"
    assert mutations == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_value, expected_state", [(100, "succeeded"), ("100.0", "succeeded"), (101, "failed")])
async def test_catalog_reconcile_compares_typed_values(write_env, remote_value, expected_state) -> None:
    from urllib.parse import unquote

    conn, secret = _make_write_connection()
    session = FakeOneCCommandSession(connection=conn, secret=secret)
    applied = False

    async def transport(_endpoint, *, method, target_path, **_kwargs):
        nonlocal applied
        if "$metadata" in unquote(target_path):
            return SAMPLE_METADATA_XML.encode()
        if method == b"PATCH":
            applied = True
            raise TimeoutError("committed without response")
        return json.dumps(
            {
                "Ref_Key": CAT_GUID,
                "DataVersion": "V2" if applied else "V1",
                "КредитныйЛимитСумма": remote_value if applied else 0,
            }
        ).encode()

    service = OneCCommandService(transport=transport)
    preview = await service.preview_command(
        tenant_id=10,
        actor_id=101,
        connection_id=conn.id,
        request=OneCWritePreviewRequest(
            capability=CAPABILITY_CATALOG_UPDATE,
            idempotency_key="typed-reconcile",
            entity_set="Catalog_Контрагенты",
            target_guid=CAT_GUID,
            fields={"КредитныйЛимитСумма": "100.00"},
        ),
        catalog=CATALOG,
        session=session,
    )
    await service.approve_command(
        tenant_id=10,
        actor_id=101,
        command_id=preview.id,
        expected_payload_hash=preview.payload_hash,
        session=session,
    )
    with pytest.raises(OneCCommandError, match="write_timeout_unknown"):
        await service.execute_command(tenant_id=10, actor_id=101, command_id=preview.id, session=session)
    result = await service.reconcile_command(tenant_id=10, actor_id=101, command_id=preview.id, session=session)
    assert result.state == expected_state


@pytest.mark.asyncio
@pytest.mark.parametrize("owned_session", [False, True])
@pytest.mark.parametrize(
    "failure, expected_error",
    [
        ("connection_failed", "connection_failed"),
        ("invalid_json", "invalid_response"),
        ("missing_field", "invalid_response"),
        ("invalid_field", "invalid_field_value"),
        ("remote_metadata", "metadata_hash_mismatch"),
        ("missing_metadata", "metadata_hash_mismatch"),
        ("revoked_field", "field_not_allowed"),
        ("revoked_entity", "entity_not_allowed"),
    ],
)
async def test_failed_reconcile_retains_unknown_and_records_audit(
    write_env, owned_session, failure, expected_error
) -> None:
    from urllib.parse import unquote

    conn, secret = _make_write_connection()
    session = FakeOneCCommandSession(connection=conn, secret=secret)
    applied = False
    healthy = False
    mutations = 0
    reconcile_reads = 0

    async def transport(_endpoint, *, method, target_path, **_kwargs):
        nonlocal applied, mutations, reconcile_reads
        if method == b"PATCH":
            applied = True
            mutations += 1
            raise TimeoutError("committed without confirmation")
        if applied:
            reconcile_reads += 1
            if not healthy and failure == "connection_failed":
                raise OneCCommandError("connection_failed")
        if "$metadata" in unquote(target_path):
            xml = SAMPLE_METADATA_XML + ("\n" if applied and not healthy and failure == "remote_metadata" else "")
            return xml.encode()
        if applied and not healthy and failure == "invalid_json":
            return b"{"
        live = {"Ref_Key": CAT_GUID, "DataVersion": "V2" if applied else "V1"}
        if not (applied and not healthy and failure == "missing_field"):
            live["Description"] = (
                None if applied and not healthy and failure == "invalid_field" else ("After" if applied else "Before")
            )
        return json.dumps(live).encode()

    service = OneCCommandService(transport=transport)
    preview = await service.preview_command(
        tenant_id=10,
        actor_id=101,
        connection_id=conn.id,
        request=OneCWritePreviewRequest(
            capability=CAPABILITY_CATALOG_UPDATE,
            idempotency_key=f"reconcile-error-{failure}",
            entity_set="Catalog_Контрагенты",
            target_guid=CAT_GUID,
            fields={"Description": "After"},
        ),
        catalog=CATALOG,
        session=session,
    )
    await service.approve_command(
        tenant_id=10,
        actor_id=101,
        command_id=preview.id,
        expected_payload_hash=preview.payload_hash,
        session=session,
    )
    with pytest.raises(OneCCommandError, match="write_timeout_unknown"):
        await service.execute_command(tenant_id=10, actor_id=101, command_id=preview.id, session=session)

    original_config = dict(conn.config)
    original_payload = dict(session.commands[preview.id].payload)
    if failure == "missing_metadata":
        session.commands[preview.id].payload = {
            key: value for key, value in original_payload.items() if key != "metadata_sha256"
        }
    elif failure == "revoked_field":
        conn.config = {**original_config, "allowed_fields": {"Catalog_Контрагенты": ["Code"]}}
    elif failure == "revoked_entity":
        conn.config = {**original_config, "allowed_entity_sets": ["Document_БезКомментария"]}

    outcomes = []

    @asynccontextmanager
    async def owned_context():
        try:
            yield session
            outcomes.append("commit")
        except Exception:
            outcomes.append("rollback")
            raise

    service._sessions._get_owned_session = owned_context
    previous_unknown = [e for e in write_env.events if e.action == "onec.command_execute" and e.outcome == "unknown"]
    with pytest.raises(OneCCommandError, match=expected_error):
        await service.reconcile_command(
            tenant_id=10, actor_id=101, command_id=preview.id, session=None if owned_session else session
        )
    assert outcomes == (["commit"] if owned_session else [])
    assert session.commands[preview.id].state == "unknown"
    assert session.commands[preview.id].error_code == expected_error
    target = next(t for t in session.targets.values() if t.command_id == preview.id)
    assert target.state == "unknown"
    assert target.provider_receipt is None
    unknown = [e for e in write_env.events if e.action == "onec.command_execute" and e.outcome == "unknown"]
    assert len(unknown) == len(previous_unknown) + 1
    assert unknown[-1].reason_code == "operation_unconfirmed"
    assert unknown[-1].resource_id == str(preview.id)
    assert not any(e.action == "onec.command_execute" and e.outcome == "success" for e in write_env.events)
    assert mutations == 1
    if failure in {"missing_metadata", "revoked_field", "revoked_entity"}:
        assert reconcile_reads == 0

    conn.config = original_config
    session.commands[preview.id].payload = original_payload
    healthy = True
    reconciled = await service.reconcile_command(tenant_id=10, actor_id=101, command_id=preview.id, session=session)
    assert reconciled.state == "succeeded"
    assert reconciled.error_code is None
    assert mutations == 1


@pytest.mark.asyncio
async def test_send_odata_command_request_transport_and_error_mappings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test IP-pinned HTTPS transport (`GET`, `POST`, `PATCH`, `409`, `429`, and mutation `TimeoutError`)."""
    endpoint = OneCEndpoint("1c.company.ru", 8443, "/erp", "odata_standard")
    resolved = OutboundTarget("1c.company.ru", 8443, ("198.51.100.25",))
    monkeypatch.setattr(commands_mod, "resolve_endpoint", lambda ep, resolver=None: resolved)

    class DummyLimiter:
        def __init__(self):
            self.signals = []

        @asynccontextmanager
        async def acquire(self, _budget, cost=2, max_wait=0):
            yield

        async def note_signal(self, _budget, sig):
            self.signals.append(sig)

    limiter = DummyLimiter()

    def _wire_response(status: int, body: bytes = b"", headers: list[tuple[bytes, bytes]] | None = None) -> bytes:
        server = h11.Connection(h11.SERVER)
        hdrs = [(b"content-type", b"application/json")] + (headers or [])
        out = bytearray(server.send(h11.Response(status_code=status, headers=hdrs)))
        if body:
            out.extend(server.send(h11.Data(data=body)))
        out.extend(server.send(h11.EndOfMessage()))
        return bytes(out)

    class FakeStreamReader:
        def __init__(self, data: bytes):
            self._buf = bytearray(data)

        async def read(self, n: int) -> bytes:
            chunk = bytes(self._buf[:n])
            del self._buf[:n]
            return chunk

    class FakeStreamWriter:
        def __init__(self):
            self.written = bytearray()

        def write(self, data: bytes) -> None:
            self.written.extend(data)

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    # 1. Successful PATCH with If-Match and JSON body
    writer_ok = FakeStreamWriter()
    resp_ok = _wire_response(200, b'{"Ref_Key":"' + CAT_GUID.encode() + b'","DataVersion":"V2="}')

    async def open_ok(*_a, **_kw):
        return FakeStreamReader(resp_ok), writer_ok

    monkeypatch.setattr(asyncio, "open_connection", open_ok)
    raw = await send_odata_command_request(
        endpoint,
        method=b"PATCH",
        target_path="/erp/odata/standard.odata/Catalog_Контрагенты(guid'" + CAT_GUID + "')?$format=json",
        login="writer",
        password="secret",
        limiter=limiter,
        budget=object(),
        json_body=b'{"Description":"Updated"}',
        if_match="V1=",
    )
    parsed = parse_odata_single_object(raw)
    assert parsed["DataVersion"] == "V2="
    assert b"if-match: v1=" in bytes(writer_ok.written).lower()

    # 2. HTTP 412 Precondition Failed -> data_version_conflict
    resp_412 = _wire_response(412, b'{"error":"precondition"}')

    async def open_412(*_a, **_kw):
        return FakeStreamReader(resp_412), FakeStreamWriter()

    monkeypatch.setattr(asyncio, "open_connection", open_412)
    with pytest.raises(OneCCommandError) as exc_412:
        await send_odata_command_request(
            endpoint,
            method=b"PATCH",
            target_path="/erp/odata/standard.odata/Catalog_Контрагенты?$format=json",
            login="writer",
            password="secret",
            limiter=limiter,
            budget=object(),
            json_body=b"{}",
        )
    assert exc_412.value.code == "data_version_conflict"

    # 3. HTTP 429 -> records rate-limit signal and raises rate_limited
    resp_429 = _wire_response(429, b"", headers=[(b"retry-after", b"15")])

    async def open_429(*_a, **_kw):
        return FakeStreamReader(resp_429), FakeStreamWriter()

    monkeypatch.setattr(asyncio, "open_connection", open_429)
    with pytest.raises(OneCCommandError) as exc_429:
        await send_odata_command_request(
            endpoint,
            method=b"GET",
            target_path="/erp/odata/standard.odata/$metadata",
            login="writer",
            password="secret",
            limiter=limiter,
            budget=object(),
        )
    assert exc_429.value.code == "rate_limited"
    assert len(limiter.signals) == 1

    # 4. Timeout on POST propagates TimeoutError (so command ledger enters `unknown`)
    async def open_timeout(*_a, **_kw):
        raise TimeoutError("socket timed out")

    monkeypatch.setattr(asyncio, "open_connection", open_timeout)
    with pytest.raises(TimeoutError):
        await send_odata_command_request(
            endpoint,
            method=b"POST",
            target_path="/erp/odata/standard.odata/Document_РеализацияТоваровУслуг?$format=json",
            login="writer",
            password="secret",
            limiter=limiter,
            budget=object(),
            json_body=b"{}",
        )

    # 5. Timeout on GET maps to connection_failed, plus 302/401/404/400/500 and invalid inputs
    with pytest.raises(OneCCommandError) as exc_get_timeout:
        await send_odata_command_request(
            endpoint,
            method=b"GET",
            target_path="/erp/odata/standard.odata/$metadata",
            login="writer",
            password="secret",
            limiter=limiter,
            budget=object(),
        )
    assert exc_get_timeout.value.code == "connection_failed"

    for status_code, expected_err in [
        (302, "redirect_forbidden"),
        (401, "authentication_failed"),
        (404, "entity_not_found"),
        (400, "remote_validation_failed"),
        (500, "service_unavailable"),
    ]:
        resp_err = _wire_response(status_code, b"{}")

        async def open_status(*_a, _r=resp_err, **_kw):
            return FakeStreamReader(_r), FakeStreamWriter()

        monkeypatch.setattr(asyncio, "open_connection", open_status)
        with pytest.raises(OneCCommandError) as exc_st:
            await send_odata_command_request(
                endpoint,
                method=b"GET",
                target_path="/erp/odata/standard.odata/$metadata",
                login="writer",
                password="secret",
                limiter=limiter,
                budget=object(),
            )
        assert exc_st.value.code == expected_err

    with pytest.raises(OneCCommandError, match="invalid_credentials"):
        await send_odata_command_request(
            endpoint,
            method=b"GET",
            target_path="/erp",
            login="bad:login",
            password="x",
            limiter=limiter,
            budget=object(),
        )
    with pytest.raises(OneCCommandError, match="invalid_command_target"):
        await send_odata_command_request(
            endpoint,
            method=b"GET",
            target_path="relative",
            login="writer",
            password="x",
            limiter=limiter,
            budget=object(),
        )


@pytest.mark.asyncio
async def test_validation_error_branches_and_owned_session_paths(write_env) -> None:
    """Cover field formatting edge cases, OData parsing, remote $metadata fetch, and owned-session paths."""
    from urllib.parse import unquote

    cat_spec = CATALOG.get_entity_set("Catalog_Контрагенты")
    doc_no_comment = CATALOG.get_entity_set("Document_БезКомментария")
    assert resolve_idempotency_comment_field(doc_no_comment) is None

    # Nullable vs non-nullable None, bad boolean/integer/datetime/string, readonly fields, unknown fields
    norm_null, fmt_null = validate_and_normalize_write_fields(
        cat_spec,
        {"ОсновнойДоговор_Key": None, "Комментарий": None},
    )
    assert norm_null["ОсновнойДоговор_Key"] is None
    assert fmt_null["ОсновнойДоговор_Key"] == "—"

    for bad_fields, expected_code in [
        ({}, "invalid_fields"),
        ({"Description": None}, "invalid_field_value"),
        ({"КредитныйЛимитСумма": True}, "invalid_field_value"),
        ({"Рейтинг": "10"}, "invalid_field_value"),
        ({"Активен": "yes"}, "invalid_field_value"),
        ({"ДатаРегистрации": "not-a-date"}, "invalid_field_value"),
        ({"ДатаРегистрации": 123}, "invalid_field_value"),
        ({"Description": "bad\x00char"}, "invalid_field_value"),
        ({"Description": "x" * 1005}, "invalid_field_value"),
        ({"1bad": "v"}, "invalid_fields"),
        ({"NotInMetadata": "v"}, "field_not_found"),
    ]:
        with pytest.raises(OneCCommandError) as exc_f:
            validate_and_normalize_write_fields(cat_spec, bad_fields)
        assert exc_f.value.code == expected_code

    with pytest.raises(OneCCommandError, match="invalid_currency"):
        validate_and_normalize_write_fields(cat_spec, {"Description": "OK"}, default_currency="INVALID")

    with pytest.raises(OneCCommandError, match="field_not_allowed"):
        validate_and_normalize_write_fields(cat_spec, {"Description": "OK"}, allowed_fields=frozenset({"Code"}))

    # Non-monetary Edm.Decimal field validation + date/datetime/guid formatting
    from datetime import date

    from app.services.onec.commands import (
        _format_single_value,
        extract_data_version,
        normalize_guid,
        validate_idempotency_key,
        verify_data_version_unchanged,
    )
    from app.services.onec.metadata import OneCFieldSpec

    for bad_idem in [123, "", "x" * 125, "bad\x00key"]:
        with pytest.raises(OneCCommandError, match="invalid_idempotency_key"):
            validate_idempotency_key(bad_idem)

    with pytest.raises(OneCCommandError, match="invalid_guid_value"):
        normalize_guid("not-a-guid")

    with pytest.raises(OneCCommandError, match="invalid_response"):
        extract_data_version([])  # type: ignore[arg-type]
    with pytest.raises(OneCCommandError, match="missing_data_version"):
        extract_data_version({})
    with pytest.raises(OneCCommandError, match="missing_data_version"):
        verify_data_version_unchanged(expected_data_version="", remote_obj={"DataVersion": "V1"})

    date_spec = cat_spec.fields["ДатаРегистрации"]
    assert _format_single_value(date_spec, date(2026, 4, 15))[0] == "2026-04-15T00:00:00"
    assert (
        _format_single_value(date_spec, datetime(2026, 4, 15, 12, 30, tzinfo=timezone.utc))[0] == "2026-04-15T12:30:00"
    )

    non_mon_dec = OneCFieldSpec(
        name="Коэффициент",
        edm_type="Edm.Decimal",
        nullable=False,
        is_key=False,
        is_monetary=False,
        precision=10,
        scale=3,
    )
    val_dec, fmt_dec = _format_single_value(non_mon_dec, Decimal("1.25"))
    assert val_dec == "1.250" and fmt_dec == "1.250"
    with pytest.raises(OneCCommandError, match="invalid_field_value"):
        _format_single_value(non_mon_dec, True)
    with pytest.raises(OneCCommandError, match="invalid_field_value"):
        _format_single_value(non_mon_dec, "not-a-decimal")

    # OData response parser branches
    assert parse_odata_single_object(b'{"d":{"Ref_Key":"abc"}}')["Ref_Key"] == "abc"
    assert parse_odata_single_object(b'{"value":[{"Ref_Key":"xyz"}]}')["Ref_Key"] == "xyz"
    assert parse_odata_object_list(b'{"d":{"results":[{"Ref_Key":"r1"}]}}')[0]["Ref_Key"] == "r1"
    for bad_body, code in [
        (b"", "invalid_response"),
        (b"not-json", "invalid_response"),
        (b"[]", "invalid_response"),
        (b'{"value":[]}', "entity_not_found"),
        (b'{"value":[123]}', "invalid_response"),
    ]:
        with pytest.raises(OneCCommandError) as exc_p:
            parse_odata_single_object(bad_body)
        assert exc_p.value.code == code

    for bad_list in [b"", b"not-json", b"[]", b'{"other":1}']:
        with pytest.raises(OneCCommandError, match="invalid_response"):
            parse_odata_object_list(bad_list)

    # Reconcile for document_post and catalog_update + owned-session paths + remote $metadata resolution
    conn, secret = _make_write_connection()
    session = FakeOneCCommandSession(connection=conn, secret=secret)

    async def fake_transport(endpoint, *, method: bytes, target_path: str, **_kw):
        decoded = unquote(target_path)
        if "$metadata" in decoded:
            return SAMPLE_METADATA_XML.encode("utf-8")
        if "$filter=" in decoded:
            return b'{"value":[]}'
        if "Document_РеализацияТоваровУслуг" in decoded and method == b"GET":
            return json.dumps(
                {
                    "Ref_Key": DOC_GUID,
                    "DataVersion": "DV1=",
                    "DeletionMark": False,
                    "Posted": True,
                }
            ).encode("utf-8")
        if "Catalog_Контрагенты" in decoded and method == b"GET":
            return json.dumps(
                {
                    "Ref_Key": CAT_GUID,
                    "DataVersion": "CV1=",
                    "DeletionMark": False,
                    "Description": "Обновлено",
                }
            ).encode("utf-8")
        return json.dumps({"Ref_Key": DOC_GUID, "DataVersion": "DV1="}).encode("utf-8")

    service = OneCCommandService(ledger=BusinessCommandLedger(), transport=fake_transport)

    @asynccontextmanager
    async def owned_session_ctx():
        yield session

    service._sessions._get_owned_session = owned_session_ctx

    # Preview with catalog=None (fetches $metadata via transport) and session=None (owned session)
    rec = await service.preview_command(
        tenant_id=10,
        actor_id=101,
        connection_id=conn.id,
        request=OneCWritePreviewRequest(
            capability=CAPABILITY_DOCUMENT_CREATE,
            idempotency_key="auto-meta-1",
            entity_set="Document_РеализацияТоваровУслуг",
            fields={"СуммаДокумента": "500.00"},
        ),
        catalog=None,
        session=None,
    )
    assert rec.to_dict()["id"] == rec.id

    approved = await service.approve_command(
        tenant_id=10,
        actor_id=101,
        command_id=rec.id,
        expected_payload_hash=rec.payload_hash,
        session=None,
    )
    assert approved.state == "approved"

    executed = await service.execute_command(
        tenant_id=10,
        actor_id=101,
        command_id=rec.id,
        session=None,
    )
    assert executed.state == "succeeded"

    # Reconcile document_post and catalog_update when in unknown state
    session.commands[rec.id].state = "unknown"
    session.commands[rec.id].capability = CAPABILITY_DOCUMENT_POST
    session.commands[rec.id].payload = {
        "entity_set": "Document_РеализацияТоваровУслуг",
        "target_guid": DOC_GUID,
        "external_ref": rec.external_ref,
    }
    rec_post = await service.reconcile_command(tenant_id=10, actor_id=101, command_id=rec.id, session=None)
    assert rec_post.state == "succeeded"

    session.commands[rec.id].state = "unknown"
    session.commands[rec.id].capability = CAPABILITY_CATALOG_UPDATE
    session.commands[rec.id].payload = {
        "entity_set": "Catalog_Контрагенты",
        "target_guid": CAT_GUID,
        "external_ref": rec.external_ref,
        "changes": {"Description": "Обновлено"},
    }
    rec_cat = await service.reconcile_command(tenant_id=10, actor_id=101, command_id=rec.id, session=None)
    assert rec_cat.state == "succeeded"

    # Guard rails in approve / execute / reconcile / list_audit_log / preview
    with pytest.raises(OneCCommandError, match="command_not_found"):
        await service.approve_command(
            tenant_id=10, actor_id=101, command_id=0, expected_payload_hash="sha256:x", session=session
        )
    with pytest.raises(OneCCommandError, match="command_not_found"):
        await service.approve_command(
            tenant_id=10, actor_id=101, command_id=999, expected_payload_hash="sha256:x", session=session
        )
    with pytest.raises(OneCCommandError, match="command_not_found"):
        await service.execute_command(tenant_id=10, actor_id=101, command_id=0, session=session)
    with pytest.raises(OneCCommandError, match="command_not_found"):
        await service.execute_command(tenant_id=10, actor_id=101, command_id=999, session=session)
    with pytest.raises(OneCCommandError, match="command_not_found"):
        await service.reconcile_command(tenant_id=10, actor_id=101, command_id=0, session=session)
    with pytest.raises(OneCCommandError, match="command_not_found"):
        await service.reconcile_command(tenant_id=10, actor_id=101, command_id=999, session=session)
    with pytest.raises(OneCCommandError, match="command_state_conflict"):
        await service.reconcile_command(tenant_id=10, actor_id=101, command_id=rec.id, session=session)
    with pytest.raises(OneCCommandError, match="invalid_limit"):
        await service.list_audit_log(tenant_id=10, actor_id=101, limit=0, session=session)

    with pytest.raises(OneCCommandError, match="entity_not_allowed"):
        await service.preview_command(
            tenant_id=10,
            actor_id=101,
            connection_id=conn.id,
            request=OneCWritePreviewRequest(
                capability=CAPABILITY_DOCUMENT_CREATE,
                idempotency_key="bad-ent",
                entity_set="1bad",
                fields={"СуммаДокумента": "1.00"},
            ),
            catalog=CATALOG,
            session=session,
        )
    with pytest.raises(OneCCommandError, match="invalid_entity_kind"):
        await service.preview_command(
            tenant_id=10,
            actor_id=101,
            connection_id=conn.id,
            request=OneCWritePreviewRequest(
                capability=CAPABILITY_DOCUMENT_CREATE,
                idempotency_key="bad-kind-1",
                entity_set="Catalog_Контрагенты",
                fields={"Description": "X"},
            ),
            catalog=CATALOG,
            session=session,
        )
    with pytest.raises(OneCCommandError, match="invalid_entity_kind"):
        await service.preview_command(
            tenant_id=10,
            actor_id=101,
            connection_id=conn.id,
            request=OneCWritePreviewRequest(
                capability=CAPABILITY_DOCUMENT_POST,
                idempotency_key="bad-kind-2",
                entity_set="Catalog_Контрагенты",
                target_guid=CAT_GUID,
            ),
            catalog=CATALOG,
            session=session,
        )
    with pytest.raises(OneCCommandError, match="invalid_entity_kind"):
        await service.preview_command(
            tenant_id=10,
            actor_id=101,
            connection_id=conn.id,
            request=OneCWritePreviewRequest(
                capability=CAPABILITY_CATALOG_UPDATE,
                idempotency_key="bad-kind-3",
                entity_set="Document_РеализацияТоваровУслуг",
                target_guid=DOC_GUID,
                fields={"СуммаДокумента": "10.00"},
            ),
            catalog=CATALOG,
            session=session,
        )
    with pytest.raises(OneCCommandError, match="metadata_hash_mismatch"):
        bad_conn, _ = _make_write_connection(extra_config={"metadata_sha256": "f" * 64})
        bad_sess = FakeOneCCommandSession(connection=bad_conn, secret=secret)
        await service.preview_command(
            tenant_id=10,
            actor_id=101,
            connection_id=bad_conn.id,
            request=OneCWritePreviewRequest(
                capability=CAPABILITY_DOCUMENT_CREATE,
                idempotency_key="bad-sha",
                entity_set="Document_РеализацияТоваровУслуг",
                fields={"СуммаДокумента": "10.00"},
            ),
            catalog=CATALOG,
            session=bad_sess,
        )

    logs = await service.list_audit_log(tenant_id=10, actor_id=101, limit=20, session=None)
    assert len(logs) >= 1
    assert logs[0].to_dict()["tenant_id"] == 10


@pytest.mark.asyncio
async def test_adapter_failure_that_is_not_onec_error_is_never_audited_as_success(write_env) -> None:
    """M1 regression check: a non-`OneCCommandError` adapter failure must not look like a success.

    The ledger catches every other exception itself and persists `state="failed"` with
    `error_code="adapter_execution_failed"` (this is what happens when the rate-budget store is
    unavailable: `ConnectionDenied` at the transport). The service previously wrote a success audit
    event and returned the record, so the API answered 200 for a failed write.
    """
    conn, secret = _make_write_connection()
    session = FakeOneCCommandSession(connection=conn, secret=secret)

    async def budget_outage_transport(endpoint, *, method: bytes, target_path: str, **_kw):
        if b"$metadata" in unquote(target_path).encode("utf-8"):
            return SAMPLE_METADATA_XML.encode("utf-8")
        raise ConnectionDenied()

    service = OneCCommandService(ledger=BusinessCommandLedger(), transport=budget_outage_transport)
    catalog = parse_odata_metadata_xml(SAMPLE_METADATA_XML)
    preview = await service.preview_command(
        tenant_id=10,
        actor_id=101,
        connection_id=conn.id,
        request=OneCWritePreviewRequest(
            capability=CAPABILITY_DOCUMENT_CREATE,
            idempotency_key="adapter-outage-1",
            entity_set="Document_РеализацияТоваровУслуг",
            fields={"СуммаДокумента": Decimal("100.00")},
        ),
        catalog=catalog,
        session=session,
    )
    await service.approve_command(
        tenant_id=10,
        actor_id=101,
        command_id=preview.id,
        expected_payload_hash=preview.payload_hash,
        session=session,
    )

    with pytest.raises(OneCCommandError) as exc_exec:
        await service.execute_command(tenant_id=10, actor_id=101, command_id=preview.id, session=session)
    assert exc_exec.value.code == "adapter_execution_failed"

    # The failure is persisted, but never audited as a success.
    assert session.commands[preview.id].state == "failed"
    assert session.commands[preview.id].error_code == "adapter_execution_failed"
    success_events = [
        e
        for e in write_env.events
        if e.action == "onec.command_execute" and e.outcome == "success" and e.resource_id == preview.id
    ]
    assert success_events == []
    unknown_events = [e for e in write_env.events if e.action == "onec.command_execute" and e.outcome == "unknown"]
    assert len(unknown_events) == 1
    assert unknown_events[0].reason_code == "operation_unconfirmed"


@pytest.mark.asyncio
async def test_owned_session_commits_failed_state_without_rollback(write_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """F1 regression check: the refusal is committed before the error reaches the caller.

    `BaseManager._get_owned_session` commits on a normal exit and rolls the transaction back
    when an exception escapes the block. The service must finish the terminal state inside the
    block and raise only afterwards; this test drives the real owned-session wrapper over the
    in-memory command session and records which branch was taken. The PostgreSQL counterpart
    lives in `tests/integrations/db/test_p17_t04_db.py`.
    """
    from contextlib import asynccontextmanager

    from app.models.managers.base_manager import BaseManager

    conn, secret = _make_write_connection()
    session = FakeOneCCommandSession(connection=conn, secret=secret)
    outcomes: list[str] = []

    @asynccontextmanager
    async def _owned_session(_self, _session=None):
        try:
            yield session
            outcomes.append("commit")
        except Exception:
            outcomes.append("rollback")
            raise

    monkeypatch.setattr(BaseManager, "_get_owned_session", _owned_session)

    remote_version = "VER_INITIAL_1="
    mutation_sent = False

    async def conflict_transport(endpoint, *, method: bytes, target_path: str, **_kw):
        nonlocal mutation_sent
        if method == b"GET":
            return json.dumps(
                {
                    "Ref_Key": CAT_GUID,
                    "DataVersion": remote_version,
                    "DeletionMark": False,
                    "Description": "ООО Старое Имя",
                }
            ).encode("utf-8")
        mutation_sent = True
        return b"{}"

    service = OneCCommandService(ledger=BusinessCommandLedger(), transport=conflict_transport)

    preview = await service.preview_command(
        tenant_id=10,
        actor_id=101,
        connection_id=conn.id,
        request=OneCWritePreviewRequest(
            capability=CAPABILITY_CATALOG_UPDATE,
            idempotency_key="owned-session-conflict-1",
            entity_set="Catalog_Контрагенты",
            target_guid=CAT_GUID,
            fields={"Description": "ООО Новое Имя"},
        ),
        catalog=CATALOG,
        session=None,
    )
    await service.approve_command(
        tenant_id=10,
        actor_id=101,
        command_id=preview.id,
        expected_payload_hash=preview.payload_hash,
        session=None,
    )

    remote_version = "VER_CHANGED_BY_OTHER_USER_2="
    with pytest.raises(OneCCommandError) as exc_conflict:
        await service.execute_command(
            tenant_id=10,
            actor_id=101,
            command_id=preview.id,
            session=None,
        )
    assert exc_conflict.value.code == "data_version_conflict"
    assert mutation_sent is False

    # The transaction was committed, never rolled back, and the terminal state survived it.
    assert "rollback" not in outcomes
    assert outcomes.count("commit") >= 1
    assert session.commands[preview.id].state == "failed"
    assert session.commands[preview.id].error_code == "data_version_conflict"
    failed_target = [t for t in session.targets.values() if t.command_id == preview.id]
    assert failed_target and failed_target[0].state == "failed"
    assert failed_target[0].error_code == "data_version_conflict"

    # A refused write is never audited as a success.
    success_events = [
        e
        for e in write_env.events
        if e.action == "onec.command_execute" and e.outcome == "success" and e.resource_id == preview.id
    ]
    assert success_events == []

    # The refusal is visible in the audit log as a denied execution.
    entries = await service.list_audit_log(tenant_id=10, actor_id=101, session=session)
    denied = [
        e
        for e in entries
        if e.command_id == preview.id and e.action == "onec.command_execute" and e.outcome == "denied"
    ]
    assert len(denied) == 1
    assert denied[0].state == "failed"
    assert denied[0].reason_code == "data_version_conflict"

    # A repeated execution is refused instead of writing to 1C again.
    with pytest.raises(OneCCommandError) as exc_retry:
        await service.execute_command(
            tenant_id=10,
            actor_id=101,
            command_id=preview.id,
            session=None,
        )
    assert exc_retry.value.code == "command_state_conflict"
    assert mutation_sent is False


@pytest.mark.asyncio
async def test_owned_session_commits_unknown_state_without_rollback(write_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """F1 regression check: a transport timeout is committed as `unknown` and can be reconciled."""
    from contextlib import asynccontextmanager

    from app.models.managers.base_manager import BaseManager

    conn, secret = _make_write_connection()
    session = FakeOneCCommandSession(connection=conn, secret=secret)
    outcomes: list[str] = []

    @asynccontextmanager
    async def _owned_session(_self, _session=None):
        try:
            yield session
            outcomes.append("commit")
        except Exception:
            outcomes.append("rollback")
            raise

    monkeypatch.setattr(BaseManager, "_get_owned_session", _owned_session)

    created_on_server = False
    post_attempts = 0

    async def timeout_transport(endpoint, *, method: bytes, target_path: str, **_kw):
        nonlocal created_on_server, post_attempts
        if method == b"GET" and "$filter=" in target_path:
            if created_on_server:
                return json.dumps({"value": [{"Ref_Key": DOC_GUID, "DataVersion": "AFTER_TIMEOUT_V1="}]}).encode(
                    "utf-8"
                )
            return json.dumps({"value": []}).encode("utf-8")
        if method == b"POST":
            post_attempts += 1
            created_on_server = True
            raise TimeoutError("1C OData gateway timed out after commit")
        raise AssertionError(f"Unexpected call: {method} {target_path}")

    service = OneCCommandService(ledger=BusinessCommandLedger(), transport=timeout_transport)

    preview = await service.preview_command(
        tenant_id=10,
        actor_id=101,
        connection_id=conn.id,
        request=OneCWritePreviewRequest(
            capability=CAPABILITY_DOCUMENT_CREATE,
            idempotency_key="owned-session-timeout-1",
            entity_set="Document_РеализацияТоваровУслуг",
            fields={"СуммаДокумента": Decimal("19900.00")},
        ),
        catalog=CATALOG,
        session=None,
    )
    await service.approve_command(
        tenant_id=10,
        actor_id=101,
        command_id=preview.id,
        expected_payload_hash=preview.payload_hash,
        session=None,
    )

    with pytest.raises(OneCCommandError) as exc_timeout:
        await service.execute_command(
            tenant_id=10,
            actor_id=101,
            command_id=preview.id,
            session=None,
        )
    assert exc_timeout.value.code == "write_timeout_unknown"
    assert post_attempts == 1

    assert "rollback" not in outcomes
    assert outcomes.count("commit") >= 1
    assert session.commands[preview.id].state == "unknown"
    assert session.commands[preview.id].error_code == "adapter_timeout_unknown"
    unknown_target = [t for t in session.targets.values() if t.command_id == preview.id]
    assert unknown_target and unknown_target[0].state == "unknown"

    unknown_events = [e for e in write_env.events if e.action == "onec.command_execute" and e.outcome == "unknown"]
    assert len(unknown_events) == 1
    assert unknown_events[0].reason_code == "operation_unconfirmed"

    # Reconciliation settles the command without a second write.
    reconciled = await service.reconcile_command(
        tenant_id=10,
        actor_id=101,
        command_id=preview.id,
        session=None,
    )
    assert reconciled.state == "succeeded"
    assert reconciled.error_code is None
    assert reconciled.provider_receipt == f"onec:document:Document_РеализацияТоваровУслуг:{DOC_GUID}"
    assert post_attempts == 1
