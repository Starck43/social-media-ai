"""Job dispatcher: claim → execute → mark done/failed with retries.

Tenancy: the queue itself is process-wide (any worker may claim any tenant's
job), but every handler runs inside the owning tenant's scope, taken from
`job.tenant_id`. Handlers therefore never receive a tenant and never filter by
it — the queryset guard in `BaseManager` applies it.

Two entry points, one execution path:
- `run_pending_once()` — the worker loop; claims the globally oldest pending job.
- `run_job_now(job_id)` — the operator path (CLI "run this task"); claims that
  exact job, so it can never pick up a different workspace's queued work.
"""

import asyncio
import logging
from dataclasses import replace
from typing import Any, Callable, NoReturn, Optional

from app.core.tenant_context import tenant_scope
from app.jobs.claim_outcomes import JobClaim, JobClaimLostError, JobNotAcquiredError, JobOutcomeReceipt, OutcomeAck
from app.jobs.handlers import HANDLERS
from app.jobs.result_outcomes import reported_llm_cost, returned_failure
from app.jobs.recovery_policy import OUTCOME_UNCONFIRMED_PREFIX, outcome_unconfirmed
from app.models.managers.job_manager import JobManager

logger = logging.getLogger(__name__)

jobs = JobManager()


_FAILURE_MESSAGE = "Не удалось завершить обработку. Подробности сохранены в задаче."
_LOG_JOB_TYPES = frozenset({"collect", "digest", "prune", "analyze", "learn", "reflect"})


class JobOutcomePersistenceError(RuntimeError):
    """Outcome write/projection failed; the caller must inspect before replay.

    This is not claim loss or proof of rollback. Public message/fields exclude
    result and original error text; committed Job/task state may differ. Display
    chaining is suppressed, not a guarantee that Python forgets exception context.
    """

    def __init__(self, job_id: int | None, tenant_id: int | None, error_code: str):
        self.job_id = job_id
        self.tenant_id = tenant_id
        self.error_code = error_code
        super().__init__("Job outcome persistence or bookkeeping failed; inspect before retry.")


class JobClaimRenewalError(RuntimeError):
    """Pre-dispatch ownership could not be confirmed; do not infer safe replay."""

    error_code = "claim_renewal_unconfirmed"

    def __init__(self, claim: JobClaim):
        self.job_id = claim.job_id
        self.tenant_id = claim.tenant_id
        super().__init__("Job ownership renewal unconfirmed; no handler dispatched by this caller.")


def _log_id(value: Any) -> int | None:
    """Only persisted integer IDs; never stringify payload-like values."""
    return value if type(value) is int and value > 0 else None


def _log_job_type(value: Any) -> str:
    return value if type(value) is str and value in _LOG_JOB_TYPES else "unknown"


def _error_kind(error: BaseException) -> str:
    """Bounded categories, not exception messages, repr, args or tracebacks."""
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, ConnectionError):
        return "connection_error"
    if isinstance(error, OSError):
        return "io_error"
    if isinstance(error, ValueError):
        return "value_error"
    if isinstance(error, RuntimeError):
        return "runtime_error"
    return "unexpected_error"


def _log_job(level: int, event: str, job: Any, *, error_code: str = "none") -> None:
    """Dispatcher-only metadata log; event/code are internal static values.

    Keep correlation without payloads, source names, provider bodies or errors.
    No exc_info: tracebacks can reproduce credentials and customer SQL parameters.
    This is not a filter for handlers, ORM, provider clients or external sinks.
    """
    logger.log(
        level,
        "%s job_id=%s tenant_id=%s task_id=%s job_type=%s error_code=%s",
        event,
        _log_id(getattr(job, "id", None)),
        _log_id(getattr(job, "tenant_id", None)),
        _log_id(getattr(job, "agent_task_id", None)),
        _log_job_type(getattr(job, "job_type", None)),
        error_code,
    )


def _outcome_write_failed(job: Any, error: Exception) -> NoReturn:
    """Report bounded persistence uncertainty without attempting another write."""
    code = _error_kind(error)
    _log_job(logging.ERROR, "job_outcome_persistence_failed", job, error_code=code)
    raise JobOutcomePersistenceError(
        _log_id(getattr(job, "id", None)), _log_id(getattr(job, "tenant_id", None)), code
    ) from None


def _job_label(job_type: str) -> str:
    labels = {
        "collect": "Сбор данных",
        "digest": "Дайджест",
        "prune": "Очистка",
        "analyze": "Анализ",
        "learn": "Обучение",
        "reflect": "Рефлексия",
    }
    return labels.get(job_type, job_type)


def _format_name_list(names: list[str], limit: int = 5) -> str:
    """Human-readable list of source names, truncated with a tail count."""
    if not names:
        return ""
    head = names[:limit]
    tail = f" и ещё {len(names) - limit}" if len(names) > limit else ""
    return ", ".join(head) + tail


def _result_count(result: dict, key: str) -> int | None:
    """Only measured nonnegative counters; missing/malformed is not zero."""
    value = result.get(key)
    return value if type(value) is int and value >= 0 else None


def _source_error_count(result: dict) -> int:
    """Reported source errors, not a terminal failure or replay permission.

    Staged errors overlap the affected-source total; never add them together.
    Legacy/malformed counters provide no evidence of an error or completeness.
    """
    return max(_result_count(result, "error") or 0, _result_count(result, "staged_errors") or 0)


def _job_success_message(job: Any, result: dict | None) -> str:
    """User-facing completion message, readable by a non-technical person.

    Collect/analyze use explicit summaries, not raw errors or per-source data.
    A completed execution with reported source errors is not complete success.
    Other job types retain their existing message contract.
    """
    label = _job_label(job.job_type)
    if not isinstance(result, dict):
        return f"Задача «{label}» успешно завершена."

    if job.job_type == "collect":
        collected = result.get("collected", 0)
        error = _source_error_count(result)
        empty = result.get("empty", 0)
        excluded = result.get("excluded", 0)
        items = result.get("items", 0)
        # `new_items` is absent on jobs written before it existed — say "not counted",
        # never "0 new", which would be a claim about data we simply did not measure.
        new_items = result.get("new_items")

        parts = [
            f"Задача «{label}» завершена с ошибками. Данные могут быть неполными."
            if error
            else f"Задача «{label}» успешно завершена."
        ]

        if collected:
            names = _format_name_list(result.get("collected_sources") or [])
            parts.append(f"Отслежено источников: {collected} ({names}).")
            parts.append(f"Получено от источников: {items}.")
            if new_items is None:
                parts.append("Новых среди них не подсчитано — этот сбор старше счётчика.")
            elif new_items:
                parts.append(f"Из них новых, ранее не виденных: {new_items}.")
            else:
                parts.append("Новых нет — все эти записи уже были получены ранее.")
        else:
            parts.append(
                "Данные от успешно обработанных источников не получены." if error else "Новых данных не получено."
            )

        if error:
            names = _format_name_list(result.get("error_sources") or [])
            names_detail = f" ({names})" if names else ""
            parts.append(f"Ошибки при сборе: {error}{names_detail}.")
        if empty:
            parts.append(f"Без контента: {empty} (источники опрошены, но постов нет).")
        if excluded:
            parts.append(f"Пропущено (исключено из мониторинга): {excluded}.")

        return " ".join(parts)

    if job.job_type == "analyze":
        error = _source_error_count(result)
        if error:
            parts = [f"Задача «{label}» завершена с ошибками. Результаты могут быть неполными."]
            parts.append(f"Источников с ошибками: {error}.")
        elif _result_count(result, "error") == 0 and _result_count(result, "staged_errors") == 0:
            parts = [f"Задача «{label}» успешно завершена."]
        else:
            # Old results cannot distinguish an exception from a normal skip.
            parts = [f"Задача «{label}» завершена. Сведения об ошибках источников не учтены в этом результате."]
        for key, caption in (
            ("sources", "Источников обработано"),
            ("analyzed", "Записей прошло фильтр анализа"),
            ("actions_created", "Действий подготовлено"),
            ("skipped", "Пропущено"),
        ):
            count = _result_count(result, key)
            if count is not None:
                parts.append(f"{caption}: {count}.")
        staged_errors = _result_count(result, "staged_errors")
        if staged_errors:
            parts.append(f"Ошибки обработки отложенных данных: {staged_errors}.")
        return " ".join(parts)

    detail = ", ".join(f"{k}: {v}" for k, v in list(result.items())[:6])
    return f"Задача «{label}» успешно завершена." + (f" {detail}" if detail else "")


async def _notify_job_result(job: Any, *, success: bool, error: str | None = None, result: dict | None = None) -> None:
    """Create a tenant-scoped Notification for a finished job.

    User-facing message (понятная пользователю формулировка) goes into the
    Notification row; failures use a fixed template, never raw exception text.
    The technical error stays on the Job (`error`) and on the
    AgentTask (`last_error`) for the admin UI. Runs inside `tenant_scope`.
    Reported collect/analyze source errors use API_ERROR without reclassifying
    the committed Job/task outcome or enabling retry. This is DB-only delivery.
    """
    from app.services.notifications.service import notify
    from app.types import NotificationType

    label = _job_label(_log_job_type(job.job_type)) if not success else _job_label(job.job_type)
    if not success:
        try:
            await notify.create(
                title=f"Ошибка задачи «{label}»",
                message=f"Задача «{label}» не выполнена. {_FAILURE_MESSAGE}",
                ntype=NotificationType.API_ERROR,
                entity_type="task",
                entity_id=job.agent_task_id,
            )
        except Exception:  # noqa: BLE001 — a failing notification must never break the worker
            _log_job(logging.ERROR, "job_error_notification_failed", job, error_code="notification_write_failed")
        return

    source_errors = (
        job.job_type in {"collect", "analyze"} and isinstance(result, dict) and _source_error_count(result) > 0
    )
    try:
        await notify.create(
            title=f"Задача «{label}» завершена с ошибками" if source_errors else f"Задача «{label}» выполнена",
            message=_job_success_message(job, result),
            ntype=NotificationType.API_ERROR if source_errors else NotificationType.REPORT_READY,
            entity_type="task",
            entity_id=job.agent_task_id,
        )
    except Exception:  # noqa: BLE001
        _log_job(logging.ERROR, "job_success_notification_failed", job, error_code="notification_write_failed")


def _require_outcome_receipt(value: Any, claim: JobClaim) -> JobOutcomeReceipt:
    if not isinstance(value, JobOutcomeReceipt) or value.claim != claim:
        raise TypeError("Ordinary finalization requires this claim's outcome receipt")
    return value


async def _post_ordinary_outcome(receipt: JobOutcomeReceipt) -> JobOutcomeReceipt:
    """Only this claim's acknowledged commit may drive post-commit projection."""
    if not isinstance(receipt, JobOutcomeReceipt):
        raise TypeError("Ordinary finalization requires an outcome receipt")
    claim = receipt.claim
    if receipt.acknowledgement == OutcomeAck.CLAIM_LOST:
        _log_job(logging.WARNING, "job_outcome_claim_lost", claim, error_code="claim_lost")
        return receipt
    if receipt.task_projection_failed:
        _log_job(logging.ERROR, "job_task_projection_failed", claim, error_code="task_projection_failed")
        return receipt
    if receipt.acknowledgement == OutcomeAck.RETRY:
        _log_job(logging.WARNING, "job_retry_scheduled", claim)
        return receipt
    success = receipt.acknowledgement == OutcomeAck.DONE
    _log_job(logging.INFO if success else logging.ERROR, "job_done" if success else "job_failed_terminal", claim)
    if success and isinstance(receipt.result, dict) and receipt.result.get("status") == "skipped":
        return receipt
    try:
        await _notify_job_result(
            claim,
            success=success,
            result=receipt.result if success else None,
            error=None if success else (
                "Результат выполнения не подтверждён. Проверьте задание перед повторным запуском."
                if outcome_unconfirmed(receipt.error) else _FAILURE_MESSAGE
            ),
        )
    except Exception:
        # An unexpected projection failure cannot invalidate the acknowledged Job write.
        _log_job(logging.ERROR, "job_notification_projection_failed", claim, error_code="notification_write_failed")
        receipt = replace(receipt, notification_projection_failed=True)
    return receipt


_ORDINARY_HEARTBEAT_SECONDS = 20


class _OrdinaryHeartbeatStopped(Exception):
    """Internal signal, distinct from errors raised by the handler itself."""

    def __init__(self, error: Exception):
        self.error = error
        super().__init__("Ordinary heartbeat stopped execution.")


class _OrdinaryHeartbeatState:
    def __init__(self):
        self.renewing = False
        self.settled = asyncio.Event()


async def _ordinary_heartbeat(claim: JobClaim, state: _OrdinaryHeartbeatState) -> None:
    while True:
        await asyncio.sleep(_ORDINARY_HEARTBEAT_SECONDS)
        state.renewing = True
        state.settled.clear()
        try:
            try:
                renewed = await jobs.renew_claim(claim)
            except Exception:
                raise JobClaimRenewalError(claim) from None
            if renewed is False:
                raise JobClaimLostError(claim) from None
            if renewed is not True:
                raise JobClaimRenewalError(claim) from None
        finally:
            state.renewing = False
            state.settled.set()


async def _run_ordinary_handler(claim: JobClaim, job: Any, handler: Callable, payload: dict) -> Any:
    async def invoke():
        if claim.job_type == "digest":
            from app.services.digest.job_delivery import execute_digest_job

            return await execute_digest_job(job, payload, handler)
        return await handler(payload)

    handler_task = asyncio.create_task(invoke())
    state = _OrdinaryHeartbeatState()
    heartbeat_task = asyncio.create_task(_ordinary_heartbeat(claim, state))
    try:
        await asyncio.wait({handler_task, heartbeat_task}, return_when=asyncio.FIRST_COMPLETED)
        # Do not cancel an in-flight DB acknowledgement merely because the handler finished.
        if handler_task.done() and state.renewing:
            await state.settled.wait()
        # Renewal uncertainty wins even if the handler completed in the same turn.
        if heartbeat_task.done():
            try:
                heartbeat_task.result()
            except (JobClaimLostError, JobClaimRenewalError) as error:
                raise _OrdinaryHeartbeatStopped(error) from None
            except asyncio.CancelledError:
                raise _OrdinaryHeartbeatStopped(JobClaimRenewalError(claim)) from None
            except Exception:
                raise _OrdinaryHeartbeatStopped(JobClaimRenewalError(claim)) from None
            raise _OrdinaryHeartbeatStopped(JobClaimRenewalError(claim)) from None
        return handler_task.result()
    finally:
        # Do not return a receipt while a cancelled handler can still run locally.
        for task in (handler_task, heartbeat_task):
            if not task.done():
                task.cancel()
        drain = asyncio.gather(handler_task, heartbeat_task, return_exceptions=True)
        interrupted = False
        while not drain.done():
            try:
                await asyncio.shield(drain)
            except asyncio.CancelledError:
                interrupted = True
        if interrupted:
            raise asyncio.CancelledError


async def _execute_ordinary(job: Any, handler: Callable, payload: dict, *, allow_retry: bool) -> JobOutcomeReceipt:
    claim = JobClaim.capture(job)  # Capture once, before any handler can mutate observed state.
    # Keep ownership uncertainty outside handler-failure/retry finalization.
    try:
        renewed = await jobs.renew_claim(claim)
    except Exception:
        raise JobClaimRenewalError(claim) from None
    if renewed is False:
        raise JobClaimLostError(claim) from None
    if renewed is not True:
        raise JobClaimRenewalError(claim) from None
    try:
        result = await _run_ordinary_handler(claim, job, handler, payload)
    except _OrdinaryHeartbeatStopped as stopped:
        # Never turn lease uncertainty into handler failure or automatic replay.
        raise stopped.error from None
    except Exception as error:
        from app.services.digest.delivery_outcomes import DeliveryFailure

        known_delivery_failure = isinstance(error, DeliveryFailure)
        retry = allow_retry and known_delivery_failure and error.retryable is True
        stored_error = str(error) if known_delivery_failure else OUTCOME_UNCONFIRMED_PREFIX
        try:
            receipt = await jobs.mark_failed(claim.job_id, error=stored_error, allow_retry=retry, claim=claim)
            receipt = _require_outcome_receipt(receipt, claim)
        except Exception as outcome_error:
            _outcome_write_failed(claim, outcome_error)
        _log_job(
            logging.ERROR if receipt.acknowledgement == OutcomeAck.FAILED else logging.WARNING,
            "job_handler_failed",
            claim,
            error_code=_error_kind(error),
        )
        return await _post_ordinary_outcome(receipt)
    failure = returned_failure(result)
    try:
        if failure is not None:
            receipt = await jobs.mark_failed(
                claim.job_id,
                error=failure.code,
                allow_retry=False,
                result=failure.audit_result(),
                llm_cost=failure.llm_cost,
                claim=claim,
            )
        else:
            receipt = await jobs.mark_done(claim.job_id, result=result, llm_cost=reported_llm_cost(result), claim=claim)
        receipt = _require_outcome_receipt(receipt, claim)
    except Exception as outcome_error:
        _outcome_write_failed(claim, outcome_error)
    if failure is not None:
        _log_job(logging.ERROR, "job_returned_failure", claim, error_code=failure.code)
    return await _post_ordinary_outcome(receipt)


async def execute_job(job: Any, handler: Callable, *, allow_retry: bool = True) -> JobOutcomeReceipt | None:
    """Execute once; ordinary outcomes return a claim-bound committed receipt.

    DB errors stop without a second write. Post-commit projection degradation
    stays distinct from handler failure. Checkpoint digest retains its protocol.
    """
    payload = dict(job.payload or {})
    payload["agent_task_id"] = job.agent_task_id
    payload["job_id"] = job.id
    with tenant_scope(job.tenant_id):
        checkpoint_mode = False
        if job.job_type == "digest":
            from app.services.digest.job_delivery import REFERENCE_KEY, enabled

            checkpoint_mode = enabled() or REFERENCE_KEY in (getattr(job, "result", None) or {})
        if not checkpoint_mode:
            return await _execute_ordinary(job, handler, payload, allow_retry=allow_retry)
        # Keep checkpoint dispatch/finalization, including persisted-reference routing.
        try:
            from app.services.digest.job_delivery import execute_digest_job, finalize_digest_job

            result = await execute_digest_job(job, payload, handler)
            if await finalize_digest_job(job, result=result) is None:
                _log_job(logging.WARNING, "job_completion_claim_lost", job, error_code="claim_lost")
                return None
            _log_job(logging.INFO, "job_done", job)
            if not (isinstance(result, dict) and result.get("status") == "skipped"):
                await _notify_job_result(job, success=True, result=result)
        except Exception as error:
            from app.services.digest.delivery_outcomes import DeliveryFailure
            from app.services.digest.job_delivery import finalize_digest_job

            retry = allow_retry and (not isinstance(error, DeliveryFailure) or error.retryable)
            will_retry = await finalize_digest_job(job, failure=error, allow_retry=retry)
            if will_retry is None:
                _log_job(logging.WARNING, "job_outcome_claim_lost", job, error_code="claim_lost")
                return None
            if will_retry:
                _log_job(logging.WARNING, "job_retry_scheduled", job, error_code=_error_kind(error))
            else:
                _log_job(logging.ERROR, "job_failed_terminal", job, error_code=_error_kind(error))
                await _notify_job_result(job, success=False, error=_FAILURE_MESSAGE)
        return None


def _claimed_outcome(receipt: JobOutcomeReceipt) -> dict[str, Any]:
    if receipt.acknowledgement == OutcomeAck.CLAIM_LOST:
        # Several legacy adapters treat any truthy non-failed dictionary as success.
        # Propagate loss as an explicit error, without touching those occupied surfaces.
        raise JobClaimLostError(receipt.claim) from None
    return receipt.as_outcome()


async def _execute_claimed(job: Any, *, allow_retry: bool = True) -> dict[str, Any]:
    """Return ordinary committed receipts, never a newer attempt's current row."""
    handler = HANDLERS.get(job.job_type)
    if not handler:
        claim = JobClaim.capture(job)
        with tenant_scope(claim.tenant_id):
            try:
                receipt = await jobs.mark_failed(
                    claim.job_id,
                    error=f"Unknown job type: {claim.job_type}",
                    allow_retry=allow_retry,
                    claim=claim,
                )
                receipt = _require_outcome_receipt(receipt, claim)
            except Exception as outcome_error:
                _outcome_write_failed(claim, outcome_error)
        # Preserve the unknown-handler notification policy (no completion notification).
        return _claimed_outcome(receipt)
    receipt = await execute_job(job, handler, allow_retry=allow_retry)
    if receipt is not None:
        return _claimed_outcome(receipt)
    # Only checkpoint digest uses the existing observation protocol in this slice.
    with tenant_scope(job.tenant_id):
        finished = await jobs.get(id=job.id)
    if finished is None:
        return {"status": "unknown", "error": "Job row not found after execution"}
    return {"status": finished.status, "result": finished.result, "error": finished.error}


async def run_job_now(job_id: int, *, allow_retry: bool = True) -> Optional[dict]:
    """Execute one specific job now and return its outcome (operator path).

    Used by `cli.main task run` and any "run this exact thing" surface. The
    difference from `run_pending_once` is the claim: it takes *this* job rather
    than the globally oldest pending one, so running a task of one workspace can
    never execute somebody else's queued work by accident.

    `allow_retry=False` for a manual run: the operator is watching, and a
    background retry would repeat the side effects unattended.
    """
    job = await jobs.claim_job(job_id)
    if not job:
        return None
    return await _execute_claimed(job, allow_retry=allow_retry)


async def _run_claimed_inline(job: Any) -> dict[str, Any]:
    """Execute a committed acquisition; ordinary receipt keeps its own generation."""
    outcome = await _execute_claimed(job, allow_retry=False)
    return {**outcome, "job_id": job.id}


async def run_task_directly(task: Any) -> Optional[dict]:
    """Run a task's job immediately in the caller's process — no queue hop.

    Enqueue then acquire the exact due row; a competing claim runs no handler here.
    The authoritative Job row remains the audit trail for inline execution.
    """
    from app.jobs.enqueue import enqueue_task_run
    from app.models.managers.job_manager import JobManager

    with tenant_scope(task.tenant_id):
        job = await enqueue_task_run(task)
        claimed = await JobManager.claim_job(job.id)
    if claimed is None:
        raise JobNotAcquiredError(job.id, job.tenant_id) from None
    return await _run_claimed_inline(claimed)


async def run_job_inline(job_type: str, payload: dict | None = None, **enqueue_kwargs: Any) -> Optional[dict]:
    """Execute a one-off job immediately; enqueue kwargs require tenant scope."""
    from app.models.managers.job_manager import JobManager

    job = await JobManager().enqueue(job_type=job_type, payload=payload or {}, **enqueue_kwargs)
    claimed = await JobManager.claim_job(job.id)
    if claimed is None:
        raise JobNotAcquiredError(job.id, job.tenant_id) from None
    return await _run_claimed_inline(claimed)


async def run_pending_once() -> int:
    """Claim and execute one due job. Returns number processed (0 or 1)."""
    with tenant_scope(bypass=True):
        await jobs.reap_stale()
        await jobs.cleanup_done()
    job = await jobs.claim_next()
    if not job:
        return 0
    await _execute_claimed(job)
    return 1


async def drain(max_jobs: int | None = None) -> int:
    """Process all due jobs (bounded). Used by worker loop and tests."""
    processed = 0
    limit = max_jobs or 100
    while processed < limit:
        n = await run_pending_once()
        if not n:
            break
        processed += n
    return processed


async def worker_forever(poll_seconds: int = 5) -> None:
    """Worker loop: poll the jobs table and process due jobs."""
    logger.info("worker_started")
    while True:
        try:
            processed = await drain(max_jobs=10)
            await asyncio.sleep(0 if processed else poll_seconds)
        except asyncio.CancelledError:
            logger.info("worker_stopped")
            break
        except Exception as error:
            logger.error("worker_iteration_failed error_code=%s", _error_kind(error))
            await asyncio.sleep(poll_seconds)
