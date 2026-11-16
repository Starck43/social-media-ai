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

from app.core.database import async_session_maker, engine
from app.core.tenant_context import tenant_scope
from app.integrations.file_workspace.content_read import ReadPlainText
from app.integrations.file_workspace.insight_candidates import InsightCandidate, InsightCandidateBatch
from app.integrations.file_workspace.line_evidence import LineRange, build_line_evidence
from app.integrations.file_workspace.plain_text import extract_plain_text
from app.integrations.file_workspace.private_admission import ExactVersionAssertion
from app.integrations.file_workspace.private_cipher import PrivateCipher
from app.models import LLMProvider, TenantUser, WorkspaceConnection, WorkspaceConnectionSecret
from app.models.file_workspace.private_insights import (
    FileInsightAttempt,
    FileInsightMount,
    FileInsightObjectState,
    FilePrivateInsight,
)
from app.models.managers.file_insight_manager import POLICY_REVISION, FileInsightManager, PrivateInsightUnavailable
from app.services.file_workspace.private_insights import PrivateInsightWorkflow
from app.services.file_workspace.private_maintenance import purge_expired_private_insights
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


async def _expire_attempt(request):
    old = datetime.now(timezone.utc) - timedelta(hours=25)
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(FileInsightAttempt)
            .where(FileInsightAttempt.id == request.nonce)
            .values(captured_at=old, expires_at=old + timedelta(hours=24))
        )


async def _result_for(store, args, object_id):
    source, batch, receipt = await completed(store, args, object_id)
    result = await PrivateInsightWorkflow(store).save(source, batch, receipt, versions=SyntheticVersion())
    return source, batch, receipt, result


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


@pytest.mark.asyncio
async def test_runtime_gc_removes_only_expired_private_content_in_bounded_batches(data, store):
    with actor(data) as args:
        object_id = await setup(store, args)
        expired = await _result_for(store, args, object_id)
        current = await _result_for(store, args, object_id)
        await _expire_attempt(expired[0].context.request)

        with tenant_scope(bypass=True):
            assert await purge_expired_private_insights(limit=1) == 1
            assert await purge_expired_private_insights(limit=1) == 0

        async with async_session_maker() as session:
            assert await session.get(FileInsightAttempt, expired[0].context.request.nonce) is None
            assert await session.get(FilePrivateInsight, expired[3].id) is None
            assert await session.get(FileInsightAttempt, current[0].context.request.nonce) is not None
            assert await session.get(FilePrivateInsight, current[3].id) is not None


@pytest.mark.asyncio
async def test_runtime_gc_skips_rows_locked_by_owner_transactions(data, store):
    with actor(data) as args:
        object_id = await setup(store, args)
        first = await completed(store, args, object_id)
        second = await completed(store, args, object_id)
        await _expire_attempt(first[0].context.request)
        await _expire_attempt(second[0].context.request)

        async with async_session_maker() as locked, locked.begin():
            await locked.scalar(
                select(FileInsightAttempt)
                .where(FileInsightAttempt.id == first[0].context.request.nonce)
                .with_for_update()
            )
            with tenant_scope(bypass=True):
                assert await purge_expired_private_insights(limit=1) == 1
            assert await locked.get(FileInsightAttempt, first[0].context.request.nonce) is not None

        with tenant_scope(bypass=True):
            assert await purge_expired_private_insights(limit=1) == 1
            assert await purge_expired_private_insights(limit=1) == 0


@pytest.mark.asyncio
async def test_private_manager_preserves_clock_highwater_on_rollback_denial(data, store):
    with actor(data) as args:
        object_id = await setup(store, args)
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        async with async_session_maker() as session, session.begin():
            mount = await session.get(FileInsightMount, args["connection_id"])
            mount.clock_highwater = future

        with pytest.raises(PrivateInsightUnavailable):
            await store.begin_read(**args, object_id=object_id)

        async with async_session_maker() as session:
            mount = await session.get(FileInsightMount, args["connection_id"])
            assert mount.clock_highwater == future
            mount.clock_highwater = datetime.now(timezone.utc)
            await session.commit()


@pytest.mark.asyncio
async def test_forget_object_racing_external_read_prevents_late_save(data, store):
    entered = asyncio.Event()
    release = asyncio.Event()

    class PausedVersion:
        async def verify(self, source, receipt):
            entered.set()
            await release.wait()
            return await SyntheticVersion().verify(source, receipt)

    with actor(data) as args:
        object_id = await setup(store, args)
        source, batch, receipt = await completed(store, args, object_id)
        save = asyncio.create_task(PrivateInsightWorkflow(store).save(source, batch, receipt, versions=PausedVersion()))
        await asyncio.wait_for(entered.wait(), timeout=5)
        await store.forget_object(**args, object_id=object_id)
        release.set()
        with pytest.raises(PrivateInsightUnavailable):
            await save

        async with async_session_maker() as session:
            assert await session.get(FileInsightAttempt, source.context.request.nonce) is None
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(FilePrivateInsight)
                    .where(FilePrivateInsight.connection_id == args["connection_id"])
                )
                == 0
            )


@pytest.mark.asyncio
async def test_private_save_enforces_exact_last_byte_of_connection_quota(data, store, monkeypatch):
    from app.integrations.file_workspace import retention_quota
    from app.services.file_workspace.private_admission import prepare_private_payload
    from app.services.file_workspace.private_insights import OwnedAdmissionAuthority

    with actor(data) as args:
        object_id = await setup(store, args)
        first = await _result_for(store, args, object_id)
        second = await completed(store, args, object_id)
        second_payload = await prepare_private_payload(
            second[0],
            second[1],
            second[2],
            authority=OwnedAdmissionAuthority(store),
            versions=SyntheticVersion(),
        )
        async with async_session_maker() as session:
            used = await session.scalar(
                select(func.sum(FilePrivateInsight.charged_size)).where(
                    FilePrivateInsight.connection_id == args["connection_id"]
                )
            )
        assert used == len(first[3].payload)

        monkeypatch.setattr(retention_quota, "CONNECTION_BYTE_LIMIT", used + len(second_payload) - 1)
        with pytest.raises(PrivateInsightUnavailable):
            await PrivateInsightWorkflow(store).save(*second, versions=SyntheticVersion())

        monkeypatch.setattr(retention_quota, "CONNECTION_BYTE_LIMIT", used + len(second_payload))
        accepted = await PrivateInsightWorkflow(store).save(*second, versions=SyntheticVersion())
        assert len(accepted.payload) == len(second_payload)


@pytest.mark.asyncio
async def test_credential_rotation_requires_explicit_private_result_purge_and_rolls_back(data, store):
    from cryptography.fernet import Fernet

    from app.core.config import settings
    from app.models import FilePrivateInsight
    from app.utils.crypto import decrypt_secret, encrypt_secret
    from scripts.ops.rotate_credentials_key import RotationError, rotate_in_transaction

    old_key = settings.CREDENTIALS_KEY
    new_key = Fernet.generate_key().decode()
    store.cipher = PrivateCipher(old_key)

    provider_id = None
    try:
        with actor(data) as args:
            object_id = await setup(store, args)
            source, batch, receipt, saved = await _result_for(store, args, object_id)
            async with async_session_maker() as session, session.begin():
                connection_secret = await session.get(WorkspaceConnectionSecret, args["connection_id"])
                original_ciphertext = connection_secret.secret_encrypted
                original_plaintext = decrypt_secret(original_ciphertext)
                provider = LLMProvider(
                    owner_user_id=args["actor_id"],
                    uid="p12t12-rotation-" + source.context.request.nonce,
                    name="Synthetic provider",
                    api_format="openai",
                    base_url="https://synthetic.invalid/v1",
                    encrypted_api_key=encrypt_secret("synthetic-provider-key"),
                    is_active=False,
                )
                session.add(provider)
                await session.flush()
                provider_id = provider.id

            with engine.begin() as connection:
                dry_run = rotate_in_transaction(
                    connection,
                    schema="test_schema",
                    old_key=old_key,
                    new_key=new_key,
                    apply=False,
                    allow_private_results_purge=False,
                )
            assert dry_run["private_consent_mounts"] == 1
            assert dry_run["private_attempts"] == 1
            assert dry_run["private_results"] == 1

            with pytest.raises(RotationError):
                with engine.begin() as connection:
                    rotate_in_transaction(
                        connection,
                        schema="test_schema",
                        old_key=old_key,
                        new_key=new_key,
                        apply=True,
                        allow_private_results_purge=False,
                    )

            async with async_session_maker() as session:
                mount = await session.get(FileInsightMount, args["connection_id"])
                assert mount.processing_allowed and mount.retention_allowed
                assert await session.get(FileInsightAttempt, source.context.request.nonce) is not None
                assert await session.get(FilePrivateInsight, saved.id) is not None
                assert (
                    await session.get(WorkspaceConnectionSecret, args["connection_id"])
                ).secret_encrypted == original_ciphertext

            async with async_session_maker() as session, session.begin():
                provider = await session.get(LLMProvider, provider_id)
                provider.encrypted_api_key = "invalid-synthetic-ciphertext"

            with pytest.raises(RotationError):
                with engine.begin() as connection:
                    rotate_in_transaction(
                        connection,
                        schema="test_schema",
                        old_key=old_key,
                        new_key=new_key,
                        apply=True,
                        allow_private_results_purge=True,
                    )

            async with async_session_maker() as session:
                mount = await session.get(FileInsightMount, args["connection_id"])
                assert mount.processing_allowed and mount.retention_allowed
                assert await session.get(FileInsightAttempt, source.context.request.nonce) is not None
                assert await session.get(FilePrivateInsight, saved.id) is not None
                assert (
                    await session.get(WorkspaceConnectionSecret, args["connection_id"])
                ).secret_encrypted == original_ciphertext

            async with async_session_maker() as session, session.begin():
                provider = await session.get(LLMProvider, provider_id)
                provider.encrypted_api_key = encrypt_secret("synthetic-provider-key")

            with engine.begin() as connection:
                applied = rotate_in_transaction(
                    connection,
                    schema="test_schema",
                    old_key=old_key,
                    new_key=new_key,
                    apply=True,
                    allow_private_results_purge=True,
                )
            assert applied["private_consent_mounts"] == 1
            assert applied["private_attempts"] == 1
            assert applied["private_results"] == 1

            async with async_session_maker() as session:
                mount = await session.get(FileInsightMount, args["connection_id"])
                assert not mount.processing_allowed and not mount.retention_allowed
                assert await session.get(FileInsightAttempt, source.context.request.nonce) is None
                assert await session.get(FilePrivateInsight, saved.id) is None
                rotated_secret = (await session.get(WorkspaceConnectionSecret, args["connection_id"])).secret_encrypted
                assert rotated_secret != original_ciphertext
                assert Fernet(new_key.encode()).decrypt(rotated_secret.encode()).decode() == original_plaintext
    finally:
        if provider_id is not None:
            async with async_session_maker() as session, session.begin():
                await session.execute(delete(LLMProvider).where(LLMProvider.id == provider_id))
