"""Opt-in PostgreSQL persistence for an ALREADY frozen digest checkpoint.

Dedicated session advisory lock survives per-part commits. All publishers must
cooperate with this lock before this path is activated; the legacy builder does
not yet do so. No snapshot creation, HTTP, job binding or implicit legacy replay.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from hashlib import sha256
from sys import exc_info
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import settings
from app.core.database import async_engine
from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass
from app.models import DigestRun, Tenant, TenantChannel
from app.services.digest.checkpoints import (
    CheckpointError,
    load_checkpoint,
    reconstruct_html_parts,
    transition,
)


class CheckpointBusy(RuntimeError):
    """Another cooperating publisher owns this run; do not send."""


class CheckpointConflict(RuntimeError):
    """Lost ownership/connection or changed history; do not send/replay."""


class DeliveryAuthorizationError(TenantContextError):
    """The current workspace/binding no longer authorizes a new send."""


def _scope() -> int:
    tenant = current_tenant_id()
    if is_bypass() or type(tenant) is not int or tenant <= 0:
        raise TenantContextError("Checkpoint delivery requires an explicit non-bypass workspace")
    return tenant


def _lock_key(run_id: int) -> int:
    raw = f"digest-checkpoint:{settings.DB_SCHEMA}:{run_id}".encode()
    return int.from_bytes(sha256(raw).digest()[:8], byteorder="big", signed=True)


def _destination(value: str) -> str:
    normalized = value.strip()
    return normalized.lower() if normalized.startswith("@") else normalized


class LockedCheckpoint:
    """Use only inside locked_checkpoint, in one task; never share the connection."""

    def __init__(self, connection: AsyncConnection, run_id: int, tenant_id: int, generation: str, backend_pid: int):
        self._connection = connection
        self._run_id = run_id
        self._tenant_id = tenant_id
        self._generation = generation
        self._backend_pid = backend_pid
        self._closed = False

    async def _read(self) -> tuple[dict[str, Any], list[str], str]:
        if _scope() != self._tenant_id:
            raise DeliveryAuthorizationError("Checkpoint workspace changed")
        if self._closed or self._connection.closed or self._connection.invalidated:
            raise CheckpointConflict("Checkpoint lock connection is unavailable")
        pid = await self._connection.scalar(text("SELECT pg_catalog.pg_backend_pid()"))
        if pid != self._backend_pid:
            raise CheckpointConflict("Checkpoint lock connection changed")
        row = (
            (
                await self._connection.execute(
                    select(DigestRun.content, DigestRun.delivery_state).where(
                        DigestRun.id == self._run_id, DigestRun.tenant_id == self._tenant_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise DeliveryAuthorizationError("Checkpoint run unavailable in this workspace")
        state = load_checkpoint(
            row["delivery_state"],
            run_id=self._run_id,
            tenant_id=self._tenant_id,
            generation=self._generation,
            content=row["content"],
            splitter=row["delivery_state"].get("splitter") if isinstance(row["delivery_state"], dict) else None,
        )
        parts = reconstruct_html_parts(state, row["content"])
        return state, parts, row["content"]

    async def load(self) -> tuple[dict[str, Any], list[str]]:
        """Reload committed receipts and verify all frozen payloads, without send."""
        state, parts, _ = await self._read()
        await self._connection.commit()
        return state, parts

    async def _save(self, before: dict[str, Any], after: dict[str, Any], content: str) -> None:
        # CAS also rejects writes from legacy/noncooperating code between read
        # and update. It cannot prevent that code from doing its own HTTP send.
        try:
            changed = await self._connection.execute(
                update(DigestRun)
                .where(
                    DigestRun.id == self._run_id,
                    DigestRun.tenant_id == self._tenant_id,
                    DigestRun.delivery_state == before,
                    DigestRun.content == content,
                )
                .values(delivery_state=after)
            )
            if changed.rowcount != 1:
                raise CheckpointConflict("Checkpoint history changed before persistence")
            await self._connection.commit()
        except BaseException:
            # Do not let a later load accidentally commit an unacknowledged
            # write after a failed/cancelled commit. This context is poisoned.
            self._closed = True
            try:
                await self._connection.rollback()
            except BaseException:
                await self._connection.invalidate()
            raise

    async def begin_part(self, target_index: int, part_index: int) -> dict[str, str]:
        """Authorize exact frozen binding and COMMIT intent before returning text.

        Caller must hold this context throughout HTTP and outcome persistence.
        A crash/cancellation after this commit leaves in-flight, not known-unsent.
        """
        state, parts, content = await self._read()
        after = transition(state, target_index, part_index, "in_flight")
        target = state["targets"][target_index]
        binding = (
            (
                await self._connection.execute(
                    select(TenantChannel.channel, TenantChannel.chat_id)
                    .join(Tenant, Tenant.id == TenantChannel.tenant_id)
                    .where(
                        Tenant.id == self._tenant_id,
                        Tenant.is_active.is_(True),
                        TenantChannel.id == target["binding_id"],
                        TenantChannel.tenant_id == self._tenant_id,
                        TenantChannel.is_active.is_(True),
                        TenantChannel.is_digest_target.is_(True),
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            binding is None
            or binding["channel"] != target["channel"]
            or _destination(binding["chat_id"]) != target["destination_id"]
        ):
            await self._connection.rollback()
            raise DeliveryAuthorizationError("Frozen digest destination is unavailable or changed")
        await self._save(state, after, content)
        return {"channel": target["channel"], "destination_id": target["destination_id"], "text": parts[part_index]}

    async def record_outcome(
        self, target_index: int, part_index: int, outcome: str, *, message_id: str | None = None
    ) -> None:
        """Commit an outcome immediately, even if binding was revoked after HTTP.

        This authorizes no further HTTP. A failed commit is not safe to replay;
        caller must surface uncertainty. 'blocked' requires proven pre-HTTP refusal.
        """
        if outcome not in ("sent", "rejected", "uncertain", "blocked"):
            raise CheckpointError("Invalid transport outcome")
        state, _, content = await self._read()
        after = transition(state, target_index, part_index, outcome, message_id=message_id)
        await self._save(state, after, content)


@asynccontextmanager
async def locked_checkpoint(run_id: int, *, generation: str):
    """Nonblocking per-run lock on a dedicated PostgreSQL connection.

    Reject foreign/unscoped/bypass IDs before locking. Never seed NULL history.
    Cleanup rolls back and unlocks before pool reuse; cleanup failure invalidates
    the connection so a session lock cannot be returned silently to the pool.
    """
    tenant_id = _scope()
    if (
        type(run_id) is not int
        or not 0 < run_id <= 2**31 - 1
        or not isinstance(generation, str)
        or not generation.strip()
    ):
        raise CheckpointError("Invalid checkpoint identity")
    async with async_engine.connect() as connection:
        acquired = False
        lock_attempted = False
        lock_resolved = False
        store = None
        try:
            owned = await connection.scalar(
                select(DigestRun.id).where(DigestRun.id == run_id, DigestRun.tenant_id == tenant_id)
            )
            await connection.commit()
            if owned is None:
                raise DeliveryAuthorizationError("Checkpoint run unavailable in this workspace")
            lock_attempted = True
            lock, backend_pid = (
                await connection.execute(
                    text("SELECT pg_catalog.pg_try_advisory_lock(:key), pg_catalog.pg_backend_pid()"),
                    {"key": _lock_key(run_id)},
                )
            ).one()
            acquired = bool(lock)
            lock_resolved = True
            await connection.commit()
            if not acquired:
                raise CheckpointBusy("Digest checkpoint is already locked")
            store = LockedCheckpoint(connection, run_id, tenant_id, generation, backend_pid)
            await store.load()
            yield store
        finally:
            has_primary_exception = exc_info()[0] is not None
            if store is not None:
                store._closed = True
            if lock_attempted and not lock_resolved:
                # Cancellation/disconnect may occur AFTER PostgreSQL accepted
                # the lock but BEFORE we saw its result. Never pool that session.
                await connection.invalidate()
            elif acquired:
                try:
                    await connection.rollback()
                    if connection.invalidated:
                        raise CheckpointConflict("Checkpoint connection was lost")
                    unlocked = await connection.scalar(
                        text("SELECT pg_catalog.pg_advisory_unlock(:key)"), {"key": _lock_key(run_id)}
                    )
                    await connection.commit()
                    if unlocked is not True:
                        raise CheckpointConflict("Checkpoint lock was lost")
                except BaseException:
                    await connection.invalidate()
                    if not has_primary_exception:
                        raise
