"""Owned-transaction operations for personal AI billing ownership."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import case, func, or_, select

from app.core.permissions import get_current_user
from app.core.tenant_context import current_tenant_id, is_bypass
from app.models.managers.base_manager import BaseManager
from app.types import ActionType


class PersonalBillingError(PermissionError):
    """Billing ownership or consent could not be established safely."""


class PersonalBillingUnavailable(RuntimeError):
    """The account is missing, inactive, or not explicitly configured."""


POLICY_VERSION = "personal-daily-cap-v1"
MAX_USD = Decimal("999999999.999999999")


def validated_daily_limit(value: Decimal | int | str) -> Decimal:
    """Return a bounded exact cap; zero is an explicit unlimited setting."""
    if type(value) not in (Decimal, int, str):
        raise ValueError("Daily limit must be a decimal amount")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Daily limit must be a decimal amount") from exc
    if not amount.is_finite() or amount < 0 or amount > MAX_USD:
        raise ValueError("Daily limit is outside the supported USD range")
    if amount != amount.quantize(Decimal("0.000000001")):
        raise ValueError("Daily limit supports at most nine decimal places")
    return amount


class PersonalBillingManager(BaseManager):
    def __init__(self):
        from ..billing import PersonalBillingAccount

        super().__init__(PersonalBillingAccount)

    @staticmethod
    def _actor_id() -> int:
        actor = get_current_user()
        if actor is None or type(getattr(actor, "id", None)) is not int or actor.id <= 0:
            raise PersonalBillingError("Authenticated user context is required")
        return actor.id

    async def configure_self(self, daily_limit_usd: Decimal | int | str):
        """Configure only the caller's account, including explicit zero=unlimited."""
        from ..billing.billing import PersonalBillingAccount
        from ..identity.user import User

        actor_id = self._actor_id()
        amount = validated_daily_limit(daily_limit_usd)
        async with self._get_owned_session() as session:
            user = (
                await session.execute(
                    select(User).where(User.id == actor_id, User.is_active.is_(True)).with_for_update()
                )
            ).scalar_one_or_none()
            if user is None:
                raise PersonalBillingError("Active account owner is required")
            account = (
                await session.execute(
                    select(PersonalBillingAccount).where(PersonalBillingAccount.user_id == actor_id).with_for_update()
                )
            ).scalar_one_or_none()
            now = datetime.now(timezone.utc)
            if account is None:
                account = PersonalBillingAccount(
                    user_id=actor_id,
                    daily_limit_usd=amount,
                    is_active=True,
                    configured_at=now,
                    policy_version=POLICY_VERSION,
                )
                session.add(account)
            else:
                account.daily_limit_usd = amount
                account.is_active = True
                account.configured_at = now
                account.policy_version = POLICY_VERSION
            await session.flush()
            return account

    async def self_account(self):
        """Return the caller's configured account, or None without writing on a read."""
        from ..billing.billing import PersonalBillingAccount
        from ..identity.user import User

        actor_id = self._actor_id()
        async with self._get_owned_session() as session:
            user = (
                await session.execute(select(User).where(User.id == actor_id, User.is_active.is_(True)))
            ).scalar_one_or_none()
            if user is None:
                raise PersonalBillingError("Active account owner is required")
            return (
                await session.execute(select(PersonalBillingAccount).where(PersonalBillingAccount.user_id == actor_id))
            ).scalar_one_or_none()

    async def self_spend_summary(self, utc_day: date | None = None) -> dict:
        """Report today's payer-bound dispatch costs by workspace, never projections.

        Provider amounts and receipt-tariff reconstruction remain separate;
        open reservations and unknown events are visible. Historical attempts
        without reviewed payer attribution are explicitly outside this report.
        """
        from ..billing.billing import PersonalBillingAccount
        from ..billing.dispatch import PersonalLLMDispatch
        from ..billing.ledger import PersonalLLMReservation, PersonalLLMReservationState
        from ..tenancy.tenant import Tenant
        from ..llm.llm_outcome_event import LLMOutcomeEvent

        actor_id = self._actor_id()
        day = utc_day or datetime.now(timezone.utc).date()
        if type(day) is not date:
            raise ValueError("A UTC billing date is required")
        zero = Decimal(0)

        async with self._get_owned_session() as session:
            account_id = (
                await session.execute(
                    select(PersonalBillingAccount.id).where(PersonalBillingAccount.user_id == actor_id)
                )
            ).scalar_one_or_none()
            if account_id is None:
                rows = []
            else:
                settled = func.coalesce(func.sum(PersonalLLMReservationState.settled_usd), zero)
                reserved = func.coalesce(func.sum(PersonalLLMReservationState.reserved_usd), zero)
                provider_reported = func.coalesce(
                    func.sum(
                        case(
                            (
                                LLMOutcomeEvent.price_source == "provider",
                                LLMOutcomeEvent.cost_usd,
                            ),
                            else_=zero,
                        )
                    ),
                    zero,
                )
                tariff_reconstructed = func.coalesce(
                    func.sum(
                        case(
                            (
                                LLMOutcomeEvent.price_source == "receipt_tariff",
                                LLMOutcomeEvent.cost_usd,
                            ),
                            else_=zero,
                        )
                    ),
                    zero,
                )
                pending_count = func.count().filter(PersonalLLMReservationState.status == "reserved")
                unknown_count = func.count().filter(
                    or_(
                        LLMOutcomeEvent.cost_certainty == "undefined",
                        (
                            LLMOutcomeEvent.event_id.is_(None)
                            & PersonalLLMReservation.ceiling_state.in_(("undefined", "unbounded"))
                        ),
                    )
                )
                statement = (
                    select(
                        PersonalLLMDispatch.tenant_id,
                        Tenant.name,
                        func.count(PersonalLLMDispatch.id),
                        settled,
                        reserved,
                        provider_reported,
                        tariff_reconstructed,
                        pending_count,
                        unknown_count,
                    )
                    .select_from(PersonalLLMDispatch)
                    .join(
                        PersonalLLMReservationState,
                        PersonalLLMReservationState.dispatch_id == PersonalLLMDispatch.dispatch_id,
                    )
                    .join(
                        PersonalLLMReservation,
                        PersonalLLMReservation.dispatch_id == PersonalLLMDispatch.dispatch_id,
                    )
                    .join(Tenant, Tenant.id == PersonalLLMDispatch.tenant_id)
                    .outerjoin(
                        LLMOutcomeEvent,
                        LLMOutcomeEvent.event_id == PersonalLLMReservationState.latest_event_id,
                    )
                    .where(
                        PersonalLLMDispatch.payer_user_id == actor_id,
                        PersonalLLMDispatch.account_id == account_id,
                        PersonalLLMDispatch.utc_day == day,
                    )
                    .group_by(PersonalLLMDispatch.tenant_id, Tenant.name)
                    .order_by(PersonalLLMDispatch.tenant_id)
                )
                rows = (await session.execute(statement)).all()

        by_workspace = []
        totals = {
            "dispatch_count": 0,
            "settled_usd": zero,
            "reserved_usd": zero,
            "provider_reported_usd": zero,
            "tariff_reconstructed_usd": zero,
            "pending_count": 0,
            "unknown_count": 0,
        }
        for row in rows:
            values = {
                "tenant_id": int(row[0]),
                "workspace": str(row[1]),
                "dispatch_count": int(row[2]),
                "settled_usd": Decimal(row[3] or zero),
                "reserved_usd": Decimal(row[4] or zero),
                "provider_reported_usd": Decimal(row[5] or zero),
                "tariff_reconstructed_usd": Decimal(row[6] or zero),
                "pending_count": int(row[7] or 0),
                "unknown_count": int(row[8] or 0),
            }
            by_workspace.append(values)
            for key in totals:
                totals[key] += values[key]
        return {
            "scope": "payer_bound_personal_dispatches",
            "utc_day": day.isoformat(),
            "currency": "USD",
            **{
                key: str(value) if isinstance(value, Decimal) else value
                for key, value in totals.items()
            },
            "estimated_usd": None,
            "legacy_history_included": False,
            "complete": totals["pending_count"] == 0 and totals["unknown_count"] == 0,
            "by_workspace": [
                {
                    key: str(value) if isinstance(value, Decimal) else value
                    for key, value in workspace.items()
                }
                for workspace in by_workspace
            ],
        }

    async def consent_task_payer(self, task_id: int):
        """Bind the caller as a task's payer after fresh user, membership and task checks."""
        from app.core.access_catalog import workspace_grants

        from ..billing.billing import PersonalBillingAccount, TaskBillingPayer
        from ..identity.user import User
        from ..scheduling.agent_task import AgentTask
        from ..tenancy.tenant import Tenant
        from ..tenancy.tenant_user import TenantUser
        from ..tenancy.workspace_role import WorkspaceRole

        actor_id = self._actor_id()
        tenant_id = current_tenant_id()
        if type(task_id) is not int or task_id <= 0 or type(tenant_id) is not int or tenant_id <= 0:
            raise PersonalBillingError("Workspace task scope is required")
        async with self._get_owned_session() as session:
            workspace = (
                await session.execute(
                    select(Tenant).where(Tenant.id == tenant_id, Tenant.is_active.is_(True)).with_for_update()
                )
            ).scalar_one_or_none()
            task = (
                await session.execute(
                    select(AgentTask).where(AgentTask.id == task_id, AgentTask.tenant_id == tenant_id).with_for_update()
                )
            ).scalar_one_or_none()
            user = (
                await session.execute(
                    select(User).where(User.id == actor_id, User.is_active.is_(True)).with_for_update()
                )
            ).scalar_one_or_none()
            if user is None or workspace is None or task is None:
                raise PersonalBillingError("Active user and editable workspace task are required")
            roles = (
                (
                    await session.execute(
                        select(WorkspaceRole.codename)
                        .select_from(TenantUser)
                        .join(WorkspaceRole, WorkspaceRole.id == TenantUser.role_id)
                        .where(
                            TenantUser.tenant_id == tenant_id,
                            TenantUser.channel == "web",
                            TenantUser.user_id == actor_id,
                            TenantUser.is_active.is_(True),
                        )
                        .with_for_update(of=TenantUser)
                    )
                )
                .scalars()
                .all()
            )
            if not any(("agenttask", ActionType.UPDATE) in workspace_grants(role) for role in roles):
                raise PersonalBillingError("Fresh workspace task edit permission is required")
            user = (
                await session.execute(
                    select(User).where(User.id == actor_id, User.is_active.is_(True)).with_for_update()
                )
            ).scalar_one_or_none()
            if user is None:
                raise PersonalBillingError("Active account owner is required")
            account = (
                await session.execute(
                    select(PersonalBillingAccount)
                    .where(
                        PersonalBillingAccount.user_id == actor_id,
                        PersonalBillingAccount.is_active.is_(True),
                        PersonalBillingAccount.configured_at.is_not(None),
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if account is None:
                raise PersonalBillingUnavailable("Configure a personal daily limit before consenting to pay")
            latest = (
                await session.execute(
                    select(TaskBillingPayer)
                    .where(TaskBillingPayer.agent_task_id == task_id, TaskBillingPayer.tenant_id == tenant_id)
                    .order_by(TaskBillingPayer.consent_version.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if latest is not None and latest.payer_user_id == actor_id:
                return latest
            version = 1 if latest is None else latest.consent_version + 1
            consent = TaskBillingPayer(
                agent_task_id=task_id,
                tenant_id=tenant_id,
                payer_user_id=actor_id,
                consented_by_user_id=actor_id,
                consent_version=version,
            )
            session.add(consent)
            await session.flush()
            return consent

    async def resolve_task_payer(self, *, task_id: int, tenant_id: int):
        """Worker lookup: resolve stored consent without relying on ambient actor identity."""
        from ..billing.billing import PersonalBillingAccount, TaskBillingPayer
        from ..identity.user import User
        from ..scheduling.agent_task import AgentTask
        from ..tenancy.tenant import Tenant
        from ..tenancy.tenant_user import TenantUser

        if type(task_id) is not int or task_id <= 0 or type(tenant_id) is not int or tenant_id <= 0:
            raise PersonalBillingError("Explicit task and workspace ids are required")
        async with self._get_owned_session() as session:
            task = (
                await session.execute(
                    select(AgentTask.id).where(AgentTask.id == task_id, AgentTask.tenant_id == tenant_id)
                )
            ).scalar_one_or_none()
            if task is None:
                raise PersonalBillingError("Task is absent from the requested workspace")
            workspace = (
                await session.execute(select(Tenant.id).where(Tenant.id == tenant_id, Tenant.is_active.is_(True)))
            ).scalar_one_or_none()
            consent = (
                await session.execute(
                    select(TaskBillingPayer)
                    .where(TaskBillingPayer.agent_task_id == task_id, TaskBillingPayer.tenant_id == tenant_id)
                    .order_by(TaskBillingPayer.consent_version.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if workspace is None or consent is None:
                raise PersonalBillingUnavailable("Task payer consent is unavailable")
            owner = (
                await session.execute(select(User).where(User.id == consent.payer_user_id, User.is_active.is_(True)))
            ).scalar_one_or_none()
            membership = (
                await session.execute(
                    select(TenantUser.id).where(
                        TenantUser.tenant_id == tenant_id,
                        TenantUser.user_id == consent.payer_user_id,
                        TenantUser.is_active.is_(True),
                    )
                )
            ).scalar_one_or_none()
            account = (
                await session.execute(
                    select(PersonalBillingAccount).where(
                        PersonalBillingAccount.user_id == consent.payer_user_id,
                        PersonalBillingAccount.is_active.is_(True),
                        PersonalBillingAccount.configured_at.is_not(None),
                        PersonalBillingAccount.policy_version == POLICY_VERSION,
                    )
                )
            ).scalar_one_or_none()
            if owner is None or membership is None or account is None:
                raise PersonalBillingUnavailable("Task payer or configured personal account is inactive")
            return account

    async def establish_clean_cutover(self, *, account_id: int, effective_at: datetime):
        """Platform-admin-only reviewed boundary at a future UTC midnight."""
        from ..billing.billing import PersonalBillingAccount
        from ..billing.ledger import PersonalBillingCutover
        from ..identity.role import PlatformRole
        from ..identity.user import User

        reviewer_id = self._actor_id()
        if type(account_id) is not int or account_id <= 0 or not isinstance(effective_at, datetime):
            raise ValueError("An account and aware UTC boundary are required")
        if effective_at.tzinfo is None or effective_at.utcoffset() is None:
            raise ValueError("Cutover time must be timezone aware")
        boundary = effective_at.astimezone(timezone.utc)
        now = datetime.now(timezone.utc)
        if boundary <= now or boundary.time() != time(0, 0):
            raise ValueError("Cutover must be a future UTC midnight")
        async with self._get_owned_session() as session:
            reviewer = (
                await session.execute(
                    select(User)
                    .join(PlatformRole, PlatformRole.id == User.role_id)
                    .where(User.id == reviewer_id, User.is_active.is_(True), PlatformRole.codename == "admin")
                    .with_for_update(of=User)
                )
            ).scalar_one_or_none()
            account = (
                await session.execute(
                    select(PersonalBillingAccount)
                    .where(
                        PersonalBillingAccount.id == account_id,
                        PersonalBillingAccount.is_active.is_(True),
                        PersonalBillingAccount.configured_at.is_not(None),
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if reviewer is None or account is None:
                raise PersonalBillingError("Active platform reviewer and configured account are required")
            existing = (
                await session.execute(
                    select(PersonalBillingCutover).where(PersonalBillingCutover.account_id == account_id)
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise PersonalBillingError("Billing cutover is already established")
            cutover = PersonalBillingCutover(
                account_id=account_id,
                effective_at=boundary,
                reviewed_by_user_id=reviewer_id,
                review_version="clean-midnight-v1",
            )
            session.add(cutover)
            await session.flush()
            return cutover

    async def review_bound_policy(
        self,
        *,
        provider_row_id: int,
        model_row_id: int,
        version: str,
        max_input_tokens: int,
        max_output_tokens: int,
        request_mode: str,
    ):
        """Record an immutable internal provider/model request envelope."""
        import re

        from ..billing.ledger import PersonalLLMBoundPolicy
        from ..identity.role import PlatformRole
        from ..identity.user import User
        from ..llm.llm_model import LLMModel
        from ..llm.llm_provider import LLMProvider

        reviewer_id = self._actor_id()
        if (
            type(provider_row_id) is not int
            or type(model_row_id) is not int
            or type(max_input_tokens) is not int
            or type(max_output_tokens) is not int
            or max_input_tokens <= 0
            or max_output_tokens <= 0
            or type(version) is not str
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", version)
            or request_mode not in {"text_only", "text_and_tools"}
        ):
            raise ValueError("Invalid reviewed provider/model request policy")
        async with self._get_owned_session() as session:
            reviewer = (
                await session.execute(
                    select(User)
                    .join(PlatformRole, PlatformRole.id == User.role_id)
                    .where(User.id == reviewer_id, User.is_active.is_(True), PlatformRole.codename == "admin")
                    .with_for_update(of=User)
                )
            ).scalar_one_or_none()
            model = (
                await session.execute(
                    select(LLMModel, LLMProvider.name)
                    .join(LLMProvider, LLMProvider.id == LLMModel.provider_id)
                    .where(
                        LLMModel.id == model_row_id,
                        LLMModel.provider_id == provider_row_id,
                        LLMModel.is_active.is_(True),
                    )
                    .with_for_update()
                )
            ).one_or_none()
            if reviewer is None or model is None:
                raise PersonalBillingError("Active platform reviewer and matching active model are required")
            model_row, provider_name = model
            if max_input_tokens + max_output_tokens != model_row.max_tokens:
                raise ValueError("Reviewed input and output bounds must equal the configured model context limit")
            policy = PersonalLLMBoundPolicy(
                provider_row_id=provider_row_id,
                model_row_id=model_row_id,
                provider_name=provider_name,
                model_id=model_row.model_id,
                version=version,
                max_input_tokens=max_input_tokens,
                max_output_tokens=max_output_tokens,
                request_mode=request_mode,
                reviewed_by_user_id=reviewer_id,
            )
            session.add(policy)
            await session.flush()
            return policy

    async def consent_legacy_attribution(self, *, source_kind: str, source_identity: str):
        """Let the active account owner consent to one exact receipt or attempt identity."""
        import re

        from ..billing.ledger import PersonalLegacyAttributionConsent
        from ..identity.user import User
        from ..llm.llm_attempt import LLMAttempt
        from ..llm.llm_dispatch_receipt import LLMDispatchReceipt
        from ..tenancy.tenant_user import TenantUser

        actor_id = self._actor_id()
        if source_kind not in {"receipt", "attempt"} or type(source_identity) is not str:
            raise ValueError("Exact legacy receipt or attempt identity is required")
        pattern = r"[0-9a-f]{32}" if source_kind == "receipt" else r"[0-9a-f]{64}"
        if not re.fullmatch(pattern, source_identity):
            raise ValueError("Invalid legacy identity")
        source_key = ("r:" if source_kind == "receipt" else "a:") + source_identity
        async with self._get_owned_session() as session:
            owner = (
                await session.execute(select(User).where(User.id == actor_id, User.is_active.is_(True)))
            ).scalar_one_or_none()
            account = (
                await session.execute(
                    select(PersonalBillingAccount).where(
                        PersonalBillingAccount.user_id == actor_id,
                        PersonalBillingAccount.is_active.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if owner is None or account is None:
                raise PersonalBillingUnavailable("Active personal account owner is required")
            if source_kind == "receipt":
                source = (
                    await session.execute(
                        select(LLMDispatchReceipt).where(LLMDispatchReceipt.dispatch_id == source_identity)
                    )
                ).scalar_one_or_none()
            else:
                source = (
                    await session.execute(select(LLMAttempt).where(LLMAttempt.attempt_key == source_identity))
                ).scalar_one_or_none()
            if source is None:
                raise PersonalBillingUnavailable("Exact legacy financial identity is unavailable")
            membership = (
                await session.execute(
                    select(TenantUser.id).where(
                        TenantUser.tenant_id == source.tenant_id,
                        TenantUser.user_id == actor_id,
                        TenantUser.is_active.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if membership is None:
                raise PersonalBillingError("Current workspace membership is required to consent to this identity")
            existing = (
                await session.execute(
                    select(PersonalLegacyAttributionConsent).where(
                        PersonalLegacyAttributionConsent.source_key == source_key,
                        PersonalLegacyAttributionConsent.account_id == account.id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing
            consent = PersonalLegacyAttributionConsent(
                source_key=source_key,
                dispatch_id=source_identity if source_kind == "receipt" else None,
                attempt_key=source_identity if source_kind == "attempt" else None,
                tenant_id=source.tenant_id,
                account_id=account.id,
                consented_by_user_id=actor_id,
            )
            session.add(consent)
            await session.flush()
            return consent

    async def review_legacy_attribution(self, consent_id: int):
        """Platform-admin approval of a prior account-owner consent; no ledger updates."""
        from ..billing.ledger import PersonalLegacyAttribution, PersonalLegacyAttributionConsent
        from ..identity.role import PlatformRole
        from ..identity.user import User

        reviewer_id = self._actor_id()
        if type(consent_id) is not int or consent_id <= 0:
            raise ValueError("A stored owner consent is required")
        async with self._get_owned_session() as session:
            reviewer = (
                await session.execute(
                    select(User)
                    .join(PlatformRole, PlatformRole.id == User.role_id)
                    .where(User.id == reviewer_id, User.is_active.is_(True), PlatformRole.codename == "admin")
                    .with_for_update(of=User)
                )
            ).scalar_one_or_none()
            consent = (
                await session.execute(
                    select(PersonalLegacyAttributionConsent)
                    .where(PersonalLegacyAttributionConsent.id == consent_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if reviewer is None or consent is None:
                raise PersonalBillingError("Active platform reviewer and stored owner consent are required")
            existing = (
                await session.execute(
                    select(PersonalLegacyAttribution).where(PersonalLegacyAttribution.source_key == consent.source_key)
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.consent_id != consent.id:
                    raise PersonalBillingError("Legacy identity was already mapped from another consent")
                return existing
            attribution = PersonalLegacyAttribution(
                source_key=consent.source_key,
                dispatch_id=consent.dispatch_id,
                attempt_key=consent.attempt_key,
                tenant_id=consent.tenant_id,
                account_id=consent.account_id,
                consent_id=consent.id,
                consented_by_user_id=consent.consented_by_user_id,
                reviewed_by_user_id=reviewer_id,
            )
            session.add(attribution)
            await session.flush()
            return attribution

    async def legacy_attribution_inventory(self, *, source_kind: str, limit: int = 100):
        """Return exact unmapped identities; never infer a payer from task/workspace ownership."""
        from ..billing.ledger import PersonalLegacyAttribution
        from ..identity.role import PlatformRole
        from ..identity.user import User
        from ..llm.llm_attempt import LLMAttempt
        from ..llm.llm_dispatch_receipt import LLMDispatchReceipt

        reviewer_id = self._actor_id()
        if source_kind not in {"receipt", "attempt"} or type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Choose receipt or attempt inventory and a limit from 1 to 500")
        async with self._get_owned_session() as session:
            reviewer = (
                await session.execute(
                    select(User.id)
                    .join(PlatformRole, PlatformRole.id == User.role_id)
                    .where(User.id == reviewer_id, User.is_active.is_(True), PlatformRole.codename == "admin")
                )
            ).scalar_one_or_none()
            if reviewer is None:
                raise PersonalBillingError("Active platform admin is required for legacy inventory")
            if source_kind == "receipt":
                rows = (
                    await session.execute(
                        select(
                            LLMDispatchReceipt.dispatch_id,
                            LLMDispatchReceipt.tenant_id,
                            LLMDispatchReceipt.agent_task_id,
                            LLMDispatchReceipt.purpose,
                            LLMDispatchReceipt.stage,
                            LLMDispatchReceipt.prepared_at,
                        )
                        .outerjoin(
                            PersonalLegacyAttribution,
                            PersonalLegacyAttribution.dispatch_id == LLMDispatchReceipt.dispatch_id,
                        )
                        .where(PersonalLegacyAttribution.id.is_(None))
                        .order_by(LLMDispatchReceipt.prepared_at, LLMDispatchReceipt.dispatch_id)
                        .limit(limit)
                    )
                ).all()
                return [
                    dict(
                        kind="receipt",
                        dispatch_id=row[0],
                        tenant_id=row[1],
                        agent_task_id=row[2],
                        purpose=row[3],
                        stage=row[4],
                        occurred_at=row[5],
                    )
                    for row in rows
                ]
            rows = (
                await session.execute(
                    select(
                        LLMAttempt.attempt_key,
                        LLMAttempt.tenant_id,
                        LLMAttempt.agent_task_id,
                        LLMAttempt.purpose,
                        LLMAttempt.stage,
                        LLMAttempt.created_at,
                    )
                    .outerjoin(
                        PersonalLegacyAttribution, PersonalLegacyAttribution.attempt_key == LLMAttempt.attempt_key
                    )
                    .where(PersonalLegacyAttribution.id.is_(None))
                    .order_by(LLMAttempt.created_at, LLMAttempt.attempt_key)
                    .limit(limit)
                )
            ).all()
            return [
                dict(
                    kind="attempt",
                    attempt_key=row[0],
                    tenant_id=row[1],
                    agent_task_id=row[2],
                    purpose=row[3],
                    stage=row[4],
                    occurred_at=row[5],
                )
                for row in rows
            ]
