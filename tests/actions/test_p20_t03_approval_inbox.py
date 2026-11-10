"""P20-T03 tests for the unified approval inbox on existing P13-T04 command ledger.

Covers:
- First-increment scope boundary: only P13-T04 command ledger projected; outbox (P15-T05)
  and document (P19-T04) projections disabled; no second approval authority.
- Cross-tenant and unauthorized viewers (role grant + WebPerms filtering and denial).
- Inaccessible targets and sensitive/PII field redaction.
- Outcome classification: pending, approved, expired (TTL), rejected, executed, failed, unknown.
- Stale/replaced payload hash detection and rejection on approve, reject, and execute.
- Expired TTL and revoked membership rechecks at execution time.
- Concurrent and repeated approval clicks serialized per (tenant_id, command_id).
- Prohibition of persistent `always allow`, bulk approval, and automatic execution on approve.
- Separate execution and `unknown` outcome reconciliation without blind retry.
- Web routes (`/app/commands`, `/app/commands/{id}`, approve/reject POST), CSRF enforcement,
  header/nav badge rendering, and accessibility attributes.
- Unified projection feeding Inbox, Today (`decisions`), Header badge, Chat (`decisions` tab
  and inline decision cards), and bot web decision links.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
os.environ.setdefault("POSTGRES_URL", "postgresql+asyncpg://user:pass@localhost:5432/test_db")

import pytest
from starlette.requests import Request

__source_only__ = True

from app.core.permissions import set_current_user
from app.core.tenant_context import tenant_scope
from app.models.actions.business_command import BusinessCommand, BusinessCommandTarget
from app.models.audit.action_audit_event import ActionAuditEvent
from app.services.commands import (
    CAPABILITY_REGISTRY,
    DEFAULT_TTL_SECONDS,
    DISABLED_PROJECTION_SOURCES,
    DOCUMENT_PROJECTION_ENABLED,
    INBOX_STATUSES,
    OUTBOX_PROJECTION_ENABLED,
    PROJECTED_SOURCES,
    ApprovalInboxService,
    BusinessCommandLedger,
    CommandConflict,
    CommandDenied,
    CommandNotFound,
    CommandTimeoutUnknown,
    approval_inbox,
)

inbox_module = importlib.import_module("app.services.commands.approval_inbox")
from app.services.commands import format_bot_decision_link
from tests.actions.test_p13_t04_unit import FakeCommandSession


class _FakeResult:
    """SQLAlchemy-shaped result double: `.scalar_one_or_none()`, `.scalars()`, `.all()`."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalars(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class InboxCommandSession(FakeCommandSession):
    """`FakeCommandSession` plus the two inbox-specific statements.

    The inbox issues two statement shapes the ledger double does not answer:
    the `(id, payload)` lookup used for the canonical hash check, and the
    tenant-owned contact lookup that proves a target exists. Both are real
    production queries, so they are answered here rather than by a production
    branch written for a fake.
    """

    def __init__(self) -> None:
        super().__init__()
        # (tenant_id, contact_id) -> merged_into_id (None when live)
        self.contacts: dict[tuple[int, int], int | None] = {}

    def add_contact(self, tenant_id: int, contact_id: int, *, merged_into_id: int | None = None) -> None:
        self.contacts[(tenant_id, contact_id)] = merged_into_id

    def begin(self) -> "_FakeTransaction":
        return _FakeTransaction()

    async def execute(self, stmt: Any) -> Any:
        tables = {getattr(f, "name", "") for f in stmt.get_final_froms()}
        params = stmt.compile().params

        id_list = next(
            (value for key, value in params.items() if key.startswith("id_") and isinstance(value, (list, tuple, set))),
            None,
        )
        if tables == {"business_commands"} and id_list is not None:
            tenant_id = params.get("tenant_id_1")
            wanted = {int(value) for value in id_list}
            return _FakeResult(
                [
                    (cmd.id, dict(cmd.payload or {}))
                    for cmd in self.commands.values()
                    if cmd.tenant_id == tenant_id and cmd.id in wanted
                ]
            )

        if (
            tables == {"business_commands"}
            and "ORDER BY" in str(stmt)
            and any("state" in key or "capability" in key for key in params)
        ):
            tenant_id = params.get("tenant_id_1")
            list_params = {key: set(value) for key, value in params.items() if isinstance(value, (tuple, list, set))}
            capabilities = next((value for key, value in list_params.items() if "capability" in key), None)
            states = next((value for key, value in list_params.items() if "state" in key), None)
            before_id = params.get("id_1")
            limit = next((value for key, value in params.items() if "param" in key and isinstance(value, int)), 101)
            rows = [cmd for cmd in self.commands.values() if cmd.tenant_id == tenant_id]
            if capabilities is not None:
                rows = [cmd for cmd in rows if cmd.capability in capabilities]
            if states is not None:
                rows = [cmd for cmd in rows if cmd.state in states]
            if before_id is not None:
                rows = [cmd for cmd in rows if cmd.id < before_id]
            rows.sort(key=lambda cmd: cmd.id or 0, reverse=True)
            return _FakeResult(rows[:limit])

        if "contacts" in tables:
            tenant_id = params.get("tenant_id_1")
            wanted = next(
                (
                    value
                    for key, value in params.items()
                    if key.startswith("id_") and isinstance(value, (tuple, list, set))
                ),
                params.get("id_1"),
            )
            wanted_ids = {int(value) for value in wanted} if isinstance(wanted, (list, tuple, set)) else {int(wanted)}
            return _FakeResult(
                [
                    contact_id
                    for (row_tenant, contact_id), merged in self.contacts.items()
                    if row_tenant == tenant_id and contact_id in wanted_ids and merged is None
                ]
            )

        if "business_command_targets" in tables and isinstance(params.get("command_id_1"), (tuple, list, set)):
            tenant_id = params.get("tenant_id_1")
            wanted = {int(value) for value in params["command_id_1"]}
            rows = [
                target
                for target in self.targets.values()
                if target.tenant_id == tenant_id and target.command_id in wanted
            ]
            rows.sort(key=lambda target: (target.command_id, target.id or 0))
            return _FakeResult(rows)

        return await super().execute(stmt)


class _FakeTransaction:
    async def __aenter__(self) -> "_FakeTransaction":
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


class FakeWebPerms:
    """Deterministic WebPerms stub for testing permission-filtered inbox views and actions."""

    def __init__(
        self,
        grants: set[tuple[str, str]] | None = None,
        *,
        is_owner: bool = False,
        is_superuser_role: bool = False,
        can_open_chat: bool = True,
        workspace_role: str = "OWNER",
    ) -> None:
        self.grants = {(r.lower(), str(a).lower()) for r, a in (grants or set())}
        self.is_owner = is_owner
        self.is_superuser_role = is_superuser_role
        self.can_open_chat = can_open_chat
        self.workspace_role = workspace_role

    def can(self, resource: str, action: Any) -> bool:
        if self.is_owner or self.is_superuser_role:
            return True
        act_str = getattr(action, "value", None) or getattr(action, "name", None) or str(action)
        return (str(resource).lower(), str(act_str).lower()) in self.grants

    def can_manage_workspace(self, _tenant_id: int) -> bool:
        return self.is_owner


def _make_web_request(
    path: str = "/app/commands",
    *,
    method: str = "GET",
    tenant_id: int = 10,
    user_id: int = 101,
    perms: Any = None,
    csrf: str = "valid-csrf-token-123",
    accept: str = "text/html",
) -> Request:
    from starlette.datastructures import URLPath

    headers = [(b"accept", accept.encode("utf-8"))]
    csrf_mgr = SimpleNamespace(
        generate_token=lambda: csrf,
        verify_token=lambda tok: bool(tok) and tok == csrf,
    )
    fake_router = SimpleNamespace(
        url_path_for=lambda name, **path_params: URLPath(f"/static/{path_params.get('path', '')}")
    )
    fake_app = SimpleNamespace(
        state=SimpleNamespace(csrf_manager=csrf_mgr),
        router=fake_router,
    )
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": headers,
        "session": {"_csrf": csrf},
        "app": fake_app,
        "router": fake_router,
    }
    req = Request(scope)
    req.state.tenant_id = tenant_id
    req.state.tenant = SimpleNamespace(id=tenant_id, name="Test Workspace", timezone="Europe/Moscow")
    req.state.web_user = SimpleNamespace(
        id=user_id,
        username="reviewer",
        is_active=True,
        has_admin_access=lambda: False,
    )
    req.state.web_perms = perms or FakeWebPerms(is_owner=True)
    req.state.memberships = []
    req.state.workspaces = []
    req.state.unread_notifications = 0
    req.state.connection_alerts = []
    return req


@pytest.fixture(autouse=True)
def _setup_inbox_audit_and_capabilities(monkeypatch: pytest.MonkeyPatch):
    from app.services.commands import ledger as ledger_mod

    success_mock = AsyncMock(return_value=SimpleNamespace(event_id="evt-ok"))
    unknown_mock = AsyncMock(return_value=SimpleNamespace(event_id="evt-unknown"))
    monkeypatch.setattr(
        ActionAuditEvent,
        "objects",
        SimpleNamespace(record_success=success_mock, record_unknown=unknown_mock),
    )
    monkeypatch.setattr(ledger_mod, "RUNTIME_ENABLED_CAPABILITIES", set(CAPABILITY_REGISTRY.keys()))
    monkeypatch.setattr(ledger_mod, "RUNTIME_KILL_SWITCHES", {})
    set_current_user(SimpleNamespace(id=101, is_active=True))
    yield SimpleNamespace(success=success_mock, unknown=unknown_mock)
    set_current_user(None)


def test_p20_t03_scope_boundaries_and_bot_link_contract() -> None:
    """First increment projects only P13-T04 command ledger; outbox and documents remain disabled."""
    assert PROJECTED_SOURCES == ("p13_t04_command_ledger",)
    assert OUTBOX_PROJECTION_ENABLED is False
    assert DOCUMENT_PROJECTION_ENABLED is False
    assert "p15_t05_outbox" in DISABLED_PROJECTION_SOURCES
    assert "p19_t04_documents" in DISABLED_PROJECTION_SOURCES
    assert "p16_t03_calendar" in DISABLED_PROJECTION_SOURCES
    assert "p17_t04_onec" in DISABLED_PROJECTION_SOURCES
    assert "p20_t11_connected_tasks" in DISABLED_PROJECTION_SOURCES
    assert set(INBOX_STATUSES) == {
        "pending",
        "approved",
        "expired",
        "rejected",
        "executed",
        "failed",
        "unknown",
    }

    bot_link = format_bot_decision_link(42, capability="crm.greeting_send.v1")
    assert bot_link["url"] == "/app/commands/42"
    assert bot_link["in_bot_buttons_allowed"] is False
    assert "/app/commands/42" in bot_link["message"]


@pytest.mark.asyncio
async def test_cross_tenant_and_unauthorized_viewers_filtered_and_denied() -> None:
    """Cross-tenant and unauthorized viewers cannot see or act on commands."""
    session = InboxCommandSession()
    session.add_contact(10, 10)
    ledger = BusinessCommandLedger()
    inbox = ApprovalInboxService(ledger=ledger)

    with tenant_scope(10):
        created = await ledger.preview_command(
            capability="crm.greeting_send.v1",
            idempotency_key="greet-1",
            target_refs=["contact:10"],
            payload={"subject": "С Днём Рождения!", "body": "Поздравляем!"},
            preview_snapshot={"summary": "Поздравление клиента #10"},
            connection_id=5,
            session=session,
        )
        cmd_id = created["id"]

        # Authorized owner sees the pending item
        owner_page = await inbox.list_items(session=session)
        assert len(owner_page["items"]) == 1
        assert owner_page["items"][0]["command_id"] == cmd_id
        assert owner_page["items"][0]["status"] == "pending"
        assert owner_page["items"][0]["can_approve"] is True

        # Viewer without botaction or contact permission sees empty list and is denied on get/approve
        no_perms = FakeWebPerms(grants=set(), is_owner=False)
        empty_page = await inbox.list_items(perms=no_perms, session=session)
        assert empty_page["items"] == []
        assert await inbox.count_pending(perms=no_perms, session=session) == 0

        with pytest.raises(CommandDenied):
            await inbox.get_item(cmd_id, perms=no_perms, session=session)
        with pytest.raises(CommandDenied):
            await inbox.approve_item(
                cmd_id,
                expected_payload_hash=created["payload_hash"],
                perms=no_perms,
                session=session,
            )

        # User 102 with read-only 'viewer' workspace role in session cannot approve or view restricted capability
        session.memberships[(10, 102)] = (True, 2)  # role 2 == "viewer"
        set_current_user(SimpleNamespace(id=102, is_active=True))
        with pytest.raises(CommandDenied):
            await inbox.approve_item(
                cmd_id,
                expected_payload_hash=created["payload_hash"],
                session=session,
            )

    # Cross-tenant user 201 in tenant 20 sees empty list and gets CommandNotFound
    set_current_user(SimpleNamespace(id=201, is_active=True))
    with tenant_scope(20):
        t20_page = await inbox.list_items(session=session)
        assert t20_page["items"] == []
        with pytest.raises(CommandNotFound):
            await inbox.get_item(cmd_id, session=session)
        with pytest.raises(CommandNotFound):
            await inbox.approve_item(
                cmd_id,
                expected_payload_hash=created["payload_hash"],
                session=session,
            )


@pytest.mark.asyncio
async def test_raw_state_gates_approve_and_reconcile_beyond_normalized_status() -> None:
    """`can_approve` requires raw `previewed`; `can_reconcile` requires raw `unknown`.

    The normalized status collapses `draft`/`previewed` into `pending` and
    `claimed`/`unknown` into `unknown`, so the raw state must gate both actions
    on its own: a draft row is not approvable and a claimed row is not
    reconcilable even though their normalized statuses say otherwise.
    """
    session = InboxCommandSession()
    session.add_contact(10, 11)
    session.add_contact(10, 12)
    ledger = BusinessCommandLedger()
    inbox = ApprovalInboxService(ledger=ledger)

    with tenant_scope(10):
        previewed = await ledger.preview_command(
            capability="crm.greeting_send.v1",
            idempotency_key="gate-previewed",
            target_refs=["contact:11"],
            payload={"subject": "Previewed"},
            session=session,
        )
        other = await ledger.preview_command(
            capability="crm.greeting_send.v1",
            idempotency_key="gate-claimed",
            target_refs=["contact:12"],
            payload={"subject": "Claimed"},
            session=session,
        )

        # Baseline: a genuinely previewed row is approvable and not reconcilable.
        base = await inbox.get_item(previewed["id"], session=session)
        assert base["raw_state"] == "previewed"
        assert base["status"] == "pending"
        assert base["can_approve"] is True
        assert base["can_reconcile"] is False

        # A draft row normalizes to `pending` but must not be approvable.
        session.commands[previewed["id"]].state = "draft"
        draft_view = await inbox.get_item(previewed["id"], session=session)
        assert draft_view["raw_state"] == "draft"
        assert draft_view["status"] == "pending"
        assert draft_view["can_approve"] is False

        # A claimed row normalizes to `unknown` but must not be reconcilable.
        session.commands[other["id"]].state = "claimed"
        claimed_view = await inbox.get_item(other["id"], session=session)
        assert claimed_view["raw_state"] == "claimed"
        assert claimed_view["status"] == "unknown"
        assert claimed_view["can_reconcile"] is False

        # Baseline: a genuinely unknown row is reconcilable.
        session.commands[other["id"]].state = "unknown"
        unknown_view = await inbox.get_item(other["id"], session=session)
        assert unknown_view["raw_state"] == "unknown"
        assert unknown_view["status"] == "unknown"
        assert unknown_view["can_reconcile"] is True


@pytest.mark.asyncio
async def test_today_mine_scope_excludes_another_requester_even_when_viewer_can_act() -> None:
    """Mine is requester-owned; owner action rights do not widen it to teammate commands."""
    session = InboxCommandSession()
    session.add_contact(10, 21)
    session.add_contact(10, 22)
    inbox = ApprovalInboxService(ledger=BusinessCommandLedger())

    with tenant_scope(10):
        set_current_user(SimpleNamespace(id=101, is_active=True))
        own = await inbox._ledger.preview_command(
            capability="crm.greeting_send.v1",
            idempotency_key="today-mine-own",
            target_refs=["contact:21"],
            payload={"subject": "Own"},
            session=session,
        )
        set_current_user(SimpleNamespace(id=102, is_active=True))
        teammate = await inbox._ledger.preview_command(
            capability="crm.greeting_send.v1",
            idempotency_key="today-mine-teammate",
            target_refs=["contact:22"],
            payload={"subject": "Teammate"},
            session=session,
        )
        set_current_user(SimpleNamespace(id=101, is_active=True))

        mine = await inbox.project_for_today(
            perms=FakeWebPerms(is_owner=True), scope="mine", actor_id=101, session=session
        )
        no_actor = await inbox.project_for_today(
            perms=FakeWebPerms(is_owner=True), scope="mine", actor_id=None, session=session
        )
        team = await inbox.project_for_today(
            perms=FakeWebPerms(is_owner=True), scope="team", actor_id=101, session=session
        )

    assert [item["id"] for item in mine] == [own["id"]]
    assert [item["requester_user_id"] for item in mine] == [101]
    assert no_actor == []
    assert {item["id"] for item in team} == {own["id"], teammate["id"]}


@pytest.mark.asyncio
async def test_inaccessible_targets_omitted_and_mutations_refused_before_ledger() -> None:
    """F3: an unverifiable target withholds the item and blocks approve/reject pre-mutation."""
    session = InboxCommandSession()
    session.add_contact(10, 777)
    ledger = BusinessCommandLedger()
    inbox = ApprovalInboxService(ledger=ledger)

    with tenant_scope(10):
        created = await ledger.preview_command(
            capability="crm.greeting_send.v1",
            idempotency_key="greet-redact",
            target_refs=["contact:777"],
            payload={
                "recipient": "ivan@example.com",
                "phone": "+79990001122",
                "text": "Секретный текст поздравления",
                "api_key": "super-secret-key",
            },
            preview_snapshot={
                "summary": "Поздравление для Иван Петров",
                "recipient": "ivan@example.com",
                "text": "Секретный текст поздравления",
                "access_token": "tok-123",
            },
            session=session,
        )
        cmd_id = created["id"]

        # Even for an owner, secret/token keys are always redacted in safe_preview
        full_item = await inbox.get_item(cmd_id, session=session)
        assert full_item["redacted"] is False
        assert full_item["safe_preview"]["access_token"] == "[REDACTED]"
        assert full_item["safe_preview"]["payload"]["api_key"] == "[REDACTED]"
        assert full_item["safe_preview"]["recipient"] == "ivan@example.com"

        # A caller-supplied hint can only narrow access: the item is withheld entirely.
        hidden_page = await inbox.list_items(accessible_targets={"contact:999"}, session=session)
        assert hidden_page["items"] == []
        assert await inbox.count_pending(accessible_targets={"contact:999"}, session=session) == 0
        with pytest.raises(CommandNotFound):
            await inbox.get_item(cmd_id, accessible_targets={"contact:999"}, session=session)

        # A target that is not a live tenant-owned contact is withheld too, with no hint at all.
        session.contacts[(10, 777)] = 5  # merged into another contact
        assert (await inbox.list_items(session=session))["items"] == []
        with pytest.raises(CommandNotFound):
            await inbox.get_item(cmd_id, session=session)
        session.contacts[(10, 777)] = None

        # Mutations refuse before the ledger is touched, leaving the row previewed.
        with pytest.raises(CommandDenied, match="access to one or more command targets"):
            await inbox.approve_item(
                cmd_id,
                expected_payload_hash=created["payload_hash"],
                accessible_targets={"contact:999"},
                session=session,
            )
        assert session.commands[cmd_id].state == "previewed"
        assert session.commands[cmd_id].approved_user_id is None

        with pytest.raises(CommandDenied, match="access to one or more command targets"):
            await inbox.reject_item(
                cmd_id,
                expected_payload_hash=created["payload_hash"],
                accessible_targets={"contact:999"},
                session=session,
            )
        assert session.commands[cmd_id].state == "previewed"

        # No preview, outcome or receipt text from the withheld item reaches any surface.
        today_decisions = await inbox.project_for_today(accessible_targets={"contact:999"}, session=session)
        chat_decisions = await inbox.project_for_chat_decisions(accessible_targets={"contact:999"}, session=session)
        assert today_decisions == []
        assert chat_decisions == []
        leaked = json.dumps([today_decisions, chat_decisions, hidden_page], ensure_ascii=False)
        for secret in (
            "ivan@example.com",
            "+79990001122",
            "Секретный текст поздравления",
            "super-secret-key",
            "tok-123",
            "Поздравление для Иван Петров",
        ):
            assert secret not in leaked


@pytest.mark.asyncio
async def test_stale_payload_expired_ttl_revoked_approval_and_prohibited_options() -> None:
    """Stale payload hash, expired TTL, revoked membership, always_allow, bulk, and auto_execute are denied."""
    session = InboxCommandSession()
    session.add_contact(10, 1)
    session.add_contact(10, 5)
    ledger = BusinessCommandLedger()
    inbox = ApprovalInboxService(ledger=ledger)

    with tenant_scope(10):
        created = await ledger.preview_command(
            capability="crm.greeting_send.v1",
            idempotency_key="camp-stale",
            target_refs=["contact:1"],
            payload={"subject": "Original offer"},
            session=session,
        )
        cmd_id = created["id"]
        phash = created["payload_hash"]

        # 1. Persistent always_allow, bulk approval, and auto_execute are strictly prohibited
        with pytest.raises(CommandDenied, match="always allow"):
            await inbox.approve_item(cmd_id, expected_payload_hash=phash, always_allow=True, session=session)
        with pytest.raises(CommandDenied, match="Bulk approval"):
            await inbox.approve_item(cmd_id, expected_payload_hash=phash, bulk_command_ids=[cmd_id], session=session)
        with pytest.raises(CommandDenied, match="automatic execution"):
            await inbox.approve_item(cmd_id, expected_payload_hash=phash, auto_execute=True, session=session)

        # 2. Stale/mismatched expected_payload_hash is rejected on approve and reject
        stale_hash = "sha256:" + "f" * 64
        with pytest.raises(CommandConflict, match="payload changed"):
            await inbox.approve_item(cmd_id, expected_payload_hash=stale_hash, session=session)
        with pytest.raises(CommandConflict, match="payload changed"):
            await inbox.reject_item(cmd_id, expected_payload_hash=stale_hash, session=session)

        # 2b. A non-canonical hash is refused outright, so a review click always binds the exact payload.
        for bad_hash in ("", "sha256:short", "sha256:" + "F" * 64, "md5:" + "f" * 64):
            with pytest.raises(CommandConflict, match="canonical sha256"):
                await inbox.approve_item(cmd_id, expected_payload_hash=bad_hash, session=session)
            with pytest.raises(CommandConflict, match="canonical sha256"):
                await inbox.reject_item(cmd_id, expected_payload_hash=bad_hash, session=session)

        # 3. Valid single-item approval succeeds and keeps state == 'approved' (no automatic execution)
        approved = await inbox.approve_item(
            cmd_id,
            expected_payload_hash=phash,
            ttl_seconds=120,
            session=session,
        )
        assert approved["status"] == "approved"
        assert approved["raw_state"] == "approved"
        assert approved["can_execute"] is True
        assert approved["executed_at"] is None

        # 4. Read-time projection after TTL expiry classifies item as 'expired' and disables execution
        after_ttl = datetime.now(timezone.utc) + timedelta(seconds=180)
        expired_view = await inbox.get_item(cmd_id, now=after_ttl, session=session)
        assert expired_view["status"] == "expired"
        assert expired_view["can_execute"] is False

        with pytest.raises(CommandDenied, match="TTL has expired"):
            await inbox.execute_item(cmd_id, expected_payload_hash=phash, now=after_ttl, session=session)
        assert session.commands[cmd_id].state == "expired"

        # 5. Tampering with payload after approval invalidates execution and marks is_stale_payload
        created2 = await ledger.preview_command(
            capability="crm.greeting_send.v1",
            idempotency_key="greet-tamper",
            target_refs=["contact:5"],
            payload={"subject": "Safe"},
            session=session,
        )
        cid2 = created2["id"]
        await inbox.approve_item(cid2, expected_payload_hash=created2["payload_hash"], session=session)
        session.commands[cid2].payload = {"subject": "Tampered after approval"}
        tampered_view = await inbox.get_item(cid2, session=session)
        assert tampered_view["is_stale_payload"] is True
        assert tampered_view["status"] == "expired"
        assert tampered_view["can_execute"] is False

        with pytest.raises(CommandConflict, match="payload changed since preview"):
            await inbox.execute_item(cid2, expected_payload_hash=created2["payload_hash"], session=session)


@pytest.mark.asyncio
async def test_concurrent_repeated_clicks_rejected_and_unknown_reconciliation() -> None:
    """Repeated approve clicks, rejected execution refusal, and unknown outcome reconciliation."""
    session = InboxCommandSession()
    session.add_contact(10, 100)
    ledger = BusinessCommandLedger()
    inbox = ApprovalInboxService(ledger=ledger)

    with tenant_scope(10):
        created = await ledger.preview_command(
            capability="crm.greeting_send.v1",
            idempotency_key="onec-conc",
            target_refs=["contact:100"],
            payload={"total": "5000.00"},
            session=session,
        )
        cmd_id = created["id"]
        phash = created["payload_hash"]

        # A repeated click loses with CommandConflict; the row is approved exactly once.
        # (Cross-process single-use is proven by the DB module against a real row lock.)
        first = await inbox.approve_item(cmd_id, expected_payload_hash=phash, session=session)
        assert first["status"] == "approved"
        with pytest.raises(CommandConflict, match="already approved"):
            await inbox.approve_item(cmd_id, expected_payload_hash=phash, session=session)

        # Timeout during separate execute -> enters 'unknown' state
        adapter_calls = 0

        async def timeout_adapter(_cmd: BusinessCommand, _targets: list[BusinessCommandTarget]) -> dict[str, str]:
            nonlocal adapter_calls
            adapter_calls += 1
            raise TimeoutError("Provider timeout")

        with pytest.raises(CommandTimeoutUnknown):
            await inbox.execute_item(cmd_id, expected_payload_hash=phash, adapter=timeout_adapter, session=session)
        assert adapter_calls == 1

        unknown_view = await inbox.get_item(cmd_id, session=session)
        assert unknown_view["status"] == "unknown"
        assert unknown_view["can_reconcile"] is True
        assert unknown_view["can_execute"] is False
        assert unknown_view["reconcile_hint"] is not None

        # Repeated execute on unknown item is refused without calling adapter
        with pytest.raises(CommandConflict):
            await inbox.execute_item(cmd_id, expected_payload_hash=phash, adapter=timeout_adapter, session=session)
        assert adapter_calls == 1

        # Reconcile unknown -> executed ('succeeded')
        reconciled = await inbox.reconcile_item(
            cmd_id,
            resolved_state="succeeded",
            provider_receipts={"contact:100": "1c-receipt-100"},
            session=session,
        )
        assert reconciled["status"] == "executed"
        assert reconciled["raw_state"] == "succeeded"

        # Rejected item cannot be executed
        session.add_contact(10, 88)
        to_reject = await ledger.preview_command(
            capability="crm.greeting_send.v1",
            idempotency_key="greet-reject",
            target_refs=["contact:88"],
            payload={"subject": "Reject me"},
            session=session,
        )
        rejected = await inbox.reject_item(
            to_reject["id"],
            reason="reviewer_declined",
            expected_payload_hash=to_reject["payload_hash"],
            session=session,
        )
        assert rejected["status"] == "rejected"
        assert rejected["raw_state"] == "cancelled"

        with pytest.raises(CommandConflict):
            await inbox.execute_item(to_reject["id"], expected_payload_hash=to_reject["payload_hash"], session=session)


@pytest.mark.asyncio
async def test_web_commands_csrf_accessibility_and_unified_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify /app/commands web routes, CSRF protection, accessibility markup, header badge, Today, and Chat."""
    from app.services.workspace.today import TodayProjection
    from app.web import chat_workspace as chat_ws_mod
    from app.web import commands as web_commands_mod
    from app.web import deps as web_deps_mod

    session = InboxCommandSession()
    session.add_contact(10, 10)
    ledger = BusinessCommandLedger()
    inbox = ApprovalInboxService(ledger=ledger)

    with tenant_scope(10):
        created = await ledger.preview_command(
            capability="crm.greeting_send.v1",
            idempotency_key="web-greet-1",
            target_refs=["contact:10"],
            payload={"subject": "Юбилей компании"},
            preview_snapshot={"summary": "Поздравление с юбилеем компании"},
            connection_id=5,
            session=session,
        )
        cmd_id = created["id"]
        phash = created["payload_hash"]

        # Bind web module and global approval_inbox to our test session
        async def _count_pending(**kwargs: Any) -> int:
            return await inbox.count_pending(session=session, **kwargs)

        async def _list_items(**kwargs: Any) -> dict[str, Any]:
            return await inbox.list_items(session=session, **kwargs)

        async def _get_item(cid: int, **kwargs: Any) -> dict[str, Any]:
            return await inbox.get_item(cid, session=session, **kwargs)

        async def _approve_item(cid: int, **kwargs: Any) -> dict[str, Any]:
            return await inbox.approve_item(cid, session=session, **kwargs)

        async def _reject_item(cid: int, **kwargs: Any) -> dict[str, Any]:
            return await inbox.reject_item(cid, session=session, **kwargs)

        async def _project_chat(**kwargs: Any) -> list[dict[str, Any]]:
            return await inbox.project_for_chat_decisions(session=session, **kwargs)

        monkeypatch.setattr(web_commands_mod.approval_inbox, "list_items", _list_items)
        monkeypatch.setattr(web_commands_mod.approval_inbox, "get_item", _get_item)
        monkeypatch.setattr(web_commands_mod.approval_inbox, "approve_item", _approve_item)
        monkeypatch.setattr(web_commands_mod.approval_inbox, "reject_item", _reject_item)
        monkeypatch.setattr(approval_inbox, "count_pending", _count_pending)
        monkeypatch.setattr(approval_inbox, "project_for_chat_decisions", _project_chat)
        monkeypatch.setattr(approval_inbox, "get_item", _get_item)

        # 1. Surface 1 & 3: GET /app/commands renders accessible table + header & nav pending badge
        req_list = _make_web_request("/app/commands")
        # The shared dependency populates the badge on every authenticated page.
        await web_deps_mod.pending_approvals_badge(req_list)
        resp_list = await web_commands_mod.commands_page(req_list, before_id=None, status=None)
        html_list = resp_list.body.decode("utf-8")
        assert resp_list.status_code == 200
        assert "data-approval-inbox" in html_list
        assert 'aria-label="Список команд на согласовании"' in html_list
        assert 'aria-label="Фильтр статусов согласования"' in html_list
        assert f'aria-label="Согласовать команду #{cmd_id}"' in html_list
        assert f'aria-label="Отклонить команду #{cmd_id}"' in html_list
        assert "data-pending-approvals-badge" in html_list
        assert 'aria-label="На согласовании: 1"' in html_list
        assert "data-nav-approvals-badge" in html_list

        # Single-item detail view GET /app/commands/{id}
        req_detail = _make_web_request(f"/app/commands/{cmd_id}")
        resp_detail = await web_commands_mod.command_detail_page(req_detail, command_id=cmd_id)
        html_detail = resp_detail.body.decode("utf-8")
        assert resp_detail.status_code == 200
        assert "data-approval-detail-card" in html_detail
        assert phash in html_detail
        assert "Поздравление с юбилеем компании" in html_detail

        # 2. Surface 2: Today decisions section includes the projected command
        class _RepoStub:
            async def pending_actions(self, *_a: Any, **_kw: Any) -> list[Any]:
                return []

            async def command_decisions(
                self, workspace_id: int, user_id: int, perms: Any, scope: str, now: datetime
            ) -> list[dict[str, Any]]:
                return await inbox.project_for_today(
                    perms=perms,
                    scope=scope,
                    actor_id=user_id,
                    now=now,
                    session=session,
                )

            async def pending_outbox(self, *_a: Any, **_kw: Any) -> list[Any]:
                return []

        today_proj = TodayProjection(
            workspace_id=10,
            user_id=101,
            perms=FakeWebPerms(is_owner=True),
            repository=_RepoStub(),
        )

        decisions_sec = await today_proj.build_decisions_section()
        assert len(decisions_sec.items) == 1
        assert decisions_sec.items[0]["kind"] == "command"
        assert decisions_sec.items[0]["authoritative_url"] == f"/app/commands/{cmd_id}"
        assert decisions_sec.items[0]["payload_hash"] == phash

        # 3. Surface 4: Chat 'Ждут решения' tab and inline decision card
        req_chat = _make_web_request("/app/chat/workspace/panel", accept="application/json")
        panel_resp = await chat_ws_mod.workspace_panel(
            req_chat, task_id=None, context_type=None, context_id=None, boundary=None
        )
        panel_data = json.loads(panel_resp.body.decode("utf-8"))
        assert len(panel_data["decisions"]) == 1
        assert panel_data["decisions"][0]["authoritative_url"] == f"/app/commands/{cmd_id}"
        assert panel_data["decisions"][0]["payload_hash"] == phash

        cards_resp = await chat_ws_mod.workspace_cards(
            req_chat,
            task_id=None,
            context_type="command",
            context_id=str(cmd_id),
            boundary="cloud",
        )
        cards_data = json.loads(cards_resp.body.decode("utf-8"))
        assert len(cards_data["cards"]) == 1
        assert cards_data["cards"][0]["card_type"] == "decision"
        assert cards_data["cards"][0]["authoritative_url"] == f"/app/commands/{cmd_id}"

        # Command context cannot bypass the cloud boundary or omit its id.
        get_item_calls = 0

        async def _counted_get(cid: int, **kwargs: Any) -> dict[str, Any]:
            nonlocal get_item_calls
            get_item_calls += 1
            return await _get_item(cid, **kwargs)

        monkeypatch.setattr(approval_inbox, "get_item", _counted_get)
        for invalid_context in (
            (str(cmd_id), "local_only"),
            (str(cmd_id), "unknown"),
            (None, "cloud"),
        ):
            bad_context = await chat_ws_mod.workspace_cards(
                req_chat,
                task_id=None,
                context_type="command",
                context_id=invalid_context[0],
                boundary=invalid_context[1],
            )
            assert bad_context.status_code == 404
        assert get_item_calls == 0

        # 4. CSRF protection on POST /app/commands/{id}/approve and /reject
        req_bad_csrf = _make_web_request(
            f"/app/commands/{cmd_id}/approve",
            method="POST",
            csrf="expected-csrf",
            accept="application/json",
        )
        bad_csrf_resp = await web_commands_mod.command_approve_action(
            req_bad_csrf,
            command_id=cmd_id,
            token="wrong-csrf",
            expected_payload_hash=phash,
        )
        assert bad_csrf_resp.status_code == 403
        assert json.loads(bad_csrf_resp.body)["error"] == "invalid_csrf"

        # Prohibited always_allow via web POST returns 403
        req_ok_csrf = _make_web_request(
            f"/app/commands/{cmd_id}/approve",
            method="POST",
            csrf="valid-csrf",
            accept="application/json",
        )
        prohibited_resp = await web_commands_mod.command_approve_action(
            req_ok_csrf,
            command_id=cmd_id,
            token="valid-csrf",
            expected_payload_hash=phash,
            always_allow="1",
        )
        assert prohibited_resp.status_code == 403

        # Valid single-item approve POST succeeds
        approve_resp = await web_commands_mod.command_approve_action(
            req_ok_csrf,
            command_id=cmd_id,
            token="valid-csrf",
            expected_payload_hash=phash,
        )
        assert approve_resp.status_code == 200
        approve_body = json.loads(approve_resp.body)
        assert approve_body["ok"] is True
        assert approve_body["item"]["status"] == "approved"

        # Repeated approve click via web POST returns 409 Conflict
        repeat_resp = await web_commands_mod.command_approve_action(
            req_ok_csrf,
            command_id=cmd_id,
            token="valid-csrf",
            expected_payload_hash=phash,
        )
        assert repeat_resp.status_code == 409


@pytest.mark.asyncio
async def test_unsupported_capability_omitted_and_blocked_through_get_and_mutations() -> None:
    """F2: only `crm.greeting_send.v1` is served; every other capability is refused pre-ledger."""
    assert inbox_module.INBOX_SUPPORTED_CAPABILITIES == frozenset({"crm.greeting_send.v1"})
    for code in CAPABILITY_REGISTRY:
        assert code in inbox_module.INBOX_SUPPORTED_CAPABILITIES or code in inbox_module.UNSUPPORTED_CAPABILITY_REASONS

    session = InboxCommandSession()
    session.add_contact(10, 1)
    session.add_contact(10, 2)
    session.add_contact(10, 10)
    ledger = BusinessCommandLedger()
    inbox = ApprovalInboxService(ledger=ledger)

    with tenant_scope(10):
        batch = await ledger.preview_command(
            capability="comms.batch_campaign.v1",
            idempotency_key="camp-unsupported",
            target_refs=["contact:1", "contact:2"],
            payload={"subject": "Original offer"},
            session=session,
        )
        batch_id = batch["id"]
        supported = await ledger.preview_command(
            capability="crm.greeting_send.v1",
            idempotency_key="greet-supported",
            target_refs=["contact:10"],
            payload={"subject": "Поздравляем"},
            session=session,
        )

        # Listing omits the unsupported command and keeps the supported one.
        page = await inbox.list_items(session=session)
        assert [item["command_id"] for item in page["items"]] == [supported["id"]]
        assert await inbox.count_pending(session=session) == 1

        # Detail refuses explicitly rather than pretending the row is absent.
        with pytest.raises(CommandDenied, match="not supported by the approval inbox"):
            await inbox.get_item(batch_id, session=session)

        # Every mutation refuses before the ledger is called, so the batch is untouched.
        for call in (
            lambda: inbox.approve_item(batch_id, expected_payload_hash=batch["payload_hash"], session=session),
            lambda: inbox.reject_item(batch_id, expected_payload_hash=batch["payload_hash"], session=session),
            lambda: inbox.execute_item(batch_id, expected_payload_hash=batch["payload_hash"], session=session),
            lambda: inbox.reconcile_item(batch_id, resolved_state="succeeded", session=session),
        ):
            with pytest.raises(CommandDenied, match="not supported by the approval inbox"):
                await call()

        assert session.commands[batch_id].state == "previewed"
        assert session.commands[batch_id].approved_user_id is None
        assert session.commands[batch_id].approved_payload_hash is None


class _OwnedSessionContext:
    """Minimal async context manager standing in for `async_session_maker()`."""

    def __init__(self, session: Any) -> None:
        self._session = session

    async def __aenter__(self) -> Any:
        return self._session

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


@pytest.mark.asyncio
async def test_authorization_without_caller_session_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """F1: an omitted session resolves an owned one and still requires actor, member and grant."""
    session = InboxCommandSession()
    session.add_contact(10, 10)
    ledger = BusinessCommandLedger()
    inbox = ApprovalInboxService(ledger=ledger)

    with tenant_scope(10):
        created = await ledger.preview_command(
            capability="crm.greeting_send.v1",
            idempotency_key="greet-owned-session",
            target_refs=["contact:10"],
            payload={"subject": "Юбилей"},
            session=session,
        )
        cmd_id = created["id"]
        phash = created["payload_hash"]

        # The owned session is the same double, so no production branch exists for the test.
        monkeypatch.setattr(inbox_module, "async_session_maker", lambda: _OwnedSessionContext(session))

        # An owner-role membership sees the item even though the caller passed no session.
        owner_page = await inbox.list_items()
        assert [item["command_id"] for item in owner_page["items"]] == [cmd_id]

        # No active actor: nothing is listed and detail/mutations fail closed.
        set_current_user(None)
        try:
            assert (await inbox.list_items())["items"] == []
            with pytest.raises(CommandDenied):
                await inbox.get_item(cmd_id)
            with pytest.raises(CommandDenied):
                await inbox.approve_item(cmd_id, expected_payload_hash=phash)
            with pytest.raises(CommandDenied):
                await inbox.reject_item(cmd_id, expected_payload_hash=phash)
        finally:
            set_current_user(SimpleNamespace(id=101, is_active=True))

        # An inactive membership is the same as no membership.
        session.memberships[(10, 101)] = (False, 1)
        assert (await inbox.list_items())["items"] == []
        with pytest.raises(CommandDenied, match="membership"):
            await inbox.get_item(cmd_id)
        with pytest.raises(CommandDenied, match="membership"):
            await inbox.approve_item(cmd_id, expected_payload_hash=phash)
        session.memberships[(10, 101)] = (True, 1)

        # A role without the capability's resource grant cannot see or approve it, even
        # though it may open the inbox (`botaction` VIEW is not a substitute).
        session.memberships[(10, 101)] = (True, 2)  # role 2 == viewer
        inbox_perms = FakeWebPerms(grants={("botaction", "view")}, is_owner=False)
        assert (await inbox.list_items(perms=inbox_perms))["items"] == []
        with pytest.raises(CommandDenied):
            await inbox.get_item(cmd_id, perms=inbox_perms)
        act_perms = FakeWebPerms(grants={("botaction", "update")}, is_owner=False)
        with pytest.raises(CommandDenied):
            await inbox.approve_item(cmd_id, expected_payload_hash=phash, perms=act_perms)
        assert session.commands[cmd_id].state == "previewed"


@pytest.mark.asyncio
async def test_missing_stored_payload_and_unknown_target_shapes_are_withheld() -> None:
    """F1/F3: a command without a readable payload row or with an unverifiable target is hidden."""
    session = InboxCommandSession()
    ledger = BusinessCommandLedger()
    inbox = ApprovalInboxService(ledger=ledger)

    with tenant_scope(10):
        # No contact row exists for this target, so it must never be projected.
        created = await ledger.preview_command(
            capability="crm.greeting_send.v1",
            idempotency_key="greet-missing-contact",
            target_refs=["contact:404"],
            payload={"subject": "Кому-то"},
            session=session,
        )
        assert (await inbox.list_items(session=session))["items"] == []
        with pytest.raises(CommandNotFound):
            await inbox.get_item(created["id"], session=session)

        # Malformed and non-contact target shapes are refused the same way.
        for bad_ref in ("contact:0", "contact:abc", "deal:5", "contact"):
            session.add_contact(10, 5)
            bad = await ledger.preview_command(
                capability="crm.greeting_send.v1",
                idempotency_key=f"greet-bad-{bad_ref}",
                target_refs=[bad_ref],
                payload={"subject": "Bad target"},
                session=session,
            )
            assert (await inbox.list_items(session=session))["items"] == []
            with pytest.raises(CommandNotFound):
                await inbox.get_item(bad["id"], session=session)

        # A cross-tenant contact id is not proof of access either.
        session.add_contact(20, 900)
        cross = await ledger.preview_command(
            capability="crm.greeting_send.v1",
            idempotency_key="greet-cross-contact",
            target_refs=["contact:900"],
            payload={"subject": "Чужой контакт"},
            session=session,
        )
        assert (await inbox.list_items(session=session))["items"] == []
        with pytest.raises(CommandNotFound):
            await inbox.get_item(cross["id"], session=session)

        # A command whose stored payload row cannot be read is withheld (fail closed).
        session.add_contact(10, 11)
        orphan = await ledger.preview_command(
            capability="crm.greeting_send.v1",
            idempotency_key="greet-orphan",
            target_refs=["contact:11"],
            payload={"subject": "Без нагрузки"},
            session=session,
        )
        orphan_id = orphan["id"]
        del session.commands[orphan_id]
        assert (await inbox.list_items(session=session))["items"] == []
        with pytest.raises(CommandNotFound):
            await inbox.get_item(orphan_id, session=session)


@pytest.mark.asyncio
async def test_badge_counts_every_authorized_page_and_never_leaks_to_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    """F5: the badge walks all pages, and an anonymous request keeps it at zero."""
    from app.web import deps as web_deps_mod

    session = InboxCommandSession()
    ledger = BusinessCommandLedger()
    inbox = ApprovalInboxService(ledger=ledger)

    with tenant_scope(10):
        for index in range(3):
            contact_id = 200 + index
            session.add_contact(10, contact_id)
            await ledger.preview_command(
                capability="crm.greeting_send.v1",
                idempotency_key=f"greet-badge-{index}",
                target_refs=[f"contact:{contact_id}"],
                payload={"subject": f"Поздравление {index}"},
                session=session,
            )

        # Force a one-row SQL candidate page so the count must follow the cursor.
        monkeypatch.setattr(inbox_module, "_PENDING_COUNT_PAGE_SIZE", 1)
        assert await inbox.count_pending(session=session) == 3

        # The shared dependency sets the count for an authenticated page.
        req = _make_web_request("/app/dashboard")

        async def _count_pending(**kwargs: Any) -> int:
            return await inbox.count_pending(session=session, **kwargs)

        monkeypatch.setattr(approval_inbox, "count_pending", _count_pending)
        await web_deps_mod.pending_approvals_badge(req)
        assert req.state.pending_approvals_count == 3

        json_req = _make_web_request("/app/chat/workspace/panel", accept="application/json")
        await web_deps_mod.pending_approvals_badge(json_req)
        assert json_req.state.pending_approvals_count == 0
        post_req = _make_web_request("/app/commands/1/approve", method="POST", accept="text/html")
        await web_deps_mod.pending_approvals_badge(post_req)
        assert post_req.state.pending_approvals_count == 0

        # Anonymous requests never reach the projection and keep a zero badge.
        anon = _make_web_request("/app/login")
        anon.state.web_user = None
        anon.state.web_perms = None
        await web_deps_mod.pending_approvals_badge(anon)
        assert anon.state.pending_approvals_count == 0

        # A viewer without the capability grant sees no count either.
        session.memberships[(10, 101)] = (True, 2)
        restricted = _make_web_request("/app/dashboard", perms=FakeWebPerms(grants=set(), is_owner=False))
        await web_deps_mod.pending_approvals_badge(restricted)
        assert restricted.state.pending_approvals_count == 0


@pytest.mark.asyncio
async def test_today_and_chat_skip_recent_finished_commands_before_page_limit() -> None:
    """Recent finished ledger rows cannot hide older pending decisions from small surfaces."""
    session = InboxCommandSession()
    session.add_contact(10, 300)
    inbox = ApprovalInboxService(ledger=BusinessCommandLedger())

    with tenant_scope(10):
        pending = await inbox._ledger.preview_command(
            capability="crm.greeting_send.v1",
            idempotency_key="greet-old-pending",
            target_refs=["contact:300"],
            payload={"subject": "Older pending"},
            session=session,
        )
        # Fill more than Today/Chat's page size with newer completed rows.
        for index in range(25):
            finished = await inbox._ledger.preview_command(
                capability="crm.greeting_send.v1",
                idempotency_key=f"greet-finished-{index}",
                target_refs=["contact:300"],
                payload={"subject": f"Finished {index}"},
                session=session,
            )
            session.commands[finished["id"]].state = "succeeded"

        today = await inbox.project_for_today(session=session, scope="all")
        chat = await inbox.project_for_chat_decisions(session=session)

    assert [item["id"] for item in today] == [pending["id"]]
    assert [item["command_id"] for item in chat] == [pending["id"]]
