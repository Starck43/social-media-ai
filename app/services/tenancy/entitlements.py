"""One pure product-entitlement decision for preview, admission and execution."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from app.core.config import settings

from .capabilities import CAPABILITIES
from .plan_catalog import CATALOG_VERSION, PLAN_CATALOG, PlanGrant, grants_capability


@dataclass(frozen=True)
class EntitlementDecision:
    allowed: bool
    plan: str
    catalog_version: int
    required: tuple[str, ...]
    denials: tuple[dict[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EntitlementDenied(ValueError):
    """Terminal policy denial; never a retryable provider failure."""

    def __init__(self, decision: EntitlementDecision):
        self.decision = decision
        reasons = ", ".join(f"{item['capability']}:{item['reason']}" for item in decision.denials)
        super().__init__(f"entitlement_denied ({decision.plan}, v{decision.catalog_version}): {reasons}")


def decide(
    workspace: Any,
    required: Iterable[str],
    *,
    used_today: Mapping[str, int] | None = None,
    reserved: Iterable[str] = (),
    now: datetime | None = None,
    policy: Any = None,
) -> EntitlementDecision:
    """No I/O or consumption. All timestamps and daily counters use UTC."""
    now = now or datetime.now(timezone.utc)
    if now.utcoffset() is None:
        raise ValueError("Entitlement decisions require an aware timestamp")
    plan = getattr(workspace, "plan", "")
    policy_error = None
    expires = getattr(workspace, "plan_expires_at", None)
    if settings.USER_SERVICE_PLANS_ENABLED and policy is None:
        from .service_plan_policy import ServicePlanPolicyError

        try:
            if workspace is None or not hasattr(workspace, "_holder_policy"):
                raise ServicePlanPolicyError("missing_plan_holder")
            policy = workspace._holder_policy()
            expires = workspace.service_plan.plan_expires_at
        except ServicePlanPolicyError as error:
            policy_error = error.reason
            plan = ""
    if policy is not None:
        plan = policy.plan_code
        grant = PlanGrant(policy.module_grants, policy.daily_quotas)
    else:
        grant = PLAN_CATALOG.get(plan)
    expired = expires is not None and (expires.utcoffset() is None or expires <= now)
    keys = tuple(sorted(set(required)))
    reserved = set(reserved)
    denials = []
    for key in keys:
        reason = None
        if key not in CAPABILITIES:
            reason = "unknown_capability"
        elif workspace is None or not workspace.is_active:
            reason = "workspace_inactive"
        elif policy_error:
            reason = policy_error
        elif grant is None:
            reason = "unknown_plan"
        elif expired:
            reason = "plan_expired"
        elif not any(grants_capability(pattern, key) for pattern in grant.capabilities):
            reason = "not_granted"
        elif key in grant.daily_quotas:
            quota = grant.daily_quotas[key]
            if type(quota) is not int or quota < 0:
                reason = "invalid_quota"
            # A reservation prevents recounting this job, not validation of the
            # catalog that authorizes it. Malformed policy always fails closed.
            elif key not in reserved and (used_today or {}).get(key, 0) >= quota:
                reason = "quota_exhausted"
        if reason:
            denials.append({"capability": key, "reason": reason})
    return EntitlementDecision(not denials, plan, CATALOG_VERSION, keys, tuple(denials))


def catalog_summary(workspace: Any) -> dict[str, Any]:
    """Static grants plus expiry; quota availability requires a live decision."""
    grant = PLAN_CATALOG.get(workspace.plan)
    expires = getattr(workspace, "plan_expires_at", None)
    plan = workspace.plan
    if settings.USER_SERVICE_PLANS_ENABLED:
        from .service_plan_policy import ServicePlanPolicyError

        try:
            policy = workspace._holder_policy()
            plan = policy.plan_code
            grant = PlanGrant(policy.module_grants, policy.daily_quotas)
            expires = workspace.service_plan.plan_expires_at
        except ServicePlanPolicyError:
            plan, grant, expires = "", None, None
    return {
        "plan": plan,
        "catalog_version": CATALOG_VERSION,
        "grants": list(grant.capabilities) if grant else [],
        "daily_quotas": dict(grant.daily_quotas) if grant else {},
        "plan_expires_at": expires.isoformat() if expires else None,
        "capabilities": sorted(CAPABILITIES),
    }
