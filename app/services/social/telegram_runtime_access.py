"""Private, freshly checked execution pins for ordinary queued Telegram reads.

JobClaim comes from acquisition, NEVER from a request/job payload. Source owner
consent remains a separate check. No operator bypass or owner-as-payer fallback.
These helpers do not reserve AI funds or authorize any model/send operation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import and_, select

from app.core.access_catalog import workspace_grants
from app.core.permissions import get_current_user
from app.jobs.claim_outcomes import JobClaim
from app.models import AgentTask, Job, PersonalBillingAccount, TaskBillingPayer, Tenant, TenantUser, User, WorkspaceRole
from app.models.managers.job_lifecycle import JobLifecycleMixin
from app.models.managers.personal_billing_manager import POLICY_VERSION
from app.services.billing.payer import resolve_scheduled_task_payer
from app.services.social.connector_authorizations import AuthorizationUnavailable
from app.services.social.telegram_grant_access import SourceGrantSelection, _positive, _validate_selection_context
from app.services.social.telegram_queue_selection import _queued_authorization_id
from app.types import ActionType

_EXECUTION_RIGHTS = frozenset({("source", ActionType.ANALYZE), ("agenttask", ActionType.UPDATE)})


@dataclass(frozen=True, repr=False)
class _ExecutionPin:
    selection: SourceGrantSelection
    claim: JobClaim
    authorization_id: int
    actor_id: int
    payload_digest: str
    payer_consent_id: int | None = None
    payer_consent_version: int | None = None
    payer_account_id: int | None = None


def _payload_digest(payload, source_id):
    """Only the exact admitted source snapshot; occupancy fallbacks are unsafe."""
    if type(payload) is not dict:
        raise AuthorizationUnavailable()
    ids = payload.get("source_ids")
    if type(ids) is not list or not ids or len(ids) > 10000:
        raise AuthorizationUnavailable()
    if any(type(value) is not int or value <= 0 for value in ids) or len(set(ids)) != len(ids) or source_id not in ids:
        raise AuthorizationUnavailable()
    try:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError):
        raise AuthorizationUnavailable() from None
    return hashlib.sha256(raw).hexdigest()


def _check_context(selection, claim, actor_id=None):
    _validate_selection_context(selection)
    if type(claim) is not JobClaim or claim.tenant_id != selection.tenant_id or claim.job_type != "collect":
        raise AuthorizationUnavailable()
    ambient = get_current_user()
    if ambient is not None and actor_id is not None and getattr(ambient, "id", None) != actor_id:
        raise AuthorizationUnavailable()


async def _job(session, selection, claim, *, lock=False):
    statement = select(Job.payload).where(
        *JobLifecycleMixin._claim_predicates(Job, claim),
        Job.status == "running",
        Job.initiated_by_user_id.is_not_distinct_from(claim.initiated_by_user_id),
    )
    if lock:
        statement = statement.with_for_update(read=True, of=Job)
    payload = (await session.execute(statement)).scalar_one_or_none()
    digest = _payload_digest(payload, selection.source_id)
    return digest, _queued_authorization_id(payload, selection)


async def _latest_payer(session, claim, *, lock=False):
    if claim.agent_task_id is None:
        raise AuthorizationUnavailable()
    # Consent writers first lock Tenant, then AgentTask. Taking matching SHARE
    # locks prevents a new "latest consent" phantom until this transaction ends.
    task = select(AgentTask.id).where(AgentTask.id == claim.agent_task_id, AgentTask.tenant_id == claim.tenant_id)
    if lock:
        task = task.with_for_update(read=True)
    if (await session.execute(task)).scalar_one_or_none() is None:
        raise AuthorizationUnavailable()
    statement = (
        select(
            TaskBillingPayer.id,
            TaskBillingPayer.consent_version,
            TaskBillingPayer.payer_user_id,
            PersonalBillingAccount.id.label("account_id"),
        )
        .join(
            PersonalBillingAccount,
            and_(
                PersonalBillingAccount.user_id == TaskBillingPayer.payer_user_id,
                PersonalBillingAccount.is_active.is_(True),
                PersonalBillingAccount.configured_at.is_not(None),
                PersonalBillingAccount.daily_limit_usd.is_not(None),
                PersonalBillingAccount.policy_version == POLICY_VERSION,
            ),
        )
        .where(
            TaskBillingPayer.agent_task_id == claim.agent_task_id,
            TaskBillingPayer.tenant_id == claim.tenant_id,
            TaskBillingPayer.consented_by_user_id == TaskBillingPayer.payer_user_id,
        )
        .order_by(TaskBillingPayer.consent_version.desc())
        .limit(1)
    )
    if lock:
        statement = statement.with_for_update(read=True, of=(TaskBillingPayer, PersonalBillingAccount))
    row = (await session.execute(statement)).one_or_none()
    if row is None:
        raise AuthorizationUnavailable()
    # Joining only active accounts must NOT fall back to an older consent when
    # the most recent payer is unavailable.
    latest_id = await session.scalar(
        select(TaskBillingPayer.id)
        .where(TaskBillingPayer.agent_task_id == claim.agent_task_id, TaskBillingPayer.tenant_id == claim.tenant_id)
        .order_by(TaskBillingPayer.consent_version.desc())
        .limit(1)
    )
    if row.id != latest_id:
        raise AuthorizationUnavailable()
    return row


async def _actor_rights(session, tenant_id, actor_id, *, lock=False):
    actor_id = _positive(actor_id)
    statement = (
        select(WorkspaceRole.codename)
        .select_from(User)
        .join(TenantUser, and_(TenantUser.user_id == User.id, TenantUser.tenant_id == tenant_id))
        .join(WorkspaceRole, WorkspaceRole.id == TenantUser.role_id)
        .where(
            User.id == actor_id,
            User.is_active.is_(True),
            TenantUser.channel == "web",
            TenantUser.is_active.is_(True),
        )
    )
    if lock:
        statement = statement.with_for_update(read=True, of=(User, TenantUser, WorkspaceRole))
    role = (await session.execute(statement)).scalar_one_or_none()
    if role is None or not _EXECUTION_RIGHTS.issubset(workspace_grants(role)):
        raise AuthorizationUnavailable()


async def _tenant(session, tenant_id, *, lock=False):
    statement = select(Tenant.id).where(Tenant.id == tenant_id, Tenant.is_active.is_(True))
    if lock:
        statement = statement.with_for_update(read=True)
    if (await session.execute(statement)).scalar_one_or_none() is None:
        raise AuthorizationUnavailable()


async def _capture_execution(session, selection, claim):
    """Capture only from a trusted acquired claim; fresh checks follow capture."""
    _check_context(selection, claim)
    digest, authorization_id = await _job(session, selection, claim)
    await _tenant(session, claim.tenant_id)
    actor_id = claim.initiated_by_user_id
    payer = None
    if actor_id is None:
        # Reuse the established scheduled-payer boundary, then pin/recheck its
        # exact consent/account in this transaction. Any change denies the run.
        account = await resolve_scheduled_task_payer(task_id=claim.agent_task_id, tenant_id=claim.tenant_id)
        payer = await _latest_payer(session, claim)
        if payer.account_id != account.id or payer.payer_user_id != account.user_id:
            raise AuthorizationUnavailable()
        actor_id = payer.payer_user_id
    pin = _ExecutionPin(
        selection,
        claim,
        authorization_id,
        _positive(actor_id),
        digest,
        payer.id if payer else None,
        payer.consent_version if payer else None,
        payer.account_id if payer else None,
    )
    await _check_execution(session, pin)
    return pin


async def _check_execution(session, pin, *, lock=False):
    """Metadata-only; lock=True is required inside the output WRITE transaction."""
    if type(pin) is not _ExecutionPin:
        raise AuthorizationUnavailable()
    _check_context(pin.selection, pin.claim, pin.actor_id)
    await _tenant(session, pin.claim.tenant_id, lock=lock)
    if await _job(session, pin.selection, pin.claim, lock=lock) != (pin.payload_digest, pin.authorization_id):
        raise AuthorizationUnavailable()
    if pin.claim.initiated_by_user_id is None:
        payer = await _latest_payer(session, pin.claim, lock=lock)
        if (payer.id, payer.consent_version, payer.account_id, payer.payer_user_id) != (
            pin.payer_consent_id,
            pin.payer_consent_version,
            pin.payer_account_id,
            pin.actor_id,
        ):
            raise AuthorizationUnavailable()
    elif pin.actor_id != pin.claim.initiated_by_user_id:
        raise AuthorizationUnavailable()
    if pin.claim.agent_task_id is not None:
        task = select(AgentTask.id).where(
            AgentTask.id == pin.claim.agent_task_id, AgentTask.tenant_id == pin.claim.tenant_id
        )
        if lock:
            task = task.with_for_update(read=True)
        if (await session.execute(task)).scalar_one_or_none() is None:
            raise AuthorizationUnavailable()
    await _actor_rights(session, pin.claim.tenant_id, pin.actor_id, lock=lock)
