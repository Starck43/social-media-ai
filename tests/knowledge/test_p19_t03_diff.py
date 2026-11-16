"""DB-exempt diff tests; persistence and authorization require PostgreSQL."""

import pytest

from app.services.knowledge.versions import RestoreRequest, diff_text


def test_diff_reconstructs_both_versions_and_preserves_whitespace():
    before, after = "one two\nthree\n", "one TWO\nthree\nfour"
    for granularity in ("line", "word"):
        parts = diff_text(before, after, granularity)
        assert "".join(part.before for part in parts) == before
        assert "".join(part.after for part in parts) == after
        assert any(part.operation != "equal" for part in parts)


def test_diff_empty_and_identical():
    assert diff_text("", "", "line") == []
    assert diff_text("same", "same", "word")[0].operation == "equal"


def test_diff_rejects_invalid_and_large_inputs():
    for before, after, mode in [("x", "y", "bad"), ("x" * 200001, "", "line"), ("x\n" * 4001, "", "line")]:
        with pytest.raises(ValueError):
            diff_text(before, after, mode)


def test_restore_request_rejects_nul_description():
    with pytest.raises(ValueError):
        RestoreRequest(expected_revision_id=1, expected_updated_at="2026-11-08T10:00:00Z", change_description="x\x00")
