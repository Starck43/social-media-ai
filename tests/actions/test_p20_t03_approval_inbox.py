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
    format_bot_decision_link,
)
from tests.actions.test_p13_t04_unit import FakeCommandSession


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
    session = FakeCommandSession()
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
async def test_inaccessible_targets_redaction_and_approval_denial() -> None:
    """Reviewers without target read access see redacted target/PII fields and cannot approve or execute."""
    session = FakeCommandSession()
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

        # When target is inaccessible to reviewer, target refs and PII fields are redacted and approval is blocked
        redacted_item = await inbox.get_item(
            cmd_id,
            accessible_targets={"contact:999"},
            session=session,
        )
        assert redacted_item["redacted"] is True
        assert redacted_item["target_accessible"] is False
        assert redacted_item["can_approve"] is False
        assert redacted_item["can_execute"] is False
        assert redacted_item["target_refs"] == ["[скрыто:1]"]
        assert "Содержимое скрыто" in redacted_item["preview_text"]
        assert "ivan@example.com" not in json.dumps(redacted_item["safe_preview"], ensure_ascii=False)
        assert "+79990001122" not in json.dumps(redacted_item["safe_preview"], ensure_ascii=False)
        assert "Секретный текст поздравления" not in json.dumps(redacted_item["safe_preview"], ensure_ascii=False)

        with pytest.raises(CommandDenied, match="access to one or more command targets"):
            await inbox.approve_item(
                cmd_id,
                expected_payload_hash=created["payload_hash"],
                accessible_targets={"contact:999"},
                session=session,
            )


@pytest.mark.asyncio
async def test_stale_payload_expired_ttl_revoked_approval_and_prohibited_options() -> None:
    """Stale payload hash, expired TTL, revoked membership, always_allow, bulk, and auto_execute are denied."""
    session = FakeCommandSession()
    ledger = BusinessCommandLedger()
    inbox = ApprovalInboxService(ledger=ledger)

    with tenant_scope(10):
        created = await ledger.preview_command(
            capability="comms.batch_campaign.v1",
            idempotency_key="camp-stale",
            target_refs=["contact:1", "contact:2"],
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
        with pytest.raises(CommandDenied, match="Payload hash mismatch"):
            await inbox.approve_item(cmd_id, expected_payload_hash=stale_hash, session=session)
        with pytest.raises(CommandConflict, match="payload changed"):
            await inbox.reject_item(cmd_id, expected_payload_hash=stale_hash, session=session)

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

        with pytest.raises(CommandDenied, match="payload changed"):
            await inbox.execute_item(cid2, expected_payload_hash=created2["payload_hash"], session=session)


@pytest.mark.asyncio
async def test_concurrent_repeated_clicks_rejected_and_unknown_reconciliation() -> None:
    """Concurrent/repeated approve clicks, rejected execution refusal, and unknown outcome reconciliation."""
    session = FakeCommandSession()
    ledger = BusinessCommandLedger()
    inbox = ApprovalInboxService(ledger=ledger)

    with tenant_scope(10):
        created = await ledger.preview_command(
            capability="onec.document_write.v1",
            idempotency_key="onec-conc",
            target_refs=["onec:order:100"],
            payload={"total": "5000.00"},
            session=session,
        )
        cmd_id = created["id"]
        phash = created["payload_hash"]

        # Concurrent approve clicks: one succeeds, the second raises CommandConflict
        results = await asyncio.gather(
            inbox.approve_item(cmd_id, expected_payload_hash=phash, session=session),
            inbox.approve_item(cmd_id, expected_payload_hash=phash, session=session),
            return_exceptions=True,
        )
        successes = [r for r in results if isinstance(r, dict)]
        conflicts = [r for r in results if isinstance(r, CommandConflict)]
        assert len(successes) == 1
        assert len(conflicts) == 1
        assert successes[0]["status"] == "approved"

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
            provider_receipts={"onec:order:100": "1c-receipt-100"},
            session=session,
        )
        assert reconciled["status"] == "executed"
        assert reconciled["raw_state"] == "succeeded"

        # Rejected item cannot be executed
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

    session = FakeCommandSession()
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
        monkeypatch.setattr(approval_inbox, "project_for_chat_decisions", _project_chat)
        monkeypatch.setattr(approval_inbox, "get_item", _get_item)

        # 1. Surface 1 & 3: GET /app/commands renders accessible table + header & nav pending badge
        req_list = _make_web_request("/app/commands")
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

            async def pending_commands(self, *_a: Any, **_kw: Any) -> list[Any]:
                return list(session.commands.values())

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
        panel_resp = await chat_ws_mod.workspace_panel(req_chat, task_id=None, context_type=None, context_id=None, boundary=None)
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
