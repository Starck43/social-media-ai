"""The analyzer's ``is_relevant`` gate: a real boolean False keeps the post out.

A parsed text answer that carries a real boolean ``is_relevant == False`` gets
the same treatment as ``is_meaningful == False``: no analytics row is written,
the filtered counter grows and the day stays retryable. The field is untrusted
model output — a missing field, ``None``, a string or a number never filters
anything; only a real boolean reaches the decision.

No database: the source and the scenario are in-memory rows, the analytics
manager is doubled and the paid-attempt journal is stubbed, so the suite
exercises exactly the gate and the write it gates.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.models import AgentScenario, AIAnalytics, Source
from app.services.ai.analyzer import AIAnalyzer
from app.types import SourceType


def _source() -> Source:
    """An in-memory source row: the gate only reads its identity."""
    return Source(id=17, tenant_id=3, name="Gate Source", source_type=SourceType.CHANNEL)


def _scenario(scope: dict[str, Any] | None = None) -> AgentScenario:
    """An in-memory scenario; an empty scope leaves the relevance filter off."""
    return AgentScenario(id=1, name="gate-scn", scope=dict(scope or {}))


def _results(**fields: Any) -> dict[str, Any]:
    """A text answer whose parsed body satisfies the derived output contract."""
    parsed: dict[str, Any] = {"summary": "Сводка дня"}
    parsed.update(fields)
    return {"text_analysis": {"parsed": parsed}}


def _arm(monkeypatch: pytest.MonkeyPatch) -> tuple[AIAnalyzer, list[dict[str, Any]]]:
    """One analyzer with the paid journal stubbed and the analytics manager doubled."""
    analyzer = AIAnalyzer()
    created: list[dict[str, Any]] = []

    monkeypatch.setattr(analyzer, "_journal_paid_attempts", AsyncMock(return_value=None))
    monkeypatch.setattr(analyzer, "_price_usage", AsyncMock(return_value=0))
    monkeypatch.setattr(
        AIAnalytics.objects,
        "filter",
        lambda **kwargs: SimpleNamespace(first=AsyncMock(return_value=None)),
    )

    async def _create(**kwargs: Any) -> SimpleNamespace:
        created.append(kwargs)
        return SimpleNamespace(id=101, source_id=kwargs.get("source_id"))

    monkeypatch.setattr(AIAnalytics.objects, "create", _create)
    return analyzer, created


async def _save(analyzer: AIAnalyzer, results: dict[str, Any], *, scenario: Any) -> Any:
    return await analyzer._save_analysis(
        analysis_results=results,
        unified_summary=None,
        source=_source(),
        content_stats={},
        platform_name="vk",
        agent_scenario=scenario,
    )


async def test_a_real_boolean_false_keeps_the_post_out(monkeypatch) -> None:
    analyzer, created = _arm(monkeypatch)

    result = await _save(analyzer, _results(is_relevant=False), scenario=_scenario())

    assert result is None
    assert created == [], "no analytics row is written for a filtered post"
    assert analyzer.filtered_skipped == 1


async def test_a_real_boolean_false_filters_even_with_the_relevance_filter_on(monkeypatch) -> None:
    """A meaningful, confident row is still kept out by its own relevance verdict."""
    analyzer, created = _arm(monkeypatch)
    results = _results(is_meaningful=True, confidence=0.9, is_relevant=False)

    result = await _save(analyzer, results, scenario=_scenario({"relevance_filter": True, "min_confidence": 0.6}))

    assert result is None
    assert created == []
    assert analyzer.filtered_skipped == 1


async def test_true_is_analysed_as_before(monkeypatch) -> None:
    analyzer, created = _arm(monkeypatch)

    result = await _save(analyzer, _results(is_relevant=True), scenario=_scenario())

    assert result is not None
    assert created and created[0]["source_id"] == 17
    assert analyzer.filtered_skipped == 0


@pytest.mark.parametrize("value", [None, "false", 0])
async def test_untrusted_values_never_filter(monkeypatch, value: Any) -> None:
    """A missing type is not a verdict: None, strings and numbers pass through."""
    analyzer, created = _arm(monkeypatch)

    result = await _save(analyzer, _results(is_relevant=value), scenario=_scenario())

    assert result is not None, f"is_relevant={value!r} must be analysed as before"
    assert created
    assert analyzer.filtered_skipped == 0


async def test_a_missing_field_is_analysed_as_before(monkeypatch) -> None:
    analyzer, created = _arm(monkeypatch)

    result = await _save(analyzer, _results(), scenario=_scenario())

    assert result is not None
    assert created
    assert analyzer.filtered_skipped == 0


async def test_without_a_scenario_the_gate_does_not_apply(monkeypatch) -> None:
    """The field exists only where a scenario declared it; no scenario, no gate."""
    analyzer, created = _arm(monkeypatch)

    result = await _save(analyzer, _results(is_relevant=False), scenario=None)

    assert result is not None
    assert created
    assert analyzer.filtered_skipped == 0
