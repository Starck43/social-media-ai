"""Pure status policy tests: no application imports, database or external I/O."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "p19_documents", Path(__file__).resolve().parents[2] / "app/services/knowledge/documents.py"
)
documents = importlib.util.module_from_spec(spec)
spec.loader.exec_module(documents)


@pytest.mark.parametrize(
    ("legacy", "expected"),
    [
        ("draft", ("draft", False)),
        ("draft_invalidated", ("draft", True)),
        ("in_review", ("in_review", False)),
        ("approved", ("approved", False)),
        ("rejected", ("rejected", False)),
        ("revision_requested", ("revision_requested", False)),
    ],
)
def test_status_mapping(legacy, expected):
    assert documents.canonical_revision_status(legacy) == expected


@pytest.mark.parametrize(
    ("current", "target"),
    [("draft", "in_review"), ("in_review", "approved"), ("in_review", "rejected"), ("in_review", "revision_requested")],
)
def test_valid_transitions(current, target):
    assert documents.validate_review_transition(current, target) is None


@pytest.mark.parametrize("current", ["draft", "in_review", "approved", "rejected", "revision_requested", "unknown"])
@pytest.mark.parametrize("target", ["draft", "in_review", "approved", "rejected", "revision_requested", "unknown"])
def test_invalid_transitions_fail_closed(current, target):
    valid = {
        ("draft", "in_review"),
        ("in_review", "approved"),
        ("in_review", "rejected"),
        ("in_review", "revision_requested"),
    }
    if (current, target) in valid:
        documents.validate_review_transition(current, target)
    else:
        with pytest.raises(ValueError):
            documents.validate_review_transition(current, target)


def test_unknown_status_is_not_silently_normalized():
    with pytest.raises(ValueError):
        documents.canonical_revision_status("unknown")
