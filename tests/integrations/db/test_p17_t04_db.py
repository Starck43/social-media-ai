"""Database integration tests for 1C write commands (`OneCCommandService`, P17-T04)."""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from sqlalchemy import select

from app.core.database import async_session_maker
from app.integrations.onec.connections import OneCConnectionService
from app.models.actions.business_command import BusinessCommand, BusinessCommandTarget
from app.models.audit.action_audit_event import ActionAuditEvent
from app.services.commands import ledger as ledger_mod
from app.services.commands.ledger import BusinessCommandLedger
from app.services.onec import commands as commands_mod
from app.services.onec.commands import (
    CAPABILITY_CATALOG_UPDATE,
    CAPABILITY_DOCUMENT_CREATE,
    ONEC_WRITE_CAPABILITIES,
    OneCCommandError,
    OneCCommandService,
    OneCWritePreviewRequest,
)
from app.services.onec.metadata import parse_odata_metadata_xml
from tests.p11_t12_audit_cleanup import purge_tenant_audit_events
from tests.shared.fixtures.personal_workspace import personal_world

pytestmark = pytest.mark.asyncio

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
        <Property Name="СуммаДокумента" Type="Edm.Decimal" Precision="15" Scale="2"/>
        <Property Name="Комментарий" Type="Edm.String" Nullable="true"/>
      </EntityType>
      <EntityType Name="Catalog_Контрагенты">
        <Key><PropertyRef Name="Ref_Key"/></Key>
        <Property Name="Ref_Key" Type="Edm.Guid" Nullable="false"/>
        <Property Name="DataVersion" Type="Edm.String"/>
        <Property Name="DeletionMark" Type="Edm.Boolean" Nullable="false"/>
        <Property Name="Description" Type="Edm.String" Nullable="false"/>
        <Property Name="Комментарий" Type="Edm.String" Nullable="true"/>
      </EntityType>
      <EntityContainer Name="StandardODATA" m:IsDefaultEntityContainer="true">
        <EntitySet Name="Document_РеализацияТоваровУслуг" EntityType="StandardODATA.Document_РеализацияТоваровУслуг"/>
        <EntitySet Name="Catalog_Контрагенты" EntityType="StandardODATA.Catalog_Контрагенты"/>
      </EntityContainer>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
"""

CATALOG = parse_odata_metadata_xml(SAMPLE_METADATA_XML)
DOC_GUID = "11111111-2222-3333-4444-555555555555"
CAT_GUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


async def test_p17_t04_persists_data_version_conflict_and_timeout_reconcile_in_real_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify on a real PostgreSQL session (`session=None` owned transactions) that:
    (a) DataVersion conflict commits `state='failed'` and `error_code='data_version_conflict'` to
        `business_commands` and `business_command_targets`, blocks repeat `execute_command`, and exposes
        the denied `onec.command_execute` entry in `list_audit_log`.
    (b) Transport timeout commits `state='unknown'`, `error_code='adapter_timeout_unknown'`, and
        `action_audit_events` rows (`outcome='unknown'`), and `reconcile_command` transitions the command
        to `state='succeeded'`, clears `error_code`, and records `succeeded` audit rows.
    """
    monkeypatch.setattr(ledger_mod, "RUNTIME_ENABLED_CAPABILITIES", set(ONEC_WRITE_CAPABILITIES))
    monkeypatch.setattr(ledger_mod, "RUNTIME_KILL_SWITCHES", {})

    async def _noop_authorize(_session, _tenant_id, _actor_id, **_kwargs):
        return None

    async def _noop_member_grant(_session, _tenant_id, _user_id, _spec):
        return None

    async def _noop_require_approved(_self, **_kwargs):
        return None

    monkeypatch.setattr(commands_mod, "_authorize", _noop_authorize)
    monkeypatch.setattr(commands_mod.WorkspaceOutboundHostManager, "require_approved", _noop_require_approved)
    monkeypatch.setattr(commands_mod, "budget_for", lambda *_a, **_kw: SimpleNamespace(key="onec:test"))
    monkeypatch.setattr(ledger_mod, "_verify_member_grant", _noop_member_grant)

    async with personal_world() as (tenant, user):
        try:
            conn_service = OneCConnectionService(probe=lambda *a, **kw: None)
            record = await conn_service.create_connection(
                tenant_id=tenant.id,
                actor_id=user.id,
                label="1C ERP Write DB Test",
                host="1c.company.example",
                port=8443,
                base_path="/accounting",
                interface="odata_standard",
                login="writer_bot",
                password="secret-password",
                auth_method="odata_write_credentials",
                verify=True,
            )

            # (a) DataVersion conflict -> state="failed", error_code="data_version_conflict" committed
            remote_version = "DV_INITIAL_1="
            patch_attempts = 0

            async def conflict_transport(_endpoint, *, method: bytes, target_path: str, **_kw):
                nonlocal patch_attempts
                decoded = unquote(target_path)
                if "$metadata" in decoded:
                    return SAMPLE_METADATA_XML.encode("utf-8")
                if method == b"GET":
                    return json.dumps(
                        {
                            "Ref_Key": CAT_GUID,
                            "DataVersion": remote_version,
                            "DeletionMark": False,
                            "Description": "ООО До Конфликта",
                        }
                    ).encode("utf-8")
                patch_attempts += 1
                return b"{}"

            service_a = OneCCommandService(ledger=BusinessCommandLedger(), transport=conflict_transport)
            preview_a = await service_a.preview_command(
                tenant_id=tenant.id,
                actor_id=user.id,
                connection_id=record.id,
                request=OneCWritePreviewRequest(
                    capability=CAPABILITY_CATALOG_UPDATE,
                    idempotency_key="db-dv-conflict-1",
                    entity_set="Catalog_Контрагенты",
                    target_guid=CAT_GUID,
                    fields={"Description": "ООО После Конфликта"},
                ),
                catalog=CATALOG,
                session=None,
            )
            approved_a = await service_a.approve_command(
                tenant_id=tenant.id,
                actor_id=user.id,
                command_id=preview_a.id,
                expected_payload_hash=preview_a.payload_hash,
                session=None,
            )
            assert approved_a.state == "approved"

            remote_version = "DV_CHANGED_REMOTELY_2="
            with pytest.raises(OneCCommandError) as exc_dv:
                await service_a.execute_command(
                    tenant_id=tenant.id,
                    actor_id=user.id,
                    command_id=preview_a.id,
                    session=None,
                )
            assert exc_dv.value.code == "data_version_conflict"
            assert patch_attempts == 0

            async with async_session_maker() as check_session:
                cmd_a = await check_session.get(BusinessCommand, preview_a.id)
                assert cmd_a is not None
                assert cmd_a.state == "failed"
                assert cmd_a.error_code == "data_version_conflict"
                target_a = (
                    await check_session.execute(
                        select(BusinessCommandTarget).where(BusinessCommandTarget.command_id == preview_a.id)
                    )
                ).scalar_one()
                assert target_a.state == "failed"
                assert target_a.error_code == "data_version_conflict"

            with pytest.raises(OneCCommandError) as exc_retry_a:
                await service_a.execute_command(
                    tenant_id=tenant.id,
                    actor_id=user.id,
                    command_id=preview_a.id,
                    session=None,
                )
            assert exc_retry_a.value.code == "command_state_conflict"

            audit_entries_a = await service_a.list_audit_log(tenant_id=tenant.id, actor_id=user.id, session=None)
            denied_a = [
                e
                for e in audit_entries_a
                if e.command_id == preview_a.id and e.action == "onec.command_execute" and e.outcome == "denied"
            ]
            assert len(denied_a) == 1
            assert denied_a[0].reason_code == "data_version_conflict"
            assert denied_a[0].state == "failed"

            # (b) Timeout -> unknown -> reconcile closes command
            created_on_1c = False
            post_attempts = 0

            async def timeout_transport(_endpoint, *, method: bytes, target_path: str, **_kw):
                nonlocal created_on_1c, post_attempts
                decoded = unquote(target_path)
                if "$metadata" in decoded:
                    return SAMPLE_METADATA_XML.encode("utf-8")
                if method == b"GET" and "$filter=" in decoded:
                    if created_on_1c:
                        return json.dumps({"value": [{"Ref_Key": DOC_GUID, "DataVersion": "DV_DOC_1="}]}).encode(
                            "utf-8"
                        )
                    return json.dumps({"value": []}).encode("utf-8")
                if method == b"POST":
                    post_attempts += 1
                    created_on_1c = True
                    raise TimeoutError("1C OData gateway timed out")
                raise AssertionError(f"Unexpected call: {method} {decoded}")

            service_b = OneCCommandService(ledger=BusinessCommandLedger(), transport=timeout_transport)
            preview_b = await service_b.preview_command(
                tenant_id=tenant.id,
                actor_id=user.id,
                connection_id=record.id,
                request=OneCWritePreviewRequest(
                    capability=CAPABILITY_DOCUMENT_CREATE,
                    idempotency_key="db-timeout-reconcile-1",
                    entity_set="Document_РеализацияТоваровУслуг",
                    fields={"СуммаДокумента": Decimal("75000.00")},
                ),
                catalog=CATALOG,
                session=None,
            )
            await service_b.approve_command(
                tenant_id=tenant.id,
                actor_id=user.id,
                command_id=preview_b.id,
                expected_payload_hash=preview_b.payload_hash,
                session=None,
            )

            with pytest.raises(OneCCommandError) as exc_to:
                await service_b.execute_command(
                    tenant_id=tenant.id,
                    actor_id=user.id,
                    command_id=preview_b.id,
                    session=None,
                )
            assert exc_to.value.code == "write_timeout_unknown"
            assert post_attempts == 1

            async with async_session_maker() as check_session:
                cmd_b = await check_session.get(BusinessCommand, preview_b.id)
                assert cmd_b is not None
                assert cmd_b.state == "unknown"
                assert cmd_b.error_code == "adapter_timeout_unknown"
                target_b = (
                    await check_session.execute(
                        select(BusinessCommandTarget).where(BusinessCommandTarget.command_id == preview_b.id)
                    )
                ).scalar_one()
                assert target_b.state == "unknown"
                unknown_events = (
                    (
                        await check_session.execute(
                            select(ActionAuditEvent).where(
                                ActionAuditEvent.tenant_id == tenant.id,
                                ActionAuditEvent.resource_id == str(preview_b.id),
                                ActionAuditEvent.outcome == "unknown",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert {e.action for e in unknown_events} == {"commands.execute", "onec.command_execute"}

            reconciled_b = await service_b.reconcile_command(
                tenant_id=tenant.id,
                actor_id=user.id,
                command_id=preview_b.id,
                session=None,
            )
            assert reconciled_b.state == "succeeded"
            assert reconciled_b.error_code is None
            assert reconciled_b.provider_receipt == f"onec:document:Document_РеализацияТоваровУслуг:{DOC_GUID}"
            assert post_attempts == 1

            async with async_session_maker() as check_session:
                cmd_b_after = await check_session.get(BusinessCommand, preview_b.id)
                assert cmd_b_after is not None
                assert cmd_b_after.state == "succeeded"
                assert cmd_b_after.error_code is None
                assert (
                    cmd_b_after.outcome_summary["provider_receipt"]
                    == f"onec:document:Document_РеализацияТоваровУслуг:{DOC_GUID}"
                )
                target_b_after = (
                    await check_session.execute(
                        select(BusinessCommandTarget).where(BusinessCommandTarget.command_id == preview_b.id)
                    )
                ).scalar_one()
                assert target_b_after.state == "succeeded"
                assert target_b_after.provider_receipt == f"onec:document:Document_РеализацияТоваровУслуг:{DOC_GUID}"
                succeeded_events = (
                    (
                        await check_session.execute(
                            select(ActionAuditEvent).where(
                                ActionAuditEvent.tenant_id == tenant.id,
                                ActionAuditEvent.resource_id == str(preview_b.id),
                                ActionAuditEvent.outcome == "succeeded",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert {"commands.execute", "onec.command_execute"}.issubset({e.action for e in succeeded_events})
        finally:
            await purge_tenant_audit_events(tenant.id)
