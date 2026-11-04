"""ORM manager for pairing, current rights, revocation and replay fences.

All business transitions acquire tenant then device/pairing row locks. No
provider/network I/O takes place in these transactions. No payloads in ledger.
"""

from __future__ import annotations

import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from cryptography import x509
from sqlalchemy import delete, func, select, update

from app.core.access_catalog import workspace_grants
from app.core.tenant_context import current_tenant_id, is_bypass
from app.models import ActionAuditEvent, Tenant, TenantUser, User, WorkspaceRole
from app.models.integrations.local_connector import LocalConnector, LocalConnectorPairing, LocalConnectorRequest
from app.models.managers.base_manager import BaseManager
from app.types import ActionType

from .wire import ConnectorUnavailable, REPLAY_CAPACITY, REPLAY_SECONDS


def now():
    return datetime.now(timezone.utc)


class LocalConnectorStore(BaseManager):
    def __init__(self, authority):
        super().__init__(LocalConnector)
        self.authority = authority

    async def _authorize(self, session, tenant_id, owner_id, action):
        tenant = (await session.execute(select(Tenant).where(Tenant.id == tenant_id).with_for_update())).scalar_one_or_none()
        if tenant is None or not tenant.is_active:
            raise ConnectorUnavailable()
        row = (await session.execute(select(User, WorkspaceRole.codename).join(
            TenantUser, TenantUser.user_id == User.id).join(WorkspaceRole, WorkspaceRole.id == TenantUser.role_id).where(
            User.id == owner_id, User.is_active.is_(True), TenantUser.tenant_id == tenant_id,
            TenantUser.channel == "web", TenantUser.is_active.is_(True)))).first()
        if row is None or ("localconnector", action) not in workspace_grants(row[1]):
            raise ConnectorUnavailable()
        return tenant

    @staticmethod
    def _tenant():
        tid = current_tenant_id()
        if is_bypass() or type(tid) is not int or tid <= 0:
            raise ConnectorUnavailable()
        return tid

    async def begin(self, *, owner_id, device_name, platform, version):
        tid = self._tenant()
        pairing_id = uuid.uuid4().hex
        secret = secrets.token_urlsafe(32)
        async with self._get_owned_session() as session:
            await self._authorize(session, tid, owner_id, ActionType.CREATE)
            pending = (await session.execute(select(func.count()).select_from(LocalConnectorPairing).where(
                LocalConnectorPairing.tenant_id == tid, LocalConnectorPairing.owner_user_id == owner_id,
                LocalConnectorPairing.expires_at > now(), LocalConnectorPairing.device_id.is_(None)))).scalar_one()
            if pending >= 3:
                raise ConnectorUnavailable()
            session.add(LocalConnectorPairing(tenant_id=tid, owner_user_id=owner_id, pairing_id=pairing_id,
                code_hash=self.authority.verifier(pairing_id, secret), expires_at=now()+timedelta(minutes=5),
                device_name_encrypted=device_name, platform=platform, version=version))
        return {"pairing_id": pairing_id, "secret": secret, "expires_in": 300}

    async def offer(self, *, pairing_id, secret, csr_pem):
        # Public secret bootstrap: only lookup at this boundary is intentionally
        # cross-tenant. No result is released until constant-time code verification.
        fingerprint, key = self.authority.csr(csr_pem)
        result = None
        async with self._get_owned_session() as session:
            initial = (await session.execute(select(LocalConnectorPairing).where(
                LocalConnectorPairing.pairing_id == pairing_id))).scalar_one_or_none()
            if initial is None:
                raise ConnectorUnavailable()
            await self._authorize(session, initial.tenant_id, initial.owner_user_id, ActionType.CREATE)
            row = (await session.execute(select(LocalConnectorPairing).where(
                LocalConnectorPairing.id == initial.id).with_for_update().execution_options(populate_existing=True))).scalar_one()
            if row.expires_at <= now() or row.attempts >= 5:
                raise ConnectorUnavailable()
            if not hmac.compare_digest(row.code_hash, self.authority.verifier(pairing_id, secret)):
                row.attempts += 1
            elif row.fingerprint is not None and not hmac.compare_digest(row.fingerprint, fingerprint):
                row.attempts += 1
            else:
                row.fingerprint, row.public_key = fingerprint, key
                result = {"fingerprint": fingerprint, "device_id": row.device_id, "certificate_pem": None}
                if row.device_id is not None:
                    device = (await session.execute(select(LocalConnector).where(
                        LocalConnector.tenant_id == row.tenant_id, LocalConnector.id == row.device_id,
                        LocalConnector.revoked_at.is_(None)))).scalar_one_or_none()
                    if device is None:
                        raise ConnectorUnavailable()
                    result["certificate_pem"] = device.certificate_pem
        if result is None:
            # Outside context: failed attempts have committed, not rolled back.
            raise ConnectorUnavailable()
        return result

    async def confirm(self, *, owner_id, pairing_id, fingerprint):
        tid = self._tenant()
        async with self._get_owned_session() as session:
            await self._authorize(session, tid, owner_id, ActionType.CREATE)
            row = (await session.execute(select(LocalConnectorPairing).where(
                LocalConnectorPairing.tenant_id == tid, LocalConnectorPairing.owner_user_id == owner_id,
                LocalConnectorPairing.pairing_id == pairing_id).with_for_update())).scalar_one_or_none()
            if row is None or row.expires_at <= now() or row.attempts >= 5 or row.fingerprint is None:
                raise ConnectorUnavailable()
            if not hmac.compare_digest(row.fingerprint, fingerprint):
                raise ConnectorUnavailable()
            if row.device_id is not None:
                return row.device_id
            device = LocalConnector(tenant_id=tid, owner_user_id=owner_id, device_name_encrypted=row.device_name_encrypted,
                fingerprint=row.fingerprint, public_key=row.public_key, platform=row.platform, version=row.version,
                certificate_pem="pending", cert_serial="pending-"+uuid.uuid4().hex, cloud_roots=[])
            session.add(device)
            await session.flush()
            device.certificate_pem = self.authority.issue(row.public_key, tenant_id=tid, owner_id=owner_id, device_id=device.id)
            device.cert_serial = str(x509.load_pem_x509_certificate(device.certificate_pem.encode()).serial_number)
            row.device_id = device.id
            await ActionAuditEvent.objects.record_success(session=session, actor_kind="user", actor_id=owner_id,
                action="files.connector.register", resource_kind="local_connector", resource_id=device.id)
            return device.id

    async def list_owned(self, *, owner_id, limit=50, before_id=None):
        tid = self._tenant()
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ConnectorUnavailable()
        async with self._get_owned_session() as session:
            await self._authorize(session, tid, owner_id, ActionType.VIEW)
            conditions = [LocalConnector.tenant_id == tid, LocalConnector.owner_user_id == owner_id]
            if before_id is not None:
                conditions.append(LocalConnector.id < before_id)
            return list((await session.execute(select(LocalConnector).where(*conditions).order_by(
                LocalConnector.id.desc()).limit(limit))).scalars())

    async def _device(self, session, tid, owner_id, device_id, action):
        await self._authorize(session, tid, owner_id, action)
        row = (await session.execute(select(LocalConnector).where(LocalConnector.tenant_id == tid,
            LocalConnector.id == device_id, LocalConnector.owner_user_id == owner_id).with_for_update())).scalar_one_or_none()
        if row is None or row.revoked_at is not None:
            raise ConnectorUnavailable()
        return row

    async def revoke(self, *, owner_id, device_id):
        tid = self._tenant()
        async with self._get_owned_session() as session:
            row = await self._device(session, tid, owner_id, device_id, ActionType.DELETE)
            row.revoked_at, row.epoch = now(), row.epoch+1
            row.cloud_roots = []
            await session.execute(update(LocalConnectorRequest).where(LocalConnectorRequest.tenant_id == tid,
                LocalConnectorRequest.device_id == device_id).values(state="unknown"))
            await ActionAuditEvent.objects.record_success(session=session, actor_kind="user", actor_id=owner_id,
                action="files.connector.revoke", resource_kind="local_connector", resource_id=device_id)

    async def consent(self, *, owner_id, device_id, roots):
        tid = self._tenant()
        async with self._get_owned_session() as session:
            row = await self._device(session, tid, owner_id, device_id, ActionType.CONFIGURE)
            row.cloud_roots = roots
            row.consent_epoch += 1
            row.policy_epoch += 1

    async def authorize(self, *, owner_id, device_id, roots, previous=None):
        tid = self._tenant()
        async with self._get_owned_session() as session:
            row = await self._device(session, tid, owner_id, device_id, ActionType.VIEW)
            if not roots or not set(roots) <= set(row.cloud_roots):
                raise ConnectorUnavailable()
            snapshot = (tid, owner_id, row.id, row.epoch, row.consent_epoch, row.policy_epoch)
            if previous is not None and snapshot != previous:
                raise ConnectorUnavailable()
            return snapshot

    async def channel_identity(self, *, fingerprint, serial):
        async with self._get_owned_session() as session:
            row = (await session.execute(select(LocalConnector).where(LocalConnector.fingerprint == fingerprint,
                LocalConnector.cert_serial == serial, LocalConnector.revoked_at.is_(None)))).scalar_one_or_none()
            if row is None:
                raise ConnectorUnavailable()
            await self._authorize(session, row.tenant_id, row.owner_user_id, ActionType.VIEW)
            row = (await session.execute(select(LocalConnector).where(
                LocalConnector.id == row.id, LocalConnector.tenant_id == row.tenant_id,
                LocalConnector.revoked_at.is_(None)).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
            if row is None:
                raise ConnectorUnavailable()
            row.last_seen = now()
            return row.tenant_id, row.owner_user_id, row.id, row.epoch

    async def reserve(self, request, digest):
        async with self._get_owned_session() as session:
            row = await self._device(session, request.tenant_id, request.owner_id, request.device_id, ActionType.VIEW)
            if (row.epoch,row.consent_epoch,row.policy_epoch) != (request.device_epoch,request.consent_epoch,request.policy_epoch):
                raise ConnectorUnavailable()
            if not set(request.args.roots) <= set(row.cloud_roots):
                raise ConnectorUnavailable()
            scope = (LocalConnectorRequest.tenant_id == row.tenant_id, LocalConnectorRequest.device_id == row.id)
            await session.execute(delete(LocalConnectorRequest).where(*scope,LocalConnectorRequest.expires_at <= now()))
            existing = (await session.execute(select(LocalConnectorRequest).where(*scope,
                LocalConnectorRequest.request_id == request.request_id))).scalar_one_or_none()
            if existing is not None:
                raise ConnectorUnavailable()  # even equal digest returns unknown; no content cache/re-execution
            count = (await session.execute(select(func.count()).select_from(LocalConnectorRequest).where(*scope))).scalar_one()
            if count >= REPLAY_CAPACITY:
                raise ConnectorUnavailable()
            session.add(LocalConnectorRequest(tenant_id=row.tenant_id, device_id=row.id,
                request_id=request.request_id, digest=digest, expires_at=now()+timedelta(seconds=REPLAY_SECONDS),state="dispatched"))

    async def finish(self, request, digest):
        async with self._get_owned_session() as session:
            row = await self._device(session, request.tenant_id, request.owner_id, request.device_id, ActionType.VIEW)
            if (row.epoch,row.consent_epoch,row.policy_epoch) != (request.device_epoch,request.consent_epoch,request.policy_epoch):
                raise ConnectorUnavailable()
            if not set(request.args.roots) <= set(row.cloud_roots):
                raise ConnectorUnavailable()
            ledger = (await session.execute(select(LocalConnectorRequest).where(
                LocalConnectorRequest.tenant_id == row.tenant_id, LocalConnectorRequest.device_id == row.id,
                LocalConnectorRequest.request_id == request.request_id,
                LocalConnectorRequest.digest == digest, LocalConnectorRequest.state == "dispatched",
                LocalConnectorRequest.expires_at > now()).with_for_update())).scalar_one_or_none()
            if ledger is None:
                raise ConnectorUnavailable()
            if int(now().timestamp()) > request.expires_at:
                raise ConnectorUnavailable()
            ledger.state = "completed"
            await ActionAuditEvent.objects.record_success(session=session, actor_kind="user", actor_id=request.owner_id,
                action="files.connector.search", resource_kind="local_connector", resource_id=row.id)
