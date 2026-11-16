"""User-bound, expiring navigation intents; never action approvals or read receipts."""

from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.core.config import settings

MAX_AGE = 7 * 24 * 60 * 60


def _signer():
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt="personal-notification-navigation-v1")


def open_link(notification_id: int, user_id: int) -> str:
    token = _signer().dumps({"notification_id": notification_id, "user_id": user_id})
    return f"/app/notifications/open/{notification_id}?intent={token}"


def valid_intent(token: str, notification_id: int, user_id: int) -> bool:
    if not isinstance(token, str) or len(token) > 2048:
        return False
    try:
        return _signer().loads(token, max_age=MAX_AGE) == {"notification_id": notification_id, "user_id": user_id}
    except BadSignature:
        return False
