"""Pure P2-T15 capability catalog and entitlement decision contract."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.tenancy import entitlements
from app.services.tenancy.capabilities import CAPABILITIES
from app.services.tenancy.entitlements import EntitlementDenied, catalog_summary, decide
from app.services.tenancy.plan_catalog import CATALOG_VERSION, PLAN_CATALOG, PlanGrant, grants_capability

__source_only__ = True

NOW = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
VK_READ = "connector.vk.read"
VK_SEARCH = "connector.vk.search"
TG_READ = "connector.telegram.read"


def _workspace(**overrides):
    values = {"plan": "pro", "plan_expires_at": None, "is_active": True}
    values.update(overrides)
    return SimpleNamespace(**values)


def _reasons(decision):
    return {item["capability"]: item["reason"] for item in decision.denials}


@pytest.mark.parametrize("plan", ["starter", "pro", "business"])
def test_catalog_v1_preserves_all_existing_plans_without_daily_quotas(plan):
    grant = PLAN_CATALOG[plan]

    assert CATALOG_VERSION == 1
    assert grant.capabilities == ("*",)
    assert grant.daily_quotas == {}
    assert decide(_workspace(plan=plan), CAPABILITIES, now=NOW).allowed is True


@pytest.mark.parametrize(
    ("pattern", "capability", "expected"),
    [
        ("*", VK_READ, True),
        (VK_READ, VK_READ, True),
        ("connector.vk.*", VK_READ, True),
        ("connector.vk.*", VK_SEARCH, True),
        ("connector.vk.*", TG_READ, False),
        ("connector.*", TG_READ, True),
        ("connector.v*.read", VK_READ, False),
        ("connector.vk", VK_READ, False),
    ],
)
def test_only_exact_namespace_and_global_patterns_are_supported(pattern, capability, expected):
    assert grants_capability(pattern, capability) is expected


def test_unknown_capability_fails_closed_even_under_global_grant():
    decision = decide(_workspace(), ["connector.future.read"], now=NOW)

    assert decision.allowed is False
    assert _reasons(decision) == {"connector.future.read": "unknown_capability"}


@pytest.mark.parametrize(
    ("workspace", "reason"),
    [
        (_workspace(plan="future"), "unknown_plan"),
        (_workspace(is_active=False), "workspace_inactive"),
        (None, "workspace_inactive"),
    ],
)
def test_unknown_plan_and_unavailable_workspace_fail_closed(workspace, reason):
    decision = decide(workspace, [VK_READ], now=NOW)

    assert decision.allowed is False
    assert _reasons(decision) == {VK_READ: reason}


def test_expiry_boundary_is_inclusive_and_future_expiry_is_allowed():
    at_boundary = decide(_workspace(plan_expires_at=NOW), [VK_READ], now=NOW)
    future = decide(_workspace(plan_expires_at=NOW + timedelta(microseconds=1)), [VK_READ], now=NOW)

    assert _reasons(at_boundary) == {VK_READ: "plan_expired"}
    assert future.allowed is True


def test_naive_expiry_fails_closed_and_naive_decision_clock_is_rejected():
    naive_expiry = decide(_workspace(plan_expires_at=NOW.replace(tzinfo=None)), [VK_READ], now=NOW)

    assert _reasons(naive_expiry) == {VK_READ: "plan_expired"}
    with pytest.raises(ValueError, match="aware timestamp"):
        decide(_workspace(), [VK_READ], now=NOW.replace(tzinfo=None))


def test_required_keys_are_deduplicated_and_stably_sorted():
    decision = decide(_workspace(), [VK_SEARCH, VK_READ, VK_SEARCH], now=NOW)

    assert decision.allowed is True
    assert decision.required == (VK_READ, VK_SEARCH)


def test_exact_and_namespace_grants_deny_ungranted_registered_keys(monkeypatch):
    monkeypatch.setitem(PLAN_CATALOG, "pro", PlanGrant(("connector.vk.*",)))

    decision = decide(_workspace(), [VK_READ, VK_SEARCH, TG_READ], now=NOW)

    assert decision.allowed is False
    assert _reasons(decision) == {TG_READ: "not_granted"}


@pytest.mark.parametrize("invalid", [-1, True, 1.5, "1", None])
def test_invalid_quota_configuration_fails_closed_even_for_reserved_job(monkeypatch, invalid):
    monkeypatch.setitem(PLAN_CATALOG, "pro", PlanGrant(("*",), {VK_READ: invalid}))

    decision = decide(_workspace(), [VK_READ], used_today={VK_READ: 10}, reserved={VK_READ}, now=NOW)

    assert decision.allowed is False
    assert _reasons(decision) == {VK_READ: "invalid_quota"}


def test_daily_quota_counts_distinct_jobs_but_not_the_same_job_reservation(monkeypatch):
    monkeypatch.setitem(PLAN_CATALOG, "pro", PlanGrant(("*",), {VK_READ: 1}))

    exhausted = decide(_workspace(), [VK_READ], used_today={VK_READ: 1}, now=NOW)
    repeated_gate = decide(
        _workspace(),
        [VK_READ],
        used_today={VK_READ: 1},
        reserved={VK_READ},
        now=NOW,
    )

    assert _reasons(exhausted) == {VK_READ: "quota_exhausted"}
    assert repeated_gate.allowed is True


@pytest.mark.parametrize(
    ("workspace", "grant", "reason"),
    [
        (_workspace(is_active=False), PlanGrant(("*",)), "workspace_inactive"),
        (_workspace(plan_expires_at=NOW), PlanGrant(("*",)), "plan_expired"),
        (_workspace(), PlanGrant((TG_READ,)), "not_granted"),
    ],
)
def test_reservation_never_bypasses_current_workspace_or_grant_policy(monkeypatch, workspace, grant, reason):
    monkeypatch.setitem(PLAN_CATALOG, workspace.plan, grant)

    decision = decide(workspace, [VK_READ], reserved={VK_READ}, now=NOW)

    assert _reasons(decision) == {VK_READ: reason}


def test_decision_and_exception_expose_bounded_structured_denial():
    decision = decide(_workspace(plan="unknown"), [VK_READ], now=NOW)
    error = EntitlementDenied(decision)

    assert decision.to_dict() == {
        "allowed": False,
        "plan": "unknown",
        "catalog_version": CATALOG_VERSION,
        "required": (VK_READ,),
        "denials": ({"capability": VK_READ, "reason": "unknown_plan"},),
    }
    assert str(error) == f"entitlement_denied (unknown, v{CATALOG_VERSION}): {VK_READ}:unknown_plan"


def test_catalog_summary_is_static_and_serializes_aware_expiry():
    workspace = _workspace(plan_expires_at=NOW + timedelta(days=1))

    summary = catalog_summary(workspace)

    assert summary == {
        "plan": "pro",
        "catalog_version": CATALOG_VERSION,
        "grants": ["*"],
        "daily_quotas": {},
        "plan_expires_at": "2026-11-13T12:00:00+00:00",
        "capabilities": sorted(CAPABILITIES),
    }


def test_decision_engine_remains_domain_neutral():
    source = __import__("inspect").getsource(entitlements)

    assert "connector.vk" not in source
    assert "connector.telegram" not in source
    assert "Source" not in source
