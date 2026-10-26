"""Read-time incomplete-delivery signal from existing outcome evidence (P8-T11).

Sources are only what delivery already commits: `DigestRun` status/ledger
(`delivery_state` summarized by `summarize_delivery`) and digest `Job` rows
written by queue finalization (committed retry) and stale-claim recovery
(`OUTCOME_UNCONFIRMED_PREFIX` quarantine, attempt-budget stop). Nothing here
writes, sends, retries, probes a provider or creates Notification rows.

Recipients follow the owner decision: workspace owner/admin see the
workspace-wide aggregate; any other role sees only digest jobs it initiated.
Both need a fresh `digestrun.view` grant. There is no broadcast to members and
no platform-operator bypass. Output is counts plus redacted row identity:
never destinations, chat IDs, content, provider errors or other users' data.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

WINDOW_DAYS = 7
ROW_LIMIT = 200
SAMPLE_LIMIT = 20
WORKSPACE_ROLES = frozenset({"owner", "admin"})
BUCKETS = ("undelivered", "unconfirmed", "retrying", "quarantined")
BUCKET_LABELS = {
    "undelivered": "не доставлено",
    "unconfirmed": "доставка не подтверждена",
    "retrying": "ожидает повтора",
    "quarantined": "в карантине",
}
RUN_STATE_LABELS = {
    "partial": "Доставлен частично",
    "uncertain": "Доставка не подтверждена",
    "blocked": "Доставка заблокирована",
    "failed": "Ошибка доставки",
    "unknown": "Статус доставки неизвестен",
}
RUN_BUCKETS = {
    "partial": "undelivered",
    "blocked": "undelivered",
    "failed": "undelivered",
    "uncertain": "unconfirmed",
    "unknown": "unconfirmed",
}
JOB_STATE_LABELS = {
    "undelivered": "Отправка завершилась ошибкой",
    "unconfirmed": "Остановлено до подтверждения итога",
    "retrying": "Ожидает повторной попытки",
    "quarantined": "Итог не подтверждён, повтор запрещён",
}


def _positive(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _moment(value):
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value):
    parsed = _moment(value)
    return parsed.astimezone(timezone.utc).isoformat() if parsed else ""


def digest_run_state(run) -> str:
    """Same classification as the Home digest status (`app/web/dashboard.py`).

    Kept as a service copy because the dashboard function is an AST-isolated
    web helper; a parity test pins both to the same answers.
    """
    error = getattr(run, "error", None) or ""
    state = getattr(run, "delivery_state", None)
    if isinstance(state, dict):
        try:
            from app.services.digest.checkpoints import validate_checkpoint
            from app.services.digest.delivery_outcomes import summarize_delivery

            checkpoint = validate_checkpoint(state)
            if checkpoint["run_id"] != getattr(run, "id", None) or checkpoint["tenant_id"] != getattr(
                run, "tenant_id", None
            ):
                return "unknown"
            if "partial_delivery_requires_review" in error:
                return "partial"
            outcome = summarize_delivery(checkpoint)
            if outcome["pending_parts"] == outcome["total_parts"] and getattr(run, "status", None) == "pending":
                return "pending"
            return outcome["status"]
        except (KeyError, TypeError, ValueError):
            return "unknown"
    if "partial_delivery_requires_review" in error:
        return "partial"
    status = getattr(run, "status", None)
    return status if status in {"sent", "failed", "skipped", "pending"} else "unknown"


def run_bucket(run) -> str | None:
    return RUN_BUCKETS.get(digest_run_state(run))


def job_bucket(job) -> str | None:
    """Bucket for one digest Job row; None when it is not a delivery problem."""
    from app.jobs.attempt_budget import ATTEMPT_BUDGET_STOP_PREFIX
    from app.jobs.recovery_policy import (
        CANCELLED_BEFORE_START_ERROR,
        OPERATOR_CANCELLED_ERROR,
        OUTCOME_UNCONFIRMED_PREFIX,
    )

    if getattr(job, "job_type", None) != "digest":
        return None
    status, error = getattr(job, "status", None), getattr(job, "error", None)
    if status == "pending":
        # `_finalize_claim` committed a retry: error kept, row re-queued.
        attempts = getattr(job, "attempts", 0)
        return "retrying" if isinstance(error, str) and error and _positive(attempts) else None
    if status != "failed":
        return None
    if not isinstance(error, str):
        return "undelivered"
    if error in (OPERATOR_CANCELLED_ERROR, CANCELLED_BEFORE_START_ERROR):
        # Deliberate cancellation is an operator decision, not a delivery failure.
        return None
    if error.startswith(OUTCOME_UNCONFIRMED_PREFIX):
        return "quarantined"
    if error.startswith(ATTEMPT_BUDGET_STOP_PREFIX):
        return "unconfirmed"
    return "undelivered"


@dataclass(frozen=True)
class DeliveryScope:
    workspace_id: int
    user_id: int
    runs: bool
    jobs: str | None  # "workspace" | "initiated" | None


def delivery_scope(workspace_id, user_id, role_codename, grants) -> DeliveryScope | None:
    from app.types import ActionType

    if not _positive(workspace_id) or not _positive(user_id) or not isinstance(role_codename, str):
        return None
    grants = grants or frozenset()
    if ("digestrun", ActionType.VIEW) not in grants:
        return None
    if role_codename in WORKSPACE_ROLES:
        jobs = "workspace" if ("job", ActionType.VIEW) in grants else None
        return DeliveryScope(workspace_id, user_id, runs=True, jobs=jobs)
    return DeliveryScope(workspace_id, user_id, runs=False, jobs="initiated")


@dataclass
class DeliverySignal:
    workspace_id: int
    scope: str  # "workspace" | "initiated"
    counts: dict = field(default_factory=lambda: dict.fromkeys(BUCKETS, 0))
    runs: list = field(default_factory=list)
    jobs: list = field(default_factory=list)
    truncated: bool = False
    latest_at: str = ""
    jobs_included: bool = False

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def fingerprint(self) -> str:
        identity = [self.workspace_id, self.scope, *(self.counts[b] for b in BUCKETS), self.latest_at]
        identity += [f"r{r['id']}:{r['bucket']}" for r in self.runs] + [f"j{j['id']}:{j['bucket']}" for j in self.jobs]
        return hashlib.sha256("|".join(map(str, identity)).encode()).hexdigest()[:16]

    def summary(self) -> str:
        parts = [f"{self.counts[b]} — {BUCKET_LABELS[b]}" for b in BUCKETS if self.counts[b]]
        text = "; ".join(parts) + f" за {WINDOW_DAYS} дн."
        return text + (" Показано не всё окно." if self.truncated else "")


class DeliverySignalRepository:
    """Explicit tenant predicates plus tenant scope; bounded newest-first windows."""

    async def memberships(self, user_id):
        from app.models import WorkspaceRole
        from app.models.managers.tenant_manager import TenantUserManager

        rows = await TenantUserManager().web_memberships(user_id)
        role_ids = sorted({m.role_id for m in rows if _positive(m.role_id)})
        roles = {r.id: str(r.codename) for r in await WorkspaceRole.objects.filter(id__in=role_ids)} if role_ids else {}
        return [(m.tenant_id, roles.get(m.role_id)) for m in rows if m.role_id in roles]

    async def runs(self, workspace_id, since):
        from app.core.tenant_context import tenant_scope
        from app.models import DigestRun

        with tenant_scope(workspace_id):
            return list(
                await DigestRun.objects.filter(
                    DigestRun.created_at >= since,
                    DigestRun.status.notin_(("sent", "skipped"))
                    | DigestRun.delivery_state.is_not(None)
                    | DigestRun.error.is_not(None),
                    tenant_id=workspace_id,
                )
                .order_by(DigestRun.created_at.desc(), DigestRun.id.desc())
                .limit(ROW_LIMIT + 1)
            )

    async def jobs(self, workspace_id, since, *, initiated_by=None):
        from app.core.tenant_context import tenant_scope
        from app.models import Job

        criteria = [Job.job_type == "digest", Job.created_at >= since]
        criteria.append((Job.status == "failed") | ((Job.status == "pending") & Job.error.is_not(None)))
        filters = {"tenant_id": workspace_id}
        if initiated_by is not None:
            filters["initiated_by_user_id"] = initiated_by
        with tenant_scope(workspace_id):
            return list(
                await Job.objects.filter(*criteria, **filters)
                .order_by(Job.created_at.desc(), Job.id.desc())
                .limit(ROW_LIMIT + 1)
            )


async def workspace_signal(scope: DeliveryScope, repository=None, now=None) -> DeliverySignal:
    repo = repository or DeliverySignalRepository()
    since = (now or datetime.now(timezone.utc)) - timedelta(days=WINDOW_DAYS)
    signal = DeliverySignal(scope.workspace_id, "workspace" if scope.runs else "initiated")
    moments = []
    if scope.runs:
        rows = await repo.runs(scope.workspace_id, since)
        signal.truncated |= len(rows) > ROW_LIMIT
        for run in rows[:ROW_LIMIT]:
            bucket = run_bucket(run)
            if bucket is None or getattr(run, "tenant_id", None) != scope.workspace_id:
                continue
            signal.counts[bucket] += 1
            moments.append(_moment(run.created_at))
            if len(signal.runs) < SAMPLE_LIMIT:
                state = digest_run_state(run)
                signal.runs.append(
                    dict(
                        id=run.id,
                        bucket=bucket,
                        label=RUN_STATE_LABELS[state],
                        period=f"{run.period_start:%d.%m.%Y} — {run.period_end:%d.%m.%Y}",
                        created_at=_iso(run.created_at),
                    )
                )
    signal.jobs_included = scope.jobs is not None
    if scope.jobs is not None:
        initiated = scope.user_id if scope.jobs == "initiated" else None
        rows = await repo.jobs(scope.workspace_id, since, initiated_by=initiated)
        signal.truncated |= len(rows) > ROW_LIMIT
        problems = []
        for job in rows[:ROW_LIMIT]:
            if getattr(job, "tenant_id", None) != scope.workspace_id:
                continue
            if initiated is not None and getattr(job, "initiated_by_user_id", None) != initiated:
                continue
            bucket = job_bucket(job)
            if bucket is not None:
                problems.append((job, bucket))
        for job, bucket in problems:
            # A later done job can belong to a different period (or be skipped).
            # It is not evidence that this particular failed delivery was fixed.
            signal.counts[bucket] += 1
            moments.append(_moment(job.created_at))
            if len(signal.jobs) < SAMPLE_LIMIT:
                signal.jobs.append(
                    dict(
                        id=job.id,
                        bucket=bucket,
                        label=JOB_STATE_LABELS[bucket],
                        attempts=f"{job.attempts}/{job.max_attempts}",
                        created_at=_iso(job.created_at),
                    )
                )
    known = [m for m in moments if m is not None]
    signal.latest_at = _iso(max(known)) if known else ""
    return signal


async def personal_signals(user, repository=None, now=None) -> list[DeliverySignal]:
    """Non-empty signals for every active web membership the User may act on."""
    from app.core.access_catalog import workspace_grants

    if user is None or not getattr(user, "is_active", False) or not _positive(getattr(user, "id", None)):
        return []
    repo = repository or DeliverySignalRepository()
    signals = []
    for workspace_id, codename in await repo.memberships(user.id):
        scope = delivery_scope(workspace_id, user.id, codename, workspace_grants(codename))
        if scope is None:
            continue
        signal = await workspace_signal(scope, repo, now)
        if signal.total:
            signals.append(signal)
    return signals


async def navigation_allowed(user, workspace_id, repository=None) -> bool:
    """Fresh check for the bell link: active membership with `digestrun.view`."""
    from app.core.access_catalog import workspace_grants

    if user is None or not getattr(user, "is_active", False) or not _positive(workspace_id):
        return False
    repo = repository or DeliverySignalRepository()
    for tenant_id, codename in await repo.memberships(user.id):
        if tenant_id == workspace_id:
            return delivery_scope(workspace_id, user.id, codename, workspace_grants(codename)) is not None
    return False


def bell_item(user_id, signal: DeliverySignal) -> dict:
    return dict(
        key=f"delivery:{signal.workspace_id}:{user_id}:{signal.fingerprint()}",
        kind="delivery",
        scope="personal",
        user_id=user_id,
        workspace_id=signal.workspace_id,
        title="Доставка требует проверки" if signal.scope == "workspace" else "Ваш запуск дайджеста требует проверки",
        message=signal.summary(),
        severity="warning",
        state="attention",
        created_at=signal.latest_at,
        destination=f"/app/notifications/delivery/{signal.workspace_id}",
        action="open",
        read_id=None,
        counts=dict(signal.counts),
    )
