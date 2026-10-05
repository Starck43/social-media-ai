"""Param registry: methodology vs specifics (see app/services/ai/param_registry.py).

Pure functions, no DB — these pin the boundary the whole feature rests on:
a scenario's `scope` holds methodology, a task's `payload` holds the specific
targets. The helpers tell the CLI, the agent tools and the admin UI which
targets a scenario expects and whether a task forgot to provide them.
"""

from app.services.ai.param_registry import (
    ALL_PAYLOAD_PARAMS,
    missing_target_params,
    target_params_in_scope,
)


def test_missing_target_params_asks_for_the_scenario_s_targets():
    missing = missing_target_params(["brand_mentions", "competitor"], {})
    assert any("бренды" in m for m in missing)
    assert any("конкурентов" in m for m in missing)


def test_missing_target_params_is_empty_when_payload_carries_them():
    payload = {"brands": ["Coca-Cola"], "competitors": ["Pepsi"]}
    assert missing_target_params(["brand_mentions", "competitor"], payload) == []


def test_missing_target_params_ignores_types_without_targets():
    assert missing_target_params(["sentiment", "trends"], {}) == []


def test_target_params_in_scope_finds_top_level_and_nested():
    assert target_params_in_scope({"brands": ["X"]}) == ["brands"]
    assert target_params_in_scope({"brand_mentions": {"brands": ["X"]}}) == ["brands"]
    assert target_params_in_scope({"competitor": {"competitors": ["Y"]}, "hashtags": ["#z"]}) == [
        "competitors",
        "hashtags",
    ]
    assert target_params_in_scope({"sentiment": {"categories": ["Позитивный"]}}) == []


def test_all_payload_params_are_known():
    assert ALL_PAYLOAD_PARAMS == {
        "brands",
        "competitors",
        "hashtags",
        "influencer_names",
        "keywords_list",
        "topic_list",
    }
