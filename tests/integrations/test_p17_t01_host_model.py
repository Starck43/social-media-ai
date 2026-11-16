"""DB-exempt schema contract; migration execution is deferred to the local runner."""

from sqlalchemy import CheckConstraint, UniqueConstraint

from app.models.integrations.workspace_outbound_host import WorkspaceOutboundHost


def test_approval_is_tenant_scoped_and_unique_per_workspace():
    table = WorkspaceOutboundHost.__table__
    assert WorkspaceOutboundHost.__tenant_scoped__
    assert not table.c.tenant_id.nullable
    assert any(
        isinstance(constraint, UniqueConstraint) and {col.name for col in constraint.columns} == {"tenant_id", "host"}
        for constraint in table.constraints
    )
    assert any(
        isinstance(constraint, CheckConstraint) and "host ~" in str(constraint.sqltext)
        for constraint in table.constraints
    )


def test_approval_retains_responsible_user_and_does_not_store_credentials():
    table = WorkspaceOutboundHost.__table__
    assert not table.c.approved_by_user_id.nullable
    assert next(iter(table.c.approved_by_user_id.foreign_keys)).ondelete == "RESTRICT"
    assert set(table.c.keys()) == {"id", "tenant_id", "host", "approved_by_user_id", "created_at"}
