"""LOCAL ONLY: P17-T04 1C write commands against an isolated, migrated test schema.

One real PostgreSQL session proves the two persisted outcomes of
`OneCCommandService` that a fake session cannot: a changed remote `DataVersion`
after approval settles the command as `failed`, and a transport timeout settles
it as `unknown` until `reconcile_command` verifies 1C out of band. Only the 1C
HTTP transport is faked; rows, transactions and audit events are real.
"""

import json
from decimal import Decimal
from urllib.parse import unquote

import pytest
from sqlalchemy import select, update

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.integrations.onec.connections import OneCConnectionService
from app.models.actions.business_command import BusinessCommand, BusinessCommandTarget
from app.models.audit.action_audit_event import ActionAuditEvent
from app.models.integrations.connection import WorkspaceConnection
from app.models.managers.workspace_outbound_host_manager import WorkspaceOutboundHostManager
from app.services.commands import ledger as ledger_mod
from app.services.commands.ledger import BusinessCommandLedger
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
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401

pytestmark = [pytest.mark.asyncio, pytest.mark.tenancy]

HOST = "erp.example.invalid"
DOC_SET = "Document_РеализацияТоваровУслуг"
CATALOG_SET = "Catalog_Контрагенты"
# Test-only Fernet key: stored connection secrets are synthetic.
SYNTHETIC_CREDENTIALS_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="

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


async def _verified_write_connection(scope: dict[str, int]) -> int:
    """Create a 1C write connection, then verify it the way the health check would.

    `OneCConnectionService.create` persists `state="unverified"` without the
    metadata contract, and the write path refuses anything less, so the row is
    completed here (allowlist + metadata hash + verified state) on a real session.
    Call inside an established actor/workspace context.
    """
    await WorkspaceOutboundHostManager().approve(**scope, host=HOST)
    record = await OneCConnectionService().create(
        **scope,
        label="1C ERP Write DB Test",
        base_url=f"https://{HOST}/erp",
        interface="odata_standard",
        login="writer_bot",
        password="synthetic-write-password",
        auth_method="odata_write_credentials",
    )
    connection_id = int(record["id"])
    async with async_session_maker() as session, session.begin():
        # The projection returned by `create` never echoes the stored config; read it back to keep
        # the persisted endpoint fields exactly as the service validated them.
        stored = (
            await session.execute(
                select(WorkspaceConnection.config).where(
                    WorkspaceConnection.id == connection_id,
                    WorkspaceConnection.tenant_id == scope["tenant_id"],
                )
            )
        ).scalar_one()
        config = dict(stored)
        config["allowed_entity_sets"] = [DOC_SET, CATALOG_SET]
        config["metadata_sha256"] = CATALOG.metadata_sha256
        await session.execute(
            update(WorkspaceConnection)
            .where(WorkspaceConnection.id == connection_id, WorkspaceConnection.tenant_id == scope["tenant_id"])
            .values(config=config, state="verified")
        )
    return connection_id


async def _target(session, command_id: int) -> BusinessCommandTarget:
    return (
        await session.execute(select(BusinessCommandTarget).where(BusinessCommandTarget.command_id == command_id))
    ).scalar_one()


async def _unknown_events(session, tenant_id: int, command_id: int) -> list[ActionAuditEvent]:
    return list(
        (
            await session.execute(
                select(ActionAuditEvent).where(
                    ActionAuditEvent.tenant_id == tenant_id,
                    ActionAuditEvent.resource_id == str(command_id),
                    ActionAuditEvent.outcome == "unknown",
                )
            )
        )
        .scalars()
        .all()
    )


async def test_p17_t04_data_version_conflict_and_timeout_reconcile_persisted(
    personal_world, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Persist a DataVersion refusal and a timeout/reconcile cycle on a real session.

    (a) A changed remote `DataVersion` after approval is refused with
        `data_version_conflict`: command and target rows are stored as `failed`, no
        write request reaches 1C, a repeat `execute_command` is refused with
        `command_state_conflict`, and `list_audit_log` reports the denied entry.
    (b) A transport timeout stores `state="unknown"` with unknown audit evidence for
        both `commands.execute` and `onec.command_execute`; `reconcile_command` then
        settles the command as `succeeded` with the target receipt and no second POST.
    """
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", SYNTHETIC_CREDENTIALS_KEY)
    monkeypatch.setattr(ledger_mod, "RUNTIME_ENABLED_CAPABILITIES", set(ONEC_WRITE_CAPABILITIES))
    monkeypatch.setattr(ledger_mod, "RUNTIME_KILL_SWITCHES", {})

    tenant, user = personal_world.tenants[0], personal_world.users[0]
    scope = {"tenant_id": tenant.id, "actor_id": user.id}
    try:
        with tenant_scope(tenant.id), permission_scope(user):
            await _run_conflict_and_timeout_scenario(scope)
    finally:
        # Audit evidence has an `ON DELETE RESTRICT` FK on the workspace: clear it before the fixture
        # drops the tenant.
        await purge_tenant_audit_events(tenant.id)


async def _run_conflict_and_timeout_scenario(scope: dict[str, int]) -> None:
    """Run both write outcomes for one tenant inside an established actor/workspace context."""
    tenant_id, actor_id = scope["tenant_id"], scope["actor_id"]
    connection_id = await _verified_write_connection(scope)
    # (a) Conflict: the catalog item changes remotely between approval and execution.
    remote_version = "DV_INITIAL_1="
    write_attempts = 0

    async def conflict_transport(_endpoint, *, method: bytes, target_path: str, **_kw):
        nonlocal write_attempts
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
        write_attempts += 1
        return b"{}"

    service_a = OneCCommandService(ledger=BusinessCommandLedger(), transport=conflict_transport)
    preview_a = await service_a.preview_command(
        tenant_id=tenant_id,
        actor_id=actor_id,
        connection_id=connection_id,
        request=OneCWritePreviewRequest(
            capability=CAPABILITY_CATALOG_UPDATE,
            idempotency_key="db-dv-conflict-1",
            entity_set=CATALOG_SET,
            target_guid=CAT_GUID,
            fields={"Description": "ООО После Конфликта"},
        ),
        catalog=CATALOG,
        session=None,
    )
    assert preview_a.expected_data_version == "DV_INITIAL_1="
    approved_a = await service_a.approve_command(
        tenant_id=tenant_id,
        actor_id=actor_id,
        command_id=preview_a.id,
        expected_payload_hash=preview_a.payload_hash,
        session=None,
    )
    assert approved_a.state == "approved"

    remote_version = "DV_CHANGED_REMOTELY_2="
    with pytest.raises(OneCCommandError) as exc_dv:
        await service_a.execute_command(
            tenant_id=tenant_id,
            actor_id=actor_id,
            command_id=preview_a.id,
            session=None,
        )
    assert exc_dv.value.code == "data_version_conflict"
    assert write_attempts == 0

    async with async_session_maker() as check_session:
        cmd_a = await check_session.get(BusinessCommand, preview_a.id)
        assert cmd_a is not None
        assert cmd_a.state == "failed"
        assert cmd_a.error_code == "data_version_conflict"
        target_a = await _target(check_session, preview_a.id)
        assert target_a.state == "failed"
        assert target_a.error_code == "data_version_conflict"
        assert target_a.provider_receipt is None

    with pytest.raises(OneCCommandError) as exc_retry_a:
        await service_a.execute_command(
            tenant_id=tenant_id,
            actor_id=actor_id,
            command_id=preview_a.id,
            session=None,
        )
    assert exc_retry_a.value.code == "command_state_conflict"
    assert write_attempts == 0

    audit_entries_a = await service_a.list_audit_log(tenant_id=tenant_id, actor_id=actor_id, session=None)
    denied_a = [
        entry
        for entry in audit_entries_a
        if entry.command_id == preview_a.id and entry.action == "onec.command_execute" and entry.outcome == "denied"
    ]
    assert len(denied_a) == 1
    assert denied_a[0].state == "failed"
    assert denied_a[0].reason_code == "data_version_conflict"

    # (b) Timeout: 1C commits the document but the response never arrives.
    created_on_1c = False
    post_attempts = 0

    async def timeout_transport(_endpoint, *, method: bytes, target_path: str, **_kw):
        nonlocal created_on_1c, post_attempts
        decoded = unquote(target_path)
        if "$metadata" in decoded:
            return SAMPLE_METADATA_XML.encode("utf-8")
        if method == b"GET" and "$filter=" in decoded:
            if created_on_1c:
                return json.dumps({"value": [{"Ref_Key": DOC_GUID, "DataVersion": "DV_DOC_1="}]}).encode("utf-8")
            return json.dumps({"value": []}).encode("utf-8")
        if method == b"POST":
            post_attempts += 1
            created_on_1c = True
            raise TimeoutError("1C OData gateway timed out")
        raise AssertionError(f"Unexpected transport call: {method} {decoded}")

    service_b = OneCCommandService(ledger=BusinessCommandLedger(), transport=timeout_transport)
    preview_b = await service_b.preview_command(
        tenant_id=tenant_id,
        actor_id=actor_id,
        connection_id=connection_id,
        request=OneCWritePreviewRequest(
            capability=CAPABILITY_DOCUMENT_CREATE,
            idempotency_key="db-timeout-reconcile-1",
            entity_set=DOC_SET,
            fields={"СуммаДокумента": Decimal("75000.00")},
        ),
        catalog=CATALOG,
        session=None,
    )
    await service_b.approve_command(
        tenant_id=tenant_id,
        actor_id=actor_id,
        command_id=preview_b.id,
        expected_payload_hash=preview_b.payload_hash,
        session=None,
    )

    with pytest.raises(OneCCommandError) as exc_timeout:
        await service_b.execute_command(
            tenant_id=tenant_id,
            actor_id=actor_id,
            command_id=preview_b.id,
            session=None,
        )
    assert exc_timeout.value.code == "write_timeout_unknown"
    assert post_attempts == 1

    async with async_session_maker() as check_session:
        cmd_b = await check_session.get(BusinessCommand, preview_b.id)
        assert cmd_b is not None
        assert cmd_b.state == "unknown"
        assert cmd_b.error_code == "adapter_timeout_unknown"
        target_b = await _target(check_session, preview_b.id)
        assert target_b.state == "unknown"
        assert target_b.provider_receipt is None
        unknown_b = await _unknown_events(check_session, tenant_id, preview_b.id)
        assert {event.action for event in unknown_b} == {"commands.execute", "onec.command_execute"}

    receipt = f"onec:document:{DOC_SET}:{DOC_GUID}"
    reconciled_b = await service_b.reconcile_command(
        tenant_id=tenant_id,
        actor_id=actor_id,
        command_id=preview_b.id,
        session=None,
    )
    assert reconciled_b.state == "succeeded"
    assert reconciled_b.error_code is None
    assert reconciled_b.provider_receipt == receipt
    assert post_attempts == 1

    async with async_session_maker() as check_session:
        cmd_b_after = await check_session.get(BusinessCommand, preview_b.id)
        assert cmd_b_after is not None
        assert cmd_b_after.state == "succeeded"
        assert cmd_b_after.error_code is None
        assert cmd_b_after.outcome_summary["provider_receipt"] == receipt
        target_b_after = await _target(check_session, preview_b.id)
        assert target_b_after.state == "succeeded"
        assert target_b_after.provider_receipt == receipt
        # Reconciliation only settles an `unknown` row: the timeout evidence stays append-only.
        unknown_after = await _unknown_events(check_session, tenant_id, preview_b.id)
        assert {event.action for event in unknown_after} == {"commands.execute", "onec.command_execute"}
