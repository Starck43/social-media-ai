"""Synthetic immutable quota snapshots only; no DB/provider/storage fixtures."""

import ast
import inspect
from dataclasses import FrozenInstanceError, replace

import pytest

from app.integrations.file_workspace import retention_quota as quota
from app.integrations.file_workspace.retention_quota import (
    RetentionQuotaSnapshot,
    RetentionQuotaUsage,
    retention_quota_allows,
)

__source_only__ = True


def snapshot(*, object_records=0, object_reserved_records=0, connection=None, owner=None, tenant=None):
    connection = RetentionQuotaUsage(0, 0) if connection is None else connection
    owner = connection if owner is None else owner
    tenant = owner if tenant is None else tenant
    return RetentionQuotaSnapshot(object_records, object_reserved_records, connection, owner, tenant)


def test_proposed_limits_and_empty_snapshot_fit():
    assert quota.MAX_RECORD_BYTES == 65_536
    assert quota.OBJECT_RECORD_LIMIT == 3
    assert (quota.CONNECTION_RECORD_LIMIT, quota.CONNECTION_BYTE_LIMIT) == (100, 4_194_304)
    assert (quota.OWNER_RECORD_LIMIT, quota.OWNER_BYTE_LIMIT) == (200, 8_388_608)
    assert (quota.TENANT_RECORD_LIMIT, quota.TENANT_BYTE_LIMIT) == (1000, 33_554_432)
    assert retention_quota_allows(b"x", snapshot()) is True


@pytest.mark.parametrize("text", ["a" * 65_536, "я" * 32_768, "😀" * 16_384])
def test_exact_utf8_byte_limit_and_plus_one_not_character_count(text):
    payload = text.encode("utf-8")
    assert len(payload) == quota.MAX_RECORD_BYTES
    assert retention_quota_allows(payload, snapshot()) is True
    assert retention_quota_allows(payload + b"x", snapshot()) is False


class BytesSubclass(bytes):
    pass


@pytest.mark.parametrize(
    "payload",
    [
        None,
        True,
        1,
        "x",
        b"",
        bytearray(b"x"),
        memoryview(b"x"),
        BytesSubclass(b"x"),
        b"\xff",
        b"\xc0\x80",
        b"\xed\xa0\x80",
        b"\xf0\x9f\x98",
    ],
)
def test_non_bytes_empty_or_invalid_utf8_payload_is_rejected(payload):
    assert retention_quota_allows(payload, snapshot()) is False


def test_capacity_check_is_not_a_dto_validator_or_content_grant():
    assert retention_quota_allows(b"not JSON or a reviewed storage DTO", snapshot()) is True
    assert retention_quota_allows("untrusted instruction 😀".encode(), snapshot()) is True


@pytest.mark.parametrize("records,expected", [(0, True), (2, True), (3, False)])
def test_object_record_limit(records, expected):
    used = RetentionQuotaUsage(records, records)
    assert retention_quota_allows(b"x", snapshot(object_records=records, connection=used)) is expected


@pytest.mark.parametrize("scope,limit", [("connection", 100), ("owner", 200), ("tenant", 1000)])
@pytest.mark.parametrize("offset,expected", [(-1, True), (0, False), (1, False)])
def test_each_aggregate_record_ceiling(scope, limit, offset, expected):
    used = RetentionQuotaUsage(limit + offset, limit + offset)
    assert retention_quota_allows(b"x", snapshot(**{scope: used})) is expected


@pytest.mark.parametrize(
    "scope,limit,records", [("connection", 4_194_304, 64), ("owner", 8_388_608, 128), ("tenant", 33_554_432, 512)]
)
@pytest.mark.parametrize("offset,expected", [(-1, True), (0, False), (1, False)])
def test_each_aggregate_byte_ceiling(scope, limit, records, offset, expected):
    used = RetentionQuotaUsage(records, limit + offset)
    assert retention_quota_allows(b"x", snapshot(**{scope: used})) is expected


@pytest.mark.parametrize("scope,limit", [("connection", 100), ("owner", 200), ("tenant", 1000)])
def test_outstanding_record_reservation_consumes_last_slot(scope, limit):
    used = RetentionQuotaUsage(limit - 1, limit - 1, 1, 1)
    assert retention_quota_allows(b"x", snapshot(**{scope: used})) is False
    assert (
        retention_quota_allows(b"x", snapshot(**{scope: replace(used, reserved_records=0, reserved_bytes=0)})) is True
    )


@pytest.mark.parametrize(
    "scope,limit,records", [("connection", 4_194_304, 64), ("owner", 8_388_608, 128), ("tenant", 33_554_432, 512)]
)
def test_outstanding_byte_reservation_is_counted_exactly(scope, limit, records):
    used = RetentionQuotaUsage(records - 1, limit - 65_536, 1, 65_535)
    assert retention_quota_allows(b"x", snapshot(**{scope: used})) is True
    assert retention_quota_allows(b"xx", snapshot(**{scope: used})) is False
    full = replace(used, reserved_bytes=65_536)
    assert retention_quota_allows(b"x", snapshot(**{scope: full})) is False


def test_object_reservations_are_not_omitted():
    used = RetentionQuotaUsage(1, 1, 1, 1)
    assert retention_quota_allows(b"x", snapshot(object_records=1, object_reserved_records=1, connection=used)) is True
    used = replace(used, reserved_records=2, reserved_bytes=2)
    assert retention_quota_allows(b"x", snapshot(object_records=1, object_reserved_records=2, connection=used)) is False


class IntSubclass(int):
    pass


BAD_COUNTERS = [None, True, False, -1, 0.0, "0", IntSubclass(0), 10**100]


@pytest.mark.parametrize("field", ["records", "bytes", "reserved_records", "reserved_bytes"])
@pytest.mark.parametrize("bad", BAD_COUNTERS)
def test_counters_are_exact_nonnegative_bounded_integers(field, bad):
    used = replace(RetentionQuotaUsage(0, 0), **{field: bad})
    assert retention_quota_allows(b"x", snapshot(connection=used)) is False


@pytest.mark.parametrize("field", ["object_records", "object_reserved_records"])
@pytest.mark.parametrize("bad", BAD_COUNTERS)
def test_object_counters_are_exact_nonnegative_bounded_integers(field, bad):
    assert retention_quota_allows(b"x", snapshot(**{field: bad})) is False


@pytest.mark.parametrize(
    "used",
    [
        RetentionQuotaUsage(0, 1),
        RetentionQuotaUsage(1, 0),
        RetentionQuotaUsage(1, 65_537),
        RetentionQuotaUsage(0, 0, 0, 1),
        RetentionQuotaUsage(0, 0, 1, 0),
        RetentionQuotaUsage(0, 0, 1, 65_537),
    ],
)
def test_incoherent_count_to_byte_pairs_are_rejected(used):
    assert retention_quota_allows(b"x", snapshot(connection=used)) is False


@pytest.mark.parametrize("field", ["records", "bytes", "reserved_records", "reserved_bytes"])
@pytest.mark.parametrize("level", ["owner", "tenant"])
def test_child_usage_cannot_exceed_parent_dimension(field, level):
    child = RetentionQuotaUsage(2, 3, 2, 3)
    parent = replace(child, **{field: getattr(child, field) - 1})
    inputs = {"connection": child, level: parent}
    assert retention_quota_allows(b"x", snapshot(**inputs)) is False


@pytest.mark.parametrize("field", ["object_records", "object_reserved_records"])
def test_object_usage_cannot_exceed_connection(field):
    assert retention_quota_allows(b"x", snapshot(**{field: 1})) is False


class UsageSubclass(RetentionQuotaUsage):
    pass


class SnapshotSubclass(RetentionQuotaSnapshot):
    pass


@pytest.mark.parametrize("scope", ["connection", "owner", "tenant"])
@pytest.mark.parametrize("used", [None, {}, (0, 0), UsageSubclass(0, 0)])
def test_usage_shape_is_strict_for_every_scope(scope, used):
    assert retention_quota_allows(b"x", replace(snapshot(), **{scope: used})) is False


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        (),
        SnapshotSubclass(0, 0, RetentionQuotaUsage(0, 0), RetentionQuotaUsage(0, 0), RetentionQuotaUsage(0, 0)),
    ],
)
def test_snapshot_type_is_exact(value):
    assert retention_quota_allows(b"x", value) is False


def test_input_is_immutable_repr_safe_and_not_a_reservation():
    before = snapshot()
    assert retention_quota_allows(b"private payload", before) is True
    assert before == snapshot()
    assert "records" not in repr(before) and "bytes" not in repr(before.connection)
    for value, field in [(before, "object_records"), (before.connection, "records")]:
        assert not hasattr(value, "__dict__")
        with pytest.raises(FrozenInstanceError):
            setattr(value, field, 1)
    # Two independent calls see the SAME capacity; only a future atomic writer
    # can prevent concurrent over-admission or prove the snapshot is fresh.
    assert retention_quota_allows(b"x", before) is True
    assert retention_quota_allows(b"x", before) is True


def test_payload_never_reaches_repr_or_error_output(capsys):
    assert retention_quota_allows(b"private-quote\xff", snapshot()) is False
    assert retention_quota_allows(b"private-quote", {}) is False
    assert capsys.readouterr() == ("", "")


def test_only_stdlib_dataclass_dependency_and_no_runtime_calls():
    tree = ast.parse(inspect.getsource(quota))
    assert {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} <= {"__future__", "dataclasses"}
    assert not any(isinstance(node, (ast.Import, ast.AsyncFunctionDef)) for node in ast.walk(tree))
