"""P21-T13 increment B: grant resolver against mocked storage only.

The real `CatalogGrantResolver` source runs against a `MockedAsyncSession` and a
recorded `resolve_holder_context` double. No database, no network, no provider
and no credential: every refusal is a redacted `DispatchDenial`. Source-only, so
it also passes without the database-backed root conftest.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from dataclasses import replace

import pytest

from app.services.ai import catalog_grants
from app.services.ai.catalog_grants import CatalogGrantError, CatalogGrantResolver
from app.services.ai.dispatch_seam import (
    CODE_ACTOR_UNRESOLVED,
    CODE_CATALOG_ACCESS_MISSING,
    CODE_PARAMS_INVALID,
    CODE_PLAN_GRANT_MISSING,
    FUNDING_CLASS_PERSONAL,
    FUNDING_CLASS_PLATFORM,
    DispatchContext,
    DispatchDenial,
    DispatchGrant,
    DispatchRequest,
)
from app.services.tenancy.capabilities import CAPABILITIES
from app.services.tenancy.service_plan_policy import (
    TokenAllowancePolicy,
    ValidatedPlanPolicy,
    WorkspacePlanContext,
)

__source_only__ = True

HOST = 11
INVITEE = 22
TENANT = 33
CATALOG = 4
PROVIDER = 5
MODEL = 6
TOKEN_UNIT = 1_000_000
MODULE = sorted(CAPABILITIES)[0]


# ── storage doubles ─────────────────────────────────────────


class FakeRow:
    """A projection row exposing a `_mapping`, like a real SQLAlchemy Row."""

    def __init__(self, values):
        self._mapping = dict(values)


class FakeResult:
    def __init__(self, rows):
        self.rows = list(rows)

    def one_or_none(self):
        assert len(self.rows) <= 1, "ambiguous fake projection"
        return self.rows[0] if self.rows else None

    def all(self):
        return list(self.rows)

    def scalars(self):
        return FakeScalars(self.rows)


class FakeScalars:
    """`ScalarResult` double: one column, so each row is a bare value or a 1-tuple."""

    def __init__(self, rows):
        self.rows = list(rows)

    def all(self):
        return [row[0] if isinstance(row, tuple) and len(row) == 1 else row for row in self.rows]


class MockedAsyncSession:
    """Answers queued results in call order and records every statement."""

    def __init__(self, results=()):
        self.results = list(results)
        self.statements = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, stmt, *args, **kwargs):
        self.statements.append(stmt)
        if not self.results:
            raise AssertionError("unexpected extra statement")
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    async def commit(self):  # pragma: no cover - guard only
        self.commits += 1
        raise AssertionError("the resolver must never commit")

    async def rollback(self):  # pragma: no cover - guard only
        self.rollbacks += 1
        raise AssertionError("the resolver must never roll back")


# ── real policy DTO builders ────────────────────────────────


def plan_policy(*, plan_code="pro", version=3, model_types=("text", "image"), modules=("*",)):
    return ValidatedPlanPolicy(
        plan_code=plan_code,
        policy_version=version,
        module_grants=tuple(modules),
        daily_quotas={},
        resource_limits={"label": plan_code, "model_types": None if model_types is None else list(model_types)},
        owned_workspace_limit_configured=True,
        max_owned_workspaces=2,
        token_policy=TokenAllowancePolicy(unit="tokens", period="day", token_limit=1000, policy_version=version),
    )


def holder(
    *,
    actor=HOST,
    tenant=TENANT,
    holder_user=HOST,
    plan_code="pro",
    version=3,
    model_types=("text", "image"),
    modules=("*",),
):
    snap = plan_policy(plan_code=plan_code, version=version, model_types=model_types, modules=modules)
    return WorkspacePlanContext(
        actor_user_id=actor,
        tenant_id=tenant,
        plan_holder_user_id=holder_user,
        plan_code=snap.plan_code,
        plan_policy_version=snap.policy_version,
        plan_expires_at=None,
        policy=snap,
    )


def make_model(*, model=MODEL, provider=PROVIDER, capabilities=("text",), api_format="openai"):
    provider_row = type("ProviderRow", (), {"id": provider, "api_format": api_format})()
    return type("ModelRow", (), {"id": model, "provider": provider_row, "capabilities": list(capabilities)})()


def make_request(*, model=None, capabilities=("text",)):
    return DispatchRequest(
        model=model if model is not None else make_model(),
        purpose="analysis",
        stage="text_analysis",
        requires_capabilities=frozenset(capabilities),
    )


def make_context(
    *, actor=HOST, tenant=TENANT, holder_user=HOST, funding=FUNDING_CLASS_PLATFORM, snapshot=None, **extra
):
    return DispatchContext(
        actor_user_id=actor,
        tenant_id=tenant,
        plan_holder_user_id=holder_user,
        funding_class=funding,
        plan_policy_version=extra.pop("plan_policy_version", 3),
        workspace_plan=(
            snapshot if snapshot is not None else holder(actor=actor, tenant=tenant, holder_user=holder_user)
        ),
        **extra,
    )


def custody_row(
    *,
    kind=FUNDING_CLASS_PLATFORM,
    owner=None,
    catalog_id=CATALOG,
    provider_catalog_id=None,
    model_owner=None,
    provider_owner=None,
    model_active=True,
    provider_active=True,
    catalog_active=True,
    capabilities=("text",),
):
    same_owner = owner if model_owner is None else model_owner
    provider_same_owner = owner if provider_owner is None else provider_owner
    return FakeRow(
        {
            "id": MODEL,
            "catalog_id": catalog_id,
            "model_owner_id": same_owner,
            "capabilities": list(capabilities),
            "model_is_active": model_active,
            "provider_id": PROVIDER,
            "provider_catalog_id": catalog_id if provider_catalog_id is None else provider_catalog_id,
            "provider_owner_id": provider_same_owner,
            "provider_is_active": provider_active,
            "catalog_row_id": catalog_id,
            "catalog_kind": kind,
            "catalog_owner_id": owner,
            "catalog_is_active": catalog_active,
        }
    )


def personal_row(owner=HOST):
    return custody_row(kind=FUNDING_CLASS_PERSONAL, owner=owner)


def resolve_with(*, session, request=None, context=None, fresh=None, resolver=None, **context_fields):
    """Run `resolve`; return the grant or the denial, plus the recorded holder calls."""
    context = context if context is not None else make_context(**context_fields)
    fresh = fresh if fresh is not None else holder(actor=context.actor_user_id, tenant=context.tenant_id)
    calls = []

    async def fake_resolve_holder_context(db, tenant_id, actor_user_id, **rest):
        calls.append((db, tenant_id, actor_user_id, rest))
        if isinstance(fresh, BaseException):
            raise fresh
        return fresh

    resolver = resolver if resolver is not None else CatalogGrantResolver(session)
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(catalog_grants, "resolve_holder_context", fake_resolve_holder_context)
        try:
            return asyncio.run(resolver.resolve(request if request is not None else make_request(), context)), calls
        except DispatchDenial as denial:
            return denial, calls


# ── platform allow ──────────────────────────────────────────


def test_platform_grant_allows_with_one_fresh_holder_read():
    session = MockedAsyncSession([FakeResult([custody_row()]), FakeResult(["text"])])
    result, calls = resolve_with(session=session)

    assert isinstance(result, DispatchGrant)
    assert result.catalog_id == str(CATALOG) and type(result.catalog_id) is str
    assert [(call[1], call[2], call[3]) for call in calls] == [(TENANT, HOST, {"lock_holder": False})]
    assert session.commits == 0 and session.rollbacks == 0


def test_every_required_capability_needs_its_own_active_grant():
    request = make_request(model=make_model(capabilities=("text", "image")), capabilities=("text", "image"))
    result, _ = resolve_with(
        session=MockedAsyncSession([FakeResult([custody_row(capabilities=("text", "image"))]), FakeResult([])]),
        request=request,
    )
    assert isinstance(result, DispatchDenial)
    # Every required capability needs its own active grant; the missing `image`
    # grant denies with the plan-grant code and no echoed grant text.
    assert result.code == CODE_PLAN_GRANT_MISSING
    assert result.detail is None


def test_whole_row_capability_set_must_fit_the_policy_list():
    request = make_request(model=make_model(capabilities=("text", "image")), capabilities=("text",))
    session = MockedAsyncSession([FakeResult([custody_row(capabilities=("text", "image"))])])
    result, _ = resolve_with(
        session=session,
        request=request,
        context=make_context(snapshot=holder(model_types=("text",))),
        fresh=holder(model_types=("text",)),
    )
    assert isinstance(result, DispatchDenial) and result.code == CODE_PLAN_GRANT_MISSING
    # The custody row is read first, then its whole set is rejected: one statement,
    # no plan-grant query.
    assert len(session.statements) == 1


@pytest.mark.parametrize("model_types", [None, (), "text"])
def test_missing_or_malformed_policy_list_denies(model_types):
    session = MockedAsyncSession([FakeResult([custody_row()])])
    result, _ = resolve_with(
        session=session,
        context=make_context(snapshot=holder(model_types=model_types)),
        fresh=holder(model_types=model_types),
    )
    assert isinstance(result, DispatchDenial) and result.code == CODE_PLAN_GRANT_MISSING
    # The policy list is the authority, so a row outside it (or a missing list)
    # never reaches the plan-grant query.
    assert len(session.statements) == 1


# ── downgrade, revoke, forged identity ──────────────────────


def test_revoked_grant_denies_the_same_attempt_on_the_next_resolve():
    request, context, fresh = make_request(), make_context(), holder()
    allowed, _ = resolve_with(
        session=MockedAsyncSession([FakeResult([custody_row()]), FakeResult(["text"])]),
        request=request,
        context=context,
        fresh=fresh,
    )
    assert isinstance(allowed, DispatchGrant)

    revoked, _ = resolve_with(
        session=MockedAsyncSession([FakeResult([custody_row()]), FakeResult([])]),
        request=request,
        context=context,
        fresh=fresh,
    )
    assert isinstance(revoked, DispatchDenial) and revoked.code == CODE_PLAN_GRANT_MISSING


def test_downgraded_holder_policy_denies_despite_a_stale_snapshot():
    request = make_request(model=make_model(capabilities=("text", "image")), capabilities=("text",))
    stale = holder()
    downgraded = holder(model_types=("text",))
    session = MockedAsyncSession([FakeResult([custody_row(capabilities=("text", "image"))])])
    result, _ = resolve_with(session=session, request=request, context=make_context(snapshot=stale), fresh=downgraded)
    assert isinstance(result, DispatchDenial) and result.code == CODE_PLAN_GRANT_MISSING
    assert len(session.statements) == 1


@pytest.mark.parametrize(
    "snapshot",
    [
        {"plan_holder_user_id": HOST, "plan_code": "pro"},  # a dict, not the typed DTO
        "pro",
        holder(holder_user=INVITEE),
        holder(plan_code="business"),
        holder(version=4),
        holder(tenant=TENANT + 1),
        holder(actor=INVITEE),
    ],
    ids=["dict", "truthy-string", "other-holder", "other-plan", "other-version", "other-tenant", "other-actor"],
)
def test_snapshot_identity_discrepancy_is_a_redacted_holder_denial(snapshot):
    result, _ = resolve_with(session=MockedAsyncSession([]), context=make_context(snapshot=snapshot), fresh=holder())
    assert isinstance(result, DispatchDenial) and result.code == CODE_PLAN_GRANT_MISSING
    assert repr(result) == f"DispatchDenial(code={result.code!r}, detail={result.detail!r})"


def test_holder_exception_text_is_never_echoed():
    sentinel = "RAW-EXCEPTION-PROVIDER-BODY"
    result, _ = resolve_with(session=MockedAsyncSession([]), fresh=RuntimeError(sentinel))
    assert isinstance(result, DispatchDenial)
    assert sentinel not in str(result) and sentinel not in repr(result)
    assert result.code == CODE_PLAN_GRANT_MISSING


def test_storage_failure_during_custody_read_denies_without_echo():
    sentinel = "RAW-STORAGE-FAILURE"
    result, _ = resolve_with(session=MockedAsyncSession([RuntimeError(sentinel)]))
    assert isinstance(result, DispatchDenial) and result.code == CODE_CATALOG_ACCESS_MISSING
    assert sentinel not in str(result)


@pytest.mark.parametrize("bad", [True, 0, -1, "6", 6.0, None])
def test_forged_model_ids_deny_before_any_read(bad):
    session = MockedAsyncSession([])
    result, calls = resolve_with(session=session, request=make_request(model=make_model(model=bad)))
    assert isinstance(result, DispatchDenial) and result.code == CODE_PARAMS_INVALID
    assert session.statements == [] and calls == []


@pytest.mark.parametrize(
    "field_name,value", [("actor_user_id", None), ("actor_user_id", True), ("tenant_id", 0), ("tenant_id", None)]
)
def test_actor_and_tenant_must_be_positive_ids(field_name, value):
    context = replace(make_context(), **{field_name: value})
    result, _ = resolve_with(session=MockedAsyncSession([]), context=context, fresh=holder())
    expected = CODE_ACTOR_UNRESOLVED if field_name == "actor_user_id" else CODE_CATALOG_ACCESS_MISSING
    assert isinstance(result, DispatchDenial) and result.code == expected


@pytest.mark.parametrize("funding", ["workspace", "unknown", None])
def test_only_explicit_funding_classes_resolve(funding):
    result, _ = resolve_with(session=MockedAsyncSession([]), funding=funding)
    assert isinstance(result, DispatchDenial) and result.code == CODE_CATALOG_ACCESS_MISSING


@pytest.mark.parametrize("capabilities", [frozenset(), frozenset({"text", "telepathy"}), {"text"}, ("text",)])
def test_required_capabilities_must_be_a_nonempty_frozenset(capabilities):
    request = DispatchRequest(model=make_model(), purpose="x", stage="x", requires_capabilities=capabilities)
    session = MockedAsyncSession([])
    result, _ = resolve_with(session=session, request=request)
    # Anything but a non-empty frozenset of stored capabilities is malformed and
    # denied before any holder read or storage statement.
    assert isinstance(result, DispatchDenial) and result.code == CODE_PARAMS_INVALID
    assert session.statements == []


# ── custody, activity, catalog identity ─────────────────────


def test_provider_catalog_mismatch_denies():
    result, _ = resolve_with(session=MockedAsyncSession([FakeResult([custody_row(provider_catalog_id=CATALOG + 1)])]))
    assert isinstance(result, DispatchDenial) and result.code == CODE_CATALOG_ACCESS_MISSING


@pytest.mark.parametrize("flags", [{"catalog_active": False}, {"provider_active": False}, {"model_active": False}])
def test_inactive_catalog_provider_or_model_denies(flags):
    result, _ = resolve_with(session=MockedAsyncSession([FakeResult([custody_row(**flags)])]))
    assert isinstance(result, DispatchDenial) and result.code == CODE_CATALOG_ACCESS_MISSING


def test_platform_row_with_an_owner_denies():
    result, _ = resolve_with(session=MockedAsyncSession([FakeResult([custody_row(owner=HOST)])]))
    assert isinstance(result, DispatchDenial) and result.code == CODE_CATALOG_ACCESS_MISSING


def test_retained_owner_must_agree_with_the_personal_catalog_owner():
    row = custody_row(kind=FUNDING_CLASS_PERSONAL, owner=HOST, model_owner=INVITEE, provider_owner=HOST)
    result, _ = resolve_with(session=MockedAsyncSession([FakeResult([row])]), funding=FUNDING_CLASS_PERSONAL)
    assert isinstance(result, DispatchDenial) and result.code == CODE_CATALOG_ACCESS_MISSING


def test_missing_row_denies_before_any_grant_read():
    session = MockedAsyncSession([FakeResult([])])
    result, _ = resolve_with(session=session)
    assert isinstance(result, DispatchDenial) and result.code == CODE_CATALOG_ACCESS_MISSING
    assert len(session.statements) == 1


def test_a_personal_row_never_funds_a_platform_attempt():
    result, _ = resolve_with(session=MockedAsyncSession([FakeResult([personal_row()])]))
    assert isinstance(result, DispatchDenial) and result.code == CODE_CATALOG_ACCESS_MISSING


def test_context_catalog_id_is_optional_but_must_match():
    mismatch, _ = resolve_with(
        session=MockedAsyncSession([FakeResult([custody_row()]), FakeResult(["text"])]), catalog_id=str(CATALOG + 9)
    )
    assert isinstance(mismatch, DispatchDenial) and mismatch.code == CODE_CATALOG_ACCESS_MISSING

    match, _ = resolve_with(
        session=MockedAsyncSession([FakeResult([custody_row()]), FakeResult(["text"])]), catalog_id=str(CATALOG)
    )
    assert isinstance(match, DispatchGrant) and match.catalog_id == str(CATALOG)


# ── personal funding ────────────────────────────────────────


def test_host_personal_grant_allows_without_consent_or_plan_query():
    session = MockedAsyncSession([FakeResult([personal_row()])])
    result, _ = resolve_with(session=session, funding=FUNDING_CLASS_PERSONAL)
    assert isinstance(result, DispatchGrant) and result.catalog_id == str(CATALOG)
    assert len(session.statements) == 1


def test_invitee_needs_a_positive_delegation_consent_version():
    allowed, _ = resolve_with(
        session=MockedAsyncSession([FakeResult([personal_row()])]),
        funding=FUNDING_CLASS_PERSONAL,
        actor=INVITEE,
        fresh=holder(actor=INVITEE),
        credential_owner_user_id=HOST,
        delegation_consent_version=7,
    )
    assert isinstance(allowed, DispatchGrant)

    for version in (None, 0, True, "7", -1):
        denied, _ = resolve_with(
            session=MockedAsyncSession([FakeResult([personal_row()])]),
            funding=FUNDING_CLASS_PERSONAL,
            actor=INVITEE,
            fresh=holder(actor=INVITEE),
            credential_owner_user_id=HOST,
            delegation_consent_version=version,
        )
        assert isinstance(denied, DispatchDenial), version
        assert denied.code == CODE_CATALOG_ACCESS_MISSING


def test_credential_owner_is_optional_at_step_four_but_must_match_the_holder():
    absent, _ = resolve_with(session=MockedAsyncSession([FakeResult([personal_row()])]), funding=FUNDING_CLASS_PERSONAL)
    assert isinstance(absent, DispatchGrant)
    matching, _ = resolve_with(
        session=MockedAsyncSession([FakeResult([personal_row()])]),
        funding=FUNDING_CLASS_PERSONAL,
        credential_owner_user_id=HOST,
    )
    assert isinstance(matching, DispatchGrant)
    contradicting, _ = resolve_with(
        session=MockedAsyncSession([FakeResult([personal_row()])]),
        funding=FUNDING_CLASS_PERSONAL,
        credential_owner_user_id=INVITEE,
    )
    assert isinstance(contradicting, DispatchDenial)


def test_another_users_personal_catalog_is_never_reachable():
    result, _ = resolve_with(
        session=MockedAsyncSession([FakeResult([personal_row(INVITEE)])]),
        funding=FUNDING_CLASS_PERSONAL,
        actor=INVITEE,
        fresh=holder(actor=INVITEE),
        delegation_consent_version=9,
        credential_owner_user_id=HOST,
    )
    assert isinstance(result, DispatchDenial) and result.code == CODE_CATALOG_ACCESS_MISSING


def test_invitee_never_borrows_its_own_personal_plan_for_a_platform_model():
    """Host grant authorizes; a higher/lower invitee tariff is neither read nor substitutable."""
    request, context = make_request(), make_context(actor=INVITEE, funding=FUNDING_CLASS_PLATFORM)
    host_fresh = holder(actor=INVITEE)  # holder policy of HOST, actor INVITEE
    allowed, _ = resolve_with(
        session=MockedAsyncSession([FakeResult([custody_row()]), FakeResult(["text"])]),
        request=request,
        context=context,
        fresh=host_fresh,
    )
    assert isinstance(allowed, DispatchGrant)

    # An invitee "own plan" appears nowhere in the query: only the host plan code
    # is filtered, so a different self-plan cannot widen or narrow the result.
    session = MockedAsyncSession([FakeResult([custody_row()]), FakeResult(["text"])])
    resolve_with(session=session, request=request, context=context, fresh=host_fresh)
    grant_sql = str(session.statements[1])
    assert "plan_model_grants.plan" in grant_sql
    assert "users.id" not in grant_sql and "tenant_users" not in grant_sql


# ── server-supplied module requirements ─────────────────────


def test_unknown_module_is_refused_at_construction():
    with pytest.raises(CatalogGrantError):
        CatalogGrantResolver(MockedAsyncSession([]), required_modules=("not_a_registered_module",))


def test_required_module_comes_from_the_fresh_holder_policy():
    allowed_session = MockedAsyncSession([FakeResult([custody_row()]), FakeResult(["text"])])
    resolver = CatalogGrantResolver(allowed_session, required_modules=(MODULE,))

    denied, _ = resolve_with(session=MockedAsyncSession([]), resolver=resolver, fresh=holder(modules=()))
    assert isinstance(denied, DispatchDenial) and denied.code == CODE_PLAN_GRANT_MISSING

    allowed, _ = resolve_with(session=allowed_session, resolver=resolver, fresh=holder(modules=("*",)))
    assert isinstance(allowed, DispatchGrant)


# ── AST / source guards ─────────────────────────────────────


FORBIDDEN_MODULES = (
    "httpx",
    "requests",
    "aiohttp",
    "openai",
    "anthropic",
    "app.services.ai.llm_client",
    "app.services.ai.credential_boundary",
    "app.services.ai.provider_egress",
    "app.services.ai.personal_catalog",
    "app.core.config",
    "app.models.managers",
    "app.models.managers.base_manager",
)
FORBIDDEN_COLUMNS = (
    ".base_url",
    ".auth_header",
    ".encrypted_api_key",
    ".managed_credential_ref",
    ".get_api_key",
    ".objects",
)


def imported_modules(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_no_provider_cipher_or_http_imports():
    for module in imported_modules(catalog_grants.__file__):
        assert not any(module == bad or module.startswith(bad + ".") for bad in FORBIDDEN_MODULES), module


def test_no_secret_column_or_settings_access_in_source():
    tree = ast.parse(inspect.getsource(catalog_grants))
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    for token in ("base_url", "auth_header", "encrypted_api_key", "managed_credential_ref", "get_api_key", "objects"):
        assert token not in attributes, token
    assert not names & {"settings", "engine", "SessionLocal"}


def test_resolver_opens_no_transaction_or_autoflush_scope():
    source = inspect.getsource(catalog_grants)
    for token in (".commit(", ".rollback(", "begin_nested", ".begin(", "no_autoflush", "with_for_update"):
        assert token not in source, token


@pytest.mark.parametrize("version", [None, True, 0, 4])
def test_context_policy_version_must_match_fresh_holder(version):
    result, calls = resolve_with(session=MockedAsyncSession([]), context=make_context(plan_policy_version=version))
    assert isinstance(result, DispatchDenial) and result.code == CODE_PLAN_GRANT_MISSING


def test_fresh_policy_payload_must_match_its_version_identity():
    fresh = replace(holder(), policy=plan_policy(version=4))
    result, _ = resolve_with(session=MockedAsyncSession([]), fresh=fresh)
    assert isinstance(result, DispatchDenial) and result.code == CODE_PLAN_GRANT_MISSING
