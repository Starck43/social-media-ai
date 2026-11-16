"""Isolated synthetic PostgreSQL tests for the owner bootstrap inventory."""

import json
import secrets

import pytest
import sqlalchemy as sa

from app.core.config import settings
from app.core.database import engine
from app.models.managers.tenant_manager import tenant_channels, tenant_users, tenants
from scripts.ops.inventory_owner_bootstrap import InventoryError, inventory, main
from tests.collection.test_source_owner import workspace  # noqa: F401


def run_report(tenant, allowed, **kwargs):
    with engine.begin() as conn:
        conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return inventory(
            conn, schema=settings.DB_SCHEMA, tenant_id=tenant.id, bootstrap_slug=tenant.slug, allowed=allowed, **kwargs
        )


async def test_inventory_scope_allowlist_paging_redaction_and_read_only(workspace, capsys, monkeypatch):
    first_id = 1000000000 + secrets.randbelow(500000000)
    second_id = first_id + 1
    absent_id = first_id + 2
    other = await tenants.create(name="Other bootstrap", slug=f"bootstrap{secrets.token_hex(4)}")
    try:
        a = await tenant_users.add_member(
            tenant_id=workspace.id, channel="telegram", external_user_id=str(first_id), role="owner"
        )
        b = await tenant_users.add_member(
            tenant_id=workspace.id, channel="max", external_user_id=str(second_id), role="viewer"
        )
        await tenant_users.update_by_id(b.id, is_active=False)
        foreign = await tenant_users.add_member(
            tenant_id=other.id, channel="telegram", external_user_id=str(absent_id), role="owner"
        )
        binding_a = await tenant_channels.bind(
            tenant_id=workspace.id, channel="telegram", chat_id="secret-chat-" + str(first_id)
        )
        binding_b = await tenant_channels.bind(
            tenant_id=workspace.id, channel="max", chat_id="secret-chat-" + str(second_id)
        )
        await tenant_channels.update_by_id(binding_b.id, is_active=False)
        allowed = {"telegram": [first_id, absent_id], "max": [second_id]}
        page1 = run_report(workspace, allowed, limit=1)
        page2 = run_report(
            workspace,
            allowed,
            limit=1,
            member_after=page1["next_member_after"],
            binding_after=page1["next_binding_after"],
        )
        assert [page1["members"][0]["membership_id"], page2["members"][0]["membership_id"]] == [a.id, b.id]
        assert [page1["bindings"][0]["binding_id"], page2["bindings"][0]["binding_id"]] == [binding_a.id, binding_b.id]
        assert page2["next_member_after"] is None and page2["next_binding_after"] is None
        assert page1["members"][0]["role"] == "owner" and page1["members"][0]["allowlisted_on_this_instance"]
        assert not page2["members"][0]["active"] and not page2["bindings"][0]["active"]
        assert page1["instance_allowlist"]["telegram"] == {"configured_count": 2, "without_membership_count": 1}
        assert page1["instance_allowlist"]["max"] == {"configured_count": 1, "without_membership_count": 0}
        rendered = json.dumps([page1, page2])
        assert all(str(value) not in rendered for value in (first_id, second_id, absent_id))
        assert "secret-chat" not in rendered and foreign.id not in [m["membership_id"] for m in page1["members"]]
        monkeypatch.setattr(settings, "DEFAULT_TENANT_SLUG", f"  {workspace.slug.upper()}  ")
        monkeypatch.setattr(settings, "TELEGRAM_OWNER_IDS", f"{first_id},{absent_id}")
        monkeypatch.setattr(settings, "MAX_OWNER_ID", str(second_id))
        assert main(["--target-approved", "--tenant-id", str(workspace.id)]) == 0
        output = capsys.readouterr()
        assert str(first_id) not in output.out and "secret-chat" not in output.out
        assert json.loads(output.out)["tenant_id"] == workspace.id
        with engine.begin() as conn:
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            with pytest.raises(sa.exc.DBAPIError), conn.begin_nested():
                conn.execute(
                    sa.text(f'UPDATE "{settings.DB_SCHEMA}".tenant_users SET is_active=false WHERE id=:id'),
                    {"id": a.id},
                )
    finally:
        await tenants.delete_by_id(other.id)


def test_refuses_unapproved_invalid_target_and_writable():
    for args in (
        ["--tenant-id", "1"],
        ["--target-approved", "--tenant-id", "0"],
        ["--target-approved", "--tenant-id", "1", "--limit", "201"],
    ):
        with pytest.raises(SystemExit):
            main(args)
    with engine.begin() as conn:
        with pytest.raises(InventoryError, match="read-only"):
            inventory(conn, schema=settings.DB_SCHEMA, tenant_id=1, bootstrap_slug="bad", allowed={})
    with engine.begin() as conn:
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        with pytest.raises(InventoryError, match="configured bootstrap"):
            inventory(conn, schema=settings.DB_SCHEMA, tenant_id=1, bootstrap_slug="nonexistent-synthetic", allowed={})


def test_failure_is_redacted(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise RuntimeError("secret-test-exception")

    monkeypatch.setattr(engine, "begin", fail)
    assert main(["--target-approved", "--tenant-id", "1"]) == 1
    captured = capsys.readouterr()
    assert "secret-test-exception" not in captured.out + captured.err
    assert "did not complete" in captured.err


def test_logging_refused(monkeypatch):
    with engine.begin() as conn:
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        monkeypatch.setattr(engine, "echo", True)
        with pytest.raises(InventoryError, match="logging"):
            inventory(conn, schema=settings.DB_SCHEMA, tenant_id=1, bootstrap_slug="owner", allowed={})


async def test_allowlist_bound_refuses_excess(workspace):
    with pytest.raises(InventoryError, match="bound"):
        run_report(workspace, {"telegram": list(range(1001))})
