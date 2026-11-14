"""P17-T05 consumer gates: 1C services authorize a workspace capability before any secret or network.

These tests are DB-exempt: every session, connection row and secret is an explicit mock,
and `authorize_capability` / `role_entity_allowlist` are observed only through call
sequencing. They never import another suite's fixtures, so an unrelated fixture change
cannot weaken a gate silently.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.integrations.onec.credentials import BasicCredentials
from app.integrations.onec.endpoint import validate_endpoint
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.onec import commands as commands_mod
from app.services.onec import nl_query as nl_query_mod
from app.services.onec import query as query_mod
from app.services.onec.commands import (
    CAPABILITY_COMMAND_APPROVE,
    CAPABILITY_COMMAND_PROPOSE,
    OneCCommandError,
    OneCCommandService,
)
from app.services.onec.metadata import OneCEntitySetSpec, OneCFieldSpec, OneCMetadataCatalog
from app.services.onec.nl_query import OneCNaturalLanguageError, OneCQueryAnswerService
from app.services.onec.query import (
    CAPABILITY_EXPORT,
    CAPABILITY_QUERY,
    OneCMetadataCache,
    OneCQueryCache,
    OneCQueryError,
    OneCReadQueryService,
)

TENANT_ID = 11
ACTOR_ID = 7
CONNECTION_ID = 5

_ALLOWED_SETS = ("Catalog_Номенклатура", "Document_РеализацияТоваровУслуг")


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class RecordingSession:
    """Minimal session double; it records what the service asked for, nothing else."""

    def __init__(self, row, secret="ciphertext-v1"):
        self.row = row
        self.secret = secret
        self.get_calls: list[tuple[Any, Any]] = []

    async def execute(self, statement):
        return Result(self.row)

    async def get(self, model, key):
        self.get_calls.append((model, key))
        return SimpleNamespace(secret_encrypted=self.secret)


def catalog() -> OneCMetadataCatalog:
    item = OneCEntitySetSpec(
        name="Catalog_Номенклатура",
        kind="catalog",
        entity_type_name="Catalog_Номенклатура",
        fields={
            "Ref_Key": OneCFieldSpec(name="Ref_Key", edm_type="Edm.Guid", is_key=True),
            "Code": OneCFieldSpec(name="Code", edm_type="Edm.String"),
        },
        key_fields=("Ref_Key",),
    )
    document = OneCEntitySetSpec(
        name="Document_РеализацияТоваровУслуг",
        kind="document",
        entity_type_name="Document_РеализацияТоваровУслуг",
        fields={
            "Ref_Key": OneCFieldSpec(name="Ref_Key", edm_type="Edm.Guid", is_key=True),
            "Number": OneCFieldSpec(name="Number", edm_type="Edm.String"),
        },
        key_fields=("Ref_Key",),
    )
    return OneCMetadataCatalog(
        metadata_sha256="0" * 64,
        namespace="StandardODATA",
        entity_sets={"Catalog_Номенклатура": item, "Document_РеализацияТоваровУслуг": document},
    )


CATALOG = catalog()


def endpoint():
    return validate_endpoint("https://erp.example.com/base", "odata_standard", frozenset({"erp.example.com"}))


def credentials():
    return BasicCredentials("odata_reader", "synthetic-secret")


def connection_row(*, auth_method="odata_credentials", config=None):
    return SimpleNamespace(
        id=CONNECTION_ID,
        tenant_id=TENANT_ID,
        owner_user_id=ACTOR_ID,
        is_active=True,
        provider_id="onec",
        auth_method=auth_method,
        schema_version=1,
        state="verified",
        account_key="account-digest-01",
        config=(
            config
            if config is not None
            else {"host": "erp.example.com", "port": 443, "base_path": "/base", "interface": "odata_standard"}
        ),
    )


class Gate:
    """Observe `authorize_capability` and the connection secret read in one ordered trail."""

    def __init__(self, monkeypatch, module, *, role="analyst", denial=None):
        self.trail: list[tuple] = []
        self.calls: list[dict] = []
        self.denial = denial
        self.role = role
        monkeypatch.setattr(module, "authorize_capability", self._authorize)
        monkeypatch.setattr(
            module, "role_entity_allowlist", lambda config, selected: self._role_allowlist(config, selected)
        )
        monkeypatch.setattr(module, "open_basic_credentials", self._open_credentials)
        self.credentials_opened = 0

    async def _authorize(self, *, tenant_id, actor_id, capability, session=None):
        self.calls.append({"tenant_id": tenant_id, "actor_id": actor_id, "capability": capability, "session": session})
        self.trail.append(("authorize", capability))
        if self.denial is not None:
            raise self.denial
        return self.role

    def _role_allowlist(self, config, selected):
        self.trail.append(("role_allowlist", selected))
        return query_mod_role_allowlist(config, selected)

    def _open_credentials(self, ciphertext):
        self.credentials_opened += 1
        self.trail.append(("credentials",))
        return credentials()


def query_mod_role_allowlist(config, selected):
    """Independent double of the `policy.role_entity_allowlist` contract.

    Kept separate on purpose: the consumer gate is asserted against the documented
    contract (narrow-only intersection, absent map preserves, unknown/malformed fails
    closed), so a regression inside the policy module cannot mask a regression here.
    """
    permitted = frozenset(config.get("allowed_entity_sets") or ())
    if "allowed_entity_sets_by_role" not in config:
        return permitted
    roles = config["allowed_entity_sets_by_role"]
    if not isinstance(roles, dict):
        return frozenset()
    chosen = roles.get(selected)
    if not isinstance(chosen, (list, tuple)) or any(not isinstance(item, str) for item in chosen):
        return frozenset()
    return permitted.intersection(chosen)


def read_service(monkeypatch, *, gate: Gate, row=None, session=None) -> OneCReadQueryService:
    session = session or RecordingSession(row if row is not None else connection_row())
    service = OneCReadQueryService(
        cache=OneCQueryCache(clock=lambda: 100.0), metadata_cache=OneCMetadataCache(clock=lambda: 100.0)
    )

    @asynccontextmanager
    async def owned_session():
        yield session

    service._sessions._get_owned_session = owned_session
    service._hosts.require_approved = AsyncMock(return_value="erp.example.com")
    monkeypatch.setattr(query_mod, "_authorize", AsyncMock())
    return service


# --------------------------------------------------------------------------- read service


async def test_read_loader_authorizes_onec_query_and_intersects_role_before_secret(monkeypatch):
    gate = Gate(monkeypatch, query_mod, role="analyst")
    config = {
        "host": "erp.example.com",
        "port": 443,
        "base_path": "/base",
        "interface": "odata_standard",
        "allowed_entity_sets": list(_ALLOWED_SETS),
        "allowed_entity_sets_by_role": {"analyst": ["Catalog_Номенклатура"], "viewer": []},
    }
    service = read_service(monkeypatch, gate=gate, row=connection_row(config=config))

    context = await service.load_connection_context(tenant_id=TENANT_ID, actor_id=ACTOR_ID, connection_id=CONNECTION_ID)

    assert gate.calls[0]["capability"] == CAPABILITY_QUERY
    assert (gate.calls[0]["tenant_id"], gate.calls[0]["actor_id"]) == (TENANT_ID, ACTOR_ID)
    assert gate.trail[0] == ("authorize", CAPABILITY_QUERY)
    assert gate.trail[1] == ("role_allowlist", "analyst")
    assert gate.trail.index(("credentials",)) > gate.trail.index(("role_allowlist", "analyst"))
    assert context.allowed_entity_sets == frozenset({"Catalog_Номенклатура"})


async def test_read_loader_absent_role_map_keeps_connection_allowlist(monkeypatch):
    gate = Gate(monkeypatch, query_mod, role="manager")
    config = {
        "host": "erp.example.com",
        "port": 443,
        "base_path": "/base",
        "interface": "odata_standard",
        "allowed_entity_sets": list(_ALLOWED_SETS),
    }
    service = read_service(monkeypatch, gate=gate, row=connection_row(config=config))

    context = await service.load_connection_context(tenant_id=TENANT_ID, actor_id=ACTOR_ID, connection_id=CONNECTION_ID)

    assert context.allowed_entity_sets == frozenset(_ALLOWED_SETS)


async def test_read_loader_unknown_role_fails_closed_on_entities(monkeypatch):
    gate = Gate(monkeypatch, query_mod, role="ghost")
    config = {
        "host": "erp.example.com",
        "port": 443,
        "base_path": "/base",
        "interface": "odata_standard",
        "allowed_entity_sets": list(_ALLOWED_SETS),
        "allowed_entity_sets_by_role": {"analyst": ["Catalog_Номенклатура"]},
    }
    service = read_service(monkeypatch, gate=gate, row=connection_row(config=config))

    context = await service.load_connection_context(tenant_id=TENANT_ID, actor_id=ACTOR_ID, connection_id=CONNECTION_ID)

    assert context.allowed_entity_sets == frozenset()


async def test_read_query_refusal_precedes_secret_network_and_history(monkeypatch):
    gate = Gate(monkeypatch, query_mod, denial=ConnectionDenied())
    session = RecordingSession(connection_row())
    service = read_service(monkeypatch, gate=gate, session=session)
    transport = AsyncMock()
    monkeypatch.setattr(query_mod, "fetch_odata_bytes", transport)

    with pytest.raises(ConnectionDenied):
        await service.execute_query(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            connection_id=CONNECTION_ID,
            plan=query_mod.OneCQueryPlan(entity_set="Catalog_Номенклатура", select_fields=("Ref_Key",)),
            catalog=CATALOG,
        )

    assert gate.trail == [("authorize", CAPABILITY_QUERY)]
    assert gate.credentials_opened == 0
    assert session.get_calls == []
    assert transport.await_count == 0
    assert service.list_history_metadata(tenant_id=TENANT_ID) == ()


async def test_read_export_capability_is_passed_through_loader(monkeypatch):
    gate = Gate(monkeypatch, query_mod, role="analyst")
    service = read_service(monkeypatch, gate=gate)

    await service.load_connection_context(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        connection_id=CONNECTION_ID,
        policy_capability=CAPABILITY_EXPORT,
    )

    assert [call["capability"] for call in gate.calls] == [CAPABILITY_EXPORT]


async def test_read_loader_rejects_invalid_connection_before_authorization(monkeypatch):
    gate = Gate(monkeypatch, query_mod)
    service = read_service(monkeypatch, gate=gate)

    with pytest.raises(ConnectionDenied):
        await service.load_connection_context(tenant_id=TENANT_ID, actor_id=ACTOR_ID, connection_id=0)

    assert gate.calls == []


# --------------------------------------------------------------------------- NL export


def export_stub_reader() -> SimpleNamespace:
    context = SimpleNamespace(allowed_entity_sets=frozenset(_ALLOWED_SETS), allowed_fields={})
    return SimpleNamespace(
        load_connection_context=AsyncMock(return_value=context),
        fetch_metadata_catalog=AsyncMock(return_value=CATALOG),
        execute_query=AsyncMock(side_effect=AssertionError("execute must not run")),
    )


async def test_nl_export_authorizes_onec_export_before_context_or_transport(monkeypatch):
    calls: list[tuple] = []

    async def fake_authorize(*, tenant_id, actor_id, capability, session=None):
        calls.append(("authorize", capability))
        raise ConnectionDenied()

    monkeypatch.setattr(nl_query_mod, "authorize_capability", fake_authorize)
    reader = export_stub_reader()
    history = SimpleNamespace(
        load_plan=AsyncMock(),
        record=AsyncMock(),
        mark_exported=AsyncMock(),
    )

    with pytest.raises(ConnectionDenied):
        await OneCQueryAnswerService().export(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            connection_id=CONNECTION_ID,
            format="xlsx",
            history=history,
            question="продажи за месяц",
            reader=reader,
        )

    assert calls == [("authorize", CAPABILITY_EXPORT)]
    assert reader.load_connection_context.await_count == 0
    assert reader.fetch_metadata_catalog.await_count == 0
    assert reader.execute_query.await_count == 0
    history.load_plan.assert_not_awaited()
    history.record.assert_not_awaited()


async def test_nl_export_invalid_format_refused_before_authorization(monkeypatch):
    authorize = AsyncMock(return_value="analyst")
    monkeypatch.setattr(nl_query_mod, "authorize_capability", authorize)

    with pytest.raises(OneCNaturalLanguageError, match="invalid_export_format"):
        await OneCQueryAnswerService().export(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            connection_id=CONNECTION_ID,
            format="csv",
            history=SimpleNamespace(),
            question="x",
            reader=export_stub_reader(),
        )

    authorize.assert_not_awaited()


async def test_nl_export_passes_export_capability_to_loader(monkeypatch):
    authorize = AsyncMock(return_value="admin")
    monkeypatch.setattr(nl_query_mod, "authorize_capability", authorize)
    context = query_mod.OneCConnectionContext(
        endpoint=endpoint(),
        credentials=credentials(),
        account_key="account-digest-01",
        allowed_entity_sets=frozenset(_ALLOWED_SETS),
        allowed_fields={"Catalog_Номенклатура": frozenset({"Ref_Key"})},
        metadata_sha256=CATALOG.metadata_sha256,
    )
    reader = SimpleNamespace(
        load_connection_context=AsyncMock(return_value=context),
        fetch_metadata_catalog=AsyncMock(return_value=CATALOG),
        execute_query=AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        nl_query_mod,
        "plan_from_json",
        lambda payload: query_mod.OneCQueryPlan(entity_set="Catalog_Номенклатура", select_fields=("Ref_Key",)),
    )
    monkeypatch.setattr(nl_query_mod, "export_result", lambda result, fmt, title=None: object())
    history = SimpleNamespace(
        load_plan=AsyncMock(return_value=SimpleNamespace(connection_id=CONNECTION_ID, plan_json={})),
        mark_exported=AsyncMock(),
    )

    file, history_id = await OneCQueryAnswerService().export(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        connection_id=CONNECTION_ID,
        format="xlsx",
        history=history,
        history_id=42,
        reader=reader,
        catalog=CATALOG,
    )

    assert authorize.await_args.kwargs["capability"] == CAPABILITY_EXPORT
    assert reader.load_connection_context.await_args.kwargs["policy_capability"] == CAPABILITY_EXPORT
    assert reader.execute_query.await_args.kwargs["policy_capability"] == CAPABILITY_EXPORT
    history.load_plan.assert_awaited_once_with(tenant_id=TENANT_ID, actor_id=ACTOR_ID, history_id=42)
    history.mark_exported.assert_awaited_once_with(
        tenant_id=TENANT_ID, actor_id=ACTOR_ID, history_id=42, exported_format="xlsx"
    )
    assert history_id == 42
    assert file is not None


# --------------------------------------------------------------------------- command service


class CommandSession(RecordingSession):
    def __init__(self, row, command):
        super().__init__(row)
        self.command = command

    async def execute(self, statement):
        self.get_calls.append(("execute",))
        return Result(self.command)


def write_row(config=None):
    return connection_row(
        auth_method=commands_mod.WRITE_METHOD,
        config=config
        or {
            "host": "erp.example.com",
            "port": 443,
            "base_path": "/base",
            "interface": "odata_standard",
            "allowed_entity_sets": list(_ALLOWED_SETS),
        },
    )


def command_service(monkeypatch, *, gate: Gate, session) -> OneCCommandService:
    service = OneCCommandService(ledger=SimpleNamespace(), transport=AsyncMock())
    service._hosts.require_approved = AsyncMock(return_value="erp.example.com")
    monkeypatch.setattr(commands_mod, "_authorize", AsyncMock())
    monkeypatch.setattr(commands_mod, "is_onec_write_capability_enabled", lambda *a, **k: (True, None))
    return service


async def test_write_loader_defaults_to_propose_capability(monkeypatch):
    gate = Gate(monkeypatch, commands_mod, role="manager")
    session = RecordingSession(
        write_row(
            {
                "host": "erp.example.com",
                "port": 443,
                "base_path": "/base",
                "interface": "odata_standard",
                "allowed_entity_sets": list(_ALLOWED_SETS),
                "allowed_entity_sets_by_role": {"manager": ["Catalog_Номенклатура"]},
            }
        )
    )
    service = command_service(monkeypatch, gate=gate, session=session)

    (
        _endpoint,
        _credentials,
        _account_key,
        allowed_sets,
        _fields,
        _sha,
        _config,
    ) = await service._load_authorized_write_connection(
        session=session,
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        connection_id=CONNECTION_ID,
        capability=commands_mod.CAPABILITY_DOCUMENT_CREATE,
    )

    assert gate.calls[0]["capability"] == CAPABILITY_COMMAND_PROPOSE
    assert gate.trail[0] == ("authorize", CAPABILITY_COMMAND_PROPOSE)
    assert gate.trail[1] == ("role_allowlist", "manager")
    assert allowed_sets == frozenset({"Catalog_Номенклатура"})


async def test_write_loader_explicit_approve_capability(monkeypatch):
    gate = Gate(monkeypatch, commands_mod, role="owner")
    session = RecordingSession(write_row())
    service = command_service(monkeypatch, gate=gate, session=session)

    await service._load_authorized_write_connection(
        session=session,
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        connection_id=CONNECTION_ID,
        capability=commands_mod.CAPABILITY_CATALOG_UPDATE,
        policy_capability=CAPABILITY_COMMAND_APPROVE,
    )

    assert [call["capability"] for call in gate.calls] == [CAPABILITY_COMMAND_APPROVE]


async def test_write_propose_refusal_precedes_secret_read(monkeypatch):
    gate = Gate(monkeypatch, commands_mod, denial=ConnectionDenied())
    session = RecordingSession(write_row())
    service = command_service(monkeypatch, gate=gate, session=session)

    with pytest.raises(ConnectionDenied):
        await service._load_authorized_write_connection(
            session=session,
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            connection_id=CONNECTION_ID,
            capability=commands_mod.CAPABILITY_DOCUMENT_CREATE,
        )

    assert gate.credentials_opened == 0
    assert session.get_calls == []
    assert service._transport.await_count == 0


async def test_approve_command_requires_approve_capability_and_stops_before_ledger(monkeypatch):
    gate = Gate(monkeypatch, commands_mod, denial=ConnectionDenied())
    command = SimpleNamespace(
        id=99,
        capability=commands_mod.CAPABILITY_DOCUMENT_CREATE,
        connection_id=CONNECTION_ID,
        payload={"entity_set": "Catalog_Номенклатура"},
    )
    session = CommandSession(write_row(), command)
    service = command_service(monkeypatch, gate=gate, session=session)
    service._ledger = SimpleNamespace(approve_command=AsyncMock())

    with pytest.raises(ConnectionDenied):
        await service.approve_command(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            command_id=99,
            expected_payload_hash="sha256:" + "0" * 64,
            session=session,
        )

    assert gate.calls[0]["capability"] == CAPABILITY_COMMAND_APPROVE
    service._ledger.approve_command.assert_not_awaited()
    assert gate.credentials_opened == 0


async def test_execute_command_requires_approve_capability_and_stops_before_ledger(monkeypatch):
    gate = Gate(monkeypatch, commands_mod, denial=ConnectionDenied())
    command = SimpleNamespace(
        id=99,
        capability=commands_mod.CAPABILITY_DOCUMENT_CREATE,
        connection_id=CONNECTION_ID,
        payload={"entity_set": "Catalog_Номенклатура"},
    )
    session = CommandSession(write_row(), command)
    service = command_service(monkeypatch, gate=gate, session=session)
    service._ledger = SimpleNamespace(execute_command=AsyncMock())

    with pytest.raises(ConnectionDenied):
        await service._execute_command_in_session(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            command_id=99,
            now=None,
            session=session,
            resolver=None,
        )

    assert gate.calls[0]["capability"] == CAPABILITY_COMMAND_APPROVE
    service._ledger.execute_command.assert_not_awaited()
    service._transport.assert_not_awaited()


async def test_reconcile_command_requires_approve_capability_and_stops_before_network(monkeypatch):
    gate = Gate(monkeypatch, commands_mod, denial=ConnectionDenied())
    command = SimpleNamespace(
        id=99,
        capability=commands_mod.CAPABILITY_DOCUMENT_POST,
        connection_id=CONNECTION_ID,
        state="unknown",
        payload={"entity_set": "Document_РеализацияТоваровУслуг", "target_guid": "0" * 8, "external_ref": "SM1C:x"},
        target_refs=["onec:document:new"],
    )
    session = CommandSession(write_row(), command)
    service = command_service(monkeypatch, gate=gate, session=session)
    service._ledger = SimpleNamespace(reconcile_unknown=AsyncMock())

    with pytest.raises(ConnectionDenied):
        await service.reconcile_command(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            command_id=99,
            session=session,
            resolver=None,
        )

    assert gate.calls[0]["capability"] == CAPABILITY_COMMAND_APPROVE
    service._ledger.reconcile_unknown.assert_not_awaited()
    service._transport.assert_not_awaited()
    assert gate.credentials_opened == 0


async def test_write_loader_unknown_role_has_no_entities(monkeypatch):
    gate = Gate(monkeypatch, commands_mod, role="nobody")
    session = RecordingSession(
        write_row(
            {
                "host": "erp.example.com",
                "port": 443,
                "base_path": "/base",
                "interface": "odata_standard",
                "allowed_entity_sets": list(_ALLOWED_SETS),
                "allowed_entity_sets_by_role": {"owner": list(_ALLOWED_SETS)},
            }
        )
    )
    service = command_service(monkeypatch, gate=gate, session=session)

    (
        _endpoint,
        _credentials,
        _account_key,
        allowed_sets,
        _fields,
        _sha,
        _config,
    ) = await service._load_authorized_write_connection(
        session=session,
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        connection_id=CONNECTION_ID,
        capability=commands_mod.CAPABILITY_DOCUMENT_CREATE,
    )

    assert allowed_sets == frozenset()


def test_gate_error_codes_never_echo_provider_text():
    """A refusal carries a fixed code only; nothing from the provider reaches the caller."""
    assert OneCCommandError("write_connection_required").code == "write_connection_required"
    assert OneCQueryError("entity_not_allowed").code == "entity_not_allowed"
    assert str(ConnectionDenied()) == "connection_unavailable"


@pytest.mark.parametrize("role", ["owner", "admin"])
async def test_read_account_scope_denial_is_logged_without_opening_credentials(monkeypatch, caplog, role):
    gate = Gate(monkeypatch, query_mod, role=role)
    service = read_service(monkeypatch, gate=gate, session=RecordingSession(None))
    with pytest.raises(ConnectionDenied):
        await service.load_connection_context(tenant_id=TENANT_ID, actor_id=ACTOR_ID, connection_id=CONNECTION_ID)
    assert "action=onec.query reason_code=permission_denied" in caplog.text
    assert gate.credentials_opened == 0


@pytest.mark.parametrize("role", ["owner", "admin"])
async def test_write_account_scope_denial_is_logged_without_opening_credentials(monkeypatch, caplog, role):
    gate = Gate(monkeypatch, commands_mod, role=role)
    session = RecordingSession(None)
    service = command_service(monkeypatch, gate=gate, session=session)
    with pytest.raises(ConnectionDenied):
        await service._load_authorized_write_connection(
            session=session,
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            connection_id=CONNECTION_ID,
            capability=commands_mod.CAPABILITY_DOCUMENT_CREATE,
        )
    assert "action=onec.command.propose reason_code=permission_denied" in caplog.text
    assert gate.credentials_opened == 0


async def test_entity_scope_denial_is_logged_before_transport(monkeypatch, caplog):
    gate = Gate(monkeypatch, query_mod, role="analyst")
    config = {
        "host": "erp.example.com",
        "port": 443,
        "base_path": "/base",
        "interface": "odata_standard",
        "allowed_entity_sets": list(_ALLOWED_SETS),
        "allowed_entity_sets_by_role": {"analyst": []},
    }
    service = read_service(monkeypatch, gate=gate, row=connection_row(config=config))
    transport = AsyncMock()
    monkeypatch.setattr(query_mod, "fetch_odata_bytes", transport)
    with pytest.raises(OneCQueryError, match="entity_not_allowed"):
        await service.execute_query(
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            connection_id=CONNECTION_ID,
            plan=query_mod.OneCQueryPlan(entity_set="Catalog_Номенклатура", select_fields=("Ref_Key",)),
            catalog=CATALOG,
        )
    assert "action=onec.query reason_code=permission_denied" in caplog.text
    transport.assert_not_awaited()
    assert service.list_history_metadata(tenant_id=TENANT_ID) == ()
