"""Scenario editor `/app/scenarios/{id}` (docs/API.md).

The editor posts the *whole* scenario as one form, so these tests treat the
rendered form as the contract: they scrape the inputs off the page instead of
hardcoding a field list, which keeps them from drifting when a column is added.

Two invariants get the most attention, because both were live bugs here:

* `scenario_save` must pass `tenant_id` explicitly. Every other `/app` route
  does; relying only on the manager's implicit guard means a superuser request
  (which holds a bypass) resolves rows by id alone.
* Clearing `is_default` must not reach another workspace — an unscoped
  `filter(...).update(...)` unsets the default globally.
"""

from __future__ import annotations

import json
import re

from app.types.enums.bot_types import BotTriggerType
from tests.test_web_permissions import (  # noqa: F401 — shared web harness
    CSRF_RE,
    DENIED,
    AsyncClient,
    PlatformRoleType,
    User,
    _client,
    _csrf,
    _drop,
    _invitee,
    _login,
    _name,
    _register,
)

# ── harness ────────────────────────────────────────────────────────────────


async def _scenario(tenant_id: int, name: str, **overrides) -> int:
    """Create a scenario owned by `tenant_id`, overriding any column by keyword.

    `tenant_id` is passed explicitly: `tests/conftest.py` patches `is_bypass()`
    to True, so the ambient scope would otherwise leave the row unowned and every
    later `tenant_id=` lookup would miss.
    """
    from app.models import AgentScenario

    fields: dict = {
        "tenant_id": tenant_id,
        "name": name,
        "description": "created by tests",
        "is_active": True,
    }
    fields.update(overrides)
    row = await AgentScenario.objects.create(**fields)
    return row.id


PROMPT_SAVED = "Новый промпт из редактора"


# ── harness ────────────────────────────────────────────────────────────────
async def _editor_payload(page_text: str) -> dict[str, str]:
    """Every text-like input on the editor page, keyed by `name`.

    Scraped rather than hardcoded: the save handler reads `request.form()`, so
    a test that invents a field name would pass while asserting nothing.
    """
    payload = {
        m.group(1): m.group(2) or ""
        for m in re.finditer(r'<input[^>]*name="([a-z_0-9]+)"[^>]*value="([^"]*)"', page_text)
    }
    for m in re.finditer(r'<textarea[^>]*name="([a-z_0-9]+)"[^>]*>(.*?)</textarea>', page_text, re.S):
        payload[m.group(1)] = m.group(2).strip()
    payload["_csrf"] = CSRF_RE.search(page_text).group(1)
    return payload


# ── reading the editor ──────────────────────────────────────────────────────
async def _open_editor(client: AsyncClient, scenario_id: int) -> tuple[int, dict[str, str]]:
    """The editor page, ready to be posted back unchanged."""
    page = await client.get(f"/app/scenarios/{scenario_id}")
    return page.status_code, await _editor_payload(page.text)


async def test_editor_renders_the_prompts_of_a_scenario() -> None:
    """The guards left the scenario with migration 0073 (they are the task's now),
    so what this page has to show are the analysis settings."""
    async with await _client() as client:
        user, tenant_id = await _register(client, "ScEdit")
        scenario_id = await _scenario(
            tenant_id,
            _name("edited"),
            base_prompt="original text prompt",
            max_tokens=512,
        )
        try:
            page = await client.get(f"/app/scenarios/{scenario_id}")
            assert page.status_code == 200
            assert "original text prompt" in page.text, "the saved prompt must be visible in the editor"
            assert "512" in page.text, "the analysis limit must round-trip into the form"
            # The task's own fields must not be offered here: saving this page
            # would silently drop them if they were.
            assert 'name="trigger_type"' not in page.text
            assert 'name="rate_limit_per_hour"' not in page.text
        finally:
            await _drop(user, tenant_id)


async def test_list_links_every_scenario_to_its_editor() -> None:
    async with await _client() as client:
        user, tenant_id = await _register(client, "ScLink")
        scenario_id = await _scenario(tenant_id, _name("linked"))
        try:
            page = await client.get("/app/scenarios")
            assert page.status_code == 200
            assert f"/app/scenarios/{scenario_id}" in page.text, "the list must reach the editor"
        finally:
            await _drop(user, tenant_id)


async def test_foreign_scenario_id_reads_as_not_found() -> None:
    owner = viewer = None
    other_tenant = viewer_tenant = foreign_id = None
    try:
        async with await _client() as c:
            owner, other_tenant = await _register(c, "ScForeignOwner")
            foreign_name = _name("foreign")
            foreign_id = await _scenario(other_tenant, foreign_name)

        async with await _client() as m:
            viewer, viewer_tenant = await _register(m, "ScForeignViewer")
            # The handler answers "not found" by bouncing to the list with a
            # flash. The list itself is not tenant-scoped under the test's
            # bypass patch, so assert on the editor never opening.
            resp = await m.get(f"/app/scenarios/{foreign_id}")
            assert 'id="sc-prompt-text"' not in resp.text, "another workspace's scenario must not open"

            list_page = await m.get("/app/scenarios")
            form = await _editor_payload(list_page.text)
            resp = await m.post(
                f"/app/scenarios/{foreign_id}",
                data={"name": "hijacked", "_csrf": form["_csrf"]},
            )
            assert "hijacked" not in resp.text

            from app.core.tenant_context import tenant_scope
            from app.models import AgentScenario

            with tenant_scope(bypass=True):
                row = await AgentScenario.objects.get(id=foreign_id, tenant_id=other_tenant)
            assert row.name == foreign_name, "a foreign write must not land"
    finally:
        await _drop(viewer, viewer_tenant)


# ── saving ──────────────────────────────────────────────────────────────────


async def test_saving_the_editor_writes_the_edited_fields() -> None:
    from app.core.tenant_context import tenant_scope
    from app.models import AgentScenario

    async with await _client() as client:
        user, tenant_id = await _register(client, "ScSave")
        scenario_id = await _scenario(tenant_id, _name("saved"))
        try:
            _, form = await _open_editor(client, scenario_id)
            form["name"] = _name("renamed")
            form["base_prompt"] = PROMPT_SAVED
            form["max_tokens"] = "11"
            form["is_active"] = "on"

            resp = await client.post(f"/app/scenarios/{scenario_id}", data=form)
            assert resp.status_code == 200
            assert "сохран" in resp.text.lower()

            with tenant_scope(tenant_id):
                row = await AgentScenario.objects.get(id=scenario_id, tenant_id=tenant_id)
            assert row.name == form["name"]
            assert row.base_prompt == PROMPT_SAVED
            assert row.max_tokens == 11
            assert row.is_active is True
        finally:
            await _drop(user, tenant_id)


async def test_model_popover_saves_numeric_id_and_never_posts_none() -> None:
    """Regression: 'Выберите модель из списка' on every wizard save.

    Model popovers used to render `value="None"` for an unset model (the caller
    stringified the id with Jinja's `|string`), so saving the wizard — even with
    a freshly picked model — posted the literal "None" and the id validation
    rejected it. The popover macro now normalizes None to '' and the editors
    pass the raw id; saving a picked model must land as an int.
    """
    from app.core.tenant_context import tenant_scope
    from app.models import AgentScenario, LLMModel, LLMProvider

    provider = await LLMProvider.objects.create(
        name="PopRegrProvider", api_format="openai", base_url="https://api.popreg.test/v1", is_active=True
    )
    glm = await LLMModel.objects.create(
        provider_id=provider.id,
        name="GLM Fail (free)",
        model_id="zai-org/GLM-fail",
        model_type="text,image",
        is_active=True,
    )
    qwen = await LLMModel.objects.create(
        provider_id=provider.id,
        name="Qwen Works",
        model_id="nvidia/Qwen-works",
        model_type="text,image",
        is_active=True,
    )
    try:
        async with await _client() as client:
            user, tenant_id = await _register(client, "ScModelPick")
            scenario_id = await _scenario(tenant_id, _name("model-pick"))
            try:
                page = await client.get(f"/app/scenarios/{scenario_id}")
                assert f'data-popover-option="{qwen.id}"' in page.text, "options carry the numeric model id"
                assert 'value="None"' not in page.text, "an unset model must not render the literal None"

                form = await _editor_payload(page.text)
                assert form.get("text_llm_model_id") == "", "an unset model stays empty, not 'None'"
                form["text_llm_model_id"] = str(qwen.id)  # what the popover JS writes

                resp = await client.post(f"/app/scenarios/{scenario_id}", data=form)
                assert "Выберите модель" not in resp.text, "a numeric model id must save cleanly"
                with tenant_scope(tenant_id):
                    row = await AgentScenario.objects.get(id=scenario_id, tenant_id=tenant_id)
                assert row.text_llm_model_id == qwen.id
            finally:
                await _drop(user, tenant_id)
    finally:
        await LLMModel.objects.filter(provider_id=provider.id).delete()
        await LLMProvider.objects.filter(id=provider.id).delete()


async def test_wizard_hides_classifiers_and_never_succeeded_models() -> None:
    """Regression: a safety classifier was selectable for text analysis and only
    manifested as failed runs («модель не ответила»). The picker now hides
    classifier families and fleets with zero successes ever; a model already
    wired into the draft stays listed so the operator can see what is set."""
    from app.core.tenant_context import tenant_scope
    from app.models import AgentScenario, LLMModel, LLMProvider

    provider = await LLMProvider.objects.create(
        name="SuitRepoProvider", api_format="openai", base_url="https://api.suitrepo.test/v1", is_active=True
    )
    guard = await LLMModel.objects.create(
        provider_id=provider.id,
        name="Guardian 4 Moderation",
        model_id="vendor/llama-guard-4",
        model_type="text,image",
        is_active=True,
    )
    ghost = await LLMModel.objects.create(
        provider_id=provider.id,
        name="Ghost Free",
        model_id="vendor/ghost-free",
        model_type="text,image",
        is_active=True,
        use_count=5,
        fail_count=5,
    )
    qwen = await LLMModel.objects.create(
        provider_id=provider.id,
        name="Qwen Works",
        model_id="vendor/qwen-works",
        model_type="text,image",
        is_active=True,
        use_count=4,
        fail_count=0,
    )
    scenario_id = None
    user = tenant_id = None
    try:
        async with await _client() as client:
            user, tenant_id = await _register(client, "ScSuitable")
            scenario_id = await _scenario(tenant_id, _name("suitable"))

            page = await client.get(f"/app/scenarios/{scenario_id}")
            assert "Guardian 4 Moderation" not in page.text, "a classifier must not be offered"
            assert "Ghost Free" not in page.text, "a never-succeeded fleet must not be offered"
            assert "Qwen Works" in page.text, "a working model stays offered"

            # A model already wired into the scenario is never a mystery: it
            # stays listed (and hint-labelled) so the operator can switch away.
            await AgentScenario.objects.update_by_id(scenario_id, text_llm_model_id=ghost.id)
            page = await client.get(f"/app/scenarios/{scenario_id}")
            assert "Ghost Free" in page.text, "the wired model must remain visible"
    finally:
        await _drop(user, tenant_id)
        if scenario_id:
            await AgentScenario.objects.filter(id=scenario_id).delete()
        await LLMModel.objects.filter(provider_id=provider.id).delete()
        await LLMProvider.objects.filter(id=provider.id).delete()


async def test_marking_a_scenario_default_does_not_unset_other_workspaces() -> None:
    """The regression: an unscoped `filter(is_default=True).update(...)`."""
    from app.core.tenant_context import tenant_scope
    from app.models import AgentScenario

    first_owner = second_owner = None
    first_tenant = second_tenant = first_id = second_id = None
    try:
        async with await _client() as c:
            first_owner, first_tenant = await _register(c, "ScDefA")
            first_id = await _scenario(first_tenant, _name("default-a"), is_default=True)
            second_owner, second_tenant = await _register(c, "ScDefB")
            second_id = await _scenario(second_tenant, _name("default-b"), is_default=True)

        async with await _client() as m:
            await _login(m, first_owner.username)
            _, form = await _open_editor(m, first_id)
            form["is_default"] = "on"
            resp = await m.post(f"/app/scenarios/{first_id}", data=form)
            assert resp.status_code == 200

        with tenant_scope(bypass=True):
            untouched = await AgentScenario.objects.get(id=second_id, tenant_id=second_tenant)
        assert untouched.is_default is True, "the other workspace's default must survive"
    finally:
        await _drop(first_owner, first_tenant)
        await _drop(second_owner, second_tenant)


async def test_a_read_only_member_cannot_save_the_scenario() -> None:
    from app.core.tenant_context import tenant_scope
    from app.models import AgentScenario

    owner = member = None
    tenant_id = scenario_id = None
    try:
        async with await _client() as c:
            owner, tenant_id = await _register(c, "ScROwner")
            scenario_id = await _scenario(tenant_id, _name("guarded"))
            member = await _invitee("ScRViewer", WorkspaceRoleType.VIEWER, tenant_id, "secret-password-1")

        async with await _client() as m:
            await _login(m, member.username)
            # A viewer is not offered the section at all; the page answers 403
            # with a reason, and a crafted POST is still refused on its own.
            page = await m.get(f"/app/scenarios/{scenario_id}")
            assert page.status_code == 403
            resp = await m.post(
                f"/app/scenarios/{scenario_id}",
                data={"name": "sneaky", "is_active": "on", "_csrf": CSRF_RE.search(page.text).group(1)},
            )
            assert resp.status_code == 403  # the redirect back lands on the refused section
            assert DENIED in resp.text

            with tenant_scope(tenant_id):
                row = await AgentScenario.objects.get(id=scenario_id, tenant_id=tenant_id)
            assert row.name != "sneaky", "a hidden form is not the check — the write must not land"
    finally:
        await _drop(member, tenant_id)
        await _drop(owner, tenant_id)


# ── the trigger test run ────────────────────────────────────────────────────


async def test_a_trigger_test_run_answers_without_touching_the_task() -> None:
    """The preview runs the *task's* trigger — the trigger moved off the
    scenario — and must leave that task's configuration untouched."""
    from app.core.tenant_context import tenant_scope
    from app.models import AgentScenario, AgentTask

    async with await _client() as client:
        user, tenant_id = await _register(client, "ScTest")
        scenario_id = await _scenario(tenant_id, _name("trigger"))
        with tenant_scope(tenant_id):
            task = await AgentTask.objects.create(
                # tenant_id explicit, like `_scenario` does: the suite runs with
                # `is_bypass()` patched True, so an unowned row would be missed
                # by the page's own `tenant_id=` lookup.
                tenant_id=tenant_id,
                name=_name("trigger-task"),
                cron_expr="0 * * * *",
                job_type="analyze",
                agent_scenario_id=scenario_id,
                trigger_type=BotTriggerType.KEYWORD_MATCH,
                trigger_config={"keywords": ["крипта", "срочно"]},
                is_active=True,
            )
            task_id = task.id
        try:
            _, form = await _open_editor(client, scenario_id)
            resp = await client.post(
                f"/app/scenarios/{scenario_id}/test-trigger",
                data={
                    "_csrf": form["_csrf"],
                    "sample_text": "Купите крипту прямо сейчас, срочно!",
                    "analysis_json": json.dumps({"sentiment": "negative", "risk": 0.9}),
                },
            )
            assert resp.status_code == 200
            body = resp.text.lower()
            assert any(w in body for w in ("вердикт", "сработал", "сценар", "задач")), "the dry run must answer"

            with tenant_scope(tenant_id):
                row = await AgentTask.objects.get(id=task_id, tenant_id=tenant_id)
            assert row.trigger_type == BotTriggerType.KEYWORD_MATCH, "a dry run must not overwrite the task"
            assert row.trigger_config == {"keywords": ["крипта", "срочно"]}
        finally:
            with tenant_scope(tenant_id):
                await AgentTask.objects.delete_by_id(task_id)
            await _drop(user, tenant_id)


async def test_a_trigger_test_run_without_a_task_explains_where_triggers_live() -> None:
    """A scenario with no active task has no trigger to preview, and the page
    says so rather than answering from a configuration nothing reads."""
    from app.models import AgentScenario

    async with await _client() as client:
        user, tenant_id = await _register(client, "ScNoTask")
        scenario_id = await _scenario(tenant_id, _name("no-task"))
        try:
            _, form = await _open_editor(client, scenario_id)
            resp = await client.post(
                f"/app/scenarios/{scenario_id}/test-trigger",
                data={"_csrf": form["_csrf"], "sample_text": "любой текст"},
            )
            assert resp.status_code == 200
            assert "задач" in resp.text.lower()
        finally:
            await _drop(user, tenant_id)
