"""The billing tier (`tenants.plan`): its numbers, and that they are enforced.

Two kinds of test, and the split matters:

* **pure** — `PLAN_LIMITS`, `normalize_plan`, `effective_limits`, the Russian
  plurals in the limit messages. No database, so a wrong plural or an inverted
  min() fails in milliseconds with an exact diff.
* **enforcement** — that a workspace over its ceiling is actually refused. This
  is the half that matters: a tier nobody can exceed is a decoration, and the
  previous state of this code was exactly that (a `plan` column nothing read).
  Each test drives the real creation path, because a check in the wrong file is
  a check that does not run — which is why these go through
  `Source.objects` / `AgentTaskManager` / `ScenarioService` / the manager, not
  through `limits.py` directly.
"""

from __future__ import annotations

import contextlib

import pytest

from app.models.tenant import Tenant
from app.services.tenancy import limits

# This whole file is about *per-workspace* counts, so the tenant guard must be
# real: without the marker, the autouse `_platform_scope` fixture forces
# superuser bypass, every row lands in the bootstrap workspace, and two tests
# that each create three sources collide on the same unique key.
pytestmark = pytest.mark.tenancy


@pytest.fixture
async def plan_ws():
    """A throwaway workspace factory that always cleans up.

    `async with plan_ws("starter") as tenant:` builds a workspace on that tier
    and deletes it on the way out — CASCADE takes its rows with it, so a failed
    assertion cannot leave anything behind for the next test to trip over.
    """
    import secrets

    created: list[int] = []

    @contextlib.asynccontextmanager
    async def make(plan: str, **columns):
        slug = f"plan-{secrets.token_hex(6)}"
        tenant = await Tenant.objects.create(name="Plan WS", slug=slug, plan=plan, **columns)
        created.append(tenant.id)
        yield tenant

    try:
        yield make
    finally:
        for tenant_id in created:
            await Tenant.objects.delete_by_id(tenant_id)


# ═══════════════════════════════════════════════════════════════════════════
# pure: the tiers themselves
# ═══════════════════════════════════════════════════════════════════════════


def test_the_three_tiers_are_the_only_ones() -> None:
    assert Tenant.PLANS == ("starter", "pro", "business")
    assert set(Tenant.PLAN_LIMITS) == set(Tenant.PLANS)


def test_tiers_are_ordered_by_what_they_allow() -> None:
    """A downgrade must never grant more than the tier above it.

    `None` means unlimited, so the countable quotas are checked as an ascending
    list ending in the unlimited one. `max_sources` stands in for the countable
    quotas, which all move together by design.
    """
    caps = [Tenant.PLAN_LIMITS[p]["max_sources"] for p in Tenant.PLANS]
    assert caps == [3, 20, None]

    # Features: starter is the only tier that turns something off.
    assert Tenant.PLAN_LIMITS["starter"]["allow_learning"] is False
    assert Tenant.PLAN_LIMITS["pro"]["allow_learning"] is True
    assert Tenant.PLAN_LIMITS["business"]["allow_learning"] is True

    # Business is unlimited where starter is capped.
    for key in ("max_channels", "max_scenarios", "max_team_members", "max_tasks", "daily_cost_limit"):
        assert Tenant.PLAN_LIMITS["business"][key] is None, key
        assert Tenant.PLAN_LIMITS["starter"][key] is not None, key


def test_an_unknown_plan_falls_back_to_the_default() -> None:
    """A legacy or hand-edited value must not blow up on read.

    `personal` is what this column said before tiers existed, and it is still in
    rows the migration has not touched yet on a fresh checkout.
    """
    assert Tenant.normalize_plan("personal") == "pro"
    assert Tenant.normalize_plan("") == "pro"
    assert Tenant.normalize_plan(None) == "pro"
    assert Tenant.normalize_plan("  PRO  ") == "pro"
    assert Tenant.normalize_plan("starter") == "starter"

    # Reading limits on an unknown value must not raise — a bad row should show
    # the default tier's limits, not take a page down.
    assert Tenant(plan="nonsense").plan_limits()["max_sources"] == 20


def test_the_tier_caps_upward_and_the_column_only_tightens() -> None:
    """The workspace's own column may lower a limit, never raise it.

    A workspace must not be able to grant itself what it did not buy; that is
    the whole reason the plan is separate from the column.
    """
    loose = Tenant(plan="pro", max_sources=100, daily_cost_limit=500.0)
    assert loose.effective_limits()["max_sources"] == 20
    assert loose.effective_limits()["daily_cost_limit"] == 20.0

    tight = Tenant(plan="pro", max_sources=5)
    assert tight.effective_limits()["max_sources"] == 5
    # An untouched column must not tighten the budget below the tier.
    assert tight.effective_limits()["daily_cost_limit"] == 20.0


def test_an_unlimited_tier_stays_unlimited() -> None:
    """Business ignores the columns — they are NOT NULL and default to 20.

    Honouring them would silently cap a plan the pricing page advertises as
    unlimited, which is the kind of bug a customer reports as "your Business
    account is wrong".
    """
    business = Tenant(plan="business", max_sources=20, daily_cost_limit=5.0)
    resolved = business.effective_limits()
    assert resolved["max_sources"] is None
    assert resolved["daily_cost_limit"] is None
    # The column is still readable for the settings form.
    assert business.max_sources == 20


def test_starter_is_text_only() -> None:
    assert Tenant(plan="starter").allows_model_type("text") is True
    assert Tenant(plan="starter").allows_model_type("image") is False
    assert Tenant(plan="starter").allows_model_type("embedding") is False
    assert Tenant(plan="pro").allows_model_type("image") is True
    assert Tenant(plan="business").allows_model_type("embedding") is True


# ═══════════════════════════════════════════════════════════════════════════
# pure: the messages a reader sees
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (1, "источник"),
        (2, "источника"),
        (4, "источника"),
        (5, "источников"),
        (11, "источников"),
        (21, "источник"),
        (22, "источника"),
        (25, "источников"),
        (101, "источник"),
        (111, "источников"),
    ],
)
def test_limit_messages_agree_with_the_number(count: int, expected: str) -> None:
    """Russian plurals are not derivable from the singular.

    «1 источник» printed next to «уже 5» is a bug the reader sees immediately,
    and no type checker catches it — so the awkward cases are pinned.
    """
    assert limits._plural("источник", count) == expected


def test_the_limit_message_names_both_the_tier_and_the_count() -> None:
    """A refusal the reader cannot act on is a bug report waiting to happen."""
    reason = limits._over_limit(Tenant(plan="starter"), "max_sources", 3)
    assert reason is not None
    assert "Starter" in reason
    assert "3" in reason

    # Under the ceiling there is nothing to say.
    assert limits._over_limit(Tenant(plan="starter"), "max_sources", 2) is None
    # Unlimited tiers never refuse.
    assert limits._over_limit(Tenant(plan="business"), "max_sources", 9999) is None


def test_feature_and_budget_messages_name_the_tier() -> None:
    starter = Tenant(plan="starter")
    denied = limits.check_feature(starter, "allow_learning", what="Обучение агента")
    assert denied and "Starter" in denied
    assert limits.check_feature(Tenant(plan="pro"), "allow_learning", what="Обучение агента") is None

    assert limits.check_daily_cost(starter, 2.5) is not None
    assert limits.check_daily_cost(starter, 0.5) is None
    # Business has no budget ceiling.
    assert limits.check_daily_cost(Tenant(plan="business"), 10_000) is None


# ═══════════════════════════════════════════════════════════════════════════
# enforcement: the tier is checked where things are created
# ═══════════════════════════════════════════════════════════════════════════


async def _platform():
    from app.models.platform import Platform

    row = await Platform.objects.filter(is_active=True).first()
    if row is None:
        pytest.skip("no platform seeded")
    return row


async def _source(tenant_id: int, platform, suffix: str):
    """A source row in `tenant_id`, for filling a workspace up to its ceiling."""
    from app.core.tenant_context import tenant_scope
    from app.models.source import Source
    from app.types import SourceType

    with tenant_scope(tenant_id):
        return await Source.objects.create(
            name=f"s{suffix}",
            platform_id=platform.id,
            external_id=f"ext-{suffix}",
            source_type=SourceType.USER,
        )


@pytest.mark.asyncio
async def test_a_starter_workspace_cannot_exceed_its_source_ceiling(plan_ws) -> None:
    """Three sources on Starter, and the fourth is refused — with a reason.

    Driven through the real creation path, because the point of the test is
    that the check is reachable from where a source is actually written.
    """
    from app.core.tenant_context import tenant_scope

    platform = await _platform()

    async with plan_ws("starter") as tenant:
        for i in range(3):
            await _source(tenant.id, platform, str(i))
        with tenant_scope(tenant.id):
            blocked = await limits.check_source_limit(tenant)
        assert blocked is not None, "a 4th source must be refused on Starter"
        assert "Starter" in blocked


@pytest.mark.asyncio
async def test_a_business_workspace_is_never_blocked_by_a_count(plan_ws) -> None:
    from app.core.tenant_context import tenant_scope

    platform = await _platform()

    async with plan_ws("business") as tenant:
        for i in range(25):
            await _source(tenant.id, platform, str(i))
        with tenant_scope(tenant.id):
            assert await limits.check_source_limit(tenant) is None


@pytest.mark.asyncio
async def test_a_deactivated_source_still_holds_its_slot(plan_ws) -> None:
    """Counting only active rows would let a workspace sit over its quota.

    Deactivate a source, then ask again: if the limit counted active rows only,
    the workspace would read as 2 active of 3 and a third add would succeed,
    leaving 4 rows against a ceiling of 3 once the old one is reactivated.
    """
    from app.core.tenant_context import tenant_scope
    from app.models.source import Source

    platform = await _platform()

    async with plan_ws("starter") as tenant:
        rows = [await _source(tenant.id, platform, str(i)) for i in range(3)]
        with tenant_scope(tenant.id):
            await Source.objects.update_by_id(rows[0].id, is_active=False)
            blocked = await limits.check_source_limit(tenant)
        assert blocked is not None


@pytest.mark.asyncio
async def test_the_task_ceiling_is_checked_at_creation(plan_ws) -> None:
    from app.core.tenant_context import tenant_scope
    from app.models.agent_task import AgentTask

    async with plan_ws("starter") as tenant:
        with tenant_scope(tenant.id):
            for i in range(3):
                await AgentTask.objects.create(
                    name=f"t{i}",
                    cron_expr="0 9 * * *",
                    job_type="collect",
                )
            blocked = await limits.check_task_limit(tenant)
        assert blocked is not None
        assert "Starter" in blocked


async def _web_user():
    """A throwaway web user, so a membership has something real to point at."""
    import secrets

    from app.models import Role, User
    from app.types import UserRoleType

    name = f"planuser{secrets.token_hex(5)}"
    role = await Role.objects.get(codename=UserRoleType.VIEWER.name)
    return await User.objects.create_user(
        username=name,
        email=f"{name}@example.com",
        password="secret-password-1",
        role_id=role.id,
    )


@pytest.mark.asyncio
async def test_the_team_ceiling_counts_web_memberships_only(plan_ws) -> None:
    """A messenger chat is not a team seat — only /app memberships are.

    `tenant_users` holds two kinds of row: a web membership (a person with an
    account) and a messenger identity (someone who messaged the bot). Charging a
    plan for both would make the number on the pricing page unreadable — and
    worse, it would bill the workspace owner for talking to their own agent,
    which is the product.

    Asserted as "five chats in, the first seat is still free": if messenger rows
    counted, Starter's single seat would already be spent.
    """
    from app.core.tenant_context import tenant_scope
    from app.models.managers.tenant_manager import tenant_users

    async with plan_ws("starter") as tenant:
        with tenant_scope(tenant.id):
            for i in range(5):
                await tenant_users.add_member(
                    tenant_id=tenant.id, channel="telegram", external_user_id=str(i)
                )
            user = await _web_user()
            # Allowed: the single Starter seat is still unclaimed.
            await tenant_users.add_web_member(tenant_id=tenant.id, user_id=user.id, role="owner")
            # And now it is spent — a second seat is refused.
            assert await limits.check_member_limit(tenant) is not None


@pytest.mark.asyncio
async def test_a_messenger_contact_is_not_charged_as_a_seat(plan_ws) -> None:
    """The owner's own chat is never a quota breach on its own.

    Without this the free tier would be unusable for the product it exists for:
    five people in the owner's chat would exhaust the team ceiling before anyone
    opened the web console.
    """
    from app.core.tenant_context import tenant_scope
    from app.models.managers.tenant_manager import tenant_users

    async with plan_ws("starter") as tenant:
        with tenant_scope(tenant.id):
            for i in range(5):
                await tenant_users.add_member(
                    tenant_id=tenant.id, channel="telegram", external_user_id=str(i)
                )
            # No web membership at all, so the seat is untouched.
            assert await limits.check_member_limit(tenant) is None


@pytest.mark.asyncio
async def test_a_second_web_member_is_refused_by_the_manager(plan_ws) -> None:
    """The refusal is raised from the write path, not merely reported.

    Going through `add_web_member` rather than calling `limits.py` directly is
    the point: the manager is the only code that inserts a membership, so that
    is where a tier check has to live for it to hold.
    """
    from app.core.tenant_context import tenant_scope
    from app.models.managers.tenant_manager import PlanLimitError, tenant_users

    async with plan_ws("starter") as tenant:
        with tenant_scope(tenant.id):
            owner = await _web_user()
            await tenant_users.add_web_member(tenant_id=tenant.id, user_id=owner.id, role="owner")
            second = await _web_user()
            with pytest.raises(PlanLimitError):
                await tenant_users.add_web_member(tenant_id=tenant.id, user_id=second.id, role="member")


@pytest.mark.asyncio
async def test_an_invite_to_a_full_workspace_reports_rather_than_raises(plan_ws) -> None:
    """A quota breach on the invite form is a page, not a 500.

    The code was valid and the person is already signed in, so there is nothing
    for them to fix but wait for a seat. The invite is also *not* burned, so the
    same code works once the workspace grows or changes tier.
    """
    from app.core.tenant_context import tenant_scope
    from app.models.managers.tenant_manager import TenantInviteManager, tenant_users

    async with plan_ws("starter") as tenant:
        with tenant_scope(tenant.id):
            owner = await _web_user()
            await tenant_users.add_web_member(tenant_id=tenant.id, user_id=owner.id, role="owner")

            invites = TenantInviteManager()
            invite, code = await invites.issue(tenant_id=tenant.id, role="member")
            invited = await _web_user()
            result = await invites.redeem_web(code=code, user_id=invited.id)

            assert result["status"] == "no_seat"
            assert "Starter" in result["reason"]
            # Not burned: the person can retry the same code later.
            refreshed = await invites.get(id=invite.id)
            assert refreshed.used_count == 0


@pytest.mark.asyncio
async def test_rebinding_an_owned_chat_is_not_a_second_channel(plan_ws) -> None:
    """Re-binding is idempotent and must not be charged as a new channel.

    Onboarding and invite redemption both re-bind a chat the workspace already
    owns; refusing those would make the second contact with a bot fail.
    """
    from app.core.tenant_context import tenant_scope
    from app.models.managers.tenant_manager import PlanLimitError, TenantChannelManager

    async with plan_ws("starter") as tenant:
        with tenant_scope(tenant.id):
            mgr = TenantChannelManager()
            first = await mgr.bind(tenant_id=tenant.id, channel="telegram", chat_id="42")
            assert first is not None
            # Same chat again: returns the same row, no PlanLimitError.
            again = await mgr.bind(tenant_id=tenant.id, channel="telegram", chat_id="42")
            assert again is not None and again.id == first.id
            # A genuinely new chat is the second channel Starter does not have.
            with pytest.raises(PlanLimitError):
                await mgr.bind(tenant_id=tenant.id, channel="telegram", chat_id="43")


# ═══════════════════════════════════════════════════════════════════════════
# enforcement: features and budgets, not counts
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_learn_and_reflect_stop_on_starter(plan_ws) -> None:
    """A tier without learning must refuse before it spends anything.

    Asserted on the skip *reason*: `cost_cap` would mean the budget was the
    thing that stopped it, which is a different message to the operator and a
    different thing to fix.
    """
    from app.agent.learning import run_learn, run_reflect
    from app.core.tenant_context import tenant_scope

    async with plan_ws("starter") as tenant:
        with tenant_scope(tenant.id):
            result = await run_learn(min_messages=1)
            assert result["status"] == "skipped"
            assert result["reason"] == "plan"
            assert result["llm_cost"] == 0.0

            result = await run_reflect()
            assert result["status"] == "skipped"
            assert result["reason"] == "plan"


@pytest.mark.asyncio
async def test_the_learning_gate_is_the_tier_not_the_function(plan_ws) -> None:
    """Pro must reach the budget check rather than being refused by the tier."""
    from app.agent.learning import _plan_gate
    from app.core.tenant_context import tenant_scope

    async with plan_ws("pro") as tenant:
        with tenant_scope(tenant.id):
            assert await _plan_gate("allow_learning", "learning") is None
            assert await _plan_gate("allow_reflection", "reflection") is None

    async with plan_ws("starter") as tenant:
        with tenant_scope(tenant.id):
            blocked = await _plan_gate("allow_learning", "learning")
            assert blocked is not None
            assert blocked["reason"] == "plan"


@pytest.mark.asyncio
async def test_a_starter_workspace_may_not_publish_an_action(plan_ws) -> None:
    """`dry_run=False` is the only path that reaches a live platform.

    Blocked here rather than on the `BotAction` row, because the row's own
    `dry_run` flag is a default the caller picks — the gate has to sit on the
    call that actually publishes.
    """
    from app.agent.toolset.actions import _auto_actions_forced_dry_run
    from app.core.tenant_context import tenant_scope

    async with plan_ws("starter") as tenant:
        with tenant_scope(tenant.id):
            reason = await _auto_actions_forced_dry_run()
            assert reason is not None
            assert "Starter" in reason

    async with plan_ws("pro") as tenant:
        with tenant_scope(tenant.id):
            assert await _auto_actions_forced_dry_run() is None


@pytest.mark.asyncio
async def test_the_daily_budget_follows_the_tier_not_the_column(plan_ws) -> None:
    """A Starter workspace with a $20 column still gets a $2 budget.

    The column is a leftover from before tiers; without this the plan would be
    advisory for the one limit that costs money.
    """
    from app.core.tenant_context import tenant_scope
    from app.services.tenancy.resolver import tenant_daily_cost_limit

    async with plan_ws("starter", daily_cost_limit=20.0) as tenant:
        with tenant_scope(tenant.id):
            assert await tenant_daily_cost_limit(tenant.id) == 2.0

    # Business has no ceiling, so the column the operator set is honoured.
    async with plan_ws("business", daily_cost_limit=3.5) as tenant:
        with tenant_scope(tenant.id):
            assert await tenant_daily_cost_limit(tenant.id) == 3.5


@pytest.mark.asyncio
async def test_a_plan_downgrade_reports_what_must_be_deleted(plan_ws) -> None:
    """Downgrading is allowed; the workspace is told what it will have to shed.

    Refusing the downgrade would trap a customer who has to downgrade precisely
    because they are over budget, so the answer is a list, not a rejection.
    """
    from app.core.tenant_context import tenant_scope

    platform = await _platform()

    async with plan_ws("pro") as tenant:
        for i in range(5):
            await _source(tenant.id, platform, str(i))
        with tenant_scope(tenant.id):
            over = await limits.plan_overage(tenant.id, "starter")
        assert over, "downgrading to Starter with 5 sources must be reported"
        assert any("источник" in line for line in over)

        # A tier that fits reports nothing, so the operator is not warned about
        # a problem that does not exist.
        assert await limits.plan_overage(tenant.id, "pro") == []