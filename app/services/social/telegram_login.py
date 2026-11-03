"""Durable, owner/workspace-bound Telegram login with no ambiguous-step retries.

Each provider operation is claimed and committed before network I/O. A process
crash leaves `processing`, never a reusable code/password submission. Secrets
are encrypted at rest; only bounded metadata crosses the service boundary.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.database import async_engine, async_session_maker
from app.models import TelegramLoginAttempt as Attempt
from app.models import TelegramLoginRequestKey as RequestKey
from app.models.managers.workspace_connection_manager import ConnectionDenied, _authorize
from app.services.social.connector_authorizations import (
    AuthorizationAlreadyExists,
    AuthorizationUnavailable,
    _account,
    _positive,
    _record_telegram_recovery,
    _record_telegram_result,
)
from app.services.social.telegram_login_provider import LoginProgress, TelegramProviderFailure, telegram_login_step
from app.utils.crypto import decrypt_secret, encrypt_secret

PENDING = {"processing", "code", "password"}
TTL = timedelta(minutes=10)
LIMITS = {"code": 5, "password": 3}
PROVIDER_FAILURES = {
    "provider_outcome_unknown",
    "adapter_unavailable",
    "provider_rate_limited",
    "authentication_failed",
}


class LoginRejected(ValueError):
    def __init__(self, code="login_conflict", status=409):
        self.code, self.status = code, status
        super().__init__(code)


def _projection(row):
    return {
        "attempt_id": row.id,
        "state": row.state,
        "revision": row.revision,
        "expires_at": row.expires_at,
        "failure_code": row.failure_code,
        "code_attempts_remaining": max(0, LIMITS["code"] - row.code_attempts),
        "password_attempts_remaining": max(0, LIMITS["password"] - row.password_attempts),
        "authorization_id": row.authorization_id,
        "grant_id": row.grant_id,
        "recovery_authorization_id": getattr(row, "recovery_authorization_id", None),
    }


def _identity(row):
    identity = {
        "purpose": "telegram_login",
        "format": 1,
        "id": row.id,
        "owner_user_id": row.owner_user_id,
        "tenant_id": row.tenant_id,
        "revision": row.revision,
        "state": row.state,
    }
    if getattr(row, "recovery_authorization_id", None) is not None:
        identity.update(
            recovery_authorization_id=row.recovery_authorization_id, recovery_revision=row.recovery_revision
        )
    return identity


def _validate(material):
    if not isinstance(material, dict) or set(material) != {"api_id", "api_hash", "phone", "session", "phone_code_hash"}:
        raise ValueError()
    if type(material["api_id"]) is not int or not 0 < material["api_id"] < 2**31:
        raise ValueError()
    if not isinstance(material["api_hash"], str) or not re.fullmatch(r"[a-fA-F0-9]{32}", material["api_hash"]):
        raise ValueError()
    if not isinstance(material["phone"], str) or not re.fullmatch(r"\+[1-9][0-9]{6,14}", material["phone"]):
        raise ValueError()
    for name, limit in (("session", 16384), ("phone_code_hash", 512)):
        value = material[name]
        if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
            raise ValueError()


def _seal(row, material):
    _validate(material)
    row.secret_encrypted = encrypt_secret(json.dumps({"identity": _identity(row), "material": material}))


def _open(row):
    payload = json.loads(decrypt_secret(row.secret_encrypted))
    if (
        not isinstance(payload, dict)
        or set(payload) != {"identity", "material"}
        or payload["identity"] != _identity(row)
    ):
        raise ValueError()
    _validate(payload["material"])
    return payload["material"]


def _finish(row, state, failure=None):
    row.state, row.failure_code, row.secret_encrypted = state, failure, ""
    row.revision += 1


async def _now(session):
    return (await session.execute(select(func.clock_timestamp()))).scalar_one()


async def _row(session, tenant_id, actor_id, attempt_id):
    row = (
        await session.execute(
            select(Attempt)
            .where(Attempt.id == attempt_id, Attempt.tenant_id == tenant_id, Attempt.owner_user_id == actor_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise ConnectionDenied()
    return row


async def _expire(session, row):
    if row.state in PENDING and row.expires_at <= await _now(session):
        _finish(row, "expired", "login_expired")


def _private_logging():
    # SQL echo/result diagnostics may expose stored encrypted login material.
    # Refuse this journey rather than promise secret-safe debug traces.
    if settings.DEBUG or async_engine.echo or logging.getLogger("sqlalchemy.engine.Engine").isEnabledFor(logging.INFO):
        raise LoginRejected("private_login_logging_required", 503)


class TelegramLoginManager:
    async def start(self, *, tenant_id, actor_id, request_id, phone, authorization_id=None, expected_revision=None):
        _private_logging()
        if (authorization_id is None) != (expected_revision is None):
            raise LoginRejected("invalid_login_input", 422)
        if authorization_id is not None:
            _positive(authorization_id)
            _positive(expected_revision)
        try:
            request_key = str(UUID(str(request_id)))
        except (ValueError, TypeError, AttributeError):
            raise LoginRejected("invalid_login_input", 422) from None
        if not isinstance(phone, str) or not re.fullmatch(r"\+[1-9][0-9]{6,14}", phone):
            raise LoginRejected("invalid_login_input", 422)
        async with async_session_maker() as session, session.begin():
            await _authorize(session, tenant_id, actor_id, write=True, require_source_view=True)
            # Same actor across all workspaces, not a per-process mutex.
            await session.execute(text("SELECT pg_advisory_xact_lock(721214, :actor)"), {"actor": actor_id})
            now = await _now(session)
            rows = (
                (
                    await session.execute(
                        select(Attempt)
                        .where(
                            Attempt.owner_user_id == actor_id,
                            or_(
                                Attempt.state.in_(PENDING),
                                Attempt.created_at >= now - TTL,
                                Attempt.request_key == request_key,
                            ),
                        )
                        .order_by(Attempt.id)
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                if row.state in PENDING and row.expires_at <= now:
                    _finish(row, "expired", "login_expired")
            for row in rows:
                if row.request_key == request_key:
                    if (
                        row.tenant_id != tenant_id
                        or row.recovery_authorization_id != authorization_id
                        or row.recovery_revision != expected_revision
                    ):
                        raise LoginRejected()
                    return _projection(row)  # Same request cannot cause another send-code.
            if (
                await session.scalar(
                    select(RequestKey.request_key).where(
                        RequestKey.owner_user_id == actor_id, RequestKey.request_key == request_key
                    )
                )
                is not None
            ):
                raise LoginRejected("login_request_retired")
            if any(row.state in PENDING for row in rows):
                raise LoginRejected("login_already_pending")
            if sum(row.created_at >= now - TTL for row in rows) >= 3:
                raise LoginRejected("login_rate_limited", 429)
            if authorization_id is not None:
                account = await _account(session, actor_id, authorization_id, include_secret=False)
                if (
                    account.provider_id != "telegram"
                    or account.auth_method != "mtproto"
                    or account.revision != expected_revision
                ):
                    raise AuthorizationUnavailable()
            api_id = settings.TELEGRAM_API_ID
            if not isinstance(api_id, str) or not re.fullmatch(r"[0-9]{1,10}", api_id):
                raise LoginRejected("login_configuration_unavailable", 503)
            await session.flush()  # Release expired partial-unique entries before inserting.
            material = {
                "api_id": int(api_id),
                "api_hash": settings.TELEGRAM_API_HASH,
                "phone": phone,
                "session": "",
                "phone_code_hash": "",
            }
            row = Attempt(
                id=str(uuid4()),
                request_key=request_key,
                tenant_id=tenant_id,
                owner_user_id=actor_id,
                state="processing",
                revision=1,
                code_attempts=0,
                password_attempts=0,
                expires_at=now + TTL,
                recovery_authorization_id=authorization_id,
                recovery_revision=expected_revision,
            )
            try:
                _seal(row, material)
            except (ValueError, RuntimeError, TypeError, UnicodeError):
                raise LoginRejected("login_configuration_unavailable", 503) from None
            session.add(RequestKey(owner_user_id=actor_id, request_key=request_key))
            session.add(row)
            await session.flush()
            attempt_id, revision = row.id, row.revision
        return await self._drive(tenant_id, actor_id, attempt_id, revision, "begin", material)

    async def status(self, *, tenant_id, actor_id, attempt_id):
        async with async_session_maker() as session, session.begin():
            await _authorize(session, tenant_id, actor_id, require_source_view=True)
            row = await _row(session, tenant_id, actor_id, attempt_id)
            await _expire(session, row)
            return _projection(row)

    async def status_by_request(self, *, tenant_id, actor_id, request_id):
        """Rediscover an uncertain start without ever calling the provider."""
        try:
            key = str(UUID(str(request_id)))
        except (ValueError, TypeError, AttributeError):
            raise LoginRejected("invalid_login_input", 422) from None
        async with async_session_maker() as session, session.begin():
            await _authorize(session, tenant_id, actor_id, require_source_view=True)
            row = (
                await session.execute(
                    select(Attempt)
                    .where(
                        Attempt.tenant_id == tenant_id,
                        Attempt.owner_user_id == actor_id,
                        Attempt.request_key == key,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                raise ConnectionDenied()
            await _expire(session, row)
            return _projection(row)

    async def cancel(self, *, tenant_id, actor_id, attempt_id):
        async with async_session_maker() as session, session.begin():
            await _authorize(session, tenant_id, actor_id, require_source_view=True)
            row = await _row(session, tenant_id, actor_id, attempt_id)
            await _expire(session, row)
            if row.state in PENDING:
                _finish(row, "cancelled")
            return _projection(row)

    async def submit(self, *, tenant_id, actor_id, attempt_id, expected_revision, operation, secret):
        _private_logging()
        if (
            operation not in LIMITS
            or type(expected_revision) is not int
            or expected_revision <= 0
            or not isinstance(secret, str)
            or not secret
            or len(secret) > 256
            or (operation == "code" and not re.fullmatch(r"[0-9]{5,8}", secret))
        ):
            raise LoginRejected("invalid_login_input", 422)
        async with async_session_maker() as session, session.begin():
            await _authorize(session, tenant_id, actor_id, write=True, require_source_view=True)
            row = await _row(session, tenant_id, actor_id, attempt_id)
            await _expire(session, row)
            if row.state == "expired":
                return _projection(row)
            if row.revision != expected_revision or row.state != operation:
                raise LoginRejected()
            try:
                material = _open(row)
                row.state, row.revision = "processing", row.revision + 1
                setattr(row, operation + "_attempts", getattr(row, operation + "_attempts") + 1)
                _seal(row, material)
            except (ValueError, RuntimeError, TypeError, UnicodeError, RecursionError):
                _finish(row, "failed", "secret_unavailable")
                return _projection(row)
            revision = row.revision
        return await self._drive(tenant_id, actor_id, attempt_id, revision, operation, material, secret)

    async def _drive(self, tenant_id, actor_id, attempt_id, revision, operation, material, secret=None):
        try:
            progress = await telegram_login_step(operation, material, secret)
            if not isinstance(progress, LoginProgress):
                raise TelegramProviderFailure()
            failure = None
        except asyncio.CancelledError:
            # The durable processing fence remains safe even if this cleanup is
            # interrupted or the DB is unavailable. Never retry the provider step.
            task = asyncio.create_task(
                self._settle(tenant_id, actor_id, attempt_id, revision, operation, None, "provider_outcome_unknown")
            )
            try:
                await asyncio.shield(task)
            except Exception:
                # Cleanup failure must not replace cancellation or invite replay.
                pass
            finally:
                # Consume task exceptions without logging secret-bearing locals.
                task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            raise
        except TelegramProviderFailure as exc:
            progress, failure = None, exc.code if exc.code in PROVIDER_FAILURES else "provider_outcome_unknown"
        except Exception:
            progress, failure = None, "provider_outcome_unknown"
        return await self._settle(tenant_id, actor_id, attempt_id, revision, operation, progress, failure)

    async def _settle(self, tenant_id, actor_id, attempt_id, revision, operation, progress, failure):
        async with async_session_maker() as session, session.begin():
            try:
                await _authorize(session, tenant_id, actor_id, write=True, require_source_view=True)
                allowed = True
            except ConnectionDenied:
                allowed = False
            # Cleanup is narrowly scoped to this already-claimed attempt, not an
            # alternate access path to any authorization or grant.
            row = await _row(session, tenant_id, actor_id, attempt_id)
            await _expire(session, row)
            if row.state != "processing" or row.revision != revision:
                return _projection(row)
            if not allowed:
                _finish(row, "failed", "access_revoked")
                return _projection(row)
            if failure:
                _finish(row, "failed", failure)
                return _projection(row)
            try:
                material = _open(row)  # Recheck key/integrity after provider I/O.
            except (ValueError, RuntimeError, TypeError, UnicodeError, RecursionError):
                _finish(row, "failed", "secret_unavailable")
                return _projection(row)
            phase = progress.phase
            if phase == "authorized" and operation in {"code", "password"}:
                try:
                    async with session.begin_nested():
                        if row.recovery_authorization_id is None:
                            account, grant = await _record_telegram_result(
                                session, tenant_id=tenant_id, actor_id=actor_id, result=progress.result
                            )
                        else:
                            account = await _record_telegram_recovery(
                                session,
                                actor_id=actor_id,
                                authorization_id=row.recovery_authorization_id,
                                expected_revision=row.recovery_revision,
                                result=progress.result,
                            )
                            grant = None
                except (AuthorizationAlreadyExists, IntegrityError):
                    _finish(row, "failed", "account_already_exists")
                except (ValueError, RuntimeError, ConnectionDenied):
                    _finish(row, "failed", "secret_unavailable")
                else:
                    row.authorization_id = account["authorization_id"]
                    row.grant_id = grant["grant_id"] if grant is not None else None
                    _finish(row, "completed")
                return _projection(row)
            valid = {"begin": {"code"}, "code": {"invalid_code", "password"}, "password": {"invalid_password"}}
            if phase not in valid.get(operation, set()):
                _finish(row, "failed", "provider_outcome_unknown")
                return _projection(row)
            if phase.startswith("invalid_") and getattr(row, operation + "_attempts") >= LIMITS[operation]:
                _finish(row, "failed", "attempt_limit_reached")
                return _projection(row)
            material["session"] = progress.session
            if phase == "code":
                material["phone_code_hash"] = progress.phone_code_hash
            if not material["session"] or not material["phone_code_hash"]:
                _finish(row, "failed", "provider_outcome_unknown")
                return _projection(row)
            row.state = operation if phase.startswith("invalid_") else phase
            row.revision += 1
            row.failure_code = phase if phase.startswith("invalid_") else None
            try:
                _seal(row, material)
            except (ValueError, RuntimeError, TypeError, UnicodeError):
                _finish(row, "failed", "secret_unavailable")
            return _projection(row)


telegram_logins = TelegramLoginManager()
