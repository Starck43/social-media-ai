"""Scenario editor `/app/scenarios/{id}` (docs/design/ui.md §4.5).

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
    User,
    UserRoleType,
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


async def test_editor_renders_the_prompts_and_guards_of_a_scenario() -> None:
    async with await _client() as client:
        user, tenant_id = await _register(client, "ScEdit")
        scenario_id = await _scenario(
            tenant_id,
            _name("edited"),
            text_prompt="original text prompt",
            blacklist=["spam", "scam"],
            rate_limit_per_hour=7,
            cooldown_seconds=900,
        )
        try:
            page = await client.get(f"/app/scenarios/{scenario_id}")
            assert page.status_code == 200
            assert "original text prompt" in page.text, "the saved prompt must be visible in the editor"
            assert "spam" in page.text and "scam" in page.text, "guards must be visible, not hidden in params"
            assert "7" in page.text and "900" in page.text, "numeric guards must round-trip into the form"
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
            form["text_prompt"] = PROMPT_SAVED
            form["rate_limit_per_hour"] = "11"
            form["is_active"] = "on"

            resp = await client.post(f"/app/scenarios/{scenario_id}", data=form)
            assert resp.status_code == 200
            assert "сохран" in resp.text.lower()

            with tenant_scope(tenant_id):
                row = await AgentScenario.objects.get(id=scenario_id, tenant_id=tenant_id)
            assert row.name == form["name"]
            assert row.text_prompt == PROMPT_SAVED
            assert row.rate_limit_per_hour == 11
            assert row.is_active is True
        finally:
            await _drop(user, tenant_id)


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
            member = await _invitee("ScRViewer", UserRoleType.VIEWER, tenant_id, "secret-password-1")

        async with await _client() as m:
            await _login(m, member.username)
            page = await m.get(f"/app/scenarios/{scenario_id}")
            resp = await m.post(
                f"/app/scenarios/{scenario_id}",
                data={"name": "sneaky", "is_active": "on", "_csrf": CSRF_RE.search(page.text).group(1)},
            )
            assert resp.status_code == 200
            assert DENIED in resp.text

            with tenant_scope(tenant_id):
                row = await AgentScenario.objects.get(id=scenario_id, tenant_id=tenant_id)
            assert row.name != "sneaky", "a hidden form is not the check — the write must not land"
    finally:
        await _drop(member, tenant_id)
        await _drop(owner, tenant_id)


# ── the trigger test run ────────────────────────────────────────────────────


async def test_a_trigger_test_run_answers_without_touching_the_scenario() -> None:
    from app.core.tenant_context import tenant_scope
    from app.models import AgentScenario

    async with await _client() as client:
        user, tenant_id = await _register(client, "ScTest")
        scenario_id = await _scenario(
            tenant_id,
            _name("trigger"),
            trigger_type="KEYWORD_MATCH",
            trigger_config={"keywords": ["крипта", "срочно"]},
        )
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
            assert any(w in body for w in ("вердикт", "сработал", "сценар")), "the dry run must answer"

            with tenant_scope(tenant_id):
                row = await AgentScenario.objects.get(id=scenario_id, tenant_id=tenant_id)
            assert row.trigger_type == BotTriggerType.KEYWORD_MATCH, "a dry run must not overwrite the scenario"
        finally:
            await _drop(user, tenant_id)
