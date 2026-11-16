"""P2-T15 API inspection, preview and structured denial responses."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.tasks import get_task_entitlements, preview_task_plan, run_task_plan
from app.schemas.task import TaskPlanPreviewRequest, TaskPlanRunRequest
from app.services.tenancy.entitlements import EntitlementDecision, EntitlementDenied

__source_only__ = True

CAPABILITY = "connector.vk.search"


def _plan():
    return {
        "version": 1,
        "goal": "Research VK",
        "platforms": ["vk"],
        "source_scope": "all",
        "source_ids": [],
        "collection": {"queries": ["repair"]},
        "window": {"type": "rolling", "days": 1},
        "trigger": {"type": "once"},
    }


def _denied_decision():
    return EntitlementDecision(
        allowed=False,
        plan="starter",
        catalog_version=1,
        required=(CAPABILITY,),
        denials=({"capability": CAPABILITY, "reason": "not_granted"},),
    )


async def test_workspace_inspection_returns_catalog_and_live_decision(monkeypatch):
    expected = {
        "plan": "starter",
        "catalog_version": 1,
        "grants": ["*"],
        "decision": {"allowed": True, "required": [CAPABILITY], "denials": []},
    }
    inspect = AsyncMock(return_value=expected)
    monkeypatch.setattr(
        "app.models.managers.entitlement_manager.EntitlementManager", lambda: SimpleNamespace(inspect=inspect)
    )

    result = await get_task_entitlements(_user=SimpleNamespace(id=7))

    assert result == expected
    inspect.assert_awaited_once_with()


async def test_plan_preview_preserves_required_keys_and_denial_reasons(monkeypatch):
    decision = _denied_decision().to_dict()
    monkeypatch.setattr(
        "app.services.collection.entitlement_policy.preview_entitlements", AsyncMock(return_value=decision)
    )
    monkeypatch.setattr(
        "app.services.social.vk_task_access.preview_vk_access",
        AsyncMock(return_value={"checked": False, "reason": "legacy_source_access"}),
    )

    response = await preview_task_plan(
        TaskPlanPreviewRequest(job_type="collect", task_plan=_plan()),
        _user=SimpleNamespace(id=7),
    )

    assert response.entitlements == decision
    assert response.entitlements["required"] == (CAPABILITY,)
    assert response.entitlements["denials"] == ({"capability": CAPABILITY, "reason": "not_granted"},)


async def test_launch_denial_is_a_403_with_the_canonical_decision(monkeypatch):
    decision = _denied_decision()
    launch = AsyncMock(side_effect=EntitlementDenied(decision))
    monkeypatch.setattr("app.services.task_launch.launch_social_task", launch)
    monkeypatch.setattr("app.core.permissions.has_permission", lambda *_args: True)

    with pytest.raises(HTTPException) as raised:
        await run_task_plan(
            TaskPlanRunRequest(job_type="collect", task_plan=_plan(), request_id="p2-t15-request"),
            _user=SimpleNamespace(id=7),
        )

    assert raised.value.status_code == 403
    assert raised.value.detail == decision.to_dict()
    assert "entitlement_denied" not in str(raised.value.detail)
