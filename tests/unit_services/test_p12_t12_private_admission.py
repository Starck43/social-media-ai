"""Inactive preparation with synthetic authority/version/time/quota assertions."""

import asyncio
import copy
import json
from dataclasses import replace
from datetime import timedelta

import pytest

from app.integrations.file_workspace.private_admission import PrivateAdmissionUnavailable
from app.integrations.file_workspace.private_payload import private_payload_is_canonical
from app.integrations.file_workspace.retention_quota import RetentionQuotaUsage
from app.services.file_workspace import private_admission as service
from tests.shared.file_private_admission import (
    SyntheticExactVersionAuthority,
    SyntheticPrivateAdmissionAuthority,
    synthetic_private_admission,
)

__source_only__ = True


def run(data=None, *, authority=None, versions=None):
    source, batch, receipt, first, final, assertion = data or synthetic_private_admission()
    events = []
    authority = authority or SyntheticPrivateAdmissionAuthority(first, final, events)
    versions = versions or SyntheticExactVersionAuthority(assertion, events)
    payload = asyncio.run(
        service.prepare_private_payload(source, batch, receipt, authority=authority, versions=versions)
    )
    return payload, authority, versions


def reject(data, *, authority=None, versions=None):
    with pytest.raises(PrivateAdmissionUnavailable) as caught:
        run(data, authority=authority, versions=versions)
    assert caught.value.args == ("private_admission_unavailable",)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_composed_flow_order_binding_and_final_time():
    data = synthetic_private_admission()
    before = copy.deepcopy(data)
    payload, authority, versions = run(data)
    assert private_payload_is_canonical(payload)
    assert authority.events == ["authorize", "version", "authorize"]
    assert authority.requests == [data[0].context.request] * 2
    assert versions.inputs == [(data[0], data[2])]
    body = json.loads(payload)
    assert body["lifecycle"] == {
        "revision": "private-retention-v1",
        "captured_at": "2026-11-03T12:00:00.000000Z",
        "created_at": "2026-11-03T12:01:01.000000Z",
        "expires_at": "2026-11-04T12:00:00.000000Z",
    }
    assert body["binding"]["consent_revision"] == data[2].binding.consent_revision
    assert body["binding"]["read_attempt_id"] == data[0].context.request.nonce
    assert body["review_status"] == "unverified"
    assert data == before
    for forbidden in (b"disk:/", b"credential-key", b"processing-policy-key", b"second"):
        assert forbidden not in payload


@pytest.mark.parametrize("index", [3, 4])
@pytest.mark.parametrize(
    "field,value",
    [
        ("processing_allowed", False),
        ("retention_allowed", False),
        ("processing_allowed", 1),
        ("retention_allowed", 1),
    ],
)
def test_independent_consent_required_twice(index, field, value):
    data = list(synthetic_private_admission())
    data[index] = replace(data[index], **{field: value})
    events = []
    authority = SyntheticPrivateAdmissionAuthority(data[3], data[4], events)
    versions = SyntheticExactVersionAuthority(data[5], events)
    reject(data, authority=authority, versions=versions)
    assert events == (["authorize"] if index == 3 else ["authorize", "version", "authorize"])


@pytest.mark.parametrize("index", [3, 4])
@pytest.mark.parametrize(
    "field,value",
    [
        ("path_key", "9" * 64),
        ("personal_owner_id", 9),
        ("personal_owner_id", True),
        ("owner_epoch", "9" * 32),
        ("connection_epoch", "9" * 32),
        ("object_epoch", "9" * 32),
        ("consent_revision", "consent-v2"),
        ("policy_revision", "policy-v2"),
    ],
)
def test_capture_binding_must_still_be_current(index, field, value):
    data = list(synthetic_private_admission())
    state = data[index]
    data[index] = replace(state, binding=replace(state.binding, **{field: value}))
    reject(data)


@pytest.mark.parametrize("index", [3, 4])
@pytest.mark.parametrize(
    "field,value",
    [
        ("credential_key", "rotated"),
        ("root_key", "9" * 64),
        ("processing_policy_key", "withdrawn"),
        ("path", "disk:/other.txt"),
        ("processing_boundary", "local_only"),
        ("observation_number", True),
    ],
)
def test_current_context_change_rejects(index, field, value):
    data = list(synthetic_private_admission())
    state = data[index]
    context = replace(state.binding.context, **{field: value})
    data[index] = replace(state, binding=replace(state.binding, context=context))
    reject(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("raw_sha256", "9" * 64),
        ("text_sha256", "9" * 64),
        ("parser_version", "other"),
        ("provider_version", "other"),
        ("version_semantics", "bad semantics"),
        ("provider_object_id", False),
    ],
)
def test_malformed_or_wrong_original_receipt_rejects_before_version(field, value):
    data = list(synthetic_private_admission())
    data[2] = replace(data[2], **{field: value})
    events = []
    versions = SyntheticExactVersionAuthority(data[5], events)
    reject(data, versions=versions)
    assert not versions.inputs


@pytest.mark.parametrize(
    "field,value",
    [
        ("raw_sha256", "9" * 64),
        ("provider_version", "other"),
        ("version_semantics", "other-v1"),
        ("provider_object_id", "other-object"),
        ("provider_version", True),
    ],
)
def test_version_assertion_must_match_original_read(field, value):
    data = list(synthetic_private_admission())
    data[5] = replace(data[5], **{field: value})
    reject(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("nonce", "9" * 32),
        ("tenant_id", 9),
        ("actor_id", 9),
        ("connection_id", 9),
        ("object_id", 9),
        ("tenant_id", True),
    ],
)
def test_version_assertion_request_identity(field, value):
    data = list(synthetic_private_admission())
    data[5] = replace(data[5], request=replace(data[5].request, **{field: value}))
    reject(data)


@pytest.mark.parametrize("when", ["before", "after", "naive"])
def test_version_observation_must_be_fresh_and_utc(when):
    data = list(synthetic_private_admission())
    checked = {
        "before": data[3].now - timedelta(microseconds=1),
        "after": data[4].now + timedelta(microseconds=1),
        "naive": data[5].checked_at.replace(tzinfo=None),
    }[when]
    data[5] = replace(data[5], checked_at=checked)
    reject(data)


def test_missing_version_is_not_replaced_with_hash():
    data = list(synthetic_private_admission())
    data[0] = replace(data[0], provider_version=None)
    data[1] = replace(data[1], evidence=replace(data[1].evidence, provider_version=None))
    data[2] = replace(data[2], provider_version=None)
    reject(data)


@pytest.mark.parametrize("index", [3, 4])
def test_future_high_water_denies(index):
    data = list(synthetic_private_admission())
    data[index] = replace(data[index], last_observed_at=data[index].now + timedelta(microseconds=1))
    reject(data)


def test_clock_rollback_between_authorities_rejects_even_with_stale_mark():
    data = list(synthetic_private_admission())
    data[4] = replace(data[4], now=data[3].now - timedelta(microseconds=1), last_observed_at=data[2].captured_at)
    reject(data)


def test_capture_not_redated_after_version_work():
    data = list(synthetic_private_admission())
    data[4] = replace(data[4], now=data[2].captured_at + timedelta(minutes=5, microseconds=1))
    reject(data)


def test_exact_capture_age_limit_and_fixed_expiry():
    data = list(synthetic_private_admission())
    data[4] = replace(data[4], now=data[2].captured_at + timedelta(minutes=5))
    payload, _, _ = run(data)
    assert json.loads(payload)["lifecycle"]["expires_at"] == "2026-11-04T12:00:00.000000Z"


def test_final_quota_not_initial_snapshot_controls_preview():
    data = list(synthetic_private_admission())
    data[4] = replace(data[4], quota=replace(data[4].quota, object_records=3))
    reject(data)


def test_final_reserved_capacity_counts():
    data = list(synthetic_private_admission())
    usage = RetentionQuotaUsage(0, 0, 100, 100)
    data[4] = replace(data[4], quota=replace(data[4].quota, connection=usage, owner=usage, tenant=usage))
    reject(data)


def test_exact_canonical_byte_quota_not_text_characters():
    data = list(synthetic_private_admission())
    baseline, _, _ = run(data)
    # One byte less than the full canonical envelope fits no connection slot.
    limit = 4 * 1_048_576
    used = limit - len(baseline) + 1
    usage = RetentionQuotaUsage(99, used)
    final = data[4]
    data[4] = replace(final, quota=replace(final.quota, connection=usage, owner=usage, tenant=usage))
    reject(data)
    usage = replace(usage, bytes=used - 1)
    data[4] = replace(final, quota=replace(final.quota, connection=usage, owner=usage, tenant=usage))
    assert run(data)[0] == baseline


@pytest.mark.parametrize("phase", [1, 2])
def test_authority_error_is_sanitized_and_no_preview(phase, caplog):
    data = synthetic_private_admission()
    authority = SyntheticPrivateAdmissionAuthority(data[3], data[4], [])
    authority.error_at = phase
    reject(data, authority=authority)
    assert "private-account-secret" not in caplog.text


def test_unsupported_exact_version_capability_denies_without_fallback():
    data = synthetic_private_admission()
    versions = SyntheticExactVersionAuthority(data[5], [])
    versions.error = NotImplementedError("private-account-secret")
    reject(data, versions=versions)


def test_external_cancellation_propagates():
    data = synthetic_private_admission()
    versions = SyntheticExactVersionAuthority(data[5], [])
    versions.error = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        run(data, versions=versions)


def test_shared_deadline_after_await(monkeypatch):
    data = synthetic_private_admission()
    versions = SyntheticExactVersionAuthority(data[5], [])

    async def slow():
        await asyncio.sleep(0.05)

    versions.hook = slow
    monkeypatch.setattr(service, "ADMISSION_TIMEOUT", 0.001)
    reject(data, versions=versions)


def test_pending_cancellation_not_swallowed_by_boundary():
    data = synthetic_private_admission()
    versions = SyntheticExactVersionAuthority(data[5], [])

    async def swallow():
        asyncio.current_task().cancel()
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            pass

    versions.hook = swallow
    with pytest.raises(asyncio.CancelledError):
        run(data, versions=versions)


def test_missing_injected_boundaries_denies():
    data = synthetic_private_admission()
    with pytest.raises(PrivateAdmissionUnavailable):
        asyncio.run(service.prepare_private_payload(*data[:3]))


def test_no_capacity_reserved_by_successful_preparation():
    data = synthetic_private_admission()
    # Both previews can pass the same snapshot; this is explicitly not a writer.
    assert run(data)[0] == run(data)[0]
    assert data[4].quota.connection.records == 0


def test_repr_hides_context_receipt_and_assertion():
    data = synthetic_private_admission()
    for item in (data[2], data[2].binding, data[3], data[5]):
        assert "disk:/" not in repr(item)
        assert "synthetic-exact-version" not in repr(item)
        assert "credential" not in repr(item)


def test_authority_must_preserve_first_observation_high_water():
    data = list(synthetic_private_admission())
    data[4] = replace(data[4], last_observed_at=data[3].now - timedelta(microseconds=1))
    reject(data)


@pytest.mark.parametrize("index", [2, 3, 4, 5])
def test_foreign_boundary_objects_fail_closed(index):
    data = list(synthetic_private_admission())
    data[index] = object()
    reject(data)
