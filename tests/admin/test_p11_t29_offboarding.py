"""Source-level policy and signed-review tests for workspace offboarding."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from itsdangerous import URLSafeTimedSerializer

__source_only__ = True

from app.services.tenancy import offboarding


@pytest.mark.parametrize(
    ("reason", "policy"),
    [
        ("owner_request", "preserve"),
        ("duplicate", "expire_elapsed"),
        ("contract_ended", "preserve"),
        ("test_completed", "expire_elapsed"),
    ],
)
def test_review_token_is_actor_and_policy_bound(monkeypatch, reason, policy):
    serializer = URLSafeTimedSerializer("test-review-secret", salt="p11-t29-source-test")
    payload = {
        "actor_id": 17,
        "workspace_id": 230,
        "reason_code": reason,
        "retention_policy": policy,
        "version": "starter:2026-10-31T00:00:00+00:00",
        "event_id": "a" * 32,
    }
    monkeypatch.setattr(offboarding, "_serializer", lambda: serializer)
    token = serializer.dumps(payload)

    assert offboarding._read_review(17, token) == payload
    with pytest.raises(offboarding.OffboardingError) as denied:
        offboarding._read_review(18, token)
    assert (denied.value.status_code, denied.value.code) == (400, "invalid_review")


@pytest.mark.parametrize("token", ["", "not-a-token", "x" * 4097])
def test_malformed_or_oversized_review_is_rejected(token):
    with pytest.raises(offboarding.OffboardingError) as error:
        offboarding._read_review(17, token)
    assert error.value.status_code == 400
    assert error.value.code == "invalid_review"


@pytest.mark.parametrize(
    ("payload_value", "expected_code"),
    [
        ({"actor_id": 17, "workspace_id": True}, "invalid_review"),
        ({"actor_id": 17, "workspace_id": 0}, "invalid_review"),
        ({"actor_id": 17, "workspace_id": 1, "reason_code": "free_text"}, "invalid_policy"),
        ({"actor_id": 17, "workspace_id": 1, "retention_policy": "delete_all"}, "invalid_policy"),
    ],
)
def test_review_payload_rejects_invalid_identity_or_unallowlisted_values(monkeypatch, payload_value, expected_code):
    payload = {
        "actor_id": 17,
        "workspace_id": 1,
        "reason_code": "owner_request",
        "retention_policy": "preserve",
        "version": "starter:v1",
        "event_id": "b" * 32,
        **payload_value,
    }
    serializer = URLSafeTimedSerializer("test-review-secret", salt="p11-t29-source-test")
    monkeypatch.setattr(offboarding, "_serializer", lambda: serializer)
    with pytest.raises(offboarding.OffboardingError) as error:
        offboarding._read_review(17, serializer.dumps(payload))
    assert error.value.status_code == 400
    assert error.value.code == expected_code


@pytest.mark.parametrize(
    "role,active,bypass,allowed",
    [
        ("superuser", True, True, True),
        ("admin", True, True, False),
        ("superuser", False, True, False),
        ("superuser", True, False, False),
    ],
)
def test_offboarding_requires_active_ambient_superuser(monkeypatch, role, active, bypass, allowed):
    actor = SimpleNamespace(id=17, is_active=active, is_superuser=role == "superuser")
    monkeypatch.setattr(offboarding, "get_current_user", lambda: actor)
    monkeypatch.setattr(offboarding, "is_bypass", lambda: bypass)
    if allowed:
        offboarding._ambient_actor(17)
    else:
        with pytest.raises(offboarding.OffboardingError) as error:
            offboarding._ambient_actor(17)
        assert error.value.status_code == 403
        assert error.value.code == "superuser_required"


def test_reason_and_retention_choices_are_closed_sets():
    for reason in offboarding.REASON_LABELS:
        for policy in offboarding.POLICY_LABELS:
            offboarding._choices(reason, policy)
    with pytest.raises(offboarding.OffboardingError) as error:
        offboarding._choices("anything else", "preserve")
    assert (error.value.status_code, error.value.code) == (400, "invalid_policy")
