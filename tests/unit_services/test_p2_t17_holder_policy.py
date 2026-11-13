"""P2-T17 increment A: validated policy snapshot and trusted holder resolution.

Pure source-only contract: no project database, no runtime, no provider call and
no network. The session double runs every statement it is handed against a
fresh **in-memory SQLite** engine built from the same `Table` metadata, so
`execute()` returns real `sqlalchemy.engine.Row` objects - the exact shape a
driver produces - for the rows SQLite actually matches. Row shape, duplicate
handling and `WHERE` clauses are therefore observed on executed SQL rather than
on a hand-built tuple, and `scalar()` mirrors SQLAlchemy and returns the first
column only.
"""

import dataclasses
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from types import MappingProxyType, SimpleNamespace

import pytest
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, MetaData, column, create_engine, insert, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Row
from sqlalchemy.sql import visitors

from app.models.identity.user import User
from app.models.tenancy.service_plan import ServicePlanPolicy, UserServicePlan
from app.models.tenancy.tenant import Tenant
from app.models.tenancy.tenant_user import TenantUser
from app.services.tenancy.service_plan_policy import (
    ServicePlanPolicyError,
    TokenAllowancePolicy,
    ValidatedPlanPolicy,
    WorkspacePlanContext,
    load_workspace_policy,
    policy_snapshot,
    resolve_holder_context,
)

__source_only__ = True

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
HOLDER, ACTOR, TENANT = 7, 11, 100
#: Identities above CPython's small-int cache (-5..256): equal values are still
#: distinct objects, so an `is` comparison would be False for them.
BIG_USER, BIG_ACTOR, BIG_TENANT = 900_001, 900_002, 900_100
MAX_BIGINT = 2**63 - 1
TABLES = (User, TenantUser, Tenant, UserServicePlan, ServicePlanPolicy)


# ── doubles ──────────────────────────────────────────────────────────────────


def _resources(**overrides):
    """A realistic, complete `resource_limits` payload (business tier shape)."""
    values = {
        "max_sources": 20,
        "daily_cost_limit": 20.0,
        "max_channels": 5,
        "max_scenarios": 10,
        "max_team_members": 5,
        "max_tasks": 20,
        "analytics_depth": ["sentiment", "topics"],
        "allow_auto_actions": True,
        "allow_learning": True,
        "allow_reflection": True,
        "retention_days": 30,
        "model_types": ["text", "image"],
        "label": "Pro",
    }
    values.update(overrides)
    return {key: value for key, value in values.items() if value is not _DROP}


class _Drop:
    pass


_DROP = _Drop()


def _policy_row(**overrides):
    values = dict(
        plan_code="pro",
        policy_version=3,
        module_grants=["*"],
        daily_quotas={},
        resource_limits=_resources(),
        owned_workspace_limit_configured=True,
        max_owned_workspaces=4,
        token_policy_configured=True,
        token_unit="token",
        token_period="utc_day",
        token_limit=1000,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _plan_row(**overrides):
    values = dict(user_id=HOLDER, plan_code="pro", policy_version=3, is_active=True, plan_expires_at=None)
    values.update(overrides)
    return SimpleNamespace(**values)


def _user_values(uid, active):
    """Every `NOT NULL` column of `users`; readonly identity fields stay unique."""
    return {
        "id": uid,
        "username": f"user-{uid}",
        "email": f"user-{uid}@example.invalid",
        "hashed_password": "x",
        "is_active": active,
        "role_id": 1,
    }


def _member_values(tenant_id, user, channel, active):
    return {
        "tenant_id": tenant_id,
        "user_id": user,
        "channel": channel,
        "external_user_id": f"{channel}-{user}",
        "is_active": active,
    }


def _workspace_values(tenant_id, holder, active):
    return {
        "id": tenant_id,
        "name": f"workspace-{tenant_id}",
        "slug": f"workspace-{tenant_id}",
        "plan_holder_user_id": holder,
        "is_active": active,
    }


def _translated():
    """The five tables alone, schema-qualified and without out-of-scope FKs.

    The resolver imports these `Table` objects; SQLite only needs the columns.
    Foreign keys point at tables this source-only test does not load (`roles`,
    `workspaces`), so they are dropped in the copy instead of being resolved.
    """
    metadata = MetaData()

    def keep_schema(table, schema, constraint, referred_schema):
        return schema

    for model in TABLES:
        table = model.__table__.to_metadata(metadata, referred_schema_fn=keep_schema)
        for constraint in list(table.constraints):
            if isinstance(constraint, (ForeignKeyConstraint, CheckConstraint)):
                table.constraints.discard(constraint)
    return metadata


class _Result:
    """The slice of `sqlalchemy.engine.Result` this module is allowed to use."""

    def __init__(self, result):
        self._result = result

    def one_or_none(self):
        return self._result.one_or_none()

    def first(self):
        return self._result.first()


class _Session:
    """Session double that executes statements on in-memory SQLite.

    `execute()` runs the real statement, so `one_or_none()` raises exactly where
    PostgreSQL would see two rows, `limit(1)`/`first()` tolerate legitimate
    duplicates, and every returned row is a genuine `sqlalchemy.engine.Row`
    built from the *selected columns*. `scalar()` mirrors SQLAlchemy and returns
    the first column of the first row.
    """

    def __init__(
        self,
        *,
        users=((ACTOR, True), (HOLDER, True)),
        members=((TENANT, ACTOR, True),),
        workspace=(TENANT, HOLDER, True),
        plan=None,
        policy=None,
    ):
        """`members` is `(tenant, user, active)` or `(tenant, user, channel, active)`."""
        memberships = [
            (*row[:2], "web", row[2]) if len(row) == 3 else (row[0], row[1], row[2], row[3]) for row in members
        ]
        self.records = {
            User: [_user_values(uid, active) for uid, active in users],
            TenantUser: [_member_values(*row) for row in memberships],
            Tenant: [] if workspace is None else [_workspace_values(*workspace)],
            UserServicePlan: [] if plan is None else [plan],
            ServicePlanPolicy: [] if policy is None else [policy],
        }
        self.calls = []
        self.engine = create_engine("sqlite://").execution_options(schema_translate_map={Tenant.__table__.schema: None})
        _translated().create_all(self.engine)
        self.connection = self.engine.connect()
        for model, rows in self.records.items():
            for row in rows:
                values = vars(row) if isinstance(row, SimpleNamespace) else row
                self.connection.execute(insert(model.__table__).values(**values))

    def close(self):
        self.connection.close()
        self.engine.dispose()

    async def execute(self, stmt):
        self.calls.append(stmt)
        return _Result(self.connection.execute(stmt))

    async def scalar(self, stmt):
        self.calls.append(stmt)
        entity = stmt.column_descriptions[0].get("entity")
        if entity in (UserServicePlan, ServicePlanPolicy):
            # Core executes the actual WHERE, while AsyncSession's ORM layer
            # returns the entity, not the first integer column. Keep the fixture's
            # aware timestamps (SQLite cannot model PostgreSQL timestamptz).
            row = self.connection.execute(stmt).first()
            return self.records[entity][0] if row is not None else None
        return self.connection.execute(stmt).scalar()

    def statements(self, model):
        return [
            stmt
            for stmt in self.calls
            if any(getattr(item, "table", None) is model.__table__ for item in visitors.iterate(stmt))
        ]

    def projected(self, model):
        return [
            stmt
            for stmt in self.calls
            if {getattr(c, "table", None) for c in stmt.selected_columns} == {model.__table__}
        ]


def _world(**overrides):
    values = dict(plan=_plan_row(), policy=_policy_row())
    values.update(overrides)
    return _Session(**values)


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def _selected_keys(stmt):
    return {column.key for column in stmt.selected_columns}


# ── snapshot: token allowance ───────────────────────────────────────────────


def test_finite_token_limit_is_exact_and_versioned():
    policy = policy_snapshot(_policy_row(token_limit=MAX_BIGINT))
    assert policy.token_policy == TokenAllowancePolicy("token", "utc_day", MAX_BIGINT, 3)


def test_zero_limit_is_a_denial_not_an_absence():
    policy = policy_snapshot(_policy_row(token_limit=0))
    assert policy.token_policy is not None and policy.token_policy.token_limit == 0


def test_null_limit_under_a_configured_policy_is_explicitly_unlimited():
    policy = policy_snapshot(_policy_row(token_limit=None))
    assert policy.token_policy is not None and policy.token_policy.token_limit is None


def test_unconfigured_token_definition_is_none_never_unlimited():
    policy = policy_snapshot(
        _policy_row(token_policy_configured=False, token_unit=None, token_period=None, token_limit=None)
    )
    assert policy.token_policy is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"token_unit": None},
        {"token_period": ""},
        {"token_unit": "   "},
        {"token_unit": "x" * 81},
        {"token_limit": -1},
        {"token_limit": MAX_BIGINT + 1},
        {"token_limit": 1.0},
        {"token_limit": True},
        {"token_policy_configured": 1},
    ],
)
def test_malformed_token_definition_fails_closed(overrides):
    with pytest.raises(ServicePlanPolicyError) as error:
        policy_snapshot(_policy_row(**overrides))
    assert error.value.reason == "invalid_token_policy"


def test_unconfigured_policy_with_leftover_values_is_malformed():
    with pytest.raises(ServicePlanPolicyError, match="invalid_token_policy"):
        policy_snapshot(_policy_row(token_policy_configured=False))


# ── snapshot: module grants ─────────────────────────────────────────────────


def test_empty_grants_are_a_valid_zero_rights_plan():
    policy = policy_snapshot(_policy_row(module_grants=[]))
    assert policy.module_grants == ()
    from app.services.tenancy.plan_catalog import grants_capability

    assert not any(grants_capability(pattern, "connector.vk.read") for pattern in policy.module_grants)


@pytest.mark.parametrize(
    "grants",
    [
        ["connector.vk.read"],
        ["connector.vk.*"],
        ["connector.telegram.read", "*"],
        ("connector.vk.search",),
    ],
)
def test_registered_and_namespace_grant_patterns_are_accepted(grants):
    assert policy_snapshot(_policy_row(module_grants=list(grants))).module_grants == tuple(grants)


@pytest.mark.parametrize(
    "grants",
    [
        ["erp.invoices.read"],
        ["connector.mail.*"],
        ["connector.vk"],
        ["connector.vk.read", "crm.*"],
        [""],
        [None],
        [1],
        "connector.vk.read",
        {"connector.vk.read": True},
    ],
)
def test_unknown_or_malformed_module_grants_are_refused(grants):
    with pytest.raises(ServicePlanPolicyError) as error:
        policy_snapshot(_policy_row(module_grants=grants))
    assert error.value.reason in {
        "unknown_module_grant",
        "invalid_module_grants",
        "invalid_module_grant_pattern",
    }


def test_module_validation_reuses_the_composed_registry():
    from app.services.tenancy.capabilities import CAPABILITIES

    registered = sorted(CAPABILITIES)[0]
    assert policy_snapshot(_policy_row(module_grants=[registered])).module_grants == (registered,)
    with pytest.raises(ServicePlanPolicyError, match="unknown_module_grant"):
        policy_snapshot(_policy_row(module_grants=[registered + ".extra"]))


# ── snapshot: resource limits ───────────────────────────────────────────────


def test_realistic_full_resource_limits_round_trip_including_label():
    snapshot = policy_snapshot(_policy_row())
    assert dict(snapshot.resource_limits) == {
        key: tuple(value) if isinstance(value, list) else value for key, value in _resources().items()
    }
    assert snapshot.resource_limits["label"] == "Pro"
    assert snapshot.resource_limits["max_sources"] == 20
    assert snapshot.resource_limits["daily_cost_limit"] == 20.0
    assert snapshot.resource_limits["model_types"] == ("text", "image")
    assert set(snapshot.resource_limits) == set(Tenant.PLAN_LIMITS["pro"])


def test_missing_resource_limit_keys_are_refused_not_read_as_unlimited():
    partial = _resources(max_sources=_DROP)
    with pytest.raises(ServicePlanPolicyError) as error:
        policy_snapshot(_policy_row(resource_limits=partial))
    assert error.value.reason == "invalid_resource_limit_keys"
    with pytest.raises(ServicePlanPolicyError, match="invalid_resource_limit_keys"):
        policy_snapshot(_policy_row(resource_limits={}))


def test_extra_resource_limit_key_is_refused():
    with pytest.raises(ServicePlanPolicyError) as error:
        policy_snapshot(_policy_row(resource_limits=_resources(token_limit=10)))
    assert error.value.reason == "invalid_resource_limit_keys"


def test_nullable_collection_and_flag_limits_keep_null():
    limits = _resources(analytics_depth=None)
    snapshot = policy_snapshot(_policy_row(resource_limits=limits))
    assert snapshot.resource_limits["analytics_depth"] is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_sources": 3.0},
        {"max_sources": True},
        {"max_sources": -1},
        {"max_sources": MAX_BIGINT + 1},
        {"max_tasks": 1.0},
        {"retention_days": 7.5},
        {"daily_cost_limit": float("inf")},
        {"daily_cost_limit": float("nan")},
        {"daily_cost_limit": -1.5},
        {"daily_cost_limit": True},
        {"allow_learning": 1},
        {"analytics_depth": "sentiment"},
        {"analytics_depth": [None]},
        {"analytics_depth": []},
        {"model_types": "text"},
        {"model_types": []},
        {"label": 5},
        {"label": ""},
        {"label": None},
    ],
)
def test_malformed_resource_limits_are_refused(overrides):
    with pytest.raises(ServicePlanPolicyError) as error:
        policy_snapshot(_policy_row(resource_limits=_resources(**overrides)))
    assert error.value.reason == "invalid_resource_limit"


@pytest.mark.parametrize("monetary", [0, 5, 0.5, 20.0, 1e6])
def test_monetary_limit_accepts_finite_nonexact_numbers(monetary):
    snapshot = policy_snapshot(_policy_row(resource_limits=_resources(daily_cost_limit=monetary)))
    assert snapshot.resource_limits["daily_cost_limit"] == monetary


def test_zero_count_ceilings_are_values_not_absences():
    snapshot = policy_snapshot(_policy_row(resource_limits=_resources(max_sources=0, max_tasks=0)))
    assert snapshot.resource_limits["max_sources"] == 0
    assert snapshot.resource_limits["max_tasks"] == 0


# ── snapshot: daily quotas ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "quotas",
    [{"connector.vk.read": 0}, {"connector.vk.read": 5}, {"connector.vk.read": MAX_BIGINT}],
)
def test_daily_quotas_are_known_capabilities_with_exact_counts(quotas):
    assert dict(policy_snapshot(_policy_row(daily_quotas=quotas)).daily_quotas) == quotas


@pytest.mark.parametrize(
    "quotas",
    [
        {"connector.unknown.read": 1},
        {"connector.vk.read": -1},
        {"connector.vk.read": True},
        {"connector.vk.read": 1.5},
        {"connector.vk.read": None},
        [],
    ],
)
def test_malformed_daily_quotas_are_refused(quotas):
    with pytest.raises(ServicePlanPolicyError) as error:
        policy_snapshot(_policy_row(daily_quotas=quotas))
    assert error.value.reason in {"unknown_daily_quota", "invalid_daily_quota_value", "invalid_daily_quotas"}


# ── snapshot: plan identity and owned workspaces ────────────────────────────


@pytest.mark.parametrize("code", ["future", "", None, "PRO", "personal"])
def test_unknown_plan_code_is_not_normalized_to_a_default(code):
    with pytest.raises(ServicePlanPolicyError, match="unknown_plan"):
        policy_snapshot(_policy_row(plan_code=code))


@pytest.mark.parametrize("version", [0, -1, True, 3.0, "3", None])
def test_unknown_or_malformed_policy_version_is_refused(version):
    with pytest.raises(ServicePlanPolicyError, match="invalid_policy_version"):
        policy_snapshot(_policy_row(policy_version=version))


def test_configured_owned_workspace_limit_with_null_is_unlimited():
    policy = policy_snapshot(_policy_row(max_owned_workspaces=None))
    assert policy.owned_workspace_limit_configured is True
    assert policy.max_owned_workspaces is None


def test_unconfigured_owned_workspace_limit_carries_no_value():
    policy = policy_snapshot(_policy_row(owned_workspace_limit_configured=False, max_owned_workspaces=None))
    assert policy.owned_workspace_limit_configured is False
    assert policy.max_owned_workspaces is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_owned_workspaces": -1},
        {"max_owned_workspaces": True},
        {"max_owned_workspaces": 1.0},
        {"max_owned_workspaces": MAX_BIGINT + 1},
        {"owned_workspace_limit_configured": False},
        {"owned_workspace_limit_configured": 1},
    ],
)
def test_malformed_owned_workspace_limit_is_refused(overrides):
    with pytest.raises(ServicePlanPolicyError, match="invalid_owned_workspace_limit"):
        policy_snapshot(_policy_row(**overrides))


def test_missing_policy_row_fails_closed():
    with pytest.raises(ServicePlanPolicyError) as error:
        policy_snapshot(None)
    assert error.value.reason == "missing_policy"


# ── frozen DTO ──────────────────────────────────────────────────────────────


def test_snapshot_is_a_deeply_frozen_detached_copy():
    row = _policy_row()
    policy = policy_snapshot(row)
    assert isinstance(policy, ValidatedPlanPolicy)
    assert isinstance(policy.daily_quotas, MappingProxyType)
    assert isinstance(policy.resource_limits, MappingProxyType)

    row.module_grants.append("connector.vk.read")
    row.daily_quotas["connector.vk.read"] = 99
    row.resource_limits["max_sources"] = 999
    row.token_limit = 5
    assert policy.module_grants == ("*",)
    assert dict(policy.daily_quotas) == {}
    assert policy.resource_limits["max_sources"] == 20
    assert policy.token_policy.token_limit == 1000

    with pytest.raises(FrozenInstanceError):
        policy.plan_code = "business"
    with pytest.raises(TypeError):
        policy.daily_quotas["connector.vk.read"] = 1
    with pytest.raises(TypeError):
        policy.resource_limits["max_sources"] = 1
    with pytest.raises(FrozenInstanceError):
        policy.token_policy.token_limit = 1


# ── row shape: a real driver row is not a tuple ─────────────────────────────


def test_sqlalchemy_row_is_not_a_tuple_but_is_accepted():
    """The defect: `isinstance(Row, tuple)` is False for every real driver row."""
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.execute(text("CREATE TABLE probe (id INTEGER, holder INTEGER)"))
        connection.execute(text("INSERT INTO probe VALUES (:i, :h)"), {"i": TENANT, "h": HOLDER})
        row = connection.execute(select(column("id"), column("holder")).select_from(text("probe"))).one_or_none()
    assert type(row) is Row
    assert not isinstance(row, tuple), "the old tuple-only check rejected exactly this object"
    assert list(row) == [TENANT, HOLDER]


def test_row_constructor_as_produced_by_the_library_is_accepted():
    """Use the library's public Result API, never private Row constructors."""
    row = _real_row(TENANT, HOLDER)
    assert not isinstance(row, tuple) and list(row) == [TENANT, HOLDER]


async def test_constructed_library_row_resolves_the_workspace():
    row = _real_row(TENANT, HOLDER)
    context = await load_workspace_policy(_RowSession(row), TENANT, now=NOW)
    assert (context.tenant_id, context.plan_holder_user_id) == (TENANT, HOLDER)


def _real_row(*values):
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT " + ", ".join(f":v{i}" for i in range(len(values)))),
            {f"v{i}": value for i, value in enumerate(values)},
        ).one()
    engine.dispose()
    return row


async def test_trusted_lookup_reads_real_rows_from_sqlite():
    world = _world()
    context = await load_workspace_policy(world, TENANT, now=NOW)
    assert (context.tenant_id, context.plan_holder_user_id) == (TENANT, HOLDER)
    world.close()


async def test_actor_resolution_reads_real_rows_from_sqlite():
    world = _world()
    context = await resolve_holder_context(world, TENANT, ACTOR, now=NOW)
    assert (context.actor_user_id, context.plan_holder_user_id) == (ACTOR, HOLDER)
    world.close()


class _RowResult:
    """Real `Row` from a real SQLAlchemy `Result`, whatever the statement."""

    def __init__(self, row):
        self.row = row

    def one_or_none(self):
        return self.row

    def first(self):
        return self.row


class _RowSession:
    """Every execution returns the given genuine `Row` (or a bare int)."""

    def __init__(self, row):
        self.row = row
        self.world = _world()
        self.first = True

    async def execute(self, stmt):
        if self.first:
            self.first = False
            return _RowResult(self.row)
        return await self.world.execute(stmt)

    async def scalar(self, stmt):
        return await self.world.scalar(stmt)


async def test_tuple_rows_keep_the_existing_contract():
    row = (TENANT, HOLDER)
    context = await load_workspace_policy(_RowSession(row), TENANT, now=NOW)
    assert context.plan_holder_user_id == HOLDER


@pytest.mark.parametrize("scalar", [TENANT, "not-a-row", (TENANT,), [TENANT, HOLDER]])
async def test_a_scalar_shaped_result_is_refused_instead_of_indexed(scalar):
    with pytest.raises(ServicePlanPolicyError) as error:
        await load_workspace_policy(_RowSession(scalar), TENANT, now=NOW)
    assert error.value.reason == "invalid_projection:tenant"


async def test_a_row_of_the_wrong_width_is_refused():
    """A one-column projection cannot carry the holder: refuse, never index."""
    narrow = _real_row(TENANT)
    with pytest.raises(ServicePlanPolicyError) as error:
        await load_workspace_policy(_RowSession(narrow), TENANT, now=NOW)
    assert error.value.reason == "invalid_projection:tenant"


# ── identity: comparisons must not depend on object interning ───────────────


def test_high_identities_are_distinct_int_objects():
    assert BIG_USER > 256 and BIG_ACTOR > 256 and BIG_TENANT > 256
    assert BIG_USER != HOLDER and int(str(BIG_USER)) is not BIG_USER, "no interned identity to hide behind"


async def test_holder_identity_above_the_int_cache_is_accepted():
    world = _world(
        users=((BIG_ACTOR, True), (BIG_USER, True)),
        members=((BIG_TENANT, BIG_ACTOR, True),),
        workspace=(BIG_TENANT, BIG_USER, True),
        plan=_plan_row(user_id=BIG_USER),
    )
    context = await resolve_holder_context(world, BIG_TENANT, BIG_ACTOR, now=NOW)
    assert (context.actor_user_id, context.plan_holder_user_id) == (BIG_ACTOR, BIG_USER)
    world.close()


@pytest.mark.parametrize("lock_holder", [False, True])
async def test_high_identity_holder_locks_in_both_modes(lock_holder):
    world = _world(
        users=((BIG_ACTOR, True), (BIG_USER, True)),
        workspace=(BIG_TENANT, BIG_USER, True),
        plan=_plan_row(user_id=BIG_USER),
    )
    context = await load_workspace_policy(world, BIG_TENANT, now=NOW, lock_holder=lock_holder)
    assert context.plan_holder_user_id == BIG_USER
    world.close()


# ── locking holder: activity is required in both modes ─────────────────────


@pytest.mark.parametrize("lock_holder", [False, True])
async def test_deactivated_holder_denies_in_both_modes(lock_holder):
    world = _world(users=((ACTOR, True), (HOLDER, False)))
    with pytest.raises(ServicePlanPolicyError) as error:
        await load_workspace_policy(world, TENANT, now=NOW, lock_holder=lock_holder)
    assert error.value.reason == "holder_inactive"
    assert world.statements(UserServicePlan) == []
    world.close()


@pytest.mark.parametrize("lock_holder", [False, True])
async def test_unknown_or_deleted_holder_denies_in_both_modes(lock_holder):
    world = _world(users=((ACTOR, True),))
    with pytest.raises(ServicePlanPolicyError) as error:
        await load_workspace_policy(world, TENANT, now=NOW, lock_holder=lock_holder)
    assert error.value.reason == "holder_missing"
    world.close()


@pytest.mark.parametrize("lock_holder", [False, True])
async def test_locked_actor_resolution_also_refuses_an_inactive_holder(lock_holder):
    world = _world(users=((ACTOR, True), (HOLDER, False)))
    with pytest.raises(ServicePlanPolicyError, match="holder_inactive"):
        await resolve_holder_context(world, TENANT, ACTOR, now=NOW, lock_holder=lock_holder)
    world.close()


async def test_holder_is_proven_active_before_the_plan_read_in_both_modes():
    for lock_holder in (False, True):
        world = _world()
        await load_workspace_policy(world, TENANT, now=NOW, lock_holder=lock_holder)
        holder_lookups = world.statements(User)
        active = [stmt for stmt in holder_lookups if "users.is_active IS true" in _sql(stmt)]
        assert active, "the holder must be proven active, not merely present"
        assert world.calls.index(active[0]) < world.calls.index(world.projected(UserServicePlan)[0])
        world.close()


async def test_holder_lock_is_unconditional_and_precedes_the_plan_read():
    world = _world()
    await load_workspace_policy(world, TENANT, now=NOW, lock_holder=True)
    locks = [stmt for stmt in world.statements(User) if "FOR UPDATE" in _sql(stmt)]
    assert len(locks) == 1
    lock_index = world.calls.index(locks[0])
    plan_index = world.calls.index(world.projected(UserServicePlan)[0])
    assert lock_index < plan_index
    assert "FOR UPDATE" in _sql(world.projected(UserServicePlan)[0])
    world.close()


async def test_non_locking_lookup_never_locks():
    world = _world()
    await load_workspace_policy(world, TENANT, now=NOW)
    assert all("FOR UPDATE" not in _sql(stmt) for stmt in world.calls)
    world.close()


# ── membership: existence, never uniqueness ────────────────────────────────


async def test_membership_across_channels_in_one_workspace_is_one_membership():
    """telegram + web rows for the same platform user must not read as "two"."""
    world = _world(members=((TENANT, ACTOR, "telegram", True), (TENANT, ACTOR, "web", True)))
    context = await resolve_holder_context(world, TENANT, ACTOR, now=NOW)
    assert context.actor_user_id == ACTOR
    world.close()


async def test_membership_duplicates_across_three_channels_are_tolerated():
    members = tuple((TENANT, ACTOR, channel, True) for channel in ("telegram", "max", "web"))
    world = _world(members=members)
    assert await _membership_proven(world) is True
    world.close()


async def test_one_active_and_one_inactive_channel_membership_is_active():
    world = _world(members=((TENANT, ACTOR, "telegram", False), (TENANT, ACTOR, "web", True)))
    assert await _membership_proven(world) is True
    world.close()


async def test_only_inactive_channel_memberships_deny():
    world = _world(members=((TENANT, ACTOR, "telegram", False), (TENANT, ACTOR, "web", False)))
    with pytest.raises(ServicePlanPolicyError, match="actor_membership_inactive"):
        await resolve_holder_context(world, TENANT, ACTOR, now=NOW)
    world.close()


async def _membership_proven(world):
    context = await resolve_holder_context(world, TENANT, ACTOR, now=NOW)
    return context.actor_user_id == ACTOR


async def test_membership_lookup_cannot_fail_on_duplicates_by_construction():
    """`one_or_none()` would raise here; the existence query must not."""
    world = _world(members=((TENANT, ACTOR, "telegram", True), (TENANT, ACTOR, "web", True)))
    await resolve_holder_context(world, TENANT, ACTOR, now=NOW)
    sql = _sql(world.statements(TenantUser)[0])
    assert "EXISTS" in sql.upper()
    assert "tenant_users.tenant_id = " in sql
    assert "tenant_users.user_id = " in sql
    assert "tenant_users.is_active IS true" in sql
    world.close()


# ── trusted workspace lookup ────────────────────────────────────────────────


async def test_workspace_projection_returns_a_row_not_the_first_column():
    world = _world()
    context = await load_workspace_policy(world, TENANT, now=NOW)
    tenant_select = world.statements(Tenant)[0]
    assert _selected_keys(tenant_select) == {"id", "plan_holder_user_id"}
    assert world.calls[0] is tenant_select, "the workspace row must be read first"
    # The holder comes from the row's second column, never from the tenant id.
    assert context.plan_holder_user_id == HOLDER != context.tenant_id
    world.close()


async def test_trusted_lookup_returns_holder_context_without_actor():
    world = _world()
    context = await load_workspace_policy(world, TENANT, now=NOW)
    assert isinstance(context, WorkspacePlanContext)
    assert context.actor_user_id is None
    assert (context.tenant_id, context.plan_holder_user_id) == (TENANT, HOLDER)
    assert (context.plan_code, context.plan_policy_version) == ("pro", 3)
    assert context.plan_expires_at is None
    assert context.policy.module_grants == ("*",)
    world.close()


async def test_expiry_boundary_is_fail_closed():
    future = NOW + timedelta(seconds=1)
    world = _world(plan=_plan_row(plan_expires_at=future))
    context = await load_workspace_policy(world, TENANT, now=NOW)
    assert context.plan_expires_at is not None
    world.close()
    expired = _world(plan=_plan_row(plan_expires_at=NOW))
    with pytest.raises(ServicePlanPolicyError) as error:
        await load_workspace_policy(expired, TENANT, now=NOW)
    assert error.value.reason == "plan_expired"
    expired.close()


async def test_naive_expiry_and_naive_now_are_refused():
    naive = NOW.replace(tzinfo=None)
    naive_expiry = _world(plan=_plan_row(plan_expires_at=naive))
    with pytest.raises(ServicePlanPolicyError, match="invalid_plan_expiry"):
        await load_workspace_policy(naive_expiry, TENANT, now=NOW)
    naive_expiry.close()
    naive_now = _world()
    with pytest.raises(ServicePlanPolicyError, match="invalid_now"):
        await load_workspace_policy(naive_now, TENANT, now=naive)
    naive_now.close()


async def test_inactive_plan_is_refused():
    world = _world(plan=_plan_row(is_active=False))
    with pytest.raises(ServicePlanPolicyError, match="plan_inactive"):
        await load_workspace_policy(world, TENANT, now=NOW)
    world.close()


async def test_missing_workspace_plan_or_policy_denies():
    cases = [
        (dict(workspace=None), "workspace_inactive"),
        (dict(plan=None), "missing_plan"),
        (dict(policy=None), "missing_policy"),
    ]
    for overrides, reason in cases:
        world = _world(**overrides)
        with pytest.raises(ServicePlanPolicyError) as error:
            await load_workspace_policy(world, TENANT, now=NOW)
        assert error.value.reason == reason
        world.close()


@pytest.mark.parametrize("holder", [None, 0, True, -1])
async def test_workspace_without_a_verified_holder_denies(holder):
    world = _RowSession((TENANT, holder))
    with pytest.raises(ServicePlanPolicyError, match="missing_plan_holder"):
        await load_workspace_policy(world, TENANT, now=NOW)
    world.world.close()


@pytest.mark.parametrize("tenant_id", [0, -1, True, "100", None])
async def test_workspace_scope_must_be_a_verified_identity(tenant_id):
    world = _world()
    with pytest.raises(ServicePlanPolicyError, match="invalid_tenant_id"):
        await load_workspace_policy(world, tenant_id, now=NOW)
    assert world.calls == []
    world.close()


async def test_inactive_workspace_is_refused():
    world = _world(workspace=(TENANT, HOLDER, False))
    with pytest.raises(ServicePlanPolicyError, match="workspace_inactive"):
        await load_workspace_policy(world, TENANT, now=NOW)
    world.close()


async def test_plan_row_and_policy_version_must_agree():
    world = _world(policy=_policy_row(policy_version=4))
    with pytest.raises(ServicePlanPolicyError, match="missing_policy"):
        await load_workspace_policy(world, TENANT, now=NOW)
    world.close()


# ── authenticated actor / holder split ──────────────────────────────────────


async def test_actor_resolution_returns_the_holder_policy_not_the_actor_tariff():
    world = _world()
    context = await resolve_holder_context(world, TENANT, ACTOR, now=NOW)
    assert context.actor_user_id == ACTOR
    assert context.plan_holder_user_id == HOLDER
    assert context.plan_code == "pro"
    assert context.plan_policy_version == 3
    # The plan row read belongs to the sponsoring holder, never to the actor.
    plan_lookups = world.projected(UserServicePlan)
    assert len(plan_lookups) == 1 and "user_id" in _sql(plan_lookups[0])
    world.close()


async def test_holder_acting_in_its_own_workspace_matches_without_a_role_bypass():
    world = _world(members=((TENANT, HOLDER, True),))
    context = await resolve_holder_context(world, TENANT, HOLDER, now=NOW)
    assert context.actor_user_id == context.plan_holder_user_id == HOLDER
    world.close()


@pytest.mark.parametrize("actor", [0, -1, True, "11", None])
async def test_actor_identity_is_validated_not_inferred(actor):
    with pytest.raises(ServicePlanPolicyError, match="invalid_actor_id"):
        await resolve_holder_context(_world(), TENANT, actor, now=NOW)


async def test_inactive_actor_user_denies():
    with pytest.raises(ServicePlanPolicyError, match="actor_inactive"):
        await resolve_holder_context(_world(users=((ACTOR, False), (HOLDER, True))), TENANT, ACTOR, now=NOW)


async def test_inactive_actor_membership_denies_without_reading_a_plan():
    world = _world(members=((TENANT, ACTOR, False),))
    with pytest.raises(ServicePlanPolicyError, match="actor_membership_inactive"):
        await resolve_holder_context(world, TENANT, ACTOR, now=NOW)
    assert world.statements(UserServicePlan) == []
    world.close()


async def test_membership_of_another_workspace_does_not_count():
    with pytest.raises(ServicePlanPolicyError, match="actor_membership_inactive"):
        await resolve_holder_context(_world(members=((TENANT + 1, ACTOR, True),)), TENANT, ACTOR, now=NOW)


async def test_actor_and_holder_stay_separate_identity_fields():
    world = _world()
    context = await resolve_holder_context(world, TENANT, ACTOR, now=NOW)
    assert context.actor_user_id != context.plan_holder_user_id
    assert {field.name for field in dataclasses.fields(context)} == {
        "actor_user_id",
        "tenant_id",
        "plan_holder_user_id",
        "plan_code",
        "plan_policy_version",
        "plan_expires_at",
        "policy",
    }
    world.close()


# ── SQL shape (static) ──────────────────────────────────────────────────────


def test_projected_lookups_select_explicit_columns_only():
    plan_columns = {column.key for column in UserServicePlan.__table__.columns}
    assert {"user_id", "plan_code", "policy_version", "is_active", "plan_expires_at"} <= plan_columns
    # Relationship names are not columns, so a projection cannot trigger a lazy load.
    assert not {"policy", "holder_user", "service_plan", "tenant"}.intersection(plan_columns)


def test_integer_columns_used_as_identity_are_plain_integers():
    assert isinstance(Tenant.__table__.c.plan_holder_user_id.type, Integer)
    assert isinstance(UserServicePlan.__table__.c.user_id.type, Integer)
