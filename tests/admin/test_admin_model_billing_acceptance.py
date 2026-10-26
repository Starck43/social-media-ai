"""Owner-local #205 acceptance: real ASGI/auth/ORM/billing, synthetic HTTP only.

Never run remotely, with --noconftest, or against a newly repaired schema.
Normal conftest validates the existing target read-only before fixture DML.
The target guard accepts `test_schema` and the user-authorized isolated schema
`test_p9_t07_20261026` only, always under the root conftest and the local
`social_manager` database.
Synthetic financial evidence and its dependencies are deliberately retained.
See docs/audit/admin-llm-billing-acceptance.md for permissions and gates.
"""

from __future__ import annotations

import json
import socket
import sys
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest

# isort: split
# Guard BEFORE application imports/engine construction, including collection.
_expected_conftest = Path(__file__).resolve().parents[1] / "conftest.py"
_conftest = next(
    (module for module in tuple(sys.modules.values()) if getattr(module, "__file__", None) == str(_expected_conftest)),
    None,
)
# The default suite schema and the one user-authorized isolated schema P9-T07
# owns. Nothing else is allowed: a typo here must stop before any fixture DML.
_APPROVED_SCHEMAS = ("test_schema", "test_p9_t07_20261026")
if _conftest is None or getattr(_conftest, "TEST_SCHEMA", None) not in _APPROVED_SCHEMAS:
    raise RuntimeError(
        "STOP: current root conftest and an approved test schema redirect required; never use --noconftest"
    )
_target = urlsplit(getattr(_conftest, "TEST_DATABASE_URL", ""))
if (
    _target.hostname not in ("localhost", "127.0.0.1")
    or (_target.port or 5432) != 5432
    or _target.path != "/social_manager"
):
    raise RuntimeError("STOP: only the owner-approved local social_manager/test_schema target is allowed")
# isort: split

from itsdangerous import TimestampSigner, URLSafeTimedSerializer
from sqlalchemy import event, select

from app.core.config import settings
from app.core.database import async_engine, async_session_maker
from app.core.permissions import operator_permission_scope, permission_scope
from app.core.tenant_context import tenant_scope
from app.models import LLMModel, LLMProvider, PlatformRole, Tenant, User, WorkspaceRole
from app.models.billing.dispatch import PersonalLLMDispatch
from app.models.billing.ledger import (
    PersonalBillingCutover,
    PersonalLLMBoundPolicy,
    PersonalLLMLedgerEntry,
    PersonalLLMReservation,
    PersonalLLMReservationState,
)
from app.models.managers.personal_billing_manager import PersonalBillingManager
from app.models.managers.tenant_manager import tenant_users
from app.services.ai.model_probe import PROBE_PROMPT
from app.types import ActionType
from app.utils.crypto import encrypt_secret

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]
BASE = "/admin/llm-model"
POST = "/admin/llm-model/test"
WEB_POST = "/app/settings/agent/model-probe"
SYNTHETIC_ANSWER = "да <script>never_execute()</script>"


class Fields(HTMLParser):
    """Read browser-owned form fields rather than fabricating login sessions."""

    def __init__(self, html):
        super().__init__()
        self.hidden = {}
        self.names = set()
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"input", "select", "textarea", "button"} and attrs.get("name"):
            self.names.add(attrs["name"])
            if tag == "input" and attrs.get("type") == "hidden":
                self.hidden[attrs["name"]] = attrs.get("value", "")


def page_ok(response):
    assert response.status_code == 200, f"Unexpected ASGI status {response.status_code}: {response.url.path}"
    return response.text


def token_from(response, name):
    fields = Fields(page_ok(response))
    assert fields.hidden.get(name), f"Missing actual form field {name} at {response.url.path}"
    return fields.hidden[name]


@contextmanager
def fixture_scope():
    """Trusted setup/readback only; NEVER surround an ASGI request with bypass."""
    with tenant_scope(bypass=True), operator_permission_scope():
        yield


async def role_id(model, code):
    row = await model.objects.filter(codename=code).first()
    assert row is not None, f"BLOCKED: existing {model.__name__}:{code} absent; do not seed"
    return row.id


@dataclass
class Case:
    app: object
    marker: str = field(default_factory=lambda: f"p205-{uuid4().hex[:20]}")
    password: str = field(default_factory=lambda: f"Synthetic-Q7!{uuid4().hex}")
    users: list = field(default_factory=list)
    metadata_sent: list = field(default_factory=list)
    expected_prompt: str = PROBE_PROMPT
    expected_max_tokens: int = 8
    personal_mode: bool = True
    tenant: object = None
    provider: object = None
    model: object = None
    admin: object = None
    sent: list = field(default_factory=list)
    payload: dict = field(
        default_factory=lambda: {
            "choices": [{"message": {"content": SYNTHETIC_ANSWER}}],
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 2,
                "total_tokens": 6,
                # OpenAI prompt_tokens includes cached tokens; explicit zeros are complete evidence.
                "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            },
        }
    )
    transport_error: str | None = None
    account_id: int | None = None
    policy_id: int | None = None
    cutover_id: int | None = None

    async def user(self, platform="user", membership=None):
        name = f"{self.marker}-{len(self.users)}"
        with fixture_scope():
            user = await User.objects.create_user(
                username=name,
                email=f"{name}@example.invalid",
                password=self.password,
                role_id=await role_id(PlatformRole, platform),
                is_active=True,
            )
            self.users.append(user.id)
            if membership:
                await tenant_users.create(
                    tenant_id=self.tenant.id,
                    user_id=user.id,
                    channel="web",
                    external_user_id=str(user.id),
                    role_id=await role_id(WorkspaceRole, membership),
                    ai_chat_enabled=False,
                    is_active=True,
                )
        return user

    async def billing(self, *, cap="10", policy=True, cutover=True):
        """Only this case's account/envelope, never reference grants or real payers."""
        with permission_scope(self.admin):
            account = await PersonalBillingManager().configure_self(Decimal(cap))
        self.account_id = account.id
        with fixture_scope():
            if cutover:
                boundary = await PersonalBillingCutover.objects.create(
                    account_id=account.id,
                    effective_at=datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0),
                    reviewed_by_user_id=self.admin.id,
                    review_version="synthetic-acceptance-v1",
                )
                self.cutover_id = boundary.id
            if policy:
                # The reviewed envelope must cover the request mode under test:
                # blank probes ask for 8 output tokens, custom questions for 512.
                output_tokens = self.expected_max_tokens
                envelope = await PersonalLLMBoundPolicy.objects.create(
                    provider_row_id=self.provider.id,
                    model_row_id=self.model.id,
                    provider_name=self.provider.name,
                    model_id=self.model.model_id,
                    version=self.marker,
                    max_input_tokens=self.model.max_tokens - output_tokens,
                    max_output_tokens=output_tokens,
                    request_mode="text_only",
                    reviewed_by_user_id=self.admin.id,
                )
                self.policy_id = envelope.id

    async def dispatches(self):
        async with async_session_maker() as session:
            return list(
                (
                    await session.execute(
                        select(PersonalLLMDispatch).where(
                            PersonalLLMDispatch.llm_model_row_id == self.model.id,
                            PersonalLLMDispatch.llm_provider_row_id == self.provider.id,
                        )
                    )
                ).scalars()
            )

    async def accounting(self, dispatch_id):
        async with async_session_maker() as session:
            reservation = (
                await session.execute(
                    select(PersonalLLMReservation).where(
                        PersonalLLMReservation.dispatch_id == dispatch_id,
                    )
                )
            ).scalar_one()
            state = (
                await session.execute(
                    select(PersonalLLMReservationState).where(
                        PersonalLLMReservationState.dispatch_id == dispatch_id,
                    )
                )
            ).scalar_one()
            ledger = list(
                (
                    await session.execute(
                        select(PersonalLLMLedgerEntry).where(
                            PersonalLLMLedgerEntry.dispatch_id == dispatch_id,
                        )
                    )
                ).scalars()
            )
            return reservation, state, ledger

    async def transport(self, request):
        # ANY unexpected HTTP is refused; no passthrough or provider credentials.
        assert request.url.host == "provider.invalid", "Forbidden external HTTP target"
        if request.method == "GET" and request.url.path == "/v1/models":
            self.metadata_sent.append(request)
            assert request.headers["authorization"] == "Bearer synthetic-p205-key"
            assert set(request.extensions["timeout"].values()) == {20.0}
            assert not request.content and not await self.dispatches()
            return httpx.Response(200, json={"data": [{"id": self.model.model_id}]})
        self.sent.append(request)
        assert request.method == "POST" and request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == self.model.model_id
        assert body["messages"] == [{"role": "user", "content": self.expected_prompt}]
        assert body["max_tokens"] == self.expected_max_tokens and body["temperature"] == 0
        assert "timeout" not in body and "personal_billing_purpose" not in body
        assert set(request.extensions["timeout"].values()) == {20.0}
        assert request.headers["authorization"] == "Bearer synthetic-p205-key"
        dispatches = await self.dispatches()
        if not self.personal_mode:
            assert not dispatches, "Single-owner mode must not create personal reservations"
            return httpx.Response(200, json=self.payload)
        assert len(dispatches) == 1, "Durable admission must precede HTTP"
        dispatch = dispatches[0]
        assert dispatch.payer_user_id == dispatch.initiated_by_user_id == self.admin.id
        assert dispatch.tenant_id == self.tenant.id and dispatch.purpose == "admin_probe"
        reservation, state, ledger = await self.accounting(dispatch.dispatch_id)
        assert reservation.ceiling_state == "bounded" and reservation.cap_usd is not None
        assert state.status == "reserved" and [row.entry_kind for row in ledger] == ["reserve"]
        if self.transport_error == "timeout":
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        if self.transport_error == "status":
            return httpx.Response(503, json={"error": "synthetic failure"})
        return httpx.Response(200, json=self.payload)

    @asynccontextmanager
    async def client(self, user=None, surface="admin"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False),
            base_url="https://testserver",
            follow_redirects=False,
        ) as client:
            if user:
                path, token_name = ("/admin/login", "csrf_token") if surface == "admin" else ("/app/login", "_csrf")
                token = token_from(await client.get(path), token_name)
                response = await client.post(
                    path,
                    data={
                        "username": user.username,
                        "password": self.password,
                        token_name: token,
                    },
                )
                assert response.status_code in {302, 303}, f"Real {surface} login failed: {response.status_code}"
                assert "login" not in response.headers.get("location", ""), "Login rejected"
            yield client

    async def csrf(self, client):
        return token_from(await client.get(f"{BASE}/action/test-model", params={"pks": self.model.id}), "csrf_token")

    async def post(self, client, mode="short", **changes):
        data = {
            "model_id": str(self.model.id),
            "test_type": mode,
            "prompt": "",
            "csrf_token": await self.csrf(client),
        }
        if mode == "real":
            data.update(tenant_id=str(self.tenant.id))
        data.update(changes)
        return await client.post(POST, data=data)

    async def no_dispatch(self, *, metadata_requests=0):
        assert len(self.metadata_sent) == metadata_requests
        assert not self.sent and not await self.dispatches()


@pytest.fixture
async def case(monkeypatch, record_property):
    # Only test-process feature selection. No .env edits, engine replacement or lifespan.
    monkeypatch.setattr(settings, "ADMIN_ENABLED", True)
    monkeypatch.setattr(settings, "PERSONAL_BILLING_ENABLED", True)
    from app.main import create_application

    assert settings.DB_SCHEMA == _conftest.TEST_SCHEMA, "STOP: application schema was not redirected"
    owned = Case(create_application())
    forbidden = []
    setup_complete = False
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def network_guard(original):
        def connect(sock, address):
            if sock.family in {socket.AF_INET, socket.AF_INET6}:
                if address[0] not in {"localhost", "127.0.0.1", "::1"} or address[1] != 5432:
                    forbidden.append("non-PostgreSQL network connection")
                    raise AssertionError("Only approved loopback PostgreSQL TCP is permitted")
            return original(sock, address)

        return connect

    monkeypatch.setattr(socket.socket, "connect", network_guard(original_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", network_guard(original_connect_ex))
    real_async = httpx.AsyncClient
    real_sync = httpx.Client

    class GuardedAsyncClient(real_async):
        def __init__(self, *args, **kwargs):
            if not isinstance(kwargs.get("transport"), httpx.ASGITransport):
                kwargs["transport"] = httpx.MockTransport(owned.transport)
                kwargs.pop("mounts", None)
                kwargs["trust_env"] = False
            super().__init__(*args, **kwargs)

    def forbid_sync(request):
        forbidden.append("synchronous HTTP")
        raise AssertionError("Synchronous external HTTP is forbidden in acceptance")

    class GuardedClient(real_sync):
        def __init__(self, *args, **kwargs):
            kwargs.update(transport=httpx.MockTransport(forbid_sync), trust_env=False)
            kwargs.pop("mounts", None)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", GuardedAsyncClient)
    monkeypatch.setattr(httpx, "Client", GuardedClient)
    try:
        with fixture_scope():
            owned.tenant = await Tenant.objects.create(
                name=owned.marker,
                slug=owned.marker,
                plan="business",
                is_active=True,
            )
        owned.admin = await owned.user("admin", membership="owner")
        with fixture_scope():
            owned.provider = await LLMProvider.objects.create(
                owner_user_id=owned.admin.id,
                uid=owned.marker,
                name=owned.marker,
                base_url="https://provider.invalid/v1",
                api_format="openai",
                is_active=True,
                encrypted_api_key=encrypt_secret("synthetic-p205-key"),
            )
            owned.model = await LLMModel.objects.create(
                provider_id=owned.provider.id,
                owner_user_id=owned.admin.id,
                name=owned.marker,
                model_id=owned.marker,
                model_type="text,image",
                max_tokens=1024,
                default_temperature=0.3,
                input_cost_per_1k=0.001,
                output_cost_per_1k=0.002,
                # Cache-aware pricing needs an explicit ordinary unit and every
                # cache rate before a capped payer can get a bounded reservation.
                cache_read_cost_per_1m=Decimal("0.0003"),
                cache_write_5m_cost_per_1m=Decimal("0.0006"),
                cache_write_1h_cost_per_1m=Decimal("0.0012"),
                pricing_token_unit=1000,
                is_active=True,
                is_default=False,
            )
        setup_complete = True
        yield owned
    finally:
        # Never delete append-only billing evidence, nor its referenced users/workspaces.
        # Retain every synthetic dependency consistently, including partial fixture setup.
        manifest = {
            "marker": owned.marker,
            "users": owned.users,
            "catalog_owner_user_id": getattr(owned.admin, "id", None),
            "tenant_id": getattr(owned.tenant, "id", None),
            "provider_id": getattr(owned.provider, "id", None),
            "model_id": getattr(owned.model, "id", None),
            "account_id": owned.account_id,
            "policy_id": owned.policy_id,
            "cutover_id": owned.cutover_id,
            "setup_complete": setup_complete,
            "retained": setup_complete
            or any(value is not None for value in (owned.account_id, owned.policy_id, owned.cutover_id)),
        }
        record_property("owned_fixture_manifest", json.dumps(manifest, sort_keys=True))
        print("OWNED_FIXTURE_MANIFEST " + json.dumps(manifest, sort_keys=True))
        if not setup_complete and not any(
            value is not None for value in (owned.account_id, owned.policy_id, owned.cutover_id)
        ):
            with fixture_scope():
                if owned.model is not None:
                    await LLMModel.objects.delete_by_id(owned.model.id)
                if owned.provider is not None:
                    await LLMProvider.objects.delete_by_id(owned.provider.id)
                if owned.tenant is not None:
                    await Tenant.objects.delete_by_id(owned.tenant.id)
                for user_id in reversed(owned.users):
                    await User.objects.delete_user(user_id)
        assert not forbidden, "Forbidden network attempted: " + ", ".join(forbidden)


async def test_admin_list_details_and_forms_are_real(case):
    """The actor's own catalogue is readable *and* writable from the console.

    Providers and models are personal (`owner_user_id`), so `admin`/`operator`
    edit the models they own — the `can_create/can_edit = False` crutch from
    0109 is gone (`docs/ADMIN.md`). `case.model` belongs to `case.admin`.
    """
    async with case.client(case.admin) as client:
        listing = page_ok(await client.get(f"{BASE}/list", params={"search": case.marker}))
        assert case.marker in listing and "0.001" in listing and "0.002" in listing
        detail = page_ok(await client.get(f"{BASE}/details/{case.model.id}"))
        assert case.marker in detail and "text,image" in detail and "1024" in detail
        for path in (f"{BASE}/create", f"{BASE}/edit/{case.model.id}"):
            response = await client.get(path)
            assert response.status_code == 200, f"The owner of the catalogue edits it: {path}"
    await case.no_dispatch()


async def test_a_foreign_personal_model_is_refused_even_to_a_platform_admin(case):
    """Someone else's model is not the platform admin's to edit — 403, direct URL."""
    other_admin = await case.user("admin")
    async with case.client(other_admin) as client:
        for path in (f"{BASE}/details/{case.model.id}", f"{BASE}/edit/{case.model.id}"):
            response = await client.get(path)
            assert response.status_code in {403, 404}, f"Foreign personal catalogue leaked: {path}"
    await case.no_dispatch()


async def test_admin_global_model_crud_is_disabled_and_personal_catalog_is_available(case):
    async with case.client(case.admin) as client:
        for path in (f"{BASE}/create", f"{BASE}/edit/{case.model.id}"):
            response = await client.get(path)
            assert response.status_code == 200, f"The actor's own catalogue is editable: {path}"
    async with case.client(case.admin, "web") as client:
        settings_page = page_ok(await client.get("/app/settings?tab=models"))
        assert case.marker in settings_page
        assert "Добавить провайдера и модели" in settings_page
    await case.no_dispatch()


async def test_admin_short_metadata_and_web_short_stays_offline(case):
    owner = case.admin
    async with case.client(case.admin) as client:
        html = page_ok(await case.post(client))
        assert "API доступен" in html and "GET /models" in html and "Нет платного вызова" in html
    async with case.client(owner, "web") as client:
        csrf = token_from(await client.get("/app/settings?tab=agent"), "_csrf")
        result = await client.post(WEB_POST, data={"agent_model_id": str(case.model.id), "_csrf": csrf})
        assert result.status_code == 302 and "tab=agent" in result.headers["location"]
        html = page_ok(await client.get(result.headers["location"]))
        assert "Статус и локальный mock: OK" in html
    await case.no_dispatch(metadata_requests=1)


@pytest.mark.parametrize("prompt", ["", "<script>never_execute()</script>"])
async def test_mock_empty_and_escaped_question(case, prompt):
    async with case.client(case.admin) as client:
        html = page_ok(await case.post(client, "mock", prompt=prompt))
        assert ("Введите вопрос" if not prompt else "локальный ответ") in html
        assert "<script>never_execute()</script>" not in html
        if prompt:
            assert "&lt;script&gt;" in html
    await case.no_dispatch()


@pytest.mark.parametrize("token_kind", ["missing", "tampered", "expired"])
async def test_admin_csrf_actual_verifier(case, token_kind):
    async with case.client(case.admin) as client:
        token = await case.csrf(client)
        if token_kind == "missing":
            token = ""
        elif token_kind == "tampered":
            token += "invalid"
        else:
            manager = case.app.state.csrf_manager

            class OldSigner(TimestampSigner):
                def get_timestamp(self):
                    return super().get_timestamp() - manager.expire_seconds - 60

            old = URLSafeTimedSerializer(settings.SECRET_KEY, salt="csrf-token", signer=OldSigner)
            token = old.dumps({"csrf": True})
            assert not manager.verify_token(token)
        response = await case.post(client, csrf_token=token)
        assert response.status_code == 403
    await case.no_dispatch()


@pytest.mark.parametrize("identity", ["anonymous", "workspace_owner", "workspace_viewer"])
async def test_non_console_identities_cannot_execute(case, identity):
    actor = None if identity == "anonymous" else await case.user(membership=identity.removeprefix("workspace_"))
    async with case.client(actor, "web") as client:
        response = await client.post(
            POST,
            data={
                "model_id": str(case.model.id),
                "test_type": "short",
                "csrf_token": case.app.state.csrf_manager.generate_token(),
            },
        )
        assert response.status_code in {401, 403}, "No redirect/200 may hide a successful execution"
    await case.no_dispatch()


async def test_existing_operator_profile_matches_console_enforcement(case):
    """Real stored profile; controlled VIEW-only policy has separate no-DB tests."""
    operator = await case.user("operator")
    with fixture_scope():
        loaded = await User.objects.prefetch_related("role.permissions").get(id=operator.id)
    grants = loaded.model_permissions("llmmodel")
    can_read = bool(grants)  # SQLAdmin implies list/details from other registered actions.
    can_configure = ActionType.CONFIGURE in grants
    print(f"OBSERVED_OPERATOR_MODEL_GRANTS {[action.db_value for action in sorted(grants, key=lambda a: a.db_value)]}")
    async with case.client(operator) as client:
        for path in (f"{BASE}/list", f"{BASE}/details/{case.model.id}"):
            response = await client.get(path)
            assert response.status_code == (200 if can_read else 403)
        action = await client.get(f"{BASE}/action/test-model", params={"pks": case.model.id})
        assert action.status_code == (200 if can_configure else 403)
        response = await client.post(
            POST,
            data={
                "model_id": str(case.model.id),
                "test_type": "short",
                "csrf_token": case.app.state.csrf_manager.generate_token(),
            },
        )
        assert response.status_code == (200 if can_configure else 403)
        if can_configure:
            assert "API доступен" in response.text
    await case.no_dispatch(metadata_requests=int(can_configure))


@pytest.mark.parametrize("change", ["disabled", "revoked"])
async def test_fresh_authority_after_page_load(case, change):
    async with case.client(case.admin) as client:
        token = await case.csrf(client)
        with fixture_scope():
            update = {"is_active": False} if change == "disabled" else {"role_id": await role_id(PlatformRole, "user")}
            await User.objects.update_by_id(case.admin.id, **update)
        response = await client.post(
            POST,
            data={
                "model_id": str(case.model.id),
                "test_type": "short",
                "csrf_token": token,
            },
        )
        assert response.status_code in {401, 403}
    await case.no_dispatch()


@pytest.mark.parametrize(
    "model_key,status", [("missing", 404), ("multiple", 422), ("0", 422), ("bad", 422), ("9223372036854775807", 422)]
)
async def test_unknown_or_multiple_selection_is_refused(case, model_key, status):
    key = (
        str(2**31 - 1)
        if model_key == "missing"
        else f"{case.model.id},{case.model.id}" if model_key == "multiple" else model_key
    )
    async with case.client(case.admin) as client:
        response = await case.post(client, model_id=key)
        assert response.status_code == status
    await case.no_dispatch()


@pytest.mark.parametrize("variant,status", [("no_workspace", 422), ("inactive_workspace", 422)])
async def test_paid_preconditions_before_dispatch(case, monkeypatch, variant, status):
    changes = {}
    if variant == "no_workspace":
        changes["tenant_id"] = ""
    elif variant == "inactive_workspace":
        with fixture_scope():
            await Tenant.objects.update_by_id(case.tenant.id, is_active=False)
    async with case.client(case.admin) as client:
        response = await case.post(client, "real", **changes)
        assert response.status_code == status
    await case.no_dispatch()


@pytest.mark.parametrize("missing", ["account", "cutover", "bound", "cap"])
async def test_real_admission_cannot_bypass_personal_safeguards(case, missing):
    if missing != "account":
        await case.billing(
            cap="0.000000001" if missing == "cap" else "10", policy=missing != "bound", cutover=missing != "cutover"
        )
    async with case.client(case.admin) as client:
        html = page_ok(await case.post(client, "real"))
        assert "Проверка не пройдена" in html
    await case.no_dispatch()


@pytest.mark.parametrize("prompt,limit", [("", 8), ("Explain API briefly", 512)])
async def test_real_request_initiator_payer_durable_settlement(case, prompt, limit):
    case.expected_prompt, case.expected_max_tokens = prompt or PROBE_PROMPT, limit
    tenant_owner = await case.user(membership="owner")
    await case.billing()
    async with case.client(case.admin) as client:
        html = page_ok(await case.post(client, "real", prompt=prompt, payer_user_id=str(tenant_owner.id)))
        assert "Проверка завершена" in html and "0.000008" in html
        assert SYNTHETIC_ANSWER not in html and "&lt;script&gt;" in html
        assert case.expected_prompt in html
        assert 'name="confirm_real"' not in html
        assert "JSON ответа провайдера" in html and "HTTP-статус API" in html
        assert "response_json" not in html  # internal wrapper is not a provider field
    assert len(case.sent) == 1
    (dispatch,) = await case.dispatches()
    assert dispatch.payer_user_id == case.admin.id != tenant_owner.id
    reservation, state, ledger = await case.accounting(dispatch.dispatch_id)
    assert reservation.bound_version == case.marker and state.status == "settled"
    assert state.settled_usd == Decimal("0.000008") and state.reserved_usd == 0
    assert {row.entry_kind for row in ledger} >= {"reserve", "settlement"}
    with fixture_scope():
        updated = await LLMModel.objects.get(id=case.model.id)
    assert updated.last_request_cost == pytest.approx(0.000008) and updated.use_count == 1


async def test_unknown_usage_keeps_reserved_liability_not_free(case):
    await case.billing()
    case.payload.pop("usage")
    async with case.client(case.admin) as client:
        html = page_ok(await case.post(client, "real"))
        assert "Проверка завершена" in html and html.count("Неизвестно") >= 2
        assert "Нет платного вызова" not in html
    assert len(case.sent) == 1
    (dispatch,) = await case.dispatches()
    _, state, ledger = await case.accounting(dispatch.dispatch_id)
    assert state.status == "reserved" and state.reserved_usd > 0
    assert not any(row.entry_kind in {"settlement", "release"} for row in ledger)


async def test_missing_cache_counters_keep_estimate_unknown(case):
    """Unreported cache counters keep the tariff estimate unknown, not free."""
    await case.billing()
    # Paid admission still has a reviewed bound; only the provider cache evidence is absent.
    case.payload["usage"] = {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}
    async with case.client(case.admin) as client:
        html = page_ok(await case.post(client, "real"))
    assert "Проверка завершена" in html
    assert "Оценка по тарифу, USD</dt><dd>Неизвестно</dd>" in html
    assert "Не сообщена" in html
    assert len(case.sent) == 1
    (dispatch,) = await case.dispatches()
    reservation, state, ledger = await case.accounting(dispatch.dispatch_id)
    assert reservation.ceiling_state == "bounded" and reservation.cap_usd is not None
    assert state.status == "reserved"
    assert not any(row.entry_kind in {"settlement", "release"} for row in ledger)
    with fixture_scope():
        updated = await LLMModel.objects.get(id=case.model.id)
    assert updated.last_request_cost is None
    assert updated.last_request_cost_at is None


async def test_capped_admission_refuses_missing_cache_rate(case):
    """A capped payer cannot claim a bound while a required cache rate is absent."""
    await case.billing()
    # Cached tokens must be positive so the absent read rate is actually required.
    case.payload["usage"] = {
        "prompt_tokens": 4,
        "completion_tokens": 2,
        "total_tokens": 6,
        "prompt_tokens_details": {"cached_tokens": 2, "cache_write_tokens": 0},
    }
    with fixture_scope():
        await LLMModel.objects.update_by_id(case.model.id, cache_read_cost_per_1m=None)
    async with case.client(case.admin) as client:
        html = page_ok(await case.post(client, "real"))
    assert "Проверка не пройдена" in html
    assert "model_bound_required" in html
    await case.no_dispatch()
    with fixture_scope():
        updated = await LLMModel.objects.get(id=case.model.id)
    assert updated.last_request_cost is None
    assert updated.last_request_cost_at is None


@pytest.mark.parametrize("failure", ["timeout", "status"])
async def test_transport_failure_never_replays(case, failure):
    await case.billing()
    case.transport_error = failure
    async with case.client(case.admin) as client:
        html = page_ok(await case.post(client, "real"))
        assert "Проверка не пройдена" in html
    assert len(case.sent) == 1 and len(await case.dispatches()) == 1


async def test_estimate_save_failure_preserves_answer_without_second_http(case):
    await case.billing()
    failed = []

    def inject(conn, cursor, statement, parameters, context, executemany):
        # No manager/auth mocks: fail the actual owned-model estimate UPDATE only.
        sql = statement.lower()
        if "update" in sql and "llm_models" in sql and "last_request_cost=" in sql.replace(" ", ""):
            assert not failed, "Unexpected estimate retry"
            failed.append(True)
            raise RuntimeError("synthetic estimate persistence failure")

    event.listen(async_engine.sync_engine, "before_cursor_execute", inject)
    try:
        async with case.client(case.admin) as client:
            html = page_ok(await case.post(client, "real"))
            assert "Проверка завершена" in html and "оценка стоимости не сохранена" in html
            assert "&lt;script&gt;" in html
    finally:
        event.remove(async_engine.sync_engine, "before_cursor_execute", inject)
    assert failed == [True] and len(case.sent) == 1
    (dispatch,) = await case.dispatches()
    _, state, _ = await case.accounting(dispatch.dispatch_id)
    assert state.status == "settled" and state.settled_usd == Decimal("0.000008")


async def test_legacy_paid_route_is_inert(case):
    async with case.client(case.admin) as client:
        response = await client.post(
            "/admin/llm-model/test-real",
            data={
                "pks": str(case.model.id),
                "tenant_id": str(case.tenant.id),
                "prompt": "no dispatch",
                "csrf_token": await case.csrf(client),
            },
        )
        assert response.status_code == 410
    await case.no_dispatch()


@pytest.mark.parametrize("variant", ["invalid_csrf", "viewer", "foreign_workspace"])
async def test_web_settings_csrf_and_workspace_boundary(case, variant):
    actor = case.admin if variant in {"invalid_csrf", "foreign_workspace"} else await case.user(membership="viewer")
    async with case.client(actor, "web") as client:
        csrf = token_from(await client.get("/app/settings?tab=agent"), "_csrf")
        data = {"agent_model_id": str(case.model.id), "_csrf": csrf}
        if variant == "invalid_csrf":
            data["_csrf"] = "invalid"
        elif variant == "foreign_workspace":
            # Posted IDs must not switch active context. Own workspace remains writable.
            with fixture_scope():
                foreign = await Tenant.objects.create(
                    name=f"{case.marker}-foreign", slug=f"{case.marker}-foreign", plan="business", is_active=True
                )
            data["tenant_id"] = str(foreign.id)
            print(f"RETAINED_SYNTHETIC_FOREIGN_WORKSPACE {foreign.id} marker={case.marker}")
        response = await client.post(WEB_POST, data=data)
        assert response.status_code == 302
        html = page_ok(await client.get(response.headers["location"]))
        if variant == "foreign_workspace":
            assert "Статус и локальный mock: OK" in html
            assert case.tenant.name in html
            assert foreign.name not in html
        else:
            assert "Статус и локальный mock: OK" not in html
    await case.no_dispatch()


async def test_short_and_mock_do_not_require_billing_or_workspace_selection(case, monkeypatch):
    monkeypatch.setattr(settings, "PERSONAL_BILLING_ENABLED", False)
    async with case.client(case.admin) as client:
        short = page_ok(await case.post(client, "short", tenant_id="bad"))
        assert "API доступен" in short
        mock = page_ok(await case.post(client, "mock", tenant_id="bad", prompt="Synthetic question"))
        assert "локальный ответ" in mock
    await case.no_dispatch(metadata_requests=1)


async def test_other_platform_admin_cannot_probe_fixture_owners_model_key(case):
    """Another admin's model is outside the scope: the action is refused, not run.

    The probe uses *the model's* provider key, so a foreign model must never reach
    the service at all — `AdminScopeMiddleware` binds the actor's own catalogue,
    and the credential check refuses the row before any HTTP call.
    """
    other_admin = await case.user("admin")
    async with case.client(other_admin) as client:
        response = await client.post(
            POST,
            data={
                "model_id": str(case.model.id),
                "test_type": "short",
                "prompt": "",
                "csrf_token": case.app.state.csrf_manager.generate_token(),
            },
        )
        assert response.status_code in {403, 404}, "Foreign personal catalogue must be refused"
        assert "API доступен" not in response.text
    await case.no_dispatch()


@pytest.mark.parametrize("broken", ["inactive_model", "inactive_provider", "missing_key", "unsupported_format"])
async def test_invalid_model_configuration_cannot_dispatch(case, broken):
    await case.billing()
    with fixture_scope():
        if broken == "inactive_model":
            await LLMModel.objects.update_by_id(case.model.id, is_active=False)
        else:
            changes = {
                "inactive_provider": {"is_active": False},
                "missing_key": {"encrypted_api_key": None},
                "unsupported_format": {"api_format": "custom"},
            }[broken]
            await LLMProvider.objects.update_by_id(case.provider.id, **changes)
    async with case.client(case.admin) as client:
        html = page_ok(await case.post(client, "real"))
        assert "Проверка не пройдена" in html
    await case.no_dispatch()


async def test_single_owner_real_runs_without_personal_account_or_workspace(case, monkeypatch):
    monkeypatch.setattr(settings, "PERSONAL_BILLING_ENABLED", False)
    case.personal_mode = False
    async with case.client(case.admin) as client:
        html = page_ok(await case.post(client, "real", tenant_id="ignored", payer_user_id="999"))
        assert "Проверка завершена" in html and "JSON ответа провайдера" in html
        assert "PERSONAL_BILLING_ENABLED" not in html and 'name="tenant_id"' not in html
        assert "Обычный учёт модели" in html
    assert len(case.sent) == 1 and not await case.dispatches()
    with fixture_scope():
        updated = await LLMModel.objects.get(id=case.model.id)
    assert updated.use_count == 1 and updated.last_request_cost == pytest.approx(0.000008)


async def test_single_owner_explicit_zero_provider_cost_is_persisted(case, monkeypatch):
    """Explicit zero provider cost outranks the tariff estimate; local DB readback only."""
    monkeypatch.setattr(settings, "PERSONAL_BILLING_ENABLED", False)
    case.personal_mode = False
    case.payload["choices"] = [{"message": {"role": "assistant", "content": None}, "finish_reason": "length"}]
    case.payload["usage"] = {"prompt_tokens": 470, "completion_tokens": 8, "total_tokens": 478, "cost": 0}
    async with case.client(case.admin) as client:
        html = page_ok(await case.post(client, "real", tenant_id="ignored"))
        assert "Проверка не пройдена" in html and "empty_content" in html
        assert "Цена провайдера, USD" in html and 'llm-state-success">0</span>' in html
        assert "478" in html and "length" in html
    assert len(case.sent) == 1 and not await case.dispatches()
    with fixture_scope():
        updated = await LLMModel.objects.get(id=case.model.id)
    assert updated.use_count == 1
    # A recognized explicit provider zero is the saved estimate, not a tariff recomputation.
    assert updated.last_request_cost == 0
