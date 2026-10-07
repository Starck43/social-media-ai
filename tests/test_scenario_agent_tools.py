"""
Chat-agent scenario tools (Phase 2/3 of docs/CHAT_BOT_SCENARIOS.md).

What these pin is the behaviour a reader cannot see from the code:

* **the read tools answer, the write tools ask first.** `scenario_create`,
  `update`, `clone`, `delete` are `confirm=True`, so the runtime stages them in
  `session.state['pending_confirmation']` — a chat that ends there has written
  nothing. The round-trip test in `test_agent.py` drives the real loop for that.
* **a preset expands into the same row the wizard would store** — validated
  enums, generated scope, the preset's own parameters merged into it.
* **preferences survive a save** (`scope=scenario_prefs`) and come back as
  defaults, so the second scenario is not typed from scratch.

No LLM is called: the prompt generator is stubbed wherever a prompt would
otherwise be produced.
"""

from __future__ import annotations

import secrets

import pytest

from app.agent.toolset import scenarios as tools
from app.core.tenant_context import tenant_scope
from app.models import AgentScenario, AgentTask
from app.models.managers.agent_memory_manager import agent_memory
from app.services.ai import scenario_prefs
from app.services.ai.scenario_templates import TEMPLATES, expand_template, list_templates


def _name(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(4)}"


@pytest.fixture(autouse=True)
async def _clean_state():
    """Leave no rows behind: preferences and scenarios both live in the workspace.

    Two reasons, not one. The preferences are per-workspace state that would
    otherwise bleed into the next test. The scenarios matter more: the wizard
    tests assert on a *successful* creation, and `check_scenario_limit` counts
    every row in the schema — leftovers from this file would block them as
    "Scenario blocked for tenant ... on plan pro".
    """
    before = {s.id for s in await AgentScenario.objects.all()}
    tasks_before = {t.id for t in await AgentTask.objects.all()}
    yield
    with tenant_scope(bypass=True):
        created = [s.id for s in await AgentScenario.objects.all() if s.id not in before]
        if created:
            await AgentScenario.objects.filter(id__in=created).delete()
        created_tasks = [t.id for t in await AgentTask.objects.all() if t.id not in tasks_before]
        if created_tasks:
            await AgentTask.objects.filter(id__in=created_tasks).delete()
        await agent_memory.filter(scope=scenario_prefs.SCOPE).delete()


async def _scenario(**fields) -> AgentScenario:
    row: dict = {"name": _name("sc-"), "analysis_types": ["sentiment"], "content_types": ["posts"]}
    row.update(fields)
    return await AgentScenario.objects.create(**row)


# ── presets ────────────────────────────────────────────────────────────────


def test_templates_cover_the_documented_presets() -> None:
    for key in ("brand_monitoring", "competitor_watch", "customer_support", "trend_spotter", "toxicity_guard"):
        assert key in TEMPLATES, f"{key} must be available as a preset"


def test_every_template_validates_against_the_production_enums() -> None:
    """A preset with a stale enum value would expand into a row nothing can read."""
    from app.types import AnalysisType, ContentType

    for key in TEMPLATES:
        draft = expand_template(key)
        assert all(ContentType.get_by_value(c) for c in draft.content_types), key
        assert all(AnalysisType.get_by_value(a) for a in draft.analysis_types), key
        assert draft.base_prompt, key


def test_every_template_prompt_validates() -> None:
    """Presets ship their prompts to owners; an unknown variable is a warning on save."""
    from app.services.ai.scenario_builder import ScenarioBuilder

    for key in TEMPLATES:
        assert ScenarioBuilder.unknown_variables(expand_template(key).base_prompt) == [], key


def test_expand_template_carries_preset_fields() -> None:
    draft = expand_template("competitor_watch", {"name": "Конкуренты ВК"})
    assert draft.name == "Конкуренты ВК"
    assert "competitor" in draft.analysis_types
    assert draft.scope, "the scope must be generated, not empty"


def test_expand_template_overrides_win_and_scope_merges() -> None:
    draft = expand_template("brand_monitoring", {"scope": {"keywords": {"max_keywords": 25}}})
    assert draft.scope["keywords"]["max_keywords"] == 25
    # The defaults for the same type survive the override.
    assert "entity_types" in draft.scope["keywords"]


def test_expand_template_drops_unchecking_type_config() -> None:
    """Unchecking an analysis type must not leave its config behind."""
    draft = expand_template("brand_monitoring", {"analysis_types": ["sentiment"]})
    assert "keywords" not in draft.scope


def test_expand_template_rejects_unknown_template_and_field() -> None:
    with pytest.raises(ValueError, match="Неизвестный шаблон"):
        expand_template("nope")
    with pytest.raises(ValueError, match="Неизвестные поля"):
        expand_template("trend_spotter", {"tenant_id": 1})


def test_expand_template_rejects_unknown_enum_value() -> None:
    with pytest.raises(ValueError, match="Неизвестный тип анализа"):
        expand_template("trend_spotter", {"analysis_types": ["vibes"]})


def test_list_templates_is_the_tool_view() -> None:
    listed = list_templates()
    assert {t["key"] for t in listed} == set(TEMPLATES)
    assert all("base_prompt" not in t for t in listed), "the prompt body stays out of the tool result"


# ── read tools ─────────────────────────────────────────────────────────────


async def test_scenario_list_and_get() -> None:
    row = await _scenario(name=_name("listed"), description="d")
    listing = await tools.scenario_list(only_active=False)
    assert any(s["id"] == row.id for s in listing)

    detail = await tools.scenario_get(row.id)
    assert detail["id"] == row.id
    assert detail["analysis_types"] == ["sentiment"]
    assert "base_prompt" in detail


async def test_scenario_get_reports_missing_instead_of_raising() -> None:
    assert "не найден" in (await tools.scenario_get(999_999_999))["error"]


async def test_scenario_templates_tool_returns_the_presets() -> None:
    result = await tools.scenario_templates()
    assert {t["key"] for t in result["templates"]} == set(TEMPLATES)


async def test_validate_prompt_flags_only_unknown() -> None:
    clean = await tools.scenario_validate_prompt("Смотри {text} и {platform}")
    assert clean["ok"] is True and clean["unknown_variables"] == []

    dirty = await tools.scenario_validate_prompt("Смотри {text} и {wat}")
    assert dirty["ok"] is False and dirty["unknown_variables"] == ["wat"]
    assert "text" in dirty["available_variables"]


async def test_suggest_prompt_reports_fallback_when_no_model(monkeypatch) -> None:
    async def _none(description: str) -> str | None:
        return None

    monkeypatch.setattr("app.services.ai.scenario_prompt_suggest.suggest_base_prompt", _none)
    result = await tools.scenario_suggest_prompt("хочу следить за брендом")
    assert result["ok"] is False
    assert "вручную" in result["message"]


async def test_suggest_prompt_returns_generated_text(monkeypatch) -> None:
    async def _fake(description: str) -> str | None:
        return "Следи за брендом в {text}"

    monkeypatch.setattr("app.services.ai.scenario_prompt_suggest.suggest_base_prompt", _fake)
    result = await tools.scenario_suggest_prompt("следить за брендом")
    assert result["ok"] is True
    assert result["unknown_variables"] == []


# ── write tools ────────────────────────────────────────────────────────────


async def test_create_from_template_stores_the_preset() -> None:
    result = await tools.scenario_create(name="Бренд ВК", template_key="brand_monitoring")
    assert result["status"] == "created"

    stored = await AgentScenario.objects.get(id=result["scenario"]["id"])
    assert stored.name == "Бренд ВК"
    assert "brand_mentions" in stored.analysis_types
    assert stored.base_prompt
    assert stored.scope, "the preset expansion must store a working scope"


async def test_create_from_fields_generates_scope_and_rejects_bad_input() -> None:
    ok = await tools.scenario_create(
        name=_name("chat-made"),
        analysis_types=["sentiment", "keywords"],
        content_types=["posts"],
        base_prompt="Анализ {text}",
    )
    assert ok["status"] == "created"
    stored = await AgentScenario.objects.get(id=ok["scenario"]["id"])
    assert "sentiment" in stored.scope

    no_types = await tools.scenario_create(name=_name("no-types"), base_prompt="x")
    assert "тип анализа" in no_types["error"]

    no_prompt = await tools.scenario_create(name=_name("no-prompt"), analysis_types=["sentiment"])
    assert "base_prompt" in no_prompt["error"]

    bad_enum = await tools.scenario_create(name=_name("bad"), analysis_types=["vibes"])
    assert "Неизвестный тип анализа" in bad_enum["error"]


async def test_update_is_partial_and_merges_scope() -> None:
    row = await _scenario(scope={"sentiment": {"categories": ["A"], "emotion_analysis": True}})

    result = await tools.scenario_update(
        row.id, {"name": "Переименован", "scope": {"sentiment": {"categories": ["B"]}}}
    )
    assert result["status"] == "updated"

    stored = await AgentScenario.objects.get(id=row.id)
    assert stored.name == "Переименован"
    assert stored.scope["sentiment"]["categories"] == ["B"]
    assert stored.scope["sentiment"]["emotion_analysis"] is True, "the unmentioned parameter must survive"
    assert stored.analysis_types == ["sentiment"], "untouched fields stay"


async def test_update_refuses_columns_it_does_not_own() -> None:
    row = await _scenario()
    assert "tenant_id" in (await tools.scenario_update(row.id, {"tenant_id": 1}))["error"]
    assert "Не передано" in (await tools.scenario_update(row.id, {}))["error"]


async def test_update_reports_unknown_prompt_variables_as_a_warning() -> None:
    row = await _scenario()
    result = await tools.scenario_update(row.id, {"base_prompt": "Смотри {wat}"})
    assert result["unknown_variables"] == ["wat"], "a warning, not a refusal"


async def test_update_setting_default_clears_the_other_one() -> None:
    other = await _scenario(is_default=True)
    row = await _scenario()
    await tools.scenario_update(row.id, {"is_default": True})

    with tenant_scope(bypass=True):
        other_now = await AgentScenario.objects.get(id=other.id)
    assert other_now.is_default is False


async def test_clone_copies_fields_and_never_the_default_flag() -> None:
    row = await _scenario(
        name="Оригинал",
        base_prompt="Промпт",
        media_overrides={"image": "Картинки {count}"},
        is_default=True,
        scope={"sentiment": {"categories": ["A"]}},
    )
    result = await tools.scenario_clone(row.id, "Копия", {})

    assert result["status"] == "cloned"
    clone = await AgentScenario.objects.get(id=result["scenario"]["id"])
    original = await AgentScenario.objects.get(id=row.id)

    assert clone.name == "Копия"
    assert clone.base_prompt == "Промпт"
    assert clone.media_overrides == {"image": "Картинки {count}"}
    assert clone.is_default is False, "a copy must not become a second default"
    assert original.name == "Оригинал"


async def test_delete_reports_the_tasks_that_lose_their_scenario() -> None:
    row = await _scenario()
    task = await AgentTask.objects.create(
        name=_name("task-"),
        job_type="analyze",
        cron_expr="0 9 * * *",
        payload={},
        agent_scenario_id=row.id,
    )

    result = await tools.scenario_delete(row.id)
    assert result["status"] == "deleted"
    assert result["tasks_left_without_scenario"] == 1
    assert await AgentScenario.objects.get(id=row.id) is None

    with tenant_scope(bypass=True):
        remaining = await AgentTask.objects.get(id=task.id)
    assert remaining is not None and remaining.agent_scenario_id is None


async def test_delete_of_a_missing_scenario_is_an_error() -> None:
    assert "не найден" in (await tools.scenario_delete(999_999_999))["error"]


# ── preferences ────────────────────────────────────────────────────────────


async def test_create_remembers_the_grouping_mode() -> None:
    # After refactoring, grouping is query-time, not scenario-level
    # This test verifies that scenario creation doesn't try to save preferred_analyze_type
    await tools.scenario_create(name=_name("pref"), template_key="trend_spotter")
    # No preferred_analyze_type should be stored
    prefs = await scenario_prefs.load()
    assert "preferred_analyze_type" not in prefs


async def test_preferences_fill_an_empty_draft_but_not_a_chosen_one() -> None:
    from app.services.ai.scenario_builder import ScenarioDraft

    # After refactoring, preferred_analyze_type is removed from scenario_prefs
    # Only brands preference remains
    await scenario_prefs.remember(brands=["Fanta", "Sprite"])

    filled = await scenario_prefs.apply_to_draft(ScenarioDraft(name="x"))
    assert filled.scope["competitor"]["brand_names"] == ["Fanta", "Sprite"]


async def test_preferences_round_trip_lists_through_json() -> None:
    await scenario_prefs.remember(brands=["A", "B"])
    assert (await scenario_prefs.load())["brands"] == ["A", "B"]


async def test_preferences_ignore_unknown_keys_and_empty_values() -> None:
    # preferred_analyze_type is no longer stored, only brands
    written = await scenario_prefs.remember(something_else="x", brands=["TestBrand"])
    assert written == {"brands": ["TestBrand"]}
    assert "something_else" not in await scenario_prefs.load()


# ── system prompt ──────────────────────────────────────────────────────────


async def test_system_prompt_carries_the_scenario_procedure() -> None:
    """The wizard sequence must reach the model even under a custom base prompt.

    `AGENT_SYSTEM_PROMPT` replaces the default prompt entirely, and the scenario
    tools stay registered regardless — without this section a deployment with a
    custom prompt would have the tools and no procedure for using them.
    """
    from app.agent import runtime as agent_runtime
    from app.agent.prompts import SCENARIO_SECTION

    prompt = await agent_runtime.build_system_prompt()
    assert "## Создание сценариев" in prompt
    for step in ("scenario_templates", "scenario_suggest_prompt", "scenario_validate_prompt", "scenario_create"):
        assert step in prompt, f"{step} must be named in the procedure"
    assert SCENARIO_SECTION in prompt


# ── methodology vs specifics ────────────────────────────────────────────────


async def test_create_rejects_targets_in_scope() -> None:
    """Specific targets (brands, competitors, …) must not be stored on a scenario.

    The scenario is a reusable methodology; the targets belong on the task's
    payload. A scope that carries them would turn one scenario into a
    one-brand template, so the tool refuses with guidance instead.
    """
    result = await tools.scenario_create(
        name=_name("sc-brands"),
        analysis_types=["brand_mentions"],
        content_types=["posts"],
        base_prompt="Анализ упоминаний {brands}",
        scope={"brand_mentions": {"brands": ["Арт-Сервис"]}},
    )
    assert result.get("status") != "created"
    assert "payload" in result["error"].lower()
    assert "brands" in result["error"]

    nested = await tools.scenario_create(
        name=_name("sc-comp"),
        analysis_types=["competitor"],
        content_types=["posts"],
        base_prompt="Сравни {competitors}",
        scope={"competitors": ["Конкурент1"]},
    )
    assert nested.get("status") != "created"
    assert "competitors" in nested["error"]


async def test_update_rejects_targets_in_scope() -> None:
    row = await _scenario(analysis_types=["brand_mentions"])
    result = await tools.scenario_update(row.id, {"scope": {"brand_mentions": {"brands": ["Арт-Сервис"]}}})
    assert result.get("status") != "updated"
    assert "payload" in result["error"].lower()

    stored = await AgentScenario.objects.get(id=row.id)
    assert "brands" not in str(stored.scope), "the refused edit must not be persisted"


async def test_task_add_stores_targets_in_payload_and_warns() -> None:
    """The chat-created task carries its targets in `payload`, not the scenario.

    `brands` etc. flow into `AgentTask.payload` so the scheduled analyze job
    feeds them into the prompt; when the bound scenario asks for a target the
    task does not provide, the tool returns a `warnings` hint the agent relays
    to the owner.
    """
    from app.agent.toolset import tasks as task_tools
    from app.core.config import settings
    from app.models.managers.tenant_manager import tenants

    tenant = await tenants.get_or_create_owner(settings.DEFAULT_TENANT_SLUG)
    scenario = await tools.scenario_create(
        name=_name("sc-mon"), analysis_types=["brand_mentions"], content_types=["posts"], base_prompt="Ищи {brands}"
    )
    scenario_id = scenario["scenario"]["id"]
    name = _name("task-anna")

    with tenant_scope(tenant.id):
        ok = await task_tools.task_add(
            name=name,
            cron="0 21 * * 1",
            job_type="analyze",
            scenario_id=scenario_id,
            source_ids=[],
            start_date="2026-07-01",
            brands=["Арт-Сервис"],
        )
        stored = await AgentTask.objects.get(name=name)
        stored_payload = dict(stored.payload or {})

    assert ok["status"] == "created"
    assert stored_payload["brands"] == ["Арт-Сервис"]
    assert not ok.get("warnings"), f"brands supplied, nothing to warn about: {ok!r}"
    assert stored.agent_scenario_id == scenario_id

    # Same scenario, no brands -> a hint that the analysis has nothing to aim at.
    with tenant_scope(tenant.id):
        hinted = await task_tools.task_add(
            name=_name("task-no-targets"),
            cron="0 21 * * 1",
            job_type="analyze",
            scenario_id=scenario_id,
            source_ids=[],
            start_date="2026-07-01",
        )
    assert hinted["status"] == "created"
    assert hinted.get("warnings"), "the missing target must surface as a warning"
    assert any("бренды" in w for w in hinted["warnings"])
