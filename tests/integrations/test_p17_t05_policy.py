"""DB-exempt OneC policy checks; authority boundaries use explicit mock identities."""

from types import SimpleNamespace

import pytest

from app.core.access_catalog import resolve_permission_key, workspace_grants
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.services.onec.policy import CAPABILITY_PERMISSIONS, capability_allowed, log_denial, role_entity_allowlist


@pytest.mark.parametrize(
    "role,expected",
    [
        ("owner", set(CAPABILITY_PERMISSIONS)),
        ("admin", set(CAPABILITY_PERMISSIONS)),
        ("manager", {"onec.query", "onec.command.propose"}),
        ("analyst", {"onec.query"}),
        ("viewer", set()),
        ("unknown", set()),
    ],
)
def test_capabilities_use_workspace_presets(role, expected):
    user = SimpleNamespace(id=12, is_active=True)
    with tenant_scope(7), permission_scope(user, member_grants=workspace_grants(role)):
        actual = {cap for cap in CAPABILITY_PERMISSIONS if capability_allowed(cap, tenant_id=7, actor_id=12)}
    assert actual == expected
    assert workspace_grants("owner") == workspace_grants("admin")


@pytest.mark.parametrize("capability", CAPABILITY_PERMISSIONS)
def test_keys_share_the_registered_catalog(capability):
    resource, action = resolve_permission_key(capability)
    assert (resource.model_name, action) == CAPABILITY_PERMISSIONS[capability]


@pytest.mark.parametrize("tenant,actor,active", [(8, 12, True), (7, 99, True), (7, 12, False), (True, 12, True)])
def test_account_and_scope_fail_closed(tenant, actor, active):
    user = SimpleNamespace(id=12, is_active=active, has_perm_for=lambda *_: True)
    with tenant_scope(7), permission_scope(user, member_grants=workspace_grants("admin")):
        assert not capability_allowed("onec.query", tenant_id=tenant, actor_id=actor)


def test_unresolved_membership_cannot_inherit_platform_admin():
    user = SimpleNamespace(id=12, is_active=True, has_perm_for=lambda *_: True)
    with tenant_scope(7), permission_scope(user):
        assert not capability_allowed("onec.query", tenant_id=7, actor_id=12)
    with tenant_scope(bypass=True), permission_scope(user, member_grants=workspace_grants("owner")):
        assert not capability_allowed("onec.query", tenant_id=7, actor_id=12)


@pytest.mark.parametrize(
    "selected,expected",
    [
        (["Catalog_Items", "Catalog_Foreign"], {"Catalog_Items"}),
        ([], set()),
        (None, set()),
        ("Catalog_Items", set()),
    ],
)
def test_entity_permissions_only_narrow_connection(selected, expected):
    config = {
        "allowed_entity_sets": ["Catalog_Items", "Document_Order"],
        "allowed_entity_sets_by_role": {"manager": selected},
    }
    assert role_entity_allowlist(config, "manager") == expected
    assert role_entity_allowlist(config, "viewer") == frozenset()


def test_optional_role_config_preserves_existing_connection_contract():
    config = {"allowed_entity_sets": ["Catalog_Items"]}
    assert role_entity_allowlist(config, "admin") == {"Catalog_Items"}
    assert role_entity_allowlist(config, "owner") == {"Catalog_Items"}
    assert not role_entity_allowlist(config, "unresolved")


def test_denial_has_only_source_owned_fields(caplog):
    log_denial(tenant_id=7, actor_id=12, capability="onec.query")
    assert "outcome=denied" in caplog.text
    assert "permission_denied" in caplog.text
    caplog.clear()
    log_denial(tenant_id=7, actor_id=12, capability="secret-password-or-payload")
    assert not caplog.text


@pytest.mark.parametrize(
    "role,propose,allowed",
    [("manager", True, True), ("manager", False, False), ("admin", False, True), ("analyst", True, False)],
)
async def test_ledger_separates_proposal_and_approval(role, propose, allowed):
    from app.services.commands.ledger import CommandDenied, _verify_member_grant
    from app.services.commands.registry import get_capability

    values = iter([12, 7, SimpleNamespace(role_id=3), role])

    async def execute(_statement):
        return SimpleNamespace(scalar_one_or_none=lambda: next(values))

    operation = _verify_member_grant(
        SimpleNamespace(execute=execute), 7, 12, get_capability("onec.document.create.v1"), propose=propose
    )
    if allowed:
        await operation
    else:
        with pytest.raises(CommandDenied):
            await operation


@pytest.mark.parametrize(
    "role,capability,allowed",
    [
        ("admin", "onec.command.approve", True),
        ("manager", "onec.command.propose", True),
        ("analyst", "onec.export", False),
    ],
)
async def test_execution_reloads_role_instead_of_trusting_stale_scope(monkeypatch, role, capability, allowed):
    from unittest.mock import AsyncMock

    from app.models.managers import workspace_connection_manager
    from app.models.managers.workspace_connection_manager import ConnectionDenied
    from app.services.onec.policy import authorize_capability

    monkeypatch.setattr(workspace_connection_manager, "_authorize", AsyncMock())

    async def execute(statement):
        assert "workspace_roles" in str(statement) and "tenant_users" in str(statement)
        return SimpleNamespace(scalar_one_or_none=lambda: role)

    operation = authorize_capability(
        tenant_id=7, actor_id=12, capability=capability, session=SimpleNamespace(execute=execute)
    )
    if allowed:
        assert await operation == role
    else:
        with pytest.raises(ConnectionDenied):
            await operation
