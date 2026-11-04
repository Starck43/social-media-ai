"""LOCAL ONLY: durable, owner-scoped pairing limits and expiry cleanup."""

import asyncio
from datetime import timedelta
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app.core.database import async_session_maker
from app.core.tenant_context import tenant_scope
from app.models.integrations.local_connector import LocalConnector, LocalConnectorPairing, LocalConnectorRequest
from app.services.local_connector.store import (
    PAIRING_CREATION_LIMIT,
    PAIRING_HISTORY_RETENTION,
    LocalConnectorStore,
    now,
)
from app.services.local_connector.wire import ConnectorUnavailable
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401

pytestmark = pytest.mark.tenancy


@pytest.fixture
async def connector_world(personal_world):
    tenants = [tenant.id for tenant in personal_world.tenants]
    authority = SimpleNamespace(
        verifier=lambda pairing_id, secret: sha256(f"{pairing_id}:{secret}".encode()).hexdigest()
    )
    world = SimpleNamespace(
        store=LocalConnectorStore(authority),
        tenant_id=tenants[0],
        owner_id=personal_world.users[0].id,
        other_owner_id=personal_world.users[1].id,
    )
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


def add_device(world):
    return LocalConnector(
        tenant_id=world.tenant_id,
        owner_user_id=world.owner_id,
        device_name_encrypted="synthetic connector",
        fingerprint=uuid4().hex[:32] * 2,
        public_key="synthetic public key",
        platform="linux",
        version="test",
        certificate_pem="synthetic certificate",
        cert_serial=uuid4().hex,
        cloud_roots=[],
    )


def pairing_row(world, *, device_id, created_at, expires_at, suffix):
    return LocalConnectorPairing(
        tenant_id=world.tenant_id,
        owner_user_id=world.owner_id,
        pairing_id=uuid4().hex,
        code_hash="synthetic-code-hash",
        created_at=created_at,
        expires_at=expires_at,
        attempts=0,
        fingerprint=None,
        public_key=None,
        device_name_encrypted=f"synthetic pending {suffix}",
        platform="linux",
        version="test",
        device_id=device_id,
    )


async def begin(world, owner_id):
    with tenant_scope(world.tenant_id):
        return await world.store.begin(
            owner_id=owner_id,
            device_name="synthetic new connector",
            platform="linux",
            version="test",
        )


@pytest.mark.asyncio
async def test_pairing_creation_limit_is_durable_serialized_and_owner_scoped(connector_world):
    world = connector_world
    moment = now()
    async with async_session_maker() as session, session.begin():
        device = add_device(world)
        session.add(device)
        await session.flush()
        # Expired codes still count for the rolling hour. A completed pairing
        # is not an active pending code and therefore does not hit the 3-code cap.
        session.add_all(
            pairing_row(
                world,
                device_id=device.id,
                created_at=moment - timedelta(minutes=10),
                expires_at=moment - timedelta(minutes=5),
                suffix=str(index),
            )
            for index in range(PAIRING_CREATION_LIMIT - 1)
        )

    async def attempt():
        try:
            return await begin(world, world.owner_id)
        except ConnectorUnavailable:
            return None

    outcomes = await asyncio.gather(*(attempt() for _ in range(5)))
    assert sum(result is not None for result in outcomes) == 1

    owner_count = await count_pairings(world, world.owner_id)
    assert owner_count == PAIRING_CREATION_LIMIT

    # A second authorized owner in the same tenant has an independent quota.
    created = await begin(world, world.other_owner_id)
    assert created["pairing_id"]
    assert await count_pairings(world, world.other_owner_id) == 1


async def count_pairings(world, owner_id):
    async with async_session_maker() as session:
        return await session.scalar(
            select(func.count())
            .select_from(LocalConnectorPairing)
            .where(
                LocalConnectorPairing.tenant_id == world.tenant_id,
                LocalConnectorPairing.owner_user_id == owner_id,
            )
        )


@pytest.mark.asyncio
async def test_begin_prunes_only_old_pairings_and_preserves_devices_and_live_codes(connector_world):
    world = connector_world
    moment = now()
    retention = PAIRING_HISTORY_RETENTION
    async with async_session_maker() as session, session.begin():
        device = add_device(world)
        session.add(device)
        await session.flush()
        old = pairing_row(
            world,
            device_id=device.id,
            created_at=moment - retention - timedelta(hours=1),
            expires_at=moment - retention - timedelta(minutes=1),
            suffix="old-completed",
        )
        recent_expired = pairing_row(
            world,
            device_id=None,
            created_at=moment - timedelta(minutes=10),
            expires_at=moment - timedelta(minutes=5),
            suffix="recent-expired",
        )
        live = pairing_row(
            world,
            device_id=None,
            created_at=moment - timedelta(seconds=2),
            expires_at=moment + timedelta(minutes=5),
            suffix="live-pending",
        )
        session.add_all((old, recent_expired, live))
        await session.flush()
        old_id, recent_id, live_id, device_id = old.id, recent_expired.id, live.id, device.id

    created = await begin(world, world.owner_id)

    async with async_session_maker() as session:

        async def pairing_exists(pairing_id):
            return await session.scalar(
                select(func.count())
                .select_from(LocalConnectorPairing)
                .where(
                    LocalConnectorPairing.tenant_id == world.tenant_id,
                    LocalConnectorPairing.id == pairing_id,
                )
            )

        assert await pairing_exists(old_id) == 0
        assert await pairing_exists(recent_id) == 1
        assert await pairing_exists(live_id) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LocalConnector)
                .where(
                    LocalConnector.tenant_id == world.tenant_id,
                    LocalConnector.id == device_id,
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LocalConnectorPairing)
                .where(
                    LocalConnectorPairing.tenant_id == world.tenant_id,
                    LocalConnectorPairing.owner_user_id == world.owner_id,
                    LocalConnectorPairing.pairing_id == created["pairing_id"],
                )
            )
            == 1
        )
