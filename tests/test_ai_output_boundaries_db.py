"""Prepared PostgreSQL regressions; use conftest's dedicated test database.

LLM calls are mocked. These cases were not executed in the authoring sandbox.
"""

import json
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from app.agent import learning
from app.core.tenant_context import tenant_scope
from app.models import AgentMemory, AgentMessage, AgentSession, Tenant
from app.models.managers.agent_memory_manager import agent_memory
from app.services.ai import llm_client
from app.services.tenancy import resolver

pytestmark = pytest.mark.tenancy


@pytest.fixture
async def workspaces(monkeypatch):
    tenants = []
    try:
        for _ in range(2):
            tenants.append(await Tenant.objects.create(
                slug=f"output-boundary-{uuid4().hex}", name="Boundary test", plan="business"
            ))
        monkeypatch.setattr(resolver, "current_daily_cost_limit", AsyncMock(return_value=0))
        monkeypatch.setattr(resolver, "daily_cost_today", AsyncMock(return_value=0))
        yield tenants
    finally:
        for tenant in reversed(tenants):
            await Tenant.objects.delete_by_id(tenant.id)


async def user_turn(tenant):
    with tenant_scope(tenant.id):
        session = await AgentSession.objects.create(channel="telegram", chat_id=f"boundary-{uuid4().hex}", kind="private")
        return await AgentMessage.objects.create(session_id=session.id, role="user", content="Use brief reports")


def fake_response(monkeypatch, payload):
    chat = AsyncMock(return_value={"content": json.dumps(payload), "usage": {"cost": 0.12}})
    monkeypatch.setattr(llm_client, "chat_with_fallback", chat)
    return chat


def extracted(message_id, **overrides):
    return {"key": "report_style", "value": "Brief reports", "confidence": 0.8,
            "evidence_id": message_id, **overrides}


async def test_invalid_learn_batch_preserves_database_memory_and_watermark(workspaces, monkeypatch):
    own, _ = workspaces
    row = await user_turn(own)
    fake_response(monkeypatch, {"facts": [extracted(row.id), extracted(row.id, key="second", value={})]})
    with tenant_scope(own.id):
        before = await learning.get_watermark()
        result = await learning.run_learn(min_messages=1)
        assert result["status"] == "failed" and result["llm_cost"] == 0.12
        assert await learning.get_watermark() == before
        assert await AgentMemory.objects.filter(scope="global") == []


async def test_learn_cannot_use_another_workspaces_message_as_evidence(workspaces, monkeypatch):
    own, other = workspaces
    await user_turn(own)
    foreign = await user_turn(other)
    fake_response(monkeypatch, {"facts": [extracted(foreign.id)]})
    with tenant_scope(own.id):
        result = await learning.run_learn(min_messages=1)
        assert result["error"] == "invalid_evidence_reference"
        assert await learning.get_watermark() == 0
        assert await AgentMemory.objects.filter(scope="global") == []
    with tenant_scope(other.id):
        assert await AgentMemory.objects.filter(scope="global") == []


async def test_valid_learn_stores_owned_evidence_and_advances_watermark(workspaces, monkeypatch):
    own, _ = workspaces
    row = await user_turn(own)
    fake_response(monkeypatch, {"facts": [extracted(row.id)]})
    with tenant_scope(own.id):
        result = await learning.run_learn(min_messages=1)
        memory = await AgentMemory.objects.get(scope="global", key="report_style")
        assert result["status"] == "ok" and result["facts_stored"] == 1
        assert memory.tenant_id == own.id and memory.evidence_message_id == row.id
        assert await learning.get_watermark() == row.id


async def test_foreign_reflection_operation_rejects_even_the_valid_delete(workspaces, monkeypatch):
    own, other = workspaces
    with tenant_scope(own.id):
        await agent_memory.write("style", "own", source="learn")
        original = await AgentMemory.objects.get(scope="global", key="style")
    with tenant_scope(other.id):
        await agent_memory.write("style", "foreign", source="learn")
        foreign = await AgentMemory.objects.get(scope="global", key="style")
    fake_response(monkeypatch, {"ops": [{"op": "delete", "id": original.id}, {"op": "delete", "id": foreign.id}]})
    with tenant_scope(own.id):
        result = await learning.run_reflect()
        assert result["error"] == "invalid_memory_reference" and result["llm_cost"] == 0.12
        assert (await AgentMemory.objects.get(id=original.id)).value == "own"
    with tenant_scope(other.id):
        assert (await AgentMemory.objects.get(id=foreign.id)).value == "foreign"


async def test_readonly_reflection_returns_advice_without_database_mutation(workspaces, monkeypatch):
    own, _ = workspaces
    with tenant_scope(own.id):
        await agent_memory.write("style", "old", source="learn")
        original = await AgentMemory.objects.get(scope="global", key="style")
    fake_response(monkeypatch, {"ops": [{"op": "delete", "id": original.id}], "prompt_advice": "Review report style"})
    with tenant_scope(own.id):
        result = await learning.run_reflect(dedup=False)
        assert result["deleted"] == 0 and result["updated"] == 0
        assert result["prompt_advice"] == "Review report style"
        assert (await AgentMemory.objects.get(id=original.id)).value == "old"
