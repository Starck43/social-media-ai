"""Pure proposed quota arithmetic, not a reservation or authority assertion.

Input is a trusted, coherent usage snapshot and an already serialized envelope.
No DB/I/O, DTO serialization, eviction, retry settlement or storage activation.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_RECORD_BYTES = 65_536
OBJECT_RECORD_LIMIT = 3
CONNECTION_RECORD_LIMIT = 100
CONNECTION_BYTE_LIMIT = 4 * 1_048_576
OWNER_RECORD_LIMIT = 200
OWNER_BYTE_LIMIT = 8 * 1_048_576
TENANT_RECORD_LIMIT = 1_000
TENANT_BYTE_LIMIT = 32 * 1_048_576


@dataclass(frozen=True, slots=True, repr=False)
class RetentionQuotaUsage:
    """Include expired/invalidated payload rows until physically purged."""

    records: int
    bytes: int
    reserved_records: int = 0
    reserved_bytes: int = 0


@dataclass(frozen=True, slots=True, repr=False)
class RetentionQuotaSnapshot:
    """One object -> connection -> personal owner within tenant -> tenant."""

    object_records: int
    object_reserved_records: int
    connection: RetentionQuotaUsage
    owner: RetentionQuotaUsage
    tenant: RetentionQuotaUsage


def _fits(usage: RetentionQuotaUsage, records: int, byte_limit: int, size: int) -> bool:
    if type(usage) is not RetentionQuotaUsage:
        return False
    for count, charged in ((usage.records, usage.bytes), (usage.reserved_records, usage.reserved_bytes)):
        if (
            type(count) is not int
            or type(charged) is not int
            or not 0 <= count <= records
            or not 0 <= charged <= byte_limit
            or not count <= charged <= count * MAX_RECORD_BYTES
        ):
            return False
    return (
        usage.records + usage.reserved_records + 1 <= records
        and usage.bytes + usage.reserved_bytes + size <= byte_limit
    )


def retention_quota_allows(payload: bytes, snapshot: RetentionQuotaSnapshot) -> bool:
    """Would ONE new, not-yet-reserved record fit every proposed ceiling?

    Exact nonempty bytes, strict UTF-8 and <=64KiB. Count logical serialized bytes,
    not characters/tokens; the caller must supply the complete canonical DTO,
    not a selected field/compressed payload. This is NOT schema/content validation.
    Existing reservations consume capacity too; no mutation, release or eviction.
    True is arithmetic only: trusted scope IDs/current usage/authorization and
    atomic multidimensional reservation remain the caller's separate contract.
    A stale coherent snapshot cannot be detected; do not use for retry settlement.
    """
    try:
        if type(payload) is not bytes or not 1 <= len(payload) <= MAX_RECORD_BYTES:
            return False
        payload.decode("utf-8", errors="strict")
        if type(snapshot) is not RetentionQuotaSnapshot:
            return False
        if any(
            type(count) is not int or not 0 <= count <= OBJECT_RECORD_LIMIT
            for count in (snapshot.object_records, snapshot.object_reserved_records)
        ):
            return False
        if snapshot.object_records + snapshot.object_reserved_records + 1 > OBJECT_RECORD_LIMIT:
            return False
        scopes = (
            (snapshot.connection, CONNECTION_RECORD_LIMIT, CONNECTION_BYTE_LIMIT),
            (snapshot.owner, OWNER_RECORD_LIMIT, OWNER_BYTE_LIMIT),
            (snapshot.tenant, TENANT_RECORD_LIMIT, TENANT_BYTE_LIMIT),
        )
        if not all(_fits(usage, records, byte_limit, len(payload)) for usage, records, byte_limit in scopes):
            return False
        if (
            snapshot.object_records > snapshot.connection.records
            or snapshot.object_reserved_records > snapshot.connection.reserved_records
        ):
            return False
        for child, parent in ((snapshot.connection, snapshot.owner), (snapshot.owner, snapshot.tenant)):
            if any(
                getattr(child, field) > getattr(parent, field)
                for field in ("records", "bytes", "reserved_records", "reserved_bytes")
            ):
                return False
        return True
    except Exception:
        return False
