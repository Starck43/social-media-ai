"""Read-only metadata inventory against isolated synthetic PostgreSQL only."""

import json
import secrets

import pytest
import sqlalchemy as sa

from app.core.config import settings
from app.core.database import engine
from app.core.tenant_context import tenant_scope
from app.models import Platform, Source, User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.models.managers.user_credential_manager import user_credentials
from app.types import SourceType
from scripts.ops.inventory_source_credentials import InventoryError, inventory, main
from tests.collection.test_source_owner import make_user, workspace  # noqa: F401


def page(tenant_id, **kwargs):
    with engine.begin() as conn:
        conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return inventory(conn, schema=settings.DB_SCHEMA, tenant_id=tenant_id, **kwargs)


async def test_scoped_inventory_redacted_paged_and_read_only(workspace, make_user, capsys):
    member, outsider, inactive = await make_user(), await make_user(), await make_user()
    memberships = TenantUserManager()
    await memberships.add_web_member(tenant_id=workspace.id, user_id=member.id, role="member")
    await memberships.add_web_member(tenant_id=workspace.id, user_id=inactive.id, role="member")
    await User.objects.update_by_id(inactive.id, is_active=False)
    await user_credentials.store(user_id=member.id, platform="vk", kind="user_token", secret="fake-vault-secret")
    await user_credentials.store(user_id=outsider.id, platform="vk", kind="user_token", secret="foreign-fake-secret")
    await user_credentials.store(user_id=member.id, platform="telegram", kind="api_id", secret="fake-partial")
    other = await tenants.create(name="Other synthetic", slug=f"inventory{secrets.token_hex(4)}")
    sources = []
    try:
        vk = await Platform.objects.filter(platform_type="vk").first()
        tg = await Platform.objects.filter(platform_type="telegram").first()
        assert vk and tg

        async def add(tenant_id, platform, params, owner=None):
            with tenant_scope(bypass=True):
                return await Source.objects.create(
                    tenant_id=tenant_id,
                    platform_id=platform.id,
                    source_type=SourceType.CHANNEL,
                    external_id=secrets.token_hex(8),
                    name="DO-NOT-PRINT-source-name",
                    params=params,
                    owner_user_id=owner,
                    is_active=True,
                )

        sources.append(await add(workspace.id, vk, {"mode": "api", "hidden": "DO-NOT-PRINT-param"}, member.id))
        sources.append(await add(workspace.id, vk, {"token_owner": outsider.id}, member.id))
        sources.append(await add(workspace.id, tg, {"token_owner": {"secret": "DO-NOT-PRINT-owner"}}, member.id))
        sources.append(await add(workspace.id, tg, {}, inactive.id))
        sources.append(await add(workspace.id, tg, {"mode": "DO-NOT-PRINT-mode"}))
        sources.append(await add(workspace.id, tg, {"mode": "user"}, member.id))
        sources.append(await add(other.id, vk, {}, member.id))
        queries = []

        def record(conn, cursor, statement, parameters, context, executemany):
            if "user_credentials" in statement:
                queries.append((statement, parameters))

        sa.event.listen(engine, "before_cursor_execute", record)
        try:
            first = page(workspace.id, limit=2)
            second = page(workspace.id, limit=2, after_id=first["next_after_id"])
            third = page(workspace.id, limit=2, after_id=second["next_after_id"])
        finally:
            sa.event.remove(engine, "before_cursor_execute", record)
        items = first["sources"] + second["sources"] + third["sources"]
        assert [item["source_id"] for item in items] == [s.id for s in sources[:6]]
        assert third["next_after_id"] is None
        assert [item["owner_status"] for item in items] == [
            "active_member",
            "not_active_workspace_member",
            "invalid_or_ambiguous",
            "account_inactive",
            "ownerless",
            "active_member",
        ]
        assert items[0]["personal_material"] == "metadata_complete_unverified"
        assert all(item["owner_user_id"] is None and item["personal_material"] is None for item in items[1:5])
        assert items[4]["mode"] == "unknown"
        assert items[5]["personal_material"] == "partial_or_missing"
        for statement, parameters in queries:
            assert "secret_encrypted" not in statement and "meta" not in statement
            assert outsider.id not in parameters["owners"] and inactive.id not in parameters["owners"]
        rendered = json.dumps(items)
        assert "DO-NOT-PRINT" not in rendered and "fake-vault-secret" not in rendered
        assert main(["--target-approved", "--tenant-id", str(workspace.id)]) == 0
        output = capsys.readouterr()
        assert "DO-NOT-PRINT" not in output.out and "fake-vault-secret" not in output.out
        assert json.loads(output.out)["tenant_id"] == workspace.id
        with engine.begin() as conn:
            conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            with pytest.raises(sa.exc.DBAPIError), conn.begin_nested():
                conn.execute(
                    sa.text(f'UPDATE "{settings.DB_SCHEMA}".sources SET is_active = false WHERE id = :id'),
                    {"id": sources[0].id},
                )
    finally:
        await tenants.delete_by_id(other.id)


def test_refuses_unapproved_invalid_and_writable(capsys):
    for args in (
        ["--tenant-id", "1"],
        ["--target-approved", "--tenant-id", "0"],
        ["--target-approved", "--tenant-id", "1", "--limit", "201"],
    ):
        with pytest.raises(SystemExit):
            main(args)
    with engine.begin() as conn:
        with pytest.raises(InventoryError, match="read-only"):
            inventory(conn, schema=settings.DB_SCHEMA, tenant_id=1)


def test_failure_does_not_echo_exception(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise RuntimeError("DO-NOT-PRINT-exception-secret")

    monkeypatch.setattr(engine, "begin", fail)
    assert main(["--target-approved", "--tenant-id", "1"]) == 1
    output = capsys.readouterr()
    assert "DO-NOT-PRINT" not in output.err + output.out
    assert "did not complete" in output.err


def test_sql_logging_disallowed(monkeypatch):
    with engine.begin() as conn:
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        monkeypatch.setattr(engine, "echo", True)
        with pytest.raises(InventoryError, match="logging"):
            inventory(conn, schema=settings.DB_SCHEMA, tenant_id=1)
