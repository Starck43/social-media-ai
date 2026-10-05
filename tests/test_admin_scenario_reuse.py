"""Admin scenario reuse: `AgentScenarioAdmin.view_prompts_action` (app/admin/views.py).

The operator console's «Просмотр промптов» action must render the exact prompt
the shared builder produces — the same one the web wizard previews — instead of
a hand-rolled copy, so the admin preview cannot drift from production. These
tests sign in over ASGI, hit the action and compare the page against
`ScenarioBuilder.final_prompt`.
"""

from __future__ import annotations

from html import unescape as html_unescape

from app.models import AgentScenario
from app.services.ai.scenario_builder import ScenarioBuilder
from app.types import MediaType
from tests.test_admin_authorization import (  # noqa: F401 — shared admin harness
    _operator,
    _role,
    _signed_in,
    _uniq,
)

# ── harness ────────────────────────────────────────────────────────────────


async def _scenario(**overrides) -> int:
    fields: dict = {
        "name": _uniq("scenario"),
        "description": "created by tests",
        "is_active": True,
    }
    fields.update(overrides)
    row = await AgentScenario.objects.create(**fields)
    return row.id


# ── the action reuses the builder ──────────────────────────────────────────


async def test_view_prompts_renders_the_shared_builder_prompt() -> None:
    scenario_id = await _scenario(
        content_types=["posts"],
        analysis_types=["sentiment", "keywords"],
        base_prompt="Тональность из {platform}: {text}",
    )

    async with _signed_in(await _operator(await _role("SUPERUSER"))) as client:
        page = await client.get(f"/admin/agent-scenario/action/view-prompts?pks={scenario_id}")

    assert page.status_code == 200
    scenario = await AgentScenario.objects.get(id=scenario_id)
    draft = ScenarioBuilder.draft_from_scenario(scenario)
    expected = ScenarioBuilder.final_prompt(draft, MediaType.TEXT)
    assert expected in html_unescape(page.text)


async def test_view_prompts_shows_scope_variables_split() -> None:
    scenario_id = await _scenario(
        content_types=["posts"],
        analysis_types=["sentiment"],
        scope={"sentiment": {"categories": ["Позитивный", "Негативный"]}, "brand_name": "VK"},
    )

    async with _signed_in(await _operator(await _role("SUPERUSER"))) as client:
        page = await client.get(f"/admin/agent-scenario/action/view-prompts?pks={scenario_id}")

    assert page.status_code == 200
    # The custom variable is listed as usable in prompts.
    assert "brand_name" in page.text
    # The analysis-type config is shown in its own section.
    assert '"categories"' in html_unescape(page.text)
