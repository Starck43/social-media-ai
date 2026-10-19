"""No-DB contract tests for the single task-selection payload writer.

Owner command: `.venv/bin/python -m pytest tests/test_task_selection_payload.py -q --noconftest -o addopts=`

The manager module needs the application package (relative imports, settings),
so this file runs with `--noconftest` to skip the DB bootstrap; nothing here
touches a database or the network.
"""

import pytest

from app.models.managers.agent_task_manager import AgentTaskManager as manager
from app.services.monitoring import selection


class TestWriter:
    def test_empty_selection_writes_nothing(self):
        assert manager.build_selection_payload() == {}

    def test_tracked_users_are_stored_in_platform_form(self):
        payload = manager.build_selection_payload(tracked_users=["VK:@Anna", {"platform": "tg", "external_id": "@b"}])
        assert payload["tracked_users"] == ["vk:anna", "telegram:b"]

    def test_clearing_tracked_users_removes_the_key(self):
        payload = manager.build_selection_payload(
            tracked_users=[], existing={"tracked_users": ["vk:1"], "brands": ["Acme"]}
        )
        assert "tracked_users" not in payload
        assert payload["brands"] == ["Acme"]

    def test_keywords_are_normalised_and_deduplicated_by_reader(self):
        payload = manager.build_selection_payload(keywords=["  Киров ", "киров", ""])
        assert payload["keywords_list"] == ["Киров", "киров"]
        scope = selection.build_scope(payload)
        assert scope.keywords == ("Киров",)

    def test_any_is_not_stored_all_is(self):
        assert "keywords_match" not in manager.build_selection_payload(keywords=["a"], keywords_match="any")
        assert manager.build_selection_payload(keywords=["a"], keywords_match="all")["keywords_match"] == "all"

    @pytest.mark.parametrize("bad", ["some", "ANY", 1])
    def test_invalid_match_mode_is_rejected(self, bad):
        with pytest.raises(selection.SelectionError):
            manager.build_selection_payload(keywords_match=bad)

    def test_malformed_tracked_user_is_rejected(self):
        with pytest.raises(selection.SelectionError):
            manager.build_selection_payload(tracked_users=["123456"])

    def test_monitored_users_is_not_a_selection_key(self):
        """The reader ignores it entirely: it is not silently reinterpreted."""
        scope = selection.build_scope({"monitored_users": ["a"]})
        assert scope.tracked_users == ()
        assert manager.build_selection_payload(existing={"period": "day"}) == {"period": "day"}

    def test_none_keeps_existing_values(self):
        existing = {"tracked_users": ["vk:1"], "keywords_list": ["x"], "brands": ["Acme"]}
        assert manager.build_selection_payload(existing=existing) == existing

    def test_unrelated_keys_survive(self):
        payload = manager.build_selection_payload(
            tracked_users=["vk:1"], existing={"cli_dates": {"start_date": "2026-10-01"}, "period": "week"}
        )
        assert payload["cli_dates"] == {"start_date": "2026-10-01"}
        assert payload["period"] == "week"
