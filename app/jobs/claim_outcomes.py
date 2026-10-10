"""Immutable ordinary-queue ownership and committed outcome receipts.

These values are not database statuses or proof of external-effect fencing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


@dataclass(frozen=True)
class JobClaim:
    """Identity captured before the handler from a successfully acquired Job."""

    job_id: int
    tenant_id: int
    job_type: str
    agent_task_id: int | None
    attempts: int
    started_at: datetime

    def __post_init__(self) -> None:
        for value in (self.job_id, self.tenant_id, self.attempts):
            if type(value) is not int or value <= 0:
                raise ValueError("Job claim requires positive persisted IDs and generation")
        if self.agent_task_id is not None and (type(self.agent_task_id) is not int or self.agent_task_id <= 0):
            raise ValueError("Job claim requires a persisted task ID or None")
        if type(self.job_type) is not str or not self.job_type:
            raise ValueError("Job claim requires a persisted job type")
        if not isinstance(self.started_at, datetime) or self.started_at.utcoffset() is None:
            raise ValueError("Job claim requires an aware persisted start time")

    @property
    def id(self) -> int:
        return self.job_id

    @classmethod
    def capture(cls, job: Any) -> JobClaim:
        if job.status != "running":
            raise ValueError("Job claim requires a running acquired row")
        return cls(job.id, job.tenant_id, job.job_type, job.agent_task_id, job.attempts, job.started_at)


class OutcomeAck(str, Enum):
    DONE = "committed_done"
    FAILED = "committed_failed"
    RETRY = "committed_retry"
    CLAIM_LOST = "claim_lost"


@dataclass(frozen=True)
class JobOutcomeReceipt:
    """A matching committed write, or explicit claim loss without current-row data."""

    claim: JobClaim
    acknowledgement: OutcomeAck
    result: Any = field(default=None, repr=False)
    error: str | None = field(default=None, repr=False)
    task_projection_failed: bool = False
    notification_projection_failed: bool = False

    def as_outcome(self) -> dict[str, Any]:
        status = {
            OutcomeAck.DONE: "done",
            OutcomeAck.FAILED: "failed",
            OutcomeAck.RETRY: "pending",
            OutcomeAck.CLAIM_LOST: "claim_lost",
        }[self.acknowledgement]
        return {
            "status": status,
            "result": self.result,
            "error": self.error,
            "job_id": self.claim.job_id,
            "task_projection_failed": self.task_projection_failed,
            "notification_projection_failed": self.notification_projection_failed,
        }


class JobClaimLostError(RuntimeError):
    """A direct caller must not render loss as completion or a newer receipt."""

    error_code = "claim_lost"

    def __init__(self, claim: JobClaim):
        self.job_id = claim.job_id
        self.tenant_id = claim.tenant_id
        super().__init__("Job claim lost; this attempt's completion is not confirmed.")


class JobNotAcquiredError(RuntimeError):
    """A direct enqueue/acquire race executed no handler for this caller."""

    error_code = "not_acquired"

    def __init__(self, job_id: int, tenant_id: int):
        self.job_id = job_id
        self.tenant_id = tenant_id
        super().__init__("Job was not acquired; no handler was executed by this caller.")
