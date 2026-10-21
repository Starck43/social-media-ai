"""Chain resolution — how analyses get (and keep) a `topic_chain_id` + label.

The chain resolver is deterministic (normalization + matching, no LLM call). It
lives on `AIAnalyzer` (`_generate_topic_chain_id`, `_resolve_chain_label`,
`_find_matching_topic_chain`) so the run that writes an analysis decides its chain
in the same place. These tests pin the three guarantees the web/digest grouping
relies on: stable per-mode chain ids, a human label per chain, and re-linking to
an existing chain when the themes still match.
"""

import uuid
from datetime import date, timedelta

import pytest

from app.models import AIAnalytics, AgentScenario, Platform, Source
from app.services.ai.analyzer import AIAnalyzer
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
        name="Chain Source",
        source_type=SourceType.CHANNEL,
        external_id=f"chain-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    yield s
    await Source.objects.delete_by_id(s.id)


@pytest.fixture(autouse=True)
async def _db_cleanup():
    await AIAnalytics.objects.delete()
    yield
    await AIAnalytics.objects.delete()


# ── generation ───────────────────────────────────────────────────────────────


async def test_generate_chain_id_is_stable_per_mode(source):
    analyzer = AIAnalyzer()
    topics = ["Отпуск в Сочи"]
    # Normalized top topic: lowercased, Cyrillic transliterated to Latin,
    # punctuation/spaces stripped, ≤20 chars.
    normalized = "otpuskvsochi"

    themes = await analyzer._generate_topic_chain_id(source, topics, None)
    # No scenario: source + normalized top topic.
    assert themes == f"src_{source.id}_{normalized}"

    scn = AgentScenario(id=7, name="scn")
    with_scn = await analyzer._generate_topic_chain_id(source, topics, scn)
    assert with_scn == f"src_{source.id}_scn_7_{normalized}"
    # Same inputs → same id (that is what makes a chain continue across runs).
    assert await analyzer._generate_topic_chain_id(source, topics, scn) == with_scn


async def test_generate_chain_id_falls_back_to_general_without_topics(source):
    analyzer = AIAnalyzer()
    cid = await analyzer._generate_topic_chain_id(source, [], None)
    assert cid == f"src_{source.id}_general"


# ── label ────────────────────────────────────────────────────────────────────


def test_chain_label_prefers_top_topic():
    assert AIAnalyzer._resolve_chain_label(["Отпуск в Сочи", "Ещё тема"], {}) == "Отпуск в Сочи"


def test_chain_label_falls_back_to_analysis_title():
    results = {"text_analysis": {"parsed": {"analysis_title": "Активность бренда"}}}
    assert AIAnalyzer._resolve_chain_label([], results) == "Активность бренда"


def test_chain_label_generic_fallback():
    assert AIAnalyzer._resolve_chain_label([], {}) == "Общая тема"


def test_chain_label_truncated_to_column_width():
    long_topic = "Т" * 400
    assert len(AIAnalyzer._resolve_chain_label([long_topic], {})) == 255


# ── re-linking ───────────────────────────────────────────────────────────────


async def test_find_existing_chain_relinks_on_topic_overlap(source):
    """Same theme appears in a recent analysis → reuse its chain id."""
    old_id = f"src_{source.id}_scn_1_отпуск"
    await AIAnalytics.objects.create(
        source_id=source.id,
        analysis_date=date.today() - timedelta(days=1),
        period_type=PeriodType.DAY,
        topic_chain_id=old_id,
        chain_label="Отпуск",
        summary_data={"multi_llm_analysis": {"text_analysis": {"main_topics": ["отпуск"]}}},
    )

    matched = await AIAnalyzer()._find_matching_topic_chain(source, ["Отпуск", "пляж"], lookback_days=7)
    assert matched == old_id


async def test_find_existing_chain_returns_none_on_no_match(source):
    await AIAnalytics.objects.create(
        source_id=source.id,
        analysis_date=date.today() - timedelta(days=1),
        period_type=PeriodType.DAY,
        topic_chain_id="chain_other",
        summary_data={"multi_llm_analysis": {"text_analysis": {"main_topics": ["финансы"]}}},
    )

    matched = await AIAnalyzer()._find_matching_topic_chain(source, ["отпуск"], lookback_days=7)
    assert matched is None


async def test_find_existing_chain_respects_lookback(source):
    old_id = f"src_{source.id}_old"
    await AIAnalytics.objects.create(
        source_id=source.id,
        analysis_date=date.today() - timedelta(days=30),
        period_type=PeriodType.DAY,
        topic_chain_id=old_id,
        summary_data={"multi_llm_analysis": {"text_analysis": {"main_topics": ["отпуск"]}}},
    )

    matched = await AIAnalyzer()._find_matching_topic_chain(source, ["отпуск"], lookback_days=7)
    assert matched is None


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ("день рождения", "день победы", 0.5),
        ("съемки команды", "работа команды", 0.5),
        ("день рождения", "рождения день", 1.0),
        ("день", "день рождения", 2 / 3),
        ("день рождения", "финансовый отчет", 0.0),
        ("", "день", 0.0),
    ],
)
def test_token_overlap_does_not_promote_any_shared_word_to_one(left, right, expected):
    from app.services.ai.chain_resolver import _token_set_ratio

    assert _token_set_ratio(left, right) == pytest.approx(expected)


async def test_resolver_does_not_join_different_topics_with_one_shared_word(source):
    from app.services.ai.chain_resolver import resolve_chain_async

    old = "existing-birthday"
    await AIAnalytics.objects.create(
        tenant_id=source.tenant_id,
        source_id=source.id,
        analysis_date=date.today() - timedelta(days=1),
        period_type=PeriodType.DAY,
        topic_chain_id=old,
        chain_label="День рождения",
        summary_data={"topic_hint": "День рождения"},
    )
    new, label = await resolve_chain_async(source.tenant_id, source.id, "День победы")
    assert new != old and label == "День победы"
    reused, _ = await resolve_chain_async(source.tenant_id, source.id, "ДЕНЬ РОЖДЕНИЯ!")
    assert reused == old
