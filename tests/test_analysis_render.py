"""Tests for `app/services/ai/analysis_render.py`.

Pure dict reshaping shared by the web UI and the sqladmin detail template, so
the two surfaces render a stored `AIAnalytics.summary_data` identically. No DB.
"""

from __future__ import annotations

from app.services.ai.analysis_render import (
    extract_content_statistics,
    extract_text_analysis,
    render_analysis,
    sentiment_summary,
)


def _text_analysis(**overrides):
    base = {
        "main_topics": ["Тема 1", "Тема 2"],
        "overall_mood": "спокойное",
        "highlights": ["Момент A"],
        "sentiment_score": 0.8,
    }
    base.update(overrides)
    return base


def test_extract_text_analysis_reads_multi_llm_block():
    data = {"multi_llm_analysis": {"text_analysis": {"main_topics": ["x"]}}}
    assert extract_text_analysis(data) == {"main_topics": ["x"]}


def test_extract_text_analysis_safe_on_garbage():
    assert extract_text_analysis({}) == {}
    assert extract_text_analysis({"multi_llm_analysis": "not-a-dict"}) == {}


def test_extract_content_statistics_defaults_every_metric():
    out = extract_content_statistics({})
    assert out["total_posts"] == 0
    assert out["avg_reactions_per_post"] == 0
    assert out["engagement_rate"] == 0


def test_extract_content_statistics_reads_real_values():
    data = {"content_statistics": {"total_posts": 5, "total_reactions": 9}}
    out = extract_content_statistics(data)
    assert out["total_posts"] == 5
    assert out["total_reactions"] == 9
    assert out["total_comments"] == 0


def test_sentiment_summary_maps_score_to_label():
    s = sentiment_summary({"multi_llm_analysis": {"text_analysis": {"sentiment_score": 0.8}}})
    assert s["score"] == 0.8
    assert s["label"] == "Позитивный"


def test_sentiment_summary_neutral_and_negative():
    assert sentiment_summary({"multi_llm_analysis": {"text_analysis": {"sentiment_score": 0.5}}})["label"] == "Нейтральный"
    assert sentiment_summary({"multi_llm_analysis": {"text_analysis": {"sentiment_score": 0.2}}})["label"] == "Негативный"


def test_sentiment_summary_missing_score_is_none():
    s = sentiment_summary({})
    assert s["score"] is None
    assert s["label"] == "—"


def test_render_analysis_flattens_display_structure():
    data = {
        "analysis_title": "Активность за 17 октября",
        "analysis_summary": "Сводка",
        "multi_llm_analysis": {"text_analysis": _text_analysis()},
        "unified_summary": {},
        "content_statistics": {"total_posts": 3},
        "source_metadata": {"source_name": "Источник"},
    }
    out = render_analysis(data)
    assert out["analysis_title"] == "Активность за 17 октября"
    assert out["main_topics"] == ["Тема 1", "Тема 2"]
    assert out["sentiment"]["score"] == 0.8
    assert out["content_statistics"]["total_posts"] == 3
    assert out["source_metadata"]["source_name"] == "Источник"


def test_render_analysis_is_safe_on_empty_and_garbage():
    assert render_analysis(None)["main_topics"] == []
    assert render_analysis("garbage")["analysis_title"] is None
