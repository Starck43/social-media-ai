"""No-DB ORM/manager/workflow boundary tests; not PostgreSQL concurrency evidence."""

import asyncio
import base64
import hashlib
import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.integrations.file_workspace.private_cipher import PrivateCipher
from app.integrations.file_workspace.private_projection import serialize_same_read_payload
from tests.shared.file_private_admission import SyntheticExactVersionAuthority, synthetic_private_admission
from tests.shared.file_private_storage import FakeSession, storage_modules

__source_only__ = True


@pytest.fixture(scope="module")
def modules():
    return storage_modules()


@pytest.fixture
def scenario(modules):
    models, module, workflow = modules
    source, batch, receipt, first, final, assertion = synthetic_private_admission()
    session = FakeSession()
    mount = SimpleNamespace(
        owner_id=2,
        owner_epoch=receipt.binding.owner_epoch,
        connection_epoch=receipt.binding.connection_epoch,
        consent_revision=receipt.binding.consent_revision,
        processing_allowed=True,
        retention_allowed=True,
        policy_revision=module.POLICY_REVISION,
    )
    attempt = models.FileInsightAttempt(
        id=source.context.request.nonce,
        tenant_id=1,
        connection_id=3,
        owner_id=2,
        object_id=4,
        captured_at=receipt.captured_at,
        expires_at=receipt.captured_at + timedelta(hours=24),
        binding_data=module._binding_data(receipt.binding),
        provenance=module._provenance(source, receipt.version_semantics, receipt.provider_object_id),
        consumed=False,
    )
    access = SimpleNamespace(
        session=session,
        mount=mount,
        connection=SimpleNamespace(id=3, tenant_id=1),
        now=final.now,
        obj=SimpleNamespace(id=4),
        object_state=SimpleNamespace(epoch=receipt.binding.object_epoch),
        binding=lambda request: receipt.binding,
    )
    manager = module.FileInsightManager(enabled=True, cipher=PrivateCipher(Fernet.generate_key().decode()))
    calls = []

    async def invoke(args, action, **kwargs):
        calls.append((args, kwargs))
        return await action(access)

    manager._call = invoke
    manager._quota = AsyncMock(return_value=final.quota)
    return SimpleNamespace(
        models=models,
        module=module,
        workflow=workflow,
        source=source,
        batch=batch,
        receipt=receipt,
        assertion=assertion,
        access=access,
        attempt=attempt,
        manager=manager,
        session=session,
        calls=calls,
        first=first,
        final=final,
    )


def save(s):
    return asyncio.run(s.manager.save(s.source, s.batch, s.receipt, s.assertion))


def test_schema_ddl_is_scoped_and_private(modules):
    models, _, _ = modules
    for name in ("FileInsightMount", "FileInsightObjectState", "FileInsightAttempt", "FilePrivateInsight"):
        table = getattr(models, name).__table__
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert "private_test" in ddl and "tenant_id" in ddl
        assert "full_text" not in ddl and "download_url" not in ddl
    assert "UNIQUE (attempt_id)" in str(
        CreateTable(models.FilePrivateInsight.__table__).compile(dialect=postgresql.dialect())
    )


def test_default_manager_denies_before_session_creation(modules):
    _, module, _ = modules
    with pytest.raises(module.PrivateInsightUnavailable):
        asyncio.run(module.FileInsightManager().begin_read(tenant_id=1, actor_id=2, connection_id=3, object_id=4))
    assert module.file_insights.enabled is False


def test_save_charges_complete_canonical_bytes_and_consumes_attempt(scenario):
    s = scenario
    s.session.queue = [s.attempt, None]
    result = save(s)
    row = s.session.added[0]
    assert row.charged_size == len(result.payload)
    assert row.payload_sha256 == hashlib.sha256(result.payload).hexdigest()
    assert row.payload_encrypted != result.payload.decode()
    assert s.manager.cipher.decrypt(row.payload_encrypted) == result.payload
    assert s.attempt.consumed and s.attempt.generation_id == s.batch.generation_id
    assert result.historical and result.review_status == "unverified"
    assert s.calls[0][1] == {"object_id": 4}
    assert s.manager._quota.await_count == 1


def test_idempotent_same_attempt_no_extra_insert_or_charge(scenario):
    s = scenario
    s.session.queue = [s.attempt, None]
    first = save(s)
    row = s.session.added[0]
    s.session.queue = [s.attempt, row]
    second = save(s)
    assert first.id == second.id and first.payload == second.payload
    assert len(s.session.added) == 1 and s.manager._quota.await_count == 1


def test_same_attempt_changed_candidates_is_rejected(scenario):
    s = scenario
    s.session.queue = [s.attempt, None]
    save(s)
    row = s.session.added[0]
    s.batch = replace(s.batch, candidates=())
    s.session.queue = [s.attempt, row]
    with pytest.raises(s.module.PrivateInsightUnavailable):
        save(s)


def test_consumed_attempt_missing_row_never_recreates(scenario):
    s = scenario
    s.attempt.consumed = True
    s.session.queue = [s.attempt, None]
    with pytest.raises(s.module.PrivateInsightUnavailable):
        save(s)
    assert not s.session.added


@pytest.mark.parametrize(
    "field",
    [
        "owner_epoch",
        "connection_epoch",
        "object_epoch",
        "consent_revision",
        "policy_revision",
        "path_key",
        "context_key",
    ],
)
def test_late_save_after_epoch_or_context_change_denied(scenario, field):
    s = scenario
    s.attempt.binding_data = dict(s.attempt.binding_data, **{field: "changed"})
    s.session.queue = [s.attempt]
    with pytest.raises(s.module.PrivateInsightUnavailable):
        save(s)
    assert not s.session.added


@pytest.mark.parametrize("delta", [timedelta(seconds=6), timedelta(seconds=-1)])
def test_stale_or_future_version_assertion_denied(scenario, delta):
    s = scenario
    s.assertion = replace(s.assertion, checked_at=s.access.now - delta)
    s.session.queue = [s.attempt]
    with pytest.raises(s.module.PrivateInsightUnavailable):
        save(s)


def test_missing_ledger_cannot_be_recreated_from_receipt(scenario):
    s = scenario
    s.session.queue = [None]
    with pytest.raises(s.module.PrivateInsightUnavailable):
        save(s)
    assert not s.session.added


def test_final_transaction_quota_denial(scenario):
    s = scenario
    s.session.queue = [s.attempt, None]
    s.manager._quota.return_value = replace(s.final.quota, object_records=3)
    with pytest.raises(s.module.PrivateInsightUnavailable):
        save(s)
    assert not s.session.added and not s.attempt.consumed


def test_get_checks_stored_binding_and_returns_only_historical(scenario):
    s = scenario
    s.session.queue = [s.attempt, None]
    first = save(s)
    row = s.session.added[0]
    s.session.queue = [row, s.attempt]
    second = asyncio.run(s.manager.get(tenant_id=1, actor_id=2, connection_id=3, object_id=4, result_id=first.id))
    assert second.payload == first.payload and second.historical


def test_content_tamper_denies_release(scenario):
    s = scenario
    s.session.queue = [s.attempt, None]
    first = save(s)
    row = s.session.added[0]
    row.payload_encrypted = row.payload_encrypted[:20] + "X" + row.payload_encrypted[21:]
    s.session.queue = [row, s.attempt]
    with pytest.raises(s.module.PrivateInsightUnavailable):
        asyncio.run(s.manager.get(tenant_id=1, actor_id=2, connection_id=3, object_id=4, result_id=first.id))


def test_consent_change_always_purges_and_rotates(scenario):
    s = scenario
    old = (s.access.mount.owner_epoch, s.access.mount.connection_epoch, s.access.mount.consent_revision)
    result = asyncio.run(
        s.manager.set_consent(
            tenant_id=1,
            actor_id=2,
            connection_id=3,
            processing=True,
            retention=True,
            policy_revision=s.module.POLICY_REVISION,
        )
    )
    assert result["processing"] and result["retention"]
    assert all(
        a != b
        for a, b in zip(
            old, (s.access.mount.owner_epoch, s.access.mount.connection_epoch, s.access.mount.consent_revision)
        )
    )
    assert "DELETE FROM private_test.file_insight_attempts" in str(s.session.executed[0])
    assert s.calls[0][1] == dict(create_mount=True, live=True, write=True, consent=False)


def test_withdrawal_works_without_live_connection(scenario):
    s = scenario
    asyncio.run(
        s.manager.set_consent(
            tenant_id=1,
            actor_id=2,
            connection_id=3,
            processing=False,
            retention=False,
            policy_revision=s.module.POLICY_REVISION,
        )
    )
    assert not s.access.mount.processing_allowed and not s.access.mount.retention_allowed
    assert s.calls[0][1]["live"] is False


@pytest.mark.parametrize("processing,retention", [(False, True), (1, True), (True, 1), (None, False)])
def test_invalid_consent_rejected_before_action(scenario, processing, retention):
    s = scenario
    with pytest.raises(s.module.PrivateInsightUnavailable):
        asyncio.run(
            s.manager.set_consent(
                tenant_id=1,
                actor_id=2,
                connection_id=3,
                processing=processing,
                retention=retention,
                policy_revision=s.module.POLICY_REVISION,
            )
        )
    assert not s.calls


def test_forget_object_rotates_fence_before_purge(scenario):
    s = scenario
    previous = s.access.object_state.epoch
    asyncio.run(s.manager.forget_object(tenant_id=1, actor_id=2, connection_id=3, object_id=4))
    assert s.access.object_state.epoch != previous
    assert s.calls[0][1] == {"object_id": 4, "consent": False}


def test_complete_read_uses_original_ledger_capture(scenario):
    s = scenario
    s.attempt.provenance = None
    s.session.queue = [s.attempt]
    receipt = asyncio.run(s.manager.complete_read(s.source, version_semantics=s.receipt.version_semantics))
    assert receipt.captured_at == s.attempt.captured_at and receipt.captured_at != s.access.now
    assert receipt == s.receipt


def test_old_capture_never_restamped(scenario):
    s = scenario
    s.access.now = s.receipt.captured_at + timedelta(minutes=5, microseconds=1)
    s.session.queue = [s.attempt]
    with pytest.raises(s.module.PrivateInsightUnavailable):
        asyncio.run(s.manager.complete_read(s.source, version_semantics=s.receipt.version_semantics))
    assert s.attempt.captured_at == s.receipt.captured_at


def test_workflow_rechecks_before_final_manager_save(scenario):
    s = scenario
    manager = SimpleNamespace(
        admission_state=AsyncMock(side_effect=[s.first, s.final]), save=AsyncMock(return_value="committed")
    )
    versions = SyntheticExactVersionAuthority(s.assertion, [])
    result = asyncio.run(
        s.workflow.PrivateInsightWorkflow(manager).save(s.source, s.batch, s.receipt, versions=versions)
    )
    assert result == "committed" and manager.admission_state.await_count == 2
    manager.save.assert_awaited_once_with(s.source, s.batch, s.receipt, s.assertion)


def test_failed_version_never_calls_manager_save(scenario):
    s = scenario
    manager = SimpleNamespace(admission_state=AsyncMock(return_value=s.first), save=AsyncMock())
    versions = SyntheticExactVersionAuthority(s.assertion, [])
    versions.error = NotImplementedError("secret")
    with pytest.raises(s.module.PrivateInsightUnavailable) as caught:
        asyncio.run(s.workflow.PrivateInsightWorkflow(manager).save(s.source, s.batch, s.receipt, versions=versions))
    assert caught.value.__context__ is None
    manager.save.assert_not_awaited()


def test_cipher_is_purpose_separated_and_rotated_key_denies():
    key = Fernet.generate_key().decode()
    cipher = PrivateCipher(key)
    source, batch, receipt, _, final, _ = synthetic_private_admission()
    from app.services.file_workspace.private_admission import _metadata

    payload = serialize_same_read_payload(
        source, batch, metadata=_metadata(receipt, final.now, receipt.captured_at + timedelta(hours=24))
    )
    token = cipher.encrypt(payload)
    assert cipher.decrypt(token) == payload
    with pytest.raises(Exception):
        Fernet(key.encode()).decrypt(token.encode())
    with pytest.raises(ValueError):
        PrivateCipher(Fernet.generate_key().decode()).decrypt(token)
    assert payload.decode() not in token


@pytest.mark.parametrize("key", ["", None, "x" * 44, base64.urlsafe_b64encode(b"a" * 31).decode()])
def test_cipher_bad_key_fails_closed(key):
    with pytest.raises(ValueError):
        PrivateCipher(key)


def test_transaction_lock_order_and_denial_commits_high_water(modules, monkeypatch):
    _, module, _ = modules
    from tests.shared.file_private_storage import FakeSession

    source, batch, receipt, first, final, assertion = synthetic_private_admission()
    events = []

    class Context:
        def __init__(self, name):
            self.name = name

        async def __aenter__(self):
            events.append(self.name + ":enter")
            return session

        async def __aexit__(self, kind, value, tb):
            events.append(self.name + (":rollback" if kind else ":commit"))

    class Session(FakeSession):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def begin(self):
            return Context("outer")

        def begin_nested(self):
            return Context("inner")

        async def execute(self, query):
            if "pg_advisory_xact_lock" in str(query):
                events.append("tenant-lock")
            return await super().execute(query)

    mount = SimpleNamespace(
        owner_id=2,
        policy_revision=module.POLICY_REVISION,
        processing_allowed=True,
        retention_allowed=True,
        clock_highwater=first.now,
    )
    session = Session([mount, final.now])
    session.get = AsyncMock(return_value=SimpleNamespace(secret_encrypted="encrypted-synthetic-only"))

    async def authorize(*args, **kwargs):
        events.append("authorize")

    async def connection(*args):
        events.append("connection-lock")
        return SimpleNamespace(id=3, tenant_id=1)

    monkeypatch.setattr(module, "async_session_maker", lambda: session)
    monkeypatch.setattr(module, "_authorize", authorize)
    monkeypatch.setattr(module, "_connection", connection)

    async def denied(a):
        raise ValueError("private-source-details")

    manager = module.FileInsightManager(enabled=True)
    with pytest.raises(module.PrivateInsightUnavailable) as caught:
        asyncio.run(manager._call(dict(tenant_id=1, actor_id=2, connection_id=3), denied))
    assert caught.value.__context__ is None
    assert events == [
        "outer:enter",
        "tenant-lock",
        "authorize",
        "connection-lock",
        "inner:enter",
        "inner:rollback",
        "outer:commit",
    ]
    assert mount.clock_highwater == final.now


def test_migration_declares_hooks_and_matches_model_columns(modules):
    from tests.shared.file_private_storage import source_module

    class Operations:
        def __init__(self):
            self.tables = {}
            self.sql = []
            self.indices = []
            self.dropped = []

        def create_table(self, name, *items, **kwargs):
            self.tables[name] = items

        def create_index(self, *args, **kwargs):
            self.indices.append(args)

        def execute(self, sql):
            self.sql.append(str(sql))

        def drop_table(self, name, **kwargs):
            self.dropped.append(name)

    op = Operations()
    module = source_module(
        "migrations/versions/0128_file_private_insights.py",
        "_private_storage_migration",
        dict(op=op, settings=SimpleNamespace(DB_SCHEMA="private_test")),
        ("alembic", "app.core"),
    )
    module.upgrade()
    assert module.revision == "0128" and module.down_revision == "0127"
    from sqlalchemy import Column

    models, _, _ = modules
    for name in ("FileInsightMount", "FileInsightObjectState", "FileInsightAttempt", "FilePrivateInsight"):
        table = getattr(models, name).__table__
        columns = {item.name for item in op.tables[table.name] if isinstance(item, Column)}
        assert columns == set(table.columns.keys())
    sql = "\n".join(op.sql)
    for table in (
        "workspace_connections",
        "workspace_connection_secrets",
        "tenant_users",
        "users",
        "tenants",
        "workspace_roles",
        "workspace_role_permission",
    ):
        assert f'ON "private_test"."{table}"' in sql
    assert "processing_allowed=false, retention_allowed=false" in sql
    assert 'DELETE FROM "private_test".file_insight_attempts' in sql
    assert "FOR UPDATE" in sql and "gen_random_uuid" in sql
    module.downgrade()
    assert op.dropped == [
        "file_private_insights",
        "file_insight_attempts",
        "file_insight_object_states",
        "file_insight_mounts",
    ]
