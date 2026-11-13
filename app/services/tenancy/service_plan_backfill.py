"""Pure validation of an explicit, complete legacy mapping; no writes or defaults.

The caller must supply an authoritative DB snapshot and verified operator identity,
not client-supplied ownership. Applying it requires a separate locked transaction.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping

from .plan_catalog import PLAN_CATALOG


class BackfillMappingError(ValueError):
    pass


@dataclass(frozen=True)
class LegacyWorkspace:
    tenant_id: int
    plan: str
    plan_expires_at: datetime | None
    owner_user_ids: tuple[int, ...]
    plan_holder_user_id: int | None = None


@dataclass(frozen=True)
class HolderSelection:
    holder_user_id: int
    decision_ref: str


@dataclass(frozen=True)
class HolderPlanSelection:
    plan_code: str
    policy_version: int
    is_active: bool
    plan_expires_at: datetime | None
    decision_ref: str = ""


@dataclass(frozen=True)
class BackfillResolution:
    workspace: LegacyWorkspace
    holder: HolderSelection
    plan: HolderPlanSelection
    operator_user_id: int
    resolved_at: datetime


def _identity(value: int) -> bool:
    return type(value) is int and value > 0


def _aware(value: datetime | None) -> bool:
    return value is None or isinstance(value, datetime) and value.utcoffset() is not None


def _reference(value: str) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 250


def validate_mapping(
    workspaces: Iterable[LegacyWorkspace],
    holders: Mapping[int, HolderSelection],
    plans: Mapping[int, HolderPlanSelection],
    *,
    verified_user_ids: Iterable[int],
    policy_keys: Iterable[tuple[str, int]],
    operator_user_id: int,
    resolved_at: datetime,
) -> tuple[BackfillResolution, ...]:
    """Refuse partial, guessed, conflicting or sponsorship-changing mappings."""
    users, policies = set(verified_user_ids), set(policy_keys)
    rows = tuple(workspaces)
    ids = {row.tenant_id for row in rows}
    if not _identity(operator_user_id) or operator_user_id not in users:
        raise BackfillMappingError("A verified operator identity is required")
    if not isinstance(resolved_at, datetime) or not _aware(resolved_at):
        raise BackfillMappingError("Resolution time must be an aware timestamp")
    if any(not _identity(value) for value in users) or any(not _identity(value) for value in ids):
        raise BackfillMappingError("Invalid user or workspace identity")
    if len(ids) != len(rows) or any(not _identity(value) for value in holders) or set(holders) != ids:
        raise BackfillMappingError("Exactly one explicit selection per workspace is required")
    selected = {selection.holder_user_id for selection in holders.values()}
    if any(not _identity(value) for value in selected) or any(not _identity(value) for value in plans):
        raise BackfillMappingError("Invalid holder identity")
    if set(plans) != selected:
        raise BackfillMappingError("Exactly one explicit plan per selected holder is required")
    result = []
    for row in sorted(rows, key=lambda item: item.tenant_id):
        holder = holders[row.tenant_id]
        if not _identity(holder.holder_user_id) or holder.holder_user_id not in users:
            raise BackfillMappingError(f"Workspace {row.tenant_id}: unknown holder")
        if not _reference(holder.decision_ref):
            raise BackfillMappingError(f"Workspace {row.tenant_id}: holder decision reference required")
        if any(not _identity(value) or value not in users for value in row.owner_user_ids):
            raise BackfillMappingError(f"Workspace {row.tenant_id}: unverified legacy owner")
        if row.owner_user_ids and holder.holder_user_id not in row.owner_user_ids:
            raise BackfillMappingError(f"Workspace {row.tenant_id}: ownership transfer is a separate lifecycle")
        if row.plan_holder_user_id is not None and row.plan_holder_user_id != holder.holder_user_id:
            raise BackfillMappingError(f"Workspace {row.tenant_id}: sponsorship cannot be reassigned")
        if row.plan not in PLAN_CATALOG or not _aware(row.plan_expires_at):
            raise BackfillMappingError(f"Workspace {row.tenant_id}: invalid legacy plan or expiry")
        plan = plans[holder.holder_user_id]
        if (
            plan.plan_code not in PLAN_CATALOG
            or type(plan.policy_version) is not int
            or plan.policy_version <= 0
            or (plan.plan_code, plan.policy_version) not in policies
            or type(plan.is_active) is not bool
            or not _aware(plan.plan_expires_at)
        ):
            raise BackfillMappingError(f"Holder {holder.holder_user_id}: invalid or missing policy")
        if (
            plan.plan_code != row.plan or plan.plan_expires_at != row.plan_expires_at or not plan.is_active
        ) and not _reference(plan.decision_ref):
            raise BackfillMappingError(f"Holder {holder.holder_user_id}: legacy plan conflict needs a decision")
        result.append(BackfillResolution(row, holder, plan, operator_user_id, resolved_at))
    return tuple(result)
