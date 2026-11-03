"""Code-owned provider policies and tenant-bound account identities."""

import base64
import binascii
import hmac
from dataclasses import dataclass, field
from hashlib import sha256
from types import MappingProxyType

from app.core.config import settings
from app.core.tenant_context import current_tenant_id, is_bypass


@dataclass(frozen=True)
class Policy:
    rate: int
    burst: int
    window: int


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
    tenant_id: int
    connection_id: int
    provider: str
    account_key: str = field(repr=False)
    rate: int
    burst: int
    window: int


def _key() -> bytes:
    """Dedicated stable secret; fail closed when missing or reused."""
    value = settings.RATE_LIMIT_KEY
    raw = value.get_secret_value().strip() if value is not None else ""
    try:
        key = base64.b64decode(raw.encode("ascii"), altchars=b"-_", validate=True)
    except (binascii.Error, UnicodeError, ValueError):
        raise ValueError("Invalid RATE_LIMIT_KEY") from None
    if len(key) < 32:
        raise ValueError("RATE_LIMIT_KEY must contain at least 32 decoded bytes")
    candidates = [settings.CREDENTIALS_KEY, settings.PII_INDEX_KEY, *(settings.PII_ENCRYPTION_KEYS or "").split(",")]
    if raw == settings.SECRET_KEY:
        raise ValueError("RATE_LIMIT_KEY must be separate")
    for candidate in candidates:
        if not candidate:
            continue
        try:
            decoded = base64.b64decode(candidate.strip().encode("ascii"), altchars=b"-_", validate=True)
        except (binascii.Error, UnicodeError, ValueError):
            continue
        if hmac.compare_digest(key, decoded):
            raise ValueError("RATE_LIMIT_KEY must be separate")
    return key


def _tenant():
    tenant_id = current_tenant_id()
    if is_bypass() or type(tenant_id) is not int or tenant_id <= 0:
        raise ValueError("Active tenant scope required")
    return tenant_id


def budget_for(provider: str, canonical_account: str, *, connection_id: int) -> RateBudget:
    """Call only after authorizing the connection and verifying provider identity."""
    tenant_id = _tenant()
    policy = POLICIES.get(provider) if isinstance(provider, str) else None
    if policy is None:
        raise ValueError("Unknown provider")
    if type(connection_id) is not int or connection_id <= 0:
        raise ValueError("Invalid connection")
    try:
        account = canonical_account.encode("utf-8") if isinstance(canonical_account, str) else b""
    except UnicodeError:
        raise ValueError("Invalid provider account") from None
    if not account or len(account) > 1024 or any(byte < 32 or byte == 127 for byte in account):
        raise ValueError("Invalid provider account")
    message = b"provider-rate-budget:v1\0" + provider.encode("ascii") + b"\0" + account
    digest = hmac.new(_key(), message, sha256).hexdigest()
    return RateBudget(tenant_id, connection_id, provider, digest, policy.rate, policy.burst, policy.window)


def validate_budget(budget: RateBudget) -> None:
    if type(budget) is not RateBudget or budget.provider not in POLICIES:
        raise ValueError("Invalid provider budget")
    if (
        type(budget.tenant_id) is not int
        or budget.tenant_id != _tenant()
        or type(budget.connection_id) is not int
        or budget.connection_id <= 0
    ):
        raise ValueError("Budget scope mismatch")
    policy = POLICIES[budget.provider]
    if (budget.rate, budget.burst, budget.window) != (policy.rate, policy.burst, policy.window):
        raise ValueError("Budget must match the code-owned catalog")
    if (
        not isinstance(budget.account_key, str)
        or len(budget.account_key) != 64
        or any(ch not in "0123456789abcdef" for ch in budget.account_key)
    ):
        raise ValueError("Invalid provider account digest")
