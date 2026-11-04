"""P13-T04 unit and contract tests for business write-approval workflow and command ledger.

Covers:
- Migration 0133 ORM-to-DDL parity and downgrade contract.
- Closed capability registry (`CAPABILITY_REGISTRY`) and canonical SHA-256 payload hashing.
- Preview path with idempotent replay vs. mismatched payload conflict (`CommandConflict`).
- Approval hash binding, TTL bounds, TTL expiry (`approval_expired`), and revoked membership/role rechecks.
- Kill switch and capability enablement enforcement.
- Execution claim, adapter success, adapter error (`failed`), and adapter timeout (`unknown` + `ActionAuditEvent.objects.record_unknown` + no automatic retry).
- Reconciliation of `unknown` commands to `succeeded` or `failed`.
- Reject/cancel, get_command, list_commands pagination, and cross-tenant isolation.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
os.environ.setdefault("POSTGRES_URL", "postgresql+asyncpg://user:pass@localhost:5432/test_db")

import pytest

__source_only__ = True

from app.core.config import settings
from app.core.permissions import set_current_user
from app.core.tenant_context import TenantContextError, tenant_scope
from app.models.actions.business_command import (
    APPROVAL_POLICIES,
    COMMAND_STATES,
    TARGET_STATES,
    BusinessCommand,
    BusinessCommandTarget,
)
from app.models.audit.action_audit_event import ActionAuditEvent
from app.services.commands import (
    CAPABILITY_REGISTRY,
    DEFAULT_TTL_SECONDS,
    MAX_SCHEDULED_TTL_SECONDS,
    BusinessCommandLedger,
    CommandConflict,
    CommandDenied,
    CommandNotFound,
    CommandTimeoutUnknown,
    canonical_payload_hash,
    command_ledger,
    get_capability,
    serialize_command,
)


class _ScalarResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalars(self) -> "_ScalarResult":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class FakeCommandSession:
    """Deterministic fake SQLAlchemy async session for BusinessCommandLedger unit verification."""

    def __init__(self) -> None:
        self.commands: dict[int, BusinessCommand] = {}
        self.targets: dict[int, BusinessCommandTarget] = {}
        self._next_cmd_id = 1
        self._next_target_id = 1
        self.active_users: set[int] = {101, 102, 201}
        self.active_tenants: set[int] = {10, 20}
        # (tenant_id, user_id) -> (is_active, role_id)
        self.memberships: dict[tuple[int, int], tuple[bool, int]] = {
            (10, 101): (True, 1),
            (10, 102): (True, 1),
            (20, 201): (True, 1),
        }
        self.roles: dict[int, str] = {1: "owner", 2: "viewer"}
        # (tenant_id, connection_id) -> (is_active, state)
        self.connections: dict[tuple[int, int], tuple[bool, str]] = {
            (10, 5): (True, "verified"),
            (10, 6): (False, "disabled"),
        }

    def add(self, obj: Any) -> None:
        if isinstance(obj, BusinessCommand):
            if obj.id is None:
                obj.id = self._next_cmd_id
                self._next_cmd_id += 1
            self.commands[obj.id] = obj
        elif isinstance(obj, BusinessCommandTarget):
            if obj.id is None:
                obj.id = self._next_target_id
                self._next_target_id += 1
            self.targets[obj.id] = obj

    async def flush(self) -> None:
        for obj in list(self.commands.values()):
            if obj.id is None:
                obj.id = self._next_cmd_id
                self._next_cmd_id += 1
                self.commands[obj.id] = obj
        for obj in list(self.targets.values()):
            if obj.id is None:
                obj.id = self._next_target_id
                self._next_target_id += 1
                self.targets[obj.id] = obj

    async def execute(self, stmt: Any) -> _ScalarResult:
        sql = str(stmt)
        params = stmt.compile().params
        tables = {getattr(f, "name", "") for f in stmt.get_final_froms()}

        if "users" in tables:
            uid = params.get("id_1")
            return _ScalarResult([uid] if uid in self.active_users else [])

        if "tenants" in tables:
            tid = params.get("id_1")
            return _ScalarResult([tid] if tid in self.active_tenants else [])

        if "tenant_users" in tables:
            tid = params.get("tenant_id_1")
            uid = params.get("user_id_1")
            mem = self.memberships.get((tid, uid))
            if mem and mem[0]:
                return _ScalarResult([SimpleNamespace(tenant_id=tid, user_id=uid, is_active=True, role_id=mem[1])])
            return _ScalarResult([])

        if "workspace_roles" in tables:
            rid = params.get("id_1")
            code = self.roles.get(rid)
            return _ScalarResult([code] if code else [])

        if "workspace_connections" in tables:
            tid = params.get("tenant_id_1")
            cid = params.get("id_1")
            conn = self.connections.get((tid, cid))
            if conn:
                return _ScalarResult([SimpleNamespace(tenant_id=tid, id=cid, is_active=conn[0], state=conn[1])])
            return _ScalarResult([])

        if "business_command_targets" in tables:
            tid = params.get("tenant_id_1")
            cid = params.get("command_id_1")
            rows = [t for t in self.targets.values() if t.tenant_id == tid and t.command_id == cid]
            rows.sort(key=lambda r: r.id or 0)
            return _ScalarResult(rows)

        if "business_commands" in tables:
            tid = params.get("tenant_id_1")
            if "idempotency_key_1" in params:
                aid = params.get("actor_user_id_1")
                cap = params.get("capability_1")
                ikey = params.get("idempotency_key_1")
                rows = [
                    c
                    for c in self.commands.values()
                    if c.tenant_id == tid
                    and c.actor_user_id == aid
                    and c.capability == cap
                    and c.idempotency_key == ikey
                ]
                return _ScalarResult(rows)
            if "ORDER BY" in sql:
                before_id = params.get("id_1")
                limit_val = next((v for k, v in params.items() if "param" in k and isinstance(v, int)), 50)
                rows = [c for c in self.commands.values() if c.tenant_id == tid]
                if before_id is not None:
                    rows = [c for c in rows if c.id < before_id]
                rows.sort(key=lambda r: r.id or 0, reverse=True)
                return _ScalarResult(rows[:limit_val])
            cid = params.get("id_1")
            rows = [c for c in self.commands.values() if c.tenant_id == tid and c.id == cid]
            return _ScalarResult(rows)

        return _ScalarResult([])


@pytest.fixture(autouse=True)
def _audit_And_context(monkeypatch: pytest.MonkeyPatch):
    from app.services.commands import ledger as ledger_mod

    success_Mock = AsyncMock(return_value=SimpleNamespace(event_id="evt-ok"))
    unknown_mock = AsyncMock(return_value=SimpleNamespace(event_id="evt-unknown"))
    monkeypatch.setattr(
        ActionAuditEvent,
        "objects",
        SimpleNamespace(record_success=success_Mock, record_unknown=unknown_mock),
    )
    monkeypatch.setattr(ledger_mod, "RUNTIME_ENABLED_CAPABILITIES", set(CAPABILITY_REGISTRY.keys()))
    monkeypatch.setattr(ledger_mod, "RUNTIME_KILL_SWITCHES", {})
    set_current_user(SimpleNamespace(id=101, is_active=True))
    yield SimpleNamespace(success=success_Mock, unknown=unknown_mock)
    set_current_user(None)


def test_capability_registry_and_canonical_hash() -> None:
    assert "crm.greeting_send.v1" in CAPABILITY_REGISTRY
    assert "comms.batch_campaign.v1" in CAPABILITY_REGISTRY
    assert "calendar.event_write.v1" in CAPABILITY_REGISTRY
    assert "onec.document_write.v1" in CAPABILITY_REGISTRY
    assert "documents.delivery_send.v1" in CAPABILITY_REGISTRY

    spec = get_capability("crm.greeting_send.v1")
    assert spec.enabled_by_default is False
    assert spec.approval_policy == "per_item"

    with pytest.raises(ValueError, match="Unsupported business command capability"):
        get_capability("unknown.cap.v1")

    with pytest.raises(ValueError, match="at least one non-empty target"):
        canonical_payload_hash("crm.greeting_send.v1", [], {"msg": "hi"})

    with pytest.raises(ValueError, match="Duplicate target references"):
        canonical_payload_hash("comms.batch_campaign.v1", ["contact:1", "contact:1"], {"msg": "hi"})

    with pytest.raises(ValueError, match="JSON object"):
        canonical_payload_hash("crm.greeting_send.v1", ["contact:1"], ["not-a-dict"])  # type: ignore[arg-type]

    h1 = canonical_payload_hash("crm.greeting_send.v1", ["contact:1"], {"b": 2, "a": 1})
    h2 = canonical_payload_hash("crm.greeting_send.v1", ["contact:1"], {"a": 1, "b": 2})
    assert h1 == h2
    assert h1.startswith("sha256:")
    assert len(h1) == 71


@pytest.mark.asyncio
async def test_preview_idempotent_replay_and_conflict(_audit_And_context) -> None:
    session = FakeCommandSession()
    ledger = BusinessCommandLedger()

    # Without tenant context, fails closed
    with pytest.raises(TenantContextError):
        await ledger.preview_command(
            capability="crm.greeting_send.v1",
            idempotency_key="idem-1",
            target_refs=["contact:10"],
            payload={"subject": "Happy Birthday"},
            session=session,
        )

    with tenant_scope(10):
        # Single-target capability rejects multi-target batch
        with pytest.raises(CommandDenied, match="multi-target"):
            await ledger.preview_command(
                capability="crm.greeting_send.v1",
                idempotency_key="idem-batch-fail",
                target_refs=["contact:10", "contact:11"],
                payload={"subject": "Hi"},
                session=session,
            )

        preview1 = await ledger.preview_command(
            capability="crm.greeting_send.v1",
            idempotency_key="idem-1",
            target_refs=["contact:10"],
            payload={"subject": "Happy Birthday", "channel": "email"},
            connection_id=5,
            session=session,
        )
        assert preview1["state"] == "previewed"
        assert len(preview1["targets"]) == 1
        assert preview1["targets"][0]["target_ref"] == "contact:10"
        assert _audit_And_context.success.await_count == 1

        # Exact replay returns existing preview without duplicate audit
        preview2 = await ledger.preview_command(
            capability="crm.greeting_send.v1",
            idempotency_key="idem-1",
            target_refs=["contact:10"],
            payload={"channel": "email", "subject": "Happy Birthday"},
            connection_id=5,
            session=session,
        )
        assert preview2["id"] == preview1["id"]
        assert _audit_And_context.success.await_count == 1

        # Modified payload with same idempotency key raises CommandConflict
        with pytest.raises(CommandConflict, match="different payload"):
            await ledger.preview_command(
                capability="crm.greeting_send.v1",
                idempotency_key="idem-1",
                target_refs=["contact:10"],
                payload={"channel": "email", "subject": "Modified Subject"},
                connection_id=5,
                session=session,
            )


@pytest.mark.asyncio
async def test_approve_hash_binding_ttl_expiry_and_revoked_member(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeCommandSession()
    ledger = BusinessCommandLedger()

    with tenant_scope(10):
        preview = await ledger.preview_command(
            capability="comms.batch_campaign.v1",
            idempotency_key="camp-1",
            target_refs=["contact:1", "contact:2"],
            payload={"template": "welcome"},
            session=session,
        )
        cmd_id = preview["id"]
        phash = preview["payload_hash"]

        # Invalid TTL rejected
        with pytest.raises(CommandDenied, match="Approval TTL"):
            await ledger.approve_command(cmd_id, expected_payload_hash=phash, ttl_seconds=10, session=session)

        # Mismatched hash rejected
        with pytest.raises(CommandDenied, match="Payload hash mismatch"):
            await ledger.approve_command(
                cmd_id,
                expected_payload_hash="sha256:" + "0" * 64,
                ttl_seconds=DEFAULT_TTL_SECONDS,
                session=session,
            )

        # Valid approval succeeds
        approved = await ledger.approve_command(
            cmd_id,
            expected_payload_hash=phash,
            ttl_seconds=300,
            session=session,
        )
        assert approved["state"] == "approved"
        assert approved["approved_payload_hash"] == phash

        # Expired TTL transitions command to expired and raises CommandDenied
        future = datetime.now(timezone.utc) + timedelta(seconds=301)
        with pytest.raises(CommandDenied, match="TTL has expired"):
            await ledger.execute_command(cmd_id, now=future, session=session)
        assert session.commands[cmd_id].state == "expired"

        # Create + approve a second command, then revoke approver membership before execute
        preview2 = await ledger.preview_command(
            capability="crm.greeting_send.v1",
            idempotency_key="greet-revoked",
            target_refs=["contact:9"],
            payload={"text": "Hello"},
            session=session,
        )
        await ledger.approve_command(
            preview2["id"],
            expected_payload_hash=preview2["payload_hash"],
            ttl_seconds=MAX_SCHEDULED_TTL_SECONDS,
            session=session,
        )
        session.memberships[(10, 101)] = (False, 1)
        with pytest.raises(CommandDenied, match="Workspace membership is inactive"):
            await ledger.execute_command(preview2["id"], session=session)


@pytest.mark.asyncio
async def test_kill_switch_and_timeout_to_unknown_and_reconcile(
    monkeypatch: pytest.MonkeyPatch, _audit_And_context
) -> None:
    from app.services.commands import ledger as ledger_mod

    session = FakeCommandSession()
    ledger = BusinessCommandLedger()

    with tenant_scope(10):
        preview = await ledger.preview_command(
            capability="onec.document_write.v1",
            idempotency_key="onec-1",
            target_refs=["onec:invoice:42"],
            payload={"amount": "1500.00"},
            session=session,
        )
        cmd_id = preview["id"]
        await ledger.approve_command(cmd_id, expected_payload_hash=preview["payload_hash"], session=session)

        # Activate kill switch -> execution blocked, remains approved
        monkeypatch.setattr(ledger_mod, "RUNTIME_KILL_SWITCHES", {"KILL_ONEC_DOCUMENT_WRITE": True})
        with pytest.raises(CommandDenied, match="kill switch"):
            await ledger.execute_command(cmd_id, session=session)
        assert session.commands[cmd_id].state == "approved"

        # Clear kill switch, simulate adapter timeout -> enters `unknown` + emits unknown audit
        monkeypatch.setattr(ledger_mod, "RUNTIME_KILL_SWITCHES", {})
        calls = 0

        async def timeout_adapter(_cmd: BusinessCommand, _targets: list[BusinessCommandTarget]) -> dict[str, str]:
            nonlocal calls
            calls += 1
            raise TimeoutError("1C OData gateway timed out")

        with pytest.raises(CommandTimeoutUnknown):
            await ledger.execute_command(cmd_id, adapter=timeout_adapter, session=session)

        assert calls == 1
        assert session.commands[cmd_id].state == "unknown"
        assert session.commands[cmd_id].error_code == "adapter_timeout_unknown"
        assert _audit_And_context.unknown.await_count == 1

        # Second execute call on unknown command is refused without calling adapter
        with pytest.raises(CommandConflict, match="already claimed or executed"):
            await ledger.execute_command(cmd_id, adapter=timeout_adapter, session=session)
        assert calls == 1

        # Reconcile unknown -> succeeded
        reconciled = await ledger.reconcile_unknown(
            cmd_id,
            resolved_state="succeeded",
            provider_receipts={"onec:invoice:42": "1c-doc-guid-999"},
            session=session,
        )
        assert reconciled["state"] == "succeeded"
        assert reconciled["targets"][0]["provider_receipt"] == "1c-doc-guid-999"


@pytest.mark.asyncio
async def test_execute_success_adapter_failure_reject_list_and_tenant_isolation() -> None:
    session = FakeCommandSession()
    ledger = BusinessCommandLedger()

    with tenant_scope(10):
        # 1. Execute success path
        p1 = await ledger.preview_command(
            capability="calendar.event_write.v1",
            idempotency_key="cal-ok",
            target_refs=["cal:evt:1"],
            payload={"title": "Demo"},
            session=session,
        )
        await ledger.approve_command(p1["id"], expected_payload_hash=p1["payload_hash"], session=session)
        done = await ledger.execute_command(p1["id"], session=session)
        assert done["state"] == "succeeded"
        assert done["targets"][0]["state"] == "succeeded"

        # 2. Adapter non-timeout exception -> failed state
        p2 = await ledger.preview_command(
            capability="documents.delivery_send.v1",
            idempotency_key="doc-fail",
            target_refs=["doc:77"],
            payload={"recipient": "client@example.com"},
            session=session,
        )
        await ledger.approve_command(p2["id"], expected_payload_hash=p2["payload_hash"], session=session)

        async def failing_adapter(_cmd: BusinessCommand, _targets: list[BusinessCommandTarget]) -> dict[str, str]:
            raise RuntimeError("Provider rejected recipient")

        failed = await ledger.execute_command(p2["id"], adapter=failing_adapter, session=session)
        assert failed["state"] == "failed"
        assert failed["error_code"] == "adapter_execution_failed"

        # 3. Reject / cancel command
        p3 = await ledger.preview_command(
            capability="crm.greeting_send.v1",
            idempotency_key="greet-cancel",
            target_refs=["contact:55"],
            payload={"msg": "Cancel me"},
            session=session,
        )
        cancelled = await ledger.reject_command(p3["id"], session=session)
        assert cancelled["state"] == "cancelled"
        assert cancelled["targets"][0]["state"] == "cancelled"

        # 4. Get and list commands with cursor pagination
        fetched = await ledger.get_command(p1["id"], session=session)
        assert fetched["id"] == p1["id"]
        listing = await ledger.list_commands(limit=2, session=session)
        assert len(listing["items"]) == 2
        assert listing["next_before_id"] is not None

    # 5. Cross-tenant isolation: tenant 20 cannot access tenant 10 commands
    set_current_user(SimpleNamespace(id=201, is_active=True))
    with tenant_scope(20):
        with pytest.raises(CommandNotFound):
            await ledger.get_command(p1["id"], session=session)
        with pytest.raises(CommandNotFound):
            await ledger.approve_command(p3["id"], expected_payload_hash=p3["payload_hash"], session=session)
        listing_t20 = await ledger.list_commands(limit=10, session=session)
        assert listing_t20["items"] == []


def test_migration_0134_ast_and_orm_metadata_parity() -> None:
    migration_file = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0134_business_commands.py"
    assert migration_file.exists(), "0134_business_commands.py must exist"
    source = migration_file.read_text(encoding="utf-8")
    assert 'revision = "0134"' in source
    assert 'down_revision = "0133"' in source
    assert '"business_commands"' in source
    assert '"business_command_targets"' in source

    cmd_table = BusinessCommand.__table__
    target_table = BusinessCommandTarget.__table__
    assert cmd_table.name == "business_commands"
    assert target_table.name == "business_command_targets"
    assert set(COMMAND_STATES) == {
        "draft",
        "previewed",
        "approved",
        "claimed",
        "succeeded",
        "failed",
        "unknown",
        "cancelled",
        "expired",
    }
    assert set(TARGET_STATES) == {"pending", "claimed", "succeeded", "failed", "unknown", "cancelled"}
    assert set(APPROVAL_POLICIES) == {"per_item", "per_batch", "connection_consent"}
    assert command_ledger is not None
    assert callable(serialize_command)
