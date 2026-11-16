"""P21-T13 increment B: funding-aware catalog grants for the P21-T12 dispatch seam.

This module is the `grant_resolver` of the one dispatch seam
(`app/services/ai/dispatch_seam.py`, step 4). It answers one question per
attempt: *may this actor reach this exact model row through this funding class,
under the sponsoring holder's fresh policy?* It never resolves an actor, a
credential or a payer — that is the authority's step 1/5/6 job — and it never
imports a provider client, a cipher, a managed-secret reader or HTTP.

Rules it enforces, all fail-closed into a redacted `DispatchDenial`:

* every call re-reads the holder context (`resolve_holder_context`) — no
  snapshot, no actor-plan fallback, no trust in `context.workspace_plan`;
* the row must belong to a real catalog with matching provider custody, and be
  active itself with an active provider;
* personal: the fresh holder owns both catalog and rows, and a non-holder actor
  must present a positive delegation consent version;
* platform: the holder's plan must carry an active `PlanModelGrant` for **every**
  required capability, with platform owners NULL and platform custody kind;
* in both classes every required capability must be supported by the row, and
  the row's **whole** capability set must be inside the holder policy's
  `resource_limits["model_types"]` list — that list, not a hardcoded modality
  rule, is the authority on what a plan may route;
* module requirements requested by the server are checked through the tenancy
  registry (`grants_capability`) against the fresh policy's `module_grants`.

`eligible_metadata` lists what a caller may offer; it is presentation data, never
an authorization, and `resolve` does not consult it or the cache.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from sqlalchemy import and_, select

from app.models.llm.llm_catalog import LLMCatalog
from app.models.llm.llm_model import LLMModel
from app.models.llm.llm_provider import LLMProvider
from app.models.llm.plan_model_grant import GRANT_CAPABILITIES, PlanModelGrant
from app.services.tenancy.capabilities import CAPABILITIES
from app.services.tenancy.plan_catalog import grants_capability
from app.services.tenancy.service_plan_policy import ValidatedPlanPolicy, WorkspacePlanContext, resolve_holder_context

from .dispatch_seam import (
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
    _row_api_format,
    _row_capabilities,
    _row_identifiers,
)
from .grant_metadata_cache import (
    CatalogMetadataError,
    CatalogModelMetadata,
    MetadataRevisionCache,
    build_answer,
    safe_revision,
)

#: Module-level marker used by the task's source-only test registry.
__source_only__ = True

#: The holder-policy list that bounds every routable capability, not a new rule.
_POLICY_CAPABILITY_KEY = "model_types"
#: Prices are read exactly; `Decimal` is not a provider payload.
_PRICE_MAX_DIGITS = 18
_DECIMAL_WIDTH = 8


class CatalogGrantError(ValueError):
    """A malformed server-side input; the resolver maps it to a denial code."""

    def __init__(self, reason: str, code: str = CODE_PARAMS_INVALID):
        self.reason = reason
        self.code = code
        super().__init__(reason)


def _positive_int(value: Any) -> bool:
    """A positive database identity; `bool` and `float` never pass."""
    return type(value) is int and value > 0


def _deny(code: str, detail: Optional[str] = None) -> DispatchDenial:
    """Every failure leaves as a fixed-label denial, never an exception body."""
    return DispatchDenial(code, detail)


def _safe_capability_set(capabilities: Any, *, code: str = CODE_PARAMS_INVALID) -> frozenset[str]:
    """Non-empty frozenset of stored model capabilities; anything else denies."""
    if not isinstance(capabilities, frozenset) or not capabilities:
        raise CatalogGrantError("invalid_capabilities", code)
    if any(type(cap) is not str or cap not in GRANT_CAPABILITIES for cap in capabilities):
        raise CatalogGrantError("unknown_capability", code)
    return capabilities


def _row_capability_set(model: Any) -> frozenset[str]:
    """The row's whole capability set, restated as a stored-capability subset."""
    caps = _row_capabilities(model)
    if not caps or any(cap not in GRANT_CAPABILITIES for cap in caps):
        raise CatalogGrantError("row_capability_invalid", CODE_CATALOG_ACCESS_MISSING)
    return frozenset(caps)


def _required_capabilities(request: DispatchRequest) -> frozenset[str]:
    """The caller's requirement, accepted only as a non-empty frozenset."""
    required = getattr(request, "requires_capabilities", None)
    return _safe_capability_set(required)


def _row_capability_set_from_row(row: Mapping[str, Any]) -> frozenset[str]:
    """The stored capability set of one custody row, closed over the registry."""
    caps = row["capabilities"]
    if not isinstance(caps, (list, tuple)) or not caps:
        raise CatalogGrantError("row_capability_invalid", CODE_CATALOG_ACCESS_MISSING)
    if any(type(cap) is not str or cap not in GRANT_CAPABILITIES for cap in caps):
        raise CatalogGrantError("row_capability_invalid", CODE_CATALOG_ACCESS_MISSING)
    return frozenset(caps)


def _policy_capability_list(policy: Any) -> frozenset[str]:
    """The holder policy's allowed capability list: the single modality authority."""
    limits = getattr(policy, "resource_limits", None)
    if not isinstance(limits, Mapping):
        raise CatalogGrantError("missing_resource_limits", CODE_PLAN_GRANT_MISSING)
    allowed = limits.get(_POLICY_CAPABILITY_KEY)
    if isinstance(allowed, (list, tuple, set, frozenset)) and allowed:
        if any(type(item) is not str for item in allowed):
            raise CatalogGrantError("invalid_policy_capabilities", CODE_PLAN_GRANT_MISSING)
        return frozenset(allowed)
    raise CatalogGrantError("missing_policy_capabilities", CODE_PLAN_GRANT_MISSING)


def _context_holder(context: Any) -> tuple[int, int, str]:
    """Positive `(actor, tenant, funding_class)` from the typed attempt context."""
    if not isinstance(context, DispatchContext):
        raise CatalogGrantError("invalid_context", CODE_ACTOR_UNRESOLVED)
    if not _positive_int(context.actor_user_id):
        raise CatalogGrantError("actor_unresolved", CODE_ACTOR_UNRESOLVED)
    if not _positive_int(context.tenant_id):
        raise CatalogGrantError("tenant_unresolved", CODE_CATALOG_ACCESS_MISSING)
    funding = context.funding_class
    if funding not in (FUNDING_CLASS_PLATFORM, FUNDING_CLASS_PERSONAL):
        raise CatalogGrantError("invalid_funding_class", CODE_CATALOG_ACCESS_MISSING)
    return context.actor_user_id, context.tenant_id, funding


def _holder_policy(context: WorkspacePlanContext) -> frozenset[str]:
    policy = getattr(context, "policy", None)
    if policy is None:
        raise CatalogGrantError("missing_policy", CODE_PLAN_GRANT_MISSING)
    return _policy_capability_list(policy)


def _assert_fresh_holder(
    context: Any,
    fresh: Any,
    *,
    actor_user_id: int,
    tenant_id: int,
) -> WorkspacePlanContext:
    """The handed snapshot must agree with the fresh holder read, field by field.

    `DispatchContext` itself carries no plan; the plan identity lives on its
    `workspace_plan` snapshot. A dict, a duck-typed object, a snapshot naming
    another holder/plan/version/tenant/actor, or a context whose holder differs
    from the fresh one is a discrepancy: the attempt is denied without echoing
    either snapshot.
    """
    snapshot = getattr(context, "workspace_plan", None)
    if not isinstance(fresh, WorkspacePlanContext) or not isinstance(snapshot, WorkspacePlanContext):
        raise DispatchDenial(CODE_PLAN_GRANT_MISSING, "step4_holder")
    if not _positive_int(fresh.plan_holder_user_id):
        raise DispatchDenial(CODE_PLAN_GRANT_MISSING, "step4_holder")
    if type(fresh.plan_policy_version) is not int or fresh.plan_policy_version <= 0:
        raise DispatchDenial(CODE_PLAN_GRANT_MISSING, "step4_holder")
    if (
        not _positive_int(context.plan_holder_user_id)
        or not _positive_int(context.plan_policy_version)
        or context.plan_policy_version != fresh.plan_policy_version
        or not isinstance(fresh.policy, ValidatedPlanPolicy)
        or fresh.policy.plan_code != fresh.plan_code
        or fresh.policy.policy_version != fresh.plan_policy_version
        or not all(
            _positive_int(value) for value in (snapshot.actor_user_id, snapshot.tenant_id, snapshot.plan_holder_user_id)
        )
        or fresh.actor_user_id != actor_user_id
        or fresh.tenant_id != tenant_id
        or fresh.plan_holder_user_id != context.plan_holder_user_id
        or snapshot.actor_user_id != actor_user_id
        or snapshot.tenant_id != tenant_id
        or snapshot.plan_holder_user_id != fresh.plan_holder_user_id
        or snapshot.plan_code != fresh.plan_code
        or snapshot.plan_policy_version != fresh.plan_policy_version
    ):
        raise DispatchDenial(CODE_PLAN_GRANT_MISSING, "step4_holder")
    return fresh


def _assert_modules(holder: WorkspacePlanContext, required_modules: frozenset[str]) -> None:
    """Server-supplied requirements must be granted by the fresh holder policy."""
    if not required_modules:
        return
    policy = getattr(holder, "policy", None)
    patterns = getattr(policy, "module_grants", None)
    if not isinstance(patterns, tuple) or any(type(p) is not str for p in patterns):
        raise DispatchDenial(CODE_PLAN_GRANT_MISSING, "step4_grants")
    for module in required_modules:
        if not any(grants_capability(pattern, module) for pattern in patterns):
            raise DispatchDenial(CODE_PLAN_GRANT_MISSING, "step4_grants")


def _optional_catalog_id(context: Any) -> Optional[int]:
    """`context.catalog_id` is optional, string-valued and must match the row."""
    raw = getattr(context, "catalog_id", None)
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise CatalogGrantError("invalid_catalog_id", CODE_CATALOG_ACCESS_MISSING) from None
    if str(value) != str(raw) or not _positive_int(value):
        raise CatalogGrantError("invalid_catalog_id", CODE_CATALOG_ACCESS_MISSING)
    return value


def _optional_owner(value: Any) -> Optional[int]:
    """A credential owner proves nothing at step 4, but if present it must be real."""
    if value is None:
        return None
    if not _positive_int(value):
        raise CatalogGrantError("invalid_credential_owner", CODE_CATALOG_ACCESS_MISSING)
    return value


def _decimal_or_none(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        price = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise CatalogGrantError("invalid_price", CODE_CATALOG_ACCESS_MISSING) from None
    if not price.is_finite() or price < 0 or len(price.as_tuple().digits) > _PRICE_MAX_DIGITS:
        raise CatalogGrantError("invalid_price", CODE_CATALOG_ACCESS_MISSING)
    return price if price.as_tuple().exponent >= -_DECIMAL_WIDTH else price.quantize(Decimal(1).scaleb(-_DECIMAL_WIDTH))


def _metadata(
    row: Mapping[str, Any], capabilities: frozenset[str], *, funding_class: str, is_default: bool
) -> CatalogModelMetadata:
    """One safe DTO from a scalar projection; no ciphertext/ref/endpoint reaches it."""
    try:
        return CatalogModelMetadata(
            id=row["id"],
            name=row["name"],
            model_id=row["model_id"],
            provider_id=row["provider_id"],
            provider_name=row["provider_name"],
            provider_uid=row["provider_uid"],
            provider_format=row["provider_format"],
            catalog_id=row["catalog_id"],
            funding_class=funding_class,
            capabilities=tuple(sorted(capabilities)),
            max_tokens=row["max_tokens"],
            max_output_tokens=row["max_output_tokens"],
            context_window_tokens=row["context_window_tokens"],
            default_temperature=row["default_temperature"],
            pricing_state=row["pricing_state"],
            input_price=_decimal_or_none(row["input_price"]),
            output_price=_decimal_or_none(row["output_price"]),
            pricing_token_unit=row["pricing_token_unit"],
            is_active=row["is_active"] is True,
            is_default=is_default,
            priority=row.get("priority"),
        )
    except CatalogMetadataError:
        raise DispatchDenial(CODE_CATALOG_ACCESS_MISSING, "step4_grants") from None


class CatalogGrantResolver:
    """Step-4 grant resolver over one caller-owned `AsyncSession`.

    `required_modules` are server-supplied product modules the calling surface
    needs; they are checked against the fresh holder policy through the tenancy
    capability engine and never define a second registry. `cache` is optional and
    holds safe metadata answers only; `resolve` ignores it entirely.
    """

    def __init__(
        self,
        session: Any,
        *,
        required_modules: Iterable[str] = (),
        cache: Optional[MetadataRevisionCache] = None,
    ):
        modules = frozenset(required_modules or ())
        if any(type(module) is not str or module not in CAPABILITIES for module in modules):
            raise CatalogGrantError("unknown_module")
        self._session = session
        self._required_modules = modules
        if cache is not None and not isinstance(cache, MetadataRevisionCache):
            raise CatalogGrantError("invalid_cache")
        self._cache = cache

    # ── public contract ──────────────────────────────────────────────

    async def resolve(self, request: DispatchRequest, context: DispatchContext) -> DispatchGrant:
        """Prove catalog access, plan grant and capability fit for one attempt.

        Independent of any cache and of `context.workspace_plan`'s contents: the
        holder context is re-read here every call. Denials carry a fixed code and
        label only. The caller's transaction is used as-is — no commit, rollback
        or nested transaction is opened.
        """
        try:
            self._assert_request(request)
            actor, tenant, funding = _context_holder(context)
            fresh = await self._fresh_holder(context, actor, tenant)
        except CatalogGrantError as exc:
            raise _deny(exc.code) from None
        return await self._authorize(request, context, fresh, actor=actor, funding=funding)

    async def eligible_metadata(
        self, context: DispatchContext, capabilities: Iterable[str] = frozenset()
    ) -> tuple[CatalogModelMetadata, ...]:
        """Safe metadata of the models this attempt could reach, deterministically.

        Platform models come from the holder's active grants (one direct query
        joined to platform custody); personal models come from the holder's own
        personal catalog with an equality on the personal kind. The two never
        share an owner-OR-platform predicate. A failed read yields `()` and drops
        the cached entry — a caller cannot serve stale metadata after an error.
        """
        try:
            key, fresh, funding, required, allowed = await self._metadata_scope(context, capabilities)
        except (CatalogGrantError, DispatchDenial):
            # A malformed listing request or a failed holder read yields nothing.
            if self._cache is not None:
                self._cache.clear()
            return ()
        try:
            answer = await self._list_metadata(
                fresh, funding=funding, required=required, allowed=allowed, holder_id=fresh.plan_holder_user_id
            )
        except BaseException:
            if self._cache is not None:
                self._cache.invalidate(key)
            raise
        cached = self._cache.get(key) if self._cache is not None else None
        if cached is not None and cached.revision == answer.revision:
            return cached.metadata
        if self._cache is not None:
            self._cache.store(key, answer)
        return answer.metadata

    @staticmethod
    def metadata_cache_key(
        context: DispatchContext, capabilities: Iterable[str] = frozenset(), *, required_modules: Iterable[str] = ()
    ) -> tuple[Any, ...]:
        """The plan/policy/funding/capability/holder key a listing is cached under."""
        _, tenant, funding = _context_holder(context)
        snapshot = getattr(context, "workspace_plan", None)
        required = _safe_capability_set(capabilities, code=CODE_CATALOG_ACCESS_MISSING)
        return (
            tenant,
            context.plan_holder_user_id,
            getattr(snapshot, "plan_code", None),
            getattr(snapshot, "plan_policy_version", None),
            funding,
            tuple(sorted(required)),
            frozenset(required_modules or ()),
        )

    async def _metadata_scope(self, context: DispatchContext, capabilities: Any):
        actor, tenant, funding = _context_holder(context)
        fresh = await self._fresh_holder(context, actor, tenant)
        required = _safe_capability_set(capabilities, code=CODE_CATALOG_ACCESS_MISSING)
        if funding == FUNDING_CLASS_PERSONAL:
            if _optional_owner(context.credential_owner_user_id) not in (None, fresh.plan_holder_user_id):
                raise CatalogGrantError("credential_owner_mismatch", CODE_CATALOG_ACCESS_MISSING)
            if actor != fresh.plan_holder_user_id and not _positive_int(context.delegation_consent_version):
                raise CatalogGrantError("delegation_missing", CODE_CATALOG_ACCESS_MISSING)
        allowed = _policy_capability_list(fresh.policy)
        key = self.metadata_cache_key(context, required, required_modules=self._required_modules)
        return key, fresh, funding, required, allowed

    # ── validation ───────────────────────────────────────────────────

    def _assert_request(self, request: Any) -> None:
        if not isinstance(request, DispatchRequest):
            raise CatalogGrantError("invalid_request")
        model = request.model
        if model is None:
            raise CatalogGrantError("invalid_model")
        row_id, provider_id = _row_identifiers(model)
        if not _positive_int(row_id) or not _positive_int(provider_id):
            raise CatalogGrantError("invalid_model")
        if _row_api_format(model) == "":
            raise CatalogGrantError("invalid_model")
        _required_capabilities(request)

    async def _fresh_holder(self, context: Any, actor: int, tenant: int) -> WorkspacePlanContext:
        try:
            fresh = await resolve_holder_context(self._session, tenant, actor, lock_holder=False)
        except Exception:
            # Tenancy fails closed with its own bounded reason; never stringify
            # it or let a partial row context escape as a grant.
            raise CatalogGrantError("holder_unresolved", CODE_PLAN_GRANT_MISSING) from None
        _assert_fresh_holder(context, fresh, actor_user_id=actor, tenant_id=tenant)
        _assert_modules(fresh, self._required_modules)
        return fresh

    # ── authorization (fresh, uncached) ──────────────────────────────

    async def _authorize(
        self,
        request: DispatchRequest,
        context: DispatchContext,
        fresh: WorkspacePlanContext,
        *,
        actor: int,
        funding: str,
    ) -> DispatchGrant:
        holder_id = fresh.plan_holder_user_id
        error: Optional[CatalogGrantError] = None
        try:
            required = _required_capabilities(request)
            row_caps = _row_capability_set(request.model)
            if not required <= row_caps:
                raise CatalogGrantError("capability_unsupported", CODE_CATALOG_ACCESS_MISSING)
            model_id, provider_id = _row_identifiers(request.model)
            row = await self._load_row(model_id, provider_id)
            # The row's own stored set, not only the caller's summary, must be
            # inside the holder policy list; a request-side predicate cannot widen it.
            row_capabilities = _row_capability_set_from_row(row)
            if not required <= row_capabilities:
                raise CatalogGrantError("capability_unsupported", CODE_CATALOG_ACCESS_MISSING)
            if not row_capabilities <= _policy_capability_list(fresh.policy):
                raise CatalogGrantError("capability_denied", CODE_PLAN_GRANT_MISSING)
            if funding == FUNDING_CLASS_PERSONAL:
                await self._authorize_personal(context, fresh, row, actor=actor)
            else:
                await self._authorize_platform(fresh, row, required=required)
            catalog_id = _optional_catalog_id(context)
            if catalog_id is not None and catalog_id != int(row["catalog_id"]):
                raise CatalogGrantError("catalog_mismatch", CODE_CATALOG_ACCESS_MISSING)
        except CatalogGrantError as exc:
            error = exc
        except Exception as exc:
            # Never stringify a storage failure: it may carry row or provider text.
            raise _deny(CODE_CATALOG_ACCESS_MISSING) from None
        if error is not None:
            raise _deny(error.code) from None
        return DispatchGrant(catalog_id=str(row["catalog_id"]))

    async def _load_row(self, model_id: int, provider_id: int) -> Mapping[str, Any]:
        """Scalar custody projection: identifiers, owners, kind, activity, caps.

        Explicit columns only — no `BaseManager`, no entity load, no ciphertext,
        managed ref, base URL or auth header is selected.
        """
        stmt = select(
            LLMModel.id.label("id"),
            LLMModel.catalog_id.label("catalog_id"),
            LLMModel.owner_user_id.label("model_owner_id"),
            LLMModel._capabilities_column.label("capabilities"),
            LLMModel.is_active.label("model_is_active"),
            LLMProvider.id.label("provider_id"),
            LLMProvider.catalog_id.label("provider_catalog_id"),
            LLMProvider.owner_user_id.label("provider_owner_id"),
            LLMProvider.is_active.label("provider_is_active"),
            LLMCatalog.id.label("catalog_row_id"),
            LLMCatalog.kind.label("catalog_kind"),
            LLMCatalog.owner_user_id.label("catalog_owner_id"),
            LLMCatalog.is_active.label("catalog_is_active"),
        ).join(
            LLMProvider,
            and_(LLMProvider.id == LLMModel.provider_id, LLMProvider.catalog_id == LLMModel.catalog_id),
        )
        stmt = stmt.join(LLMCatalog, LLMCatalog.id == LLMModel.catalog_id).where(
            LLMModel.id == model_id,
            LLMModel.provider_id == provider_id,
        )
        row = await self._execute_one(stmt, width=13)
        if row is None:
            raise CatalogGrantError("model_missing", CODE_CATALOG_ACCESS_MISSING)
        return row

    async def _execute_one(self, stmt: Any, *, width: int) -> Optional[Mapping[str, Any]]:
        """One strict projection row keyed by column label, or `None`.

        `session.scalar()` on a multi-column statement would return the first
        column only, so projections go through `execute(...).one_or_none()`.
        """
        result = await self._session.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return None
        mapping = getattr(row, "_mapping", None)
        if mapping is None or len(mapping) != width:
            raise CatalogGrantError("invalid_projection", CODE_CATALOG_ACCESS_MISSING)
        return mapping

    def _custody_of(self, row: Mapping[str, Any]) -> tuple[int, str, Optional[int]]:
        """Prove one consistent custody triple; a mismatch never authorizes."""
        catalog_id = row["catalog_id"]
        if (
            not _positive_int(catalog_id)
            or row["provider_catalog_id"] != catalog_id
            or row["catalog_row_id"] != catalog_id
            or row["catalog_kind"] not in (FUNDING_CLASS_PLATFORM, FUNDING_CLASS_PERSONAL)
        ):
            raise CatalogGrantError("custody_mismatch", CODE_CATALOG_ACCESS_MISSING)
        if row["catalog_is_active"] is not True:
            raise CatalogGrantError("inactive_custody", CODE_CATALOG_ACCESS_MISSING)
        if row["provider_is_active"] is not True or row["model_is_active"] is not True:
            raise CatalogGrantError("inactive_custody", CODE_CATALOG_ACCESS_MISSING)
        kind = row["catalog_kind"]
        owner = row["catalog_owner_id"]
        if kind == FUNDING_CLASS_PLATFORM:
            if owner is not None or row["model_owner_id"] is not None or row["provider_owner_id"] is not None:
                raise CatalogGrantError("platform_owner_present", CODE_CATALOG_ACCESS_MISSING)
        elif not _positive_int(owner):
            raise CatalogGrantError("personal_owner_missing", CODE_CATALOG_ACCESS_MISSING)
        if row["model_owner_id"] != owner or row["provider_owner_id"] != owner:
            raise CatalogGrantError("owner_mismatch", CODE_CATALOG_ACCESS_MISSING)
        return int(catalog_id), kind, owner

    async def _authorize_personal(
        self,
        context: DispatchContext,
        fresh: WorkspacePlanContext,
        row: Mapping[str, Any],
        *,
        actor: int,
    ) -> None:
        holder_id = fresh.plan_holder_user_id
        _, kind, owner = self._custody_of(row)
        if kind != FUNDING_CLASS_PERSONAL or owner != holder_id:
            # A platform row never funds a personal attempt, and another user's
            # personal catalog is never reachable through an invited workspace.
            raise CatalogGrantError("personal_catalog_denied", CODE_CATALOG_ACCESS_MISSING)
        if _optional_owner(context.credential_owner_user_id) not in (None, holder_id):
            # Step 5 owns credential custody; a contradicting hint denies here.
            raise CatalogGrantError("credential_owner_mismatch", CODE_CATALOG_ACCESS_MISSING)
        if actor != holder_id and not _positive_int(context.delegation_consent_version):
            raise CatalogGrantError("delegation_missing", CODE_CATALOG_ACCESS_MISSING)

    async def _authorize_platform(
        self,
        fresh: WorkspacePlanContext,
        row: Mapping[str, Any],
        *,
        required: frozenset[str],
    ) -> None:
        holder_id = fresh.plan_holder_user_id
        _, kind, _ = self._custody_of(row)
        if kind != FUNDING_CLASS_PLATFORM:
            raise CatalogGrantError("platform_catalog_denied", CODE_CATALOG_ACCESS_MISSING)
        # The grant names the plan code; the fresh snapshot must describe the same
        # plan for the same holder, so a stale policy cannot borrow another plan.
        if not _positive_int(holder_id) or fresh.policy.plan_code != fresh.plan_code:
            raise CatalogGrantError("plan_mismatch", CODE_PLAN_GRANT_MISSING)
        stmt = select(PlanModelGrant.capability).where(
            PlanModelGrant.plan == fresh.plan_code,
            PlanModelGrant.llm_model_id == row["id"],
            PlanModelGrant.is_active.is_(True),
            PlanModelGrant.capability.in_(sorted(required)),
        )
        try:
            result = await self._session.execute(stmt)
            granted = {value for value in result.scalars().all() if type(value) is str}
        except Exception:
            raise CatalogGrantError("grant_read_failed", CODE_PLAN_GRANT_MISSING) from None
        if not required <= granted:
            raise CatalogGrantError("plan_grant_missing", CODE_PLAN_GRANT_MISSING)

    # ── metadata listing (cached, safe, additive) ────────────────────

    async def _list_metadata(
        self,
        fresh: WorkspacePlanContext,
        *,
        funding: str,
        required: frozenset[str],
        allowed: frozenset[str],
        holder_id: int,
    ):
        if funding == FUNDING_CLASS_PLATFORM:
            rows, grants = await self._platform_metadata(fresh, required=required, allowed=allowed)
        else:
            rows, grants = await self._personal_metadata(holder_id, required=required, allowed=allowed)
        metadata = tuple(
            _metadata(
                row,
                frozenset(row["capabilities"] or ()),
                funding_class=funding,
                is_default=row["is_default"],
            )
            for row in rows
        )
        return build_answer(metadata, grants=grants)

    @staticmethod
    def _metadata_columns() -> tuple[Any, ...]:
        return (
            LLMModel.id.label("id"),
            LLMModel.name.label("name"),
            LLMModel.model_id.label("model_id"),
            LLMModel._capabilities_column.label("capabilities"),
            LLMModel.max_tokens.label("max_tokens"),
            LLMModel.max_output_tokens.label("max_output_tokens"),
            LLMModel.context_window_tokens.label("context_window_tokens"),
            LLMModel.default_temperature.label("default_temperature"),
            LLMModel.pricing_state.label("pricing_state"),
            LLMModel.input_price.label("input_price"),
            LLMModel.output_price.label("output_price"),
            LLMModel.pricing_token_unit.label("pricing_token_unit"),
            LLMModel.is_active.label("is_active"),
            LLMProvider.id.label("provider_id"),
            LLMProvider.name.label("provider_name"),
            LLMProvider.uid.label("provider_uid"),
            LLMProvider.api_format.label("provider_format"),
            LLMModel.catalog_id.label("catalog_id"),
        )

    async def _platform_metadata(
        self, fresh: WorkspacePlanContext, *, required: frozenset[str], allowed: frozenset[str]
    ):
        """Active grants of the holder plan joined to active platform custody."""
        caps = sorted(required)
        columns = self._metadata_columns()
        stmt = (
            select(
                *columns,
                PlanModelGrant.id.label("grant_id"),
                PlanModelGrant.capability.label("grant_capability"),
                PlanModelGrant.is_active.label("grant_is_active"),
                PlanModelGrant.priority.label("priority"),
                PlanModelGrant.is_default.label("is_default"),
            )
            .select_from(PlanModelGrant)
            .join(LLMModel, LLMModel.id == PlanModelGrant.llm_model_id)
            .join(
                LLMProvider,
                and_(LLMProvider.id == LLMModel.provider_id, LLMProvider.catalog_id == LLMModel.catalog_id),
            )
            .join(LLMCatalog, LLMCatalog.id == LLMModel.catalog_id)
            .where(
                PlanModelGrant.plan == fresh.plan_code,
                PlanModelGrant.capability.in_(caps),
                PlanModelGrant.is_active.is_(True),
                LLMModel.owner_user_id.is_(None),
                LLMProvider.owner_user_id.is_(None),
                LLMCatalog.kind == FUNDING_CLASS_PLATFORM,
                LLMCatalog.owner_user_id.is_(None),
                LLMCatalog.is_active.is_(True),
                LLMProvider.is_active.is_(True),
                LLMModel.is_active.is_(True),
            )
            .order_by(PlanModelGrant.is_default.desc(), PlanModelGrant.priority, LLMModel.id)
        )
        rows = await self._execute_all(stmt)
        grants = [
            {
                "grant_id": row["grant_id"],
                "is_active": row["grant_is_active"],
                "priority": row["priority"],
                "is_default": row["is_default"],
            }
            for row in rows
        ]
        # A multi-capability model is offered only when EVERY required grant
        # exists. Collapse per-capability grant rows into one model choice.
        grouped = {}
        for row in rows:
            grouped.setdefault(row["id"], []).append(row)
        choices = []
        for group in grouped.values():
            granted = {row["grant_capability"] for row in group}
            if not required <= granted:
                continue
            choice = dict(group[0])
            choice["is_default"] = all(row["is_default"] for row in group)
            choice["priority"] = max(row["priority"] for row in group)
            choices.append(choice)
        choices.sort(key=lambda row: (not row["is_default"], row["priority"], row["id"]))
        return self._eligible(choices, required=required, allowed=allowed), grants

    async def _personal_metadata(self, holder_id: int, *, required: frozenset[str], allowed: frozenset[str]):
        """The holder's own personal catalog; equality on the personal kind, never OR."""
        columns = self._metadata_columns()
        stmt = (
            select(*columns, LLMModel.is_default.label("is_default"))
            .join(
                LLMProvider,
                and_(LLMProvider.id == LLMModel.provider_id, LLMProvider.catalog_id == LLMModel.catalog_id),
            )
            .join(LLMCatalog, LLMCatalog.id == LLMModel.catalog_id)
            .where(
                LLMCatalog.kind == FUNDING_CLASS_PERSONAL,
                LLMCatalog.owner_user_id == holder_id,
                LLMModel.owner_user_id == holder_id,
                LLMProvider.owner_user_id == holder_id,
                LLMCatalog.is_active.is_(True),
                LLMProvider.is_active.is_(True),
                LLMModel.is_active.is_(True),
            )
            .order_by(LLMModel.is_default.desc(), LLMModel.id)
        )
        rows = await self._execute_all(stmt)
        for row in rows:
            row["priority"] = 100
        return self._eligible(rows, required=required, allowed=allowed), []

    @staticmethod
    def _eligible(
        rows: list[Mapping[str, Any]], *, required: frozenset[str], allowed: frozenset[str]
    ) -> list[Mapping[str, Any]]:
        """Listing filter, not authorization: whole set inside the plan's list."""
        eligible = []
        for row in rows:
            caps = frozenset(row["capabilities"] or ())
            if caps and caps <= allowed and required <= caps:
                eligible.append(row)
        return eligible

    async def _execute_all(self, stmt: Any) -> list[dict[str, Any]]:
        result = await self._session.execute(stmt)
        rows = []
        for row in result.all():
            mapping = getattr(row, "_mapping", None)
            if mapping is None:
                raise CatalogGrantError("invalid_projection", CODE_CATALOG_ACCESS_MISSING)
            rows.append(dict(mapping))
        return rows
