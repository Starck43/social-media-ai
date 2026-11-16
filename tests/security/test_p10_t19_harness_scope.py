"""P10-T19: the bootstrap grant is a test fixture, not a rights bypass.

`tests/conftest.py` runs legacy suites inside the bootstrap workspace and, for
those same suites, inside `operator_permission_scope()`. A security/tenancy test
marks itself out of that story; it must then observe the real fail-closed
contract of `has_permission`/`has_role` instead of the grant, which answers
`True` before any identity check.
"""

from __future__ import annotations

import pytest

from app.core import permissions as perms
from app.models import User
from app.types import PlatformRoleType

pytestmark = pytest.mark.tenancy


def test_operator_grant_is_not_in_force_for_a_tenancy_test() -> None:
    assert perms._operator_grant.get() is False
    # The enclosing autouse fixture already removed the data bypass; nothing may
    # hand it back for a security suite.
    assert perms.is_bypass() is False


def test_identity_is_required_once_the_grant_is_gone() -> None:
    from app.core.tenant_context import tenant_scope

    actor = User(id=7, username="p19-actor", email="p19@example.invalid", is_active=True)
    with tenant_scope(11), perms.permission_scope(None):
        # A workspace call needs an identity and a membership answer; the
        # bootstrap bypass alone is data access, never a grant.
        assert perms.has_permission(None, "source", "delete") is False
        assert perms.has_role(None, PlatformRoleType.ADMIN) is False
    # An identity is not authority either: outside a workspace the account's
    # own role decides, and this one carries no grants.
    with perms.permission_scope(actor):
        assert perms.has_permission(actor, "source", "delete") is False


def test_legacy_suite_still_gets_the_bootstrap_grant(request, monkeypatch) -> None:
    """The other half of the fixture: an unmarked suite keeps the grant.

    The autouse fixture already ran for this test, so its effect is checked the
    way the fixture itself is applied: `permission_scope` clears an inherited
    grant, and only the bootstrap wrapper puts it back for the next test.
    """
    assert perms._operator_grant.get() is False
    from app.core.tenant_context import tenant_scope

    with perms.operator_permission_scope():
        # Inside the bootstrap wrapper a legacy arrange needs no actor...
        assert perms.has_permission(None, "source", "delete") is True
        # ...while an interactive scope still clears the inherited grant.
        with tenant_scope(11), perms.permission_scope(None):
            assert perms.has_permission(None, "source", "delete") is False
