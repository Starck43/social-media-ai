"""Bounded ordinary-queue uncertainty vocabulary; no I/O or schema changes."""

from app.jobs.attempt_budget import ATTEMPT_BUDGET_STOP_PREFIX

OUTCOME_UNCONFIRMED_PREFIX = "Queue outcome unconfirmed; inspect before retry. "


def outcome_unconfirmed(error) -> bool:
    """Unknown/exhausted operational stops are not a confirmed external failure."""
    return type(error) is str and error.startswith((OUTCOME_UNCONFIRMED_PREFIX, ATTEMPT_BUDGET_STOP_PREFIX))
