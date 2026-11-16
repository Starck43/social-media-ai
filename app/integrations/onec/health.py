"""Authorized, rate-limited 1C health check and guarded status persistence.

The dispatch transaction holds shared connection and workspace-host locks.
The limiter opens another transaction and takes a compatible connection share
lock; taking an exclusive connection lock during dispatch would deadlock it.
The finishing transaction revalidates the full snapshot before recording status.
"""

from datetime import datetime, timezone

from sqlalchemy import select

from app.integrations.onec.connections import (
    ALLOWED_METHODS,
    METHOD,
    PROVIDER,
    SCHEMA_VERSION,
    OneCConnectionError,
    _endpoint,
    _projection,
)
from app.integrations.onec.credentials import OneCCredentialError, open_basic_credentials
from app.integrations.onec.transport import OneCHealthError, check_health
from app.integrations.ratelimit import ProviderDeferred, RateLimiter, budget_for
from app.integrations.ratelimit.store import BudgetConnectionUnavailable
from app.models.integrations.connection import WorkspaceConnection, WorkspaceConnectionSecret
from app.models.managers.base_manager import BaseManager
from app.models.managers.workspace_connection_manager import ConnectionDenied, _authorize, _connection
from app.models.managers.workspace_outbound_host_manager import WorkspaceOutboundHostManager


class OneCHealthService:
    def __init__(self):
        self._sessions = BaseManager(WorkspaceConnection)
        self._hosts = WorkspaceOutboundHostManager()
        self._limiter = RateLimiter()

    async def check(self, *, tenant_id, actor_id, connection_id):
        if type(connection_id) is not int or connection_id <= 0:
            raise ConnectionDenied()
        async with self._sessions._get_owned_session() as session:
            await _authorize(session, tenant_id, actor_id, write=True)
            row = (
                await session.execute(
                    select(WorkspaceConnection)
                    .where(
                        WorkspaceConnection.id == connection_id,
                        WorkspaceConnection.tenant_id == tenant_id,
                        WorkspaceConnection.owner_user_id == actor_id,
                    )
                    .with_for_update(read=True)
                )
            ).scalar_one_or_none()
            if (
                row is None
                or not row.is_active
                or row.provider_id != PROVIDER
                or row.auth_method not in ALLOWED_METHODS
                or row.schema_version != SCHEMA_VERSION
            ):
                raise ConnectionDenied()
            config = dict(row.config)
            try:
                host, port, path, interface = (config[key] for key in ("host", "port", "base_path", "interface"))
                if not isinstance(host, str) or type(port) is not int or not isinstance(path, str):
                    raise ValueError
                endpoint = _endpoint(f"https://{host}:{port}{path}", interface)
                if (endpoint.host, endpoint.port, endpoint.base_path) != (host, port, path):
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise OneCConnectionError("invalid_configuration") from None
            await self._hosts.require_approved(session=session, tenant_id=tenant_id, actor_id=actor_id, host=host)
            secret = await session.get(WorkspaceConnectionSecret, connection_id)
            ciphertext = secret.secret_encrypted if secret is not None else ""
            account_key = row.account_key
            auth_method = row.auth_method
            try:
                credentials = open_basic_credentials(ciphertext)
                budget = budget_for(PROVIDER, account_key, connection_id=connection_id)
                result = await check_health(
                    endpoint, credentials.login, credentials.password, limiter=self._limiter, budget=budget
                )
                state, code, metadata = "verified", result.code, result.metadata_sha256
            except OneCCredentialError:
                state, code, metadata = "reauth", "secret_unavailable", None
            except OneCHealthError as exc:
                state, code, metadata = ("reauth" if exc.code == "authentication_failed" else "error"), exc.code, None
            except ProviderDeferred:
                state, code, metadata = "error", "rate_limited", None
            except BudgetConnectionUnavailable:
                raise ConnectionDenied() from None

        # Release the shared connection lock before acquiring an exclusive lock.
        # Do not let an old response revive a disabled or edited connection.
        async with self._sessions._get_owned_session() as session:
            await _authorize(session, tenant_id, actor_id, write=True)
            row = await _connection(session, tenant_id, actor_id, connection_id)
            secret = await session.get(WorkspaceConnectionSecret, connection_id)
            if (
                not row.is_active
                or row.provider_id != PROVIDER
                or row.auth_method != auth_method
                or row.schema_version != SCHEMA_VERSION
                or row.account_key != account_key
                or row.config != config
                or (secret.secret_encrypted if secret is not None else "") != ciphertext
            ):
                raise ConnectionDenied()
            await self._hosts.require_approved(session=session, tenant_id=tenant_id, actor_id=actor_id, host=host)
            row.config = {**config, "metadata_sha256": metadata}
            row.state, row.check_code, row.checked_at = state, code, datetime.now(timezone.utc)
            await session.flush()
            return _projection(row)
