"""Import-only checks for canonical ownership and compatibility entry points."""

import importlib
import json
import subprocess
import sys

import pytest

DOMAINS = {
    "agent": {
        "agent_feedback": "AgentFeedback",
        "agent_memory": "AgentMemory",
        "agent_message": "AgentMessage",
        "agent_session": "AgentSession",
    },
    "analysis": {"agent_scenario": "AgentScenario", "ai_analytics": "AIAnalytics"},
    "billing": {
        "billing": "PersonalBillingAccount",
        "dispatch": "PersonalLLMDispatch",
    },
    "collection": {"collected_item": "CollectedItem", "platform": "Platform", "source": "Source"},
    "identity": {
        "model_type": "ModelType",
        "permission": "Permission",
        "role": "PlatformRole",
        "user": "User",
        "user_credential": "UserCredential",
    },
    "llm": {
        "llm_attempt": "LLMAttempt",
        "llm_budget": "LLMBudgetReservation",
        "llm_dispatch_receipt": "LLMDispatchReceipt",
        "llm_model": "LLMModel",
        "llm_outcome_event": "LLMOutcomeEvent",
        "llm_provider": "LLMProvider",
    },
    "notifications": {
        "digest_run": "DigestRun",
        "notification": "Notification",
        "notification_preference": "NotificationPreference",
    },
    "scheduling": {"agent_task": "AgentTask", "bot_action": "BotAction", "job": "Job"},
    "tenancy": {
        "tenant": "Tenant",
        "tenant_user": "TenantUser",
        "tenant_invite": "TenantInvite",
        "tenant_channel": "TenantChannel",
        "workspace_role": "WorkspaceRole",
    },
}

# New domain modules have no legacy flat `app.models.<module>` facade for the
# canonical class; their domain/package identity is still asserted below.
NO_LEGACY_FLAT_MODULE = {
    ("billing", "billing"),
    ("billing", "dispatch"),
    ("notifications", "notification_preference"),
}


@pytest.mark.parametrize("domain", DOMAINS)
def test_canonical_classes_keep_legacy_identity_and_manager_binding(domain):
    public = importlib.import_module("app.models")
    for module, name in DOMAINS[domain].items():
        cls = getattr(public, name)
        assert cls is getattr(importlib.import_module(f"app.models.{domain}"), name)
        assert cls is getattr(importlib.import_module(f"app.models.{domain}.{module}"), name)
        legacy = "tenant" if domain == "tenancy" and name != "WorkspaceRole" else module
        if name != "WorkspaceRole" and (domain, module) not in NO_LEGACY_FLAT_MODULE:
            assert cls is getattr(importlib.import_module(f"app.models.{legacy}"), name)
        assert cls.__module__ == f"app.models.{domain}.{module}"
        assert cls.objects.model is cls
        assert sum(mapper.class_ is cls for mapper in public.Base.registry.mappers) == 1


def test_task_payer_consent_has_one_canonical_registered_class():
    public = importlib.import_module("app.models")
    cls = public.TaskBillingPayer
    assert cls is importlib.import_module("app.models.billing").TaskBillingPayer
    assert cls is importlib.import_module("app.models.billing.billing").TaskBillingPayer
    assert cls.objects.model is cls
    assert sum(mapper.class_ is cls for mapper in public.Base.registry.mappers) == 1


@pytest.mark.parametrize("entry", ["app.models", *[f"app.models.{domain}" for domain in DOMAINS]])
def test_fresh_process_import_configures_all_mappers(entry):
    code = f"""
import importlib
import json
from sqlalchemy.orm import configure_mappers
importlib.import_module({entry!r})
from app.models import Base, Role, PlatformRole, LLMBudgetDay
configure_mappers()
assert Role is PlatformRole
assert LLMBudgetDay.objects.model is LLMBudgetDay
assert len(list(Base.registry.mappers)) == 44
assert len(Base.metadata.tables) == 47
print(json.dumps(sorted(Base.metadata.tables)))
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert len(json.loads(result.stdout.strip().splitlines()[-1])) == 47
