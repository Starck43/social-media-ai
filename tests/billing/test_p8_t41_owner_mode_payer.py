"""Owner-mode payer resolution when personal billing is disabled.

Source-only: real `app/services/billing/payer.py` over local doubles. No DB,
provider or personal account is touched.
"""

import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import Column, Integer

from tests.source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]


def _module(name, **attrs):
    module = ModuleType(name)
    module.__path__ = []
    module.__dict__.update(attrs)
    return module


class OwnerModePayerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = {
            "enabled": False,
            "actor": SimpleNamespace(id=7),
            "tenant": 3,
            "user": SimpleNamespace(id=7, is_active=True),
            "memberships": [SimpleNamespace(tenant_id=3)],
            "job_initiator": 7,
        }
        self.manager = SimpleNamespace(
            consent_task_payer=AsyncMock(return_value=SimpleNamespace(user_id=7)),
            resolve_task_payer=AsyncMock(return_value=SimpleNamespace(user_id=7)),
        )
        imports = {
            "app.core.config": _module("config", settings=SimpleNamespace(PERSONAL_BILLING_ENABLED=None)),
            "app.core.permissions": _module("permissions", get_current_user=lambda: self.state["actor"]),
            "app.core.tenant_context": _module("tenant_context", current_tenant_id=lambda: self.state["tenant"]),
            "app.core.database": _module("database", async_session_maker=self._session),
            "app.models": _module(
                "models",
                User=SimpleNamespace(objects=SimpleNamespace(get=AsyncMock(side_effect=self._get_user))),
                Job=SimpleNamespace(
                    initiated_by_user_id=Column("initiated_by_user_id", Integer),
                    agent_task_id=Column("agent_task_id", Integer),
                    tenant_id=Column("tenant_id", Integer),
                    id=Column("id", Integer),
                ),
                PersonalBillingAccount=SimpleNamespace(objects=self.manager),
            ),
            "app.models.managers.tenant_manager": _module(
                "tenant_manager",
                tenant_users=SimpleNamespace(web_memberships=AsyncMock(side_effect=self._memberships)),
            ),
            "app.services.billing": _module("billing"),
        }
        imports["app.core.config"].settings.PERSONAL_BILLING_ENABLED = False
        self.payer = load_isolated_source("_p8_t41_owner_payer", ROOT / "app/services/billing/payer.py", imports)

    def _session(self):
        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def execute(self, _statement):
                return SimpleNamespace(scalar_one_or_none=lambda: self.state["job_initiator"])

        state = self.state
        session = Session()
        session.state = state
        return session

    async def _get_user(self, id, is_active):
        assert is_active is True
        return self.state["user"] if id == 7 else None

    async def _memberships(self, user_id):
        return self.state["memberships"]

    def _set_enabled(self, value):
        self.payer.__isolated_imports__["app.core.config"].settings.PERSONAL_BILLING_ENABLED = value

    async def test_consent_uses_active_actor_without_a_personal_account(self):
        result = await self.payer.consent_current_user_as_task_payer(42)
        self.assertEqual(result.user_id, 7)
        self.assertEqual(result.payer_user_id, 7)
        self.assertEqual(result.agent_task_id, 42)
        self.manager.consent_task_payer.assert_not_awaited()

    async def test_consent_refuses_inactive_actor(self):
        self.state["actor"] = None
        with self.assertRaises(PermissionError):
            await self.payer.consent_current_user_as_task_payer(42)

    async def test_worker_resolves_job_initiator_without_stored_consent(self):
        result = await self.payer.resolve_scheduled_task_payer(task_id=42, tenant_id=3)
        self.assertEqual(result.user_id, 7)
        self.manager.resolve_task_payer.assert_not_awaited()

    async def test_worker_refuses_when_job_has_no_trusted_initiator(self):
        self.state["job_initiator"] = None
        with self.assertRaises(PermissionError):
            await self.payer.resolve_scheduled_task_payer(task_id=42, tenant_id=3)

    async def test_worker_refuses_revoked_membership(self):
        self.state["memberships"] = [SimpleNamespace(tenant_id=99)]
        with self.assertRaises(PermissionError):
            await self.payer.resolve_scheduled_task_payer(task_id=42, tenant_id=3)

    async def test_enabled_flag_still_uses_the_stored_payer(self):
        self._set_enabled(True)
        result = await self.payer.consent_current_user_as_task_payer(42)
        self.assertEqual(result.user_id, 7)
        self.manager.consent_task_payer.assert_awaited_once_with(42)
        self.manager.resolve_task_payer.assert_not_awaited()
        await self.payer.resolve_scheduled_task_payer(task_id=42, tenant_id=3)
        self.manager.resolve_task_payer.assert_awaited_once_with(task_id=42, tenant_id=3)
