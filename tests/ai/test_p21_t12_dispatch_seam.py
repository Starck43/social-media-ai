"""P21-T12 dispatch seam: one ordered entry per provider attempt, no HTTP on denial.

Fakes only: no database, no network, no real provider. The root conftest needs a
live database, so this file must also pass standalone:

    PYTHONPATH=.:tests python -m pytest tests/ai/test_p21_t12_dispatch_seam.py \
        --noconftest -p no:cacheprovider -o addopts="" -q
"""

import asyncio
import json
import sys
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.config import settings
from app.services.ai import analyzer as analyzer_module
from app.services.ai import attempt_journal
from app.services.ai import dispatch_seam as seam
from app.services.ai import llm_client
from app.services.ai.analyzer import AIAnalyzer
from app.services.ai.llm_client import AnthropicClient, CustomClient, LLMClientFactory, OpenAICompatibleClient

OWNER = 7
TENANT = 3
PRIVATE_PROMPT = "PRIVATE-PROMPT-CUSTOMER-CONTENT"
PRIVATE_KEY = "sk-PRIVATE-CREDENTIAL-TOKEN"
PRIVATE_BODY = "PRIVATE-PROVIDER-BODY"
TEXT_ONLY = {"type": "object", "properties": {"analysis": {"type": "string"}}}

#: Doubles only: the root conftest must not need a database for this file.
__source_only__ = True


# ── Doubles ──────────────────────────────────────────────────


class HttpSpy:
    """Fake provider transport: records every request and never opens a socket."""

    def __init__(self):
        self.requests = []
        self.response = {
            "choices": [{"message": {"content": '{"analysis": "ok"}'}}],
            "content": [{"type": "text", "text": '{"analysis": "ok"}'}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "input_tokens": 3, "output_tokens": 2},
        }

    def factory(self, fail=False, **kwargs):
        async def post(url, *, headers=None, json=None, timeout=None, **rest):
            assert not fail, "provider HTTP must not happen on a denial"
            self.requests.append({"url": url, "headers": dict(headers or {}), "json": json})
            return httpx.Response(200, json=self.response, request=httpx.Request("POST", url))

        client = MagicMock()
        client.post = AsyncMock(side_effect=post)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        return client


def make_model(identity=4, *, owner=OWNER, api_format="openai", capabilities=("text",), provider_owner=None):
    provider = NS(
        id=identity,
        name=f"prov-{api_format}",
        api_format=api_format,
        owner_user_id=owner if provider_owner is None else provider_owner,
        base_url="https://provider.invalid",
        auth_header=None,
        is_active=True,
        get_api_key=lambda: PRIVATE_KEY,
    )
    return NS(
        id=identity,
        owner_user_id=owner,
        is_active=True,
        name=f"model-{identity}",
        model_id=f"model-{identity}",
        provider=provider,
        capabilities=list(capabilities),
        max_tokens=32,
        default_temperature=0.2,
        custom_endpoint_path="/v1/generate",
        input_cost_per_1k=0.1,
        output_cost_per_1k=0.1,
        use_count=0,
        fail_count=0,
        last_used_at=None,
        last_success_at=None,
        is_default=False,
    )


class ClientLike:
    def __init__(self, model):
        self.model = model


def arm_stub(monkeypatch):
    """Replace the seam call with a stub and hand back the real callable."""
    stub = AsyncMock()
    real = seam.dispatch_attempt
    monkeypatch.setattr(seam, "dispatch_attempt", stub)
    return stub, real


def spy_seam(monkeypatch):
    """Record every seam entry while still delegating to the real adapter.

    The clients resolve the seam lazily (``_seam_module()``) and call its
    ``dispatch_attempt`` attribute at call time, so patching the attribute is
    what the production path observes.
    """
    real = seam.dispatch_attempt
    calls = []

    async def record(client, **kwargs):
        calls.append({"client": client, "kwargs": kwargs})
        return await real(client, **kwargs)

    monkeypatch.setattr(seam, "dispatch_attempt", record)
    assert seam.dispatch_attempt is record
    return calls


class Grants:
    def __init__(self, *, denial=None, error=None):
        self.calls = 0
        self.denial = denial
        self.error = error

    async def resolve(self, request, context):
        self.calls += 1
        if self.denial is not None:
            raise self.denial
        if self.error is not None:
            raise self.error
        return seam.DispatchGrant(catalog_id="holder-catalog")


class Rate:
    def __init__(self, *, denial=None, error=None):
        self.calls = 0
        self.denial = denial
        self.error = error
        self.released = []

    async def acquire(self, request, context):
        self.calls += 1
        if self.denial is not None:
            raise self.denial
        if self.error is not None:
            raise self.error
        return seam.DispatchLease(lease_id="capacity")

    async def release(self, admission):
        self.released.append(admission)


class Reservation:
    def __init__(self, *, credential_version=None):
        self.calls = 0
        self.credential_version = credential_version

    async def check_allowance(self, request, context):
        return True

    async def reserve(self, request, context):
        self.calls += 1
        return seam.DispatchReservation(
            dispatch_id=request.dispatch_id,
            credential_version=(
                self.credential_version if self.credential_version is not None else context.credential_version
            ),
            receipt={"receipt_id": request.dispatch_id},
        )


class Authority:
    """Explicit trusted double; production authority is not wired by this task."""

    async def resolve_identity(self, request, hints):
        return hints

    async def resolve_credential(self, request, context):
        return context

    async def resolve_payer(self, request, context):
        return context

    async def revalidate(self, admission):
        return admission.context


class Settlement:
    def __init__(self, *, error=None):
        self.calls = []
        self.error = error

    async def settle(self, admission, *, outcome, evidence=None):
        self.calls.append(outcome)
        if self.error is not None:
            raise self.error


def full_foundation(grants=None, rate=None, admission=None, settlement=None, authority=None):
    return seam.DispatchFoundation(
        grants=grants if grants is not None else Grants(),
        rate=rate if rate is not None else Rate(),
        admission=admission if admission is not None else Reservation(),
        settlement=settlement if settlement is not None else Settlement(),
        authority=authority if authority is not None else Authority(),
    )


def strict_context(**changes):
    base = dict(
        actor_user_id=OWNER,
        tenant_id=TENANT,
        plan_holder_user_id=OWNER,
        catalog_id=None,
        credential_owner_user_id=OWNER,
        funding_class=seam.FUNDING_CLASS_PERSONAL,
        payer=f"tenant:{TENANT}",
        plan_policy_version=3,
        credential_version=3,
    )
    base.update(changes)
    return seam.DispatchContext(**base)


def request_for(model=None, **changes):
    base = dict(model=model if model is not None else make_model(), purpose="chat", stage="chat")
    base.update(changes)
    return seam.DispatchRequest(**base)


def without_model(**changes):
    """A request with no row at all: `request_for(model=None)` would default to a healthy row."""
    base = dict(model=None, purpose="chat", stage="chat")
    base.update(changes)
    return seam.DispatchRequest(**base)


def install_transport(monkeypatch, spy, *, fail=False):
    real = httpx.AsyncClient
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda **kwargs: spy.factory(fail=fail, **kwargs))
    return real


def client_for(model, monkeypatch):
    client = LLMClientFactory.create(model)
    client._rate_limit = AsyncMock()
    return client


def quiet_client_for(monkeypatch):
    """Stub the legacy per-client bookkeeping that would otherwise need storage."""
    monkeypatch.setattr(llm_client, "_personal_admission", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(llm_client, "_record_llm_usage", AsyncMock())
    monkeypatch.setattr(llm_client, "_verify_personal_dispatch_day", AsyncMock())
    monkeypatch.setattr(llm_client, "_revalidate_personal_dispatch_before_http", AsyncMock())


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def seam_flag(monkeypatch):
    monkeypatch.setattr(settings, "LLM_DISPATCH_SEAM_ENABLED", False)


@pytest.fixture
def scope(monkeypatch):
    """A bound catalog owner and workspace, as the real runtime provides."""
    monkeypatch.setattr("app.core.personal_catalog_context.current_catalog_owner_id", lambda: OWNER)
    monkeypatch.setattr("app.core.tenant_context.current_tenant_id", lambda: TENANT)


@pytest.fixture
def http(monkeypatch):
    return HttpSpy()


@pytest.fixture
def quiet_client(monkeypatch):
    quiet_client_for(monkeypatch)


# ── D1: pure seam ────────────────────────────────────────────


def test_funding_constants_and_all_seventeen_codes():
    assert (seam.FUNDING_CLASS_PLATFORM, seam.FUNDING_CLASS_PERSONAL, seam.FUNDING_CLASS_WORKSPACE) == (
        "platform",
        "personal",
        "workspace",
    )
    assert len(seam.DENIAL_CODES) == 17
    assert seam.resolve_funding_class(make_model(), catalog_owner_id=None) == seam.FUNDING_CLASS_PLATFORM


def test_resolve_funding_class_requires_row_and_provider_ownership():
    assert seam.resolve_funding_class(make_model(), catalog_owner_id=OWNER) == seam.FUNDING_CLASS_PERSONAL
    assert seam.resolve_funding_class(make_model(owner=9), catalog_owner_id=OWNER) == seam.FUNDING_CLASS_PLATFORM
    foreign_provider = make_model(owner=OWNER, provider_owner=9)
    assert seam.resolve_funding_class(foreign_provider, catalog_owner_id=OWNER) == seam.FUNDING_CLASS_PLATFORM


@pytest.mark.asyncio
async def test_admit_compatibility_mode_allocates_identity_without_foundation():
    given = await seam.admit(request_for(), foundation=None, context=strict_context())
    assert given.reserved is False and given.reservation is None
    assert seam.is_valid_dispatch_id(given.dispatch_id)
    allocated = await seam.admit(request_for(), foundation=None, context=strict_context())
    assert allocated.dispatch_id != given.dispatch_id
    kept = await seam.admit(request_for(dispatch_id="a" * 32), foundation=None, context=strict_context())
    assert kept.dispatch_id == "a" * 32


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["A" * 32, "a" * 31, "a" * 33, "g" * 32, 123])
async def test_admit_rejects_a_non_canonical_identity(bad):
    with pytest.raises(seam.DispatchDenial) as raised:
        await seam.admit(request_for(dispatch_id=bad), foundation=None, context=strict_context())
    assert raised.value.code == seam.CODE_PARAMS_INVALID


@pytest.mark.asyncio
async def test_strict_order_and_codes():
    base = request_for()
    cases = [
        (strict_context(actor_user_id=None), seam.CODE_ACTOR_UNRESOLVED),
        (strict_context(tenant_id=-1), seam.CODE_WORKSPACE_RIGHTS_MISSING),
        (strict_context(funding_class=seam.FUNDING_CLASS_WORKSPACE), seam.CODE_FUNDING_CLASS_UNSUPPORTED),
        (strict_context(plan_holder_user_id=None), seam.CODE_PLAN_HOLDER_UNRESOLVED),
        (strict_context(credential_owner_user_id=None), seam.CODE_CREDENTIAL_SOURCE_UNAVAILABLE),
        (strict_context(payer=None), seam.CODE_PAYER_UNRESOLVED),
        (strict_context(plan_policy_version=None), seam.CODE_ALLOWANCE_UNAVAILABLE),
    ]
    for context, code in cases:
        with pytest.raises(seam.DispatchDenial) as raised:
            await seam.admit(base, foundation=full_foundation(), context=context)
        assert raised.value.code == code

    for broken, code in (
        (without_model(), seam.CODE_PARAMS_INVALID),
        (request_for(model=make_model(api_format="")), seam.CODE_PARAMS_INVALID),
        (
            request_for(model=make_model(capabilities=("text",)), requires_capabilities=frozenset({"text", "image"})),
            seam.CODE_CAPABILITY_UNFIT,
        ),
    ):
        with pytest.raises(seam.DispatchDenial) as raised:
            await seam.admit(broken, foundation=full_foundation(), context=strict_context())
        assert raised.value.code == code


@pytest.mark.asyncio
async def test_strict_admit_reserves_once_and_a_stale_version_denies():
    grants, rate = Grants(), Rate()
    admission_service = Reservation(credential_version=3)
    foundation = full_foundation(grants=grants, rate=rate, admission=admission_service)
    admission = await seam.admit(request_for(), foundation=foundation, context=strict_context(credential_version=3))
    assert admission.reserved is True and admission.reservation is not None
    assert (admission_service.calls, grants.calls, rate.calls) == (1, 1, 1)

    stale = Reservation(credential_version=4)
    with pytest.raises(seam.DispatchDenial) as raised:
        await seam.admit(request_for(), foundation=full_foundation(admission=stale), context=strict_context())
    assert raised.value.code == seam.CODE_CREDENTIAL_VERSION_STALE


@pytest.mark.asyncio
async def test_own_denial_survives_and_foreign_exception_maps_redacted():
    own = seam.DispatchDenial(seam.CODE_CATALOG_ACCESS_MISSING)
    with pytest.raises(seam.DispatchDenial) as raised:
        await seam.admit(request_for(), foundation=full_foundation(grants=Grants(denial=own)), context=strict_context())
    assert raised.value.code == seam.CODE_CATALOG_ACCESS_MISSING

    with pytest.raises(seam.DispatchDenial) as raised:
        await seam.admit(
            request_for(),
            foundation=full_foundation(rate=Rate(error=RuntimeError(PRIVATE_BODY))),
            context=strict_context(),
        )
    assert raised.value.code == seam.CODE_CAPACITY_UNAVAILABLE
    assert PRIVATE_BODY not in str(raised.value)
    assert PRIVATE_BODY not in str(raised.value.detail)


@pytest.mark.asyncio
async def test_settle_swallows_failures_but_not_cancellation():
    admission = await seam.admit(request_for(), foundation=full_foundation(), context=strict_context())
    settlement = Settlement()
    await seam.settle(admission, outcome="succeeded", foundation=full_foundation(settlement=settlement))
    assert settlement.calls == ["succeeded"]

    broken = full_foundation(settlement=Settlement(error=RuntimeError(PRIVATE_BODY)))
    recorder = MagicMock()
    with patch.object(seam, "logger", NS(warning=recorder)):
        await seam.settle(admission, outcome="failed_output", foundation=broken)
    recorder.assert_called_once_with("llm_dispatch_settlement_failed outcome=%s", "failed_output")
    assert PRIVATE_BODY not in repr(recorder.call_args_list)

    class Cancelling:
        async def settle(self, *args, **kwargs):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await seam.settle(admission, outcome="succeeded", foundation=seam.DispatchFoundation(settlement=Cancelling()))


def test_fallback_compatibility_rules():
    managed = make_model(identity=1, owner=99, provider_owner=99)
    personal = make_model(identity=2)
    assert seam.require_same_funding_class(managed, managed, catalog_owner_id=OWNER, payer=f"tenant:{TENANT}") is None
    with pytest.raises(seam.DispatchDenial) as raised:
        seam.require_same_funding_class(managed, personal, catalog_owner_id=OWNER, payer=f"tenant:{TENANT}")
    assert raised.value.code == seam.CODE_FALLBACK_FUNDING_CLASS_MISMATCH
    # A different row of the same class keeps the attempt's payer binding.
    other = make_model(identity=3, owner=99, provider_owner=99)
    assert seam.require_same_funding_class(managed, other, catalog_owner_id=OWNER, payer=f"tenant:{TENANT}") is None
    # An unproven payer is not a switch: the next try keeps the attempt's binding.
    assert seam.require_same_funding_class(managed, other, catalog_owner_id=OWNER, payer=None) is None
    # Two proven bindings that differ are a payer switch.
    with pytest.raises(seam.DispatchDenial) as raised:
        seam.require_same_funding_class(
            managed, other, catalog_owner_id=OWNER, payer=f"tenant:{TENANT}", next_payer="tenant:99"
        )
    assert raised.value.code == seam.CODE_FALLBACK_PAYER_MISMATCH
    with pytest.raises(seam.DispatchDenial) as raised:
        seam.require_same_funding_class(managed, other, catalog_owner_id=OWNER, payer=None, next_payer="tenant:99")
    assert raised.value.code == seam.CODE_FALLBACK_PAYER_MISMATCH


# ── D2: compatibility adapter and strict mode ────────────────


@pytest.mark.asyncio
async def test_adapter_compatibility_mode_admits_without_reservation(scope):
    admission = await seam.dispatch_attempt(ClientLike(make_model()), purpose="chat", stage="chat")
    assert admission.reserved is False and admission.reservation is None
    assert seam.is_valid_dispatch_id(admission.dispatch_id)
    context = admission.context
    assert context.actor_user_id == OWNER
    assert context.tenant_id == TENANT
    assert context.payer == f"tenant:{TENANT}"
    assert context.credential_owner_user_id == OWNER
    assert context.funding_class == seam.FUNDING_CLASS_PERSONAL


@pytest.mark.asyncio
async def test_adapter_without_catalog_owner_never_invents_actor_from_row_owner(monkeypatch):
    def denied():
        raise PermissionError("no actor")

    monkeypatch.setattr("app.core.personal_catalog_context.current_catalog_owner_id", denied)
    admission = await seam.dispatch_attempt(ClientLike(make_model(owner=11)), purpose="chat", stage="chat")
    assert admission.reserved is False
    assert admission.context.actor_user_id is None


@pytest.mark.asyncio
async def test_compatibility_mode_never_denies_a_platform_attempt(monkeypatch):
    """§9: the compatibility adapter keeps today's behaviour, so it never denies.

    A platform-fleet attempt outside a personal catalog has no proven actor,
    no payer and no holder; denying it would break every existing route.
    """
    monkeypatch.setattr("app.core.personal_catalog_context.current_catalog_owner_id", lambda: None)
    monkeypatch.setattr("app.core.tenant_context.current_tenant_id", lambda: None)
    admission = await seam.dispatch_attempt(
        ClientLike(make_model(owner=None, provider_owner=None)),
        purpose="analysis",
        stage="text_analysis",
    )
    assert admission.reserved is False and admission.reservation is None
    assert seam.is_valid_dispatch_id(admission.dispatch_id)
    assert admission.context.funding_class == seam.FUNDING_CLASS_PLATFORM
    assert admission.context.actor_user_id is None


@pytest.mark.asyncio
async def test_adapter_strict_mode_denies_for_each_missing_foundation(scope, monkeypatch):
    model = make_model()
    expected = [
        (seam.DispatchFoundation(), seam.CODE_WORKSPACE_RIGHTS_MISSING),
        (seam.DispatchFoundation(authority=Authority()), seam.CODE_PLAN_GRANT_MISSING),
        (seam.DispatchFoundation(authority=Authority(), grants=Grants()), seam.CODE_CAPACITY_UNAVAILABLE),
        (seam.DispatchFoundation(authority=Authority(), grants=Grants(), rate=Rate()), seam.CODE_ADMISSION_UNAVAILABLE),
        (
            seam.DispatchFoundation(authority=Authority(), grants=Grants(), rate=Rate(), settlement=Settlement()),
            seam.CODE_ADMISSION_UNAVAILABLE,
        ),
        (replace(full_foundation(), settlement=None), seam.CODE_ADMISSION_UNAVAILABLE),
    ]

    def wired_context(client, request):
        # The compatibility adapter cannot prove an allowance version; strict
        # mode needs one, so the test supplies it the way P21-T14 will.
        return seam.DispatchContext(**{**strict_context().__dict__, "catalog_id": None})

    with patch.object(settings, "LLM_DISPATCH_SEAM_ENABLED", True):
        for foundation, code in expected:
            with patch.object(seam, "_compat_foundation", lambda value=foundation: value):
                with pytest.raises(seam.DispatchDenial) as raised:
                    await seam.dispatch_attempt(ClientLike(model), purpose="chat", stage="chat")
                assert raised.value.code == code
        foundation = full_foundation()
        with patch.object(seam, "_compat_foundation", lambda: foundation):
            with patch.object(seam, "_compat_context", wired_context):
                admission = await seam.dispatch_attempt(ClientLike(model), purpose="chat", stage="chat")
    assert admission.reserved is True and admission.reservation is not None


# ── D4: provider clients — one entry per attempt, none on denial ──


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "client_cls,api_format",
    [(OpenAICompatibleClient, "openai"), (AnthropicClient, "anthropic"), (CustomClient, "custom")],
)
async def test_each_provider_method_enters_the_seam_exactly_once(
    monkeypatch, scope, quiet_client, client_cls, api_format
):
    http = HttpSpy()
    install_transport(monkeypatch, http)
    client = client_for(make_model(api_format=api_format), monkeypatch)
    assert isinstance(client, client_cls)
    calls = spy_seam(monkeypatch)

    chat = await client.chat([{"role": "user", "content": PRIVATE_PROMPT}])
    assert len(calls) == 1 and len(http.requests) == 1
    assert calls[0]["kwargs"]["stage"] == "chat"
    assert seam.CHAT_DISPATCH_ID_KEY not in chat
    assert seam.is_valid_dispatch_id(chat.dispatch_id)

    await client.analyze(PRIVATE_PROMPT)
    assert len(calls) == 2 and len(http.requests) == 2
    analyze_call = calls[1]["kwargs"]
    assert (analyze_call["purpose"], analyze_call["stage"]) == ("analysis", "text_analysis")
    assert seam.is_valid_dispatch_id(analyze_call["dispatch_id"])


@pytest.mark.asyncio
async def test_route_capabilities_follow_media_and_tools(monkeypatch, scope, quiet_client):
    http = HttpSpy()
    install_transport(monkeypatch, http)
    calls = spy_seam(monkeypatch)
    client = client_for(make_model(capabilities=("text", "image")), monkeypatch)

    await client.analyze(PRIVATE_PROMPT, media_urls=["https://media.invalid/a.png"])
    assert calls[-1]["kwargs"]["has_media"] is True
    request = seam._compat_request(
        client,
        purpose="analysis",
        stage="text_analysis",
        execution=None,
        has_media=True,
        uses_tools=False,
        requested_output_tokens=32,
        requires_capabilities=None,
        dispatch_id=None,
    )
    assert request.requires_capabilities == frozenset({"text", "image"})
    text_only = seam._compat_request(
        client,
        purpose="chat",
        stage="chat",
        execution=None,
        has_media=False,
        uses_tools=True,
        requested_output_tokens=32,
        requires_capabilities=None,
        dispatch_id=None,
    )
    assert text_only.requires_capabilities == frozenset({"text"})


@pytest.mark.asyncio
async def test_no_http_when_the_seam_denies(monkeypatch, scope, quiet_client):
    http = HttpSpy()
    install_transport(monkeypatch, http, fail=True)

    async def deny(client, **kwargs):
        raise seam.DispatchDenial(seam.CODE_PLAN_GRANT_MISSING)

    monkeypatch.setattr(seam, "dispatch_attempt", deny)
    client = client_for(make_model(), monkeypatch)
    with pytest.raises(seam.DispatchDenial):
        await client.chat([{"role": "user", "content": PRIVATE_PROMPT}])
    with pytest.raises(seam.DispatchDenial):
        await client.analyze(PRIVATE_PROMPT)
    assert http.requests == []


@pytest.mark.asyncio
async def test_denial_exposes_no_prompt_key_body_or_token(monkeypatch, scope, quiet_client):
    http = HttpSpy()
    install_transport(monkeypatch, http, fail=True)

    async def leaking_denial(client, **kwargs):
        raise seam.DispatchDenial(seam.CODE_CAPACITY_UNAVAILABLE, detail=f"{PRIVATE_BODY} {PRIVATE_KEY}")

    monkeypatch.setattr(seam, "dispatch_attempt", leaking_denial)
    client = client_for(make_model(), monkeypatch)
    with pytest.raises(seam.DispatchDenial) as raised:
        await client.chat([{"role": "user", "content": PRIVATE_PROMPT}])
    denial = raised.value
    rendered = " ".join([str(denial), denial.code, str(denial.detail), repr(denial)])
    for secret in (PRIVATE_PROMPT, PRIVATE_KEY, PRIVATE_BODY):
        assert secret not in rendered
    assert denial.detail is None
    assert http.requests == []


# ── D4: chat fallback identity and compatibility ─────────────


async def run_chat(monkeypatch, models, *, errors=None, create=None):
    """Drive the real `chat_with_fallback` with faked clients and a captured journal.

    The journal hook is installed at the attempt-journal boundary, so the real
    `journal_chat_attempt` still drains the seam identity from the response.
    """
    errors = errors or {}
    monkeypatch.setattr(llm_client, "_llm_models", AsyncMock(return_value=list(models)))
    monkeypatch.setattr(llm_client, "_allowed_model_types", AsyncMock(return_value=None))
    monkeypatch.setattr(llm_client, "_eligible_text_model", lambda m, allowed: True)
    captured = capture_journal(monkeypatch)

    def default_create(model):
        async def chat(messages, **kwargs):
            return {
                "content": "ok",
                "tool_calls": [],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.0},
                "raw": {"usage": {"prompt_tokens": 1, "completion_tokens": 1}},
                "_personal_dispatch_id": None,
            }

        return named(model, chat)

    def create_with_errors(model):
        if create is not None:
            return create(model)
        error = errors.get(model.id)
        if error is None:
            return default_create(model)

        async def chat(messages, **kwargs):
            raise error

        return named(model, chat)

    monkeypatch.setattr(llm_client, "LLMClientFactory", NS(create=create_with_errors))
    try:
        result = await llm_client.chat_with_fallback([{"role": "user", "content": "x"}])
        return result, captured
    except Exception as error:  # the refusal under test
        return error, captured


def named(model, chat, **kwargs):
    """A client double that keeps its row, provider and name for the seam and journal."""
    return NS(
        **{
            "chat": chat,
            "api_key": "k",
            "provider": model.provider,
            "model_name": model.model_id,
            "model": model,
            **kwargs,
        }
    )


def record_envelopes(sink):
    async def journal_scoped_attempts(envelopes, *, purpose, task_payload=None):
        for envelope in envelopes.values():
            sink.append({"purpose": purpose, "dispatch_id": envelope["request"].get("dispatch_id")})
        return len(envelopes)

    return journal_scoped_attempts


def capture_journal(monkeypatch):
    """Capture journaled attempt identities at the attempt-journal boundary."""
    sink = []
    monkeypatch.setattr(attempt_journal, "journal_scoped_attempts", record_envelopes(sink))
    return sink


@pytest.mark.asyncio
async def test_every_fallback_try_gets_its_own_dispatch_identity(monkeypatch, scope):
    models = [make_model(identity=1), make_model(identity=2)]
    quiet_client_for(monkeypatch)
    entered = []

    def create(model):
        async def chat(messages, **kwargs):
            entered.append(model.id)
            if model.id == 1:
                raise httpx.HTTPStatusError(
                    "429", request=httpx.Request("POST", "https://x.invalid"), response=httpx.Response(429)
                )
            return {"content": "ok", "tool_calls": []}

        return named(model, chat)

    result, rows = await run_chat(monkeypatch, models, create=create)
    assert result["content"] == "ok"
    assert entered == [1, 2]
    identities = [row["dispatch_id"] for row in rows]
    assert len(identities) == 2 and len(set(identities)) == 2
    assert all(seam.is_valid_dispatch_id(value) for value in identities)


@pytest.mark.asyncio
async def test_client_chat_carries_the_seam_identity_into_the_journal(monkeypatch, scope, http):
    install_transport(monkeypatch, http)
    quiet_client_for(monkeypatch)
    captured = capture_journal(monkeypatch)
    client = client_for(make_model(), monkeypatch)

    first = await client.chat([{"role": "user", "content": "x"}])
    assert seam.CHAT_DISPATCH_ID_KEY not in first and seam.is_valid_dispatch_id(first.dispatch_id)
    await llm_client.journal_chat_attempt(client, "chat", first)
    second = await client.chat([{"role": "user", "content": "x"}])
    await llm_client.journal_chat_attempt(client, "chat", second)
    identities = [row["dispatch_id"] for row in captured]
    assert len(identities) == 2 and len(set(identities)) == 2
    assert all(seam.is_valid_dispatch_id(value) for value in identities)
    # The private key never survives into the journaled or returned payload.
    assert seam.CHAT_DISPATCH_ID_KEY not in first and seam.CHAT_DISPATCH_ID_KEY not in second


@pytest.mark.asyncio
async def test_personal_dispatch_identity_still_wins_in_the_journal(monkeypatch, scope, http):
    install_transport(monkeypatch, http)
    monkeypatch.setattr(llm_client, "_record_llm_usage", AsyncMock())
    monkeypatch.setattr(llm_client, "_verify_personal_dispatch_day", AsyncMock())
    monkeypatch.setattr(llm_client, "_revalidate_personal_dispatch_before_http", AsyncMock())
    reserved = "c" * 32
    monkeypatch.setattr(llm_client, "_personal_admission", AsyncMock(return_value=(None, reserved)))
    captured = capture_journal(monkeypatch)
    models = [make_model()]
    monkeypatch.setattr(llm_client, "_llm_models", AsyncMock(return_value=list(models)))
    monkeypatch.setattr(llm_client, "_allowed_model_types", AsyncMock(return_value=None))
    monkeypatch.setattr(llm_client, "_eligible_text_model", lambda m, allowed: True)

    result = await llm_client.chat_with_fallback([{"role": "user", "content": "x"}])
    assert result["content"]
    # The durable personal reservation identity, not the seam's own, is journaled.
    assert [row["dispatch_id"] for row in captured] == [reserved]


@pytest.mark.asyncio
async def test_cross_class_fallback_is_refused_and_sends_no_http(monkeypatch, scope):
    managed = make_model(identity=1, owner=99, provider_owner=99)
    personal = make_model(identity=2)
    quiet_client_for(monkeypatch)
    entered = []

    def create(model):
        async def chat(messages, **kwargs):
            entered.append(model.id)
            # A retryable transport failure: the fleet would walk on to the next row.
            raise httpx.TimeoutException("timeout")

        return named(model, chat)

    error, rows = await run_chat(monkeypatch, [managed, personal], create=create)
    assert isinstance(error, seam.DispatchDenial)
    assert error.code == seam.CODE_FALLBACK_FUNDING_CLASS_MISMATCH
    # The managed try ran and was journaled; the personal row never got a client.
    assert entered == [1]
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_same_class_fallback_still_walks_the_fleet(monkeypatch, scope):
    models = [make_model(identity=1), make_model(identity=2)]
    retryable = httpx.HTTPStatusError(
        "429", request=httpx.Request("POST", "https://x.invalid"), response=httpx.Response(429)
    )
    result, rows = await run_chat(monkeypatch, models, errors={1: retryable})
    assert not isinstance(result, Exception)
    assert result["content"] == "ok"


# ── D4: analyzer candidate loop ──────────────────────────────


class Scenario:
    def __init__(self, name="scenario"):
        self.name = name
        self.max_tokens = 32
        self.output_schema = TEXT_ONLY
        self.media_overrides = {}
        self.base_prompt = None


def prepare_analyzer(monkeypatch, candidates, analyze):
    async def first_model(self, scenario, media_type):
        return candidates[0]

    monkeypatch.setattr(AIAnalyzer, "_get_llm_model", first_model)
    monkeypatch.setattr(analyzer_module, "validate_with_pydantic", lambda parsed, model, strict: parsed)
    monkeypatch.setattr(analyzer_module, "compile_scenario_output", lambda scenario: NS(model=None, schema=TEXT_ONLY))
    monkeypatch.setattr(OpenAICompatibleClient, "analyze", analyze)
    manager = NS(capability_candidates=AsyncMock(return_value=candidates[1:]))
    monkeypatch.setitem(sys.modules, "app.models.managers.llm_model_manager", NS(LLMModelManager=lambda: manager))
    return manager


async def run_text_stage():
    """Drive the real analyzer text stage; the caller's patches are already applied."""
    return await AIAnalyzer()._analyze_text(
        [{"text": "local text long enough to be selected"}], Scenario(), {}, "vk", NS(source_type=None, id=1)
    )


@pytest.mark.asyncio
async def test_analyzer_text_candidates_enter_the_seam_once_each(monkeypatch, scope, quiet_client):
    candidates = [make_model(identity=1), make_model(identity=2)]
    calls = spy_seam(monkeypatch)
    entered = []

    async def analyze(self, prompt, media_urls=None, pydantic_model=None, **kwargs):
        entered.append(self.model.id)
        await seam.dispatch_attempt(self, purpose="analysis", stage="text_analysis", dispatch_id="b" * 32)
        if self.model.id == 1:
            raise httpx.HTTPStatusError(
                "429", request=httpx.Request("POST", "https://x.invalid"), response=httpx.Response(429)
            )
        return {"request": {}, "response": {"ok": True}, "parsed": {"analysis": "ok"}}

    prepare_analyzer(monkeypatch, candidates, analyze)
    result = await run_text_stage()
    assert result is not None and result["parsed"] == {"analysis": "ok"}
    assert entered == [1, 2]
    # One recorded route-level entry per candidate; `entered` counts the client calls.
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_analyzer_denial_ends_the_candidate_loop(monkeypatch, scope, quiet_client):
    candidates = [make_model(identity=1), make_model(identity=2)]
    http = HttpSpy()
    install_transport(monkeypatch, http, fail=True)
    stub, real = arm_stub(monkeypatch)
    entered = []

    async def analyze(self, prompt, media_urls=None, pydantic_model=None, **kwargs):
        entered.append(self.model.id)
        # The real client enters the seam before HTTP; so does this double.
        await seam.dispatch_attempt(self, purpose="analysis", stage="text_analysis")
        raise httpx.TimeoutException("timeout")

    async def deny_second(client, **kwargs):
        if client.model.id == 2:
            entered.append("seam-2")
            raise seam.DispatchDenial(seam.CODE_CAPACITY_UNAVAILABLE)
        return await real(client, **kwargs)

    stub.side_effect = deny_second
    prepare_analyzer(monkeypatch, candidates, analyze)
    with pytest.raises(seam.DispatchDenial) as raised:
        await run_text_stage()
    assert raised.value.code == seam.CODE_CAPACITY_UNAVAILABLE
    # Candidate 2 was created, but its seam entry denied it before any HTTP.
    assert entered == [1, 2, "seam-2"]
    assert http.requests == []


@pytest.mark.asyncio
async def test_analyzer_cross_class_candidate_is_refused(monkeypatch, scope, quiet_client):
    candidates = [make_model(identity=1), make_model(identity=2, owner=99, provider_owner=99)]
    entered = []

    async def analyze(self, prompt, media_urls=None, pydantic_model=None, **kwargs):
        entered.append(self.model.id)
        if self.model.id == 1:
            raise httpx.TimeoutException("timeout")
        return {"request": {}, "response": {"ok": True}, "parsed": {"analysis": "ok"}}

    prepare_analyzer(monkeypatch, candidates, analyze)
    with pytest.raises(seam.DispatchDenial) as raised:
        await run_text_stage()
    assert raised.value.code == seam.CODE_FALLBACK_FUNDING_CLASS_MISMATCH
    assert entered == [1]


# ── D4: route matrix (§4) and compatibility mode (§9) ───────


@pytest.mark.asyncio
@pytest.mark.parametrize("api_format,cls", [("openai", OpenAICompatibleClient), ("anthropic", AnthropicClient)])
async def test_route_matrix_chat_and_analyze_adapters(monkeypatch, scope, quiet_client, api_format, cls):
    http = HttpSpy()
    install_transport(monkeypatch, http)
    seen = []
    real = seam.dispatch_attempt

    async def record(client, **kwargs):
        seen.append((kwargs["purpose"], kwargs["stage"]))
        return await real(client, **kwargs)

    monkeypatch.setattr(seam, "dispatch_attempt", record)
    client = client_for(make_model(api_format=api_format), monkeypatch)
    assert isinstance(client, cls)
    await client.chat([{"role": "user", "content": "x"}])
    await client.analyze("p")
    assert seen == [("agent_chat", "chat"), ("analysis", "text_analysis")]


@pytest.mark.asyncio
async def test_route_matrix_custom_client(monkeypatch, scope, quiet_client):
    http = HttpSpy()
    install_transport(monkeypatch, http)
    seen = []
    real = seam.dispatch_attempt

    async def record(client, **kwargs):
        seen.append((kwargs["purpose"], kwargs["stage"]))
        return await real(client, **kwargs)

    monkeypatch.setattr(seam, "dispatch_attempt", record)
    client = client_for(make_model(api_format="custom"), monkeypatch)
    await client.chat([{"role": "user", "content": "x"}])
    await client.analyze("p")
    assert seen == [("agent_chat", "chat"), ("analysis", "text_analysis")]


@pytest.mark.asyncio
async def test_compatibility_mode_keeps_public_keys_and_pops_the_seam_identity(monkeypatch, scope, http):
    install_transport(monkeypatch, http)
    quiet_client_for(monkeypatch)
    captured = capture_journal(monkeypatch)
    client = client_for(make_model(), monkeypatch)
    response = await client.chat([{"role": "user", "content": "x"}])
    assert {"content", "usage", "raw"} <= set(response)
    assert seam.CHAT_DISPATCH_ID_KEY not in response and "_personal_dispatch_id" in response
    assert "dispatch_id" not in json.loads(json.dumps(response))
    await llm_client.journal_chat_attempt(client, "chat", response)
    assert seam.CHAT_DISPATCH_ID_KEY not in response
    assert not hasattr(response, "dispatch_id")
    assert seam.is_valid_dispatch_id(captured[0]["dispatch_id"])


def test_seam_module_is_storage_free_at_import():
    import inspect

    head = inspect.getsource(seam).split("# ── Funding classes", 1)[0]
    assert "import app." not in head and "from app." not in head
    assert "app.models" not in head and "app.services.tenancy" not in head and "app.services.billing" not in head


@pytest.mark.asyncio
async def test_strict_resolution_order_identity_reaches_receipt_and_holder_is_shared_across_spaces():
    calls = []
    plan = NS(plan_holder_user_id=OWNER, plan_policy_version=3)

    class OrderedAuthority(Authority):
        async def resolve_identity(self, request, hints):
            calls.append("identity_model")
            # A submitted actor/holder/payer never widens the host policy.
            return strict_context(
                actor_user_id=8, tenant_id=hints.tenant_id, delegation_consent_version=2, workspace_plan=plan
            )

        async def resolve_credential(self, request, context):
            calls.append("credential")
            return context

        async def resolve_payer(self, request, context):
            calls.append("payer")
            return replace(context, payer=f"holder:{context.plan_holder_user_id}")

        async def revalidate(self, admission):
            calls.append("freshness")
            return admission.context

    class OrderedGrants(Grants):
        async def resolve(self, request, context):
            calls.append("grants")
            assert context.plan_holder_user_id == OWNER and context.actor_user_id == 8
            return await super().resolve(request, context)

    class OrderedRate(Rate):
        async def acquire(self, request, context):
            calls.append("rate")
            assert seam.is_valid_dispatch_id(request.dispatch_id)
            return await super().acquire(request, context)

    class OrderedReservation(Reservation):
        async def check_allowance(self, request, context):
            calls.append("allowance")
            return True

        async def reserve(self, request, context):
            calls.append("reserve")
            return await super().reserve(request, context)

    foundation = full_foundation(
        authority=OrderedAuthority(), grants=OrderedGrants(), rate=OrderedRate(), admission=OrderedReservation()
    )
    admitted = []
    for tenant in (3, 4):
        calls.clear()
        result = await seam.admit(
            request_for(execution={"actor_user_id": 999, "payer": "attacker"}),
            foundation=foundation,
            context=strict_context(tenant_id=tenant),
        )
        assert calls == ["identity_model", "grants", "credential", "payer", "allowance", "rate", "reserve", "freshness"]
        assert result.context.workspace_plan is plan
        assert result.dispatch_id == result.request.dispatch_id == result.reservation["receipt_id"]
        admitted.append(result)
        await seam.settle(result, outcome="succeeded", foundation=foundation)
    assert admitted[0].context.payer == admitted[1].context.payer == f"holder:{OWNER}"
    assert admitted[0].dispatch_id != admitted[1].dispatch_id


@pytest.mark.asyncio
async def test_platform_credential_is_allowlisted_reference_not_personal_owner():
    context = strict_context(
        funding_class=seam.FUNDING_CLASS_PLATFORM, credential_owner_user_id=None, credential_ref="deployment-model-key"
    )
    result = await seam.admit(request_for(model=make_model(owner=None)), context=context, foundation=full_foundation())
    assert result.context.credential_owner_user_id is None
    with pytest.raises(seam.DispatchDenial):
        await seam.admit(request_for(), context=replace(context, credential_ref=None), foundation=full_foundation())


@pytest.mark.asyncio
async def test_invited_admin_needs_holder_consent_not_actor_owned_credential():
    for changes in (
        {"delegation_consent_version": None},
        {"credential_owner_user_id": 8, "delegation_consent_version": 2},
    ):
        with pytest.raises(seam.DispatchDenial):
            await seam.admit(
                request_for(), context=strict_context(actor_user_id=8, **changes), foundation=full_foundation()
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("actor_user_id", None),
        ("delegation_consent_version", None),
        ("plan_policy_version", 4),
        ("credential_version", 4),
    ],
)
async def test_queued_revocation_releases_receipt_and_rate_before_http(field, value):
    class Revoked(Authority):
        async def revalidate(self, admission):
            return replace(admission.context, **{field: value})

    rate, settlement = Rate(), Settlement()
    foundation = full_foundation(authority=Revoked(), rate=rate, settlement=settlement)
    with pytest.raises(seam.DispatchDenial):
        await seam.admit(
            request_for(), context=strict_context(actor_user_id=8, delegation_consent_version=2), foundation=foundation
        )
    assert settlement.calls == ["not_dispatched"]
    assert len(rate.released) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [None, False, NS(granted=False)])
async def test_unproven_grant_never_reaches_rate_or_reservation(bad):
    grants, rate, reservation = Grants(), Rate(), Reservation()
    grants.resolve = AsyncMock(return_value=bad)
    with pytest.raises(seam.DispatchDenial):
        await seam.admit(
            request_for(),
            context=strict_context(),
            foundation=full_foundation(grants=grants, rate=rate, admission=reservation),
        )
    assert rate.calls == reservation.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [None, False, NS(slot="capacity")])
async def test_unproven_capacity_never_reaches_reservation(bad):
    rate, reservation = Rate(), Reservation()
    rate.acquire = AsyncMock(return_value=bad)
    with pytest.raises(seam.DispatchDenial):
        await seam.admit(
            request_for(), context=strict_context(), foundation=full_foundation(rate=rate, admission=reservation)
        )
    assert reservation.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [None, False, NS(credential_version=3)])
async def test_unproven_receipt_releases_capacity(bad):
    rate, reservation = Rate(), Reservation()
    reservation.reserve = AsyncMock(return_value=bad)
    with pytest.raises(seam.DispatchDenial):
        await seam.admit(
            request_for(), context=strict_context(), foundation=full_foundation(rate=rate, admission=reservation)
        )
    assert len(rate.released) == 1


@pytest.mark.asyncio
async def test_reservation_cancellation_releases_rate():
    rate, reservation = Rate(), Reservation()
    reservation.reserve = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await seam.admit(
            request_for(), context=strict_context(), foundation=full_foundation(rate=rate, admission=reservation)
        )
    assert len(rate.released) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [True, 0, -1, "32", 1.5])
async def test_invalid_token_limit_is_denied_before_capacity(bad):
    rate = Rate()
    with pytest.raises(seam.DispatchDenial) as denied:
        await seam.admit(
            request_for(requested_output_tokens=bad), context=strict_context(), foundation=full_foundation(rate=rate)
        )
    assert denied.value.code == seam.CODE_PARAMS_INVALID and rate.calls == 0


def test_denial_labels_and_identity_reject_secret_shaped_values():
    assert seam.DispatchDenial(seam.CODE_PARAMS_INVALID, "sk_secret_token").detail is None
    assert seam.DispatchDenial(seam.CODE_PARAMS_INVALID, ["secret"]).detail is None
    assert not seam.is_valid_dispatch_id("a" * 32 + "\n")
    with pytest.raises(seam.DispatchDenial):
        seam.require_same_funding_class(
            make_model(), make_model(), catalog_owner_id=OWNER, payer="holder:7", next_payer="holder:8"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("api_format", ["openai", "anthropic", "custom"])
async def test_actual_failed_chat_keeps_seam_identity_in_journal(monkeypatch, scope, quiet_client, api_format):
    client = client_for(make_model(api_format=api_format), monkeypatch)
    captured, admissions = capture_journal(monkeypatch), []
    real = seam.dispatch_attempt

    async def record(client, **kwargs):
        result = await real(client, **kwargs)
        admissions.append(result)
        return result

    monkeypatch.setattr(seam, "dispatch_attempt", record)
    http = HttpSpy()
    fake = http.factory()
    fake.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", lambda **kwargs: fake)
    with pytest.raises(httpx.TimeoutException) as failed:
        await client.chat([{"role": "user", "content": "x"}])
    assert failed.value.dispatch_id == admissions[0].dispatch_id
    await llm_client.journal_chat_attempt(client, "chat", None, dispatch_id=failed.value.dispatch_id)
    assert [row["dispatch_id"] for row in captured] == [admissions[0].dispatch_id]


@pytest.mark.asyncio
async def test_empty_chat_attempt_is_journaled_before_fallback(monkeypatch, scope):
    models = [make_model(identity=1), make_model(identity=2)]

    def create(model):
        async def chat(messages, **kwargs):
            return seam.DispatchResponse(
                {"content": "" if model.id == 1 else "ok", "tool_calls": []}, str(model.id) * 32
            )

        return named(model, chat)

    result, rows = await run_chat(monkeypatch, models, create=create)
    assert result["content"] == "ok"
    assert [row["dispatch_id"] for row in rows] == ["1" * 32, "2" * 32]
