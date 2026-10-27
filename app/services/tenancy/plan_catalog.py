"""Versioned product grants, independent of providers and existing resource limits."""

from dataclasses import dataclass, field

CATALOG_VERSION = 1


@dataclass(frozen=True)
class PlanGrant:
    capabilities: tuple[str, ...]
    daily_quotas: dict[str, int] = field(default_factory=dict)


# Deliberately no product packaging yet. Unknown capabilities still fail closed.
PLAN_CATALOG = {name: PlanGrant(("*",)) for name in ("starter", "pro", "business")}


def grants_capability(pattern: str, capability: str) -> bool:
    """Only exact keys, '*' and namespace suffix wildcards are supported."""
    return pattern == "*" or pattern == capability or (
        pattern.endswith(".*") and capability.startswith(pattern[:-1])
    )
