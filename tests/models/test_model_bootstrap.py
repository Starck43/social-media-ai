"""Import-only checks for canonical ownership and public model entry points."""

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
    "actions": {"agent_action": "BotAction"},
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
        "notification_receipt": "NotificationReceipt",
    },
    "scheduling": {"agent_task": "AgentTask", "job": "Job"},
    "tenancy": {
        "tenant": "Tenant",
        "tenant_user": "TenantUser",
        "tenant_invite": "TenantInvite",
        "tenant_channel": "TenantChannel",
        "workspace_role": "WorkspaceRole",
    },
}

@pytest.mark.parametrize("domain", DOMAINS)
def test_canonical_classes_keep_facade_identity_and_manager_binding(domain):
    public = importlib.import_module("app.models")
    for module, name in DOMAINS[domain].items():
        cls = getattr(public, name)
        assert cls is getattr(importlib.import_module(f"app.models.{domain}"), name)
        assert cls is getattr(importlib.import_module(f"app.models.{domain}.{module}"), name)
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
from app.core.config import settings
from app.models import Base, Role, PlatformRole, LLMBudgetDay
configure_mappers()
assert Role is PlatformRole
assert LLMBudgetDay.objects.model is LLMBudgetDay
# The layout must be complete and coherent, not a hand-copied size: every
# registered mapper belongs to Base and every table sits in the one schema.
for mapper in Base.registry.mappers:
    assert issubclass(mapper.class_, Base)
tables = sorted(Base.metadata.tables)
assert tables
assert {{Base.metadata.tables[name].schema for name in tables}} == {{settings.DB_SCHEMA}}
print(json.dumps(tables))
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    public = importlib.import_module("app.models")
    emitted = json.loads(result.stdout.strip().splitlines()[-1])
    assert emitted
    assert emitted == sorted(emitted)
    assert emitted == sorted(public.Base.metadata.tables)
