"""Default-off owner-bound encrypted retention and durable read-attempt ledger.

All mutations serialize on a tenant advisory transaction lock before authority
locks; connection/control/object rows fence revocation and late writes. No I/O
except the existing session maker; no provider/model call under database locks.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import delete, func, select

from app.core.database import async_session_maker
from app.integrations.file_workspace.content_read import ContentReadContext, ContentReadRequest, ReadPlainText
from app.integrations.file_workspace.insight_candidates import InsightCandidateBatch
from app.integrations.file_workspace.line_evidence import LineRange, build_line_evidence, verify_line_evidence
from app.integrations.file_workspace.policy import FileOperation
from app.integrations.file_workspace.private_admission import (
    ExactVersionAssertion,
    PrivateAdmissionState,
    PrivateReadReceipt,
    PrivateScopeBinding,
)
from app.integrations.file_workspace.private_cipher import PrivateCipher
from app.integrations.file_workspace.private_projection import serialize_same_read_payload
from app.integrations.file_workspace.retention_quota import (
    RetentionQuotaSnapshot,
    RetentionQuotaUsage,
    retention_quota_allows,
)
from app.integrations.file_workspace.retention_time import MAX_CAPTURE_AGE, RETENTION_TTL, make_retention_deadline
from app.models import FileCatalogObject, WorkspaceConnectionSecret
from app.models.file_workspace.private_insights import (
    FileInsightAttempt,
    FileInsightMount,
    FileInsightObjectState,
    FilePrivateInsight,
)
from app.models.managers.file_catalog_manager import _cloud_policy, _key, _policy_key
from app.models.managers.workspace_connection_manager import _authorize, _connection

POLICY_REVISION = "file-private-policy-v1"
LOCK_NAMESPACE = 12120028
ATTEMPT_LIMITS = (16, 100, 200, 1000)
VERSION_MAX_AGE = timedelta(seconds=5)


class PrivateInsightUnavailable(Exception):
    def __init__(self):
        super().__init__("private_insight_unavailable")


def _deny():
    raise PrivateInsightUnavailable()


def _id(value):
    if type(value) is not int or not 1 <= value <= 2147483647:
        _deny()
    return value


def _nonce(value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        _deny()
    return value


def _context_key(context):
    request = context.request
    return _key(
        json.dumps(
            [
                request.tenant_id,
                request.actor_id,
                request.connection_id,
                request.object_id,
                request.nonce,
                context.path,
                context.name,
                context.root_key,
                context.policy_key,
                context.credential_key,
                context.observation_number,
                context.processing_boundary,
                context.processing_policy_key,
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        )
    )


def _stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _binding_data(binding):
    return {
        "context_key": _context_key(binding.context),
        "path_key": binding.path_key,
        "owner_epoch": binding.owner_epoch,
        "connection_epoch": binding.connection_epoch,
        "object_epoch": binding.object_epoch,
        "consent_revision": binding.consent_revision,
        "policy_revision": binding.policy_revision,
    }


def _provenance(source, semantics, provider_object_id):
    value = source.extraction
    return {
        "raw_sha256": value.raw_sha256,
        "text_sha256": value.text_sha256,
        "parser_version": value.parser_version,
        "provider_version": source.provider_version,
        "version_semantics": semantics,
        "provider_object_id": provider_object_id,
    }


def _receipt(row, binding):
    if type(row.provenance) is not dict:
        _deny()
    return PrivateReadReceipt(binding, row.captured_at, **row.provenance)


def _metadata(receipt, created_at):
    data = _binding_data(receipt.binding)
    del data["context_key"]
    data.update(
        {
            "version_semantics": receipt.version_semantics,
            "provider_object_id": receipt.provider_object_id,
            "lifecycle": {
                "revision": "private-retention-v1",
                "captured_at": _stamp(receipt.captured_at),
                "created_at": _stamp(created_at),
                "expires_at": _stamp(receipt.captured_at + RETENTION_TTL),
            },
        }
    )
    return data


def _assert_version(assertion, source, receipt, now):
    if type(assertion) is not ExactVersionAssertion or type(assertion.request) is not ContentReadRequest:
        _deny()
    request = assertion.request
    if any(type(v) is not int for v in (request.tenant_id, request.actor_id, request.connection_id, request.object_id)):
        _deny()
    if type(request.nonce) is not str or request != source.context.request:
        _deny()
    for actual, expected in (
        (assertion.raw_sha256, receipt.raw_sha256),
        (assertion.provider_version, receipt.provider_version),
        (assertion.version_semantics, receipt.version_semantics),
        (assertion.provider_object_id, receipt.provider_object_id),
    ):
        if type(actual) is not type(expected) or actual != expected:
            _deny()
    checked = assertion.checked_at
    if type(checked) is not datetime or checked.tzinfo is not timezone.utc or not receipt.captured_at <= checked <= now:
        _deny()
    if now - checked > VERSION_MAX_AGE:
        _deny()


@dataclass(frozen=True, slots=True, repr=False)
class StoredPrivateInsight:
    id: str
    payload: bytes
    captured_at: datetime
    expires_at: datetime
    historical: bool = True
    review_status: str = "unverified"


@dataclass
class _Access:
    session: object
    mount: object
    connection: object
    policy: object
    root_key: str | None
    secret_key: str | None
    now: datetime
    obj: object = None
    object_state: object = None

    def binding(self, request):
        if self.obj is None or self.object_state is None:
            _deny()
        context = ContentReadContext(
            request,
            self.obj.path,
            self.obj.name,
            self.root_key,
            _policy_key(self.connection.config),
            self.secret_key,
            self.obj.observation_number,
            "cloud",
            self.mount.consent_revision,
        )
        return PrivateScopeBinding(
            context,
            self.obj.path_key,
            self.mount.owner_id,
            self.mount.owner_epoch,
            self.mount.connection_epoch,
            self.object_state.epoch,
            self.mount.consent_revision,
            self.mount.policy_revision,
        )


class FileInsightManager:
    def __init__(self, *, enabled=False, cipher: PrivateCipher | None = None):
        # No settings flag, default runtime registration or public toggle.
        self.enabled = enabled is True
        self.cipher = cipher

    async def _call(self, args, action, *, object_id=None, create_mount=False, live=True, write=False, consent=True):
        if not self.enabled:
            _deny()
        result = None
        failed = False
        try:
            tenant, actor, connection_id = (_id(args[k]) for k in ("tenant_id", "actor_id", "connection_id"))
            if object_id is not None:
                _id(object_id)
            async with async_session_maker() as session, session.begin():
                # Acquired before shared authorization locks, avoiding lock upgrades.
                await session.execute(select(func.pg_advisory_xact_lock(LOCK_NAMESPACE, tenant)))
                await _authorize(session, tenant, actor, write=write, require_source_view=True)
                connection = await _connection(session, tenant, actor, connection_id)
                policy = root = secret_key = None
                if live:
                    policy, root = _cloud_policy(connection)
                    secret = await session.get(WorkspaceConnectionSecret, connection_id)
                    if secret is None or not secret.secret_encrypted:
                        _deny()
                    secret_key = _key(secret.secret_encrypted)
                mount = await session.scalar(
                    select(FileInsightMount)
                    .where(
                        FileInsightMount.tenant_id == tenant,
                        FileInsightMount.connection_id == connection_id,
                    )
                    .with_for_update()
                )
                now = (await session.scalar(select(func.clock_timestamp()))).astimezone(timezone.utc)
                if mount is None and create_mount:
                    mount = FileInsightMount(
                        tenant_id=tenant,
                        connection_id=connection_id,
                        owner_id=actor,
                        processing_allowed=False,
                        retention_allowed=False,
                        owner_epoch=uuid4().hex,
                        connection_epoch=uuid4().hex,
                        consent_revision=uuid4().hex,
                        policy_revision=POLICY_REVISION,
                        clock_highwater=now,
                    )
                    session.add(mount)
                    await session.flush()
                if mount is not None and mount.owner_id != actor and create_mount:
                    # Explicit new-owner consent starts a new empty namespace.
                    await session.execute(
                        delete(FileInsightAttempt).where(FileInsightAttempt.connection_id == connection_id)
                    )
                    mount.owner_id = actor
                    mount.processing_allowed = mount.retention_allowed = False
                    mount.owner_epoch, mount.connection_epoch, mount.consent_revision = (uuid4().hex for _ in range(3))
                if mount is None or mount.owner_id != actor:
                    _deny()
                previous = mount.clock_highwater.astimezone(timezone.utc)
                mount.clock_highwater = max(now, previous)
                await session.flush()
                # Expected denials roll back content mutations but commit high-water.
                try:
                    async with session.begin_nested():
                        if now < previous or mount.policy_revision != POLICY_REVISION:
                            _deny()
                        if consent and (mount.processing_allowed is not True or mount.retention_allowed is not True):
                            _deny()
                        access = _Access(
                            session, mount, connection, policy, _key(root) if root else None, secret_key, now
                        )
                        if object_id is not None:
                            obj = await session.scalar(
                                select(FileCatalogObject)
                                .where(
                                    FileCatalogObject.id == object_id,
                                    FileCatalogObject.tenant_id == tenant,
                                    FileCatalogObject.connection_id == connection_id,
                                )
                                .with_for_update()
                            )
                            if (
                                obj is None
                                or obj.kind != "file"
                                or obj.root_key != access.root_key
                                or not all(
                                    policy.permits(obj.path, operation)
                                    for operation in (FileOperation.LIST, FileOperation.READ)
                                )
                            ):
                                _deny()
                            access.obj = obj
                            state = await session.get(FileInsightObjectState, object_id, with_for_update=True)
                            if state is None:
                                state = FileInsightObjectState(
                                    tenant_id=tenant,
                                    connection_id=connection_id,
                                    object_id=object_id,
                                    epoch=uuid4().hex,
                                )
                                session.add(state)
                                await session.flush()
                            if state.tenant_id != tenant or state.connection_id != connection_id:
                                _deny()
                            access.object_state = state
                        result = await action(access)
                        await session.flush()
                except Exception:
                    failed = True
        except Exception:
            failed = True
        if failed:
            _deny()
        return result

    async def _drop_mount(self, access):
        await access.session.execute(
            delete(FileInsightAttempt).where(FileInsightAttempt.connection_id == access.connection.id)
        )
        access.mount.owner_epoch = uuid4().hex
        access.mount.connection_epoch = uuid4().hex
        access.mount.consent_revision = uuid4().hex

    async def set_consent(self, *, tenant_id, actor_id, connection_id, processing, retention, policy_revision):
        """Trusted internal consent command; future UI must collect informed CSRF-bound choices."""
        if (
            type(processing) is not bool
            or type(retention) is not bool
            or (retention and not processing)
            or type(policy_revision) is not str
            or policy_revision != POLICY_REVISION
        ):
            _deny()
        args = dict(tenant_id=tenant_id, actor_id=actor_id, connection_id=connection_id)

        async def action(a):
            # Every explicit choice rotates epochs, even re-granting identical values.
            await self._drop_mount(a)
            a.mount.processing_allowed, a.mount.retention_allowed = processing, retention
            return {"processing": processing, "retention": retention, "revision": a.mount.consent_revision}

        return await self._call(args, action, create_mount=True, live=processing, write=True, consent=False)

    async def consent_state(self, *, tenant_id, actor_id, connection_id):
        """Read current stored choices without granting consent or creating rows."""
        if not self.enabled:
            _deny()
        try:
            async with async_session_maker() as session, session.begin():
                await _authorize(session, _id(tenant_id), _id(actor_id), require_source_view=True)
                await _connection(session, tenant_id, actor_id, _id(connection_id))
                mount = await session.scalar(
                    select(FileInsightMount)
                    .where(
                        FileInsightMount.tenant_id == tenant_id,
                        FileInsightMount.connection_id == connection_id,
                        FileInsightMount.owner_id == actor_id,
                    )
                    .with_for_update(read=True)
                )
                current = mount is not None and mount.policy_revision == POLICY_REVISION
                return {
                    "processing": mount.processing_allowed is True if current else False,
                    "retention": mount.retention_allowed is True if current else False,
                    "revision": mount.consent_revision if current else None,
                    "policy_revision": POLICY_REVISION,
                }
        except Exception:
            pass
        _deny()

    async def list_results(self, *, tenant_id, actor_id, connection_id, object_id):
        """At most three historical records in one freshly authorized personal object."""
        args = dict(tenant_id=tenant_id, actor_id=actor_id, connection_id=connection_id)

        async def action(a):
            rows = (
                await a.session.scalars(
                    select(FilePrivateInsight)
                    .where(
                        FilePrivateInsight.tenant_id == tenant_id,
                        FilePrivateInsight.owner_id == actor_id,
                        FilePrivateInsight.connection_id == connection_id,
                        FilePrivateInsight.object_id == object_id,
                    )
                    .order_by(FilePrivateInsight.created_at.desc(), FilePrivateInsight.id)
                    .limit(3)
                )
            ).all()
            result = []
            for row in rows:
                request = ContentReadRequest(tenant_id, actor_id, connection_id, object_id, row.attempt_id)
                try:
                    attempt = await self._attempt(a, request, allow_consumed=True, capture_age=False)
                except PrivateInsightUnavailable:
                    continue
                if attempt.consumed and row.expires_at == attempt.expires_at and row.captured_at == attempt.captured_at:
                    result.append(
                        {
                            "id": row.id,
                            "captured_at": row.captured_at,
                            "expires_at": row.expires_at,
                            "review_status": "unverified",
                        }
                    )
            return result

        return await self._call(args, action, object_id=object_id)

    async def begin_read(self, *, tenant_id, actor_id, connection_id, object_id):
        args = dict(tenant_id=tenant_id, actor_id=actor_id, connection_id=connection_id)

        async def action(a):
            # Bound abandoned/completed ledger metadata separately from payload quotas.
            conditions = (
                (
                    FileInsightAttempt.tenant_id == tenant_id,
                    FileInsightAttempt.owner_id == actor_id,
                    FileInsightAttempt.connection_id == connection_id,
                    FileInsightAttempt.object_id == object_id,
                ),
                (FileInsightAttempt.tenant_id == tenant_id, FileInsightAttempt.connection_id == connection_id),
                (FileInsightAttempt.tenant_id == tenant_id, FileInsightAttempt.owner_id == actor_id),
                (FileInsightAttempt.tenant_id == tenant_id,),
            )
            for where, maximum in zip(conditions, ATTEMPT_LIMITS):
                count = await a.session.scalar(select(func.count()).select_from(FileInsightAttempt).where(*where))
                if count >= maximum:
                    _deny()
            request = ContentReadRequest(tenant_id, actor_id, connection_id, object_id, uuid4().hex)
            a.session.add(
                FileInsightAttempt(
                    id=request.nonce,
                    tenant_id=tenant_id,
                    connection_id=connection_id,
                    owner_id=actor_id,
                    object_id=object_id,
                    captured_at=a.now,
                    expires_at=a.now + RETENTION_TTL,
                    binding_data=_binding_data(a.binding(request)),
                    consumed=False,
                )
            )
            return request

        return await self._call(args, action, object_id=object_id)

    async def _attempt(self, a, request, *, allow_consumed=False, capture_age=True):
        if type(request) is not ContentReadRequest:
            _deny()
        _nonce(request.nonce)
        row = await a.session.scalar(
            select(FileInsightAttempt)
            .where(
                FileInsightAttempt.id == request.nonce,
                FileInsightAttempt.tenant_id == request.tenant_id,
                FileInsightAttempt.owner_id == request.actor_id,
                FileInsightAttempt.connection_id == request.connection_id,
                FileInsightAttempt.object_id == request.object_id,
            )
            .with_for_update()
        )
        if row is None or (row.consumed and not allow_consumed) or row.expires_at != row.captured_at + RETENTION_TTL:
            _deny()
        if not row.captured_at <= a.now < row.expires_at or row.binding_data != _binding_data(a.binding(request)):
            _deny()
        if capture_age and a.now - row.captured_at > MAX_CAPTURE_AGE:
            _deny()
        return row

    @staticmethod
    def _args(request):
        if type(request) is not ContentReadRequest:
            _deny()
        return dict(tenant_id=request.tenant_id, actor_id=request.actor_id, connection_id=request.connection_id)

    async def read_context(self, request):
        async def action(a):
            await self._attempt(a, request)
            return a.binding(request).context

        return await self._call(self._args(request), action, object_id=request.object_id)

    async def complete_read(self, source, *, version_semantics, provider_object_id=None):
        """Called only by trusted reader orchestration, never a public DTO endpoint."""
        if type(source) is not ReadPlainText:
            _deny()
        request = source.context.request

        async def action(a):
            attempt = await self._attempt(a, request)
            # Re-extract and validate exact source/context without storing full text.
            evidence = build_line_evidence(source, ranges=(LineRange(1, 1),))
            if not verify_line_evidence(
                evidence, ReadPlainText(a.binding(request).context, source.extraction, source.provider_version)
            ):
                _deny()
            provenance = _provenance(source, version_semantics, provider_object_id)
            if attempt.provenance is not None and attempt.provenance != provenance:
                _deny()
            attempt.provenance = provenance
            receipt = _receipt(attempt, a.binding(request))
            # Reuse strict DTO limits before persisting any opaque provenance.
            batch = InsightCandidateBatch("0" * 32, evidence, ())
            serialize_same_read_payload(source, batch, metadata=_metadata(receipt, a.now))
            return receipt

        return await self._call(self._args(request), action, object_id=request.object_id)

    async def _quota(self, a):
        tenant, owner, connection = a.connection.tenant_id, a.mount.owner_id, a.connection.id

        async def usage(*where):
            count, size = (
                await a.session.execute(
                    select(func.count(), func.coalesce(func.sum(FilePrivateInsight.charged_size), 0)).where(*where)
                )
            ).one()
            return RetentionQuotaUsage(count, size)

        object_count = await a.session.scalar(
            select(func.count())
            .select_from(FilePrivateInsight)
            .where(
                FilePrivateInsight.tenant_id == tenant,
                FilePrivateInsight.connection_id == connection,
                FilePrivateInsight.object_id == a.obj.id,
            )
        )
        return RetentionQuotaSnapshot(
            object_count,
            0,
            await usage(FilePrivateInsight.tenant_id == tenant, FilePrivateInsight.connection_id == connection),
            await usage(FilePrivateInsight.tenant_id == tenant, FilePrivateInsight.owner_id == owner),
            await usage(FilePrivateInsight.tenant_id == tenant),
        )

    async def admission_state(self, request):
        async def action(a):
            await self._attempt(a, request, allow_consumed=True)
            # clock_highwater has already been advanced durably in this transaction.
            return PrivateAdmissionState(
                a.binding(request), True, True, a.now, a.mount.clock_highwater, await self._quota(a)
            )

        return await self._call(self._args(request), action, object_id=request.object_id)

    async def save(self, source, batch, receipt, assertion):
        """Final transaction rechecks all authority/epochs, quota and attempt identity.

        Assertion must come from a reviewed trusted injected adapter; its Python
        type is not proof. No default real version authority exists for Yandex.
        """
        if (
            type(source) is not ReadPlainText
            or type(batch) is not InsightCandidateBatch
            or type(receipt) is not PrivateReadReceipt
            or self.cipher is None
        ):
            _deny()
        request = source.context.request

        async def action(a):
            attempt = await self._attempt(a, request, allow_consumed=True)
            current = _receipt(attempt, a.binding(request))
            if (
                current != receipt
                or _provenance(source, receipt.version_semantics, receipt.provider_object_id) != attempt.provenance
            ):
                _deny()
            _assert_version(assertion, source, current, a.now)
            if attempt.generation_id is not None and attempt.generation_id != batch.generation_id:
                _deny()
            existing = await a.session.scalar(
                select(FilePrivateInsight)
                .where(
                    FilePrivateInsight.tenant_id == request.tenant_id,
                    FilePrivateInsight.attempt_id == request.nonce,
                    FilePrivateInsight.owner_id == request.actor_id,
                    FilePrivateInsight.connection_id == request.connection_id,
                    FilePrivateInsight.object_id == request.object_id,
                )
                .with_for_update()
            )
            if attempt.consumed:
                if existing is None:
                    _deny()  # deletion/expiry never recreates content from a retry
                expected = serialize_same_read_payload(source, batch, metadata=_metadata(current, existing.created_at))
                if self._payload(existing) != expected:
                    _deny()
                return self._result(existing, expected)
            if existing is not None:
                _deny()
            payload = serialize_same_read_payload(source, batch, metadata=_metadata(current, a.now))
            if not retention_quota_allows(payload, await self._quota(a)):
                _deny()
            row = FilePrivateInsight(
                id=uuid4().hex,
                tenant_id=request.tenant_id,
                connection_id=request.connection_id,
                owner_id=request.actor_id,
                object_id=request.object_id,
                attempt_id=request.nonce,
                payload_encrypted=self.cipher.encrypt(payload),
                payload_sha256=hashlib.sha256(payload).hexdigest(),
                charged_size=len(payload),
                captured_at=attempt.captured_at,
                expires_at=attempt.expires_at,
                created_at=a.now,
            )
            a.session.add(row)
            attempt.consumed, attempt.generation_id = True, batch.generation_id
            return self._result(row, payload)

        return await self._call(self._args(request), action, object_id=request.object_id)

    def _payload(self, row):
        try:
            payload = self.cipher.decrypt(row.payload_encrypted)
            if len(payload) == row.charged_size and hashlib.sha256(payload).hexdigest() == row.payload_sha256:
                return payload
        except Exception:
            pass
        _deny()

    @staticmethod
    def _result(row, payload):
        return StoredPrivateInsight(row.id, payload, row.captured_at, row.expires_at)

    async def get(self, *, tenant_id, actor_id, connection_id, object_id, result_id):
        """Historical unverified bytes only; never a claim of current provider content."""
        _nonce(result_id)
        if self.cipher is None:
            _deny()
        args = dict(tenant_id=tenant_id, actor_id=actor_id, connection_id=connection_id)

        async def action(a):
            row = await a.session.scalar(
                select(FilePrivateInsight).where(
                    FilePrivateInsight.id == result_id,
                    FilePrivateInsight.tenant_id == tenant_id,
                    FilePrivateInsight.owner_id == actor_id,
                    FilePrivateInsight.connection_id == connection_id,
                    FilePrivateInsight.object_id == object_id,
                )
            )
            if row is None:
                _deny()
            request = ContentReadRequest(tenant_id, actor_id, connection_id, object_id, row.attempt_id)
            attempt = await self._attempt(a, request, allow_consumed=True, capture_age=False)
            if not attempt.consumed or row.expires_at != attempt.expires_at or row.captured_at != attempt.captured_at:
                _deny()
            payload = self._payload(row)
            body = json.loads(payload)
            binding = a.binding(request)
            expected_scope = {
                "tenant_id": tenant_id,
                "owner_id": actor_id,
                "connection_id": connection_id,
                "object_id": object_id,
                "root_key": binding.context.root_key,
                "path_key": binding.path_key,
            }
            expected_binding = _binding_data(binding)
            del expected_binding["context_key"]
            del expected_binding["path_key"]
            expected_binding.update(read_attempt_id=request.nonce, generation_attempt_id=attempt.generation_id)
            if (
                body["scope"] != expected_scope
                or body["binding"] != expected_binding
                or body["source"] != attempt.provenance
                or body["lifecycle"]["captured_at"] != _stamp(attempt.captured_at)
                or body["lifecycle"]["expires_at"] != _stamp(attempt.expires_at)
            ):
                _deny()
            return self._result(row, payload)

        return await self._call(args, action, object_id=object_id)

    async def forget_object(self, *, tenant_id, actor_id, connection_id, object_id):
        """Explicitly purge ALL private results/attempts for this object and fence late saves."""
        args = dict(tenant_id=tenant_id, actor_id=actor_id, connection_id=connection_id)

        async def action(a):
            a.object_state.epoch = uuid4().hex
            await a.session.execute(
                delete(FileInsightAttempt).where(
                    FileInsightAttempt.tenant_id == tenant_id,
                    FileInsightAttempt.connection_id == connection_id,
                    FileInsightAttempt.object_id == object_id,
                )
            )

        return await self._call(args, action, object_id=object_id, consent=False)

    async def purge_expired(self, *, tenant_id, actor_id, connection_id, limit=100):
        """Owner-bound bounded cleanup, even after consent withdrawal/connection disable.

        No provider, content release or GC scheduler. Attempt deletion cascades
        encrypted results; missing attempts can never be recreated by save.
        """
        if type(limit) is not int or not 1 <= limit <= 100:
            _deny()
        args = dict(tenant_id=tenant_id, actor_id=actor_id, connection_id=connection_id)

        async def action(a):
            ids = (
                await a.session.scalars(
                    select(FileInsightAttempt.id)
                    .where(
                        FileInsightAttempt.tenant_id == tenant_id,
                        FileInsightAttempt.owner_id == actor_id,
                        FileInsightAttempt.connection_id == connection_id,
                        FileInsightAttempt.expires_at <= a.now,
                    )
                    .order_by(FileInsightAttempt.expires_at, FileInsightAttempt.id)
                    .limit(limit)
                    .with_for_update()
                )
            ).all()
            if ids:
                await a.session.execute(delete(FileInsightAttempt).where(FileInsightAttempt.id.in_(ids)))
            return len(ids)

        return await self._call(args, action, live=False, consent=False)


# Importing this module cannot enable persistence.
file_insights = FileInsightManager()
