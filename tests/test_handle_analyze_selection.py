"""Отбор записей на уровне задачи перед платным анализом.

Owner command:
`.venv/bin/python -m pytest tests/test_handle_analyze_selection.py -q --noconftest -o addopts=`

Дизайн: `docs/design/task-selection.md`. Задача владеет отбором: слои
применяются по порядку (источник → автор → текстовый скоуп), а сценарий
по-прежнему решает «как анализировать» и какие `content_types` собираются.

Проверяется три вещи:

1. записи, уже лежащие в `collected_items`, фильтруются **до** передачи в
   `AIAnalyzer.analyze_content` — отсеянные не оплачиваются;
2. пустой отбор (`tracked_users` и ключи пусты) передаёт все строки: задача
   смотрит сам источник, типы контента задаёт сценарий;
3. в per-source результат попадает диагностика отбора (`selection` с числами
   `eligible`/`rejected`), чтобы оператор видел, почему запись не проанализирована.

LLM и БД не вызываются: analyzer, источники, сценарий и стадии обработки —
двойники. Условие «аналитик получил именно эти записи» фиксируется двойником
`analyze_content`, поэтому тест не зависит от внутренностей отбора.
"""

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.jobs import handlers as module

ROOT = Path(__file__).resolve().parents[1]
SOURCE = SimpleNamespace(id=11, name="wall", external_id="wall", params={}, tenant_id=1)


# --- помощники -------------------------------------------------------------


def staged(author_id=None, text="", platform="vk", key=None):
    """Строка `collected_items` в том виде, в каком её видит обработчик.

    Настоящий `as_agent_item()` кладёт `author` снимком и `platform` записи —
    отбор читает именно их (см. `selection._author_keys`).
    """
    author = {"id": author_id, "external_id": author_id, "name": f"u{author_id}"} if author_id is not None else {}
    item = {
        "platform": platform,
        "external_id": f"post-{key}",
        "text": text,
        "author": author,
        "permalink": f"https://example.test/{key}",
        "content_hash": f"hash-{key}",
    }
    return SimpleNamespace(content_hash=f"hash-{key}", as_agent_item=lambda: dict(item))


def selection_progress(result):
    """Достаёт диагностику отбора из результата джобы.

    Форма считается минимально-инвазивной: per-source dict с ключом
    `selection`, у которого есть числовые `eligible` и `rejected`. Возможные
    синонимы имён (selected/filtered_out) терпим — `reasons` проверяется, если
    он есть.
    """
    rows = result.get("per_source") or []
    assert rows, "per_source пуст — отбор не отчитался"
    per = rows[0]
    selection = per.get("selection")
    assert isinstance(selection, dict), f"нет per-source ключа 'selection' (есть: {sorted(per)})"
    for name in ("eligible", "rejected"):
        assert type(selection.get(name)) is int, f"в selection нет целого '{name}': {selection}"
    return selection


@pytest.fixture
def analyze_env(monkeypatch):
    """Окружение handle_analyze: задача, источник, сценарий и двойники стадий."""
    task = SimpleNamespace(
        id=5,
        payload={},
        tenant_id=1,
        agent_scenario=SimpleNamespace(id=3, is_active=True),
        trigger_config=None,
        action_type=None,
    )
    analyzed_items = []

    async def analyze_content(items, source, **_kwargs):
        analyzed_items.append([dict(item) for item in items])
        return []

    analyzer = SimpleNamespace(
        analyze_content=AsyncMock(side_effect=analyze_content),
        completed_hashes=set(),
        current_fingerprint=None,
        reported_errors=0,
    )
    staged_rows = []

    monkeypatch.setattr(module, "_load_task", AsyncMock(return_value=task))
    monkeypatch.setattr(module, "_resolve_sources", AsyncMock(return_value=[SOURCE]))
    monkeypatch.setattr(module, "_resolve_content_window", lambda *_a, **_k: {})
    monkeypatch.setattr(module, "_llm_failure_code", AsyncMock(return_value=None))
    monkeypatch.setattr(module, "_retire_staged", AsyncMock(return_value=0))
    monkeypatch.setattr(module, "_count_failed_staged", AsyncMock(return_value=0))
    monkeypatch.setattr("app.jobs.progress.step", AsyncMock())
    monkeypatch.setattr("app.jobs.progress.update_current", AsyncMock())

    collected = SimpleNamespace(
        objects=SimpleNamespace(
            count_for_source=AsyncMock(side_effect=lambda *_a, **_k: len(staged_rows)),
            for_source=AsyncMock(side_effect=lambda *_a, **_k: list(staged_rows)),
        )
    )
    monkeypatch.setattr("app.models.CollectedItem", collected)
    monkeypatch.setattr("app.services.ai.analyzer.AIAnalyzer", lambda: analyzer)

    trigger = SimpleNamespace(
        should_analyze=AsyncMock(side_effect=lambda content, _task: content),
        should_act=AsyncMock(return_value=False),
    )
    monkeypatch.setattr("app.services.ai.trigger_evaluator.trigger_evaluator", trigger)

    return SimpleNamespace(
        task=task,
        analyzer=analyzer,
        analyzed=analyzed_items,
        stage=lambda *rows: staged_rows.extend(rows),
    )


def run_analyze(env, payload=None):
    return asyncio.run(module.handle_analyze({"agent_task_id": env.task.id, **(payload or {})}))


# --- контракт --------------------------------------------------------------


class TestItemsAreFilteredBeforeTheModelPays:
    def test_tracked_authors_are_the_only_ones_analysed(self, analyze_env):
        """Отбор по автору: записи не отслеживаемых людей не доходят до модели."""
        analyze_env.task.payload = {"tracked_users": ["vk:1"]}
        analyze_env.stage(
            staged(1, "пост Анны", key="a"),
            staged(2, "пост Бориса", key="b"),
            staged(1, "ещё Анна", key="c"),
        )

        result = run_analyze(analyze_env)

        analyze_env.analyzer.analyze_content.assert_awaited_once()
        sent = analyze_env.analyzed[-1]
        assert [item["external_id"] for item in sent] == ["post-a", "post-c"], "передан посторонний автор"
        selection = selection_progress(result)
        assert (selection["eligible"], selection["rejected"]) == (2, 1)

    def test_keywords_filter_items_of_an_eligible_author(self, analyze_env):
        """Текстовый скоуп: у подходящего автора остаются только совпавшие записи."""
        analyze_env.task.payload = {"tracked_users": ["vk:1"], "keywords_list": ["Кирове"]}
        analyze_env.stage(
            # Phrase matching is literal (no stemming): the test states the
            # phrase as the operator would have to type it.
            staged(1, "Погода в Кирове испортилась", key="hit"),
            staged(1, "Совсем про другое", key="miss"),
            staged(2, "Киров, но чужой автор", key="other"),
        )

        result = run_analyze(analyze_env)

        sent = analyze_env.analyzed[-1]
        assert [item["external_id"] for item in sent] == ["post-hit"]
        selection = selection_progress(result)
        assert selection["eligible"] == 1
        assert selection["rejected"] == 2
        # Причины — если реализация их пишет: два разных слоя, а не один счётчик.
        reasons = selection.get("reasons")
        if isinstance(reasons, dict) and reasons:
            assert sum(reasons.values()) >= selection["rejected"]

    def test_all_rejected_means_the_model_is_not_called(self, analyze_env):
        """Если не прошла ни одна запись, платного вызова быть не должно."""
        analyze_env.task.payload = {"tracked_users": ["vk:99"]}
        analyze_env.stage(staged(1, "раз", key="a"), staged(2, "два", key="b"))

        result = run_analyze(analyze_env)

        analyze_env.analyzer.analyze_content.assert_not_awaited()
        selection = selection_progress(result)
        assert (selection["eligible"], selection["rejected"]) == (0, 2)

    def test_every_item_of_a_tracked_author_survives_without_a_text_scope(self, analyze_env):
        """Пустой `keywords_list` не режет автора: фильтр по фразе — отдельный слой."""
        analyze_env.task.payload = {"tracked_users": ["vk:7"], "keywords_list": []}
        analyze_env.stage(staged(7, "любой текст", key="a"), staged(7, "", key="b"))

        result = run_analyze(analyze_env)

        assert len(analyze_env.analyzed[-1]) == 2
        assert selection_progress(result)["eligible"] == 2


class TestEmptySelectionLooksAtTheSourceItself:
    def test_without_tracked_users_and_keywords_all_items_are_analysed(self, analyze_env):
        """Пустой отбор = «смотрим сам источник», типы контента задаёт сценарий.

        Отбор не должен добавлять ни второго условия, ни дефолта «никого»:
        все строки, дождавшиеся анализа, уходят в модель.
        """
        analyze_env.task.payload = {"tracked_users": [], "keywords_list": []}
        analyze_env.stage(
            staged(1, "первый", key="a"),
            staged(2, "второй", key="b"),
            staged(None, "без автора", key="c"),
        )

        result = run_analyze(analyze_env)

        assert len(analyze_env.analyzed[-1]) == 3
        selection = selection_progress(result)
        assert (selection["eligible"], selection["rejected"]) == (3, 0)

    def test_absent_payload_also_selects_everything(self, analyze_env):
        """Отсутствие ключей — то же, что пустые списки."""
        analyze_env.task.payload = {}
        analyze_env.stage(staged(1, "a", key="a"), staged(2, "b", key="b"))

        result = run_analyze(analyze_env)

        assert len(analyze_env.analyzed[-1]) == 2
        assert selection_progress(result)["rejected"] == 0

    def test_unknown_tracked_user_is_reported_not_silently_empty(self, analyze_env):
        """Все авторы чужие — отчёт об отборе называет причину, а не молчит."""
        analyze_env.task.payload = {"tracked_users": ["vk:404"]}
        analyze_env.stage(staged(1, "раз", key="a"))

        result = run_analyze(analyze_env)

        selection = selection_progress(result)
        assert selection["rejected"] == 1
        unknown = selection.get("unknown_tracked_users")
        if unknown is not None:
            assert "vk:404" in unknown


class TestHandlerKeepsTheSelectionWiring:
    def test_handler_applies_the_selection_core(self):
        """AST: обработчик вызывает ядро отбора, а не собственный фильтр.

        Имя функции (`select_items`) — контракт ядра; импорт проверяется по
        исходнику, поэтому тест не ломается от порядка импортов в функции.
        """
        source = (ROOT / "app" / "jobs" / "handlers.py").read_text(encoding="utf-8")
        analyze = re.search(r"^async def handle_analyze\(.*?(?=^async def |\Z)", source, re.S | re.M)
        assert analyze, "handle_analyze не найден"
        body = analyze.group(0)
        assert "build_scope" in body, "отбор не читает задачу через build_scope"
        assert "select_items" in body, "отбор не применяется к строкам collected_items"
        assert "monitored_users" not in body
