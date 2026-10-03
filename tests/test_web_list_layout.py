"""List-page UI contracts for sources, scenarios and tasks.

Three pages moved affordances around, and each move has a failure mode that
only shows up in the rendered HTML — not in the handler that serves it:

* **sources** — «Режим» left the table for the detail page, «Доступ» gained a
  centred cell (the `unconfigured` label is a sentence, and left-aligned under a
  pill it read as leftover text), and the workspace column is «Пространство».
* **scenarios** — the status wears the same pill as every other list, and the
  name is the way into the editor.
* **tasks** — the name is the way into the editor, delete lives inside that
  editor, and «Активировать» no longer wears the glyph «Выполнить» wears.

So these assert on markup, not on behaviour: the routes are covered elsewhere,
what is checked here is which control sits where and how it is drawn. That the
endpoint behind each one enforces the right is `test_web_permissions.py`'s job.
"""

from __future__ import annotations

import re

import pytest

from app.core.tenant_context import tenant_scope
from tests.test_web_permissions import (  # noqa: F401 — shared web harness
    _client,
    _drop,
    _login,
    _name,
    _register,
)


def _table_rows(html: str) -> list[str]:
    """Every `<tr>` of the page, so a check can scope itself to the rows."""
    return re.findall(r"<tr\b.*?</tr>", html, re.S)


def _list_table(html: str) -> str:
    """The listing table — the first one, not a modal."""
    match = re.search(r"<table\b.*?</table>", html, re.S)
    assert match, "the page rendered no table"
    return match.group(0)


# ── sources ────────────────────────────────────────────────────────────────


@pytest.mark.tenancy
async def test_the_sources_list_leaves_the_mode_to_the_detail_page() -> None:
    """The mode is a property of one source, so it belongs on its own page.

    The detail page's «Как собирается» block already carries the label *and* the
    sentence explaining it; the column repeated the label with the explanation
    cut off, which is the worst of both.
    """
    from tests.test_web_sources import _drop_source, _make_source

    source_id = None
    async with await _client() as client:
        user, tenant_id = await _register(client, "SrcNoModeCol")
        await _login(client, user.username)
        try:
            source_id = await _make_source(client, _name("mode"), mode="user")
            page = await client.get("/app/sources")
            assert page.status_code == 200

            table = _list_table(page.text)
            assert ">Режим<" not in table, "the mode column must be gone from the list"
            assert "Личный аккаунт" not in table, "nor the badge it used to carry"

            # ...and it is still reachable, with its explanation, one click in.
            detail = await client.get(f"/app/sources/{source_id}")
            assert detail.status_code == 200
            assert "Как собирается" in detail.text
            assert "Личный аккаунт" in detail.text, "the detail page names the mode"
        finally:
            await _drop_source(source_id)
            await _drop(user, tenant_id)


@pytest.mark.tenancy
async def test_the_access_column_is_centred_and_still_carries_its_reason() -> None:
    """A badge plus the sentence under it has to read as one centred block.

    `unconfigured` is the state that motivated this: there is no button to
    offer, so the cell is the label *and* the explanation. Left-aligned, the
    explanation trailed off past the pill above it.
    """
    from tests.test_web_sources import _drop_source, _make_source

    source_id = None
    async with await _client() as client:
        user, tenant_id = await _register(client, "SrcAccessCell")
        await _login(client, user.username)
        try:
            source_id = await _make_source(client, _name("access"), mode="pull")
            page = await client.get("/app/sources")
            assert page.status_code == 200

            assert '<th class="px-4 py-3 text-center">Доступ</th>' in page.text

            row = next(r for r in _table_rows(page.text) if "access" in r)
            cell = re.search(r'<td class="px-4 py-3 text-center">(.*?)</td>', row, re.S)
            assert cell, "the Доступ cell is no longer centred"
            # Whatever the state is, it is drawn as the shared pill — or as the
            # "—" that means "this source needs no personal secret at all".
            assert "rounded-full" in cell.group(1) or "—" in cell.group(1)
        finally:
            await _drop_source(source_id)
            await _drop(user, tenant_id)


# ── scenarios ──────────────────────────────────────────────────────────────


async def _scenario(tenant_id: int, name: str, **overrides) -> int:
    from app.models import AgentScenario

    fields: dict = {
        "name": name,
        "description": "created by tests",
        "is_active": True,
    }
    fields.update(overrides)
    # `tenant_id` is passed inside a bypass scope: `tests/conftest.py` patches
    # `is_bypass()` to True, and under the `@pytest.mark.tenancy` marker these
    # tests use the real write path, which stamps whatever the scope says.
    with tenant_scope(bypass=True):
        row = await AgentScenario.objects.create(tenant_id=tenant_id, **fields)
    return row.id


@pytest.mark.tenancy
@pytest.mark.parametrize("is_active", [True, False])
async def test_scenario_status_wears_the_shared_pill(is_active: bool) -> None:
    """No leading dot: the status is a pill like every other list's.

    The dot-plus-word pair was the only state on the page not wearing the badge,
    so a column of them read as a column of sentences.
    """
    async with await _client() as client:
        user, tenant_id = await _register(client, "ScPill")
        await _login(client, user.username)
        try:
            await _scenario(tenant_id, _name("pill"), is_active=is_active)
            page = await client.get("/app/scenarios")
            assert page.status_code == 200

            row = next(r for r in _table_rows(page.text) if "pill" in r)
            assert "h-1.5 w-1.5 rounded-full bg-" not in row, "the status dot is back"
            # The same pill the sources/tasks lists wear: emerald when live,
            # the muted slate one when not.
            bg = "bg-emerald-950" if is_active else "bg-slate-800"
            fg = "text-emerald-400" if is_active else "text-slate-500"
            cell = next(c for c in re.findall(r"<td\b.*?</td>", row, re.S) if "активен" in c)
            assert f"rounded-full {bg}" in cell and fg in cell
            assert ("активен" if is_active else "неактивен") in cell
        finally:
            await _drop(user, tenant_id)


@pytest.mark.tenancy
async def test_the_scenario_name_is_the_way_into_the_editor() -> None:
    """«Настроить» is gone; the name links to the page it already linked to.

    The button and the link had one destination, so the button only made the
    reader scan across the row for a control the name already pointed at.
    """
    async with await _client() as client:
        user, tenant_id = await _register(client, "ScNameLink")
        await _login(client, user.username)
        try:
            scenario_id = await _scenario(tenant_id, _name("byname"))
            page = await client.get("/app/scenarios")
            assert page.status_code == 200

            assert "Настроить" not in page.text
            assert "Посмотреть" not in page.text
            row = next(r for r in _table_rows(page.text) if "byname" in r)
            assert f'<a href="/app/scenarios/{scenario_id}"' in row
        finally:
            await _drop(user, tenant_id)


# ── tasks ──────────────────────────────────────────────────────────────────


async def _task(tenant_id: int, name: str, **overrides) -> int:
    from app.models import AgentTask

    fields: dict = {
        "name": name,
        "job_type": "collect",
        "cron_expr": "0 * * * *",
        "payload": {},
        "is_active": True,
    }
    fields.update(overrides)
    # Same reason as `_scenario`: under the tenancy marker the write path is the
    # real one, so the row is stamped from an explicit bypass scope.
    with tenant_scope(bypass=True):
        row = await AgentTask.objects.create(tenant_id=tenant_id, **fields)
    return row.id


async def _drop_task(task_id: int | None) -> None:
    if task_id is None:
        return
    from app.models import AgentTask

    with tenant_scope(bypass=True):
        await AgentTask.objects.delete(id=task_id)


@pytest.mark.tenancy
async def test_the_task_name_opens_the_editor() -> None:
    """The pencil column is gone; the name carries the same call."""
    task_id = None
    async with await _client() as client:
        user, tenant_id = await _register(client, "TaskNameLink")
        await _login(client, user.username)
        try:
            task_id = await _task(tenant_id, _name("byname"))
            page = await client.get("/app/tasks")
            assert page.status_code == 200

            assert 'aria-label="Настроить задачу"' not in page.text
            row = next(r for r in _table_rows(page.text) if "byname" in r)
            assert f'@click="editTask({task_id})"' in row, "the name must reach the editor"
        finally:
            await _drop_task(task_id)
            await _drop(user, tenant_id)


@pytest.mark.tenancy
async def test_the_editor_is_addressable_by_url() -> None:
    """Opening the editor puts the task id in the URL, and that URL reopens it.

    The modal is the only way into a task's settings, so its address has to name
    the task: a reload, a bookmark or a link pasted to a colleague must land on
    the open editor rather than on the bare list. The same id therefore has to
    travel both ways — out through `history.replaceState`, back in through
    `?task_id=` — and a link naming a task this page does not show must open
    nothing rather than something from another workspace.
    """
    task_id = None
    other_task_id = None
    async with await _client() as client:
        user, tenant_id = await _register(client, "TaskUrl")
        await _login(client, user.username)
        try:
            task_id = await _task(tenant_id, _name("url"))
            foreign = await _task(tenant_id, _name("foreign"))
            other_task_id = foreign

            page = await client.get("/app/tasks")
            assert page.status_code == 200
            # Opening writes the id; closing takes it away, so the address bar
            # never names a task whose editor is not on screen.
            assert "setTaskParam" in page.text
            assert 'searchParams.set("task_id"' in page.text
            assert 'searchParams.delete("task_id"' in page.text
            assert "replaceState" in page.text, "the id must not spam the history stack"
            close = re.search(r'x-show="openEdit".*?</form>', page.text, re.S)
            assert close and "closeEdit()" in close.group(0), "closing must clear the parameter"

            # A direct link opens that editor, and only that one.
            deep = await client.get(f"/app/tasks?task_id={task_id}")
            assert deep.status_code == 200
            assert f"const OPEN_TASK_ID = {task_id}" in deep.text

            stranger = await client.get(f"/app/tasks?task_id={foreign + 100_000}")
            assert "const OPEN_TASK_ID = null" in stranger.text
        finally:
            await _drop_task(task_id)
            await _drop_task(other_task_id)
            await _drop(user, tenant_id)


@pytest.mark.tenancy
async def test_delete_lives_in_the_task_form_not_the_row() -> None:
    """Delete is irreversible, so it belongs beside the other task actions.

    A bare trash icon one misclick from «Выполнить» was the old arrangement;
    the edit form is where a reader has already decided this task is theirs.
    """
    task_id = None
    async with await _client() as client:
        user, tenant_id = await _register(client, "TaskDelete")
        await _login(client, user.username)
        try:
            task_id = await _task(tenant_id, _name("delme"))
            page = await client.get("/app/tasks")
            assert page.status_code == 200

            row = next(r for r in _table_rows(page.text) if "delme" in r)
            assert f"/app/tasks/{task_id}/delete" not in row, "delete is back in the row"

            form = re.search(r'x-show="openEdit".*?</form>', page.text, re.S)
            assert form, "no edit form on the page"
            assert ":formaction=\"'/app/tasks/' + editing.id + '/delete'\"" in form.group(0)
            assert re.search(r">\s*Удалить\s*<", form.group(0))
        finally:
            await _drop_task(task_id)
            await _drop(user, tenant_id)


@pytest.mark.tenancy
async def test_activate_does_not_wear_the_run_now_glyph() -> None:
    """Two identical triangles meant reading tooltips to tell them apart.

    «Выполнить сейчас» keeps the triangle; «Активировать» gets a power switch.
    An active flag alone is not *effective* activity — a collect task with no
    sources is not runnable — so a source-less row is the «Активировать» case.
    """
    play_triangle = "M8 5v14l11-7z"

    task_id = None
    async with await _client() as client:
        user, tenant_id = await _register(client, "TaskIcons")
        await _login(client, user.username)
        try:
            task_id = await _task(tenant_id, _name("glyphs"), is_active=True)
            page = await client.get("/app/tasks")
            assert page.status_code == 200

            row = next(r for r in _table_rows(page.text) if "glyphs" in r)

            # Scope to the toggle form: the row legitimately carries the play
            # triangle for «Выполнить сейчас», which is the glyph being kept.
            toggle = re.search(r'<form method="post" action="/app/tasks/\d+/toggle".*?</form>', row, re.S)
            assert toggle, "the toggle button is gone"
            toggle_html = toggle.group(0)
            assert 'aria-label="Активировать"' in toggle_html
            assert play_triangle not in toggle_html, "«Активировать» still wears «Выполнить»'s glyph"
            assert "M5.636 5.636a9 9 0 1 0 12.728 0M12 3v9" in toggle_html

            # The run button itself is untouched.
            assert 'aria-label="Выполнить сейчас"' in row
            assert play_triangle in row
        finally:
            await _drop_task(task_id)
            await _drop(user, tenant_id)


# ── the workspace column, everywhere it appears ────────────────────────────


@pytest.mark.tenancy
@pytest.mark.parametrize(
    "path",
    # The dashboard's router is mounted at `/app/` itself, so that is its URL —
    # `/app/dashboard` is a 404, not a second entry point.
    ["/app/sources", "/app/tasks", "/app/", "/app/analytics", "/app/jobs", "/app/digests"],
)
async def test_no_page_still_calls_the_workspace_a_tenant(path: str) -> None:
    """«Тенант» was the column header on three pages; «Пространство» is the word.

    Only the *visible* labels are asserted — `Tenant`/`tenant_id` stay in the
    code: they are the storage name, not the product word.
    """
    async with await _client() as client:
        user, tenant_id = await _register(client, "WsWord")
        await _login(client, user.username)
        try:
            page = await client.get(path)
            assert page.status_code == 200, path
            assert ">Тенант<" not in page.text, f"{path} still says «Тенант»"
            assert "Все тенанты" not in page.text, f"{path} still says «Все тенанты»"
            # The jobs/digests filter labelled the very same control «Workspace».
            assert ">Workspace<" not in page.text, f"{path} still says «Workspace»"
        finally:
            await _drop(user, tenant_id)