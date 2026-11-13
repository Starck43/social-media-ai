"""The one LLM dispatch seam: ordered, fail-closed resolution before every HTTP attempt.

Normative spec: [LLM_DISPATCH_CONTRACT](../../../docs/platform/LLM_DISPATCH_CONTRACT.md).
This module owns the resolution order (§1), the typed identity context (§2), the
funding classes (§3), the fallback compatibility rule (§5), the redacted denial
taxonomy (§6) and the injection interfaces (§7). It is a pure stdlib module: it
reads no storage, imports no model, tenancy or billing module at import time and
never touches a provider. The foundation services (grants, rate, admission,
settlement) are injected by P21-T13…P21-T16; a missing or failing foundation is
a denial, never a permissive fallback.

A denial is redacted by construction: it carries a stable code and one short
fixed label. Prompts, provider bodies, credentials, tokens, endpoint secrets and
user content must never reach its message, its detail or its log line.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Protocol, Set
from uuid import uuid4

logger = logging.getLogger(__name__)

# ── Funding classes (§3) ──────────────────────────────────────
FUNDING_CLASS_PLATFORM = "platform"
FUNDING_CLASS_PERSONAL = "personal"
#: Reserved class: never returned by resolution, never selectable, never paid.
FUNDING_CLASS_WORKSPACE = "workspace"

FUNDING_CLASSES = frozenset({FUNDING_CLASS_PLATFORM, FUNDING_CLASS_PERSONAL, FUNDING_CLASS_WORKSPACE})

# ── Redacted denial taxonomy (§6), one constant per code ──────
CODE_ACTOR_UNRESOLVED = "actor_unresolved"
CODE_WORKSPACE_RIGHTS_MISSING = "workspace_rights_missing"
CODE_PLAN_HOLDER_UNRESOLVED = "plan_holder_unresolved"
CODE_PLAN_GRANT_MISSING = "plan_grant_missing"
CODE_CATALOG_ACCESS_MISSING = "catalog_access_missing"
CODE_CREDENTIAL_SOURCE_UNAVAILABLE = "credential_source_unavailable"
CODE_CREDENTIAL_VERSION_STALE = "credential_version_stale"
CODE_PAYER_UNRESOLVED = "payer_unresolved"
CODE_PARAMS_INVALID = "params_invalid"
CODE_CAPABILITY_UNFIT = "capability_unfit"
CODE_ALLOWANCE_UNAVAILABLE = "allowance_unavailable"
CODE_CAPACITY_UNAVAILABLE = "capacity_unavailable"
CODE_ADMISSION_UNAVAILABLE = "admission_unavailable"
CODE_FALLBACK_FUNDING_CLASS_MISMATCH = "fallback_funding_class_mismatch"
CODE_FALLBACK_PAYER_MISMATCH = "fallback_payer_mismatch"
CODE_FUNDING_CLASS_UNSUPPORTED = "funding_class_unsupported"
CODE_WORKSPACE_FUNDING_INACTIVE = "workspace_funding_inactive"

DENIAL_CODES = frozenset(
    {
        CODE_ACTOR_UNRESOLVED,
        CODE_WORKSPACE_RIGHTS_MISSING,
        CODE_PLAN_HOLDER_UNRESOLVED,
        CODE_PLAN_GRANT_MISSING,
        CODE_CATALOG_ACCESS_MISSING,
        CODE_CREDENTIAL_SOURCE_UNAVAILABLE,
        CODE_CREDENTIAL_VERSION_STALE,
        CODE_PAYER_UNRESOLVED,
        CODE_PARAMS_INVALID,
        CODE_CAPABILITY_UNFIT,
        CODE_ALLOWANCE_UNAVAILABLE,
        CODE_CAPACITY_UNAVAILABLE,
        CODE_ADMISSION_UNAVAILABLE,
        CODE_FALLBACK_FUNDING_CLASS_MISMATCH,
        CODE_FALLBACK_PAYER_MISMATCH,
        CODE_FUNDING_CLASS_UNSUPPORTED,
        CODE_WORKSPACE_FUNDING_INACTIVE,
    }
)

#: Short fixed labels. Never build a detail from an exception, a payload or a name.
_DETAIL_MAX = 64
_LABEL = re.compile(r"^[a-z0-9_]{1,%d}$" % _DETAIL_MAX)


class DispatchDenial(RuntimeError):
    """A redacted, fail-closed refusal of one provider attempt.

    `code` is one of the §6 taxonomy codes; `detail` is a short fixed label
    (`step_<n>_<service>`, `fallback_class`, ...) or None. Neither field ever
    carries content, credentials or provider text.
    """

    def __init__(self, code: str, detail: str | None = None):
        self.code = code if code in DENIAL_CODES else CODE_PARAMS_INVALID
        self.detail = detail if isinstance(detail, str) and _LABEL.match(detail) else None
        super().__init__(f"llm_dispatch_denied: {self.code}")

    def __repr__(self) -> str:  # never expose the call stack's locals
        return f"DispatchDenial(code={self.code!r}, detail={self.detail!r})"


# ── Typed request, identity context and admission (§1, §2) ────


@dataclass(frozen=True)
class DispatchRequest:
    """What one provider attempt asks for; no identity ever comes from here."""

    model: Any
    purpose: str
    stage: str
    requires_capabilities: frozenset = frozenset({"text"})
    uses_tools: bool = False
    has_media: bool = False
    requested_output_tokens: Optional[int] = None
    execution: Optional[Mapping[str, Any]] = None
    dispatch_id: Optional[str] = None


@dataclass(frozen=True)
class DispatchContext:
    """The four distinct subjects and the version evidence of one attempt (§2)."""

    actor_user_id: Optional[int] = None
    tenant_id: Optional[int] = None
    plan_holder_user_id: Optional[int] = None
    catalog_id: Optional[str] = None
    credential_owner_user_id: Optional[int] = None
    funding_class: str = FUNDING_CLASS_PLATFORM
    payer: Optional[str] = None
    delegation_consent_version: Optional[int] = None
    plan_policy_version: Optional[int] = None
    credential_version: Optional[int] = None


@dataclass(frozen=True)
class DispatchAdmission:
    """One admitted attempt: its identity, its context and its reservation."""

    dispatch_id: str
    context: DispatchContext
    request: DispatchRequest
    reserved: bool = False
    reservation: Optional[Mapping[str, Any]] = None


# ── Injection interfaces (§7) ─────────────────────────────────


class GrantResolver(Protocol):
    """P21-T13: catalog access, plan grant and capability fit for the holder."""

    async def resolve(self, request: DispatchRequest, context: DispatchContext) -> Any: ...


class RateAcquirer(Protocol):
    """P21-T14: acquire provider capacity for one attempt and release it after."""

    async def acquire(self, request: DispatchRequest, context: DispatchContext) -> Any: ...

    async def release(self, admission: DispatchAdmission) -> None: ...


class AdmissionService(Protocol):
    """P21-T15: commit the durable reservation and its receipt, or deny."""

    async def reserve(self, request: DispatchRequest, context: DispatchContext) -> Any: ...


class SettlementService(Protocol):
    """P21-T16: settle actual spend, release the rest or retain unknown liability."""

    async def settle(
        self, admission: DispatchAdmission, *, outcome: str, evidence: Optional[Mapping[str, Any]] = None
    ) -> None: ...


@dataclass(frozen=True)
class DispatchFoundation:
    """The injected foundations; every one is optional and absent = denial in strict mode."""

    grants: Optional[GrantResolver] = None
    rate: Optional[RateAcquirer] = None
    admission: Optional[AdmissionService] = None
    settlement: Optional[SettlementService] = None


# ── Helpers ───────────────────────────────────────────────────


_HEX32 = re.compile(r"^[0-9a-f]{32}$")


def is_valid_dispatch_id(value: Any) -> bool:
    """True for a canonical attempt identity: 32 lowercase hex characters."""
    return type(value) is str and bool(_HEX32.match(value))


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def _row_provider(model: Any) -> Any:
    return getattr(model, "provider", None)


def _row_identifiers(model: Any) -> tuple[Any, Any]:
    row_id = getattr(model, "id", None)
    provider = _row_provider(model)
    provider_id = getattr(provider, "id", None)
    return row_id, provider_id


def _row_capabilities(model: Any) -> Set[str]:
    capabilities = getattr(model, "capabilities", None)
    if isinstance(capabilities, (set, frozenset, list, tuple)):
        return {cap for cap in capabilities if isinstance(cap, str)}
    # Protocol-style rows may expose the predicate instead of the list.
    can_handle = getattr(model, "can_handle", None)
    if callable(can_handle):
        return {cap for cap in ("text", "image", "video", "embedding") if can_handle(cap)}
    return set()


def _row_api_format(model: Any) -> str:
    return str(getattr(_row_provider(model), "api_format", "") or "").strip().lower()


async def _call_service(service: Any, method: str, *args: Any, detail: str, fallback_code: str, **kwargs: Any) -> Any:
    """Call one foundation method; its own denial survives, anything else is mapped redacted."""
    try:
        return await getattr(service, method)(*args, **kwargs)
    except DispatchDenial:
        raise
    except asyncio.CancelledError:
        raise
    except Exception:
        # Never stringify the exception: its text may carry a provider body.
        raise DispatchDenial(fallback_code, detail) from None


def _identity_for(request: DispatchRequest) -> str:
    """The attempt identity: uuid4().hex when absent, a validated one otherwise (§5)."""
    given = request.dispatch_id
    if given is None:
        return uuid4().hex
    if not is_valid_dispatch_id(given):
        raise DispatchDenial(CODE_PARAMS_INVALID, "dispatch_id_invalid")
    return given


# ── Resolution (§1) ───────────────────────────────────────────


async def admit(
    request: DispatchRequest,
    *,
    foundation: Optional[DispatchFoundation] = None,
    context: Optional[DispatchContext] = None,
) -> DispatchAdmission:
    """Resolve one provider attempt in the contract order, failing closed.

    `foundation=None` is the compatibility mode: the identity facts come from
    the given `context`, no foundation service is required and nothing is
    reserved. A provided foundation is strict: every step below is enforced and
    an absent or failing service denies before HTTP.
    """
    if not isinstance(request, DispatchRequest):
        raise DispatchDenial(CODE_PARAMS_INVALID, "request_invalid")
    strict = foundation is not None
    ctx = context if isinstance(context, DispatchContext) else DispatchContext()

    if not strict:
        # Compatibility mode (§9): today's path stays the authority. The seam
        # only allocates the attempt identity and carries the facts it can
        # prove; it never denies, so no legacy behaviour changes.
        return DispatchAdmission(
            dispatch_id=_identity_for(request),
            context=ctx,
            request=request,
            reserved=False,
            reservation=None,
        )

    # 1. Authenticated actor.
    if ctx.actor_user_id is None:
        raise DispatchDenial(CODE_ACTOR_UNRESOLVED, "step1_actor")
    if not _positive_int(ctx.actor_user_id):
        raise DispatchDenial(CODE_ACTOR_UNRESOLVED, "step1_actor")
    # 2. Verified workspace scope.
    if ctx.tenant_id is not None and not _positive_int(ctx.tenant_id):
        raise DispatchDenial(CODE_WORKSPACE_RIGHTS_MISSING, "step2_tenant")
    # 3. Owner-configured model choice: a readable provider row.
    row_id, provider_id = _row_identifiers(request.model)
    if not _positive_int(row_id) or not _positive_int(provider_id):
        raise DispatchDenial(CODE_PARAMS_INVALID, "step3_model")
    # A row with an id but no usable provider adapter cannot serve any route.
    if not _row_api_format(request.model):
        raise DispatchDenial(CODE_PARAMS_INVALID, "step3_model")

    # The workspace class is reserved and never selectable (§3).
    if ctx.funding_class == FUNDING_CLASS_WORKSPACE:
        raise DispatchDenial(CODE_FUNDING_CLASS_UNSUPPORTED, "class_workspace")
    if ctx.funding_class not in (FUNDING_CLASS_PLATFORM, FUNDING_CLASS_PERSONAL):
        raise DispatchDenial(CODE_FUNDING_CLASS_UNSUPPORTED, "class_unknown")

    # 4. Catalog access, plan grant and the sponsoring holder.
    if not _positive_int(ctx.plan_holder_user_id):
        raise DispatchDenial(CODE_PLAN_HOLDER_UNRESOLVED, "step4_holder")
    grants = foundation.grants
    if grants is None:
        raise DispatchDenial(CODE_PLAN_GRANT_MISSING, "step4_grants")
    await _call_service(
        grants,
        "resolve",
        request,
        ctx,
        detail="step4_grants",
        fallback_code=CODE_PLAN_GRANT_MISSING,
    )
    # 5. Credential source for the funding class.
    credential_owner = ctx.credential_owner_user_id
    if not _positive_int(credential_owner):
        raise DispatchDenial(CODE_CREDENTIAL_SOURCE_UNAVAILABLE, "step5_credential")
    if ctx.funding_class == FUNDING_CLASS_PERSONAL and credential_owner != ctx.plan_holder_user_id:
        # Delegated BYOK needs explicit holder consent; the seam has no
        # consent store yet (gap G7), so an unproven delegate has no source.
        if not _positive_int(ctx.delegation_consent_version):
            raise DispatchDenial(CODE_CREDENTIAL_SOURCE_UNAVAILABLE, "step5_consent")
    # 6. Trusted payer binding.
    if not (type(ctx.payer) is str and ctx.payer):
        raise DispatchDenial(CODE_PAYER_UNRESOLVED, "step6_payer")

    # 7. Parameter and capability fit of the row for this route.
    required = request.requires_capabilities or frozenset()
    if not isinstance(required, (set, frozenset)) or not all(type(cap) is str for cap in required):
        raise DispatchDenial(CODE_PARAMS_INVALID, "step7_request")
    api_format = _row_api_format(request.model)
    if request.stage in ("chat", "text_analysis") and api_format == "custom":
        if not getattr(request.model, "custom_endpoint_path", None):
            raise DispatchDenial(CODE_PARAMS_INVALID, "step7_endpoint")
    if type(request.requested_output_tokens) is int and request.requested_output_tokens <= 0:
        raise DispatchDenial(CODE_PARAMS_INVALID, "step7_output_tokens")
    if required and not required <= _row_capabilities(request.model):
        raise DispatchDenial(CODE_CAPABILITY_UNFIT, "step7_capability")

    dispatch_id = _identity_for(request)
    rate = foundation.rate
    admission_service = foundation.admission

    # 7b. Shared allowance eligibility, then provider rate capacity.
    if type(ctx.plan_policy_version) is not int:
        raise DispatchDenial(CODE_ALLOWANCE_UNAVAILABLE, "step7_allowance")
    if rate is None:
        raise DispatchDenial(CODE_CAPACITY_UNAVAILABLE, "step7_rate")
    await _call_service(
        rate,
        "acquire",
        request,
        ctx,
        detail="step7_rate",
        fallback_code=CODE_CAPACITY_UNAVAILABLE,
    )
    # 8. Durable atomic reservation/receipt.
    if admission_service is None:
        raise DispatchDenial(CODE_ADMISSION_UNAVAILABLE, "step8_admission")
    reservation = await _call_service(
        admission_service,
        "reserve",
        request,
        ctx,
        detail="step8_admission",
        fallback_code=CODE_ADMISSION_UNAVAILABLE,
    )
    # 8b. Freshness: the credential version must not have changed after admission.
    reserved_version = _reserved_credential_version(reservation)
    if reserved_version is not None and reserved_version != ctx.credential_version:
        raise DispatchDenial(CODE_CREDENTIAL_VERSION_STALE, "step8_version")
    return DispatchAdmission(
        dispatch_id=dispatch_id,
        context=ctx,
        request=request,
        reserved=True,
        reservation={
            "value": reservation,
            "credential_version": ctx.credential_version,
            "payer": ctx.payer,
            "funding_class": ctx.funding_class,
        },
    )


def _reserved_credential_version(reservation: Any) -> Any:
    """The credential version a reservation was committed under, when it reports one."""
    if isinstance(reservation, DispatchAdmission):
        return reservation.context.credential_version
    if isinstance(reservation, Mapping):
        return reservation.get("credential_version")
    return getattr(reservation, "credential_version", None)


async def settle(
    admission: DispatchAdmission,
    *,
    outcome: str,
    evidence: Optional[Mapping[str, Any]] = None,
    foundation: Optional[DispatchFoundation] = None,
) -> None:
    """Terminal accounting of one admitted attempt; never breaks the caller.

    A settlement failure is logged with a bounded warning exactly like
    `journal_scoped_attempts` does: no exception text, no provider body, no
    credential. Cancellation is re-raised so the caller's cancellation is
    preserved.
    """
    service = foundation.settlement if isinstance(foundation, DispatchFoundation) else None
    if service is None or not isinstance(admission, DispatchAdmission) or not admission.reserved:
        return
    try:
        await service.settle(admission, outcome=outcome, evidence=evidence)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("llm_dispatch_settlement_failed outcome=%s", outcome)


# ── Funding class and fallback compatibility (§3, §5) ─────────


def resolve_funding_class(model: Any, *, catalog_owner_id: Optional[int]) -> str:
    """Personal when the row and its provider belong to the catalog owner; else platform.

    Pure and never returns the reserved workspace class.
    """
    if not _positive_int(catalog_owner_id):
        return FUNDING_CLASS_PLATFORM
    provider = _row_provider(model)
    if (
        getattr(model, "owner_user_id", None) == catalog_owner_id
        and provider is not None
        and getattr(provider, "owner_user_id", None) == catalog_owner_id
    ):
        return FUNDING_CLASS_PERSONAL
    return FUNDING_CLASS_PLATFORM


def require_same_funding_class(
    previous_model: Any,
    following_model: Any,
    *,
    catalog_owner_id: Optional[int],
    payer: Optional[str],
    next_payer: Optional[str] = None,
) -> None:
    """Refuse a fallback that would switch the funding class or the trusted payer.

    The class is resolved for both rows against the same acting catalog owner,
    so a managed/BYOK switch is a denial (`fallback_funding_class_mismatch`).
    The payer is the server-side binding of the attempt, not a row attribute:
    only two proven bindings can differ, so an unproven payer is never a
    mismatch here. A strict attempt without a proven payer is already denied
    earlier as `payer_unresolved` (step 6). `next_payer` is the binding the
    next try would use; without it the next try keeps the attempt's payer.
    """
    if (
        previous_model is not None
        and following_model is not None
        and _row_identifiers(previous_model) == _row_identifiers(following_model)
    ):
        # The same row retried is the same class and the same payer by definition.
        return
    previous = resolve_funding_class(previous_model, catalog_owner_id=catalog_owner_id)
    following = resolve_funding_class(following_model, catalog_owner_id=catalog_owner_id)
    if previous != following:
        raise DispatchDenial(CODE_FALLBACK_FUNDING_CLASS_MISMATCH, "fallback_class")
    if next_payer is None:
        # The next try keeps the attempt's payer binding: nothing to compare.
        return
    if type(payer) is not str or not payer:
        raise DispatchDenial(CODE_FALLBACK_PAYER_MISMATCH, "fallback_payer")
    if next_payer != payer:
        raise DispatchDenial(CODE_FALLBACK_PAYER_MISMATCH, "fallback_payer")


# ── Compatibility adapter (§9) ────────────────────────────────
# The clients call `dispatch_attempt` once per provider attempt. With the flag
# off this is a behaviour-preserving adapter over `admit`; with it on the
# attempt is strict and an unwired foundation denies before HTTP.

#: Private key that carries the seam identity of a chat try to the journal.
CHAT_DISPATCH_ID_KEY = "_dispatch_id"


def seam_enabled() -> bool:
    """Whether the strict seam is activated; dormant until the foundations exist."""
    from app.core.config import settings

    return bool(getattr(settings, "LLM_DISPATCH_SEAM_ENABLED", False))


def _compat_foundation() -> Optional[DispatchFoundation]:
    """The injected foundations; none is wired by this task.

    Kept as the single wiring point so P21-T13...P21-T16 only have to populate
    it. Absent entries stay absent and are therefore denials in strict mode.
    """
    return DispatchFoundation()


def _compat_route_capabilities(
    *,
    has_media: bool,
    uses_tools: bool,
    requires_capabilities: Optional[Any],
    stage: str,
) -> frozenset:
    """Capabilities the route needs: text always, image when media must be read."""
    if requires_capabilities is not None:
        return frozenset(str(cap) for cap in requires_capabilities)
    capabilities = {"text"}
    if has_media:
        capabilities.add("image")
    return frozenset(capabilities)


def _compat_catalog_owner_id() -> Optional[int]:
    """The acting catalog owner, or None when this call runs outside one."""
    try:
        from app.core.personal_catalog_context import current_catalog_owner_id

        owner = current_catalog_owner_id()
    except Exception:
        return None
    return owner if type(owner) is int and owner > 0 else None


def _compat_tenant_id() -> Optional[int]:
    try:
        from app.core.tenant_context import current_tenant_id

        tenant_id = current_tenant_id()
    except Exception:
        return None
    return tenant_id if type(tenant_id) is int and tenant_id > 0 else None


def _compat_request(
    client: Any,
    *,
    purpose: str,
    stage: str,
    execution: Optional[Mapping[str, Any]],
    has_media: bool,
    uses_tools: bool,
    requested_output_tokens: Optional[int],
    requires_capabilities: Optional[Any],
    dispatch_id: Optional[str],
) -> DispatchRequest:
    return DispatchRequest(
        model=getattr(client, "model", None),
        purpose=str(purpose),
        stage=str(stage),
        requires_capabilities=_compat_route_capabilities(
            has_media=has_media,
            uses_tools=uses_tools,
            requires_capabilities=requires_capabilities,
            stage=stage,
        ),
        uses_tools=bool(uses_tools),
        has_media=bool(has_media),
        requested_output_tokens=requested_output_tokens if type(requested_output_tokens) is int else None,
        execution=dict(execution) if isinstance(execution, Mapping) else None,
        dispatch_id=dispatch_id,
    )


def _compat_context(client: Any, request: DispatchRequest) -> DispatchContext:
    """Identity facts today's code can prove; every probe degrades to None."""
    model = request.model
    provider = getattr(model, "provider", None)
    actor = _compat_catalog_owner_id()
    tenant_id = _compat_tenant_id()
    row_owner = getattr(model, "owner_user_id", None)
    provider_owner = getattr(provider, "owner_user_id", None)

    def positive(value: Any) -> Optional[int]:
        return value if type(value) is int and value > 0 else None

    credential_owner = positive(row_owner) or positive(provider_owner)
    # Compatibility keeps today's shared-allowance behaviour: the workspace
    # scope is the payer binding the legacy path already used.
    payer = f"tenant:{tenant_id}" if tenant_id is not None else None
    return DispatchContext(
        actor_user_id=actor if actor is not None else credential_owner,
        tenant_id=tenant_id,
        plan_holder_user_id=actor,
        catalog_id=None,
        credential_owner_user_id=credential_owner,
        funding_class=resolve_funding_class(model, catalog_owner_id=actor),
        payer=payer,
    )


def _compat_missing_foundation(foundation: Optional[DispatchFoundation]) -> Optional[DispatchDenial]:
    """First absent pre-HTTP foundation in the deterministic grants->rate->admission order."""
    if foundation.grants is None:
        return DispatchDenial(CODE_PLAN_GRANT_MISSING, "step4_grants")
    if foundation.rate is None:
        return DispatchDenial(CODE_CAPACITY_UNAVAILABLE, "step7_rate")
    if foundation.admission is None:
        return DispatchDenial(CODE_ADMISSION_UNAVAILABLE, "step8_admission")
    return None


async def dispatch_attempt(
    client: Any,
    *,
    purpose: str,
    stage: str,
    execution: Optional[Mapping[str, Any]] = None,
    has_media: bool = False,
    uses_tools: bool = False,
    requested_output_tokens: Optional[int] = None,
    requires_capabilities: Optional[Any] = None,
    dispatch_id: Optional[str] = None,
) -> DispatchAdmission:
    """Admit one provider attempt; the single seam entry of every client method.

    A caller-supplied identity must be canonical; an absent one is allocated
    here so every try - including every fallback try - has its own.
    """
    if dispatch_id is not None and not is_valid_dispatch_id(dispatch_id):
        raise DispatchDenial(CODE_PARAMS_INVALID, "dispatch_id_invalid")
    request = _compat_request(
        client,
        purpose=purpose,
        stage=stage,
        execution=execution,
        has_media=has_media,
        uses_tools=uses_tools,
        requested_output_tokens=requested_output_tokens,
        requires_capabilities=requires_capabilities,
        dispatch_id=dispatch_id,
    )
    if not seam_enabled():
        # Compatibility mode: today's path stays the authority, so an absent
        # identity fact is carried as-is and never turned into a denial.
        context = _compat_context(client, request)
        return await admit(request, foundation=None, context=context)

    foundation = _compat_foundation()
    context = _compat_context(client, request)
    # Settlement is not a pre-HTTP blocker: the terminal evidence runs after
    # the request and settle() already tolerates an absent service. Only the
    # services that decide whether the request may be sent deny here.
    missing = _compat_missing_foundation(foundation)
    if missing is not None:
        logger.warning("llm_dispatch_denied code=%s detail=%s", missing.code, missing.detail)
        raise missing
    try:
        return await admit(request, foundation=foundation, context=context)
    except DispatchDenial as denial:
        logger.warning("llm_dispatch_denied code=%s detail=%s", denial.code, denial.detail)
        raise
