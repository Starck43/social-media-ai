"""Pure output-policy tests; no storage, provider or application bootstrap."""

from types import SimpleNamespace

import pytest

from app.services.ai.generation_policy import requested_output_limit

__source_only__ = True


def test_output_precedence_keeps_default_and_context_distinct():
    model = SimpleNamespace(max_tokens=4096, max_output_tokens=2048, context_window_tokens=128000)
    assert requested_output_limit(model) == 2048
    assert requested_output_limit(model, requested=1024, purpose_limit=512, approved_plan_limit=768) == 512
    assert model.max_tokens == 4096


def test_unknown_ceiling_is_not_inferred_from_context_or_default():
    model = SimpleNamespace(max_tokens=4096, max_output_tokens=None, context_window_tokens=16)
    assert requested_output_limit(model, requested=8192) == 8192


@pytest.mark.parametrize("key", ["purpose_limit", "approved_plan_limit"])
def test_zero_approved_bound_denies(key):
    with pytest.raises(PermissionError):
        requested_output_limit(SimpleNamespace(max_tokens=4096), **{key: 0})


@pytest.mark.parametrize("value", [True, False, -1, "1024", 1.5])
@pytest.mark.parametrize("key", ["requested", "purpose_limit", "approved_plan_limit"])
def test_malformed_output_policy_fails_closed(key, value):
    with pytest.raises(ValueError):
        requested_output_limit(SimpleNamespace(max_tokens=4096), **{key: value})


def test_zero_request_is_not_a_missing_value():
    with pytest.raises(ValueError):
        requested_output_limit(SimpleNamespace(max_tokens=4096), requested=0)
