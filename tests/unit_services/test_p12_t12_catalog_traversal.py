"""Pure checkpoint transitions; no DB, provider, scheduler or application state."""

from copy import deepcopy

import pytest

from app.integrations.file_workspace import catalog_traversal as traversal

__source_only__ = True
ROOT = "disk:/work"


def test_queue_is_persistent_deduplicated_and_does_not_mutate_loaded_json():
    initial = traversal.initial_traversal(ROOT)
    before = deepcopy(initial)
    updated, offset, state = traversal.advance_traversal(ROOT, initial, [ROOT + "/a"], 2, False)
    assert initial == before
    assert (offset, state) == (2, "partial")
    assert updated == {"pending": [ROOT, ROOT + "/a"], "seen": [ROOT, ROOT + "/a"], "pages": 1}
    updated, offset, state = traversal.advance_traversal(ROOT, updated, [ROOT + "/a", ROOT + "/b"], None, False)
    assert (offset, state) == (0, "partial")
    assert traversal.active_folder(ROOT, updated) == ROOT + "/a"
    assert updated["pending"] == [ROOT + "/a", ROOT + "/b"]
    updated, _, _ = traversal.advance_traversal(ROOT, updated, [], None, False)
    updated, offset, state = traversal.advance_traversal(ROOT, updated, [], None, False)
    assert (updated["pending"], offset, state) == ([], 0, "exhausted")


@pytest.mark.parametrize(
    "bound,value,folders",
    [("MAX_SCAN_FOLDERS", 1, [ROOT + "/a"]), ("MAX_SCAN_DEPTH", 0, [ROOT + "/a"]), ("MAX_SCAN_PAGES", 1, [])],
)
def test_bounds_stop_without_claiming_completion(monkeypatch, bound, value, folders):
    monkeypatch.setattr(traversal, bound, value)
    _, _, state = traversal.advance_traversal(ROOT, traversal.initial_traversal(ROOT), folders, 2, False)
    assert state == "limited"


def test_last_allowed_page_can_exhaust_but_provider_truncation_never_does(monkeypatch):
    monkeypatch.setattr(traversal, "MAX_SCAN_PAGES", 1)
    for truncated, expected in [(False, "exhausted"), (True, "limited")]:
        _, _, state = traversal.advance_traversal(ROOT, traversal.initial_traversal(ROOT), [], None, truncated)
        assert state == expected


@pytest.mark.parametrize(
    "value",
    [
        [],
        {},
        {"pending": [], "seen": [ROOT], "pages": False},
        {"pending": [ROOT], "seen": [ROOT, ROOT], "pages": 0},
        {"pending": ["disk:/other"], "seen": [ROOT], "pages": 0},
        {"pending": [ROOT], "seen": [ROOT, ROOT + "/../secret"], "pages": 0},
        {"pending": [ROOT], "seen": [ROOT], "pages": -1},
        {"pending": [ROOT], "seen": [ROOT], "pages": 0, "unknown": 1},
    ],
)
def test_corrupt_checkpoints_fail_closed(value):
    with pytest.raises(ValueError):
        traversal.validate_traversal(ROOT, value)


def test_empty_pending_cannot_be_resumed_and_non_child_cannot_be_queued():
    with pytest.raises(ValueError):
        traversal.active_folder(ROOT, {"pending": [], "seen": [ROOT], "pages": 1})
    with pytest.raises(ValueError):
        traversal.advance_traversal(ROOT, traversal.initial_traversal(ROOT), [ROOT + "/a/b"], None, False)
