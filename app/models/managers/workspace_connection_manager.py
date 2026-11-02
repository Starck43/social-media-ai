"""Transactional workspace connections; the only public persistence/check API.

Every operation checks fresh active actor/workspace/membership and ownership.
Shared authorization locks block revocation during an operation; a connection
row lock serializes edit/check/disable so late checks cannot revive disabled or
changed credentials. Never resolve these secrets through the personal vault.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.access_catalog import workspace_grants
from app.core.database import async_session_maker
from app.core.permissions import get_current_user
from app.core.tenant_context import current_tenant_id
from app.integrations.catalog import (
    CATALOG_VERSION,
    PROVIDERS,
    WORKSPACE_PROVIDER_IDS,
    InvalidConnectorInput,
    validate_input,
)
from app.integrations.mail_check import CheckResult, check_mail
from app.models import Tenant, TenantUser, User, WorkspaceConnection, WorkspaceConnectionSecret, WorkspaceRole
from app.types import ActionType
from app.utils.crypto import decrypt_secret, encrypt_secret

STATE_LABELS = {
    "unverified": "Сохранено · не проверено",
    "verified": "Вход по IMAP проверен",
    "error": "Ошибка проверки",
    "reauth": "Требуется новый пароль приложения",
    "disabled": "Отключено",
}
CHECK_LABELS = {
    "imap_authenticated": "Вход выполнен. Чтение писем и отправка не запускались.",
    "unsafe_address": "Адрес сервера не прошёл проверку безопасности.",
    "tls_failed": "Не удалось установить защищённое TLS-соединение.",
    "service_unavailable": "Почтовый сервер временно недоступен.",
    "authentication_failed": "Проверьте адрес, пароль приложения и разрешение IMAP у провайдера.",
    "connection_failed": "Не удалось подключиться к почтовому серверу. Повторите проверку позже.",
    "secret_unavailable": "Секрет недоступен. Задайте новый пароль приложения.",
    "unsupported_provider": "Этот способ подключения пока не поддерживается.",
}


class ConnectionDenied(PermissionError):
    def __init__(self):
        super().__init__("connection_unavailable")


async def _authorize(session, tenant_id, actor_id, *, write=False):
    if any(type(value) is not int or value <= 0 for value in (tenant_id, actor_id)):
        raise ConnectionDenied()
    actor = get_current_user()
    if actor is None or actor.id != actor_id or not actor.is_active or current_tenant_id() != tenant_id:
        raise ConnectionDenied()
    # Match lock ordering in every operation. No platform/operator bypass.
    user = (
        await session.execute(
            select(User.id).where(User.id == actor_id, User.is_active.is_(True)).with_for_update(read=True)
        )
    ).scalar_one_or_none()
    tenant = (
        await session.execute(
            select(Tenant.id).where(Tenant.id == tenant_id, Tenant.is_active.is_(True)).with_for_update(read=True)
        )
    ).scalar_one_or_none()
    member = (
        await session.execute(
            select(TenantUser)
            .where(
                TenantUser.tenant_id == tenant_id,
                TenantUser.user_id == actor_id,
                TenantUser.channel == "web",
                TenantUser.is_active.is_(True),
            )
            .with_for_update(read=True)
        )
    ).scalar_one_or_none()
    if user is None or tenant is None or member is None or member.role_id is None:
        raise ConnectionDenied()
    role = (
        await session.execute(
            select(WorkspaceRole.codename).where(WorkspaceRole.id == member.role_id).with_for_update(read=True)
        )
    ).scalar_one_or_none()
    if role is None or (write and ("source", ActionType.CREATE) not in workspace_grants(role)):
        raise ConnectionDenied()
    return ("source", ActionType.CREATE) in workspace_grants(role)


def _projection(row):
    # Explicit allowlist: never project arbitrary config, metadata or ciphertext.
    return {
        "id": row.id,
        "label": row.label,
        "provider_id": row.provider_id,
        "provider_label": PROVIDERS[row.provider_id].label if row.provider_id in PROVIDERS else "Неизвестный сервис",
        "auth_method": row.auth_method,
        "account_login": row.config.get("account_login", ""),
        "state": row.state,
        "state_label": STATE_LABELS.get(row.state, STATE_LABELS["error"]),
        "check_detail": CHECK_LABELS.get(row.check_code, ""),
        "checked_at": row.checked_at,
        "is_active": row.is_active,
    }


def _configuration(provider_id, method, values, label):
    if provider_id not in WORKSPACE_PROVIDER_IDS:
        raise InvalidConnectorInput("unsupported_provider")
    config, secrets = validate_input(provider_id, method, values)
    if not isinstance(label, str) or len(label.strip()) > 100 or any(ord(ch) < 32 for ch in label):
        raise InvalidConnectorInput("invalid_label")
    return config, secrets, label.strip() or PROVIDERS[provider_id].label


async def _connection(session, tenant_id, actor_id, connection_id):
    if type(connection_id) is not int or connection_id <= 0:
        raise ConnectionDenied()
    row = (
        await session.execute(
            select(WorkspaceConnection)
            .where(
                WorkspaceConnection.id == connection_id,
                WorkspaceConnection.tenant_id == tenant_id,
                WorkspaceConnection.owner_user_id == actor_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise ConnectionDenied()
    return row


class WorkspaceConnectionManager:
    async def list_for_actor(self, *, tenant_id, actor_id):
        async with async_session_maker() as session, session.begin():
            can_write = await _authorize(session, tenant_id, actor_id)
            rows = (
                (
                    await session.execute(
                        select(WorkspaceConnection)
                        .where(
                            WorkspaceConnection.tenant_id == tenant_id, WorkspaceConnection.owner_user_id == actor_id
                        )
                        .order_by(WorkspaceConnection.id)
                    )
                )
                .scalars()
                .all()
            )
            return [_projection(row) for row in rows], can_write

    async def save(self, *, tenant_id, actor_id, provider_id, method, values, label="", connection_id=None):
        config, secrets, label = _configuration(provider_id, method, values, label)
        try:
            async with async_session_maker() as session, session.begin():
                await _authorize(session, tenant_id, actor_id, write=True)
                if connection_id is None:
                    row = WorkspaceConnection(
                        tenant_id=tenant_id,
                        owner_user_id=actor_id,
                        platform=PROVIDERS[provider_id].platform,
                        provider_id=provider_id,
                        auth_method=method,
                        schema_version=CATALOG_VERSION,
                        label=label,
                        account_key=config["account_login"].casefold(),
                        config=config,
                    )
                    session.add(row)
                    await session.flush()
                    secret_row = WorkspaceConnectionSecret(
                        connection_id=row.id, secret_encrypted=encrypt_secret(secrets["app_password"])
                    )
                    session.add(secret_row)
                else:
                    row = await _connection(session, tenant_id, actor_id, connection_id)
                    # A provider/method change requires deliberate creation of a new connection.
                    if row.provider_id != provider_id or row.auth_method != method:
                        raise InvalidConnectorInput("provider_change_forbidden")
                    secret_row = await session.get(WorkspaceConnectionSecret, row.id)
                    if secret_row is None:
                        raise ConnectionDenied()
                    secret_row.secret_encrypted = encrypt_secret(secrets["app_password"])
                    row.config, row.label = config, label
                    row.account_key = config["account_login"].casefold()
                    row.schema_version = CATALOG_VERSION
                row.state, row.is_active, row.check_code, row.checked_at = "unverified", True, None, None
                await session.flush()
                return _projection(row)
        except IntegrityError:
            raise InvalidConnectorInput("duplicate_account") from None

    async def disable(self, *, tenant_id, actor_id, connection_id):
        async with async_session_maker() as session, session.begin():
            await _authorize(session, tenant_id, actor_id, write=True)
            row = await _connection(session, tenant_id, actor_id, connection_id)
            row.is_active, row.state, row.checked_at, row.check_code = False, "disabled", None, None
            # Remove the local secret on disable; reactivation needs a new password.
            secret_row = await session.get(WorkspaceConnectionSecret, row.id)
            if secret_row is not None:
                secret_row.secret_encrypted = ""

    async def verify(self, *, tenant_id, actor_id, connection_id):
        async with async_session_maker() as session, session.begin():
            await _authorize(session, tenant_id, actor_id, write=True)
            row = await _connection(session, tenant_id, actor_id, connection_id)
            if not row.is_active:
                raise ConnectionDenied()
            if (
                row.provider_id not in WORKSPACE_PROVIDER_IDS
                or row.schema_version != CATALOG_VERSION
                or row.auth_method != "app_password"
            ):
                result = CheckResult("error", "unsupported_provider")
            else:
                secret_row = await session.get(WorkspaceConnectionSecret, row.id)
                try:
                    password = decrypt_secret(secret_row.secret_encrypted) if secret_row else ""
                except (ValueError, RuntimeError):
                    password = ""
                if not password:
                    result = CheckResult("reauth", "secret_unavailable")
                else:
                    # Never use stored endpoints: the code-owned provider preset controls networking.
                    worker = asyncio.create_task(
                        asyncio.to_thread(check_mail, row.provider_id, row.config["account_login"], password)
                    )
                    try:
                        result = await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        # Keep authorization/connection locks until socket work ends.
                        await worker
                        raise
            row.state, row.check_code = result.state, result.code
            row.checked_at = datetime.now(timezone.utc)
            return _projection(row)


workspace_connections = WorkspaceConnectionManager()
