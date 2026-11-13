#!/usr/bin/env python3
"""Serialized local P2-T17 increment-A verifier on its own `test_p2_t17` schema.

`--plan` validates the target and opens no connection. `--execute`:

1. holds advisory lock 1370093 on a dedicated autocommit connection for the whole
   run, refuses a pre-existing `test_p2_t17`, snapshots the `public` and
   `test_schema` inventories, then creates the schema;
2. runs `p17t08_onec_history_status -> p2t17_service_plan_storage ->
   p2t17_service_plan_finalize` through `migrations/env.py`'s
   `config.attributes["connection"]` contract on an ordinary transactional
   SQLAlchemy connection, so a refused migration rolls back intact;
3. probes the storage CHECK constraints, immutability triggers and the policy
   management API inside SAVEPOINTs that are always rolled back, then proves an
   empty downgrade;
4. seeds retained legacy users/workspaces/memberships with Core INSERTs on the
   predecessor columns only, proves they survive a downgrade that rests on no
   target rows, re-applies storage, then runs policy/backfill/finalize, engine,
   workspace-scoped admission, owned limits, races, RESTRICT, retention and a
   refused non-empty downgrade;
5. drops only the schema it created, re-inventories the other schemas, releases
   the lock. Synthetic rows carry a `p2t17` username/slug.

Admissions are counted per workspace (`entitlement_usage`), while the policy, its
daily quota and its limits belong to the holder. Not a pytest module: it owns its
isolated schema and performs no provider or external write.

    set -a; . ./.env; set +a
    DB_SCHEMA=test_p2_t17 python scripts/verify_p2_t17_service_plans.py --execute
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import traceback
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOCK_ID = 1370093
SCHEMA = "test_p2_t17"
PREDECESSOR = "p17t08_onec_history_status"
STORAGE = "p2t17_service_plan_storage"
FINALIZE = "p2t17_service_plan_finalize"
STORAGE_TABLES = ("service_plan_policies", "user_service_plans", "service_plan_backfill_resolutions")
#: Registered collection capabilities only: a key the validator would reject is
#: never used. CONSUME is what the engine checks, GRANTED what the policy grants.
CAPABILITY, DENIED, UNKNOWN = "connector.vk.read", "connector.vk.search", "connector.unknown.read"
CONSUME = (CAPABILITY,)
#: Test-only packaging at policy version 1: 3 daily admissions, a bounded
#: owned-workspace limit and the token allowance definition (unit `token`).
VERSION, QUOTA, TOKEN_LIMIT, OWNED_FINITE = 1, 3, 1000, 2
WORKSPACES = ("primary", "secondary", "ambiguous", "holdered", "invite_only", "other_scope")
RESULTS: list[tuple[str, str, object]] = []
STATE: dict = {}


class Refusal(RuntimeError):
    """Precondition or contract breach; the run stops with FAIL."""


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise Refusal(message)


def resolve_target() -> str:
    """Validate the local database target and `DB_SCHEMA` without importing app."""
    raw = (os.environ.get("TEST_POSTGRES_URL") or os.environ.get("POSTGRES_URL") or "").strip()
    parsed = urlsplit(raw)
    if (
        not raw
        or parsed.scheme not in {"postgresql", "postgresql+asyncpg", "postgresql+psycopg", "postgresql+psycopg2"}
        or parsed.hostname not in {"localhost", "127.0.0.1"}
        or (parsed.port or 5432) != 5432
        or parsed.path != "/social_manager"
    ):
        raise Refusal("STOP: the verifier requires localhost|127.0.0.1:5432/social_manager")
    if os.environ.get("DB_SCHEMA", "").strip() != SCHEMA:
        raise Refusal(f"STOP: DB_SCHEMA must be exactly {SCHEMA!r}")
    return raw


def libpq_dsn(raw: str) -> str:
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://", "postgresql+psycopg2://"):
        if raw.startswith(prefix):
            return "postgresql://" + raw[len(prefix) :]
    return raw


def _inventory() -> dict:
    from scripts.ci import schema_inventory

    return {name: schema_inventory.snapshot(name) for name in ("public", "test_schema")}


# ── psycopg2 helpers on the run's own autocommit connection ───────────────────


def _rows(sql: str, params=()) -> list[tuple]:
    with closing(STATE["conn"].cursor()) as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []


def _one(sql: str, params=()):
    rows = _rows(sql, params)
    return None if not rows else rows[0]


def _scalar(sql: str, params=()):
    return _one(sql, params)[0]


def _tables() -> set[str]:
    return {
        row[0]
        for row in _rows(
            "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = %s and c.relkind in ('r', 'p')",
            (SCHEMA,),
        )
    }


def _revision() -> str | None:
    if "alembic_version" not in _tables():
        return None
    versions = [row[0] for row in _rows(f'select version_num from "{SCHEMA}".alembic_version')]
    _assert(len(versions) == 1, f"alembic_version holds {len(versions)} rows")
    return versions[0]


def _storage_rows() -> tuple[int, int, int]:
    present = _tables()
    return tuple(_scalar(f"select count(*) from {SCHEMA}.{t}") if t in present else 0 for t in STORAGE_TABLES)


def _refused(sql: str, params=()) -> str:
    """Run one statement in a SAVEPOINT, roll it back and return the error line."""
    with closing(STATE["conn"].cursor()) as cur:
        from psycopg2.extensions import STATUS_READY

        owns_transaction = STATE["conn"].status == STATUS_READY
        if owns_transaction:
            cur.execute("begin")
        cur.execute("savepoint p2t17_probe")
        try:
            cur.execute(sql, params)
        except Exception as error:  # noqa: BLE001 - the server message is the evidence
            cur.execute("rollback to savepoint p2t17_probe")
            cur.execute("release savepoint p2t17_probe")
            if owns_transaction:
                cur.execute("rollback")
            return str(error).strip().splitlines()[0]
        cur.execute("rollback to savepoint p2t17_probe")
        cur.execute("release savepoint p2t17_probe")
        if owns_transaction:
            cur.execute("rollback")
    raise Refusal(f"statement was not refused: {sql[:90]}")


def _expect(sql: str, *markers: str, params=()) -> str:
    """A refusal counts only when the server names the expected guard."""
    message = _refused(sql, params)
    _assert(any(marker in message for marker in markers), f"{sql[:60]} refused unexpectedly: {message}")
    return message


def _insert(sql: str, params) -> int:
    with closing(STATE["conn"].cursor()) as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


# ── migration chain on a transactional connection ─────────────────────────────


def _migrate(action: str, target: str, *, expect: str | None = None) -> None:
    """Upgrade/downgrade in one transaction; `expect` demands a rolled-back refusal."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    before = _revision()
    engine = create_engine(libpq_dsn(os.environ["POSTGRES_URL"]).replace("postgresql://", "postgresql+psycopg2://", 1))
    try:
        with engine.begin() as connection:
            config = Config(str(ROOT / "alembic.ini"))
            config.attributes["connection"] = connection
            try:
                {"upgrade": command.upgrade, "downgrade": command.downgrade}[action](config, target)
            except RuntimeError as error:
                _assert(expect is not None and expect in str(error), f"{action} {target} refused: {error}")
                STATE["refusal"] = str(error).strip().splitlines()[-1]
                connection.rollback()
            else:
                _assert(expect is None, f"{action} {target} did not refuse {expect!r}")
    finally:
        engine.dispose()
    expected_revision = before if expect is not None else target
    _assert(_revision() == expected_revision, f"{action} {target} ended at {_revision()}")


# ── storage contract: constraints, immutability, shape (all rolled back) ──────


def _storage_contract() -> dict:
    policies = f"{SCHEMA}.service_plan_policies"
    head = "(plan_code, policy_version, module_grants, daily_quotas, resource_limits"
    cases = {
        "plan_code": ("ck_service_plan_policy_code", "values ('enterprise', 1, '[]', '{}', '{}')"),
        "version": ("ck_service_plan_policy_version", "values ('pro', 0, '[]', '{}', '{}')"),
        "grants": ("ck_service_plan_policy_grants", "values ('pro', 9, '{}', '{}', '{}')"),
    }
    refused = {name: _expect(f"insert into {policies} {head}) {tail}", guard) for name, (guard, tail) in cases.items()}
    tail = f"{head}, owned_workspace_limit_configured, max_owned_workspaces) values ('pro', 9, '[]', '{{}}', '{{}}'"
    refused["unconfigured_limit"] = _expect(
        f"insert into {policies} {tail}, false, 5)", "ck_service_plan_policy_workspaces"
    )
    refused["negative_limit"] = _expect(
        f"insert into {policies} {tail}, true, -1)", "ck_service_plan_policy_workspaces"
    )
    tokens = f"{head}, token_policy_configured"
    refused["partial_token"] = _expect(
        f"insert into {policies} {tokens}, token_unit) values ('pro', 9, '[]', '{{}}', '{{}}', true, 'token')",
        "ck_service_plan_policy_tokens",
    )
    refused["stray_token"] = _expect(
        f"insert into {policies} {tokens}, token_limit) values ('pro', 9, '[]', '{{}}', '{{}}', false, 10)",
        "ck_service_plan_policy_tokens",
    )
    refused["usage_holder"] = _expect(
        f"insert into {SCHEMA}.entitlement_usage (tenant_id, capability, job_id, plan, catalog_version,"
        " plan_holder_user_id) values (1, 'connector.vk.read', 1, 'pro', 1, 0)",
        "ck_entitlement_usage_holder_policy",
    )
    _assert(_storage_rows() == (0, 0, 0), "a refused insert left a storage row")
    # Immutability must hold for a version no consumer references yet: the row is
    # inserted in a SAVEPOINT that is released only after the probes, so every
    # trigger has a row to refuse and nothing survives.
    with closing(STATE["conn"].cursor()) as cur:
        cur.execute("begin")
        cur.execute("savepoint p2t17_immutable")
        cur.execute(
            f"insert into {policies} (plan_code, policy_version, module_grants, daily_quotas, resource_limits,"
            " owned_workspace_limit_configured, max_owned_workspaces, token_policy_configured, token_unit,"
            " token_period, token_limit) values ('pro', 8, '[]', '{}', '{}', true, 1, true, 'token', 'utc_day', 1)"
        )
    immutable = {
        "update": _expect(f"update {policies} set module_grants = '[]' where policy_version = 8", "immutable"),
        "delete": _expect(f"delete from {policies} where policy_version = 8", "immutable"),
        "truncate": _expect(f"truncate {policies}", "immutable"),
    }
    _assert(_scalar(f"select count(*) from {policies} where policy_version = 8") == 1, "a trigger let the row go")
    with closing(STATE["conn"].cursor()) as cur:
        cur.execute("rollback to savepoint p2t17_immutable")
        cur.execute("release savepoint p2t17_immutable")
        cur.execute("rollback")
    _assert(_storage_rows() == (0, 0, 0), "the immutability probe left rows")
    columns = {
        row[0]
        for row in _rows(
            "select table_name || '.' || column_name from information_schema.columns where table_schema = %s "
            "and ((table_name = 'tenants' and column_name = 'plan_holder_user_id') or (table_name ="
            " 'entitlement_usage' and column_name in ('plan_holder_user_id', 'plan_policy_version')))",
            (SCHEMA,),
        )
    }
    _assert(set(STORAGE_TABLES) <= _tables(), "storage tables missing")
    _assert(
        columns
        == {
            "tenants.plan_holder_user_id",
            "entitlement_usage.plan_holder_user_id",
            "entitlement_usage.plan_policy_version",
        },
        f"holder evidence columns are wrong: {columns}",
    )
    return {"refusals": sorted(refused), "immutable": sorted(immutable), "seed_rows": 0}


# ── legacy seed on the predecessor revision (Core INSERT, old columns only) ───


def _seed_legacy() -> dict:
    """Users, workspaces and memberships; no storage column is referenced."""
    roles = {
        name: _scalar(f"select id from {SCHEMA}.roles where codename = %s", (name,)) for name in ("user", "superuser")
    }
    roles.update(
        {
            name: _scalar(f"select id from {SCHEMA}.workspace_roles where codename = %s", (name,))
            for name in ("owner", "viewer")
        }
    )
    _assert(all(roles.values()), f"reference roles missing: {roles}")
    user_sql = (
        f"insert into {SCHEMA}.users (username, email, hashed_password, is_active, role_id, created_at, updated_at)"
        " values (%s, %s, 'x-not-a-credential', %s, %s, now(), now()) returning id"
    )
    tenant_sql = (
        f"insert into {SCHEMA}.tenants (name, slug, plan, plan_expires_at, is_active)"
        " values (%s, %s, %s, %s, true) returning id"
    )
    member_sql = (
        f"insert into {SCHEMA}.tenant_users (tenant_id, channel, external_user_id, user_id, role_id, is_active)"
        " values (%s, 'web', %s, %s, %s, true) returning id"
    )
    people = {
        label: _insert(user_sql, (f"p2t17-{label}", f"p2t17-{label}@example.invalid", active, roles[role]))
        for label, role, active in (
            ("holder", "user", True),
            ("invitee", "user", True),
            ("other", "user", True),
            ("shared", "user", True),
            ("inactive", "user", False),
            ("operator", "superuser", True),
        )
    }
    legacy, pods = {}, {}
    for slug, plan in (
        ("primary", "starter"),
        ("secondary", "business"),
        ("ambiguous", "pro"),
        ("holdered", "pro"),
        ("invite-only", "pro"),
        ("other-scope", "pro"),
    ):
        expires = datetime.now(timezone.utc) + timedelta(days=30) if slug == "holdered" else None
        pods[slug.replace("-", "_")] = tenant = _insert(tenant_sql, (f"P2-T17 {slug}", f"p2t17-{slug}", plan, expires))
        legacy[tenant] = (plan, expires)
    for slug, member, role in (
        ("primary", "holder", "owner"),
        ("primary", "invitee", "viewer"),
        ("secondary", "holder", "owner"),
        ("secondary", "invitee", "viewer"),
        ("ambiguous", "holder", "owner"),
        ("ambiguous", "shared", "owner"),
        ("holdered", "inactive", "owner"),
        ("invite_only", "holder", "viewer"),
        ("other_scope", "other", "owner"),
    ):
        _insert(member_sql, (pods[slug], str(people[member]), people[member], roles[role]))
    STATE.update(users=people, pods=pods, legacy=legacy)
    return {"users": len(people), "workspaces": len(pods), "memberships": 9}


def _identity() -> dict:
    """Retained workspace columns the whole run must keep unchanged."""
    return {
        row[0]: row[1:]
        for row in _rows(f"select id, name, slug, plan, plan_expires_at, is_active from {SCHEMA}.tenants order by id")
    }


# ── policy management through the real API ────────────────────────────────────


def _policy_values(plan_code: str, **overrides) -> dict:
    from app.models.tenancy.tenant import Tenant
    from app.services.tenancy.capabilities import CAPABILITIES

    _assert({CAPABILITY, DENIED} <= set(CAPABILITIES), "verifier capabilities are not registered")
    values = dict(
        plan_code=plan_code,
        policy_version=VERSION,
        module_grants=[CAPABILITY],
        daily_quotas={CAPABILITY: QUOTA},
        resource_limits=json.loads(json.dumps(Tenant.PLAN_LIMITS[plan_code])),
        owned_workspace_limit_configured=True,
        max_owned_workspaces=OWNED_FINITE,
        token_policy_configured=True,
        token_unit="token",
        token_period="utc_day",
        token_limit=TOKEN_LIMIT,
    )
    values.update(overrides)
    return values


def _policies() -> dict:
    from sqlalchemy import select

    from app.core.database import async_session_maker
    from app.models.tenancy.service_plan import ServicePlanPolicy
    from app.services.tenancy.service_plan_management import create_policy
    from app.services.tenancy.service_plan_policy import ServicePlanPolicyError, policy_snapshot

    operator, holder = STATE["users"]["operator"], STATE["users"]["holder"]

    async def make(values: dict, expected: str | None = None) -> str:
        async with async_session_maker() as session:
            try:
                await create_policy(session, actor_user_id=operator, values=values)
            except ServicePlanPolicyError as error:
                await session.rollback()
                _assert(expected is not None and error.reason == expected, f"refused as {error.reason!r}")
                return error.reason
            await session.commit()
        _assert(expected is None, f"{expected!r} was not refused")
        return "created"

    async def run() -> dict:
        await make(_policy_values("pro"))
        await make(_policy_values("starter", max_owned_workspaces=0, token_limit=0))
        unconfigured = dict(token_policy_configured=False, token_unit=None, token_period=None, token_limit=None)
        await make(_policy_values("business", max_owned_workspaces=None, token_limit=None))
        await make(
            _policy_values(
                "pro",
                policy_version=2,
                owned_workspace_limit_configured=False,
                max_owned_workspaces=None,
                **unconfigured,
            )
        )
        refused = {
            "unknown_module_grant": await make(
                _policy_values("pro", policy_version=8, module_grants=[UNKNOWN]), "unknown_module_grant"
            ),
            "unknown_daily_quota": await make(
                _policy_values("pro", policy_version=3, daily_quotas={CAPABILITY.rsplit(".", 1)[0]: QUOTA}),
                "unknown_daily_quota",
            ),
        }
        partial = _policy_values("pro", policy_version=4)
        partial.pop("token_period")
        refused["incomplete_policy_definition"] = await make(partial, "incomplete_policy_definition")
        async with async_session_maker() as session:
            try:
                await create_policy(session, actor_user_id=holder, values=_policy_values("pro", policy_version=5))
            except PermissionError as error:
                refused["plain_user"] = str(error)
            else:
                raise Refusal("a plain user created a policy")
            await session.rollback()
        async with async_session_maker() as session:
            snapshot = {
                (row.plan_code, row.policy_version): policy_snapshot(row)
                for row in (await session.scalars(select(ServicePlanPolicy))).all()
            }
        pro = snapshot[("pro", VERSION)]
        _assert(pro.module_grants == (CAPABILITY,) and pro.daily_quotas.get(CAPABILITY) == QUOTA, f"pro policy {pro}")
        _assert(pro.token_policy.unit == "token" and pro.token_policy.token_limit == TOKEN_LIMIT, "tokens")
        _assert(pro.max_owned_workspaces == OWNED_FINITE, "owned-workspace limit lost")
        _assert(snapshot[("business", VERSION)].token_policy.token_limit is None, "explicit unlimited token lost")
        _assert(snapshot[("starter", VERSION)].token_policy.token_limit == 0, "zero token definition lost")
        _assert(snapshot[("pro", 2)].token_policy is None, "an unconfigured token became unlimited")
        return {"policies": sorted(snapshot), "refusals": refused}

    return asyncio.run(run())


# ── backfill mapping, evidence, finalization ──────────────────────────────────


def _expected_holders() -> dict:
    users, pods = STATE["users"], STATE["pods"]
    return {
        pods["primary"]: users["holder"],
        pods["secondary"]: users["holder"],
        pods["ambiguous"]: users["shared"],
        pods["holdered"]: users["inactive"],
        pods["invite_only"]: users["other"],
        pods["other_scope"]: users["other"],
    }


def _mapping() -> tuple[dict, dict]:
    from app.services.tenancy.service_plan_backfill import HolderPlanSelection, HolderSelection

    users = STATE["users"]
    holders = {
        tenant: HolderSelection(holder, f"P2-T17 operator decision for workspace {tenant}")
        for tenant, holder in _expected_holders().items()
    }
    # starter/business holders of pro workspaces and an inactive holder conflict.
    decision = "P2-T17 decision for the legacy tier conflict"
    return holders, {
        users["holder"]: HolderPlanSelection("pro", VERSION, True, None, decision),
        users["shared"]: HolderPlanSelection("pro", VERSION, True, None, decision),
        users["inactive"]: HolderPlanSelection("pro", VERSION, True, None, decision),
        users["other"]: HolderPlanSelection("pro", VERSION, True, None),
    }


def _retained(identity: dict) -> dict:
    """Resolved holders plus the exact retained evidence rows."""
    rows = _rows(
        f"select tenant_id, holder_user_id, owner_user_ids, legacy_plan, legacy_plan_expires_at, plan_decision_ref,"
        f" operator_user_id, decision_ref from {SCHEMA}.service_plan_backfill_resolutions order by tenant_id"
    )
    holders = dict(_rows(f"select id, plan_holder_user_id from {SCHEMA}.tenants order by id"))
    expected = _expected_holders()
    _assert(holders == expected, f"resolved holders {holders} != {expected}")
    _assert(len(rows) == len(WORKSPACES), f"{len(rows)} backfill evidence rows")
    _assert(_identity() == identity, "retained workspace identity changed")
    for row in rows:
        _assert(row[1] == expected[row[0]], f"evidence holder mismatch: {row[:2]}")
        _assert((row[3], row[4]) == STATE["legacy"][row[0]], f"legacy plan/expiry rewritten: {row[:5]}")
        _assert(row[6] == STATE["users"]["operator"] and bool(row[7]), f"operator/decision missing: {row[:2]}")
        if STATE["legacy"][row[0]][0] != "pro":
            _assert(bool(row[5]), f"a tier conflict was not marked with a plan decision: {row[:2]}")
    ambiguous = next(row for row in rows if row[0] == STATE["pods"]["ambiguous"])
    _assert(
        sorted(ambiguous[2]) == sorted([STATE["users"]["holder"], STATE["users"]["shared"]]),
        f"ambiguous owner snapshot is lossy: {ambiguous[2]}",
    )
    return {"holders": holders, "rows": rows}


def _apply_backfill() -> int:
    from app.core.database import async_session_maker
    from app.services.tenancy.service_plan_management import apply_backfill

    holders, plans = _mapping()

    async def run() -> int:
        async with async_session_maker() as session, session.begin():
            applied = await apply_backfill(
                session, actor_user_id=STATE["users"]["operator"], holders=holders, plans=plans
            )
        return len(applied)

    return asyncio.run(run())


def _backfill() -> dict:
    identity = STATE["identity"]
    _assert(_storage_rows()[1:] == (0, 0), "an account plan existed before the explicit mapping")
    first, evidence = _apply_backfill(), None
    evidence = _retained(identity)
    second = _apply_backfill()
    _assert(first == second == len(WORKSPACES), f"{first}/{second} resolutions")
    _assert(_retained(identity) == evidence, "idempotent retry changed retained evidence")
    return {"applied": first, "evidence_rows": len(evidence["rows"]), "identity_unchanged": True}


def _finalization() -> dict:
    pods, users = STATE["pods"], STATE["users"]
    _rows(f"update {SCHEMA}.tenants set plan_holder_user_id = null where id = {int(pods['holdered'])}")
    _migrate("upgrade", FINALIZE, expect="unresolved sponsorship")
    refused = STATE.pop("refusal")
    # Restore exactly the cleared sponsorship from its retained evidence.
    holder = _scalar(
        f"select holder_user_id from {SCHEMA}.service_plan_backfill_resolutions where tenant_id = %s",
        (pods["holdered"],),
    )
    _assert(holder == users["inactive"], "the retained evidence names another holder")
    _rows(f"update {SCHEMA}.tenants set plan_holder_user_id = {int(holder)} where id = {int(pods['holdered'])}")
    _migrate("upgrade", FINALIZE)
    nullable = _scalar(
        "select is_nullable from information_schema.columns where table_schema = %s "
        "and table_name = 'tenants' and column_name = 'plan_holder_user_id'",
        (SCHEMA,),
    )
    _assert(_revision() == FINALIZE and nullable == "NO", f"finalize: {_revision()} nullable={nullable}")
    return {"refused": refused, "revision": _revision(), "nullable": nullable}


# ── resolution and the holder-authoritative engine ────────────────────────────


def _resolution() -> dict:
    from app.core.database import async_session_maker
    from app.services.tenancy.service_plan_management import assign_user_plan
    from app.services.tenancy.service_plan_policy import ServicePlanPolicyError, resolve_holder_context

    users, pods, legacy = STATE["users"], STATE["pods"], STATE["legacy"]

    async def resolve(tenant: str, actor: str):
        async with async_session_maker() as session:
            return await resolve_holder_context(session, pods[tenant], users[actor])

    async def reason(tenant: str, actor: str) -> str:
        try:
            await resolve(tenant, actor)
        except ServicePlanPolicyError as error:
            return error.reason
        raise Refusal(f"{tenant}/{actor} resolved without a denial")

    async def assign(**kwargs) -> None:
        async with async_session_maker() as session, session.begin():
            await assign_user_plan(session, actor_user_id=users["operator"], user_id=users["holder"], **kwargs)

    async def run() -> dict:
        invitee = await resolve("primary", "invitee")
        _assert(invitee.plan_holder_user_id == users["holder"], "the holder was not the sponsor")
        _assert(invitee.actor_user_id == users["invitee"], "the actor was replaced by the holder")
        _assert(invitee.policy.module_grants == (CAPABILITY,), f"invitee received {invitee.policy.module_grants}")
        _assert((invitee.plan_code, invitee.plan_policy_version) == ("pro", VERSION), "wrong holder plan")
        owner = await resolve("secondary", "holder")
        _assert(owner.plan_holder_user_id == users["holder"], "a second owned workspace read another sponsor")
        denied = {
            "non_member": await reason("primary", "other"),
            "inactive_actor": await reason("holdered", "inactive"),
        }
        await assign(plan_code="pro", policy_version=VERSION, plan_expires_at=datetime.now(timezone.utc))
        denied["expired_holder"] = await reason("primary", "invitee")
        await assign(plan_code="pro", policy_version=VERSION, is_active=False)
        denied["inactive_plan"] = await reason("primary", "invitee")
        await assign(plan_code="pro", policy_version=VERSION, plan_expires_at=legacy[pods["primary"]][1])
        restored = await resolve("primary", "invitee")
        _assert(
            (restored.plan_policy_version, restored.plan_expires_at) == (VERSION, legacy[pods["primary"]][1]),
            "the restored plan is not the expected one",
        )
        return {"holder": invitee.plan_holder_user_id, "actor": invitee.actor_user_id, "denials": denied}

    result = asyncio.run(run())
    _assert(
        result["denials"]
        == {
            "non_member": "actor_membership_inactive",
            "inactive_actor": "actor_inactive",
            "expired_holder": "plan_expired",
            "inactive_plan": "plan_inactive",
        },
        f"denials are wrong: {result['denials']}",
    )
    return result


def _engine() -> dict:
    from sqlalchemy import select

    from app.core.database import async_session_maker
    from app.models.tenancy.tenant import Tenant
    from app.services.tenancy.entitlements import decide
    from app.services.tenancy.service_plan_policy import ServicePlanPolicyError

    async def run() -> dict:
        async with async_session_maker() as session:
            workspace = (
                await session.execute(select(Tenant).where(Tenant.id == STATE["pods"]["primary"]))
            ).scalar_one()
        decision = decide(workspace, [CAPABILITY, DENIED, UNKNOWN], policy=workspace._holder_policy())
        denials = {item["capability"]: item["reason"] for item in decision.denials}
        reasons = {}
        # Fail closed at two levels, asserted separately: the model property raises
        # `holder_plan_missing` (no loaded sponsor relationship), while the resolver
        # and `decide` report `missing_plan_holder` for an unverified holder row.
        for name, tenant_id, inactive in (
            ("inactive_workspace", STATE["pods"]["primary"], True),
            # A live workspace id that never existed cannot resolve a holder either.
            ("missing_holder", 2147483000, False),
        ):
            probe = Tenant(id=tenant_id, name="P2-T17 probe", slug="p2t17-probe", plan="pro")
            probe.is_active = not inactive
            try:
                probe._holder_policy()
            except ServicePlanPolicyError as error:
                reasons[name] = error.reason
        return {"decision": decision, "denials": denials, "reasons": reasons, "workspace": workspace}

    result = asyncio.run(run())
    decision, denials, workspace = result["decision"], result["denials"], result["workspace"]
    _assert(
        decision.plan == "pro" and denials == {DENIED: "not_granted", UNKNOWN: "unknown_capability"},
        f"granted capability is not the holder's: {decision.plan}, {denials}",
    )
    limits, ceiling = workspace.effective_limits(), workspace.PLAN_LIMITS["pro"]
    _assert(limits["max_sources"] == ceiling["max_sources"] and workspace.plan_label == ceiling["label"], "limits")
    _assert(workspace.plan == "pro", f"compatibility mirror is {workspace.plan}")
    _assert(
        result["reasons"] == {"inactive_workspace": "workspace_inactive", "missing_holder": "holder_plan_missing"},
        f"fail-closed branches are wrong: {result['reasons']}",
    )
    return {
        "plan": decision.plan,
        "max_sources": limits["max_sources"],
        "denials": denials,
        "reasons": result["reasons"],
    }


# ── workspace-scoped admissions under the holder's policy ─────────────────────


def _admission() -> dict:
    from sqlalchemy import func, select

    from app.core.database import async_session_maker
    from app.core.permissions import permission_scope
    from app.core.tenant_context import reset_tenant, set_tenant
    from app.models.identity.user import User
    from app.models.managers.entitlement_manager import EntitlementManager
    from app.models.scheduling.job import Job
    from app.models.tenancy.entitlement_usage import EntitlementUsage
    from app.services.tenancy.entitlements import EntitlementDenied

    users, pods = STATE["users"], STATE["pods"]

    async def stage(tenant: str, actor: str, count: int) -> list[tuple[int, str]]:
        """Commit the running jobs first: a persisted execution never stages under the gate.

        Each job gets its own committed transaction, so the `initiated_by_user_id`
        KEY SHARE locks are gone before any admission starts. Staging inside the
        admission transaction would hold them while `lock_source_admission` takes
        the holder FOR UPDATE, which is an artificial multi-session deadlock.
        """
        async with async_session_maker() as session:
            fresh = await session.get(User, users[actor])
        staged = []
        token = set_tenant(pods[tenant])
        try:
            with permission_scope(fresh):
                for _ in range(count):
                    async with async_session_maker() as session, session.begin():
                        job = Job(
                            tenant_id=pods[tenant],
                            job_type="collect",
                            payload={},
                            status="running",
                            run_at=datetime.now(timezone.utc),
                            initiated_by_user_id=users[actor],
                        )
                        session.add(job)
                        await session.flush()
                        staged.append((job.id, actor))
        finally:
            reset_tenant(token)
        return staged

    async def admit(tenant: str, job_id: int, actor: str) -> str:
        """One admission exactly as the runtime performs it for a persisted job.

        Ordinary workspace scope and an actor-loaded `permission_scope` only, then
        the runtime gate (`User` FOR UPDATE -> `Tenant` NO KEY UPDATE) followed by
        the holder-biased decision and the append-only usage row.
        """
        async with async_session_maker() as session, session.begin():
            fresh = await session.get(User, users[actor])
            token = set_tenant(pods[tenant])
            try:
                with permission_scope(fresh):
                    await EntitlementManager().evaluate(session, CONSUME, job_id=job_id, consume=True)
            except EntitlementDenied as error:
                await session.rollback()
                return f"{actor}:{error.decision.denials[0]['reason']}"
            finally:
                reset_tenant(token)
        return f"{actor}:admitted"

    async def run() -> dict:
        primary = await stage("primary", "holder", QUOTA + 1)
        secondary = await stage("secondary", "invitee", QUOTA + 1)
        outcomes = [await admit("primary", job_id, actor) for job_id, actor in primary]
        outcomes.extend(await asyncio.gather(*(admit("secondary", *job) for job in secondary)))
        invitee = await stage("primary", "invitee", 1)
        outcomes.append(await admit("primary", *invitee[0]))
        # A committed job is what the runtime holds; a burst exercises the gate.
        burst = await stage("primary", "holder", QUOTA)
        concurrent = await asyncio.gather(*(admit("primary", job_id, actor) for job_id, actor in burst))
        async with async_session_maker() as session:
            rows = [
                tuple(row)
                for row in await session.execute(
                    select(
                        EntitlementUsage.tenant_id,
                        EntitlementUsage.plan,
                        EntitlementUsage.plan_holder_user_id,
                        EntitlementUsage.plan_policy_version,
                        EntitlementUsage.initiated_by_user_id,
                        EntitlementUsage.job_id,
                    )
                )
            ]
            jobs = dict((await session.execute(select(Job.id, Job.initiated_by_user_id))).all())
            total = await session.scalar(select(func.count()).select_from(EntitlementUsage))
        return {"outcomes": outcomes, "concurrent": concurrent, "rows": rows, "jobs": jobs, "total": total}

    result = asyncio.run(run())
    outcomes, rows = result["outcomes"], result["rows"]
    _assert(outcomes[:QUOTA] == ["holder:admitted"] * QUOTA, f"primary admissions: {outcomes}")
    _assert(outcomes[QUOTA] == "holder:quota_exhausted", "a further admission passed the holder quota")
    _assert(outcomes[QUOTA + 1 :].count("invitee:admitted") == QUOTA, "secondary lost its workspace quota")
    _assert(outcomes[QUOTA + 1 :].count("invitee:quota_exhausted") == 2, "invitee bypassed the host workspace quota")
    _assert(result["concurrent"] == ["holder:quota_exhausted"] * QUOTA, f"concurrent: {result['concurrent']}")
    _assert(len(rows) == result["total"] == 2 * QUOTA, f"{len(rows)} admitted rows for workspace quota {QUOTA}")
    _assert({row[0] for row in rows} == {pods["primary"], pods["secondary"]}, "workspace attribution lost")
    _assert(
        {(row[1], row[2], row[3]) for row in rows} == {("pro", users["holder"], VERSION)},
        f"admissions did not bind the holder policy: {rows}",
    )
    _assert({row[4] for row in rows} == {users["holder"], users["invitee"]}, "the initiating actor was not persisted")
    _assert(all(result["jobs"][row[5]] == row[4] for row in rows), "job attribution was lost")
    return {"outcomes": outcomes, "admitted_rows": len(rows), "quota": QUOTA}


# ── owned-workspace limit and two-session races ───────────────────────────────


def _account(label: str) -> int:
    role = _scalar(f"select id from {SCHEMA}.roles where codename = 'user'")
    return _insert(
        f"insert into {SCHEMA}.users (username, email, hashed_password, is_active, role_id, created_at, updated_at)"
        " values (%s, %s, 'x-not-a-credential', true, %s, now(), now()) returning id",
        (f"p2t17-{label}", f"p2t17-{label}@example.invalid", role),
    )


def _assign(user_id: int, code: str, **extra) -> None:
    from app.core.database import async_session_maker
    from app.services.tenancy.service_plan_management import assign_user_plan

    async def run() -> None:
        async with async_session_maker() as session, session.begin():
            await assign_user_plan(
                session,
                actor_user_id=STATE["users"]["operator"],
                user_id=user_id,
                plan_code=code,
                policy_version=extra.pop("policy_version", VERSION),
                **extra,
            )

    asyncio.run(run())


def _create(user_id: int, slug: str) -> str:
    from app.core.database import async_session_maker
    from app.models.managers.tenant_manager import PlanLimitError, TenantManager

    async def run() -> str:
        try:
            async with async_session_maker() as session, session.begin():
                await TenantManager().create_owned_workspace(
                    user_id=user_id, name=f"P2-T17 {slug}", slug=slug, session=session
                )
        except PlanLimitError:
            return "denied"
        return "created"

    return asyncio.run(run())


def _owned(user_id: int) -> int:
    return _scalar(f"select count(*) from {SCHEMA}.tenants where plan_holder_user_id = %s", (user_id,))


def _memberships(user_id: int) -> int:
    return _scalar(
        f"select count(*) from {SCHEMA}.tenant_users tu join {SCHEMA}.tenants t on t.id = tu.tenant_id "
        "where tu.user_id = %s and t.plan_holder_user_id = %s",
        (user_id, user_id),
    )


def _owned_limits() -> dict:
    from app.core.config import settings

    _assert(settings.USER_SERVICE_PLANS_ENABLED, "USER_SERVICE_PLANS_ENABLED must be on for this run")
    _assert(_owned(STATE["users"]["other"]) == 2, "the mapped holder does not own 2 workspaces")
    cases = {
        "finite": (_account("finite"), "pro"),
        "zero": (_account("zero"), "starter"),
        "unlimited": (_account("unlimited"), "business"),
        "missing": (_account("missing"), None),
    }
    for label, (user, code) in cases.items():
        _assert(_owned(user) == 0, f"{label} already owns workspaces")
        if code:
            _assign(user, code)
        else:
            _assign(user, "pro", policy_version=2)
    outcomes = {
        label: [_create(user, f"{label}-{slot}") for slot in range(2)] for label, (user, _code) in cases.items()
    }
    outcomes["no_plan"] = _create(_account("no-plan"), "no-plan")
    _assert(outcomes["finite"] == ["created"] * 2, f"finite limit: {outcomes['finite']}")
    _assert(outcomes["zero"] == ["denied"] * 2, f"zero limit created a workspace: {outcomes['zero']}")
    _assert(outcomes["unlimited"] == ["created"] * 2, f"unlimited plan was capped: {outcomes['unlimited']}")
    _assert(outcomes["no_plan"] == "denied", "an account without a plan created a workspace")
    _assert(outcomes["missing"] == ["denied"] * 2, "unconfigured workspace limit allowed creation")
    counts = {label: [_owned(user), _memberships(user)] for label, (user, _code) in cases.items()}
    _assert(
        counts == {"finite": [2, 2], "zero": [0, 0], "unlimited": [2, 2], "missing": [0, 0]},
        f"owned/membership counts: {counts}",
    )
    return {"outcomes": outcomes, "owned_and_memberships": counts}


def _races() -> dict:
    from sqlalchemy import text

    from app.core.database import async_session_maker
    from app.models.managers.tenant_manager import PlanLimitError, TenantManager
    from app.services.tenancy.service_plan_management import assign_user_plan
    from app.services.tenancy.service_plan_policy import resolve_holder_context

    users, pods = STATE["users"], STATE["pods"]
    holder = _account("race")
    _assign(holder, "pro")

    async def run() -> dict:
        async def create(slot: int) -> str:
            try:
                async with async_session_maker() as session, session.begin():
                    await TenantManager().create_owned_workspace(
                        user_id=holder, name=f"P2-T17 race {slot}", slug=f"p2t17-race-{slot}", session=session
                    )
            except PlanLimitError:
                return "denied"
            return "created"

        creation = sorted([result for result in await asyncio.gather(*(create(slot) for slot in range(4)))])
        reader_locked, writer_done = asyncio.Event(), asyncio.Event()

        async def reader() -> dict:
            async with async_session_maker() as session, session.begin():
                ctx = await resolve_holder_context(session, pods["primary"], users["invitee"], lock_holder=True)
                reader_locked.set()
                # Only a real row-lock wait in another session releases the reader.
                await asyncio.wait_for(writer_done.wait(), timeout=30)
                return {"plan": ctx.plan_code, "version": ctx.plan_policy_version}

        async def writer() -> str:
            await asyncio.wait_for(reader_locked.wait(), timeout=30)
            async with async_session_maker() as session:
                pending = asyncio.ensure_future(
                    session.execute(
                        text(f"select id from {SCHEMA}.users where id = :uid for update"), {"uid": users["holder"]}
                    )
                )
                try:
                    # Order is established by PostgreSQL, not by a timer: the writer
                    # must sit in a lock wait while the reader still holds the row.
                    for _ in range(60):
                        if not pending.done():
                            blocked = _scalar(
                                "select count(*) from pg_stat_activity where state = 'active' "
                                "and wait_event_type = 'Lock' and pid <> pg_backend_pid()"
                            )
                            if blocked:
                                break
                        await asyncio.sleep(0.1)
                    else:
                        raise Refusal("the plan-change writer never blocked on the locked holder row")
                finally:
                    writer_done.set()
                await pending
                await session.rollback()
            async with async_session_maker() as session, session.begin():
                await assign_user_plan(
                    session,
                    actor_user_id=users["operator"],
                    user_id=users["holder"],
                    plan_code="starter",
                    policy_version=VERSION,
                )
            return "written"

        task = asyncio.ensure_future(writer())
        during = await reader()
        written = await task
        async with async_session_maker() as session:
            after = await resolve_holder_context(session, pods["primary"], users["invitee"])
        async with async_session_maker() as session, session.begin():
            await assign_user_plan(
                session,
                actor_user_id=users["operator"],
                user_id=users["holder"],
                plan_code="pro",
                policy_version=VERSION,
                plan_expires_at=STATE["legacy"][pods["primary"]][1],
            )
        return {"creation": creation, "during": during, "writer": written, "after": after.plan_code}

    result = asyncio.run(run())
    _assert(result["creation"] == ["created", "created", "denied", "denied"], f"creation race: {result['creation']}")
    _assert((_owned(holder), _memberships(holder)) == (OWNED_FINITE, OWNED_FINITE), "the race exceeded/broke the count")
    _assert(
        result["during"] == {"plan": "pro", "version": VERSION}
        and result["writer"] == "written"
        and result["after"] == "starter",
        f"locked resolution and plan change were incoherent: {result}",
    )
    return result


# ── RESTRICT, retention, refused non-empty downgrade ─────────────────────────


def _restrict() -> dict:
    users, rows = STATE["users"], f"{SCHEMA}.service_plan_backfill_resolutions"
    refused = {
        "holder_user": _expect(
            f"delete from {SCHEMA}.users where id = {users['holder']}",
            "fk_tenants_plan_holder_user",
            "fk_jobs_initiated_by_user",
            "user_service_plans_user_id_fkey",
        ),
        "policy_version": _expect(
            f"delete from {SCHEMA}.service_plan_policies where plan_code = 'pro' and policy_version = {VERSION}",
            "immutable",
        ),
        "policy_fk": _expect(
            f"update {SCHEMA}.user_service_plans set policy_version = 99 where user_id = {users['holder']}",
            "fk_user_service_plan_policy",
        ),
        "backfill_delete": _expect(f"delete from {rows}", "immutable"),
        "backfill_update": _expect(f"update {rows} set holder_user_id = holder_user_id", "immutable"),
        "backfill_truncate": _expect(f"truncate {rows}", "immutable"),
    }
    _assert(_scalar(f"select count(*) from {SCHEMA}.user_service_plans") > 0, "a refused delete removed plans")
    _assert(_scalar(f"select count(*) from {rows}") == len(WORKSPACES), "backfill evidence was destroyed")
    return {"refused": sorted(refused)}


def _retention() -> dict:
    from sqlalchemy import select

    from app.core.database import async_session_maker
    from app.core.permissions import permission_scope
    from app.core.tenant_context import reset_tenant, set_tenant
    from app.models.identity.user import User
    from app.models.tenancy.tenant import Tenant
    from app.services.tenancy.offboarding import OffboardingError, confirm_offboarding, preview_offboarding

    workspace, operator = STATE["pods"]["holdered"], STATE["users"]["operator"]

    async def run() -> dict:
        async def mirror() -> dict:
            async with async_session_maker() as session:
                return dict((await session.execute(select(Tenant.id, Tenant.plan).order_by(Tenant.id))).all())

        before = await mirror()
        async with async_session_maker() as session:
            actor = await session.get(User, operator)
        token = set_tenant(workspace, bypass=True)
        try:
            with permission_scope(actor):
                summary = await preview_offboarding(operator, workspace)
                _assert(summary["workspace_id"] == workspace, "the preview named another workspace")
                _assert(
                    {"blockers", "can_delete", "review_token", "retained_audit_count"} <= set(summary),
                    f"preview contract is {sorted(summary)}",
                )
                confirmed = (
                    await confirm_offboarding(operator, summary["review_token"]) if summary["can_delete"] else None
                )
                result = {"blockers": summary["blockers"], "can_delete": summary["can_delete"], "confirmed": confirmed}
        except OffboardingError as error:
            result = {"blockers": [f"{error.status_code}:{error.code}"], "can_delete": False, "confirmed": None}
        finally:
            reset_tenant(token)
        result["mirrors_unchanged"] = before == await mirror()
        return result

    result = asyncio.run(run())
    evidence = _scalar(
        f"select count(*) from {SCHEMA}.service_plan_backfill_resolutions where tenant_id = %s", (workspace,)
    )
    remaining = _scalar(f"select count(*) from {SCHEMA}.tenants where id = %s", (workspace,))
    if result["confirmed"] is None:
        _assert(result["blockers"] and remaining == 1, "a blocked offboarding deleted the workspace")
    else:
        events = _scalar(
            f"select count(*) from {SCHEMA}.workspace_offboarding_events where workspace_id = %s", (workspace,)
        )
        _assert(remaining == 0 and events == 1, "offboarding kept the workspace or journalled no receipt")
    _assert(evidence == 1, "historical backfill evidence was not retained")
    _assert(_scalar(f"select count(*) from {SCHEMA}.service_plan_policies") > 0, "policy evidence was destroyed")
    return {
        "can_delete": result["can_delete"],
        "blockers": result["blockers"],
        "offboarded": result["confirmed"] is not None,
        "backfill_evidence_retained": evidence,
    }


def _nonempty_downgrade() -> dict:
    rows = _storage_rows()
    _assert(rows[0] > 0, "the non-empty downgrade needs retained policy rows")
    _migrate("downgrade", PREDECESSOR, expect="must be preserved")
    _assert(_revision() == FINALIZE, f"refused downgrade left revision {_revision()}")
    _assert(_storage_rows() == rows, "refused downgrade damaged retained rows")
    return {"refused": STATE.pop("refusal"), "rows": list(rows), "revision": _revision()}


# ── orchestration ────────────────────────────────────────────────────────────


def _record(name: str, call) -> None:
    try:
        RESULTS.append((name, "PASS", call()))
    except BaseException as error:  # noqa: BLE001 - evidence, then stop
        RESULTS.append((name, "FAIL", f"{type(error).__name__}: {error}"))
        raise


def _summary() -> str:
    lines = ["", f"P2-T17 increment A: schema={SCHEMA} chain={PREDECESSOR}->{STORAGE}->{FINALIZE}"]
    for name, status, detail in RESULTS:
        text = detail if isinstance(detail, str) else json.dumps(detail, sort_keys=True, default=str)
        lines.append(f"[{status}] {name}: {text}")
    failed = sum(1 for _, status, _ in RESULTS if status != "PASS")
    lines.append(f"SUMMARY: {len(RESULTS) - failed} passed, {failed} failed")
    return "\n".join(lines)


def _prepare() -> None:
    """Empty downgrade/re-apply, then upgrade over retained legacy rows."""
    _record("storage contract and constraints", _storage_contract)
    _record("empty downgrade", lambda: (_migrate("downgrade", PREDECESSOR), _storage_rows(), _revision())[1:])
    _assert(_storage_rows() == (0, 0, 0) and _revision() == PREDECESSOR, "empty downgrade left storage objects")
    _record("legacy seed", _seed_legacy)
    STATE["identity"] = identity = _identity()
    _assert(len(identity) == len(WORKSPACES), f"expected {len(WORKSPACES)} seeded workspaces, found {len(identity)}")
    _record("re-apply storage over retained rows", lambda: (_migrate("upgrade", STORAGE), _identity() == identity)[1])
    _assert(_identity() == identity, "storage upgrade changed retained workspace rows")
    _assert(_scalar(f"select count(*) from {SCHEMA}.tenant_users") == 9, "storage upgrade changed memberships")
    _assert(_revision() == STORAGE, f"upgrade ended at {_revision()}")


STEPS = (
    ("policy definitions and management", _policies),
    ("backfill mapping and evidence", _backfill),
    ("finalization", _finalization),
    ("holder resolution", _resolution),
    ("holder engine", _engine),
    ("workspace-scoped admissions", _admission),
    ("owned-workspace limits", _owned_limits),
    ("creation race and locked resolution", _races),
    ("restrictive FKs and immutable history", _restrict),
    ("retention and offboarding", _retention),
    ("nonempty downgrade refusal", _nonempty_downgrade),
)


def _cleanup(conn, before: dict) -> bool:
    from scripts.ci.schema_inventory import diff

    with closing(conn.cursor()) as cur:
        cur.execute(f'drop schema if exists "{SCHEMA}" cascade')
        cur.execute("select 1 from pg_namespace where nspname = %s", (SCHEMA,))
        absent = cur.fetchone() is None
    RESULTS.append(("cleanup: owned schema dropped", "PASS" if absent else "FAIL", {"absent": absent}))
    after = _inventory()
    problems = {name: diff(before[name], after[name]) for name in before}
    problems = {name: found for name, found in problems.items() if found}
    RESULTS.append(
        ("cleanup: other schema inventories", "FAIL" if problems else "PASS", problems or {"unchanged": True})
    )
    return absent and not problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--execute", action="store_true", help="create, verify and drop the isolated schema")
    parser.add_argument("--plan", action="store_true", help="print the plan only; opens no connection")
    args = parser.parse_args(argv)
    raw = resolve_target()
    print(f"Target: localhost:5432/social_manager/{SCHEMA}; lock {LOCK_ID}; {PREDECESSOR}->{STORAGE}->{FINALIZE}")
    if not args.execute:
        print("No connection made. Pass --execute for the isolated run.")
        return 0

    os.environ.update(
        {"POSTGRES_URL": raw, "DB_SCHEMA": SCHEMA, "DB_POOL": "null", "USER_SERVICE_PLANS_ENABLED": "true"}
    )
    import psycopg2

    failure: BaseException | None = None
    created, before, lock, cleanup_ok = False, {}, None, True
    try:
        lock = psycopg2.connect(libpq_dsn(raw))
        lock.autocommit = True
        with closing(lock.cursor()) as cur:
            cur.execute("select pg_try_advisory_lock(%s)", (LOCK_ID,))
            _assert(bool(cur.fetchone()[0]), f"another runner holds advisory lock {LOCK_ID}")
            cur.execute("select 1 from pg_namespace where nspname = %s", (SCHEMA,))
            _assert(cur.fetchone() is None, f"refusing: schema {SCHEMA} exists and is never dropped automatically")
        before = _inventory()
        STATE["conn"] = psycopg2.connect(libpq_dsn(raw))
        STATE["conn"].autocommit = True
        with closing(STATE["conn"].cursor()) as cur:
            cur.execute(f'create schema "{SCHEMA}"')
        created = True
        _record("upgrade to storage", lambda: (_migrate("upgrade", STORAGE), _revision())[1])
        _prepare()
        for name, step in STEPS:
            _record(name, step)
    except BaseException as error:  # noqa: BLE001 - cleanup must run for every failure
        failure = error
        traceback.print_exc()
    finally:
        if STATE.get("conn") is not None:
            try:
                STATE.pop("conn").close()
            except Exception:  # noqa: BLE001
                pass
        if lock is not None:
            try:
                if created:
                    cleanup_ok = _cleanup(lock, before)
                with closing(lock.cursor()) as cur:
                    cur.execute("select pg_advisory_unlock(%s)", (LOCK_ID,))
            except Exception as error:  # noqa: BLE001 - reported, never fatal
                RESULTS.append(("cleanup", "FAIL", f"{type(error).__name__}: {error}"))
                cleanup_ok = False
            finally:
                lock.close()

    print(_summary())
    return 0 if failure is None and cleanup_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
