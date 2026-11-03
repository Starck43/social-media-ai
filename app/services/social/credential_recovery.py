"""Narrow, operator-initiated recovery of an unsent VK refresh attempt."""

from __future__ import annotations


class RecoveryRefused(RuntimeError):
    """A deliberately nonspecific refusal; never include credential data."""


async def recover_unreadable_vk(*, user_id: int, credential_id: int, apply: bool) -> str:
    """Probe both secrets and optionally clear only the exact unsent marker.

    Call only in an authorized maintenance window with all credential writers
    stopped and a consistent key on every instance. No provider request is made.
    The expected row ID prevents a stale operator selection affecting a new login.
    """
    from app.models.managers.user_credential_manager import user_credentials
    from app.utils.crypto import decrypt_secret

    if type(user_id) is not int or user_id <= 0 or type(credential_id) is not int or credential_id <= 0:
        raise RecoveryRefused("Invalid target")
    async with user_credentials.locked_vk_token(user_id=user_id) as (session, row):
        if row is None or row.id != credential_id or not isinstance(row.meta, dict):
            raise RecoveryRefused("Target changed or unavailable")
        meta = dict(row.meta)
        # Only this exact value is written before any provider request. Pending,
        # failed, extended or malformed claims may have consumed a token.
        if meta.get("refresh_attempt") != {"state": "unreadable"}:
            raise RecoveryRefused("Attempt is not eligible for recovery")
        if meta.get("refresh_available") is not True or "refresh_token" in meta or not row.refresh_secret_encrypted:
            raise RecoveryRefused("Refresh material is unavailable")
        try:
            access = row.reveal()
            refresh = decrypt_secret(row.refresh_secret_encrypted)
            if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
                raise ValueError("Empty secret")
        except (ValueError, RuntimeError, TypeError, UnicodeError, AttributeError):
            raise RecoveryRefused("Configured key cannot read the target") from None
        if apply:
            meta.pop("refresh_attempt")
            await user_credentials.update_by_id(row.id, session=session, meta=meta)
            return "cleared"
        return "eligible"
