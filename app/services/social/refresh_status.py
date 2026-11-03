"""Pure, secret-free interpretation of a persisted refresh-failure marker."""

from datetime import datetime, timedelta, timezone

REFRESH_FAILURE_COOLDOWN = timedelta(hours=6)


def refresh_failure_blocks(value, *, now: datetime) -> bool:
    """Treat malformed nonempty markers as blocked, matching the refresh path."""
    if not value:
        return False
    try:
        failed = datetime.fromisoformat(str(value))
        failed = failed if failed.tzinfo else failed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return True
    return now - failed < REFRESH_FAILURE_COOLDOWN
