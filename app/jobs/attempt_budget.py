"""Shared acquisition predicates and a bounded operational-stop marker.

This is an attempt cap, not external-effect idempotency or a spend ledger.
"""

ATTEMPT_BUDGET_STOP_PREFIX = "Queue attempt budget stopped; outcome unconfirmed. "


def available_attempt_budget(model):
    """A persisted nonnegative attempt counter below a positive limit."""
    from sqlalchemy import and_

    return and_(model.attempts >= 0, model.max_attempts > 0, model.attempts < model.max_attempts)


def unavailable_attempt_budget(model):
    """Exhausted or malformed counters fail closed, never become unlimited."""
    from sqlalchemy import or_

    return or_(
        model.attempts.is_(None),
        model.max_attempts.is_(None),
        model.attempts < 0,
        model.max_attempts <= 0,
        model.attempts >= model.max_attempts,
    )
