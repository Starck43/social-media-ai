"""Enforce the workspace's billing tier (`tenants.plan`).

Every quota in `Tenant.PLAN_LIMITS` is checked here, and every write path that
can consume one calls a function from this module. The point of the module is
that there is exactly one answer to "may this workspace add a fourth source?" —
a rule split across `owner.py`, `resolver.py` and an admin view is a rule that
some path forgets.

Two conventions, so callers stay short and messages stay useful:

* each `check_*` returns a **reason string** or `None`. `None` means allowed.
  A caller that blocks shows the string; that is why they are written in the
  user's language and name both the number and the plan, instead of a bare
  "limit reached" the reader cannot act on.
* checks run **inside** `tenant_scope(...)` where the caller is already scoped,
  because counting rows through a scoped manager is what keeps one workspace's
  total from counting another's.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.models.tenant import Tenant

logger = logging.getLogger(__name__)

#: Quota key in `PLAN_LIMITS` -> how a user reads it. Used to build the message
#: so a key like "max_sources" never leaks into the UI.
_UNITS = {
    "max_sources": "источник",
    "max_channels": "канал доставки",
    "max_scenarios": "сценарий агента",
    "max_team_members": "участник команды",
    "max_tasks": "задачу по расписанию",
}

# Russian plurals are not derivable from the singular, and a limit message that
# says "1 источник" for a fifth source reads like a bug to the reader.
_FORMS = {
    "источник": ("источник", "источника", "источников"),
    "канал доставки": ("канал доставки", "канала доставки", "каналов доставки"),
    "сценарий агента": ("сценарий агента", "сценария агента", "сценариев агента"),
    "участник команды": ("участник команды", "участника команды", "участников команды"),
    "задачу по расписанию": ("задачу по расписанию", "задачи по расписанию", "задач по расписанию"),
}


def _plural(unit: str, count: int) -> str:
    """Pick the Russian form agreeing with `count`."""
    forms = _FORMS.get(unit, (unit, unit, unit))
    if count % 10 == 1 and count % 100 != 11:
        return forms[0]
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return forms[1]
    return forms[2]


def _over_limit(tenant: Any, key: str, current: int) -> Optional[str]:
    """The message for `current` already at the ceiling, else None."""
    ceiling = tenant.effective_limits().get(key)
    if ceiling is None or current < ceiling:
        return None
    unit = _UNITS.get(key, key)
    return (
        f"Тариф «{tenant.plan_label}»: максимум {int(ceiling)} {_plural(unit, int(ceiling))} "
        f"— уже {current}. Сменить тариф: /app/settings."
    )


# ═══════════════════════════════════════════════════════════════════════════
# sources
# ═══════════════════════════════════════════════════════════════════════════


async def check_source_limit(tenant: Any) -> Optional[str]:
    """May this workspace add one more source? Call before `Source.objects.create`.

    Deactivated rows still count: the user can bring one back, so ignoring them
    would let a workspace sit over its quota invisibly.
    """
    from app.models.source import Source

    total = await Source.objects.count()
    reason = _over_limit(tenant, "max_sources", total)
    if reason is not None:
        logger.info("Source blocked for tenant %s on plan %s", getattr(tenant, "id", "?"), tenant.plan)
    return reason


# ═══════════════════════════════════════════════════════════════════════════
# channels
# ═══════════════════════════════════════════════════════════════════════════


async def check_channel_limit(tenant: Any) -> Optional[str]:
    """May this workspace bind one more delivery channel?

    Call before `TenantChannelManager.bind` creates a *new* binding. Rebinding a
    chat this workspace already owns is not a new channel and must not be
    refused — that is the idempotent path invite redemption and onboarding take.
    """
    from app.models.managers.tenant_manager import TenantChannelManager

    current = await TenantChannelManager().filter(tenant_id=tenant.id).count()
    reason = _over_limit(tenant, "max_channels", current)
    if reason is not None:
        logger.info("Channel blocked for tenant %s on plan %s", tenant.id, tenant.plan)
    return reason


# ═══════════════════════════════════════════════════════════════════════════
# scenarios
# ═══════════════════════════════════════════════════════════════════════════


async def check_scenario_limit(tenant: Any) -> Optional[str]:
    """May this workspace create one more agent scenario?"""
    from app.models.agent_scenario import AgentScenario

    total = await AgentScenario.objects.count()
    reason = _over_limit(tenant, "max_scenarios", total)
    if reason is not None:
        logger.info("Scenario blocked for tenant %s on plan %s", tenant.id, tenant.plan)
    return reason


# ═══════════════════════════════════════════════════════════════════════════
# team members
# ═══════════════════════════════════════════════════════════════════════════


async def check_member_limit(tenant: Any) -> Optional[str]:
    """May this workspace add one more member?

    Counts web memberships only: `tenant_users` also holds messenger identities
    bound by invite redemption, and charging a plan for the same person reaching
    the workspace through two surfaces would make the number unreadable.
    """
    from app.models.managers.tenant_manager import TenantUserManager

    current = len(await TenantUserManager().web_memberships_for_tenant(tenant.id))
    reason = _over_limit(tenant, "max_team_members", current)
    if reason is not None:
        logger.info("Member blocked for tenant %s on plan %s", tenant.id, tenant.plan)
    return reason


# ═══════════════════════════════════════════════════════════════════════════
# scheduled tasks
# ═══════════════════════════════════════════════════════════════════════════


async def check_task_limit(tenant: Any) -> Optional[str]:
    """May this workspace create one more scheduled task?"""
    from app.models.agent_task import AgentTask

    total = await AgentTask.objects.count()
    reason = _over_limit(tenant, "max_tasks", total)
    if reason is not None:
        logger.info("Task blocked for tenant %s on plan %s", tenant.id, tenant.plan)
    return reason


# ═══════════════════════════════════════════════════════════════════════════
# features (a flag, not a quota — nothing to count)
# ═══════════════════════════════════════════════════════════════════════════


def check_feature(tenant: Any, feature: str, *, what: str) -> Optional[str]:
    """A plan boolean rendered as a message, or None when the tier includes it."""
    if tenant.has_feature(feature):
        return None
    return (
        f"Функция «{what}» доступна на тарифах Pro и Business. "
        f"Текущий тариф: «{tenant.plan_label}»."
    )


def check_daily_cost(tenant: Any, spent: float) -> Optional[str]:
    """Whether the workspace has room for another LLM call today (UTC day)."""
    limit = tenant.effective_limits().get("daily_cost_limit")
    if not limit or spent < limit:
        return None
    return f"Дневной лимит LLM исчерпан (${spent:.2f} из ${limit:.2f}). Тариф «{tenant.plan_label}»."


def check_model_type(tenant: Any, model_type: str) -> Optional[str]:
    """Whether this tier may route calls to a model of that type."""
    if tenant.allows_model_type(model_type):
        return None
    return (
        f"Тип моделей «{model_type}» недоступен на тарифе «{tenant.plan_label}» "
        f"(только text)."
    )


# ═══════════════════════════════════════════════════════════════════════════
# plan changes
# ═══════════════════════════════════════════════════════════════════════════


async def plan_overage(tenant_id: int, plan: str) -> list[str]:
    """What a workspace would exceed by moving to `plan`, in plain words.

    A downgrade is *allowed* to leave a workspace over its new ceiling: refusing
    the change would trap a customer who has to downgrade precisely because they
    are over budget. So the answer is not "yes/no" but "delete these first",
    which the admin shows before saving.
    """
    from app.core.tenant_context import tenant_scope
    from app.models.agent_scenario import AgentScenario
    from app.models.agent_task import AgentTask
    from app.models.managers.tenant_manager import TenantChannelManager, TenantUserManager, tenants
    from app.models.source import Source

    tenant = await tenants.get(id=tenant_id)
    if tenant is None:
        return []

    # Scope the counts to the workspace being moved. Without this a superuser
    # who is *viewing* one workspace while changing another's would get the
    # active scope's numbers reported against the target.
    with tenant_scope(tenant_id):
        counts = {
            "max_sources": await Source.objects.count(),
            "max_channels": await TenantChannelManager().count(),
            "max_scenarios": await AgentScenario.objects.count(),
            "max_team_members": len(await TenantUserManager().web_memberships_for_tenant(tenant_id)),
            "max_tasks": await AgentTask.objects.count(),
        }

    # Preview against the target plan, so the numbers come from the same
    # `effective_limits()` the runtime will use once the change is saved.
    tenant.plan = Tenant.normalize_plan(plan)

    over: list[str] = []
    for key, current in counts.items():
        ceiling = tenant.effective_limits().get(key)
        if ceiling is not None and current > ceiling:
            unit = _plural(_UNITS.get(key, key), int(ceiling))
            over.append(f"{_plural(_UNITS.get(key, key), current)} при максимуме {unit}")
    return over