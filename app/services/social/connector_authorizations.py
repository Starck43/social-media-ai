"""Explicit reuse of the same person's authorization, never ambient cross-tenant access.

The bounded Telegram login adapter calls the internal completion only AFTER
provider identity verification. No HTTP/CLI endpoint accepts a StringSession.
Persistence and login do not yet supply a collector grant resolver.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import select, update
from sqlalchemy.orm import defer

from app.core.database import async_session_maker
from app.core.permissions import get_current_user
from app.core.tenant_context import current_tenant_id, is_bypass
from app.models import PersonalConnectorAuthorization as Authorization
from app.models import User
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
    not a typed phone/username.
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


def _decode_telegram_material(row):
    # A capability allowlist, not "all connectors can share credentials".
    if row.provider_id != PROVIDER or row.auth_method != METHOD or row.state != "active":
        raise AuthorizationUnavailable()
    try:
        payload = json.loads(decrypt_secret(row.secret_encrypted))
        if not isinstance(payload, dict) or set(payload) != {"identity", "material"}:
            raise AuthorizationUnavailable()
        identity = payload["identity"]
        expected = _identity(row)
        # JSON booleans/floats must not compare equal to integer envelope pins.
        if (
            not isinstance(identity, dict)
            or identity != expected
            or any(type(identity[key]) is not type(value) for key, value in expected.items())
        ):
            raise AuthorizationUnavailable()
        _validate_material(payload["material"])
        return payload["material"]
    except (ValueError, RuntimeError, TypeError, UnicodeError, RecursionError):
        raise AuthorizationUnavailable() from None


def _check_reusable(row):
    # Do not return plaintext to metadata/reuse callers. The internal runtime
    # resolver uses the identical envelope validation, not a weaker decoder.
    _decode_telegram_material(row)


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


async def _account(session, actor_id, authorization_id, *, include_secret=True):
    statement = (
        select(Authorization)
        .where(Authorization.id == _positive(authorization_id), Authorization.owner_user_id == actor_id)
        .with_for_update()
    )
    if not include_secret:
        statement = statement.options(defer(Authorization.secret_encrypted, raiseload=True))
    row = (await session.execute(statement)).scalar_one_or_none()
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


async def _authorize_personal(session, actor_id):
    """Account self-service only; no workspace, operator or ambient-actor bypass."""
    _positive(actor_id)
    actor = get_current_user()
    if actor is None or actor.id != actor_id or not actor.is_active or current_tenant_id() is not None or is_bypass():
        raise AuthorizationUnavailable()
    active = (
        await session.execute(
            select(User.id).where(User.id == actor_id, User.is_active.is_(True)).with_for_update(read=True)
        )
    ).scalar_one_or_none()
    if active is None:
        raise AuthorizationUnavailable()


async def _revoke_owned(session, *, actor_id, authorization_id, expected_revision):
    account = await _account(session, actor_id, authorization_id, include_secret=False)
    if account.revision != expected_revision:
        raise AuthorizationUnavailable()
    if account.state != "revoked":
        account.revision += 1
    account.state, account.secret_encrypted = "revoked", ""
    # Repeating with the current revision also repairs inconsistent local rows;
    # never leave active grants or ciphertext merely because state says revoked.
    await session.execute(
        update(Grant)
        .where(Grant.authorization_id == account.id, Grant.owner_user_id == actor_id)
        .values(is_active=False)
    )
    return _account_projection(account)


class ConnectorAuthorizationManager:
    async def list_personal(self, *, actor_id):
        """Own Telegram identities, including unusable ones, without any membership.

        No source/workspace/grant metadata or credential ciphertext is selected.
        This is recovery/revocation metadata, not a provider-health catalog.
        """
        async with async_session_maker() as session, session.begin():
            await _authorize_personal(session, actor_id)
            rows = (
                (
                    await session.execute(
                        select(
                            Authorization.id.label("authorization_id"),
                            Authorization.provider_id,
                            Authorization.auth_method,
                            Authorization.subject_key,
                            Authorization.revision,
                            Authorization.state,
                        )
                        .where(
                            Authorization.owner_user_id == actor_id,
                            Authorization.provider_id == PROVIDER,
                            Authorization.auth_method == METHOD,
                        )
                        .order_by(Authorization.id)
                    )
                )
                .mappings()
                .all()
            )
            return [dict(row) for row in rows]

    async def revoke_personal(self, *, actor_id, authorization_id, expected_revision):
        """Withdraw own authorization everywhere, even with no workspace access.

        This is local ciphertext/grant invalidation, not a Telegram logout.
        Rights are checked from the current active account, never a tenant header.
        """
        _positive(expected_revision)
        async with async_session_maker() as session, session.begin():
            await _authorize_personal(session, actor_id)
            return await _revoke_owned(
                session, actor_id=actor_id, authorization_id=authorization_id, expected_revision=expected_revision
            )

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
            return await _revoke_owned(
                session, actor_id=actor_id, authorization_id=authorization_id, expected_revision=expected_revision
            )


async def _record_telegram_result(session, *, tenant_id, actor_id, result: VerifiedTelegramResult):
    """Trusted completion in the caller's authorized transaction, including its attempt."""
    if not isinstance(result, VerifiedTelegramResult):
        raise AuthorizationUnavailable()
    _positive(result.subject_id)
    material = {"api_id": result.api_id, "api_hash": result.api_hash, "session": result.session}
    _validate_material(material)
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
    account.secret_encrypted = encrypt_secret(json.dumps({"identity": _identity(account), "material": material}))
    grant = await _attach(session, tenant_id=tenant_id, actor_id=actor_id, account=account)
    return _account_projection(account), grant


async def _record_telegram_recovery(session, *, actor_id, authorization_id, expected_revision, result):
    """Replace only the provider-verified same subject; no workspace is reconsented.

    The caller has freshly authorized its actor and owns the claimed login row.
    This routine does not read/decrypt the old envelope, so wrong-key recovery
    needs a new login, not access to lost credentials. All writes are atomic.
    """
    if type(result) is not VerifiedTelegramResult:
        raise AuthorizationUnavailable()
    _positive(result.subject_id)
    _positive(expected_revision)
    account = await _account(session, actor_id, authorization_id, include_secret=False)
    if (
        account.provider_id != PROVIDER
        or account.auth_method != METHOD
        or account.revision != expected_revision
        or account.subject_key != str(result.subject_id)
    ):
        raise AuthorizationUnavailable()
    material = {"api_id": result.api_id, "api_hash": result.api_hash, "session": result.session}
    _validate_material(material)
    account.revision += 1
    account.execution_generation += 1
    account.state = "active"
    account.secret_encrypted = encrypt_secret(json.dumps({"identity": _identity(account), "material": material}))
    await session.execute(update(Grant).where(Grant.authorization_id == account.id).values(is_active=False))
    await session.flush()
    return _account_projection(account)


async def record_telegram_result(*, tenant_id, actor_id, result: VerifiedTelegramResult):
    """Internal trusted completion; no public session-import or legacy migration."""
    from sqlalchemy.exc import IntegrityError

    try:
        async with async_session_maker() as session, session.begin():
            await _authorize(session, tenant_id, actor_id, write=True, require_source_view=True)
            return await _record_telegram_result(session, tenant_id=tenant_id, actor_id=actor_id, result=result)
    except IntegrityError:
        raise AuthorizationAlreadyExists() from None


connector_authorizations = ConnectorAuthorizationManager()
