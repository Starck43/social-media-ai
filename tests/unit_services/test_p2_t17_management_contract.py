"""P2-T17 increment A: account-scoped tariff management and explicit backfill apply.

Source-only contract: no database, no runtime, no provider call. Two focused
session doubles mirror the SQLAlchemy surface the service actually asks for,
and every statement is compiled so the assertions can inspect its projection,
`FOR UPDATE` clause and the exact maintenance `LOCK TABLE` text:

* packaging (`create_policy`, `assign_user_plan`) uses `scalars`/`scalar`/`get`
  and one mirror `UPDATE`;
* `apply_backfill` first takes the exclusive table maintenance lock through
  `session.bind.dialect.identifier_preparer.quote_schema(...)` plus
  `session.execute(text(...))`, then `scalars` for user ids/policies,
  `execute` for the ordered `Tenant` select and the `TenantUser`/`WorkspaceRole`
  owner-membership join, then `get` by primary key before adding rows.

Assertions encode the fail-closed contract in `docs/tasks/p2-t17.md`; production
is expected to match it. A mismatch is a reported defect, never a softened test.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from app.models.identity.user import User
from app.models.tenancy.service_plan import ServicePlanBackfillResolution, ServicePlanPolicy, UserServicePlan
from app.models.tenancy.tenant import Tenant
from app.models.tenancy.tenant_user import TenantUser
from app.models.tenancy.workspace_role import WorkspaceRole
from app.services.tenancy.service_plan_backfill import (
    BackfillMappingError,
    HolderPlanSelection,
    HolderSelection,
)
from app.services.tenancy.service_plan_management import apply_backfill, assign_user_plan, create_policy
from app.services.tenancy.service_plan_policy import ServicePlanPolicyError

__source_only__ = True

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(days=30)
REFERENCE = "ses_test_p2_t17_operator_decision"
OWNER, HOLDER, HELD, OUTSIDER = 1, 2, 3, 4
TENANT_A, TENANT_B, TENANT_C = 100, 101, 102
DIALECT = postgresql.dialect()
#: The maintenance lock derives every table from the model's own schema, so the
#: test never assumes `public`; the bound dialect quotes the identifier.
QUOTED_SCHEMA = DIALECT.identifier_preparer.quote_schema(Tenant.__table__.schema)
MAINTENANCE_LOCK = (
    f"LOCK TABLE {QUOTED_SCHEMA}.users, {QUOTED_SCHEMA}.tenants, {QUOTED_SCHEMA}.tenant_users IN ACCESS EXCLUSIVE MODE"
)


# ── shared rows and payloads ────────────────────────────────────────────────


class _AccountRow(SimpleNamespace):
    # Use the real computed authority property: demoting a role must not leave
    # a cached fixture boolean granting platform-owner rights.
    is_superuser = User.is_superuser


def _users(*rows):
    return [
        _AccountRow(
            id=user_id,
            is_active=active,
            role=SimpleNamespace(codename=role),
            username=f"user{user_id}",
        )
        for user_id, role, active in rows
    ]


def _tenant(tenant_id, plan="pro", holder=None, expiry=None):
    return SimpleNamespace(id=tenant_id, plan=plan, plan_expires_at=expiry, plan_holder_user_id=holder, is_active=True)


def _resources(**overrides):
    limits = {
        "max_sources": 20,
        "daily_cost_limit": 20.0,
        "max_channels": 5,
        "max_scenarios": 10,
        "max_team_members": 5,
        "max_tasks": 20,
        "analytics_depth": ["sentiment", "topics", "content_mix"],
        "allow_auto_actions": True,
        "allow_learning": True,
        "allow_reflection": True,
        "retention_days": 30,
        "model_types": ["text", "image", "embedding"],
        "label": "Pro",
    }
    limits.update(overrides)
    return limits


def _policy_values(**overrides):
    """Every stored column except the server timestamp, exactly as an operator supplies it.

    `_DROP` removes a required key (an incomplete definition); `_EXTRA_<name>`
    adds a key no column backs (a defect the service must refuse, never guess
    at). Extra keys are deliberately not routed through the ORM constructor:
    the declarative constructor would reject them before the service sees them.
    """
    values = dict(
        plan_code="pro",
        policy_version=2,
        module_grants=["*"],
        daily_quotas={},
        resource_limits=_resources(),
        owned_workspace_limit_configured=True,
        max_owned_workspaces=3,
        token_policy_configured=False,
        token_unit=None,
        token_period=None,
        token_limit=None,
    )
    for key, value in overrides.items():
        if value is _DROP:
            values.pop(key)
        elif key.startswith(_EXTRA):
            values[key.removeprefix(_EXTRA)] = value
        else:
            values[key] = value
    # The builder must stay exactly the operator-supplied column set.
    columns = set(ServicePlanPolicy.__table__.c.keys()) - {"created_at"}
    if not overrides:
        assert set(values) == columns, "a complete definition supplies every stored column"
    return values


def _policy_row(**overrides):
    """A stored row; only for complete, column-backed definitions."""
    return ServicePlanPolicy(**_policy_values(**overrides))


class _Drop:
    pass


_DROP = _Drop()
#: Prefix that turns an override into an operator-supplied key with no column.
_EXTRA = "extra__"


def _compiled(statement):
    return " ".join(str(statement.compile(dialect=DIALECT)).split())


def _for_update(statement):
    return getattr(statement, "_for_update_arg", None) is not None


def _criteria(statement):
    clause = statement.whereclause
    return list(getattr(clause, "clauses", [clause]))


def _values_by_key(statement):
    return {criterion.left.key: criterion.right.value for criterion in _criteria(statement)}


def _bind():
    """The `session.bind` shape `apply_backfill` dereferences for schema quoting."""
    return SimpleNamespace(dialect=DIALECT)


class _Scalars(list):
    def all(self):
        return list(self)


# ── packaging session ───────────────────────────────────────────────────────


class _PackagingSession:
    bind = _bind()

    def __init__(self, users, policies=(), plans=()):
        self.users = {row.id: row for row in _users(*users)}
        self.policies = {(row.plan_code, row.policy_version): row for row in policies}
        self.plans = {row.user_id: row for row in plans}
        self.pending = []
        self.locks = []
        self.updates = []

    async def scalars(self, statement):
        local = statement.compile(dialect=DIALECT).params
        if _for_update(statement):
            self.locks.append(User.__tablename__)
        ids = local.get("id_1", ())
        if not isinstance(ids, (tuple, list, set)):
            ids = (ids,)
        return _Scalars(
            sorted((self.users[user_id] for user_id in ids if user_id in self.users), key=lambda row: row.id)
        )

    async def scalar(self, statement):
        table = next(iter({column.table for column in statement.selected_columns}))
        values = _values_by_key(statement)
        if _for_update(statement):
            self.locks.append(table.name)
        if table is UserServicePlan.__table__:
            return self.plans.get(values.get("user_id"))
        if table is ServicePlanPolicy.__table__:
            return self.policies.get((values.get("plan_code"), values.get("policy_version")))
        raise AssertionError(f"unsupported scalar over {table.name}")

    async def get(self, model, key, **kwargs):
        if model is ServicePlanPolicy:
            return self.policies.get(key)
        if model is UserServicePlan:
            return self.plans.get(key)
        raise AssertionError(f"unexpected get({model!r})")

    async def execute(self, statement):
        self.updates.append(statement)
        return None

    def add(self, row):
        self.pending.append(row)

    async def flush(self):
        for row in self.pending:
            if isinstance(row, UserServicePlan):
                self.plans[row.user_id] = row
            elif isinstance(row, ServicePlanPolicy):
                self.policies[(row.plan_code, row.policy_version)] = row
            else:  # pragma: no cover - packaging never adds other models
                raise AssertionError(f"unexpected pending row {row!r}")
        self.pending.clear()


def _packaging(*, users=None, with_policy=True, plans=()):
    return _PackagingSession(
        users=users if users is not None else [(OWNER, "superuser", True), (HOLDER, "user", True)],
        policies=[_policy_row()] if with_policy else [],
        plans=plans,
    )


# ── account-scoped authority ────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["user", "operator", "admin"])
async def test_plan_assignment_requires_the_platform_owner_not_a_workspace_owner_or_admin(role):
    session = _packaging(users=[(OUTSIDER, role, True), (HOLDER, "user", True)])
    with pytest.raises(PermissionError, match="Platform owner"):
        await assign_user_plan(
            session, actor_user_id=OUTSIDER, user_id=HOLDER, plan_code="pro", policy_version=2, plan_expires_at=LATER
        )
    assert session.updates == [] and session.pending == [] and session.plans == {}


@pytest.mark.parametrize("role", ["user", "operator", "admin"])
async def test_policy_authoring_requires_the_platform_owner(role):
    session = _packaging(users=[(OUTSIDER, role, True)])
    with pytest.raises(PermissionError, match="Platform owner"):
        await create_policy(session, actor_user_id=OUTSIDER, values=_policy_values())
    assert session.pending == []


async def test_authority_comes_from_a_fresh_locked_row_not_cached_state():
    session = _packaging()
    session.users[OWNER].role = SimpleNamespace(codename="user")  # demoted between request and execution
    with pytest.raises(PermissionError, match="Platform owner"):
        await create_policy(session, actor_user_id=OWNER, values=_policy_values())
    assert User.__tablename__ in session.locks
    assert session.pending == []


async def test_inactive_owner_is_refused():
    session = _packaging(users=[(OWNER, "superuser", False), (HOLDER, "user", True)])
    with pytest.raises(PermissionError, match="Platform owner"):
        await assign_user_plan(session, actor_user_id=OWNER, user_id=HOLDER, plan_code="pro", policy_version=2)
    assert session.plans == {}


@pytest.mark.parametrize("actor", [0, -1, True, "1", None])
async def test_missing_or_malformed_actor_fails_closed_before_reading_accounts(actor):
    session = _packaging()
    with pytest.raises(PermissionError, match="Platform owner"):
        await create_policy(session, actor_user_id=actor, values=_policy_values())
    assert session.locks == []


async def test_unknown_target_account_is_refused_without_partial_mutation():
    session = _packaging()
    with pytest.raises(ValueError, match="Unknown account"):
        await assign_user_plan(session, actor_user_id=OWNER, user_id=999, plan_code="pro", policy_version=2)
    assert session.updates == [] and session.plans == {}


# ── create_policy ───────────────────────────────────────────────────────────


async def test_create_policy_inserts_one_immutable_revision():
    session = _packaging()
    created = await create_policy(session, actor_user_id=OWNER, values=_policy_values())
    assert isinstance(created, ServicePlanPolicy)
    assert (created.plan_code, created.policy_version) == ("pro", 2)
    assert created.token_policy_configured is False
    assert session.policies[("pro", 2)] is created
    assert session.updates == []


@pytest.mark.parametrize(
    "drop", ["policy_version", "module_grants", "token_policy_configured", "max_owned_workspaces", "resource_limits"]
)
async def test_create_policy_requires_the_complete_definition(drop):
    session = _packaging()
    with pytest.raises(ServicePlanPolicyError, match="incomplete_policy_definition"):
        await create_policy(session, actor_user_id=OWNER, values=_policy_values(**{drop: _DROP}))
    assert session.pending == []


@pytest.mark.parametrize(
    "extra", [{_EXTRA + "plan_price_usd": 19}, {_EXTRA + "created_at": NOW}, {_EXTRA + "is_default": True}]
)
async def test_create_policy_refuses_keys_outside_the_stored_policy(extra):
    session = _packaging()
    values = _policy_values(**extra)
    assert set(values) - (set(ServicePlanPolicy.__table__.c.keys()) - {"created_at"}) == {
        key.removeprefix(_EXTRA) for key in extra
    }
    with pytest.raises(ServicePlanPolicyError, match="incomplete_policy_definition"):
        await create_policy(session, actor_user_id=OWNER, values=values)
    assert session.pending == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"plan_code": "enterprise"},
        {"policy_version": 0},
        {"max_owned_workspaces": -1},
        {"owned_workspace_limit_configured": False},
        {"token_policy_configured": True, "token_limit": 10},
        {"module_grants": ["erp.invoices.read"]},
    ],
)
async def test_create_policy_refuses_invalid_policies_through_the_snapshot(overrides):
    session = _packaging()
    with pytest.raises(ServicePlanPolicyError):
        await create_policy(session, actor_user_id=OWNER, values=_policy_values(**overrides))
    assert session.pending == []


# ── assign_user_plan: fresh assignment ──────────────────────────────────────


async def test_assignment_creates_the_account_plan_and_records_its_exact_values():
    session = _packaging()
    row = await assign_user_plan(
        session, actor_user_id=OWNER, user_id=HOLDER, plan_code="pro", policy_version=2, plan_expires_at=LATER
    )
    assert row.user_id == HOLDER
    assert (row.plan_code, row.policy_version, row.is_active, row.plan_expires_at) == ("pro", 2, True, LATER)
    assert session.plans[HOLDER] is row
    assert len(session.updates) == 1


async def test_assignment_binds_the_selected_policy_row_explicitly():
    """The stored plan carries the immutable policy revision it was validated against."""
    policy = _policy_row()
    session = _PackagingSession(users=[(OWNER, "superuser", True), (HOLDER, "user", True)], policies=[policy])
    row = await assign_user_plan(session, actor_user_id=OWNER, user_id=HOLDER, plan_code="pro", policy_version=2)
    assert row.policy is policy
    assert (row.plan_code, row.policy_version) == (policy.plan_code, policy.policy_version)


async def test_mirror_is_one_holder_scoped_update_not_the_actor_workspace():
    session = _packaging()
    await assign_user_plan(
        session, actor_user_id=OWNER, user_id=HOLDER, plan_code="pro", policy_version=2, plan_expires_at=LATER
    )
    assert len(session.updates) == 1
    mirror = session.updates[0]
    compiled = _compiled(mirror)
    assert compiled.startswith(f"UPDATE {Tenant.__table__.fullname} SET ")
    assert "plan_holder_user_id" in compiled.split("WHERE", 1)[1]
    assert "plan_expires_at" in compiled.split("SET", 1)[1].split("WHERE", 1)[0]
    assert "tenants.id" not in compiled.split("WHERE", 1)[1]


async def test_mirror_predicate_is_the_holder_id_the_caller_selected():
    session = _packaging()
    await assign_user_plan(
        session, actor_user_id=OWNER, user_id=HOLDER, plan_code="pro", policy_version=2, plan_expires_at=LATER
    )
    mirror = session.updates[0]
    parameters = mirror.compile(dialect=DIALECT).params
    assert parameters["plan_holder_user_id_1"] == HOLDER
    assert parameters["plan"] == "pro"
    assert parameters["plan_expires_at"] == LATER


async def test_assignment_rotates_an_existing_row_in_place_instead_of_duplicating():
    existing = UserServicePlan(
        user_id=HOLDER, plan_code="starter", policy_version=2, is_active=True, plan_expires_at=None
    )
    session = _packaging(plans=[existing])
    session.policies[("starter", 2)] = _policy_row(plan_code="starter")
    row = await assign_user_plan(
        session, actor_user_id=OWNER, user_id=HOLDER, plan_code="starter", policy_version=2, is_active=False
    )
    assert row is existing
    assert (row.plan_code, row.policy_version, row.is_active) == ("starter", 2, False)
    assert list(session.plans) == [HOLDER]


async def test_assignment_requires_an_existing_policy_revision():
    session = _packaging()
    with pytest.raises(ServicePlanPolicyError):
        await assign_user_plan(session, actor_user_id=OWNER, user_id=HOLDER, plan_code="pro", policy_version=7)
    assert session.plans == {} and session.updates == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"is_active": 1},
        {"is_active": None},
        {"plan_expires_at": NOW.replace(tzinfo=None)},
        {"plan_expires_at": "2026-12-01"},
    ],
)
async def test_assignment_values_are_strict_and_never_coerced(overrides):
    session = _packaging()
    arguments = {"is_active": True, "plan_expires_at": None, **overrides}
    with pytest.raises(ValueError, match="Invalid plan state or expiry"):
        await assign_user_plan(
            session, actor_user_id=OWNER, user_id=HOLDER, plan_code="pro", policy_version=2, **arguments
        )
    assert session.plans == {} and session.updates == []


# ── assign_user_plan: strict plan identity ──────────────────────────────────


@pytest.mark.parametrize(
    "plan_code",
    ["", " ", "enterprise", "personal", "Pro", "pro ", str(Tenant.PLANS), None, 1],
)
async def test_assignment_refuses_a_plan_code_outside_the_workspace_plan_set(plan_code):
    session = _packaging()
    with pytest.raises(ServicePlanPolicyError, match="invalid_plan_identity"):
        await assign_user_plan(session, actor_user_id=OWNER, user_id=HOLDER, plan_code=plan_code, policy_version=2)
    assert session.plans == {} and session.updates == []


@pytest.mark.parametrize("plan_code", list(Tenant.PLANS))
async def test_assignment_accepts_every_configured_plan_code(plan_code):
    session = _packaging()
    session.policies[(plan_code, 2)] = _policy_row(plan_code=plan_code)
    row = await assign_user_plan(session, actor_user_id=OWNER, user_id=HOLDER, plan_code=plan_code, policy_version=2)
    assert row.plan_code == plan_code


@pytest.mark.parametrize("revision", [0, -1, True, 1.0, "2", 2**31, None])
async def test_assignment_refuses_a_revision_that_is_not_a_positive_exact_int(revision):
    session = _packaging()
    with pytest.raises(ServicePlanPolicyError, match="invalid_plan_identity"):
        await assign_user_plan(session, actor_user_id=OWNER, user_id=HOLDER, plan_code="pro", policy_version=revision)
    assert session.plans == {} and session.updates == []


async def test_rejected_identity_is_not_a_missing_policy_and_writes_nothing():
    session = _packaging(with_policy=False)
    with pytest.raises(ServicePlanPolicyError, match="invalid_plan_identity"):
        await assign_user_plan(session, actor_user_id=OWNER, user_id=HOLDER, plan_code="", policy_version=2)
    assert session.pending == []


# ── assign_user_plan: state preservation ────────────────────────────────────


async def test_preserve_state_keeps_the_existing_expiry_and_active_flag():
    current_expiry = NOW - timedelta(days=2)  # an already expired tariff: not renewed here
    existing = UserServicePlan(
        user_id=HOLDER, plan_code="starter", policy_version=1, is_active=False, plan_expires_at=current_expiry
    )
    session = _packaging(plans=[existing])
    session.policies[("starter", 1)] = _policy_row(plan_code="starter", policy_version=1)
    row = await assign_user_plan(
        session,
        actor_user_id=OWNER,
        user_id=HOLDER,
        plan_code="pro",
        policy_version=2,
        is_active=True,
        plan_expires_at=LATER,
        preserve_state=True,
    )
    assert row is existing
    # Only the packaging identity moves; lifecycle state stays authoritative.
    assert (row.plan_code, row.policy_version) == ("pro", 2)
    assert row.is_active is False
    assert row.plan_expires_at == current_expiry


async def test_preserve_state_mirrors_the_preserved_state_not_the_requested_one():
    current_expiry = NOW + timedelta(days=3)
    existing = UserServicePlan(
        user_id=HOLDER, plan_code="starter", policy_version=2, is_active=True, plan_expires_at=current_expiry
    )
    session = _packaging(plans=[existing])
    session.policies[("starter", 2)] = _policy_row(plan_code="starter")
    await assign_user_plan(
        session,
        actor_user_id=OWNER,
        user_id=HOLDER,
        plan_code="starter",
        policy_version=2,
        plan_expires_at=LATER,
        preserve_state=True,
    )
    parameters = session.updates[0].compile(dialect=DIALECT).params
    assert parameters["plan_expires_at"] == current_expiry


async def test_preserve_state_requires_an_existing_plan_and_writes_nothing():
    session = _packaging()
    with pytest.raises(ServicePlanPolicyError, match="missing_plan"):
        await assign_user_plan(
            session, actor_user_id=OWNER, user_id=HOLDER, plan_code="pro", policy_version=2, preserve_state=True
        )
    assert session.plans == {} and session.pending == [] and session.updates == []


async def test_preserve_state_still_requires_the_selected_policy_revision():
    existing = UserServicePlan(user_id=HOLDER, plan_code="pro", policy_version=2, is_active=True, plan_expires_at=None)
    session = _packaging(plans=[existing])
    with pytest.raises(ServicePlanPolicyError):
        await assign_user_plan(
            session, actor_user_id=OWNER, user_id=HOLDER, plan_code="pro", policy_version=9, preserve_state=True
        )
    assert (existing.plan_code, existing.policy_version) == ("pro", 2)
    assert session.updates == []


async def test_preserve_state_still_validates_the_requested_state_arguments():
    """Even unused state arguments are type-checked: the API stays strict, not silently ignored."""
    existing = UserServicePlan(user_id=HOLDER, plan_code="pro", policy_version=2, is_active=True, plan_expires_at=None)
    session = _packaging(plans=[existing])
    with pytest.raises(ValueError, match="Invalid plan state or expiry"):
        await assign_user_plan(
            session,
            actor_user_id=OWNER,
            user_id=HOLDER,
            plan_code="pro",
            policy_version=2,
            is_active=1,
            preserve_state=True,
        )
    assert session.updates == []


@pytest.mark.parametrize("preserve_state", [1, "true", None])
async def test_preserve_state_flag_refuses_non_boolean_truthy_input(preserve_state):
    """A non-empty string/`1` must not silently enable 'keep the current state'."""
    existing = UserServicePlan(user_id=HOLDER, plan_code="pro", policy_version=2, is_active=False, plan_expires_at=None)
    session = _packaging(plans=[existing])
    with pytest.raises(ValueError, match="Invalid plan state or expiry"):
        await assign_user_plan(
            session,
            actor_user_id=OWNER,
            user_id=HOLDER,
            plan_code="pro",
            policy_version=2,
            is_active=True,
            plan_expires_at=LATER,
            preserve_state=preserve_state,
        )
    assert (existing.is_active, existing.plan_expires_at) == (False, None)
    assert session.updates == []


async def test_falsy_preserve_state_is_equivalent_to_a_fresh_assignment():
    existing = UserServicePlan(user_id=HOLDER, plan_code="pro", policy_version=2, is_active=False, plan_expires_at=None)
    session = _packaging(plans=[existing])
    row = await assign_user_plan(
        session,
        actor_user_id=OWNER,
        user_id=HOLDER,
        plan_code="pro",
        policy_version=2,
        is_active=True,
        plan_expires_at=LATER,
        preserve_state=False,
    )
    assert row is existing
    assert (row.is_active, row.plan_expires_at) == (True, LATER)
    assert len(session.updates) == 1


async def test_preserve_state_does_not_skip_actor_authority():
    existing = UserServicePlan(user_id=HOLDER, plan_code="pro", policy_version=2, is_active=True, plan_expires_at=None)
    session = _packaging(users=[(OUTSIDER, "admin", True), (HOLDER, "user", True)], plans=[existing])
    with pytest.raises(PermissionError, match="Platform owner"):
        await assign_user_plan(
            session, actor_user_id=OUTSIDER, user_id=HOLDER, plan_code="pro", policy_version=2, preserve_state=True
        )
    assert session.updates == []


# ── apply_backfill session double ───────────────────────────────────────────


class _Rows(list):
    def all(self):
        return list(self)


class _BackfillSession:
    """Ordered-row double: statements are classified by type and compiled SQL."""

    def __init__(self, users, tenants, membership_rows, policies):
        self.bind = _bind()
        self.users = {row.id: row for row in _users(*users)}
        self.tenants = {row.id: row for row in tenants}
        self.memberships = list(membership_rows)
        self.policies = {(row.plan_code, row.policy_version): row for row in policies}
        self.plans = {}
        self.resolutions = {}
        self.pending = []
        self.executed = []
        self.locks = []

    async def execute(self, statement):
        self.executed.append(statement)
        if isinstance(statement, type(text(""))):
            self.locks.append(" ".join(str(statement).split()))
            return _Rows()
        if getattr(statement, "is_update", False) and statement.entity_description["entity"] is Tenant:
            assert "tenants.id" in " ".join(str(statement).split()), "sponsorship updates must be per workspace"
            self.locks.append("sponsorship")
            return None
        sql = _compiled(statement)
        if "JOIN" in sql:
            assert TenantUser.__tablename__ in sql and WorkspaceRole.__tablename__ in sql
            assert _for_update(statement), "owner memberships must be locked"
            self.locks.append(TenantUser.__tablename__)
            return _Rows(
                [
                    (row.tenant_id, row.user_id)
                    for row in self.memberships
                    if row.role == "owner" and row.user_id is not None
                ]
            )
        raise AssertionError(f"unsupported execute(): {sql}")

    async def scalars(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        if entity is User:
            if _for_update(statement):
                self.locks.append(User.__tablename__)
            if {column.key for column in statement.selected_columns} == {"id"}:
                return _Scalars(sorted(self.users))
            ids = statement.compile(dialect=DIALECT).params["id_1"]
            return _Scalars([self.users[user_id] for user_id in sorted(ids) if user_id in self.users])
        if entity is ServicePlanPolicy:
            return _Scalars(self.policies.values())
        if entity is Tenant:
            assert _for_update(statement), "retained workspaces must be locked"
            self.locks.append(Tenant.__tablename__)
            return _Scalars(sorted(self.tenants.values(), key=lambda row: row.id))
        raise AssertionError(f"unsupported scalars() over {entity!r}")

    async def get(self, model, key, **kwargs):
        if model is UserServicePlan:
            return self.plans.get(key)
        if model is ServicePlanBackfillResolution:
            return self.resolutions.get(key)
        raise AssertionError(f"unexpected get({model!r})")

    def add(self, row):
        self.pending.append(row)

    async def flush(self):
        for row in self.pending:
            if isinstance(row, UserServicePlan):
                self.plans[row.user_id] = row
            elif isinstance(row, ServicePlanBackfillResolution):
                self.resolutions[row.tenant_id] = row
            else:  # pragma: no cover - defensive
                raise AssertionError(f"unexpected pending row {row!r}")
        self.pending.clear()

    def maintenance_locks(self):
        return [entry for entry in self.locks if entry.startswith("LOCK TABLE")]


def _membership(tenant_id, user_id, role):
    return SimpleNamespace(tenant_id=tenant_id, user_id=user_id, role=role)


def _backfill_world():
    return _BackfillSession(
        users=[(OWNER, "superuser", True), (HOLDER, "user", True), (HELD, "user", True)],
        tenants=[_tenant(TENANT_A, "pro"), _tenant(TENANT_B, "starter"), _tenant(TENANT_C, "starter")],
        membership_rows=[
            _membership(TENANT_A, HOLDER, "owner"),
            _membership(TENANT_B, HELD, "owner"),
            _membership(TENANT_B, OUTSIDER, "admin"),
            _membership(TENANT_C, HELD, "owner"),
        ],
        policies=[_policy_row(), _policy_row(plan_code="starter")],
    )


def _mapping():
    """B/C share one holder plan; their common `starter` legacy tier needs no extra decision."""
    return dict(
        holders={
            TENANT_A: HolderSelection(HOLDER, REFERENCE),
            TENANT_B: HolderSelection(HELD, REFERENCE),
            TENANT_C: HolderSelection(HELD, REFERENCE),
        },
        plans={HOLDER: HolderPlanSelection("pro", 2, True, None), HELD: HolderPlanSelection("starter", 2, True, None)},
    )


# ── apply_backfill: exclusive maintenance lock ──────────────────────────────


async def test_backfill_takes_exactly_one_exclusive_table_lock_first():
    session = _backfill_world()
    await apply_backfill(session, actor_user_id=OWNER, now=NOW, **_mapping())
    assert session.maintenance_locks() == [MAINTENANCE_LOCK]
    first = session.executed[0]
    assert isinstance(first, type(text("")))
    assert "ACCESS EXCLUSIVE MODE" in str(first)
    assert session.locks.count(MAINTENANCE_LOCK) == 1


async def test_maintenance_lock_covers_users_tenants_and_memberships_but_nothing_else():
    """A phantom user/workspace/membership during a complete-map check must be blocked."""
    session = _backfill_world()
    await apply_backfill(session, actor_user_id=OWNER, now=NOW, **_mapping())
    locked = session.maintenance_locks()[0]
    for name in ("users", "tenants", "tenant_users"):
        assert f".{name}," in locked or locked.endswith(f".{name} IN ACCESS EXCLUSIVE MODE")
    assert "service_plan_policies" not in locked
    assert "user_service_plans" not in locked


async def test_lock_schema_is_quoted_through_the_bound_dialect():
    session = _backfill_world()
    await apply_backfill(session, actor_user_id=OWNER, now=NOW, **_mapping())
    quoted = DIALECT.identifier_preparer.quote_schema(Tenant.__table__.schema)
    assert session.maintenance_locks()[0].startswith(f"LOCK TABLE {quoted}.users, {quoted}.tenants")


async def test_permission_refusal_happens_after_the_maintenance_lock_not_before():
    """The lock is unconditional maintenance: it never depends on the mapping or actor role."""
    session = _backfill_world()
    session.users[OWNER].role = SimpleNamespace(codename="admin")
    with pytest.raises(PermissionError, match="Platform owner"):
        await apply_backfill(session, actor_user_id=OWNER, now=NOW, **_mapping())
    assert session.maintenance_locks() == [MAINTENANCE_LOCK]
    assert session.pending == [] and session.resolutions == {}


# ── apply_backfill: complete explicit mapping ───────────────────────────────


async def test_complete_mapping_records_evidence_and_plans_for_every_workspace():
    session = _backfill_world()
    await apply_backfill(session, actor_user_id=OWNER, now=NOW, **_mapping())
    assert set(session.resolutions) == {TENANT_A, TENANT_B, TENANT_C}
    evidence = session.resolutions[TENANT_A]
    assert evidence.holder_user_id == HOLDER
    assert list(evidence.owner_user_ids) == [HOLDER]
    assert evidence.operator_user_id == OWNER
    assert evidence.decision_ref == REFERENCE
    assert evidence.resolved_at == NOW
    assert (evidence.legacy_plan, evidence.plan_code, evidence.policy_version) == ("pro", "pro", 2)
    second = session.resolutions[TENANT_B]
    assert (second.legacy_plan, second.plan_code, second.policy_version) == ("starter", "starter", 2)
    assert set(session.plans) == {HOLDER, HELD}
    assert {(row.plan_code, row.policy_version) for row in session.plans.values()} == {("pro", 2), ("starter", 2)}
    # The admin membership is not an owner and must not enter the snapshot.
    assert set(second.owner_user_ids) == {HELD}


async def test_backfill_records_the_operator_and_holder_decision_references():
    session = _backfill_world()
    await apply_backfill(session, actor_user_id=OWNER, now=NOW, **_mapping())
    assert {row.operator_user_id for row in session.resolutions.values()} == {OWNER}
    assert {row.decision_ref for row in session.resolutions.values()} == {REFERENCE}
    assert session.users[OWNER].is_superuser


async def test_backfill_locks_users_before_tenants_and_memberships():
    session = _backfill_world()
    await apply_backfill(session, actor_user_id=OWNER, now=NOW, **_mapping())
    order = [
        name for name in session.locks if name in {User.__tablename__, Tenant.__tablename__, TenantUser.__tablename__}
    ]
    assert order == [User.__tablename__, Tenant.__tablename__, TenantUser.__tablename__]


async def test_backfill_updates_sponsorship_by_workspace_identity_only():
    session = _backfill_world()
    await apply_backfill(session, actor_user_id=OWNER, now=NOW, **_mapping())
    statements = [
        statement
        for statement in session.executed
        # A `Select` has no `entity_description`; only ORM DML does.
        if getattr(statement, "is_update", False) and statement.entity_description["entity"] is Tenant
    ]
    assert len(statements) == 3
    for statement in statements:
        sql = " ".join(str(statement).split())
        assert sql.startswith(f"UPDATE {Tenant.__table__.fullname} SET plan_holder_user_id=")
        assert "lower" not in sql
        assert " plan=" not in sql and "SET plan=" not in sql
        # Per-workspace identity: the primary key is the whole predicate.
        where = sql.split("WHERE", 1)[1]
        assert where.strip() == f"{Tenant.__table__.fullname}.id = :id_1", where


# ── apply_backfill: missing / conflicting / retained rows ───────────────────


async def test_missing_workspace_selection_is_refused_before_any_write():
    session = _backfill_world()
    mapping = _mapping()
    mapping["holders"].pop(TENANT_B)
    with pytest.raises(BackfillMappingError, match="per workspace"):
        await apply_backfill(session, actor_user_id=OWNER, now=NOW, **mapping)
    assert session.pending == [] and session.resolutions == {} and session.plans == {}
    assert "sponsorship" not in session.locks


async def test_conflicting_existing_evidence_is_refused():
    session = _backfill_world()
    await apply_backfill(session, actor_user_id=OWNER, now=NOW, **_mapping())
    session.pending.clear()
    conflicting = _mapping()
    conflicting["holders"][TENANT_B] = HolderSelection(HELD, REFERENCE + "_v2")
    with pytest.raises(BackfillMappingError, match="evidence conflicts"):
        await apply_backfill(session, actor_user_id=OWNER, now=NOW, **conflicting)
    assert session.resolutions[TENANT_B].decision_ref == REFERENCE


async def test_retained_rows_still_require_a_complete_mapping():
    session = _backfill_world()
    with pytest.raises(BackfillMappingError, match="per workspace"):
        await apply_backfill(session, actor_user_id=OWNER, now=NOW, holders={}, plans={})
    assert session.pending == []


@pytest.mark.parametrize("role", ["user", "operator", "admin"])
async def test_backfill_requires_the_platform_owner(role):
    session = _backfill_world()
    session.users[OWNER].role = SimpleNamespace(codename=role)
    with pytest.raises(PermissionError, match="Platform owner"):
        await apply_backfill(session, actor_user_id=OWNER, now=NOW, **_mapping())
    assert session.pending == []


async def test_legacy_tier_conflict_for_one_holder_needs_a_plan_decision():
    session = _backfill_world()
    session.tenants[TENANT_B].plan = "pro"
    with pytest.raises(BackfillMappingError, match="conflict needs a decision"):
        await apply_backfill(session, actor_user_id=OWNER, now=NOW, **_mapping())
    assert session.pending == []


# ── apply_backfill: idempotency and plan protection ─────────────────────────


async def test_reapplying_the_identical_mapping_yields_no_new_rows():
    session = _backfill_world()
    await apply_backfill(session, actor_user_id=OWNER, now=NOW, **_mapping())
    stored = (dict(session.resolutions), dict(session.plans))
    session.executed.clear()
    session.locks.clear()
    await apply_backfill(session, actor_user_id=OWNER, now=NOW + timedelta(hours=1), **_mapping())
    assert session.pending == []
    assert dict(session.resolutions) == stored[0]
    assert dict(session.plans) == stored[1]
    # The maintenance lock is taken again; only per-workspace sponsorship is rewritten.
    assert session.maintenance_locks() == [MAINTENANCE_LOCK]
    rewrites = [row for row in session.executed if getattr(row, "is_update", False)]
    assert len(rewrites) == 3
    assert all(row.entity_description["entity"] is Tenant for row in rewrites)
    assert session.executed[0].__class__ is type(text(""))


async def test_conflicting_existing_account_plan_cannot_be_overwritten():
    session = _backfill_world()
    session.plans[HELD] = UserServicePlan(
        user_id=HELD, plan_code="business", policy_version=2, is_active=True, plan_expires_at=None
    )
    session.policies[("business", 2)] = _policy_row(plan_code="business")
    with pytest.raises(BackfillMappingError, match="cannot overwrite"):
        await apply_backfill(session, actor_user_id=OWNER, now=NOW, **_mapping())
    assert session.plans[HELD].plan_code == "business"
    assert HOLDER not in session.plans
