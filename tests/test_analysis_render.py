"""Tests for `app/services/ai/analysis_render.py`.

Pure dict reshaping shared by the web UI and the sqladmin detail template, so
the two surfaces render a stored `AIAnalytics.summary_data` identically. No DB.
"""

from __future__ import annotations

import pytest

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


def test_extract_content_statistics_missing_metrics_are_unknown():
    out = extract_content_statistics({})
    assert out["total_posts"] is None
    assert out["avg_reactions_per_post"] is None
    assert out["engagement_rate"] is None


def test_extract_content_statistics_reads_real_values():
    data = {"content_statistics": {"total_posts": 5, "total_reactions": 9}}
    out = extract_content_statistics(data)
    assert out["total_posts"] == 5
    assert out["total_reactions"] == 9
    assert out["total_comments"] is None


def test_sentiment_summary_maps_score_to_label():
    s = sentiment_summary({"multi_llm_analysis": {"text_analysis": {"sentiment_score": 0.8}}})
    assert s["score"] == 0.8
    assert s["label"] == "Позитивный"


def test_sentiment_summary_neutral_and_negative():
    assert (
        sentiment_summary({"multi_llm_analysis": {"text_analysis": {"sentiment_score": 0.5}}})["label"] == "Нейтральный"
    )
    assert (
        sentiment_summary({"multi_llm_analysis": {"text_analysis": {"sentiment_score": 0.2}}})["label"] == "Негативный"
    )


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


def test_measured_zero_missing_and_partial_are_distinct():
    data = {
        "content_statistics": {
            "total_posts": 2,
            "total_reactions": 0,
            "total_comments": 0,
            "total_views": 12,
            "metric_coverage": {
                "total_reactions": {"known": 2, "total": 2},
                "total_comments": {"known": 0, "total": 2},
                "total_views": {"known": 1, "total": 2},
            },
        }
    }
    stats = extract_content_statistics(data)
    assert stats["total_reactions"] == 0 and stats["avg_reactions_per_post"] == 0
    assert stats["total_comments"] is None and stats["total_views"] is None
    assert (
        extract_content_statistics({"content_statistics": {"total_posts": 1, "total_reactions": 0}})["total_reactions"]
        is None
    )


def test_parsed_flat_titles_topics_and_highlight_states():
    parsed = {
        "analysis_title": "Nested title",
        "analysis_summary": "Nested summary",
        "topics": [{"name": "F1"}],
        "sentiment_score": 0.95,
        "highlights": [],
    }
    display = render_analysis({"multi_llm_analysis": {"text_analysis": {"parsed": parsed}}})
    assert display["analysis_title"] == "Nested title" and display["analysis_summary"] == "Nested summary"
    assert display["main_topics"] == ["F1"] and display["sentiment"]["score"] == 0.95
    assert display["highlights_available"] and display["highlights"] == []
    assert not render_analysis({})["highlights_available"]


def test_invalid_sentiment_and_saved_original_url_safety():
    from app.services.ai.analysis_render import safe_original_url

    for value in (float("nan"), float("inf"), -1, 1.2, True, "bad"):
        assert sentiment_summary({"sentiment_score": value})["score"] is None
    for url in (
        "javascript:alert(1)",
        "//evil.example/path",
        "https://user:secret@example.com/post",
        "https://example.com\n/",
        "http://[bad",
    ):
        assert safe_original_url(url) is None
    display = render_analysis(
        {
            "post_url": "https://example.com/post",
            "content_statistics": {"original_links": ["https://example.com/post", "javascript:bad"]},
        }
    )
    assert display["original_links"] == ["https://example.com/post"]


def test_rollup_authors_not_presented_as_unique_people():
    data = {"period_rollup": {"start": "2026-03-01", "end": "2026-03-07"}, "content_statistics": {"active_users": 20}}
    display = render_analysis(data)
    assert display["content_statistics"]["active_users"] is None
    assert display["content_window_start"] == "2026-03-01"


@pytest.mark.parametrize("container", ["flat", "text", "parsed", "unified", "legacy"])
def test_common_title_reads_all_supported_payload_shapes(container):
    text = {"analysis_title": "  Дизайн интерьера  ", "analysis_summary": "Сводка", "topics": ["Тема"]}
    data = {
        "flat": text,
        "text": {"multi_llm_analysis": {"text_analysis": text}},
        "parsed": {"multi_llm_analysis": {"text_analysis": {"parsed": text}}},
        "unified": {"unified_summary": {"parsed": text}},
        "legacy": {"ai_analysis": text},
    }[container]
    display = render_analysis(data, source_name="Анна")
    assert display["display_title"] == "Дизайн интерьера"
    assert display["analysis_summary"] == "Сводка"


def test_common_title_fallback_never_uses_database_id_or_chain_label():
    assert render_analysis({"topics": ["дизайн интерьера"]})["display_title"] == "дизайн интерьера"
    assert render_analysis({"analysis_title": "  ", "topics": [{"name": "Тема"}]})["display_title"] == "Тема"
    assert (
        render_analysis({"analysis_title": {"bad": "value"}}, source_name="Анна")["display_title"]
        == "Материалы источника «Анна»"
    )
    assert (
        render_analysis({"source_metadata": {"source_name": "Анна"}})["display_title"] == "Материалы источника «Анна»"
    )
    assert render_analysis({})["display_title"] == "Материалы источника"


@pytest.mark.parametrize(
    "summary, expected",
    [
        ("Первая фраза. Вторая фраза.", "Первая фраза."),
        ("Что изменилось? Ответ в следующей фразе.", "Что изменилось?"),
        ("  Дизайн\n  интерьера   обновлён! Далее детали.", "Дизайн интерьера обновлён!"),
        ("Цена 3.5 млн руб. за помещение. Другая тема.", "Цена 3.5 млн руб. за помещение."),
        ("Проект на ул. Ленина в г. Киров. Следующая фраза.", "Проект на ул. Ленина в г. Киров."),
        ("А. Петров рассказал о ремонте. Дальше детали.", "А. Петров рассказал о ремонте."),
        (
            "Упомянуты мебель, свет и т.д. в новом интерьере. Другая тема.",
            "Упомянуты мебель, свет и т.д. в новом интерьере.",
        ),
        ("Короткая сводка без точки", "Короткая сводка без точки"),
        (None, None),
        ({"bad": "value"}, None),
        ("  \n ", None),
    ],
)
def test_summary_display_heading_uses_first_sentence_without_false_boundaries(summary, expected):
    from app.services.ai.analysis_render import summary_display_heading

    assert summary_display_heading(summary) == expected


def test_summary_display_heading_only_adds_ellipsis_when_truncated():
    from app.services.ai.analysis_render import summary_display_heading

    assert summary_display_heading("а" * 120) == "а" * 120
    assert summary_display_heading("а" * 121) == "а" * 119 + "…"
    heading = summary_display_heading("Содержательная фраза " * 20)
    assert len(heading) <= 120 and heading.endswith("…")


def test_display_title_cascade_summary_before_topic_without_changing_saved_data():
    data = {"topics": ["Дизайн"], "analysis_summary": "Интерьер стал светлее. Далее описание."}
    assert render_analysis(data)["display_title"] == "Интерьер стал светлее."
    assert render_analysis(data)["analysis_summary"] == data["analysis_summary"]
    assert data["topics"] == ["Дизайн"]
    data["analysis_title"] = "Я"
    assert render_analysis(data)["display_title"] == "Я"
    assert render_analysis({"analysis_summary": " ", "topics": ["Дизайн"]})["display_title"] == "Дизайн"
    assert (
        render_analysis({"content_hashes": ["hash"]}, source_name="Анна")["display_title"]
        == "Материалы источника «Анна»"
    )
    assert (
        render_analysis({"analysis_summary": {"bad": "value"}, "unified_summary": {"summary": "Сводка."}})[
            "display_title"
        ]
        == "Сводка."
    )
