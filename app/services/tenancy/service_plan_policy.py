"""Validated plan-policy snapshot and the trusted holder resolution for P21-T12.

The subscription subject is the user; a workspace is *sponsored* by exactly one
holder (`tenants.plan_holder_user_id`). This module owns the read side of that
contract (see `docs/tasks/p2-t17.md`, "Holder and policy interface"):

* `policy_snapshot` turns a stored policy row into a deeply frozen, validated DTO;
* `load_workspace_policy` resolves a trusted internal workspace scope;
* `resolve_holder_context` additionally proves the authenticated actor is a live
  member of that workspace and returns the holder's policy, never the actor's.

Every lookup fails closed with a bounded `.reason` instead of falling back to a
guessed tier, `PLAN_CATALOG` or a default limit. Absence of a policy (missing
row, unconfigured limit) is never treated as "unlimited" except where the column
explicitly says so (configured + NULL).

Callers are trusted internal code and supply an already-authenticated actor and
workspace scope. Nothing here reads a payload, query string or client-supplied
id, and nothing here writes, consumes allowances or counts workspaces.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any

from sqlalchemy import Row, exists, select

from app.models.identity.user import User
from app.models.tenancy.service_plan import ServicePlanPolicy, UserServicePlan
from app.models.tenancy.tenant import Tenant
from app.models.tenancy.tenant_user import TenantUser

from .capabilities import CAPABILITIES
from .plan_catalog import PLAN_CATALOG, grants_capability

#: PostgreSQL BIGINT upper bound for exact integer policy values.
_MAX_BIGINT = 2**63 - 1
#: Column widths of `token_unit` / `token_period` in the storage schema.
_TEXT_LIMIT = 80
#: The capability pattern that grants every registered key.
_ALL_CAPABILITIES = "*"


class ServicePlanPolicyError(ValueError):
    """Fail-closed policy/holder resolution error; `reason` is a bounded token."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _fail(reason: str) -> ServicePlanPolicyError:
    return ServicePlanPolicyError(reason)


# ── scalar validation ────────────────────────────────────────────────────────


def _identity(value: Any) -> bool:
    """A positive database identity; `bool` is rejected despite being an int."""
    return type(value) is int and value > 0


def _bounded_int(value: Any) -> bool:
    return type(value) is int and 0 <= value <= _MAX_BIGINT


def _bounded_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= _TEXT_LIMIT


def _aware(value: Any) -> bool:
    return isinstance(value, datetime) and value.utcoffset() is not None


def _frozen_mapping(values: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(values))


# ── DTOs ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TokenAllowancePolicy:
    """Token allowance *definition* only; reservation/settlement is P21-T15's."""

    unit: str
    period: str
    #: `None` because the policy configured NULL = explicitly unlimited.
    token_limit: int | None
    policy_version: int


@dataclass(frozen=True)
class ValidatedPlanPolicy:
    """Immutable snapshot of one `(plan_code, policy_version)` policy row."""

    plan_code: str
    policy_version: int
    module_grants: tuple[str, ...]
    daily_quotas: Mapping[str, int]
    resource_limits: Mapping[str, Any]
    owned_workspace_limit_configured: bool
    max_owned_workspaces: int | None
    #: `None` means the plan defines no token allowance at all (token dispatch
    #: must deny); it is never a synonym for "unlimited".
    token_policy: TokenAllowancePolicy | None


@dataclass(frozen=True)
class WorkspacePlanContext:
    """The typed holder context handed by reference to P21-T12.

    `actor_user_id` is the authenticated actor for `resolve_holder_context` and
    `None` for a trusted internal workspace lookup. Identity fields stay
    separate: the holder is not the actor.
    """

    actor_user_id: int | None
    tenant_id: int
    plan_holder_user_id: int
    plan_code: str
    plan_policy_version: int
    plan_expires_at: datetime | None
    policy: ValidatedPlanPolicy


# ── pure snapshot ────────────────────────────────────────────────────────────


def _module_grants(raw: Any) -> tuple[str, ...]:
    """Every pattern must be a registered key, a namespace wildcard or `*`.

    Composition stays in `.capabilities.CAPABILITIES`; this only refuses what
    the accepted engine could never grant. An empty tuple is a legitimate plan
    with zero module rights: the capability engine then denies every required
    key, so it is not malformed.
    """
    if isinstance(raw, (str, bytes, Mapping)) or not isinstance(raw, (list, tuple)):
        raise _fail("invalid_module_grants")
    for pattern in raw:
        if not isinstance(pattern, str) or not pattern:
            raise _fail("invalid_module_grant_pattern")
        if pattern == _ALL_CAPABILITIES:
            continue
        if pattern.endswith(".*"):
            namespace = pattern[:-1]
            if not any(key.startswith(namespace) for key in CAPABILITIES):
                raise _fail("unknown_module_grant")
            continue
        if pattern not in CAPABILITIES:
            raise _fail("unknown_module_grant")
        # `grants_capability` is the single decision rule; a registered key must
        # be granted by its own literal pattern or the registry is inconsistent.
        if not grants_capability(pattern, pattern):
            raise _fail("unknown_module_grant")
    return tuple(raw)


def _daily_quotas(raw: Any) -> Mapping[str, int]:
    if not isinstance(raw, Mapping):
        raise _fail("invalid_daily_quotas")
    quotas: dict[str, int] = {}
    for key, value in raw.items():
        # The policy grants capabilities; a quota key must be one of them.
        if not isinstance(key, str) or key not in CAPABILITIES:
            raise _fail("unknown_daily_quota")
        # A count is exact: `None`, booleans and floats are data defects.
        if not _bounded_int(value):
            raise _fail("invalid_daily_quota_value")
        quotas[key] = value
    return _frozen_mapping(quotas)


def _resource_schema() -> Mapping[str, Any]:
    """The existing resource-limit schema; every tier declares the same keys."""
    return Tenant.PLAN_LIMITS[next(iter(Tenant.PLAN_LIMITS))]


def _limit_kind(key: str) -> Any:
    """A concrete example of the key's JSON kind from the existing tiers.

    Business declares several keys unlimited (`None`), so a nullable key falls
    back to the first finite example elsewhere in `PLAN_LIMITS`.
    """
    for limits in Tenant.PLAN_LIMITS.values():
        value = limits[key]
        if value is not None:
            return value
    raise _fail("invalid_resource_limit")


def _limit_value(key: str, value: Any) -> Any:
    """Strict JSON check against `Tenant.PLAN_LIMITS`' existing schema.

    * a key business declares unlimited keeps `None` (meaningful, never a default);
    * counts (`max_*`, `retention_days`) are exact nonnegative ints - a float or a
      bool here would silently change a ceiling;
    * money (`daily_cost_limit`) is a finite nonnegative int or float;
    * flags are exact bools, lists are nonempty string tuples, labels are text.
    """
    if value is None and Tenant.PLAN_LIMITS["business"][key] is None:
        return None
    example = _limit_kind(key)
    if isinstance(example, bool):
        if type(value) is not bool:
            raise _fail("invalid_resource_limit")
        return value
    if isinstance(example, list):
        if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, (list, tuple)):
            raise _fail("invalid_resource_limit")
        if any(not isinstance(item, str) or not item for item in value):
            raise _fail("invalid_resource_limit")
        return tuple(value)
    if isinstance(example, str):
        if not isinstance(value, str) or not value.strip():
            raise _fail("invalid_resource_limit")
        return value
    if isinstance(example, int):
        # Counts: `type` keeps bools and floats out. Zero is a valid ceiling.
        if type(value) is not int or not _bounded_int(value):
            raise _fail("invalid_resource_limit")
        return value
    if isinstance(example, float):
        # Monetary limits may be int or float, but always finite and nonnegative.
        if type(value) is int:
            return value if _bounded_int(value) else _fail("invalid_resource_limit")
        if type(value) is not float or not math.isfinite(value) or value < 0:
            raise _fail("invalid_resource_limit")
        return value
    raise _fail("invalid_resource_limit")


def _resource_limits(raw: Any) -> Mapping[str, Any]:
    """The exact `PLAN_LIMITS` key set, fully typed; missing or extra keys deny.

    A partial dict would hand consumers a missing `max_sources` that reads as
    unlimited, so completeness is part of validation rather than a caller's job.
    """
    if not isinstance(raw, Mapping):
        raise _fail("invalid_resource_limits")
    schema = _resource_schema()
    if set(raw) != set(schema):
        raise _fail("invalid_resource_limit_keys")
    limits: dict[str, Any] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or key not in schema:
            raise _fail("unknown_resource_limit")
        limits[key] = _limit_value(key, value)
    return _frozen_mapping(limits)


def _token_policy(row: Any) -> TokenAllowancePolicy | None:
    configured = getattr(row, "token_policy_configured", None)
    if type(configured) is not bool:
        raise _fail("invalid_token_policy")
    unit = getattr(row, "token_unit", None)
    period = getattr(row, "token_period", None)
    limit = getattr(row, "token_limit", None)
    if not configured:
        # Unconfigured values must be NULL; a stray value is malformed, not implicit.
        if unit is not None or period is not None or limit is not None:
            raise _fail("invalid_token_policy")
        # Explicitly missing definition: denies token dispatch downstream.
        return None
    if not _bounded_text(unit) or not _bounded_text(period):
        raise _fail("invalid_token_policy")
    if limit is not None and not _bounded_int(limit):
        raise _fail("invalid_token_policy")
    return TokenAllowancePolicy(
        unit=unit,
        period=period,
        token_limit=limit,
        policy_version=row.policy_version,
    )


def policy_snapshot(policy_row: Any) -> ValidatedPlanPolicy:
    """Validate one stored policy row; no I/O, no defaults, no catalog fallback."""
    if policy_row is None:
        raise _fail("missing_policy")
    plan_code = getattr(policy_row, "plan_code", None)
    policy_version = getattr(policy_row, "policy_version", None)
    if not isinstance(plan_code, str) or plan_code not in PLAN_CATALOG:
        raise _fail("unknown_plan")
    if not _identity(policy_version):
        raise _fail("invalid_policy_version")

    configured = getattr(policy_row, "owned_workspace_limit_configured", None)
    max_owned = getattr(policy_row, "max_owned_workspaces", None)
    if type(configured) is not bool:
        raise _fail("invalid_owned_workspace_limit")
    if not configured:
        # Not configured must be NULL: an implicit value would be a guessed default.
        if max_owned is not None:
            raise _fail("invalid_owned_workspace_limit")
    elif max_owned is not None and not _bounded_int(max_owned):
        raise _fail("invalid_owned_workspace_limit")

    return ValidatedPlanPolicy(
        plan_code=plan_code,
        policy_version=policy_version,
        module_grants=_module_grants(getattr(policy_row, "module_grants", None)),
        daily_quotas=_daily_quotas(getattr(policy_row, "daily_quotas", None)),
        resource_limits=_resource_limits(getattr(policy_row, "resource_limits", None)),
        owned_workspace_limit_configured=configured,
        max_owned_workspaces=max_owned,
        token_policy=_token_policy(policy_row),
    )


# ── SQL lookups (existing AsyncSession only) ─────────────────────────────────


def _is_projection(row: Any) -> bool:
    """A multi-column result row: a real `sqlalchemy.engine.Row` or a plain tuple.

    `Row` is *not* a `tuple` subclass, so a `tuple`-only check refused every real
    driver row. Both carry positional indexing, which is all this module needs.
    """
    return isinstance(row, tuple) or isinstance(row, Row)


async def _one(session: Any, stmt: Any, *, name: str = "holder", width: int = 1) -> Any:
    """One projected row (or `None`) from an explicit column select.

    `session.scalar()` on a multi-column statement returns the first column only,
    so projections always go through `execute(...).one_or_none()`. The shape check
    turns a harness that ignored the projection into a refusal instead of an
    `IndexError`; the width check proves the projection held what the caller reads.
    Every caller here reads a column that is unique by key, so `one_or_none()` is
    the right strictness; existence rules use `_exists()` instead.
    """
    result = await session.execute(stmt)
    row = result.one_or_none()
    if row is None:
        return None
    if not _is_projection(row):
        raise _fail(f"invalid_projection:{name}")
    if len(row) != width:
        raise _fail(f"invalid_projection:{name}")
    return row


def _now(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if not _aware(current):
        raise _fail("invalid_now")
    return current


def _expiry(plan: Any) -> datetime | None:
    """Aware, optionally NULL (= no expiry). A naive expiry cannot be compared."""
    expires = getattr(plan, "plan_expires_at", None)
    if expires is not None and not _aware(expires):
        raise _fail("invalid_plan_expiry")
    return expires


def _expired(expires: datetime | None, now: datetime) -> bool:
    return expires is not None and expires <= now


async def _lock_holder(session: Any, holder_id: int, *, lock: bool) -> None:
    """Prove a fresh *active* holder in both modes; lock its row when asked.

    Locked mode locks and proves existence in one statement; the activity proof
    still runs as part of the same lock scope, because a locked-but-deactivated
    holder must deny exactly like the non-locking path. Lock order is User →
    (never Tenant) here; `outbox_store.actor_authority` also takes User first, so
    no cycle is introduced.
    """
    row = await _one(
        session,
        (
            select(User.id).where(User.id == holder_id).with_for_update()
            if lock
            else select(User.id).where(User.id == holder_id)
        ),
        name="holder",
    )
    if row is None or row[0] != holder_id:
        raise _fail("holder_missing")
    # A fresh active check precedes the plan read: an MRU cache hit must not
    # decide entitlement for a user that was just deactivated.
    if not await _holder_is_active(session, holder_id):
        raise _fail("holder_inactive")


async def _holder_is_active(session: Any, holder_id: int) -> bool:
    row = await _one(session, select(User.id).where(User.id == holder_id, User.is_active.is_(True)))
    return row is not None


async def _exists(session: Any, condition: Any) -> bool:
    """Existence of at least one matching row; never a uniqueness claim."""
    return bool(await session.scalar(select(exists().where(condition))))


async def _active_user(session: Any, user_id: int) -> bool:
    return await _exists(session, (User.id == user_id) & (User.is_active.is_(True)))


async def _active_member(session: Any, tenant_id: int, user_id: int) -> bool:
    """Existence of a live membership; the platform user may hold several rows.

    `tenant_users` is unique per `(tenant_id, channel, external_user_id)` and per
    web membership, so one platform user can have a telegram, a web (and more)
    row in the same workspace. Any single active one is the proven membership.
    """
    return await _exists(
        session,
        (TenantUser.tenant_id == tenant_id) & (TenantUser.user_id == user_id) & (TenantUser.is_active.is_(True)),
    )


async def _workspace_holder(session: Any, tenant_id: int, *, lock_holder: bool) -> tuple[int, int]:
    """Active workspace → `(tenant_id, plan_holder_user_id)`; no role fallback."""
    row = await _one(
        session,
        select(Tenant.id, Tenant.plan_holder_user_id).where(Tenant.id == tenant_id, Tenant.is_active.is_(True)),
        name="tenant",
        width=2,
    )
    if row is None:
        raise _fail("workspace_inactive")
    holder_id = row[1]
    if not _identity(holder_id):
        raise _fail("missing_plan_holder")
    await _lock_holder(session, holder_id, lock=lock_holder)
    # Same `!=` discipline as the holder row: the identity that comes back must be
    # the requested workspace, not merely comparable.
    if row[0] != tenant_id:
        raise _fail("workspace_mismatch")
    return row[0], holder_id


async def _holder_policy(
    session: Any, holder_id: int, *, now: datetime, lock_holder: bool
) -> tuple[Any, ValidatedPlanPolicy]:
    query = select(UserServicePlan).where(UserServicePlan.user_id == holder_id)
    if lock_holder:
        # Re-read the possibly just-written plan under the holder row lock.
        query = query.with_for_update()
    plan = await session.scalar(query.execution_options(populate_existing=True))
    if plan is None:
        raise _fail("missing_plan")
    if plan.is_active is not True:
        raise _fail("plan_inactive")
    expires = _expiry(plan)
    if _expired(expires, now):
        raise _fail("plan_expired")
    policy_row = await session.scalar(
        select(ServicePlanPolicy)
        .where(
            ServicePlanPolicy.plan_code == plan.plan_code,
            ServicePlanPolicy.policy_version == plan.policy_version,
        )
        .execution_options(populate_existing=True)
    )
    if policy_row is None:
        raise _fail("missing_policy")
    policy = policy_snapshot(policy_row)
    if policy.plan_code != plan.plan_code or policy.policy_version != plan.policy_version:
        raise _fail("policy_mismatch")
    return plan, policy


def _context(
    *,
    actor_user_id: int | None,
    tenant_id: int,
    holder_id: int,
    plan: Any,
    policy: ValidatedPlanPolicy,
) -> WorkspacePlanContext:
    return WorkspacePlanContext(
        actor_user_id=actor_user_id,
        tenant_id=tenant_id,
        plan_holder_user_id=holder_id,
        plan_code=policy.plan_code,
        plan_policy_version=policy.policy_version,
        plan_expires_at=getattr(plan, "plan_expires_at", None),
        policy=policy,
    )


async def load_workspace_policy(
    session: Any,
    tenant_id: int,
    *,
    now: datetime | None = None,
    lock_holder: bool = False,
) -> WorkspacePlanContext:
    """Trusted internal lookup of the sponsoring holder's policy for a workspace.

    No actor is validated here: callers without an authenticated actor (jobs,
    planners) use this and pass `actor_user_id=None`. Authenticated requests must
    use `resolve_holder_context`, which also proves the membership.
    """
    if not _identity(tenant_id):
        raise _fail("invalid_tenant_id")
    current = _now(now)
    resolved_tenant, holder_id = await _workspace_holder(session, tenant_id, lock_holder=lock_holder)
    plan, policy = await _holder_policy(session, holder_id, now=current, lock_holder=lock_holder)
    return _context(actor_user_id=None, tenant_id=resolved_tenant, holder_id=holder_id, plan=plan, policy=policy)


async def resolve_holder_context(
    session: Any,
    tenant_id: int,
    actor_user_id: int,
    *,
    now: datetime | None = None,
    lock_holder: bool = False,
) -> WorkspacePlanContext:
    """Holder policy for an authenticated actor inside a verified workspace scope.

    The actor must be a live platform user and an active member of the workspace;
    no workspace role (including `owner`/`admin`) bypasses that. The returned
    policy is always the sponsoring holder's: when the actor is an invitee, the
    actor's own plan is neither read nor allowed to widen the holder's rights.
    """
    if not _identity(tenant_id):
        raise _fail("invalid_tenant_id")
    if not _identity(actor_user_id):
        raise _fail("invalid_actor_id")
    current = _now(now)
    if not await _active_user(session, actor_user_id):
        raise _fail("actor_inactive")
    if not await _active_member(session, tenant_id, actor_user_id):
        raise _fail("actor_membership_inactive")
    resolved_tenant, holder_id = await _workspace_holder(session, tenant_id, lock_holder=lock_holder)
    plan, policy = await _holder_policy(session, holder_id, now=current, lock_holder=lock_holder)
    return _context(
        actor_user_id=actor_user_id, tenant_id=resolved_tenant, holder_id=holder_id, plan=plan, policy=policy
    )
