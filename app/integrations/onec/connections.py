"""Persist 1C connection configuration in the existing workspace vault.

This service only stores and lists connections; it does not itself perform a
network check. Host approval is checked in the same transaction as creation.
"""

import hashlib
import json
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.integrations.onec.credentials import seal_basic_credentials
from app.integrations.onec.endpoint import OneCEndpointError, validate_endpoint
from app.models.integrations.connection import WorkspaceConnection, WorkspaceConnectionSecret
from app.models.managers.base_manager import BaseManager
from app.models.managers.workspace_connection_manager import WorkspaceConnectionManager, _authorize
from app.models.managers.workspace_outbound_host_manager import WorkspaceOutboundHostManager

PROVIDER = "onec"
METHOD = "odata_credentials"
SCHEMA_VERSION = 1


class OneCConnectionError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _endpoint(base_url, interface):
    try:
        host = urlsplit(base_url).hostname
    except (TypeError, ValueError):
        raise OneCEndpointError("invalid_endpoint") from None
    # This is a syntax check only; it must be followed by the DB approval check.
    return validate_endpoint(base_url, interface, frozenset({host}) if host else frozenset())


def _label(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 100:
        raise OneCConnectionError("invalid_label")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise OneCConnectionError("invalid_label")
    return value.strip()


def _account_key(endpoint, login):
    identity = json.dumps(
        [endpoint.host, endpoint.port, endpoint.base_path, login], ensure_ascii=True, separators=(",", ":")
    )
    return hashlib.sha256(identity.encode("ascii")).hexdigest()


def _projection(row):
    return {
        "id": row.id,
        "label": row.label,
        "host": row.config["host"],
        "port": row.config["port"],
        "base_path": row.config["base_path"],
        "interface": row.config["interface"],
        "state": row.state,
        "check_code": row.check_code,
        "checked_at": row.checked_at,
        "is_active": row.is_active,
    }


class OneCConnectionService:
    def __init__(self):
        self._sessions = BaseManager(WorkspaceConnection)
        self._hosts = WorkspaceOutboundHostManager()

    async def create(self, *, tenant_id, actor_id, label, base_url, interface, login, password):
        name = _label(label)
        endpoint = _endpoint(base_url, interface)
        try:
            async with self._sessions._get_owned_session() as session:
                await self._hosts.require_approved(
                    session=session, tenant_id=tenant_id, actor_id=actor_id, host=endpoint.host
                )
                ciphertext = seal_basic_credentials(login, password)
                row = WorkspaceConnection(
                    tenant_id=tenant_id,
                    owner_user_id=actor_id,
                    platform=PROVIDER,
                    provider_id=PROVIDER,
                    auth_method=METHOD,
                    schema_version=SCHEMA_VERSION,
                    label=name,
                    account_key=_account_key(endpoint, login),
                    config={
                        "host": endpoint.host,
                        "port": endpoint.port,
                        "base_path": endpoint.base_path,
                        "interface": endpoint.interface,
                    },
                    is_active=True,
                    state="unverified",
                )
                session.add(row)
                await session.flush()
                session.add(WorkspaceConnectionSecret(connection_id=row.id, secret_encrypted=ciphertext))
                await session.flush()
                return _projection(row)
        except IntegrityError:
            raise OneCConnectionError("duplicate_connection") from None

    async def list_for_actor(self, *, tenant_id, actor_id):
        async with self._sessions._get_owned_session() as session:
            await _authorize(session, tenant_id, actor_id, require_source_view=True)
            rows = (
                (
                    await session.execute(
                        select(WorkspaceConnection)
                        .where(
                            WorkspaceConnection.tenant_id == tenant_id,
                            WorkspaceConnection.owner_user_id == actor_id,
                            WorkspaceConnection.provider_id == PROVIDER,
                        )
                        .order_by(WorkspaceConnection.id)
                    )
                )
                .scalars()
                .all()
            )
            return [_projection(row) for row in rows]

    async def disable(self, *, tenant_id, actor_id, connection_id):
        await WorkspaceConnectionManager().disable(
            tenant_id=tenant_id, actor_id=actor_id, connection_id=connection_id, expected_provider=PROVIDER
        )
