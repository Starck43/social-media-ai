"""Code-owned provider budgets and privacy-preserving account identities.

Adapters must supply the same verified, canonical provider account identifier
for the same real account across every workspace/process. Aliases or mutable
connection IDs can fragment a provider quota and are not safe account keys.
"""

import hmac
from dataclasses import dataclass, field
from hashlib import sha256
from types import MappingProxyType

from app.core.pii import _index_key


@dataclass(frozen=True)
class Policy:
    rate: int
    burst: int
    window: int  # seconds


# Values are intentionally code-owned, never accepted from request data.
POLICIES = MappingProxyType(
    {
        "bitrix24": Policy(rate=2, burst=50, window=1),
        "onec": Policy(rate=10, burst=10, window=60),
        "google_calendar": Policy(rate=30, burst=5, window=60),
        "caldav": Policy(rate=30, burst=3, window=60),
    }
)


@dataclass(frozen=True)
class RateBudget:
    provider: str
    account_key: str = field(repr=False)  # HMAC digest, never the provider identifier
    rate: int
    burst: int
    window: int


def budget_for(provider: str, canonical_account: str) -> RateBudget:
    """Build a budget from a verified canonical account, without persisting it.

    PII_INDEX_KEY must be the same stable, separately managed key in all API
    and runtime processes. Rotating it requires an approved coordinated rebuild;
    silently changing it would give the same account a new full bucket.
    """
    policy = POLICIES.get(provider) if isinstance(provider, str) else None
    if policy is None:
        raise ValueError("Unknown provider")
    if (
        not isinstance(canonical_account, str)
        or not canonical_account
        or len(canonical_account.encode("utf-8")) > 1024
        or any(ord(char) < 32 or ord(char) == 127 for char in canonical_account)
    ):
        raise ValueError("Invalid provider account")
    message = b"provider-rate-budget:v1\0" + provider.encode("ascii") + b"\0" + canonical_account.encode("utf-8")
    digest = hmac.new(_index_key(), message, sha256).hexdigest()
    return RateBudget(provider, digest, policy.rate, policy.burst, policy.window)


def validate_budget(budget: RateBudget) -> None:
    if type(budget) is not RateBudget or budget.provider not in POLICIES:
        raise ValueError("Invalid provider budget")
    policy = POLICIES[budget.provider]
    if (budget.rate, budget.burst, budget.window) != (policy.rate, policy.burst, policy.window):
        raise ValueError("Budget must match the code-owned catalog")
    if (
        not isinstance(budget.account_key, str)
        or len(budget.account_key) != 64
        or any(ch not in "0123456789abcdef" for ch in budget.account_key)
    ):
        raise ValueError("Invalid provider account digest")
