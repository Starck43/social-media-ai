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


async def test_p17_t04_revoked_field_permission_persisted(personal_world, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the new permission regression without replaying the already-green conflict/timeout scenario."""
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", SYNTHETIC_CREDENTIALS_KEY)
    monkeypatch.setattr(ledger_mod, "RUNTIME_ENABLED_CAPABILITIES", set(ONEC_WRITE_CAPABILITIES))
    monkeypatch.setattr(ledger_mod, "RUNTIME_KILL_SWITCHES", {})
    tenant, user = personal_world.tenants[0], personal_world.users[0]
    scope = {"tenant_id": tenant.id, "actor_id": user.id}
    write_attempts = 0

    async def transport(_endpoint, *, method, **_kwargs):
        nonlocal write_attempts
        if method == b"GET":
            return json.dumps({"Ref_Key": CAT_GUID, "DataVersion": "V1", "Description": "Before"}).encode()
        write_attempts += 1
        return b"{}"

    try:
        with tenant_scope(tenant.id), permission_scope(user):
            connection_id = await _verified_write_connection(scope)
            service = OneCCommandService(transport=transport)
            revoked = await service.preview_command(
                **scope,
                connection_id=connection_id,
                request=OneCWritePreviewRequest(
                    capability=CAPABILITY_CATALOG_UPDATE,
                    idempotency_key="db-field-revoked-1",
                    entity_set=CATALOG_SET,
                    target_guid=CAT_GUID,
                    fields={"Description": "No longer permitted"},
                ),
                catalog=CATALOG,
            )
            await service.approve_command(**scope, command_id=revoked.id, expected_payload_hash=revoked.payload_hash)
            async with async_session_maker() as session, session.begin():
                connection = await session.get(WorkspaceConnection, connection_id)
                original_config = dict(connection.config)
                connection.config = {**original_config, "allowed_fields": {CATALOG_SET: ["Code"]}}
            with pytest.raises(OneCCommandError, match="field_not_allowed"):
                await service.execute_command(**scope, command_id=revoked.id)
            assert write_attempts == 0
            async with async_session_maker() as session, session.begin():
                persisted = await session.get(BusinessCommand, revoked.id)
                assert persisted.state == "failed"
                assert persisted.error_code == "field_not_allowed"
                assert (await _target(session, revoked.id)).error_code == "field_not_allowed"
                connection = await session.get(WorkspaceConnection, connection_id)
                connection.config = original_config
            audit_entries = await service.list_audit_log(**scope)
            assert any(
                entry.command_id == revoked.id
                and entry.outcome == "denied"
                and entry.reason_code == "field_not_allowed"
                for entry in audit_entries
            )
    finally:
        await purge_tenant_audit_events(tenant.id)


@pytest.mark.parametrize("failure", ["invalid_response", "missing_metadata"])
async def test_p17_t04_failed_reconcile_audit_persisted(
    personal_world, monkeypatch: pytest.MonkeyPatch, failure
) -> None:
    """A failed observation commits its error, retains unknown and appends real immutable audit evidence."""
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", SYNTHETIC_CREDENTIALS_KEY)
    monkeypatch.setattr(ledger_mod, "RUNTIME_ENABLED_CAPABILITIES", set(ONEC_WRITE_CAPABILITIES))
    monkeypatch.setattr(ledger_mod, "RUNTIME_KILL_SWITCHES", {})
    tenant, user = personal_world.tenants[0], personal_world.users[0]
    scope = {"tenant_id": tenant.id, "actor_id": user.id}
    applied = False
    healthy = False
    mutations = 0
    reconcile_reads = 0

    async def transport(_endpoint, *, method, target_path, **_kwargs):
        nonlocal applied, mutations, reconcile_reads
        if method == b"PATCH":
            applied = True
            mutations += 1
            raise TimeoutError("write committed without confirmation")
        if applied:
            reconcile_reads += 1
        if "$metadata" in unquote(target_path):
            return SAMPLE_METADATA_XML.encode()
        if applied and not healthy and failure == "invalid_response":
            return b"{"
        return json.dumps(
            {
                "Ref_Key": CAT_GUID,
                "DataVersion": "V2" if applied else "V1",
                "Description": "After" if applied else "Before",
            }
        ).encode()

    try:
        with tenant_scope(tenant.id), permission_scope(user):
            connection_id = await _verified_write_connection(scope)
            service = OneCCommandService(transport=transport)
            preview = await service.preview_command(
                **scope,
                connection_id=connection_id,
                request=OneCWritePreviewRequest(
                    capability=CAPABILITY_CATALOG_UPDATE,
                    idempotency_key=f"db-reconcile-error-{failure}",
                    entity_set=CATALOG_SET,
                    target_guid=CAT_GUID,
                    fields={"Description": "After"},
                ),
                catalog=CATALOG,
            )
            await service.approve_command(**scope, command_id=preview.id, expected_payload_hash=preview.payload_hash)
            with pytest.raises(OneCCommandError, match="write_timeout_unknown"):
                await service.execute_command(**scope, command_id=preview.id)

            async with async_session_maker() as session, session.begin():
                cmd = await session.get(BusinessCommand, preview.id)
                original_payload = dict(cmd.payload)
                before_events = await _unknown_events(session, tenant.id, preview.id)
                assert {event.action for event in before_events} == {"commands.execute", "onec.command_execute"}
                before_ids = {event.event_id for event in before_events}
                if failure == "missing_metadata":
                    cmd.payload = {key: value for key, value in original_payload.items() if key != "metadata_sha256"}

            expected_error = "metadata_hash_mismatch" if failure == "missing_metadata" else "invalid_response"
            with pytest.raises(OneCCommandError, match=expected_error):
                await service.reconcile_command(**scope, command_id=preview.id)
            assert mutations == 1
            if failure == "missing_metadata":
                assert reconcile_reads == 0

            async with async_session_maker() as session:
                cmd = await session.get(BusinessCommand, preview.id)
                assert cmd.state == "unknown"
                assert cmd.error_code == expected_error
                target = await _target(session, preview.id)
                assert target.state == "unknown"
                assert target.provider_receipt is None
                after_events = await _unknown_events(session, tenant.id, preview.id)
                added_events = [event for event in after_events if event.event_id not in before_ids]
                assert len(added_events) == 1
                assert added_events[0].action == "onec.command_execute"
                assert added_events[0].reason_code == "operation_unconfirmed"
                assert len(after_events) == len(before_events) + 1

            entries = await service.list_audit_log(**scope)
            assert (
                sum(
                    entry.command_id == preview.id
                    and entry.action == "onec.command_execute"
                    and entry.outcome == "unknown"
                    for entry in entries
                )
                == 2
            )
            with pytest.raises(OneCCommandError, match="command_state_conflict"):
                await service.execute_command(**scope, command_id=preview.id)
            assert mutations == 1

            if failure == "missing_metadata":
                async with async_session_maker() as session, session.begin():
                    cmd = await session.get(BusinessCommand, preview.id)
                    cmd.payload = original_payload
            healthy = True
            reconciled = await service.reconcile_command(**scope, command_id=preview.id)
            assert reconciled.state == "succeeded"
            assert reconciled.error_code is None
            assert mutations == 1
            async with async_session_maker() as session:
                cmd = await session.get(BusinessCommand, preview.id)
                assert cmd.state == "succeeded"
                assert cmd.error_code is None
                target = await _target(session, preview.id)
                assert target.state == "succeeded"
                assert target.provider_receipt == reconciled.provider_receipt
                retained_events = await _unknown_events(session, tenant.id, preview.id)
                assert {event.event_id for event in retained_events} == {event.event_id for event in after_events}
    finally:
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
