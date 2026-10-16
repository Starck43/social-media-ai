"""Capture real authenticated HTML for the mobile list-page viewport check."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# isort: off
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tests.test_merged_home_acceptance import existing_target, home
from tests.test_web_settings import _client, _csrf, _login

from app.core.permissions import operator_permission_scope
from app.core.tenant_context import tenant_scope
from app.models import AgentTask, Source

# isort: on

pytestmark = pytest.mark.tenancy

CSRF_INPUT = re.compile(r'name="_csrf"\s+value="([^"]+)"')


def _write_mobile_fixtures(pages: dict[str, str]) -> None:
    configured = os.environ.get("MOBILE_PAGE_FIXTURE_DIR")
    if not configured:
        return

    destination = Path(configured).expanduser().resolve()
    allowed_root = Path("/private/tmp").resolve()
    try:
        destination.relative_to(allowed_root)
    except ValueError as exc:
        raise AssertionError("MOBILE_PAGE_FIXTURE_DIR must stay under /private/tmp") from exc
    if destination == allowed_root:
        raise AssertionError("MOBILE_PAGE_FIXTURE_DIR must name a dedicated subdirectory")

    destination.mkdir(parents=True, exist_ok=True)
    for filename, html in pages.items():
        # The pages prove the real CSRF paths above, but the static browser
        # artifact does not need a reusable token.
        for token in set(CSRF_INPUT.findall(html)):
            html = html.replace(token, "CSRF_REDACTED")
        (destination / filename).write_text(html, encoding="utf-8")


@pytest.mark.asyncio
async def test_authenticated_mobile_list_pages_render_and_can_be_captured(home, existing_target):
    users, tenant_ids, sources = home
    owner, tenant_id = users[0], tenant_ids[0]
    source = next(row for row in sources if row.tenant_id == tenant_id)
    with tenant_scope(tenant_id):
        task = await AgentTask.objects.filter(tenant_id=tenant_id).first()
    assert task is not None

    task_label = "MobileTaskRow" + "T" * 70
    source_label = "MobileSourceRow" + "S" * 150
    with tenant_scope(bypass=True), operator_permission_scope():
        await AgentTask.objects.update_by_id(task.id, name=task_label)
        await Source.objects.update_by_id(source.id, name=source_label)

    async with await _client() as client:
        await _login(client, owner.username)
        paths = {
            "tasks.html": "/app/tasks",
            "task-edit.html": f"/app/tasks/{task.id}/edit",
            "sources.html": "/app/sources",
        }
        pages = {filename: await client.get(path) for filename, path in paths.items()}

        for filename, response in pages.items():
            assert response.status_code == 200, f"{paths[filename]} -> {response.status_code}"
            assert CSRF_INPUT.search(response.text), f"{paths[filename]} must render a real CSRF field"
            assert await _csrf(client, paths[filename]), f"{paths[filename]} must support the CSRF helper path"

        assert task_label in pages["tasks.html"].text
        assert task_label in pages["task-edit.html"].text
        assert source_label in pages["sources.html"].text
        _write_mobile_fixtures({name: response.text for name, response in pages.items()})
