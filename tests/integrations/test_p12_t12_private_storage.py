"""LOCAL ONLY: real PostgreSQL 0128, root conftest and exclusive synthetic schema.

Never run with --noconftest. No provider/model/network; synthetic trusted readers.
Follow the DB lock/inventory/cleanup procedure before/after this explicit file.
"""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, func, select, update

from app.core.database import async_session_maker
from app.integrations.file_workspace.content_read import ReadPlainText
from app.integrations.file_workspace.insight_candidates import InsightCandidate, InsightCandidateBatch
from app.integrations.file_workspace.line_evidence import LineRange, build_line_evidence
from app.integrations.file_workspace.plain_text import extract_plain_text
from app.integrations.file_workspace.private_admission import ExactVersionAssertion
from app.integrations.file_workspace.private_cipher import PrivateCipher
from app.models import TenantUser, WorkspaceConnection, WorkspaceConnectionSecret
from app.models.file_workspace.private_insights import (
    FileInsightAttempt,
    FileInsightMount,
    FileInsightObjectState,
    FilePrivateInsight,
)
from app.models.managers.file_insight_manager import POLICY_REVISION, FileInsightManager, PrivateInsightUnavailable
from app.services.file_workspace.private_insights import PrivateInsightWorkflow
from tests.shared.file_catalog_fixtures import actor, admit
from tests.shared.file_catalog_fixtures import data as catalog_data
from tests.shared.file_catalog_fixtures import manager as catalog


@pytest.fixture
async def data(catalog_data):
    try:
        yield catalog_data
    finally:
        async with async_session_maker() as session, session.begin():
            await session.execute(
                delete(FileInsightMount).where(FileInsightMount.connection_id.in_(catalog_data.connections))
            )
        async with async_session_maker() as session:
            for model in (FileInsightMount, FileInsightObjectState, FileInsightAttempt, FilePrivateInsight):
                assert (
                    await session.scalar(
                        select(func.count()).select_from(model).where(model.connection_id.in_(catalog_data.connections))
                    )
                    == 0
                )


@pytest.fixture
def store():
    return FileInsightManager(enabled=True, cipher=PrivateCipher(Fernet.generate_key().decode()))


async def completed(store, args, object_id):
    request = await store.begin_read(**args, object_id=object_id)
    context = await store.read_context(request)
    source = ReadPlainText(
        context, extract_plain_text(b"synthetic first\nsecond\n", name=context.name), "synthetic-version"
    )
    receipt = await store.complete_read(source, version_semantics="synthetic-bytes-v1")
    evidence = build_line_evidence(source, ranges=(LineRange(1, 1),))
    batch = InsightCandidateBatch(request.nonce, evidence, (InsightCandidate("Unverified synthetic", (1,)),))
    return source, batch, receipt


class SyntheticVersion:
    async def verify(self, source, receipt):
        return ExactVersionAssertion(
            source.context.request,
            receipt.raw_sha256,
            receipt.provider_version,
            receipt.version_semantics,
            receipt.provider_object_id,
            datetime.now(timezone.utc),
        )


async def setup(store, args):
    await store.set_consent(**args, processing=True, retention=True, policy_revision=POLICY_REVISION)
    await admit(args, "synthetic.txt")
    page = await catalog.list_page(**args)
    return next(item["id"] for item in page["items"] if item["name"] == "synthetic.txt")


@pytest.mark.asyncio
async def test_default_off_and_consent_required(data, store):
    with actor(data) as args:
        with pytest.raises(PrivateInsightUnavailable):
            await FileInsightManager().begin_read(**args, object_id=1)
        with pytest.raises(PrivateInsightUnavailable):
            await store.begin_read(**args, object_id=1)


@pytest.mark.asyncio
async def test_save_retry_delete_and_no_replay(data, store):
    with actor(data) as args:
        object_id = await setup(store, args)
        source, batch, receipt = await completed(store, args, object_id)
        flow = PrivateInsightWorkflow(store)
        first = await flow.save(source, batch, receipt, versions=SyntheticVersion())
        second = await flow.save(source, batch, receipt, versions=SyntheticVersion())
        assert first.id == second.id and first.payload == second.payload
        async with async_session_maker() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(FilePrivateInsight)
                    .where(FilePrivateInsight.connection_id == args["connection_id"])
                )
                == 1
            )
        result = await store.get(**args, object_id=object_id, result_id=first.id)
        assert result.historical
        await store.forget_object(**args, object_id=object_id)
        with pytest.raises(PrivateInsightUnavailable):
            await flow.save(source, batch, receipt, versions=SyntheticVersion())


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["membership", "secret", "connection", "policy"])
async def test_revoke_restore_never_resurrects_old_attempt(data, store, mutation):
    with actor(data) as args:
        object_id = await setup(store, args)
        source, batch, receipt = await completed(store, args, object_id)
        result = await PrivateInsightWorkflow(store).save(source, batch, receipt, versions=SyntheticVersion())
        async with async_session_maker() as session, session.begin():
            if mutation == "membership":
                where = (TenantUser.tenant_id == args["tenant_id"], TenantUser.user_id == args["actor_id"])
                await session.execute(update(TenantUser).where(*where).values(is_active=False))
                await session.execute(update(TenantUser).where(*where).values(is_active=True))
            elif mutation == "secret":
                secret = await session.get(WorkspaceConnectionSecret, args["connection_id"])
                original = secret.secret_encrypted
                secret.secret_encrypted = ""
                await session.flush()
                secret.secret_encrypted = original
            else:
                conn = await session.get(WorkspaceConnection, args["connection_id"])
                if mutation == "connection":
                    conn.is_active = False
                    await session.flush()
                    conn.is_active = True
                else:
                    original = conn.config
                    conn.config = {"file_policy": {"version": 1, "root": "disk:/elsewhere", "restrictions": []}}
                    await session.flush()
                    conn.config = original
        async with async_session_maker() as session:
            mount = await session.get(FileInsightMount, args["connection_id"])
            assert not mount.processing_allowed and not mount.retention_allowed
            assert await session.get(FileInsightAttempt, source.context.request.nonce) is None
        with pytest.raises(PrivateInsightUnavailable):
            await store.get(**args, object_id=object_id, result_id=result.id)
        await store.set_consent(**args, processing=True, retention=True, policy_revision=POLICY_REVISION)
        with pytest.raises(PrivateInsightUnavailable):
            await PrivateInsightWorkflow(store).save(source, batch, receipt, versions=SyntheticVersion())


@pytest.mark.asyncio
async def test_concurrent_last_object_slot_admits_once(data, store):
    with actor(data) as args:
        object_id = await setup(store, args)
        attempts = [await completed(store, args, object_id) for _ in range(4)]
        flow = PrivateInsightWorkflow(store)
        for values in attempts[:2]:
            await flow.save(*values, versions=SyntheticVersion())

        async def save_one(values):
            try:
                return await flow.save(*values, versions=SyntheticVersion())
            except PrivateInsightUnavailable:
                return None

        results = await asyncio.gather(*(save_one(values) for values in attempts[2:]))
        assert sum(result is not None for result in results) == 1
        async with async_session_maker() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(FilePrivateInsight)
                    .where(FilePrivateInsight.object_id == object_id)
                )
                == 3
            )


@pytest.mark.asyncio
async def test_other_personal_owner_cannot_read_or_consent(data, store):
    with actor(data) as args:
        object_id = await setup(store, args)
        values = await completed(store, args, object_id)
        saved = await PrivateInsightWorkflow(store).save(*values, versions=SyntheticVersion())
    with actor(data, user=1) as other:
        with pytest.raises(PrivateInsightUnavailable):
            await store.get(**other, object_id=object_id, result_id=saved.id)
        with pytest.raises(PrivateInsightUnavailable):
            await store.set_consent(**other, processing=True, retention=True, policy_revision=POLICY_REVISION)


@pytest.mark.asyncio
async def test_expired_attempt_gc_is_idempotent(data, store):
    with actor(data) as args:
        object_id = await setup(store, args)
        request = await store.begin_read(**args, object_id=object_id)
        old = datetime.now(timezone.utc) - timedelta(hours=25)
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(FileInsightAttempt)
                .where(FileInsightAttempt.id == request.nonce)
                .values(captured_at=old, expires_at=old + timedelta(hours=24))
            )
        assert await store.purge_expired(**args) == 1
        assert await store.purge_expired(**args) == 0
        with pytest.raises(PrivateInsightUnavailable):
            await store.read_context(request)
