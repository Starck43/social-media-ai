"""Unit and contract tests for Communications & Follow-ups workspace (P12-T04).

Covers:
  - T04.1: Outbox state machine (draft -> validated -> approved -> claimed -> sent | uncertain_delivery)
  - T04.1: Single-use approval bound to (actor_id, content_hash, ttl_expires_at)
  - T04.1: Kill-switch cancellation & prohibition of blind retries on timeout
  - T04.2: Route verification gate (blocks approval when route_verified is False)
  - T04.2: Tenant quiet hours calculation & rescheduling (21:00–08:00)
  - T04.2: Imprecise date detection and human confirmation gate
  - T04.3: StrictUndefined Jinja2 template rendering for web/communications.html and web/followups.html
  - T04.3: FastAPI router endpoints for /communications and /followups
  - Reachability: `/app/communications` and `/app/followups` are registered in the application.
"""

from __future__ import annotations

import types
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from tests.shared.evidence_fixtures import evidence_row, workspace_double
from tests.source_import_isolation import load_isolated_source as _isolated_source

ROOT = Path(__file__).resolve().parents[2]


class NS(types.SimpleNamespace):
    def __getitem__(self, item):
        return getattr(self, item)


def load_isolated_source(module_name: str, file_path: Path, mocked_deps: dict[str, Any] | None = None):
    """Keep route/service doubles module-local, including deferred imports."""
    return _isolated_source(module_name, file_path, mocked_deps)


def _load_communications_outbox():
    return load_isolated_source(
        "_p12_t04_services_outbox",
        ROOT / "app/services/communications/outbox.py",
    )


def _load_communications_followups():
    return load_isolated_source(
        "_p12_t04_services_followups",
        ROOT / "app/services/communications/followups.py",
    )


def _build_test_jinja_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(ROOT / "app/web/templates"),
        autoescape=select_autoescape(),
        undefined=StrictUndefined,
        extensions=["jinja2.ext.do"],
    )
    env.filters["ui_label"] = str
    env.filters["human_dt"] = lambda val, **kw: str(val or "")
    env.globals["static_v"] = lambda path: "http://fixture.test/static/" + path
    env.globals["url_for"] = lambda name, path: "http://fixture.test/static/" + path
    return env


def _build_base_template_context() -> dict[str, Any]:
    perms = NS(
        can=lambda *args: True,
        can_manage_workspace=lambda *a: False,
        is_owner=False,
        is_superuser_role=False,
        workspace_role="viewer",
    )
    return {
        "section": "communications",
        "tab": "",
        "tenant": NS(id=1, name="ООО Тест", slug="test"),
        "user": NS(id=42, username="saloon.as", email="saloon.as@fixture.test", has_admin_access=lambda: False),
        "workspaces": [],
        "memberships": [],
        "unread_notifications": 0,
        "nav": [
            NS(key="dashboard", href="/app/", label="Дашборд", ready=True, icon="home"),
            NS(key="communications", href="#", label="Коммуникации", ready=False, icon="chat"),
            NS(key="followups", href="#", label="Фоллоу-апы", ready=False, icon="clock"),
        ],
        "csrf": "fixture-token",
        "messages": [],
        "flashes": [],
        "request": NS(query_params={}),
        "perms": perms,
        "is_superuser": False,
        "member_level": None,
    }


def test_outbox_hash_determinism_and_lifecycle():
    """T04.1: Deterministic hash calculation, draft creation, and approval invalidation on body update."""
    outbox = _load_communications_outbox()

    h1 = outbox.compute_message_hash("a@b.com", "Email", "Subject", "Body 1")
    h2 = outbox.compute_message_hash("a@b.com", "Email", "Subject", "Body 1")
    h3 = outbox.compute_message_hash("a@b.com", "Email", "Subject", "Body 2")
    assert h1.startswith("sha256:")
    assert h1 == h2
    assert h1 != h3

    item = outbox.create_outbox_item(
        item_id="out-test-1",
        channel="Email (IMAP/SMTP)",
        recipient_label="Test Recipient",
        recipient_address="test@example.com",
        subject="Test Subject",
        body_text="Test Body 1",
        route_verified=True,
    )
    assert item["state"] == outbox.STATE_VALIDATED
    assert item["bound_actor"] is None

    # Approve item
    approved = outbox.approve_outbox_item(item, actor_id="operator-1", ttl_expires_at="2026-11-02T18:00:00Z")
    assert approved["state"] == outbox.STATE_APPROVED
    assert approved["bound_actor"] == "operator-1"
    assert approved["ttl_expires_at"] == "2026-11-02T18:00:00Z"

    # Modifying body MUST invalidate approval!
    edited = outbox.update_message_body(approved, "New Body Content")
    assert edited["state"] == outbox.STATE_VALIDATED
    assert edited["bound_actor"] is None
    assert edited["ttl_expires_at"] is None
    assert edited["content_hash"] != approved["content_hash"]


def test_route_verification_gate_blocks_approval():
    """T04.2: Transition to approved is strictly blocked when route_verified is False."""
    outbox = _load_communications_outbox()

    unverified_item = outbox.create_outbox_item(
        item_id="out-unverified",
        channel="Telegram",
        recipient_label="Ambiguous Contact",
        recipient_address="@unknown_contact",
        subject="Meeting confirmation",
        body_text="Let's meet tomorrow.",
        route_verified=False,
        route_warning="Ambiguous recipient handle",
    )
    assert unverified_item["state"] == outbox.STATE_DRAFT

    with pytest.raises(outbox.RouteNotVerifiedError) as exc_info:
        outbox.approve_outbox_item(unverified_item, actor_id="operator-1", ttl_expires_at="2026-11-02T18:00:00Z")
    assert "Cannot approve message" in str(exc_info.value)

    # Validate route explicitly
    verified_item = outbox.validate_route(unverified_item, is_verified=True, warning=None)
    assert verified_item["state"] == outbox.STATE_VALIDATED
    assert verified_item["route_verified"] is True

    # Now approval succeeds
    approved_item = outbox.approve_outbox_item(
        verified_item, actor_id="operator-1", ttl_expires_at="2026-11-02T18:00:00Z"
    )
    assert approved_item["state"] == outbox.STATE_APPROVED


def test_approval_binding_claim_and_ttl_expiration():
    """T04.1: Claim transitions message and enforces TTL expiration."""
    outbox = _load_communications_outbox()

    item = outbox.create_outbox_item(
        item_id="out-claim-test",
        channel="Email",
        recipient_label="Recipient",
        recipient_address="r@example.com",
        subject="Subject",
        body_text="Body",
        route_verified=True,
    )
    approved = outbox.approve_outbox_item(item, actor_id="op", ttl_expires_at="2026-11-02T12:00:00Z")

    # Claim before TTL
    claimed = outbox.claim_outbox_item(approved, current_time_iso="2026-11-02T11:30:00Z")
    assert claimed["state"] == outbox.STATE_CLAIMED
    assert claimed["attempts_count"] == 1

    # Claim after TTL should raise ApprovalExpiredError
    with pytest.raises(outbox.ApprovalExpiredError):
        outbox.claim_outbox_item(approved, current_time_iso="2026-11-02T12:30:00Z")


def test_delivery_timeout_and_blind_retry_forbidden():
    """T04.1: Provider timeout moves to uncertain_delivery and blind retry is forbidden."""
    outbox = _load_communications_outbox()

    item = outbox.create_outbox_item(
        item_id="out-timeout-test",
        channel="SMS Gateway",
        recipient_label="Recipient",
        recipient_address="+79991234567",
        subject="Alert",
        body_text="Alert text",
        route_verified=True,
    )
    approved = outbox.approve_outbox_item(item, actor_id="op", ttl_expires_at="2026-11-02T18:00:00Z")
    claimed = outbox.claim_outbox_item(approved)

    # Provider timeout
    timed_out = outbox.timeout_delivery(claimed, timeout_note="HTTP 504 Gateway Timeout")
    assert timed_out["state"] == outbox.STATE_UNCERTAIN_DELIVERY
    assert "Gateway timeout" in timed_out["last_error"]

    # Blind automatic retry is strictly forbidden!
    assert outbox.can_retry_blindly(timed_out) is False

    # Confirmed delivery from claimed
    sent = outbox.confirm_delivery(claimed, provider_ack="msg-ack-9981")
    assert sent["state"] == outbox.STATE_SENT
    assert sent["provider_ack"] == "msg-ack-9981"


def test_kill_switch_cancellation():
    """T04.1: Kill-switch cancellation transitions message to cancelled."""
    outbox = _load_communications_outbox()

    item = outbox.create_outbox_item(
        item_id="out-cancel-test",
        channel="Email",
        recipient_label="Recipient",
        recipient_address="r@example.com",
        subject="Cancel Me",
        body_text="Body",
        route_verified=True,
    )
    approved = outbox.approve_outbox_item(item, actor_id="op", ttl_expires_at="2026-11-02T18:00:00Z")
    cancelled = outbox.cancel_outbox_item(approved, actor_id="security-officer", reason="Revocation order #44")
    assert cancelled["state"] == outbox.STATE_CANCELLED
    assert "Revocation order #44" in cancelled["cancellation_reason"]


def test_quiet_hours_window_and_rescheduling():
    """T04.2: Tenant quiet hours calculation & delivery rescheduling (21:00–08:00)."""
    followups = _load_communications_followups()

    # In quiet hours: 22:30 at night, 04:00 early morning
    dt_night = datetime(2026, 11, 2, 22, 30)
    dt_early = datetime(2026, 11, 2, 4, 15)
    # Outside quiet hours: 14:00 afternoon
    dt_day = datetime(2026, 11, 2, 14, 0)

    assert followups.is_in_quiet_hours(dt_night, start_hour=21, end_hour=8) is True
    assert followups.is_in_quiet_hours(dt_early, start_hour=21, end_hour=8) is True
    assert followups.is_in_quiet_hours(dt_day, start_hour=21, end_hour=8) is False

    # Adjusting night time rolls forward to next morning at 08:00
    adj_night = followups.adjust_for_quiet_hours(dt_night, start_hour=21, end_hour=8)
    assert adj_night.year == 2026 and adj_night.month == 11 and adj_night.day == 3
    assert adj_night.hour == 8 and adj_night.minute == 0

    # Adjusting early morning rolls forward to same day at 08:00
    adj_early = followups.adjust_for_quiet_hours(dt_early, start_hour=21, end_hour=8)
    assert adj_early.year == 2026 and adj_early.month == 11 and adj_early.day == 2
    assert adj_early.hour == 8 and adj_early.minute == 0

    # Daytime remains unchanged
    adj_day = followups.adjust_for_quiet_hours(dt_day, start_hour=21, end_hour=8)
    assert adj_day == dt_day


def test_imprecise_date_detection_and_confirmation():
    """T04.2: Imprecise date detection and human confirmation gate."""
    followups = _load_communications_followups()

    assert followups.detect_imprecise_date("в начале следующей недели") is True
    assert followups.detect_imprecise_date("на днях передадим КП") is True
    assert followups.detect_imprecise_date("после праздников") is True
    assert followups.detect_imprecise_date("2026-11-05 14:00 MSK") is False

    item = followups.create_followup_item(
        item_id="fu-test-1",
        counterparty="ООО Сибирь",
        commitment_text="График отгрузок",
        source_citation="Email UID 110",
        due_at="в начале следующей недели",
    )
    assert item["status"] == followups.STATUS_REQUIRES_CONFIRMATION
    assert item["requires_date_confirmation"] is True

    # Confirm date
    confirmed = followups.confirm_followup_date(item, "2026-11-09 10:00 MSK", actor_id="operator-1")
    assert confirmed["status"] == followups.STATUS_CONFIRMED
    assert confirmed["requires_date_confirmation"] is False
    assert confirmed["due_at"] == "2026-11-09 10:00 MSK"
    assert confirmed["confirmed_by"] == "operator-1"


def test_communications_jinja_template_strict_rendering():
    """T04.3: StrictUndefined rendering of web/communications.html."""
    env = _build_test_jinja_env()
    template = env.get_template("web/communications.html")
    context = _build_base_template_context()

    outbox = _load_communications_outbox()
    # Evidence is not a claim that a message was verified or sent.
    # Outbox lifecycle coverage above remains unchanged.
    context["evidence_rows"] = [evidence_row("communication")]
    context["boundary"] = "cloud"

    rendered = template.render(**context)
    assert 'data-p12-communications="communications-outbox"' in rendered
    assert "Переписка" in rendered
    assert "Stored test evidence" in rendered
    assert 'data-evidence-context-type="communication"' in rendered
    assert "Маршрут верифицирован" not in rendered
    assert "sha256:fixture" in rendered
    assert "не отправить сообщение" in rendered


def test_followups_jinja_template_strict_rendering():
    """T04.3: StrictUndefined rendering of web/followups.html."""
    env = _build_test_jinja_env()
    template = env.get_template("web/followups.html")
    context = _build_base_template_context()

    followups = _load_communications_followups()
    context["evidence_rows"] = [evidence_row("followup")]
    context["boundary"] = "cloud"

    rendered = template.render(**context)
    assert 'data-p12-followups="followups-catalog"' in rendered
    assert "Договорённости и сроки" in rendered
    assert "Stored test evidence" in rendered
    assert "Test-only evidence text" in rendered
    assert 'data-evidence-context-type="followup"' in rendered
    assert "Катализатор КС-4" not in rendered


@pytest.mark.asyncio
async def test_fastapi_domain_routers_communications_and_followups(monkeypatch):
    """T04.3: Verify FastAPI routers for communications and followups return render response."""
    fake_deps = types.ModuleType("app.web.deps")
    rendered_calls = []

    def fake_render(request, template_name, **extra):
        rendered_calls.append((template_name, extra))
        return NS(template=template_name, context=extra, status_code=200)

    fake_deps.render = fake_render

    comm_web = load_isolated_source(
        "_p12_t04_web_communications",
        ROOT / "app/web/communications.py",
        {
            "app.services.communications.outbox": _load_communications_outbox(),
            "app.web.deps": fake_deps,
            "app.services.knowledge.workspace": workspace_double(),
        },
    )
    followups_web = load_isolated_source(
        "_p12_t04_web_followups",
        ROOT / "app/web/followups.py",
        {
            "app.services.communications.followups": _load_communications_followups(),
            "app.web.deps": fake_deps,
            "app.services.knowledge.workspace": workspace_double(),
        },
    )

    req = NS(state=NS(web_user=NS(id=1)))

    # Test communications router
    resp_comm = await comm_web.communications_page(req, boundary="cloud")
    assert resp_comm.status_code == 200
    assert rendered_calls[-1][0] == "web/communications.html"
    assert rendered_calls[-1][1]["evidence_rows"] == [evidence_row("communication")]

    # Test followups router
    resp_fu = await followups_web.followups_page(req, boundary="cloud")
    assert resp_fu.status_code == 200
    assert rendered_calls[-1][0] == "web/followups.html"
    assert rendered_calls[-1][1]["evidence_rows"] == [evidence_row("followup")]


def test_communications_and_followups_are_registered():
    """The pages must exist in the app, not only as callable handlers.

    This test used to assert the opposite — that the routers were absent from
    `app/web/__init__.py` and the nav entries stayed `ready=False` — which
    locked in a 404 for `/app/communications` and `/app/followups`. P12-T09
    activated them; the application-level check lives in
    `tests/web/test_p12_routes.py`.
    """
    nav_text = (ROOT / "app/web/nav.py").read_text()
    init_text = (ROOT / "app/web/__init__.py").read_text()

    assert '"/app/communications"' in nav_text
    assert '"/app/followups"' in nav_text
    assert "communications_router" in init_text
    assert "followups_router" in init_text
