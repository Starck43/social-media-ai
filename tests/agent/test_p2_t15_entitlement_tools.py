"""P2-T15 chat tools expose previews and bounded policy denials."""

from unittest.mock import AsyncMock

from app.agent.toolset import tasks
from app.services.tenancy.entitlements import EntitlementDecision, EntitlementDenied

__source_only__ = True

CAPABILITY = "connector.vk.search"


def _decision(reason="plan_expired"):
    return EntitlementDecision(
        allowed=False,
        plan="pro",
        catalog_version=1,
        required=(CAPABILITY,),
        denials=({"capability": CAPABILITY, "reason": reason},),
    )


async def test_search_plan_includes_required_keys_and_denial_reasons(monkeypatch):
    preview = _decision().to_dict()
    monkeypatch.setattr(
        "app.services.collection.entitlement_policy.preview_entitlements", AsyncMock(return_value=preview)
    )
    monkeypatch.setattr(
        "app.services.social.vk_task_access.preview_vk_access",
        AsyncMock(return_value={"checked": False, "reason": "legacy_source_access"}),
    )

    result = await tasks.search_plan("Find repair requests", "vk", "all", queries=["repair"])

    assert result["status"] == "proposal"
    assert result["execution"] == "not_started"
    assert result["entitlements"] == preview


async def test_task_plan_includes_same_workspace_decision(monkeypatch):
    preview = _decision("not_granted").to_dict()
    monkeypatch.setattr(
        "app.services.collection.entitlement_policy.preview_entitlements", AsyncMock(return_value=preview)
    )
    monkeypatch.setattr(
        "app.services.social.vk_task_access.preview_vk_access",
        AsyncMock(return_value={"checked": False, "reason": "legacy_source_access"}),
    )
    plan = {
        "version": 1,
        "goal": "Find repair requests",
        "platforms": ["vk"],
        "source_scope": "all",
        "collection": {"queries": ["repair"]},
        "window": {"type": "rolling", "days": 1},
        "trigger": {"type": "once"},
    }

    result = await tasks.task_plan(plan)

    assert result["status"] == "validated_preview"
    assert result["writes"] is False
    assert result["run_created"] is False
    assert result["entitlements"] == preview


async def test_execute_once_returns_bounded_structured_entitlement_denial(monkeypatch):
    decision = _decision("plan_expired")
    monkeypatch.setattr(
        "app.services.task_launch.launch_social_task", AsyncMock(side_effect=EntitlementDenied(decision))
    )
    plan = {
        "goal": "Find repair requests",
        "platforms": ["vk"],
        "source_scope": "all",
        "collection": {"queries": ["repair"]},
    }

    result = await tasks.task_execute_once(request_id="p2-t15-request", plan=plan, job_type="collect")

    assert result == {
        "error": "Task launch is denied by workspace policy.",
        "code": "entitlement_denied",
        "decision": decision.to_dict(),
    }
