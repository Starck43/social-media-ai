"""Parent-owned migration cycle on the already prepared disposable schema."""

import asyncio
import os
import runpy
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
assert os.environ.get("DB_SCHEMA") == "p17_t05_validation"
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, select, text

from app.core.database import async_engine, async_session_maker, engine
from app.core.tenant_context import tenant_scope
from app.models import ActionAuditEvent, Tenant, User
from scripts.ci.schema_inventory import snapshot

S = "p17_t05_validation"
cfg = Config("alembic.ini")
migration = runpy.run_path("migrations/versions/p17t05_onec_audit_export.py")


def migrate(*, down=False):
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        if down:
            command.downgrade(cfg, "0155")
        else:
            command.upgrade(cfg, "p17t05_onec_audit_export")
    cfg.attributes.pop("connection", None)


def metadata(export):
    with engine.connect() as conn:
        revision = conn.execute(text(f"select version_num from {S}.alembic_version")).scalar_one()
        assert revision == ("p17t05_onec_audit_export" if export else "0155")
        checks = conn.execute(
            text(
                "select conname, pg_get_constraintdef(oid) from pg_constraint "
                "where conrelid = CAST(:table AS regclass) and contype='c'"
            ),
            {"table": S + ".action_audit_events"},
        ).all()
        owned = [
            definition
            for name, definition in checks
            if name
            in {
                "ck_action_audit_events_action",
                "ck_action_audit_events_resource_kind",
                "ck_action_audit_events_reason_code",
            }
        ]
        assert len(owned) == 3
        assert all(("onec.audit_export" in definition) == export for definition in owned)
        resources = conn.execute(
            text(
                f"select resource_key, model_name, table_name from {S}.model_types "
                "where resource_key in ('onec.data','onec.command') order by resource_key"
            )
        ).all()
        if not export:
            assert resources == []
            return
        assert {tuple(row) for row in resources} == {tuple(row[:3]) for row in migration["CAPABILITY_RESOURCES"]}
        rows = conn.execute(
            text(
                f"select wr.codename, m.resource_key, p.action_type::text "
                f"from {S}.workspace_role_permission l join {S}.workspace_roles wr on wr.id=l.role_id "
                f"join {S}.permissions p on p.id=l.permission_id join {S}.model_types m on m.id=p.model_type_id "
                "where m.resource_key in ('onec.data','onec.command')"
            )
        ).all()
        actual = {(r, k, a) for r, k, a in rows}
        expected = {(r, k, a) for r, pairs in migration["CAPABILITY_GRANTS"].items() for k, a in pairs}
        assert actual == expected
        actions = conn.execute(
            text(
                f"select m.resource_key,p.action_type::text from {S}.permissions p "
                f"join {S}.model_types m on m.id=p.model_type_id "
                "where m.resource_key in ('onec.data','onec.command')"
            )
        ).all()
        assert {tuple(row) for row in actions} == {
            (k, a) for k, _, _, acts in migration["CAPABILITY_RESOURCES"] for a in acts
        }


metadata(True)
migrate(down=True)
metadata(False)
migrate()
metadata(True)
print("PASS migration upgrade/empty downgrade/upgrade; three CHECKs and exact capability permissions/presets")


async def evidence():
    async with async_session_maker() as session, session.begin():
        role = (await session.execute(text(f"select id from {S}.roles where codename='user'"))).scalar_one()
        tag = uuid.uuid4().hex[:8]
        user = User(
            username="p17migration" + tag,
            email="p17migration" + tag + "@example.invalid",
            hashed_password="disabled",
            role_id=role,
        )
        session.add(user)
        await session.flush()
        tenant = Tenant(name="P17 migration synthetic", slug="p17-migration-" + tag, plan_holder_user_id=user.id)
        session.add(tenant)
        await session.flush()
        uid, tid = user.id, tenant.id
        with tenant_scope(tid):
            await ActionAuditEvent.objects.record_success(
                session=session,
                actor_kind="user",
                actor_id=uid,
                action="onec.audit_export",
                resource_kind="workspace",
                resource_id=tid,
            )
    return uid, tid


uid, tid = asyncio.run(evidence())
before = snapshot(S)
try:
    migrate(down=True)
except ValueError as exc:
    assert str(exc) == "onec_audit_downgrade_requires_expired_events"
else:
    raise AssertionError("Nonempty downgrade succeeded")
assert snapshot(S) == before
metadata(True)
print("PASS refused downgrade; schema/head/export evidence preserved")


async def expire():
    with tenant_scope(tid):
        removed = await ActionAuditEvent.objects.delete_expired(now=datetime.now(timezone.utc) + timedelta(days=100))
        assert removed == 1
    async with async_session_maker() as session, session.begin():
        assert not (await session.execute(select(ActionAuditEvent.id).where(ActionAuditEvent.tenant_id == tid))).all()
        await session.execute(delete(Tenant).where(Tenant.id == tid))
        await session.execute(delete(User).where(User.id == uid))
    await async_engine.dispose()


asyncio.run(expire())
migrate(down=True)
metadata(False)
migrate()
metadata(True)
print("PASS supported retention cleanup; empty downgrade/re-upgrade; one task head; synthetic cycle rows absent")
engine.dispose()
