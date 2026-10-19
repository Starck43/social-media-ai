"""Content detail keeps useful results visible and task navigation safe."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("content_layout_projection", ROOT / "app/services/ai/analysis_render.py")
projection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(projection)


def render(data):
    env = Environment(
        loader=ChoiceLoader(
            [
                DictLoader(
                    {"web/base.html": "{% block page_actions %}{% endblock %}{% block content %}{% endblock %}"}
                ),
                FileSystemLoader(ROOT / "app/web/templates"),
            ]
        ),
        autoescape=select_autoescape(("html",)),
    )
    env.filters["human_dt"] = lambda value, **_kwargs: str(value or "")
    env.filters["ui_label"] = str
    return env.get_template("web/analytics_detail.html").render(
        analysis=SimpleNamespace(id=1132, source_id=2939, analysis_date=None, llm_model=None),
        display=projection.render_analysis(data),
        retry={"task_links": [{"url": "/app/tasks/1582", "name": "Daily task"}], "blocking": []},
        back_url="/app/tasks/1582",
        back_label="К задаче",
        chain_url=None,
        platform_name="VK",
        window_start=None,
        analyzed_at=None,
        topics=[],
        vk_reactions=False,
    )


def test_empty_metrics_and_sections_do_not_obscure_saved_content():
    html = render({"analysis_summary": "Saved explanation"})
    assert "Saved explanation" in html
    assert 'class="ui-card" data-metric=' not in html
    assert "Выделяющиеся моменты" not in html
    assert "Тональность текста" not in html
    assert "/app/sources/" not in html
    assert 'href="/app/tasks/1582"' in html


def test_confirmed_zero_is_shown_after_summary():
    html = render(
        {
            "analysis_summary": "Saved explanation",
            "content_statistics": {
                "total_posts": 1,
                "total_reactions": 0,
                "metric_coverage": {"total_reactions": {"known": 1, "total": 1}},
            },
        }
    )
    assert html.index("Saved explanation") < html.index('data-metric="total_reactions"')
    assert 'data-state="available"' in html


def test_incomplete_content_and_saved_text_remain_escaped():
    html = render(
        {
            "analysis_summary": '<script id="content-xss">bad()</script>',
            "analysis_metadata": {"analysis_complete": False, "coverage_limits": ["text_sampled"]},
        }
    )
    assert "Неполный анализ" in html
    assert "часть текстов не вошла" in html
    assert '<script id="content-xss">' not in html
    assert "&lt;script" in html
