import pytest

from app.core.access_catalog import workspace_grants
from app.core.permissions import member_decision, permission_scope
from app.core.tenant_context import tenant_scope
from app.models import User
from app.types import ActionType, PlatformRoleType

OWNER = workspace_grants("owner")
ADMIN = workspace_grants("admin")
MANAGER = workspace_grants("manager")
VIEWER = workspace_grants("viewer")


def _actor(role: str) -> User:
    return User(id=901, username=f"p19-{role}", email="p19@example.invalid", is_active=True)


@pytest.mark.parametrize(
    "grant,model,action,expected",
    [
        (OWNER, "source", ActionType.DELETE, True),
        (OWNER, "agenttask", ActionType.CREATE, True),
        (OWNER, "agentscenario", ActionType.CREATE, True),
        (ADMIN, "agenttask", ActionType.CREATE, True),
        (MANAGER, "source", ActionType.CREATE, True),
        (MANAGER, "aianalytics", ActionType.VIEW, True),
        (MANAGER, "agentscenario", ActionType.DELETE, True),
        (MANAGER, "aianalytics", ActionType.DELETE, False),
        (VIEWER, "source", ActionType.VIEW, True),
        (VIEWER, "source", ActionType.CREATE, False),
        (MANAGER, "llmprovider", ActionType.CONFIGURE, False),  # personal/account scope, never a preset
    ],
)
def test_workspace_presets_decide_the_workspace_models(grant, model, action, expected) -> None:
    actor = _actor("member")
    with tenant_scope(11), permission_scope(actor, member_grants=grant):
        assert member_decision(actor, model, action) is expected


def test_without_a_preset_the_platform_role_answers() -> None:
    """A scope that states no membership rights abstains; it never denies.

    `member_decision` answers "what does this *membership* allow". With no
    preset there is no membership question, so it must return None and let the
    platform role decide — otherwise every platform grant on a workspace model
    (the CLI, a worker, a source-only double) turns into a refusal.
    """
    actor = _actor("nopreset")
    with tenant_scope(11), permission_scope(actor):
        assert member_decision(actor, "source", ActionType.VIEW) is None


def test_the_decision_is_scoped_to_the_tenant_it_was_taken_in() -> None:
    """The preset is bound to the tenant active when the scope opened."""
    actor = _actor("other")
    with tenant_scope(11), permission_scope(actor, member_grants=OWNER):
        assert member_decision(actor, "source", ActionType.DELETE) is True
        with tenant_scope(12):
            assert member_decision(actor, "source", ActionType.DELETE) is False
        # A different user id never borrows this membership, even in the tenant.
        stranger = User(id=903, username="p19-stranger", email="p19@example.invalid", is_active=True)
        assert member_decision(stranger, "source", ActionType.DELETE) is False


def test_a_different_identity_cannot_borrow_the_preset() -> None:
    """The preset names one user id; another caller is refused outright."""
    actor = _actor("holder")
    other = User(id=902, username="p19-intruder", email="p19@example.invalid", is_active=True)
    with tenant_scope(11), permission_scope(actor, member_grants=OWNER):
        assert member_decision(other, "source", ActionType.DELETE) is False


def test_platform_roles_are_not_workspace_roles() -> None:
    """The enum the old fixtures looked names up in has no VIEWER/MANAGER."""
    assert {r.value for r in PlatformRoleType} == {"user", "operator", "admin", "superuser"}
