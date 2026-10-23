"""Real ASGI acceptance for the operator financial exposure page.

`GET /app/billing/exposure` is exercised through the actual application:
session login, `TenantUIMiddleware`, workspace selection, seeded platform roles
and `guard_superuser` are the real ones, not mocks. Only the three billing
reads are replaced with bounded synthetic data, which lets the test prove both
halves of the contract:

* platform operators/admins read the *selected* workspace, and the global
  catalog read carries no tenant;
* an ordinary platform user and an anonymous visitor are refused before any
  billing read happens.

The rendered page must never carry payer identities, prompts or raw provider
payloads — the read helpers do not return them and the template must not
invent them.
"""

from __future__ import annotations

import secrets
import sys
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import pytest

# isort: split
# Guard BEFORE application imports/engine construction, including collection.
_expected_conftest = Path(__file__).resolve().parents[1] / "conftest.py"
_conftest = next(
    (module for module in tuple(sys.modules.values()) if getattr(module, "__file__", None) == str(_expected_conftest)),
    None,
)
if _conftest is None or getattr(_conftest, "TEST_SCHEMA", None) != "test_schema":
    raise RuntimeError("STOP: current root conftest and test_schema redirect required; never use --noconftest")
_target = urlsplit(getattr(_conftest, "TEST_DATABASE_URL", ""))
if (
    _target.hostname not in ("localhost", "127.0.0.1")
    or (_target.port or 5432) != 5432
    or _target.path != "/social_manager"
):
    raise RuntimeError("STOP: only the owner-approved local social_manager/test_schema target is allowed")
# isort: split

from app.core.config import settings
from app.core.permissions import operator_permission_scope
from app.core.tenant_context import tenant_scope
from app.models import PlatformRole, Tenant, User
from app.models.managers.tenant_manager import TenantUserManager
from app.types import PlatformRoleType, WorkspaceRoleType
from app.web import billing as billing_web
from tests.web.test_web_settings import PASSWORD, _client, _csrf, _login, _name

pytestmark = pytest.mark.tenancy

EXPOSURE = "/app/billing/exposure"
DENIED = "Недостаточно прав для этого действия"
FORBIDDEN_PAYER = "SYNTHETIC_PAYER_ID_5e1d7c0f"
FORBIDDEN_PROMPT = "SYNTHETIC_PROMPT_LEAK_9a3b6c2d"
FORBIDDEN_PAYLOAD = "SYNTHETIC_RAW_PROVIDER_PAYLOAD_4f8c2d71"
FORBIDDEN_MARKERS = (FORBIDDEN_PAYER, FORBIDDEN_PROMPT, FORBIDDEN_PAYLOAD)

SPEND = {
    "scope": "workspace_payer_bound_dispatches",
    "dispatch_count": 3,
    "settled_usd": "1.230000",
    "reserved_usd": "0.010000",
    "provider_reported_usd": "1.200000",
    "tariff_reconstructed_usd": "0.030000",
    "pending_count": 1,
    "unknown_count": 1,
    "complete": False,
    "payer_user_id": FORBIDDEN_PAYER,
    "prompt": FORBIDDEN_PROMPT,
}
UNPRICED = [
    {
        "provider": "synthetic-provider-marker",
        "model_id": "synthetic-model-marker",
        "attempts": 2,
        "last_observed_at": None,
        "raw_provider_payload": FORBIDDEN_PAYLOAD,
    }
]
ZERO_PRICE = {
    "total": 1,
    "models": [
        {
            "provider": "synthetic-provider-marker",
            "model_id": "synthetic-free-marker",
            "name": "Synthetic free",
            "is_default": True,
        }
    ],
}


@contextmanager
def fixture_scope():
    """Trusted fixture setup/teardown only; never wraps an ASGI request."""
    with tenant_scope(bypass=True), operator_permission_scope():
        yield


async def _workspace(name: str) -> Tenant:
    with fixture_scope():
        return await Tenant.objects.create(
            name=name,
            slug=f"{name}-{secrets.token_hex(5)}",
            plan="business",
            is_active=True,
        )


async def _platform_user(role: PlatformRoleType) -> User:
    role_row = await PlatformRole.objects.get(codename=role.value)
    assert role_row is not None, f"BLOCKED: seeded platform role {role.value} is absent"
    username = _name(f"signals-{role.value}")
    with fixture_scope():
        return await User.objects.create_user(
            username=username,
            email=f"{username}@example.invalid",
            password=PASSWORD,
            role_id=role_row.id,
            is_active=True,
        )


async def _join(user: User, tenant_id: int) -> None:
    with fixture_scope():
        await TenantUserManager().add_web_member(
            tenant_id=tenant_id,
            user_id=user.id,
            role=WorkspaceRoleType.OWNER.value,
        )


async def _cleanup(users: list[User], tenants: list[Tenant]) -> None:
    with fixture_scope():
        for user in users:
            await User.objects.delete_user(user.id)
        for tenant in tenants:
            await Tenant.objects.delete_by_id(tenant.id)


def _install_reads(monkeypatch) -> list[tuple[str, tuple, dict]]:
    """Replace the three billing reads while recording every call."""
    calls: list[tuple[str, tuple, dict]] = []
    values = {
        "workspace_spend_summary": ("spend", SPEND),
        "unpriced_attempt_models": ("unpriced", UNPRICED),
        "zero_price_active_models": ("zero_price", ZERO_PRICE),
    }
    for name, (label, result) in values.items():

        async def read(*args, _label=label, _result=result, **kwargs):
            calls.append((_label, args, kwargs))
            return _result

        monkeypatch.setattr(billing_web, name, read)
    return calls


@pytest.mark.parametrize("role", [PlatformRoleType.OPERATOR, PlatformRoleType.ADMIN])
async def test_platform_operators_read_the_selected_workspace_only(role, monkeypatch):
    """A permitted operator sees bounded evidence pinned to the active workspace."""
    calls = _install_reads(monkeypatch)
    users: list[User] = []
    tenants: list[Tenant] = []
    try:
        first = await _workspace(_name("signals-first"))
        tenants.append(first)
        second = await _workspace(_name("signals-second"))
        tenants.append(second)
        user = await _platform_user(role)
        users.append(user)
        await _join(user, first.id)
        await _join(user, second.id)

        async with await _client() as client:
            await _login(client, user.username)
            # Login activates the first membership; the operator switches the
            # active workspace through the real selection endpoint.
            token = await _csrf(client, "/app/")
            selected = await client.post(
                "/app/workspaces/select",
                data={"tenant_id": str(second.id), "_csrf": token},
            )
            assert selected.status_code == 200

            page = await client.get(EXPOSURE)
            assert page.status_code == 200, "a permitted platform operator must reach the page"
            html = page.text

        assert [label for label, _, _ in calls] == ["spend", "unpriced", "zero_price"]
        assert calls[0][1] == (second.id,), "spend must read the selected workspace"
        assert calls[1][1] == (second.id,), "unpriced evidence must read the selected workspace"
        assert calls[2][1] == () and calls[2][2] == {}, "the model catalog is global, not tenant-pinned"
        assert first.id not in {call[1][0] for call in calls if call[1]}

        assert "Расходы пространства за сутки (UTC)" in html
        assert "$1.230000" in html
        assert "Попыток: 3." in html
        assert "Итог неполный" in html
        assert "synthetic-provider-marker" in html
        assert "synthetic-model-marker" in html
        assert "synthetic-free-marker" in html
        assert "Показано 1 из 1." in html
        assert "по умолчанию" in html
        for marker in FORBIDDEN_MARKERS:
            assert marker not in html, f"the exposure page leaked {marker}"
    finally:
        await _cleanup(users, tenants)


@pytest.mark.parametrize("identity", ["platform_user", "anonymous"])
async def test_non_operator_identities_are_denied_before_any_billing_read(identity, monkeypatch):
    """The guard runs before the reads, so a refusal cannot cost a ledger query."""
    calls = _install_reads(monkeypatch)
    users: list[User] = []
    tenants: list[Tenant] = []
    user = None
    try:
        tenant = await _workspace(_name("signals-denied"))
        tenants.append(tenant)
        if identity == "platform_user":
            user = await _platform_user(PlatformRoleType.USER)
            users.append(user)
            await _join(user, tenant.id)
        async with await _client() as client:
            if user is not None:
                await _login(client, user.username)
            page = await client.get(EXPOSURE)

        assert calls == [], "authorization must decide before any billing read"
        if user is not None:
            assert page.status_code == 200
            assert str(page.url).endswith("/app/jobs")
            assert DENIED in page.text
        else:
            assert page.status_code == 200
            assert "/app/login" in str(page.url)
    finally:
        await _cleanup(users, tenants)
