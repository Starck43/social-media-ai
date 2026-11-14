"""LOCAL ONLY: P17-T05 PostgreSQL acceptance for 1C RBAC, entity scopes and audit views.

Every row, membership, role change, SQL filter, commit and audit event in this
module is real. Only the 1C OData transport (the paid provider call) is replaced
by an explicit double: no socket is opened, no DNS is resolved and no external
write happens.

Reuse the root `conftest.py` redirect and run the file on an exclusively owned,
migrated isolated schema (never the shared `test_schema`, never the public
schema). The module also requires placeholder migration
`p17t05_onec_audit_export` to be applied: the root reference check rejects a
schema without its capability catalog rows.

Because migration 0155 makes `tenants.plan_holder_user_id` NOT NULL, the module
owns its accounts and creates its workspaces through the atomic account-owned
path (`TenantManager.create_owned_workspace`), never a plain `Tenant` insert.

Cleanup uses supported paths only:

* persisted audit evidence is expired through the existing
  `ActionAuditEventManager.delete_expired` with an advanced test clock, never an
  aging UPDATE or delete that bypasses the immutability trigger;
* outbound-host approvals via `WorkspaceOutboundHostManager.revoke`;
* connections via `OneCConnectionService.disable` (cascades the stored secret);
* role/account changes are restored, then this module's memberships, workspaces
  and accounts are removed.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, func, select
from starlette.applications import Starlette
from starlette.requests import Request

from app.admin.csrf import CSRFTokenManager
from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.integrations.onec.connections import OneCConnectionService
from app.models import PlatformRole, Tenant, TenantUser, User, WorkspaceConnection, WorkspaceRole
from app.models.actions.business_command import BusinessCommand
from app.models.audit.action_audit_event import ActionAuditEvent
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.models.managers.workspace_outbound_host_manager import WorkspaceOutboundHostManager
from app.services.commands import ledger as ledger_mod
from app.services.commands.ledger import BusinessCommandLedger
from app.services.onec import query as query_mod
from app.services.onec.commands import (
    CAPABILITY_CATALOG_UPDATE,
    ONEC_WRITE_CAPABILITIES,
    OneCCommandError,
    OneCCommandService,
    OneCWritePreviewRequest,
)
from app.services.onec.history import OneCQueryHistoryRepository
from app.services.onec.metadata import OneCMetadataError, parse_odata_metadata_xml
from app.services.onec.nl_query import OneCNaturalLanguageError, OneCQueryAnswerService
from app.services.onec.policy import OneCAuditView, authorize_capability, capability_allowed, role_entity_allowlist
from app.services.onec.query import (
    OneCMetadataCache,
    OneCQueryCache,
    OneCQueryError,
    OneCQueryPlan,
    OneCReadQueryService,
)
from app.web import onec_audit

pytestmark = [pytest.mark.asyncio, pytest.mark.tenancy]

HOST = "erp.example.invalid"
ITEM_SET = "Catalog_Номенклатура"
OTHER_SET = "Document_РеализацияТоваровУслуг"
WRITE_SET = "Catalog_Контрагенты"
OTHER_WRITE_SET = "Document_ЗаказПокупателя"
READ_METHOD = "odata_credentials"
WRITE_METHOD = "odata_write_credentials"
CAT_GUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
ONEC_ACTIONS = frozenset({"onec.command_approve", "onec.command_execute", "onec.audit_export"})
CAPABILITIES = ("onec.query", "onec.export", "onec.command.propose", "onec.command.approve")
ENVELOPE = ("id", "occurred_at", "actor_id", "action", "outcome", "resource_kind", "resource_id", "reason_code")

SAMPLE_METADATA_XML = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx Version="1.0" xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx">
  <edmx:DataServices m:DataServiceVersion="3.0" xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">
    <Schema Namespace="StandardODATA" xmlns="http://schemas.microsoft.com/ado/2009/11/edm">
      <EntityType Name="Catalog_Номенклатура">
        <Key><PropertyRef Name="Ref_Key"/></Key>
        <Property Name="Ref_Key" Type="Edm.Guid" Nullable="false"/>
        <Property Name="Description" Type="Edm.String"/>
      </EntityType>
      <EntityType Name="Document_РеализацияТоваровУслуг">
        <Key><PropertyRef Name="Ref_Key"/></Key>
        <Property Name="Ref_Key" Type="Edm.Guid" Nullable="false"/>
        <Property Name="Number" Type="Edm.String"/>
      </EntityType>
      <EntityType Name="Catalog_Контрагенты">
        <Key><PropertyRef Name="Ref_Key"/></Key>
        <Property Name="Ref_Key" Type="Edm.Guid" Nullable="false"/>
        <Property Name="DataVersion" Type="Edm.String"/>
        <Property Name="DeletionMark" Type="Edm.Boolean" Nullable="false"/>
        <Property Name="Description" Type="Edm.String" Nullable="false"/>
        <Property Name="Code" Type="Edm.String"/>
      </EntityType>
      <EntityType Name="Document_ЗаказПокупателя">
        <Key><PropertyRef Name="Ref_Key"/></Key>
        <Property Name="Ref_Key" Type="Edm.Guid" Nullable="false"/>
        <Property Name="DataVersion" Type="Edm.String"/>
        <Property Name="DeletionMark" Type="Edm.Boolean" Nullable="false"/>
        <Property Name="Number" Type="Edm.String"/>
      </EntityType>
      <EntityContainer Name="StandardODATA" m:IsDefaultEntityContainer="true">
        <EntitySet Name="Catalog_Номенклатура" EntityType="StandardODATA.Catalog_Номенклатура"/>
        <EntitySet Name="Document_РеализацияТоваровУслуг" EntityType="StandardODATA.Document_РеализацияТоваровУслуг"/>
        <EntitySet Name="Catalog_Контрагенты" EntityType="StandardODATA.Catalog_Контрагенты"/>
        <EntitySet Name="Document_ЗаказПокупателя" EntityType="StandardODATA.Document_ЗаказПокупателя"/>
      </EntityContainer>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
"""

CATALOG = parse_odata_metadata_xml(SAMPLE_METADATA_XML)
ABSENT = object()


# --------------------------------------------------------------------------- provider double


class ProviderTransport:
    """The only mocked seam: the paid 1C OData HTTP call, recorded per request."""

    def __init__(self, entities: Mapping[str, tuple[str, ...]] | None = None):
        self.entities = dict(entities or {})
        self.calls: list[tuple[bytes, str]] = []

    async def __call__(self, _endpoint, *, method: bytes, target_path: str, **_kwargs) -> bytes:
        self.calls.append((method, target_path))
        if method == b"GET":
            if WRITE_SET in target_path:
                return json.dumps(
                    {"Ref_Key": CAT_GUID, "DataVersion": "DV_001=", "DeletionMark": False, "Description": "Synthetic"}
                ).encode("utf-8")
            if OTHER_WRITE_SET in target_path:
                return json.dumps({"Ref_Key": CAT_GUID, "DataVersion": "DV_002=", "Number": "1"}).encode("utf-8")
            for name, fields in self.entities.items():
                if name in target_path:
                    row = {
                        "Ref_Key": "00000000-0000-0000-0000-000000000001",
                        **dict.fromkeys((field for field in fields if field != "Ref_Key"), "synthetic"),
                    }
                    return json.dumps({"value": [row]}).encode("utf-8")
            return b'{"value": []}'
        return json.dumps({"Ref_Key": CAT_GUID, "DataVersion": "DV_003=", "Number": "1"}).encode("utf-8")

    @property
    def writes(self) -> list[tuple[bytes, str]]:
        return [call for call in self.calls if call[0] != b"GET"]


def provider_transport(monkeypatch: pytest.MonkeyPatch, entities: Mapping[str, tuple[str, ...]] | None = None):
    """Patch both provider seams immediately with one recording double."""
    double = ProviderTransport(entities)
    monkeypatch.setattr(query_mod, "fetch_odata_bytes", double)
    monkeypatch.setattr("app.services.onec.commands.fetch_odata_bytes", double)
    return double


# --------------------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def forbid_unmocked_provider_calls(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("A PostgreSQL acceptance case attempted an unmocked provider call")

    monkeypatch.setattr(query_mod, "fetch_odata_bytes", forbidden)
    monkeypatch.setattr("app.services.onec.commands.fetch_odata_bytes", forbidden)


async def _create_workspaces(slug_prefix: str, holders) -> list[Tenant]:
    """Create owned workspaces through the atomic account-owned path.

    `Tenant.plan_holder_user_id` is NOT NULL since migration 0155, so a plain
    `Tenant.objects.create` cannot insert; `create_owned_workspace` sets the
    holder (the account that owns the workspace) and its owner membership.
    Parent preflight requires the holder to be an existing test account.
    """
    from app.models.managers.tenant_manager import TenantManager

    created: list[Tenant] = []
    for index, holder in enumerate(holders):
        with tenant_scope(bypass=True), permission_scope(holder):
            workspace = await TenantManager().create_owned_workspace(
                user_id=holder.id, name=f"synthetic p17t05 {index}", slug=f"{slug_prefix}-{index}"
            )
        created.append(workspace)
    return created


@pytest.fixture
async def onec_accounts(monkeypatch: pytest.MonkeyPatch):
    """Two real, active accounts owned by this module; each later becomes a workspace holder.

    The shared `personal_world` fixture also creates plain `Tenant` rows, which
    migration 0155 now rejects (`plan_holder_user_id` is NOT NULL), so this module
    owns its accounts and workspaces instead and creates them through the atomic
    account-owned path.
    """
    tag = "p17t05-" + uuid4().hex[:12]
    user_ids: list[int] = []
    users = []
    with tenant_scope(bypass=True), permission_scope(None):
        platform_role = await PlatformRole.objects.get(codename="user")
        for index in range(2):
            created = await User.objects.create(
                username=f"{tag}-holder-{index}",
                email=f"{tag}-holder-{index}@example.invalid",
                hashed_password="synthetic-not-a-password",
                role_id=platform_role.id,
                is_active=True,
            )
            user_ids.append(created.id)
        users = [await User.objects.prefetch_related("role.permissions").get(id=user_id) for user_id in user_ids]
    try:
        yield SimpleNamespace(users=users, tag=tag)
    finally:
        await _remove_owned_identity([], user_ids, [])


@pytest.fixture
async def onec_world(onec_accounts, monkeypatch: pytest.MonkeyPatch):
    """Two owned workspaces with real owner/admin/manager/analyst/viewer memberships.

    Workspaces come from the supported atomic creation path (holder + owner
    membership), so `plan_holder_user_id` is satisfied without a placeholder
    value. The fixture also adds a deactivated work-account membership, an
    inactive account and a downgrade candidate, and removes exactly what it
    created.
    """
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(ledger_mod, "RUNTIME_ENABLED_CAPABILITIES", set(ONEC_WRITE_CAPABILITIES))
    monkeypatch.setattr(ledger_mod, "RUNTIME_KILL_SWITCHES", {})
    tag = onec_accounts.tag
    user_ids: list[int] = []
    membership_ids: list[int] = []
    workspace_ids: list[int] = []
    try:
        primary_holder, secondary_holder = onec_accounts.users[0], onec_accounts.users[1]
        for workspace in await _create_workspaces(tag, (primary_holder, secondary_holder)):
            workspace_ids.append(workspace.id)
        primary, secondary = (
            await Tenant.objects.get(id=workspace_ids[0]),
            await Tenant.objects.get(id=workspace_ids[1]),
        )
        with tenant_scope(bypass=True), permission_scope(None):
            platform_role = await PlatformRole.objects.get(codename="user")
            roles = {
                name: (await WorkspaceRole.objects.get(codename=name)).id
                for name in ("owner", "admin", "manager", "analyst", "viewer")
            }

            async def make_user(suffix: str, *, is_active: bool = True):
                created = await User.objects.create(
                    username=f"{tag}-{suffix}",
                    email=f"{tag}-{suffix}@example.invalid",
                    hashed_password="synthetic-not-a-password",
                    role_id=platform_role.id,
                    is_active=is_active,
                )
                user_ids.append(created.id)
                return await User.objects.prefetch_related("role.permissions").get(id=created.id)

            async def join(tenant_id: int, user_id: int, role: str, *, is_active: bool = True) -> None:
                created = await TenantUser.objects.create(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    channel="web",
                    external_user_id=str(user_id),
                    role_id=roles[role],
                    is_active=is_active,
                )
                membership_ids.append(created.id)

        with tenant_scope(bypass=True), permission_scope(None):
            members = {role: await make_user(role) for role in ("admin", "manager", "analyst", "viewer")}
            members["inactive_account"] = await make_user("inactive", is_active=False)
            members["revoked_member"] = await make_user("revoked")
            members["stale_grant"] = await make_user("stale")
            for role in ("admin", "manager", "analyst", "viewer"):
                await join(primary.id, members[role].id, role)
            await join(primary.id, members["inactive_account"].id, "manager")
            await join(primary.id, members["revoked_member"].id, "manager", is_active=False)
            await join(primary.id, members["stale_grant"].id, "owner")
            # The same account is a manager in workspace B, so capability answers
            # bind to a workspace rather than the account's platform role.
            await join(secondary.id, members["analyst"].id, "manager")
    except BaseException:
        await _remove_owned_identity(membership_ids, user_ids, workspace_ids)
        raise

    yield SimpleNamespace(
        primary=primary,
        secondary=secondary,
        owner=primary_holder,
        foreign_owner=secondary_holder,
        roles=roles,
        **members,
    )

    await _remove_owned_identity(membership_ids, user_ids, workspace_ids)


async def _remove_owned_identity(membership_ids: list[int], user_ids: list[int], workspace_ids: list[int]) -> None:
    """Remove only memberships/accounts/workspaces this fixture added.

    Order follows the foreign keys: `business_commands.actor_id` is
    `ON DELETE RESTRICT`, so the workspace's commands go first (their targets
    cascade), then the workspace, then its accounts.
    """
    with tenant_scope(bypass=True), permission_scope(None):
        for membership_id in membership_ids:
            if await TenantUser.objects.get(id=membership_id) is not None:
                await TenantUser.objects.delete_by_id(membership_id)
        for workspace_id in workspace_ids:
            for membership in await TenantUser.objects.filter(tenant_id=workspace_id).all():
                await TenantUser.objects.delete_by_id(membership.id)
            async with async_session_maker() as session, session.begin():
                await session.execute(delete(BusinessCommand).where(BusinessCommand.tenant_id == workspace_id))
            if await Tenant.objects.get(id=workspace_id) is not None:
                await Tenant.objects.delete_by_id(workspace_id)
        for user_id in user_ids:
            if await User.objects.get(id=user_id) is not None:
                await User.objects.delete_by_id(user_id)
        async with async_session_maker() as session:
            assert await session.scalar(select(func.count()).select_from(User).where(User.id.in_(user_ids))) == 0
            assert (
                await session.scalar(
                    select(func.count()).select_from(TenantUser).where(TenantUser.id.in_(membership_ids))
                )
                == 0
            )
            assert (
                await session.scalar(select(func.count()).select_from(Tenant).where(Tenant.id.in_(workspace_ids))) == 0
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(BusinessCommand)
                    .where(BusinessCommand.tenant_id.in_(workspace_ids))
                )
                == 0
            )


async def _configure(connection_id: int, *, allowed_sets, method: str, by_role=None, fields=None) -> dict:
    """Complete the stored config the way a health check would persist it; return the prior dict."""
    async with async_session_maker() as session, session.begin():
        row = await session.get(WorkspaceConnection, connection_id)
        previous = dict(row.config)
        config: dict[str, Any] = dict(previous)
        config["allowed_entity_sets"] = list(allowed_sets)
        config["metadata_sha256"] = CATALOG.metadata_sha256
        if fields is not None:
            config["allowed_fields"] = fields
        if by_role is not None:
            config["allowed_entity_sets_by_role"] = by_role
        row.config = config
        row.state = "verified"
        row.is_active = True
        row.auth_method = method
    return previous


@pytest.fixture
async def onec_connections(onec_world, monkeypatch: pytest.MonkeyPatch):
    """Two verified synthetic 1C connections in workspace A and one in workspace B.

    Rows are created by the real service (approved host, sealed secret); only the
    administrator allowlist/metadata pin is completed by `_configure`.
    """
    hosts = WorkspaceOutboundHostManager()
    connections = OneCConnectionService()
    primary, secondary = onec_world.primary, onec_world.secondary
    owner, foreign_owner = onec_world.owner, onec_world.foreign_owner
    created: dict[str, tuple[int, int, int]] = {}
    try:
        for tenant, user, key, method in (
            (primary, owner, "read", READ_METHOD),
            (primary, owner, "write", WRITE_METHOD),
            (secondary, foreign_owner, "foreign", WRITE_METHOD),
        ):
            with tenant_scope(tenant.id), permission_scope(user):
                approval = await hosts.approve(tenant_id=tenant.id, actor_id=user.id, host=HOST)
                record = await connections.create(
                    tenant_id=tenant.id,
                    actor_id=user.id,
                    label=f"1C {key}",
                    base_url=f"https://{HOST}/{key}",
                    interface="odata_standard",
                    login=f"synthetic-{key}",
                    password=f"synthetic-{key}-password",
                    auth_method=method,
                )
            created[key] = (tenant.id, user.id, int(record["id"]))
            approval_ids = created.setdefault("approvals", [])
            approval_ids.append((tenant.id, user.id, int(approval["id"])))
    except BaseException:
        await _remove_owned_connections(connections, hosts, created)
        raise

    await _configure(created["read"][2], allowed_sets=(ITEM_SET, OTHER_SET), method=READ_METHOD)
    await _configure(created["write"][2], allowed_sets=(WRITE_SET, OTHER_WRITE_SET), method=WRITE_METHOD)
    await _configure(created["foreign"][2], allowed_sets=(WRITE_SET,), method=WRITE_METHOD)
    try:
        yield SimpleNamespace(
            primary=primary,
            secondary=secondary,
            owner=owner,
            foreign_owner=foreign_owner,
            read=created["read"][2],
            write=created["write"][2],
            foreign=created["foreign"][2],
        )
    finally:
        await _remove_owned_connections(connections, hosts, created)


async def _remove_owned_connections(connections, hosts, created: dict) -> None:
    """Supported-path teardown: disable connections, revoke approvals, expire evidence.

    `disable` only deactivates and blanks the sealed secret, so the fixture's own
    rows are then deleted; audit evidence has an `ON DELETE RESTRICT` FK on the
    workspace and is expired first through `delete_expired`.
    """
    for key in ("read", "write", "foreign"):
        if key not in created:
            continue
        tenant_id, actor_id, connection_id = created[key]
        # Tests deliberately rebind their synthetic account fixtures; restore the
        # creation actor before supported disable/revoke operations.
        async with async_session_maker() as session, session.begin():
            row = await session.get(WorkspaceConnection, connection_id)
            if row is not None:
                row.owner_user_id = actor_id
        try:
            with tenant_scope(tenant_id), permission_scope(SimpleNamespace(id=actor_id, is_active=True)):
                await connections.disable(tenant_id=tenant_id, actor_id=actor_id, connection_id=connection_id)
        except Exception:  # noqa: BLE001 - teardown must not mask the test outcome
            pass
    for tenant_id, actor_id, host_id in created.get("approvals", []):
        try:
            with tenant_scope(tenant_id), permission_scope(SimpleNamespace(id=actor_id, is_active=True)):
                await hosts.revoke(tenant_id=tenant_id, actor_id=actor_id, host_id=host_id)
        except Exception:  # noqa: BLE001 - teardown must not mask the test outcome
            pass
    tenants = {created[key][0] for key in ("read", "write", "foreign") if key in created}
    for tenant_id in sorted(tenants):
        await purge_tenant_audit_events(tenant_id)
    connection_ids = [created[key][2] for key in ("read", "write", "foreign") if key in created]
    with tenant_scope(bypass=True), permission_scope(None):
        async with async_session_maker() as session, session.begin():
            await session.execute(delete(WorkspaceConnection).where(WorkspaceConnection.id.in_(connection_ids)))
        async with async_session_maker() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(WorkspaceConnection)
                    .where(WorkspaceConnection.id.in_(connection_ids))
                )
                == 0
            )


async def purge_tenant_audit_events(tenant_id: int) -> None:
    """Advance only the synthetic cleanup clock; retain every evidence guard."""
    retention = max(plan["retention_days"] for plan in Tenant.PLAN_LIMITS.values())
    with tenant_scope(tenant_id):
        while await ActionAuditEvent.objects.delete_expired(
            now=datetime.now(timezone.utc) + timedelta(days=retention + 1)
        ):
            pass


# --------------------------------------------------------------------------- helpers


def read_service() -> OneCReadQueryService:
    return OneCReadQueryService(
        cache=OneCQueryCache(clock=lambda: 100.0), metadata_cache=OneCMetadataCache(clock=lambda: 100.0)
    )


def read_plan(entity_set: str = ITEM_SET, fields: tuple[str, ...] = ("Ref_Key",)) -> OneCQueryPlan:
    return OneCQueryPlan(entity_set=entity_set, select_fields=fields)


def write_request(key: str, entity_set: str = WRITE_SET, fields: Mapping[str, Any] | None = None):
    return OneCWritePreviewRequest(
        capability=CAPABILITY_CATALOG_UPDATE,
        idempotency_key=key,
        entity_set=entity_set,
        target_guid=CAT_GUID,
        fields=dict(fields or {"Description": "Synthetic write"}),
    )


async def count_events(tenant_id: int, **where) -> int:
    async with async_session_maker() as session:
        statement = select(func.count()).select_from(ActionAuditEvent).where(ActionAuditEvent.tenant_id == tenant_id)
        for field, value in where.items():
            statement = statement.where(getattr(ActionAuditEvent, field) == value)
        return int(await session.scalar(statement) or 0)


async def change_membership(user_id: int, *, role=ABSENT, is_active: bool = True) -> None:
    async with async_session_maker() as session, session.begin():
        row = (
            await session.execute(select(TenantUser).where(TenantUser.user_id == user_id, TenantUser.channel == "web"))
        ).scalar_one()
        row.is_active = is_active
        if role is not ABSENT:
            row.role_id = (
                None
                if role is None
                else (
                    await session.execute(select(WorkspaceRole.id).where(WorkspaceRole.codename == role))
                ).scalar_one()
            )


async def bind_synthetic_connection(connection_id: int, user_id: int) -> None:
    """Prepare an actor-owned synthetic connection; runtime ownership checks stay real."""
    async with async_session_maker() as session, session.begin():
        row = await session.get(WorkspaceConnection, connection_id)
        row.owner_user_id = user_id


async def change_account(user_id: int, *, is_active: bool) -> None:
    async with async_session_maker() as session, session.begin():
        row = await session.get(User, user_id)
        assert row is not None
        row.is_active = is_active


async def approved_and_executed(
    commands: OneCCommandService, *, tenant_id: int, actor_id: int, connection_id: int, key: str
) -> int:
    preview = await commands.preview_command(
        tenant_id=tenant_id,
        actor_id=actor_id,
        connection_id=connection_id,
        request=write_request(key),
        catalog=CATALOG,
    )
    await commands.approve_command(
        tenant_id=tenant_id, actor_id=actor_id, command_id=preview.id, expected_payload_hash=preview.payload_hash
    )
    await commands.execute_command(tenant_id=tenant_id, actor_id=actor_id, command_id=preview.id)
    return preview.id


# --------------------------------------------------------------------------- 1. role / account authority


CAPABILITY_MATRIX = {
    "owner": set(CAPABILITIES),
    "admin": set(CAPABILITIES),
    "manager": {"onec.query", "onec.command.propose"},
    "analyst": {"onec.query"},
    "viewer": set(),
}


async def test_real_membership_capability_matrix_and_owner_admin_identity(onec_world):
    """The requested matrix answers from real memberships; owner and admin are identical."""
    observed: dict[str, set[str]] = {}
    for role in CAPABILITY_MATRIX:
        user = onec_world.owner if role == "owner" else getattr(onec_world, role)
        with tenant_scope(onec_world.primary.id), permission_scope(user):
            allowed = set()
            for capability in CAPABILITIES:
                try:
                    await authorize_capability(tenant_id=onec_world.primary.id, actor_id=user.id, capability=capability)
                except ConnectionDenied:
                    assert capability not in CAPABILITY_MATRIX[role]
                else:
                    allowed.add(capability)
        observed[role] = allowed
    assert observed == CAPABILITY_MATRIX
    assert observed["owner"] == observed["admin"]


async def test_capability_is_bound_to_workspace_and_request_actor(onec_world):
    """A live membership answers only for its own workspace and its own actor."""
    analyst = onec_world.analyst
    with tenant_scope(onec_world.secondary.id), permission_scope(analyst):
        # The shared fixture gives this account a manager membership in workspace B only.
        assert (
            await authorize_capability(tenant_id=onec_world.secondary.id, actor_id=analyst.id, capability="onec.query")
            == "manager"
        )
        assert not capability_allowed("onec.query", tenant_id=onec_world.primary.id, actor_id=analyst.id)
        with pytest.raises(ConnectionDenied):
            await authorize_capability(tenant_id=onec_world.primary.id, actor_id=analyst.id, capability="onec.query")
        # Another actor's live grants never answer for the requesting actor.
        assert not capability_allowed("onec.query", tenant_id=onec_world.secondary.id, actor_id=onec_world.owner.id)
        with pytest.raises(ConnectionDenied):
            await authorize_capability(
                tenant_id=onec_world.secondary.id, actor_id=onec_world.owner.id, capability="onec.query"
            )


@pytest.mark.parametrize(
    "case", ["deactivated_account", "inactive_membership", "missing_membership", "unresolved_role"]
)
async def test_refused_identity_reads_and_writes_never_reach_the_provider(
    onec_world, onec_connections, monkeypatch, caplog, case
):
    """A missing/deactivated identity is refused, logged, and costs no provider call."""
    tenant_id = onec_connections.primary.id
    user = {
        "deactivated_account": onec_world.inactive_account,
        "inactive_membership": onec_world.revoked_member,
        "missing_membership": onec_world.foreign_owner,
        "unresolved_role": onec_world.stale_grant,
    }[case]
    if case == "deactivated_account":
        await change_account(user.id, is_active=False)
    elif case == "unresolved_role":
        await change_membership(user.id, role=None)
    transmitted = provider_transport(monkeypatch)
    read, commands = read_service(), OneCCommandService(ledger=BusinessCommandLedger())
    scope = {"tenant_id": tenant_id, "actor_id": user.id}
    try:
        with tenant_scope(tenant_id), permission_scope(user):
            with pytest.raises(ConnectionDenied):
                await read.execute_query(
                    **scope, connection_id=onec_connections.read, plan=read_plan(), catalog=CATALOG
                )
            with pytest.raises(ConnectionDenied):
                await read.load_connection_context(**scope, connection_id=onec_connections.read)
            with pytest.raises(ConnectionDenied):
                await OneCAuditView().read(**scope)
            with pytest.raises(ConnectionDenied):
                await OneCAuditView().read(**scope, export=True)
            with pytest.raises(ConnectionDenied):
                await commands.preview_command(
                    **scope,
                    connection_id=onec_connections.write,
                    request=write_request(f"refused-{case}"),
                    catalog=CATALOG,
                )
            with pytest.raises(ConnectionDenied):
                await commands.approve_command(**scope, command_id=1, expected_payload_hash="sha256:" + "0" * 64)
            with pytest.raises(ConnectionDenied):
                await commands.execute_command(**scope, command_id=1)
    finally:
        if case == "deactivated_account":
            await change_account(user.id, is_active=True)
        elif case == "unresolved_role":
            await change_membership(user.id, role="owner")
    assert transmitted.calls == []
    assert "reason_code=permission_denied" in caplog.text
    assert await count_events(tenant_id, action="onec.command_approve") == 0
    assert await count_events(tenant_id, action="onec.command_execute") == 0
    assert await count_events(tenant_id, action="onec.audit_export") == 0
    assert await count_events(tenant_id, outcome="unknown") == 0


async def test_foreign_connection_is_refused_for_a_permitted_actor(onec_connections, monkeypatch):
    """A permitted actor cannot reach another workspace's connection from either side."""
    transmitted = provider_transport(monkeypatch)
    commands = OneCCommandService(ledger=BusinessCommandLedger())
    with tenant_scope(onec_connections.primary.id), permission_scope(onec_connections.owner):
        with pytest.raises(ConnectionDenied):
            await commands.preview_command(
                tenant_id=onec_connections.primary.id,
                actor_id=onec_connections.owner.id,
                connection_id=onec_connections.foreign,
                request=write_request("cross-tenant"),
                catalog=CATALOG,
            )
    with tenant_scope(onec_connections.secondary.id), permission_scope(onec_connections.foreign_owner):
        with pytest.raises(ConnectionDenied):
            await commands.preview_command(
                tenant_id=onec_connections.secondary.id,
                actor_id=onec_connections.foreign_owner.id,
                connection_id=onec_connections.write,
                request=write_request("cross-tenant-back"),
                catalog=CATALOG,
            )
    assert transmitted.calls == []


async def test_revoked_and_changed_role_is_reread_before_execution(onec_world, onec_connections, monkeypatch):
    """A stale in-request grant is discarded: every operation re-reads the live role."""
    transmitted = provider_transport(monkeypatch)
    tenant_id = onec_connections.primary.id
    user = onec_world.stale_grant  # owner at first, later downgraded or deactivated
    await bind_synthetic_connection(onec_connections.write, user.id)
    await bind_synthetic_connection(onec_connections.read, user.id)
    commands = OneCCommandService(ledger=BusinessCommandLedger())
    with tenant_scope(tenant_id), permission_scope(user):
        preview = await commands.preview_command(
            tenant_id=tenant_id,
            actor_id=user.id,
            connection_id=onec_connections.write,
            request=write_request("stale-grant"),
            catalog=CATALOG,
        )
        assert (
            await authorize_capability(tenant_id=tenant_id, actor_id=user.id, capability="onec.command.approve")
            == "owner"
        )
        await change_membership(user.id, role="analyst")
        try:
            with pytest.raises(ConnectionDenied):
                await commands.approve_command(
                    tenant_id=tenant_id,
                    actor_id=user.id,
                    command_id=preview.id,
                    expected_payload_hash=preview.payload_hash,
                )
            with pytest.raises(ConnectionDenied):
                await commands.execute_command(tenant_id=tenant_id, actor_id=user.id, command_id=preview.id)
            with pytest.raises(ConnectionDenied):
                await read_service().execute_query(
                    tenant_id=tenant_id,
                    actor_id=user.id,
                    connection_id=onec_connections.read,
                    plan=read_plan(),
                    catalog=CATALOG,
                    policy_capability="onec.export",
                )
            await change_membership(user.id, is_active=False)
            with pytest.raises(ConnectionDenied):
                await read_service().execute_query(
                    tenant_id=tenant_id,
                    actor_id=user.id,
                    connection_id=onec_connections.read,
                    plan=read_plan(),
                    catalog=CATALOG,
                )
        finally:
            await change_membership(user.id, role="owner")
    assert transmitted.calls == []
    async with async_session_maker() as session:
        assert (await session.get(BusinessCommand, preview.id)).state == "previewed"
    assert await count_events(tenant_id, action="onec.command_approve") == 0
    assert await count_events(tenant_id, action="onec.command_execute") == 0


# --------------------------------------------------------------------------- 2. entity scopes


async def _set_role_map(connection_id: int, value) -> dict:
    async with async_session_maker() as session, session.begin():
        row = await session.get(WorkspaceConnection, connection_id)
        previous = dict(row.config)
        config = dict(previous)
        if value is ABSENT:
            config.pop("allowed_entity_sets_by_role", None)
        else:
            config["allowed_entity_sets_by_role"] = value
        row.config = config
    return previous


async def _restore_config(connection_id: int, config: Mapping[str, Any]) -> None:
    async with async_session_maker() as session, session.begin():
        row = await session.get(WorkspaceConnection, connection_id)
        row.config = dict(config)


async def test_role_map_narrows_query_and_absent_or_malformed_maps_fail_closed(
    onec_world, onec_connections, monkeypatch
):
    """One permitted entity works; the other is refused; absent/malformed maps grant nothing."""
    transmitted = provider_transport(monkeypatch, {ITEM_SET: ("Ref_Key",)})
    tenant_id = onec_connections.primary.id
    user = onec_world.analyst
    await bind_synthetic_connection(onec_connections.read, user.id)
    async with async_session_maker() as session:
        original = dict((await session.get(WorkspaceConnection, onec_connections.read)).config)
    try:
        with tenant_scope(tenant_id), permission_scope(user):
            await _set_role_map(onec_connections.read, {"analyst": [ITEM_SET], "manager": [OTHER_SET]})
            allowed = await read_service().execute_query(
                tenant_id=tenant_id,
                actor_id=user.id,
                connection_id=onec_connections.read,
                plan=read_plan(),
                catalog=CATALOG,
            )
            assert allowed.row_count == 1
            with pytest.raises(OneCQueryError, match="entity_not_allowed"):
                await read_service().execute_query(
                    tenant_id=tenant_id,
                    actor_id=user.id,
                    connection_id=onec_connections.read,
                    plan=read_plan(OTHER_SET),
                    catalog=CATALOG,
                )
            context = await read_service().load_connection_context(
                tenant_id=tenant_id, actor_id=user.id, connection_id=onec_connections.read
            )
            assert context.allowed_entity_sets == frozenset({ITEM_SET})

            connection_level = set(original["allowed_entity_sets"])
            for value, expected in (
                (ABSENT, connection_level),
                ({}, set()),
                ({"nobody": [ITEM_SET]}, set()),
                ({"analyst": OTHER_SET}, set()),
                ({"analyst": [1]}, set()),
                ({"analyst": [OTHER_SET]}, {OTHER_SET}),
            ):
                await _set_role_map(onec_connections.read, value)
                context = await read_service().load_connection_context(
                    tenant_id=tenant_id, actor_id=user.id, connection_id=onec_connections.read
                )
                assert context.allowed_entity_sets == frozenset(expected), value
    finally:
        await _restore_config(onec_connections.read, original)
    assert len(transmitted.calls) == 1


async def test_entity_and_field_scope_denial_is_logged_before_any_provider_request(
    onec_world, onec_connections, monkeypatch, caplog
):
    """A field/entity outside the role intersection is refused with a denial log and no network."""
    transmitted = provider_transport(monkeypatch, {ITEM_SET: ("Ref_Key", "Description")})
    tenant_id = onec_connections.primary.id
    user = onec_world.analyst
    await bind_synthetic_connection(onec_connections.read, user.id)
    async with async_session_maker() as session:
        original = dict((await session.get(WorkspaceConnection, onec_connections.read)).config)
    try:
        async with async_session_maker() as session, session.begin():
            row = await session.get(WorkspaceConnection, onec_connections.read)
            row.config = {
                **original,
                "allowed_fields": {ITEM_SET: ["Ref_Key"]},
                "allowed_entity_sets_by_role": {"analyst": [ITEM_SET]},
            }
        with tenant_scope(tenant_id), permission_scope(user):
            with pytest.raises(OneCQueryError, match="field_not_allowed"):
                await read_service().execute_query(
                    tenant_id=tenant_id,
                    actor_id=user.id,
                    connection_id=onec_connections.read,
                    plan=read_plan(fields=("Description",)),
                    catalog=CATALOG,
                )
    finally:
        await _restore_config(onec_connections.read, original)
    assert transmitted.calls == []
    assert "action=onec.query reason_code=permission_denied" in caplog.text


async def test_stored_plan_export_and_write_cannot_use_the_other_entity(onec_world, onec_connections, monkeypatch):
    """A stored plan, an export and a write all re-check the current role map."""
    transmitted = provider_transport(monkeypatch, {ITEM_SET: ("Ref_Key",)})
    tenant_id = onec_connections.primary.id
    owner, manager = onec_connections.owner, onec_world.manager
    await bind_synthetic_connection(onec_connections.write, manager.id)
    history = OneCQueryHistoryRepository()
    async with async_session_maker() as session:
        write_config = dict((await session.get(WorkspaceConnection, onec_connections.write)).config)
        read_config = dict((await session.get(WorkspaceConnection, onec_connections.read)).config)
    try:
        await _set_role_map(onec_connections.write, {"manager": [WRITE_SET], "owner": [WRITE_SET, OTHER_WRITE_SET]})
        await _set_role_map(onec_connections.read, {"owner": [ITEM_SET]})
        with tenant_scope(tenant_id), permission_scope(owner):
            history_id = await history.record(
                tenant_id=tenant_id,
                actor_id=owner.id,
                connection_id=onec_connections.read,
                entity_set=ITEM_SET,
                plan_json={"entity_set": ITEM_SET, "select_fields": ["Ref_Key"]},
                plan_fingerprint="b" * 64,
            )
            stored = await history.load_plan(tenant_id=tenant_id, actor_id=owner.id, history_id=history_id)
            assert stored.entity_set == ITEM_SET
            await _set_role_map(onec_connections.read, {"owner": [OTHER_SET]})
            with pytest.raises(OneCNaturalLanguageError, match="entity_not_allowed"):
                await OneCQueryAnswerService().export(
                    tenant_id=tenant_id,
                    actor_id=owner.id,
                    connection_id=onec_connections.read,
                    format="xlsx",
                    history=history,
                    history_id=history_id,
                    reader=read_service(),
                    catalog=CATALOG,
                )
        commands = OneCCommandService(ledger=BusinessCommandLedger())
        with tenant_scope(tenant_id), permission_scope(manager):
            with pytest.raises(OneCCommandError, match="entity_not_allowed"):
                await commands.preview_command(
                    tenant_id=tenant_id,
                    actor_id=manager.id,
                    connection_id=onec_connections.write,
                    request=write_request("entity-scope", entity_set=OTHER_WRITE_SET, fields={"Number": "1"}),
                    catalog=CATALOG,
                )
            # The administrator field list still applies inside a permitted entity.
            async with async_session_maker() as session, session.begin():
                row = await session.get(WorkspaceConnection, onec_connections.write)
                row.config = {**dict(row.config), "allowed_fields": {WRITE_SET: ["Code"]}}
            with pytest.raises(OneCCommandError, match="field_not_allowed"):
                await commands.preview_command(
                    tenant_id=tenant_id,
                    actor_id=manager.id,
                    connection_id=onec_connections.write,
                    request=write_request("field-scope", fields={"Description": "Denied field"}),
                    catalog=CATALOG,
                )
    finally:
        await _restore_config(onec_connections.write, write_config)
        await _restore_config(onec_connections.read, read_config)
    assert transmitted.calls == []


async def test_map_change_after_preview_is_enforced_at_execution(onec_world, onec_connections, monkeypatch, caplog):
    """A stale preview cannot execute after the role map narrows, entity or fields.

    The entity and field policy is re-checked after the approved command is
    locked and before the ledger is asked to dispatch it. A refusal is therefore
    a policy answer, not a provider failure: the command keeps its `approved`
    state (it is not marked `failed`), no success or unknown evidence is written,
    one structured `onec.command.approve` denial is logged, and no request
    reaches the provider.
    """
    transmitted = provider_transport(monkeypatch)
    tenant_id = onec_connections.primary.id
    user = onec_connections.owner
    commands = OneCCommandService(ledger=BusinessCommandLedger())
    async with async_session_maker() as session:
        original = dict((await session.get(WorkspaceConnection, onec_connections.write)).config)
    try:
        await _set_role_map(onec_connections.write, {"owner": [WRITE_SET]})
        with tenant_scope(tenant_id), permission_scope(user):
            preview = await commands.preview_command(
                tenant_id=tenant_id,
                actor_id=user.id,
                connection_id=onec_connections.write,
                request=write_request("map-changed"),
                catalog=CATALOG,
            )
            await commands.approve_command(
                tenant_id=tenant_id, actor_id=user.id, command_id=preview.id, expected_payload_hash=preview.payload_hash
            )
            # 1. The entity set leaves the role intersection after approval.
            await _set_role_map(onec_connections.write, {"owner": [OTHER_WRITE_SET]})
            assert await count_events(tenant_id, action="onec.command_execute") == 0
            caplog.clear()
            with pytest.raises(OneCCommandError, match="entity_not_allowed"):
                await commands.execute_command(tenant_id=tenant_id, actor_id=user.id, command_id=preview.id)
            entity_denials = _denial_lines(caplog, action="onec.command.approve", tenant_id=tenant_id, actor_id=user.id)
            assert len(entity_denials) == 1

            # 2. The entity set stays permitted, but the field allowlist narrows.
            await _set_role_map(onec_connections.write, {"owner": [WRITE_SET]})
            field_preview = await commands.preview_command(
                tenant_id=tenant_id,
                actor_id=user.id,
                connection_id=onec_connections.write,
                request=write_request("field-changed", fields={"Description": "Stale field"}),
                catalog=CATALOG,
            )
            await commands.approve_command(
                tenant_id=tenant_id,
                actor_id=user.id,
                command_id=field_preview.id,
                expected_payload_hash=field_preview.payload_hash,
            )
            async with async_session_maker() as session, session.begin():
                row = await session.get(WorkspaceConnection, onec_connections.write)
                row.config = {
                    **dict(row.config),
                    "allowed_entity_sets_by_role": {"owner": [WRITE_SET]},
                    "allowed_fields": {WRITE_SET: ["Code"]},
                }
            caplog.clear()
            with pytest.raises(OneCCommandError, match="field_not_allowed"):
                await commands.execute_command(tenant_id=tenant_id, actor_id=user.id, command_id=field_preview.id)
            field_denials = _denial_lines(caplog, action="onec.command.approve", tenant_id=tenant_id, actor_id=user.id)
            assert len(field_denials) == 1
    finally:
        await _restore_config(onec_connections.write, original)
    assert transmitted.calls == []
    async with async_session_maker() as session:
        for command_id in (preview.id, field_preview.id):
            command = await session.get(BusinessCommand, command_id)
            # The refusal is not a provider outcome, so the approved command is untouched.
            assert (command.state, command.error_code) == ("approved", None)
    assert await count_events(tenant_id, action="onec.command_execute") == 0
    assert await count_events(tenant_id, outcome="unknown") == 0


def _denial_lines(caplog, *, action: str, tenant_id: int, actor_id: int) -> list[str]:
    """Structured `permission_denied` lines for exactly this actor's refused action."""
    expected = (
        f"action_audit_path=denied outcome=denied tenant_id={tenant_id} actor_kind=user "
        f"actor_id={actor_id} action={action} reason_code=permission_denied"
    )
    return [record.getMessage() for record in caplog.records if expected in record.getMessage()]


async def test_reconcile_of_a_revoked_entity_preserves_unknown_and_writes_no_event(
    onec_world, onec_connections, monkeypatch, caplog
):
    """Reconcile refuses a revoked entity before it observes 1C or appends evidence.

    The command first reaches `unknown` through a real adapter timeout, which
    records its own unknown evidence. Narrowing the role map afterwards must make
    `reconcile_command` refuse as a policy answer: the command stays `unknown`, no
    second observation is made, and no new (invented) unknown or success row is
    appended.
    """
    tenant_id = onec_connections.primary.id
    user = onec_connections.owner
    timed_out = {"sent": False}

    async def failing_transport(_endpoint, *, method: bytes, target_path: str, **_kwargs):
        if method != b"GET":
            timed_out["sent"] = True
            raise TimeoutError("synthetic adapter timeout")
        return json.dumps({"Ref_Key": CAT_GUID, "DataVersion": "DV_001=", "Description": "Synthetic"}).encode()

    monkeypatch.setattr("app.services.onec.commands.fetch_odata_bytes", failing_transport)
    commands = OneCCommandService(ledger=BusinessCommandLedger())
    async with async_session_maker() as session:
        original = dict((await session.get(WorkspaceConnection, onec_connections.write)).config)
    try:
        await _set_role_map(onec_connections.write, {"owner": [WRITE_SET]})
        with tenant_scope(tenant_id), permission_scope(user):
            preview = await commands.preview_command(
                tenant_id=tenant_id,
                actor_id=user.id,
                connection_id=onec_connections.write,
                request=write_request("reconcile-revoked"),
                catalog=CATALOG,
            )
            await commands.approve_command(
                tenant_id=tenant_id, actor_id=user.id, command_id=preview.id, expected_payload_hash=preview.payload_hash
            )
            with pytest.raises(OneCCommandError, match="write_timeout_unknown"):
                await commands.execute_command(tenant_id=tenant_id, actor_id=user.id, command_id=preview.id)
    finally:
        await _restore_config(onec_connections.write, original)
    assert timed_out["sent"] is True

    async with async_session_maker() as session:
        command = await session.get(BusinessCommand, preview.id)
    assert (command.state, command.error_code) == ("unknown", "adapter_timeout_unknown")
    unknown_before = await count_events(tenant_id, action="onec.command_execute", outcome="unknown")

    # A reconcile call after the entity leaves the intersection must not observe 1C.
    observed_after_revoke: list[bytes] = []

    async def recording_transport(_endpoint, *, method: bytes, target_path: str, **_kwargs):
        observed_after_revoke.append(method)
        return json.dumps({"value": []}).encode()

    monkeypatch.setattr("app.services.onec.commands.fetch_odata_bytes", recording_transport)
    try:
        await _set_role_map(onec_connections.write, {"owner": [OTHER_WRITE_SET]})
        with tenant_scope(tenant_id), permission_scope(user):
            caplog.clear()
            with pytest.raises(OneCCommandError, match="entity_not_allowed"):
                await commands.reconcile_command(tenant_id=tenant_id, actor_id=user.id, command_id=preview.id)
            denials = _denial_lines(caplog, action="onec.command.approve", tenant_id=tenant_id, actor_id=user.id)
            assert len(denials) == 1
    finally:
        await _restore_config(onec_connections.write, original)
    assert observed_after_revoke == []
    async with async_session_maker() as session:
        command = await session.get(BusinessCommand, preview.id)
    # The prior unknown state is the truth; a policy refusal adds no evidence.
    assert (command.state, command.error_code) == ("unknown", "adapter_timeout_unknown")
    assert await count_events(tenant_id, action="onec.command_execute", outcome="unknown") == unknown_before
    assert await count_events(tenant_id, action="onec.command_execute", outcome="succeeded") == 0


def test_absent_role_map_preserves_the_connection_allowlist():
    """The accepted connection-level list survives an omitted map; provided maps only narrow."""
    allowlist = {"allowed_entity_sets": [ITEM_SET, OTHER_SET], "allowed_fields": {ITEM_SET: ["Ref_Key"]}}
    assert role_entity_allowlist(allowlist, "viewer") == frozenset({ITEM_SET, OTHER_SET})
    narrowed = {**allowlist, "allowed_entity_sets_by_role": {"viewer": [ITEM_SET]}}
    assert role_entity_allowlist(narrowed, "viewer") == frozenset({ITEM_SET})
    assert role_entity_allowlist(narrowed, "analyst") == frozenset()
    with pytest.raises(OneCMetadataError):
        role_entity_allowlist({**allowlist, "allowed_entity_sets": ITEM_SET}, "viewer")


# --------------------------------------------------------------------------- 3. audit filtering


async def test_combined_audit_filters_apply_in_sql_before_limit(onec_world, onec_connections, monkeypatch):
    """Connection/actor/operation/time/cursor filters run in SQL and never leak another tenant."""
    transmitted = provider_transport(monkeypatch)
    tenant_a, tenant_b = onec_connections.primary.id, onec_connections.secondary.id
    owner = onec_connections.owner
    commands = OneCCommandService(ledger=BusinessCommandLedger(), transport=transmitted)
    command_ids: list[int] = []
    with tenant_scope(tenant_a), permission_scope(owner):
        command_ids.append(
            await approved_and_executed(
                commands, tenant_id=tenant_a, actor_id=owner.id, connection_id=onec_connections.write, key="filter-0"
            )
        )
        command_ids.append(
            await approved_and_executed(
                commands, tenant_id=tenant_a, actor_id=owner.id, connection_id=onec_connections.write, key="filter-1"
            )
        )
    foreign_owner = onec_connections.foreign_owner
    with tenant_scope(tenant_b), permission_scope(foreign_owner):
        foreign_id = await approved_and_executed(
            commands,
            tenant_id=tenant_b,
            actor_id=foreign_owner.id,
            connection_id=onec_connections.foreign,
            key="foreign",
        )
    # A non-OneC event in workspace A and two workspace-scoped export events.
    with tenant_scope(tenant_a), permission_scope(owner):
        async with ActionAuditEvent.objects._get_owned_session() as session:
            await ActionAuditEvent.objects.record_success(
                session=session,
                actor_kind="user",
                actor_id=owner.id,
                action="crm.contact_create",
                resource_kind="contact",
                resource_id=1,
            )
        for _ in range(2):
            await OneCAuditView().read(tenant_id=tenant_a, actor_id=owner.id, export=True)

    now = datetime.now(timezone.utc)
    window = {"since": now - timedelta(days=1), "until": now + timedelta(minutes=1)}
    with tenant_scope(tenant_a), permission_scope(owner):
        combined = await OneCAuditView().read(
            tenant_id=tenant_a,
            actor_id=owner.id,
            event_actor_id=owner.id,
            connection_id=onec_connections.write,
            action="onec.command_execute",
            limit=10,
            **window,
        )
        assert {row["resource_id"] for row in combined} == {str(i) for i in command_ids}
        assert {row["action"] for row in combined} == {"onec.command_execute"}
        assert {row["actor_id"] for row in combined} == {str(owner.id)}

        # The actor filter selects the event author; it never overrides the requester.
        by_other_actor = await OneCAuditView().read(
            tenant_id=tenant_a, actor_id=owner.id, event_actor_id=onec_world.analyst.id, **window
        )
        assert by_other_actor == []

        # Cursor applies in SQL before LIMIT, so it skips the newest row rather than the limit.
        newest = await OneCAuditView().read(
            tenant_id=tenant_a,
            actor_id=owner.id,
            connection_id=onec_connections.write,
            action="onec.command_execute",
            limit=1,
            **window,
        )
        older = await OneCAuditView().read(
            tenant_id=tenant_a,
            actor_id=owner.id,
            connection_id=onec_connections.write,
            action="onec.command_execute",
            before_id=newest[0]["id"],
            limit=10,
            **window,
        )
        assert len(newest) == 1 and len(older) == 1
        assert {row["id"] for row in older}.isdisjoint({row["id"] for row in newest})

        # Time window: events before `since` never enter the result.
        later = await OneCAuditView().read(
            tenant_id=tenant_a,
            actor_id=owner.id,
            connection_id=onec_connections.write,
            since=now + timedelta(seconds=1),
            until=now + timedelta(days=1),
        )
        assert later == []

        # Foreign and empty filters return no rows, never another tenant's evidence.
        for filters in (
            {"connection_id": onec_connections.foreign},
            {"connection_id": onec_connections.foreign, "action": "onec.command_execute"},
            {"actor_id": foreign_owner.id},
        ):
            assert await OneCAuditView().read(tenant_id=tenant_a, actor_id=owner.id, **window, **filters) == []

        # `onec.audit_export` is workspace-scoped and intentionally absent from a connection filter.
        by_connection = await OneCAuditView().read(
            tenant_id=tenant_a, actor_id=owner.id, connection_id=onec_connections.write, limit=1000, **window
        )
        assert by_connection and all(row["action"] != "onec.audit_export" for row in by_connection)

        everything = await OneCAuditView().read(tenant_id=tenant_a, actor_id=owner.id, limit=1000, **window)
        assert await count_events(tenant_a, action="crm.contact_create") == 1
        assert {row["action"] for row in everything} <= ONEC_ACTIONS
        assert not any(row["resource_id"] == str(foreign_id) for row in everything)
        exports = await OneCAuditView().read(
            tenant_id=tenant_a, actor_id=owner.id, action="onec.audit_export", limit=1000, **window
        )
        assert len(exports) == 2

    # A non-integer audit resource ID is cast to text, never coerced into a command id.
    with tenant_scope(tenant_a), permission_scope(owner):
        async with ActionAuditEvent.objects._get_owned_session() as session:
            await ActionAuditEvent.objects.record_success(
                session=session,
                actor_kind="user",
                actor_id=owner.id,
                action="onec.command_execute",
                resource_kind="command",
                resource_id=2147483647,
            )
        by_connection = await OneCAuditView().read(
            tenant_id=tenant_a, actor_id=owner.id, connection_id=onec_connections.write, limit=1000, **window
        )
    assert "2147483647" not in {row["resource_id"] for row in by_connection}


async def test_t04_actor_scoped_audit_log_is_unchanged(onec_world, onec_connections, monkeypatch):
    """T04's `list_audit_log` still lists only the requesting actor's own 1C commands."""
    transmitted = provider_transport(monkeypatch)
    tenant_id = onec_connections.primary.id
    owner, analyst = onec_connections.owner, onec_world.analyst
    commands = OneCCommandService(ledger=BusinessCommandLedger(), transport=transmitted)
    with tenant_scope(tenant_id), permission_scope(owner):
        command_id = await approved_and_executed(
            commands, tenant_id=tenant_id, actor_id=owner.id, connection_id=onec_connections.write, key="t04-scope"
        )
        entries = await commands.list_audit_log(tenant_id=tenant_id, actor_id=owner.id)
        assert {entry.command_id for entry in entries} == {command_id}
    with tenant_scope(tenant_id), permission_scope(analyst):
        assert await commands.list_audit_log(tenant_id=tenant_id, actor_id=analyst.id) == ()


# --------------------------------------------------------------------------- 4. audited CSV export


def make_request(*, user, tenant_id: int, query: bytes = b"", csrf: CSRFTokenManager | None = None) -> Request:
    app = Starlette()
    if csrf is not None:
        app.state.csrf_manager = csrf
    request = Request(
        {
            "type": "http",
            "method": "POST" if csrf is not None else "GET",
            "path": "/settings/onec/audit",
            "query_string": query,
            "headers": [],
            "app": app,
        }
    )
    request.state.web_user = user
    request.state.tenant_id = tenant_id
    return request


async def csv_export(user, tenant_id: int, query: bytes = b""):
    manager = CSRFTokenManager(uuid4().hex)
    request = make_request(user=user, tenant_id=tenant_id, query=query, csrf=manager)
    with tenant_scope(tenant_id), permission_scope(user):
        return await onec_audit.export_audit(request, token=manager.generate_token())


async def test_authorized_export_commits_exactly_one_workspace_event_before_returning(
    onec_world, onec_connections, monkeypatch
):
    """One `onec.audit_export`/workspace success row is committed for the requester."""
    transmitted = provider_transport(monkeypatch)
    tenant_id, owner = onec_connections.primary.id, onec_connections.owner
    commands = OneCCommandService(ledger=BusinessCommandLedger(), transport=transmitted)
    with tenant_scope(tenant_id), permission_scope(owner):
        await approved_and_executed(
            commands,
            tenant_id=tenant_id,
            actor_id=owner.id,
            connection_id=onec_connections.write,
            key="export-evidence",
        )
    assert await count_events(tenant_id, action="onec.audit_export") == 0
    response = await csv_export(owner, tenant_id, f"connection_id={onec_connections.write}".encode())
    assert response.status_code == 200
    body = response.body.decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(body)))
    assert [row["action"] for row in rows] == ["onec.command_execute", "onec.command_approve"]
    assert set(rows[0]) == set(ENVELOPE)
    assert "payload" not in body and "Synthetic write" not in body
    assert await count_events(tenant_id, action="onec.audit_export") == 1
    async with async_session_maker() as session:
        event = (
            await session.execute(
                select(ActionAuditEvent).where(
                    ActionAuditEvent.tenant_id == tenant_id, ActionAuditEvent.action == "onec.audit_export"
                )
            )
        ).scalar_one()
    assert (event.resource_kind, event.resource_id, event.outcome) == ("workspace", str(tenant_id), "succeeded")
    assert event.actor_id == str(owner.id)


async def test_empty_export_is_audited_too(onec_world, onec_connections):
    """An empty CSV is still audited exactly once and carries only the header."""
    tenant_id, owner = onec_connections.primary.id, onec_connections.owner
    response = await csv_export(owner, tenant_id, f"actor_id={onec_world.analyst.id}".encode())
    assert response.status_code == 200
    body = response.body.decode("utf-8")
    assert list(csv.DictReader(io.StringIO(body))) == []
    assert body.strip() == ",".join(ENVELOPE)
    assert await count_events(tenant_id, action="onec.audit_export") == 1


async def test_export_refusals_release_no_csv_and_record_no_success(onec_world, onec_connections):
    """A CSRF failure or a missing OneC/audit grant releases nothing and claims no success."""
    tenant_id = onec_connections.primary.id
    request = make_request(user=onec_connections.owner, tenant_id=tenant_id, csrf=CSRFTokenManager(uuid4().hex))
    refused = await onec_audit.export_audit(request, token="")
    assert refused.status_code == 403
    assert "onec.command_execute" not in refused.body.decode("utf-8")

    for user in (onec_world.viewer, onec_world.manager, onec_world.analyst):
        unauthorised = await csv_export(user, tenant_id)
        assert unauthorised.status_code == 403
    assert await count_events(tenant_id, action="onec.audit_export") == 0


async def test_audit_write_failure_prevents_the_download(onec_world, onec_connections, monkeypatch):
    """An audit insert failure reaches the response boundary without a CSV."""
    tenant_id = onec_connections.primary.id

    async def failing_record(**_kwargs):
        raise RuntimeError("synthetic audit insert failure")

    monkeypatch.setattr(ActionAuditEvent.objects, "record_success", failing_record)
    with pytest.raises(RuntimeError, match="synthetic audit insert failure"):
        await csv_export(onec_connections.owner, tenant_id)
    assert await count_events(tenant_id, action="onec.audit_export") == 0


async def test_audit_commit_failure_rolls_back_insert_and_releases_no_csv(onec_world, onec_connections, monkeypatch):
    """An inserted-but-uncommitted event cannot authorize releasing the download."""
    from sqlalchemy.ext.asyncio import AsyncSession

    tenant_id = onec_connections.primary.id
    commits = []

    async def fail_commit(session):
        pending = await session.scalar(
            select(func.count())
            .select_from(ActionAuditEvent)
            .where(ActionAuditEvent.tenant_id == tenant_id, ActionAuditEvent.action == "onec.audit_export")
        )
        assert pending == 1
        commits.append(pending)
        raise RuntimeError("synthetic commit refusal")

    with monkeypatch.context() as scope:
        scope.setattr(AsyncSession, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="synthetic commit refusal"):
            await csv_export(onec_connections.owner, tenant_id)
    assert commits == [1]
    assert await count_events(tenant_id, action="onec.audit_export") == 0


async def test_export_actor_is_the_requester_not_the_selected_event_author(onec_world, onec_connections):
    """Selecting another event author in the filter never changes who is credited with the export."""
    tenant_id, owner = onec_connections.primary.id, onec_connections.owner
    response = await csv_export(owner, tenant_id, f"actor_id={onec_world.analyst.id}".encode())
    assert response.status_code == 200
    async with async_session_maker() as session:
        event = (
            await session.execute(
                select(ActionAuditEvent).where(
                    ActionAuditEvent.tenant_id == tenant_id, ActionAuditEvent.action == "onec.audit_export"
                )
            )
        ).scalar_one()
    assert event.actor_id == str(owner.id)
    assert event.actor_id != str(onec_world.analyst.id)
