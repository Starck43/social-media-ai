"""Explicit reuse of the same person's authorization, never ambient cross-tenant access.

Foundation only: the trusted Telegram login completion calls record_telegram_result
AFTER provider identity verification. No HTTP/CLI endpoint accepts a StringSession
or calls that completion yet. Do not confuse this persistence contract with a
shipped phone/code/2FA journey or a collector secret resolver.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import select, update

from app.core.database import async_session_maker
from app.models import PersonalConnectorAuthorization as Authorization
from app.models import WorkspaceAuthorizationGrant as Grant
from app.models.managers.workspace_connection_manager import ConnectionDenied, _authorize
from app.utils.crypto import decrypt_secret, encrypt_secret

PROVIDER = "telegram"
METHOD = "mtproto"


class AuthorizationUnavailable(ConnectionDenied):
    """Deliberately opaque: no account existence, key or provider detail to callers."""


class AuthorizationAlreadyExists(ValueError):
    def __init__(self):
        super().__init__("use_existing_authorization_or_explicit_reauthorization")


@dataclass(frozen=True, repr=False)
class VerifiedTelegramResult:
    """Internal trusted adapter result; never construct from HTTP request values.

    subject_id must come from Telegram get_me AFTER completed authentication,
    not a typed phone/username. The login adapter still needs to be implemented.
    """

    subject_id: int
    api_id: int
    api_hash: str = field(repr=False)
    session: str = field(repr=False)


def _positive(value):
    if type(value) is not int or value <= 0:
        raise AuthorizationUnavailable()
    return value


def _identity(row):
    return {
        "purpose": "personal_connector_authorization",
        "format": 1,
        "id": row.id,
        "owner_user_id": row.owner_user_id,
        "provider_id": row.provider_id,
        "auth_method": row.auth_method,
        "subject_key": row.subject_key,
        "revision": row.revision,
    }


def _validate_material(material):
    if not isinstance(material, dict) or set(material) != {"api_id", "api_hash", "session"}:
        raise AuthorizationUnavailable()
    _positive(material["api_id"])
    for key, maximum in (("api_hash", 256), ("session", 16384)):
        value = material[key]
        if not isinstance(value, str) or not value or len(value) > maximum or any(ord(c) < 32 for c in value):
            raise AuthorizationUnavailable()


def _check_reusable(row):
    # A capability allowlist, not "all connectors can share credentials".
    if row.provider_id != PROVIDER or row.auth_method != METHOD or row.state != "active":
        raise AuthorizationUnavailable()
    try:
        payload = json.loads(decrypt_secret(row.secret_encrypted))
        if not isinstance(payload, dict) or set(payload) != {"identity", "material"}:
            raise AuthorizationUnavailable()
        if payload["identity"] != _identity(row):
            raise AuthorizationUnavailable()
        _validate_material(payload["material"])
    except (ValueError, RuntimeError, TypeError, UnicodeError, RecursionError):
        raise AuthorizationUnavailable() from None
    # Do not return plaintext to metadata/reuse callers.


def _account_projection(row):
    return {
        "authorization_id": row.id,
        "provider_id": row.provider_id,
        "auth_method": row.auth_method,
        "subject_key": row.subject_key,
        "revision": row.revision,
        "state": row.state,
    }


def _grant_projection(row):
    return {
        "grant_id": row.id,
        "authorization_id": row.authorization_id,
        "authorization_revision": row.authorization_revision,
        "is_active": row.is_active,
    }


async def _account(session, actor_id, authorization_id):
    row = (
        await session.execute(
            select(Authorization)
            .where(Authorization.id == _positive(authorization_id), Authorization.owner_user_id == actor_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise AuthorizationUnavailable()
    return row


async def _attach(session, *, tenant_id, actor_id, account):
    _check_reusable(account)
    row = (
        await session.execute(
            select(Grant)
            .where(
                Grant.tenant_id == tenant_id,
                Grant.owner_user_id == actor_id,
                Grant.authorization_id == account.id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        row = Grant(tenant_id=tenant_id, owner_user_id=actor_id, authorization_id=account.id)
        session.add(row)
    row.authorization_revision, row.is_active = account.revision, True
    await session.flush()
    return _grant_projection(row)


class ConnectorAuthorizationManager:
    async def list_reusable(self, *, tenant_id, actor_id):
        """Only the actor's own identities, no source-workspace names or secrets.

        Metadata does not promise that Telegram still accepts the session.
        Attach separately verifies local decryption and payload integrity.
        """
        async with async_session_maker() as session, session.begin():
            can_write = await _authorize(session, tenant_id, actor_id, require_source_view=True)
            rows = (
                (
                    await session.execute(
                        select(Authorization)
                        .where(
                            Authorization.owner_user_id == actor_id,
                            Authorization.provider_id == PROVIDER,
                            Authorization.auth_method == METHOD,
                            Authorization.state == "active",
                        )
                        .order_by(Authorization.id)
                    )
                )
                .scalars()
                .all()
            )
            return [_account_projection(row) for row in rows], can_write

    async def attach(self, *, tenant_id, actor_id, authorization_id, expected_revision):
        """Explicit selection/consent; no login/key entry, secret copy or provider I/O.

        Locks serialize same-account attach/revoke; the unique constraint protects
        duplicate target grants. A repeated attach returns the same grant ID.
        """
        _positive(expected_revision)
        async with async_session_maker() as session, session.begin():
            await _authorize(session, tenant_id, actor_id, write=True, require_source_view=True)
            account = await _account(session, actor_id, authorization_id)
            if account.revision != expected_revision:
                raise AuthorizationUnavailable()
            return await _attach(session, tenant_id=tenant_id, actor_id=actor_id, account=account)

    async def list_grants(self, *, tenant_id, actor_id):
        async with async_session_maker() as session, session.begin():
            await _authorize(session, tenant_id, actor_id, require_source_view=True)
            rows = (
                (
                    await session.execute(
                        select(Grant).where(Grant.tenant_id == tenant_id, Grant.owner_user_id == actor_id)
                    )
                )
                .scalars()
                .all()
            )
            return [_grant_projection(row) for row in rows]

    async def detach(self, *, tenant_id, actor_id, grant_id):
        """Local disconnect preserves the personal authorization and other grants.

        Viewing members may withdraw their own consent even after losing CREATE.
        No remote Telegram logout/revoke is implied.
        """
        async with async_session_maker() as session, session.begin():
            await _authorize(session, tenant_id, actor_id, require_source_view=True)
            row = (
                await session.execute(
                    select(Grant).where(
                        Grant.id == _positive(grant_id), Grant.tenant_id == tenant_id, Grant.owner_user_id == actor_id
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                raise AuthorizationUnavailable()
            # Same account -> grant lock order as attach/revoke, not the reverse.
            await _account(session, actor_id, row.authorization_id)
            await session.refresh(row, with_for_update=True)
            row.is_active = False
            return _grant_projection(row)

    async def revoke(self, *, tenant_id, actor_id, authorization_id, expected_revision):
        """Explicit personal-wide local revocation, distinct from workspace detach.

        Clear the sole ciphertext and invalidate all grants in one transaction.
        Provider-side session revocation is separate and is not attempted here.
        """
        _positive(expected_revision)
        async with async_session_maker() as session, session.begin():
            await _authorize(session, tenant_id, actor_id, require_source_view=True)
            account = await _account(session, actor_id, authorization_id)
            if account.revision != expected_revision:
                raise AuthorizationUnavailable()
            if account.state != "revoked":
                account.revision += 1
                account.state, account.secret_encrypted = "revoked", ""
                await session.execute(
                    update(Grant)
                    .where(Grant.authorization_id == account.id, Grant.owner_user_id == actor_id)
                    .values(is_active=False)
                )
            return _account_projection(account)


async def record_telegram_result(*, tenant_id, actor_id, result: VerifiedTelegramResult):
    """Internal login-completion seam, NOT a proof of provider authentication.

    The future login adapter must verify the session/identity before calling this.
    It cannot silently overwrite an existing account or revive revoked grants.
    There is deliberately no public session-import endpoint or legacy migration.
    """
    from sqlalchemy.exc import IntegrityError

    if not isinstance(result, VerifiedTelegramResult):
        raise AuthorizationUnavailable()
    _positive(result.subject_id)
    material = {"api_id": result.api_id, "api_hash": result.api_hash, "session": result.session}
    _validate_material(material)
    try:
        async with async_session_maker() as session, session.begin():
            await _authorize(session, tenant_id, actor_id, write=True, require_source_view=True)
            existing = (
                await session.execute(
                    select(Authorization.id).where(
                        Authorization.owner_user_id == actor_id,
                        Authorization.provider_id == PROVIDER,
                        Authorization.auth_method == METHOD,
                        Authorization.subject_key == str(result.subject_id),
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise AuthorizationAlreadyExists()
            account = Authorization(
                owner_user_id=actor_id,
                provider_id=PROVIDER,
                auth_method=METHOD,
                subject_key=str(result.subject_id),
                state="active",
                revision=1,
                secret_encrypted="",
            )
            session.add(account)
            await session.flush()
            account.secret_encrypted = encrypt_secret(
                json.dumps({"identity": _identity(account), "material": material})
            )
            grant = await _attach(session, tenant_id=tenant_id, actor_id=actor_id, account=account)
            return _account_projection(account), grant
    except IntegrityError:
        raise AuthorizationAlreadyExists() from None


connector_authorizations = ConnectorAuthorizationManager()
