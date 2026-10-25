"""An invited member gets chat and settings shaped by their own access level.

* the agent is offered only tools the member's level may call — it neither
  proposes nor stages what the member cannot do (dispatch still rechecks);
* settings show a member their own part (profile, team, personal connections)
  and their level, not the workspace-only agent and channel tabs;
* the refused-section page names the level and lists what is open.
"""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

from app.models import User
from app.types import PlatformRoleType, WorkspaceRoleType
from tests.web.test_web_settings import _cleanup, _client, _drop_workspaces, _invitee, _login, _register


def _tool_names(specs) -> set[str]:
    return {schema["function"]["name"] for schema in specs}


def test_agent_is_offered_only_the_tools_the_member_may_call(monkeypatch) -> None:
    import app.agent.toolset  # noqa: F401 — registers the tools
    from app.agent import runtime
    from app.agent.tools import TOOL_REGISTRY, tool_specs

    monkeypatch.setattr(runtime, "permission_scope", lambda *a, **k: nullcontext())
    monkeypatch.setattr(runtime, "has_permission_by_codename", lambda user, code: code.endswith(".view"))
    member = SimpleNamespace(user=object(), is_owner=False, member_grants=frozenset())

    offered = _tool_names(runtime.tool_specs_for(member))
    everything = _tool_names(tool_specs())
    writes = {name for name in everything if (TOOL_REGISTRY[name].required_permission or "").endswith(".create")}
    assert writes, "premise: some tools create things"
    assert not offered & writes
    assert offered == {
        name
        for name in everything
        if TOOL_REGISTRY[name].required_permission is None or TOOL_REGISTRY[name].required_permission.endswith(".view")
    }
    # No identity (system paths) keeps the full catalogue.
    assert _tool_names(runtime.tool_specs_for(None)) == everything


async def test_a_viewer_gets_their_part_of_settings_and_chat() -> None:
    owner: User | None = None
    viewer: User | None = None
    tenant_id = None
    try:
        async with await _client() as c:
            owner, tenant_id = await _register(c, "MbrOwn")
            page = await c.get("/app/settings")
            for tab in ("workspace", "team", "connections", "agent", "channels"):
                assert f"/app/settings?tab={tab}" in page.text, tab
            viewer = await _invitee("MbrViewer", WorkspaceRoleType.VIEWER, tenant_id)

        async with await _client() as c:
            await _login(c, viewer.username)
            page = await c.get("/app/settings")
            for tab in ("workspace", "team", "connections"):
                assert f"/app/settings?tab={tab}" in page.text, tab
            for tab in ("agent", "channels"):
                assert f"/app/settings?tab={tab}" not in page.text, tab

            # A hand-typed workspace-only tab lands on the member's first tab.
            agent_tab = await c.get("/app/settings?tab=agent")
            assert agent_tab.status_code == 200
            assert 'action="/app/settings/agent"' not in agent_tab.text

            team = await c.get("/app/settings?tab=team")
            assert "data-own-level" in team.text and "Наблюдатель" in team.text
            assert 'action="/app/settings/invites"' not in team.text

            chat = await c.get("/app/chat")
            assert chat.status_code == 200
            assert "Ваш уровень — «Наблюдатель»" in chat.text
            assert "умеет их менять" not in chat.text

            denied = await c.get("/app/tasks")
            assert denied.status_code == 403
            assert "Ошибка 403" in denied.text and "Наблюдатель" in denied.text
            assert "Вам доступны" in denied.text and 'href="/app/sources"' in denied.text
    finally:
        await _cleanup(owner, viewer)
        await _drop_workspaces(tenant_id)
