"""`ReportAggregator.generate_digest_brief` — the digest's algorithmic half.

The brief is the whole point of the hybrid digest: the LLM narrates it, so
whatever it omits cannot reach the reader. It therefore has to be right about
three things that are easy to get subtly wrong — which rows are in scope, how
they are grouped, and whether the "dynamics" it claims are a real movement or
an artefact of comparing overlapping windows.

The dynamics tests are the load-bearing ones. Reporting "toxicity up 40pp"
between two windows that overlap is not a small bug: it sends the owner looking
for a crisis that did not happen, and it is invisible until you check the dates.
"""

import uuid
from datetime import date, timedelta

import pytest

from app.models import AgentScenario, AIAnalytics, Platform, Source
from app.services.ai.reporting import ReportAggregator
from app.types import PeriodType, PlatformType, SourceType


@pytest.fixture
async def platform():
    p = await Platform.objects.create(
        name=f"vk_{uuid.uuid4().hex[:8]}",
        platform_type=PlatformType.VK.db_value,
        base_url="https://vk.com",
        params={},
    )
    yield p
    await Platform.objects.delete_by_id(p.id)


@pytest.fixture
async def source(platform):
    s = await Source.objects.create(
        platform_id=platform.id,
        name="Brief Source",
        source_type=SourceType.CHANNEL,
        external_id=f"brief-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    yield s
    await Source.objects.delete_by_id(s.id)


@pytest.fixture(autouse=True)
async def _db_cleanup():
    await AIAnalytics.objects.delete()
    yield
    await AIAnalytics.objects.delete()


def _summary(text: dict | None = None, *, topics: list[str] | None = None, scenario_id: int | None = None) -> dict:
    """A summary in the shape the analyzer actually writes.

    Topics go *inside* `text_analysis.main_topics` — that is where
    `_extract_topics` looks. Putting them at the top level (as this helper's
    first version did) yields a brief that quietly reports no topics at all,
    which reads as "no themes this period" rather than as a test that never
    filled the field.
    """
    payload = dict(text or {})
    if topics:
        payload["main_topics"] = topics
    data: dict = {"multi_llm_analysis": {"text_analysis": payload}}
    if scenario_id is not None:
        data["scenario_metadata"] = {"scenario_id": scenario_id}
    return data


async def _row(
    source,
    *,
    day_offset: int,
    text: dict | None = None,
    topics=None,
    scenario_id=None,
    topic_chain_id: str | None = None,
    chain_label: str | None = None,
):
    """One analytics row `day_offset` days ago (0 = today)."""
    return await AIAnalytics.objects.create(
        source_id=source.id,
        analysis_date=date.today() - timedelta(days=day_offset),
        period_type=PeriodType.DAY,
        topic_chain_id=topic_chain_id,
        chain_label=chain_label,
        summary_data=_summary(text, topics=topics, scenario_id=scenario_id),
    )


# ── shape ────────────────────────────────────────────────────────────────────


async def test_brief_is_empty_without_data(source):
    assert await ReportAggregator().generate_digest_brief(period="day") == ""


async def test_brief_states_the_period_it_covers(source):
    await _row(source, day_offset=0, text={})
    brief = await ReportAggregator().generate_digest_brief(period="week")
    today = date.today()
    assert "Период" in brief
    assert (today - timedelta(days=6)).isoformat() in brief
    assert today.isoformat() in brief


# ── grouping ─────────────────────────────────────────────────────────────────


async def test_themes_group_ranks_topics_by_repeat_mentions(source):
    await _row(source, day_offset=0, topics=["запуск"])
    await _row(source, day_offset=1, topics=["запуск"])
    await _row(source, day_offset=2, topics=["отзывы"])

    brief = await ReportAggregator().generate_digest_brief(period="week", group_by="themes")
    assert "По темам" in brief
    assert "запуск" in brief
    # Counted, so the repeated topic leads the numbered list.
    assert brief.index("запуск") < brief.index("отзывы")


async def test_sources_group_names_each_source(source, platform):
    second = await Source.objects.create(
        platform_id=platform.id,
        name="Second Source",
        source_type=SourceType.CHANNEL,
        external_id=f"brief2-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    try:
        await _row(source, day_offset=0, text={})
        await AIAnalytics.objects.create(
            source_id=second.id,
            analysis_date=date.today(),
            period_type=PeriodType.DAY,
            summary_data=_summary({}),
        )
        brief = await ReportAggregator().generate_digest_brief(period="day", group_by="sources")
        assert "Источники" in brief or "источник" in brief.lower()
        assert "Brief Source" in brief and "Second Source" in brief
    finally:
        await Source.objects.delete_by_id(second.id)


async def test_days_group_lists_each_day(source):
    await _row(source, day_offset=0, text={"content_statistics": {"total_posts": 3}})
    brief = await ReportAggregator().generate_digest_brief(period="week", group_by="days")
    assert "По дням" in brief
    assert date.today().isoformat() in brief


async def test_monitored_users_group_lists_the_author(source):
    """The per-person grouping keys off the chain id, which only the
    `monitored_users` analysis mode builds."""
    await _row(source, day_offset=0, text={}, topic_chain_id=f"src_{source.id}_user_ivan")
    brief = await ReportAggregator().generate_digest_brief(period="week", group_by="monitored_users")
    assert "По отслеживаемым пользователям" in brief
    assert "ivan" in brief


async def test_specialized_sections_are_filtered_by_the_scenario(source):
    """A section the scenario does not enable must not appear."""
    enabled = await AgentScenario.objects.create(
        name=f"only-tox-{uuid.uuid4().hex[:6]}",
        analysis_types=["toxicity"],
        is_active=True,
    )
    try:
        # Both rows carry the scenario id: the filter matches on
        # summary_data.scenario_metadata, so a row without it belongs to no
        # scenario and the whole brief comes back empty.
        await _row(
            source,
            day_offset=0,
            scenario_id=enabled.id,
            text={"toxicity_level": "low", "toxicity_score": 0.1},
        )
        await _row(source, day_offset=1, scenario_id=enabled.id, text={"brand_mentions": ["Acme"]})
        brief = await ReportAggregator().generate_digest_brief(period="week", scenario_id=enabled.id)
        assert "Токсичность" in brief
        assert "Упоминания бренда" not in brief
    finally:
        await AgentScenario.objects.delete_by_id(enabled.id)


# ── dynamics ────────────────────────────────────────────────────────────────


async def test_sentiment_fall_is_reported_against_the_previous_period(source):
    """Cheerful last week, sour this week — the brief must say so."""
    for offset in (8, 9, 10):  # previous window
        await _row(source, day_offset=offset, text={"sentiment_score": 0.8})
    for offset in (0, 1):  # current window
        await _row(source, day_offset=offset, text={"sentiment_score": 0.1})

    brief = await ReportAggregator().generate_digest_brief(period="week")
    assert "Динамика" in brief
    assert "Тональность упала" in brief


async def test_sentiment_rise_is_reported(source):
    for offset in (8, 9):
        await _row(source, day_offset=offset, text={"sentiment_score": 0.1})
    for offset in (0, 1):
        await _row(source, day_offset=offset, text={"sentiment_score": 0.9})

    brief = await ReportAggregator().generate_digest_brief(period="week")
    assert "Тональность выросла" in brief


async def test_toxicity_spike_is_reported(source):
    for offset in (8, 9, 10):
        await _row(source, day_offset=offset, text={"toxicity_level": "low", "toxicity_score": 0.1})
    for offset in (0, 1):
        await _row(source, day_offset=offset, text={"toxicity_level": "high", "toxicity_score": 0.9})

    brief = await ReportAggregator().generate_digest_brief(period="week")
    assert "Токсичность выросла" in brief


async def test_stable_metrics_are_not_reported_as_a_change(source):
    """Flat lines are the common case; a brief that shouts every day trains the
    reader to ignore it."""
    for offset in range(0, 11):
        await _row(
            source,
            day_offset=offset,
            text={"sentiment_score": 0.5, "toxicity_level": "low", "toxicity_score": 0.1},
        )

    brief = await ReportAggregator().generate_digest_brief(period="week")
    assert "упала" not in brief and "выросла" not in brief
    assert "выросла" not in brief  # toxicity too


async def test_dynamics_are_absent_without_a_previous_period(source):
    """A brand-new workspace has nothing to compare against, and inventing a
    delta from an empty baseline is worse than saying nothing."""
    await _row(source, day_offset=0, text={"sentiment_score": 0.1})

    brief = await ReportAggregator().generate_digest_brief(period="week")
    assert "Тональность упала" not in brief
    assert "Тональность выросла" not in brief


async def test_dynamics_never_compare_overlapping_windows(source):
    """The regression this guards: both windows read from "the last N days", so a
    movement is diluted across the overlap and a real change reads as flat."""
    # A steady positive run through last week, turning sharply sour on the last
    # two days. If both windows were read as "the last N days" they would
    # overlap on the middle days, the turn would be diluted across them, and the
    # brief would report no movement in a period that clearly moved.
    for offset in range(0, 8):
        score = 0.1 if offset <= 1 else 0.9
        await _row(source, day_offset=offset, text={"sentiment_score": score})

    brief = await ReportAggregator().generate_digest_brief(period="week")
    assert "Тональность" in brief
    assert "Тональность упала" in brief


# ── chains ────────────────────────────────────────────────────────────────────


async def test_chains_section_absent_without_chains(source):
    """Rows without a `topic_chain_id` produce no "Цепочки" section."""
    await _row(source, day_offset=0, topics=["запуск"])
    brief = await ReportAggregator().generate_digest_brief(period="week", group_by="themes")
    assert "Цепочки" not in brief


async def test_chains_section_lists_chains_by_entry_count(source):
    """Chains grouped by `topic_chain_id`, ranked by entry count, labelled."""
    await _row(source, day_offset=0, topic_chain_id="chain_vac", chain_label="Отпуск")
    await _row(source, day_offset=1, topic_chain_id="chain_vac", chain_label="Отпуск")
    await _row(source, day_offset=2, topic_chain_id="chain_vac", chain_label="Отпуск")
    await _row(source, day_offset=3, topic_chain_id="chain_new", chain_label="Новый проект")
    await _row(source, day_offset=4, topic_chain_id="chain_new", chain_label="Новый проект")

    brief = await ReportAggregator().generate_digest_brief(period="week")
    assert "## Цепочки" in brief
    # The heavier chain leads the list.
    assert brief.index("Отпуск") < brief.index("Новый проект")
    assert "3 записей" in brief
    assert "2 записей" in brief


async def test_chains_section_falls_back_to_chain_id_without_label(source):
    await _row(source, day_offset=0, topic_chain_id="chain_no_label")
    brief = await ReportAggregator().generate_digest_brief(period="week")
    assert "chain_no_label" in brief


async def test_chain_dynamics_reports_new_and_continued_chains(source):
    """A chain present in both windows is "continued"; one only in the current
    period is "new". Labels come from `chain_label`."""
    # Chain "Отпуск": 3 rows in the current week, 1 in the previous one.
    for offset in (0, 1, 2):
        await _row(source, day_offset=offset, topic_chain_id="chain_vac", chain_label="Отпуск")
    await _row(source, day_offset=8, topic_chain_id="chain_vac", chain_label="Отпуск")
    # Chain "Новый проект": 2 rows, only in the current week.
    for offset in (3, 4):
        await _row(source, day_offset=offset, topic_chain_id="chain_new", chain_label="Новый проект")

    brief = await ReportAggregator().generate_digest_brief(period="week")
    assert "## Динамика цепочек" in brief
    assert "Новая цепочка: **Новый проект** (2 записей)" in brief
    assert "Продолжение: **Отпуск** (+2 записей)" in brief
