"""LOCAL ONLY: database-backed pairing, device, consent and revocation lifecycle."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID
from sqlalchemy import delete, func, select, update

from app.core.database import async_session_maker
from app.core.tenant_context import tenant_scope
from app.models.integrations.local_connector import LocalConnector, LocalConnectorPairing, LocalConnectorRequest
from app.services.local_connector import store as store_module
from app.services.local_connector.crypto import ConnectorAuthority
from app.services.local_connector.store import LocalConnectorStore
from app.services.local_connector.wire import ConnectorUnavailable, SearchArgs, new_request
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401

pytestmark = pytest.mark.tenancy


def make_authority():
    ca_key = Ed25519PrivateKey.generate()
    signer = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "P18 test CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, algorithm=None)
    )
    return ConnectorAuthority(
        ca_pem=ca.public_bytes(serialization.Encoding.PEM),
        ca_key_pem=ca_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ),
        signing_key_pem=signer.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ),
        pairing_key=b"p18-test-pairing-key-material-32-bytes",
        key_id="p18-test",
    )


def make_csr():
    key = Ed25519PrivateKey.generate()
    csr = x509.CertificateSigningRequestBuilder().subject_name(x509.Name([])).sign(key, algorithm=None)
    return key, csr.public_bytes(serialization.Encoding.PEM).decode("ascii")


@pytest.fixture
async def connector_world(personal_world):
    tenants = [tenant.id for tenant in personal_world.tenants]
    world = SimpleNamespace(
        store=LocalConnectorStore(make_authority()),
        authority=None,
        tenant_id=tenants[0],
        other_tenant_id=tenants[1],
        owner_id=personal_world.users[0].id,
        other_owner_id=personal_world.users[1].id,
        other_tenant_owner_id=personal_world.users[2].id,
    )
    world.authority = world.store.authority
    try:
        yield world
    finally:
        async with async_session_maker() as session, session.begin():
            await session.execute(delete(LocalConnectorRequest).where(LocalConnectorRequest.tenant_id.in_(tenants)))
            await session.execute(delete(LocalConnectorPairing).where(LocalConnectorPairing.tenant_id.in_(tenants)))
            await session.execute(delete(LocalConnector).where(LocalConnector.tenant_id.in_(tenants)))
        async with async_session_maker() as session:
            for model in (LocalConnectorRequest, LocalConnectorPairing, LocalConnector):
                remaining = await session.scalar(
                    select(func.count()).select_from(model).where(model.tenant_id.in_(tenants))
                )
                assert remaining == 0


async def pair_device(world, *, owner_id=None, csr_pem=None):
    owner_id = owner_id or world.owner_id
    key, generated_csr = make_csr() if csr_pem is None else (None, csr_pem)
    with tenant_scope(world.tenant_id):
        pairing = await world.store.begin(
            owner_id=owner_id, device_name="synthetic test connector", platform="linux", version="test"
        )
        offered = await world.store.offer(
            pairing_id=pairing["pairing_id"], secret=pairing["secret"], csr_pem=generated_csr
        )
        device_id = await world.store.confirm(
            owner_id=owner_id, pairing_id=pairing["pairing_id"], fingerprint=offered["fingerprint"]
        )
    return SimpleNamespace(pairing=pairing, offered=offered, device_id=device_id, key=key)


async def pairing_attempts(world, pairing_id):
    async with async_session_maker() as session:
        row = (
            await session.execute(select(LocalConnectorPairing).where(LocalConnectorPairing.pairing_id == pairing_id))
        ).scalar_one()
        return row.attempts, row.expires_at


@pytest.mark.asyncio
async def test_pairing_code_expiry_and_attempt_exhaustion(connector_world):
    world = connector_world
    key, csr_pem = make_csr()
    with tenant_scope(world.tenant_id):
        expired = await world.store.begin(
            owner_id=world.owner_id, device_name="expired", platform="linux", version="test"
        )
    async with async_session_maker() as session, session.begin():
        row = (
            await session.execute(
                select(LocalConnectorPairing).where(LocalConnectorPairing.pairing_id == expired["pairing_id"])
            )
        ).scalar_one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with tenant_scope(world.tenant_id), pytest.raises(ConnectorUnavailable):
        await world.store.offer(pairing_id=expired["pairing_id"], secret=expired["secret"], csr_pem=csr_pem)

    with tenant_scope(world.tenant_id):
        exhausted = await world.store.begin(
            owner_id=world.owner_id, device_name="exhausted", platform="linux", version="test"
        )
        for _ in range(5):
            with pytest.raises(ConnectorUnavailable):
                await world.store.offer(pairing_id=exhausted["pairing_id"], secret="x" * 48, csr_pem=csr_pem)
        with pytest.raises(ConnectorUnavailable):
            await world.store.offer(pairing_id=exhausted["pairing_id"], secret=exhausted["secret"], csr_pem=csr_pem)
    attempts, _ = await pairing_attempts(world, exhausted["pairing_id"])
    assert attempts == 5
    assert key is not None


@pytest.mark.asyncio
async def test_wrong_secret_and_fingerprint_substitution_count_as_failures(connector_world):
    world = connector_world
    _, csr_one = make_csr()
    _, csr_two = make_csr()
    with tenant_scope(world.tenant_id):
        pairing = await world.store.begin(
            owner_id=world.owner_id, device_name="substitution", platform="linux", version="test"
        )
        with pytest.raises(ConnectorUnavailable):
            await world.store.offer(pairing_id=pairing["pairing_id"], secret="z" * 48, csr_pem=csr_one)
        first = await world.store.offer(pairing_id=pairing["pairing_id"], secret=pairing["secret"], csr_pem=csr_one)
        with pytest.raises(ConnectorUnavailable):
            await world.store.offer(pairing_id=pairing["pairing_id"], secret=pairing["secret"], csr_pem=csr_two)
        with pytest.raises(ConnectorUnavailable):
            await world.store.confirm(
                owner_id=world.owner_id, pairing_id=pairing["pairing_id"], fingerprint=world.authority.csr(csr_two)[0]
            )
    attempts, _ = await pairing_attempts(world, pairing["pairing_id"])
    assert attempts == 2
    assert first["fingerprint"] == world.authority.csr(csr_one)[0]


@pytest.mark.asyncio
async def test_confirmation_is_idempotent_and_serializes_concurrent_redemption(connector_world):
    world = connector_world
    _, csr_pem = make_csr()
    with tenant_scope(world.tenant_id):
        pairing = await world.store.begin(
            owner_id=world.owner_id, device_name="concurrent", platform="linux", version="test"
        )
        offered = await world.store.offer(pairing_id=pairing["pairing_id"], secret=pairing["secret"], csr_pem=csr_pem)
        confirm = lambda: world.store.confirm(
            owner_id=world.owner_id, pairing_id=pairing["pairing_id"], fingerprint=offered["fingerprint"]
        )
        results = await asyncio.gather(confirm(), confirm())
        assert results[0] == results[1]
        assert await confirm() == results[0]
    async with async_session_maker() as session:
        count = await session.scalar(
            select(func.count()).select_from(LocalConnector).where(LocalConnector.id == results[0])
        )
    assert count == 1


@pytest.mark.asyncio
async def test_owner_and_tenant_boundaries_apply_to_listing_consent_and_revoke(connector_world):
    world = connector_world
    device = await pair_device(world)
    with tenant_scope(world.tenant_id):
        assert [row.id for row in await world.store.list_owned(owner_id=world.owner_id)] == [device.device_id]
        assert await world.store.list_owned(owner_id=world.other_owner_id) == []
        with pytest.raises(ConnectorUnavailable):
            await world.store.consent(owner_id=world.other_owner_id, device_id=device.device_id, roots=["company"])
        with pytest.raises(ConnectorUnavailable):
            await world.store.revoke(owner_id=world.other_owner_id, device_id=device.device_id)
    with tenant_scope(world.other_tenant_id):
        assert await world.store.list_owned(owner_id=world.other_tenant_owner_id) == []
        with pytest.raises(ConnectorUnavailable):
            await world.store.consent(
                owner_id=world.other_tenant_owner_id, device_id=device.device_id, roots=["company"]
            )
        with pytest.raises(ConnectorUnavailable):
            await world.store.revoke(owner_id=world.other_tenant_owner_id, device_id=device.device_id)


@pytest.mark.asyncio
async def test_company_root_consent_epochs_and_revocation_invalidate_ledger_and_channel_identity(connector_world):
    world = connector_world
    device = await pair_device(world)
    with tenant_scope(world.tenant_id):
        await world.store.consent(owner_id=world.owner_id, device_id=device.device_id, roots=["company"])
        before = await world.store.authorize(owner_id=world.owner_id, device_id=device.device_id, roots=["company"])
        await world.store.consent(owner_id=world.owner_id, device_id=device.device_id, roots=["company"])
        after = await world.store.authorize(owner_id=world.owner_id, device_id=device.device_id, roots=["company"])
        assert after[:3] == before[:3]
        assert after[3] == before[3]
        assert after[4] == before[4] + 1
        assert after[5] == before[5] + 1
        with pytest.raises(ConnectorUnavailable):
            await world.store.authorize(
                owner_id=world.owner_id, device_id=device.device_id, roots=["company"], previous=before
            )
        request = new_request(
            key_id=world.authority.key_id,
            tenant_id=after[0],
            owner_id=after[1],
            device_id=after[2],
            device_epoch=after[3],
            consent_epoch=after[4],
            policy_epoch=after[5],
            args=SearchArgs(query="synthetic", roots=["company"]),
        )
        digest = "a" * 64
        await world.store.reserve(request, digest)
        async with async_session_maker() as session:
            certificate_pem = await session.scalar(
                select(LocalConnector.certificate_pem).where(LocalConnector.id == device.device_id)
            )
        peer_fingerprint, serial = world.authority.peer(
            {
                "scheme": "wss",
                "extensions": {"tls": {"client_cert_chain": [certificate_pem]}},
            }
        )
        identity = await world.store.channel_identity(fingerprint=peer_fingerprint, serial=serial)
        assert identity == (after[0], after[1], after[2], after[3])
        await world.store.revoke(owner_id=world.owner_id, device_id=device.device_id)
        with pytest.raises(ConnectorUnavailable):
            await world.store.channel_identity(fingerprint=peer_fingerprint, serial=serial)
        with pytest.raises(ConnectorUnavailable):
            await world.store.finish(request, digest)

    async with async_session_maker() as session:
        device_row = (
            await session.execute(select(LocalConnector).where(LocalConnector.id == device.device_id))
        ).scalar_one()
        ledger = (
            await session.execute(
                select(LocalConnectorRequest).where(LocalConnectorRequest.device_id == device.device_id)
            )
        ).scalar_one()
        assert device_row.revoked_at is not None
        assert device_row.epoch == after[3] + 1
        assert device_row.cloud_roots == []
        assert ledger.state == "unknown"


@pytest.mark.asyncio
async def test_replay_ledger_persists_across_store_restart_and_prunes_expired_rows(connector_world, monkeypatch):
    world = connector_world
    device = await pair_device(world)
    with tenant_scope(world.tenant_id):
        await world.store.consent(owner_id=world.owner_id, device_id=device.device_id, roots=["company"])
        snapshot = await world.store.authorize(owner_id=world.owner_id, device_id=device.device_id, roots=["company"])

        def request():
            return new_request(
                key_id=world.authority.key_id,
                tenant_id=snapshot[0],
                owner_id=snapshot[1],
                device_id=snapshot[2],
                device_epoch=snapshot[3],
                consent_epoch=snapshot[4],
                policy_epoch=snapshot[5],
                args=SearchArgs(query="synthetic replay", roots=["company"]),
            )

        first = request()
        first_digest = "a" * 64
        restarted_store = LocalConnectorStore(world.authority)
        await world.store.reserve(first, first_digest)
        with pytest.raises(ConnectorUnavailable):
            await restarted_store.reserve(first, first_digest)
        with pytest.raises(ConnectorUnavailable):
            await restarted_store.reserve(first, "b" * 64)

        await restarted_store.finish(first, first_digest)
        with pytest.raises(ConnectorUnavailable):
            await restarted_store.finish(first, first_digest)

        async with async_session_maker() as session:
            ledger = (
                await session.execute(
                    select(LocalConnectorRequest).where(
                        LocalConnectorRequest.tenant_id == world.tenant_id,
                        LocalConnectorRequest.device_id == device.device_id,
                        LocalConnectorRequest.request_id == first.request_id,
                    )
                )
            ).scalar_one()
            assert ledger.state == "completed"

        monkeypatch.setattr(store_module, "REPLAY_CAPACITY", 1)
        second = request()
        with pytest.raises(ConnectorUnavailable):
            await restarted_store.reserve(second, "c" * 64)

        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(LocalConnectorRequest)
                .where(
                    LocalConnectorRequest.tenant_id == world.tenant_id,
                    LocalConnectorRequest.device_id == device.device_id,
                    LocalConnectorRequest.request_id == first.request_id,
                )
                .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
            )

        await restarted_store.reserve(second, "c" * 64)
        third = request()
        with pytest.raises(ConnectorUnavailable):
            await restarted_store.reserve(third, "d" * 64)

        async with async_session_maker() as session:
            remaining_ids = set(
                (
                    await session.scalars(
                        select(LocalConnectorRequest.request_id).where(
                            LocalConnectorRequest.tenant_id == world.tenant_id,
                            LocalConnectorRequest.device_id == device.device_id,
                        )
                    )
                ).all()
            )
        assert remaining_ids == {second.request_id}
