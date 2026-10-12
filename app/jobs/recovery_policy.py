"""Bounded ordinary-queue uncertainty vocabulary; no I/O or schema changes."""

from app.jobs.attempt_budget import ATTEMPT_BUDGET_STOP_PREFIX

OUTCOME_UNCONFIRMED_PREFIX = "Queue outcome unconfirmed; inspect before retry. "
# Existing cancellation writes use this exact value; no historical rewrite is needed.
OPERATOR_CANCELLED_ERROR = "Cancelled by operator"
# A pending row cancelled before any claim: no handler ran, so nothing is unconfirmed.
CANCELLED_BEFORE_START_ERROR = "Cancelled before start"


def outcome_unconfirmed(error) -> bool:
    """Operational stops, including cancellation, do not confirm external failure."""
    return type(error) is str and (
        error == OPERATOR_CANCELLED_ERROR or error.startswith((OUTCOME_UNCONFIRMED_PREFIX, ATTEMPT_BUDGET_STOP_PREFIX))
    )
